# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Задача 5: measure the shipped matcher and check on every real ru-source cohort.

Runs the product code, never a local copy of the rules:

* source side - ``fetch_glossary_terms`` twice, once with the stem fallback
  disabled, so the difference is exactly what the fallback recovers;
* target side - ``evaluate_glossary_terms``, splitting hard from advisory and
  counting how many terms the morphological comparison lifted;
* ``glossary_matcher_fingerprint`` per cohort, because a measurement is only
  comparable against a matching fingerprint.

Read-only. Pipe into ``weblate shell``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import weblate.glossary.models as glossary_models
from weblate.checks.glossary import evaluate_glossary_terms
from weblate.checks.morphology import (
    SOURCE_STEM_LANGUAGES,
    get_text_stems,
    iter_word_spans,
)
from weblate.glossary.models import (
    cleanup_glossary_term,
    fetch_glossary_terms,
    get_glossary_term_modes,
    glossary_matcher_fingerprint,
)
from weblate.trans.models import Component
from weblate.utils.state import STATE_TRANSLATED

CHUNK = 200
SAMPLE_LIMIT = 6


def inflected_surfaces(term: str, text: str, language_code: str) -> list[str]:
    """
    Return the words of the text that matched the term's stem sequence.

    This is what makes a lift auditable: the stem comparison accepted the
    string, so the question is whether the accepted word really is a form of
    the term or a different word sharing its stem.
    """
    needle = get_text_stems(term, language_code)
    if not needle:
        return []
    spans = list(iter_word_spans(text))
    haystack = get_text_stems(text, language_code)
    found = []
    for index in range(len(haystack) - len(needle) + 1):
        if haystack[index : index + len(needle)] == needle:
            start = spans[index][1]
            end = spans[index + len(needle) - 1][2]
            found.append(text[start:end])
    return found


def cohorts():
    """Yield translations whose component and glossary stem on the source side."""
    for component in Component.objects.filter(is_glossary=False).select_related(
        "project", "source_language"
    ):
        if component.source_language.base_code not in SOURCE_STEM_LANGUAGES:
            continue
        glossaries = [
            glossary
            for glossary in component.project.glossaries
            if glossary.source_language_id == component.source_language_id
        ]
        if not glossaries:
            continue
        for translation in component.translation_set.select_related("language"):
            if translation.is_source:
                continue
            yield component, translation


def matched(units):
    """Return term sources the matcher finds per unit, using the product path."""
    for unit in units:
        unit.glossary_terms = None
    result = {}
    for start in range(0, len(units), CHUNK):
        chunk = units[start : start + CHUNK]
        fetch_glossary_terms(chunk, include_variants=False)
        for unit in chunk:
            result[unit.pk] = {term.source for term in unit.glossary_terms}
    return result


def with_stem(units, *, enabled: bool):
    original = glossary_models.SOURCE_STEM_LANGUAGES
    glossary_models.SOURCE_STEM_LANGUAGES = (
        SOURCE_STEM_LANGUAGES if enabled else frozenset()
    )
    glossary_models.GLOSSARY_STEM_CACHE.clear()
    try:
        return matched(units)
    finally:
        glossary_models.SOURCE_STEM_LANGUAGES = original
        glossary_models.GLOSSARY_STEM_CACHE.clear()


totals = defaultdict(int)
hard_samples = []
recovered_samples = []
lifted_samples = []
lifted_pairs: Counter = Counter()
recovered_pairs: Counter = Counter()
fingerprints = {}

