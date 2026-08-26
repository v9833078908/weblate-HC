#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Freeze the en->fr corpus of need-for-greed/ui from the production instance.

The judge-seat pair search needs a second language pair, and the only existing
French golden set belongs to a different project and a different source language
(`col4/data`, ru->fr). This dumps the live component once, so every later stage
reads a committed file rather than the instance - the corpus has to stay fixed
while models are compared against each other.

Read-only. Writes two artifacts:

    analysis/data/nfg-ui-fr-units.jsonl      one row per unit
    analysis/data/nfg-ui-fr-glossary.json    the project glossary for fr

Each unit row carries what a JudgeRequest needs (`weblate/trans/judge_loop.py:53-65`):
context, source, target, note/explanation, the state, and whether the unit
currently fails a deterministic check. That last flag comes from the search
filter rather than the unit's own `checks` array, which this deployment returns
empty even for units that do fail.

Usage:
    PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/nfg-ui-fr-dump.py
"""

from __future__ import annotations

import json
import operator
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://l10n.herocraft.com/api"
PROJECT = "need-for-greed"
COMPONENT = "ui"
LANGUAGE = "fr"

TOKEN = os.environ.get("PROD_WEBLATE_API_TOKEN", "").strip()
if not TOKEN:
    sys.exit("PROD_WEBLATE_API_TOKEN is required")

ROOT = Path(__file__).resolve().parents[2]
OUT_UNITS = ROOT / "analysis/data/nfg-ui-fr-units.jsonl"
OUT_GLOSSARY = ROOT / "analysis/data/nfg-ui-fr-glossary.json"


def get(url: str) -> dict:
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage] - fixed https host above
        url, headers={"Authorization": f"Token {TOKEN}"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def paginate(url: str) -> list[dict]:
    """Follow Weblate's `next` links until the last page."""
    rows: list[dict] = []
    while url:
        page = get(url)
        rows.extend(page.get("results", []))
        url = page.get("next") or ""
    return rows


def first(values: list | None) -> str:
    """Unwrap a monolingual unit's source/target, which arrive as lists."""
    if not values:
        return ""
    return values[0] if isinstance(values[0], str) else str(values[0])


def main() -> None:
    units = paginate(
        f"{API}/translations/{PROJECT}/{COMPONENT}/{LANGUAGE}/units/?format=json"
    )
    print(f"units fetched: {len(units)}")

    # This deployment returns an empty `checks` array on the units endpoint even
    # when a unit really does fail checks, so the only reliable source is the
    # search filter. Without this pass, known-suspect strings would silently
    # enter the clean pool and be counted as false positives later.
    flagged = {
        unit["id"]
        for unit in paginate(
            f"{API}/translations/{PROJECT}/{COMPONENT}/{LANGUAGE}/units/"
            f"?q={urllib.parse.quote('has:check')}&format=json"
        )
    }
    print(f"units failing a check (via search): {len(flagged)}")

    rows = [
        {
            "id": unit["id"],
            "context": unit.get("context", ""),
            "source": first(unit.get("source")),
            "target": first(unit.get("target")),
            # Weblate's own note field, plus the source-side explanation the
            # judge prompt actually receives.
            "note": unit.get("note", ""),
            "explanation": unit.get("explanation", ""),
            "state": unit.get("state"),
            "fails_check": unit["id"] in flagged,
            "position": unit.get("position"),
        }
        for unit in units
    ]

    rows.sort(key=lambda row: (row["position"] or 0, row["id"]))
    with OUT_UNITS.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    glossary = paginate(
        f"{API}/translations/{PROJECT}/glossary/{LANGUAGE}/units/?format=json"
    )
    terms = [
        {
            "source": first(term.get("source")),
            "target": first(term.get("target")),
            "explanation": term.get("explanation", ""),
            "flags": term.get("flags", ""),
        }
        for term in glossary
    ]
    terms = [term for term in terms if term["source"]]
    terms.sort(key=operator.itemgetter("source"))
    OUT_GLOSSARY.write_text(
        json.dumps(
            {
                "dataset": f"{PROJECT}/glossary/{LANGUAGE}",
                "terms": terms,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    translated = sum(1 for row in rows if row["state"] and row["state"] >= 20)
    failing = sum(1 for row in rows if row["fails_check"])
    print(f"  translated (state >= 20): {translated}")
    print(f"  units with a failing check: {failing}")
    print(f"  glossary terms with a target: {sum(1 for t in terms if t['target'])}")
    print(f"wrote {OUT_UNITS.relative_to(ROOT)} and {OUT_GLOSSARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
