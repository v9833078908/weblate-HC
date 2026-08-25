# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: file-ignore[import-private-name]

# Gate: game-number must not introduce a new firing on the local corpora beyond
# the one retained defect, and the invariant (the new rule never fires where
# the old digit-multiset rule was silent, unless the target holds a parsed CJK
# scale run) must hold. Read-only, no network. Usage: with
# DJANGO_SETTINGS_MODULE=weblate.settings_test and
# PYTHONPATH=weblate_customization/src set, run
# python3 analysis/probes/game-number-replay.py.
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

# The replay gate reaches into the check's own building blocks to detect a
# parsed CJK run, so its notion of "parsed" can never drift from the check's.
from weblate_customization.checks import (
    FULL_DATE,
    MARKUP,
    NUMBER,
    URL,
    _base_language,
    _closed_spans,
    _prepare,
    game_number_fails,
)

DATA = Path(__file__).resolve().parent.parent / "data"

HEART_ABYSS_TARGET_LANGUAGES = ("en", "fr")
HEART_ABYSS_FIELDS = (
    "pos",
    "context",
    "speaker",
    "ru",
    "en",
    "fr",
    "ru_checks",
    "en_checks",
    "fr_checks",
    "en_lost_final_punct",
    "fr_lost_final_punct",
    "en_hidden_checks",
    "fr_hidden_checks",
    "duplicate_divergence",
)
HEART_ABYSS_9LANG_TARGET_LANGUAGES = (
    "de",
    "en",
    "es",
    "fr",
    "it",
    "ja",
    "ko",
    "zh_Hans",
    "zh_Hant",
)


def _old_numbers(text: str) -> Counter[str]:
    """Return the digit-multiset rule this plan replaces: bare NUMBER tokens."""
    body = FULL_DATE.sub(" ", MARKUP.sub(" ", URL.sub(" ", text)))
    return Counter(match.group() for match in NUMBER.finditer(body))


def _has_cjk_run(target: str, target_language: str) -> bool:
    """Whether the target holds at least one well-formed parsed CJK scale run."""
    body = _prepare(target, drop_ordinals=False)
    return bool(_closed_spans(body, _base_language(target_language)))


