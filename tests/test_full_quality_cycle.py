# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.db import DomainError, Store
from app.integrations.weblate import WeblateRejected
from app.services.quality_contract import FULL_QUALITY_CAPABILITIES
from app.services.quality_cycle import advance_quality_cycle, quality_unit_census
from app.services.quality_state import create_quality_cycle, update_quality_cycle
from sqlalchemy import event


class FullRemote:
    def __init__(self, *, unacceptable=False, timeout_apply=False, count=2):
        self.revisions = {str(i): f"r{i}" for i in range(1, count + 1)}
        self.judged = {}
        self.starts = []
        self.applies = []
        self.rechecks = []
        self.repeat_keys = []
        self.operations = {}
        self.unacceptable = unacceptable
        self.timeout_apply = timeout_apply
        self.repeat_applied = False

    def producer_me(self):
        return {
            "capabilities": {
                "judge": True,
                "judge_seats": 2,
                "quality_api_version": 2,
                **dict.fromkeys(FULL_QUALITY_CAPABILITIES, True),
            }
        }

    def judge_estimate(self, project, scope):
        return {
            "estimate_id": "estimate",
            "selected": len(scope["unit_ids"]),
            "preparation": {
                "missing": 0,
                "engine": "openrouter",
                "blockers": [],
            },
        }

    def start_quality_judge(self, project, estimate, scope, key):
        self.starts.append(scope["unit_ids"])
        result = {"id": str(len(self.starts)), "status": "queued"}
        self.operations[key] = {"state": "confirmed", "result": result}
        return result

    def get_producer_run(self, run):
        return {"id": run, "status": "completed", "coverage": {"scope_complete": True}}

    def list_decisions(self, project, run):
        result = []
        for unit in self.starts[int(run) - 1]:
            candidate = run == "1" and unit == 1
            if not candidate:
                self.judged[str(unit)] = self.revisions[str(unit)]
            result.append(
                {
                    "unit_id": unit,
                    "revision": self.revisions[str(unit)],
                    "severity": "major" if candidate else "none",
                    "live_seat_results": {"1": "parsed", "2": "parsed"},
                    "seat_results": {"1": "parsed", "2": "parsed"},
                    "candidate_id": 101 if candidate else None,
                    "candidate_verified": candidate,
                    "candidate_acceptable": candidate and not self.unacceptable,
                    "approval_loss": False,
                    "terminology_conflict": False,
                    "issue_categories": [],
                    "applicability": "ready" if candidate else "no_candidate",
                }
            )
        return result

    def quality_snapshot(self, project, language, scope):
        ids = scope["unit_ids"]
        issues = (
            []
            if self.repeat_applied
            else [
                {
                    "group_id": "repeat:1",
                    "kind": "repeat-drift",
                    "hard": False,
                    "advisory": True,
                    "unit_ids": [1, 2],
                }
            ]
        )
        return {
            "snapshot_hash": "after" if self.repeat_applied else "before",
            "scope_revision": "scope-after" if self.repeat_applied else "scope-before",
            "complete": True,
            "unit_ids": ids,
            "revisions": dict(self.revisions),
            "judged_revisions": dict(self.judged),
            "issues": issues,
        }

    def apply_quality_candidates(self, project, items, key):
        self.applies.append(items)
        self.revisions["1"] = "r1-fixed"
        self.judged["1"] = "r1-fixed"
        result = [{"unit": 1, "outcome": "applied", "revision": "r1-fixed", "application_id": 501}]
        self.operations[key] = {"state": "confirmed", "result": result}
        if self.timeout_apply:
            self.timeout_apply = False
            raise DomainError("weblate_unknown", "Response lost after write", 503)
        return result

    def observe_quality_operation(self, project, key):
        return self.operations.get(key)

    def recheck_quality(self, project, items):
        self.rechecks.append(items)
        return {
            "snapshot_hash": "checks",
            "items": [{**item, "outcome": "rechecked", "checks": []} for item in items],
            "affected_repeat_group_ids": ["repeat:1"],
            "affected_unit_ids": [1, 2],
        }

    def recheck_full_quality(self, project, items, key):
        result = self.recheck_quality(project, items)
        self.operations[key] = {"state": "confirmed", "result": result}
        return result

    def plan_repeat_quality(self, project, language, scope, snapshot_hash, application_ids, key):
        self.repeat_keys.append((snapshot_hash, application_ids))
        result = {
            "token": "plan-1",
            "status": "ready",
            "groups": [
                {
                    "group_id": "repeat:1",
                    "revision": "group-r1",
                    "unit_ids": [1, 2],
                    "outcome": "needs_review" if self.unacceptable else "ready",
                }
            ],
        }
        self.operations[key] = {"state": "confirmed", "result": result}
        return result

    def apply_repeat_plan(self, token, key):
        self.repeat_applied = True
        self.revisions["2"] = "r2-fixed"
        self.judged.pop("2", None)
        result = {
            "token": "repeat-run",
            "status": "completed",
            "changed_unit_ids": [2],
            "results": [{"unit": 2, "revision": "r2-fixed", "outcome": "applied"}],
        }
        self.operations[key] = {"state": "confirmed", "result": result}
        return result


