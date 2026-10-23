# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import DomainError
from app.settings import Settings
from test_mt_recovery import advance, recovery_fixture


def test_recovery_deadline_is_not_extended_by_new_settings(tmp_path, monkeypatch):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    scope = data["mt_batches"]["kit-1:de"]
    scope["deadline"] = clock[0] + 5
    original = scope["deadline"]
    from app.services.run_recovery import advance_run_recovery

    advance_run_recovery(
        store,
        "run",
        remote,
        data=data,
        scope=scope,
        checkpoint=checkpoint,
        settings=Settings(mt_max_observation_seconds=86400),
        component="kit-1",
        language="de",
    )
    assert scope["deadline"] == original


def test_legacy_observation_deadline_is_fixed_across_settings_changes(monkeypatch):
    import time

    from app.services.mt_observation import record_translation_observation

    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    state = {"created": clock[0]}
    record_translation_observation(state, Settings(mt_max_observation_seconds=1800))
    clock[0] += 1700
    assert record_translation_observation(
        state, Settings(mt_max_observation_seconds=86400), filled=1, total=2
    )
    assert state["observe_until"] == 2800


@pytest.mark.parametrize("status", [400, 401, 403, 429, 503, None])
def test_unavailable_read_suspends_at_saved_deadline(tmp_path, monkeypatch, status):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    scope = data["mt_batches"]["kit-1:de"]
    clock[0] = scope["deadline"] + 1

    def unavailable(*args):
        error = DomainError("weblate_unavailable", "Unavailable", 503)
        error.remote_status = status
        error.retry_after = 999999
        raise error

    remote.list_units = unavailable
    with pytest.raises(DomainError) as caught:
        advance(args)
    assert caught.value.code == "weblate_unknown"
    saved = store.get("mt_fixture", "run", "owner")["mt_batches"]["kit-1:de"]
    assert saved["state"] == "observation_expired"
    assert saved["coverage_unknown"] is True
    assert saved["next_poll_at"] == 0
    from app.db import Store

    restored = Store(tmp_path).get("mt_fixture", "run", "owner")
    restored.pop("revision")
    data.clear()
    data.update(restored)
    with pytest.raises(DomainError):
        advance(args)
    assert len(remote.posts) == 1


def test_waiting_reconciliation_finishes_with_explicit_deadline_exception(tmp_path, monkeypatch):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    reconcile = remote.reconcile_mt_batch

    def waiting(*a):
        result = reconcile(*a)
        result["reconciliation"]["paid_unknown"] = []
        result["reconciliation"]["waiting"] = [2]
        result["units"][0]["state"] = "requesting"
        return result

    remote.reconcile_mt_batch = waiting
    data["mt_batches"]["kit-1:de"]["submitted"].append(1)
    clock[0] = data["mt_batches"]["kit-1:de"]["deadline"] + 1
    assert advance(args)
    scope = data["mt_batches"]["kit-1:de"]
    assert scope["state"] == "completed_with_exceptions"
    assert scope["exceptions"] == {"observation_deadline": [2]}
    assert scope["payment_unknown"] == [2]
    assert len(remote.posts) == 1


def terminal_unknown(args):
    store, data, remote, checkpoint, clock, rows = args
    remote.producer_me = lambda: {"capabilities": {"mt_async_batches": 1, "mt_reconciliation": 1}}
    remote.lose = False
    assert not advance(args)
    remote.receipt["state"] = "provider_unknown"
    remote.receipt["units"][0]["state"] = "provider_unknown"
    clock[0] += 31

    def reconcile(project, key):
        return {
            **copy.deepcopy(remote.receipt),
            "reconciliation": {
                "ready": [2] if rows[1]["target"][0] else [],
                "never_sent": [],
                "waiting": [],
                "paid_unknown": [] if rows[1]["target"][0] else [2],
                "paid_invalid": [],
                "stale": [],
                "forbidden": [],
            },
        }

    remote.reconcile_mt_batch = reconcile
    return args


