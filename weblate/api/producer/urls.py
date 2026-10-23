# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""URLs for the producer console API."""

from django.urls import path

from weblate.api.producer.views import (
    ProducerJudgeApplicationUndo,
    ProducerMe,
    ProducerProjectDecisionsApply,
    ProducerProjectJudgeEstimate,
    ProducerProjectRunStart,
    ProducerProjects,
    ProducerRunCancel,
    ProducerRunDetail,
    ProducerRunResume,
    ProducerUnitApplyCandidate,
    ProducerUnitClarification,
)

urlpatterns = [
    path("me/", ProducerMe.as_view(), name="producer-me"),
    path("projects/", ProducerProjects.as_view(), name="producer-projects"),
    path("runs/<uuid:pk>/", ProducerRunDetail.as_view(), name="producer-run-detail"),
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
