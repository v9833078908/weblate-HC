#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Repeat-drift measurement: one source text, several different translations.

Read-only against a Weblate instance (production by default). Answers the
question the stock `inconsistent` check cannot, because
`Unit.objects.same()` (weblate/trans/models/unit.py:377-392) groups by
`source` AND `context`, and in monolingual game formats `context` is the
string key:

    within one project and one target language, take every group of units
    whose SOURCE text is identical and whose targets are not.

Feeds docs/llm-first/plans/2026-08-14-intra-component-consistency-check.md:

1. group counts per language, split into intra-component and
   cross-component-only drift (the plan's scope decision R1);
2. glossary involvement (glossary-vs-glossary, glossary-vs-regular,
   regular-only), because a glossary term and a UI string sharing a text are
   not the same defect;
3. how many groups survive case-, punctuation- and whitespace-insensitive
   comparison, which is what the plan's noise-suppression flags R3
   (`ignore-case`, `ignore-punctuation`, `ignore-whitespace`) would gate;
4. a fixture dump of the surviving groups for regression tests.

Usage:
    PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/source-repeat-drift.py
    PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/source-repeat-drift.py \
        --project space-arena --api https://l10n.herocraft.com/api
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from operator import itemgetter
from pathlib import Path

DEFAULT_API = "https://l10n.herocraft.com/api"
DEFAULT_PROJECT = "need-for-greed"

ROOT = Path(__file__).resolve().parents[2]

TOKEN = os.environ.get("PROD_WEBLATE_API_TOKEN", "").strip()
if not TOKEN:
    sys.exit("PROD_WEBLATE_API_TOKEN is required")

# Unit states (weblate/utils/state.py): only translated (20), approved (30)
# and read-only (100) carry a target worth comparing. Needs-editing units are
# incompleteness, not drift - the plan's decision R5.
COMPARABLE_STATES = {20, 30, 100}

PUNCTUATION_CATEGORIES = {"Pd", "Pe", "Pf", "Pi", "Po", "Ps", "Sm"}


def get(url: str, api: str) -> dict | list:
    if not url.startswith(api):
        msg = f"refusing to fetch outside {api}: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage] - host checked above
        url, headers={"Authorization": f"Token {TOKEN}"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def paginate(url: str, api: str) -> list[dict]:
    rows: list[dict] = []
    while url:
        page = get(url, api)
        if isinstance(page, list):
            return page
        rows.extend(page.get("results", []))
        url = page.get("next") or ""
    return rows


def first(values: list[str] | None) -> str:
    return values[0] if values else ""


def fold_case(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def fold_whitespace(text: str) -> str:
    return " ".join(fold_case(text).split())


def fold_punctuation(text: str) -> str:
    stripped = "".join(
        char
        for char in fold_whitespace(text)
        if unicodedata.category(char) not in PUNCTUATION_CATEGORIES
    )
    return " ".join(stripped.split())


FOLDS = {
    "strict": lambda text: unicodedata.normalize("NFC", text),
    "ignore-case": fold_case,
    "ignore-whitespace": fold_whitespace,
    "ignore-punctuation": fold_punctuation,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument(
        "--captured-at", default="", help="date stamp for the output path"
    )
    parser.add_argument("--fixtures", type=int, default=25)
    args = parser.parse_args()

    api = args.api.rstrip("/")
    project = args.project

    components = [
        component["slug"]
        for component in paginate(
            f"{api}/projects/{project}/components/?page_size=100", api
        )
    ]
    languages = [
        language["code"]
        for language in paginate(
            f"{api}/projects/{project}/languages/?page_size=100", api
        )
    ]
    source_languages = {
        component["slug"]: component["source_language"]["code"]
        for component in paginate(
            f"{api}/projects/{project}/components/?page_size=100", api
        )
    }
    glossaries = {
        component["slug"]
        for component in paginate(
            f"{api}/projects/{project}/components/?page_size=100", api
        )
        if component["is_glossary"]
    }

    # source text per (component, key), read from each component's own source
    # language, plus every target unit.
    source_of: dict[tuple[str, str], str] = {}
    targets: list[dict] = []
    fetched = 0
    for slug in components:
        for code in languages:
            units = paginate(
                f"{api}/translations/{project}/{slug}/{code}/units/?page_size=1000", api
            )
            fetched += len(units)
            if code == source_languages[slug]:
                for unit in units:
                    source_of[slug, unit["context"]] = first(unit["source"])
                continue
            for unit in units:
                if unit["state"] not in COMPARABLE_STATES:
                    continue
                target = first(unit["target"])
                if not target:
                    continue
                targets.append(
                    {
                        "id": unit["id"],
                        "component": slug,
                        "language": code,
                        "context": unit["context"],
                        "target": target,
                    }
                )

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for unit in targets:
        source = source_of.get((unit["component"], unit["context"]))
        if not source:
            continue
        groups[unit["language"], source].append(unit)

    repeated = {key: members for key, members in groups.items() if len(members) > 1}

    result: dict = {
        "project": project,
        "api": api,
        "captured_at": args.captured_at or "unset",
        "components": components,
        "glossary_components": sorted(glossaries),
        "languages": languages,
        "units_fetched": fetched,
        "target_units_compared": len(targets),
        "repeated_source_groups": len(repeated),
        "folds": {},
    }

    fixtures: list[dict] = []
    for fold_name, fold in FOLDS.items():
        diverging = {
            key: members
            for key, members in repeated.items()
            if len({fold(member["target"]) for member in members}) > 1
        }
        per_language: dict[str, int] = defaultdict(int)
        scope = {"intra_component": 0, "cross_component_only": 0}
        glossary_split = {
            "glossary_only": 0,
            "glossary_and_regular": 0,
            "regular_only": 0,
        }
        for (language, _source), members in diverging.items():
            per_language[language] += 1
            per_component: dict[str, set[str]] = defaultdict(set)
            for member in members:
                per_component[member["component"]].add(fold(member["target"]))
            if any(len(values) > 1 for values in per_component.values()):
                scope["intra_component"] += 1
            else:
                scope["cross_component_only"] += 1
            slugs = {member["component"] for member in members}
            if slugs <= glossaries:
                glossary_split["glossary_only"] += 1
            elif slugs & glossaries:
                glossary_split["glossary_and_regular"] += 1
            else:
                glossary_split["regular_only"] += 1

        result["folds"][fold_name] = {
            "diverging_groups": len(diverging),
            "distinct_sources": len({source for _language, source in diverging}),
            "per_language": dict(sorted(per_language.items(), key=lambda row: -row[1])),
            "scope": scope,
            "glossary": glossary_split,
        }

        if fold_name == "ignore-punctuation":
            # The hardest fold: what survives here is drift no cosmetic flag
            # can explain away, which is what a regression fixture needs.
            ranked = sorted(
                diverging.items(),
                key=lambda row: (-len({m["target"] for m in row[1]}), row[0]),
            )
            for (language, source), members in ranked[: args.fixtures]:
                fixtures.append(
                    {
                        "language": language,
                        "source": source,
                        "members": sorted(
                            (
                                {
                                    "component": member["component"],
                                    "context": member["context"],
                                    "target": member["target"],
                                    "unit": member["id"],
                                }
                                for member in members
                            ),
                            key=itemgetter("component", "context"),
                        ),
                    }
                )

    result["fixtures_ignore_punctuation"] = fixtures

    stamp = args.captured_at or "latest"
    out = ROOT / f"analysis/data/source-repeat-drift-{stamp}/{project}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"project {project}: {fetched} units fetched, {len(targets)} targets compared"
    )
    print(f"repeated-source groups (language x source, >1 unit): {len(repeated)}")
    for fold_name, summary in result["folds"].items():
        print(
            f"  {fold_name:20s} diverging={summary['diverging_groups']:4d} "
            f"sources={summary['distinct_sources']:4d} "
            f"intra={summary['scope']['intra_component']:4d} "
            f"cross={summary['scope']['cross_component_only']:4d} "
            f"glossary_only={summary['glossary']['glossary_only']:4d} "
            f"gl+reg={summary['glossary']['glossary_and_regular']:4d} "
            f"regular={summary['glossary']['regular_only']:4d}"
        )
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
