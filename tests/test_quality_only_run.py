# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import hashlib
import uuid

import pytest
from app.main import create_app
from app.models import Job, Record
from app.services.exports import build_export
from app.services.quality_state import update_quality_cycle
from app.settings import Settings
from app.worker import process_once
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_full_quality_cycle import FullRemote


class PreparedCopy(FullRemote):
    def get_project(self, project):
        assert project == "remote-copy"
        return {"slug": project, "name": "Copy", "translation_review": True}

    def collect(self, path):
        if path == "projects/remote-copy/components/":
            return [
                {"slug": "copy-ui", "name": "UI", "source_language": "en", "file_format": "po-mono"}
            ]
        assert path == "components/remote-copy/copy-ui/translations/"
        return [{"language": "en"}, {"language": "de"}]

    def request(self, method, path):
        assert method == "GET" and path == "projects/remote-copy/machinery_settings/"
        return {}

    def list_units(self, project, component, language):
        assert (project, component) == ("remote-copy", "copy-ui")
        if language == "en":
            return [
                {"id": 100 + i, "source": [key], "target": ["Play"], "context": key}
                for i, key in enumerate(("one", "two"), 1)
            ]
        assert language == "de"
        values = self.download_translation(project, component, language)
        return [
            {"id": i, "source_unit": 100 + i, "target": [values[key]]}
            for i, key in enumerate(("one", "two"), 1)
        ]

    def download_translation(self, project, component, language):
        assert (project, component, language) == ("remote-copy", "copy-ui", "de")
        return {
            "one": "corrected" if self.revisions["1"] != "r1" else "old",
            "two": "corrected" if self.repeat_applied else "other",
        }


@pytest.fixture
def quality_setup(tmp_path, monkeypatch):
    from app.routes import runs

    settings = Settings(
        data_dir=tmp_path,
        external_writes_enabled=True,
        quality_cycle_enabled=True,
        full_quality_cycle_enabled=True,
    )
    app = create_app(settings)
    store = app.state.store
    store.create_user("owner", "test-long-password")
    remote = PreparedCopy()
    monkeypatch.setattr(runs, "WeblateClient", lambda _settings: remote, raising=False)
    manifest = {
        "source": "en",
        "targets": ["de"],
        "quarantine": [],
        "components": [
            {
                "id": "kit-1",
                "file": "kit.csv",
                "sheet": "kit",
                "rows": [["key", "en", "de"], ["one", "Play", "old"], ["two", "Play", "other"]],
                "profile": {
                    "header_row": 1,
                    "languages": [{"code": "en", "column": 2}, {"code": "de", "column": 3}],
                },
                "units": [
                    {"key": key, "row": i, "values": {"en": "Play", "de": value}}
                    for i, (key, value) in enumerate((("one", "old"), ("two", "other")), 2)
                ],
            }
        ],
    }
    for slug, run_id, remote_slug in (
        ("original", "parent", "remote-original"),
        ("copy", "prepared", "remote-copy"),
    ):
        store.save("project", slug, "owner", {"slug": slug, "name": slug, "persistent": True})
        exported = build_export(manifest, {})
        store.save("export", "export:" + run_id, "owner", exported)
        store.save(
            "export_pointer",
            "export-pointer:" + slug,
            "owner",
            {"run_id": run_id, "export_id": "export:" + run_id},
        )
        with store.session() as session:
            session.add(
                Job(
                    id=run_id,
                    owner="owner",
                    idempotency_key=run_id,
                    status="completed",
                    data={
                        "project": slug,
                        "remote_project": remote_slug,
                        "snapshot": {
                            "manifest": copy.deepcopy(manifest),
                            "engine": "openrouter",
                            "persistent": True,
                        },
                        "operations": {
                            "project": {"state": "confirmed", "result": {"slug": remote_slug}}
                        },
                        "created": "2026-10-20T00:00:00Z",
                        "started": None,
                        "finished": "2026-10-20T01:00:00Z",
                        "attempt": 1,
                        "failure": "",
                        "warnings": [],
                        "phase": "completed",
                        "export_id": "export:" + run_id,
                    },
                )
            )
    store.save(
        "project_link",
        "link:copy",
        "owner",
        {
            "project": "copy",
            "remote_slug": "remote-copy",
            "origin": "created-v2",
            "read_only": False,
            "component_map": {"kit.csv\u001fkit": "copy-ui"},
            "snapshot": {
                "languages": ["de"],
                "components": {
                    "copy-ui": {
                        "name": "UI",
                        "source": "en",
                        "languages": ["de"],
                        "units": [
                            {"key": "one", "source": "Play", "targets": {"de": "old"}},
                            {"key": "two", "source": "Play", "targets": {"de": "other"}},
                        ],
                    }
                },
            },
        },
    )
    body = {
        "parent_run_id": "parent",
        "language": "de",
        "scope": {"unit_ids": [1, 2]},
        "glossary_revision": "scope-before",
        "client_request_id": str(uuid.uuid4()),
    }
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "owner", "password": "test-long-password"}
        ).json()["csrf_token"]
        client.headers["X-CSRFToken"] = csrf
        yield store, settings, client, remote, body


