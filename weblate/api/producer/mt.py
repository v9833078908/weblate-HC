# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from weblate.api.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)
from weblate.trans.forms import configured_routed_engine
from weblate.trans.models import (
    ProducerMTOperation,
    ProducerMTUnitAttempt,
    ProducerRun,
    Translation,
    Unit,
)
from weblate.trans.models.judge import compute_decision_revision
from weblate.trans.models.producer_mt import unit_revision
from weblate.trans.mt_operations import machinery_config_digest, reconcile_mt_operation
from weblate.wizard_auth.grants import request_binding


class MTUnitSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField(min_value=1)
    revision = serializers.RegexField(r"^[0-9a-f]{64}$")


class MTBatchSerializer(serializers.Serializer):
    component = serializers.SlugField()
    language = serializers.RegexField(r"^[A-Za-z0-9_-]+$")
    engine = serializers.ChoiceField(choices=("openrouter", "litellm"))
    threshold = serializers.IntegerField(min_value=0, max_value=100)
    units: serializers.ListSerializer = serializers.ListSerializer(
        child=MTUnitSerializer(), min_length=1, max_length=20
    )

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError(
                {"non_field_errors": ["Unknown MT batch fields are not permitted."]}
            )
        return super().to_internal_value(data)


class MTUnitResultSerializer(MTUnitSerializer):
    state = serializers.CharField()
    after_revision = serializers.CharField(allow_blank=True)


class MTReconciliationSerializer(serializers.Serializer):
    ready = serializers.ListField(child=serializers.IntegerField(min_value=1))
    never_sent = serializers.ListField(child=serializers.IntegerField(min_value=1))
    waiting = serializers.ListField(child=serializers.IntegerField(min_value=1))
    paid_unknown = serializers.ListField(child=serializers.IntegerField(min_value=1))
    paid_invalid = serializers.ListField(child=serializers.IntegerField(min_value=1))
    stale = serializers.ListField(child=serializers.IntegerField(min_value=1))
    forbidden = serializers.ListField(child=serializers.IntegerField(min_value=1))


class MTReceiptSerializer(serializers.Serializer):
    key = serializers.UUIDField()
    run_id = serializers.UUIDField()
    state = serializers.CharField()
    total = serializers.IntegerField(min_value=1, max_value=20)
    applied = serializers.IntegerField(min_value=0, max_value=20)
    units = MTUnitResultSerializer(many=True)
    retryable = serializers.BooleanField()
    reconciliation = MTReconciliationSerializer(required=False)


def _retryable_dispatch(operation):
    run = operation.run
    return (
        run.status == "failed"
        and run.dispatch_published_at is None
        and bool(run.dispatch_error)
        and not operation.units.exclude(state="pending").exists()
    )


def _schedule(run_id):
    def dispatch():
        # Keep task registry initialization out of API module imports.
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.tasks import publish_producer_run_dispatch

        publish_producer_run_dispatch(run_id=run_id)

    transaction.on_commit(dispatch)


def operation_result(operation: ProducerMTOperation) -> dict:
    units = [
        {
            "unit_id": u.unit_id,
            "revision": u.revision,
            "state": u.state,
            "after_revision": u.after_revision,
        }
        for u in operation.units.order_by("unit_id")
    ]
    return {
        "key": str(operation.key),
        "run_id": str(operation.run_id),
        "state": "failed"
        if operation.run.status in {"failed", "cancel_requested", "cancelled"}
        else operation.state,
        "retryable": _retryable_dispatch(operation),
        "units": units,
        "total": len(units),
        "applied": sum(u["state"] == "applied" for u in units),
    }


def _check_access(actor, translation):
    if not actor.has_perm("translation.auto", translation):
        msg = "Translation permission is required."
        raise PermissionDenied(msg)


