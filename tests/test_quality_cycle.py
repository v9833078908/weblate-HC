# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import DomainError, Store
from app.services.quality_cycle import (
    MAX_TRANSIENT_RETRIES,
    advance_quality_cycle,
    handle_quality_error,
    quality_unit_census,
)
from app.services.quality_state import create_quality_cycle


class QualityRemote:
    def __init__(self):
        self.judge_starts = []
        self.applied_batches = []
        self.rechecks = []

    def producer_me(self):
        return {
            "username": "producer",
            "capabilities": {"judge": True, "judge_seats": 2},
        }

    def judge_estimate(self, project, scope):
        return {
            "estimate_id": f"estimate-{len(self.judge_starts)}",
            "selected": len(scope["unit_ids"]),
            "preparation": {"missing": 0, "engine": "openrouter", "blockers": []},
        }

    def start_judge(self, project, estimate_id, scope):
        run_id = f"run-{len(self.judge_starts) + 1}"
        self.judge_starts.append((run_id, tuple(scope["unit_ids"])))
        return {"id": run_id, "status": "queued"}

    def get_producer_run(self, run_id):
        return {
            "id": run_id,
            "status": "completed",
            "coverage": {"scope_complete": True},
        }

    def list_decisions(self, project, run_id):
        if run_id == "run-1":
            return [
                {
                    "unit_id": 1,
                    "revision": "revision-1",
                    "severity": "major",
                    "seat_results": {"1": "parsed", "2": "parsed"},
                    "candidate_id": 101,
                    "candidate_verified": True,
                    "approval_loss": False,
                    "terminology_conflict": False,
                    "issue_categories": ["meaning"],
                    "applicability": "ready",
                },
                {
                    "unit_id": 2,
                    "revision": "revision-2",
                    "severity": "major",
                    "seat_results": {"1": "parsed", "2": "unparsed"},
                    "candidate_id": None,
                    "candidate_verified": False,
                    "approval_loss": False,
                    "terminology_conflict": False,
                    "issue_categories": [],
                    "applicability": "not_verified",
                },
            ]
        return [
            {
                "unit_id": 1,
                "revision": "revision-1b",
                "severity": "none",
                "seat_results": {"1": "parsed", "2": "parsed"},
                "candidate_id": None,
                "candidate_verified": False,
                "approval_loss": False,
                "terminology_conflict": False,
                "issue_categories": [],
                "applicability": "no_candidate",
            }
        ]

    def list_quality_issues(self, project, language):
        return {"snapshot_hash": "a" * 64, "issues": []}

    def bulk_apply_candidates(self, project, items):
        self.applied_batches.append(items)
        return [
            {
                "unit": item["unit"],
                "outcome": "applied",
                "revision": "revision-1a",
                "application_id": 501,
            }
            for item in items
        ]

    def recheck_quality(self, project, items):
        self.rechecks.append(items)
        return {"snapshot_hash": "b" * 64, "items": []}


def test_cycle_repairs_verified_candidate_and_does_not_stop_on_unparsed(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote-project",
        {"revision": "policy-v1", "max_repair_rounds": 2},
    )
    remote = QualityRemote()

    for _ in range(20):
        cycle = advance_quality_cycle(
            store,
            "owner",
            cycle["id"],
            remote,
            scope={"unit_ids": [1, 2]},
            language="de",
        )
        if cycle["phase"] == "completed":
            break

    assert cycle["status"] == "completed_with_exceptions"
    assert [run for run, _scope in remote.judge_starts] == ["run-1", "run-2"]
    assert remote.applied_batches == [[{"unit": 1, "revision": "revision-1", "candidate_id": 101}]]
    assert remote.rechecks == [[{"unit_id": 1, "revision": "revision-1a"}]]
    census = quality_unit_census(store, cycle["id"])
    assert [item["unit_id"] for item in census] == [1, 2]
    assert next(item for item in census if item["unit_id"] == 2)["outcome"] == "not_verified"


def test_clean_cycle_finishes_without_apply(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote-project",
        {"revision": "policy-v1", "max_repair_rounds": 2},
    )
    remote = QualityRemote()
    remote.list_decisions = lambda project, run_id: [
        {
            "unit_id": 1,
            "revision": "revision",
            "severity": "none",
            "seat_results": {"1": "parsed", "2": "parsed"},
            "candidate_id": None,
            "candidate_verified": False,
            "approval_loss": False,
            "terminology_conflict": False,
            "issue_categories": [],
            "applicability": "no_candidate",
        }
    ]
    for _ in range(10):
        cycle = advance_quality_cycle(
            store,
            "owner",
            cycle["id"],
            remote,
            scope={"unit_ids": [1]},
            language="de",
        )
        if cycle["phase"] == "completed":
            break
    assert cycle["status"] == "completed"
    assert remote.applied_batches == []


def test_transient_quality_errors_are_retried_then_quarantined(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote-project",
        {"revision": "policy-v1", "max_repair_rounds": 2},
    )
    error = DomainError("weblate_unavailable", "Temporary failure", 503)
    for attempt in range(1, MAX_TRANSIENT_RETRIES + 2):
        cycle = handle_quality_error(store, "owner", cycle["id"], error)
        assert cycle["counts"]["transient_retries"] == attempt
    assert cycle["phase"] == "completed"
    assert cycle["status"] == "completed_with_exceptions"
    assert cycle["counts"]["quarantined_transport"] == 1


def test_configuration_error_waits_without_failing_parent_job(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote-project",
        {"revision": "policy-v1", "max_repair_rounds": 2},
    )
    cycle = handle_quality_error(
        store,
        "owner",
        cycle["id"],
        DomainError("weblate_configuration", "Token is unavailable", 503),
    )
    assert cycle["phase"] == "preflight"
    assert cycle["status"] == "waiting_for_configuration"
    assert cycle["counts"]["configuration_waits"] == 1


def test_unknown_mutation_outcome_is_not_retried(tmp_path):
    store = Store(tmp_path)
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote-project",
        {"revision": "policy-v1", "max_repair_rounds": 2},
    )
    cycle = handle_quality_error(
        store,
        "owner",
        cycle["id"],
        DomainError("weblate_unknown", "Mutation outcome is unknown", 503),
    )
    assert cycle["phase"] == "completed"
    assert cycle["status"] == "completed_with_exceptions"
    assert cycle["counts"]["quarantined_transport"] == 1
    assert "transient_retries" not in cycle["counts"]
