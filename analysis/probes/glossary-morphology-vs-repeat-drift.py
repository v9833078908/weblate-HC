#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Measure whether Snowball glossary morphology and ``repeat-drift`` collide.

The two features answer different questions on the same string:

* ``GlossaryCheck`` (``weblate/checks/glossary.py``) asks whether every matched
  glossary term is rendered in the target, accepting a grammatical inflection
  through ``weblate/checks/morphology.py`` and comparing case-insensitively;
* ``RepeatDriftCheck`` (``weblate/checks/consistency.py:299``) asks whether two
  units with a byte-identical source carry a byte-identical target, project
  wide, per language.

This probe reports, on a real project, how often the two overlap, whether the
byte-equality ``repeat-drift`` demands can be satisfied without turning a
glossary-green target into an advisory, and how much of the glossary matching
depends on the Snowball path at all.

Read-only against a Weblate instance (production by default). No writes, no
machinery, no LLM calls, no cost.

The stem comparison is imported from ``weblate.checks.morphology`` (Django-free)
so it cannot drift from the product. Two rules are re-derived instead of
imported because they need a live Django ORM:

* the source-side exact matcher of ``weblate/glossary/models.py:506-536`` -
  the lowercased term with a non-word character or an edge on both sides;
* the target-side evaluation of ``weblate/checks/glossary.py:52-99``, including
  the ``exact``/``read-only``/``forbidden``/``not-applicable`` modes and the
  occurrence-count comparison.

Variants (``include_variants``) are out of scope: the check itself matches with
``include_variants=False``.

Usage:
    PROD_WEBLATE_API_TOKEN=... uv run python \
        analysis/probes/glossary-morphology-vs-repeat-drift.py --project col4 \
        --captured-at 2026-09-04
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from weblate.checks.morphology import (
    SOURCE_STEM_LANGUAGES,
    count_inflected,
    get_algorithm,
    get_snowball_version,
    is_acronym,
)

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_API = "https://l10n.herocraft.com/api"
DEFAULT_PROJECT = "col4"

TOKEN = os.environ.get("PROD_WEBLATE_API_TOKEN", "").strip()
if not TOKEN:
    sys.exit("PROD_WEBLATE_API_TOKEN is required")

# weblate/checks/consistency.py:379-380 - translated (20) and approved (30);
# read-only (100) is excluded by the check itself.
COMPARABLE_STATES = range(20, 100)
# weblate/glossary/models.py:46
NON_WORD_RE = re.compile(r"\W")
# weblate/glossary/models.py:65 - the modes that switch a term to exact-only
EXACT_ONLY_MODES = frozenset({"exact", "read-only", "forbidden"})
PLURAL_SEPARATOR = "\x1e"
# An all-caps Cyrillic word, the form a game shouts a term in.
CAPS_WORD_RE = re.compile(r"\b[А-ЯЁ]{4,}\b")


