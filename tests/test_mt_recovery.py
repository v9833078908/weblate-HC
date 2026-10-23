# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import hashlib
import json
import time
from uuid import uuid4

import httpx
import pytest
from app.db import DomainError
from app.integrations.weblate import WeblateClient
from app.settings import Settings


def client_for(tmp_path, handler):
    secret = tmp_path / "synthetic-token"
    secret.write_text("local-fixture-not-a-service-key")
    return WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(handler))


def test_mutating_http_timeout_is_longer_than_gateway_legacy_limit(tmp_path):
    client = client_for(
        tmp_path,
        lambda req: httpx.Response(200, json={"read_seconds": req.extensions["timeout"]["read"]}),
    )
    assert client.request("POST", "projects/")["read_seconds"] == 600
    assert client.request("GET", "projects/")["read_seconds"] == 60


def test_batch_submission_and_observation_keep_same_key(tmp_path):
    key = str(uuid4())
    payload = {
        "component": "kit-1",
        "language": "de",
        "engine": "openrouter",
        "threshold": 80,
        "units": [{"unit_id": 551, "revision": "a" * 64}],
    }
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(
            202 if request.method == "POST" else 200,
            json={"key": key, "run_id": "one-run", "state": "accepted", "units": []},
        )

    client = client_for(tmp_path, handler)
    assert client.start_mt_batch("test", payload, key)["run_id"] == "one-run"
    assert client.get_mt_batch("test", key)["run_id"] == "one-run"
    assert captured[0].headers["Idempotency-Key"] == key
    assert captured[1].url.path == f"/api/producer/projects/test/mt/batches/{key}/"


def test_batch_post_504_is_unknown_not_an_immediate_paid_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(504)

    client = client_for(tmp_path, handler)
    with pytest.raises(DomainError) as caught:
        client.start_mt_batch("test", {"units": []}, str(uuid4()))
    assert caught.value.code == "weblate_unknown"
    assert calls == ["POST"]


def test_batch_observation_404_is_not_another_translation_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(404, json={"detail": "Not found"})

    client = client_for(tmp_path, handler)
    assert client.get_mt_batch("test", str(uuid4())) is None
    assert calls == ["GET"]


def test_completed_550_rows_are_never_resubmitted():
    from app.services.mt_batches import pending_mt_units

    rows = [
        {
            "id": i,
            "source": [f"Source {i}"],
            "context": f"key-{i}",
            "target": [f"Ready {i}" if i <= 550 else ""],
            "state": 20 if i <= 550 else 0,
        }
        for i in range(1, 1663)
    ]
    selected = pending_mt_units(rows)
    assert len(selected) == 1112
    assert {r["unit_id"] for r in selected} == set(range(551, 1663))
    assert all(len(r["revision"]) == 64 for r in selected)


