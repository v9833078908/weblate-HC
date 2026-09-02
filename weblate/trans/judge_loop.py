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
import math
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.translation import gettext

from weblate.checks.judge import JUDGE_CHECKS
from weblate.glossary.models import get_matched_glossary_prompt_entries
from weblate.machinery.base import MACHINERY_DEFAULT_THRESHOLD, MachineTranslationError
from weblate.machinery.models import MACHINERY
from weblate.trans.actions import ActionEvents
from weblate.trans.forms import configured_routed_engine
from weblate.trans.judge import (
    JUDGE_SEATS,
    JudgeError,
    JudgeRequest,
    JudgeResult,
    OnBatch,
    RetryBudget,
    judge_configuration_snapshot,
    request_verdicts,
    resolve_judge_seat_profile,
    validate_judge_configuration,
)
from weblate.trans.machinery import fetch_machinery_matches
from weblate.trans.models.judge import (
    JudgeAdaptiveState,
    JudgeCandidateError,
    JudgeCandidateMetadata,
    JudgeDeferral,
    JudgeRequestAttempt,
    JudgeRun,
    JudgeRunUnit,
    JudgeVerdict,
    collegium_verdict,
    compute_context_hash,
    compute_judge_request_identity,
    compute_target_hash,
    compute_target_storage_hash,
    current_round,
    current_verdict,
    has_complete_current_evidence,
    state_for_verdict,
)
from weblate.utils.state import STATE_FUZZY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from weblate.auth.models import AuthenticatedHttpRequest, User
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict
    from weblate.trans.models.project import Project
    from weblate.trans.models.suggestion import Suggestion
    from weblate.trans.models.unit import Unit

# Verdicts whose round produces a stored producer candidate instead of a
# mutating repair. minor maps to PASS and never has a candidate.
_CANDIDATE_VERDICTS = frozenset(
    {
        JudgeVerdict.Verdict.REJECT,
        JudgeVerdict.Verdict.FLAG,
    }
)
# A run purpose names candidate severities, not verdicts: critical selects
# REJECT and major selects FLAG (models/judge.py severity vocabulary).
_CANDIDATE_VERDICT_BY_SEVERITY = {
    JudgeVerdict.Severity.CRITICAL: JudgeVerdict.Verdict.REJECT,
    JudgeVerdict.Severity.MAJOR: JudgeVerdict.Verdict.FLAG,
}
DEFAULT_CANDIDATE_SEVERITIES: tuple[str, ...] = (
    JudgeVerdict.Severity.CRITICAL,
    JudgeVerdict.Severity.MAJOR,
)

LOGGER = logging.getLogger(__name__)
_DEFERRAL_MAX_ELAPSED_SECONDS = 240
_TOKEN_PRECISION = Decimal("0.000001")


def build_request(unit: Unit) -> JudgeRequest:
    """Collect everything the judge is told about one unit."""
    translation = unit.translation
    return JudgeRequest(
        unit_key=unit.context,
        source=unit.source,
        target=unit.target,
        source_language=translation.component.source_language.code,
        target_language=translation.language.code,
        project_id_snapshot=translation.component.project_id,
        component_id_snapshot=translation.component_id,
        component_slug=translation.component.slug,
        note=unit.source_unit.note,
        explanation=unit.source_unit.explanation,
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
    *,
    seat: int,
    attempt: int,
    run_id: uuid.UUID,
    result,
    profile,
    project_context: str,
    request_round: int = 0,
) -> None:
    target_hash = compute_target_hash(request.target_plurals or [request.target])
    context_hash = compute_context_hash(
        source=request.source,
        note=request.note,
        explanation=request.explanation,
        glossary_terms=request.glossary_terms,
    )
    project_context_hash = compute_target_hash([project_context])
    # Prefer the profile that actually served this result: after a
    # fallback attempt it differs from the requesting seat's bound
    # `profile`, which is always the primary (see `make_seat_job`). Every
    # pre-failover path leaves `result.served_*` blank and behaves exactly
    # as before.
    judge_model = result.served_model or profile.model
    judge_provider = result.served_provider or profile.provider
    profile_fingerprint = (
        result.served_profile_fingerprint or profile.profile_fingerprint
    )
    prompt_schema_version = (
        result.served_prompt_schema_version or profile.prompt_schema_version
    )
    request_identity = compute_judge_request_identity(
        unit_id=unit.id,
        target_hash=target_hash,
        context_hash=context_hash,
        project_context_hash=project_context_hash,
        source_language=request.source_language,
        target_language=request.target_language,
        profile_fingerprint=profile_fingerprint,
        prompt_schema_version=prompt_schema_version,
    )
    storage_hash = compute_target_storage_hash(request.target)
    JudgeVerdict.objects.create(
        unit=unit,
        model_verdict=result.model_verdict,
        max_severity=result.max_severity,
        unparsed=result.unparsed,
        errors=result.errors,
        back_translation=result.back_translation,
        instruction="",
        judge_model=judge_model,
        judge_provider=judge_provider,
        seat=seat,
        attempt=attempt,
        request_round=request_round,
        target_hash=target_hash,
        target_storage_hash=storage_hash,
        context_hash=context_hash,
        run_id=run_id,
        request_attempt_id=result.request_attempt_id,
        request_identity=request_identity,
        project_context_hash=project_context_hash,
        source_language=request.source_language,
        target_language=request.target_language,
        profile_fingerprint=profile_fingerprint,
        prompt_schema_version=prompt_schema_version,
    )
    # A round written before the target_storage_hash backfill (or one whose
    # text was not current at that one-time migration) never got the field
    # populated: the SQL status annotations, which can only match through
    # it, silently disagree with active_round() until the same text is
    # judged again. Self-heal opportunistically: whenever a fresh request
    # shares that historical round's exact target, we have the same raw
    # text in hand and can backfill every sibling missing it for free.
    JudgeVerdict.objects.filter(
        unit=unit, target_hash=target_hash, target_storage_hash__isnull=True
    ).update(target_storage_hash=storage_hash)
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


def _request_identity(
    unit: Unit, request: JudgeRequest, profile, project_context: str
) -> str:
    """Build the shared text-free cache, attempt, and deferral identity."""
    return compute_judge_request_identity(
        unit_id=unit.id,
        target_hash=compute_target_hash(request.target_plurals or [request.target]),
        context_hash=compute_context_hash(
            source=request.source,
            note=request.note,
            explanation=request.explanation,
            glossary_terms=request.glossary_terms,
        ),
        project_context_hash=compute_target_hash([project_context]),
        source_language=request.source_language,
        target_language=request.target_language,
        profile_fingerprint=profile.profile_fingerprint,
        prompt_schema_version=profile.prompt_schema_version,
    )


def _cached_verdict(
    unit: Unit, request: JudgeRequest, profiles: dict[int, object], project_context: str
) -> JudgeVerdict | None:
    """Reuse only complete current parsed evidence for an unchanged request."""
    identities = {
        seat: _request_identity(unit, request, profile, project_context)
        for seat, profile in profiles.items()
    }
    rows = []
    for seat, identity in identities.items():
        row = (
            unit.judge_verdicts.filter(
                request_identity=identity,
                seat=seat,
            )
            .order_by("-timestamp", "-pk")
            .first()
        )
        if row is None or row.unparsed or row.judge_model != profiles[seat].model:
            return None
        rows.append(row)
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
    needs_candidate: bool
    needs_mutating_repair: bool
    before_target: list[str]
    before_checks: set[str]
    before_state: int


@dataclass(frozen=True)
class _SeatJob:
    seat: int
    model: str
    requests: list[JudgeRequest]
    persist: OnBatch
    run: JudgeRun | None
    retry_budget: RetryBudget
    attempt: int
    retry_deadline: float | None


@dataclass
class _BatchReady:
    seat_index: int
    start_offset: int
    end_offset: int
    requests: Sequence[JudgeRequest]
    results: Sequence[JudgeResult]
    acknowledged: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass(frozen=True)
class _SeatDone:
    seat_index: int
    error: BaseException | None = None


