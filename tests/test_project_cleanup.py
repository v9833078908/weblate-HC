# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


def cleanup_module():
    path = Path(__file__).parents[1] / "scripts" / "cleanup_wizard_projects.py"
    assert path.exists(), "The approved cleanup script has not been implemented"
    spec = importlib.util.spec_from_file_location("project_cleanup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SLUG = "test-5dacb142c2714e46"
JOB = "3cc5dcb7-1f61-4218-8fec-fa7252f0c5c7"
DRAFT = "135cedcf-f213-418e-9e83-393e1dd9969c"


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    with sqlite3.connect(root / "wizard.sqlite") as db:
        db.executescript("""
        CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT, owner TEXT, revision TEXT, data TEXT);
        CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT, idempotency_key TEXT,
                          lease_owner TEXT, lease_until REAL, status TEXT, data TEXT);
        CREATE TABLE users(username TEXT, password_hash TEXT, is_admin INT);
        CREATE TABLE sessions(id TEXT, username TEXT, csrf TEXT, expires REAL);
        INSERT INTO users VALUES('producer','protected',1);
        INSERT INTO sessions VALUES('session','producer','protected',9999999999);
        """)
        rows = [
            (SLUG, "project", {"name": "same name"}),
            ("old-project", "project", {"name": "same name"}),
            ("profile:" + SLUG, "profile", {}),
            (DRAFT, "draft", {"project": SLUG}),
            ("export:" + JOB, "export", {"sheets": []}),
            ("export-pointer:" + SLUG, "export_pointer", {"export_id": "export:" + JOB}),
            ("heartbeat", "heartbeat", {}),
        ]
        db.executemany(
            "INSERT INTO records VALUES(?,?,?, ?,?)",
            [(key, kind, "producer", "revision-1", json.dumps(value)) for key, kind, value in rows],
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
            (
                JOB,
                "producer",
                "key",
                "",
                0,
                "completed",
                json.dumps({"project": SLUG, "operations": {}, "export_id": "export:" + JOB}),
            ),
        )
    for folder, uid in [("runs", JOB), ("uploads", DRAFT)]:
        target = root / folder / uid
        target.mkdir(parents=True)
        (target / "source.txt").write_text("preserve original", encoding="utf-8")
    return root


def test_dry_run_is_read_only_and_apply_preserves_other_project_and_credentials(data, tmp_path):
    cleanup = cleanup_module()
    before = (data / "wizard.sqlite").read_bytes()
    manifest = cleanup.build_cleanup_manifest(data, [("producer", SLUG)])
    assert (data / "wizard.sqlite").read_bytes() == before
    assert (data / "runs" / JOB / "source.txt").exists()
    assert len(manifest["records"]) == 5
    report = cleanup.apply_cleanup_manifest(
        data, manifest, tmp_path / "archive", cleanup.digest(manifest)
    )
    assert report["deleted_records"] == 5
    assert report["deleted_jobs"] == 1
    with sqlite3.connect(data / "wizard.sqlite") as db:
        assert db.execute("SELECT id FROM records ORDER BY id").fetchall() == [
            ("heartbeat",),
            ("old-project",),
        ]
        assert db.execute("SELECT password_hash FROM users").fetchone() == ("protected",)
        assert db.execute("SELECT csrf FROM sessions").fetchone() == ("protected",)
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
    with sqlite3.connect(tmp_path / "archive" / "wizard.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM records").fetchone() == (7,)
    assert not (data / "runs" / JOB).exists()
    assert (
        tmp_path / "archive" / "removed" / "runs" / JOB / "source.txt"
    ).read_text() == "preserve original"


def test_changed_record_blocks_entire_cleanup(data, tmp_path):
    cleanup = cleanup_module()
    manifest = cleanup.build_cleanup_manifest(data, [("producer", SLUG)])
    with sqlite3.connect(data / "wizard.sqlite") as db:
        db.execute("UPDATE records SET revision='changed' WHERE id=?", (SLUG,))
    with pytest.raises(ValueError, match="changed"):
        cleanup.apply_cleanup_manifest(
            data, manifest, tmp_path / "archive", cleanup.digest(manifest)
        )
    assert (data / "runs" / JOB).exists()
    with sqlite3.connect(data / "wizard.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM records").fetchone() == (7,)


@pytest.mark.parametrize(
    "status,lease,operations",
    [
        ("running", 0, {}),
        ("failed", 9999999999, {}),
        ("failed", 0, {"mt:sheet:de": {"state": "intent"}}),
        ("failed", 0, {"mt:sheet:de": {"state": "accepted"}}),
    ],
)
def test_active_or_unconfirmed_operation_blocks_cleanup(data, status, lease, operations):
    cleanup = cleanup_module()
    with sqlite3.connect(data / "wizard.sqlite") as db:
        db.execute(
            "UPDATE jobs SET status=?,lease_until=?,data=?",
            (
                status,
                lease,
                json.dumps({"project": SLUG, "operations": operations}),
            ),
        )
    with pytest.raises(ValueError, match="active|unconfirmed"):
        cleanup.build_cleanup_manifest(data, [("producer", SLUG)])


def test_export_referenced_by_another_project_blocks_cleanup(data):
    cleanup = cleanup_module()
    with sqlite3.connect(data / "wizard.sqlite") as db:
        db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?)",
            (
                "export-pointer:old-project",
                "export_pointer",
                "producer",
                "r",
                json.dumps({"export_id": "export:" + JOB}),
            ),
        )
    with pytest.raises(ValueError, match="shared"):
        cleanup.build_cleanup_manifest(data, [("producer", SLUG)])


def test_symlink_and_unapproved_slug_are_rejected(data, tmp_path):
    cleanup = cleanup_module()
    with pytest.raises(ValueError, match="allowlist"):
        cleanup.build_cleanup_manifest(data, [("producer", "old-project")])
    target = data / "runs" / JOB / "source.txt"
    target.unlink()
    target.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError, match="symlink"):
        cleanup.build_cleanup_manifest(data, [("producer", SLUG)])


def test_nested_shared_file_reference_blocks_cleanup(data):
    cleanup = cleanup_module()
    with sqlite3.connect(data / "wizard.sqlite") as db:
        db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?)",
            (
                "other-draft",
                "draft",
                "producer",
                "r",
                json.dumps(
                    {
                        "project": "old-project",
                        "_files": [
                            {"path": str(data / "uploads" / DRAFT / "inner" / ".." / "source.txt")}
                        ],
                    }
                ),
            ),
        )
    with pytest.raises(ValueError, match="shared"):
        cleanup.build_cleanup_manifest(data, [("producer", SLUG)])


def test_failed_move_leaves_partial_report_and_verified_backup(data, tmp_path, monkeypatch):
    cleanup = cleanup_module()
    manifest = cleanup.build_cleanup_manifest(data, [("producer", SLUG)])

    def unavailable(*args):
        raise OSError("Archive move unavailable")

    monkeypatch.setattr(cleanup.shutil, "move", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        cleanup.apply_cleanup_manifest(
            data, manifest, tmp_path / "archive", cleanup.digest(manifest)
        )
    report = json.loads((tmp_path / "archive" / "result.json").read_text())
    assert report["deleted_jobs"] == 1
    assert report["archived_directories"] == []
    assert set(report["remaining_directories"]) == {"runs/" + JOB, "uploads/" + DRAFT}
    assert (tmp_path / "archive" / "files-backup" / "runs" / JOB / "source.txt").exists()
