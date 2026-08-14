# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import hashlib
import re
from bisect import bisect_left
from collections import OrderedDict, defaultdict
from copy import copy
from functools import lru_cache
from itertools import chain
from threading import Lock
from typing import TYPE_CHECKING, cast

import ahocorasick_rs
from django.core.cache import cache
from django.db.models import Prefetch, Q, Value
from django.db.models.functions import MD5, Lower

from weblate.checks.morphology import (
    MORPHOLOGY_LANGUAGES,
    SOURCE_STEM_LANGUAGES,
    WORD_RE,
    get_algorithm,
    get_snowball_version,
    get_stemmer,
    stem_word,
)
from weblate.trans.models.unit import Unit
from weblate.utils.csv import PROHIBITED_INITIAL_CHARS
from weblate.utils.state import STATE_TRANSLATED
from weblate.utils.tracing import start_span
from weblate.utils.unicodechars import CONTROLCHARS

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from weblate.trans.models import Project, Translation

SPLIT_RE = re.compile(r"[\s,.:!?]+")
NON_WORD_RE = re.compile(r"\W")
PROHIBITED_INITIAL_CHARS_RE = re.compile(
    f"^({'|'.join(re.escape(char) for char in PROHIBITED_INITIAL_CHARS)})*"
)
CONTROLCHARS_TRANS = str.maketrans(dict.fromkeys(CONTROLCHARS))
GLOSSARY_AUTOMATON_CACHE_SIZE = 16
GLOSSARY_AUTOMATON_CACHE: OrderedDict[tuple[int, int], ahocorasick_rs.AhoCorasick] = (
    OrderedDict()
)
GLOSSARY_AUTOMATON_CACHE_LOCK = Lock()
GLOSSARY_STEM_CACHE_SIZE = 32
GLOSSARY_STEM_CACHE: OrderedDict[
    tuple[int, str, str, int],
    tuple[ahocorasick_rs.AhoCorasick | None, tuple[tuple[str, ...], ...]],
] = OrderedDict()
GLOSSARY_STEM_CACHE_LOCK = Lock()
STEM_SEPARATOR = "\x00"
# Mode names read from the target unit's own flags and from its source unit.
# Kept as module constants so the memoized parse below has a hashable key.
TARGET_SCOPED_MODES = frozenset({"exact", "not-applicable", "read-only", "forbidden"})
SOURCE_SCOPED_MODES = frozenset({"read-only", "forbidden"})


def cleanup_glossary_term(text: str) -> str:
    """
    Clean up the provided glossary term by removing unwanted characters.

    - Translates and removes control characters.
    - Strips leading and trailing whitespace.
    - Removes prohibited leading characters.
    """
    text = text.translate(CONTROLCHARS_TRANS)
    return PROHIBITED_INITIAL_CHARS_RE.sub("", text).strip()


def get_glossary_sources(component):
    # Fetch list of terms defined in a translation
    return list(
        component.source_translation.unit_set.filter(state__gte=STATE_TRANSLATED)
        .values_list(Lower("source"), flat=True)
        .distinct()
    )


def clear_glossary_automaton_cache(project_id: int | None = None) -> None:
    """Clear process-local glossary automatons."""
    with GLOSSARY_AUTOMATON_CACHE_LOCK:
        if project_id is None:
            GLOSSARY_AUTOMATON_CACHE.clear()
        else:
            for cache_key in list(GLOSSARY_AUTOMATON_CACHE):
                if cache_key[0] == project_id:
                    del GLOSSARY_AUTOMATON_CACHE[cache_key]
    clear_glossary_stem_cache(project_id)


def clear_glossary_stem_cache(project_id: int | None = None) -> None:
    """Clear process-local glossary stem indexes."""
    with GLOSSARY_STEM_CACHE_LOCK:
        if project_id is None:
            GLOSSARY_STEM_CACHE.clear()
        else:
            for cache_key in list(GLOSSARY_STEM_CACHE):
                if cache_key[0] == project_id:
                    del GLOSSARY_STEM_CACHE[cache_key]


