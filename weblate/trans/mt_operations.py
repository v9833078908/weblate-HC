# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable MT attempts: no web request owns paid work or its result."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import httpx2
from django.db import transaction
from django.utils import timezone

from weblate.machinery.base import MachineTranslationServiceError
from weblate.machinery.models import MACHINERY
from weblate.trans.autotranslate import AutoTranslate, check_auto_translate_permission
from weblate.trans.models import (
    ProducerMTOperation,
    ProducerMTUnitAttempt,
    ProducerRun,
    Unit,
)
from weblate.trans.models.judge import compute_decision_revision
from weblate.trans.models.producer_mt import unit_revision
from weblate.trans.util import join_plural, split_plural
from weblate.wizard_auth.grants import (
    WizardAuthorizationRequired,
    delegated_run_actor,
    delegation_active,
)

if TYPE_CHECKING:
    from weblate.machinery.llm import BaseLLMTranslation
    from weblate.machinery.types import TranslationResultDict

TERMINAL = {"completed", "partial", "failed", "provider_unknown", "auth_required"}


def _attempt_current(attempt, unit):
    if attempt.source_revision:
        source = Unit.objects.select_for_update().get(pk=unit.source_unit_id)
        if compute_decision_revision(source) != attempt.source_revision:
            return False
        unit.source_unit = source
    return unit_revision(unit) == attempt.revision and (
        not attempt.audit_revision
        or compute_decision_revision(unit) == attempt.audit_revision
    )


