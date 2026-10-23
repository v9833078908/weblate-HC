# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

from sqlalchemy import select

from app.db import DomainError
from app.models import QualityOperation, QualityUnit
from app.services.quality_preflight import run_quality_preflight
from app.services.quality_report import quality_counts, terminal_quality_status
from app.services.quality_state import (
    get_quality_cycle,
    record_quality_units,
    reserve_quality_operation,
    transition_quality_operation,
    update_quality_cycle,
    update_quality_unit_outcomes,
)
from app.services.retry_policy import failure_class, retry_delay

TERMINAL_RUN_STATUSES = {"completed", "partial", "cancelled", "failed"}
MAX_TRANSIENT_RETRIES = 5


def _operation(store, cycle_id: str, key: str) -> dict | None:
    with store.session() as session:
        value = session.scalar(
            select(QualityOperation).where(
                QualityOperation.cycle_id == cycle_id,
                QualityOperation.operation_key == key,
            )
        )
        if value is None:
            return None
        return {
            "id": value.id,
            "state": value.state,
            "remote_id": value.remote_id,
            "attempt": value.attempt,
            "data": value.data,
        }


def _waiting(store, cycle_id: str, seconds: int = 15) -> dict:
    return update_quality_cycle(
        store,
        cycle_id,
        status="waiting",
        next_wakeup=time.time() + seconds,
    )


def handle_quality_error(store, owner: str, cycle_id: str, error: DomainError) -> dict:
    """Persist a safe project-level outcome instead of failing the parent job."""
    cycle = get_quality_cycle(store, owner, cycle_id)
    counts = dict(cycle["counts"])
    classification = failure_class(error.code)
    remote_status = getattr(error, "remote_status", None)
    if remote_status in {401, 403}:
        classification = "configuration"
    if classification in {"configuration", "permission"}:
        counts["configuration_waits"] = counts.get("configuration_waits", 0) + 1
        return update_quality_cycle(
            store,
            cycle_id,
            status="waiting_for_configuration",
            next_wakeup=time.time() + 300,
            counts=counts,
        )
    if classification == "transient":
        attempts = counts.get("transient_retries", 0) + 1
        counts["transient_retries"] = attempts
        if attempts <= MAX_TRANSIENT_RETRIES:
            return update_quality_cycle(
                store,
                cycle_id,
                status="waiting",
                next_wakeup=time.time() + retry_delay(attempts),
                counts=counts,
            )
    counts["quarantined_transport"] = counts.get("quarantined_transport", 0) + 1
    if cycle["policy"].get("workflow_version") == 2:
        if classification not in {"unknown-write", "schema"} and cycle["phase"] != "final-scan":
            return update_quality_cycle(
                store,
                cycle_id,
                phase="final-scan",
                status="running",
                next_wakeup=0,
                counts=counts,
            )
        return update_quality_cycle(
            store,
            cycle_id,
            phase="completed",
            status="failed_verification",
            counts=counts,
        )
    return update_quality_cycle(
        store,
        cycle_id,
        phase="completed",
        status="completed_with_exceptions",
        counts=counts,
    )


def _start_judge(store, cycle: dict, client, scope: dict) -> dict:
    key = f"judge:{cycle['round']}:{cycle['policy_revision']}"
    operation = _operation(store, cycle["id"], key)
    if operation is None:
        operation = reserve_quality_operation(store, cycle["id"], key, "judge-start")
    if operation["state"] in {"accepted", "confirmed"} and operation["remote_id"]:
        return update_quality_cycle(store, cycle["id"], phase="dual-judge-poll", status="running")
    estimate = _operation(
        store,
        cycle["id"],
        f"estimate:{cycle['round']}:{cycle['policy_revision']}",
    )
    if estimate is None or estimate["state"] != "confirmed":
        raise DomainError("quality_state", "Judge estimate is not confirmed", 409)
    try:
        remote_run = client.start_judge(
            cycle["remote_project"], estimate["data"]["estimate_id"], scope
        )
    except DomainError as error:
        classification = failure_class(error.code)
        if classification == "unknown-write":
            transition_quality_operation(
                store,
                operation["id"],
                "unknown",
                failure_class=classification,
                next_retry=time.time() + retry_delay(operation["attempt"] + 1),
            )
            return _waiting(store, cycle["id"])
        raise
    transition_quality_operation(
        store,
        operation["id"],
        "accepted",
        remote_id=str(remote_run["id"]),
        data={"status": remote_run["status"]},
    )
    return update_quality_cycle(store, cycle["id"], phase="dual-judge-poll", status="running")


