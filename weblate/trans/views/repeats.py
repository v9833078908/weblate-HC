# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
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
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationRun,
    Unit,
)
from weblate.trans.models.llm_usage import LLMUsageLog, recent_cost_range
from weblate.trans.repeat_bulk import (
    CODE_ACTOR_MISSING,
    CODE_ITEM_ERROR,
    CODE_LATER_DECISION,
    CODE_NO_WRITE,
    CODE_PERMISSION_CHANGED,
    CODE_POLICY_DISABLED,
    CODE_STALE,
    CODE_UNDONE,
    plan_bulk,
    resume_bulk,
    start_bulk,
    start_undo,
)
from weblate.trans.repeat_recommendations import (
    current_recommendations,
    plan_recommendations,
    prepare_run,
    queue_importance,
    reconcile_expired_attempts,
    requeue_reserved_attempts,
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
from weblate.trans.util import join_plural
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
        # repeated under two keys is seen once. One rule for the queue, the
        # review table and request packing.
        return (
            status_order[item["status"]],
            *queue_importance(item["group"].source_forms[0], len(item["units"])),
        )

    return sorted(groups, key=importance)


def _add_recommendations(items, current) -> None:
    """Show only current recommendations for groups on this page."""
    for item in items:
        item["recommendation"] = current.get(item["group"].pk)


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
    current = (
        current_recommendations(policy, actor=request.user)
        if policy is not None
        else {}
    )
    _add_recommendations(page_obj.object_list, current)
    visible_components = Component.objects.filter(project=obj).filter_access(
        request.user
    )
    visible_labels = Label.objects.filter(project=obj).order_by("name")
    selected_components = _selected_ids(request, "component")
    selected_labels = _selected_ids(request, "label")
    bulk_ready = 0
    bulk_review_url = reverse(
        "repeat-bulk-review", kwargs={"project": project, "language": language}
    )
    if policy is not None and request.user.has_perm("project.edit", obj):
        # The banner counts the same current, applicable results the review
        # page freezes, across every recommendation run.
        bulk_ready = sum(
            result.action in {"use_existing", "propose_new"}
            for result in current.values()
        )
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
            "bulk_ready": bulk_ready,
            "bulk_review_url": bulk_review_url,
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
        .filter(source=join_plural(group.source_forms))
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


def _recommendation_attempt_rows(policy: RepeatPolicy) -> list[dict]:
    """Expose durable per-run attempt progress and overdue sends on a GET."""
    now = timezone.now()
    rows = []
    for run in (
        RepeatRecommendationRun.objects.filter(policy=policy)
        .order_by("-created_at")
        .prefetch_related("attempts", "results")
    ):
        attempts = list(run.attempts.all())
        rows.append(
            {
                "run": run,
                "status_label": run.get_status_display(),
                "reserved": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.RESERVED
                    for attempt in attempts
                ),
                "sent": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.SENT
                    for attempt in attempts
                ),
                "completed": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.COMPLETED
                    for attempt in attempts
                ),
                "failed": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.FAILED
                    for attempt in attempts
                ),
                "unknown": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.UNKNOWN
                    for attempt in attempts
                ),
                "overdue": sum(
                    attempt.status == RepeatRecommendationAttempt.Status.SENT
                    and attempt.deadline_at is not None
                    and attempt.deadline_at < now
                    for attempt in attempts
                ),
                "results": len(run.results.all()),
            }
        )
    return rows


