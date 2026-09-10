#!/usr/bin/env python3
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Join a blind LQA annotation file with the arm key and aggregate M2.

The annotator reads ``--packet`` (variants under labels A/B/C, shuffled per
line, no arm anywhere in the file) and writes findings keyed by
``lang|context|label``. Only here, after the annotations exist, are the
labels mapped back to arms through ``--key``.

Annotation format::

    {"de|hub1_ramen_1_38|A": [{"category": "register", "severity": "major",
                               "note": "Sie against the project's du"}]}

Categories follow the MQM-Core Game profile of ``skill://weblate-lqa``:
``register``, ``grammar_agreement``, ``accuracy``, ``omission_addition``,
``terminology``. Severity is ``minor``, ``major`` or ``critical``.

Usage::

    python3 analysis/probes/mt-scene-context-lqa.py \
      --packet analysis/data/mt-scene-context-2026-09-10/lqa-packet.json \
      --key analysis/data/mt-scene-context-2026-09-10/lqa-key.json \
      --annotations analysis/data/mt-scene-context-2026-09-10/lqa-annotations.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter, defaultdict

SEVERITY_POINTS = {"neutral": 0, "minor": 1, "major": 5, "critical": 25}
CATEGORIES = (
    "register",
    "grammar_agreement",
    "accuracy",
    "omission_addition",
    "terminology",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--json")
    args = parser.parse_args()

    packet = json.loads(pathlib.Path(args.packet).read_text(encoding="utf-8"))
    key_rows = json.loads(pathlib.Path(args.key).read_text(encoding="utf-8"))
    annotations = json.loads(
        pathlib.Path(args.annotations).read_text(encoding="utf-8")
    )

    labels = {
        (row["lang"], row["context"]): row["labels"] for row in key_rows
    }
    reviewed = {(entry["lang"], entry["context"]) for entry in packet}

    per_arm: dict[tuple[str, str], Counter] = defaultdict(Counter)
    points: dict[tuple[str, str], int] = defaultdict(int)
    findings: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for annotation_key, entries in annotations.items():
        lang, context, label = annotation_key.split("|")
        if (lang, context) not in reviewed:
            msg = f"annotation for a line outside the packet: {annotation_key}"
            raise SystemExit(msg)
        arm = labels[lang, context][label]
        for entry in entries:
            category = entry["category"]
            severity = entry["severity"]
            if category not in CATEGORIES:
                msg = f"unknown category {category}"
                raise SystemExit(msg)
            if severity not in SEVERITY_POINTS:
                msg = f"unknown severity {severity}"
                raise SystemExit(msg)
            per_arm[lang, arm][category] += 1
            per_arm[lang, arm][f"{category}:{severity}"] += 1
            per_arm[lang, arm]["total"] += 1
            points[lang, arm] += SEVERITY_POINTS[severity]
            findings[lang, arm].append({"context": context, **entry})

    langs = sorted({lang for lang, _arm in per_arm})
    header = (
        f"{'scope':10} {'lines':>5} {'reg':>4} {'gram':>5} {'acc':>4} "
        f"{'om/ad':>6} {'term':>5} {'crit':>5} {'total':>6} {'points':>7}"
    )
    print(header)
    report: dict[str, object] = {}
    for lang in langs:
        lines = sum(1 for entry in packet if entry["lang"] == lang)
        for arm in ("P", "S0", "S"):
            counts = per_arm.get((lang, arm), Counter())
            critical = sum(
                value
                for name, value in counts.items()
                if name.endswith(":critical")
            )
            print(
                f"{lang}-{arm:7} {lines:5} {counts['register']:4} "
                f"{counts['grammar_agreement']:5} {counts['accuracy']:4} "
                f"{counts['omission_addition']:6} {counts['terminology']:5} "
                f"{critical:5} {counts['total']:6} {points[lang, arm]:7}"
            )
            report[f"{lang}-{arm}"] = {
                "reviewed_lines": lines,
                "counts": dict(counts),
                "critical": critical,
                "penalty_points": points[lang, arm],
                "findings": findings[lang, arm],
            }

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
