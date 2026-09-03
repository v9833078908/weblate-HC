# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import (
    Case,
    CharField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import MD5
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import gettext, gettext_lazy

from weblate.trans.actions import ActionEvents
from weblate.trans.models.unit import Unit
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_TRANSLATED,
    StringState,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from weblate.auth.models import User
    from weblate.glossary.models import GlossaryPromptEntry
else:
    from collections.abc import Mapping

JUDGE_ERROR_SEPARATOR = " | "
JUDGE_REPAIR_REQUIREMENT = (
    "Fix all listed errors while preserving the remaining meaning, placeholders, and "
    "markup."
)

# These are deliberately limited to request-shape and resolved-profile data.
# In particular, do not add request text, response text, credentials, or full
# URLs here: JudgeRun is a durable report visible to the producer who started
# it. ``fallback_hostname`` is a narrow exception: a bare hostname (no
# scheme, path or query) identifies which provider served a batch without
# recording the request URL.
_SAFE_CONFIGURATION_SNAPSHOT_KEYS = frozenset(
    {
        "provider",
        "endpoint_fingerprint",
        "model",
        "upstream_model",
        "alias_revision",
        "model_fingerprint",
        "profile_fingerprint",
        "response_format",
        "reasoning",
        "stream",
        "temperature",
        "request_deadline",
        "prompt_schema_version",
        "fallback_hostname",
        "fallback_model",
        "fallback_reasoning",
        "fallback_response_format",
    }
)


def safe_configuration_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    """Return a serializable, non-secret Judge configuration snapshot."""
    unknown = set(snapshot) - _SAFE_CONFIGURATION_SNAPSHOT_KEYS
    if unknown:
        msg = f"Unsafe Judge configuration snapshot keys: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    # Round-trip so callers cannot retain a mutable nested value after it was
    # assigned to the model instance.
    return json.loads(json.dumps(dict(snapshot)))


def _digest(parts: Sequence[str]) -> str:
    """
    Hash a sequence unambiguously.

    JSON encoding of the whole list keeps element boundaries, so a form
    containing the separator cannot forge another form's digest.
    """
    payload = json.dumps(list(parts), ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class JudgeCandidateError(Exception):
    """Malformed or untrusted judge repair candidate metadata."""


_CANDIDATE_METADATA_SCHEMA = 1
_CANDIDATE_METADATA_FIELDS = (
    "kind",
    "schema",
    "judge_verdict_id",
    "judge_run_id",
    "target_hash",
    "context_hash",
    "engine",
)


@dataclass(frozen=True, slots=True)
class JudgeCandidateMetadata:
    """
    The closed metadata contract carried in ``Suggestion.userdetails``.

    Identifiers, hashes, and route identity only: never prompts, responses,
    credentials, or endpoint URLs (invariant 7). The constructor validates
    strictly so a row written before a schema change can never be mistaken
    for a live candidate.
    """

    verdict_id: int
    run_id: uuid.UUID
    target_hash: str
    context_hash: str
    engine: str

    def __init__(self, details: Mapping[str, object]) -> None:
        if not isinstance(details, Mapping):
            msg = "metadata must be an object"
            raise JudgeCandidateError(msg)
        if set(details) != set(_CANDIDATE_METADATA_FIELDS):
            msg = "metadata keys do not match the contract"
            raise JudgeCandidateError(msg)
        if details["kind"] != "judge-repair":
            msg = "unknown candidate kind"
            raise JudgeCandidateError(msg)
        schema = details["schema"]
        # `1.0` and `True` both compare equal to 1 in Python; neither is a
        # valid schema version under the closed contract.
        if (
            not isinstance(schema, int)
            or isinstance(schema, bool)
            or schema != _CANDIDATE_METADATA_SCHEMA
        ):
            msg = "unsupported candidate schema"
            raise JudgeCandidateError(msg)
        verdict_id = details["judge_verdict_id"]
        if not isinstance(verdict_id, int) or isinstance(verdict_id, bool):
            msg = "judge_verdict_id must be an integer"
            raise JudgeCandidateError(msg)
        run_id = details["judge_run_id"]
        if isinstance(run_id, uuid.UUID):
            parsed_run = run_id
        else:
            try:
                parsed_run = uuid.UUID(str(run_id))
            except (AttributeError, TypeError, ValueError) as error:
                msg = "judge_run_id must be a UUID"
                raise JudgeCandidateError(msg) from error
        for field in ("target_hash", "context_hash"):
            value = details[field]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                msg = f"{field} must be a hex sha256"
                raise JudgeCandidateError(msg)
        engine = details["engine"]
        if not isinstance(engine, str) or not engine or len(engine) > 100:
            msg = "engine must be a non-empty identifier"
            raise JudgeCandidateError(msg)
        object.__setattr__(self, "verdict_id", verdict_id)
        object.__setattr__(self, "run_id", parsed_run)
        object.__setattr__(self, "target_hash", details["target_hash"])
        object.__setattr__(self, "context_hash", details["context_hash"])
        object.__setattr__(self, "engine", engine)

    @classmethod
    def parse(cls, details: object) -> JudgeCandidateMetadata | None:
        """Return a candidate or None: unknown metadata is a normal suggestion."""
        if not isinstance(details, Mapping) or details.get("kind") != "judge-repair":
            return None
        return cls(details)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": "judge-repair",
            "schema": _CANDIDATE_METADATA_SCHEMA,
            "judge_verdict_id": self.verdict_id,
            "judge_run_id": str(self.run_id),
            "target_hash": self.target_hash,
            "context_hash": self.context_hash,
            "engine": self.engine,
        }