def _recover_attempts(request, policy, project: str, language: str):
    """Reconcile overdue sends and re-enqueue reserved ones; never pay twice."""
    reconciled = 0
    requeued = 0
    for run in (
        RepeatRecommendationRun.objects.filter(
            policy=policy,
            attempts__status__in=[
                RepeatRecommendationAttempt.Status.RESERVED,
                RepeatRecommendationAttempt.Status.SENT,
            ],
        )
        .distinct()
        .order_by("pk")
    ):
        reconciled += reconcile_expired_attempts(run=run, actor=request.user)
        requeued += requeue_reserved_attempts(run=run, actor=request.user)
    messages.success(
        request,
        gettext(
            "Marked %(reconciled)s overdue sends unknown; re-enqueued %(requeued)s "
            "reserved requests. No new paid request was created."
        )
        % {"reconciled": reconciled, "requeued": requeued},
    )
    return redirect("repeat-recommend", project=project, language=language)


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
        try:
            request_cap = max(1, int(request.GET.get("request_cap", "1")))
        except ValueError:
            request_cap = 1
        plan = None
        profile = None
        cost_range = None
        unavailable = ""
        try:
            profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
            plan = plan_recommendations(
                policy=policy,
                actor=request.user,
                profile=profile,
                request_cap=request_cap,
            )
            cost_range = recent_cost_range(
                policy.project_id,
                profile.provider,
                profile.model,
                LLMUsageLog.Operation.REPEAT_RECOMMEND,
            )
        except JudgeError as error:
            unavailable = str(error)
        attempt_rows = _recommendation_attempt_rows(policy)
        return render(
            request,
            "repeat_recommend.html",
            {
                "policy": policy,
                "profile": profile,
                "cost_range": cost_range,
                "candidate_count": len(plan.contexts) if plan else 0,
                "request_count": len(plan.requests) if plan else 0,
                "unsent_count": plan.unsent if plan else 0,
                "oversized_count": len(plan.oversized) if plan else 0,
                "request_cap": request_cap,
                "unavailable": unavailable,
                "attempt_rows": attempt_rows,
                "can_recover": any(
                    row["reserved"] or row["overdue"] for row in attempt_rows
                ),
            },
        )
    action = request.POST.get("action", "")
    if action == "reconcile":
        # Strictly separate from starting a new capped paid run below.
        return _recover_attempts(request, policy, project, language)
    if action not in {"", "start"}:
        return HttpResponseBadRequest(gettext("Unknown action."))
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


def _member_reason_label(reason: str) -> str:
    """Map one stable preview reason code to its translated explanation."""
    return {
        "already-matches": gettext("Already translated this way"),
        "approved": gettext("Approved, not changed"),
        "locked": gettext("The component is locked"),
        "max-length": gettext("Too long for this string"),
        "rule-conflict": gettext("Covered by another repeat rule"),
    }.get(reason, reason)


def _failure_reason(code: str) -> str:
    """Map one stable outcome code to its translated reason; never raw errors."""
    return {
        CODE_STALE: gettext(
            "A string changed after this decision was reviewed; refresh and decide again."
        ),
        CODE_NO_WRITE: gettext(
            "Nothing needed changing: every place already matched or was excluded."
        ),
        CODE_ITEM_ERROR: gettext(
            "Processing hit an unexpected error; resume to process the remaining groups."
        ),
        CODE_PERMISSION_CHANGED: gettext(
            "Permission to change this project was revoked during processing."
        ),
        CODE_ACTOR_MISSING: gettext(
            "The user who started this batch no longer exists."
        ),
        CODE_POLICY_DISABLED: gettext(
            "The repeat rule was disabled before this batch finished."
        ),
        CODE_LATER_DECISION: gettext(
            "A later decision changed this group; it was not undone."
        ),
        CODE_UNDONE: gettext("Undone; previous translations were restored."),
    }.get(code, "")


def _conflict_reason_label(reason: str) -> str:
    """Map one stable undo-conflict code to its translated reason."""
    return {
        "changed": gettext("Changed after the decision"),
        "missing-or-approved": gettext("Deleted or approved after the decision"),
    }.get(reason, reason)


def _action_label(action: str) -> str:
    """Map one stable recommendation action code to its translated label."""
    return {
        "use_existing": gettext("Use an existing translation"),
        "propose_new": gettext("Propose a new translation"),
        "keep_independent": gettext("Keep these places independent"),
        "needs_human": gettext("Needs a human decision"),
    }.get(action, action)


