# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from weblate.lang.models import Language
from weblate.trans.models import Project, RepeatGroup, RepeatPolicy, Unit
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
        for candidate in detect_policy_groups(policy):
            unit = policy_units(policy).get(pk=candidate.unit_ids[0])
            group = get_or_create_group(policy, unit)
            units = list(Unit.objects.filter(pk__in=candidate.unit_ids).order_by("pk"))
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
