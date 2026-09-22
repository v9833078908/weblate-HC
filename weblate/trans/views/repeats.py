# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.lang.models import Language
from weblate.trans.forms import RepeatPolicyForm
from weblate.trans.models import (
    Component,
    Label,
    Project,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationResult,
)
from weblate.trans.repeat_recommendations import (
    prepare_run,
    queue_attempt,
    reserve_attempt,
)
from weblate.trans.repeats import (
    apply_preview,
    detect_policy_groups,
    get_or_create_group,
    keep_group_independent,
    policy_overlaps,
    policy_units,
    preview_group,
)
from weblate.utils.state import STATE_APPROVED


def _selected_ids(request, name: str) -> set[int]:
    """Parse repeated GET selector values without trusting malformed values."""
    result = set()
    for value in request.GET.getlist(name):
        try:
            result.add(int(value))
        except ValueError:
            continue
    return result


def _group_status(group, units) -> str:
    """Return the user-facing queue state, separate from recipient notes."""
    approved_targets = {
        tuple(unit.get_target_plurals())
        for unit in units
        if unit.state == STATE_APPROVED
    }
    if policy_overlaps(group.policy, exclude_policy_id=group.policy_id):
        return "rule-conflict"
    if len(approved_targets) > 1:
        return "approved-conflict"
    if group.shared_target or group.decision_origin == "independent":
        return "resolved"
    return "open"


def _queue_groups(request, policy):
    """Build filtered, permission-safe view data for the repeat queue."""
    component_ids = _selected_ids(request, "component")
    label_ids = _selected_ids(request, "label")
    visible_units = policy_units(policy).filter_access(request.user)
    if component_ids:
        visible_units = visible_units.filter(
            translation__component_id__in=component_ids
        )
    if label_ids:
        visible_units = visible_units.filter(
            source_unit__labels__in=label_ids
        ).distinct()

    groups = []
    for candidate in detect_policy_groups(policy, user=request.user):
        units = list(visible_units.filter(pk__in=candidate.unit_ids).order_by("pk"))
        if len(units) < 2:
            continue
        group = get_or_create_group(policy, units[0])
        variants = {}
        for unit in units:
            variants.setdefault(tuple(unit.get_target_plurals()), []).append(unit)
        status = _group_status(group, units)
        recommendation = (
            RepeatRecommendationResult.objects.filter(
                group=group,
                group_revision=group.revision,
                run__status="completed",
            )
            .order_by("-created_at")
            .first()
        )
        groups.append(
            {
                "group": group,
                "units": units,
                "variants": [
                    {"target": target, "units": grouped_units}
                    for target, grouped_units in sorted(
                        variants.items(), key=lambda item: (-len(item[1]), item[0])
                    )
                ],
                "status": status,
                "recommendation": recommendation,
                "approved_conflict": status == "approved-conflict",
                "all_approved": all(unit.state == STATE_APPROVED for unit in units),
                "writable_count": sum(
                    unit.state != STATE_APPROVED
                    and not unit.translation.component.locked
                    and bool(request.user.has_perm("unit.edit", unit))
                    for unit in units
                ),
            }
        )
    status_order = {
        "rule-conflict": 0,
        "approved-conflict": 1,
        "open": 2,
        "resolved": 3,
    }
    return sorted(
        groups, key=lambda item: (status_order[item["status"]], -len(item["units"]))
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
    groups = _queue_groups(request, policy) if policy is not None else []
    requested_status = request.GET.get("status", "all")
    status_aliases = {
        "all": None,
        "open": "open",
        "conflict": {"rule-conflict", "approved-conflict"},
        "resolved": "resolved",
    }
    if requested_status not in status_aliases:
        requested_status = "all"
    wanted = status_aliases[requested_status]
    filtered_groups = [
        item
        for item in groups
        if wanted is None or item["status"] == wanted or item["status"] in wanted
    ]
    status_counts = {
        "all": len(groups),
        "open": sum(item["status"] == "open" for item in groups),
        "conflict": sum(
            item["status"] in {"rule-conflict", "approved-conflict"} for item in groups
        ),
        "resolved": sum(item["status"] == "resolved" for item in groups),
    }
    page_obj = Paginator(filtered_groups, 20).get_page(request.GET.get("page"))
    visible_components = Component.objects.filter(project=obj).filter_access(
        request.user
    )
    visible_labels = Label.objects.filter(project=obj).order_by("name")
    selected_components = _selected_ids(request, "component")
    selected_labels = _selected_ids(request, "label")
    return render(
        request,
        "repeat_queue.html",
        {
            "project": obj,
            "language": target_language,
            "policy": policy,
            "groups": page_obj.object_list,
            "page_obj": page_obj,
            "status": requested_status,
            "status_counts": status_counts,
            "components": visible_components,
            "labels": visible_labels,
            "selected_components": selected_components,
            "selected_labels": selected_labels,
            "open_group": request.GET.get("group"),
            "shown_count": len(page_obj.object_list),
            "total_count": len(filtered_groups),
        },
    )


@login_required
@require_POST
def repeat_preview(request, group_id: int):
    """Show the server-derived before/after view without writing targets."""
    group = get_object_or_404(RepeatGroup, pk=group_id)
    if not request.user.can_access_project(group.policy.project):
        raise PermissionDenied
    choice = request.POST.get("choice", "")
    if choice == "keep":
        event = keep_group_independent(group=group, actor=request.user)
        return render(request, "repeat_report.html", {"event": event})
    target = request.POST.getlist("target")
    if choice == "custom":
        target = request.POST.getlist("custom_target")
    if not target or not all(target):
        msg = "Choose a target before previewing the repeat decision."
        raise ValidationError(msg)
    preview = preview_group(group=group, target=target, actor=request.user)
    return render(
        request,
        "repeat_preview.html",
        {
            "group": group,
            "preview": preview,
            "queue_url": reverse(
                "repeat-queue",
                kwargs={
                    "project": group.policy.project.slug,
                    "language": group.policy.target_language.code,
                },
            ),
        },
    )


@login_required
@require_POST
def repeat_apply(request):
    """Commit a previewed decision and render a per-recipient report."""
    selected = [int(value) for value in request.POST.getlist("unit") if value.isdigit()]
    event = apply_preview(
        token=request.POST["token"], actor=request.user, unit_ids=selected
    )
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
    groups = [item for item in run.snapshot["groups"] if item.get("sendable", True)]
    if not groups:
        messages.info(
            request,
            gettext("No repeat groups require a model request."),
        )
        return redirect("repeat-queue", project=project, language=language)
    attempt = reserve_attempt(
        run=run,
        request_snapshot={"groups": groups},
    )
    queue_attempt(attempt=attempt)
    messages.success(request, gettext("Repeat recommendations were queued."))
    return redirect("repeat-queue", project=project, language=language)


@login_required
def repeat_rule(request, project: str, language: str):
    """Create or update the policy controlling this queue's future scope."""
    obj = get_object_or_404(Project, slug=project)
    target_language = get_object_or_404(Language, code=language)
    if not request.user.has_perm("project.edit", obj):
        raise PermissionDenied
    policy = RepeatPolicy.objects.filter(
        project=obj, target_language=target_language
    ).first()
    form = RepeatPolicyForm(
        project=obj,
        actor=request.user,
        data=request.POST or None,
        instance=policy,
        initial={"target_language": target_language} if policy is None else None,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("repeat-queue", project=project, language=language)
    return render(
        request,
        "repeat_rule.html",
        {"project": obj, "language": target_language, "form": form},
    )
