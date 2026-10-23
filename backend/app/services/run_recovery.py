# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from uuid import uuid4

from app.db import DomainError, revision
from app.services.mt_reconciliation import reconcile_mt_receipt


def next_mt_wakeup(data):
    waiting = [
        scope.get("next_poll_at", 0)
        for scope in data.get("mt_batches", {}).values()
        if scope.get("state") not in {"completed", "completed_with_exceptions"}
    ]
    return min(waiting) if waiting else 0


def record_unit_progress(store, job_id, purpose, rows, scope, lease):
    if store is None:
        return
    from sqlalchemy import update

    from app.models import Job, RunUnitProgress
    from app.services.mt_batches import mt_unit_revision

    with store.session() as session:
        job = session.get(Job, job_id)
        if job is not None:
            changed = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.lease_owner == lease, Job.lease_until > time.time())
                .values(lease_owner=lease)
            )
            if changed.rowcount != 1:
                raise DomainError("lease_lost", "Job ownership changed", 409)
        for row in rows:
            ident = revision([job_id, row["id"], purpose])
            record = session.get(RunUnitProgress, ident)
            disposition = (
                "ready"
                if any(row["target"])
                else scope["blocked"].get(
                    str(row["id"]), "waiting" if row["id"] in scope["submitted"] else "never_sent"
                )
            )
            receipt_key = (scope.get("active") or {}).get("key", "")
            if not receipt_key:
                receipt_key = next(
                    (
                        item["key"]
                        for item in reversed(scope["history"])
                        if any(unit.get("unit_id") == row["id"] for unit in item.get("units", []))
                    ),
                    record.receipt_key if record is not None else "",
                )
            values = {
                "disposition": disposition,
                "receipt_key": receipt_key,
                "remote_revision": mt_unit_revision(row),
                "updated": time.time(),
            }
            if record is None:
                session.add(
                    RunUnitProgress(
                        id=ident, job_id=job_id, unit_id=row["id"], purpose=purpose, **values
                    )
                )
            else:
                for name, value in values.items():
                    setattr(record, name, value)