for component, translation in cohorts():
    units = list(
        translation.unit_set.filter(state__gte=STATE_TRANSLATED).select_related(
            "translation__component__source_language",
            "translation__language",
            "source_unit",
        )
    )
    if not units:
        continue
    label = f"{component.project.slug}/{component.slug}/{translation.language.code}"

    baseline = with_stem(units, enabled=False)
    stemmed = with_stem(units, enabled=True)

    recovered_units = 0
    recovered_terms = 0
    for unit in units:
        extra = stemmed[unit.pk] - baseline[unit.pk]
        if extra:
            recovered_units += 1
            recovered_terms += len(extra)
            for term_source in extra:
                for surface in inflected_surfaces(
                    term_source, unit.source, component.source_language.code
                ):
                    recovered_pairs[
                        term_source,
                        surface.lower(),
                        term_source[:1].isupper(),
                        surface[:1].isupper(),
                        term_source.isupper() and len(term_source) <= 5,
                    ] += 1
            if len(recovered_samples) < SAMPLE_LIMIT:
                recovered_samples.append((label, sorted(extra), unit.source[:110]))

    # Target side runs on the shipped configuration, i.e. stem fallback on.
    language = translation.language
    boundary = r"\b" if language.uses_whitespace() else ""
    hard_n = advisory_n = lifted_n = 0
    for unit in units:
        source = unit.get_source_plurals()[0]
        target = unit.get_target_plurals()[0]
        hard, advisory = evaluate_glossary_terms(unit, source, target)
        advisory -= hard
        hard_n += len(hard)
        advisory_n += len(advisory)
        for term_source in sorted(hard):
            if len(hard_samples) < SAMPLE_LIMIT * 4:
                hard_samples.append((label, term_source, source[:80], target[:80]))
        # A term neither hard nor advisory whose exact expected form is absent
        # was accepted by the morphological comparison alone.
        for term in unit.glossary_terms or []:
            name = term.source
            if name in hard or name in advisory:
                continue
            modes = get_glossary_term_modes(term)
            if modes & {"not-applicable", "forbidden"}:
                continue
            expected = name if "read-only" in modes else term.target
            expected = cleanup_glossary_term(expected)
            if not expected:
                continue
            pattern = rf"{boundary}{re.escape(expected)}{boundary}"
            if not re.search(pattern, target, re.IGNORECASE):
                lifted_n += 1
                for surface in inflected_surfaces(expected, target, language.code):
                    lifted_pairs[language.code, expected, surface.lower()] += 1
                if len(lifted_samples) < SAMPLE_LIMIT:
                    lifted_samples.append((label, name, expected, target[:90]))

    print(
        f"{label:52s} units={len(units):6d} "
        f"recovered_units={recovered_units:5d} recovered_terms={recovered_terms:5d} "
        f"hard={hard_n:4d} advisory={advisory_n:5d} lifted={lifted_n:5d}"
    )
    totals["units"] += len(units)
    totals["recovered_units"] += recovered_units
    totals["recovered_terms"] += recovered_terms
    totals["hard"] += hard_n
    totals["advisory"] += advisory_n
    totals["lifted"] += lifted_n

    key = (component.project.slug, component.source_language.code, language.code)
    if key not in fingerprints:
        fingerprints[key] = glossary_matcher_fingerprint(
            component.project, component.source_language, language
        )

print()
print("TOTALS", dict(totals))
print()
print("=== FINGERPRINTS ===")
for key, value in sorted(fingerprints.items()):
    print(
        f"{'/'.join(key)}: snowball={value['snowball_version']} "
        f"src_alg={value['source_algorithm']} tgt_alg={value['target_algorithm']} "
        f"terms={value['glossary_term_count']} exact_only={value['exact_only_term_count']} "
        f"n/a={value['not_applicable_term_count']} hash={str(value['glossary_hash'])[:12]}"
    )
print()
print("=== ALL HARD (go criterion: every one must be a real defect) ===")
for label, term_source, source, target in hard_samples:
    print(f"  [{label}] term={term_source!r}")
    print(f"     SRC {source!r}")
    print(f"     TGT {target!r}")
print()
print("=== SOURCE-SIDE: every distinct (glossary term -> matched word) pair ===")
cap_kept = cap_lost = acronym_hits = 0
for key, count in recovered_pairs.most_common():
    term_source, surface, term_cap, surface_cap, term_is_acronym = key
    marks = []
    if term_is_acronym:
        marks.append("ACRONYM")
        acronym_hits += count
    elif term_cap and not surface_cap:
        marks.append("CAP-MISMATCH")
        cap_lost += count
    elif term_cap:
        cap_kept += count
    print(f"  {term_source!r} -> {surface!r} x{count} {' '.join(marks)}")
print()
print(
    f"CAP SIGNAL: capitalized term + capitalized surface = {cap_kept}, "
    f"capitalized term + lowercase surface = {cap_lost}, "
    f"acronym matches = {acronym_hits}"
)
print()
print("=== SOURCE-SIDE RECOVERED BY STEM (check for false matches) ===")
for label, terms, source in recovered_samples:
    print(f"  [{label}] {terms} <- {source!r}")
print()
print("=== LIFTED: every distinct (expected term -> accepted word) pair ===")
print("A pair whose accepted word is not a form of the term is a wrong lift.")
for (lang, expected, surface), count in lifted_pairs.most_common():
    flag = (
        ""
        if surface.startswith(expected.lower()[: max(3, len(expected) - 2)])
        else "  <-- INSPECT"
    )
    print(f"  {lang} {expected!r} -> {surface!r} x{count}{flag}")
print()
print("=== LIFTED BY MORPHOLOGY (check none is a real miss) ===")
for label, name, expected, target in lifted_samples:
    print(f"  [{label}] term={name!r} expected={expected!r}")
    print(f"     TGT {target!r}")
