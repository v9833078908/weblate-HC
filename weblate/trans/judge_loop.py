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

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction

from weblate.checks.judge import JUDGE_CHECKS
from weblate.glossary.models import get_matched_glossary_prompt_entries
from weblate.machinery.base import MACHINERY_DEFAULT_THRESHOLD, MachineTranslationError
from weblate.machinery.models import MACHINERY
from weblate.trans.forms import configured_routed_engine
from weblate.trans.judge import (
    JUDGE_SEATS,
    JudgeRequest,
    OnBatch,
    request_verdicts,
    validate_judge_configuration,
)
from weblate.trans.machinery import fetch_machinery_matches
from weblate.trans.models.judge import (
    JudgeVerdict,
    collegium_verdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
    current_round,
)
from weblate.utils.state import STATE_FUZZY

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict
    from weblate.trans.models.project import Project
    from weblate.trans.models.unit import Unit

# A round is left alone (no repair attempt) when it did not confirm a
# defect: PASS needs no fix, UNPARSED is a transport failure, not an
# opinion to act on.
_NON_REPAIRABLE_VERDICTS = frozenset(
    {
        JudgeVerdict.Verdict.PASS,
        JudgeVerdict.Verdict.UNPARSED,
    }
)

LOGGER = logging.getLogger(__name__)


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
        glossary_terms=get_matched_glossary_prompt_entries(unit),
        # The judge's own projection is not evidence: a judge-* row is the
        # previous round's opinion, and feeding it back lets a seat cite
        # itself as proof ("the judge-flag check indicates ...").
        failing_checks=sorted(unit.all_checks_names - JUDGE_CHECKS),
        target_plurals=unit.get_target_plurals(),
    )


def _select_repair_texts(
    unit: Unit, plural_candidates: list[list[dict]]
) -> list[str] | None:
    """Pick one usable text per plural form, or None."""
    if len(plural_candidates) != len(unit.get_target_plurals()):
        return None
    texts: list[str] = []
    for candidates in plural_candidates:
        if not candidates:
            return None
        best = max(candidates, key=lambda item: item.get("quality", 0))
        text = best.get("text", "")
        if not isinstance(text, str) or not text:
            return None
        texts.append(text)
    if texts == unit.get_target_plurals():
        return None
    return texts


def _machinery_candidates(
    unit: Unit, result: UnitMemoryResultDict
) -> list[list[dict]] | None:
    """Present one batch result the way translate() presents its own."""
    texts = result.get("translation", ())
    qualities = result.get("quality", ())
    if len(texts) != len(qualities):
        unit.translation.log_error(
            "judge repair: unusable machinery result for unit %d", unit.id
        )
        return None
    return [
        [{"text": text, "quality": quality}]
        for text, quality in zip(texts, qualities, strict=True)
    ]


def repair_targets(units: list[Unit], user: User | None) -> dict[int, list[str]]:
    """
    Return usable repair targets keyed by unit id, writing nothing.

    Judge evidence reaches the repair prompt for free: the caller has already
    run run_checks(), so the round's judge-* Check row exists and
    weblate/machinery/llm.py feeds failing_checks into the prompt of every
    string in the batch. Callers MUST run_checks() first.
    """
    if not units:
        return {}
    translation = units[0].translation
    settings_map = translation.component.project.get_machinery_settings()
    engine_id = configured_routed_engine(settings_map)
    if engine_id is None or engine_id not in MACHINERY:
        return {}
    setting = settings_map[engine_id]
    engine = MACHINERY[engine_id](setting)
    if engine.batch_size == 1:
        return _repair_targets_per_unit(engine, units, user)
    try:
        matches = fetch_machinery_matches(
            units=units,
            user=user,
            services=[engine],
            threshold=MACHINERY_DEFAULT_THRESHOLD,
            log_translation=translation,
        )
    except MachineTranslationError as error:
        translation.log_error("failed judge repair: %s", error)
        return {}
    repairs: dict[int, list[str]] = {}
    for unit in units:
        result = matches.get(unit.id)
        if result is None:
            continue
        candidates = _machinery_candidates(unit, result)
        if candidates is None:
            continue
        texts = _select_repair_texts(unit, candidates)
        if texts is not None:
            repairs[unit.id] = texts
    return repairs


