# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Views for the producer console API.

Permissions are the existing ones: a producer sees exactly the projects
Weblate already allows them, and no new role is introduced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from weblate.api.producer.serializers import (
    ProducerMeSerializer,
    ProducerProjectSummarySerializer,
)
from weblate.trans.judge import judge_configuration_ready

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from rest_framework.request import Request

    from weblate.auth.models import User
    from weblate.trans.models import Project

    class AuthenticatedRequest(Request):
        """DRF request after Weblate's authentication middleware."""

        user: User


class ProducerMe(APIView):
    """Producer identity and server capabilities."""

    permission_classes = (IsAuthenticated,)
    serializer_class = ProducerMeSerializer
    request: AuthenticatedRequest  # type: ignore[assignment]

    @extend_schema(
        operation_id="api_producer_me_retrieve",
        responses=ProducerMeSerializer,
    )
    def get(self, request: Request) -> Response:
        """Return the current user and what this server can actually do."""
        user = self.request.user
        payload = {
            "username": user.username,
            "full_name": user.full_name,
            "is_superuser": user.is_superuser,
            "capabilities": {
                "judge": judge_configuration_ready(),
                "glossary_profile_analysis": (
                    settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED
                ),
            },
            "advanced_url": request.build_absolute_uri(reverse("profile")),
        }
        return Response(ProducerMeSerializer(payload).data)


class ProducerProjects(ListAPIView):
    """Projects the authenticated producer may work with."""

    permission_classes = (IsAuthenticated,)
    serializer_class = ProducerProjectSummarySerializer
    request: AuthenticatedRequest  # type: ignore[assignment]

    @extend_schema(
        operation_id="api_producer_projects_list",
        responses=ProducerProjectSummarySerializer(many=True),
    )
    def get(self, request: Request, *args, **kwargs) -> Response:
        """Return the permission-filtered project list."""
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Project]:
        # The same access filter the native project API uses, so the console
        # can never widen what a producer already sees in the interface.
        return self.request.user.allowed_projects.order_by("id")
