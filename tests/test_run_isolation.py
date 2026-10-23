# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from app.db import DomainError, Store, revision
from app.models import Job, QualityCycle, Record
from app.services.runs import cancel_run, claim_job, ensure_project_available, get_run, resume_run


def deployment_module(name):
    path = Path(__file__).parents[1] / "deploy" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_job(database, ident="legacy"):
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone())


def payload_hash(database):
    return hashlib.sha256(raw_job(database)["data"].encode()).hexdigest()


def database_rows(database):
    with sqlite3.connect(database) as db:
        return {
            name: db.execute('SELECT * FROM "' + name + '" ORDER BY 1').fetchall()
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def seal_backup(database, root):
    root.mkdir()
    (root / "data").mkdir()
    backup = root / "data/wizard.sqlite"
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as dest:
        source.backup(dest)
    job = raw_job(database)
    value = json.loads(job["data"])
    evidence = {
        "schema": 1,
        "run_id": job["id"],
        "owner": job["owner"],
        "project": value["project"],
        "remote_project": value["remote_project"],
        "payload_sha256": payload_hash(database),
        "operations_sha256": revision(value["operations"]),
        "outcome": "provider_unknown",
        "live_lease": False,
        "pending": 0,
        "observed_at": time.time(),
    }
    (root / "run-reconciliation.json").write_text(json.dumps(evidence))
    (root / "restore-result.json").write_text(
        json.dumps(
            dict.fromkeys(("database", "baseline_app", "login", "projects", "file_checksums"), "ok")
        )
    )
    manifest = {
        "schema": 1,
        "release": "fixture",
        "files": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("data/wizard.sqlite", "run-reconciliation.json", "restore-result.json")
        },
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Isolation must never use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    store = Store(tmp_path / "current")
    data = {
        "project": "local",
        "remote_project": "protected-remote",
        "created": "2026-10-21T00:00:00+00:00",
        "started": None,
        "finished": None,
        "attempt": 1,
        "operations": {"mt:kit-3:de": {"state": "intent", "key": "paid-key"}},
        "snapshot": {"manifest": {"source": "ru", "targets": ["de"], "components": []}},
        "warnings": [],
        "failure": "Unknown provider result",
        "phase": "translate",
        "export_id": None,
    }
    with store.session() as session:
        session.add(
            Job(
                id="legacy",
                owner="producer",
                idempotency_key="paid-run",
                status="unknown",
                data=data,
            )
        )
        session.add(
            Record(
                id="unrelated",
                owner="other",
                kind="project",
                revision="kept",
                data={"translations": 1310},
            )
        )
    database = store.data_dir / "wizard.sqlite"
    manifest = seal_backup(database, tmp_path / "backup")
    yield store, database, manifest
    store.engine.dispose()


def isolate(legacy, *, apply=False, reason="Preserve unresolved provider outcome"):
    from app.services.run_isolation import isolate_run

    _, database, manifest = legacy
    return isolate_run(database, "legacy", payload_hash(database), manifest, reason, apply=apply)


def test_dry_run_and_apply_only_archive_target_and_seal_journal(legacy):
    store, database, _ = legacy
    before = database_rows(database)
    preview = isolate(legacy)
    assert preview["applied"] is False
    assert database_rows(database) == before
    applied = isolate(legacy, apply=True)
    assert applied["applied"] is True
    assert raw_job(database)["status"] == "archived_unknown"
    assert raw_job(database)["data"] == before["jobs"][0][-1]
    after = database_rows(database)
    for table in before.keys() - {"jobs", "records"}:
        assert after[table] == before[table]
    assert before["records"][0] in after["records"]
    assert len(after["records"]) == len(before["records"]) + 1
    assert isolate(legacy, apply=True)["already_isolated"] is True
    assert database_rows(database) == after
    with pytest.raises(ValueError):
        isolate(legacy, apply=True, reason="Different reason")
    public = get_run(store, "producer", "legacy")
    assert public["status"] == "archived_unknown"
    assert public["resume_allowed"] is False
    assert public["outcome"] is None


@pytest.mark.parametrize(
    "mutation", ["lease", "payload", "checksum", "restore", "evidence", "another_job", "quality"]
)
def test_isolation_refuses_unverified_or_live_state(legacy, mutation):
    store, database, manifest = legacy
    expected = payload_hash(database)
    if mutation in {"lease", "payload", "another_job", "quality"}:
        with store.session() as session:
            job = session.get(Job, "legacy")
            if mutation == "lease":
                job.lease_until, job.lease_owner = time.time() + 100, "active-worker"
            elif mutation == "payload":
                job.data = {**job.data, "attempt": 2}
            elif mutation == "another_job":
                session.add(
                    Job(
                        id="other",
                        owner="other",
                        idempotency_key="other",
                        status="queued",
                        data=copy.deepcopy(job.data),
                    )
                )
            else:
                session.add(
                    QualityCycle(
                        id="cycle",
                        owner="producer",
                        job_id="legacy",
                        remote_project="protected-remote",
                        policy_revision="p",
                        policy={},
                        phase="dual-judge-start",
                        status="queued",
                    )
                )
    else:
        path = (
            manifest.parent
            / {
                "checksum": "data/wizard.sqlite",
                "restore": "restore-result.json",
                "evidence": "run-reconciliation.json",
            }[mutation]
        )
        if mutation == "checksum":
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            value = json.loads(path.read_text())
            value.update({"database": "failed"} if mutation == "restore" else {"pending": 1})
            path.write_text(json.dumps(value))
            m = json.loads(manifest.read_text())
            m["files"][str(path.relative_to(manifest.parent)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            manifest.write_text(json.dumps(m))
    before = database_rows(database)
    from app.services.run_isolation import isolate_run

    with pytest.raises(ValueError):
        isolate_run(database, "legacy", expected, manifest, "Operator archive", apply=True)
    assert database_rows(database) == before


@pytest.mark.parametrize(
    "corruption",
    ["none", "queued", "missing_record", "bad_revision", "wrong_remote", "wrong_owner"],
)
def test_archival_fence_survives_corruption_and_cannot_block_other_projects(legacy, corruption):
    store, database, _ = legacy
    isolate(legacy, apply=True)
    with store.session() as session:
        job, record = session.get(Job, "legacy"), session.get(Record, "run-isolation:legacy")
        if corruption == "queued":
            job.status = "queued"
        elif corruption == "missing_record":
            session.delete(record)
        elif corruption == "bad_revision":
            record.revision = "broken"
        elif corruption == "wrong_remote":
            record.data = {**record.data, "remote_project": "different"}
        elif corruption == "wrong_owner":
            record.owner = "other"
    before = raw_job(database)
    assert claim_job(store, "worker") is None
    for action in (
        lambda: resume_run(store, "producer", "legacy", 1),
        lambda: cancel_run(store, "producer", "legacy"),
    ):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.code == "run_isolated"
    assert raw_job(database) == before
    with store.session() as session:
        with pytest.raises(DomainError):
            ensure_project_available(
                session, "different-owner", "new-alias", remote_slug="protected-remote"
            )
        if corruption in {"none", "queued", "missing_record"}:
            ensure_project_available(session, "other", "unrelated", remote_slug="unrelated")


def test_isolated_project_blocks_quality_reservation_before_any_new_intent(legacy):
    store, _, _ = legacy
    from app.services.quality_state import reserve_quality_operation

    isolate(legacy, apply=True)
    with store.session() as session:
        session.add(
            QualityCycle(
                id="cycle",
                owner="other",
                job_id="new-job",
                remote_project="protected-remote",
                policy_revision="p",
                policy={},
                phase="dual-judge-start",
                status="queued",
            )
        )
    with pytest.raises(DomainError) as error:
        reserve_quality_operation(store, "cycle", "judge:0", "judge-start")
    assert error.value.code == "run_isolated"


def test_preflight_accepts_only_valid_isolation_and_still_blocks_another_unknown(legacy):
    store, database, _ = legacy
    isolate(legacy, apply=True)
    module = deployment_module("preflight")
    module.assert_quiet(database)
    with store.session() as session:
        job = session.get(Job, "legacy")
        session.add(
            Job(
                id="another",
                owner="other",
                idempotency_key="another",
                status="unknown",
                data=copy.deepcopy(job.data),
            )
        )
    with pytest.raises(ValueError):
        module.assert_quiet(database)


def test_isolated_profile_queue_does_not_starve_an_unrelated_project(legacy):
    from app.services.profile_jobs import process_profile_once
    from app.settings import Settings

    store, _, _ = legacy
    isolate(legacy, apply=True)
    with store.session() as session:
        for ident, project, created in (
            ("blocked-profile", "local", "2026-01-01"),
            ("other-profile", "unrelated", "2026-01-02"),
        ):
            value = {
                "state": "queued",
                "project": project,
                "created": created,
                "input": {"upload_token": "missing"},
                "warnings": [],
            }
            session.add(
                Record(
                    id=ident,
                    kind="profile_generation",
                    owner="producer",
                    revision=revision(value),
                    data=value,
                )
            )

    def forbidden(*args):
        raise AssertionError("No model call is needed to reject a stale unrelated profile")

    assert process_profile_once(store, Settings(), generator=forbidden) is True
    with store.session() as session:
        assert session.get(Record, "blocked-profile").data["state"] == "queued"
        assert session.get(Record, "other-profile").data["state"] == "stale"


def test_offline_cli_requires_only_standard_library_and_keeps_payload(legacy):
    _, database, manifest = legacy
    before = raw_job(database)["data"]
    script = Path(__file__).parents[1] / "deploy/isolate_legacy_run.py"
    command = [
        sys.executable,
        "-S",
        str(script),
        str(database),
        "legacy",
        "--expected-payload-sha256",
        payload_hash(database),
        "--backup-manifest",
        str(manifest),
        "--reason",
        "Offline isolation",
    ]
    preview = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(preview.stdout)["applied"] is False
    assert raw_job(database)["status"] == "unknown"
    applied = subprocess.run([*command, "--apply"], check=True, capture_output=True, text=True)
    assert json.loads(applied.stdout)["applied"] is True
    assert raw_job(database)["data"] == before


def test_read_only_rollback_also_refuses_corrupt_archive_journal(legacy):
    store, database, _ = legacy
    isolate(legacy, apply=True)
    with store.session() as session:
        session.get(Record, "run-isolation:legacy").revision = "corrupt"
    script = Path(__file__).parents[1] / "deploy/preflight.py"
    result = subprocess.run(
        [sys.executable, "-S", str(script), str(database), "--read-only-rollback"],
        capture_output=True,
    )
    assert result.returncode != 0


def test_sqlite_lock_race_rechecks_live_lease_after_operator_backup(legacy):
    from concurrent.futures import ThreadPoolExecutor

    from app.services.run_isolation import isolate_run

    _, database, manifest = legacy
    expected = payload_hash(database)
    with sqlite3.connect(database) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute("BEGIN IMMEDIATE")
        future = pool.submit(
            isolate_run, database, "legacy", expected, manifest, "Archive", apply=True
        )
        writer.execute(
            "UPDATE jobs SET lease_owner='winner',lease_until=? WHERE id='legacy'",
            (time.time() + 60,),
        )
        writer.commit()
        with pytest.raises(ValueError):
            future.result(timeout=35)
    assert raw_job(database)["status"] == "unknown"


def test_only_the_isolated_project_is_skipped_when_claiming_jobs(legacy):
    store, _, _ = legacy
    isolate(legacy, apply=True)
    with store.session() as session:
        data = copy.deepcopy(session.get(Job, "legacy").data)
        for ident, remote in (
            ("a-same-remote", "protected-remote"),
            ("z-other-remote", "unrelated"),
        ):
            value = {**data, "project": ident, "remote_project": remote}
            session.add(
                Job(
                    id=ident,
                    owner="different-owner",
                    idempotency_key=ident,
                    status="queued",
                    data=value,
                )
            )
    assert claim_job(store, "worker")["id"] == "z-other-remote"


def test_late_cancel_callback_cannot_overwrite_a_new_isolation_marker(legacy):
    from app.settings import Settings
    from app.worker import Cancelled, process_once

    store, _, _ = legacy
    with store.session() as session:
        job = session.get(Job, "legacy")
        data = copy.deepcopy(job.data)
        data["snapshot"].update(
            engine="openrouter", quality={"enabled": True, "workflow_version": 2}
        )
        job.data, job.status = data, "queued"

    class InterruptedRemote:
        def producer_me(self):
            with store.session() as session:
                session.add(
                    Record(
                        id="run-isolation:legacy",
                        kind="run_isolation",
                        owner="producer",
                        revision=revision({}),
                        data={},
                    )
                )
            raise Cancelled()

    assert (
        process_once(
            store,
            Settings(quality_cycle_enabled=True, full_quality_cycle_enabled=True),
            InterruptedRemote(),
        )
        is True
    )
    with store.session() as session:
        assert session.get(Job, "legacy").status == "running"
        assert session.get(Record, "run-isolation:legacy") is not None
    assert get_run(store, "producer", "legacy")["resume_allowed"] is False


def test_durable_mt_cannot_dispatch_without_local_isolation_authority():
    from app.services.mt_batches import advance_mt_batches

    with pytest.raises(DomainError) as caught:
        advance_mt_batches(
            None,
            "job",
            object(),
            data={"remote_project": "protected"},
            checkpoint=None,
            settings=None,
            component="kit",
            language="de",
        )
    assert caught.value.code == "run_isolation"


def test_changed_unrelated_durable_data_requires_a_fresh_backup(legacy):
    store, _, _ = legacy
    with store.session() as session:
        session.get(Record, "unrelated").data = {"translations": 1311}
    with pytest.raises(ValueError, match="backup|snapshot"):
        isolate(legacy, apply=True)


def test_offline_isolation_also_operates_on_original_0001_schema(legacy, tmp_path):
    store, database, _ = legacy
    store.engine.dispose()
    with sqlite3.connect(database) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.startswith("quality_") or name == "run_unit_progress":
                db.execute('DROP TABLE "' + name + '"')
        db.execute("UPDATE alembic_version SET version_num='0001'")
    manifest = seal_backup(database, tmp_path / "old-schema-backup")
    from app.services.run_isolation import isolate_run

    assert (
        isolate_run(database, "legacy", payload_hash(database), manifest, "Old schema", apply=True)[
            "applied"
        ]
        is True
    )
