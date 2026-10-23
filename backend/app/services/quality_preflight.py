# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import DomainError


def filled_quality_scope(rows):
    """Quality never widens a partial MT result into preparatory paid MT."""
    selected, excluded = [], []
    for row in rows:
        targets = row.get("target")
        filled = (
            isinstance(targets, list)
            and targets
            and all(isinstance(text, str) and text.strip() for text in targets)
        )
        (selected if filled else excluded).append(row["id"])
    if len(set(selected + excluded)) != len(selected + excluded):
        raise DomainError("quality_scope", "Duplicate quality unit identity", 409)
    return {"unit_ids": sorted(selected)}, sorted(excluded)


def run_quality_preflight(
    client,
    project: str,
    scope: dict,
    *,
    full_cycle: bool = False,
    require_no_mt: bool = False,
    execution_mode: str = "repair",
) -> dict:
    """Fail before paid work unless the complete two-seat contract is ready."""
    identity = client.producer_me()
    if full_cycle:
        from app.services.quality_contract import require_full_quality_capabilities

        require_full_quality_capabilities(identity)
    capabilities = identity.get("capabilities")
    if execution_mode not in {"repair", "verify_only"}:
        raise DomainError("quality_policy", "Unknown quality execution mode", 409)
    if execution_mode == "verify_only" and (
        not isinstance(capabilities, dict)
        or capabilities.get("quality_execution_policy") != 1
        or capabilities.get("verify_only_immutable") != 1
    ):
        raise DomainError(
            "quality_configuration", "Server does not enforce immutable verification", 409
        )
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("judge") is not True
        or capabilities.get("judge_seats") != 2
    ):
        raise DomainError(
            "quality_configuration",
            "Weblate dual-judge quality checks are not fully configured",
            503,
        )

    estimate = (
        client.judge_estimate(project, scope, execution_mode=execution_mode)
        if execution_mode == "verify_only"
        else client.judge_estimate(project, scope)
    )
    if execution_mode == "verify_only" and estimate.get("execution_mode") != execution_mode:
        raise DomainError("weblate_schema", "Server did not bind verification policy", 502)
    preparation = estimate.get("preparation")
    if not isinstance(preparation, dict):
        raise DomainError("weblate_schema", "Weblate estimate has no preparation contract", 502)
    blockers = preparation.get("blockers")
    if not isinstance(blockers, list):
        raise DomainError("weblate_schema", "Weblate estimate has invalid blockers", 502)
    missing = preparation.get("missing")
    engine = preparation.get("engine")
    if (
        type(missing) is not int
        or missing < 0
        or "engine" not in preparation
        or (engine is not None and (not isinstance(engine, str) or not engine.strip()))
    ):
        raise DomainError("weblate_schema", "Weblate estimate has invalid preparation fields", 502)
    if require_no_mt and (missing != 0 or estimate.get("selected") != len(scope["unit_ids"])):
        raise DomainError(
            "quality_input_incomplete",
            "Quality-only requires a fully translated, reviewable scope and never prepares MT",
            409,
        )
    # A filled scope needs no preparatory MT; Weblate correctly reports engine=null.
    if blockers or (missing > 0 and engine is None):
        raise DomainError(
            "quality_configuration",
            "Weblate machine translation prerequisites are not ready",
            503,
        )
    selected = estimate.get("selected")
    if type(selected) is not int or selected < 0:
        raise DomainError("weblate_schema", "Weblate estimate has invalid selected count", 502)
    if selected == 0:
        raise DomainError(
            "quality_configuration", "Weblate selected no reviewable units for the judge scope", 503
        )
    return estimate
