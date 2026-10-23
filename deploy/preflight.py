# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def assert_quiet(path, *, allow_profile_unknown=False):
    with readonly(path) as db:
        for status, data in db.execute("select status,data from jobs"):
            value = json.loads(data)
            if status in {"queued", "running", "unknown", "cancel_requested"} or any(
                op["state"] in {"intent", "accepted"} for op in value.get("operations", {}).values()
            ):
                raise ValueError("Unsettled external operation prevents maintenance")
        if not allow_profile_unknown:
            for (data,) in db.execute("select data from records where kind='profile_generation'"):
                if json.loads(data).get("state") in {"running", "unknown"}:
                    raise ValueError("Unsettled profile LLM operation prevents maintenance")


def ensure_compatible(current, baseline):
    def schema(path):
        with readonly(path) as db:
            return (
                db.execute(
                    "select name,sql from sqlite_master where type='table' order by name"
                ).fetchall(),
                db.execute("select version_num from alembic_version").fetchall(),
            )

    if schema(current) != schema(baseline):
        raise ValueError("Database schema differs from the rollback baseline")


def ensure_data_unchanged(current, baseline):
    # Liveness/session rows may change between backup and installation. Durable
    # projects, uploads, exports and operation journals may not.
    def rows(path):
        with readonly(path) as db:
            return (
                db.execute(
                    "select * from records where kind != 'heartbeat' order by id"
                ).fetchall(),
                db.execute("select * from jobs order by id").fetchall(),
                db.execute("select * from users order by username").fetchall(),
            )

    if rows(current) != rows(baseline):
        raise ValueError("Durable data changed since backup; create a fresh backup")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("database")
    p.add_argument("--compatible")
    p.add_argument("--unchanged")
    p.add_argument("--allow-profile-unknown", action="store_true")
    args = p.parse_args()
    assert_quiet(args.database, allow_profile_unknown=args.allow_profile_unknown)
    if args.compatible:
        ensure_compatible(args.database, args.compatible)
    if args.unchanged:
        ensure_data_unchanged(args.database, args.unchanged)
