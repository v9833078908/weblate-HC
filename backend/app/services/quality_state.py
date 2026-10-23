# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import DomainError
from app.models import QualityCycle, QualityEvent, QualityGroup, QualityOperation, QualityUnit
from app.services.run_isolation import ensure_project_not_isolated

_OPERATION_ORDER = {
    "intent": 0,
    "accepted": 1,
    "unknown": 1,
    "confirmed": 2,
    "failed": 2,
}


def _public_cycle(cycle: QualityCycle) -> dict:
    return {
        "id": cycle.id,
        "owner": cycle.owner,
        "job_id": cycle.job_id,
        "remote_project": cycle.remote_project,
        "policy": copy.deepcopy(cycle.policy),
        "policy_revision": cycle.policy_revision,
        "phase": cycle.phase,
        "round": cycle.round,
        "status": cycle.status,
        "lease_owner": cycle.lease_owner,
        "lease_until": cycle.lease_until,
        "heartbeat_at": cycle.heartbeat_at,
        "next_wakeup": cycle.next_wakeup,
        "counts": copy.deepcopy(cycle.counts),
    }


def _public_operation(operation: QualityOperation) -> dict:
    return {
        "id": operation.id,
        "cycle_id": operation.cycle_id,
        "operation_key": operation.operation_key,
        "kind": operation.kind,
        "state": operation.state,
        "remote_id": operation.remote_id,
        "attempt": operation.attempt,
        "next_retry": operation.next_retry,
        "failure_class": operation.failure_class,
        "data": copy.deepcopy(operation.data),
    }


def create_quality_cycle(store, owner: str, job_id: str, remote_project: str, policy: dict) -> dict:
    policy_revision = policy.get("revision")
    if not isinstance(policy_revision, str) or not policy_revision:
        raise DomainError("quality_policy", "Quality policy revision is required")
    try:
        with store.session() as session:
            ensure_project_not_isolated(session, remote_project)
            existing = session.scalar(
                select(QualityCycle).where(
                    QualityCycle.job_id == job_id,
                    QualityCycle.policy_revision == policy_revision,
                )
            )
            if existing:
                if existing.owner != owner or existing.remote_project != remote_project:
                    raise DomainError("not_found", "Not found", 404)
                if existing.policy != policy:
                    raise DomainError("quality_policy", "Immutable quality policy changed", 409)
                return _public_cycle(existing)
            cycle = QualityCycle(
                id=str(uuid.uuid4()),
                owner=owner,
                job_id=job_id,
                remote_project=remote_project,
                policy_revision=policy_revision,
                policy=copy.deepcopy(policy),
                phase="preflight",
                round=0,
                status="queued",
                lease_owner="",
                lease_until=0,
                heartbeat_at=0,
                next_wakeup=0,
                counts={},
            )
            session.add(cycle)
            session.flush()
            return _public_cycle(cycle)
    except IntegrityError:
        with store.session() as session:
            existing = session.scalar(
                select(QualityCycle).where(
                    QualityCycle.job_id == job_id,
                    QualityCycle.policy_revision == policy_revision,
                )
            )
            if existing and existing.owner == owner and existing.remote_project == remote_project:
                return _public_cycle(existing)
        raise


def get_quality_cycle(store, owner: str, cycle_id: str) -> dict:
    with store.session() as session:
        cycle = session.get(QualityCycle, cycle_id)
        if cycle is None or cycle.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        return _public_cycle(cycle)


def record_quality_groups(store, owner: str, cycle_id: str, groups: list[dict], round_number: int):
    from app.services.quality_contract import require_text_free

    get_quality_cycle(store, owner, cycle_id)
    require_text_free(groups)
    with store.session() as session:
        for group in groups:
            row = session.scalar(
                select(QualityGroup).where(
                    QualityGroup.cycle_id == cycle_id,
                    QualityGroup.remote_group_id == group["group_id"],
                )
            )
            if row is None:
                row = QualityGroup(
                    id=str(uuid.uuid4()),
                    cycle_id=cycle_id,
                    remote_group_id=group["group_id"],
                )
                session.add(row)
            row.revision = group["revision"]
            row.round = round_number
            row.outcome = group["outcome"]
            row.data = copy.deepcopy(group)


def list_quality_groups(store, owner: str, cycle_id: str) -> list[dict]:
    get_quality_cycle(store, owner, cycle_id)
    with store.session() as session:
        return [
            copy.deepcopy(row.data)
            for row in session.scalars(
                select(QualityGroup)
                .where(QualityGroup.cycle_id == cycle_id)
                .order_by(QualityGroup.remote_group_id)
            )
        ]


