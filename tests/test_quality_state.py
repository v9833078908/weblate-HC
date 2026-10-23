# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.db import DomainError, Store
from app.services.quality_state import (
    claim_quality_cycle,
    create_quality_cycle,
    get_quality_cycle,
    reserve_quality_operation,
    transition_quality_operation,
)


def test_quality_cycle_is_idempotent_for_job_and_policy(tmp_path):
    store = Store(tmp_path)
    policy = {
        "revision": "quality-v1",
        "max_repair_rounds": 2,
        "consistency": ["repeat-drift", "check_glossary"],
    }

    first = create_quality_cycle(store, "producer", "job-1", "remote-project", policy)
    second = create_quality_cycle(store, "producer", "job-1", "remote-project", policy)

    assert second["id"] == first["id"]
    assert second["phase"] == "preflight"
    assert second["policy"] == policy


def test_quality_operation_transitions_are_monotonic_and_idempotent(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "producer",
        "job-1",
        "remote-project",
        {"revision": "quality-v1", "max_repair_rounds": 2},
    )

    first = reserve_quality_operation(store, cycle["id"], "judge:round:0", "judge-start")
    duplicate = reserve_quality_operation(store, cycle["id"], "judge:round:0", "judge-start")
    assert duplicate["id"] == first["id"]
    assert duplicate["state"] == "intent"

    accepted = transition_quality_operation(
        store,
        first["id"],
        "accepted",
        remote_id="judge-run-1",
    )
    confirmed = transition_quality_operation(store, first["id"], "confirmed")
    assert accepted["remote_id"] == "judge-run-1"
    assert confirmed["state"] == "confirmed"

    with pytest.raises(DomainError) as error:
        transition_quality_operation(store, first["id"], "intent")
    assert error.value.code == "quality_operation_state"


def test_expired_quality_cycle_lease_can_be_reclaimed(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "producer",
        "job-1",
        "remote-project",
        {"revision": "quality-v1", "max_repair_rounds": 2},
    )

    claimed = claim_quality_cycle(store, "worker-a", now=100.0, lease_seconds=30)
    assert claimed["id"] == cycle["id"]
    assert claim_quality_cycle(store, "worker-b", now=110.0, lease_seconds=30) is None

    reclaimed = claim_quality_cycle(store, "worker-b", now=131.0, lease_seconds=30)
    assert reclaimed["id"] == cycle["id"]
    assert get_quality_cycle(store, "producer", cycle["id"])["lease_owner"] == "worker-b"