def start(client, body, slug="copy"):
    return client.post(f"/api/producer/projects/{slug}/quality-runs/", json=body)


def finish(store, settings, remote, run_id):
    for _ in range(75):
        process_once(store, settings, remote)
        with store.session() as session:
            job = session.get(Job, run_id)
            assert job.status not in {"failed", "unknown"}, job.data
            if job.status == "completed":
                return copy.deepcopy(job.data)
            job.lease_until = 0
            cycle = job.data.get("quality_cycle_id")
        if cycle:
            update_quality_cycle(store, cycle, next_wakeup=0)
    pytest.fail("Quality-only job did not complete")


def test_quality_only_never_calls_mt(quality_setup):
    store, settings, client, remote, body = quality_setup
    result = start(client, body)
    assert result.status_code == 202, result.text
    assert result.json()["requested_mode"] == "quality"
    data = finish(store, settings, remote, result.json()["id"])
    assert data["quality"]["status"] == "completed"
    assert remote.starts == [[1, 2], [2]]
    assert data["operations"] == {}
    exported = store.get("export", data["export_id"], "owner")
    assert exported["sheets"][0]["rows"][1:] == [
        ["one", "Play", "corrected"],
        ["two", "Play", "corrected"],
    ]
    run_file = client.get(f"/api/producer/runs/{result.json()['id']}/export/?format=json").json()
    assert run_file["sheets"][0]["rows"][1][2] == "corrected"


def test_verify_preview_and_start_preserve_read_only_link_and_ready_job(quality_setup):
    store, settings, client, remote, body = quality_setup
    original_me = remote.producer_me
    remote.producer_me = lambda: {
        "capabilities": {
            **original_me()["capabilities"],
            "quality_execution_policy": 1,
            "verify_only_immutable": 1,
        }
    }
    original_estimate = remote.judge_estimate
    remote.judge_estimate = lambda project, scope, **kwargs: {
        **original_estimate(project, scope),
        "execution_mode": kwargs["execution_mode"],
    }
    original_start = remote.start_quality_judge
    remote.start_quality_judge = lambda project, estimate, scope, key, **kwargs: (
        original_start(project, estimate, scope, key)
        if kwargs["execution_mode"] == "verify_only"
        else pytest.fail("Wrong mode")
    )
    original_snapshot = remote.quality_snapshot
    remote.quality_snapshot = lambda *args: {
        **original_snapshot(*args),
        "protected_content_digest": "unchanged-protected-content",
    }
    with store.session() as session:
        link = session.get(Record, "link:copy")
        link.data = {**link.data, "read_only": True}
    response = client.get("/api/producer/runs/prepared/verification-preview/?language=de")
    assert response.status_code == 200, response.text
    preview = response.json()
    payload = {**preview["request"], "client_request_id": str(uuid.uuid4())}
    result = client.post("/api/producer/projects/copy/quality-runs/", json=payload)
    assert result.status_code == 202, result.text
    assert result.json()["execution_mode"] == "verify_only"
    with store.session() as session:
        assert session.get(Record, "link:copy").data["read_only"] is True
        assert session.get(Job, "prepared").status == "completed"
        assert session.get(Job, result.json()["id"]).data["operations"] == {}
    before = remote.download_translation("remote-copy", "copy-ui", "de")
    data = finish(store, settings, remote, result.json()["id"])
    assert remote.download_translation("remote-copy", "copy-ui", "de") == before
    assert remote.applies == []
    assert remote.repeat_keys == []
    assert data["quality"]["counts"]["automatically_corrected"] == 0


def test_completed_parent_is_immutable(quality_setup):
    store, settings, client, remote, body = quality_setup
    with store.session() as session:
        parent_before = copy.deepcopy(session.get(Job, "parent").data)
    old_export = store.get("export", "export:parent", "owner")
    old_pointer = store.get("export_pointer", "export-pointer:original", "owner")
    payload = client.get("/api/producer/runs/parent/export/?format=json").content
    digest = hashlib.sha256(payload).hexdigest()
    result = start(client, body)
    assert result.status_code == 202, result.text
    finish(store, settings, remote, result.json()["id"])
    with store.session() as session:
        parent = session.get(Job, "parent")
        assert parent.status == "completed"
        assert parent.data == parent_before
    assert store.get("export", "export:parent", "owner") == old_export
    assert store.get("export_pointer", "export-pointer:original", "owner") == old_pointer
    assert (
        hashlib.sha256(
            client.get("/api/producer/runs/parent/export/?format=json").content
        ).hexdigest()
        == digest
    )