def claim_quality_cycle(
    store,
    lease_owner: str,
    *,
    now: float | None = None,
    lease_seconds: float = 120,
) -> dict | None:
    stamp = time.time() if now is None else now
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        candidates = session.scalars(
            select(QualityCycle)
            .where(
                QualityCycle.status.in_(["queued", "running", "waiting"]),
                QualityCycle.lease_until <= stamp,
                QualityCycle.next_wakeup <= stamp,
            )
            .order_by(QualityCycle.id)
        )
        candidate = None
        for cycle in candidates:
            try:
                ensure_project_not_isolated(session, cycle.remote_project)
            except DomainError:
                continue
            candidate = cycle.id
            break
        if candidate is None:
            return None
        changed = session.execute(
            update(QualityCycle)
            .where(QualityCycle.id == candidate, QualityCycle.lease_until <= stamp)
            .values(
                lease_owner=lease_owner,
                lease_until=stamp + lease_seconds,
                heartbeat_at=stamp,
                status="running",
            )
        )
        if changed.rowcount != 1:
            return None
        return _public_cycle(session.get(QualityCycle, candidate))


def reserve_quality_operation(store, cycle_id: str, operation_key: str, kind: str) -> dict:
    try:
        with store.session() as session:
            cycle = session.get(QualityCycle, cycle_id)
            if cycle is not None:
                ensure_project_not_isolated(session, cycle.remote_project)
            existing = session.scalar(
                select(QualityOperation).where(
                    QualityOperation.cycle_id == cycle_id,
                    QualityOperation.operation_key == operation_key,
                )
            )
            if existing:
                if existing.kind != kind:
                    raise DomainError(
                        "quality_operation_key", "Operation key belongs to another operation"
                    )
                return _public_operation(existing)
            if session.get(QualityCycle, cycle_id) is None:
                raise DomainError("not_found", "Not found", 404)
            operation = QualityOperation(
                id=str(uuid.uuid4()),
                cycle_id=cycle_id,
                operation_key=operation_key,
                kind=kind,
                state="intent",
                remote_id="",
                attempt=0,
                next_retry=0,
                failure_class="",
                data={},
            )
            session.add(operation)
            session.flush()
            return _public_operation(operation)
    except IntegrityError:
        with store.session() as session:
            existing = session.scalar(
                select(QualityOperation).where(
                    QualityOperation.cycle_id == cycle_id,
                    QualityOperation.operation_key == operation_key,
                )
            )
            if existing and existing.kind == kind:
                return _public_operation(existing)
        raise


def get_quality_operation(store, cycle_id: str, key: str) -> dict | None:
    with store.session() as session:
        row = session.scalar(
            select(QualityOperation).where(
                QualityOperation.cycle_id == cycle_id,
                QualityOperation.operation_key == key,
            )
        )
        return _public_operation(row) if row else None


def checkpoint_full_quality(store, cycle_id: str, context: dict, **changes) -> dict:
    """Commit next pass/scope and its phase in one transaction."""
    from app.services.quality_contract import require_text_free

    require_text_free(context)
    if set(changes) - {"phase", "round", "status", "next_wakeup", "counts"}:
        raise DomainError("quality_cycle_state", "Unsupported quality cycle field")
    with store.session() as session:
        cycle = session.get(QualityCycle, cycle_id)
        if cycle is None:
            raise DomainError("not_found", "Not found", 404)
        row = session.scalar(
            select(QualityOperation).where(
                QualityOperation.cycle_id == cycle_id,
                QualityOperation.operation_key == "full-context",
            )
        )
        if row is None:
            row = QualityOperation(
                id=str(uuid.uuid4()),
                cycle_id=cycle_id,
                operation_key="full-context",
                kind="full-quality-state",
                state="confirmed",
                data={},
            )
            session.add(row)
        row.data = copy.deepcopy(context)
        for key, value in changes.items():
            setattr(cycle, key, copy.deepcopy(value))
        cycle.heartbeat_at = time.time()
        session.add(
            QualityEvent(
                cycle_id=cycle_id,
                kind="full-checkpoint",
                created=time.time(),
                data=copy.deepcopy(changes),
            )
        )
        session.flush()
        return _public_cycle(cycle)


