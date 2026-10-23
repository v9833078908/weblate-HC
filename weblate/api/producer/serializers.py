# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Serializers for the producer console API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

if TYPE_CHECKING:
    from weblate.trans.models import ProducerRun, Project

# The only run kind this contract accepts today. A named module-level
# constant, not an inline tuple literal: drf-spectacular otherwise cannot
# tell this "kind" field apart from the unrelated Report.Kind choices
# also named "kind" elsewhere in the API, and resolves the naming
# collision with an opaque auto-generated enum name.
PRODUCER_RUN_KIND_CHOICES = ("judge",)


class ProducerCapabilitiesSerializer(serializers.Serializer):
    """
    Server-side switches the console must honour.

    Only capabilities with a real configuration source are reported. A key
    is added together with the feature that backs it, never as a
    placeholder: the console decides what to hide, so an invented ``true``
    would offer an action the server cannot perform.
    """

    mt_async_batches = serializers.IntegerField(min_value=1, max_value=1)
    mt_reconciliation = serializers.IntegerField(read_only=True)
    quality_execution_policy = serializers.IntegerField(read_only=True)
    verify_only_immutable = serializers.IntegerField(read_only=True)
    judge = serializers.BooleanField(
        help_text="Whether the LLM judge is configured and usable."
    )
    judge_seats = serializers.IntegerField(
        min_value=0,
        help_text="Number of independently configured judge seats.",
    )
    glossary_profile_analysis = serializers.BooleanField(
        help_text="Whether loc-kit glossary profile analysis is enabled."
    )
    quality_api_version = serializers.IntegerField(read_only=True)
    candidate_quality_gate = serializers.BooleanField(read_only=True)
    glossary_direct_scan = serializers.BooleanField(read_only=True)
    quality_group_recheck = serializers.BooleanField(read_only=True)
    quality_repeat_plan = serializers.BooleanField(read_only=True)
    quality_final_snapshot = serializers.BooleanField(read_only=True)
    quality_idempotency = serializers.BooleanField(read_only=True)


class ProducerMeSerializer(serializers.Serializer):
    """Identity and capabilities for the authenticated producer."""

    username = serializers.CharField()
    full_name = serializers.CharField()
    is_superuser = serializers.BooleanField()
    capabilities = ProducerCapabilitiesSerializer()
    advanced_url = serializers.URLField(
        help_text="Weblate interface entry point for this user."
    )


class ProducerProjectSummarySerializer(serializers.Serializer):
    """One project the authenticated user may access."""

    slug = serializers.CharField()
    name = serializers.CharField()
    advanced_url = serializers.SerializerMethodField(
        help_text="Weblate interface entry point for this project."
    )

    @extend_schema_field(OpenApiTypes.URI)
    def get_advanced_url(self, obj: Project) -> str:
        url = obj.get_absolute_url()
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request is not None else url


class ProducerRunSerializer(serializers.Serializer):
    """Durable producer-run state without text-bearing judge evidence."""

    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    requested_mode = serializers.CharField(read_only=True)
    created = serializers.DateTimeField(read_only=True)
    started = serializers.DateTimeField(read_only=True, allow_null=True)
    finished = serializers.DateTimeField(read_only=True, allow_null=True)
    summary = serializers.JSONField(read_only=True)
    failure = serializers.CharField(read_only=True)
    warnings = serializers.JSONField(read_only=True)
    preparation_phase = serializers.CharField(read_only=True)
    coverage = serializers.SerializerMethodField(read_only=True)
    execution_mode = serializers.SerializerMethodField(read_only=True)

    @extend_schema_field(OpenApiTypes.STR)
    def get_execution_mode(self, obj: ProducerRun) -> str:
        return obj.execution_options.get("execution_mode", "repair")

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_coverage(self, obj: ProducerRun) -> dict[str, object] | None:
        if obj.requested_mode in {"judge", "recheck", "drain"}:
            return obj.get_coverage()
        return None


class ProducerDecisionListRequestSerializer(serializers.Serializer):
    """Filters for the project-scoped producer decision queue."""

    kind = serializers.ChoiceField(choices=PRODUCER_RUN_KIND_CHOICES)
    run = serializers.UUIDField()