def _forms_text(forms) -> str:
    """Show every plural form of one source or target."""
    return " / ".join(forms)


def _review_refresh_error() -> str:
    return gettext(
        "This review confirmation is no longer valid: a string, the repeat rule "
        "or your permissions changed after the review. Nothing was written; the "
        "review below was refreshed and can be selected again."
    )


def _review_rows_display(review, *, actor) -> list[dict]:
    """Render review rows with each place's current translation in one lookup."""
    unit_ids = {member["unit_id"] for row in review.rows for member in row.members}
    units = {
        unit.pk: unit
        for unit in Unit.objects.filter(pk__in=unit_ids)
        .filter_access(actor)
        .select_related(
            "translation__component",
            "translation__component__project",
            "translation__component__category",
            "translation__language",
            "translation__plural",
        )
    }
    rows = []
    for row in review.rows:
        members = []
        for member in row.members:
            unit = units.get(member["unit_id"])
            members.append(
                {
                    "unit_id": member["unit_id"],
                    "url": unit.get_absolute_url() if unit is not None else "",
                    "key": member["key"],
                    "component": member["component"],
                    "current_target": (
                        _forms_text(unit.get_target_plurals())
                        if unit is not None
                        else "—"
                    ),
                    "excluded": member["excluded"],
                    "will_change": member["eligible"] and not member["excluded"],
                    "reason_label": (
                        ""
                        if member["eligible"]
                        else _member_reason_label(member["reason"])
                    ),
                }
            )
        rows.append(
            {
                "result_id": row.result_id,
                "group_id": row.group_id,
                "source_forms": list(row.source_forms),
                "source_text": _forms_text(row.source_forms),
                "target_forms": list(row.target),
                "target_text": _forms_text(row.target),
                "action": row.action,
                "action_label": _action_label(row.action),
                "rationale": row.rationale,
                "exclusions": list(row.exclusions),
                "writable": row.writable,
                "already_matching": row.already_matching,
                "blocked": [
                    {"reason_label": _member_reason_label(reason), "count": count}
                    for reason, count in sorted(row.blocked.items())
                ],
                "members": members,
            }
        )
    return rows


def _decision_groups_display(results, *, actor, policy, queue_url: str) -> list[dict]:
    """List manual decisions with links to their individual places and group."""
    if not results:
        return []
    wanted = {join_plural(list(result.group.source_forms)) for result in results}
    units_by_source: dict[tuple[str, ...], list] = {}
    for unit in (
        policy_units(policy)
        .filter_access(actor)
        .filter(source__in=sorted(wanted))
        .select_related(
            "translation__component__project",
            "translation__component__category",
            "translation__language",
        )
    ):
        units_by_source.setdefault(tuple(unit.get_source_plurals()), []).append(unit)
    display = []
    for result in results:
        forms = tuple(result.group.source_forms)
        display.append(
            {
                "result_id": result.pk,
                "group_id": result.group_id,
                "source_text": _forms_text(forms),
                "rationale": result.rationale,
                "group_url": f"{queue_url}?group={result.group_id}",
                "units": [
                    {"key": unit.context, "url": unit.get_absolute_url()}
                    for unit in sorted(
                        units_by_source.get(forms, []), key=lambda unit: unit.pk
                    )
                ],
            }
        )
    return display


def _review_context(
    request, *, project: str, language: str, policy, review, error: str
) -> dict:
    queue_url = reverse(
        "repeat-queue", kwargs={"project": project, "language": language}
    )
    return {
        "project": policy.project,
        "language": policy.target_language,
        "policy": policy,
        "rows": _review_rows_display(review, actor=request.user),
        "needs_human": _decision_groups_display(
            review.needs_human, actor=request.user, policy=policy, queue_url=queue_url
        ),
        "independent": _decision_groups_display(
            review.independent, actor=request.user, policy=policy, queue_url=queue_url
        ),
        "stale": review.stale,
        "manifest": review.manifest,
        "expires_at": review.expires_at,
        "error": error,
        "queue_url": queue_url,
    }


