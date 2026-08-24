#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Offline detector for batch-misaligned machine translation.

A large language model asked to translate N strings in one request can answer
with N strings that are shifted by one, or that repeat one source and drop
another. The reply has the right length, carries no placeholder violation and
is stored against the wrong sources. Weblate cannot see this: every check
compares a target against the source it was stored under.

This detector compares the *language-independent shape* of a target - the
numbers, placeholders, engine separators and terminal punctuation it carries -
against the shape of its own source and of its neighbours. A translation stored
one position away from its source usually carries the neighbour's shape.

It reports neighbourhoods, not verdicts. Read `LIMITS` before using the output.

LIMITS
------
* It cannot see a shift whose strings happen to share a shape. On
  `heart-abyss/hub-1` it missed the confirmed zh_Hant pair 372096/372097, where
  clause content moved across a segment boundary while the shape was preserved.
* A run it reports may be a translator's legitimate punctuation choice.
* Silence is not proof that a component is aligned.

Use it to narrow tens of thousands of units to a few dozen for human review.
Never as a release gate.

Usage:
    detect_misalignment.py --dir /tmp/lqa/scan
    detect_misalignment.py --self-test
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from operator import itemgetter
from typing import Any, NamedTuple

# Engine placeholders that survive translation unchanged. Square-bracket tokens
# are deliberately absent: this project localizes them (`[ЛКМ]` becomes `[LMT]`,
# `[shift]` becomes `[Umschalttaste]`), so treating them as invariant reported
# correctly translated keybinding help as a shift.
PLACEHOLDER = re.compile(r"\{[^{}]*\}|%[A-Za-z0-9_]+%|@@PH\d+@@|<[^<>]{1,40}>")
DIGITS = re.compile(r"\d+")
# Terminal punctuation, full-width folded to ASCII.
FOLD = str.maketrans({"？": "?", "！": "!", "。": ".", "，": ",", "、": ",", "…": "."})
TERMINAL = re.compile(r"[.?!,;:]+$")
# A quantity written without ASCII digits. CJK numerals and scale words in the
# source languages make a digit comparison meaningless: `10 тысяч` and `一万`
# are the same quantity and share no digit at all.
CJK_NUMERAL = re.compile(
    r"[〇零一二三四五六七八九十百千万萬億兆壹貳參肆伍陸柒捌玖拾佰仟]"
)
SCALE_WORD = re.compile(
    r"тысяч|тыс\.|миллион|млн|миллиард|thousand|million|billion|\bk\b",
    re.IGNORECASE,
)


class Shape(NamedTuple):
    """Language-independent fingerprint of a string."""

    placeholders: tuple[str, ...]
    numbers: tuple[str, ...]
    separators: int
    terminal: str
    digits_readable: bool


def shape(text: str) -> Shape:
    stripped = text.translate(FOLD).rstrip()
    match = TERMINAL.search(stripped)
    return Shape(
        placeholders=tuple(sorted(PLACEHOLDER.findall(text))),
        numbers=tuple(sorted(DIGITS.findall(text))),
        separators=text.count("$"),
        terminal="".join(sorted(set(match.group(0)))) if match else "",
        digits_readable=not CJK_NUMERAL.search(text) and not SCALE_WORD.search(text),
    )


def same_shape(a: Shape, b: Shape) -> bool:
    """
    Compare two fingerprints, refusing to judge numbers it cannot read.

    When either side spells a quantity without ASCII digits, the digit
    comparison is dropped rather than reported as a difference. Claiming a
    mismatch there produced false positives on every Chinese and Japanese
    target whose source wrote `10 тысяч`.
    """
    if (a.placeholders, a.separators, a.terminal) != (
        b.placeholders,
        b.separators,
        b.terminal,
    ):
        return False
    if a.digits_readable and b.digits_readable:
        return a.numbers == b.numbers
    return True


def offset_runs(units: list[dict], window: int, min_run: int) -> list[dict[str, Any]]:
    """
    Find maximal runs of consecutive units explained by one shift offset.

    A unit at position i supports offset k when its target carries the shape of
    the source at i+k and that shape differs from its own source's shape - so
    the comparison actually discriminates between the two positions.
    """
    src = [shape(u["source"][0]) for u in units]
    tgt = [shape(u["target"][0]) for u in units]
    n = len(units)
    runs: list[dict[str, Any]] = []
    for k in range(-window, window + 1):
        if k == 0:
            continue
        supported = [
            0 <= i + k < n
            and same_shape(tgt[i], src[i + k])
            and not same_shape(src[i], src[i + k])
            for i in range(n)
        ]
        i = 0
        while i < n:
            if not supported[i]:
                i += 1
                continue
            j = i
            while j < n and supported[j]:
                j += 1
            if j - i >= min_run:
                runs.append(
                    {
                        "offset": k,
                        "length": j - i,
                        "units": [
                            {
                                "id": units[p]["id"],
                                "context": units[p]["context"],
                                "source": units[p]["source"][0],
                                "target": units[p]["target"][0],
                                "belongs_to": units[p + k]["context"],
                            }
                            for p in range(i, j)
                        ],
                    }
                )
            i = j
    return runs