def compute_target_hash(target: Sequence[str]) -> str:
    """Hash every plural form of a target."""
    return _digest(target)


def compute_target_storage_hash(target: str) -> str:
    return hashlib.md5(target.encode(), usedforsecurity=False).hexdigest()


def compute_context_hash(
    *,
    source: str,
    note: str,
    explanation: str,
    glossary_terms: Iterable[Mapping[str, object]],
) -> str:
    """
    Hash source, note, explanation and every prompt-visible glossary-entry field.

    Neither mapping key order nor glossary order is context, so keys and
    serialized entries are both sorted: a reordered glossary must not
    invalidate a verdict. Entry content and multiplicity are context.
    """
    terms = sorted(
        json.dumps(
            dict(entry),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for entry in glossary_terms
    )
    return _digest([source, note, explanation, *terms])


def compute_judge_request_identity(
    *,
    unit_id: int,
    target_hash: str,
    context_hash: str,
    project_context_hash: str,
    source_language: str,
    target_language: str,
    profile_fingerprint: str,
    prompt_schema_version: str,
) -> str:
    """
    Return the stable, text-free identity of one seat's judge request.

    A seat is intentionally not part of this digest: the same canonical
    request can be deferred independently by each seat, while
    ``JudgeDeferral`` keeps the seat as a separate, queryable field.
    """
    return _digest(
        [
            str(unit_id),
            target_hash,
            context_hash,
            project_context_hash,
            source_language,
            target_language,
            profile_fingerprint,
            prompt_schema_version,
        ]
    )


class JudgeRun(models.Model):
    """One permission-checked producer launch across one closed scope."""

    class ScopeType(models.TextChoices):
        TRANSLATION = "translation"
        COMPONENT = "component"
        PROJECT = "project"
        WORKSPACE = "workspace"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="judge_runs",
    )
    task_id = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(null=True, blank=True)
    finished = models.DateTimeField(null=True, blank=True)
    scope_type = models.CharField(max_length=20, choices=ScopeType)
    scope_id = models.CharField(max_length=64)
    scope_label = models.CharField(max_length=255)
    scope_path = models.CharField(max_length=500)
    requested_query = models.TextField(blank=True)
    requested_mode = models.CharField(max_length=20)
    cap = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20, choices=Status, default=Status.QUEUED, db_index=True
    )
    summary = models.JSONField(default=dict, blank=True)
    next_request_round = models.PositiveIntegerField(default=0)
    failure = models.TextField(blank=True)
    warnings = models.JSONField(default=list, blank=True)
    configuration_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge run")
        verbose_name_plural = gettext_lazy("Judge runs")
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(fields=["actor", "-created"], name="judge_run_actor_idx"),
            models.Index(
                fields=["scope_type", "scope_id", "-created"],
                name="judge_run_scope_idx",
            ),
            models.Index(fields=["status", "-created"], name="judge_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.scope_type}: {self.scope_label}"

    def save(self, *args, **kwargs) -> None:
        self.configuration_snapshot = safe_configuration_snapshot(
            self.configuration_snapshot
        )
        super().save(*args, **kwargs)


class JudgeRequestAttempt(models.Model):
    """
    One outbound Judge HTTP request, including calls without a response body.

    This is deliberately an observability record rather than a transcript.
    All text-bearing request and response material stays outside this model.
    """

    class FailureKind(models.TextChoices):
        TRANSPORT = "transport"
        DEADLINE = "deadline"
        RESPONSE_TOO_LARGE = "response-too-large"
        HTTP_AUTH = "http-auth"
        HTTP_RATE_LIMIT = "http-rate-limit"
        HTTP_SERVER = "http-server"
        HTTP_REQUEST_INVALID = "http-request-invalid"
        HTTP_OTHER = "http-other"
        EMPTY_RESPONSE = "empty-response"
        INVALID_JSON = "invalid-json"
        INVALID_ENVELOPE = "invalid-envelope"
        SEGMENT_COUNT = "segment-count"
        INVALID_SEGMENT = "invalid-segment"
        FINISH_LENGTH = "finish-length"
        UNKNOWN = "unknown"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    run = models.ForeignKey(
        JudgeRun,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="request_attempts",
    )
    seat = models.PositiveSmallIntegerField()
    attempt = models.PositiveSmallIntegerField(default=0)
    provider = models.CharField(max_length=32, blank=True)
    endpoint_fingerprint = models.CharField(max_length=64)
    model = models.CharField(max_length=200)
    model_fingerprint = models.CharField(max_length=64, blank=True)
    profile_fingerprint = models.CharField(max_length=64)
    prompt_schema_version = models.CharField(max_length=64)
    batch_digest = models.CharField(max_length=64)
    batch_size = models.PositiveSmallIntegerField()
    transport_succeeded = models.BooleanField(default=False)
    parsed = models.BooleanField(default=False)
    failure_kind = models.CharField(
        max_length=24, choices=FailureKind, blank=True, db_index=True
    )
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    exception_class = models.CharField(max_length=255, blank=True)
    finish_reason = models.CharField(max_length=64, blank=True)
    response_shape = models.CharField(max_length=64, blank=True)
    response_segment_count = models.PositiveSmallIntegerField(null=True, blank=True)
    elapsed_ms = models.PositiveIntegerField(null=True, blank=True)
    first_byte_ms = models.PositiveIntegerField(null=True, blank=True)
    response_bytes = models.PositiveIntegerField(null=True, blank=True)
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    reasoning_tokens = models.PositiveIntegerField(null=True, blank=True)
    response_id = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge request attempt")
        verbose_name_plural = gettext_lazy("Judge request attempts")
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(
                fields=["endpoint_fingerprint", "model", "seat", "-created_at"],
                name="judge_attempt_seat_recent_idx",
            ),
            models.Index(
                fields=["failure_kind", "-created_at"],
                name="judge_attempt_failure_idx",
            ),
            models.Index(fields=["run", "-created_at"], name="judge_attempt_run_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.model} seat {self.seat} attempt {self.pk}"


class JudgeAdaptiveState(models.Model):
    """Shared adaptive batching, circuit-breaker, and token-bucket state."""

    class CircuitState(models.TextChoices):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half-open"
        OPERATOR_STOPPED = "operator-stopped"

    endpoint_fingerprint = models.CharField(max_length=64)
    model = models.CharField(max_length=200)
    seat = models.PositiveSmallIntegerField()
    batch_budget = models.PositiveSmallIntegerField()
    clean_attempt_streak = models.PositiveSmallIntegerField(default=0)
    failure_streak = models.PositiveSmallIntegerField(default=0)
    last_failure_kind = models.CharField(
        max_length=24, choices=JudgeRequestAttempt.FailureKind, blank=True
    )
    circuit_state = models.CharField(
        max_length=20, choices=CircuitState, default=CircuitState.CLOSED
    )
    circuit_opened_at = models.DateTimeField(null=True, blank=True)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    token_bucket_capacity = models.PositiveIntegerField(default=0)
    token_bucket_available = models.DecimalField(
        max_digits=18, decimal_places=6, default=0
    )
    token_bucket_refill_per_second = models.DecimalField(
        max_digits=18, decimal_places=6, default=0
    )
    token_bucket_updated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Judge adaptive state")
        verbose_name_plural = gettext_lazy("Judge adaptive states")
        # ruff: ignore[mutable-class-default]
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint_fingerprint", "model", "seat"],
                name="judge_adaptive_state_identity",
            )
        ]

    def __str__(self) -> str:
        return f"{self.model} seat {self.seat}"


