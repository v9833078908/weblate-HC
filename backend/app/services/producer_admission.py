# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import time

from app.models import Record


def admission_key(endpoint, credential):
    # Aggregate all model/seat lanes until their independent quotas are known.
    return (
        "producer-admission:"
        + hashlib.sha256((endpoint + "\0" + credential + "\0producer:*:*").encode()).hexdigest()
    )


def reserve_admission(store, key, interval):
    now = time.time()
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Record, key)
        next_allowed = row.data["next_allowed"] if row else 0
        if next_allowed > now:
            return next_allowed - now
        value = {"next_allowed": now + interval}
        if row:
            row.data = value
        else:
            session.add(
                Record(id=key, kind="producer_admission", owner="system", revision="1", data=value)
            )
    return 0


def defer_admission(store, key, delay):
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Record, key)
        deadline = max(time.time() + delay, row.data["next_allowed"] if row else 0)
        if row:
            row.data = {"next_allowed": deadline}
        else:
            session.add(
                Record(
                    id=key,
                    kind="producer_admission",
                    owner="system",
                    revision="1",
                    data={"next_allowed": deadline},
                )
            )
