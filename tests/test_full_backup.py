# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3

import pytest
from app import backup


def test_full_backup_preserves_release_files_and_consistent_database(tmp_path):
    root = tmp_path / "wizard"
    release = root / "releases" / "baseline"
    release.mkdir(parents=True)
    (release / "code.py").write_text("baseline")
    # Windows symlinks may require privileges; explicit release is part of the API.
    data = root / "shared" / "data"
    data.mkdir(parents=True)
    (data / "original.xlsx").write_bytes(b"original")
    with sqlite3.connect(data / "wizard.sqlite") as db:
        db.execute("create table example(value text)")
        db.execute("insert into example values ('saved')")
    destination = tmp_path / "backup"
    assert hasattr(backup, "create_full_backup"), "full backup is missing"
    manifest = backup.create_full_backup(root, release, destination)
    assert manifest["release"] == "baseline"
    backup.verify_full_backup(destination)
    with sqlite3.connect(destination / "data" / "wizard.sqlite") as db:
        assert db.execute("select value from example").fetchone() == ("saved",)
    assert (destination / "data" / "original.xlsx").read_bytes() == b"original"
    (destination / "data" / "original.xlsx").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        backup.verify_full_backup(destination)
