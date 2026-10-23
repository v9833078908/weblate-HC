# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import sqlite3

import pytest
from app.db import DomainError
from sqlalchemy import create_engine
from test_full_quality_cycle import FullRemote, drive


def test_export_retries_changed_snapshot_and_returns_matching_revision(tmp_path):
    from app.services.quality_export import download_stable_quality_export

    remote = FullRemote()
    store, cycle = drive(tmp_path, remote)
    downloads = []

    def download():
        downloads.append(1)
        if len(downloads) == 1:
            remote.revisions["2"] = "concurrent-edit"
            remote.judged["2"] = "concurrent-edit"
        return {"de": "fresh-values"}

    original = remote.quality_snapshot

    def snapshot(*args):
        value = original(*args)
        value["scope_revision"] = remote.revisions["2"]
        return value

    remote.quality_snapshot = snapshot
    values, provenance = download_stable_quality_export(
        store, "owner", cycle["id"], remote, download
    )
    assert len(downloads) == 2
    assert values == {"de": "fresh-values"}
    assert provenance["scope_revision"] == "concurrent-edit"


def test_export_never_publishes_mixed_versions_after_two_attempts(tmp_path):
    from app.services.quality_export import download_stable_quality_export

    remote = FullRemote()
    store, cycle = drive(tmp_path, remote)
    original = remote.quality_snapshot

    def snapshot(*args):
        value = original(*args)
        value["scope_revision"] = remote.revisions["2"]
        return value

    remote.quality_snapshot = snapshot

    def download():
        remote.revisions["2"] += "-changed"
        return {"de": "unverified-mixed-file"}

    with pytest.raises(DomainError) as error:
        download_stable_quality_export(store, "owner", cycle["id"], remote, download)
    assert error.value.code == "quality_export_unstable"


def test_existing_database_is_backed_up_before_additive_migration(tmp_path):
    from app.migrate import upgrade

    path = tmp_path / "wizard.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table alembic_version(version_num varchar(32) not null)")
        db.execute("insert into alembic_version values ('0002')")
        db.execute("create table retained(value text)")
        db.execute("insert into retained values ('preserved')")
    engine = create_engine(f"sqlite:///{path}")
    upgrade(engine)
    backups = list((tmp_path / "backups" / "migrations").glob("*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("pragma integrity_check").fetchone() == ("ok",)
        assert db.execute("select version_num from alembic_version").fetchone() == ("0002",)
        assert db.execute("select value from retained").fetchone() == ("preserved",)
        assert (
            db.execute("select name from sqlite_master where name='quality_groups'").fetchone()
            is None
        )
    with sqlite3.connect(path) as db:
        assert db.execute("select version_num from alembic_version").fetchone() == ("0004",)
    upgrade(engine)
    assert len(list((tmp_path / "backups" / "migrations").glob("*.sqlite"))) == 1


@pytest.mark.parametrize("remote_value", ["corrected", ""])
def test_corrected_file_uses_remote_values_even_when_upload_was_nonempty(remote_value):
    from app.services.quality_export import build_corrected_quality_export

    manifest = {
        "source": "en",
        "targets": ["de"],
        "quarantine": [],
        "components": [
            {
                "id": "kit-1",
                "file": "kit.csv",
                "sheet": "kit",
                "rows": [["key", "en", "de"], ["one", "Play", "old"]],
                "profile": {
                    "languages": [
                        {"code": "en", "column": 2},
                        {"code": "de", "column": 3},
                    ]
                },
                "units": [{"key": "one", "row": 2, "values": {"en": "Play", "de": "old"}}],
            }
        ],
    }
    result = build_corrected_quality_export(manifest, {"kit-1": {"de": {"one": remote_value}}})
    assert result["sheets"][0]["rows"][1] == ["one", "Play", remote_value]
    assert manifest["components"][0]["units"][0]["values"]["de"] == "old"


def version_export_inputs():
    manifest = {
        "source": "en",
        "targets": ["de"],
        "quarantine": [],
        "components": [
            {
                "id": "kit-1",
                "file": "kit.csv",
                "sheet": "kit",
                "rows": [["key", "en", "de"], ["one", "Play", "old"]],
                "profile": {
                    "header_row": 1,
                    "languages": [{"code": "en", "column": 2}, {"code": "de", "column": 3}],
                },
                "units": [{"key": "one", "row": 2, "values": {"en": "Play", "de": "old"}}],
            }
        ],
    }
    snapshot = {
        "languages": ["de"],
        "issues": [],
        "components": {
            "remote-kit": {
                "complete": True,
                "source": "en",
                "languages": ["de"],
                "units": [{"key": "one", "source": "Play", "targets": {"de": "Spielen"}}],
            }
        },
    }
    return manifest, snapshot, {"mapping": {"kit.csv\u001fkit": "remote-kit"}}


@pytest.mark.parametrize(
    "defect",
    [
        "incomplete",
        "issue",
        "missing_component",
        "source",
        "missing_key",
        "duplicate_key",
        "changed_source",
        "language",
        "missing_target",
    ],
)
def test_version_quality_export_rejects_partial_or_mismatched_snapshot(defect):
    from app.services.quality_export import build_corrected_quality_export

    manifest, snapshot, plan = version_export_inputs()
    original = copy.deepcopy(manifest)
    component = snapshot["components"]["remote-kit"]
    if defect == "incomplete":
        component.update(complete=False, units=[])
    elif defect == "issue":
        snapshot["issues"] = [{"component": "remote-kit", "code": "remote_identity"}]
    elif defect == "missing_component":
        snapshot["components"].clear()
    elif defect == "source":
        component["source"] = "fr"
    elif defect == "missing_key":
        component["units"] = []
    elif defect == "duplicate_key":
        component["units"].append(copy.deepcopy(component["units"][0]))
    elif defect == "changed_source":
        component["units"][0]["source"] = "Other text"
    elif defect == "language":
        component["languages"] = []
    elif defect == "missing_target":
        component["units"][0]["targets"] = {}
    with pytest.raises(DomainError) as error:
        build_corrected_quality_export(
            manifest,
            {"kit-1": {"de": {"one": "Spielen"}}},
            change_plan=plan,
            remote_snapshot=snapshot,
        )
    assert error.value.code == "quality_export_incomplete"
    assert manifest == original


def test_version_quality_export_keeps_verified_remote_empty_and_nonempty_targets():
    from app.services.quality_export import build_corrected_quality_export

    for target in ["Spielen", ""]:
        manifest, snapshot, plan = version_export_inputs()
        snapshot["components"]["remote-kit"]["units"][0]["targets"]["de"] = target
        result = build_corrected_quality_export(
            manifest,
            {"kit-1": {"de": {"one": target}}},
            change_plan=plan,
            remote_snapshot=snapshot,
        )
        assert result["sheets"][0]["rows"][1] == ["one", "Play", target]
