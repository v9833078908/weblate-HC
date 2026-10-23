# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


def prepare(baseline, current, destination):
    baseline, current, destination = map(
        lambda value: Path(value).resolve(), (baseline, current, destination)
    )
    relative = Path("backend/app/migrations/versions")
    filenames = (
        "0001_initial.py",
        "0002_quality_cycle.py",
        "0003_quality_groups.py",
        "0004_run_recovery.py",
    )
    previous = sorted(path.name for path in (baseline / relative).glob("[0-9]*.py"))
    current_inventory = sorted(path.name for path in (current / relative).glob("[0-9]*.py"))
    if tuple(current_inventory) != filenames:
        raise ValueError("Unsupported rollback migration chain")
    if not previous or tuple(previous) != filenames[: len(previous)]:
        raise ValueError("Unsupported rollback migration chain")
    for name in filenames:
        source = current / relative / name
        old = baseline / relative / name
        if not source.is_file() or (
            old.exists()
            and old.read_bytes().replace(b"\r\n", b"\n")
            != source.read_bytes().replace(b"\r\n", b"\n")
        ):
            raise ValueError("Unsupported rollback migration chain")
    if len(previous) == len(filenames):
        return baseline
    if destination.exists() or destination == baseline or destination == current:
        raise ValueError("Rollback bridge must be a new release directory")
    shutil.copytree(baseline, destination, symlinks=True)
    copied = {}
    for name in filenames[len(previous) :]:
        source = current / relative / name
        shutil.copy2(source, destination / relative / name)
        copied[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (destination / "rollback-bridge.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "baseline": baseline.name,
                "recovery_revision": "0004",
                "migration_sha256": copied[filenames[-1]],
                "added_migration_sha256": copied,
                "workers_must_remain_disabled": True,
            },
            indent=2,
        ),
        encoding="utf8",
    )
    return destination


if __name__ == "__main__":
    print(prepare(*sys.argv[1:]))