def drive(tmp_path, remote, *, restart_each_phase=False):
    store = Store(tmp_path)
    scope = {"unit_ids": [int(item) for item in remote.revisions]}
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote",
        {
            "revision": "v2",
            "workflow_version": 2,
            "max_repair_rounds": 2,
        },
    )
    for _ in range(80):
        if restart_each_phase:
            store.engine.dispose()
            store = Store(tmp_path)
        cycle = advance_quality_cycle(
            store, "owner", cycle["id"], remote, scope=scope, language="de"
        )
        if cycle["phase"] == "completed":
            return store, cycle
        if cycle["next_wakeup"]:
            update_quality_cycle(store, cycle["id"], next_wakeup=0)
    pytest.fail(f"Cycle did not terminate: {cycle}")


def test_full_cycle_propagates_judge_fix_then_judges_only_changed_recipient(tmp_path):
    remote = FullRemote()
    store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed"
    assert remote.starts == [[1, 2], [2]]
    assert remote.repeat_keys == [("before", [501])]
    assert remote.rechecks == [
        [{"unit_id": 1, "revision": "r1-fixed"}],
        [{"unit_id": 2, "revision": "r2-fixed"}],
    ]
    assert cycle["counts"]["consistency_issues"] == 0
    assert len(quality_unit_census(store, cycle["id"])) == 2


def test_two_parsed_judges_do_not_allow_unacceptable_candidate(tmp_path):
    remote = FullRemote(unacceptable=True)
    _store, cycle = drive(tmp_path, remote)
    assert remote.applies == []
    assert cycle["status"] == "completed_with_exceptions"
    assert cycle["counts"]["consistency_issues"] == 1


def test_mixed_apply_continues_without_counting_already_applied_as_a_new_write(tmp_path):
    class ConcurrentApply(FullRemote):
        def list_decisions(self, project, run):
            rows = super().list_decisions(project, run)
            if run == "1":
                rows[2].update(
                    candidate_id=103,
                    candidate_verified=True,
                    candidate_acceptable=True,
                    applicability="ready",
                )
            return rows

        def apply_quality_candidates(self, project, items, key):
            assert [item["unit"] for item in items] == [1, 3]
            result = super().apply_quality_candidates(project, items, key)
            result.append({"unit": 3, "outcome": "already_applied", "revision": "r3"})
            return result

    remote = ConcurrentApply(count=3)
    _store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed"
    assert cycle["counts"]["automatically_corrected"] == 2
    assert remote.repeat_keys == [("before", [501])]
    assert remote.rechecks == [
        [{"unit_id": 1, "revision": "r1-fixed"}],
        [{"unit_id": 2, "revision": "r2-fixed"}],
    ]


def test_disabled_repeat_coverage_cannot_complete_cleanly_even_with_two_judge_passes(tmp_path):
    from app.services.quality_report import full_quality_report

    class DisabledRepeats(FullRemote):
        def quality_snapshot(self, project, language, scope):
            result = super().quality_snapshot(project, language, scope)
            result["issues"] = [
                {
                    "group_id": "check:1:repeat-coverage",
                    "kind": "check",
                    "hard": True,
                    "advisory": False,
                    "unit_ids": [1, 2],
                    "check_id": "repeat-coverage",
                }
            ]
            return result

    store, cycle = drive(tmp_path, DisabledRepeats())
    report = full_quality_report(store, "owner", cycle["id"])
    assert report["status"] == "completed_with_exceptions"
    assert report["counts"]["not_verified"] == 0
    assert report["residual_issues"][0]["check_id"] == "repeat-coverage"
    assert all(unit["final_reasons"] == ["check"] for unit in report["units"])


def test_lost_apply_response_is_observed_without_second_mutating_call(tmp_path):
    remote = FullRemote(timeout_apply=True)
    _store, cycle = drive(tmp_path, remote)
    assert len(remote.applies) == 1
    assert cycle["status"] == "completed"