def get(url: str) -> dict:
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers={"Authorization": f"Token {TOKEN}", "User-Agent": "drift-probe"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def paginate(url: str) -> list[dict]:
    rows: list[dict] = []
    while url:
        payload = get(url)
        rows.extend(payload["results"])
        url = payload.get("next") or ""
    return rows


def joined(values: list[str] | None) -> str:
    return PLURAL_SEPARATOR.join(values or [])


def modes_of(unit: dict) -> set[str]:
    """Glossary modes of a target term unit (weblate/glossary/models.py:173)."""
    flags = {flag.strip() for flag in (unit.get("extra_flags") or "").split(",")}
    return flags & (EXACT_ONLY_MODES | {"not-applicable"})


def exact_source_hit(term: str, source: str) -> bool:
    """Whole-word case-insensitive hit, the automaton rule of the matcher."""
    lowered = source.lower()
    needle = term.lower()
    boundaries = {match.start() for match in NON_WORD_RE.finditer(lowered)}
    boundaries |= {-1, len(lowered)}
    start = lowered.find(needle)
    while start != -1:
        if (start - 1) in boundaries and (start + len(needle)) in boundaries:
            return True
        start = lowered.find(needle, start + 1)
    return False


class Glossary:
    """One project glossary, as the matcher and the check see it."""

    def __init__(self, terms: dict[str, dict[str, dict]], source_code: str) -> None:
        # terms: term source -> language code -> {"target", "modes"}
        self.terms = terms
        self.source_code = source_code
        self.stems_enabled = (
            source_code.split("-", maxsplit=1)[0] in SOURCE_STEM_LANGUAGES
        )

    def match(self, source: str, language: str) -> dict[str, dict]:
        """Return the terms the source-side matcher attaches to this source."""
        matched: dict[str, dict] = {}
        for term, by_language in self.terms.items():
            entry = by_language.get(language)
            if entry is None or "not-applicable" in entry["modes"]:
                continue
            if exact_source_hit(term, source):
                matched[term] = {**entry, "path": "exact"}
                continue
            # The stem index excludes exact-only terms and acronyms
            # (weblate/glossary/models.py:229-235).
            if not self.stems_enabled or entry["modes"] & EXACT_ONLY_MODES:
                continue
            if is_acronym(term):
                continue
            if count_inflected(term, source, self.source_code) > 0:
                matched[term] = {**entry, "path": "stem"}
        return matched

    def evaluate(self, source: str, target: str, language: str) -> dict[str, str]:
        """Mirror evaluate_glossary_terms(): term source -> outcome."""
        outcome: dict[str, str] = {}
        for term, entry in self.match(source, language).items():
            modes = entry["modes"]
            expected = term if "read-only" in modes else entry["target"]
            pattern = rf"\b{re.escape(expected)}\b"
            if "forbidden" in modes:
                outcome[term] = (
                    "hard-forbidden"
                    if re.search(pattern, target, re.IGNORECASE)
                    else "ok-absent"
                )
                continue
            # No truthiness guard on `expected`: the product does not have one
            # either, so an untranslated term is a zero-width `\b\b` match and
            # passes vacuously. Reported as `vacuous-untranslated-term`.
            if re.search(pattern, target, re.IGNORECASE):
                outcome[term] = (
                    "exact-ignorecase" if expected else "vacuous-untranslated-term"
                )
                continue
            if modes & EXACT_ONLY_MODES:
                outcome[term] = "hard-missing"
                continue
            source_count = len(
                re.findall(rf"\b{re.escape(term)}\b", source, re.IGNORECASE)
            )
            if self.stems_enabled:
                source_count = max(
                    source_count, count_inflected(term, source, self.source_code)
                )
            if count_inflected(expected, target, language) >= max(source_count, 1):
                outcome[term] = "morphology"
            else:
                outcome[term] = "advisory"
        return outcome


def load_glossary(api: str, project: str) -> tuple[Glossary, dict]:
    coverage: dict = {}
    terms: dict[str, dict[str, dict]] = defaultdict(dict)
    source_code = ""
    for component in paginate(f"{api}/projects/{project}/components/?page_size=100"):
        if not component["is_glossary"]:
            continue
        source_code = component["source_language"]["code"]
        for translation in paginate(component["translations_url"] + "?page_size=100"):
            # Language.code, not translation["language_code"]: the latter is
            # the filename-derived code (`tr_TR.po` -> `tr_TR`), while the
            # product matches glossary units on translation__language
            # (weblate/glossary/models.py:311-316).
            language = translation["language"]["code"]
            if language == source_code:
                continue
            units = paginate(translation["units_list_url"] + "?page_size=1000")
            coverage[language] = {
                "component": component["slug"],
                "terms": len(units),
                "translated_terms": sum(1 for unit in units if joined(unit["target"])),
            }
            for unit in units:
                terms[joined(unit["source"])][language] = {
                    "target": joined(unit["target"]),
                    "modes": modes_of(unit),
                }
    return Glossary(dict(terms), source_code), coverage


def load_units(api: str, project: str) -> tuple[list[dict], dict]:
    """Return comparable target units plus the component inventory."""
    units: list[dict] = []
    components: dict = {}
    for component in paginate(f"{api}/projects/{project}/components/?page_size=100"):
        components[component["slug"]] = {
            "is_glossary": component["is_glossary"],
            "source_language": component["source_language"]["code"],
            "propagation": component["allow_translation_propagation"],
            "check_flags": component["check_flags"],
        }
        if component["is_glossary"] or not component["allow_translation_propagation"]:
            # Both are excluded from the repeat group
            # (weblate/trans/models/unit.py:2626-2642).
            continue
        for translation in paginate(component["translations_url"] + "?page_size=100"):
            language = translation["language"]["code"]
            if language == component["source_language"]["code"]:
                continue
            for unit in paginate(translation["units_list_url"] + "?page_size=1000"):
                if unit["state"] not in COMPARABLE_STATES:
                    continue
                units.append(
                    {
                        "id": unit["id"],
                        "component": component["slug"],
                        "language": language,
                        "context": unit["context"],
                        "source": joined(unit["source"]),
                        "target": joined(unit["target"]),
                        "flags": unit["extra_flags"],
                    }
                )
    return units, components


def case_class(word: str) -> str:
    if word.isupper():
        return "caps"
    return "title" if word[:1].isupper() else "lower"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--captured-at", default="")
    parser.add_argument(
        "--term",
        default="ГИГАХРУЩ",
        help="term to report the source-form and target-case breakdown for",
    )
    parser.add_argument(
        "--case-fallback",
        default="en",
        help="glossary language supplying the expected rendering when the "
        "focus term is untranslated in a language of its own",
    )
    args = parser.parse_args()
    api = args.api.rstrip("/")

    glossary, coverage = load_glossary(api, args.project)
    units, components = load_units(api, args.project)

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for unit in units:
        groups[unit["language"], unit["source"]].append(unit)
    repeated = {key: members for key, members in groups.items() if len(members) > 1}
    diverging = {
        key: members
        for key, members in repeated.items()
        if len({member["target"] for member in members}) > 1
    }

    interaction: dict[str, Counter] = defaultdict(Counter)
    case_only: list[dict] = []
    hard_groups: list[dict] = []
    for (language, source), members in sorted(diverging.items()):
        stats = interaction[language]
        stats["diverging_groups"] += 1
        stats["flagged_units"] += len(members)
        if len({member["target"].casefold() for member in members}) == 1:
            stats["case_only_divergence"] += 1
            case_only.append(
                {
                    "language": language,
                    "source": source,
                    "targets": sorted({member["target"] for member in members}),
                    "terms": glossary.evaluate(source, members[0]["target"], language),
                }
            )
        # Counted before the coverage gate: an all-caps term in the source is
        # a property of the source, so a language without a glossary must not
        # drop out of this row.
        if CAPS_WORD_RE.search(source) and any(
            word.lower() == term.lower()
            or (
                not is_acronym(term)
                and count_inflected(term, word, glossary.source_code) > 0
            )
            for word in CAPS_WORD_RE.findall(source)
            for term in glossary.terms
        ):
            stats["source_carries_an_all_caps_term"] += 1
        if not glossary.terms:
            continue
        if language not in coverage:
            stats["no_glossary_for_this_language"] += 1
            continue
        candidates = sorted({member["target"] for member in members})
        verdicts = {
            candidate: glossary.evaluate(source, candidate, language)
            for candidate in candidates
        }
        if glossary.match(source, language):
            stats["source_carries_a_term"] += 1
        if any(
            outcome == "vacuous-untranslated-term"
            for candidate in candidates
            for outcome in verdicts[candidate].values()
        ):
            stats["term_passes_only_because_it_is_untranslated"] += 1
        if any(
            outcome == "advisory"
            for candidate in candidates
            for outcome in verdicts[candidate].values()
        ):
            stats["glossary_advisory_on_some_member"] += 1
        else:
            stats["glossary_green_on_every_member"] += 1
        green = [
            candidate
            for candidate in candidates
            if "advisory" not in verdicts[candidate].values()
            and "hard-missing" not in verdicts[candidate].values()
            and "hard-forbidden" not in verdicts[candidate].values()
        ]
        if green:
            stats["conflict_free_canonical_candidate_exists"] += 1
        else:
            stats["every_candidate_fails_the_glossary"] += 1
            hard_groups.append(
                {
                    "language": language,
                    "source": source,
                    "candidates": {
                        candidate: verdicts[candidate] for candidate in candidates
                    },
                }
            )

    # How much of the matching depends on Snowball at all, and how the term
    # under focus behaves.
    paths: dict[str, Counter] = defaultdict(Counter)
    per_term: dict[str, Counter] = defaultdict(Counter)
    term_focus: dict[str, dict] = {}
    focus = args.term
    focus_rows: dict[str, Counter] = defaultdict(Counter)
    focus_forms: Counter = Counter()
    focus_case: dict[tuple[str, str], Counter] = defaultdict(Counter)
    focus_target = {
        language: entry["target"]
        for language, entry in glossary.terms.get(focus, {}).items()
    }
    focus_re = (
        re.compile(rf"{re.escape(focus[:5])}\w*", re.IGNORECASE) if focus else None
    )
    for unit in units:
        language = unit["language"]
        if language not in coverage:
            continue
        for term in glossary.terms:
            entry = glossary.terms[term].get(language)
            if entry is None or "not-applicable" in entry["modes"]:
                continue
            if exact_source_hit(term, unit["source"]):
                paths[language]["exact"] += 1
                per_term[term]["exact"] += 1
            elif (
                glossary.stems_enabled
                and not entry["modes"] & EXACT_ONLY_MODES
                and not is_acronym(term)
                and count_inflected(term, unit["source"], glossary.source_code) > 0
            ):
                paths[language]["stem_only"] += 1
                per_term[term]["stem_only"] += 1
        if focus_re is None:
            continue
        forms = focus_re.findall(unit["source"])
        if not forms:
            continue
        outcome = glossary.evaluate(unit["source"], unit["target"], language)
        focus_rows[language][outcome.get(focus, "term-not-matched")] += 1
        focus_forms.update(forms)
        # A language whose own glossary entry is untranslated has no expected
        # rendering to compare a case against. The fallback is a probe-only
        # convenience for that case, never product behaviour.
        expected = focus_target.get(language) or focus_target.get(
            args.case_fallback, ""
        )
        if expected:
            rendered = re.findall(
                rf"\b[\w'’-]*{re.escape(expected[:6])}[\w'’-]*",
                unit["target"],
                re.IGNORECASE,
            )
            bucket = (
                "caps-source" if any(f.isupper() for f in forms) else "other-source"
            )
            for word in rendered:
                focus_case[language, bucket][case_class(word)] += 1
    if focus_re is not None:
        term_focus = {
            "term": focus,
            "glossary_target": focus_target,
            "source_forms": dict(focus_forms.most_common()),
            "outcome_per_language": {
                language: dict(counter) for language, counter in focus_rows.items()
            },
            "case_fallback_language": args.case_fallback,
            "target_case_by_source_case": {
                f"{language}/{bucket}": dict(counter)
                for (language, bucket), counter in sorted(focus_case.items())
            },
        }

    result = {
        "project": args.project,
        "api": api,
        "captured_at": args.captured_at or "unset",
        "snowball_version": get_snowball_version(),
        "source_language": glossary.source_code,
        "source_stemming_enabled": glossary.stems_enabled,
        "target_algorithms": {
            language: get_algorithm(language) for language in sorted(coverage)
        },
        "components": components,
        "glossary_language_coverage": coverage,
        "glossary_terms": len(glossary.terms),
        "units_compared": len(units),
        "repeated_source_groups": len(repeated),
        "diverging_groups": len(diverging),
        "flagged_units": sum(len(members) for members in diverging.values()),
        "interaction": {
            language: dict(counter) for language, counter in sorted(interaction.items())
        },
        "source_match_paths": {
            language: dict(counter) for language, counter in sorted(paths.items())
        },
        # Summed over every glossary-covered target language, so a project
        # with n covered languages counts each source occurrence n times.
        "terms_most_dependent_on_snowball_all_languages": {
            term: dict(counter)
            for term, counter in sorted(
                per_term.items(), key=lambda row: -row[1]["stem_only"]
            )[:15]
        },
        "term_focus": term_focus,
        "case_only_divergence_groups": case_only,
        "groups_no_candidate_satisfies": hard_groups,
    }

    stamp = args.captured_at or "latest"
    out = ROOT / f"analysis/data/glossary-drift-interaction-{stamp}/{args.project}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{args.project}: {len(units)} comparable target units")
    print(
        f"repeat groups {len(repeated)}, diverging {len(diverging)}, "
        f"flagged units {result['flagged_units']}"
    )
    for language, counter in result["interaction"].items():
        print(f"  {language:8s} {dict(counter)}")
    print(f"source match paths: {result['source_match_paths']}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
