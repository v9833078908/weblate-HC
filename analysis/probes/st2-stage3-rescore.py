#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Re-score the stored Stage 3 zh verdicts under the split recall/calibration gate.

Stage 3 judged a 15-unit zh slice, not the full 124-unit corpus, so the
arm-level metrics in st2-zh-score.py do not apply. The gate logic is imported
from that scorer rather than reimplemented, so both paths stay in step.

Usage:
    python3 st2-stage3-rescore.py \
        --verdicts ../data/st2-stage3-verdicts.json \
        --revision ../data/st2-zh-critical-revision.json \
        --truth ../data/st2-zh-groundtruth.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

# The scorer's filename is not an importable module name, and it is the single
# source of the gate logic, so it is loaded by path rather than copied.
_spec = importlib.util.spec_from_file_location(
    "st2_zh_score", Path(__file__).resolve().parent / "st2-zh-score.py"
)
_score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_score)
LEVELS, RANK = _score.LEVELS, _score.RANK
load_revision, run_label, split_metrics = (
    _score.load_revision,
    _score.run_label,
    _score.split_metrics,
)


def main() -> None:
    p = argparse.ArgumentParser(description="re-score Stage 3 under the split gate")
    p.add_argument("--verdicts", default="../data/st2-stage3-verdicts.json")
    p.add_argument("--revision", default="../data/st2-zh-critical-revision.json")
    p.add_argument("--truth", default="../data/st2-zh-groundtruth.json")
    args = p.parse_args()

    runs = json.loads(Path(args.verdicts).read_text(encoding="utf-8"))["runs"]
    gate = load_revision(args.revision)
    sealed = {
        i: v["severity"]
        for i, v in json.loads(Path(args.truth).read_text(encoding="utf-8"))[
            "labels"
        ].items()
    }
    sealed_crit = [i for i, s in sealed.items() if s == "critical"]

    print(
        f"in-gate {len(gate)}: "
        + " ".join(f"{i}={LEVELS[r]}" for i, r in sorted(gate.items()))
    )
    print(f"sealed criticals: {len(sealed_crit)}\n")
    header = f"{'model':32} {'unparsed':>8} {'missed_crit':>11} {'missed_defect':>13} {'absent':>6} {'sev_under':>9} {'sev_over':>8}"
    print(header)
    print("-" * len(header))

    for model, run in runs.items():
        lab = run_label(run)
        unparsed = sum(1 for v in run.values() if v.get("unparsed"))
        # Legacy counter, restricted to the criticals this slice actually carried.
        present_crit = [i for i in sealed_crit if i in lab]
        missed_crit = sum(1 for i in present_crit if lab[i] != "critical")
        s = split_metrics(lab, gate)
        print(
            f"{model:32} {unparsed:>4}/{len(run):<3} "
            f"{missed_crit:>7}/{len(present_crit):<3} "
            f"{s['missed_defect']:>9}/{len(gate):<3} "
            f"{s['absent']:>6} {s['sev_under']:>9} {s['sev_over']:>8}"
        )

    print("\nper-unit labels on the gate:")
    for model, run in runs.items():
        lab = run_label(run)
        cells = " ".join(f"{i}:{lab.get(i, '-'):8}" for i in sorted(gate))
        print(f"  {model:32} {cells}")

    print("\nRANK reference: " + " ".join(f"{k}={v}" for k, v in RANK.items()))


if __name__ == "__main__":
    main()
