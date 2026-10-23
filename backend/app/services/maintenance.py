# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import shutil
import time
from uuid import UUID

from sqlalchemy import delete, select

from app.models import Record, SessionRecord


def cleanup_expired(store):
    stamp = time.time()
    directories = []
    with store.session() as session:
        session.execute(delete(SessionRecord).where(SessionRecord.expires < stamp))
        records = session.scalars(select(Record).where(Record.kind.in_(["draft", "bdhc_preview"])))
        for record in records:
            if record.data.get("expires", stamp + 1) >= stamp:
                continue
            if record.kind == "draft":
                directories.append(record.id)
            session.delete(record)
    root = (store.data_dir / "uploads").resolve()
    for token in directories:
        try:
            UUID(token)
        except ValueError:
            continue
        directory = root / token
        if directory.is_symlink():
            continue
        resolved = directory.resolve()
        if resolved.parent == root and resolved.is_dir():
            shutil.rmtree(resolved)
    # Jobs and exports contain their immutable snapshots and are retained.
