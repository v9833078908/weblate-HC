# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from app.db import DomainError


def async_mt_enabled(settings, project):
    return settings.mt_async_batches_enabled and (
        "*" in settings.mt_async_projects or project in settings.mt_async_projects
    )


def public_mt_observation(data):
    """Explicit allowlist: never expose payloads, prompts or provider answers."""
    scopes = {
        key: {
            name: value.get(name)
            for name in (
                "state",
                "filled",
                "total",
                "last_progress",
                "next_poll_at",
                "deadline",
                "no_progress_warning",
                "exceptions",
                "payment_unknown",
                "coverage_unknown",
            )
        }
        for key, value in data.get("mt_batches", {}).items()
    }
    return {
        "translated": sum(value.get("filled") or 0 for value in scopes.values()),
        "total": sum(value.get("total") or 0 for value in scopes.values()),
        "scopes": scopes,
    }


def mt_unit_revision(row):
    value = {key: row[key] for key in ("id", "source", "context", "target", "state")}
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def pending_mt_units(rows):
    selected = []
    seen = set()
    for row in rows:
        ident = row.get("id")
        if type(ident) is not int or ident <= 0 or ident in seen:
            raise DomainError("weblate_schema", "Invalid MT unit identity", 502)
        seen.add(ident)
        target = row.get("target")
        if not isinstance(target, list) or any(not isinstance(v, str) for v in target):
            raise DomainError("weblate_schema", "Invalid MT plural target", 502)
        if any(target) or row.get("state") != 0:
            continue
        selected.append({"unit_id": ident, "revision": mt_unit_revision(row)})
    return sorted(selected, key=lambda r: r["unit_id"])