class JudgeDeferral(models.Model):
    """A durable, per-seat retry record for a request without an opinion."""

    class State(models.TextChoices):
        QUEUED = "queued"
        SLOW = "slow"
        CLOSED = "closed"

    unit = models.ForeignKey(
        "trans.Unit",
        on_delete=models.deletion.CASCADE,
        related_name="judge_deferrals",
    )
    request_identity = models.CharField(max_length=64)
    target_hash = models.CharField(max_length=64)
    context_hash = models.CharField(max_length=64)
    project_context_hash = models.CharField(max_length=64)
    source_language = models.CharField(max_length=32)
    target_language = models.CharField(max_length=32)
    profile_fingerprint = models.CharField(max_length=64)
    prompt_schema_version = models.CharField(max_length=64)
    seat = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=10, choices=State, default=State.QUEUED, db_index=True
    )
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    last_failure_kind = models.CharField(
        max_length=24, choices=JudgeRequestAttempt.FailureKind, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.CharField(max_length=64, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge deferral")
        verbose_name_plural = gettext_lazy("Judge deferrals")
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(
                fields=["state", "next_attempt_at"],
                name="judge_deferral_ready_idx",
            ),
            models.Index(
                fields=["state", "claim_expires_at"],
                name="judge_deferral_claim_idx",
            ),
            models.Index(
                fields=["unit", "seat", "-created_at"],
                name="judge_deferral_unit_idx",
            ),
            models.Index(
                fields=["state", "closed_at"],
                name="judge_deferral_closed_idx",
            ),
        ]
        # ruff: ignore[mutable-class-default]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "seat", "request_identity"],
                name="judge_deferral_identity",
            )
        ]

    def __str__(self) -> str:
        return f"{self.unit_id} seat {self.seat}: {self.state}"


