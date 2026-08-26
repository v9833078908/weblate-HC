#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Offline probe for the deterministic batch anchor gate.

Answers the two questions Task 1 of
`docs/llm-first/plans/2026-08-25-deterministic-batch-anchor-gate.md` must settle
before any code ships:

1. At what minimum target length do two *different* sources stop legitimately
   sharing one identical target inside a single request? Below that length the
   duplicate-target rule would refuse correct replies.
2. Does the candidate anchor gate ever refuse a correctly aligned production
   pair, and how often does it abstain instead of judging?

It reads only corpora already in the repository, calls no service and writes
nothing. Run with ``uv run python analysis/probes/batch-anchor-threshold.py``.
"""

from __future__ import annotations

import csv
import itertools
import json
import pathlib
import re
from collections import Counter

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"

# Tokens the real machinery has already placeholderized, plus engine markup,
# whose digits must not be read as the string's numbers. `<size=14>` is why
# markup is here: it carries a digit that no translation states.
PLACEHOLDER_LIKE = re.compile(r"\{[^{}]*\}|%[A-Za-z0-9_]+%|@@PH\d+@@|<[^<>]{1,40}>")
ASCII_NUMBER = re.compile(r"\d+")
# A grouped thousand: `1 000`, `1,000`, `1.000`, or French U+202F. The group
# separator is language policy, so the digit multiset is not comparable.
GROUPED_NUMBER = re.compile(r"\d[.,\u00a0\u2009\u202f\u2007 ]\d{3}(?!\d)")
# A full date renders as prose in most target languages.
DATE_LIKE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")
# A quantity spelled without ASCII digits: `10 тысяч` and `一万` are one amount
# sharing no digit.
UNREADABLE_QUANTITY = re.compile(
    r"[〇零一二三四五六七八九十百千万萬億兆壹貳參肆伍陸柒捌玖拾佰仟]"
    r"|тысяч|тыс\.|миллион|млн|миллиард"
    r"|thousand|million|billion|\bk\b",
    re.IGNORECASE,
)
SEPARATOR_SPACE = r"[ \t\u00a0\u2009\u202f]"
# Mirrors weblate_customization.checks.SEPARATOR_LOOSE_IN_SOURCE.
SEPARATOR_LOOSE = re.compile(rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}|^\$|\$$")


def without_placeholders(text: str) -> str:
    return PLACEHOLDER_LIKE.sub(" ", text)


def number_form_abstains(text: str) -> str | None:
    """Why this side's numbers cannot be compared, or None when they can."""
    stripped = without_placeholders(text)
    if any(char.isdigit() and not char.isascii() for char in stripped):
        return "non_ascii_digit"
    if GROUPED_NUMBER.search(stripped):
        return "grouped_number"
    if DATE_LIKE.search(stripped):
        return "date"
    if UNREADABLE_QUANTITY.search(stripped):
        return "unreadable_quantity"
    return None


def separator_is_tight(source: str) -> bool:
    """Whether `$` is used as the engine line separator, not as currency."""
    return "$" in source and not SEPARATOR_LOOSE.search(source)


def anchor_verdict(source: str, target: str) -> tuple[str, str]:
    """Return (verdict, reason): pass, refuse or abstain, with its cause."""
    if separator_is_tight(source):
        if target.count("$") != source.count("$"):
            return "refuse", "separator_count"
    elif "$" in source:
        return "abstain", "separator_loose"

    source_reason = number_form_abstains(source)
    if source_reason:
        return "abstain", f"source_{source_reason}"
    source_numbers = Counter(ASCII_NUMBER.findall(without_placeholders(source)))
    if not source_numbers:
        return "abstain", "no_number_in_source"
    target_reason = number_form_abstains(target)
    if target_reason:
        return "abstain", f"target_{target_reason}"
    target_numbers = Counter(ASCII_NUMBER.findall(without_placeholders(target)))
    if source_numbers - target_numbers:
        return "refuse", "number_lost"
    return "pass", "number_kept"


def load_hub1() -> dict[str, list[tuple[str, str, str]]]:
    rows = list(
        csv.DictReader(
            (DATA / "heart-abyss-hub-1-units.tsv").open(encoding="utf-8"),
            delimiter="\t",
        )
    )
    rows.sort(key=lambda row: int(row["pos"]))
    out = {}
    for lang in ("en", "fr"):
        out[f"hub-1 ru->{lang}"] = [
            (row["context"], row["ru"].strip(), (row[lang] or "").strip())
            for row in rows
            if row["ru"].strip() and (row[lang] or "").strip()
        ]
    return out


def load_st2() -> list[tuple[str, str, str]]:
    records = [
        json.loads(line)
        for line in (DATA / "st2-zh-units.jsonl").open(encoding="utf-8")
        if line.strip()
    ]
    records.sort(key=lambda record: record.get("position") or 0)
    return [
        (record["context"], _first(record["source"]), _first(record["target"]))
        for record in records
        if _first(record["source"]) and _first(record["target"])
    ]


