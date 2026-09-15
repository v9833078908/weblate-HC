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