class JudgeRunUnit(models.Model):
    """The immutable participation record for one unit in one producer run."""

    class Outcome(models.TextChoices):
        PASSED = "passed"
        MINOR = "minor"
        MAJOR = "major"
        CRITICAL = "critical"
        UNPARSED = "unparsed"
        DEFERRED = "deferred"
        REFUSED = "refused"
        SKIPPED = "skipped"
        STALE_CONFLICT = "stale-conflict"

    class SkipReason(models.TextChoices):
        PERMISSION = "permission"
        CAP = "cap"

    class RepairStatus(models.TextChoices):
        NOT_ATTEMPTED = "not-attempted"
        NO_CANDIDATE = "no-candidate"
        # Additive: the engine's own routing map excludes this language
        # outright, discovered without spending a call. Existing rows
        # keep NO_CANDIDATE; only new runs can tell the two apart.
        NO_ENGINE_FOR_LANGUAGE = "no-engine-for-language"
        CANDIDATE_STORED = "candidate-stored"
        APPLIED = "applied"
        ROLLED_BACK = "rolled-back"

    run = models.ForeignKey(JudgeRun, on_delete=models.deletion.CASCADE)
    unit = models.ForeignKey(
        "trans.Unit",
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="judge_run_units",
    )
    unit_id_snapshot = models.BigIntegerField()
    translation_id = models.BigIntegerField()
    component_id = models.BigIntegerField()
    project_id = models.BigIntegerField()
    input_target = models.JSONField(default=list)
    input_target_hash = models.CharField(max_length=64)
    context_hash = models.CharField(max_length=64)
    verdict = models.ForeignKey(
        "JudgeVerdict",
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="run_outcomes",
    )
    outcome = models.CharField(max_length=20, choices=Outcome)
    skip_reason = models.CharField(max_length=20, choices=SkipReason, blank=True)
    repair_status = models.CharField(
        max_length=24, choices=RepairStatus, default=RepairStatus.NOT_ATTEMPTED
    )
    initial_severity = models.CharField(
        max_length=10,
        choices=(
            ("none", "none"),
            ("minor", "minor"),
            ("major", "major"),
            ("critical", "critical"),
        ),
        blank=True,
    )
    final_severity = models.CharField(
        max_length=10,
        choices=(
            ("none", "none"),
            ("minor", "minor"),
            ("major", "major"),
            ("critical", "critical"),
        ),
        blank=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    before_target = models.JSONField(default=list)
    after_target = models.JSONField(default=list)
    cached = models.BooleanField(default=False)
    projection_succeeded = models.BooleanField(default=False)

    class Meta:
        verbose_name = gettext_lazy("Judge run unit")
        verbose_name_plural = gettext_lazy("Judge run units")
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(fields=["run", "outcome"], name="judge_run_outcome_idx"),
            models.Index(fields=["run", "repair_status"], name="judge_run_repair_idx"),
        ]
        # ruff: ignore[mutable-class-default]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "unit_id_snapshot"], name="judge_run_one_unit"
            )
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.unit_id_snapshot}"