def get_glossary_automaton(project: Project) -> ahocorasick_rs.AhoCorasick:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.component import (
        prefetch_glossary_terms,
    )

    with start_span(op="glossary.automaton", name=project.slug):
        cache_key = (project.pk, project.glossary_automaton_cache_version)
        with GLOSSARY_AUTOMATON_CACHE_LOCK:
            if cache_key in GLOSSARY_AUTOMATON_CACHE:
                GLOSSARY_AUTOMATON_CACHE.move_to_end(cache_key)
                return GLOSSARY_AUTOMATON_CACHE[cache_key]

        # Chain terms
        prefetch_glossary_terms(project.glossaries)
        terms = set(
            chain.from_iterable(
                glossary.glossary_sources for glossary in project.glossaries
            )
        )
        # Remove blank string as that is not really reasonable to match
        terms.discard("")
        # Build automaton for efficient Aho-Corasick search
        result = ahocorasick_rs.AhoCorasick(
            terms,
            implementation=ahocorasick_rs.Implementation.ContiguousNFA,
            store_patterns=False,
        )
        with GLOSSARY_AUTOMATON_CACHE_LOCK:
            GLOSSARY_AUTOMATON_CACHE[cache_key] = result
            GLOSSARY_AUTOMATON_CACHE.move_to_end(cache_key)
            while len(GLOSSARY_AUTOMATON_CACHE) > GLOSSARY_AUTOMATON_CACHE_SIZE:
                GLOSSARY_AUTOMATON_CACHE.popitem(last=False)
        return result


