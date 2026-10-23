# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import getpass
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services.isolation_contract import (
    ARCHIVED,
    PREFIX,
    digest,
    payload_digest,
    validate_isolation_record,
)


def _record(row) -> dict:
    return {key: getattr(row, key) for key in ("id", "kind", "owner", "revision", "data")}


def has_isolation_marker(session, job) -> bool:
    from app.models import Record

    return job.status == ARCHIVED or session.get(Record, PREFIX + job.id) is not None


def validated_isolation(session, job) -> dict | None:
    from app.models import Record

    record = session.get(Record, PREFIX + job.id)
    if record is None and job.status != ARCHIVED:
        return None
    if record is None:
        raise ValueError("Archived job has no isolation record")
    raw = (
        session.connection()
        .exec_driver_sql("SELECT * FROM jobs WHERE id=?", (job.id,))
        .mappings()
        .one()
    )
    return validate_isolation_record(raw, _record(record))


def ensure_run_not_isolated(session, job) -> None:
    from app.db import DomainError

    if has_isolation_marker(session, job):
        raise DomainError(
            "run_isolated", "Run is isolated; resuming and paid actions are forbidden", 409
        )


def ensure_project_not_isolated(session, remote_slug: str, local_slug: str | None = None) -> None:
    from sqlalchemy import select

    from app.db import DomainError
    from app.models import Job, Record

    link = session.get(Record, "link:" + local_slug) if local_slug else None
    remotes = {remote_slug}
    if link:
        remotes.add(link.data.get("remote_slug"))
    remotes.discard(None)
    remotes.discard("")
    jobs = list(session.scalars(select(Job).where(Job.status == ARCHIVED)))
    records = list(
        session.scalars(
            select(Record).where((Record.kind == "run_isolation") | Record.id.startswith(PREFIX))
        )
    )
    by_id = {job.id: job for job in jobs}
    for record in records:
        ident = record.id.removeprefix(PREFIX)
        job = session.get(Job, ident)
        if not job:
            raise DomainError(
                "run_isolated", "Isolation journal requires operator reconciliation", 409
            )
        by_id[job.id] = job
        value = record.data if isinstance(record.data, dict) else {}
        # Both independently stored identities remain fenced after corruption.
        if value.get("remote_project") in remotes or (
            local_slug and value.get("project") == local_slug
        ):
            raise DomainError(
                "run_isolated", "This project has an isolated run; paid actions are forbidden", 409
            )
    for job in by_id.values():
        if job.data.get("remote_project") in remotes or (
            local_slug is not None and job.data.get("project") == local_slug
        ):
            raise DomainError(
                "run_isolated", "This project has an isolated run; paid actions are forbidden", 409
            )


def assert_project_not_isolated(
    store, remote_slug: str = "", local_slug: str | None = None
) -> None:
    if store is None:
        from app.db import DomainError

        raise DomainError("run_isolation", "Paid dispatch requires a local isolation journal", 409)
    with store.session() as session:
        ensure_project_not_isolated(session, remote_slug, local_slug)


def _verified_backup(manifest_path: Path) -> tuple[Path, dict, str]:
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    names = {"data/wizard.sqlite", "run-reconciliation.json", "restore-result.json"}
    if manifest.get("schema") != 1 or not names <= set(manifest.get("files", {})):
        raise ValueError("Verified backup, restoration and read-only reconciliation are required")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Backup checksum mismatch")
    restored = json.loads((root / "restore-result.json").read_text())
    if any(
        restored.get(key) != "ok"
        for key in ("database", "baseline_app", "login", "projects", "file_checksums")
    ):
        raise ValueError("Backup restoration was not verified")
    return (
        root,
        json.loads((root / "run-reconciliation.json").read_text()),
        hashlib.sha256(raw).hexdigest(),
    )


def _durable_snapshot(db) -> dict:
    schema = [
        tuple(row)
        for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
    ]
    result = {"schema": digest(schema)}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if name in {"sessions"} or name.startswith("sqlite_"):
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        columns = len(db.execute(f"PRAGMA table_info({quoted})").fetchall())
        order = ",".join(str(index + 1) for index in range(columns))
        where = " WHERE kind != 'heartbeat'" if name == "records" else ""
        checksum = hashlib.sha256()
        for row in db.execute(f"SELECT * FROM {quoted}{where} ORDER BY {order}"):
            checksum.update(repr(tuple(row)).encode())
        result[name] = checksum.hexdigest()
    return result


def isolate_run(
    database: Path,
    run_id: str,
    expected_payload_sha256: str,
    backup_manifest: Path,
    reason: str,
    *,
    apply: bool = False,
) -> dict:
    """Offline, fail-closed maintenance command. Its default never writes the database."""
    try:
        return _isolate_run(
            database, run_id, expected_payload_sha256, backup_manifest, reason, apply=apply
        )
    except (OSError, KeyError, TypeError, sqlite3.Error) as error:
        raise ValueError("Isolation inputs or database are invalid") from error