def transition_quality_operation(
    store,
    operation_id: str,
    state: str,
    *,
    remote_id: str | None = None,
    failure_class: str | None = None,
    next_retry: float | None = None,
    data: dict | None = None,
) -> dict:
    if state not in _OPERATION_ORDER:
        raise DomainError("quality_operation_state", "Unsupported quality operation state")
    with store.session() as session:
        operation = session.get(QualityOperation, operation_id)
        if operation is None:
            raise DomainError("not_found", "Not found", 404)
        if _OPERATION_ORDER[state] < _OPERATION_ORDER[operation.state]:
            raise DomainError("quality_operation_state", "Quality operation cannot move backwards")
        if operation.state in {"confirmed", "failed"} and state != operation.state:
            raise DomainError("quality_operation_state", "Quality operation is already terminal")
        operation.state = state
        if remote_id is not None:
            operation.remote_id = remote_id
        if failure_class is not None:
            operation.failure_class = failure_class
        if next_retry is not None:
            operation.next_retry = next_retry
        if data is not None:
            operation.data = copy.deepcopy(data)
        if state in {"accepted", "unknown"}:
            operation.attempt += 1
        session.flush()
        return _public_operation(operation)


def update_quality_cycle(store, cycle_id: str, **changes) -> dict:
    """Persist one orchestrator checkpoint and append a text-free event."""
    allowed = {"phase", "round", "status", "next_wakeup", "counts"}
    if set(changes) - allowed:
        raise DomainError("quality_cycle_state", "Unsupported quality cycle field")
    with store.session() as session:
        cycle = session.get(QualityCycle, cycle_id)
        if cycle is None:
            raise DomainError("not_found", "Not found", 404)
        before = {key: copy.deepcopy(getattr(cycle, key)) for key in changes}
        for key, value in changes.items():
            setattr(cycle, key, copy.deepcopy(value))
        cycle.heartbeat_at = time.time()
        session.add(
            QualityEvent(
                cycle_id=cycle.id,
                kind="cycle-checkpoint",
                created=time.time(),
                data={"before": before, "after": copy.deepcopy(changes)},
            )
        )
        session.flush()
        return _public_cycle(cycle)


def record_quality_units(store, cycle_id: str, decisions: list[dict], round_number: int):
    """Upsert the complete decision census without storing translation text."""
    with store.session() as session:
        if session.get(QualityCycle, cycle_id) is None:
            raise DomainError("not_found", "Not found", 404)
        for decision in decisions:
            remote_id = decision["unit_id"]
            unit = session.scalar(
                select(QualityUnit).where(
                    QualityUnit.cycle_id == cycle_id,
                    QualityUnit.remote_unit_id == remote_id,
                )
            )
            values = {
                "input_revision": decision["revision"],
                "round": round_number,
                "outcome": decision["applicability"],
                "candidate_id": decision.get("candidate_id"),
                "issue_kinds": list(decision.get("issue_categories", [])),
                "data": {
                    "severity": decision.get("severity", ""),
                    "seat_results": copy.deepcopy(decision.get("seat_results", {})),
                    "candidate_verified": bool(decision.get("candidate_verified")),
                    "candidate_acceptable": bool(decision.get("candidate_acceptable")),
                    "live_seat_results": copy.deepcopy(decision.get("live_seat_results", {})),
                    "approval_loss": bool(decision.get("approval_loss")),
                    "terminology_conflict": bool(decision.get("terminology_conflict")),
                },
            }
            if unit is None:
                unit = QualityUnit(
                    id=str(uuid.uuid4()),
                    cycle_id=cycle_id,
                    remote_unit_id=remote_id,
                    input_hash="",
                    application_id=None,
                    retry_count=0,
                    **values,
                )
                session.add(unit)
            else:
                for key, value in values.items():
                    setattr(unit, key, value)
        session.flush()


def update_quality_unit_outcomes(store, cycle_id: str, results: list[dict]):
    with store.session() as session:
        for result in results:
            unit = session.scalar(
                select(QualityUnit).where(
                    QualityUnit.cycle_id == cycle_id,
                    QualityUnit.remote_unit_id == result["unit"],
                )
            )
            if unit is None:
                continue
            unit.outcome = result["outcome"]
            unit.application_id = result.get("application_id")


def record_final_quality_outcomes(store, cycle_id: str, snapshot: dict):
    from app.services.quality_policy import required_issue

    with store.session() as session:
        for unit in session.scalars(select(QualityUnit).where(QualityUnit.cycle_id == cycle_id)):
            reasons = sorted(
                {
                    issue["kind"]
                    for issue in snapshot["issues"]
                    if required_issue(issue) and unit.remote_unit_id in issue["unit_ids"]
                }
            )
            verified = snapshot["judged_revisions"].get(str(unit.remote_unit_id)) == (
                snapshot["revisions"].get(str(unit.remote_unit_id))
            )
            data = copy.deepcopy(unit.data)
            data.setdefault("decision_outcome", unit.outcome)
            data["final_reasons"] = reasons
            data["current_judge_verified"] = verified
            unit.data = data
            unit.outcome = "residual_issues" if reasons else "clean" if verified else "not_verified"