class ProducerJudgeDecisionSerializer(serializers.Serializer):
    """One text-free judge decision with every safe-apply guard exposed."""

    unit_id = serializers.IntegerField(read_only=True)
    run_unit_outcome = serializers.CharField(read_only=True)
    severity = serializers.CharField(read_only=True)
    seat_results = serializers.DictField(
        child=serializers.ChoiceField(choices=("parsed", "unparsed", "missing")),
        read_only=True,
    )
    disagreement = serializers.BooleanField(read_only=True)
    issue_categories = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )
    candidate_id = serializers.IntegerField(read_only=True, allow_null=True)
    candidate_verified = serializers.BooleanField(read_only=True)
    candidate_acceptable = serializers.BooleanField(read_only=True)
    candidate_glossary_status = serializers.CharField(read_only=True)
    live_seat_results = serializers.DictField(
        child=serializers.ChoiceField(choices=("parsed", "unparsed", "missing")),
        read_only=True,
    )
    approval_loss = serializers.BooleanField(read_only=True)
    terminology_conflict = serializers.BooleanField(read_only=True)
    generation_outcome = serializers.CharField(read_only=True)
    applicability = serializers.ChoiceField(
        choices=(
            "ready",
            "no_candidate",
            "not_verified",
            "stale",
            "needs_individual_confirmation",
            "forbidden",
        ),
        read_only=True,
    )
    revision = serializers.CharField(read_only=True)


class ProducerQualityIssueFilterSerializer(serializers.Serializer):
    """Closed filters for repeat and glossary quality issues."""

    language = serializers.CharField()
    kind = serializers.CharField()

    def validate_kind(self, value: str) -> list[str]:
        kinds = [item.strip() for item in value.split(",") if item.strip()]
        if not kinds or set(kinds) - {"repeat-drift", "glossary"}:
            msg = "kind must contain repeat-drift and/or glossary"
            raise serializers.ValidationError(msg)
        return kinds


class ProducerQualityIssueSerializer(serializers.Serializer):
    """A text-free consistency group derived from Weblate checks."""

    group_id = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=("repeat-drift", "glossary"), read_only=True)
    hard = serializers.BooleanField(read_only=True)
    advisory = serializers.BooleanField(read_only=True)
    unit_ids = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    revisions = serializers.DictField(child=serializers.CharField(), read_only=True)
    variants = serializers.ListField(child=serializers.JSONField(), read_only=True)
    term_mode = serializers.CharField(read_only=True)
    approved_conflict = serializers.BooleanField(read_only=True)
    auto_repair_eligible = serializers.BooleanField(read_only=True)


class ProducerQualityIssueListSerializer(serializers.Serializer):
    """Stable snapshot of every requested consistency issue."""

    snapshot_hash = serializers.CharField(read_only=True)
    issues = ProducerQualityIssueSerializer(many=True, read_only=True)


class ProducerQualityRecheckItemSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField()
    revision = serializers.CharField()


class ProducerQualityScopeSerializer(serializers.Serializer):
    unit_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=10000
    )

    def validate_unit_ids(self, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            msg = "unit_ids must be unique"
            raise serializers.ValidationError(msg)
        return value


class ProducerQualitySnapshotRequestSerializer(serializers.Serializer):
    language = serializers.CharField()
    scope = ProducerQualityScopeSerializer()


class ProducerQualitySnapshotSerializer(serializers.Serializer):
    protected_content_digest = serializers.CharField(read_only=True)
    complete = serializers.BooleanField(read_only=True)
    unit_ids = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    revisions = serializers.DictField(child=serializers.CharField(), read_only=True)
    judged_revisions = serializers.DictField(
        child=serializers.CharField(), read_only=True
    )
    scope_revision = serializers.CharField(read_only=True)
    snapshot_hash = serializers.CharField(read_only=True)
    issues = serializers.ListField(child=serializers.JSONField(), read_only=True)


class ProducerRepeatPlanRequestSerializer(ProducerQualitySnapshotRequestSerializer):
    issue_snapshot_hash = serializers.CharField(min_length=64, max_length=64)
    application_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=True, max_length=10000
    )

    def validate_application_ids(self, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            msg = "application_ids must be unique"
            raise serializers.ValidationError(msg)
        return value


class ProducerRepeatGroupSerializer(serializers.Serializer):
    group_id = serializers.CharField(read_only=True)
    revision = serializers.CharField(read_only=True)
    unit_ids = serializers.ListField(child=serializers.IntegerField(), read_only=True)
    outcome = serializers.ChoiceField(
        choices=(
            "pending",
            "ready",
            "applied",
            "no_change",
            "independent",
            "approved_conflict",
            "needs_review",
            "stale",
            "forbidden",
            "unrepairable",
            "failed",
        ),
        read_only=True,
    )
    preferred_origin = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)