@login_required
def repeat_bulk_review(request, project: str, language: str):
    """Freeze current recommendations for review; GET never writes anything."""
    policy = get_object_or_404(
        RepeatPolicy,
        project__slug=project,
        target_language__code=language,
        enabled=True,
    )
    if not request.user.can_access_project(policy.project):
        raise PermissionDenied
    if not request.user.has_perm("project.edit", policy.project):
        raise PermissionDenied
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action != "apply":
            return HttpResponseBadRequest(gettext("Unknown action."))
        selected = request.POST.getlist("result")
        if not selected:
            error = gettext("Select at least one recommendation to apply.")
        elif any(not value.isdigit() for value in selected):
            error = _review_refresh_error()
        else:
            try:
                run = start_bulk(
                    policy=policy,
                    actor=request.user,
                    manifest=request.POST.get("manifest", ""),
                    result_ids=[int(value) for value in selected],
                )
            except ValidationError:
                # Stale, expired or tampered confirmation: nothing was written.
                error = _review_refresh_error()
            else:
                return redirect(
                    "repeat-bulk-status",
                    project=project,
                    language=language,
                    token=run.token,
                )
    review = plan_bulk(policy=policy, actor=request.user)
    return render(
        request,
        "repeat_bulk_review.html",
        _review_context(
            request,
            project=project,
            language=language,
            policy=policy,
            review=review,
            error=error,
        ),
    )


def _bulk_summary(run) -> dict:
    """Distinct counts: matching, excluded, blocked, stale, left untouched."""
    excluded = blocked = matching = stale = untouched = 0
    for item in run.items.all():
        outcome = item.outcome or {}
        excluded += len(outcome.get("exclusions", []))
        for skipped in outcome.get("skipped", []):
            if skipped.get("reason") in {"approved", "protected"}:
                blocked += 1
            elif skipped.get("reason") == "already-matches":
                matching += 1
        if item.failure_code == CODE_STALE:
            stale += 1
        if item.status in {RepeatBulkItem.Status.SKIPPED, RepeatBulkItem.Status.FAILED}:
            untouched += 1
    return {
        "excluded": excluded,
        "blocked": blocked,
        "matching": matching,
        "stale": stale,
        "untouched": untouched,
        "partial": bool(
            run.status == RepeatBulkRun.Status.COMPLETED and (untouched or run.conflict)
        ),
    }


def _bulk_items_display(run, *, actor) -> list[dict]:
    """Per-group audit rows; undo conflicts link their exact recipients."""
    loaded = []
    conflict_ids = set()
    for item in run.items.all():
        outcome = item.outcome or {}
        conflicts = outcome.get("conflicts", [])
        conflict_ids.update(conflict.get("unit") for conflict in conflicts)
        loaded.append((item, outcome, conflicts))
    units = {
        unit.pk: unit
        for unit in Unit.objects.filter(pk__in=conflict_ids)
        .filter_access(actor)
        .select_related(
            "translation__component",
            "translation__component__project",
            "translation__component__category",
            "translation__language",
            "translation__plural",
        )
    }
    display = []
    for item, outcome, conflicts in loaded:
        skipped = outcome.get("skipped", [])
        if run.action == RepeatBulkRun.Action.UNDO:
            candidates = [
                (gettext("Places restored"), len(outcome.get("restored", []))),
                (gettext("Places not restored"), len(conflicts)),
            ]
        else:
            candidates = [
                (gettext("Places changed"), len(outcome.get("written", []))),
                (
                    gettext("Approved or locked places unchanged"),
                    sum(
                        entry.get("reason") in {"approved", "protected"}
                        for entry in skipped
                    ),
                ),
                (
                    gettext("Places already matching"),
                    sum(entry.get("reason") == "already-matches" for entry in skipped),
                ),
                (gettext("Places excluded"), len(outcome.get("exclusions", []))),
            ]
        conflict_display = []
        for conflict in conflicts:
            unit = units.get(conflict.get("unit"))
            conflict_display.append(
                {
                    "unit_id": conflict.get("unit"),
                    "url": unit.get_absolute_url() if unit is not None else "",
                    "key": unit.context if unit is not None else "",
                    "reason_label": _conflict_reason_label(conflict.get("reason", "")),
                }
            )
        display.append(
            {
                "ordinal": item.ordinal,
                "source_text": _forms_text(item.group_identity.get("source_forms", [])),
                "status_label": item.get_status_display(),
                "reason": _failure_reason(item.failure_code),
                "counts": [
                    {"label": label, "value": value}
                    for label, value in candidates
                    if value
                ],
                "conflicts": conflict_display,
            }
        )
    return display


