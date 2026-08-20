# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The two-seat collegium and its repair loop.

Pure orchestration over the judge client: both seats judge EVERY string
independently (no cascade, no seat may lower another — measurement B2'
rejected the cascade), the round verdict is the strictest of the two,
and confirmed defects are repaired until the attempt budget is spent,
then left for the human queue. Repairs only touch strings the caller
declared writable (D3): without the overwrite switch a human translation
is judged, never rewritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction

from weblate.glossary.models import get_glossary_terms
from weblate.machinery.models import MACHINERY
from weblate.trans.forms import AutoForm
from weblate.trans.judge import (
    JudgeRequest,
    request_verdicts,
    validate_judge_configuration,
)
from weblate.trans.models.judge import (
    JudgeVerdict,
    collegium_verdict,
    compute_context_hash,
    compute_target_hash,
    current_round,
)
from weblate.utils.state import STATE_FUZZY

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.trans.models.project import Project
    from weblate.trans.models.unit import Unit

# A round is left alone (no repair attempt) when it did not confirm a
# defect: PASS needs no fix, UNPARSED is a transport failure, not an
# opinion to act on.
_NON_REPAIRABLE_VERDICTS = frozenset(
    {
        JudgeVerdict.Verdict.PASS,
        JudgeVerdict.Verdict.FLAG,
        JudgeVerdict.Verdict.UNPARSED,
    }
)


def _glossary_pairs(unit: Unit) -> list[tuple[str, str]]:
    """Source/target text of the glossary terms attached to the unit."""
    return [
        (term.source, term.target)
        for term in get_glossary_terms(unit, full=True)
        if term.target
    ]


def build_request(unit: Unit) -> JudgeRequest:
    """Collect everything the judge is told about one unit."""
    translation = unit.translation
    return JudgeRequest(
        unit_key=unit.context,
        source=unit.source,
        target=unit.target,
        source_language=translation.component.source_language.code,
        target_language=translation.language.code,
        note=unit.source_unit.note,
        glossary_terms=_glossary_pairs(unit),
        failing_checks=sorted(unit.all_checks_names),
        target_plurals=unit.get_target_plurals(),
    )


def repair_target(unit: Unit, user: User | None) -> list[str] | None:
    """
    Re-translate the unit through the project MT engine, or None.

    Judge evidence reaches the repair prompt for free: run_checks() has
    already projected the round's judge-* Check row, and
    weblate/machinery/llm.py feeds failing_checks with get_description()
    into the translator prompt. Callers MUST run_checks() first.
    """
    settings_map = unit.translation.component.project.get_machinery_settings()
    engine_id = AutoForm.DEFAULT_ENGINE
    setting = settings_map.get(engine_id)
    if setting is None or engine_id not in MACHINERY:
        return None
    results = MACHINERY[engine_id](setting).translate(unit, user)
    if not results:
        return None
    best = max(results, key=lambda item: item.get("quality", 0))
    text = best.get("text", "")
    if not text or text == unit.target:
        return None
    return [text]


def judge_project_context(project: Project) -> str:
    """
    Return the project's own description of its game, for the prompt.

    Read from the same machinery configuration the translator uses
    (``persona`` and ``style`` of the project's MT engine), so the judge
    and the translator cannot hold different ideas of the game. Nothing
    configured yields an empty string, and the client then falls back to
    a neutral context instead of another project's setting.
    """
    setting = project.get_machinery_settings().get(AutoForm.DEFAULT_ENGINE)
    if not setting:
        return ""
    parts = [
        str(setting.get(field, "") or "").strip() for field in ("persona", "style")
    ]
    return "\n\n".join(part for part in parts if part)


def _write_verdict(
    unit: Unit,
    request: JudgeRequest,
    seat: int,
    attempt: int,
    run_id: uuid.UUID,
    result,
    model: str,
) -> None:
    JudgeVerdict.objects.create(
        unit=unit,
        model_verdict=result.model_verdict,
        max_severity=result.max_severity,
        unparsed=result.unparsed,
        errors=result.errors,
        back_translation=result.back_translation,
        judge_model=model,
        seat=seat,
        attempt=attempt,
        target_hash=compute_target_hash(request.target_plurals or [request.target]),
        context_hash=compute_context_hash(
            source=request.source,
            note=request.note,
            glossary_terms=request.glossary_terms,
        ),
        run_id=run_id,
    )


