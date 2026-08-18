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

Usage:
    python3 st2-zh-score.py --truth st2-zh-groundtruth.json --out-dir st2-zh-recal
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics as st
from pathlib import Path

RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}
LEVELS = ["none", "minor", "major", "critical"]
ANCHOR14 = {
    "24130", "24160", "24161", "24180", "24181", "24182", "24183",
    "24184", "24190", "24194", "24200", "24221", "24240", "24241",
}


def maxsev(errors) -> int:
    return max((RANK.get(e.get("severity", ""), 0) for e in errors), default=0)


def run_label(run: dict) -> dict[str, str]:
    return {i: LEVELS[maxsev(v["errors"])] for i, v in run.items()}


def collegium(a: dict, b: dict) -> dict[str, str]:
    return {
        i: LEVELS[max(maxsev(a[i]["errors"]), maxsev(b.get(i, {"errors": []})["errors"]))]
        for i in a
    }


def degenerate(run: dict) -> bool:
    return all(v.get("unparsed") for v in run.values()) or all(
        v["verdict"] == "pass" for v in run.values()
    ) and sum(1 for v in run.values() if v.get("unparsed")) == len(run)


def metrics(lab, gt, true_crit, true_majorplus, true_none):
    return dict(
        missed_crit=sum(1 for i in true_crit if lab[i] != "critical"),
        false_crit=sum(1 for i in true_none if lab[i] == "critical"),
        real14=sum(1 for i in ANCHOR14 if RANK[lab[i]] >= 2),
        real24=sum(1 for i in true_majorplus if RANK[lab[i]] >= 2),
        fp=sum(1 for i in true_none if RANK[lab[i]] >= 2),
    )


def load(fn: Path) -> dict | None:
    run = json.load(fn.open(encoding="utf-8"))
    return None if degenerate(run) else run


def main() -> None:
    p = argparse.ArgumentParser(description="score zh recalibration arms")
    p.add_argument("--truth", default="st2-zh-groundtruth.json")
    p.add_argument("--out-dir", default="st2-zh-recal")
    p.add_argument(
        "--seat1", default="deepseek-v4-pro"
    )
    p.add_argument("--seat2", default="qwen3-235b-a22b-2507")
    args = p.parse_args()

    gt = {i: v["severity"] for i, v in json.load(open(args.truth, encoding="utf-8"))["labels"].items()}
    true_crit = {i for i, s in gt.items() if s == "critical"}
    true_majorplus = {i for i, s in gt.items() if RANK[s] >= 2}
    true_none = {i for i, s in gt.items() if s == "none"}
    print(f"truth: critical {len(true_crit)}  major+ {len(true_majorplus)}  none {len(true_none)}")

    out = Path(args.out_dir)
    for arm in ("A", "B", "C"):
        s1 = [load(out / f"arm{arm}-{args.seat1}-run{k}.json") for k in range(1, 6)]
        s2 = [load(out / f"arm{arm}-{args.seat2}-run{k}.json") for k in range(1, 6)]
        g1 = [r for r in s1 if r is not None]
        g2 = [r for r in s2 if r is not None]
        print(f"\n=== arm {arm} ===  seat1 good {len(g1)}/5  seat2 good {len(g2)}/5")
        if len(g1) < 5 or len(g2) < 5:
            print("  incomplete/degenerate arm — skipping arm-level medians")
        pairs = min(len(g1), len(g2))
        configs = []
        if g1:
            configs.append((args.seat1, [run_label(r) for r in g1]))
        if g2:
            configs.append((args.seat2, [run_label(r) for r in g2]))
        if pairs:
            configs.append(("collegium", [collegium(g1[k], g2[k]) for k in range(pairs)]))
        for name, labs in configs:
            def med(key):
                return st.median(metrics(l, gt, true_crit, true_majorplus, true_none)[key] for l in labs)

            ids = list(labs[0])
            flip = sum(1 for i in ids if len({RANK[l[i]] >= 2 for l in labs}) > 1)
            print(
                f"  {name:10} missed_crit={med('missed_crit')}/{len(true_crit)} "
                f"false_crit={med('false_crit')} REAL@14={med('real14')}/14 "
                f"REAL@24={med('real24')}/24 FP={med('fp')}/{len(true_none)} "
                f"noise>=flag={flip}/124"
            )
        if pairs == 5 and name == "collegium":
            conf = collections.Counter()
            for lab in configs[-1][1]:
                for i in lab:
                    conf[(gt[i], lab[i])] += 1
            print("  confusion (truth rows x judge cols, summed over collegium runs):")
            print("    truth\\judge  none minor major crit")
            for t in LEVELS:
                print(f"    {t:9} " + "".join(f"{conf[(t, j)]:6}" for j in LEVELS))


if __name__ == "__main__":
    main()
