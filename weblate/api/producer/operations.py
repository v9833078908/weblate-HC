# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Atomic idempotency receipts, shared by all mutating quality endpoints."""

from __future__ import annotations

import hashlib
import json
from functools import wraps
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from weblate.trans.delivery_policy import durable_admission
from weblate.trans.models.producer_quality import ProducerQualityOperation


def idempotency_parameter(*, required: bool = False) -> OpenApiParameter:
    """Describe the durable recovery key on the actual public API boundary."""
    return OpenApiParameter(
        name="Idempotency-Key",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.HEADER,
        required=required,
        description="Actor/project/request-bound UUID for durable local operation recovery.",
    )


def operation_key(request) -> UUID | None:
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        raise ValidationError({"Idempotency-Key": "A UUID is required."}) from None


def execute_quality_operation(*, request, project, action: str, callback) -> Response:
    key = operation_key(request)
    if key is None:
        return callback(None)
    digest = hashlib.sha256(
        json.dumps(
            [action, request.path, request.data],
            sort_keys=True,
            separators=(",", ":"),
            cls=DjangoJSONEncoder,
        ).encode()
    ).hexdigest()
    with transaction.atomic():
        operation, created = (
            ProducerQualityOperation.objects.select_for_update().get_or_create(
                actor=request.user,
                project=project,
                key=key,
                defaults={"action": action, "request_hash": digest},
            )
        )
        if not created:
            if operation.action != action or operation.request_hash != digest:
                return Response({"code": "idempotency-conflict"}, status=409)
            if operation.state != "confirmed":
                return Response({"code": "operation-pending"}, status=409)
            return Response(operation.result, status=operation.response_status)
        with durable_admission():
            response = callback(operation)
        if response.status_code >= 400:
            # A refused operation is not accepted: no receipt or partial local
            # mutation may survive an explicit 401/403/429/validation refusal.
            transaction.set_rollback(True)
            return response
        operation.result = response.data
        operation.response_status = response.status_code
        operation.state = "confirmed"
        operation.save(update_fields=["result", "response_status", "state", "updated"])
        return response


def quality_idempotent(action: str):
    """Keep the legacy contract when no key is supplied; bind keyed writes atomically."""

    def decorate(method):
        @wraps(method)
        def invoke(self, request, slug, *args, **kwargs):
            if operation_key(request) is None:
                return method(self, request, slug, *args, **kwargs)
            project = get_object_or_404(request.user.allowed_projects, slug=slug)
            return execute_quality_operation(
                request=request,
                project=project,
                action=action,
                callback=lambda _operation: method(
                    self, request, slug, *args, **kwargs
                ),
            )

        return invoke

    return decorate
