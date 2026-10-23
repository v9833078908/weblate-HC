# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

from app.db import DomainError
from app.services.quality_contract import (
    RepeatPlan,
    RepeatRun,
    require_text_free,
    validate_contract,
    validate_snapshot,
)
from app.services.quality_policy import acceptable_candidate, required_issue, two_seats_parsed
from app.services.quality_preflight import run_quality_preflight
from app.services.quality_state import (
    checkpoint_full_quality,
    get_quality_cycle,
    get_quality_operation,
    record_final_quality_outcomes,
    record_quality_groups,
    record_quality_units,
    reserve_quality_operation,
    transition_quality_operation,
    update_quality_cycle,
    update_quality_unit_outcomes,
)


def _save(store, cycle, key, data):
    operation = reserve_quality_operation(store, cycle["id"], key, "full-quality-state")
    transition_quality_operation(store, operation["id"], "confirmed", data=data)


def _data(store, cycle, key):
    operation = get_quality_operation(store, cycle["id"], key)
    return operation["data"] if operation else None


def _move(store, cycle, context, phase, **changes):
    return checkpoint_full_quality(
        store,
        cycle["id"],
        context,
        phase=phase,
        status="running",
        next_wakeup=0,
        **changes,
    )


def _wait(store, cycle):
    return update_quality_cycle(
        store,
        cycle["id"],
        status="waiting",
        next_wakeup=time.time() + 15,
    )


def _remote_once(store, cycle, client, key, perform):
    """Persist intent before sending; recover a lost response by read-after-write."""
    operation = reserve_quality_operation(store, cycle["id"], key, "full-quality-write")
    remote_key = operation["id"]
    if operation["state"] == "confirmed":
        return operation["data"]["result"]
    if operation["state"] in {"accepted", "unknown"}:
        observed = client.observe_quality_operation(cycle["remote_project"], remote_key)
        if observed and observed["state"] == "confirmed" and "result" in observed:
            result = observed["result"]
            require_text_free(result)
            transition_quality_operation(store, remote_key, "confirmed", data={"result": result})
            return result
        if observed and observed["state"] == "failed":
            raise DomainError("quality_operation_failed", "Remote quality operation failed", 409)
        if observed is None and operation["data"].get("definite_refusal") in {401, 403, 429}:
            if operation["attempt"] >= 5 and operation["data"]["definite_refusal"] != 429:
                raise DomainError(
                    "quality_operation_failed", "Quality refusal retry limit reached", 409
                )
        else:
            if operation["attempt"] >= 5:
                raise DomainError("weblate_unknown", "Quality write remains unconfirmed", 503)
            transition_quality_operation(store, remote_key, "unknown")
            return None
    if operation["state"] == "failed":
        raise DomainError("quality_operation_failed", "Quality operation was refused", 409)
    transition_quality_operation(store, remote_key, "accepted", data={})
    try:
        result = perform(remote_key)
    except DomainError as error:
        refusal = getattr(error, "remote_status", None)
        if refusal in {401, 403, 429}:
            transition_quality_operation(
                store,
                remote_key,
                "accepted",
                failure_class=error.code,
                data={"definite_refusal": refusal},
            )
            raise
        transition_quality_operation(
            store,
            remote_key,
            "unknown" if error.code == "weblate_unknown" else "failed",
            failure_class=error.code,
        )
        if error.code == "weblate_unknown":
            return None
        raise
    require_text_free(result)
    transition_quality_operation(store, remote_key, "confirmed", data={"result": result})
    return result


def _check_results(results, requested):
    outcomes = {
        "applied",
        "already_applied",
        "no_change",
        "stale",
        "forbidden",
        "approved",
        "independent",
        "needs_review",
        "failed",
        "needs_individual_confirmation",
        "not_verified",
    }
    if (
        not isinstance(results, list)
        or any(not isinstance(row, dict) for row in results)
        or any(
            type(row.get("unit")) is not int
            or row.get("outcome") not in outcomes
            or not isinstance(row.get("revision"), str)
            for row in results
        )
        or {row["unit"] for row in results} != set(requested)
        or len(results) != len(requested)
        or any(row["outcome"] == "applied" and not row["revision"] for row in results)
    ):
        raise DomainError("weblate_schema", "Quality apply results do not match the request", 502)