class ProducerRepeatPlanSerializer(serializers.Serializer):
    token = serializers.UUIDField(read_only=True)
    status = serializers.ChoiceField(
        choices=("queued", "running", "ready", "failed"), read_only=True
    )
    groups = ProducerRepeatGroupSerializer(many=True, read_only=True)


class ProducerRepeatResultSerializer(serializers.Serializer):
    unit = serializers.IntegerField(read_only=True)
    revision = serializers.CharField(read_only=True)
    outcome = serializers.ChoiceField(
        choices=(
            "applied",
            "no_change",
            "stale",
            "forbidden",
            "approved",
            "independent",
            "needs_review",
            "failed",
        ),
        read_only=True,
    )
    # This serializer is response-only. Conditional fields must stay optional
    # in OpenAPI as well as in the actual projection.
    event_id = serializers.UUIDField(required=False)
    reason = serializers.CharField(required=False)


class ProducerRepeatRunSerializer(serializers.Serializer):
    token = serializers.UUIDField(read_only=True)
    status = serializers.ChoiceField(
        choices=("queued", "running", "completed", "partial", "failed"), read_only=True
    )
    changed_unit_ids = serializers.ListField(
        child=serializers.IntegerField(), read_only=True
    )
    results = ProducerRepeatResultSerializer(many=True, read_only=True)


class ProducerRepeatApplyRequestSerializer(serializers.Serializer):
    """The server-owned frozen plan is the entire apply authority."""


class ProducerQualityOperationSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=("pending", "confirmed", "failed"), read_only=True
    )
    result = serializers.JSONField(required=False)


class ProducerQualityRecheckRequestSerializer(serializers.Serializer):
    items = ProducerQualityRecheckItemSerializer(many=True, allow_empty=False)

    def validate_items(self, value: list[dict]) -> list[dict]:
        unit_ids = [item["unit_id"] for item in value]
        if len(unit_ids) != len(set(unit_ids)):
            msg = "unit_id values must be unique"
            raise serializers.ValidationError(msg)
        if len(unit_ids) > 1000:
            msg = "at most 1000 units can be rechecked"
            raise serializers.ValidationError(msg)
        return value


class ProducerQualityRecheckResultItemSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField(read_only=True)
    outcome = serializers.ChoiceField(
        choices=("rechecked", "stale", "forbidden", "missing"), read_only=True
    )
    revision = serializers.CharField(read_only=True)
    checks = serializers.ListField(child=serializers.CharField(), read_only=True)


class ProducerQualityRecheckResultSerializer(serializers.Serializer):
    snapshot_hash = serializers.CharField(read_only=True)
    items = ProducerQualityRecheckResultItemSerializer(many=True, read_only=True)
    affected_unit_ids = serializers.ListField(
        child=serializers.IntegerField(), read_only=True
    )
    affected_repeat_group_ids = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )


class ProducerJudgeEstimateRequestSerializer(serializers.Serializer):
    """A read-only judge estimate for a closed project scope."""

    kind = serializers.ChoiceField(choices=PRODUCER_RUN_KIND_CHOICES)
    execution_mode = serializers.ChoiceField(
        choices=("repair", "verify_only"), default="repair"
    )
    scope = serializers.DictField(child=serializers.JSONField(), required=False)

    def validate_scope(self, value: dict) -> dict:
        unknown = set(value) - {"query", "unit_ids"}
        if unknown:
            msg = f"Unsupported scope fields: {', '.join(sorted(unknown))}"
            raise serializers.ValidationError(msg)
        query = value.get("query", "")
        if not isinstance(query, str):
            msg = "query must be a string"
            raise serializers.ValidationError(msg)
        unit_ids = value.get("unit_ids")
        if unit_ids is not None and (
            not isinstance(unit_ids, list)
            or not all(isinstance(unit_id, int) for unit_id in unit_ids)
        ):
            msg = "unit_ids must be a list of integers"
            raise serializers.ValidationError(msg)
        return value