def advance_mt_batches(
    store,
    run_id,
    client,
    *,
    data,
    checkpoint,
    settings,
    component,
    language,
    unit_ids=None,
    lease=None,
):
    """Advance one durable scope; the caller's checkpoint retains its worker fence."""
    from app.services.run_isolation import assert_project_not_isolated

    assert_project_not_isolated(store, data["remote_project"], data.get("project"))
    project = data["remote_project"]
    scope_key = component + ":" + language
    now = time.time()
    scope = data.setdefault("mt_batches", {}).setdefault(
        scope_key,
        {
            "started": now,
            "deadline": now + settings.mt_max_observation_seconds,
            "last_progress": now,
            "filled": 0,
            "history": [],
            "active": None,
            "next_poll_at": 0,
            "poll_attempt": 0,
        },
    )
    data["mt_next_poll_at"] = scope["next_poll_at"]
    # Older checkpoints did not save the configured maximum. Preserve the
    # previous release's conservative ceiling instead of granting a new window.
    scope.setdefault("deadline", scope["started"] + min(7200, settings.mt_max_observation_seconds))
    if not scope.get("autonomous") and (
        now >= scope["deadline"] or now - scope["last_progress"] >= settings.mt_observation_seconds
    ):
        raise DomainError(
            "weblate_unknown",
            "MT observation deadline reached; saved requests were not repeated",
            503,
        )

    if not scope.get("autonomous") and now < scope["next_poll_at"]:
        return False

    def defer(delay, state):
        scope["next_poll_at"] = time.time() + max(1, delay)
        scope["state"] = state
        data["mt_next_poll_at"] = scope["next_poll_at"]
        checkpoint()
        return False

    def rows_now():
        nonlocal transport_phase
        transport_phase = "read"
        rows = client.list_units(project, component, language)
        transport_phase = None
        if unit_ids is not None:
            wanted = set(unit_ids)
            rows = [r for r in rows if r["id"] in wanted]
            if {r["id"] for r in rows} != wanted:
                raise DomainError("mt_scope", "MT scope changed", 409)
        # Validation also rejects duplicate identities and malformed plural targets.
        pending_mt_units(rows)
        if any(not any(row["target"]) and row.get("state") != 0 for row in rows):
            raise DomainError(
                "mt_scope", "Empty rows with protected state require reconciliation", 409
            )
        filled = sum(any(r["target"]) for r in rows)
        if filled > scope["filled"]:
            scope["last_progress"] = time.time()
        scope.update(filled=filled, total=len(rows))
        return rows

    transport_phase = None
    try:
        if not scope.get("capability_verified"):
            transport_phase = "read"
            capabilities = client.producer_me().get("capabilities", {})
            transport_phase = None
            if (
                type(capabilities.get("mt_async_batches")) is not int
                or capabilities["mt_async_batches"] != 1
            ):
                raise DomainError(
                    "mt_configuration", "Weblate does not support durable MT batches", 503
                )
            scope["capability_verified"] = True
            scope["autonomous"] = capabilities.get("mt_reconciliation") == 1
        if scope.get("autonomous"):
            from app.services.run_recovery import advance_run_recovery

            return advance_run_recovery(
                store,
                run_id,
                client,
                data=data,
                scope=scope,
                checkpoint=checkpoint,
                settings=settings,
                component=component,
                language=language,
                unit_ids=unit_ids,
                lease=lease,
            )
        active = scope["active"]
        receipt = None
        if active is not None:
            transport_phase = "observe"
            receipt = client.get_mt_batch(project, active["key"])
            transport_phase = None
            if receipt and receipt.get("state") == "failed" and receipt.get("retryable") is True:
                # Server proves no unit outbound intent or confirmed publication.
                receipt = None
        if active is None:
            rows = rows_now()
            pending = pending_mt_units(rows)
            if not pending:
                scope.update(state="completed", next_poll_at=0)
                data["mt_next_poll_at"] = 0
                data["operations"]["mt:" + scope_key] = {"state": "confirmed", "async": True}
                checkpoint()
                return True
            legacy = [
                op
                for key, op in data["operations"].items()
                if (key == "mt:" + scope_key or key.startswith("delta:mt:" + scope_key + ":"))
                and op.get("state") in {"intent", "accepted"}
                and not op.get("async")
            ]
            if legacy and scope_key not in data.get("legacy_mt_reconciled", []):
                raise DomainError(
                    "weblate_unknown", "Legacy MT must be reconciled before new requests", 503
                )
            active = {
                "key": str(uuid4()),
                "attempts": 0,
                "payload": {
                    "component": component,
                    "language": language,
                    "engine": data["snapshot"]["engine"],
                    "threshold": 80,
                    "units": pending[:20],
                },
            }
            scope["active"] = active
            data["operations"]["mt:" + scope_key] = {"state": "intent", "async": True}
            checkpoint()
        if receipt is None:
            if active["attempts"] >= 8:
                raise DomainError(
                    "weblate_unknown",
                    "MT acceptance remains unconfirmed; request key retained",
                    503,
                )
            active["attempts"] += 1
            checkpoint()
            transport_phase = "submit"
            receipt = client.start_mt_batch(project, active["payload"], active["key"])
            transport_phase = None
        if receipt.get("key") != active["key"]:
            raise DomainError("weblate_schema", "MT receipt key mismatch", 502)
        expected = {r["unit_id"]: r["revision"] for r in active["payload"]["units"]}
        results = receipt.get("units", [])
        if (
            len(results) != len(expected)
            or {r.get("unit_id") for r in results} != set(expected)
            or any(r.get("revision") != expected[r["unit_id"]] for r in results)
        ):
            raise DomainError("weblate_schema", "MT receipt scope mismatch", 502)
        if receipt["state"] in {"accepted", "running"}:
            rows_now()
            scope["poll_attempt"] += 1
            return defer((5, 10, 20, 30)[min(scope["poll_attempt"] - 1, 3)], "polling")
        if receipt["state"] != "completed":
            scope["state"] = receipt["state"]
            checkpoint()
            raise DomainError(
                "weblate_unknown", "MT batch needs reconciliation; no paid retry was sent", 503
            )
        current = {r["id"]: r for r in rows_now()}
        for result in results:
            row = current.get(result["unit_id"])
            if (
                result.get("state") not in {"applied", "skipped_nonempty"}
                or not row
                or not any(row["target"])
                or mt_unit_revision(row) != result.get("after_revision")
            ):
                raise DomainError(
                    "weblate_unknown",
                    "MT result changed; saved translations were not overwritten",
                    503,
                )
        scope["history"].append(
            {"key": active["key"], "run_id": receipt["run_id"], "units": results}
        )
        scope.update(active=None, poll_attempt=0, state="preparing", next_poll_at=0)
        if not pending_mt_units(list(current.values())):
            scope["state"] = "completed"
            data["mt_next_poll_at"] = 0
            data["operations"]["mt:" + scope_key] = {"state": "confirmed", "async": True}
            checkpoint()
            return True
        return defer(1, "preparing")
    except DomainError as error:
        if getattr(error, "remote_status", None) == 429:
            scope["poll_attempt"] += 1
            delay = getattr(error, "retry_after", None)
            return defer(
                delay
                if delay is not None
                else min(300, 5 * 2 ** min(scope["poll_attempt"] - 1, 6)),
                "rate-limited",
            )
        transient = (
            error.code in {"weblate_unavailable", "weblate_unknown"}
            or getattr(error, "remote_status", 0) >= 500
        )
        if transport_phase is not None and transient:
            scope["poll_attempt"] += 1
            return defer(min(300, 5 * 2 ** min(scope["poll_attempt"] - 1, 6)), "reconciling")
        raise
