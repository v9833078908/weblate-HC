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
    from weblate.trans.models import Project

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

    judge = serializers.BooleanField(
        help_text="Whether the LLM judge is configured and usable."
    )
    glossary_profile_analysis = serializers.BooleanField(
        help_text="Whether loc-kit glossary profile analysis is enabled."
    )


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


class ProducerJudgeEstimateRequestSerializer(serializers.Serializer):
    """A read-only judge estimate for a closed project scope."""

    kind = serializers.ChoiceField(choices=PRODUCER_RUN_KIND_CHOICES)
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


class ProducerJudgeEstimateSerializer(serializers.Serializer):
    """Upper bounds and exact selection counts for a judge scope."""

    estimate_id = serializers.UUIDField()
    scope_hash = serializers.CharField()
    strings = serializers.IntegerField()
    selected = serializers.IntegerField()
    excluded = serializers.IntegerField()
    initial_calls = serializers.IntegerField()
    worst_case_calls = serializers.IntegerField()
    basis = serializers.CharField()


class ProducerRunStartRequestSerializer(serializers.Serializer):
    """Start the run an estimate priced, or fail closed on drift."""

    kind = serializers.ChoiceField(choices=PRODUCER_RUN_KIND_CHOICES)
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