def _isolate_run(database, run_id, expected_payload_sha256, backup_manifest, reason, *, apply):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("A bounded archival reason is required")
    root, evidence, backup_sha = _verified_backup(backup_manifest)
    backup = root / "data/wizard.sqlite"
    database = Path(database).resolve()
    if database == backup.resolve():
        raise ValueError("Do not modify the verified backup")
    uri = database.as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Target job is missing")
        job = dict(row)
        if payload_digest(job) != expected_payload_sha256:
            raise ValueError("Job payload changed")
        previous = db.execute("SELECT * FROM records WHERE id=?", (PREFIX + run_id,)).fetchone()
        if previous is not None or job["status"] == ARCHIVED:
            value = validate_isolation_record(job, dict(previous) if previous else {})
            if value["reason"] != reason or value["backup_manifest_sha256"] != backup_sha:
                raise ValueError("Existing isolation identity cannot be overwritten")
            return {"run_id": run_id, "applied": False, "already_isolated": True}
        if job["status"] != "unknown" or job["lease_owner"] or job["lease_until"] > time.time():
            raise ValueError("Only an unleased unknown job can be isolated")
        with sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True) as baseline:
            baseline.row_factory = sqlite3.Row
            if baseline.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup database is corrupt")
            saved = baseline.execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone()
            if saved is None or dict(saved) != job:
                raise ValueError("Job changed since verified backup")
            if _durable_snapshot(db) != _durable_snapshot(baseline):
                raise ValueError("Durable snapshot changed since backup; create a fresh backup")
        data = json.loads(job["data"])
        expected = {
            "schema": 1,
            "run_id": run_id,
            "owner": job["owner"],
            "project": data["project"],
            "remote_project": data["remote_project"],
            "payload_sha256": expected_payload_sha256,
            "operations_sha256": digest(data["operations"]),
            "outcome": "provider_unknown",
            "live_lease": False,
            "pending": 0,
        }
        if any(
            evidence.get(key) != value or type(evidence.get(key)) is not type(value)
            for key, value in expected.items()
        ):
            raise ValueError("Read-only reconciliation does not match the unresolved operation")
        observed = evidence.get("observed_at")
        if type(observed) not in (float, int) or not 0 <= time.time() - observed <= 900:
            raise ValueError("Read-only reconciliation is stale")
        if db.execute(
            "SELECT 1 FROM jobs WHERE id<>? AND (status IN ('queued','running','unknown','cancel_requested') OR lease_owner<>'' OR lease_until>?)",
            (run_id, time.time()),
        ).fetchone():
            raise ValueError("Other active jobs prevent isolation")
        for (raw,) in db.execute("SELECT data FROM records WHERE kind='profile_generation'"):
            if json.loads(raw).get("state") in {"queued", "running", "unknown"}:
                raise ValueError("Unsettled profile generation prevents isolation")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (
            "quality_cycles" in tables
            and db.execute(
                "SELECT 1 FROM quality_cycles WHERE (job_id=? OR remote_project=?) AND (status NOT IN ('completed','failed','cancelled') OR lease_owner<>'' OR lease_until>?)",
                (run_id, data["remote_project"], time.time()),
            ).fetchone()
        ):
            raise ValueError("Related active quality cycle prevents isolation")
        if {"quality_cycles", "quality_operations"} <= tables and db.execute(
            "SELECT 1 FROM quality_operations o JOIN quality_cycles c ON c.id=o.cycle_id WHERE (c.job_id=? OR c.remote_project=?) AND o.state IN ('intent','accepted','unknown')",
            (run_id, data["remote_project"]),
        ).fetchone():
            raise ValueError("Related unsettled quality operation prevents isolation")
        value = {
            **{
                key: expected[key]
                for key in (
                    "schema",
                    "run_id",
                    "owner",
                    "project",
                    "remote_project",
                    "payload_sha256",
                    "operations_sha256",
                )
            },
            "idempotency_key": job["idempotency_key"],
            "original_status": job["status"],
            "lease_until": job["lease_until"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operator": getpass.getuser(),
            "reason": reason,
            "backup_manifest_sha256": backup_sha,
            "reconciliation_sha256": hashlib.sha256(
                (root / "run-reconciliation.json").read_bytes()
            ).hexdigest(),
        }
        record = {
            "id": PREFIX + run_id,
            "kind": "run_isolation",
            "owner": job["owner"],
            "revision": digest(value),
            "data": value,
        }
        validate_isolation_record({**job, "status": ARCHIVED}, record)
        if apply:
            db.execute(
                "INSERT INTO records(id,kind,owner,revision,data) VALUES(?,?,?,?,?)",
                (
                    record["id"],
                    record["kind"],
                    record["owner"],
                    record["revision"],
                    json.dumps(value),
                ),
            )
            db.execute("UPDATE jobs SET status=? WHERE id=?", (ARCHIVED, run_id))
        else:
            db.rollback()
        return {
            "run_id": run_id,
            "applied": apply,
            "already_isolated": False,
            "status": ARCHIVED,
            "payload_sha256": expected_payload_sha256,
        }
