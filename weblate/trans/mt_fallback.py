# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Shared MT credit-fallback stage boundary: config, policy, enrollment, dispatch.

One module owns the financial decision for both orchestrators (legacy
autotranslate and durable ``process_mt_operation``). It is deliberately not a
second scheduler and not a provider retry framework: every send reuses the
existing low-level delivery lifecycle in :mod:`weblate.trans.llm_delivery`.

Contracts enforced here:

* Only a confirmed primary ``insufficient-credit`` refusal authorizes a
  fallback. Quota, auth, permission, rate-limit, timeout, lost/paid-unknown,
  parse/partial and generic errors never do.
* Enrollment happens before the first primary call and freezes the immutable
  scope; a changed scope under the same authorization is refused.
* One exchange covers all fallback slots of a unit; a unit is never split into
  two fallback HTTP requests.
* Every fallback reservation commits its secret-free request snapshot in the
  same transaction, before any transport.
* ``recover_mt_stage_answers`` only reads persisted evidence and never sends.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext

from weblate.machinery.base import MachineTranslationServiceError
from weblate.trans.models.mt_fallback import (
    FALLBACK_POLICY,
    STAGE_FALLBACK,
    STAGE_PRIMARY,
    ProducerMTFallbackAuthorization,
    ProducerMTFallbackUnit,
    ProducerMTStageMember,
    canonical_identity,
    semantic_key_for,
)
from weblate.trans.util import join_plural

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from weblate.machinery.types import SettingsDict
    from weblate.trans.models import Translation, Unit
    from weblate.trans.models.judge import ProducerRun
    from weblate.trans.models.producer_mt import ProducerMTOperation

#: The only primary reason that authorizes a fallback stage.
FALLBACK_TRIGGER = MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT

#: Reasons that terminate a service for a whole run and are never eligible.
_TERMINAL_NON_CREDIT = frozenset(
    {
        MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
        MachineTranslationServiceError.REASON_AUTHENTICATION,
        MachineTranslationServiceError.REASON_PERMISSION,
    }
)

#: Accepted fallback profile: the fixed pilot route only.
_FALLBACK_ENGINE = "litellm"
_FALLBACK_MODEL = "QWEN3.7-plus"


@dataclass(frozen=True, slots=True)
class FallbackProfile:
    """Secret-free identity of the resolved fallback route."""

    engine: str
    model: str
    profile: str
    configuration_digest: str


@dataclass(frozen=True, slots=True)
class ExchangeRef:
    """A reserved exchange plus the authorization it belongs to."""

    id: UUID
    authorization_id: UUID
    owner: str
    can_send: bool


@dataclass(frozen=True, slots=True)
class MTStageDispatchResult:
    """The exchange a stage dispatch used and the captured receipt."""

    exchange_id: UUID
    authorization_id: UUID
    receipt: str


@dataclass(frozen=True, slots=True)
class StageRecoveryAnswer:
    """One persisted stage answer, ready for local application."""

    unit_id: int
    identity: str
    stage: str
    text: str
    usable: bool


@dataclass(frozen=True, slots=True)
class StageRecoveryResult:
    """Persisted stage evidence for one authorization, never a new send."""

    authorization_id: UUID
    answers: list[StageRecoveryAnswer]
    fallback_state: str


def configuration_digest(settings_block: dict) -> str:
    """Digest one engine's effective settings block for immutable pinning."""
    return hashlib.sha256(
        json.dumps(
            settings_block, sort_keys=True, ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()


#: Keys a secret-free request snapshot must never carry, at any depth. A
#: snapshot is the same private class of data as a raw reply; it never holds
#: credentials, and a recursive structure is refused outright.
_FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "key",
        "secret",
        "token",
        "password",
        "credential",
        "credentials",
        "cookie",
        "headers",
        "auth",
    }
)

#: Structural bounds of a snapshot: a wire request with its occurrence mapping
#: and frozen parser inputs is small, so anything past these limits is not one.
_SNAPSHOT_MAX_DEPTH = 64
_SNAPSHOT_MAX_NODES = 4096
_SNAPSHOT_MAX_BYTES = 1024 * 1024