class JudgeVerdict(models.Model):
    """
    One judge opinion about one version of one unit.

    ``(unit, run_id, attempt, request_round, seat)`` so the collegium and
    its repair loop stay auditable. ``attempt`` records a translation mutation
    cycle; ``request_round`` records transport recovery within that cycle. Only
    ``max_severity`` and ``unparsed`` are
    stored; the verdict is derived (severity->verdict mapping was
    reopened by measurement R3 and must stay changeable without a data
    migration).
    """

    # Stored and API-facing values: deliberately not localized.
    class Verdict(models.TextChoices):
        PASS = "pass"  # ruff: ignore[hardcoded-password-string]
        FLAG = "flag"
        REJECT = "reject"
        # Transport failure, never an opinion. Architecture invariant 4.3.
        UNPARSED = "unparsed"

    class Severity(models.TextChoices):
        # Declaration order IS strictness order (see SEVERITY_RANK, task 2).
        NONE = "none"
        MINOR = "minor"
        MAJOR = "major"
        CRITICAL = "critical"

    class Resolution(models.TextChoices):
        ACCEPTED_AS_IS = "accepted_as_is"
        SENT_BACK = "sent_back"
        ESCALATED = "escalated"

    # Stable codes returned by generate_candidate_for_verdict; "generated"
    # is deliberately absent because a successful generation clears this
    # pair instead of storing it (the stored candidate is then the state
    # worth rendering).
    class GenerationOutcome(models.TextChoices):
        EXISTING = "existing"
        STALE = "stale"
        DENIED = "denied"
        INVALID_VERDICT = "invalid-verdict"
        RESOLVED = "resolved"
        MAX_LENGTH = "max-length"
        NO_ENGINE = "no-engine"
        BUSY = "busy"
        FAILED = "failed"
        DRIFT = "drift"

    unit = models.ForeignKey(
        "trans.Unit", on_delete=models.deletion.CASCADE, related_name="judge_verdicts"
    )
    # The model's own pass/flag/reject choice, kept as evidence (measured
    # arm D returns it per segment). The gate uses max_severity, not this.
    model_verdict = models.CharField(max_length=10, choices=Verdict, blank=True)
    max_severity = models.CharField(
        max_length=10, choices=Severity, default=Severity.NONE
    )
    # A transport failure is not a severity: it is a separate axis so an
    # unparsed row can never read as a real "none"/pass (invariant 4.3).
    unparsed = models.BooleanField(default=False)
    errors = models.JSONField(default=list, blank=True)
    back_translation = models.TextField(blank=True)
    instruction = models.TextField(blank=True)
    judge_model = models.CharField(max_length=200)
    # The endpoint that actually served this verdict. Blank on every row
    # written before the availability fallback existed; no data migration.
    judge_provider = models.CharField(max_length=32, blank=True)
    # Place in the collegium, not seniority: seat 2 may not lower seat 1.
    seat = models.SmallIntegerField()
    attempt = models.SmallIntegerField(default=0)
    request_round = models.PositiveSmallIntegerField(default=0)
    target_hash = models.CharField(max_length=64)
    target_storage_hash = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=32, null=True, db_index=True
    )
    context_hash = models.CharField(max_length=64)
    run_id = models.UUIDField(default=uuid.uuid4)
    timestamp = models.DateTimeField(auto_now_add=True)
    request_attempt = models.ForeignKey(
        JudgeRequestAttempt,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="verdicts",
    )
    request_identity = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=64, null=True, blank=True
    )
    project_context_hash = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=64, null=True, blank=True
    )
    source_language = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=32, null=True, blank=True
    )
    target_language = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=32, null=True, blank=True
    )
    profile_fingerprint = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=64, null=True, blank=True
    )
    prompt_schema_version = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=64, null=True, blank=True
    )

    resolution = models.CharField(max_length=20, choices=Resolution, blank=True)
    resolution_reason = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="judge_resolutions",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    generation_outcome = models.CharField(
        max_length=20, choices=GenerationOutcome, blank=True
    )
    generation_outcome_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge verdict")
        verbose_name_plural = gettext_lazy("Judge verdicts")
        # No default ordering: it would force a sort on every queryset of
        # a table that accumulates. Callers order explicitly.
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(fields=["unit", "-timestamp"], name="judge_unit_recent_idx"),
            models.Index(
                fields=["unit", "target_hash", "-timestamp"],
                name="judge_unit_target_idx",
            ),
            models.Index(fields=["run_id"], name="judge_run_idx"),
            models.Index(
                fields=[
                    "unit",
                    "request_identity",
                    "profile_fingerprint",
                    "prompt_schema_version",
                    "-timestamp",
                ],
                name="judge_verdict_cache_idx",
            ),
        ]
        # ruff: ignore[mutable-class-default]
        constraints = [
            # One vote per seat per request round. Transport recovery must not
            # consume a repair attempt coordinate.
            models.UniqueConstraint(
                fields=["unit", "run_id", "attempt", "request_round", "seat"],
                name="judge_one_vote_per_seat_round",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit_id}: {self.verdict} (seat {self.seat})"

    @property
    def verdict(self) -> str:
        """
        Derive the verdict, never stored.

        The severity->verdict mapping is reopened by R3 and must change
        without a data migration (D4).
        """
        if self.unparsed:
            return self.Verdict.UNPARSED
        return verdict_for_severity(self.max_severity)

    @property
    def primary_error(self) -> dict | None:
        """The first recorded error at the verdict's own maximum severity."""
        for error in self.errors:
            if error.get("severity") == self.max_severity:
                return error
        return self.errors[0] if self.errors else None

    def is_stale(self, target: Sequence[str]) -> bool:
        """Whether the judged text differs from the text stored now."""
        return self.target_hash != compute_target_hash(target)


# Design "Гейт по severity выражается штатными настройками". minor is a
# pass: the errors are recorded, but they do not hold the string back.
_SEVERITY_VERDICT = {
    "none": JudgeVerdict.Verdict.PASS,
    "minor": JudgeVerdict.Verdict.PASS,
    "major": JudgeVerdict.Verdict.FLAG,
    "critical": JudgeVerdict.Verdict.REJECT,
}

# Strictness order, so a round reduces to its strictest seat without a
# seat ever lowering another. Derived from the declared scale: Severity
# is ordered by definition (task 2 pins this with a test).
SEVERITY_RANK = {name: rank for rank, name in enumerate(JudgeVerdict.Severity.values)}


def verdict_for_severity(max_severity: str) -> str:
    """Derive the verdict from the worst error the judge reported."""
    return _SEVERITY_VERDICT[max_severity]


def state_for_verdict(
    verdict: str, *, enable_review: bool, may_approve: bool
) -> StringState | None:
    """
    Target state for a verdict, or None when the state must not move.

    ``critical`` lands on STATE_FUZZY, which the project-level
    ``WITHOUT_NEEDS_EDITING`` commit policy already excludes from export
    (FUZZY_STATES): an unresolved critical is held back from shipping.
    ``major`` stops at STATE_TRANSLATED instead: measurement shows most
    ``major`` findings are false positives or matters of taste, so the
    cost of blocking every one of them (holding back real, shippable
    strings while a human works through the queue) outweighs the cost of
    an occasional unresolved major shipping with judge-flag evidence
    attached (review D1). ``pass`` stops at STATE_TRANSLATED unless the
    site opts into judge approval (JUDGE_MAY_APPROVE) AND the project has
    review: measurement shows pass misses real critical defects, so the
    judge does not hand out the top trust state by default (review D2).
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.PASS and enable_review and may_approve:
        return STATE_APPROVED
    return STATE_TRANSLATED


def latest_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Return every seat of the newest round, stale or not.

    For the card's 'previous version' note. Not for projection.
    """
    newest = unit.judge_verdicts.order_by("-timestamp", "-pk").first()
    if newest is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            run_id=newest.run_id,
            attempt=newest.attempt,
            request_round=newest.request_round,
        ).order_by("seat")
    )


