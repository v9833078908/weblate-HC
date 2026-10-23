# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def reconcile(path):
    """After workers stop, mark sent unfinished calls for manual reconciliation."""
    changed = 0
    with sqlite3.connect(Path(path).resolve()) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("select id,data from records where kind='profile_generation'").fetchall()
        for identifier, payload in rows:
            value = json.loads(payload)
            if value.get("state") != "running":
                continue
            value.update(
                state="unknown" if value.get("dispatch_started") else "queued", lease_until=0
            )
            if value.get("dispatch_started"):
                value.setdefault("warnings", []).append("llm_unknown")
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
            rev = hashlib.sha256(serialized.encode()).hexdigest()
            db.execute(
                "update records set data=?,revision=? where id=?", (serialized, rev, identifier)
            )
            changed += 1
    return changed


if __name__ == "__main__":
    print(f"Profile jobs reconciled locally: {reconcile(sys.argv[1])}; no provider request sent")