def _collect_decisions(store, cycle: dict, client, language: str) -> dict:
    key = f"judge:{cycle['round']}:{cycle['policy_revision']}"
    operation = _operation(store, cycle["id"], key)
    decisions = client.list_decisions(cycle["remote_project"], operation["remote_id"])
    issue_snapshot = client.list_quality_issues(cycle["remote_project"], language)
    issues = issue_snapshot["issues"]
    issue_units = {unit_id for issue in issues for unit_id in issue.get("unit_ids", [])}
    decisions = [
        {
            **decision,
            "applicability": "clean",
        }
        if decision.get("applicability") == "no_candidate"
        and decision.get("severity") in {"", "none", "minor"}
        and decision["unit_id"] not in issue_units
        else decision
        for decision in decisions
    ]
    record_quality_units(store, cycle["id"], decisions, cycle["round"])
    counts = quality_counts(decisions)
    for outcome in (
        "forbidden",
        "needs_individual_confirmation",
        "not_verified",
        "stale",
        "unparsed",
        "exhausted",
        "quarantined_transport",
    ):
        if cycle["counts"].get(outcome):
            counts[outcome] = cycle["counts"][outcome]
    counts["consistency_issues"] = len(issues)
    apply_items = [
        {
            "unit": decision["unit_id"],
            "revision": decision["revision"],
            "candidate_id": decision["candidate_id"],
        }
        for decision in decisions
        if decision.get("applicability") == "ready"
        and decision.get("candidate_verified") is True
        and decision.get("candidate_id") is not None
    ]
    collection = reserve_quality_operation(
        store,
        cycle["id"],
        f"collection:{cycle['round']}:{cycle['policy_revision']}",
        "quality-collection",
    )
    transition_quality_operation(
        store,
        collection["id"],
        "confirmed",
        data={
            "apply_items": apply_items,
            "issue_snapshot": issue_snapshot["snapshot_hash"],
        },
    )
    return update_quality_cycle(
        store,
        cycle["id"],
        phase="safe-apply" if apply_items else "completed",
        status=("running" if apply_items else terminal_quality_status(counts)),
        counts=counts,
    )


def _apply(store, cycle: dict, client) -> dict:
    collection = _operation(
        store,
        cycle["id"],
        f"collection:{cycle['round']}:{cycle['policy_revision']}",
    )
    items = collection["data"]["apply_items"]
    operation = reserve_quality_operation(
        store,
        cycle["id"],
        f"apply:{cycle['round']}:{cycle['policy_revision']}",
        "bulk-apply",
    )
    if operation["state"] != "confirmed":
        results = client.bulk_apply_candidates(cycle["remote_project"], items)
        transition_quality_operation(store, operation["id"], "confirmed", data={"results": results})
    else:
        results = operation["data"]["results"]
    update_quality_unit_outcomes(store, cycle["id"], results)
    applied = {result["unit"] for result in results if result["outcome"] == "applied"}
    recheck_items = [item for item in items if item["unit"] in applied]
    counts = dict(cycle["counts"])
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    if not recheck_items:
        return update_quality_cycle(
            store,
            cycle["id"],
            phase="completed",
            status=terminal_quality_status(counts),
            counts=counts,
        )
    operation = reserve_quality_operation(
        store,
        cycle["id"],
        f"recheck:{cycle['round']}:{cycle['policy_revision']}",
        "quality-recheck",
    )
    if operation["state"] != "confirmed":
        result_revisions = {
            result["unit"]: result.get("revision") for result in results if result.get("revision")
        }
        result = client.recheck_quality(
            cycle["remote_project"],
            [
                {
                    "unit_id": item["unit"],
                    "revision": result_revisions.get(item["unit"], item["revision"]),
                }
                for item in recheck_items
            ],
        )
        transition_quality_operation(
            store,
            operation["id"],
            "confirmed",
            data={**result, "unit_ids": sorted(applied)},
        )
    max_rounds = int(cycle["policy"].get("max_repair_rounds", 2))
    if cycle["round"] + 1 >= max_rounds:
        counts["exhausted"] = counts.get("exhausted", 0) + len(recheck_items)
        return update_quality_cycle(
            store,
            cycle["id"],
            phase="completed",
            status="completed_with_exceptions",
            counts=counts,
        )
    return update_quality_cycle(
        store,
        cycle["id"],
        phase="preflight",
        round=cycle["round"] + 1,
        status="running",
        counts=counts,
    )