@login_required
def repeat_bulk_status(request, project: str, language: str, token):
    """Show one durable batch; mutations are explicit, idempotent POST actions."""
    run = get_object_or_404(
        RepeatBulkRun.objects.select_related(
            "policy__project", "policy__target_language", "apply_run"
        ).prefetch_related("items"),
        token=token,
        policy__project__slug=project,
        policy__target_language__code=language,
    )
    policy = run.policy
    if not request.user.can_access_project(policy.project):
        raise PermissionDenied
    if not request.user.has_perm("project.edit", policy.project):
        raise PermissionDenied
    kwargs = {"project": project, "language": language, "token": run.token}
    status_url = reverse("repeat-bulk-status", kwargs=kwargs)
    paused = request.GET.get("pause") == "1" or request.POST.get("pause") == "1"
    pause_query = "?pause=1" if paused else ""
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action not in {"undo", "resume"}:
            return HttpResponseBadRequest(gettext("Unknown action."))
        try:
            if action == "undo":
                undo_run = start_undo(run=run, actor=request.user)
                return redirect(
                    f"{reverse('repeat-bulk-status', kwargs={**kwargs, 'token': undo_run.token})}{pause_query}"
                )
            resume_bulk(run=run, actor=request.user)
            return redirect(f"{status_url}{pause_query}")
        except ValidationError:
            error = gettext(
                "This batch cannot accept that action now: it is still being "
                "processed, was already undone, or has nothing left to process. "
                "Nothing was changed."
            )
    undo_run = run.undo_runs.first()
    summary = _bulk_summary(run)
    return render(
        request,
        "repeat_bulk_status.html",
        {
            "project": policy.project,
            "language": policy.target_language,
            "policy": policy,
            "run": run,
            "items": _bulk_items_display(run, actor=request.user),
            "summary": summary,
            "failure_reason": _failure_reason(run.failure_code),
            # Undo follows the committed events, never the written counter: a
            # partially applied failed batch keeps its undo inventory.
            "can_undo": (
                run.action == RepeatBulkRun.Action.APPLY
                and run.status
                in {RepeatBulkRun.Status.COMPLETED, RepeatBulkRun.Status.FAILED}
                and run.items.exclude(decision_event__isnull=True).exists()
                and undo_run is None
            ),
            "can_resume": (
                run.items.filter(status=RepeatBulkItem.Status.PENDING).exists()
                and not (
                    run.action == RepeatBulkRun.Action.APPLY and run.undo_runs.exists()
                )
            ),
            "undo_run": undo_run,
            "apply_run": run.apply_run,
            "paused": paused,
            "refresh_seconds": (
                30
                if run.status
                in {RepeatBulkRun.Status.QUEUED, RepeatBulkRun.Status.RUNNING}
                and not paused
                else None
            ),
            "pause_url": f"{status_url}?pause=1",
            "unpause_url": status_url,
            "refresh_url": f"{status_url}?pause=1" if paused else status_url,
            "status_url": status_url,
            "error": error,
        },
    )