def test_full_cycle_accounts_for_500_strings(tmp_path):
    remote = FullRemote(count=500)
    store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed"
    assert len(quality_unit_census(store, cycle["id"])) == 500


def test_final_report_includes_residual_groups_and_both_judge_results(tmp_path):
    from app.services.quality_report import full_quality_report

    remote = FullRemote(unacceptable=True)
    store, cycle = drive(tmp_path, remote)
    report = full_quality_report(store, "owner", cycle["id"])
    assert report["residual_issues"][0]["group_id"] == "repeat:1"
    assert report["groups"][0]["outcome"] == "needs_review"
    assert report["units"][0]["seat_results"] == {"1": "parsed", "2": "parsed"}
    assert report["units"][0]["final_reasons"] == ["repeat-drift"]


def test_partial_judge_coverage_is_reported_per_unit_without_aborting_other_repairs(tmp_path):
    remote = FullRemote()
    original = remote.list_decisions

    def decisions(project, run):
        rows = original(project, run)
        remote.judged.pop("2", None)
        return [row for row in rows if row["unit_id"] != 2]

    remote.list_decisions = decisions
    remote.get_producer_run = lambda run: {
        "id": run,
        "status": "partial",
        "coverage": {"scope_complete": False},
    }
    store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed_with_exceptions"
    assert cycle["counts"]["not_verified"] == 1
    assert len(remote.applies) == 1
    assert len(quality_unit_census(store, cycle["id"])) == 2


@pytest.mark.parametrize("status", [403, 429])
def test_definite_temporary_refusal_can_resume_without_poisoning_operation(tmp_path, status):
    remote = FullRemote()
    original = remote.apply_quality_candidates
    refused = False

    def apply(project, items, key):
        nonlocal refused
        if not refused:
            refused = True
            raise WeblateRejected(status)
        return original(project, items, key)

    remote.apply_quality_candidates = apply
    from app.services import quality_cycle

    actual_advance = quality_cycle.advance_quality_cycle

    def recover(*args, **kwargs):
        try:
            return actual_advance(*args, **kwargs)
        except WeblateRejected as error:
            return quality_cycle.handle_quality_error(args[0], args[1], args[2], error)

    # Keep drive exercising persisted recovery, rather than a second POST in one call.
    import test_full_quality_cycle as module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "advance_quality_cycle", recover)
        _store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed"
    assert len(remote.applies) == 1


def test_phase_context_and_round_are_atomic_on_checkpoint_failure(tmp_path):
    from app.services.quality_state import get_quality_cycle, get_quality_operation

    store = Store(tmp_path)
    remote = FullRemote()
    scope = {"unit_ids": [1, 2]}
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote",
        {
            "revision": "v2",
            "workflow_version": 2,
            "max_repair_rounds": 2,
        },
    )
    for _ in range(30):
        cycle = advance_quality_cycle(
            store, "owner", cycle["id"], remote, scope=scope, language="de"
        )
        if cycle["phase"] == "recipient-recheck":
            break
    assert cycle["phase"] == "recipient-recheck"

    def fail_checkpoint(connection, cursor, statement, *args):
        if statement.startswith("UPDATE quality_cycles"):
            raise RuntimeError("simulated crash before phase commit")

    event.listen(store.engine, "before_cursor_execute", fail_checkpoint)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            advance_quality_cycle(store, "owner", cycle["id"], remote, scope=scope, language="de")
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_checkpoint)
    assert get_quality_operation(store, cycle["id"], "full-context")["data"]["pass"] == 0
    assert get_quality_cycle(store, "owner", cycle["id"])["phase"] == "recipient-recheck"


def test_failed_judge_run_reaches_final_scan_and_explains_unverified_rows(tmp_path):
    remote = FullRemote(unacceptable=True)
    remote.get_producer_run = lambda run: {"id": run, "status": "failed", "coverage": {}}
    store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed_with_exceptions"
    from app.services.quality_state import get_quality_operation

    assert get_quality_operation(store, cycle["id"], "final-snapshot") is not None


def test_completed_cycle_records_terminal_repeat_group_outcome(tmp_path):
    from app.services.quality_state import list_quality_groups

    store, cycle = drive(tmp_path, FullRemote())
    assert list_quality_groups(store, "owner", cycle["id"])[0]["outcome"] == "applied"


