# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from collections import Counter

_EXCEPTION_OUTCOMES = {
    "forbidden",
    "needs_individual_confirmation",
    "not_verified",
    "stale",
    "unparsed",
    "no_candidate",
    "exhausted",
    "consistency_issues",
    "quarantined_transport",
}


def quality_counts(decisions: list[dict], apply_results: list[dict] | None = None) -> dict:
    counts = Counter()
    for decision in decisions:
        severity = decision.get("severity") or "none"
        counts[severity] += 1
        counts[decision.get("applicability") or "unknown"] += 1
    for result in apply_results or []:
        counts[result.get("outcome") or "unknown"] += 1
    return dict(sorted(counts.items()))


def terminal_quality_status(counts: dict) -> str:
    return (
        "completed_with_exceptions"
        if any(counts.get(outcome, 0) for outcome in _EXCEPTION_OUTCOMES)
        else "completed"
    )


def full_quality_report(store, owner: str, cycle_id: str) -> dict:
    from app.services.quality_cycle import quality_unit_census
    from app.services.quality_state import (
        get_quality_cycle,
        get_quality_operation,
        list_quality_groups,
    )

    cycle = get_quality_cycle(store, owner, cycle_id)
    final = get_quality_operation(store, cycle_id, "final-snapshot")
    return {
        "cycle_id": cycle_id,
        "phase": cycle["phase"],
        "round": cycle["round"],
        "status": cycle["status"],
        "counts": cycle["counts"],
        "units": quality_unit_census(store, cycle_id),
        "groups": list_quality_groups(store, owner, cycle_id),
        "checks_complete": final is not None,
        "scope_revision": final["data"]["scope_revision"] if final else None,
        "residual_issues": final["data"]["issues"] if final else [],
    }
