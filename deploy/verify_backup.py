# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def verify(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema") != 1 or not manifest.get("files"):
        raise ValueError("Unsupported backup manifest")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Backup checksum mismatch")
    with sqlite3.connect((root / "data" / "wizard.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Backup database is corrupt")
    return manifest


def ensure_restoration(directory):
    result = json.loads((Path(directory) / "restore-result.json").read_text())
    if any(
        result.get(key) != "ok"
        for key in ("database", "baseline_app", "login", "projects", "file_checksums")
    ):
        raise ValueError("Baseline restore probe did not pass")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--restored", action="store_true")
    args = parser.parse_args()
    if args.restored:
        ensure_restoration(args.directory)
    print(verify(args.directory)["release"])
