# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.lang.models import Language
from weblate.trans.forms import RepeatPolicyForm
from weblate.trans.judge import (
    JudgeError,
    judge_primary_endpoint,
    resolve_judge_seat_profile,
)
from weblate.trans.models import (
    Component,
    Label,
    Project,
    RepeatDecisionEvent,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationResult,
    Unit,
)
from weblate.trans.models.llm_usage import LLMUsageLog, recent_cost_range
from weblate.trans.repeat_recommendations import (
    prepare_run,
    queue_attempt,
    reserve_attempt,
)
from weblate.trans.repeats import (
    apply_preview,
    detect_policy_groups,
    ensure_default_policy,
    get_or_create_group,
    policy_overlaps,
    policy_units,
    preview_group,
    preview_keep_group,
    source_fingerprint,
    undo_event,
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


def _group_status(group, units, variant_count: int, rule_conflict: bool) -> str:
    """Return the user-facing queue state, separate from recipient notes."""
    approved_targets = {
        tuple(unit.get_target_plurals())
        for unit in units
        if unit.state == STATE_APPROVED
    }
    if rule_conflict:
        return "rule-conflict"
    if len(approved_targets) > 1:
        return "approved-conflict"
    if group.shared_target or group.decision_origin == "independent":
        return "resolved"
    if variant_count == 1:
        return "consistent"
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

    candidates = detect_policy_groups(policy, user=request.user)
    units_by_pk = {
        unit.pk: unit
        for unit in visible_units.filter(
            pk__in=[pk for candidate in candidates for pk in candidate.unit_ids]
        )
    }
    existing_groups = {
        (group.source_hash, group.plural_number): group
        for group in RepeatGroup.objects.filter(policy=policy)
    }
    # Overlap depends only on the policy, not on the group.
    rule_conflict = bool(policy_overlaps(policy, exclude_policy_id=policy.pk))

    groups = []
    for candidate in candidates:
        units = [
            units_by_pk[pk] for pk in sorted(candidate.unit_ids) if pk in units_by_pk
        ]
        if len(units) < 2:
            continue
        source_forms = list(candidate.source_forms)
        group = existing_groups.get(
            (
                source_fingerprint(source_forms, candidate.plural_number),
                candidate.plural_number,
            )
        )
        if group is None or group.source_forms != source_forms:
            group = get_or_create_group(policy, units[0])
        variants = {}
        for unit in units:
            variants.setdefault(tuple(unit.get_target_plurals()), []).append(unit)
        status = _group_status(group, units, len(variants), rule_conflict)
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
                "approved_conflict": status == "approved-conflict",
                "all_approved": all(unit.state == STATE_APPROVED for unit in units),
            }
        )
    status_order = {
        "rule-conflict": 0,
        "approved-conflict": 1,
        "open": 2,
        "consistent": 3,
        "resolved": 4,
    }

    def importance(item) -> tuple[int, bool, int]:
        # Short strings are the ones players see many times; a dialogue line
        # repeated under two keys is seen once.
        words = len(item["group"].source_forms[0].split())
        return (status_order[item["status"]], words > 3, -len(item["units"]))

    return sorted(groups, key=importance)


def _add_recommendations(items) -> None:
    """Look up recommendations only for the groups shown on the page."""
    for item in items:
        group = item["group"]
        item["recommendation"] = (
            RepeatRecommendationResult.objects.filter(
                group=group,
                group_revision=group.revision,
                run__status="completed",
            )
            .order_by("-created_at")
            .first()
        )


def _queue_url(group) -> str:
    return reverse(
        "repeat-queue",
        kwargs={
            "project": group.policy.project.slug,
            "language": group.policy.target_language.code,
        },
    )


def _own_event(request, token: str):
    """Return the requesting user's decision event, or None for anything else."""
    try:
        return RepeatDecisionEvent.objects.select_related(
            "group__policy__project", "group__policy__target_language"
        ).get(token=token, actor=request.user)
    except (RepeatDecisionEvent.DoesNotExist, ValidationError):
        return None