def test_duplicate_request_returns_same_run(quality_setup):
    store, settings, client, remote, body = quality_setup
    first = start(client, body)
    assert first.status_code == 202, first.text
    assert first.json()["translation_coverage"] == {"filled": 2, "total": 2, "missing": 0}
    assert start(client, body).json()["id"] == first.json()["id"]
    finish(store, settings, remote, first.json()["id"])
    assert start(client, body).json()["id"] == first.json()["id"]
    assert remote.starts == [[1, 2], [2]]
    assert start(client, {**body, "language": "fr"}).status_code == 409


@pytest.mark.parametrize(
    "defect",
    [
        "original",
        "foreign_parent",
        "foreign_unit",
        "partial_scope",
        "connected",
        "changed_source",
        "empty",
        "stale_glossary",
    ],
)
def test_foreign_or_original_project_scope_is_rejected(quality_setup, defect):
    store, _settings, client, remote, body = quality_setup
    if defect == "foreign_parent":
        with store.session() as session:
            session.get(Job, "parent").owner = "other"
    elif defect in {"foreign_unit", "partial_scope"}:
        body["scope"]["unit_ids"] = [999] if defect == "foreign_unit" else [1]
    elif defect == "connected":
        with store.session() as session:
            record = session.get(Record, "link:copy")
            record.data = {**record.data, "read_only": True, "origin": "connected"}
    elif defect == "changed_source":
        original = remote.list_units

        def changed(*args):
            rows = original(*args)
            if args[-1] == "en":
                rows[0]["target"] = ["Different source"]
            return rows

        remote.list_units = changed
    elif defect == "empty":
        remote.download_translation = lambda *args: {"one": "", "two": "other"}
    elif defect == "stale_glossary":
        body["glossary_revision"] = "stale"
    result = start(client, body, "original" if defect == "original" else "copy")
    assert result.status_code in {403, 404, 409}, result.text
    expected = {
        "original": "quality_copy_required",
        "foreign_parent": "not_found",
        "foreign_unit": "quality_scope",
        "partial_scope": "quality_scope",
        "connected": "quality_copy_required",
        "changed_source": "quality_source_changed",
        "empty": "quality_input_incomplete",
        "stale_glossary": "quality_glossary_changed",
    }
    assert result.json()["code"] == expected[defect]
    with store.session() as session:
        assert len(session.scalars(select(Job)).all()) == 2
    assert remote.starts == []


@pytest.mark.parametrize("scope", [[1, 2, 2], [True, 2], [1, "2"], [1, -2]])
def test_scope_does_not_coerce_or_duplicate_unit_ids(quality_setup, scope):
    _store, _settings, client, remote, body = quality_setup
    body["scope"]["unit_ids"] = scope
    result = start(client, body)
    assert result.status_code == 422
    assert remote.starts == []


def test_quality_only_restart_observes_lost_apply_without_repeating_it(quality_setup):
    from app.db import Store

    store, settings, client, remote, body = quality_setup
    result = start(client, body)
    assert result.status_code == 202, result.text
    remote.timeout_apply = True
    for _ in range(75):
        store.engine.dispose()
        store = Store(settings.data_dir)
        process_once(store, settings, remote)
        with store.session() as session:
            job = session.get(Job, result.json()["id"])
            assert job.status not in {"failed", "unknown"}, job.data
            if job.status == "completed":
                break
            job.lease_until = 0
            cycle_id = job.data.get("quality_cycle_id")
        if cycle_id:
            update_quality_cycle(store, cycle_id, next_wakeup=0)
    else:
        pytest.fail("Restarted job did not complete")
    assert len(remote.applies) == 1
    assert remote.starts == [[1, 2], [2]]


@pytest.mark.parametrize("defect", ["writes_disabled", "quality_disabled", "no_csrf", "remote_url"])
def test_quality_only_cannot_bypass_operator_or_request_boundary(quality_setup, defect):
    _store, settings, client, remote, body = quality_setup
    if defect == "writes_disabled":
        settings.external_writes_enabled = False
    elif defect == "quality_disabled":
        settings.full_quality_cycle_enabled = False
    elif defect == "no_csrf":
        del client.headers["X-CSRFToken"]
    elif defect == "remote_url":
        body["remote_project"] = "http://untrusted.invalid"
    result = start(client, body)
    assert (
        result.status_code
        == {"writes_disabled": 403, "quality_disabled": 409, "no_csrf": 403, "remote_url": 422}[
            defect
        ]
    )
    assert remote.starts == []