def validate_request_snapshot(snapshot: dict) -> None:
    """
    Refuse a snapshot that carries credentials or a recursive structure.

    The snapshot commits in the same transaction as the reservation and is
    immutable afterwards, so a caller - including a direct helper caller - can
    never widen it into a credential store or nest unbounded data in it.
    Containers are tracked by identity, so a self-referential structure is
    refused instead of hanging, and the traversal is depth- and size-bounded.
    """
    if not isinstance(snapshot, dict):
        msg = "Request snapshot must be a mapping"
        # ruff: ignore[type-check-without-type-error]
        raise ValueError(msg)
    for required in ("method", "url", "payload", "codec", "slots"):
        if required not in snapshot:
            msg = f"Request snapshot is missing {required}"
            raise ValueError(msg)
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(snapshot, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _SNAPSHOT_MAX_NODES or depth > _SNAPSHOT_MAX_DEPTH:
            msg = "Request snapshot is too large"
            raise ValueError(msg)
        if isinstance(current, dict | list):
            if id(current) in seen:
                msg = "Request snapshot is recursive"
                raise ValueError(msg)
            seen.add(id(current))
        if isinstance(current, dict):
            for key, value in current.items():
                if not isinstance(key, str) or key.lower() in _FORBIDDEN_SNAPSHOT_KEYS:
                    msg = "Request snapshot carries a forbidden key"
                    raise ValueError(msg)
                stack.append((value, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif not isinstance(current, str | int | float | bool | None):
            msg = "Request snapshot carries an unsupported value"
            # ruff: ignore[type-check-without-type-error]
            raise ValueError(msg)
    if (
        len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
        > _SNAPSHOT_MAX_BYTES
    ):
        msg = "Request snapshot is too large"
        raise ValueError(msg)


def _validate_stage_request(snapshot, digest, codec, ordered_slots) -> None:
    """Require the stored wire and occurrence mapping to describe the paid request."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import DeliveryUnresolved

    validate_request_snapshot(snapshot)
    expected = [
        {"unit_stage_id": str(stage_id), "source_slot": slot}
        for stage_id, slot in ordered_slots
    ]
    wire = {key: snapshot[key] for key in ("url", "method", "payload")}
    actual_digest = hashlib.sha256(
        json.dumps(wire, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    if (
        snapshot["method"] != "post"
        or not isinstance(snapshot["url"], str)
        or not isinstance(snapshot["payload"], dict)
        or snapshot["codec"] != codec
        or snapshot["slots"] != expected
        or actual_digest != digest
    ):
        msg = "Stage request snapshot does not match the request"
        raise DeliveryUnresolved(msg)


def fallback_profile_configured(
    machinery_settings: dict[str, SettingsDict], *, primary_engine: str = "openrouter"
) -> bool:
    """Whether the project carries the exact fixed fallback pilot route."""
    return (
        resolve_fallback_profile(machinery_settings, primary_engine=primary_engine)
        is not None
    )


def fallback_warning(fallback_engine_name: str, translated: int) -> str:
    """Describe a run that used the fallback engine, without provider detail."""
    return gettext(
        "%(count)d string(s) were translated via %(service)s fallback after "
        "the primary engine's credit balance was spent."
    ) % {"count": translated, "service": fallback_engine_name}


def resolve_fallback_profile(
    machinery_settings: dict[str, SettingsDict],
    *,
    primary_engine: str = "openrouter",
) -> FallbackProfile | None:
    """
    Resolve the fixed fallback route from the primary engine's settings.

    Absent, empty or any non-exact mapping (other engine, other model, extra
    keys) means fallback off. Malformed historical JSON resolves to ``None``
    with zero fallback calls, never to an unexpected route.
    """
    if primary_engine != "openrouter":
        return None
    primary_settings = machinery_settings.get(primary_engine)
    if not isinstance(primary_settings, dict):
        return None
    mapping = primary_settings.get("mt_fallback")
    if not isinstance(mapping, dict) or not mapping:
        return None
    if set(mapping) != {"engine", "routing"}:
        return None
    if mapping.get("engine") != _FALLBACK_ENGINE:
        return None
    routing = mapping.get("routing")
    if not isinstance(routing, dict) or set(routing) != {"*"}:
        return None
    if routing.get("*") != _FALLBACK_MODEL:
        return None
    fallback_settings = machinery_settings.get(_FALLBACK_ENGINE)
    if not isinstance(fallback_settings, dict):
        return None
    # The fallback needs its own usable credentials and language pair; a
    # missing key or unsupported language is an explicit unavailability.
    if not fallback_settings.get("key"):
        return None
    return FallbackProfile(
        engine=_FALLBACK_ENGINE,
        model=_FALLBACK_MODEL,
        profile=FALLBACK_POLICY,
        configuration_digest=configuration_digest(fallback_settings),
    )


def build_fallback_engine_settings(
    machinery_settings: dict[str, SettingsDict],
) -> SettingsDict | None:
    """
    Build the runtime settings block for the fallback engine instance.

    Credentials, base URL and transport come from the fallback engine's own
    effective configuration; the prompt fields (persona, style,
    language_instructions) come from the primary, including explicit empty
    values. Stored settings are never mutated.
    """
    fallback_settings = machinery_settings.get(_FALLBACK_ENGINE)
    primary_settings = machinery_settings.get("openrouter")
    if not isinstance(fallback_settings, dict) or not isinstance(
        primary_settings, dict
    ):
        return None
    engine_settings: SettingsDict = dict(fallback_settings)
    engine_settings["routing"] = {"*": _FALLBACK_MODEL}
    for field in ("_project", "persona", "style", "language_instructions"):
        if field in primary_settings:
            engine_settings[field] = primary_settings[field]
    return engine_settings


def primary_saw_confirmed_credit_refusal(reason_codes: Sequence[str | None]) -> bool:
    """Whether any primary internal request confirmed an insufficient-credit."""
    return FALLBACK_TRIGGER in {code for code in reason_codes if isinstance(code, str)}


def _scope_digest(identities: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(identities), ensure_ascii=False).encode()
    ).hexdigest()


def enroll_mt_credit_scope(
    *,
    run: ProducerRun,
    translation: Translation,
    units: Sequence[Unit],
    operation: ProducerMTOperation | None = None,
    machinery_settings: dict[str, SettingsDict] | None = None,
) -> ProducerMTFallbackAuthorization:
    """
    Freeze the immutable enrolled scope before the first primary call.

    One authorization per ``(run, translation)``: a repeated enrollment of the
    same scope reuses the row, a changed scope under the same authorization is
    refused rather than recorded as a second authorization. The scope is the
    full selected translation scope, not the current chunk.
    """
    profile = None
    if machinery_settings is not None:
        profile = resolve_fallback_profile(machinery_settings)
    primary_engine = (
        operation.payload["engine"]
        if operation is not None and operation.payload.get("engine")
        else "openrouter"
    )
    identities = [
        canonical_identity(
            unit.pk, translation.language.code, _source_decision_revision(unit)
        )
        for unit in units
    ]
    digest = _scope_digest(identities)
    settings_block = (
        machinery_settings.get(primary_engine) if machinery_settings else None
    ) or {}
    with transaction.atomic():
        existing = (
            ProducerMTFallbackAuthorization.objects.select_for_update()
            .filter(run=run, translation=translation)
            .first()
        )
        if existing is not None:
            if existing.scope_digest != digest:
                msg = "MT fallback scope changed under an existing authorization"
                raise ValueError(msg)
            return existing
        return ProducerMTFallbackAuthorization.objects.create(
            run=run,
            translation=translation,
            operation=operation,
            actor=run.actor,
            actor_id_snapshot=run.actor_id,
            project=translation.component.project,
            scope_digest=digest,
            scope_size=len(identities),
            policy=FALLBACK_POLICY,
            primary_engine=primary_engine,
            primary_configuration_digest=configuration_digest(settings_block),
            fallback_engine=profile.engine if profile else "",
            fallback_configuration_digest=(
                profile.configuration_digest if profile else ""
            ),
            fallback_model=profile.model if profile else _FALLBACK_MODEL,
            fallback_profile=profile.profile if profile else FALLBACK_POLICY,
        )


def source_decision_revision(unit: Unit) -> str:
    """Return the immutable source revision pinned by a canonical identity."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import compute_decision_revision

    source_unit = unit.source_unit
    if source_unit is None:
        return compute_decision_revision(unit)
    return compute_decision_revision(source_unit)


def compute_audit_revision(unit: Unit) -> str:
    """Return the immutable audit revision pinned by a stage row."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import compute_decision_revision

    return compute_decision_revision(unit)


def _source_decision_revision(unit: Unit) -> str:
    return source_decision_revision(unit)


def _unit_slots(unit: Unit) -> list[str]:
    return list(unit.get_source_plurals())


def authorize_mt_credit_fallback(
    authorization_id: UUID | str,
) -> ProducerMTFallbackAuthorization:
    """
    Move an observing authorization to authorized, or refuse the promotion.

    The authorization only exists after enrollment; promoting it records the
    standing financial approval for exactly one separate fallback attempt per
    enrolled unit. Calling it twice returns the same row: the second call is a
    no-op, never a second charge. A closed authorization is never reopened, so
    a terminal run cannot authorize a new paid route.
    """
    with transaction.atomic():
        authorization = ProducerMTFallbackAuthorization.objects.select_for_update().get(
            pk=authorization_id
        )
        if authorization.state == "closed":
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.llm_delivery import DeliveryUnresolved

            msg = "MT fallback authorization is closed"
            raise DeliveryUnresolved(msg)
        if authorization.state == "observing":
            authorization.state = "authorized"
            authorization.authorized_at = timezone.now()
            authorization.save(update_fields=["state", "authorized_at"])
        return authorization


def close_mt_credit_fallback(
    authorization_id: UUID | str, *, state: str = "closed"
) -> None:
    """
    Close an authorization once its stage reached a terminal state.

    Closing is the last transition of the state machine: no new reservation,
    send or authorization may follow it, while the already captured receipts,
    answers and membership stay readable.
    """
    with transaction.atomic():
        authorization = ProducerMTFallbackAuthorization.objects.select_for_update().get(
            pk=authorization_id
        )
        if authorization.state == "closed":
            return
        authorization.state = "closed"
        authorization.closed_at = timezone.now()
        authorization.save(update_fields=["state", "closed_at"])


def _lock_units(unit_ids: Sequence[int]) -> None:
    """Shared lock boundary: Unit rows by sorted PK, no provider I/O inside."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit

    if not unit_ids:
        return
    list(
        Unit.objects.select_for_update()
        .filter(pk__in=sorted(set(unit_ids)))
        .order_by("pk")
    )


def _lock_work_rows(identities: Sequence[str]) -> None:
    """Then the canonical work rows of exactly these identities, by sorted key."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.llm_delivery import ProducerLLMWork

    if not identities:
        return
    keys = [semantic_key_for(identity) for identity in identities]
    list(
        ProducerLLMWork.objects.select_for_update()
        .filter(semantic_key__in=keys)
        .order_by("semantic_key")
    )


def _lock_stage_rows(authorization_id: UUID | str) -> None:
    """Then this authorization's unit-stage rows and their canonical work rows."""
    unit_stages = list(
        ProducerMTFallbackUnit.objects.select_for_update()
        .filter(authorization_id=authorization_id)
        .order_by("semantic_key")
    )
    _lock_work_rows([stage.identity for stage in unit_stages])


def enroll_unit_stages(
    authorization: ProducerMTFallbackAuthorization,
    units: Sequence[Unit],
    *,
    dispositions: dict[int, str] | dict[int, list[str]] | None = None,
) -> list[ProducerMTFallbackUnit]:
    """
    Create the immutable unit-stage slots for enrolled units.

    Called once per unit of the enrolled scope: the globally unique canonical
    ``semantic_key`` makes a second slot for the same unit of work impossible
    forever. A unit already enrolled keeps its frozen slot, its dispositions
    and its captured primary answers.

    ``dispositions`` maps a unit id to either one disposition for all its
    slots or the exact per-slot list. It is only used when the slot is created:
    a re-enrollment never rewrites frozen evidence. The default is ``unknown``,
    never an eligibility: a slot becomes eligible only from its own receipt
    evidence or from being provably unreserved inside a credit-stopped scope.
    """
    dispositions = dispositions or {}
    created: list[ProducerMTFallbackUnit] = []
    with transaction.atomic():
        _lock_units([unit.pk for unit in units])
        _lock_stage_rows(authorization.pk)
        for unit in units:
            identity = canonical_identity(
                unit.pk,
                authorization.translation.language.code,
                _source_decision_revision(unit),
            )
            key = semantic_key_for(identity)
            existing = ProducerMTFallbackUnit.objects.filter(semantic_key=key).first()
            if existing is not None:
                if existing.authorization_id != authorization.pk:
                    msg = "Canonical unit work already carries a fallback slot"
                    raise ValueError(msg)
                created.append(existing)
                continue
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.llm_delivery import ProducerLLMWork

            work = ProducerLLMWork.objects.filter(identity=identity).first()
            # Cross-path admission denial: an existing durable primary claim
            # whose exchange is unresolved (unknown, sending or reserved) is
            # not evidence this legacy run may spend on. A received claim is
            # the unit's own recorded primary attempt and stays attached.
            if work is not None and work.exchange.state in {
                "unknown",
                "sending",
                "reserved",
            }:
                msg = "Canonical work already carries an unresolved claim"
                raise ValueError(msg)
            slots = _unit_slots(unit)
            given = dispositions.get(unit.pk, "unknown")
            if isinstance(given, str):
                slot_dispositions = [given for _slot in slots]
            else:
                slot_dispositions = list(given)
                while len(slot_dispositions) < len(slots):
                    slot_dispositions.append("unknown")
            audit_revision = compute_audit_revision(unit)
            created.append(
                ProducerMTFallbackUnit.objects.create(
                    authorization=authorization,
                    identity=identity,
                    semantic_key=key,
                    unit=unit,
                    unit_id_snapshot=unit.pk,
                    source_decision_revision=_source_decision_revision(unit),
                    language_code=authorization.translation.language.code,
                    unit_attempt=None,
                    primary_work=work,
                    audit_revision=audit_revision,
                    target_revision=audit_revision,
                    source_slots=slots,
                    primary_dispositions=slot_dispositions,
                    primary_answers=["" for _slot in slots],
                    eligibility_reason=slot_dispositions[0]
                    if slot_dispositions
                    else "",
                )
            )
    return created


def unit_stage_for_unit(
    authorization: ProducerMTFallbackAuthorization, unit: Unit
) -> ProducerMTFallbackUnit | None:
    """Return a unit's immutable stage row inside its authorization."""
    identity = canonical_identity(
        unit.pk,
        authorization.translation.language.code,
        _source_decision_revision(unit),
    )
    return ProducerMTFallbackUnit.objects.filter(
        authorization_id=authorization.pk,
        identity=identity,
    ).first()


def stage_send_guard(
    authorization: ProducerMTFallbackAuthorization,
    *,
    actor=None,
    mode: str = "translate",
    slots: Sequence[tuple[UUID, int]] = (),
):
    """
    Build the fresh guard a stage send runs immediately before its transport.

    The guard re-reads, under the row lock the reservation already holds, what
    the plan requires before every fallback send: the original actor of the run
    - never the worker's, never a deleted user - through
    ``check_auto_translate_permission``, the run's cancellation and SSO state,
    the translation's readonly and the component's lock, and, for every exact
    member of this request, that its target is still empty and its source and
    audit revisions are still the frozen ones. A change in any of them blocks
    the send instead of paying for a request nobody may apply.

    A bot or add-on actor is a legitimate owner of generated changes and is
    allowed through by ``check_auto_translate_permission`` itself; a deleted or
    permission-less user is not.
    """

    def guard() -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.autotranslate import check_auto_translate_permission

        # ruff: ignore[import-outside-top-level]
        from weblate.trans.llm_delivery import DeliveryUnresolved

        # ruff: ignore[import-outside-top-level]
        from weblate.wizard_auth.grants import guard_paid_run

        authorization.refresh_from_db()
        translation = authorization.translation
        translation.refresh_from_db()
        translation.component.refresh_from_db()
        run = authorization.run
        guard_paid_run(run.pk)
        if run.status in {"cancel_requested", "cancelled", "completed", "failed"}:
            msg = "MT fallback run is no longer active"
            raise DeliveryUnresolved(msg)
        # The original actor is re-read from the run, so a revoked or deleted
        # account blocks the send instead of silently authorizing a charge.
        # ruff: ignore[import-outside-top-level]
        from django.apps import apps

        current_actor = (
            apps.get_model("weblate_auth", "User")
            .objects.filter(pk=authorization.actor_id_snapshot or run.actor_id)
            .first()
            if (authorization.actor_id_snapshot or run.actor_id)
            else None
        )
        if actor is not None and current_actor is None:
            msg = "MT fallback actor no longer exists"
            raise DeliveryUnresolved(msg)
        if translation.is_readonly or translation.component.locked:
            msg = "Translation or component became read-only"
            raise DeliveryUnresolved(msg)
        if not check_auto_translate_permission(
            current_actor if actor is not None else actor, translation, mode
        ):
            msg = "MT fallback actor lost the required permission"
            raise DeliveryUnresolved(msg)
        # Every exact member of this request is re-checked: a target a human
        # wrote in the meantime, or a source that moved, blocks this send.
        for unit_stage_id, _source_slot in slots:
            unit_stage = ProducerMTFallbackUnit.objects.filter(
                pk=str(unit_stage_id)
            ).first()
            if unit_stage is None or unit_stage.unit is None:
                msg_0 = "MT fallback unit no longer exists"
                raise DeliveryUnresolved(msg_0)
            unit = unit_stage.unit
            if any(unit.get_target_plurals()):
                msg = "MT fallback unit already carries a target"
                raise DeliveryUnresolved(msg)
            if unit_stage.source_decision_revision and (
                source_decision_revision(unit) != unit_stage.source_decision_revision
            ):
                msg = "MT fallback source revision changed"
                raise DeliveryUnresolved(msg)
            if unit_stage.audit_revision and (
                compute_audit_revision(unit) != unit_stage.audit_revision
            ):
                msg = "MT fallback audit revision changed"
                raise DeliveryUnresolved(msg)
            if unit.readonly:
                msg = "MT fallback unit became read-only"
                raise DeliveryUnresolved(msg)
        # The frozen policy must still be the one the authorization carries:
        # a switched-off or re-pointed route is never paid through an old one.
        project = authorization.project
        profile = resolve_fallback_profile(
            project.get_machinery_settings(),
            primary_engine=authorization.primary_engine,
        )
        if profile is None or profile.configuration_digest != (
            authorization.fallback_configuration_digest
        ):
            msg = "MT fallback route is no longer configured"
            raise DeliveryUnresolved(msg)

    return guard


def eligible_fallback_slots(
    authorization_id: UUID | str,
) -> list[tuple[ProducerMTFallbackUnit, int, str]]:
    """
    Return the exact ordered eligible ``(unit_stage, source_slot, source_text)`` list.

    Two dispositions are eligible, and only these two:

    ``credit_refused``
        the slot's own primary request received a full qualifying response
        classified as ``REASON_INSUFFICIENT_CREDIT`` (402, a confirmed 403 or
        an upstream 402 inside a 200);
    ``never_sent_due_credit``
        the slot has no primary reservation at all, was inside the enrolled
        scope and the run was stopped by that credit refusal.

    A rate-limit skip, an unknown sending state, a partial, invalid, generic,
    auth or quota outcome, and a successful primary slot are never eligible:
    they are not reclassified because another request saw a credit refusal.
    """
    eligible = {"credit_refused", "never_sent_due_credit"}
    result: list[tuple[ProducerMTFallbackUnit, int, str]] = []
    for unit_stage in ProducerMTFallbackUnit.objects.filter(
        authorization_id=authorization_id
    ).order_by("semantic_key"):
        for index, disposition in enumerate(unit_stage.primary_dispositions):
            if disposition not in eligible:
                continue
            if index >= len(unit_stage.source_slots):
                continue
            result.append((unit_stage, index, unit_stage.source_slots[index]))
    return result


def mark_slot_disposition(
    unit_stage: ProducerMTFallbackUnit,
    source_slot: int,
    disposition: str,
    *,
    answer: str = "",
) -> None:
    """Record the frozen per-slot primary disposition, never widening it."""
    dispositions = list(unit_stage.primary_dispositions)
    answers = list(unit_stage.primary_answers)
    while len(dispositions) <= source_slot:
        dispositions.append("unknown")
        answers.append("")
    dispositions[source_slot] = disposition
    answers[source_slot] = answer
    unit_stage.primary_dispositions = dispositions
    unit_stage.primary_answers = answers
    unit_stage.eligibility_reason = disposition
    unit_stage.save(
        update_fields=["primary_dispositions", "primary_answers", "eligibility_reason"]
    )


def reserve_mt_fallback_exchange(
    authorization_id: UUID | str,
    ordered_slots: Sequence[tuple[UUID, int]],
    request_digest: str,
    *,
    request_snapshot: dict,
    request_codec: str,
    replay_only: bool = False,
    run_id=None,
) -> ExchangeRef:
    """
    Atomically reserve the one exchange for a fallback request.

    The exact ordered slots, the request digest, the codec and the secret-free
    snapshot commit together in one transaction, before any transport. One
    exchange covers every fallback slot of the unit: the slot mapping is
    immutable after reservation. A changed membership, order, digest, codec or
    policy never opens a second exchange.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import DeliveryUnresolved

    unit_stage_ids = {str(slot[0]) for slot in ordered_slots}
    if len(unit_stage_ids) != 1:
        msg = "One fallback exchange covers exactly one unit's slots"
        raise ValueError(msg)
    _validate_stage_request(
        request_snapshot, request_digest, request_codec, ordered_slots
    )
    if not replay_only:
        # A new reservation requires a live, non-closed authorization. A replay
        # of an already captured answer stays readable even after the stage is
        # closed or the policy is switched off.
        authorization = authorize_mt_credit_fallback(authorization_id)
    else:
        authorization = ProducerMTFallbackAuthorization.objects.get(pk=authorization_id)
    expected = [(str(stage_id), slot) for stage_id, slot in ordered_slots]
    with transaction.atomic():
        # Shared lock boundary: Unit rows by sorted PK, then the unit-stage
        # rows, then the canonical work rows of exactly these stages.
        _lock_units(
            list(
                ProducerMTFallbackUnit.objects.filter(
                    pk__in=unit_stage_ids
                ).values_list("unit_id", flat=True)
            )
        )
        _lock_stage_rows(authorization.pk)
        unit_stage = ProducerMTFallbackUnit.objects.select_for_update().get(
            pk=next(iter(unit_stage_ids))
        )
        if unit_stage.authorization_id != authorization.pk:
            msg = "Unit stage does not belong to this authorization"
            raise ValueError(msg)
        if unit_stage.fallback_exchange_id is not None:
            exchange = unit_stage.fallback_exchange
            if exchange.request_digest != request_digest:
                msg = "Changed paid request requires reconciliation"
                raise DeliveryUnresolved(msg)
            if exchange.request_codec and exchange.request_codec != request_codec:
                msg = "Changed paid codec requires reconciliation"
                raise DeliveryUnresolved(msg)
            members = list(
                exchange.stage_members.filter(stage=STAGE_FALLBACK)
                .order_by("position")
                .values_list("unit_stage_id", "source_slot")
            )
            if (
                exchange.request_snapshot != request_snapshot
                or [(str(stage_id), slot) for stage_id, slot in members] != expected
            ):
                msg_0 = "Changed fallback stage requires reconciliation"
                raise DeliveryUnresolved(msg_0)
            if (
                not replay_only
                and exchange.state == "reserved"
                and exchange.lease_until <= timezone.now()
            ):
                exchange.owner = uuid4().hex
                exchange.lease_until = timezone.now() + _FALLBACK_LEASE
                exchange.save(update_fields=["owner", "lease_until", "updated"])
                return ExchangeRef(exchange.pk, authorization.pk, exchange.owner, True)
            return ExchangeRef(exchange.pk, authorization.pk, exchange.owner, False)
        if replay_only:
            msg = "No saved fallback delivery for replay"
            raise DeliveryUnresolved(msg)
        dispositions = unit_stage.primary_dispositions
        eligible = {"credit_refused", "never_sent_due_credit"}
        if (
            unit_stage.unit_id is None
            or len(dispositions) != len(unit_stage.source_slots)
            or any(value not in eligible | {"primary_usable"} for value in dispositions)
            or [slot for _, slot in expected]
            != [index for index, value in enumerate(dispositions) if value in eligible]
            or not expected
        ):
            msg_0 = "Fallback membership is not eligible"
            raise DeliveryUnresolved(msg_0)
        # The canonical identity keeps its own primary claim; a tail without
        # one is claimed here under the real fallback exchange.
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.llm_delivery import (
            ProducerLLMExchange,
            ProducerLLMWork,
        )

        exchange = ProducerLLMExchange.objects.create(
            request_digest=request_digest,
            request_codec=request_codec,
            # A short, deterministic owner token: the authorization row already
            # carries the durable identity, the field only fences the lease.
            owner=uuid4().hex,
            lease_until=timezone.now() + _FALLBACK_LEASE,
            run_id=run_id,
            mt_authorization=authorization,
            mt_stage=STAGE_FALLBACK,
            request_snapshot=request_snapshot,
        )
        if not ProducerLLMWork.objects.filter(identity=unit_stage.identity).exists():
            ProducerLLMWork.objects.create(
                semantic_key=unit_stage.semantic_key,
                identity=unit_stage.identity,
                position=0,
                exchange=exchange,
            )
        for position, (_slot_id, source_slot) in enumerate(ordered_slots):
            ProducerMTStageMember.objects.create(
                unit_stage=unit_stage,
                exchange=exchange,
                stage=STAGE_FALLBACK,
                source_slot=source_slot,
                position=position,
            )
        unit_stage.fallback_exchange = exchange
        unit_stage.fallback_state = "reserved"
        unit_stage.save(update_fields=["fallback_exchange", "fallback_state"])
        return ExchangeRef(exchange.pk, authorization.pk, exchange.owner, True)


#: How long a fallback exchange may be re-sent after a lost lease. Only a
#: provably unsent ``reserved`` fallback may be reclaimed at all.
_FALLBACK_LEASE = timedelta(seconds=900)

#: How long a primary stage exchange may be re-sent after a lost lease. It is
#: the only reason a primary reservation may be reclaimed at all.
_PRIMARY_LEASE = timedelta(seconds=900)


def _stage_slots(
    authorization_id: UUID | str, ordered_slots: Sequence[tuple[UUID, int]]
) -> list[ProducerMTFallbackUnit]:
    """
    Lock and return every unit-stage row these slots belong to.

    A primary batch legitimately spans several enrolled units, so the rows are
    locked by sorted PK inside the caller's transaction and a concurrent
    enrollment or reservation of the same canonical work waits here instead of
    opening a second claim.
    """
    unit_stage_ids = {str(slot[0]) for slot in ordered_slots}
    stages = list(
        ProducerMTFallbackUnit.objects.select_for_update()
        .filter(pk__in=unit_stage_ids)
        .order_by("semantic_key")
    )
    if {str(stage.pk) for stage in stages} != unit_stage_ids:
        msg = "Unit stage does not exist"
        raise ValueError(msg)
    for stage in stages:
        if stage.authorization_id != authorization_id:
            msg = "Unit stage does not belong to this authorization"
            raise ValueError(msg)
    return stages


def attach_durable_primary_exchange(
    exchange, authorization_id, ordered_slots, request_snapshot, identity
) -> None:
    """Attach exact stage evidence inside the canonical reservation transaction."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import DeliveryUnresolved

    _validate_stage_request(
        request_snapshot, exchange.request_digest, exchange.request_codec, ordered_slots
    )
    stages = _stage_slots(authorization_id, ordered_slots)
    by_id = {str(stage.pk): stage for stage in stages}
    expected = [(str(stage_id), slot) for stage_id, slot in ordered_slots]
    if not expected or len(set(expected)) != len(expected):
        msg = "Invalid primary stage membership"
        raise DeliveryUnresolved(msg)
    if any(
        slot < 0 or slot >= len(by_id[stage_id].source_slots)
        for stage_id, slot in expected
    ):
        msg = "Invalid primary source slot"
        raise DeliveryUnresolved(msg)
    if {by_id[stage_id].identity for stage_id, _slot in expected} != set(identity):
        msg = "Primary stage and canonical work differ"
        raise DeliveryUnresolved(msg)
    mapping = [
        {"unit_stage_id": stage_id, "source_slot": slot} for stage_id, slot in expected
    ]
    if request_snapshot.get("slots") != mapping:
        msg = "Primary snapshot and stage membership differ"
        raise DeliveryUnresolved(msg)
    existing = list(
        exchange.stage_members.filter(stage=STAGE_PRIMARY)
        .order_by("position")
        .values_list("unit_stage_id", "source_slot")
    )
    if exchange.mt_authorization_id is not None:
        if (
            str(exchange.mt_authorization_id) != str(authorization_id)
            or exchange.mt_stage != STAGE_PRIMARY
            or exchange.request_snapshot != request_snapshot
            or [(str(stage_id), slot) for stage_id, slot in existing] != expected
        ):
            msg = "Changed primary stage requires reconciliation"
            raise DeliveryUnresolved(msg)
        return
    if exchange.state != "reserved" or existing:
        msg = "Cannot attach an already dispatched primary"
        raise DeliveryUnresolved(msg)
    works = {work.identity: work for work in exchange.work.all()}
    if set(works) != set(identity):
        msg = "Primary canonical work differs"
        raise DeliveryUnresolved(msg)
    exchange.mt_authorization_id = authorization_id
    exchange.mt_stage = STAGE_PRIMARY
    exchange.request_snapshot = request_snapshot
    exchange.save(update_fields=["mt_authorization", "mt_stage", "request_snapshot"])
    for position, (stage_id, slot) in enumerate(expected):
        stage = by_id[stage_id]
        work = works[stage.identity]
        if stage.primary_work_id not in {None, work.pk}:
            msg = "Primary work changed"
            raise DeliveryUnresolved(msg)
        stage.primary_work = work
        stage.save(update_fields=["primary_work"])
        ProducerMTStageMember.objects.create(
            unit_stage=stage,
            exchange=exchange,
            stage=STAGE_PRIMARY,
            source_slot=slot,
            position=position,
        )


def reserve_mt_primary_exchange(
    authorization_id: UUID | str,
    ordered_slots: Sequence[tuple[UUID, int]],
    request_digest: str,
    *,
    request_snapshot: dict,
    request_codec: str,
    replay_only: bool = False,
    run_id=None,
) -> ExchangeRef:
    """
    Reserve the one exchange that carries a primary stage request.

    The primary stage is an observation, not a second charge: the exchange
    created here is the receipt of an internal primary request, and its
    membership is the exact ordered source slots of that request. A changed
    membership, order, digest or codec never opens a second exchange; only a
    provably unsent ``reserved`` exchange may be reclaimed after its lease.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import DeliveryUnresolved

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.llm_delivery import ProducerLLMExchange, ProducerLLMWork

    _validate_stage_request(
        request_snapshot, request_digest, request_codec, ordered_slots
    )
    with transaction.atomic():
        _lock_units(
            list(
                ProducerMTFallbackUnit.objects.filter(
                    pk__in={str(slot[0]) for slot in ordered_slots}
                ).values_list("unit_id", flat=True)
            )
        )
        unit_stages = _stage_slots(authorization_id, ordered_slots)
        identities = [stage.identity for stage in unit_stages]
        _lock_work_rows(identities)
        expected = [(str(stage_id), slot) for stage_id, slot in ordered_slots]
        stages_by_id = {str(stage.pk): stage for stage in unit_stages}
        if (
            not expected
            or len(set(expected)) != len(expected)
            or any(
                slot < 0 or slot >= len(stages_by_id[stage_id].source_slots)
                for stage_id, slot in expected
            )
        ):
            msg = "Invalid primary stage membership"
            raise DeliveryUnresolved(msg)
        if request_snapshot.get("slots") != [
            {"unit_stage_id": stage_id, "source_slot": slot}
            for stage_id, slot in expected
        ]:
            msg = "Primary snapshot and membership differ"
            raise DeliveryUnresolved(msg)
        if any(stage.state_blocks_primary for stage in unit_stages):
            msg = "Fallback already claims primary work"
            raise DeliveryUnresolved(msg)
        if ProducerLLMWork.objects.filter(identity__in=identities).exists():
            msg = "Canonical work already has a paid claim"
            raise DeliveryUnresolved(msg)
        stage_ids = [stage.pk for stage in unit_stages]
        # Only overlapping source slots share a request. Other slots of the
        # same plural unit may legitimately belong to another primary exchange.
        existing = list(
            ProducerMTStageMember.objects.filter(
                unit_stage_id__in=stage_ids, stage=STAGE_PRIMARY
            ).order_by("exchange_id", "position")
        )
        requested = set(expected)
        existing = [
            member
            for member in existing
            if (str(member.unit_stage_id), member.source_slot) in requested
        ]
        if existing:
            exchange_ids = {member.exchange_id for member in existing}
            if len(exchange_ids) != 1:
                msg = "Overlapping primary stage work requires reconciliation"
                raise DeliveryUnresolved(msg)
            saved = ProducerLLMExchange.objects.select_for_update().get(
                pk=existing[0].exchange_id
            )
            if saved.request_digest != request_digest:
                msg = "Changed paid request requires reconciliation"
                raise DeliveryUnresolved(msg)
            if saved.request_codec and saved.request_codec != request_codec:
                msg = "Changed paid codec requires reconciliation"
                raise DeliveryUnresolved(msg)
            if (
                saved.mt_authorization_id != unit_stages[0].authorization_id
                or saved.request_snapshot != request_snapshot
                or [
                    (str(member.unit_stage_id), member.source_slot)
                    for member in existing
                ]
                != expected
            ):
                msg_0 = "Changed primary stage requires reconciliation"
                raise DeliveryUnresolved(msg_0)
            if replay_only:
                return ExchangeRef(saved.pk, authorization_id, saved.owner, False)
            if saved.state == "reserved" and saved.lease_until <= timezone.now():
                saved.owner = uuid4().hex
                saved.lease_until = timezone.now() + _PRIMARY_LEASE
                saved.save(update_fields=["owner", "lease_until", "updated"])
                return ExchangeRef(saved.pk, authorization_id, saved.owner, True)
            return ExchangeRef(saved.pk, authorization_id, saved.owner, False)
        if replay_only:
            msg = "No saved primary delivery for replay"
            raise DeliveryUnresolved(msg)
        exchange = ProducerLLMExchange.objects.create(
            request_digest=request_digest,
            request_codec=request_codec,
            owner=uuid4().hex,
            lease_until=timezone.now() + _PRIMARY_LEASE,
            run_id=run_id,
            mt_authorization=ProducerMTFallbackAuthorization.objects.get(
                pk=unit_stages[0].authorization_id
            ),
            mt_stage=STAGE_PRIMARY,
            request_snapshot=request_snapshot,
        )
        # One member per wire position, in request order: every slot of every
        # enrolled unit this request actually sends.
        for position, (slot_id, source_slot) in enumerate(ordered_slots):
            unit_stage = next(
                stage for stage in unit_stages if str(stage.pk) == str(slot_id)
            )
            ProducerMTStageMember.objects.create(
                unit_stage=unit_stage,
                exchange=exchange,
                stage=STAGE_PRIMARY,
                source_slot=source_slot,
                position=position,
            )
        return ExchangeRef(exchange.pk, authorization_id, exchange.owner, True)


def dispatch_mt_primary_exchange(
    authorization_id: UUID | str,
    ordered_slots: Sequence[tuple[UUID, int]],
    request_digest: str,
    send: Callable[[], str],
    *,
    request_snapshot: dict,
    request_codec: str,
    replay_only: bool = False,
    before_send: Callable[[], None] | None = None,
    run_id=None,
) -> MTStageDispatchResult:
    """
    Observe one internal primary request through the shared delivery lifecycle.

    The exact ordered slots, the request digest, the codec and the secret-free
    snapshot of the actual wire request commit in one transaction before any
    transport, so the primary 402 is durable evidence instead of a batch-wide
    inference. An exact replay returns the same exchange and body without a
    second send.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import dispatch_reserved_exchange, read_response

    reference = reserve_mt_primary_exchange(
        authorization_id,
        ordered_slots,
        request_digest,
        request_snapshot=request_snapshot,
        request_codec=request_codec,
        replay_only=replay_only,
        run_id=run_id,
    )
    if not reference.can_send:
        body = read_response(reference.id)
        return MTStageDispatchResult(reference.id, reference.authorization_id, body)
    body = dispatch_reserved_exchange(
        reference.id, reference.owner, send, before_send=before_send
    )
    return MTStageDispatchResult(reference.id, reference.authorization_id, body)


def dispatch_mt_fallback_exchange(
    authorization_id: UUID | str,
    ordered_slots: Sequence[tuple[UUID, int]],
    request_digest: str,
    send: Callable[[], str],
    *,
    request_snapshot: dict,
    request_codec: str,
    replay_only: bool = False,
    before_send: Callable[[], None] | None = None,
    run_id=None,
) -> MTStageDispatchResult:
    """
    Reserve, then send, through the shared delivery lifecycle.

    The snapshot commits before any transport, so a crash between reservation
    and send still leaves the frozen wire request and its occurrence mapping.
    An exact replay returns the same exchange and body without a second send
    or a second usage record.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.llm_delivery import dispatch_reserved_exchange, read_response

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.llm_delivery import ProducerLLMExchange

    reference = reserve_mt_fallback_exchange(
        authorization_id,
        ordered_slots,
        request_digest,
        request_snapshot=request_snapshot,
        request_codec=request_codec,
        replay_only=replay_only,
        run_id=run_id,
    )
    if not reference.can_send:
        body = read_response(reference.id)
        return MTStageDispatchResult(reference.id, reference.authorization_id, body)
    try:
        body = dispatch_reserved_exchange(
            reference.id, reference.owner, send, before_send=before_send
        )
    except BaseException:
        state = ProducerLLMExchange.objects.values_list("state", flat=True).get(
            pk=reference.id
        )
        if state in {"received", "unknown"}:
            ProducerMTFallbackUnit.objects.filter(
                fallback_exchange_id=reference.id
            ).update(fallback_state=state)
        raise
    return MTStageDispatchResult(reference.id, reference.authorization_id, body)


def recover_mt_stage_answers(authorization_id: UUID | str) -> StageRecoveryResult:
    """
    Read the persisted stage answers without sending anything.

    Returns the frozen per-slot primary answers and the fallback answer once
    one was received. A slot whose evidence is missing or unusable is reported
    as such; recovery never invents an answer and never pays again.
    """
    from weblate.trans.mt_fallback_replay import (  # ruff: ignore[import-outside-top-level]
        replay_mt_stage_receipts,
    )

    replay_mt_stage_receipts(authorization_id)
    answers: list[StageRecoveryAnswer] = []
    fallback_state = "none"
    for unit_stage in ProducerMTFallbackUnit.objects.filter(
        authorization_id=authorization_id
    ).order_by("semantic_key"):
        if unit_stage.fallback_answer:
            fallback_state = unit_stage.fallback_state
            answers.append(
                StageRecoveryAnswer(
                    unit_id=unit_stage.unit_id_snapshot,
                    identity=unit_stage.identity,
                    stage="fallback",
                    text=unit_stage.fallback_answer,
                    usable=True,
                )
            )
            continue
        for index, disposition in enumerate(unit_stage.primary_dispositions):
            answer = (
                unit_stage.primary_answers[index]
                if index < len(unit_stage.primary_answers)
                else ""
            )
            if disposition == "primary_usable" and answer:
                answers.append(
                    StageRecoveryAnswer(
                        unit_id=unit_stage.unit_id_snapshot,
                        identity=unit_stage.identity,
                        stage="primary",
                        text=answer,
                        usable=True,
                    )
                )
            elif disposition == "credit_refused":
                answers.append(
                    StageRecoveryAnswer(
                        unit_id=unit_stage.unit_id_snapshot,
                        identity=unit_stage.identity,
                        stage="primary",
                        text="",
                        usable=False,
                    )
                )
    return StageRecoveryResult(
        authorization_id=authorization_id
        if isinstance(authorization_id, UUID)
        else UUID(str(authorization_id)),
        answers=answers,
        fallback_state=fallback_state,
    )


def save_fallback_answer(
    authorization_id: UUID | str, answers: dict[str, list[str]]
) -> None:
    """Persist the received fallback answer vector per canonical identity."""
    with transaction.atomic():
        for unit_stage in ProducerMTFallbackUnit.objects.filter(
            authorization_id=authorization_id
        ):
            vector = answers.get(unit_stage.identity)
            if vector is None:
                continue
            if (
                not isinstance(vector, list)
                or len(vector) != len(unit_stage.source_slots)
                or any(not isinstance(item, str) or not item for item in vector)
            ):
                msg = "Fallback answer must be a complete plural vector"
                raise ValueError(msg)
            unit_stage.fallback_answer = join_plural(vector)
            unit_stage.fallback_state = "received"
            unit_stage.save(update_fields=["fallback_answer", "fallback_state"])


def mt_fallback_run_summary(run) -> dict | None:
    """
    Additive provenance of one run's fallback stage, or ``None``.

    Read from the stage journal, so the numbers describe the same financial
    boundary the run used: primary and fallback engine, fallback model, the
    unit ids the stage covered, and the requested, usable, applied and
    suggested counts. No prompt, receipt or credential is included.
    """
    authorizations = list(
        ProducerMTFallbackAuthorization.objects.filter(run=run).order_by("created")
    )
    if not authorizations:
        return None
    unit_stages = list(
        ProducerMTFallbackUnit.objects.filter(
            authorization_id__in=[authorization.pk for authorization in authorizations]
        )
    )
    requested_stages = [
        unit_stage
        for unit_stage in unit_stages
        if unit_stage.fallback_exchange_id is not None
    ]
    unit_ids = {unit_stage.unit_id_snapshot for unit_stage in requested_stages}
    requested = ProducerMTStageMember.objects.filter(
        unit_stage__in=requested_stages, stage=STAGE_FALLBACK
    ).count()
    usable = sum(
        unit_stage.fallback_state in {"received", "applied", "suggested"}
        for unit_stage in requested_stages
    )
    applied = sum(
        unit_stage.fallback_state == "applied" for unit_stage in requested_stages
    )
    suggested = sum(
        unit_stage.fallback_state == "suggested" for unit_stage in requested_stages
    )
    return {
        "primary_engine": authorizations[0].primary_engine,
        "fallback_engine": authorizations[0].fallback_engine,
        "fallback_model": authorizations[0].fallback_model,
        "fallback_unit_ids": sorted(unit_ids),
        "requested": requested,
        "usable": usable,
        "applied": applied,
        "suggested": suggested,
    }
