# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Views for the producer console API.

Permissions are the existing ones: a producer sees exactly the projects
Weblate already allows them, and no new role is introduced.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from weblate.api.producer.serializers import (
    ProducerApplyCandidateRequestSerializer,
    ProducerBulkApplyRequestSerializer,
    ProducerBulkApplyResultSerializer,
    ProducerClarificationRequestSerializer,
    ProducerClarificationSerializer,
    ProducerDecisionSerializer,
    ProducerJudgeEstimateRequestSerializer,
    ProducerJudgeEstimateSerializer,
    ProducerMeSerializer,
    ProducerProjectSummarySerializer,
    ProducerRunResumeRequestSerializer,
    ProducerRunSerializer,
    ProducerRunStartRequestSerializer,
)
from weblate.api.serializers import ProducerConflictSerializer
from weblate.api.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)
from weblate.machinery.base import MACHINERY_DEFAULT_THRESHOLD
from weblate.trans.autotranslate import BatchAutoTranslate, PreparationScope
from weblate.trans.forms import configured_routed_engine
from weblate.trans.judge import judge_configuration_ready, judge_configuration_snapshot
from weblate.trans.judge_loop import (
    DEFAULT_CANDIDATE_SEVERITIES,
    accept_judge_candidate,
    undo_judge_application,
)
from weblate.trans.models import Project
from weblate.trans.models.judge import (
    JudgeApplication,
    JudgeApplicationUndoError,
    JudgeCandidateError,
    JudgeCandidateMetadata,
    JudgeVerdict,
    ProducerRun,
    UnitClarification,
    compute_decision_revision,
)
from weblate.trans.models.suggestion import Suggestion
from weblate.trans.models.unit import Unit
from weblate.trans.tasks import publish_producer_run_dispatch
from weblate.trans.views.judge import _get_scope, user_can_view_producer_run
from weblate.utils.state import STATE_APPROVED

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from rest_framework.request import Request

    from weblate.auth.models import User

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


def _preparation_estimate_payload(
    project: Project, user: User, scope: dict
) -> tuple[
    BatchAutoTranslate, object, list[Unit], dict, list[int], PreparationScope | None
]:
    """
    Build the shared estimate/start view of the run's two volumes (Task 3).

    Returns the batch, the judge preview, its selected units, the
    preparation summary and the payload's ``blockers``. A missing string
    past the judge cap is still prepared, so the preparation scope is
    deliberately built over the whole query, not the capped selection.
    Permission problems surface here as blockers, before any dispatch.
    """
    batch = BatchAutoTranslate(
        project,
        user=user,
        q=scope.get("query", ""),
        unit_ids=scope.get("unit_ids"),
        mode="judge",
        judge_proposal_only=True,
        judge_pretranslate=False,
        judge_mutating_repairs=False,
    )
    preview, units = batch.preview_judge_scope_snapshot()
    try:
        preparation = batch.build_preparation_scope()
    except PermissionDenied as error:
        blockers = [str(error)]
        preparation = None
    else:
        blockers = [
            warning
            for warning in batch.get_warnings()
            if "cannot be prepared" in warning or "configured" in warning
        ]
    summary = {
        "missing": len(preparation.missing_ids) if preparation else 0,
        "per_language": (
            dict(sorted(preparation.per_language_missing.items()))
            if preparation
            else {}
        ),
        "engine": preparation.mt_engine if preparation else None,
        "blockers": blockers,
        # A real cost basis needs LLMUsageLog history for this engine; the
        # honest default is "unknown" until such a basis is measured.
        "mt_cost_known": False,
    }
    return batch, preview, units, summary, blockers, preparation