class _SeatAbortedError(Exception):
    """Private control flow after another seat or caller failed."""


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
                explanation=locked.source_unit.explanation,
                glossary_terms=get_matched_glossary_prompt_entries(locked),
            )
            != compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
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


def _store_candidate(
    unit: Unit,
    request: JudgeRequest,
    before_target: list[str],
    before_state,
    new_target: list[str],
    verdict: JudgeVerdict,
    engine: str,
    user: User | None,
) -> str:
    """
    Persist a repair candidate as a native Suggestion under the unit lock.

    Mirrors the _apply_repair snapshot check: the candidate is stored only
    when the judged target/state/context still owns the unit. The unit
    target itself is never mutated (invariant 1).

    Returns ``stored``, ``drift`` (the judged snapshot lost the unit, so the
    round's projection can no longer be trusted) or ``refused`` (the snapshot
    still holds but the manager kept no row, because the repair matched the
    live text or was too long). ``refused`` is not drift: the verdict itself
    remains valid and must keep holding the string.
    """
    from weblate.trans.models.suggestion import (  # ruff: ignore[import-outside-top-level]
        Suggestion,
        SuggestionAddResult,
    )

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
                explanation=locked.source_unit.explanation,
                glossary_terms=get_matched_glossary_prompt_entries(locked),
            )
            != compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            )
        ):
            return "drift"
        metadata = {
            "kind": "judge-repair",
            "schema": 1,
            "judge_verdict_id": verdict.pk,
            "judge_run_id": str(verdict.run_id),
            "target_hash": compute_target_hash(locked.get_target_plurals()),
            "context_hash": compute_context_hash(
                source=locked.source,
                note=locked.source_unit.note,
                explanation=locked.source_unit.explanation,
                glossary_terms=get_matched_glossary_prompt_entries(locked),
            ),
            "engine": engine,
        }
        suggestion, result = Suggestion.objects.add(
            locked,
            new_target,
            request=None,
            vote=False,
            user=user,
            raise_exception=False,
            userdetails=metadata,
            change_details={
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
            },
        )
    # DUPLICATE is a real stored candidate for this same verdict, so it
    # counts as success; anything else left no row behind.
    if suggestion is not None and result in {
        SuggestionAddResult.CREATED,
        SuggestionAddResult.DUPLICATE,
    }:
        return "stored"
    return "refused"


