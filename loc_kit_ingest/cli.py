# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
from pathlib import Path

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
        "--profile",
        required=True,
        help="Path to the JSON profile file",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory (must not exist; parent must exist)",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        dest="zip_components",
        help="Create one ZIP per component",
    )
    args = parser.parse_args(argv)

    return run(
        kit_paths=[Path(k) for k in args.kit],
        profile_path=Path(args.profile),
        output=Path(args.out),
        zip_components=args.zip_components,
    )
