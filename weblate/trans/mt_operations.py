# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable MT attempts: no web request owns paid work or its result."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from weblate.machinery.models import MACHINERY
from weblate.trans.autotranslate import AutoTranslate
from weblate.trans.models import (
    ProducerMTOperation,
    ProducerMTUnitAttempt,
    ProducerRun,
    Unit,
)
from weblate.trans.models.judge import compute_decision_revision
from weblate.trans.models.producer_mt import unit_revision
from weblate.wizard_auth.grants import (
    WizardAuthorizationRequired,
    delegation_active,
    run_delegation_active,
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


def _recover_saved_bodies(operation) -> None:
    """Re-run only the local parser on original, scope-bound saved replies."""
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
            permitted = (
                current_actor is not None
                and run_delegation_active(operation.run)
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
        try:
            answers = engine._translate_sources(  # ruff: ignore[private-member-access]
                translation.component.source_language.code,
                translation.language.code,
                sources,
                user=current_actor,
                threshold=operation.payload["threshold"],
            )
        except Exception as error:
            # Native machinery wraps domain exceptions once while preserving
            # __cause__; keep the typed pre-transport refusal through that seam.
            authorization_refused = isinstance(
                error, WizardAuthorizationRequired
            ) or isinstance(error.__cause__, WizardAuthorizationRequired)
            # Some internal context groups can already have a free cached answer.
            answers = None
            recovered = engine._prepare_translation_download(  # ruff: ignore[private-member-access]
                translation.component.source_language.code,
                translation.language.code,
                sources,
                operation.payload["threshold"],
            ).output
        for index, (attempt, target_unit) in enumerate(chunk):
            results = answers[index] if answers is not None else recovered[index]
            with transaction.atomic():
                _owned(operation.pk, owner)
                saved = ProducerMTUnitAttempt.objects.select_for_update().get(
                    pk=attempt.pk
                )
                if (
                    results
                    and results[0].get("quality", 0) >= operation.payload["threshold"]
                ):
                    saved.answer = results[0]["text"]
                    saved.state = "received"
                else:
                    saved.state = "provider_unknown" if answers is None else "failed"
                    if authorization_refused:
                        # A context group may have completed before the refused
                        # group. Only absence of its durable delivery claim proves
                        # that this individual unit never entered paid transport.
                        # ruff: ignore[import-outside-top-level]
                        from weblate.trans.models.llm_delivery import ProducerLLMWork

                        identity = f"mt:{saved.unit_id}:{translation.language.code}:{compute_decision_revision(target_unit.source_unit)}"
                        if not ProducerLLMWork.objects.filter(
                            identity=identity
                        ).exists():
                            saved.state = "pending"
                saved.save(update_fields=["state", "answer"])
        if answers is None:
            # Do not label the unrequested tail paid/unknown or silently retry it.
            return authorization_refused
    return False


# Keep the state transitions together; row locks end before every provider call.
# ruff: ignore[complex-structure, too-many-statements]
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
    actor = operation.actor
    run = operation.run
    cancelled = _cancelled(run_id)
    permitted = (
        actor is not None
        and run_delegation_active(run)
        and actor.has_perm("translation.auto", translation)
    )
    permitted = (
        permitted
        and not translation.component.locked
        and not translation.is_readonly
        and not translation.is_source
    )
    delegated = bool(run.execution_options.get("_wizard_delegation"))
    authorization_paused = delegated and not permitted
    engine = build_engine(operation) if permitted and not cancelled else None
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
            if delegated and (
                authorization_paused
                or not run_delegation_active(operation.run)
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
                or not run_delegation_active(operation.run)
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
                auto.update(unit, auto.target_state, [saved.answer])
                auto.post_process()
                unit.refresh_from_db()
                saved.state = "applied" if any(unit.get_target_plurals()) else "failed"
            saved.after_revision = unit_revision(unit)
            saved.save(update_fields=["state", "after_revision"])

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
        actor = operation.actor
        permitted = (
            actor is not None
            and run_delegation_active(run)
            and actor.has_perm("translation.auto", translation)
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
            elif saved.state in {"pending", "released"}:
                saved.state = "released"
                saved.save(update_fields=["state"])
                disposition = "never_sent"
            elif saved.state == "received" and saved.answer and saved.audit_revision:
                auto = AutoTranslate(
                    translation=translation,
                    user=actor,
                    mode="translate",
                    q="state:empty",
                    unit_ids=[unit.pk],
                )
                if len(saved.answer) <= unit.get_max_length():
                    auto.update(unit, auto.target_state, [saved.answer])
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