def _recheck(store, cycle, client, context, items, key):
    for offset in range(0, len(items), 1000):
        batch = items[offset : offset + 1000]
        part = f"{key}:{offset}"
        if _data(store, cycle, part) is not None:
            continue
        result = _remote_once(
            store,
            cycle,
            client,
            part + ":write",
            lambda identity: client.recheck_full_quality(cycle["remote_project"], batch, identity),
        )
        if result is None:
            return False
        require_text_free(result)
        if (
            not isinstance(result.get("affected_unit_ids"), list)
            or not isinstance(result.get("affected_repeat_group_ids"), list)
            or not isinstance(result.get("items"), list)
            or {row.get("unit_id") for row in result["items"]} != {row["unit_id"] for row in batch}
        ):
            raise DomainError("weblate_schema", "Group-aware recheck is incomplete", 502)
        _save(store, cycle, part, result)
    return True


def finalize_quality_snapshot(store, cycle, context, snapshot):
    assert_verification_baseline(store, cycle, snapshot)
    required = [issue for issue in snapshot["issues"] if required_issue(issue)]
    unverified = [
        unit
        for unit, rev in snapshot["revisions"].items()
        if snapshot["judged_revisions"].get(unit) != rev
    ]
    counts = {
        "processed": len(snapshot["unit_ids"]),
        "automatically_corrected": len(context["corrected"]),
        "consistency_issues": len(required),
        "glossary_hard": sum(issue["kind"] == "glossary" for issue in required),
        "repeat_groups": sum(issue["kind"] == "repeat-drift" for issue in required),
        "advisory": len(snapshot["issues"]) - len(required),
        "not_verified": len(unverified),
        "quarantined_transport": cycle["counts"].get("quarantined_transport", 0),
    }
    _save(store, cycle, "final-snapshot", snapshot)
    record_final_quality_outcomes(store, cycle["id"], snapshot)
    from app.services.quality_cycle import quality_unit_census

    counts["both_parsed"] = sum(
        row.get("current_judge_verified") is True
        or (
            row.get("input_revision") == snapshot["revisions"].get(str(row["unit_id"]))
            and two_seats_parsed(row.get("live_seat_results", {}))
        )
        for row in quality_unit_census(store, cycle["id"])
    )
    counts["pass"] = len(snapshot["unit_ids"]) - len(unverified)
    from app.services.quality_state import list_quality_groups

    unresolved = {issue["group_id"] for issue in required}
    groups = list_quality_groups(store, cycle["owner"], cycle["id"])
    for group in groups:
        if group["group_id"] not in unresolved:
            group["outcome"] = (
                "applied" if set(group["unit_ids"]) & set(context["corrected"]) else "no_change"
            )
        elif group["outcome"] == "ready":
            group["outcome"] = "needs_review"
    record_quality_groups(store, cycle["owner"], cycle["id"], groups, cycle["round"])
    _save(store, cycle, "full-context", context)
    return update_quality_cycle(
        store,
        cycle["id"],
        phase="completed",
        next_wakeup=0,
        counts=counts,
        status="completed_with_exceptions"
        if required or unverified or counts["quarantined_transport"]
        else "completed",
    )


def assert_verification_baseline(store, cycle, snapshot):
    if cycle["policy"].get("execution_mode") != "verify_only":
        return
    initial = _data(store, cycle, "initial-snapshot")
    if (
        not initial
        or not initial.get("protected_content_digest")
        or (initial["protected_content_digest"] != snapshot.get("protected_content_digest"))
    ):
        raise DomainError(
            "quality_snapshot_stale",
            "Verification protected content changed; no result published",
            409,
        )