def current_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Return each seat's freshest verdict for the current text and context.

    A seat whose freshest row is unparsed contributes that transport
    failure: orchestration must not repair or finalize a unit using a
    fallback opinion from an older round of that seat.
    """
    target_hash, context_hash = _current_snapshot_hashes(unit)
    return _seat_round_rows(unit, target_hash, context_hash, prefer_parsed=False)


def _glossary_prompt_entries(unit: Unit) -> list[GlossaryPromptEntry]:
    from weblate.glossary.models import (  # ruff: ignore[import-outside-top-level]
        get_matched_glossary_prompt_entries,
    )

    return get_matched_glossary_prompt_entries(unit)


def _current_snapshot_hashes(unit: Unit) -> tuple[str, str]:
    """Return the (target, context) hashes of the unit as currently stored."""
    return (
        compute_target_hash(unit.get_target_plurals()),
        compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            explanation=unit.source_unit.explanation,
            glossary_terms=_glossary_prompt_entries(unit),
        ),
    )


def _seat_round_rows(
    unit: Unit,
    target_hash: str,
    context_hash: str | None,
    *,
    prefer_parsed: bool,
) -> list[JudgeVerdict]:
    """
    Assemble a round by taking each seat's own freshest matching row.

    Grouping by a single ``(run_id, attempt)`` couples the two seats'
    timelines: a durable one-seat retry (judge deferrals) lands in a newer
    round key, and the strictest seat's opinion stops being read even
    though nothing replaced it. Selecting per seat keeps every valid
    opinion visible across run boundaries while still never letting a
    transport failure erase a real verdict (D5).

    ``prefer_parsed=True`` (the ``active_round`` read) drops a seat that
    never parsed this snapshot: an unparsed row is not an opinion, so it
    cannot project a check. ``prefer_parsed=False`` (``current_round``)
    keeps it so orchestration sees the transport failure.

    ``context_hash=None`` keeps the historical target-only matching of
    ``active_round``: a glossary/note drift must not unproject a check.
    """
    base = unit.judge_verdicts.filter(target_hash=target_hash)
    if context_hash is not None:
        base = base.filter(context_hash=context_hash)
    rows: list[JudgeVerdict] = []
    for seat in base.values_list("seat", flat=True).distinct():
        seat_rows = base.filter(seat=seat)
        if prefer_parsed:
            newest = (
                seat_rows.filter(unparsed=False).order_by("-timestamp", "-pk").first()
            )
        else:
            newest = seat_rows.order_by("-timestamp", "-pk").first()
        if newest is not None:
            rows.append(newest)
    rows.sort(key=lambda row: row.seat)
    return rows


def active_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Newest parsed per-seat round that describes the current text.

    Staleness is handled by filtering on target_hash only, matching the
    historical behavior: a glossary/note drift must not unproject a
    judge check. Each seat keeps its newest parsed row for this snapshot,
    so a transport failure never erases the last real verdict (D5) and a
    one-seat retry cannot hide the other seat's opinion.
    """
    target_hash = compute_target_hash(unit.get_target_plurals())
    return _seat_round_rows(unit, target_hash, None, prefer_parsed=True)


def collegium_verdict(rows: Sequence[JudgeVerdict]) -> JudgeVerdict | None:
    """
    Return the strictest opinion of a round. No seat may lower another.

    A transport failure is not an opinion, so an unparsed row neither
    raises nor lowers the round; only when every seat failed does the
    round read as unparsed.
    """
    if not rows:
        return None
    parsed = [row for row in rows if not row.unparsed]
    if not parsed:
        return rows[0]
    return max(parsed, key=lambda row: (SEVERITY_RANK[row.max_severity], -row.seat))


def active_verdict(unit: Unit) -> JudgeVerdict | None:
    """Return the collegium verdict that still describes the stored text."""
    return collegium_verdict(active_round(unit))


def has_complete_current_evidence(unit: Unit, *, seats: Sequence[int]) -> bool:
    """Whether every configured seat has parsed the current snapshot."""
    rows = current_round(unit)
    return (
        {row.seat for row in rows} == set(seats)
        and len(rows) == len(seats)
        and all(not row.unparsed for row in rows)
    )


@dataclass(frozen=True)
class RepairEvidence:
    """Bounded evidence for a round that already went through a repair."""

    # Every seat of the attempt-0 round: what the judge originally flagged.
    original_seats: list[JudgeVerdict]
    # The joined-plural text a unique Change proves stood before the
    # repair, or None when the window below matched zero or several rows.
    previous_target: str | None


