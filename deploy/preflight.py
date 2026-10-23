# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.isolation_contract import ARCHIVED, PREFIX, validate_isolation_record

# Canonical SQLite table/index definitions from the closed 0002–0004 migrations.
# Normalize whitespace only; unknown columns, constraints, indexes or triggers fail closed.
ADDITIVE_SCHEMA_DIGESTS = {
    "quality_cycles": "1bceab8cd50c2ea1fcea205a541b085ccffcb497730cdb3899fcc1c4b02d0a89",
    "quality_operations": "aa79afc145eba72ddee65b72aca7688dca1f3177743ec60c1fe2339cc33d85b6",
    "quality_units": "3bf9db98978249ee727256e6f1457315146299068fc05dd4dc501de86e35a4f8",
    "quality_events": "a3d4416ff5984a91384818af4be615180915200d67497995fe45ad54cd3beb03",
    "quality_groups": "d6408a21ff123c0aaf1b416968a2f42b96097afb5c7b4404165501af1dd592da",
    "run_unit_progress": "e425a6927307b15f5c2f7b46159c396ef58043902b577aad512d6fb208217c52",
}


def schema_fingerprint(db, table):
    rows = [
        (kind, name, " ".join((sql or "").split()))
        for kind, name, sql in db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE tbl_name=? ORDER BY type,name",
            (table,),
        )
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def isolation_ids(db):
    records = {
        row["id"]: dict(row)
        for row in db.execute(
            "SELECT * FROM records WHERE kind='run_isolation' OR id LIKE 'run-isolation:%'"
        )
    }
    validated = set()
    for row in db.execute("SELECT * FROM jobs"):
        job = dict(row)
        ident = PREFIX + job["id"]
        if job["status"] == ARCHIVED or ident in records:
            validate_isolation_record(job, records.get(ident, {}))
            validated.add(ident)
    if set(records) != validated:
        raise ValueError("Orphan isolation record prevents maintenance")
    return {ident.removeprefix(PREFIX) for ident in validated}


def validate_isolations(path):
    with readonly(path) as db:
        db.row_factory = sqlite3.Row
        return isolation_ids(db)


def assert_quiet(path, *, allow_profile_unknown=False):
    with readonly(path) as db:
        db.row_factory = sqlite3.Row
        isolated = isolation_ids(db)
        for row in db.execute("SELECT * FROM jobs"):
            job = dict(row)
            if job["id"] in isolated:
                continue
            value = json.loads(job["data"])
            if job["status"] in {"queued", "running", "unknown", "cancel_requested"} or any(
                op["state"] in {"intent", "accepted"} for op in value.get("operations", {}).values()
            ):
                raise ValueError("Unsettled external operation prevents maintenance")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (
            "quality_cycles" in tables
            and db.execute(
                "SELECT 1 FROM quality_cycles WHERE status IN ('queued','running','waiting','unknown')"
            ).fetchone()
        ):
            raise ValueError("Unsettled quality cycle prevents maintenance")
        if not allow_profile_unknown:
            for (data,) in db.execute("select data from records where kind='profile_generation'"):
                if json.loads(data).get("state") in {"running", "unknown"}:
                    raise ValueError("Unsettled profile LLM operation prevents maintenance")


def ensure_compatible(current, baseline, *, allow_recovery_addition=False):
    def schema(path):
        with readonly(path) as db:
            return (
                db.execute(
                    "select name,sql from sqlite_master where type='table' order by name"
                ).fetchall(),
                db.execute("select version_num from alembic_version").fetchall(),
                {
                    name: schema_fingerprint(db, name)
                    for (name,) in db.execute("select name from sqlite_master where type='table'")
                },
            )

    current_schema, baseline_schema = schema(current), schema(baseline)
    if (
        allow_recovery_addition
        and current_schema[1] == [("0004",)]
        and baseline_schema[1] in ([("0001",)], [("0002",)], [("0003",)])
    ):
        baseline_revision = baseline_schema[1][0][0]
        additions = {
            "0002": {"quality_cycles", "quality_operations", "quality_units", "quality_events"},
            "0003": {"quality_groups"},
            "0004": {"run_unit_progress"},
        }
        allowed = set().union(
            *(names for version, names in additions.items() if version > baseline_revision)
        )
        if not allowed <= {row[0] for row in current_schema[0]}:
            raise ValueError("Database schema is missing known additive tables")
        if any(current_schema[2][name] != ADDITIVE_SCHEMA_DIGESTS[name] for name in allowed):
            raise ValueError("Database schema contains unknown additive definitions")
        current_schema = (
            [row for row in current_schema[0] if row[0] not in allowed],
            baseline_schema[1],
            {name: digest for name, digest in current_schema[2].items() if name not in allowed},
        )
    if current_schema != baseline_schema:
        raise ValueError("Database schema differs from the rollback baseline")


def ensure_data_unchanged(current, baseline):
    # Liveness/session rows may change between backup and installation. Durable
    # projects, uploads, exports and operation journals may not.
    def rows(path):
        with readonly(path) as db:
            result = {}
            tables = db.execute("select name from sqlite_master where type='table'").fetchall()
            for (name,) in tables:
                if name in {"sessions", "alembic_version"} or name.startswith("sqlite_"):
                    continue
                quoted = '"' + name.replace('"', '""') + '"'
                count = len(db.execute(f"PRAGMA table_info({quoted})").fetchall())
                order = ",".join(str(index + 1) for index in range(count))
                where = " WHERE kind != 'heartbeat'" if name == "records" else ""
                values = db.execute(f"SELECT * FROM {quoted}{where} ORDER BY {order}").fetchall()
                # Absent baseline tables equal new empty tables, never a new receipt.
                if values:
                    result[name] = values
            return result

    if rows(current) != rows(baseline):
        raise ValueError("Durable data changed since backup; create a fresh backup")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("database")
    p.add_argument("--compatible")
    p.add_argument("--unchanged")
    p.add_argument("--allow-profile-unknown", action="store_true")
    p.add_argument("--allow-recovery-addition", action="store_true")
    p.add_argument("--read-only-rollback", action="store_true")
    args = p.parse_args()
    validate_isolations(args.database)
    if not args.read_only_rollback:
        assert_quiet(args.database, allow_profile_unknown=args.allow_profile_unknown)
    if args.compatible:
        ensure_compatible(
            args.database, args.compatible, allow_recovery_addition=args.allow_recovery_addition
        )
    if args.unchanged:
        ensure_data_unchanged(args.database, args.unchanged)