def advance_full_quality_cycle(store, owner, cycle_id, client, *, scope, language):
    cycle = get_quality_cycle(store, owner, cycle_id)
    from app.services.run_isolation import assert_project_not_isolated

    assert_project_not_isolated(store, cycle["remote_project"])
    if cycle["phase"] == "completed" or cycle["next_wakeup"] > time.time():
        return cycle
    context = _data(store, cycle, "full-context") or {
        "scope": scope,
        "judge_scope": cycle["policy"].get("initial_judge_scope", scope),
        "applications": [],
        "corrected": [],
        "pass": 0,
        "apply_items": [],
        "last_changed": [],
    }
    if context["scope"] != scope:
        raise DomainError("quality_scope", "Saved full-quality scope changed", 409)
    project = cycle["remote_project"]
    number = context["pass"]
    suffix = str(number)
    phase = cycle["phase"]
    verify_only = cycle["policy"].get("execution_mode", "repair") == "verify_only"
    if verify_only and phase in {
        "individual-apply",
        "affected-recheck",
        "repeat-plan",
        "repeat-apply",
        "recipient-recheck",
    }:
        context["skipped_mutations"] = "verify_only"
        return _move(store, cycle, context, "final-scan")
    if phase == "preflight":
        if not context["judge_scope"]["unit_ids"]:
            context["skipped_judge"] = "no_filled_translations"
            return _move(store, cycle, context, "initial-scan")
        estimate = run_quality_preflight(
            client,
            project,
            context["judge_scope"],
            full_cycle=True,
            require_no_mt=cycle["policy"].get("require_no_mt", False),
            execution_mode=cycle["policy"].get("execution_mode", "repair"),
        )
        _save(store, cycle, f"full-estimate:{suffix}", estimate)
        return _move(store, cycle, context, "initial-scan" if number == 0 else "full-judge-start")
    if phase == "initial-scan":
        snapshot = validate_snapshot(client.quality_snapshot(project, language, scope), scope)
        if verify_only and not snapshot.get("protected_content_digest"):
            raise DomainError(
                "quality_configuration", "Server has no protected verification baseline", 409
            )
        _save(store, cycle, "initial-snapshot", snapshot)
        return _move(
            store,
            cycle,
            context,
            "full-judge-start" if context["judge_scope"]["unit_ids"] else "final-scan",
        )
    if phase == "full-judge-start":
        estimate = _data(store, cycle, f"full-estimate:{suffix}")
        run = _remote_once(
            store,
            cycle,
            client,
            f"full-judge:{suffix}",
            lambda key: client.start_quality_judge(
                project,
                estimate["estimate_id"],
                context["judge_scope"],
                key,
                **({"execution_mode": "verify_only"} if verify_only else {}),
            ),
        )
        if run is None:
            return _wait(store, cycle)
        context["run_id"] = run["id"]
        return _move(store, cycle, context, "full-judge-poll")
    if phase == "full-judge-poll":
        run = client.get_producer_run(context["run_id"])
        if run["status"] in {"queued", "running", "cancel_requested"}:
            return _wait(store, cycle)
        decisions = (
            client.list_decisions(project, context["run_id"])
            if run["status"] in {"completed", "partial"}
            else []
        )
        require_text_free(decisions)
        requested = set(context["judge_scope"]["unit_ids"])
        if len(decisions) != len({row.get("unit_id") for row in decisions}) or not {
            row.get("unit_id") for row in decisions
        }.issubset(requested):
            raise DomainError(
                "weblate_schema", "Judge decisions exceed or duplicate the scope", 502
            )
        missing = requested - {row["unit_id"] for row in decisions}
        if missing:
            current = validate_snapshot(client.quality_snapshot(project, language, scope), scope)
            decisions.extend(
                {
                    "unit_id": unit,
                    "revision": current["revisions"][str(unit)],
                    "severity": "unparsed",
                    "applicability": "not_verified",
                    "live_seat_results": {},
                    "seat_results": {},
                }
                for unit in sorted(missing)
            )
        normalized, items = [], []
        for decision in decisions:
            row = dict(decision)
            if (
                not verify_only
                and acceptable_candidate(row)
                and number < cycle["policy"].get("max_repair_rounds", 2)
            ):
                items.append(
                    {
                        "unit": row["unit_id"],
                        "revision": row["revision"],
                        "candidate_id": row["candidate_id"],
                    }
                )
            elif row.get("applicability") == "no_candidate" and (
                row.get("severity") in {"none", "minor"}
                and two_seats_parsed(row.get("live_seat_results", {}))
            ):
                row["applicability"] = "clean"
            elif row.get("applicability") == "ready":
                row["applicability"] = "not_verified"
            normalized.append(row)
        record_quality_units(store, cycle_id, normalized, number)
        context["apply_items"] = items
        return _move(store, cycle, context, "individual-apply")
    if phase == "individual-apply":
        items = context["apply_items"]
        results = []
        if items:
            results = _remote_once(
                store,
                cycle,
                client,
                f"full-apply:{suffix}",
                lambda key: client.apply_quality_candidates(project, items, key),
            )
            if results is None:
                return _wait(store, cycle)
            _check_results(results, [item["unit"] for item in items])
            update_quality_unit_outcomes(store, cycle_id, results)
        applied = [row for row in results if row["outcome"] == "applied"]
        context["applications"] = sorted(
            set(context["applications"])
            | {row["application_id"] for row in applied if type(row.get("application_id")) is int}
        )
        context["last_changed"] = [
            {"unit_id": row["unit"], "revision": row["revision"]} for row in applied
        ]
        context["corrected"] = sorted(set(context["corrected"]) | {row["unit"] for row in applied})
        return _move(store, cycle, context, "affected-recheck")
    if phase == "affected-recheck":
        if not _recheck(
            store, cycle, client, context, context["last_changed"], f"full-recheck:{suffix}"
        ):
            return _wait(store, cycle)
        snapshot = validate_snapshot(client.quality_snapshot(project, language, scope), scope)
        _save(store, cycle, f"repeat-snapshot:{suffix}", snapshot)
        return _move(store, cycle, context, "repeat-plan")
    if phase == "repeat-plan":
        snapshot = _data(store, cycle, f"repeat-snapshot:{suffix}")
        if not any(issue["kind"] == "repeat-drift" for issue in snapshot["issues"]):
            return _move(store, cycle, context, "final-scan")
        plan = _remote_once(
            store,
            cycle,
            client,
            f"full-repeat-plan:{suffix}",
            lambda key: client.plan_repeat_quality(
                project, language, scope, snapshot["snapshot_hash"], context["applications"], key
            ),
        )
        if plan is None:
            return _wait(store, cycle)
        plan = validate_contract(RepeatPlan, plan)
        if plan["status"] in {"queued", "running"}:
            plan = validate_contract(RepeatPlan, client.get_repeat_plan(plan["token"]))
        if plan["status"] in {"queued", "running"}:
            return _wait(store, cycle)
        if plan["status"] == "failed":
            return _move(store, cycle, context, "final-scan")
        record_quality_groups(store, owner, cycle_id, plan["groups"], number)
        if not any(group["outcome"] == "ready" for group in plan["groups"]):
            return _move(store, cycle, context, "final-scan")
        context["plan_token"] = plan["token"]
        return _move(store, cycle, context, "repeat-apply")
    if phase == "repeat-apply":
        run = _remote_once(
            store,
            cycle,
            client,
            f"full-repeat-apply:{suffix}",
            lambda key: client.apply_repeat_plan(context["plan_token"], key),
        )
        if run is None:
            return _wait(store, cycle)
        run = validate_contract(RepeatRun, run)
        if run["status"] in {"queued", "running"}:
            run = validate_contract(RepeatRun, client.get_repeat_run(run["token"]))
        if run["status"] in {"queued", "running"}:
            return _wait(store, cycle)
        changed = run["changed_unit_ids"]
        if not set(changed).issubset(scope["unit_ids"]) or len(changed) != len(set(changed)):
            raise DomainError("weblate_schema", "Repeat write exceeded saved scope", 502)
        applied = {row["unit"]: row for row in run["results"] if row["outcome"] == "applied"}
        if set(applied) != set(changed):
            raise DomainError(
                "weblate_schema", "Repeat results do not match changed recipients", 502
            )
        context["corrected"] = sorted(set(context["corrected"]) | set(changed))
        context["last_changed"] = [
            {"unit_id": unit, "revision": applied[unit]["revision"]} for unit in changed
        ]
        return _move(store, cycle, context, "recipient-recheck")
    if phase == "recipient-recheck":
        if not _recheck(
            store, cycle, client, context, context["last_changed"], f"recipient-check:{suffix}"
        ):
            return _wait(store, cycle)
        changed = [row["unit_id"] for row in context["last_changed"]]
        if changed and number < cycle["policy"].get("max_repair_rounds", 2):
            context["judge_scope"] = {"unit_ids": changed}
            context["pass"] += 1
            return _move(store, cycle, context, "preflight", round=context["pass"])
        return _move(store, cycle, context, "final-scan")
    if phase == "final-scan":
        snapshot = validate_snapshot(client.quality_snapshot(project, language, scope), scope)
        return finalize_quality_snapshot(store, cycle, context, snapshot)
    raise DomainError("quality_state", "Unsupported full-quality phase", 409)
