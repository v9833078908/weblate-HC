#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Score the zh severity-recalibration arms against the sealed ground truth.

Reads st2-zh-groundtruth.json and the per-run verdict files in a --out-dir
(arm{A,B,C}-<model>-run{k}.json), and prints, per arm and per configuration
(each seat, and the max-severity collegium), median across repeats of:

  missed_crit  true criticals not rejected (R2 gate: must be 0 in every run)
  false_crit   clean strings wrongly rejected (R2 gate: <=2 by median)
  REAL@14/@24  hand-confirmed defects (anchor-14 / rubric major+ 24) reaching >=flag
  FP           clean strings judged >=major

Plus the n=5 noise floor (units whose >=flag status varies across repeats) and
the 4x4 severity confusion matrix summed over the collegium repeats. Degenerate
runs (all-unparsed transport failures) are reported and skipped.

With --revision (analysis/data/st2-zh-critical-revision.json) three further
numbers separate the faults that missed_crit conflates, over the overlay's
in-gate units only:

  missed_defect    judge ranked a confirmed defect below major - a recall
                   failure, and the only one of the three that disqualifies
  sev_under        judge reached >=major but below the revised severity
  sev_over         judge exceeded the revised severity on a real defect

Usage:
    python3 st2-zh-score.py --truth st2-zh-groundtruth.json --out-dir st2-zh-recal
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics as st
from pathlib import Path

RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}
LEVELS = ["none", "minor", "major", "critical"]
ANCHOR14 = {
    "24130",
    "24160",
    "24161",
    "24180",
    "24181",
    "24182",
    "24183",
    "24184",
    "24190",
    "24194",
    "24200",
    "24221",
    "24240",
    "24241",
}


def maxsev(errors) -> int:
    return max((RANK.get(e.get("severity", ""), 0) for e in errors), default=0)


def run_label(run: dict) -> dict[str, str]:
    return {i: LEVELS[maxsev(v["errors"])] for i, v in run.items()}


def collegium(a: dict, b: dict) -> dict[str, str]:
    return {
        i: LEVELS[
            max(maxsev(a[i]["errors"]), maxsev(b.get(i, {"errors": []})["errors"]))
        ]
        for i in a
    }


def degenerate(run: dict) -> bool:
    return all(v.get("unparsed") for v in run.values()) or (
        all(v["verdict"] == "pass" for v in run.values())
        and sum(1 for v in run.values() if v.get("unparsed")) == len(run)
    )


def metrics(lab, gt, true_crit, true_majorplus, true_none):
    return dict(
        missed_crit=sum(1 for i in true_crit if lab[i] != "critical"),
        false_crit=sum(1 for i in true_none if lab[i] == "critical"),
        real14=sum(1 for i in ANCHOR14 if RANK[lab[i]] >= 2),
        real24=sum(1 for i in true_majorplus if RANK[lab[i]] >= 2),
        fp=sum(1 for i in true_none if RANK[lab[i]] >= 2),
    )


def load_revision(fn: str) -> dict[str, int]:
    """Map an in-gate unit id to the rank the revision can defend."""
    with open(fn, encoding="utf-8") as fh:
        labels = json.load(fh)["labels"]
    return {i: RANK[v["revised_severity"]] for i, v in labels.items() if v["in_gate"]}


def split_metrics(lab, gate: dict[str, int]) -> dict[str, int]:
    # Every gate id is scored. A unit the run never labelled - dropped from the
    # batch, or unparsed - is rank 0, so it counts as missed rather than
    # shrinking the denominator and flattering the run.
    seen = {i: RANK[lab[i]] if i in lab else 0 for i in gate}
    return {
        "missed_defect": sum(1 for r in seen.values() if r < 2),
        "absent": sum(1 for i in gate if i not in lab),
        "sev_under": sum(1 for i, r in seen.items() if 2 <= r < gate[i]),
        "sev_over": sum(1 for i, r in seen.items() if r > gate[i]),
    }


def load(fn: Path) -> dict | None:
    if not fn.exists():
        return None
    run = json.load(fn.open(encoding="utf-8"))
    return None if degenerate(run) else run


