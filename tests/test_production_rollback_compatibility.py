# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest
from app.db import Store

ROOT = Path(__file__).parents[1]
ADDITIONS = {
    "0002": {"quality_cycles", "quality_operations", "quality_units", "quality_events"},
    "0003": {"quality_groups"},
    "0004": {"run_unit_progress"},
}


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["0001", "0002", "0003"])
def test_current_schema_allows_read_only_rollback_to_known_additive_baseline(tmp_path, version):
    store = Store(tmp_path / "current")
    current = store.data_dir / "wizard.sqlite"
    baseline = tmp_path / "baseline.sqlite"
    with sqlite3.connect(current) as source, sqlite3.connect(baseline) as old:
        source.backup(old)
        for revision, names in ADDITIONS.items():
            if revision > version:
                for name in names:
                    old.execute(f'DROP TABLE "{name}"')
        old.execute("UPDATE alembic_version SET version_num=?", (version,))
    preflight = load_script("preflight")
    preflight.ensure_compatible(current, baseline, allow_recovery_addition=True)
    with sqlite3.connect(current) as db:
        db.execute("CREATE TABLE unsupported_future_data(id integer)")
    with pytest.raises(ValueError, match="schema"):
        preflight.ensure_compatible(current, baseline, allow_recovery_addition=True)


@pytest.mark.parametrize("version", ["0001", "0002", "0003"])
def test_rollback_bridge_adds_only_missing_known_migration_declarations(tmp_path, version):
    baseline = tmp_path / "legacy"
    relative = Path("backend/app/migrations/versions")
    destination = baseline / relative
    destination.mkdir(parents=True)
    for path in (ROOT / relative).glob("000*.py"):
        if path.name[:4] <= version:
            shutil.copy2(path, destination / path.name)
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    bridge = load_script("prepare_rollback_bridge").prepare(baseline, ROOT, tmp_path / "bridge")
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before
    assert {path.name for path in (bridge / relative).glob("000*.py")} == {
        path.name for path in (ROOT / relative).glob("000*.py")
    }
    assert all((bridge / relative / name).read_bytes() == data for name, data in before.items())


def test_rollback_rejects_unknown_definition_hidden_under_known_table_name(tmp_path):
    store = Store(tmp_path / "current")
    current = store.data_dir / "wizard.sqlite"
    baseline = tmp_path / "baseline.sqlite"
    with sqlite3.connect(current) as source, sqlite3.connect(baseline) as old:
        source.backup(old)
        for names in ADDITIONS.values():
            for name in names:
                old.execute(f'DROP TABLE "{name}"')
        old.execute("UPDATE alembic_version SET version_num='0001'")
    with sqlite3.connect(current) as db:
        db.execute("ALTER TABLE quality_operations ADD COLUMN unexpected_column TEXT")
    with pytest.raises(ValueError, match="schema"):
        load_script("preflight").ensure_compatible(current, baseline, allow_recovery_addition=True)


@pytest.mark.parametrize("table", sorted(set().union(*ADDITIONS.values()) - {"run_unit_progress"}))
def test_full_rollback_preserves_every_quality_journal_table(tmp_path, table):
    store = Store(tmp_path / "current")
    current = store.data_dir / "wizard.sqlite"
    baseline = tmp_path / "baseline.sqlite"
    with sqlite3.connect(current) as source, sqlite3.connect(baseline) as old:
        source.backup(old)
        for names in ADDITIONS.values():
            for name in names:
                old.execute(f'DROP TABLE "{name}"')
        old.execute("UPDATE alembic_version SET version_num='0001'")
    with sqlite3.connect(current) as db:
        columns = db.execute(f'PRAGMA table_info("{table}")').fetchall()
        names = [row[1] for row in columns]
        values = [1 if "INT" in row[2].upper() else "synthetic-paid-receipt" for row in columns]
        quoted = ",".join(f'"{name}"' for name in names)
        placeholders = ",".join("?" for _ in values)
        db.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', values)
    with pytest.raises(ValueError, match="Durable data changed"):
        load_script("preflight").ensure_data_unchanged(current, baseline)


def test_rollback_bridge_rejects_unknown_current_migration_file(tmp_path):
    baseline = tmp_path / "legacy"
    current = tmp_path / "current"
    relative = Path("backend/app/migrations/versions")
    (baseline / relative).mkdir(parents=True)
    (current / relative).mkdir(parents=True)
    for path in (ROOT / relative).glob("000*.py"):
        shutil.copy2(path, current / relative / path.name)
        if path.name.startswith("0001_"):
            shutil.copy2(path, baseline / relative / path.name)
    (current / relative / "0005_unknown.py").write_text("revision = '0005'\n")
    with pytest.raises(ValueError, match="migration chain"):
        load_script("prepare_rollback_bridge").prepare(baseline, current, tmp_path / "bridge")