def machinery_config_digest(project, engine: str) -> str:
    project.refresh_from_db(fields=["machinery_settings"])
    return hashlib.sha256(
        json.dumps(
            project.get_machinery_settings().get(engine, {}),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


def _captured_request_reason(identity: str, engine) -> str | None:
    """Classify this canonical work's captured response with its own engine."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import read_response

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.llm_delivery import ProducerLLMWork

    work = (
        ProducerLLMWork.objects.select_related("exchange")
        .filter(identity=identity)
        .first()
    )
    if work is None or work.exchange.state != "received":
        return None
    try:
        saved = json.loads(read_response(work.exchange_id))
        response = httpx2.Response(
            saved["status"],
            content=saved["body"].encode(),
            headers=saved["headers"],
            request=httpx2.Request("POST", "https://invalid.local/"),
        )
        engine.check_failure(response)
    except MachineTranslationServiceError as error:
        return error.reason_code
    except Exception:
        return None
    return None


def _recover_saved_bodies(operation) -> None:
    """Re-run only the local parser on original, scope-bound saved replies."""
    _recover_stage_answers(operation)
    from weblate.machinery.base import (  # ruff: ignore[import-outside-top-level]
        MachineTranslationError,
    )
    from weblate.trans.llm_delivery import (  # ruff: ignore[import-outside-top-level]
        DeliveryUnresolved,
        read_response,
    )
    from weblate.trans.models.llm_delivery import (  # ruff: ignore[import-outside-top-level]
        ProducerLLMWork,
    )

    if (
        not operation.configuration_digest
        or operation.configuration_digest
        != machinery_config_digest(operation.project, operation.payload["engine"])
    ):
        return
    attempts = list(
        operation.units.select_related("unit__source_unit").filter(
            state__in={"requesting", "provider_unknown", "failed"}
        )
    )
    identities = {
        f"mt:{attempt.unit_id}:{operation.translation.language.code}:{compute_decision_revision(attempt.unit.source_unit)}"
        for attempt in attempts
        if attempt.audit_revision and _attempt_current(attempt, attempt.unit)
    }
    exchanges = set(
        ProducerLLMWork.objects.filter(identity__in=identities).values_list(
            "exchange_id", flat=True
        )
    )
    engine = None
    for exchange in exchanges:
        members = list(
            ProducerLLMWork.objects.filter(exchange_id=exchange).order_by("position")
        )
        original = []
        for member in members:
            parts = member.identity.split(":")
            if (
                len(parts) != 4
                or parts[0] != "mt"
                or parts[2] != operation.translation.language.code
            ):
                break
            unit = (
                Unit.objects.filter(pk=parts[1], translation=operation.translation)
                .select_related("source_unit")
                .first()
            )
            if unit is None or compute_decision_revision(unit.source_unit) != parts[3]:
                break
            original.append(unit)
        if len(original) != len(members):
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            raw = json.loads(read_response(exchange))
            if raw.get("status") != 200:
                continue
            engine = engine or build_engine(operation)
            engine.delivery_replay_only = True
            engine.cache_translations = False
            sources: list[tuple[str, Unit | None]] = [
                (unit.get_source_plurals()[0], unit) for unit in original
            ]
            # Follow the original producer path: cleanup, context grouping,
            # exact request replay and protected-token restoration in order.
            results = engine._translate_sources(  # ruff: ignore[private-member-access]
                operation.translation.component.source_language.code,
                operation.translation.language.code,
                sources,
                user=operation.actor,
                threshold=operation.payload["threshold"],
            )
        except (
            DeliveryUnresolved,
            MachineTranslationError,
            ValueError,
            KeyError,
            TypeError,
        ):
            continue
        for index, unit in enumerate(original):
            answers = results[index] if index < len(results) else []
            if (
                not answers
                or answers[0].get("quality", 0) < operation.payload["threshold"]
            ):
                continue
            operation.units.filter(
                unit=unit,
                state__in={"requesting", "provider_unknown", "failed"},
                audit_revision=compute_decision_revision(unit),
            ).update(state="received", answer=answers[0]["text"])


def _recover_stage_answers(operation) -> None:
    """
    Apply persisted fallback answers locally, without any provider I/O.

    A captured fallback answer is durable stage evidence: recovery reads it
    back under the same current guards as a fresh apply - empty target,
    current source/context revision, unit and translation state and the
    original actor's rights - and never overwrites anything a human wrote.
    It never re-sends, and a disabled policy still allows the local read of
    an already-captured answer.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.mt_fallback import (
        ProducerMTFallbackAuthorization,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.mt_fallback import recover_mt_stage_answers

    authorization = ProducerMTFallbackAuthorization.objects.filter(
        run_id=operation.run_id
    ).first()
    if authorization is None:
        return
    result = recover_mt_stage_answers(authorization.pk)
    for answer in result.answers:
        if not answer.usable or not answer.text:
            continue
        with transaction.atomic():
            saved = (
                ProducerMTUnitAttempt.objects.select_for_update()
                .filter(
                    operation=operation,
                    unit_id=answer.unit_id,
                    state__in={
                        "credit_refused",
                        "credit_stopped",
                        "requesting",
                        "provider_unknown",
                        "failed",
                        "pending",
                    },
                )
                .select_related("unit")
                .first()
            )
            if saved is None or saved.unit is None:
                continue
            unit = Unit.objects.select_for_update().get(pk=saved.unit_id)
            translation = operation.translation
            if (
                any(unit.get_target_plurals())
                or not _attempt_current(saved, unit)
                or unit.readonly
                or translation.is_readonly
                or translation.component.locked
            ):
                continue
            actor = (
                type(operation.actor).objects.filter(pk=operation.actor_id).first()
                if operation.actor is not None
                else None
            )
            actor = delegated_run_actor(
                operation.run, actor, component=translation.component
            )
            if (
                actor is None
                or _cancelled(operation.run_id)
                or not check_auto_translate_permission(actor, translation, "translate")
            ):
                continue
            auto = AutoTranslate(
                translation=translation,
                user=actor,
                mode="translate",
                q="state:empty",
                unit_ids=[unit.pk],
            )
            auto.producer_run = operation.run
            auto.update(unit, auto.target_state, split_plural(answer.text))
            auto.post_process()
            unit.refresh_from_db()
            if not any(unit.get_target_plurals()):
                continue
            saved.state = "applied"
            saved.after_revision = unit_revision(unit)
            saved.save(update_fields=["state", "after_revision"])
            authorization.fallback_units.filter(
                unit_id_snapshot=unit.pk,
                fallback_state="received",
                fallback_exchange__isnull=False,
            ).update(fallback_state="applied")


def build_engine(operation: ProducerMTOperation) -> BaseLLMTranslation:
    config = operation.project.get_machinery_settings()
    engine = cast(
        "BaseLLMTranslation",
        MACHINERY[operation.payload["engine"]](config[operation.payload["engine"]]),
    )
    # The durable journal, not machinery retry/split heuristics, owns spend.
    engine.retry_attempts = 0
    engine.durable_single_attempt = True
    engine.usage_run_id = str(operation.run_id)
    return engine


def _cancelled(run_id) -> bool:
    return ProducerRun.objects.filter(
        pk=run_id, status__in={"cancel_requested", "cancelled"}
    ).exists()


def _owned(operation_id, owner):
    operation = ProducerMTOperation.objects.select_for_update().get(pk=operation_id)
    if (
        operation.lease_owner != owner
        or operation.lease_until is None
        or operation.lease_until <= timezone.now()
    ):
        msg = "MT operation ownership changed"
        raise RuntimeError(msg)
    return operation


def _receive_pending(operation, owner, engine, translation, actor, pending) -> bool:
    """Persist each engine-sized reply before authorizing another outbound chunk."""
    size = max(1, min(20, getattr(engine, "batch_size", 20)))
    for offset in range(0, len(pending), size):
        chunk = []
        with transaction.atomic():
            _owned(operation.pk, owner)
            if _cancelled(operation.run_id):
                return False
            translation.refresh_from_db()
            translation.component.refresh_from_db()
            current_actor = (
                type(actor).objects.filter(pk=operation.actor_id).first()
                if actor
                else None
            )
            current_actor = delegated_run_actor(
                operation.run, current_actor, component=translation.component
            )
            permitted = (
                current_actor is not None
                and current_actor.has_perm("translation.auto", translation)
                and not translation.component.locked
                and not translation.is_readonly
            )
            if (
                operation.run.execution_options.get("_wizard_delegation")
                and not permitted
            ):
                return True
            for attempt, _unit in pending[offset : offset + size]:
                saved = ProducerMTUnitAttempt.objects.select_for_update().get(
                    pk=attempt.pk
                )
                unit = Unit.objects.select_for_update().get(pk=saved.unit_id)
                if any(unit.get_target_plurals()):
                    saved.state = "skipped_nonempty"
                elif not _attempt_current(saved, unit) or unit.readonly:
                    saved.state = "stale"
                elif not permitted:
                    saved.state = "failed"
                else:
                    saved.state = "requesting"
                    chunk.append((saved, unit))
                saved.after_revision = unit_revision(unit)
                saved.save(update_fields=["state", "after_revision"])
        if not chunk:
            continue
        sources: list[tuple[str, Unit | None]] = [
            (unit.get_source_plurals()[0], unit) for _a, unit in chunk
        ]
        recovered: list[list[TranslationResultDict]] = [[] for _ in chunk]
        authorization_refused = False
        credit_refused = False
        answers = None
        try:
            answers = engine._translate_sources(  # ruff: ignore[private-member-access]
                translation.component.source_language.code,
                translation.language.code,
                sources,
                user=current_actor,
                threshold=operation.payload["threshold"],
            )
        except Exception as error:
            authorization_refused = isinstance(
                error, WizardAuthorizationRequired
            ) or isinstance(error.__cause__, WizardAuthorizationRequired)
            if isinstance(error, MachineTranslationServiceError) and (
                error.reason_code
                == MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT
            ):
                credit_refused = any(
                    _captured_request_reason(
                        f"mt:{attempt.unit_id}:{translation.language.code}:"
                        f"{compute_decision_revision(unit.source_unit)}",
                        engine,
                    )
                    == MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT
                    for attempt, unit in chunk
                )
            recovered = engine._prepare_translation_download(  # ruff: ignore[private-member-access]
                translation.component.source_language.code,
                translation.language.code,
                sources,
                operation.payload["threshold"],
            ).output
        _persist_chunk_results(
            operation,
            owner,
            translation,
            chunk,
            answers,
            recovered,
            authorization_refused,
            credit_refused,
            engine,
        )
        if answers is None:
            # A credit refusal stops new primary submissions, so the chunks
            # that were never asked must be classified before returning: a
            # unit among them with no durable claim of its own is the
            # never-sent tail, and it is the eligible remainder of the run.
            # A unit that already carries a claim is never released.
            if credit_refused:
                _classify_unsubmitted_tail(
                    operation,
                    owner,
                    translation,
                    pending[offset + size :],
                )
            # Only a transport refusal (WizardAuthorizationRequired) pauses the
            # operation; a credit refusal opens the fallback stage instead.
            return authorization_refused
    return False


def _classify_unsubmitted_tail(operation, owner, translation, tail) -> None:
    """
    Classify the chunks a confirmed credit refusal kept from being asked.

    A unit with no durable delivery claim of its own was provably never sent,
    so it is the run's eligible remainder. A unit that already carries a claim
    keeps that claim: it is never relabelled as unsent, which would erase a
    paid witness.
    """
    if not tail:
        return
    with transaction.atomic():
        _owned(operation.pk, owner)
        for attempt, unit in tail:
            saved = ProducerMTUnitAttempt.objects.select_for_update().get(pk=attempt.pk)
            if saved.state != "pending":
                continue
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.llm_delivery import ProducerLLMWork

            identity = f"mt:{saved.unit_id}:{translation.language.code}:{compute_decision_revision(unit.source_unit)}"
            if ProducerLLMWork.objects.filter(identity=identity).exists():
                saved.state = "provider_unknown"
            else:
                saved.state = "credit_stopped"
            saved.after_revision = unit_revision(unit)
            saved.save(update_fields=["state", "after_revision"])


# ruff: ignore[too-many-positional-arguments]
def _persist_chunk_results(
    operation,
    owner,
    translation,
    chunk,
    answers,
    recovered,
    authorization_refused,
    credit_refused,
    engine,
) -> None:
    """Persist one chunk's classified results under the operation lease."""
    for index, (attempt, target_unit) in enumerate(chunk):
        results = answers[index] if answers is not None else recovered[index]
        with transaction.atomic():
            _owned(operation.pk, owner)
            saved = ProducerMTUnitAttempt.objects.select_for_update().get(pk=attempt.pk)
            if (
                results
                and results[0].get("quality", 0) >= operation.payload["threshold"]
            ):
                saved.answer = results[0]["text"]
                saved.state = "received"
            else:
                # Every unit of a chunk carries its own durable delivery claim,
                # so the classification is read per attempt from that attempt's
                # own captured request - never from a peer's refusal in the
                # same chunk.
                identity = f"mt:{saved.unit_id}:{translation.language.code}:{compute_decision_revision(target_unit.source_unit)}"
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.models.llm_delivery import ProducerLLMWork

                claim = ProducerLLMWork.objects.filter(identity=identity).first()
                own_reason = _captured_request_reason(identity, engine)
                if (
                    own_reason
                    == MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT
                ):
                    # This unit's own request was refused for credit: the only
                    # evidence that authorizes its fallback slot. A quota,
                    # auth, permission or generic refusal never does.
                    saved.state = "credit_refused"
                elif claim is None and credit_refused:
                    # No reservation at all, and the run was stopped by a
                    # confirmed credit refusal: this is the tail that was never
                    # asked. The attempt records that it was stopped by the
                    # credit refusal without a paid request of its own; the
                    # stage journal carries the exact ``never_sent_due_credit``
                    # disposition for this slot.
                    saved.state = "credit_stopped"
                elif claim is None and authorization_refused:
                    # The transport guard refused before any reservation.
                    saved.state = "pending"
                elif claim is None and answers is not None:
                    saved.state = "failed"
                else:
                    # A claim exists: whether it is unresolved or merely
                    # generic, it is never released or retried as unsent.
                    saved.state = "provider_unknown" if answers is None else "failed"
            saved.save(update_fields=["state", "answer"])


# ruff: ignore[complex-structure]
def _run_fallback_stage(operation, owner, translation, actor) -> bool:
    """
    Run the journaled fallback stage for credit-refused primary attempts.

    The stage is materialized through the shared boundary in
    :mod:`weblate.trans.mt_fallback`: an immutable authorization, one canonical
    financial slot per unit of work, exact stage membership and one exchange
    per unit. Canonical ``ProducerLLMWork`` rows keep pointing at their primary
    exchange; only a never-sent tail identity without any claim is claimed
    under the real fallback exchange. No identity substitution, no digest
    weakening, at most one fallback charge per unit of work ever.

    Every received fallback answer is persisted on the unit attempt as
    ``received`` before the stage returns, so the caller's apply pass stores it
    under the same guards as a primary answer.

    Returns ``True`` only when the stage itself was blocked by an authorization
    pause (a disabled policy, a closed authorization or a revoked delegation),
    which the caller maps to ``auth_required``. A terminal fallback refusal,
    an unknown outcome or a successful stage all return ``False``: the run's
    own state comes from the units it did or did not resolve.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.mt_fallback import (
        STAGE_PRIMARY,
        ProducerMTFallbackAuthorization,
        ProducerMTStageMember,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.mt_fallback import (
        authorize_mt_credit_fallback,
        build_fallback_engine_settings,
        close_mt_credit_fallback,
        fallback_profile_configured,
        mark_slot_disposition,
        resolve_fallback_profile,
        save_fallback_answer,
    )

    # Recovery must remain available even after policy or credentials change.
    _recover_stage_answers(operation)
    machinery_settings = operation.project.get_machinery_settings()
    authorization = ProducerMTFallbackAuthorization.objects.filter(
        run_id=operation.run_id, translation=translation, operation=operation
    ).first()
    if authorization is None or not fallback_profile_configured(machinery_settings):
        return False
    # Two kinds of unit are eligible, and only these two: the unit whose own
    # primary request confirmed the credit refusal, and the unit inside the
    # enrolled scope that was never reserved at all because the run was stopped
    # by that refusal. A quota, auth, permission, timeout, partial, generic or
    # unknown outcome never authorizes a fallback, and a successful prefix is
    # never repurchased.
    eligible_attempts = list(
        operation.units.filter(
            state__in={"credit_refused", "credit_stopped"}
        ).select_related("unit")
    )
    if not eligible_attempts:
        return False
    fallback_units = list(authorization.fallback_units.select_related("unit"))
    attempts = {attempt.unit_id: attempt for attempt in eligible_attempts}
    for unit_stage in fallback_units:
        attempt = attempts.get(unit_stage.unit_id_snapshot)
        if attempt is None:
            continue
        for index, disposition in enumerate(unit_stage.primary_dispositions):
            if disposition != "unknown":
                continue
            primary = ProducerMTStageMember.objects.filter(
                unit_stage=unit_stage,
                stage=STAGE_PRIMARY,
                source_slot=index,
                exchange__work__semantic_key=unit_stage.semantic_key,
            ).exists()
            if attempt.state == "credit_refused" and primary:
                mark_slot_disposition(unit_stage, index, "credit_refused")
            elif attempt.state == "credit_stopped" and not primary:
                # A canonical claim, even one lacking stage membership, is
                # never evidence of an unpaid tail.
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.models.llm_delivery import ProducerLLMWork

                if not ProducerLLMWork.objects.filter(
                    semantic_key=unit_stage.semantic_key
                ).exists():
                    mark_slot_disposition(unit_stage, index, "never_sent_due_credit")
    eligible = [
        unit_stage
        for unit_stage in fallback_units
        if any(
            disposition in {"never_sent_due_credit", "credit_refused"}
            for disposition in unit_stage.primary_dispositions
        )
    ]
    if eligible:
        authorize_mt_credit_fallback(authorization.pk)
    if not eligible:
        return False
    profile = resolve_fallback_profile(machinery_settings)
    if profile is None:
        return False
    engine_settings = build_fallback_engine_settings(machinery_settings)
    if engine_settings is None:
        return False
    fallback_engine = cast(
        "BaseLLMTranslation", MACHINERY[profile.engine](engine_settings)
    )
    # The journal, not machinery retry/split heuristics, owns the spend. The
    # durable identity is enabled for the fallback stage alone: it carries the
    # canonical work identity the shared reservation fences. The legacy
    # primary keeps its own (non-durable) identity and its own fence.
    fallback_engine.retry_attempts = 0
    fallback_engine.durable_single_attempt = True
    fallback_engine.credit_fallback_single_attempt = True
    fallback_engine.usage_run_id = str(operation.run_id)
    # ruff: ignore[private-member-access]
    fallback_engine._mt_stage = "fallback"
    # ruff: ignore[private-member-access]
    fallback_engine._mt_fallback_authorization_id = authorization.pk
    # ruff: ignore[private-member-access]
    fallback_engine._mt_stage_actor = actor
    answers: dict[str, list[str]] = {}
    # ruff: ignore[too-many-statements-in-try-clause]
    try:
        for unit_stage in eligible:
            slots = []
            for index, disposition in enumerate(unit_stage.primary_dispositions):
                if disposition not in {"never_sent_due_credit", "credit_refused"}:
                    continue
                # The slot already carries its own frozen evidence: its own
                # request was refused for credit, or it was never reserved at
                # all inside the credit-stopped scope. It is not reclassified
                # here.
                slots.append((unit_stage.pk, index))
            if not slots:
                continue
            unit = unit_stage.unit
            if unit is None:
                continue
            # Every eligible unresolved source slot of this unit travels in
            # one occurrence-safe request; the results stay aligned by
            # position, never merged by text. ``slots`` pairs the stage row
            # with the unit's original source-slot index.
            ordered = [
                (stage_id, index, unit_stage.source_slots[index], unit)
                for stage_id, index in slots
            ]
            results = fallback_engine.translate_mt_fallback_slots(
                translation.component.source_language.code,
                translation.language.code,
                ordered,
                user=actor,
                threshold=operation.payload["threshold"],
            )
            texts = [
                item["text"]
                for group in results
                for item in group
                if item.get("quality", 0) >= operation.payload["threshold"]
            ]
            if not texts:
                continue
            # One combined vector per unit: the fallback answers of its
            # eligible slots joined with the primary answers already captured
            # for its other slots, in the original source-slot order. No
            # newline-joined pseudo-vector and no dropped primary prefix.
            combined = list(unit_stage.primary_answers)
            while len(combined) < len(unit_stage.source_slots):
                combined.append("")
            for (_stage_id, index), text in zip(slots, texts, strict=True):
                combined[index] = text
            if any(not text for text in combined):
                continue
            answers[unit_stage.identity] = combined
    except MachineTranslationServiceError:
        # A fallback refusal is terminal: there is no third paid route. The
        # durable primary evidence and the stage journal stay as they are,
        # and the authorization is closed so no later dispatch reopens it.
        close_mt_credit_fallback(authorization.pk)
        return False
    if not answers:
        # Nothing usable came back. The stage is over for this authorization:
        # close it so a later delivery cannot open a second paid route.
        close_mt_credit_fallback(authorization.pk)
        return False
    save_fallback_answer(authorization.pk, answers)
    # Persist the received answers on their attempts so the caller's apply
    # pass stores them under the same empty-target and revision guards. The
    # answer is the unit's complete plural vector, never a joined string.
    for unit_stage in fallback_units:
        vector = answers.get(unit_stage.identity)
        if vector is None:
            continue
        with transaction.atomic():
            _owned(operation.pk, owner)
            saved = (
                ProducerMTUnitAttempt.objects.select_for_update()
                .filter(
                    operation=operation,
                    unit_id=unit_stage.unit_id_snapshot,
                    state__in={
                        "credit_refused",
                        "credit_stopped",
                        "failed",
                        "pending",
                        "requesting",
                        "provider_unknown",
                    },
                )
                .first()
            )
            if saved is None:
                continue
            saved.answer = join_plural(vector)
            saved.state = "received"
            saved.save(update_fields=["state", "answer"])
    return False


# Keep the state transitions together; row locks end before every provider call.
# ruff: ignore[complex-structure, too-many-statements]
# ruff: ignore[too-many-locals]
def process_mt_operation(run_id: str | UUID) -> dict:
    run_id = UUID(str(run_id))
    owner = uuid4().hex
    with transaction.atomic():
        operation = ProducerMTOperation.objects.select_for_update().get(run_id=run_id)
        if operation.state in TERMINAL:
            return {"state": operation.state, "warnings": []}
        run = ProducerRun.objects.select_for_update().get(pk=run_id)
        if run.status in {"cancel_requested", "cancelled"}:
            operation.state = "failed"
            operation.save(update_fields=["state", "updated"])
            operation.units.filter(state="pending").update(state="failed")
            run.status = "cancelled"
            run.finished = timezone.now()
            run.save(update_fields=["status", "finished"])
            return {"state": "failed", "warnings": []}
        if run.status in {"failed", "completed", "partial"}:
            return {"state": "failed", "warnings": []}
        if (
            operation.lease_owner
            and operation.lease_until is not None
            and operation.lease_until > timezone.now()
        ):
            return {"state": "running", "warnings": []}
        operation.lease_owner = owner
        operation.lease_until = timezone.now() + timedelta(seconds=600)
        operation.state = "running"
        operation.save(update_fields=["lease_owner", "lease_until", "state", "updated"])
        run.status = "running"
        run.started = run.started or timezone.now()
        run.save(update_fields=["status", "started"])
    translation = operation.translation
    run = operation.run
    actor = delegated_run_actor(run, operation.actor, component=translation.component)
    cancelled = _cancelled(run_id)
    permitted = actor is not None and actor.has_perm("translation.auto", translation)
    permitted = (
        permitted
        and not translation.component.locked
        and not translation.is_readonly
        and not translation.is_source
    )
    delegated = bool(run.execution_options.get("_wizard_delegation"))
    authorization_paused = delegated and not permitted
    engine = build_engine(operation) if permitted and not cancelled else None
    if engine is not None and operation.payload["engine"] == "openrouter":
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.mt_fallback import (
            enroll_mt_credit_scope,
            enroll_unit_stages,
            resolve_fallback_profile,
        )

        machinery_settings = operation.project.get_machinery_settings()
        if resolve_fallback_profile(machinery_settings) is not None:
            attempts = list(
                operation.units.select_related("unit__source_unit").order_by("unit_id")
            )
            if all(attempt.unit is not None for attempt in attempts):
                units = [attempt.unit for attempt in attempts]
                authorization = enroll_mt_credit_scope(
                    run=run,
                    translation=translation,
                    units=units,
                    operation=operation,
                    machinery_settings=machinery_settings,
                )
                stages = enroll_unit_stages(authorization, units)
                attempts_by_unit = {attempt.unit_id: attempt for attempt in attempts}
                for stage in stages:
                    if stage.unit_attempt_id is None:
                        stage.unit_attempt = attempts_by_unit[stage.unit_id_snapshot]
                        stage.save(update_fields=["unit_attempt"])
                engine.credit_fallback_single_attempt = True
                # ruff: ignore[private-member-access]
                engine._mt_stage = "primary"
                # ruff: ignore[private-member-access]
                engine._mt_fallback_authorization_id = authorization.pk
                # ruff: ignore[private-member-access]
                engine._mt_stage_actor = actor
    selected = []
    # Each unit is checked independently; no row lock survives provider I/O.
    for attempt_id in operation.units.order_by("unit_id").values_list("pk", flat=True):
        with transaction.atomic():
            _owned(operation.pk, owner)
            attempt = (
                ProducerMTUnitAttempt.objects.select_for_update()
                .select_related("unit")
                .get(pk=attempt_id)
            )
            if attempt.state in {
                "applied",
                "skipped_nonempty",
                "stale",
                "failed",
                "provider_unknown",
                "released",
                # A unit whose own primary request confirmed the credit
                # refusal is not re-sent to the primary engine: it is either
                # resolved by the fallback stage below or it stays unresolved.
                "credit_refused",
                "credit_stopped",
            }:
                continue
            unit = Unit.objects.select_for_update().get(pk=attempt.unit_id)
            if any(unit.get_target_plurals()):
                attempt.state = "skipped_nonempty"
            elif not _attempt_current(attempt, unit) or unit.readonly:
                attempt.state = "stale"
            elif not permitted or cancelled or _cancelled(run_id):
                if not delegated or cancelled or _cancelled(run_id):
                    attempt.state = "failed"
            else:
                selected.append((attempt, unit))
                continue
            attempt.after_revision = unit_revision(unit)
            attempt.save(update_fields=["state", "after_revision"])

    if selected:
        # Selection above requires permission and an initialized routed engine.
        engine = cast("BaseLLMTranslation", engine)
        sources: list[tuple[str, Unit | None]] = [
            (unit.get_source_plurals()[0], unit) for _attempt, unit in selected
        ]
        cached = engine._prepare_translation_download(  # ruff: ignore[private-member-access]
            translation.component.source_language.code,
            translation.language.code,
            sources,
            operation.payload["threshold"],
        )
        pending = []
        for (attempt, unit), results in zip(selected, cached.output, strict=True):
            with transaction.atomic():
                _owned(operation.pk, owner)
                saved = ProducerMTUnitAttempt.objects.select_for_update().get(
                    pk=attempt.pk
                )
                if saved.answer:
                    saved.state = "received"
                elif (
                    results
                    and results[0].get("quality", 0) >= operation.payload["threshold"]
                ):
                    saved.answer = results[0]["text"]
                    saved.state = "received"
                elif saved.state == "requesting":
                    # Redelivery cannot tell an unpaid request from a paid lost reply.
                    saved.state = "provider_unknown"
                else:
                    pending.append((saved, unit))
                saved.save(update_fields=["state", "answer"])
        if pending:
            authorization_paused = _receive_pending(
                operation, owner, engine, translation, actor, pending
            )

    # A confirmed primary credit refusal may authorise exactly one fallback
    # stage, journaled as a ProducerMTFallbackAuthorization.
    if not authorization_paused:
        authorization_paused = _run_fallback_stage(operation, owner, translation, actor)

    for ident in operation.units.filter(state="received").values_list("pk", flat=True):
        with transaction.atomic():
            _owned(operation.pk, owner)
            if _cancelled(run_id):
                # The answer stays private and durable; cancellation never applies it.
                break
            translation.refresh_from_db()
            # A component can be locked while the provider is answering.
            translation.component.refresh_from_db()
            actor = (
                type(actor).objects.filter(pk=operation.actor_id).first()
                if actor
                else None
            )
            actor = delegated_run_actor(
                operation.run, actor, component=translation.component
            )
            if delegated and (
                authorization_paused
                or actor is None
                or not actor.has_perm("translation.auto", translation)
            ):
                authorization_paused = True
                break
            saved = ProducerMTUnitAttempt.objects.select_for_update().get(pk=ident)
            unit = Unit.objects.select_for_update().get(pk=saved.unit_id)
            if any(unit.get_target_plurals()):
                saved.state = "skipped_nonempty"
            elif not _attempt_current(saved, unit) or unit.readonly:
                saved.state = "stale"
            elif (
                actor is None
                or not actor.has_perm("translation.auto", translation)
                or translation.component.locked
                or translation.is_readonly
                or not saved.answer.strip()
                or len(saved.answer) > unit.get_max_length()
            ):
                saved.state = "failed"
            else:
                auto = AutoTranslate(
                    translation=translation,
                    user=actor,
                    mode="translate",
                    q="state:empty",
                    unit_ids=[unit.pk],
                )
                auto.update(unit, auto.target_state, split_plural(saved.answer))
                auto.post_process()
                unit.refresh_from_db()
                saved.state = "applied" if any(unit.get_target_plurals()) else "failed"
            saved.after_revision = unit_revision(unit)
            saved.save(update_fields=["state", "after_revision"])
            if saved.state == "applied":
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.models.mt_fallback import ProducerMTFallbackUnit

                ProducerMTFallbackUnit.objects.filter(
                    authorization__operation=operation,
                    unit_id_snapshot=unit.pk,
                    fallback_state="received",
                    fallback_exchange__isnull=False,
                ).update(fallback_state="applied")

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.mt_fallback import mt_fallback_run_summary

    fallback_summary = mt_fallback_run_summary(run)
    with transaction.atomic():
        current = _owned(operation.pk, owner)
        cancelled = _cancelled(run_id)
        states = list(current.units.values_list("state", flat=True))
        state = (
            "provider_unknown"
            if "provider_unknown" in states
            else "completed"
            if all(s in {"applied", "skipped_nonempty"} for s in states)
            else "partial"
        )
        if cancelled:
            state = "failed"
        elif authorization_paused:
            state = "auth_required"
        current.state = state
        current.lease_owner = ""
        current.lease_until = None
        current.save(update_fields=["state", "lease_owner", "lease_until", "updated"])
        ProducerRun.objects.filter(pk=run_id).update(
            status="cancelled"
            if cancelled
            else "completed"
            if state == "completed"
            else "partial",
            finished=timezone.now(),
            scope_cursor=sum(state != "pending" for state in states),
            summary={
                "completed": state == "completed",
                "units": len(states),
                "applied": states.count("applied"),
                "pending": states.count("pending"),
                "mt_state": state,
                **(
                    fallback_summary
                    if fallback_summary and fallback_summary["requested"]
                    else {}
                ),
            },
        )
    if state == "completed":
        from weblate.trans.llm_delivery import (  # ruff: ignore[import-outside-top-level]
            resolve_run_deliveries,
        )

        resolve_run_deliveries(ProducerRun.objects.get(pk=run_id))
    return {"state": state, "warnings": []}


def reconcile_mt_operation(operation_id, *, reauthorize_family=None) -> dict:
    """Separate coverage from financial history; never dispatch provider work."""
    groups: dict[str, list[int]] = {
        name: []
        for name in (
            "ready",
            "never_sent",
            "waiting",
            "paid_unknown",
            "paid_invalid",
            "stale",
            "forbidden",
        )
    }
    with transaction.atomic():
        operation = ProducerMTOperation.objects.select_for_update().get(pk=operation_id)
        run = ProducerRun.objects.select_for_update().get(pk=operation.run_id)
        if reauthorize_family is not None:
            # Explicit current-user reconciliation may renew an existing SSO
            # binding, never adopt a legacy service-account run or change actor.
            # ruff: ignore[import-outside-top-level]
            from rest_framework.exceptions import PermissionDenied

            if (
                not run.execution_options.get("_wizard_delegation")
                or reauthorize_family.user_id != operation.actor_id
                or not delegation_active(reauthorize_family.pk, operation.actor_id)
            ):
                msg = "Only the original delegated actor may reauthorize this batch"
                raise PermissionDenied(msg)
            run.execution_options = {
                **run.execution_options,
                "_wizard_delegation": str(reauthorize_family.pk),
            }
            run.save(update_fields=["execution_options"])
        translation = operation.translation
        actor = delegated_run_actor(
            run, operation.actor, component=translation.component
        )
        permitted = actor is not None and actor.has_perm(
            "translation.auto", translation
        )
        permitted = (
            permitted
            and not translation.component.locked
            and not translation.is_readonly
        )
        active = operation.state not in TERMINAL or (
            operation.lease_until is not None and operation.lease_until > timezone.now()
        )
        if (
            not active
            and permitted
            and run.status not in {"cancelled", "cancel_requested"}
        ):
            _recover_saved_bodies(operation)
        for saved in (
            operation.units.select_for_update()
            .select_related("unit")
            .order_by("unit_id")
        ):
            unit = saved.unit
            if not permitted or run.status in {"cancelled", "cancel_requested"}:
                disposition = "forbidden"
            elif any(unit.get_target_plurals()):
                # A human target closes coverage, not the old provider receipt.
                disposition = "ready"
            elif not _attempt_current(saved, unit) or unit.readonly:
                disposition = "stale"
            elif active:
                disposition = "waiting"
            elif saved.state in {
                "pending",
                "released",
                "requesting",
                "provider_unknown",
            }:
                # The stage journal is checked before any release, retry or
                # exchange selection: a unit with a reserved or captured
                # fallback exchange is never released as never_sent, which
                # would erase the financial witness.
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.models.mt_fallback import ProducerMTFallbackUnit

                fallback_slot = ProducerMTFallbackUnit.objects.filter(
                    authorization__run_id=operation.run_id,
                    unit_id=unit.pk,
                ).first()
                if fallback_slot is not None and fallback_slot.fallback_exchange_id:
                    disposition = (
                        "paid_unknown"
                        if fallback_slot.fallback_state
                        in {"reserved", "sending", "unknown"}
                        else "ready"
                        if fallback_slot.fallback_state == "received"
                        else "paid_unknown"
                    )
                elif saved.state in {"pending", "released"}:
                    saved.state = "released"
                    saved.save(update_fields=["state"])
                    disposition = "never_sent"
                else:
                    disposition = (
                        "paid_invalid" if saved.state == "failed" else "paid_unknown"
                    )
            elif saved.state == "received" and saved.answer and saved.audit_revision:
                auto = AutoTranslate(
                    translation=translation,
                    user=actor,
                    mode="translate",
                    q="state:empty",
                    unit_ids=[unit.pk],
                )
                if len(saved.answer) <= unit.get_max_length():
                    auto.update(unit, auto.target_state, split_plural(saved.answer))
                    auto.post_process()
                    unit.refresh_from_db()
                if any(unit.get_target_plurals()):
                    saved.state = "applied"
                    saved.after_revision = unit_revision(unit)
                    saved.save(update_fields=["state", "after_revision"])
                    disposition = "ready"
                else:
                    disposition = "paid_invalid"
            else:
                disposition = (
                    "paid_invalid" if saved.state == "failed" else "paid_unknown"
                )
            groups[disposition].append(unit.pk)
    return groups


def recover_expired_mt_operations() -> None:
    """Use the existing periodic outbox drain, not a second scheduler."""
    ids = ProducerMTOperation.objects.filter(
        state="running", lease_until__lt=timezone.now()
    ).values_list("pk", flat=True)
    for ident in ids.iterator():
        with transaction.atomic():
            operation = (
                ProducerMTOperation.objects.select_for_update(skip_locked=True)
                .filter(pk=ident, state="running", lease_until__lt=timezone.now())
                .first()
            )
            if (
                operation is None
                or operation.state != "running"
                or operation.lease_until is None
                or operation.lease_until >= timezone.now()
            ):
                continue
            run = ProducerRun.objects.select_for_update().get(pk=operation.run_id)
            if run.status not in {"queued", "running"}:
                continue
            operation.lease_owner = ""
            operation.lease_until = None
            operation.save(update_fields=["lease_owner", "lease_until", "updated"])
            run.dispatch_task_id = uuid4()
            run.dispatch_published_at = None
            run.status = "queued"
            run.save(
                update_fields=["dispatch_task_id", "dispatch_published_at", "status"]
            )
    # An old rollback worker can reject the deliberately unknown task name.
    # Re-offer only accepted/pending work under the same receipt after re-upgrade;
    # duplicate messages still share the operation's ownership fence.
    cutoff = timezone.now() - timedelta(seconds=600)
    accepted = ProducerMTOperation.objects.filter(
        state="accepted", run__status="queued", run__dispatch_published_at__lt=cutoff
    ).values_list("pk", flat=True)
    for ident in accepted.iterator():
        with transaction.atomic():
            operation = (
                ProducerMTOperation.objects.select_for_update(skip_locked=True)
                .filter(pk=ident, state="accepted")
                .first()
            )
            if operation is None or operation.units.exclude(state="pending").exists():
                continue
            run = ProducerRun.objects.select_for_update().get(pk=operation.run_id)
            if (
                run.status != "queued"
                or run.dispatch_published_at is None
                or run.dispatch_published_at >= cutoff
            ):
                continue
            run.dispatch_task_id = uuid4()
            run.dispatch_published_at = None
            run.save(update_fields=["dispatch_task_id", "dispatch_published_at"])