def _prepare_round_unit(
    unit: Unit,
    request: JudgeRequest,
    round_state,
    *,
    writable_ids: set[int],
    attempt: int,
    attempts: int,
    candidate_verdicts: frozenset[str] = _CANDIDATE_VERDICTS,
    mutating_repairs: bool = True,
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
    # Precedence: an active max-length check selects the mutating path and
    # suppresses candidate storage for the round (a preview that still
    # overflows is not applicable). Otherwise the run purpose's candidate
    # verdicts select the candidate path regardless of writable state or
    # remaining attempts. A producer re-check turns mutating repairs off
    # entirely, so max-length there neither mutates nor stores.
    max_length_active = _has_active_check(current, "max-length")
    needs_mutating_repair = (
        mutating_repairs
        and verdict.verdict != JudgeVerdict.Verdict.UNPARSED
        and max_length_active
        and attempt < attempts
        and unit.id in writable_ids
    )
    # An active max-length check suppresses candidate storage unconditionally,
    # not merely when the mutating branch happens to be taken. A non-writable
    # unit, an exhausted repair budget, or a producer re-check (which turns
    # mutating repairs off entirely) must all still leave the unit on the
    # deterministic path with no preview stored (invariant 9).
    needs_candidate = (
        verdict.verdict in candidate_verdicts
        and not needs_mutating_repair
        and not max_length_active
    )
    return _PreparedRound(
        unit=current,
        request=request,
        verdict=verdict,
        needs_candidate=needs_candidate,
        needs_mutating_repair=needs_mutating_repair,
        before_target=current.get_target_plurals(),
        before_checks=_deterministic_checks(current),
        before_state=current.state,
    )


def _deferral_values(
    unit: Unit, request: JudgeRequest, profile, project_context: str
) -> dict[str, object]:
    """Return the durable, text-free representation of one seat request."""
    target_hash = compute_target_hash(request.target_plurals or [request.target])
    context_hash = compute_context_hash(
        source=request.source,
        note=request.note,
        explanation=request.explanation,
        glossary_terms=request.glossary_terms,
    )
    project_context_hash = compute_target_hash([project_context])
    return {
        "request_identity": compute_judge_request_identity(
            unit_id=unit.id,
            target_hash=target_hash,
            context_hash=context_hash,
            project_context_hash=project_context_hash,
            source_language=request.source_language,
            target_language=request.target_language,
            profile_fingerprint=profile.profile_fingerprint,
            prompt_schema_version=profile.prompt_schema_version,
        ),
        "target_hash": target_hash,
        "context_hash": context_hash,
        "project_context_hash": project_context_hash,
        "source_language": request.source_language,
        "target_language": request.target_language,
        "profile_fingerprint": profile.profile_fingerprint,
        "prompt_schema_version": profile.prompt_schema_version,
    }


def _deferral_int_setting(name: str, default: int, *, minimum: int = 0) -> int:
    """Return a non-boolean non-negative deferral setting."""
    value = getattr(settings, name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, value)


def _deferral_decimal_setting(name: str, default: float) -> Decimal:
    """Return a non-negative rate without accepting non-finite values."""
    value = getattr(settings, name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Decimal(str(default))
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        return Decimal(str(default))
    return max(Decimal(), result) if result.is_finite() else Decimal(str(default))


def _locked_adaptive_state(profile, now) -> JudgeAdaptiveState:
    """Fetch and lock the shared per-seat adaptive and queue budget state."""
    capacity = _deferral_int_setting(
        "JUDGE_DEFERRAL_TOKEN_BUCKET_CAPACITY", 200, minimum=1
    )
    refill = _deferral_decimal_setting(
        "JUDGE_DEFERRAL_TOKEN_BUCKET_REFILL_PER_SECOND", 0.25
    )
    state, _created = JudgeAdaptiveState.objects.select_for_update().get_or_create(
        endpoint_fingerprint=profile.endpoint_fingerprint,
        model=profile.model,
        seat=profile.seat,
        defaults={
            "batch_budget": profile.batch_size,
            "token_bucket_capacity": capacity,
            "token_bucket_available": Decimal(capacity),
            "token_bucket_refill_per_second": refill,
            "token_bucket_updated_at": now,
        },
    )
    if state.token_bucket_updated_at is None:
        state.token_bucket_available = Decimal(capacity)
    else:
        elapsed = max(0, (now - state.token_bucket_updated_at).total_seconds())
        state.token_bucket_available = min(
            Decimal(capacity),
            (state.token_bucket_available + Decimal(str(elapsed)) * refill).quantize(
                _TOKEN_PRECISION
            ),
        )
    state.token_bucket_capacity = capacity
    state.token_bucket_refill_per_second = refill
    state.token_bucket_updated_at = now
    return state


def _save_adaptive_state(state: JudgeAdaptiveState) -> None:
    state.save(
        update_fields=[
            "failure_streak",
            "last_failure_kind",
            "circuit_state",
            "circuit_opened_at",
            "circuit_open_until",
            "token_bucket_capacity",
            "token_bucket_available",
            "token_bucket_refill_per_second",
            "token_bucket_updated_at",
            "updated_at",
        ]
    )


def _record_deferral_circuit_outcome(profile, result: JudgeResult) -> None:
    """Apply one parsed or terminal queue outcome to the shared circuit."""
    if not settings.JUDGE_DEFERRAL_ENABLED:
        return
    try:
        _update_deferral_circuit(profile, result, timezone.now())
    except Exception:
        LOGGER.exception("Failed to update judge deferral circuit")


_AVAILABILITY_FAILURE_KINDS = frozenset(
    {"transport", "deadline", "http-rate-limit", "http-server", "http-other"}
)


def _update_deferral_circuit(profile, result: JudgeResult, now) -> None:
    """Persist a circuit state transition inside the shared-state lock."""
    with transaction.atomic():
        state = _locked_adaptive_state(profile, now)
        if settings.JUDGE_DEFERRAL_OPERATOR_STOPPED:
            state.circuit_state = JudgeAdaptiveState.CircuitState.OPERATOR_STOPPED
            state.circuit_opened_at = now
            state.circuit_open_until = None
        elif state.circuit_state == JudgeAdaptiveState.CircuitState.OPERATOR_STOPPED:
            state.circuit_state = JudgeAdaptiveState.CircuitState.CLOSED
            state.failure_streak = 0
            state.last_failure_kind = ""
            state.circuit_opened_at = None
            state.circuit_open_until = None
        elif result.unparsed and result.failure_kind in _AVAILABILITY_FAILURE_KINDS:
            # Only an availability signal (transport loss, a request that
            # never finished, or an upstream rate-limit/server error) says
            # anything about endpoint health. A protocol or content defect
            # scoped to one unit (finish-length, invalid-segment, ...) must
            # not trip a circuit shared by every other unit and seat.
            state.failure_streak += 1
            state.last_failure_kind = result.failure_kind or "unknown"
            threshold = _deferral_int_setting(
                "JUDGE_DEFERRAL_CIRCUIT_FAILURE_THRESHOLD", 5, minimum=1
            )
            if (
                state.failure_streak >= threshold
                or state.circuit_state == JudgeAdaptiveState.CircuitState.HALF_OPEN
            ):
                state.circuit_state = JudgeAdaptiveState.CircuitState.OPEN
                state.circuit_opened_at = now
                state.circuit_open_until = now + timedelta(
                    seconds=_deferral_int_setting(
                        "JUDGE_DEFERRAL_CIRCUIT_OPEN_SECONDS", 900, minimum=1
                    )
                )
        elif not result.unparsed:
            state.failure_streak = 0
            state.last_failure_kind = ""
            state.circuit_state = JudgeAdaptiveState.CircuitState.CLOSED
            state.circuit_opened_at = None
            state.circuit_open_until = None
        _save_adaptive_state(state)


def _reserve_deferral_requests(profile, requested_calls: int) -> int:
    """
    Reserve shared queue capacity and return the number of HTTP calls allowed.

    A half-open circuit permits exactly one batched call. Returning a smaller
    number leaves the unreserved leases to be released for a later pass.
    """
    if requested_calls < 1:
        return 0
    try:
        return _reserve_deferral_requests_locked(
            profile, requested_calls, timezone.now()
        )
    except Exception:
        LOGGER.exception("Failed to reserve judge deferral capacity")
        return 0


def _reserve_deferral_requests_locked(profile, requested_calls: int, now) -> int:
    """Reserve tokens under the shared-state lock."""
    with transaction.atomic():
        state = _locked_adaptive_state(profile, now)
        if settings.JUDGE_DEFERRAL_OPERATOR_STOPPED:
            state.circuit_state = JudgeAdaptiveState.CircuitState.OPERATOR_STOPPED
            state.circuit_opened_at = now
            state.circuit_open_until = None
            _save_adaptive_state(state)
            return 0
        if state.circuit_state == JudgeAdaptiveState.CircuitState.OPERATOR_STOPPED:
            state.circuit_state = JudgeAdaptiveState.CircuitState.CLOSED
            state.failure_streak = 0
            state.last_failure_kind = ""
            state.circuit_opened_at = None
            state.circuit_open_until = None
        if state.circuit_state == JudgeAdaptiveState.CircuitState.OPEN:
            if state.circuit_open_until and state.circuit_open_until > now:
                _save_adaptive_state(state)
                return 0
            state.circuit_state = JudgeAdaptiveState.CircuitState.HALF_OPEN
            state.circuit_open_until = now + timedelta(
                seconds=max(300, settings.JUDGE_DEFERRAL_MIN_INTERVAL)
            )
        elif state.circuit_state == JudgeAdaptiveState.CircuitState.HALF_OPEN:
            if state.circuit_open_until and state.circuit_open_until > now:
                _save_adaptive_state(state)
                return 0
            state.circuit_state = JudgeAdaptiveState.CircuitState.OPEN
            state.circuit_opened_at = now
            state.circuit_open_until = now + timedelta(
                seconds=_deferral_int_setting(
                    "JUDGE_DEFERRAL_CIRCUIT_OPEN_SECONDS", 900, minimum=1
                )
            )
            _save_adaptive_state(state)
            return 0
        available = max(0, int(state.token_bucket_available))
        reserved = min(requested_calls, available)
        if state.circuit_state == JudgeAdaptiveState.CircuitState.HALF_OPEN:
            reserved = min(reserved, 1)
        if reserved:
            state.token_bucket_available -= Decimal(reserved)
        _save_adaptive_state(state)
        return reserved


def _sync_deferral(
    unit: Unit,
    request: JudgeRequest,
    *,
    seat: int,
    profile,
    project_context: str,
    result: JudgeResult,
    attempt_started_at: datetime | None = None,
) -> None:
    """Synchronize one seat's bounded-recovery outcome to its durable queue."""
    if not settings.JUDGE_DEFERRAL_ENABLED:
        return
    now = timezone.now()
    values = _deferral_values(unit, request, profile, project_context)
    identity = values["request_identity"]
    stale = JudgeDeferral.objects.filter(unit=unit, seat=seat).exclude(
        request_identity=identity
    )
    if attempt_started_at is not None:
        # A late response from a request that started before this pass
        # must not close a deferral queued by a newer one: only rows the
        # arriving attempt can actually supersede (created before the
        # request started) are stale.
        stale = stale.filter(created_at__lte=attempt_started_at)
    stale.exclude(state=JudgeDeferral.State.CLOSED).update(
        state=JudgeDeferral.State.CLOSED,
        closed_at=now,
        claim_token="",
        claimed_at=None,
        claim_expires_at=None,
    )
    if not result.unparsed:
        JudgeDeferral.objects.filter(
            unit=unit,
            seat=seat,
            request_identity=identity,
        ).exclude(state=JudgeDeferral.State.CLOSED).update(
            state=JudgeDeferral.State.CLOSED,
            closed_at=now,
            claim_token="",
            claimed_at=None,
            claim_expires_at=None,
        )
        return
    defaults = {
        **values,
        "next_attempt_at": now,
    }
    try:
        with transaction.atomic():
            deferral, _created = JudgeDeferral.objects.get_or_create(
                unit=unit,
                seat=seat,
                request_identity=identity,
                defaults=defaults,
            )
    except IntegrityError:
        # The uniqueness constraint is the final arbiter when concurrent
        # producer runs observe the same transport failure.
        deferral = JudgeDeferral.objects.get(
            unit=unit, seat=seat, request_identity=identity
        )

    failures = deferral.consecutive_failures + 1
    terminal = result.failure_kind == "finish-length"
    min_interval = max(1, settings.JUDGE_DEFERRAL_MIN_INTERVAL)
    max_interval = max(min_interval, settings.JUDGE_DEFERRAL_MAX_INTERVAL)
    delay = min(max_interval, min_interval * (2 ** min(failures - 1, 30)))
    JudgeDeferral.objects.filter(pk=deferral.pk).update(
        state=(
            JudgeDeferral.State.CLOSED
            if terminal
            else (
                JudgeDeferral.State.SLOW
                if failures >= settings.JUDGE_DEFERRAL_SLOW_AFTER
                else JudgeDeferral.State.QUEUED
            )
        ),
        consecutive_failures=failures,
        last_failure_kind=result.failure_kind or "unknown",
        last_attempt_at=now,
        next_attempt_at=now + timedelta(seconds=delay),
        closed_at=now if terminal else None,
        claim_token="",
        claimed_at=None,
        claim_expires_at=None,
    )


def _allocate_request_round(run_id: uuid.UUID, run: JudgeRun | None) -> int:
    """Reserve one monotonic request-round coordinate for a JudgeRun."""
    if run is not None:
        with transaction.atomic():
            locked_run = JudgeRun.objects.select_for_update().get(pk=run.pk)
            request_round = locked_run.next_request_round
            locked_run.next_request_round += 1
            locked_run.save(update_fields=["next_request_round"])
            return request_round
    highest = JudgeVerdict.objects.filter(run_id=run_id).aggregate(
        highest=Max("request_round")
    )["highest"]
    return 0 if highest is None else highest + 1


def _persist_verdict_batches(
    request_units: list[Unit],
    *,
    seat: int,
    attempt: int,
    request_round: int,
    run_id: uuid.UUID,
    profile,
    project_context: str,
    on_batch: OnBatch | None,
    attempt_started_at: datetime | None = None,
) -> OnBatch:
    cursor = 0

    def persist(batch_requests, batch_results) -> None:
        nonlocal cursor
        batch_units = request_units[cursor : cursor + len(batch_requests)]
        cursor += len(batch_requests)
        if batch_results:
            _record_deferral_circuit_outcome(
                profile,
                next(
                    (result for result in batch_results if result.unparsed),
                    batch_results[0],
                ),
            )
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
                    seat=seat,
                    attempt=attempt,
                    run_id=run_id,
                    result=result,
                    profile=profile,
                    project_context=project_context,
                    request_round=request_round,
                )
                _sync_deferral(
                    unit,
                    request,
                    seat=seat,
                    profile=profile,
                    project_context=project_context,
                    result=result,
                    attempt_started_at=attempt_started_at,
                )
        if on_batch is not None:
            on_batch(batch_requests, batch_results)

    return persist


def _log_seat_done(run_id: uuid.UUID, job: _SeatJob) -> None:
    LOGGER.info(
        "judge run %s: seat %d done, %d strings judged with %s",
        run_id,
        job.seat,
        len(job.requests),
        job.model,
    )


def _run_seats(  # ruff: ignore[complex-structure]
    seat_jobs: list[_SeatJob],
    *,
    project_slug: str,
    project_context: str,
    run_id: uuid.UUID,
) -> None:
    if not seat_jobs:
        return
    if any(job.requests != seat_jobs[0].requests for job in seat_jobs[1:]):
        msg = "judge seats must receive the same ordered requests"
        raise ValueError(msg)

    events: queue.Queue[_BatchReady | _SeatDone] = queue.Queue()

    def judge(index: int, job: _SeatJob) -> None:
        error: BaseException | None = None
        offset = 0

        def publish(
            batch_requests: Sequence[JudgeRequest],
            batch_results: Sequence[JudgeResult],
        ) -> None:
            nonlocal offset
            event = _BatchReady(
                seat_index=index,
                start_offset=offset,
                end_offset=offset + len(batch_requests),
                requests=tuple(batch_requests),
                results=tuple(batch_results),
            )
            offset = event.end_offset
            events.put(event)
            event.acknowledged.wait()
            if event.error is not None:
                raise _SeatAbortedError from event.error

        try:
            request_verdicts(
                job.requests,
                model=job.model,
                project_slug=project_slug,
                project_context=project_context,
                on_batch=publish,
                seat=job.seat,
                run=job.run,
                persist_attempts=True,
                retry_budget=job.retry_budget,
                adaptive=True,
                attempt=job.attempt,
                retry_deadline=job.retry_deadline,
            )
        except BaseException as caught:
            error = caught
        try:
            connections.close_all()
        except BaseException as caught:
            if error is None:
                error = caught
        finally:
            events.put(_SeatDone(index, error))
        if error is not None:
            raise error

    def release(
        batch_events: list[_BatchReady], error: BaseException | None = None
    ) -> None:
        for batch_event in batch_events:
            batch_event.error = error
            batch_event.acknowledged.set()

    fatal_error: BaseException | None = None
    pending: list[_BatchReady] = []
    coverage = dict.fromkeys(range(len(seat_jobs)), 0)
    successful: list[int] = []
    completed: set[int] = set()

    def abort_pending() -> None:
        if fatal_error is not None:
            release(pending, fatal_error)
            pending.clear()

    def release_covered() -> None:
        for event in tuple(pending):
            if all(
                index == event.seat_index
                or index in completed
                or coverage[index] >= event.end_offset
                for index in coverage
            ):
                pending.remove(event)
                release([event])

    with ThreadPoolExecutor(
        max_workers=len(seat_jobs),
        thread_name_prefix="judge-seat",
    ) as pool:
        futures = [
            pool.submit(judge, index, job) for index, job in enumerate(seat_jobs)
        ]
        open_seats = len(seat_jobs)
        while open_seats:
            event = events.get()
            if isinstance(event, _SeatDone):
                open_seats -= 1
                if (
                    event.error is not None
                    and not isinstance(event.error, _SeatAbortedError)
                    and fatal_error is None
                ):
                    fatal_error = event.error
                if event.error is None:
                    successful.append(event.seat_index)
                    completed.add(event.seat_index)
                    coverage[event.seat_index] = len(
                        seat_jobs[event.seat_index].requests
                    )
                if fatal_error is not None:
                    abort_pending()
                else:
                    release_covered()
                continue

            try:
                seat_jobs[event.seat_index].persist(event.requests, event.results)
            except BaseException as error:
                if fatal_error is None:
                    fatal_error = error
            else:
                coverage[event.seat_index] = event.end_offset
            pending.append(event)
            if fatal_error is not None:
                abort_pending()
            else:
                release_covered()

        for future in futures:
            try:
                future.result()
            except _SeatAbortedError:
                pass
            except BaseException as error:
                if fatal_error is None:
                    fatal_error = error

    for index in successful:
        _log_seat_done(run_id, seat_jobs[index])
    if fatal_error is not None:
        raise fatal_error


class JudgeBatchResult(dict[int, JudgeVerdict]):  # ruff: ignore[subclass-builtin]
    """Final verdicts plus producer-run participation metadata."""

    def __init__(self) -> None:
        super().__init__()
        self.cached_unit_ids: set[int] = set()
        self.initial_severity: dict[int, str] = {}
        self.repair_status: dict[int, str] = {}
        self.attempt_counts: dict[int, int] = {}


def run_judge_batch(  # ruff: ignore[complex-structure, too-many-locals, too-many-statements]
    units: list[Unit],
    *,
    writable_ids: set[int],
    user: User | None,
    on_batch: OnBatch | None = None,
    run: JudgeRun | None = None,
    seats: tuple[int, ...] | None = None,
    use_cache: bool = True,
    retry_deadline: float | None = None,
    candidate_severities: tuple[str, ...] = DEFAULT_CANDIDATE_SEVERITIES,
    mutating_repairs: bool = True,
) -> JudgeBatchResult:
    """
    Judge every unit with both seats; repair writable defects.

    ``JudgeVerdict.run_id`` remains a per-invocation model-call identity.
    ``run`` exists only to keep the producer-run boundary explicit to callers.
    ``retry_deadline`` (monotonic seconds) bounds how long an in-run retry
    may sleep, so a drain pass can never sleep past its deferral lease.
    ``candidate_severities`` names the severities whose verdict stores a
    producer candidate; a producer one-unit re-check narrows it to critical
    and turns ``mutating_repairs`` off, which makes the run evidence-only.
    """
    candidate_verdicts = frozenset(
        _CANDIDATE_VERDICT_BY_SEVERITY[severity]
        for severity in candidate_severities
        if severity in _CANDIDATE_VERDICT_BY_SEVERITY
    )
    if not units:
        return JudgeBatchResult()
    validate_judge_configuration()
    profiles = {seat: resolve_judge_seat_profile(seat) for seat in JUDGE_SEATS}
    selected_seats = tuple(JUDGE_SEATS if seats is None else seats)
    if (
        not selected_seats
        or len(set(selected_seats)) != len(selected_seats)
        or any(seat not in profiles for seat in selected_seats)
    ):
        msg = "Judge seats must be a non-empty subset of configured seats"
        raise ValueError(msg)
    original_units = {unit.id: unit for unit in units}

    def record_final_snapshot(unit: Unit) -> None:
        original = original_units[unit.id]
        original.target = unit.target
        original.state = unit.state

    def make_seat_job(
        seat_units: list[Unit], seat: int, repair_attempt: int, request_round: int
    ) -> _SeatJob:
        profile = profiles[seat]
        started_at = timezone.now()
        return _SeatJob(
            seat=seat,
            model=profile.model,
            requests=[round_requests[unit.id] for unit in seat_units],
            persist=_persist_verdict_batches(
                seat_units,
                seat=seat,
                attempt=repair_attempt,
                run_id=run_id,
                request_round=request_round,
                profile=profile,
                project_context=project_context,
                on_batch=on_batch,
                attempt_started_at=started_at,
            ),
            run=run,
            retry_budget=retry_budget,
            attempt=repair_attempt,
            retry_deadline=retry_deadline,
        )

    run_id = run.id if run is not None else uuid.uuid4()
    project_slug = units[0].translation.component.project.slug
    project_context = judge_project_context(units[0].translation.component.project)
    pending = list(units)
    verdicts = JudgeBatchResult()
    attempts = settings.JUDGE_MAX_REPAIR_ATTEMPTS
    unparsed_rounds = _deferral_int_setting(
        "JUDGE_MAX_UNPARSED_RETRY_ROUNDS", 1, minimum=0
    )
    initial_paid_calls = sum(
        math.ceil(len(pending) / profiles[seat].batch_size) for seat in selected_seats
    )
    retry_ratio = settings.JUDGE_RETRY_BUDGET_RATIO
    retry_budget = RetryBudget(
        maximum=math.ceil(initial_paid_calls * retry_ratio)
        if isinstance(retry_ratio, (int, float)) and retry_ratio >= 0
        else 0
    )

    attempt = 0
    while True:
        pending = [_refresh_unit(unit) for unit in pending]
        round_requests: dict[int, JudgeRequest] = {}
        cached_ids: set[int] = set()
        round_states = {unit.id: unit.state for unit in pending}
        for unit in pending:
            request = build_request(unit)
            round_requests[unit.id] = request
            cached = (
                _cached_verdict(unit, request, profiles, project_context)
                if use_cache and set(selected_seats) == set(JUDGE_SEATS)
                else None
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
        request_units = [unit for unit in pending if unit.id not in cached_ids]
        if request_units:
            request_round = _allocate_request_round(run_id, run)
            _run_seats(
                [
                    make_seat_job(request_units, seat, attempt, request_round)
                    for seat in selected_seats
                ],
                project_slug=project_slug,
                project_context=project_context,
                run_id=run_id,
            )
        if unparsed_rounds and request_units:
            # Retry only a round in which every selected seat failed. Use
            # its persisted rows directly: reader-side per-seat joining may
            # legitimately retain an older opinion after a transport loss,
            # but that does not make this invocation's all-dead round parsed.
            retry_round = 0
            last_request_round = request_round
            while retry_round < unparsed_rounds:
                rows_by_unit: dict[int, list[JudgeVerdict]] = {}
                for row in JudgeVerdict.objects.filter(
                    unit_id__in=[unit.id for unit in request_units],
                    run_id=run_id,
                    attempt=attempt,
                    request_round=last_request_round,
                    seat__in=selected_seats,
                ):
                    rows_by_unit.setdefault(row.unit_id, []).append(row)
                unparsed_ids = {
                    unit_id
                    for unit_id, rows in rows_by_unit.items()
                    if len({row.seat for row in rows}) == len(selected_seats)
                    and all(row.unparsed for row in rows)
                }
                if not unparsed_ids:
                    break
                retry_round += 1
                last_request_round = _allocate_request_round(run_id, run)
                LOGGER.info(
                    "judge run %s: unparsed retry round %d for %d strings",
                    run_id,
                    retry_round,
                    len(unparsed_ids),
                )
                retry_units = [unit for unit in pending if unit.id in unparsed_ids]
                if retry_units:
                    _run_seats(
                        [
                            make_seat_job(
                                retry_units,
                                seat,
                                attempt,
                                last_request_round,
                            )
                            for seat in selected_seats
                        ],
                        project_slug=project_slug,
                        project_context=project_context,
                        run_id=run_id,
                    )
        prepared = [
            _prepare_round_unit(
                unit,
                round_requests[unit.id],
                round_states[unit.id],
                writable_ids=(
                    writable_ids if set(selected_seats) == set(JUDGE_SEATS) else set()
                ),
                attempt=attempt,
                attempts=attempts,
                candidate_verdicts=candidate_verdicts,
                mutating_repairs=mutating_repairs,
            )
            for unit in pending
        ]
        # A candidate that is still active for this exact verdict is reused:
        # regenerating pays a second repair MT for an unchanged string and
        # would replace a preview the producer may already be reading.
        reused_candidate_ids = {
            item.unit.id
            for item in prepared
            if item is not None
            and item.needs_candidate
            and active_judge_candidate(item.unit, item.verdict) is not None
        }
        repairable_units = [
            item.unit
            for item in prepared
            if item is not None
            and (
                item.needs_mutating_repair
                or (item.needs_candidate and item.unit.id not in reused_candidate_ids)
            )
        ]
        repairs = repair_targets(repairable_units, user) if repairable_units else {}
        candidate_engine = (
            configured_routed_engine(
                units[0].translation.component.project.get_machinery_settings()
            )
            if any(
                item is not None
                and item.needs_candidate
                and item.unit.id not in reused_candidate_ids
                for item in prepared
            )
            else None
        )
        next_pending = []
        for unit, item in zip(pending, prepared, strict=True):
            if item is None:
                verdicts.pop(unit.id, None)
                verdicts.cached_unit_ids.discard(unit.id)
                continue
            verdicts[unit.id] = item.verdict
            verdicts.initial_severity.setdefault(unit.id, item.verdict.max_severity)
            verdicts.attempt_counts[unit.id] = attempt + 1
            wants_repair = item.needs_candidate or item.needs_mutating_repair
            if item.needs_candidate and unit.id in reused_candidate_ids:
                verdicts.repair_status[unit.id] = "candidate-stored"
                if unit.id in cached_ids:
                    verdicts.cached_unit_ids.add(unit.id)
                else:
                    verdicts.cached_unit_ids.discard(unit.id)
                record_final_snapshot(item.unit)
                continue
            new_target = repairs.get(unit.id) if wants_repair else None
            if new_target is None:
                if wants_repair:
                    verdicts.repair_status[unit.id] = "no-candidate"
                if unit.id in cached_ids:
                    verdicts.cached_unit_ids.add(unit.id)
                else:
                    verdicts.cached_unit_ids.discard(unit.id)
                record_final_snapshot(item.unit)
                continue
            if item.needs_candidate:
                if candidate_engine is None:
                    verdicts.repair_status[unit.id] = "no-candidate"
                    record_final_snapshot(item.unit)
                    continue
                stored = _store_candidate(
                    item.unit,
                    item.request,
                    item.before_target,
                    item.before_state,
                    new_target,
                    item.verdict,
                    candidate_engine,
                    user,
                )
                if stored == "drift":
                    # The judged snapshot no longer owns the unit: discard
                    # the output and stop trusting the round's projection.
                    verdicts.repair_status[unit.id] = "no-candidate"
                    verdicts.pop(unit.id, None)
                    verdicts.cached_unit_ids.discard(unit.id)
                    continue
                if stored == "refused":
                    # Nothing previewable came back, but the verdict still
                    # owns the unit and must keep holding it.
                    verdicts.repair_status[unit.id] = "no-candidate"
                    verdicts.cached_unit_ids.discard(unit.id)
                    record_final_snapshot(item.unit)
                    continue
                verdicts.repair_status[unit.id] = "candidate-stored"
                verdicts.cached_unit_ids.discard(unit.id)
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
                verdicts.cached_unit_ids.discard(unit.id)
                continue
            if outcome.changed:
                verdicts.repair_status[unit.id] = "applied"
                verdicts.cached_unit_ids.discard(unit.id)
                next_pending.append(outcome.unit)
            else:
                verdicts.repair_status[unit.id] = "rolled-back"
                verdicts.cached_unit_ids.discard(unit.id)
                record_final_snapshot(outcome.unit)
        pending = next_pending
        if not pending:
            break
        attempt += 1
        if attempt > attempts:
            break
        LOGGER.info("judge run %s: starting repair attempt %d", run_id, attempt)
    return verdicts


def _claim_judge_deferrals() -> tuple[str, list[JudgeDeferral]]:
    """Claim a bounded set of due records without holding locks during HTTP."""
    now = timezone.now()
    limit = max(1, settings.JUDGE_DEFERRAL_MAX_UNITS_PER_PASS)
    token = uuid.uuid4().hex
    lease = timedelta(seconds=max(300, settings.JUDGE_DEFERRAL_MIN_INTERVAL))
    with transaction.atomic():
        candidates = list(
            JudgeDeferral.objects.select_for_update(skip_locked=True)
            .filter(state__in=(JudgeDeferral.State.QUEUED, JudgeDeferral.State.SLOW))
            .filter(next_attempt_at__lte=now)
            .filter(Q(claim_expires_at__isnull=True) | Q(claim_expires_at__lte=now))
            .order_by("next_attempt_at", "pk")[: limit * len(JUDGE_SEATS)]
        )
        unit_ids: set[int] = set()
        claimed: list[JudgeDeferral] = []
        for deferral in candidates:
            if deferral.unit_id not in unit_ids and len(unit_ids) >= limit:
                continue
            unit_ids.add(deferral.unit_id)
            deferral.claim_token = token
            deferral.claimed_at = now
            deferral.claim_expires_at = now + lease
            deferral.save(
                update_fields=("claim_token", "claimed_at", "claim_expires_at")
            )
            claimed.append(deferral)
    return token, claimed


def _release_judge_deferrals(token: str) -> None:
    """Release only our still-owned leases after an aborted drain pass."""
    JudgeDeferral.objects.filter(
        claim_token=token,
        state__in=(JudgeDeferral.State.QUEUED, JudgeDeferral.State.SLOW),
    ).update(claim_token="", claimed_at=None, claim_expires_at=None)


_DRAIN_SEVERITY_OUTCOMES = {
    JudgeVerdict.Severity.NONE: JudgeRunUnit.Outcome.PASSED,
    JudgeVerdict.Severity.MINOR: JudgeRunUnit.Outcome.MINOR,
    JudgeVerdict.Severity.MAJOR: JudgeRunUnit.Outcome.MAJOR,
    JudgeVerdict.Severity.CRITICAL: JudgeRunUnit.Outcome.CRITICAL,
}


def _close_deferrals_for_disabled_judge() -> None:
    """
    Close every pending deferral when the judge scope is structurally invalid.

    A missing model, key, or ``JUDGE_ENABLED=False`` cannot self-repair: an
    ever-growing backoff queue for work that can never succeed again is
    worse than a clean, auditable close an operator can see and reopen by
    fixing the site-wide configuration (a fresh deferral is created the
    next time a live judge run hits the same failure).
    """
    JudgeDeferral.objects.filter(
        state__in=(JudgeDeferral.State.QUEUED, JudgeDeferral.State.SLOW)
    ).update(
        state=JudgeDeferral.State.CLOSED,
        closed_at=timezone.now(),
        last_failure_kind=JudgeRequestAttempt.FailureKind.HTTP_AUTH,
        claim_token="",
        claimed_at=None,
        claim_expires_at=None,
    )


def _finalize_drain_run(
    run: JudgeRun,
    units: Sequence[Unit],
    before_snapshots: dict[int, tuple[list[str], int]],
) -> None:
    """
    Audit every unit this drain pass touched and project a fresh verdict.

    Runs under the same per-unit lock a live judge run projects through:
    only ``state`` may move, driven by the current-round collegium verdict,
    and only when the unit is still exactly as it was when this pass began.
    Target text is never written here.
    """
    for unit in units:
        with transaction.atomic():
            locked = (
                type(unit)
                .objects.select_for_update()
                .prefetch()
                .prefetch_source()
                .get(pk=unit.pk)
            )
            before_target, before_state = before_snapshots[unit.id]
            stale_conflict = (
                locked.get_target_plurals() != before_target
                or locked.state != before_state
            )
            verdict = None if stale_conflict else current_verdict(locked)
            still_open = (
                not stale_conflict
                and JudgeDeferral.objects.filter(
                    unit_id=locked.pk,
                    state__in=(JudgeDeferral.State.QUEUED, JudgeDeferral.State.SLOW),
                ).exists()
            )
            if stale_conflict:
                outcome = JudgeRunUnit.Outcome.STALE_CONFLICT
            elif verdict is None or verdict.unparsed:
                outcome = (
                    JudgeRunUnit.Outcome.DEFERRED
                    if still_open
                    else JudgeRunUnit.Outcome.UNPARSED
                )
            else:
                outcome = _DRAIN_SEVERITY_OUTCOMES[verdict.max_severity]
                state = state_for_verdict(
                    verdict.verdict,
                    enable_review=locked.translation.enable_review,
                    may_approve=(
                        settings.JUDGE_MAY_APPROVE
                        and has_complete_current_evidence(locked, seats=JUDGE_SEATS)
                    ),
                )
                if verdict.verdict == JudgeVerdict.Verdict.PASS and any(
                    check.name == "max-length" for check in locked.active_checks
                ):
                    # A repair-exhausted over-budget candidate must not
                    # ship just because the judge approved its content.
                    state = STATE_FUZZY
                if state is not None and locked.state != state:
                    locked.translate(None, locked.get_target_plurals(), state)
            context_hash = (
                verdict.context_hash
                if verdict is not None
                else compute_context_hash(
                    source=locked.source,
                    note=locked.source_unit.note,
                    explanation=locked.source_unit.explanation,
                    glossary_terms=get_matched_glossary_prompt_entries(locked),
                )
            )
            JudgeRunUnit.objects.update_or_create(
                run=run,
                unit_id_snapshot=locked.pk,
                defaults={
                    "unit": locked,
                    "translation_id": locked.translation_id,
                    "component_id": locked.translation.component_id,
                    "project_id": locked.translation.component.project_id,
                    "input_target": before_target,
                    "input_target_hash": compute_target_hash(before_target),
                    "context_hash": context_hash,
                    "verdict": verdict,
                    "outcome": outcome,
                    "initial_severity": verdict.max_severity if verdict else "",
                    "final_severity": verdict.max_severity if verdict else "",
                    "before_target": before_target,
                    "after_target": locked.get_target_plurals(),
                    "cached": False,
                    "projection_succeeded": not stale_conflict,
                },
            )


def _select_drain_requests(
    token: str,
    profiles: dict[int, object],
    deferrals: list[JudgeDeferral],
) -> tuple[list[Unit], dict[int, list[Unit]]]:
    """
    Resolve one translation's claimed deferrals into per-seat requests.

    A deferral whose identity no longer matches the unit's current request
    is closed outright; one whose lease cannot cover a full request
    deadline starting now is left alone for `_release_judge_deferrals` to
    hand back to a later pass.
    """
    unit_ids = {deferral.unit_id for deferral in deferrals}
    units = list(
        type(deferrals[0].unit)
        .objects.filter(pk__in=unit_ids)
        .prefetch()
        .prefetch_source()
        .order_by("pk")
    )
    project = units[0].translation.component.project if units else None
    if project is None:
        return units, {}
    project_context = judge_project_context(project)
    requested_by_seat: dict[int, list[Unit]] = {}
    for unit in units:
        request = build_request(unit)
        for deferral in (item for item in deferrals if item.unit_id == unit.id):
            profile = profiles.get(deferral.seat)
            if profile is None or (
                _request_identity(unit, request, profile, project_context)
                != deferral.request_identity
            ):
                JudgeDeferral.objects.filter(pk=deferral.pk, claim_token=token).update(
                    state=JudgeDeferral.State.CLOSED,
                    closed_at=timezone.now(),
                    claim_token="",
                    claimed_at=None,
                    claim_expires_at=None,
                )
                continue
            margin = timedelta(seconds=profile.request_deadline)
            if not JudgeDeferral.objects.filter(
                pk=deferral.pk,
                claim_token=token,
                claim_expires_at__gt=timezone.now() + margin,
            ).exists():
                # A slow pass can outlive its lease, or the lease cannot
                # cover a full request deadline for a call starting now.
                # Leave it for `_release_judge_deferrals` so a later pass
                # can claim it, rather than starting a call another worker
                # may duplicate concurrently.
                continue
            requested_by_seat.setdefault(deferral.seat, []).append(unit)
    return units, requested_by_seat


def _drain_seat(
    run: JudgeRun,
    profiles: dict[int, object],
    claimed: list[JudgeDeferral],
    started: float,
    seat: int,
    seat_units: list[Unit],
) -> int:
    """Reserve capacity and judge one seat's due units within a drain pass."""
    profile = profiles[seat]
    requested_calls = math.ceil(len(seat_units) / profile.batch_size)
    reserved_calls = _reserve_deferral_requests(profile, requested_calls)
    if not reserved_calls:
        return 0
    seat_units = seat_units[: reserved_calls * profile.batch_size]
    # Bound in-run retry sleeps by the earliest claimed lease: sleeping past
    # it would let another worker reclaim and pay for the same units while
    # this pass still holds them.
    earliest_expiry = min(
        (
            deferral.claim_expires_at
            for deferral in claimed
            if deferral.seat == seat
            and deferral.unit_id in {unit.id for unit in seat_units}
            and deferral.claim_expires_at is not None
        ),
        default=None,
    )
    lease_deadline = None
    if earliest_expiry is not None:
        # Reserve the full request deadline for the retried call itself: a
        # retry whose request could still run when the lease expires must
        # not start.
        margin = profile.request_deadline
        remaining = (earliest_expiry - timezone.now()).total_seconds()
        lease_deadline = started + max(0.0, remaining - margin)
    # `run_judge_batch` persists and synchronizes the claimed rows. Empty
    # writable IDs and an empty candidate severity set make this retry
    # strictly read-only: a drain never mutates a target, and it never pays
    # a repair MT to store a producer-facing candidate either.
    run_judge_batch(
        seat_units,
        writable_ids=set(),
        user=None,
        run=run,
        seats=(seat,),
        use_cache=False,
        retry_deadline=lease_deadline,
        candidate_severities=(),
    )
    return len(seat_units)


def _run_drain_translation(
    profiles: dict[int, object],
    claimed: list[JudgeDeferral],
    started: float,
    units: list[Unit],
    requested_by_seat: dict[int, list[Unit]],
) -> int:
    """Judge one translation's due seats and audit/project the outcome."""
    translation = units[0].translation
    before_snapshots = {
        unit.id: (unit.get_target_plurals(), unit.state) for unit in units
    }
    touched_unit_ids = {
        unit.id for seat_units in requested_by_seat.values() for unit in seat_units
    }
    run = JudgeRun.objects.create(
        actor=None,
        started=timezone.now(),
        status=JudgeRun.Status.RUNNING,
        scope_type=JudgeRun.ScopeType.TRANSLATION,
        scope_id=str(translation.pk),
        scope_label=str(translation),
        scope_path=translation.get_absolute_url(),
        requested_mode="drain",
        cap=len(touched_unit_ids),
    )
    processed = 0
    try:
        for seat, seat_units in requested_by_seat.items():
            if time.monotonic() - started >= _DEFERRAL_MAX_ELAPSED_SECONDS:
                break
            processed += _drain_seat(run, profiles, claimed, started, seat, seat_units)
        touched_units = [unit for unit in units if unit.id in touched_unit_ids]
        _finalize_drain_run(run, touched_units, before_snapshots)
    except Exception:
        JudgeRun.objects.filter(pk=run.pk).update(
            status=JudgeRun.Status.FAILED,
            failure="Deferred retry drain pass failed.",
            finished=timezone.now(),
        )
        raise
    else:
        JudgeRun.objects.filter(pk=run.pk).update(
            status=JudgeRun.Status.COMPLETED, finished=timezone.now()
        )
    return processed


def drain_judge_deferrals() -> int:
    """
    Rejudge due deferred seats without ever changing a translation.

    Claims are made before calling a paid endpoint and released only by their
    opaque token.  A changed request identity is closed rather than retried.
    """
    if not settings.JUDGE_DEFERRAL_ENABLED:
        return 0
    try:
        validate_judge_configuration()
        profiles = {seat: resolve_judge_seat_profile(seat) for seat in JUDGE_SEATS}
    except JudgeError:
        # A structural misconfiguration (disabled, missing key/model) can
        # never self-repair: close the queue rather than let it grow
        # forever. A transient/unexpected failure below is recoverable and
        # must leave every deferral queued for the next pass.
        LOGGER.warning("judge deferral drain: judge scope disabled, closing queue")
        _close_deferrals_for_disabled_judge()
        return 0
    except Exception:
        LOGGER.exception("judge deferral drain skipped: recoverable failure")
        return 0
    token, claimed = _claim_judge_deferrals()
    if not claimed:
        return 0
    started = time.monotonic()
    processed = 0
    try:
        by_translation: dict[int, list[JudgeDeferral]] = {}
        for deferral in claimed:
            by_translation.setdefault(deferral.unit.translation_id, []).append(deferral)
        for deferrals in by_translation.values():
            if time.monotonic() - started >= _DEFERRAL_MAX_ELAPSED_SECONDS:
                break
            units, requested_by_seat = _select_drain_requests(
                token, profiles, deferrals
            )
            if not requested_by_seat:
                continue
            processed += _run_drain_translation(
                profiles, claimed, started, units, requested_by_seat
            )
    finally:
        _release_judge_deferrals(token)
    return processed


# --- Producer single-unit flows: re-check and candidate generation ---------

# A generation that never completed (a crashed worker) unlocks after this
# TTL; a healthy task clears the key in `finally`.
GENERATION_LOCK_TTL_SECONDS = 900


def recheck_query(unit_id: int) -> str:
    """Return the exact-one-string query a producer re-check run carries."""
    return f"id:{unit_id}"


def active_recheck_run(unit: Unit) -> JudgeRun | None:
    """Return the queued or running re-check for this unit, if in flight."""
    return (
        JudgeRun.objects.filter(
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(unit.translation_id),
            requested_mode="recheck",
            requested_query=recheck_query(unit.pk),
            status__in=[JudgeRun.Status.QUEUED, JudgeRun.Status.RUNNING],
        )
        .order_by("-created")
        .first()
    )


def _unit_context_hash(unit: Unit) -> str:
    return compute_context_hash(
        source=unit.source,
        note=unit.source_unit.note,
        explanation=unit.source_unit.explanation,
        glossary_terms=get_matched_glossary_prompt_entries(unit),
    )


def active_judge_candidate(unit: Unit, verdict: JudgeVerdict | None):
    """
    Return the active stored candidate for a verdict, or None.

    Active means: the verdict is a current unresolved REJECT/FLAG, and the
    candidate's own target/context hashes still match the unit (invariant 2).
    Malformed metadata is never a candidate; an identical-text human
    suggestion is not reclassified either.
    """
    from weblate.trans.models.suggestion import (  # ruff: ignore[import-outside-top-level]
        Suggestion,
    )

    if (
        verdict is None
        or verdict.resolution
        or verdict.verdict not in _CANDIDATE_VERDICTS
    ):
        return None
    target_hash = compute_target_hash(unit.get_target_plurals())
    context_hash = _unit_context_hash(unit)
    for suggestion in Suggestion.objects.filter(unit=unit).order_by("-timestamp"):
        try:
            metadata = JudgeCandidateMetadata.parse(suggestion.userdetails)
        except JudgeCandidateError:
            continue
        if metadata is None:
            continue
        if (
            metadata.verdict_id == verdict.pk
            and metadata.run_id == verdict.run_id
            and metadata.target_hash == target_hash
            and metadata.context_hash == context_hash
        ):
            return suggestion
    return None


def queue_judge_recheck(unit: Unit, actor: User) -> tuple[JudgeRun, bool]:
    """
    Reuse or create the one queued/running re-check run for this unit.

    Returns the run and whether it was newly created; the view needs the
    distinction for its message while both callers must never dispatch a
    second paid run. Under the Unit lock so two rapid POSTs cannot queue
    two runs (invariant 8). The task is dispatched on commit; a broker
    failure marks the run FAILED rather than leaving it queued forever.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit as UnitModel

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import auto_translate

    query = recheck_query(unit.pk)
    translation = unit.translation
    with transaction.atomic():
        UnitModel.objects.select_for_update().get(pk=unit.pk)
        existing = active_recheck_run(unit)
        if existing is not None:
            return existing, False
        run = JudgeRun.objects.create(
            actor=actor,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(translation.pk),
            scope_label=str(translation),
            scope_path=translation.get_absolute_url(),
            requested_query=query,
            requested_mode="recheck",
            cap=1,
            status=JudgeRun.Status.QUEUED,
            configuration_snapshot=judge_configuration_snapshot(),
        )

    def dispatch() -> None:
        try:
            task = auto_translate.delay(
                user_id=actor.pk,
                mode="judge",
                q=query,
                auto_source="mt",
                source_component_id=None,
                engines=[],
                threshold=MACHINERY_DEFAULT_THRESHOLD,
                translation_id=translation.pk,
                unit_ids=[unit.pk],
                judge_run_id=str(run.pk),
                judge_pretranslate=False,
                judge_mutating_repairs=False,
                judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
            )
        except Exception:
            LOGGER.exception("Failed to dispatch a judge re-check run")
            JudgeRun.objects.filter(pk=run.pk).update(
                status=JudgeRun.Status.FAILED,
                finished=timezone.now(),
                failure="The re-check could not be queued for execution.",
            )
            return
        JudgeRun.objects.filter(pk=run.pk).update(task_id=task.id)

    transaction.on_commit(dispatch)
    return run, True


def accept_judge_candidate(
    candidate: Suggestion, request: AuthenticatedHttpRequest
) -> None:
    """
    Accept a stored judge repair candidate under every acceptance guard.

    Stronger than a plain ``suggestion.accept``: requires both unit.review
    and translation.auto (invariant 5). Locks Unit, Suggestion, then the
    representative JudgeVerdict, in that order (matching _store_candidate's
    and queue_judge_recheck's lock order), and only proceeds while that
    verdict is still the current unresolved REJECT/FLAG for this exact
    target/context (invariant 2). Writes STATE_FUZZY with ActionEvents.ACCEPT
    provenance and propagate=False (invariant 4), consumes the candidate, and
    queues the one paid re-check that alone can make the string shippable
    again (invariant 8). Every guard failure raises JudgeCandidateError with
    a producer-facing message; callers translate it into their own response
    shape.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit as UnitModel

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.suggestion import Suggestion as SuggestionModel

    user = request.user
    unit = candidate.unit
    if not user.has_perm("unit.review", unit) or not user.has_perm(
        "translation.auto", unit.translation
    ):
        raise JudgeCandidateError(
            gettext("You do not have permission to accept this candidate.")
        )

    with transaction.atomic():
        locked_unit = (
            UnitModel.objects.select_for_update()
            .prefetch()
            .prefetch_source()
            .get(pk=unit.pk)
        )
        try:
            locked_candidate = SuggestionModel.objects.select_for_update().get(
                pk=candidate.pk
            )
        except SuggestionModel.DoesNotExist:
            raise JudgeCandidateError(
                gettext("This candidate has already been handled.")
            ) from None
        metadata = JudgeCandidateMetadata.parse(locked_candidate.userdetails)
        if metadata is None:
            msg = "not a judge repair candidate"
            raise JudgeCandidateError(msg)
        try:
            verdict = JudgeVerdict.objects.select_for_update().get(
                pk=metadata.verdict_id
            )
        except JudgeVerdict.DoesNotExist:
            raise JudgeCandidateError(
                gettext("The verdict behind this candidate no longer exists.")
            ) from None
        # Checked before verdict identity: current_verdict() is itself
        # hash-matched, so a target/context drift and "another verdict
        # superseded this one" would otherwise both surface as the same
        # "no longer current" outcome. Producers need the more specific
        # message when the text itself is what moved (invariant 2).
        if metadata.target_hash != compute_target_hash(
            locked_unit.get_target_plurals()
        ) or metadata.context_hash != _unit_context_hash(locked_unit):
            raise JudgeCandidateError(
                gettext("This candidate no longer matches the current text.")
            )
        current = current_verdict(locked_unit)
        if (
            verdict.resolution
            or verdict.verdict not in _CANDIDATE_VERDICTS
            or current is None
            or current.pk != verdict.pk
            # Route identity is part of the contract: a row claiming a run
            # that did not produce this verdict is not a live candidate, and
            # accepting it would record forged provenance.
            or metadata.run_id != verdict.run_id
        ):
            raise JudgeCandidateError(
                gettext("The verdict is no longer current for this string.")
            )

        locked_unit.translate(
            user,
            locked_candidate.target_list,
            STATE_FUZZY,
            change_action=ActionEvents.ACCEPT,
            propagate=False,
            change_details={
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
            },
        )
        locked_candidate.delete()

    queue_judge_recheck(locked_unit, user)


def _generation_lock_key(unit_id: int, verdict_id: int) -> str:
    return f"judge-candidate-generation:{unit_id}:{verdict_id}"


def generation_pending(unit_id: int, verdict_id: int) -> bool:
    """Whether a generation task is currently in flight for this verdict."""
    from django.core.cache import cache  # ruff: ignore[import-outside-top-level]

    return bool(cache.get(_generation_lock_key(unit_id, verdict_id)))


def generate_candidate_for_verdict(
    *, unit_id: int, verdict_id: int, user: User | None, replace: bool
) -> str:
    """
    Generate one stored candidate for the unit's current unresolved verdict.

    Calls the repair engine exactly once, writes nothing but a candidate,
    and never mutates the target (invariant 1). Outcome codes are stable:
    generated / existing / stale / resolved / invalid-verdict / max-length /
    no-engine / busy / failed / drift. A paid output that no longer matches
    the judged snapshot is discarded, keeping any older candidate.
    """
    from django.core.cache import cache  # ruff: ignore[import-outside-top-level]

    key = _generation_lock_key(unit_id, verdict_id)
    if not cache.add(key, "1", timeout=GENERATION_LOCK_TTL_SECONDS):
        return "busy"
    try:
        return _generate_candidate(
            unit_id=unit_id, verdict_id=verdict_id, user=user, replace=replace
        )
    finally:
        cache.delete(key)


def _generate_candidate(
    *, unit_id: int, verdict_id: int, user: User | None, replace: bool
) -> str:
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        Unit as UnitModel,
    )

    unit = UnitModel.objects.filter(pk=unit_id).prefetch_full().first()
    if unit is None:
        return "stale"
    # The view checked permissions before queueing; re-check them here so a
    # revocation between enqueue and execution cannot still spend money.
    if user is not None and (
        not user.has_perm("unit.review", unit)
        or not user.has_perm("translation.auto", unit.translation)
    ):
        return "denied"
    unit.invalidate_checks_cache()
    unit.clear_checks_cache()
    unit.run_checks()
    verdict = current_verdict(unit)
    if verdict is None or verdict.pk != verdict_id:
        return "stale"
    if verdict.verdict not in _CANDIDATE_VERDICTS:
        return "invalid-verdict"
    if verdict.resolution:
        return "resolved"
    if _has_active_check(unit, "max-length"):
        # That unit stays on the deterministic mutating path; a preview of
        # an overflowing text is not a usable candidate.
        return "max-length"
    if not replace and active_judge_candidate(unit, verdict) is not None:
        return "existing"
    engine = configured_routed_engine(
        unit.translation.component.project.get_machinery_settings()
    )
    if engine is None:
        return "no-engine"
    repairs = repair_targets([unit], user)
    new_target = repairs.get(unit.pk)
    if new_target is None:
        return "failed"
    request = build_request(unit)
    stored = _store_candidate(
        unit,
        request,
        unit.get_target_plurals(),
        unit.state,
        new_target,
        verdict,
        engine,
        user,
    )
    if stored == "stored":
        return "generated"
    return "drift" if stored == "drift" else "failed"
