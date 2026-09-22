# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, bounded preparation of read-only repeat recommendations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from weblate.trans.judge import judge_primary_endpoint, resolve_judge_seat_profile
from weblate.trans.models import (
    RepeatRecommendationAttempt,
    RepeatRecommendationRun,
)
from weblate.trans.repeats import (
    detect_policy_groups,
    fingerprint,
    get_or_create_group,
    policy_units,
)

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.trans.models.repeat import RepeatPolicy


REPEAT_RECOMMENDATION_PROMPT_REVISION = "repeat-recommendation-v1"


def prepare_run(*, policy: RepeatPolicy, actor: User, request_cap: int):
    """Freeze a visible policy scope and its sole primary-seat profile."""
    if request_cap < 1:
        raise ValidationError("The recommendation request cap must be positive.")
    if not actor.has_perm("project.edit", policy.project):
        raise PermissionDenied
    # Passing the primary endpoint avoids resolve_judge_seat_profile()'s
    # pair-wide validation and therefore never consults/falls back to seat 2.
    profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
    groups = []
    for candidate in detect_policy_groups(policy):
        unit = policy_units(policy).get(pk=candidate.unit_ids[0])
        group = get_or_create_group(policy, unit)
        groups.append(
            {
                "group": group.pk,
                "group_revision": group.revision,
                "source_forms": list(candidate.source_forms),
                "unit_ids": list(candidate.unit_ids),
            }
        )
    snapshot = {"policy_revision": policy.revision, "groups": groups}
    return RepeatRecommendationRun.objects.create(
        policy=policy,
        actor=actor,
        snapshot=snapshot,
        snapshot_fingerprint=fingerprint(snapshot),
        profile_fingerprint=profile.profile_fingerprint,
        prompt_fingerprint=fingerprint(REPEAT_RECOMMENDATION_PROMPT_REVISION),
        request_cap=request_cap,
    )


def reserve_attempt(*, run: RepeatRecommendationRun, request_snapshot: dict):
    """Reserve one request before I/O; unknown sends are deliberately not replayed."""
    with transaction.atomic():
        run = RepeatRecommendationRun.objects.select_for_update().get(pk=run.pk)
        if run.status in {
            RepeatRecommendationRun.Status.CANCELLED,
            RepeatRecommendationRun.Status.COMPLETED,
            RepeatRecommendationRun.Status.FAILED,
        }:
            raise ValidationError("This recommendation run cannot send another request.")
        if run.requests_reserved >= run.request_cap:
            raise ValidationError("The recommendation request cap has been reached.")
        run.requests_reserved += 1
        run.status = RepeatRecommendationRun.Status.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["requests_reserved", "status", "started_at"])
        return RepeatRecommendationAttempt.objects.create(
            run=run,
            ordinal=run.requests_reserved,
            request_snapshot=request_snapshot,
        )


def cancel_run(*, run: RepeatRecommendationRun, actor: User) -> None:
    """Prevent future requests while preserving every durable attempt/result."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    run.status = RepeatRecommendationRun.Status.CANCELLED
    run.cancelled_at = timezone.now()
    run.save(update_fields=["status", "cancelled_at"])