class ProducerMTBatch(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle, AnonRateThrottle, ScopedRateThrottle)
    throttle_scope = "producer"

    @extend_schema(
        request=MTBatchSerializer,
        responses={202: MTReceiptSerializer},
        parameters=[
            OpenApiParameter(
                "Idempotency-Key",
                OpenApiTypes.UUID,
                OpenApiParameter.HEADER,
                required=True,
            )
        ],
        description="Accept 1–20 empty non-plural unit revisions for durable background MT. No provider I/O in HTTP.",
    )
    def post(self, request, slug):
        project = get_object_or_404(request.user.allowed_projects, slug=slug)
        try:
            key = UUID(request.headers.get("Idempotency-Key", ""))
        except (ValueError, AttributeError):
            raise ValidationError({"Idempotency-Key": "A UUID is required."}) from None
        serializer = MTBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        ids = [u["unit_id"] for u in payload["units"]]
        if len(set(ids)) != len(ids):
            raise ValidationError({"units": "Duplicate unit IDs."})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        translation = get_object_or_404(
            Translation.objects.select_related("component__project", "language"),
            component__project=project,
            component__slug=payload["component"],
            language__code=payload["language"],
        )
        _check_access(request.user, translation)
        # Project lock serializes acceptance, including two keys for the same unit.
        with transaction.atomic():
            type(project).objects.select_for_update().get(pk=project.pk)
            existing = ProducerMTOperation.objects.filter(
                actor=request.user, project=project, key=key
            ).first()
            if existing:
                if existing.request_hash != digest:
                    return Response({"code": "idempotency-conflict"}, status=409)
                existing.run = ProducerRun.objects.select_for_update().get(
                    pk=existing.run_id
                )
                if _retryable_dispatch(existing):
                    run = existing.run
                    run.status = "queued"
                    run.finished = None
                    run.dispatch_attempts = 0
                    run.dispatch_error = ""
                    run.save(
                        update_fields=[
                            "status",
                            "finished",
                            "dispatch_attempts",
                            "dispatch_error",
                        ]
                    )
                    _schedule(run.pk)
                return Response(operation_result(existing), status=202)
            if (
                translation.is_source
                or translation.is_readonly
                or translation.component.locked
            ):
                return Response({"code": "translation-readonly"}, status=409)
            if (
                configured_routed_engine(project.get_machinery_settings())
                != payload["engine"]
            ):
                raise ValidationError(
                    {"engine": "The project routed engine must be used."}
                )
            units = list(
                Unit.objects.select_for_update()
                .filter(translation=translation, pk__in=ids)
                .order_by("pk")
            )
            revisions = {u["unit_id"]: u["revision"] for u in payload["units"]}
            if len(units) != len(ids):
                return Response({"code": "unit-scope-mismatch"}, status=409)
            for unit in units:
                if len(unit.get_source_plurals()) != 1:
                    return Response(
                        {"code": "mt-plurals-unsupported", "unit_id": unit.pk},
                        status=409,
                    )
                if unit.readonly or unit.state != 0 or any(unit.get_target_plurals()):
                    return Response(
                        {"code": "unit-not-empty", "unit_id": unit.pk}, status=409
                    )
                if unit_revision(unit) != revisions[unit.pk]:
                    return Response(
                        {"code": "stale-revision", "unit_id": unit.pk}, status=409
                    )
                if (
                    ProducerMTUnitAttempt.objects.filter(
                        unit=unit, revision=revisions[unit.pk]
                    )
                    .exclude(state="released")
                    .exists()
                ):
                    return Response(
                        {"code": "unit-already-reserved", "unit_id": unit.pk},
                        status=409,
                    )
            task_id = uuid4()
            run = ProducerRun.objects.create(
                actor=request.user,
                scope_type="translation",
                scope_id=str(translation.pk),
                scope_label=str(translation),
                scope_path=translation.get_absolute_url(),
                requested_mode="translate",
                requested_query="state:empty",
                cap=len(units),
                # Old outbox versions recognize execution>=1 as legacy MT.
                # Version0 + a new closed phase is deliberately unknown to them.
                execution_version=0,
                scope_snapshot=ids,
                dispatch_task_id=task_id,
                dispatch_phase="mt-batch",
                dispatch_requested_at=timezone.now(),
                execution_options={
                    **request_binding(request),
                    "mode": "translate",
                    "auto_source": "mt",
                    "engines": [payload["engine"]],
                    "threshold": payload["threshold"],
                    "overwrite_existing": False,
                },
            )
            operation = ProducerMTOperation.objects.create(
                actor=request.user,
                project=project,
                translation=translation,
                run=run,
                key=key,
                request_hash=digest,
                configuration_digest=machinery_config_digest(
                    project, payload["engine"]
                ),
                payload=payload,
            )
            ProducerMTUnitAttempt.objects.bulk_create(
                [
                    ProducerMTUnitAttempt(
                        operation=operation,
                        unit=u,
                        revision=revisions[u.pk],
                        audit_revision=compute_decision_revision(u),
                        source_revision=compute_decision_revision(u.source_unit),
                    )
                    for u in units
                ]
            )

            _schedule(run.pk)
            return Response(operation_result(operation), status=202)


class ProducerMTBatchDetail(APIView):
    permission_classes = (IsAuthenticated,)
    # Status reads spend no provider budget. Keep the ordinary API user limit;
    # the separate paid-admission ceiling belongs to POST only.
    throttle_classes = (UserRateThrottle, AnonRateThrottle)

    @extend_schema(responses=MTReceiptSerializer)
    def get(self, request, slug, key):
        project = get_object_or_404(request.user.allowed_projects, slug=slug)
        operation = get_object_or_404(
            ProducerMTOperation.objects.select_related("translation__component"),
            actor=request.user,
            project=project,
            key=key,
        )
        _check_access(request.user, operation.translation)
        return Response(operation_result(operation))


class ProducerMTBatchReconcile(ProducerMTBatchDetail):
    """No paid calls: release only provably unsent terminal reservations."""

    @extend_schema(request=None, responses=MTReceiptSerializer)
    def post(self, request, slug, key):
        project = get_object_or_404(request.user.allowed_projects, slug=slug)
        operation = get_object_or_404(
            ProducerMTOperation.objects.select_related("translation__component"),
            actor=request.user,
            project=project,
            key=key,
        )
        _check_access(request.user, operation.translation)
        reconciliation = reconcile_mt_operation(
            operation.pk,
            reauthorize_family=getattr(request, "wizard_delegation", None),
        )
        operation.refresh_from_db()
        return Response(
            {**operation_result(operation), "reconciliation": reconciliation}
        )
