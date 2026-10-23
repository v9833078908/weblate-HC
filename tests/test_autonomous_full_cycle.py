# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import Store
from test_mt_recovery import advance, recovery_fixture


@pytest.mark.parametrize("ready", [210, 810, 1070, 1620, 1642, 1662])
def test_progress_milestones_never_resubmit_ready_1662_scope(tmp_path, monkeypatch, ready):
    args = recovery_fixture(tmp_path, monkeypatch)
    store, data, remote, checkpoint, clock, rows = args
    rows[:] = [
        {
            "id": i,
            "source": [f"Synthetic source {i}"],
            "context": f"key-{i}",
            "target": [f"Ready {i}" if i <= ready else ""],
            "state": 20 if i <= ready else 0,
        }
        for i in range(1, 1663)
    ]
    before = copy.deepcopy(rows[:ready])
    remote.lose = False
    remote.producer_me = lambda: {"capabilities": {"mt_async_batches": 1, "mt_reconciliation": 1}}
    assert advance(args) is (ready == 1662)
    for payload, key in remote.posts:
        assert all(row["unit_id"] > ready for row in payload["units"])
    restored = Store(tmp_path)
    saved = restored.get("mt_fixture", "run", "owner")
    saved.pop("revision")
    data.clear()
    data.update(saved)
    clock[0] += 31
    previous = len(remote.posts)
    advance(args)
    assert len(remote.posts) == previous
    assert rows[:ready] == before


def test_rollback_accepts_only_known_additive_recovery_schema(tmp_path):
    import importlib.util
    import sqlite3
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "recovery_preflight", Path(__file__).parents[1] / "deploy/preflight.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = Store(tmp_path / "current")
    baseline = tmp_path / "before.sqlite"
    with sqlite3.connect(store.data_dir / "wizard.sqlite") as db, sqlite3.connect(baseline) as old:
        db.backup(old)
        old.execute("DROP TABLE run_unit_progress")
        old.execute("UPDATE alembic_version SET version_num='0003'")
    module.ensure_compatible(
        store.data_dir / "wizard.sqlite", baseline, allow_recovery_addition=True
    )
    with sqlite3.connect(store.data_dir / "wizard.sqlite") as db:
        db.execute("CREATE TABLE unknown_future_data(id integer)")
    with pytest.raises(ValueError, match="schema"):
        module.ensure_compatible(
            store.data_dir / "wizard.sqlite", baseline, allow_recovery_addition=True
        )


def test_previous_release_reads_current_database_through_bridge(tmp_path):
    import importlib.util
    import io
    import os
    import subprocess
    import sys
    import zipfile
    from pathlib import Path

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        "rollback_bridge", root / "deploy/prepare_rollback_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = tmp_path / "baseline"
    refs = (
        "production/wizard-20261023",
        "origin/production/wizard-20261023",
        "corporate/production/wizard-20261023",
        "1a763b5a33f948be98acb8e8d1d7005d9dee1f9a",
    )
    baseline_ref = next(
        ref
        for ref in refs
        if subprocess.run(
            ["git", "rev-parse", "--verify", ref], cwd=root, capture_output=True
        ).returncode
        == 0
    )
    archive = subprocess.check_output(
        ["git", "archive", "--format=zip", baseline_ref, "backend"], cwd=root
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        source.extractall(baseline)
    data = tmp_path / "data"
    store = Store(data)
    protected = {"finished": 1662, "paid_key": "synthetic-paid-receipt"}
    store.save("fixture", "protected", "owner", protected)
    store.engine.dispose()
    bridge = module.prepare(baseline, root, tmp_path / "bridge")
    env = {**os.environ, "PYTHONPATH": str(bridge / "backend")}
    script = "from pathlib import Path; from app.db import Store; s=Store(Path(__import__('sys').argv[1])); assert s.get('fixture','protected','owner')['finished']==1662; assert s.get('fixture','protected','owner')['paid_key']=='synthetic-paid-receipt'"
    result = subprocess.run(
        [sys.executable, "-c", script, str(data)],
        cwd=bridge,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    with __import__("sqlite3").connect(data / "wizard.sqlite") as db:
        assert db.execute("select version_num from alembic_version").fetchone() == ("0004",)
        assert db.execute("select count(*) from run_unit_progress").fetchone() == (0,)
