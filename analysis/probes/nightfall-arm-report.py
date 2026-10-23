#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Turn a nightfall-arm-compare dump into the study's comparison tables.

Read the JSON dump and print the study tables.

Usage:
    uv run python analysis/probes/nightfall-arm-report.py \
        analysis/data/2026-09-30-nightfall-arm-dump.json

Reads the JSON the in-container probe prints and reports, per arm and language:
the judge severity distribution, the paired per-key comparison (McNemar-style
counts of keys where only one arm is critical/major) and the layer-0 check
inventory. Layer-0 counts come from the dump itself (the probe records each
unit's active checks).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

SEVERITIES = ("none", "minor", "major", "critical")
BLOCKING = {"major", "critical"}


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def severity_rows(rows: list[dict]) -> None:
    print("## Judge severity per arm/language (sample)\n")
    print("| Arm | Lang | judged | none | minor | major | critical | blocking /100 |")
    print("|---|---|---|---|---|---|---|---|")
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("missing"):
            continue
        grouped[row["arm"], row["language"]].append(row)
    for (arm, language), items in sorted(grouped.items()):
        counts = Counter(item["severity"] or "unjudged" for item in items)
        judged = sum(
            count for severity, count in counts.items() if severity != "unjudged"
        )
        blocking = counts["major"] + counts["critical"]
        rate = 100 * blocking / judged if judged else 0
        print(
            f"| {arm} | {language} | {judged} | {counts['none']} | {counts['minor']} | "
            f"{counts['major']} | {counts['critical']} | {rate:.1f} |"
        )
    print()


def paired_rows(rows: list[dict]) -> None:
    print("## Paired per-key comparison (arm A vs arm B)\n")
    pairs: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row.get("missing"):
            continue
        pairs[row["language"], row["key"]][row["arm"]] = row
    print(
        "| Lang | keys | both clean | only A blocking | only B blocking | both blocking | unjudged |"
    )
    print("|---|---|---|---|---|---|---|")
    for language in sorted({key[0] for key in pairs}):
        only_a = only_b = both = clean = unjudged = 0
        for (lang, _key), arms in pairs.items():
            if lang != language or "A" not in arms or "B" not in arms:
                continue
            sev_a = arms["A"]["severity"]
            sev_b = arms["B"]["severity"]
            if sev_a is None or sev_b is None:
                unjudged += 1
            elif sev_a in BLOCKING and sev_b in BLOCKING:
                both += 1
            elif sev_a in BLOCKING:
                only_a += 1
            elif sev_b in BLOCKING:
                only_b += 1
            else:
                clean += 1
        total = only_a + only_b + both + clean + unjudged
        print(
            f"| {language} | {total} | {clean} | {only_a} | {only_b} | {both} | {unjudged} |"
        )
    print()


def check_rows(rows: list[dict]) -> None:
    print("## Layer-0 checks by arm/language (sample of 300 keys)\n")
    print("| Arm | Lang | units with checks | checks (top) |")
    print("|---|---|---|---|")
    grouped: dict[tuple[str, str], Counter] = defaultdict(Counter)
    with_checks: Counter = Counter()
    for row in rows:
        if row.get("missing"):
            continue
        key = (row["arm"], row["language"])
        for check in row["checks"]:
            grouped[key][check] += 1
        if row["checks"]:
            with_checks[key] += 1
    for key in sorted(grouped):
        top = ", ".join(
            f"{name}:{count}" for name, count in grouped[key].most_common(8)
        )
        print(f"| {key[0]} | {key[1]} | {with_checks[key]} | {top or '—'} |")
    print()


def error_rows(rows: list[dict]) -> None:
    print("## Judge error categories (blocking rows only)\n")
    print("| Arm | Lang | category | count | example key |")
    print("|---|---|---|---|---|")
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in rows:
        if row.get("missing") or (row["severity"] not in BLOCKING):
            continue
        for error in row["errors"]:
            if isinstance(error, dict):
                category = error.get("category") or "?"
                grouped[row["arm"], row["language"], category].append(row["key"])
    for key in sorted(grouped):
        examples = grouped[key]
        print(f"| {key[0]} | {key[1]} | {key[2]} | {len(examples)} | `{examples[0]}` |")
    print()


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    rows = load(sys.argv[1])
    print(f"# Nightfall Spire arm comparison — {len(rows)} dumped rows\n")
    severity_rows(rows)
    paired_rows(rows)
    check_rows(rows)
    error_rows(rows)


if __name__ == "__main__":
    main()
