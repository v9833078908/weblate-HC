# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

from sqlalchemy import select

from app.db import DomainError
from app.models import Job, QualityCycle, QualityOperation
from app.services.quality_contract import require_text_free

_TERMINAL = {
    "full-judge": {"completed", "partial", "failed", "cancelled"},
    "full-repeat-apply": {"completed", "partial", "failed"},
    "full-repeat-plan": {"ready", "failed"},
}


def _unsettled(state, key, data):
    if state in {"accepted", "unknown"}:
        return True
    kind = key.split(":", 1)[0]
    return (
        state == "confirmed"
        and kind in _TERMINAL
        and (
            not isinstance(data.get("result"), dict)
            or data["result"].get("status") not in _TERMINAL[kind]
        )
    )


def has_unsettled_quality(session, job_id):
    operations = session.scalars(
        select(QualityOperation)
        .join(QualityCycle, QualityCycle.id == QualityOperation.cycle_id)
        .where(QualityCycle.job_id == job_id, QualityOperation.kind == "full-quality-write")
    )
    return any(_unsettled(op.state, op.operation_key, op.data) for op in operations)


def reconcile_cancelled_quality(store, owner, project, client):
    """Read remote receipts/runs; local cancellation never proves remote completion."""
    with store.session() as session:
        rows = [
            (op.id, job.id, op.state, op.operation_key, copy.deepcopy(op.data))
            for op, job in session.execute(
                select(QualityOperation, Job)
                .join(QualityCycle, QualityCycle.id == QualityOperation.cycle_id)
                .join(Job, Job.id == QualityCycle.job_id)
                .where(
                    QualityCycle.owner == owner,
                    QualityCycle.remote_project == project,
                    Job.owner == owner,
                    Job.status.in_(["cancelled", "failed"]),
                    Job.lease_owner == "",
                    QualityOperation.kind == "full-quality-write",
                )
            )
            if _unsettled(op.state, op.operation_key, op.data)
        ]
    for op_id, job_id, before_state, key, before_data in rows:
        state, data = before_state, copy.deepcopy(before_data)
        if state in {"accepted", "unknown"}:
            observed = client.observe_quality_operation(project, op_id)
            require_text_free(observed)
            if observed and observed.get("state") == "confirmed" and "result" in observed:
                state, data = "confirmed", {**data, "result": observed["result"]}
            elif observed and observed.get("state") == "failed":
                state = "failed"
            elif observed is None and data.get("definite_refusal") in {401, 403, 429}:
                state = "failed"
            else:
                continue
        kind = key.split(":", 1)[0]
        if _unsettled(state, key, data):
            result = data.get("result")
            if not isinstance(result, dict):
                raise DomainError("weblate_schema", "Quality receipt has no run identity", 502)
            if kind == "full-judge":
                observed = client.get_producer_run(result["id"])
                identity = "id"
            elif kind == "full-repeat-apply":
                observed = client.get_repeat_run(result["token"])
                identity = "token"
            elif kind == "full-repeat-plan":
                observed = client.get_repeat_plan(result["token"])
                identity = "token"
            else:
                continue
            require_text_free(observed)
            if observed.get(identity) != result[identity]:
                raise DomainError("weblate_schema", "Quality observation changed run identity", 502)
            data["result"] = observed
        with store.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job, op = session.get(Job, job_id), session.get(QualityOperation, op_id)
            if (
                job
                and job.owner == owner
                and job.status in {"cancelled", "failed"}
                and not job.lease_owner
                and op
                and op.state == before_state
                and op.data == before_data
            ):
                op.state, op.data = state, data
