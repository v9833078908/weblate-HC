#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Offline secondary-metric calculator for the judge A/B experiments (iteration-2
# task 4/5). The decisive gate stays the runner's conservative aggregation
# ("an unparsed seat means no pair coverage"); this script recomputes the same
# quality metrics under the production collegium semantics
# (weblate.trans.models.judge.collegium_verdict / collegium_severity with
# JUDGE_CONSENSUS_REJECT=True): an unparsed seat is not an opinion, the round
# reads from its parsed seats, a disputed critical reads as major, and only an
# all-unparsed round is unparsed.
#
# Usage: python3 collegium-metrics.py <experiment-manifest.json>
# No Django, no DB, no network: it reads the saved block files only.

from __future__ import annotations

import json
import pathlib
import sys

RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}


def collegium_round(seats: dict[int, dict]) -> str:
    """Production round severity: strictest parsed seat, disputed critical -> major."""
    parsed = [row for row in seats.values() if not row["unparsed"]]
    if not parsed:
        return "unparsed"
    strictest = max(RANK[row["max_severity"]] for row in parsed)
    if (
        strictest == RANK["critical"]
        and any(row["max_severity"] != "critical" for row in parsed)
    ):
        return "major"
    return ["none", "minor", "major", "critical"][strictest]


def conservative_round(seats: dict[int, dict]) -> str:
    if any(row["unparsed"] for row in seats.values()):
        return "unparsed"
    return max(
        (row["max_severity"] for row in seats.values()),
        key=lambda severity: RANK.get(severity, 0),
    )


def metrics(observations: list[dict], labels: dict[int, str], severities: dict[int, str]) -> dict:
    """One observation = (unit, round severity) under one aggregation."""
    recall_hits = recall_total = 0
    critical_misses = 0
    false_flags = false_critical = false_minors = 0
    unparsed = 0
    per_language: dict[str, dict] = {}
    for obs in observations:
        unit_id, severity, language = obs["unit"], obs["severity"], obs["language"]
        bucket = per_language.setdefault(
            language, {"defects": 0, "recalled": 0, "clean": 0, "flagged": 0, "unparsed": 0}
        )
        rank = 0 if severity == "unparsed" else RANK.get(severity, 0)
        if severity == "unparsed":
            unparsed += 1
            bucket["unparsed"] += 1
        if labels.get(unit_id) == "defect":
            bucket["defects"] += 1
            need = RANK.get(severities.get(unit_id, "major"), 2)
            if rank >= need:
                recall_hits += 1
                recall_total += 1
                bucket["recalled"] += 1
            else:
                recall_total += 1
                if need == RANK["critical"]:
                    critical_misses += 1
        else:
            bucket["clean"] += 1
            if rank >= 2:
                false_flags += 1
                bucket["flagged"] += 1
                if rank >= 3:
                    false_critical += 1
            elif rank >= 1:
                false_minors += 1
    return {
        "recall": f"{recall_hits}/{recall_total}",
        "critical_misses": critical_misses,
        "false_flags": false_flags,
        "false_critical": false_critical,
        "false_minors": false_minors,
        "unparsed_rounds": unparsed,
        "per_language": dict(sorted(per_language.items())),
    }


def main() -> int:
    manifest_path = pathlib.Path(sys.argv[1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    directory = manifest_path.parent
    corpus_path = pathlib.Path(manifest["corpus"]["path"])
    if not corpus_path.exists():
        corpus_path = manifest_path.parent / "corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    labels = {r["unit_id"]: r["label"] for r in corpus["records"]}
    severities = {r["unit_id"]: r.get("severity") or "major" for r in corpus["records"]}
    languages = {r["unit_id"]: r["language"] for r in corpus["records"]}
    out = {"experiment": manifest["experiment_id"], "arms": {}}
    for arm in sorted(manifest["arms"]):
        blocks = sorted((directory / "results").glob("block-*.json"))
        latest: dict[int, dict[int, dict]] = {}
        all_obs_c: list[dict] = []
        all_obs_k: list[dict] = []
        latest_obs_c: list[dict] = []
        latest_obs_k: list[dict] = []
        for block_path in blocks:
            block = json.loads(block_path.read_text(encoding="utf-8"))
            if block["arm"] != arm:
                continue
            seats_by_unit: dict[int, dict[int, dict]] = {}
            for row in block["verdicts"]:
                seats_by_unit.setdefault(row["unit_id"], {})[row["seat"]] = row
            for unit_id, seats in seats_by_unit.items():
                for seat in (1, 2):
                    # A seat row missing from the block is not coverage either.
                    seats.setdefault(seat, {"unparsed": True, "max_severity": "none"})
                all_obs_c.append(
                    {
                        "unit": unit_id,
                        "severity": conservative_round(seats),
                        "language": languages[unit_id],
                    }
                )
                all_obs_k.append(
                    {
                        "unit": unit_id,
                        "severity": collegium_round(seats),
                        "language": languages[unit_id],
                    }
                )
                latest[unit_id] = seats
        for unit_id, seats in latest.items():
            latest_obs_c.append(
                {
                    "unit": unit_id,
                    "severity": conservative_round(seats),
                    "language": languages[unit_id],
                }
            )
            latest_obs_k.append(
                {
                    "unit": unit_id,
                    "severity": collegium_round(seats),
                    "language": languages[unit_id],
                }
            )
        out["arms"][arm] = {
            "conservative_all_repeats": metrics(all_obs_c, labels, severities),
            "collegium_all_repeats": metrics(all_obs_k, labels, severities),
            "conservative_latest_repeat": metrics(latest_obs_c, labels, severities),
            "collegium_latest_repeat": metrics(latest_obs_k, labels, severities),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