def main() -> None:
    p = argparse.ArgumentParser(description="score zh recalibration arms")
    p.add_argument("--truth", default="st2-zh-groundtruth.json")
    p.add_argument("--out-dir", default="st2-zh-recal")
    p.add_argument("--seat1", default="deepseek-v4-pro")
    p.add_argument("--seat2", default="qwen3-235b-a22b-2507")
    # The 2026-08-20 prompt arms are E/F/G, and G is a single-model,
    # 3-repeat arm: pass --arms G --repeats 3 --seat1 "" for it.
    p.add_argument("--arms", default="A,B,C,D")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument(
        "--revision",
        default="",
        help="severity revision overlay; adds the split recall/calibration gate",
    )
    args = p.parse_args()

    gt = {
        i: v["severity"]
        for i, v in json.load(open(args.truth, encoding="utf-8"))["labels"].items()
    }
    true_crit = {i for i, s in gt.items() if s == "critical"}
    true_majorplus = {i for i, s in gt.items() if RANK[s] >= 2}
    true_none = {i for i, s in gt.items() if s == "none"}
    print(
        f"truth: critical {len(true_crit)}  major+ {len(true_majorplus)}  none {len(true_none)}"
    )
    gate = load_revision(args.revision) if args.revision else {}
    if gate:
        print(
            f"revision: in-gate {len(gate)}  "
            + " ".join(f"{i}={LEVELS[r]}" for i, r in sorted(gate.items()))
        )

    out = Path(args.out_dir)
    for arm in args.arms.split(","):
        runs = range(1, args.repeats + 1)
        s1 = (
            [load(out / f"arm{arm}-{args.seat1}-run{k}.json") for k in runs]
            if args.seat1
            else []
        )
        s2 = (
            [load(out / f"arm{arm}-{args.seat2}-run{k}.json") for k in runs]
            if args.seat2
            else []
        )
        g1 = [r for r in s1 if r is not None]
        g2 = [r for r in s2 if r is not None]
        print(
            f"\n=== arm {arm} ===  seat1 good {len(g1)}/{args.repeats}  "
            f"seat2 good {len(g2)}/{args.repeats}"
        )
        if (args.seat1 and len(g1) < args.repeats) or (
            args.seat2 and len(g2) < args.repeats
        ):
            print("  incomplete/degenerate arm — skipping arm-level medians")
        pairs = min(len(g1), len(g2))
        configs = []
        if g1:
            configs.append((args.seat1, [run_label(r) for r in g1]))
        if g2:
            configs.append((args.seat2, [run_label(r) for r in g2]))
        if pairs:
            configs.append(
                ("collegium", [collegium(g1[k], g2[k]) for k in range(pairs)])
            )
        for name, labs in configs:

            def med(key):
                return st.median(
                    metrics(l, gt, true_crit, true_majorplus, true_none)[key]
                    for l in labs
                )

            ids = list(labs[0])
            flip = sum(1 for i in ids if len({RANK[l[i]] >= 2 for l in labs}) > 1)
            print(
                f"  {name:10} missed_crit={med('missed_crit')}/{len(true_crit)} "
                f"false_crit={med('false_crit')} REAL@14={med('real14')}/14 "
                f"REAL@24={med('real24')}/24 FP={med('fp')}/{len(true_none)} "
                f"noise>=flag={flip}/124"
            )
            if gate:

                def smed(key, runs=labs):
                    return st.median(split_metrics(run, gate)[key] for run in runs)

                print(
                    f"  {'':10} missed_defect={smed('missed_defect')}/{len(gate)} "
                    f"absent={smed('absent')} sev_under={smed('sev_under')} "
                    f"sev_over={smed('sev_over')}"
                )
        if pairs == args.repeats and configs and configs[-1][0] == "collegium":
            conf = collections.Counter()
            for lab in configs[-1][1]:
                for i in lab:
                    conf[gt[i], lab[i]] += 1
            print("  confusion (truth rows x judge cols, summed over collegium runs):")
            print("    truth\\judge  none minor major crit")
            for t in LEVELS:
                print(f"    {t:9} " + "".join(f"{conf[t, j]:6}" for j in LEVELS))


if __name__ == "__main__":
    main()
