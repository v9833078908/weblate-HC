# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _backup_before_upgrade(connection, config):
    """Called under the startup migration lock, before any schema changes."""
    path = connection.engine.url.database
    if connection.dialect.name != "sqlite" or not path or path == ":memory:":
        return
    exists = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
    ).fetchone()
    if not exists:
        return
    current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").fetchone()
    if not current or current[0] in ScriptDirectory.from_config(config).get_heads():
        return
    source = Path(path).resolve()
    destination = source.parent / "backups" / "migrations"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination / f"wizard-before-upgrade-{stamp}-{uuid.uuid4().hex}.sqlite"
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(target) as backup,
    ):
        original.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Pre-migration database backup failed verification")
    target.chmod(0o600)
    manifest = {
        "schema": 1,
        "previous_revision": current[0],
        "file": target.name,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    target.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    target.with_suffix(".json").chmod(0o600)


def upgrade(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.connect() as connection:
        # Serialise API/worker startup against the same private SQLite database.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _backup_before_upgrade(connection, config)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