def _refresh_unit(unit: Unit) -> Unit:
    """Reload a unit with the relations needed by judge requests and checks."""
    return type(unit).objects.filter(pk=unit.pk).prefetch().prefetch_source().get()


def _deterministic_checks(unit: Unit) -> set[str]:
    """Exclude judge projections from the no-regress check snapshot."""
    return {name for name in unit.all_checks_names if not name.startswith("judge-")}


def _cached_verdict(
    unit: Unit, request: JudgeRequest, models: tuple[str, str]
) -> JudgeVerdict | None:
    """Reuse the newest parsed two-seat verdict for an unchanged request."""
    target_hash = compute_target_hash(request.target_plurals or [request.target])
    context_hash = compute_context_hash(
        source=request.source,
        note=request.note,
        glossary_terms=request.glossary_terms,
    )
    queryset = JudgeVerdict.objects.filter(
        unit=unit,
        target_hash=target_hash,
        context_hash=context_hash,
        judge_model__in=models,
    )
    newest = queryset.order_by("-timestamp", "-pk").first()
    if newest is None:
        return None
    rows = list(
        queryset.filter(run_id=newest.run_id, attempt=newest.attempt).order_by("seat")
    )
    if (
        len(rows) != 2
        or any(row.unparsed for row in rows)
        or {row.judge_model for row in rows} != set(models)
    ):
        return None
    if {row.seat for row in rows} != {1, 2}:
        return None
    if any(
        row.judge_model != models[row.seat - 1]
        for row in rows
        if 1 <= row.seat <= len(models)
    ):
        return None
    if len({(row.run_id, row.attempt) for row in rows}) != 1:
        return None
    return collegium_verdict(rows)


def _round_verdict(unit: Unit) -> JudgeVerdict | None:
    """Read only the current round, never historical fallback evidence."""
    return collegium_verdict(current_round(unit))


@dataclass(frozen=True)
class _RepairOutcome:
    unit: Unit | None
    changed: bool = False


def _apply_repair(
    unit: Unit,
    request: JudgeRequest,
    before_target: list[str],
    before_checks: set[str],
    before_state,
    new_target: list[str],
    user: User | None,
) -> _RepairOutcome:
    """Apply a repair only when the request snapshot still owns the unit."""
    with transaction.atomic():
        locked = (
            type(unit)
            .objects.select_for_update()
            .prefetch()
            .prefetch_source()
            .get(pk=unit.pk)
        )
        if (
            compute_target_hash(locked.get_target_plurals())
            != compute_target_hash(before_target)
            or locked.state != before_state
            or compute_context_hash(
                source=locked.source,
                note=locked.source_unit.note,
                glossary_terms=_glossary_pairs(locked),
            )
            != compute_context_hash(
                source=request.source,
                note=request.note,
                glossary_terms=request.glossary_terms,
            )
        ):
            return _RepairOutcome(None)
        # A10: the state is written now — a repaired but not yet
        # re-judged string must not stand in a shippable state.
        locked.translate(user, new_target, STATE_FUZZY)
        locked.invalidate_checks_cache()
        locked.clear_checks_cache()
        locked.run_checks()
        after_checks = _deterministic_checks(locked)
        if not after_checks.issubset(before_checks):
            # Never ship a repair that introduces a new deterministic
            # failure. Restore the exact target/state under the same lock
            # and leave the negative verdict for human review.
            locked.translate(user, before_target, before_state)
            return _RepairOutcome(locked)
    return _RepairOutcome(locked, changed=True)