def _decision_summary(request, project: Project):
    """Describe the decision the user has just made, shown on top of the queue."""
    event = _own_event(request, request.GET.get("done", ""))
    if event is None or event.group.policy.project_id != project.pk:
        return None
    group = event.group
    result = event.result
    summary = {"action": event.action, "source": group.source_forms[0]}
    if event.action == RepeatDecisionEvent.Action.APPLY:
        skipped = result.get("skipped", [])
        blocked = [
            item["unit"]
            for item in skipped
            if item["reason"] in {"approved", "protected"}
        ]
        written = len(result.get("written", []))
        summary.update(
            token=event.token,
            target=" / ".join(event.snapshot["target"]),
            written=written,
            already=sum(item["reason"] == "already-matches" for item in skipped),
            unchecked=sum(item["reason"] == "not-selected" for item in skipped),
            units=list(
                Unit.objects.filter(pk__in=blocked)
                .filter_access(request.user)
                .select_related("translation__component")
            ),
            can_undo=written > 0
            and group.revision == event.group_revision + 1
            and request.user.has_perm("project.edit", project),
        )
    elif event.action == RepeatDecisionEvent.Action.UNDO:
        conflicts = [item["unit"] for item in result.get("conflicts", [])]
        summary.update(
            restored=len(result.get("restored", [])),
            units=list(
                Unit.objects.filter(pk__in=conflicts)
                .filter_access(request.user)
                .select_related("translation__component")
            ),
        )
    return summary


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
    if policy is None and request.user.has_perm("project.edit", obj):
        policy = ensure_default_policy(
            project=obj, target_language=target_language, actor=request.user
        )
    groups = _queue_groups(request, policy) if policy is not None else []
    requested_status = request.GET.get("status", "open")
    status_aliases = {
        "all": None,
        "open": "open",
        "conflict": {"rule-conflict", "approved-conflict"},
        "consistent": "consistent",
        "resolved": "resolved",
    }
    if requested_status not in status_aliases:
        requested_status = "open"
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
        "consistent": sum(item["status"] == "consistent" for item in groups),
        "resolved": sum(item["status"] == "resolved" for item in groups),
    }
    query = request.GET.copy()
    query.pop("page", None)
    query.pop("limit", None)
    query.pop("done", None)
    query["status"] = requested_status
    page_obj = Paginator(filtered_groups, 20).get_page(request.GET.get("page"))
    _add_recommendations(page_obj.object_list)
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
            "query_string": query.urlencode(),
            "decision": _decision_summary(request, obj),
        },
    )


@login_required
@require_POST
def repeat_preview(request, group_id: int):
    """Show the server-derived before/after view without writing targets."""
    group = get_object_or_404(RepeatGroup, pk=group_id)
    if not request.user.can_access_project(group.policy.project):
        raise PermissionDenied
    visible_members = (
        policy_units(group.policy)
        .filter_access(request.user)
        .filter(source=group.source_forms[0])
    )
    if not any(
        tuple(unit.get_source_plurals()) == tuple(group.source_forms)
        for unit in visible_members
    ):
        raise PermissionDenied
    choice = request.POST.get("choice", "")
    if choice == "keep":
        preview = preview_keep_group(group=group, actor=request.user)
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
    """Commit a previewed decision and return to the queue with its summary."""
    group_id = request.POST.get("group", "")
    group = get_object_or_404(
        RepeatGroup.objects.select_related(
            "policy__project", "policy__target_language"
        ),
        pk=int(group_id) if group_id.isdigit() else 0,
    )
    if not request.user.can_access_project(group.policy.project):
        raise PermissionDenied
    selected = [int(value) for value in request.POST.getlist("unit") if value.isdigit()]
    try:
        event = apply_preview(
            token=request.POST.get("token", ""),
            actor=request.user,
            unit_ids=selected,
        )
    except ValidationError:
        messages.error(
            request,
            gettext(
                "Nothing was changed: the strings changed or the preview expired. "
                "Open the group and decide again."
            ),
        )
        return redirect(_queue_url(group))
    return redirect(f"{_queue_url(event.group)}?done={event.token}")


@login_required
@require_POST
def repeat_undo(request):
    """Undo the user's own shared-translation decision from the queue."""
    event = _own_event(request, request.POST.get("token", ""))
    if event is None:
        raise Http404
    try:
        undo = undo_event(token=str(event.token), actor=request.user)
    except (ValidationError, RepeatDecisionEvent.DoesNotExist):
        messages.error(
            request,
            gettext("This decision can no longer be undone: the group changed."),
        )
        return redirect(_queue_url(event.group))
    return redirect(f"{_queue_url(event.group)}?done={undo.token}")


@login_required
def repeat_recommend(request, project: str, language: str):
    """Preview, then explicitly reserve a bounded recommendation request."""
    policy = get_object_or_404(
        RepeatPolicy,
        project__slug=project,
        target_language__code=language,
        enabled=True,
    )
    if not request.user.has_perm("project.edit", policy.project):
        raise PermissionDenied
    if request.method == "GET":
        group_count = len(detect_policy_groups(policy, user=request.user))
        profile = None
        cost_range = None
        unavailable = ""
        try:
            profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
            cost_range = recent_cost_range(
                policy.project_id,
                profile.provider,
                profile.model,
                LLMUsageLog.Operation.REPEAT_RECOMMEND,
            )
        except JudgeError as error:
            unavailable = str(error)
        return render(
            request,
            "repeat_recommend.html",
            {
                "policy": policy,
                "group_count": group_count,
                "profile": profile,
                "cost_range": cost_range,
                "unavailable": unavailable,
            },
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
