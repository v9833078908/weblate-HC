# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.run_isolation import isolate_run


def main():
    parser = argparse.ArgumentParser(
        description="Offline isolation; dry run unless --apply is explicit"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("--expected-payload-sha256", required=True)
    parser.add_argument("--backup-manifest", required=True, type=Path)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = isolate_run(
            args.database,
            args.run_id,
            args.expected_payload_sha256,
            args.backup_manifest,
            args.reason,
            apply=args.apply,
        )
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
