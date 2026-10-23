#!/usr/bin/env python3

# Copyright © HCGameLoc contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Export a monolingual Weblate component as one wide CSV: key + one column per language.

Weblate stores one file per language, so neither the UI download nor
``GET /api/components/.../file/`` can produce the single-table layout game teams
keep in Google Sheets. This rebuilds that table from the component's own files,
in the key order of the source language.

Values are taken from the stored translation files, not from the CSV exporter,
so keys and strings starting with ``= + - @ | %`` are not wrapped in quotes
(see ``weblate/formats/exporters.py`` ``CSVExporter.string_filter``).

Usage::

    export WEBLATE_API_TOKEN=wlu_...
    python3 scripts/wide-csv-export.py \\
        --url http://localhost:3001/api \\
        --component space-arena/game-strings \\
        --languages 'en,ru,de,fr,es,pt-br=pt_BR,ch-s=zh_Hans,jp=ja,kr=ko,fa,tr,hi,vi,id,th' \\
        --output sheet.csv

``--languages`` takes an ordered list of columns; ``header=weblate_code`` renames
a column whose header in the sheet differs from the Weblate language code.
Omit it to export every language of the component under its Weblate code.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request


def api_get(url: str, token: str) -> bytes:
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        msg = f"refusing to send the API token to a non-HTTP URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers={"Authorization": f"Token {token}"}
    )
    with urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
        request, timeout=60
    ) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3001/api")
    parser.add_argument("--component", required=True, help="project/component")
    parser.add_argument("--languages", help="ordered columns, e.g. 'en,jp=ja'")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    token = os.environ.get("WEBLATE_API_TOKEN")
    if not token:
        print("WEBLATE_API_TOKEN is not set", file=sys.stderr)
        return 1

    base = args.url.rstrip("/")
    if args.languages:
        columns = [
            (column.split("=", 1) if "=" in column else [column, column])
            for column in args.languages.split(",")
        ]
    else:
        listing = json.loads(
            api_get(
                f"{base}/components/{args.component}/translations/?page_size=1000",
                token,
            )
        )
        columns = [[result["language"]["code"]] * 2 for result in listing["results"]]

    strings: dict[str, dict[str, str]] = {}
    for header, code in columns:
        strings[header] = json.loads(
            api_get(f"{base}/translations/{args.component}/{code}/file/", token)
        )

    source = strings[columns[0][0]]
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(["", *(header for header, _ in columns)])
        for key in source:
            writer.writerow(
                [key, *(strings[header].get(key, "") for header, _ in columns)]
            )

    print(f"{len(source)} keys x {len(columns)} languages -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
