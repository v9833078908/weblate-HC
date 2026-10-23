# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.settings import Settings


def create_full_backup(
    root: Path,
    release: Path,
    destination: Path,
    *,
    systemd_dir: Path = Path("/etc/systemd/system"),
) -> dict:
    """Caller must quiesce the wizard; SQLite is copied with its backup API."""
    root, release, destination = root.resolve(), release.resolve(), destination.resolve()
    if not release.is_relative_to(root / "releases") or destination.is_relative_to(release):
        raise ValueError("Invalid backup paths")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = root / "shared" / "data"
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Data symlinks are not supported")
    shutil.copytree(source, destination / "data", ignore=shutil.ignore_patterns("wizard.sqlite*"))
    with sqlite3.connect(source.joinpath("wizard.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        with sqlite3.connect(destination / "data" / "wizard.sqlite") as target:
            db.backup(target)
    shutil.copytree(release, destination / "release", symlinks=True)
    private = destination / "private"
    private.mkdir(mode=0o700)
    for name in ("wizard.env", "previous-release", "secrets"):
        path = root / "shared" / name
        if path.is_dir():
            shutil.copytree(path, private / name)
        elif path.is_file():
            shutil.copy2(path, private / name)
    units = destination / "systemd"
    units.mkdir()
    for name in (
        "wizard-api.service",
        "wizard-worker.service",
        "wizard-profile-worker.service",
        "wizard-backup.service",
        "wizard-backup.timer",
    ):
        path = systemd_dir / name
        if path.is_file():
            shutil.copy2(path, units / name)
    files = {
        str(path.relative_to(destination)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in destination.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    manifest = {
        "schema": 1,
        "release": release.name,
        "created": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for path in private.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    verify_full_backup(destination)
    return manifest


def verify_full_backup(destination: Path) -> dict:
    destination = destination.resolve()
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = (destination / name).resolve()
        if not path.is_relative_to(destination):
            raise ValueError("Invalid backup paths")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Backup checksum mismatch")
    with sqlite3.connect(
        (destination / "data" / "wizard.sqlite").as_uri() + "?mode=ro", uri=True
    ) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Database integrity check failed")
    return manifest


def main():
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / (
        "wizard-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite"
    )
    source = (Settings().data_dir / "wizard.sqlite").resolve()
    # SQLite backup captures WAL consistently; the database stores run/export snapshots.
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection,
        sqlite3.connect(output) as backup,
    ):
        connection.backup(backup)
    output.chmod(0o600)
    print(output.name)


if __name__ == "__main__":
    main()
