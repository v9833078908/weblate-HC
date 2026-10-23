# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import DomainError
from app.models import Job, Record
from app.services.quality_state import update_quality_cycle
from app.worker import process_once
from sqlalchemy import event
from test_quality_only_run import finish, start

pytest_plugins = ["test_quality_only_run"]


def test_worker_freezes_artifact_with_same_export_and_snapshot(quality_setup):
    store, settings, client, remote, body = quality_setup
    run_id = start(client, body).json()["id"]
    data = finish(store, settings, remote, run_id)
    assert data.get("quality_artifact_id") == "quality-artifact:" + run_id
    artifact = store.get("quality_artifact", data["quality_artifact_id"], "owner")
    exported = store.get("export", data["export_id"], "owner")
    assert artifact["export_id"] == data["export_id"]
    assert artifact["provenance"] == exported["quality_provenance"]
    assert artifact["issues"] == []
    assert artifact["summary"]["automatically_corrected"] == 2
    before = copy.deepcopy(artifact)
    remote.revisions["1"] = "later-human-edit"
    process_once(store, settings, remote)
    assert store.get("quality_artifact", data["quality_artifact_id"], "owner") == before


@pytest.mark.parametrize("failure", ["build", "insert", "cancel", "lease"])
def test_failed_publication_never_leaves_half_a_result(quality_setup, monkeypatch, failure):
    from app.services import quality_artifacts
    from app.services.runs import cancel_run

    store, settings, client, remote, body = quality_setup
    run_id = start(client, body).json()["id"]
    before = store.get("export", "export:parent", "owner")
    original = getattr(quality_artifacts, "build_quality_artifacts", None)
    assert original is not None, "Artifact publication must be implemented"

    def intercept(**kwargs):
        if failure == "build":
            raise DomainError("quality_report_test", "Synthetic report failure", 409)
        if failure == "cancel":
            cancel_run(store, "owner", run_id)
        if failure == "lease":
            with store.session() as session:
                session.get(Job, run_id).lease_until = 0
        return original(**kwargs)

    monkeypatch.setattr(quality_artifacts, "build_quality_artifacts", intercept)

    def refuse_insert(connection, cursor, statement, parameters, context, executemany):
        if (
            failure == "insert"
            and statement.startswith("INSERT INTO records")
            and "quality_artifact" in str(parameters)
        ):
            raise DomainError("quality_report_test", "Synthetic insert failure", 409)

    event.listen(store.engine, "before_cursor_execute", refuse_insert)
    try:
        for _ in range(60):
            process_once(store, settings, remote)
            with store.session() as session:
                job = session.get(Job, run_id)
                assert job.status != "completed"
                if job.status in {"failed", "cancelled"} or (job.data.get("phase") == "export"):
                    break
                job.lease_until = 0
                cycle_id = job.data.get("quality_cycle_id")
            if cycle_id:
                update_quality_cycle(store, cycle_id, next_wakeup=0)
        with store.session() as session:
            assert session.get(Record, "export:" + run_id) is None
            assert session.get(Record, "quality-artifact:" + run_id) is None
            assert session.get(Job, run_id).status != "completed"
        assert store.get("export", "export:parent", "owner") == before
    finally:
        event.remove(store.engine, "before_cursor_execute", refuse_insert)
