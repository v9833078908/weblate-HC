# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from app.db import Store
from app.models import Job


def module():
    spec = importlib.util.spec_from_file_location(
        "deploy_preflight", Path(__file__).parents[1] / "deploy/preflight.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_installation_and_backup_refuse_unconfirmed_external_operation(tmp_path):
    store = Store(tmp_path)
    with store.session() as session:
        session.add(
            Job(
                id="job",
                owner="owner",
                idempotency_key="job",
                status="failed",
                data={"operations": {"delta:mt:ui:de:0": {"state": "intent"}}},
            )
        )
    with pytest.raises(ValueError, match="Unsettled"):
        module().assert_quiet(tmp_path / "wizard.sqlite")


def test_code_rollback_checks_actual_schema_compatibility(tmp_path):
    Store(tmp_path / "current")
    with (
        sqlite3.connect(tmp_path / "current/wizard.sqlite") as source,
        sqlite3.connect(tmp_path / "copy.sqlite") as target,
    ):
        source.backup(target)
    module().ensure_compatible(tmp_path / "current/wizard.sqlite", tmp_path / "copy.sqlite")
    with sqlite3.connect(tmp_path / "copy.sqlite") as target:
        target.execute("create table future_column(id integer)")
    with pytest.raises(ValueError, match="schema"):
        module().ensure_compatible(tmp_path / "current/wizard.sqlite", tmp_path / "copy.sqlite")


def test_failed_restore_probe_cannot_authorize_installation(tmp_path):
    import json

    spec = importlib.util.spec_from_file_location(
        "verify_backup", Path(__file__).parents[1] / "deploy/verify_backup.py"
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    (tmp_path / "restore-result.json").write_text(
        json.dumps(
            {
                "database": "failed",
                "baseline_app": "ok",
                "login": "ok",
                "projects": "ok",
                "file_checksums": "ok",
            }
        )
    )
    with pytest.raises(ValueError, match="restore"):
        verifier.ensure_restoration(tmp_path)