def load_col4() -> dict[str, list[tuple[str, str, str]]]:
    records = [
        json.loads(line)
        for line in (DATA / "col4-b0-annotations.jsonl").open(encoding="utf-8")
        if line.strip()
    ]
    out: dict[str, list[tuple[str, str, str]]] = {
        "col4 ru->fr (human pass)": [],
        "col4 ru->fr (human defect)": [],
    }
    for record in records:
        source = (record.get("source") or "").strip()
        target = (record.get("target") or "").strip()
        if not source or not target:
            continue
        bucket = (
            "col4 ru->fr (human pass)"
            if record.get("label") == "pass"
            else "col4 ru->fr (human defect)"
        )
        out[bucket].append((record.get("context", ""), source, target))
    return out


def _first(value) -> str:
    if isinstance(value, list):
        return (value[0] if value else "").strip()
    return (value or "").strip()


def report_anchors(name: str, pairs: list[tuple[str, str, str]]) -> None:
    verdicts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    refusals: list[tuple[str, str, str, str]] = []
    for context, source, target in pairs:
        verdict, reason = anchor_verdict(source, target)
        verdicts[verdict] += 1
        reasons[reason] += 1
        if verdict == "refuse":
            refusals.append((context, source, target, reason))
    total = len(pairs)
    judged = verdicts["pass"] + verdicts["refuse"]
    print(f"\n{name}: n={total}")
    print(
        f"  judged={judged} ({100 * judged / total:.1f}%)  "
        f"pass={verdicts['pass']}  refuse={verdicts['refuse']}  "
        f"abstain={verdicts['abstain']}"
    )
    for reason, count in reasons.most_common():
        print(f"    {reason:34s} {count}")
    for context, source, target, reason in refusals[:8]:
        print(f"    REFUSED [{reason}] {context}: {source[:56]!r} -> {target[:56]!r}")


def report_duplicates(name: str, pairs: list[tuple[str, str, str]]) -> None:
    print(f"\n{name}: in-window duplicate targets of different sources, window=10")
    windows = [pairs[i : i + 10] for i in range(0, len(pairs), 10)]
    for min_length in range(0, 61, 4):
        collisions = 0
        for window in windows:
            seen: dict[str, str] = {}
            for _context, source, target in window:
                text = target.strip()
                if len(text) < min_length:
                    continue
                if seen.setdefault(text, source) != source:
                    collisions += 1
        print(f"    min_length={min_length:3d}  collisions={collisions}")


def report_convergence(name: str, pairs: list[tuple[str, str, str]]) -> None:
    """
    Every legitimate convergence in the corpus, with its length and distance.

    The duplicate rule only ever looks inside one request, so what bounds its
    false-refusal rate is not how often two sources converge but how *close*
    together they sit. A pair 40 units apart never shares a request.
    """
    by_target: dict[str, list[tuple[int, str]]] = {}
    for position, (_context, source, target) in enumerate(pairs):
        text = target.strip()
        if text:
            by_target.setdefault(text, []).append((position, source))
    found: list[tuple[int, int, str, str, str]] = []
    for text, group in by_target.items():
        for (first, left), (second, right) in itertools.combinations(group, 2):
            if left != right:
                found.append((len(text), abs(first - second), text, left, right))
    print(f"\n{name}: {len(found)} convergent pairs of different sources")
    if not found:
        return
    print(
        f"    target length min={min(item[0] for item in found)} "
        f"max={max(item[0] for item in found)}"
    )
    print(
        f"    positional distance min={min(item[1] for item in found)} "
        f"max={max(item[1] for item in found)}"
    )
    print(f"    pairs closer than a 10-unit window: "
          f"{sum(1 for item in found if item[1] < 10)}")
    for length, distance, text, left, right in sorted(found, key=lambda i: -i[0]):
        print(
            f"    len={length:3d} dist={distance:4d} {text[:44]!r} "
            f"<- {left[:24]!r} | {right[:24]!r}"
        )


def report_separators(name: str, pairs: list[tuple[str, str, str]]) -> None:
    tight = sum(1 for _c, source, _t in pairs if separator_is_tight(source))
    loose = sum(
        1 for _c, source, _t in pairs if "$" in source and not separator_is_tight(source)
    )
    print(f"  {name}: sources with tight $ = {tight}, with loose/currency $ = {loose}")


def main() -> None:
    corpora: dict[str, list[tuple[str, str, str]]] = {}
    corpora.update(load_hub1())
    corpora["st2-zh ru->zh"] = load_st2()
    corpora.update(load_col4())

    print("=" * 72)
    print("ANCHOR GATE: verdicts on production pairs")
    print("=" * 72)
    for name, pairs in corpora.items():
        report_anchors(name, pairs)

    print("\n" + "=" * 72)
    print("SEPARATOR COVERAGE")
    print("=" * 72)
    for name, pairs in corpora.items():
        report_separators(name, pairs)

    print("\n" + "=" * 72)
    print("DUPLICATE-TARGET THRESHOLD")
    print("=" * 72)
    for name in ("hub-1 ru->en", "hub-1 ru->fr", "st2-zh ru->zh"):
        report_duplicates(name, corpora[name])

    print("\n" + "=" * 72)
    print("LEGITIMATE CONVERGENCE: length and distance")
    print("=" * 72)
    for name, pairs in corpora.items():
        report_convergence(name, pairs)

    aligned = sum(
        len(pairs) for name, pairs in corpora.items() if "human defect" not in name
    )
    print(f"\ntotal aligned pairs measured: {aligned}")


main()
