#!/usr/bin/env python3

# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Split the Hero Craft combined localization table into per-language files.

The game repository keeps every language in one file
(``Assets/Resources/Data/Localization.json``): a JSON array of records with a
``text_id`` key and one key per language code. That file is not strict JSON -
it carries ``//`` line comments written by the game designer - and no
translation tool can consume the shape as is.

This script produces one file per language in a shape Weblate reads natively:

``go-i18n``
    ``[{"id": ..., "description": ..., "translation": ...}]`` - the go-i18n v1
    JSON format (``go-i18n-json``). Keeps per-string context.
``flat``
    ``{"id": "text"}`` - the plain JSON format (``json``). No place for
    context, so descriptions are dropped.

Every generated file is parsed back with the same library Weblate uses, and
the values are compared against the source table before the run is accepted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

RECORD_KEY = "text_id"
ID_LINE_RE = re.compile(r'\s*"text_id"\s*:\s*"([^"]+)"')


def strip_comments(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Remove // and /* */ comments that sit outside JSON strings."""
    out: list[str] = []
    comments: list[tuple[int, str]] = []
    index = 0
    line = 1
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
        if in_string:
            if char == "\\":
                out.append(text[index : index + 2])
                index += 2
                continue
            if char == '"':
                in_string = False
            out.append(char)
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and text[index + 1 : index + 2] == "/":
            end = text.find("\n", index)
            end = length if end < 0 else end
            comments.append((line, text[index + 2 : end].strip()))
            index = end
            continue
        if char == "/" and text[index + 1 : index + 2] == "*":
            end = text.find("*/", index)
            end = length if end < 0 else end + 2
            comments.append((line, text[index:end].strip()))
            line += text.count("\n", index, end)
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out), comments


def collect_descriptions(text: str, comments: list[tuple[int, str]]) -> dict[str, str]:
    """Attach every comment to the record it was written in."""
    lines = text.split("\n")
    starts: list[tuple[int, str]] = [
        (number, match.group(1))
        for number, line in enumerate(lines, 1)
        if (match := ID_LINE_RE.match(line))
    ]
    descriptions: dict[str, list[str]] = {}
    for line_number, comment in comments:
        owners = [name for start, name in starts if start <= line_number]
        if not owners or not comment:
            continue
        descriptions.setdefault(owners[-1], []).append(comment)
    return {name: "\n".join(parts) for name, parts in descriptions.items()}


def load_table(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    text = path.read_bytes().decode("utf-8-sig")
    clean, comments = strip_comments(text)
    records = json.loads(clean)
    if not isinstance(records, list):
        msg = f"{path}: expected a JSON array of records"
        raise SystemExit(msg)
    return records, collect_descriptions(text, comments)


def language_codes(records: list[dict[str, str]]) -> list[str]:
    codes: list[str] = []
    for record in records:
        for key in record:
            if key != RECORD_KEY and key not in codes:
                codes.append(key)
    return codes


def render(
    records: list[dict[str, str]],
    language: str,
    descriptions: dict[str, str],
    shape: str,
) -> bytes:
    payload: Any
    if shape == "flat":
        payload = {record[RECORD_KEY]: record[language] for record in records}
    else:
        payload = []
        for record in records:
            name = record[RECORD_KEY]
            entry: dict[str, str] = {"id": name}
            if name in descriptions:
                entry["description"] = descriptions[name]
            entry["translation"] = record[language]
            payload.append(entry)
    return (json.dumps(payload, indent="\t", ensure_ascii=False) + "\n").encode()


def validate(blob: bytes, records: list[dict[str, str]], language: str, shape: str):
    """Parse the generated file back with the library Weblate uses."""
    from translate.storage.jsonl10n import GoI18NJsonFile, JsonFile

    store = (JsonFile if shape == "flat" else GoI18NJsonFile)(blob)
    got = [(unit.getid().lstrip("."), unit.target) for unit in store.units]
    expected = [(record[RECORD_KEY], record[language]) for record in records]
    if got != expected:
        mismatch = next(
            (pair for pair in zip(got, expected, strict=True) if pair[0] != pair[1]),
            None,
        )
        msg = f"{language}: parse-back mismatch, first difference: {mismatch}"
        raise SystemExit(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", type=Path, help="combined Localization.json")
    parser.add_argument("output", type=Path, help="directory for the split files")
    parser.add_argument("--prefix", default="Localization_")
    parser.add_argument("--shape", choices=("flat", "go-i18n"), default="flat")
    parser.add_argument(
        "--descriptions",
        choices=("sidecar", "inline", "drop"),
        default="sidecar",
        help=(
            "sidecar: write explanations.json for upload into Weblate and keep "
            "the translation files clean; inline: keep them in the source "
            "language file (go-i18n only); drop: discard them"
        ),
    )
    parser.add_argument(
        "--source-language",
        default="ru",
        help="only this file carries inline descriptions",
    )
    args = parser.parse_args()

    records, descriptions = load_table(args.table)
    codes = language_codes(records)
    missing = [
        record[RECORD_KEY]
        for record in records
        if any(code not in record for code in codes)
    ]
    if missing:
        msg = f"records missing a language column: {missing[:5]}"
        raise SystemExit(msg)

    if args.descriptions == "inline" and args.shape == "flat":
        msg = "a flat object has no place for descriptions, use --descriptions sidecar"
        raise SystemExit(msg)

    args.output.mkdir(parents=True, exist_ok=True)
    for code in codes:
        inline = (
            descriptions
            if args.descriptions == "inline" and code == args.source_language
            else {}
        )
        blob = render(records, code, inline, args.shape)
        validate(blob, records, code, args.shape)
        target = args.output / f"{args.prefix}{code}.json"
        target.write_bytes(blob)
        print(f"{target.name:30s} {len(blob):>9,d} bytes")

    if args.descriptions == "sidecar" and descriptions:
        sidecar = args.output / "explanations.json"
        sidecar.write_bytes(
            (json.dumps(descriptions, indent="\t", ensure_ascii=False) + "\n").encode()
        )
        print(f"{sidecar.name:30s} {sidecar.stat().st_size:>9,d} bytes")

    print(
        f"\n{len(records)} strings, {len(codes)} languages, "
        f"{len(descriptions)} descriptions ({args.descriptions})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
