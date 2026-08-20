# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The two-seat collegium and its repair loop.

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
from typing import TYPE_CHECKING

from django.db import transaction

from weblate.machinery.models import MACHINERY
from weblate.trans.forms import AutoForm
from weblate.trans.judge import JudgeRequest, request_verdicts
from weblate.trans.models.judge import (
    JudgeVerdict,
    active_verdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.utils.state import STATE_FUZZY

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.trans.models.unit import Unit


def _glossary_pairs(unit: Unit) -> list[tuple[str, str]]:
    """Source/target text of the glossary terms attached to the unit."""
    from weblate.glossary.models import get_glossary_terms

    return [
        (term.source, term.target)
        for term in get_glossary_terms(unit)
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
    )


def repair_target(unit: Unit, user: User | None) -> list[str] | None:
    """Re-translate the unit through the project MT engine, or None.

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


def _write_verdict(
    unit: Unit, seat: int, attempt: int, run_id: uuid.UUID, result, model: str
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
        target_hash=compute_target_hash(unit.get_target_plurals()),
        context_hash=compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            glossary_terms=_glossary_pairs(unit),
        ),
        run_id=run_id,
    )


def run_judge_batch(
    units: list[Unit], *, writable_ids: set[int], user: User | None
) -> dict[int, JudgeVerdict]:
    """Judge every unit with both seats; repair writable defects.

    Returns the final active verdict per unit id. The repair loop runs
    until JUDGE_MAX_REPAIR_ATTEMPTS is spent; a string that stays
    negative keeps its last verdict and its state-10 hold for the human
    queue (applied by the caller from state_for_verdict).
    """
    from django.conf import settings

    run_id = uuid.uuid4()
    pending = list(units)
    verdicts: dict[int, JudgeVerdict] = {}
    attempts = settings.JUDGE_MAX_REPAIR_ATTEMPTS
    seats = (
        (1, settings.JUDGE_MODEL_SEAT_1),
        (2, settings.JUDGE_MODEL_SEAT_2),
    )

    attempt = 0
    while True:
        # Both seats judge EVERY string unconditionally; there is no if
        # between the two calls (B2': a cascade loses recall).
        for seat, model in seats:
            requests = [build_request(unit) for unit in pending]
            results = request_verdicts(requests, model=model)
            with transaction.atomic():
                for unit, result in zip(pending, results):
                    _write_verdict(unit, seat, attempt, run_id, result, model)

        next_pending = []
        for unit in pending:
            unit.invalidate_checks_cache()
            unit.clear_checks_cache()
            unit.run_checks()
            verdict = active_verdict(unit)
            if verdict is None:
                # An all-unparsed round with no prior verdict: surface it
                # as unparsed (the run's warning counts these), never as
                # a real pass (D5).
                verdict = unit.judge_verdicts.filter(
                    run_id=run_id, attempt=attempt
                ).order_by("seat").first()
            if verdict is not None:
                verdicts[unit.id] = verdict
            if verdict is None or verdict.verdict in (
                JudgeVerdict.Verdict.PASS,
                JudgeVerdict.Verdict.UNPARSED,
            ):
                continue
            if attempt >= attempts:
                continue
            if unit.id not in writable_ids:  # D3/A3: never rewrite a human string
                continue
            new_target = repair_target(unit, user)
            if new_target is None:
                continue
            # A10: the state is written now — a repaired but not yet
            # re-judged string must not stand in a shippable state.
            unit.translate(user, new_target, STATE_FUZZY)
            next_pending.append(unit)
        pending = next_pending
        if not pending:
            break
        attempt += 1
        if attempt > attempts:
            break
    return verdicts