def advance_quality_cycle(
    store,
    owner: str,
    cycle_id: str,
    client,
    *,
    scope: dict,
    language: str,
) -> dict:
    """Advance at most one durable remote phase; per-unit failures stay local."""
    cycle = get_quality_cycle(store, owner, cycle_id)
    if cycle["phase"] == "completed":
        return cycle
    if cycle["policy"].get("workflow_version") == 2:
        from app.services.full_quality_cycle import advance_full_quality_cycle

        return advance_full_quality_cycle(
            store,
            owner,
            cycle_id,
            client,
            scope=scope,
            language=language,
        )
    if cycle["next_wakeup"] > time.time():
        return cycle
    if cycle["round"]:
        previous_recheck = _operation(
            store,
            cycle["id"],
            f"recheck:{cycle['round'] - 1}:{cycle['policy_revision']}",
        )
        if previous_recheck and previous_recheck["data"].get("unit_ids"):
            scope = {"unit_ids": previous_recheck["data"]["unit_ids"]}
    if cycle["phase"] == "preflight":
        estimate = run_quality_preflight(client, cycle["remote_project"], scope)
        operation = reserve_quality_operation(
            store,
            cycle["id"],
            f"estimate:{cycle['round']}:{cycle['policy_revision']}",
            "judge-estimate",
        )
        transition_quality_operation(
            store,
            operation["id"],
            "confirmed",
            data={"estimate_id": str(estimate["estimate_id"])},
        )
        return update_quality_cycle(store, cycle["id"], phase="dual-judge-start", status="running")
    if cycle["phase"] == "dual-judge-start":
        return _start_judge(store, cycle, client, scope)
    if cycle["phase"] == "dual-judge-poll":
        operation = _operation(
            store,
            cycle["id"],
            f"judge:{cycle['round']}:{cycle['policy_revision']}",
        )
        run = client.get_producer_run(operation["remote_id"])
        if run["status"] not in TERMINAL_RUN_STATUSES:
            return _waiting(store, cycle["id"])
        if run["status"] not in {"completed", "partial"}:
            counts = dict(cycle["counts"])
            counts["quarantined_transport"] = counts.get("quarantined_transport", 0) + 1
            return update_quality_cycle(
                store,
                cycle["id"],
                phase="completed",
                status="completed_with_exceptions",
                counts=counts,
            )
        coverage = run.get("coverage") or {}
        if coverage.get("scope_complete") is False:
            return _waiting(store, cycle["id"])
        transition_quality_operation(
            store,
            operation["id"],
            "confirmed",
            data={"status": run["status"], "coverage": coverage},
        )
        return update_quality_cycle(store, cycle["id"], phase="consistency-scan", status="running")
    if cycle["phase"] == "consistency-scan":
        return _collect_decisions(store, cycle, client, language)
    if cycle["phase"] == "safe-apply":
        return _apply(store, cycle, client)
    raise DomainError("quality_state", "Unsupported quality cycle phase", 409)


def quality_unit_census(store, cycle_id: str) -> list[dict]:
    with store.session() as session:
        return [
            {
                "unit_id": unit.remote_unit_id,
                "input_revision": unit.input_revision,
                "severity": unit.data.get("severity", ""),
                "outcome": unit.outcome,
                "round": unit.round,
                "candidate_id": unit.candidate_id,
                "application_id": unit.application_id,
                "issue_kinds": list(unit.issue_kinds),
                "seat_results": dict(unit.data.get("seat_results", {})),
                "live_seat_results": dict(unit.data.get("live_seat_results", {})),
                "final_reasons": list(unit.data.get("final_reasons", [])),
                "current_judge_verified": unit.data.get("current_judge_verified"),
            }
            for unit in session.scalars(
                select(QualityUnit)
                .where(QualityUnit.cycle_id == cycle_id)
                .order_by(QualityUnit.remote_unit_id)
            )
        ]
