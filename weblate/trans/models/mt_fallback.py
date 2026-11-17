# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Audited MT credit-fallback stage: one separate LiteLLM budget after confirmed credit.

The financial boundary of the fallback lives here, not in the orchestrators.
Three models and one journal carry it:

``ProducerMTFallbackAuthorization``
    The standing, immutable authorization for one fallback stage launch. It is
    created only from persisted evidence that the primary engine confirmed an
    ``insufficient-credit`` refusal, and it pins the exact enrolled scope so a
    later scope change can never silently widen a paid request.

``ProducerMTFallbackUnit``
    One immutable, globally canonical financial slot per canonical unit of work.
    Its ``semantic_key`` is the SHA-256 of the same canonical
    ``mt:{unit}:{language}:{source_decision_revision}`` identity the primary
    journal uses, so policy version, replacement unit-attempts and run keys can
    never open a second slot. The row keeps the frozen ordered source slots with
    their per-slot primary disposition and any privately captured primary
    answer, plus the fallback answer once one was received.

``ProducerMTStageMember``
    The exact membership of one internal HTTP request in one stage: which
    unit-stage slot of which authorization was asked at which request position.
    This is a mapping, never a new financial identity, and it is immutable once
    the exchange is reserved.

Historical rows without these links keep their previous semantics and are never
granted a fallback retroactively.
"""

from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.db import models

#: Policy identifier pinned on every authorization row.
FALLBACK_POLICY = "openrouter-credit-to-litellm-qwen37-none-v1"

STAGE_PRIMARY = "primary"
STAGE_FALLBACK = "fallback"

OBSERVING = "observing"
AUTHORIZED = "authorized"
CLOSED = "closed"


def canonical_identity(unit_id: int, language_code: str, source_decision_revision: str):
    """Return the one canonical financial identity of a unit of MT work."""
    return f"mt:{unit_id}:{language_code}:{source_decision_revision}"


def semantic_key_for(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


class ProducerMTFallbackAuthorization(models.Model):
    """
    Immutable standing authorization for one fallback stage launch.

    Unique per ``(run, translation)``: one enrollment per run and translation,
    so a repeated redelivery reuses the same row instead of opening a second
    paid route. The enrolled scope digest is frozen at enrollment; a changed
    scope under the same authorization is refused rather than recorded as a
    second authorization.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        "trans.ProducerRun", on_delete=models.PROTECT, related_name="mt_fallbacks"
    )
    translation = models.ForeignKey(
        "trans.Translation", on_delete=models.PROTECT, related_name="mt_fallbacks"
    )
    operation = models.ForeignKey(
        "trans.ProducerMTOperation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mt_fallbacks",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mt_fallback_authorizations",
    )
    actor_id_snapshot = models.IntegerField(null=True, blank=True)
    project = models.ForeignKey(
        "trans.Project", on_delete=models.PROTECT, related_name="mt_fallbacks"
    )
    #: Frozen enrolled scope: sorted canonical unit identities.
    scope_digest = models.CharField(max_length=64)
    scope_size = models.PositiveIntegerField()
    policy = models.CharField(max_length=64, default=FALLBACK_POLICY)
    primary_engine = models.CharField(max_length=50)
    primary_configuration_digest = models.CharField(max_length=64)
    fallback_engine = models.CharField(max_length=50)
    fallback_configuration_digest = models.CharField(max_length=64)
    fallback_model = models.CharField(max_length=100)
    fallback_profile = models.CharField(max_length=64, default=FALLBACK_POLICY)
    state = models.CharField(
        max_length=20,
        choices=[
            (OBSERVING, "Observing"),
            (AUTHORIZED, "Authorized"),
            (CLOSED, "Closed"),
        ],
        default=OBSERVING,
    )
    authorized_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "trans"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["run", "translation"], name="mt_fallback_run_translation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.primary_engine}->{self.fallback_engine}:{self.state}"


class ProducerMTFallbackUnit(models.Model):
    """
    One immutable financial slot per canonical unit of work, forever.

    The ``semantic_key`` is the SHA-256 of the canonical primary identity, so
    the same unit of work can never receive a second fallback slot under a new
    policy version, a replacement unit-attempt, another run key or another
    operation. The frozen source slots and per-slot dispositions describe the
    exact primary evidence; the fallback fields stay empty until a fallback
    answer was actually received for this slot.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    authorization = models.ForeignKey(
        ProducerMTFallbackAuthorization,
        on_delete=models.PROTECT,
        related_name="fallback_units",
    )
    identity = models.CharField(max_length=500)
    semantic_key = models.CharField(max_length=64, unique=True)
    unit = models.ForeignKey(
        "trans.Unit", on_delete=models.SET_NULL, null=True, blank=True
    )
    unit_id_snapshot = models.IntegerField()
    source_decision_revision = models.CharField(max_length=64)
    language_code = models.CharField(max_length=32)
    unit_attempt = models.ForeignKey(
        "trans.ProducerMTUnitAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fallback_unit_slots",
    )
    primary_work = models.ForeignKey(
        "trans.ProducerLLMWork",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fallback_unit_slots",
    )
    audit_revision = models.CharField(max_length=64, blank=True)
    target_revision = models.CharField(max_length=64, blank=True)
    #: Frozen ordered source texts (plural forms) as sent to the primary engine.
    source_slots = models.JSONField(default=list)
    #: Per-slot primary disposition, aligned with ``source_slots``.
    primary_dispositions = models.JSONField(default=list)
    #: Privately captured usable primary answers per slot, if any.
    primary_answers = models.JSONField(default=list)
    eligibility_reason = models.CharField(max_length=64, blank=True)
    fallback_exchange = models.ForeignKey(
        "trans.ProducerLLMExchange",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fallback_unit_slots",
    )
    fallback_state = models.CharField(max_length=20, default="none")
    fallback_answer = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    #: Stage states that already own this unit of work, so no other route -
    #: legacy enrollment or an ordinary primary reservation - may claim it.
    BLOCKING_STATES = frozenset(
        {"reserved", "sending", "received", "applied", "suggested", "unknown"}
    )

    class Meta:
        app_label = "trans"

    def __str__(self) -> str:
        return self.identity

    @property
    def state_blocks_primary(self) -> bool:
        """Whether this stage row already owns the unit's canonical work."""
        return self.fallback_state in self.BLOCKING_STATES


class ProducerMTStageMember(models.Model):
    """
    Exact membership of one internal HTTP request in one stage.

    Unique per ``(unit_stage, stage, source_slot)``: a slot is asked at most
    once per stage. Unique per ``(exchange, position)``: one request position
    carries at most one slot. One fallback exchange covers all fallback slots
    of a unit; the mapping is immutable after reservation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit_stage = models.ForeignKey(
        ProducerMTFallbackUnit,
        on_delete=models.PROTECT,
        related_name="stage_members",
    )
    exchange = models.ForeignKey(
        "trans.ProducerLLMExchange",
        on_delete=models.PROTECT,
        related_name="stage_members",
    )
    stage = models.CharField(
        max_length=20,
        choices=[
            (STAGE_PRIMARY, "Primary"),
            (STAGE_FALLBACK, "Fallback"),
        ],
    )
    source_slot = models.PositiveSmallIntegerField()
    position = models.PositiveSmallIntegerField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "trans"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["unit_stage", "stage", "source_slot"],
                name="mt_stage_member_slot_unique",
            ),
            models.UniqueConstraint(
                fields=["exchange", "position"], name="mt_stage_member_position_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit_stage.identity}:{self.stage}:{self.source_slot}"