def _repair_targets_per_unit(
    engine: BatchMachineTranslation, units: list[Unit], user: User | None
) -> dict[int, list[str]]:
    repairs: dict[int, list[str]] = {}
    for unit in units:
        try:
            candidates = engine.translate(unit, user)
        except MachineTranslationError as error:
            unit.translation.log_error("failed judge repair: %s", error)
            continue
        texts = _select_repair_texts(unit, candidates)
        if texts is not None:
            repairs[unit.id] = texts
    return repairs


def judge_project_context(project: Project) -> str:
    """
    Return the project's own description of its game, for the prompt.

    Read from the same machinery configuration the translator uses
    (``persona`` and ``style`` of the project's MT engine), so the judge
    and the translator cannot hold different ideas of the game. Nothing
    configured yields an empty string, and the client then falls back to
    a neutral context instead of another project's setting.
    """
    settings_map = project.get_machinery_settings()
    engine_id = configured_routed_engine(settings_map)
    if engine_id is None:
        return ""
    setting = settings_map[engine_id]
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
        target_storage_hash=compute_target_storage_hash(request.target),
        context_hash=compute_context_hash(
            source=request.source,
            note=request.note,
            glossary_terms=request.glossary_terms,
        ),
        run_id=run_id,
    )
    try:
        unit.translation.invalidate_cache()
    except Exception:
        LOGGER.exception("Could not invalidate judge translation statistics")


def _refresh_unit(unit: Unit) -> Unit:
    """Reload a unit with the relations needed by judge requests and checks."""
    return type(unit).objects.filter(pk=unit.pk).prefetch().prefetch_source().get()


def _deterministic_checks(unit: Unit) -> set[str]:
    """Exclude judge projections from the no-regress check snapshot."""
    return {name for name in unit.all_checks_names if not name.startswith("judge-")}


def _has_active_check(unit: Unit, check_id: str) -> bool:
    """Whether a non-dismissed check row is currently on the unit."""
    return any(check.name == check_id for check in unit.active_checks)


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


@dataclass(frozen=True)
class _PreparedRound:
    unit: Unit
    request: JudgeRequest
    verdict: JudgeVerdict
    needs_repair: bool
    before_target: list[str]
    before_checks: set[str]
    before_state: int


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
                glossary_terms=get_matched_glossary_prompt_entries(locked),
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


def _prepare_round_unit(
    unit: Unit,
    request: JudgeRequest,
    round_state,
    *,
    writable_ids: set[int],
    attempt: int,
    attempts: int,
) -> _PreparedRound | None:
    """Project a round and describe its repair inputs without fetching them."""
    current = _refresh_unit(unit)
    if current.state != round_state:
        return None
    current.invalidate_checks_cache()
    current.clear_checks_cache()
    current.run_checks()
    verdict = _round_verdict(current)
    if verdict is None:
        # A changed target/context or an all-unparsed round must not fall
        # back to an older opinion for repair or finalization.
        return None
    needs_repair = (
        verdict.verdict != JudgeVerdict.Verdict.UNPARSED
        and (
            verdict.verdict not in _NON_REPAIRABLE_VERDICTS
            or _has_active_check(current, "max-length")
        )
        and attempt < attempts
        and unit.id in writable_ids
    )
    return _PreparedRound(
        unit=current,
        request=request,
        verdict=verdict,
        needs_repair=needs_repair,
        before_target=current.get_target_plurals(),
        before_checks=_deterministic_checks(current),
        before_state=current.state,
    )