class ProducerPreparationEstimateSerializer(serializers.Serializer):
    """
    The mandatory pre-judge machine-translation volume and its blockers.

    ``missing`` is the exact number of strings the run would machine
    translate first; ``per_language`` breaks it down; ``blockers`` carries
    human-readable reasons the judge could not start today; ``mt_cost`` is
    deliberately absent when no cost basis exists - an absent estimate is
    "unknown", never zero.
    """

    missing = serializers.IntegerField()
    per_language = serializers.DictField(child=serializers.IntegerField())
    engine = serializers.CharField(allow_null=True)
    blockers = serializers.ListField(child=serializers.CharField())
    mt_cost_known = serializers.BooleanField()


class ProducerJudgeEstimateSerializer(serializers.Serializer):
    """Upper bounds and exact selection counts for a judge scope."""

    estimate_id = serializers.UUIDField()
    execution_mode = serializers.CharField()
    scope_hash = serializers.CharField()
    strings = serializers.IntegerField()
    selected = serializers.IntegerField()
    excluded = serializers.IntegerField()
    initial_calls = serializers.IntegerField()
    worst_case_calls = serializers.IntegerField()
    preparation = ProducerPreparationEstimateSerializer()
    basis = serializers.CharField()


class ProducerRunStartRequestSerializer(serializers.Serializer):
    """Start the run an estimate priced, or fail closed on drift."""

    kind = serializers.ChoiceField(choices=PRODUCER_RUN_KIND_CHOICES)
    execution_mode = serializers.ChoiceField(
        choices=("repair", "verify_only"), default="repair"
    )
    scope = serializers.DictField(child=serializers.JSONField(), required=False)
    estimate_id = serializers.UUIDField()


class ProducerRunResumeRequestSerializer(serializers.Serializer):
    """Retry a technically-failed run's remaining work as a new linked run."""

    attempt = serializers.IntegerField(min_value=1)


class ProducerApplyCandidateRequestSerializer(serializers.Serializer):
    """Apply one already-verified judge repair candidate (Task 6, G5)."""

    revision = serializers.CharField()
    candidate_id = serializers.IntegerField()
    acknowledge = serializers.DictField(
        child=serializers.BooleanField(), required=False
    )


class ProducerDecisionSerializer(serializers.Serializer):
    """The unit-side outcome of one applied judge decision."""

    unit_id = serializers.IntegerField(read_only=True)
    revision = serializers.CharField(read_only=True)
    state = serializers.IntegerField(read_only=True)
    target = serializers.CharField(read_only=True)
    application_id = serializers.IntegerField(read_only=True, allow_null=True)


class ProducerBulkApplyItemRequestSerializer(serializers.Serializer):
    """One row of a bulk apply; never carries its own acknowledgement (G6)."""

    unit = serializers.IntegerField()
    revision = serializers.CharField()
    candidate_id = serializers.IntegerField()


class ProducerBulkApplyRequestSerializer(serializers.Serializer):
    """A closed batch of ordinary-candidate applications for one project."""

    items = ProducerBulkApplyItemRequestSerializer(many=True)


class ProducerBulkApplyResultSerializer(serializers.Serializer):
    """One row's honest, independent outcome from a bulk apply."""

    unit = serializers.IntegerField()
    outcome = serializers.ChoiceField(
        choices=(
            "applied",
            "already_applied",
            "stale",
            "forbidden",
            "needs_individual_confirmation",
            "not_verified",
        )
    )
    revision = serializers.CharField(allow_blank=True)
    application_id = serializers.IntegerField(allow_null=True)


class ProducerClarificationSerializer(serializers.Serializer):
    """A producer's own answer to a meaning-clarifying question (Task 7)."""

    answer = serializers.CharField(read_only=True, allow_blank=True)
    answered_by = serializers.CharField(read_only=True, allow_null=True)
    answered_at = serializers.DateTimeField(read_only=True, allow_null=True)
    revision = serializers.CharField(read_only=True)


class ProducerClarificationRequestSerializer(serializers.Serializer):
    """Answer (or clear) the clarifying question for one target unit."""

    revision = serializers.CharField()
    answer = serializers.CharField(allow_blank=True)
