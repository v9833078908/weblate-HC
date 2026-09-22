# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.lang.models import Language
from weblate.trans.models import Project, RepeatGroup, RepeatPolicy
from weblate.trans.repeat_recommendations import (
    prepare_run,
    queue_attempt,
    reserve_attempt,
)
from weblate.trans.repeats import (
    apply_preview,
    detect_policy_groups,
    get_or_create_group,
    policy_units,
    preview_group,
)


@login_required
def repeat_queue(request, project: str, language: str):
    """Render one project/language repeat queue using the variant-C hierarchy."""
    obj = get_object_or_404(Project, slug=project)
    if not request.user.can_access_project(obj):
        raise PermissionDenied
    target_language = get_object_or_404(Language, code=language)
    policy = RepeatPolicy.objects.filter(
        project=obj, target_language=target_language, enabled=True
    ).first()
    groups = []
    if policy is not None:
        visible_units = policy_units(policy).filter_access(request.user)
        for candidate in detect_policy_groups(policy, user=request.user):
            unit = visible_units.get(pk=candidate.unit_ids[0])
            group = get_or_create_group(policy, unit)
            units = list(visible_units.filter(pk__in=candidate.unit_ids).order_by("pk"))
            variants = sorted({unit.target for unit in units})
            groups.append({"group": group, "units": units, "variants": variants})
    return render(
        request,
        "repeat_queue.html",
        {
            "project": obj,
            "language": target_language,
            "policy": policy,
            "groups": groups,
        },
    )


@login_required
@require_POST
def repeat_preview(request, group_id: int):
    """Show the server-derived before/after view without writing targets."""
    group = get_object_or_404(RepeatGroup, pk=group_id)
    if not request.user.can_access_project(group.policy.project):
        raise PermissionDenied
    target = request.POST.get("target", "")
    if not target:
        msg = "Choose a target before previewing the repeat decision."
        raise ValidationError(msg)
    preview = preview_group(group=group, target=[target], actor=request.user)
    return render(request, "repeat_preview.html", {"group": group, "preview": preview})


@login_required
@require_POST
def repeat_apply(request):
    """Commit a previewed decision and render a per-recipient report."""
    event = apply_preview(token=request.POST["token"], actor=request.user)
    return render(request, "repeat_report.html", {"event": event})


@login_required
@require_POST
def repeat_recommend(request, project: str, language: str):
    """Explicitly reserve and queue a bounded recommendation request."""
    policy = get_object_or_404(
        RepeatPolicy,
        project__slug=project,
        target_language__code=language,
        enabled=True,
    )
    try:
        request_cap = int(request.POST["request_cap"])
    except (KeyError, TypeError, ValueError) as error:
        msg = "A positive recommendation request cap is required."
        raise ValidationError(msg) from error
    run = prepare_run(policy=policy, actor=request.user, request_cap=request_cap)
    attempt = reserve_attempt(
        run=run,
        request_snapshot={"groups": run.snapshot["groups"]},
    )
    queue_attempt(attempt=attempt)
    messages.success(request, gettext("Repeat recommendations were queued."))
    return redirect("repeat-queue", project=project, language=language)
