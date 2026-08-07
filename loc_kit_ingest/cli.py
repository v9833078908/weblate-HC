# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
from pathlib import Path

from loc_kit_ingest.infer import DEFAULT_MIN_FILL
from loc_kit_ingest.pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loc_kit_ingest",
        description="Import loc-kits into Weblate-compatible formats.",
    )
    parser.add_argument(
        "kit",
        nargs="+",
        help="Path(s) to kit file(s) (.csv, .tsv, .xlsx)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory (must not exist; parent must exist)",
    )
    parser.add_argument(
        "--profile",
        help=(
            "Path to a JSON profile. Omit to derive one from the kit's own header "
            "row; the derived profile is written to the output directory. A "
            "schema_version 2 profile can also define a TBX record-map glossary."
        ),
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        dest="zip_components",
        help="Create one ZIP per component",
    )
    parser.add_argument(
        "--source-lang",
        metavar="CODE",
        help=(
            "Language code holding the original text. Defaults to the leftmost "
            "populated language column."
        ),
    )
    parser.add_argument(
        "--component",
        metavar="NAME",
        help="Component name. Defaults to the kit file name.",
    )
    parser.add_argument(
        "--min-fill",
        type=float,
        default=DEFAULT_MIN_FILL,
        metavar="PERCENT",
        help=(
            "Minimum share of rows a language column must fill to count as a "
            f"translation rather than stray content (default {DEFAULT_MIN_FILL:g})."
        ),
    )
    parser.add_argument(
        "--include-lang",
        action="append",
        default=[],
        metavar="CODE",
        help="Import this language even if it is below --min-fill. Repeatable.",
    )
    args = parser.parse_args(argv)

    return run(
        kit_paths=[Path(k) for k in args.kit],
        output=Path(args.out),
        profile_path=Path(args.profile) if args.profile else None,
        zip_components=args.zip_components,
        source_lang=args.source_lang,
        component=args.component,
        min_fill=args.min_fill,
        include_languages=frozenset(args.include_lang),
    )