def test_nonfatal_quality_operation_failure_still_gets_final_snapshot(tmp_path):
    from app.services.quality_cycle import handle_quality_error

    store = Store(tmp_path)
    remote = FullRemote()
    scope = {"unit_ids": [1, 2]}
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote",
        {
            "revision": "v2",
            "workflow_version": 2,
            "max_repair_rounds": 2,
        },
    )
    for _ in range(20):
        cycle = advance_quality_cycle(
            store, "owner", cycle["id"], remote, scope=scope, language="de"
        )
        if cycle["phase"] == "individual-apply":
            break
    cycle = handle_quality_error(
        store,
        "owner",
        cycle["id"],
        DomainError("quality_operation_failed", "Refused operation", 409),
    )
    assert cycle["phase"] == "final-scan"
    cycle = advance_quality_cycle(store, "owner", cycle["id"], remote, scope=scope, language="de")
    assert cycle["status"] == "completed_with_exceptions"


def test_restart_each_phase_does_not_duplicate_judge_or_apply(tmp_path):
    remote = FullRemote(timeout_apply=True)
    _store, cycle = drive(tmp_path, remote, restart_each_phase=True)
    assert cycle["status"] == "completed"
    assert len(remote.applies) == 1
    assert remote.starts == [[1, 2], [2]]


def test_500_rows_with_partial_judge_errors_continue_and_report_exceptions(tmp_path):
    remote = FullRemote(count=500)
    original = remote.list_decisions

    def decisions(project, run):
        rows = original(project, run)
        for row in rows:
            if row["unit_id"] % 4 == 0:
                row.update(
                    severity="unparsed",
                    applicability="not_verified",
                    live_seat_results={"1": "parsed", "2": "unparsed"},
                )
                remote.judged.pop(str(row["unit_id"]), None)
        return rows

    remote.list_decisions = decisions
    store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed_with_exceptions"
    assert cycle["counts"]["processed"] == 500
    assert cycle["counts"]["not_verified"] == 125
    assert cycle["counts"]["automatically_corrected"] == 2
    assert len(quality_unit_census(store, cycle["id"])) == 500


def test_conflicting_corrected_variants_do_not_force_a_repeat_write(tmp_path):
    remote = FullRemote()

    def plan(*args):
        return {
            "token": "conflict",
            "status": "ready",
            "groups": [
                {
                    "group_id": "repeat:1",
                    "revision": "group-r1",
                    "unit_ids": [1, 2],
                    "outcome": "needs_review",
                    "reason": "conflicting_judge_anchors",
                }
            ],
        }

    remote.plan_repeat_quality = plan
    _store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed_with_exceptions"
    assert remote.repeat_applied is False


def test_second_judge_repair_rebuilds_repeats_before_judge_only_final_pass(tmp_path):
    remote = FullRemote()
    original_decisions = remote.list_decisions
    original_apply = remote.apply_quality_candidates
    original_repeat = remote.apply_repeat_plan
    refined = False

    def decisions(project, run):
        rows = original_decisions(project, run)
        if run == "2":
            remote.judged.pop("2", None)
            rows[0].update(
                severity="major",
                applicability="ready",
                candidate_id=102,
                candidate_verified=True,
                candidate_acceptable=True,
            )
        return rows

    def apply(project, items, key):
        nonlocal refined
        if items[0]["unit"] == 1:
            return original_apply(project, items, key)
        remote.applies.append(items)
        refined = True
        remote.repeat_applied = False
        remote.revisions["2"] = "r2-refined"
        remote.judged["2"] = "r2-refined"
        result = [
            {"unit": 2, "outcome": "applied", "revision": "r2-refined", "application_id": 502}
        ]
        remote.operations[key] = {"state": "confirmed", "result": result}
        return result

    def repeat(token, key):
        if not refined:
            return original_repeat(token, key)
        remote.repeat_applied = True
        remote.revisions["1"] = "r1-refined"
        remote.judged.pop("1", None)
        result = {
            "token": "repeat-run-2",
            "status": "completed",
            "changed_unit_ids": [1],
            "results": [{"unit": 1, "outcome": "applied", "revision": "r1-refined"}],
        }
        remote.operations[key] = {"state": "confirmed", "result": result}
        return result

    remote.list_decisions = decisions
    remote.apply_quality_candidates = apply
    remote.apply_repeat_plan = repeat
    _store, cycle = drive(tmp_path, remote)
    assert cycle["status"] == "completed"
    assert remote.starts == [[1, 2], [2], [1]]
    assert len(remote.applies) == 2
    assert remote.repeat_keys[-1][1] == [501, 502]