@pytest.mark.parametrize(
    "missing,engine,selected", [(1, "openrouter", 2), (1, None, 2), (0, None, 1)]
)
def test_missing_translation_in_judge_estimate_never_starts_paid_work(
    quality_setup, missing, engine, selected
):
    store, settings, client, remote, body = quality_setup
    result = start(client, body)
    assert result.status_code == 202, result.text
    original = remote.judge_estimate

    def changed(*args):
        estimate = original(*args)
        estimate["preparation"].update(missing=missing, engine=engine)
        estimate["selected"] = selected
        return estimate

    remote.judge_estimate = changed
    for _ in range(3):
        process_once(store, settings, remote)
        with store.session() as session:
            session.get(Job, result.json()["id"]).lease_until = 0
    assert remote.starts == []
    with store.session() as session:
        job = session.get(Job, result.json()["id"])
        assert job.status == "failed"
        assert job.data["export_id"] is None


def test_worker_rechecks_binding_before_any_quality_work(quality_setup):
    store, settings, client, remote, body = quality_setup
    result = start(client, body)
    assert result.status_code == 202, result.text
    with store.session() as session:
        record = session.get(Record, "link:copy")
        record.data = {**record.data, "remote_slug": "remote-original"}
    process_once(store, settings, remote)
    with store.session() as session:
        job = session.get(Job, result.json()["id"])
        assert job.status == "failed"
        assert job.data["export_id"] is None
    assert remote.starts == []


def test_export_revalidates_source_inside_the_revision_window(quality_setup, monkeypatch):
    from app.services import quality_export

    store, settings, client, remote, body = quality_setup
    result = start(client, body)
    assert result.status_code == 202, result.text
    actual_download = quality_export.download_stable_quality_export
    original_units = remote.list_units

    def changed_units(*args):
        rows = original_units(*args)
        if args[-1] == "en":
            rows[0]["target"] = ["Changed before export revision A"]
        return rows

    def racing_download(*args, **kwargs):
        remote.list_units = changed_units
        return actual_download(*args, **kwargs)

    monkeypatch.setattr(quality_export, "download_stable_quality_export", racing_download)
    for _ in range(75):
        process_once(store, settings, remote)
        with store.session() as session:
            job = session.get(Job, result.json()["id"])
            if job.status in {"completed", "failed"}:
                assert job.status == "failed", job.data
                assert job.data["export_id"] is None
                assert job.data["failure_code"] == "quality_source_changed"
                return
            job.lease_until = 0
            cycle = job.data.get("quality_cycle_id")
        if cycle:
            update_quality_cycle(store, cycle, next_wakeup=0)
    pytest.fail("Export race was not resolved")


@pytest.mark.parametrize("kind", ["judge", "repeat", "unknown"])
def test_cancelled_quality_work_reserves_copy_until_remote_completion(quality_setup, kind):
    from app.services.quality_state import (
        create_quality_cycle,
        reserve_quality_operation,
        transition_quality_operation,
    )

    store, settings, client, remote, body = quality_setup
    started = start(client, body)
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    cycle = create_quality_cycle(
        store, "owner", run_id, "remote-copy", {"revision": "cancel-test", "workflow_version": 2}
    )
    key = "full-repeat-apply:0" if kind == "repeat" else "full-judge:0"
    op = reserve_quality_operation(store, cycle["id"], key, "full-quality-write")
    result = (
        {"token": "pending-repeat", "status": "running"}
        if kind == "repeat"
        else {"id": "pending-judge", "status": "queued"}
    )
    transition_quality_operation(store, op["id"], "accepted")
    transition_quality_operation(
        store,
        op["id"],
        "unknown" if kind == "unknown" else "confirmed",
        data={} if kind == "unknown" else {"result": result},
    )
    with store.session() as session:
        job = session.get(Job, run_id)
        job.data = {**job.data, "quality_cycle_id": cycle["id"]}
    assert client.post(f"/api/producer/runs/{run_id}/cancel/", json={}).status_code == 200
    process_once(store, settings, remote)
    terminal = [False]
    remote.get_producer_run = lambda _run: {
        "id": "pending-judge",
        "status": "completed" if terminal[0] else "running",
    }
    remote.get_repeat_run = lambda _run: {
        "token": "pending-repeat",
        "status": "completed" if terminal[0] else "running",
        "changed_unit_ids": [],
        "results": [],
    }
    remote.observe_quality_operation = lambda project, key: (
        {"state": "confirmed", "result": {"id": "pending-judge", "status": "completed"}}
        if terminal[0]
        else None
    )
    replacement = {**body, "client_request_id": str(uuid.uuid4())}
    refused = start(client, replacement)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "run_active"
    terminal[0] = True
    accepted = start(client, replacement)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["id"] != run_id
    assert remote.starts == []
