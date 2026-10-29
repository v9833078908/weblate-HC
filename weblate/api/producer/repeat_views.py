# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Actor- and project-scoped resources for the producer repeat workflow."""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from weblate.api.producer.operations import (
    execute_quality_operation,
    idempotency_parameter,
    operation_key,
)
from weblate.api.producer.repeat_quality import (
    apply_producer_repeat_plan,
    build_producer_repeat_plan,
    project_repeat_plan,
    project_repeat_run,
)
from weblate.api.producer.serializers import (
    ProducerRepeatApplyRequestSerializer,
    ProducerRepeatPlanRequestSerializer,
    ProducerRepeatPlanSerializer,
    ProducerRepeatRunSerializer,
)
from weblate.api.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)
from weblate.trans.models.producer_quality import ProducerQualityOperation
from weblate.trans.quality_execution import reject_verification_mutation


def _resource(request, token, action, *, lock=False):
    query = ProducerQualityOperation.objects.select_related("project")
    if lock:
        query = query.select_for_update()
    return get_object_or_404(
        query,
        pk=token,
        action=action,
        actor=request.user,
        project__in=request.user.allowed_projects,
    )


def _require_key(request):
    if operation_key(request) is None:
        raise ValidationError(
            {"Idempotency-Key": "A UUID is required for repeat operations."}
        )


class ProducerProjectRepeatPlan(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle, AnonRateThrottle, ScopedRateThrottle)
    throttle_scope = "producer"

    @extend_schema(
        request=ProducerRepeatPlanRequestSerializer,
        parameters=[idempotency_parameter(required=True)],
        responses=ProducerRepeatPlanSerializer,
    )
    def post(self, request, slug):
        _require_key(request)
        project = get_object_or_404(request.user.allowed_projects, slug=slug)

        def create(operation):
            serializer = ProducerRepeatPlanRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            operation.private_data = build_producer_repeat_plan(
                project=project, actor=request.user, **serializer.validated_data
            )
            operation.save(update_fields=["private_data", "updated"])
            return Response(
                ProducerRepeatPlanSerializer(
                    project_repeat_plan(operation, request.user)
                ).data
            )

        return execute_quality_operation(
            request=request, project=project, action="repeat-plan", callback=create
        )


class ProducerRepeatPlanDetail(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses=ProducerRepeatPlanSerializer)
    def get(self, request, token):
        with transaction.atomic():
            operation = _resource(request, token, "repeat-plan", lock=True)
            return Response(
                ProducerRepeatPlanSerializer(
                    project_repeat_plan(operation, request.user)
                ).data
            )


class ProducerRepeatPlanApply(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        request=ProducerRepeatApplyRequestSerializer,
        parameters=[idempotency_parameter(required=True)],
        responses=ProducerRepeatRunSerializer,
    )
    def post(self, request, token):
        _require_key(request)
        plan = _resource(request, token, "repeat-plan")

        reject_verification_mutation(request, plan.project)

        def apply(operation):
            payload = apply_producer_repeat_plan(
                plan=plan, operation=operation, actor=request.user
            )
            return Response(ProducerRepeatRunSerializer(payload).data)

        return execute_quality_operation(
            request=request, project=plan.project, action="repeat-apply", callback=apply
        )


class ProducerRepeatRunDetail(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses=ProducerRepeatRunSerializer)
    def get(self, request, token):
        operation = _resource(request, token, "repeat-apply")
        return Response(
            ProducerRepeatRunSerializer(
                project_repeat_run(operation, request.user)
            ).data
        )