def recovery_fixture(tmp_path, monkeypatch):
    from app.db import Store

    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    store = Store(tmp_path)
    data = {"remote_project": "test", "operations": {}, "snapshot": {"engine": "openrouter"}}
    rows = [
        {"id": 1, "source": ["Hello"], "context": "one", "target": ["Ready"], "state": 20},
        {"id": 2, "source": ["World"], "context": "two", "target": [""], "state": 0},
    ]

    def checkpoint():
        store.save("mt_fixture", "run", "owner", copy.deepcopy(data))

    class Remote:
        posts = []
        receipt = None
        lose = True
        completed = False

        def producer_me(self):
            return {"capabilities": {"mt_async_batches": 1}}

        def list_units(self, *args):
            return copy.deepcopy(rows)

        def start_mt_batch(self, project, payload, key):
            self.posts.append((copy.deepcopy(payload), key))
            self.receipt = {
                "key": key,
                "run_id": "remote-run",
                "state": "accepted",
                "units": [
                    {**r, "state": "pending", "after_revision": ""} for r in payload["units"]
                ],
            }
            if self.lose:
                raise DomainError("weblate_unknown", "504 after commit", 503)
            return copy.deepcopy(self.receipt)

        def get_mt_batch(self, project, key):
            if self.completed:
                rows[1].update(target=["Welt"], state=20)
                value = {k: rows[1][k] for k in ["id", "source", "context", "target", "state"]}
                after = hashlib.sha256(
                    json.dumps(
                        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest()
                self.receipt.update(
                    state="completed",
                    units=[
                        {**self.receipt["units"][0], "state": "applied", "after_revision": after}
                    ],
                )
            return copy.deepcopy(self.receipt)

    return store, data, Remote(), checkpoint, clock, rows


def advance(args):
    from app.services.mt_batches import advance_mt_batches

    store, data, remote, checkpoint, clock, rows = args
    return advance_mt_batches(
        store,
        "run",
        remote,
        data=data,
        checkpoint=checkpoint,
        settings=Settings(),
        component="kit-1",
        language="de",
    )


def test_504_reconciles_same_batch_key(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    store, data, remote, checkpoint, clock, rows = args
    assert not advance(args)
    assert len(remote.posts) == 1
    assert [u["unit_id"] for u in remote.posts[0][0]["units"]] == [2]
    first_key = remote.posts[0][1]
    clock[0] += 31
    assert not advance(args)
    assert len(remote.posts) == 1
    remote.completed = True
    clock[0] += 31
    assert advance(args)
    assert remote.posts[0][1] == first_key
    assert rows[0]["target"] == ["Ready"]


def test_restart_retains_next_poll_and_idempotency_key(tmp_path, monkeypatch):
    from app.db import Store

    args = recovery_fixture(tmp_path, monkeypatch)
    store, data, remote, checkpoint, clock, rows = args
    assert not advance(args)
    saved = Store(tmp_path).get("mt_fixture", "run", "owner")
    assert saved is not None
    data.clear()
    data.update(saved["data"] if "data" in saved else saved)
    assert not advance(args)
    assert len(remote.posts) == 1


def test_running_receipt_does_not_repeat_admission(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, _, remote, _, clock, _ = args
    assert not advance(args)
    remote.receipt["state"] = "running"
    for _ in range(4):
        clock[0] += 31
        assert not advance(args)
    assert len(remote.posts) == 1


def test_receipt_429_respects_long_retry_after(tmp_path, monkeypatch):
    from app.integrations.weblate import WeblateRejected

    args = recovery_fixture(tmp_path, monkeypatch)
    _, _, remote, _, clock, _ = args
    assert not advance(args)
    original = remote.get_mt_batch
    reads = []

    def throttled(*a):
        reads.append(clock[0])
        raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "90"}))

    remote.get_mt_batch = throttled
    clock[0] += 31
    assert not advance(args)
    clock[0] += 89
    assert not advance(args)
    assert len(reads) == 1
    remote.get_mt_batch = original
    clock[0] += 1
    assert not advance(args)
    assert len(remote.posts) == 1


def test_worker_uses_receipt_instead_of_legacy_translation(tmp_path, monkeypatch):
    from app.models import Job
    from app.services.runs import get_run
    from app.worker import process_once
    from test_mt_wait import waiting_run

    store, owner, run_id, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    with store.session() as session:
        project = session.get(Job, run_id).data["remote_project"]
    settings.mt_async_batches_enabled = True
    settings.mt_async_projects = (project,)
    remote.values["one"] = "Ready"
    rows = [
        {
            "id": i,
            "source": [source],
            "context": key,
            "target": [remote.values[key]],
            "state": 20 if remote.values[key] else 0,
        }
        for i, (key, source) in enumerate(
            [("one", "Hello"), ("two", "Goodbye"), ("three", "Play")], 1
        )
    ]
    remote.producer_me = lambda: {"capabilities": {"mt_async_batches": 1}}
    remote.list_units = lambda *a: copy.deepcopy(rows)
    batch_posts = []
    receipt = {}

    def start(project, payload, key):
        batch_posts.append((payload, key))
        receipt.update(
            key=key,
            run_id="remote-run",
            state="running",
            units=[{**u, "state": "pending", "after_revision": ""} for u in payload["units"]],
        )
        raise DomainError("weblate_unknown", "Lost response", 503)

    remote.start_mt_batch = start
    remote.get_mt_batch = lambda *a: copy.deepcopy(receipt)
    assert process_once(store, settings, remote)
    assert remote.posts == 0
    assert len(batch_posts) == 1
    clock[0] += 31
    assert process_once(store, settings, remote)
    assert len(batch_posts) == 1
    remote.values.update(two="Tschüss", three="Spielen")
    results = []
    for unit in rows[1:]:
        unit.update(target=[remote.values[unit["context"]]], state=20)
        after = hashlib.sha256(
            json.dumps(unit, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        expected = next(u for u in batch_posts[0][0]["units"] if u["unit_id"] == unit["id"])
        results.append({**expected, "state": "applied", "after_revision": after})
    receipt.update(state="completed", units=results)
    clock[0] += 31
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run_id)["status"] == "completed"
    assert remote.values["one"] == "Ready"
    assert remote.posts == 0


def test_incremental_uses_durable_scope_only_for_planned_empty_ids(tmp_path, monkeypatch):
    from app.db import revision
    from app.services.incremental import process_incremental

    args = recovery_fixture(tmp_path, monkeypatch)
    _, data, remote, checkpoint, clock, rows = args
    profile = {"persona": "P", "style": "S", "language_instructions": {}}

    def snapshot():
        return {
            "slug": "test",
            "profile": {"openrouter": profile},
            "components": {
                "kit-1": {
                    "source": "en",
                    "languages": ["de"],
                    "complete": True,
                    "units": [
                        {
                            "key": r["context"],
                            "source": r["source"][0],
                            "targets": {"de": r["target"][0]},
                            "target_ids": {"de": r["id"]},
                        }
                        for r in rows
                    ],
                }
            },
        }

    initial = snapshot()
    data["snapshot"].update(
        manifest={"source": "en", "targets": ["de"]},
        profile=profile,
        change_plan={
            "snapshot": initial,
            "snapshot_hash": revision(initial),
            "components": [
                {
                    "local_id": "local",
                    "remote_id": "kit-1",
                    "new": [],
                    "seed": {"de": {}},
                    "translate": {"de": ["two"]},
                }
            ],
        },
    )
    remote.start_scoped_translation = lambda *a: pytest.fail("Legacy paid endpoint called")
    settings = Settings(mt_async_batches_enabled=True, mt_async_projects=("test",))
    with pytest.raises(DomainError) as caught:
        process_incremental(data, remote, checkpoint, settings=settings, snapshot_loader=snapshot)
    assert caught.value.code == "incremental_wait"
    assert len(remote.posts) == 1
    assert [u["unit_id"] for u in remote.posts[0][0]["units"]] == [2]
    clock[0] += 31
    remote.completed = True
    result = process_incremental(
        data, remote, checkpoint, settings=settings, snapshot_loader=snapshot
    )
    assert result["local"]["de"] == {"one": "Ready", "two": "Welt"}
    assert len(remote.posts) == 1


def test_unconfirmed_admission_retries_same_key_at_most_eight_times(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, data, remote, _, clock, _ = args
    remote.get_mt_batch = lambda *a: None
    for _ in range(8):
        assert not advance(args)
        clock[0] = data["mt_batches"]["kit-1:de"]["next_poll_at"]
    with pytest.raises(DomainError) as caught:
        advance(args)
    assert caught.value.code == "weblate_unknown"
    assert len(remote.posts) == 8
    assert len({key for _, key in remote.posts}) == 1
    assert all(payload == remote.posts[0][0] for payload, _ in remote.posts)


def test_observation_deadline_survives_worker_restarts(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, _, remote, _, clock, _ = args
    assert not advance(args)
    clock[0] += 1801
    with pytest.raises(DomainError) as caught:
        advance(args)
    assert caught.value.code == "weblate_unknown"
    assert len(remote.posts) == 1


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, 1801])
def test_mutating_timeout_is_bounded(value):
    with pytest.raises(ValueError):
        Settings(weblate_write_read_timeout=value)


def test_public_progress_and_diagnostics_exclude_private_batch_payload(tmp_path, monkeypatch):
    from app.models import Job
    from app.services.diagnostics import diagnostic_report
    from app.services.runs import public_run
    from test_mt_wait import waiting_run

    store, _, run_id, _, _, clock = waiting_run(tmp_path, monkeypatch)
    with store.session() as session:
        job = session.get(Job, run_id)
        job.data = {
            **job.data,
            "mt_batches": {
                "kit-1:de": {
                    "state": "reconciling",
                    "filled": 550,
                    "total": 1662,
                    "last_progress": clock[0] - 60,
                    "next_poll_at": clock[0] + 30,
                    "active": {"payload": {"private": "never-public"}, "key": str(uuid4())},
                }
            },
        }
        public = public_run(job)
        report = diagnostic_report(run_id, job.data)
    progress = public["stages"]["mt_observation"]
    assert progress["translated"] == 550
    assert progress["total"] == 1662
    assert progress["scopes"]["kit-1:de"]["state"] == "reconciling"
    assert report["mt_observation"] == progress
    assert "never-public" not in json.dumps(public)
    assert "never-public" not in json.dumps(report)


def test_exhausted_unpublished_dispatch_rearms_same_key(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, _, remote, _, clock, _ = args
    assert not advance(args)
    original_key = remote.posts[0][1]
    remote.receipt.update(state="failed", retryable=True)
    clock[0] += 31
    assert not advance(args)
    assert len(remote.posts) == 2
    assert {key for _, key in remote.posts} == {original_key}


def test_ineligible_empty_rows_are_not_reported_as_completed(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, _, remote, _, _, rows = args
    rows[1]["state"] = 10
    with pytest.raises(DomainError) as caught:
        advance(args)
    assert caught.value.code == "mt_scope"
    assert remote.posts == []


def test_retry_after_does_not_extend_absolute_observation_deadline(tmp_path, monkeypatch):
    args = recovery_fixture(tmp_path, monkeypatch)
    _, data, remote, _, clock, _ = args
    assert not advance(args)
    scope = data["mt_batches"]["kit-1:de"]
    scope["next_poll_at"] = clock[0] + 10000
    clock[0] += 7201
    scope["last_progress"] = clock[0] - 1
    with pytest.raises(DomainError) as caught:
        advance(args)
    assert caught.value.code == "weblate_unknown"
    assert len(remote.posts) == 1
