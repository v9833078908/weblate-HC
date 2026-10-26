# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""URLs for the producer console API."""

from django.urls import path

from weblate.api.producer.mt import (
    ProducerMTBatch,
    ProducerMTBatchDetail,
    ProducerMTBatchReconcile,
)
from weblate.api.producer.profile import ProducerTranslationProfile
from weblate.api.producer.repeat_views import (
    ProducerProjectRepeatPlan,
    ProducerRepeatPlanApply,
    ProducerRepeatPlanDetail,
    ProducerRepeatRunDetail,
)
from weblate.api.producer.views import (
    ProducerJudgeApplicationUndo,
    ProducerMe,
    ProducerProjectDecisions,
    ProducerProjectDecisionsApply,
    ProducerProjectJudgeEstimate,
    ProducerProjectQualityIssues,
    ProducerProjectQualityRecheck,
    ProducerProjectQualitySnapshot,
    ProducerProjectRunStart,
    ProducerProjects,
    ProducerQualityOperationDetail,
    ProducerRunCancel,
    ProducerRunDetail,
    ProducerRunResume,
    ProducerUnitApplyCandidate,
    ProducerUnitClarification,
)

urlpatterns = [
    path(
        "projects/<slug:slug>/translation-profile/",
        ProducerTranslationProfile.as_view(),
        name="producer-translation-profile",
    ),
    path(
        "projects/<slug:slug>/mt/batches/<uuid:key>/reconcile/",
        ProducerMTBatchReconcile.as_view(),
        name="producer-mt-batch-reconcile",
    ),
    path(
        "projects/<slug:slug>/mt/batches/",
        ProducerMTBatch.as_view(),
        name="producer-mt-batch",
    ),
    path(
        "projects/<slug:slug>/mt/batches/<uuid:key>/",
        ProducerMTBatchDetail.as_view(),
        name="producer-mt-batch-detail",
    ),
    path("me/", ProducerMe.as_view(), name="producer-me"),
    path("projects/", ProducerProjects.as_view(), name="producer-projects"),
    path(
        "projects/<slug:slug>/quality/repeats/plan/",
        ProducerProjectRepeatPlan.as_view(),
        name="producer-project-repeat-plan",
    ),
    path(
        "repeat-plans/<uuid:token>/",
        ProducerRepeatPlanDetail.as_view(),
        name="producer-repeat-plan-detail",
    ),
    path(
        "repeat-plans/<uuid:token>/apply/",
        ProducerRepeatPlanApply.as_view(),
        name="producer-repeat-plan-apply",
    ),
    path(
        "repeat-runs/<uuid:token>/",
        ProducerRepeatRunDetail.as_view(),
        name="producer-repeat-run-detail",
    ),
    path("runs/<uuid:pk>/", ProducerRunDetail.as_view(), name="producer-run-detail"),
    path(
        "projects/<slug:slug>/quality/issues/",
        ProducerProjectQualityIssues.as_view(),
        name="producer-project-quality-issues",
    ),
    path(
        "projects/<slug:slug>/quality/operations/<uuid:operation_id>/",
        ProducerQualityOperationDetail.as_view(),
        name="producer-quality-operation-detail",
    ),
    path(
        "projects/<slug:slug>/quality/snapshot/",
        ProducerProjectQualitySnapshot.as_view(),
        name="producer-project-quality-snapshot",
    ),
    path(
        "projects/<slug:slug>/quality/recheck/",
        ProducerProjectQualityRecheck.as_view(),
        name="producer-project-quality-recheck",
    ),
    path(
        "projects/<slug:slug>/runs/estimate/",
        ProducerProjectJudgeEstimate.as_view(),
        name="producer-project-judge-estimate",
    ),
    path(
        "projects/<slug:slug>/runs/",
        ProducerProjectRunStart.as_view(),
        name="producer-project-run-start",
    ),
    path(
        "runs/<uuid:pk>/cancel/",
        ProducerRunCancel.as_view(),
        name="producer-run-cancel",
    ),
    path(
        "runs/<uuid:pk>/resume/",
        ProducerRunResume.as_view(),
        name="producer-run-resume",
    ),
    path(
        "decisions/<int:pk>/apply-candidate/",
        ProducerUnitApplyCandidate.as_view(),
        name="producer-decision-apply-candidate",
    ),
    path(
        "projects/<slug:slug>/decisions/",
        ProducerProjectDecisions.as_view(),
        name="producer-project-decisions",
    ),
    path(
        "projects/<slug:slug>/decisions/apply/",
        ProducerProjectDecisionsApply.as_view(),
        name="producer-project-decisions-apply",
    ),
    path(
        "decisions/<int:pk>/clarification/",
        ProducerUnitClarification.as_view(),
        name="producer-decision-clarification",
    ),
    path(
        "judge-applications/<int:pk>/undo/",
        ProducerJudgeApplicationUndo.as_view(),
        name="producer-judge-application-undo",
    ),
]