def repair_evidence(
    unit: Unit, *, active: JudgeVerdict | None = None
) -> RepairEvidence | None:
    """
    Evidence for the repair the active round has already been through.

    None unless the active round's own attempt is greater than zero.
    Attempt-0 errors always accompany a repaired round; the previous
    text is added only when exactly one ``Change`` unambiguously sits in
    this window (never guess):

        last attempt-0 timestamp
          < Change.timestamp with Change.target == repaired target
          < first attempt-1 timestamp

    ``active`` lets a caller that already computed ``active_verdict(unit)``
    (for example ``_judge_view_context``) pass it in, avoiding a second
    ``active_round`` query on a hot per-unit-view path. Always compares
    ``original_seats`` remain attempt 0, but the comparison window follows
    the active attempt: a unit repaired twice must show the text before the
    second repair, not a stale first-repair comparison.
    """
    if active is None:
        active = active_verdict(unit)
    if active is None or active.attempt == 0:
        return None
    original_seats = list(
        unit.judge_verdicts.filter(run_id=active.run_id, attempt=0).order_by("seat")
    )
    if not original_seats:
        return None
    previous_seats = list(
        unit.judge_verdicts.filter(
            run_id=active.run_id, attempt=active.attempt - 1
        ).order_by("timestamp")
    )
    if not previous_seats:
        return None
    current_seats = list(
        unit.judge_verdicts.filter(
            run_id=active.run_id, attempt=active.attempt
        ).order_by("timestamp")
    )
    if not current_seats:
        return None
    window_start = max(row.timestamp for row in previous_seats)
    window_end = current_seats[0].timestamp
    candidates = list(
        unit.change_set.filter(
            target=unit.target,
            timestamp__gt=window_start,
            timestamp__lt=window_end,
        )
    )
    previous_target = candidates[0].old if len(candidates) == 1 else None
    return RepairEvidence(
        original_seats=original_seats, previous_target=previous_target
    )


def judge_status_annotations() -> dict[str, models.Expression]:
    """Annotate a Unit queryset with target-fresh judge evidence."""

    def _has_newer_sibling(*, newer_parsed: bool) -> Exists:
        """Exists: a fresher same-seat row for the same stored target."""
        conditions = [
            Q(unit_id=OuterRef("unit_id")),
            Q(target_storage_hash=OuterRef("target_storage_hash")),
            Q(seat=OuterRef("seat")),
            Q(timestamp__gt=OuterRef("timestamp"))
            | Q(timestamp=OuterRef("timestamp"), pk__gt=OuterRef("pk")),
        ]
        if newer_parsed:
            conditions.append(Q(unparsed=False))
        return Exists(
            JudgeVerdict.objects.filter(*conditions),
        )

    # The per-seat assembly used by _seat_round_rows, expressed for SQL:
    # a row is its seat's freshest parsed evidence for the current text
    # when no newer parsed same-seat row exists for that text.
    current_parsed_round = JudgeVerdict.objects.filter(
        unit_id=OuterRef("pk"),
        target_storage_hash=MD5(OuterRef("target")),
        unparsed=False,
    ).exclude(_has_newer_sibling(newer_parsed=True))
    severity_rank = Case(
        *(
            When(max_severity=severity, then=Value(rank))
            for severity, rank in SEVERITY_RANK.items()
        ),
        output_field=IntegerField(),
    )
    # Some seat's freshest row (parsed or not) for the current text is a
    # transport failure: the per-seat view of the former "latest round has
    # no parsed row" signal.
    seat_fresh_unparsed = JudgeVerdict.objects.filter(
        unit_id=OuterRef("pk"),
        target_storage_hash=MD5(OuterRef("target")),
        unparsed=True,
    ).exclude(_has_newer_sibling(newer_parsed=False))

    return {
        "judge_active_severity": Subquery(
            current_parsed_round.annotate(severity_rank=severity_rank)
            .order_by("-severity_rank", "seat")
            .values("max_severity")[:1],
            output_field=CharField(),
        ),
        "judge_active_resolution": Subquery(  # type: ignore[dict-item]
            current_parsed_round.annotate(severity_rank=severity_rank)
            .order_by("-severity_rank", "seat")
            .values("resolution")[:1],
            output_field=CharField(),
        ),
        "judge_has_parsed_history": Exists(
            JudgeVerdict.objects.filter(unit_id=OuterRef("pk"), unparsed=False)
        ),
        "judge_latest_incomplete": Exists(seat_fresh_unparsed),
    }


def current_verdict(unit: Unit) -> JudgeVerdict | None:
    """Return only the verdict from the newest current-context round."""
    return collegium_verdict(current_round(unit))