def _scope_hash_for(
    project: Project,
    scope: dict,
    units: list[Unit],
    preparation: dict,
    execution_version: int = 1,
) -> str:
    """Bind the estimate to its exact selection, MT volume and execution version."""
    return hashlib.sha256(
        json.dumps(
            {
                "execution_version": execution_version,
                "project": project.pk,
                "scope": scope,
                "unit_ids": [unit.pk for unit in units],
                "preparation": preparation,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class ProducerProjectJudgeEstimate(APIView):
    """Estimate the permission-filtered judge scope without contacting a provider."""

    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle, AnonRateThrottle, ScopedRateThrottle)
    throttle_scope = "producer"
    request: AuthenticatedRequest  # type: ignore[assignment]

    @extend_schema(
        request=ProducerJudgeEstimateRequestSerializer,
        responses={
            200: ProducerJudgeEstimateSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def post(self, request: Request, slug: str) -> Response:
        serializer = ProducerJudgeEstimateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = get_object_or_404(self.request.user.allowed_projects, slug=slug)
        if not judge_configuration_ready():
            return Response(
                {"detail": "Judge is not configured.", "code": "not-configured"},
                status=409,
            )
        scope = serializer.validated_data.get("scope", {})
        _batch, preview, units, preparation, _blockers, _scope = (
            _preparation_estimate_payload(project, self.request.user, scope)
        )
        scope_hash = _scope_hash_for(
            project, scope, units, preparation, execution_version=1
        )
        estimate = ProducerRun.objects.create(
            actor=self.request.user,
            scope_type=ProducerRun.ScopeType.PROJECT,
            scope_id=str(project.pk),
            scope_label=str(project),
            scope_path=project.get_absolute_url(),
            requested_query=scope.get("query", ""),
            requested_mode="judge-estimate",
            cap=len(units),
            execution_version=1,
            scope_cursor=0,
            scope_hash=scope_hash,
            scope_snapshot=[unit.pk for unit in units],
            configuration_snapshot=judge_configuration_snapshot(),
        )
        payload = {
            "estimate_id": str(estimate.pk),
            "scope_hash": scope_hash,
            "strings": preview.processed,
            "selected": preview.matched,
            "excluded": preview.matched - preview.processed,
            "initial_calls": preview.initial_calls,
            "worst_case_calls": preview.worst_case_calls,
            "preparation": preparation,
            "basis": "Worst case includes live-text judge rounds only. "
            "The preparation cost is reported separately and is not "
            "included in the judge call counts.",
        }
        return Response(ProducerJudgeEstimateSerializer(payload).data)


class ProducerProjectRunStart(APIView):
    """Start the run an estimate priced, atomically fenced against drift."""

    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle, AnonRateThrottle, ScopedRateThrottle)
    throttle_scope = "producer"
    request: AuthenticatedRequest  # type: ignore[assignment]

    @extend_schema(
        request=ProducerRunStartRequestSerializer,
        responses={
            200: ProducerRunSerializer,
            201: ProducerRunSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def post(self, request: Request, slug: str) -> Response:
        serializer = ProducerRunStartRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = get_object_or_404(self.request.user.allowed_projects, slug=slug)
        estimate = get_object_or_404(
            ProducerRun,
            pk=serializer.validated_data["estimate_id"],
            actor=self.request.user,
            scope_type=ProducerRun.ScopeType.PROJECT,
            scope_id=str(project.pk),
            requested_mode="judge-estimate",
        )
        if estimate.execution_version != 1:
            return Response(
                {
                    "detail": "The estimate uses a legacy execution contract.",
                    "code": "estimate-drift",
                },
                status=409,
            )
        scope = serializer.validated_data.get("scope", {})
        if scope.get("query", "") != estimate.requested_query:
            return Response(
                {
                    "detail": "The scope no longer matches the estimate.",
                    "code": "estimate-drift",
                },
                status=409,
            )
        if not judge_configuration_ready():
            return Response(
                {"detail": "Judge is not configured.", "code": "not-configured"},
                status=409,
            )
        if judge_configuration_snapshot() != estimate.configuration_snapshot:
            return Response(
                {
                    "detail": "The judge configuration changed since the estimate.",
                    "code": "estimate-drift",
                },
                status=409,
            )
        _batch, _preview, units, preparation, blockers, preparation_scope = (
            _preparation_estimate_payload(project, self.request.user, scope)
        )
        scope_hash = _scope_hash_for(
            project, scope, units, preparation, execution_version=1
        )
        if scope_hash != estimate.scope_hash:
            return Response(
                {
                    "detail": "The scope changed since the estimate.",
                    "code": "estimate-drift",
                },
                status=409,
            )
        if blockers:
            # A preparation blocker (permission, no engine) is a pre-flight
            # refusal: the run never dispatches with a known-bad barrier.
            return Response(
                {
                    "detail": " ".join(blockers),
                    "code": "preparation-blocked",
                },
                status=409,
            )

        execution_options = {
            "mode": "judge",
            "q": estimate.requested_query,
            "auto_source": "mt",
            "engines": [],
            "threshold": MACHINERY_DEFAULT_THRESHOLD,
            "judge_proposal_only": True,
            "judge_pretranslate": False,
            "judge_mutating_repairs": False,
            "judge_candidate_severities": list(DEFAULT_CANDIDATE_SEVERITIES),
            "overwrite_existing": False,
        }
        idempotency_key = str(estimate.pk)
        try:
            with transaction.atomic():
                run = ProducerRun.objects.create(
                    actor=self.request.user,
                    dispatch_task_id=uuid4(),
                    dispatch_phase="judge-project",
                    dispatch_requested_at=timezone.now(),
                    scope_type=ProducerRun.ScopeType.PROJECT,
                    scope_id=str(project.pk),
                    scope_label=str(project),
                    scope_path=project.get_absolute_url(),
                    requested_query=estimate.requested_query,
                    requested_mode="judge",
                    cap=len(units),
                    execution_version=1,
                    scope_cursor=0,
                    execution_options=execution_options,
                    scope_hash=scope_hash,
                    scope_snapshot=[unit.pk for unit in units],
                    configuration_snapshot=judge_configuration_snapshot(),
                    idempotency_key=idempotency_key,
                    # Task 3: the closed preparation scope is fixed at start,
                    # before dispatch. A run dispatched without it is legacy by
                    # definition, and the executor must refuse rather than
                    # resume with a silently new MT volume.
                    preparation_snapshot=(
                        preparation_scope.to_json() if preparation_scope else {}
                    ),
                    preparation_phase="pending",
                )
        except IntegrityError:
            # A concurrent identical POST already reserved this estimate's
            # run: return that one run, not a duplicate.
            run = ProducerRun.objects.get(
                actor=self.request.user,
                scope_type=ProducerRun.ScopeType.PROJECT,
                scope_id=str(project.pk),
                requested_mode="judge",
                idempotency_key=idempotency_key,
            )
            created = False
        else:
            created = True
        if created:
            transaction.on_commit(lambda: publish_producer_run_dispatch(run_id=run.pk))
        return Response(ProducerRunSerializer(run).data, status=201 if created else 200)


class ProducerRunDetail(APIView):
    """Read one durable run with current scope permissions."""

    permission_classes = (IsAuthenticated,)
    serializer_class = ProducerRunSerializer
    request: AuthenticatedRequest  # type: ignore[assignment]

    def _get_run(self, pk) -> ProducerRun:
        run = get_object_or_404(ProducerRun, pk=pk)
        try:
            scope = _get_scope(run)
        except Http404:
            raise Http404 from None
        if not user_can_view_producer_run(self.request.user, scope, run):
            raise Http404
        return run

    @extend_schema(responses=ProducerRunSerializer)
    def get(self, request: Request, pk) -> Response:
        return Response(ProducerRunSerializer(self._get_run(pk)).data)


class ProducerRunCancel(ProducerRunDetail):
    """Request cancellation before the next worker dispatch."""

    @extend_schema(responses=ProducerRunSerializer)
    def post(self, request: Request, pk) -> Response:
        run = self._get_run(pk)
        if run.status in {ProducerRun.Status.QUEUED, ProducerRun.Status.RUNNING}:
            run.status = ProducerRun.Status.CANCEL_REQUESTED
            run.save(update_fields=["status"])
        return Response(ProducerRunSerializer(run).data)


class ProducerRunResume(ProducerRunDetail):
    """
    Retry a technically-failed run's remaining work as a new linked run.

    Resume is for technical failures only (Task 5b): a run whose seats
    disagreed, produced a candidate awaiting review, or was cancelled is
    not "failed" and is not resumable through this endpoint. The new run
    replays the same scope; ``_cached_verdict`` (keyed by unit, request
    and profile, not by run) keeps an unchanged already-judged string
    from being paid for twice.
    """

    throttle_classes = (UserRateThrottle, AnonRateThrottle, ScopedRateThrottle)
    throttle_scope = "producer"

    @extend_schema(
        request=ProducerRunResumeRequestSerializer,
        responses={
            200: ProducerRunSerializer,
            201: ProducerRunSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def post(self, request: Request, pk) -> Response:
        serializer = ProducerRunResumeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = self._get_run(pk)
        if run.status != ProducerRun.Status.FAILED:
            return Response(
                {
                    "detail": "Only a failed run can be resumed.",
                    "code": "not-resumable",
                },
                status=409,
            )
        attempt = serializer.validated_data["attempt"]
        # A resume re-runs the remaining preparation with the *current*
        # engine settings. When the project's routed MT engine changed since
        # the run fixed its preparation scope, the old consent covers a
        # different spend than the resume would make: refuse and require a
        # fresh estimate. A provider quota increase without a configuration
        # change keeps the ordinary explicit resume path.
        if run.scope_type == ProducerRun.ScopeType.PROJECT and run.preparation_snapshot:
            current_engine = configured_routed_engine(
                Project.objects.filter(pk=run.scope_id)
                .values_list("machinery_settings", flat=True)
                .first()
                or {}
            )
            if current_engine != run.preparation_snapshot.get("mt_engine"):
                return Response(
                    {
                        "detail": "The machine translation configuration changed "
                        "since this run; estimate and start a new run.",
                        "code": "estimate-drift",
                    },
                    status=409,
                )
        idempotency_key = f"resume:{run.pk}:{attempt}"
        try:
            with transaction.atomic():
                resumed = ProducerRun.objects.create(
                    actor=self.request.user,
                    dispatch_task_id=uuid4(),
                    dispatch_phase=run.dispatch_phase,
                    dispatch_requested_at=timezone.now(),
                    scope_type=run.scope_type,
                    scope_id=run.scope_id,
                    scope_label=run.scope_label,
                    scope_path=run.scope_path,
                    requested_query=run.requested_query,
                    requested_mode=run.requested_mode,
                    cap=run.cap,
                    scope_hash=run.scope_hash,
                    scope_snapshot=run.scope_snapshot,
                    configuration_snapshot=run.configuration_snapshot,
                    preparation_snapshot=run.preparation_snapshot,
                    preparation_phase=(
                        # A blocked preparation retries only its remaining
                        # missing strings; the closed scope never widens.
                        "pending"
                        if run.preparation_phase == "blocked"
                        else run.preparation_phase
                    ),
                    resumed_from=run,
                    idempotency_key=idempotency_key,
                )
        except IntegrityError:
            # A concurrent identical POST already reserved this resume: return
            # that one run, not a duplicate.
            resumed = ProducerRun.objects.get(
                actor=self.request.user,
                scope_type=run.scope_type,
                scope_id=run.scope_id,
                requested_mode=run.requested_mode,
                idempotency_key=idempotency_key,
            )
            created = False
        else:
            created = True
        if created:
            transaction.on_commit(
                lambda: publish_producer_run_dispatch(run_id=resumed.pk)
            )
        return Response(
            ProducerRunSerializer(resumed).data, status=201 if created else 200
        )


def _decision_response(
    unit: Unit, application: JudgeApplication | None = None
) -> Response:
    refreshed = Unit.objects.get(pk=unit.pk)
    return Response(
        ProducerDecisionSerializer(
            {
                "unit_id": refreshed.pk,
                "revision": compute_decision_revision(refreshed),
                "state": refreshed.state,
                "target": refreshed.target,
                "application_id": application.pk if application is not None else None,
            }
        ).data
    )


class ProducerUnitApplyCandidate(APIView):
    """
    Apply one already-verified judge repair candidate (Task 6, G5).

    ``accept_judge_candidate`` remains the sole write primitive; this view
    only adds the revision staleness check and the HTTP error shape. A
    stale ``revision`` is a client-visible ``409``, distinct from the
    primitive's own ``400`` verification failures.
    """

    permission_classes = (IsAuthenticated,)
    request: AuthenticatedRequest

    @extend_schema(
        request=ProducerApplyCandidateRequestSerializer,
        responses={
            200: ProducerDecisionSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def post(self, request: Request, pk) -> Response:
        serializer = ProducerApplyCandidateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unit = get_object_or_404(Unit.objects.filter_access(self.request.user), pk=pk)
        if not self.request.user.has_perm(
            "unit.review", unit
        ) or not self.request.user.has_perm("translation.auto", unit.translation):
            self.permission_denied(request)
        if compute_decision_revision(unit) != serializer.validated_data["revision"]:
            return Response(
                {
                    "detail": "This decision is stale; reload and try again.",
                    "code": "stale-revision",
                },
                status=409,
            )
        candidate = get_object_or_404(
            Suggestion, pk=serializer.validated_data["candidate_id"], unit=unit
        )
        try:
            application = accept_judge_candidate(
                candidate,
                self.request,
                acknowledge=serializer.validated_data.get("acknowledge"),
            )
        except JudgeCandidateError as error:
            return Response({"detail": str(error), "code": "not-verified"}, status=400)
        return _decision_response(unit, application)


class ProducerProjectDecisionsApply(APIView):
    """
    Bulk-apply already-verified, ordinary judge candidates for one project.

    Every row is independent (Task 6): one row's failure never rolls back
    another, and every row reports its own honest outcome rather than a
    single pass/fail for the whole batch. A row is never applied through
    this endpoint without a per-row ``acknowledge`` field to attach it to
    (G6); it is instead surfaced as ``needs_individual_confirmation`` so the
    producer repeats it, one string at a time, through
    ``ProducerUnitApplyCandidate``.
    """

    permission_classes = (IsAuthenticated,)
    request: AuthenticatedRequest

    @extend_schema(
        request=ProducerBulkApplyRequestSerializer,
        responses=ProducerBulkApplyResultSerializer(many=True),
    )
    def post(self, request: Request, slug: str) -> Response:
        project = get_object_or_404(self.request.user.allowed_projects, slug=slug)
        serializer = ProducerBulkApplyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = [
            self._apply_one(project, item)
            for item in serializer.validated_data["items"]
        ]
        return Response(ProducerBulkApplyResultSerializer(results, many=True).data)

    def _apply_one(self, project: Project, item: dict) -> dict:
        unit_id = item["unit"]
        try:
            unit = Unit.objects.filter_access(self.request.user).get(
                pk=unit_id, translation__component__project=project
            )
        except Unit.DoesNotExist:
            return {"unit": unit_id, "outcome": "forbidden"}
        if not self.request.user.has_perm(
            "unit.review", unit
        ) or not self.request.user.has_perm("translation.auto", unit.translation):
            return {"unit": unit_id, "outcome": "forbidden"}
        # Candidate existence is checked before revision staleness: a
        # completed apply always advances the revision too, so a retry
        # (or a losing side of a concurrent apply race) must report the
        # more specific "already_applied" rather than a generic "stale".
        candidate = Suggestion.objects.filter(
            pk=item["candidate_id"], unit=unit
        ).first()
        if candidate is None:
            return {"unit": unit_id, "outcome": "already_applied"}
        if compute_decision_revision(unit) != item["revision"]:
            return {"unit": unit_id, "outcome": "stale"}
        if self._needs_individual_confirmation(unit, candidate):
            return {"unit": unit_id, "outcome": "needs_individual_confirmation"}
        try:
            accept_judge_candidate(candidate, self.request)
        except JudgeCandidateError:
            if not Suggestion.objects.filter(pk=candidate.pk).exists():
                # Lost a race to a concurrent apply of the same candidate,
                # not a validation failure.
                return {"unit": unit_id, "outcome": "already_applied"}
            return {"unit": unit_id, "outcome": "not_verified"}
        return {"unit": unit_id, "outcome": "applied"}

    @staticmethod
    def _needs_individual_confirmation(unit: Unit, candidate: Suggestion) -> bool:
        if unit.state == STATE_APPROVED:
            return True
        metadata = JudgeCandidateMetadata.parse(candidate.userdetails)
        if metadata is None:
            return False
        verdict = JudgeVerdict.objects.filter(pk=metadata.verdict_id).first()
        if verdict is None:
            return False
        return any(
            isinstance(error, dict) and error.get("category") == "terminology"
            for error in verdict.errors
        )


class ProducerUnitClarification(APIView):
    """
    Read or answer a meaning-clarifying question for one target unit (Task 7).

    Writing an answer never calls an LLM, applies a candidate, or changes
    target/state/glossary; it only extends what ``compute_context_hash``
    covers for this exact unit, so a fresh answer invalidates cached judge
    and back-translation evidence the same way any other context change
    does. ``revision`` is the same opaque per-unit staleness token every
    other decision endpoint uses.
    """

    permission_classes = (IsAuthenticated,)
    request: AuthenticatedRequest

    def _get_unit(self, pk) -> Unit:
        unit = get_object_or_404(Unit.objects.filter_access(self.request.user), pk=pk)
        if not self.request.user.has_perm(
            "unit.review", unit
        ) or not self.request.user.has_perm("translation.auto", unit.translation):
            self.permission_denied(self.request)
        return unit

    @staticmethod
    def _payload(unit: Unit, clarification: UnitClarification | None) -> dict:
        return {
            "answer": clarification.answer if clarification is not None else "",
            "answered_by": (
                clarification.actor.username
                if clarification is not None and clarification.actor is not None
                else None
            ),
            "answered_at": (
                clarification.updated_at if clarification is not None else None
            ),
            "revision": compute_decision_revision(unit),
        }

    @extend_schema(responses=ProducerClarificationSerializer)
    def get(self, request: Request, pk) -> Response:
        unit = self._get_unit(pk)
        clarification = UnitClarification.objects.filter(unit=unit).first()
        return Response(
            ProducerClarificationSerializer(self._payload(unit, clarification)).data
        )

    @extend_schema(
        request=ProducerClarificationRequestSerializer,
        responses={
            200: ProducerClarificationSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def patch(self, request: Request, pk) -> Response:
        unit = self._get_unit(pk)
        serializer = ProducerClarificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if compute_decision_revision(unit) != serializer.validated_data["revision"]:
            return Response(
                {
                    "detail": "This decision is stale; reload and try again.",
                    "code": "stale-revision",
                },
                status=409,
            )
        clarification, _created = UnitClarification.objects.update_or_create(
            unit=unit,
            defaults={
                "answer": serializer.validated_data["answer"],
                "actor": self.request.user,
            },
        )
        return Response(
            ProducerClarificationSerializer(self._payload(unit, clarification)).data
        )


class ProducerJudgeApplicationUndo(APIView):
    """
    Undo one previously applied judge repair candidate (Task 8).

    ``undo_judge_application`` remains the sole write primitive; this view
    only adds the scoped lookup and the HTTP error shape. The route is
    flat (``judge-applications/{id}/undo/``): the receipt's own id names
    the application to undo, not the unit or run it happened under.
    """

    permission_classes = (IsAuthenticated,)
    request: AuthenticatedRequest

    @extend_schema(
        request=None,
        responses={
            200: ProducerDecisionSerializer,
            409: ProducerConflictSerializer,
        },
    )
    def post(self, request: Request, pk) -> Response:
        application = get_object_or_404(
            JudgeApplication.objects.filter(
                unit__in=Unit.objects.filter_access(self.request.user)
            ),
            pk=pk,
        )
        if not self.request.user.has_perm(
            "translation.auto", application.unit.translation
        ):
            self.permission_denied(request)
        try:
            undo_judge_application(application, self.request)
        except JudgeApplicationUndoError as error:
            status = 403 if error.code == "forbidden" else 409
            return Response({"detail": str(error), "code": error.code}, status=status)
        return _decision_response(application.unit)