@lru_cache(maxsize=4096)
def _glossary_modes_from_flags(
    flags_text: str, scoped: frozenset[str]
) -> frozenset[str]:
    """
    Parse one flags string into the subset of glossary modes it sets.

    Memoized on the raw flag text: the stem index build and every matcher
    call ask this for each glossary unit, and a term base holds far fewer
    distinct flag strings than units.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.checks.flags import Flags

    if not flags_text:
        return frozenset()
    flags = None
    with contextlib.suppress(Exception):
        flags = Flags(flags_text)
    if flags is None:
        return frozenset()
    return frozenset(flag for flag in scoped if flag in flags)


def get_glossary_term_modes(unit: Unit) -> set[str]:
    """
    Return effective glossary modes for a target glossary unit.

    ``forbidden`` and ``read-only`` may be set on the source unit and apply
    to every language, while ``exact`` and ``not-applicable`` are per-language
    and are read from the target unit's own flags only.
    """
    modes = set(
        _glossary_modes_from_flags(
            getattr(unit, "extra_flags", "") or "", TARGET_SCOPED_MODES
        )
    )
    modes |= _glossary_modes_from_flags(
        getattr(getattr(unit, "source_unit", None), "extra_flags", "") or "",
        SOURCE_SCOPED_MODES,
    )
    return modes


def get_glossary_stem_automaton(
    project: Project, source_language, language
) -> tuple[ahocorasick_rs.AhoCorasick | None, tuple[tuple[str, ...], ...]]:
    """
    Return the stem index for one source/target language pair.

    The index maps a Snowball stem sequence of a term to the canonical term
    sources. Terms that are exact-only (``exact``, ``read-only``,
    ``forbidden``) or ``not-applicable`` for the target language are excluded.
    Source stemming is enabled only for languages in ``SOURCE_STEM_LANGUAGES``.
    The cache key embeds the glossary automaton cache version because the
    modes live on target units and invalidate together with the glossary.
    """
    if source_language.base_code not in SOURCE_STEM_LANGUAGES:
        return (None, ())
    cache_key = (
        project.pk,
        source_language.code,
        language.code,
        project.glossary_automaton_cache_version,
    )
    with GLOSSARY_STEM_CACHE_LOCK:
        if cache_key in GLOSSARY_STEM_CACHE:
            GLOSSARY_STEM_CACHE.move_to_end(cache_key)
            return GLOSSARY_STEM_CACHE[cache_key]

    with start_span(op="glossary.stems", name=project.slug):
        stemmer = get_stemmer(source_language.code)
        patterns: dict[str, set[str]] = {}
        if stemmer is not None:
            algorithm = stemmer[0]
            units = prepare_glossary_units(project, source_language, language).filter(
                state__gte=STATE_TRANSLATED
            )
            for unit in units:
                modes = get_glossary_term_modes(unit)
                if modes & {"exact", "not-applicable", "read-only", "forbidden"}:
                    continue
                source = cleanup_glossary_term(unit.source)
                if not source:
                    continue
                stems = [
                    stem_word(algorithm, word.lower())
                    for word in WORD_RE.findall(source)
                ]
                if not stems or any(not stem for stem in stems):
                    continue
                pattern = STEM_SEPARATOR + STEM_SEPARATOR.join(stems) + STEM_SEPARATOR
                patterns.setdefault(pattern, set()).add(source.lower())

        if patterns:
            automaton: ahocorasick_rs.AhoCorasick | None = ahocorasick_rs.AhoCorasick(
                list(patterns),
                implementation=ahocorasick_rs.Implementation.ContiguousNFA,
                store_patterns=True,
            )
            term_sources = tuple(
                tuple(sorted(patterns[pattern])) for pattern in patterns
            )
        else:
            automaton = None
            term_sources = ()

    result = (automaton, term_sources)
    with GLOSSARY_STEM_CACHE_LOCK:
        GLOSSARY_STEM_CACHE[cache_key] = result
        GLOSSARY_STEM_CACHE.move_to_end(cache_key)
        while len(GLOSSARY_STEM_CACHE) > GLOSSARY_STEM_CACHE_SIZE:
            GLOSSARY_STEM_CACHE.popitem(last=False)
    return result


def match_glossary_stems(
    source: str,
    source_language,
    automaton: ahocorasick_rs.AhoCorasick,
    term_sources: tuple[tuple[str, ...], ...],
) -> dict[str, list[tuple[int, int]]]:
    """
    Match glossary terms against the source by stem sequences.

    Returns canonical term sources with the character spans of the matched
    words. Only whole words can match, never substrings.
    """
    stemmer = get_stemmer(source_language.code)
    if stemmer is None:
        return {}
    word_spans = [match.span() for match in WORD_RE.finditer(source)]
    if not word_spans:
        return {}
    algorithm = stemmer[0]
    joined_parts = [STEM_SEPARATOR]
    sep_offsets = [0]
    offset = 1
    for start, end in word_spans:
        stem = stem_word(algorithm, source[start:end].lower())
        offset += len(stem)
        joined_parts.extend((stem, STEM_SEPARATOR))
        sep_offsets.append(offset)
        offset += 1
    joined = "".join(joined_parts)

    result: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for termno, start, end in automaton.find_matches_as_indexes(
        joined, overlapping=True
    ):
        first_word = bisect_left(sep_offsets, start)
        last_word = bisect_left(sep_offsets, end - 1) - 1
        if not 0 <= first_word <= last_word < len(word_spans):
            continue
        span = (word_spans[first_word][0], word_spans[last_word][1])
        for term in term_sources[termno]:
            result[term].add(span)
    return {term: sorted(spans) for term, spans in result.items()}


def get_glossary_units(project, source_language, target_language):
    return Unit.objects.filter(
        translation__component__in=project.glossaries,
        translation__component__source_language=source_language,
        translation__language=target_language,
    )


def prepare_glossary_units(project, source_language, language, *, full: bool = False):
    """Return glossary units carrying what flags, ordering and rendering need."""
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        Component,
        Project,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.workspaces.models import Workspace

    # Variant is used for variant grouping, source unit for flags
    base_units = get_glossary_units(project, source_language, language).select_related(
        "source_unit", "variant"
    )
    if full:
        # Include full details needed for rendering
        return base_units.prefetch()
    # Component priority is needed for ordering, file format and flags for flags
    return base_units.prefetch_related(
        Prefetch(
            "translation__component",
            queryset=Component.objects.only(
                "priority",
                "file_format",
                "check_flags",
                "project",
            ),
        ),
        Prefetch(
            "translation__component__project",
            queryset=Project.objects.only(
                "check_flags",
            ),
        ),
        Prefetch(
            "translation__component__project__workspace",
            queryset=Workspace.objects.defer_huge(),
        ),
    )


def get_glossary_terms(
    unit: Unit, *, full: bool = False, include_variants: bool = True
) -> list[Unit]:
    """Return list of term pairs for an unit."""
    if unit.glossary_terms is None:
        fetch_glossary_terms([unit], full=full, include_variants=include_variants)
    return cast("list[Unit]", unit.glossary_terms)


def fetch_glossary_terms(  # ruff: ignore[complex-structure]
    units: list[Unit], *, full: bool = False, include_variants: bool = True
) -> None:
    """Fetch glossary terms for list of units."""
    if len(units) == 0:
        return

    translations: dict[int, Translation] = {}
    translation_units: dict[int, list[Unit]] = defaultdict(list)

    for unit in units:
        translations[unit.translation.id] = unit.translation
        translation_units[unit.translation.id].append(unit)
        # Initialize glossary terms
        unit.glossary_terms = []

    for translation_id, translation in translations.items():
        language = translation.language
        component = translation.component
        # Do not get glossary matches when display is disabled
        if component.hide_glossary_matches:
            continue
        project = component.project
        source_language = component.source_language

        # Extract all source strings
        sources = [unit.source.lower() for unit in translation_units[translation_id]]

        # Match word boundaries if needed
        uses_whitespace = source_language.uses_whitespace()
        boundaries: list[set[int]] = [set() for i in range(len(sources))]
        if uses_whitespace:
            # Get list of word boundaries
            for i, source in enumerate(sources):
                boundaries[i] = {
                    match.span()[0] for match in NON_WORD_RE.finditer(source)
                }
                boundaries[i].add(-1)
                boundaries[i].add(len(source))

        automaton = project.glossary_automaton
        positions: list[dict[str, list[tuple[int, int]]]] = [
            defaultdict(list) for i in range(len(sources))
        ]
        terms: set[str] = set()
        # Extract terms present in the source
        with start_span(op="glossary.match", name=project.slug):
            for i, source in enumerate(sources):
                for _termno, start, end in automaton.find_matches_as_indexes(
                    source, overlapping=True
                ):
                    if not uses_whitespace or (
                        (start - 1 in boundaries[i]) and (end in boundaries[i])
                    ):
                        term = source[start:end].lower()
                        terms.add(term)
                        positions[i][term].append((start, end))

            # Stem fallback: recover inflected forms the exact matcher drops
            stem_automaton, stem_term_sources = get_glossary_stem_automaton(
                project, source_language, language
            )
            if stem_automaton is not None:
                for i, source in enumerate(sources):
                    for term, spans in match_glossary_stems(
                        source, source_language, stem_automaton, stem_term_sources
                    ).items():
                        if term in positions[i]:
                            continue
                        terms.add(term)
                        positions[i][term].extend(spans)

            # Skip processing when there are no matches
            if not terms:
                continue

            base_units = prepare_glossary_units(
                project, source_language, language, full=full
            )

            # Exclude currently edited unit items to prevent self-referencing glossary items
            current_unit_ids = [u.pk for u in translation_units[translation_id] if u.pk]
            if current_unit_ids:
                base_units = base_units.exclude(pk__in=current_unit_ids)

            glossary_units = [
                unit
                for unit in base_units.filter(
                    Q(source__lower__md5__in=[MD5(Value(term)) for term in terms]),
                )
                # not-applicable pairs are excluded from matching for this
                # target language. Filtered in Python rather than SQL because
                # extra_flags is free-form text: a LIKE would also match a
                # longer flag name containing this one, and cannot use an
                # index. The parse itself is memoized.
                if "not-applicable" not in get_glossary_term_modes(unit)
            ]

            # Add variants manually. This could be done by adding filtering on
            # variant__unit__source in the above query, but this slows down the query
            # considerably and variants are rarely used.
            glossary_variants: dict[int, dict[int, Unit]] = defaultdict(dict)
            if include_variants:
                processed_variants = set()

                for match in glossary_units:
                    if not match.variant_id or match.variant_id in processed_variants:
                        continue
                    processed_variants.add(match.variant_id)
                    for child in base_units.filter(variant_id=match.variant_id).exclude(
                        pk=match.pk
                    ):
                        glossary_variants[match.pk][child.pk] = child

            # Prepare term lookup
            glossary_lookup: dict[str, list[Unit]] = defaultdict(list)
            for match in glossary_units:
                glossary_lookup[match.source.lower()].append(match)

            # Inject matches back to the units
            for i, unit in enumerate(translation_units[translation_id]):
                result: dict[int, Unit] = {}
                for term, glossary_positions in positions[i].items():
                    try:
                        matches = glossary_lookup[term]
                    except KeyError:
                        continue

                    for match in matches:
                        item = copy(match)
                        item.glossary_positions = tuple(glossary_positions)
                        result[item.pk] = item
                        for variant in glossary_variants[match.pk].values():
                            item = copy(variant)
                            item.glossary_positions = tuple(glossary_positions)
                            result[item.pk] = item

                # Store sorted results in a unit cache
                unit.glossary_terms = sorted(
                    result.values(), key=lambda x: x.glossary_sort_key
                )


def get_glossary_tuples(units: Iterable[Unit]) -> Generator[tuple[str, str]]:
    r"""
    Build a glossary content as word tuples.

    Based on the DeepL specification:

    - duplicate source entries are not allowed
    - neither source nor target entry may be empty
    - source and target entries must not contain any C0 or C1 control characters (including, e.g., "\t" or "\n") or any Unicode newline
    - source and target entries must not contain any leading or trailing Unicode whitespace character
    - source/target entry pairs are separated by a newline
    - source entries and target entries are separated by a tab
    """
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        Component,
        Project,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.workspaces.models import (
        Workspace,
    )

    # We can get list or iterator as well
    if hasattr(units, "prefetch_related"):
        units = units.prefetch_related(
            "source_unit",
            "translation",
            Prefetch("translation__component", queryset=Component.objects.defer_huge()),
            Prefetch(
                "translation__component__project",
                queryset=Project.objects.defer_huge(),
            ),
            Prefetch(
                "translation__component__project__workspace",
                queryset=Workspace.objects.defer_huge(),
            ),
        )

    included = set()
    for unit in units:
        # Skip forbidden term
        if "forbidden" in unit.all_flags:
            continue

        if not unit.translated and "read-only" not in unit.all_flags:
            continue

        # Cleanup strings
        source = cleanup_glossary_term(unit.source)
        target = (
            source
            if "read-only" in unit.all_flags
            else cleanup_glossary_term(unit.target)
        )

        # Skip blanks and duplicates
        if not source or not target or source in included:
            continue

        # Memoize included
        included.add(source)

        # Render TSV
        yield source, target


def render_glossary_units_tsv(units: Iterable[Unit]) -> str:
    """Build a tab separated glossary."""
    return "\n".join(
        f"{source}\t{target}" for source, target in get_glossary_tuples(units)
    )


def get_glossary_tsv(translation) -> str:
    project = translation.component.project
    source_language = translation.component.source_language
    language = translation.language

    cache_key = project.get_glossary_tsv_cache_key(source_language, language)

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Get glossary units
    units = get_glossary_units(project, source_language, language)

    # Render as tsv
    result = render_glossary_units_tsv(units.filter(state__gte=STATE_TRANSLATED))

    cache.set(cache_key, result, 24 * 3600)

    return result


def glossary_matcher_fingerprint(
    project: Project, source_language, language
) -> dict[str, object]:
    """
    Capture the matcher configuration a probe or golden-set run depends on.

    Historical measurements are comparable only when every field here
    matches: a change to the Snowball version, either language's algorithm,
    an allowlist, ``LLM_FULL_GLOSSARY_LIMIT``, or the glossary content
    itself can change which terms reach the prompt or fire the check.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.machinery.llm import LLM_FULL_GLOSSARY_LIMIT

    units = list(prepare_glossary_units(project, source_language, language, full=True))
    exact_only_count = 0
    not_applicable_count = 0
    for unit in units:
        modes = get_glossary_term_modes(unit)
        if modes & {"exact", "read-only", "forbidden"}:
            exact_only_count += 1
        if "not-applicable" in modes:
            not_applicable_count += 1
    glossary_digest = hashlib.sha256(
        "\n".join(
            "\x1f".join(
                (
                    unit.source,
                    unit.target,
                    unit.extra_flags,
                    getattr(unit.source_unit, "extra_flags", ""),
                )
            )
            for unit in sorted(units, key=lambda unit: (unit.source, unit.context))
        ).encode()
    ).hexdigest()
    return {
        "snowball_version": get_snowball_version(),
        "source_algorithm": get_algorithm(source_language.code),
        "target_algorithm": get_algorithm(language.code),
        "source_stem_allowlist": sorted(SOURCE_STEM_LANGUAGES),
        "target_morphology_allowlist": sorted(MORPHOLOGY_LANGUAGES),
        "llm_full_glossary_limit": LLM_FULL_GLOSSARY_LIMIT,
        "exact_only_term_count": exact_only_count,
        "not_applicable_term_count": not_applicable_count,
        "glossary_term_count": len(units),
        "glossary_hash": glossary_digest,
    }