class JudgeResolutionError(Exception):
    """A producer's judge verdict resolution request could not be applied."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# (representative verdict, current resolution, requested resolution). "" is
# unresolved. sent_back is never a requested resolution (D: not exposed).
# A terminal accepted_as_is, or a re-request of the current resolution,
# is deliberately absent - both read as an invalid/duplicate transition.
ALLOWED_RESOLUTION_TRANSITIONS = {
    (
        JudgeVerdict.Verdict.REJECT,
        "",
        JudgeVerdict.Resolution.ESCALATED,
    ),
    (
        JudgeVerdict.Verdict.REJECT,
        "",
        JudgeVerdict.Resolution.ACCEPTED_AS_IS,
    ),
    (
        JudgeVerdict.Verdict.REJECT,
        JudgeVerdict.Resolution.ESCALATED,
        JudgeVerdict.Resolution.ACCEPTED_AS_IS,
    ),
    (
        JudgeVerdict.Verdict.FLAG,
        "",
        JudgeVerdict.Resolution.ESCALATED,
    ),
    (
        JudgeVerdict.Verdict.FLAG,
        "",
        JudgeVerdict.Resolution.ACCEPTED_AS_IS,
    ),
    (
        JudgeVerdict.Verdict.FLAG,
        JudgeVerdict.Resolution.ESCALATED,
        JudgeVerdict.Resolution.ACCEPTED_AS_IS,
    ),
}


def resolve_verdict(
    *,
    unit: Unit,
    expected_verdict_id: int,
    actor: User,
    resolution: str,
    reason: str,
) -> JudgeVerdict:
    """
    Record a producer decision on the representative verdict of a unit.

    One transaction: locks the Unit, then the representative JudgeVerdict;
    rejects a missing, stale, or invalid transition; updates the current
    resolution without touching severity/errors; writes one immutable
    Change (verdict id, old/new resolution, reason - old/new state comes
    for free from Unit.generate_change()); applies state through
    Unit.translate() reusing the same Unit lock. A critical escalation
    does not change state, so it writes its Change directly instead of
    going through translate(), which would otherwise skip writing
    anything when neither state nor target changed.
    """
    reason = reason.strip()
    if resolution not in {
        JudgeVerdict.Resolution.ESCALATED,
        JudgeVerdict.Resolution.ACCEPTED_AS_IS,
    }:
        msg = "invalid_transition"
        raise JudgeResolutionError(msg, gettext("That decision is not available."))
    if not actor.has_perm("unit.review", unit):
        raise PermissionDenied

    with transaction.atomic():
        locked_unit = Unit.objects.select_for_update().get(pk=unit.pk)
        representative = current_verdict(locked_unit)
        stale_message = gettext(
            "This verdict no longer matches the current text or context; "
            "reload and try again."
        )
        if representative is None:
            # Something was judged before but no round matches the current
            # target/context (a stale round) is a different situation from
            # nothing having been judged at all (a missing round).
            if locked_unit.judge_verdicts.exists():
                msg = "stale"
                raise JudgeResolutionError(msg, stale_message)
            msg = "missing"
            raise JudgeResolutionError(
                msg,
                gettext("No current judge verdict was found for this string."),
            )
        if representative.pk != expected_verdict_id:
            msg = "stale"
            raise JudgeResolutionError(msg, stale_message)
        representative = JudgeVerdict.objects.select_for_update().get(
            pk=representative.pk
        )
        old_resolution = representative.resolution
        verdict = representative.verdict
        old_state = locked_unit.state

        if (verdict, old_resolution, resolution) not in ALLOWED_RESOLUTION_TRANSITIONS:
            msg = "invalid_transition"
            raise JudgeResolutionError(
                msg,
                gettext("That decision does not apply to this verdict."),
            )
        if resolution == JudgeVerdict.Resolution.ESCALATED:
            # A major is held for the first time. A critical is forced
            # back to fuzzy rather than left at whatever old_state
            # happens to be: escalating a critical must never leave it
            # shipping, even if something else changed its state since
            # the verdict without invalidating the round (target unchanged).
            new_state = (
                STATE_NEEDS_CHECKING
                if verdict == JudgeVerdict.Verdict.FLAG
                else STATE_FUZZY
            )
        else:
            new_state = STATE_TRANSLATED

        representative.resolution = resolution
        representative.resolution_reason = reason
        representative.resolved_by = actor
        representative.resolved_at = timezone.now()
        representative.save(
            update_fields=[
                "resolution",
                "resolution_reason",
                "resolved_by",
                "resolved_at",
            ]
        )

        change_details = {
            "judge_verdict_id": representative.pk,
            "old_resolution": old_resolution,
            "new_resolution": resolution,
            "reason": reason,
        }
        if new_state == old_state:
            locked_unit.generate_change(
                actor,
                actor,
                ActionEvents.JUDGE_RESOLUTION,
                change_details=change_details,
            )
        else:
            locked_unit.translate(
                actor,
                locked_unit.get_target_plurals(),
                new_state,
                change_action=ActionEvents.JUDGE_RESOLUTION,
                change_details=change_details,
                propagate=False,
                select_for_update=False,
            )
        locked_unit.translation.invalidate_cache()
    return representative


def describe_latest_verdict(unit: Unit) -> str:
    """
    Human-readable evidence for the active round, or an empty string.

    Rendered into the check description, which weblate/machinery/llm.py
    feeds to the translator as failing_checks during repair. Both seats
    are merged. Descriptions are escaped and joined with an explicit
    separator: llm.py runs the text through strip_tags().split(), which
    would otherwise eat game markup like <color=#RRGGBB> and collapse
    newlines into one blob (review Q1).
    """
    lines: list[str] = []
    for row in active_round(unit):
        for error in row.errors:
            severity = error.get("severity", "unspecified")
            category = error.get("category", "unspecified")
            description = escape(error.get("description", ""))
            line = f"{severity}/{category}: {description}"
            if line not in lines:
                lines.append(line)
    return JUDGE_ERROR_SEPARATOR.join(lines)


def describe_latest_instruction(unit: Unit) -> str:
    """
    Build the deterministic repair instruction for the active verdict.

    Historical rows can contain arbitrary model-generated instructions. Repair
    must use only locally validated error descriptions plus this fixed
    requirement, so a valid verdict can never be discarded or its repair path
    changed by optional model prose.
    """
    description = describe_latest_verdict(unit)
    if not description:
        return ""
    return f"{description}\n\n{JUDGE_REPAIR_REQUIREMENT}"