def advance_run_recovery(
    store,
    job_id,
    client,
    *,
    data,
    scope,
    checkpoint,
    settings,
    component,
    language,
    unit_ids=None,
    lease=None,
):
    """Advance one fenced scope without stopping unrelated components."""
    from app.services.mt_batches import pending_mt_units

    now = time.time()
    project = data["remote_project"]
    scope_key = component + ":" + language
    scope.setdefault("submitted", [])
    scope.setdefault("blocked", {})
    scope.setdefault("payment_unknown", [])
    scope.setdefault("deadline", scope["started"] + settings.mt_max_observation_seconds)
    observed_rows = []

    def save():
        data["mt_next_poll_at"] = next_mt_wakeup(data)
        checkpoint()
        record_unit_progress(store, job_id, "mt:" + scope_key, observed_rows, scope, lease)

    def defer(delay, state):
        if time.time() >= scope["deadline"]:
            if scope.get("active"):
                scope["payment_unknown"] = sorted(
                    set(scope["payment_unknown"])
                    | {item["unit_id"] for item in scope["active"]["payload"]["units"]}
                )
            if observed_rows:
                for row in observed_rows:
                    if not any(row["target"]):
                        scope["blocked"].setdefault(str(row["id"]), "observation_deadline")
                return finish(observed_rows)
            scope.update(state="observation_expired", coverage_unknown=True, next_poll_at=0)
            save()
            raise DomainError(
                "weblate_unknown",
                "Observation deadline reached without a current coverage read; no paid request repeated",
                503,
            )
        scope.update(next_poll_at=time.time() + max(1, delay), state=state)
        save()
        return False

    if now < scope["next_poll_at"] and now < scope["deadline"]:
        return False

    def read_rows():
        nonlocal observed_rows
        rows = client.list_units(project, component, language)
        pending_mt_units(rows)
        if unit_ids is not None:
            expected = set(unit_ids)
            rows = [row for row in rows if row["id"] in expected]
            if {row["id"] for row in rows} != expected:
                raise DomainError("mt_scope", "MT scope changed", 409)
        filled = sum(bool(any(row["target"])) for row in rows)
        if filled > scope["filled"]:
            scope["last_progress"] = time.time()
        scope.update(filled=filled, total=len(rows))
        scope["no_progress_warning"] = (
            time.time() - scope["last_progress"] >= settings.mt_observation_seconds
        )
        observed_rows = rows
        return rows

    def finish(rows):
        empty = {row["id"] for row in rows if not any(row["target"])}
        exceptions = {}
        for ident in empty:
            reason = scope["blocked"].get(str(ident), "never_sent")
            exceptions.setdefault(reason, []).append(ident)
        scope["exceptions"] = {reason: sorted(ids) for reason, ids in exceptions.items()}
        scope.update(
            state="completed_with_exceptions"
            if exceptions or scope["payment_unknown"]
            else "completed",
            next_poll_at=0,
        )
        data["operations"]["mt:" + scope_key] = {
            "state": "confirmed",
            "async": True,
            "with_exceptions": bool(exceptions or scope["payment_unknown"]),
        }
        save()
        return True

    try:
        rows = read_rows()
        active = scope["active"]
        receipt = None
        if active:
            receipt = client.get_mt_batch(project, active["key"])
            if receipt is None or (
                receipt.get("retryable") is True and receipt.get("state") == "failed"
            ):
                if now >= scope["deadline"] or active["attempts"] >= 8:
                    for item in active["payload"]["units"]:
                        scope["blocked"][str(item["unit_id"])] = "paid_unknown"
                    scope["payment_unknown"] = sorted(
                        set(scope["payment_unknown"])
                        | {item["unit_id"] for item in active["payload"]["units"]}
                    )
                    scope["history"].append(
                        {
                            "key": active["key"],
                            "state": "acceptance_unknown",
                            "units": active["payload"]["units"],
                        }
                    )
                    scope["active"] = None
                    return finish(rows)
                active["attempts"] += 1
                save()
                receipt = client.start_mt_batch(project, active["payload"], active["key"])
        if active is not None and receipt is not None:
            if receipt.get("key") != active["key"]:
                raise DomainError("weblate_schema", "MT receipt key mismatch", 502)
            expected = {item["unit_id"]: item["revision"] for item in active["payload"]["units"]}
            if receipt.get("state") in {"accepted", "running"}:
                if now >= scope["deadline"]:
                    for ident in expected:
                        scope["blocked"][str(ident)] = "paid_unknown"
                    scope["payment_unknown"] = sorted(set(scope["payment_unknown"]) | set(expected))
                    return finish(rows)
                scope["poll_attempt"] += 1
                return defer((5, 10, 20, 30, 60)[min(scope["poll_attempt"] - 1, 4)], "polling")
            reconciled = client.reconcile_mt_batch(project, active["key"])
            if reconciled.get("key") != active["key"]:
                raise DomainError("weblate_schema", "MT reconciliation key mismatch", 502)
            rows = read_rows()
            groups = reconcile_mt_receipt(reconciled, rows, expected)
            if groups["waiting"]:
                return defer(15, "reconciling")
            for reason, identities in groups.items():
                if reason not in {"ready", "never_sent", "waiting"}:
                    for ident in identities:
                        scope["blocked"][str(ident)] = reason
            scope["payment_unknown"] = sorted(
                set(scope["payment_unknown"])
                | {
                    item["unit_id"]
                    for item in reconciled["units"]
                    if item.get("state") in {"provider_unknown", "requesting"}
                }
            )
            scope["submitted"] = sorted(set(scope["submitted"]) - set(groups["never_sent"]))
            scope["history"].append(
                {
                    "key": active["key"],
                    "run_id": reconciled["run_id"],
                    "state": receipt["state"],
                    "units": reconciled["units"],
                    "reconciliation": groups,
                }
            )
            scope.update(active=None, poll_attempt=0, next_poll_at=0)
            save()

        submitted = set(scope["submitted"])
        blocked = {int(ident) for ident in scope["blocked"]}
        pending = [
            row for row in pending_mt_units(rows) if row["unit_id"] not in submitted | blocked
        ]
        if not pending or now >= scope["deadline"]:
            return finish(rows)
        # Existing legacy uncertain work cannot be reclassified as never sent.
        if any(
            not op.get("async") and op.get("state") in {"intent", "accepted"}
            for key, op in data["operations"].items()
            if key == "mt:" + scope_key or key.startswith("delta:mt:" + scope_key + ":")
        ):
            raise DomainError("weblate_unknown", "Legacy MT requires reconciliation", 503)
        active = {
            "key": str(uuid4()),
            "attempts": 1,
            "payload": {
                "component": component,
                "language": language,
                "engine": data["snapshot"]["engine"],
                "threshold": 80,
                "units": pending[:20],
            },
        }
        scope["submitted"] = sorted(submitted | {row["unit_id"] for row in pending[:20]})
        scope["active"] = active
        data["operations"]["mt:" + scope_key] = {"state": "intent", "async": True}
        save()
        receipt = client.start_mt_batch(project, active["payload"], active["key"])
        if receipt.get("key") != active["key"]:
            raise DomainError("weblate_schema", "MT receipt key mismatch", 502)
        return defer(5, "polling")
    except DomainError as error:
        status = getattr(error, "remote_status", None)
        if status in {400, 401, 403}:
            scope["configuration_error"] = error.code
            return defer(60, "needs_configuration")
        if status == 429:
            return defer(getattr(error, "retry_after", None) or 60, "rate-limited")
        if error.code in {"weblate_unavailable", "weblate_unknown"} or (status and status >= 500):
            scope["poll_attempt"] += 1
            return defer(min(300, 5 * 2 ** min(scope["poll_attempt"] - 1, 6)), "reconciling")
        raise