def _persist_verdict_batches(
    request_units: list[Unit],
    *,
    seat: int,
    attempt: int,
    run_id: uuid.UUID,
    model: str,
    on_batch: OnBatch | None,
) -> OnBatch:
    cursor = 0

    def persist(batch_requests, batch_results) -> None:
        nonlocal cursor
        batch_units = request_units[cursor : cursor + len(batch_requests)]
        cursor += len(batch_requests)
        with transaction.atomic():
            for unit, request, result in zip(
                batch_units,
                batch_requests,
                batch_results,
                strict=True,
            ):
                _write_verdict(
                    unit,
                    request,
                    seat,
                    attempt,
                    run_id,
                    result,
                    model,
                )
        if on_batch is not None:
            on_batch(batch_requests, batch_results)

    return persist


def run_judge_batch(
    units: list[Unit],
    *,
    writable_ids: set[int],
    user: User | None,
    on_batch: OnBatch | None = None,
) -> dict[int, JudgeVerdict]:
    """
    Judge every unit with both seats; repair writable defects.

    Returns the final active verdict per unit id. The repair loop runs
    until JUDGE_MAX_REPAIR_ATTEMPTS is spent; a string that stays
    negative keeps its last verdict for the caller to project into the
    appropriate human-review state.
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
    project_slug = units[0].translation.component.project.slug
    # Read once per run: every unit of a run shares one project, and the
    # value goes into the system message of every batch.
    project_context = judge_project_context(units[0].translation.component.project)
    pending = list(units)
    verdicts: dict[int, JudgeVerdict] = {}
    attempts = settings.JUDGE_MAX_REPAIR_ATTEMPTS
    seats = tuple(
        zip(
            JUDGE_SEATS,
            (settings.JUDGE_MODEL_SEAT_1, settings.JUDGE_MODEL_SEAT_2),
            strict=True,
        )
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
        LOGGER.info(
            "judge run %s: %d strings, %d writable, %d cached",
            run_id,
            len(pending),
            len(writable_ids),
            len(cached_ids),
        )
        for seat, model in seats:
            request_units = [unit for unit in pending if unit.id not in cached_ids]
            if not request_units:
                continue
            requests = [round_requests[unit.id] for unit in request_units]
            persist = _persist_verdict_batches(
                request_units,
                seat=seat,
                attempt=attempt,
                run_id=run_id,
                model=model,
                on_batch=on_batch,
            )

            request_verdicts(
                requests,
                model=model,
                project_slug=project_slug,
                project_context=project_context,
                on_batch=persist,
            )
            LOGGER.info(
                "judge run %s: seat %d done, %d strings judged with %s",
                run_id,
                seat,
                len(request_units),
                model,
            )

        prepared = [
            _prepare_round_unit(
                unit,
                round_requests[unit.id],
                round_states[unit.id],
                writable_ids=writable_ids,
                attempt=attempt,
                attempts=attempts,
            )
            for unit in pending
        ]
        repairable_units = [
            item.unit for item in prepared if item is not None and item.needs_repair
        ]
        repairs = repair_targets(repairable_units, user) if repairable_units else {}
        next_pending = []
        for unit, item in zip(pending, prepared, strict=True):
            if item is None:
                verdicts.pop(unit.id, None)
                continue
            verdicts[unit.id] = item.verdict
            new_target = repairs.get(unit.id) if item.needs_repair else None
            if new_target is None:
                record_final_snapshot(item.unit)
                continue
            outcome = _apply_repair(
                item.unit,
                item.request,
                item.before_target,
                item.before_checks,
                item.before_state,
                new_target,
                user,
            )
            if outcome.unit is None:
                verdicts.pop(unit.id, None)
                continue
            if outcome.changed:
                next_pending.append(outcome.unit)
            else:
                record_final_snapshot(outcome.unit)
        pending = next_pending
        if not pending:
            break
        attempt += 1
        if attempt > attempts:
            break
        LOGGER.info("judge run %s: starting repair attempt %d", run_id, attempt)
    return verdicts