def _process_round_unit(
    unit: Unit,
    request: JudgeRequest,
    round_state,
    *,
    writable_ids: set[int],
    user: User | None,
    attempt: int,
    attempts: int,
) -> tuple[JudgeVerdict | None, _RepairOutcome]:
    """Project a round and optionally prepare its next repair attempt."""
    current = _refresh_unit(unit)
    if current.state != round_state:
        return None, _RepairOutcome(None)
    current.invalidate_checks_cache()
    current.clear_checks_cache()
    current.run_checks()
    verdict = _round_verdict(current)
    if verdict is None:
        # A changed target/context or an all-unparsed round must not fall
        # back to an older opinion for repair or finalization.
        return None, _RepairOutcome(None)
    if verdict.verdict in _NON_REPAIRABLE_VERDICTS:
        return verdict, _RepairOutcome(current)
    if attempt >= attempts or unit.id not in writable_ids:
        return verdict, _RepairOutcome(current)

    before_target = current.get_target_plurals()
    before_checks = _deterministic_checks(current)
    before_state = current.state
    new_target = repair_target(current, user)
    if new_target is None:
        return verdict, _RepairOutcome(current)
    return verdict, _apply_repair(
        current,
        request,
        before_target,
        before_checks,
        before_state,
        new_target,
        user,
    )


def run_judge_batch(
    units: list[Unit], *, writable_ids: set[int], user: User | None
) -> dict[int, JudgeVerdict]:
    """
    Judge every unit with both seats; repair writable defects.

    Returns the final active verdict per unit id. The repair loop runs
    until JUDGE_MAX_REPAIR_ATTEMPTS is spent; a string that stays
    negative keeps its last verdict and its state-10 hold for the human
    queue (applied by the caller from state_for_verdict).
    """
    if not units:
        return {}
    validate_judge_configuration()
    original_units = {unit.id: unit for unit in units}

    def record_final_snapshot(unit: Unit) -> None:
        original = original_units[unit.id]
        original.target = unit.target
        original.state = unit.state

    run_id = uuid.uuid4()
    # Accounting must be symmetric with machinery, which attributes every
    # paid request to a project (machinery/openai.py:147). All units of a
    # run share one translation, so the slug is read once.
    project = units[0].translation.component.project
    project_slug = project.slug
    # Read once per run: every unit of a run shares one project, and the
    # value goes into the system message of every batch.
    project_context = judge_project_context(project)
    pending = list(units)
    verdicts: dict[int, JudgeVerdict] = {}
    attempts = settings.JUDGE_MAX_REPAIR_ATTEMPTS
    seats = (
        (1, settings.JUDGE_MODEL_SEAT_1),
        (2, settings.JUDGE_MODEL_SEAT_2),
    )

    attempt = 0
    while True:
        # Do not continue with instances that may have been changed while the
        # previous request or repair was in flight.
        pending = [_refresh_unit(unit) for unit in pending]
        # Both seats judge EVERY string unconditionally; there is no if
        # between the two calls (B2': a cascade loses recall).
        round_requests: dict[int, JudgeRequest] = {}
        cached_ids: set[int] = set()
        round_states = {unit.id: unit.state for unit in pending}
        for unit in pending:
            request = build_request(unit)
            round_requests[unit.id] = request
            cached = _cached_verdict(
                unit,
                request,
                (settings.JUDGE_MODEL_SEAT_1, settings.JUDGE_MODEL_SEAT_2),
            )
            if cached is not None:
                cached_ids.add(unit.id)
                verdicts[unit.id] = cached
        for seat, model in seats:
            request_units = [unit for unit in pending if unit.id not in cached_ids]
            if not request_units:
                continue
            requests = [round_requests[unit.id] for unit in request_units]
            results = request_verdicts(
                requests,
                model=model,
                project_slug=project_slug,
                project_context=project_context,
            )
            with transaction.atomic():
                for unit, request, result in zip(
                    request_units, requests, results, strict=True
                ):
                    _write_verdict(unit, request, seat, attempt, run_id, result, model)

        next_pending = []
        for unit in pending:
            verdict, outcome = _process_round_unit(
                unit,
                round_requests[unit.id],
                round_states[unit.id],
                writable_ids=writable_ids,
                user=user,
                attempt=attempt,
                attempts=attempts,
            )
            if outcome.unit is None:
                verdicts.pop(unit.id, None)
                continue
            if verdict is not None:
                verdicts[unit.id] = verdict
            if verdict is not None and not outcome.changed:
                record_final_snapshot(outcome.unit)
            if outcome.changed:
                next_pending.append(outcome.unit)
        pending = next_pending
        if not pending:
            break
        attempt += 1
        if attempt > attempts:
            break
    return verdicts