def _read_tsv(
    path: Path,
    fields: tuple[str, ...],
    expected_rows: int,
    required_fields: tuple[str, ...] | None = None,
):
    """Read one exact TSV manifest, rejecting a partial corpus."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    if fieldnames != fields:
        msg = f"{path}: expected columns {fields}, found {fieldnames}"
        raise ValueError(msg)
    if len(rows) != expected_rows:
        msg = f"{path}: expected {expected_rows} rows, found {len(rows)}"
        raise ValueError(msg)
    required = fields if required_fields is None else required_fields
    if any(
        not isinstance(row.get(field), str) or not row[field].strip()
        for row in rows
        for field in required
    ):
        msg = f"{path}: contains a blank required cell"
        raise ValueError(msg)
    return rows


def _read_jsonl(path: Path, fields: tuple[str, ...], expected_rows: int):
    """Read one exact JSONL manifest, rejecting a partial corpus."""
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if len(rows) != expected_rows:
        msg = f"{path}: expected {expected_rows} rows, found {len(rows)}"
        raise ValueError(msg)
    if any(
        not isinstance(row, dict)
        or any(
            not isinstance(row.get(field), str) or not row[field].strip()
            for field in fields
        )
        for row in rows
    ):
        msg = f"{path}: contains a blank required field"
        raise ValueError(msg)
    return rows


def _heart_abyss_pairs() -> list[tuple[str, str, str, str]]:
    rows = _read_tsv(
        DATA / "heart-abyss-hub-1-units.tsv",
        HEART_ABYSS_FIELDS,
        396,
        ("context", "ru", *HEART_ABYSS_TARGET_LANGUAGES),
    )
    return [
        (row["context"], row["ru"], row[target_language], target_language)
        for row in rows
        for target_language in HEART_ABYSS_TARGET_LANGUAGES
    ]


def _heart_abyss_9lang_pairs() -> list[tuple[str, str, str, str]]:
    """
    Return the nine-language live replay's pairs: the only corpus with real CJK notation.

    Recorded in docs/product/measurements/2026-08-25-game-number-nine-language-replay.md.
    """
    rows = _read_tsv(
        DATA / "heart-abyss-hub-1-units-9lang.tsv",
        ("context", "ru", *HEART_ABYSS_9LANG_TARGET_LANGUAGES),
        396,
    )
    return [
        (row["context"], row["ru"], row[target_language], target_language)
        for row in rows
        for target_language in HEART_ABYSS_9LANG_TARGET_LANGUAGES
    ]


def _st2_pairs() -> list[tuple[str, str, str, str]]:
    rows = _read_jsonl(
        DATA / "st2-zh-units.jsonl", ("context", "source", "target"), 124
    )
    return [(row["context"], row["source"], row["target"], "zh_Hans") for row in rows]


def _col4_pairs() -> list[tuple[str, str, str, str]]:
    rows = _read_jsonl(
        DATA / "col4-b0-annotations.jsonl", ("context", "source", "target"), 260
    )
    return [(row["context"], row["source"], row["target"], "fr") for row in rows]


def _replay(pairs: list[tuple[str, str, str, str]]) -> tuple[int, set[str], int, int]:
    """Run one corpus: (firings, firing keys, invariant violations, cjk-run targets)."""
    firings = 0
    firing_keys: set[str] = set()
    violations = 0
    cjk_run_targets = 0
    for key, source, target, target_language in pairs:
        new_fires = game_number_fails(
            source, target, source_language="ru", target_language=target_language
        )
        if new_fires:
            firings += 1
            firing_keys.add(key)
        has_run = _has_cjk_run(target, target_language)
        if has_run:
            cjk_run_targets += 1
        old_fires = bool(_old_numbers(source) - _old_numbers(target))
        if new_fires and not old_fires and not has_run:
            violations += 1
    return firings, firing_keys, violations, cjk_run_targets


def _report(
    label: str, pairs: list[tuple[str, str, str, str]], expected_keys: set[str]
):
    firings, keys, violations, cjk_run_targets = _replay(pairs)
    noun = "firing" if firings == 1 else "firings"
    print(f"{label}: {firings} {noun} / {len(pairs)} pairs")
    for key in sorted(keys):
        print(f"  {key}")
    mismatch = keys != expected_keys
    if mismatch:
        print(f"  MISMATCH: expected firing keys {sorted(expected_keys)}")
    return len(pairs), violations, cjk_run_targets, mismatch


def main() -> None:
    heart_abyss_pairs = _heart_abyss_pairs()
    st2_pairs = _st2_pairs()
    col4_pairs = _col4_pairs()
    heart_abyss_9lang_pairs = _heart_abyss_9lang_pairs()

    heart_abyss_count, heart_abyss_violations, heart_abyss_cjk, heart_abyss_mismatch = (
        _report("heart-abyss/hub-1 ru->en,fr", heart_abyss_pairs, set())
    )
    st2_count, st2_violations, st2_cjk, st2_mismatch = _report(
        "st2 ru->zh_Hans", st2_pairs, set()
    )
    col4_count, col4_violations, col4_cjk, col4_mismatch = _report(
        "col4 b0 ru->fr", col4_pairs, {"EVENT_516_RESULT_977"}
    )
    (
        heart_abyss_9lang_count,
        heart_abyss_9lang_violations,
        heart_abyss_9lang_cjk,
        heart_abyss_9lang_mismatch,
    ) = _report(
        "heart-abyss/hub-1 9lang ru->de,en,es,fr,it,ja,ko,zh_Hans,zh_Hant",
        heart_abyss_9lang_pairs,
        set(),
    )

    total_pairs = heart_abyss_count + st2_count + col4_count + heart_abyss_9lang_count
    total_violations = (
        heart_abyss_violations
        + st2_violations
        + col4_violations
        + heart_abyss_9lang_violations
    )
    total_cjk = heart_abyss_cjk + st2_cjk + col4_cjk + heart_abyss_9lang_cjk
    any_mismatch = (
        heart_abyss_mismatch
        or st2_mismatch
        or col4_mismatch
        or heart_abyss_9lang_mismatch
    )

    print(f"invariant violations: {total_violations} / {total_pairs} pairs")
    print(f"targets with a parsed CJK run: {total_cjk}")

    if any_mismatch or total_violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