def duplicate_targets(units: list[dict]) -> list[dict[str, Any]]:
    """Two different sources rendered by one identical target."""
    by_target: dict[str, list[dict]] = defaultdict(list)
    for u in units:
        text = u["target"][0].strip()
        # Short strings legitimately repeat across a component.
        if len(text) < 12:
            continue
        by_target[text].append(u)
    out = []
    for text, group in by_target.items():
        sources = {u["source"][0] for u in group}
        if len(group) > 1 and len(sources) > 1:
            out.append(
                {
                    "target": text,
                    "units": [
                        {
                            "id": u["id"],
                            "context": u["context"],
                            "source": u["source"][0],
                        }
                        for u in group
                    ],
                }
            )
    return out


def scan_dir(
    directory: pathlib.Path,
    window: int,
    min_run: int,
    batch_ids: dict[str, list[int]] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Scan every fetched pair, restricted to the strings a batch actually held.

    The restriction matters. A shift happens inside one request, so only the
    units that request translated are neighbours of each other. Scanning a
    whole component when the batch touched a fraction of it makes unrelated
    strings adjacent and manufactures runs: on `space-arena/lockit`, 45
    machine-translated units per language sat inside 4864, and the unrestricted
    scan reported 285 neighbourhoods where the restricted scan reports none.
    """
    scope = json.loads((directory / "scope.json").read_text(encoding="utf-8"))
    batch_ids = batch_ids or {}
    results: dict[str, dict[str, Any]] = {}
    for entry in scope:
        proj, comp = entry["project"], entry["component"]
        for lang in entry["languages"]:
            path = directory / f"{proj}__{comp}__{lang}.json"
            if not path.exists():
                continue
            key = f"{proj}/{comp}/{lang}"
            units = json.loads(path.read_text(encoding="utf-8"))
            units = [u for u in units if u["source"] and u["target"] and u["target"][0]]
            total = len(units)
            if allowed := batch_ids.get(key):
                permitted = set(allowed)
                units = [u for u in units if u["id"] in permitted]
            results[key] = {
                "units": len(units),
                "component_units": total,
                "runs": offset_runs(units, window, min_run),
                "duplicates": duplicate_targets(units),
            }
    return results


def flagged_contexts(result: dict[str, Any]) -> set[str]:
    out = set()
    for run in result["runs"]:
        out.update(u["context"] for u in run["units"])
    return out


def annotate_consensus(results: dict[str, dict[str, Any]]) -> None:
    """
    Mark a run as an outlier when few languages of its component share it.

    A context flagged in every language is a source-side quirk. A shift happens
    inside one language's batch, so a real one is an outlier.
    """
    by_component: dict[str, list[str]] = defaultdict(list)
    for key in results:
        by_component[key.rsplit("/", 1)[0]].append(key)
    for keys in by_component.values():
        votes: Counter[str] = Counter()
        for key in keys:
            votes.update(flagged_contexts(results[key]))
        for key in keys:
            for run in results[key]["runs"]:
                shared = [votes[u["context"]] for u in run["units"]]
                run["languages_in_component"] = len(keys)
                run["max_languages_sharing_a_unit"] = max(shared) if shared else 0
                run["outlier"] = len(keys) >= 3 and run[
                    "max_languages_sharing_a_unit"
                ] <= max(1, len(keys) // 3)


SELF_TEST_ALIGNED = [
    {
        "id": 1,
        "context": "a_1",
        "source": ["Ten thousand coins!"],
        "target": ["Zehntausend Münzen!"],
    },
    {"id": 2, "context": "a_2", "source": ["What?.."], "target": ["Was?.."]},
    {
        "id": 3,
        "context": "a_3",
        "source": ["I feel sick."],
        "target": ["Mir ist schlecht."],
    },
    {
        "id": 4,
        "context": "a_4",
        "source": ["Why so prickly?"],
        "target": ["Warum so stachelig?"],
    },
    {
        "id": 5,
        "context": "a_5",
        "source": ["Take {0} gold."],
        "target": ["Nimm {0} Gold."],
    },
]


def _shift(units: list[dict], start: int, length: int) -> list[dict]:
    """Store each target one position early, the way a skipped reply item does."""
    out = [dict(u, target=list(u["target"])) for u in units]
    for i in range(start, start + length):
        out[i]["target"] = list(units[i + 1]["target"])
    return out


def self_test() -> int:
    failures = []

    clean = offset_runs(SELF_TEST_ALIGNED, window=3, min_run=2)
    if clean:
        failures.append(f"aligned data produced {len(clean)} run(s), expected none")

    shifted = _shift(SELF_TEST_ALIGNED, 0, 4)
    runs = offset_runs(shifted, window=3, min_run=2)
    if not runs:
        failures.append("shifted data produced no run")
    else:
        best = max(runs, key=itemgetter("length"))
        if best["offset"] != 1:
            failures.append(f"offset {best['offset']}, expected +1")
        if best["length"] < 3:
            failures.append(f"run length {best['length']}, expected at least 3")

    dupes = duplicate_targets(
        [
            {
                "id": 1,
                "context": "a",
                "source": ["I feel sick."],
                "target": ["Mir ist schlecht."],
            },
            {
                "id": 2,
                "context": "b",
                "source": ["Why so prickly?"],
                "target": ["Mir ist schlecht."],
            },
        ]
    )
    if len(dupes) != 1:
        failures.append(f"duplicate detector returned {len(dupes)}, expected 1")

    # Both cases below are real production units from heart-abyss/hub-1,
    # captured before the 2026-08-24 repair. Synthetic stand-ins were tried
    # first and passed with the defect still in place, so they proved nothing.
    fixture_path = (
        pathlib.Path(__file__).parent.parent / "tests" / "misalignment_regression.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Four German targets each holding the next source's translation, with the
    # first source's content absent from the component entirely.
    rotation = offset_runs(fixture["rotation_de"], window=3, min_run=2)
    covered = {u["id"] for run in rotation for u in run["units"]}
    if missing := {370314, 370315, 370316, 370317} - covered:
        failures.append(f"real rotation not covered, missed units {sorted(missing)}")
    if not any(r["offset"] == 1 and r["length"] >= 3 for r in rotation):
        failures.append("real rotation not reported as a +1 run of at least 3")

    # A Chinese target writes `10 тысяч` as `一万`, sharing no digit with its
    # source. Comparing digits there reported two aligned guard lines as a
    # shift; the comparison must be dropped instead of guessed.
    if noise := offset_runs(fixture["cjk_numerals_zh_Hans"], window=3, min_run=2):
        failures.append(
            f"CJK numerals reported as a shift: "
            f"{[[u['id'] for u in r['units']] for r in noise]}"
        )

    for line in failures:
        print(f"FAIL {line}")
    if not failures:
        print(
            "OK synthetic aligned data silent, synthetic shift found at +1, "
            "duplicate found, real production rotation covered, CJK numerals silent"
        )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", help="directory of fetched units plus scope.json")
    parser.add_argument("--window", type=int, default=3, help="offsets to try")
    parser.add_argument("--min-run", type=int, default=2, help="minimum run length")
    parser.add_argument("--json", help="write full findings here")
    parser.add_argument(
        "--batch-ids",
        help="JSON map of 'project/component/language' to the unit ids a batch "
        "translated; without it a component larger than its batch manufactures runs",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.dir:
        parser.error("--dir or --self-test is required")

    batch_ids = (
        json.loads(pathlib.Path(args.batch_ids).read_text(encoding="utf-8"))
        if args.batch_ids
        else None
    )
    results = scan_dir(pathlib.Path(args.dir), args.window, args.min_run, batch_ids)
    annotate_consensus(results)

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    print(
        f"{'component/language':46} {'scanned':>8} {'of':>6} {'runs':>5} "
        f"{'longest':>7} {'dupes':>6}"
    )
    for key, r in sorted(results.items()):
        longest = max((run["length"] for run in r["runs"]), default=0)
        print(
            f"{key:46} {r['units']:8} {r['component_units']:6} {len(r['runs']):5} "
            f"{longest:7} {len(r['duplicates']):6}"
        )
    strong = [
        (key, run)
        for key, r in results.items()
        for run in r["runs"]
        if run["length"] >= 3 or run.get("outlier")
    ]
    print(f"\nneighbourhoods worth reading: {len(strong)}")
    for key, run in sorted(strong, key=lambda x: -x[1]["length"]):
        head = run["units"][0]
        print(
            f"  {key} offset={run['offset']:+d} len={run['length']} from {head['context']} (unit {head['id']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