def test_unknown_paid_row_does_not_block_never_sent_tail(tmp_path, monkeypatch):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    rows.append({"id": 3, "source": ["Next"], "context": "three", "target": [""], "state": 0})
    assert not advance(args)
    clock[0] += 31
    assert not advance(args)
    assert [[unit["unit_id"] for unit in payload["units"]] for payload, _key in remote.posts] == [
        [2],
        [3],
    ]
    assert rows[0]["target"] == ["Ready"]
    assert rows[1]["target"] == [""]
    assert data["mt_batches"]["kit-1:de"]["history"][0]["state"] == "provider_unknown"


def test_manual_completion_is_reconciled_before_expired_observation_window(tmp_path, monkeypatch):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    rows[1].update(target=["Human"], state=20)
    clock[0] += 86401
    assert advance(args)
    assert len(remote.posts) == 1
    assert rows[1]["target"] == ["Human"]


def test_only_unresolved_rows_are_reported_when_scope_finishes(tmp_path, monkeypatch):
    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    assert advance(args)
    assert len(remote.posts) == 1
    assert data["mt_batches"]["kit-1:de"]["state"] == "completed_with_exceptions"
    assert data["mt_batches"]["kit-1:de"]["exceptions"] == {"paid_unknown": [2]}


def test_completed_exception_keeps_receipt_link_after_store_restart(tmp_path, monkeypatch):
    from app.db import Store
    from app.models import RunUnitProgress
    from sqlalchemy import select

    args = terminal_unknown(recovery_fixture(tmp_path, monkeypatch))
    store, data, remote, checkpoint, clock, rows = args
    assert advance(args)
    restored = Store(tmp_path)
    with restored.session() as session:
        progress = session.scalar(select(RunUnitProgress).where(RunUnitProgress.unit_id == 2))
        assert progress.disposition == "paid_unknown"
        assert progress.receipt_key == remote.posts[0][1]


def test_worker_advances_next_sheet_while_first_scope_waits(tmp_path, monkeypatch):
    from app.models import Job
    from app.worker import process_once
    from test_mt_wait import waiting_run

    store, owner, run_id, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    with store.session() as session:
        job = session.get(Job, run_id)
        data = copy.deepcopy(job.data)
        project = data["remote_project"]
        second = copy.deepcopy(data["snapshot"]["manifest"]["components"][0])
        second["id"] = "kit-2"
        data["snapshot"]["manifest"]["components"].append(second)
        data["operations"]["component:kit-2"] = {"state": "confirmed"}
        job.data = data
    settings.mt_async_batches_enabled = True
    settings.mt_async_projects = (project,)
    remote.producer_me = lambda: {"capabilities": {"mt_async_batches": 1, "mt_reconciliation": 1}}
    remote.list_units = lambda project, component, language: [
        {
            "id": 1 if component == "kit-1" else 2,
            "source": ["Hello"],
            "context": "one",
            "target": [""],
            "state": 0,
        }
    ]
    posts = []

    def submit(project, payload, key):
        posts.append(payload["component"])
        return {
            "key": key,
            "run_id": "remote",
            "state": "accepted",
            "units": [
                {**unit, "state": "pending", "after_revision": ""} for unit in payload["units"]
            ],
        }

    remote.start_mt_batch = submit
    process_once(store, settings, remote)
    assert posts == ["kit-1", "kit-2"]


def test_upgrade_preserves_unknown_paid_receipt_and_finished_text(tmp_path):
    from app.db import Store
    from app.models import Job

    store = Store(tmp_path)
    original = {
        "mt_batches": {
            "kit-1:pl": {
                "active": {"key": "retained-paid-key"},
                "history": [{"state": "provider_unknown"}],
            }
        },
        "finished_translations": 1662,
    }
    with store.session() as session:
        session.add(
            Job(
                id="old-job",
                owner="owner",
                idempotency_key="old-key",
                status="unknown",
                data=original,
            )
        )
    with store.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE run_unit_progress")
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='0003'")
    store.engine.dispose()
    upgraded = Store(tmp_path)
    with upgraded.session() as session:
        job = session.get(Job, "old-job")
        assert job.status == "unknown"
        assert job.idempotency_key == "old-key"
        assert job.data == original
    assert list((tmp_path / "backups" / "migrations").glob("*.sqlite"))
