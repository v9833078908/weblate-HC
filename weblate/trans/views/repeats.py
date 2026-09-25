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
    judge_configuration_ready,
    judge_primary_endpoint,
    resolve_judge_seat_profile,
)
from weblate.trans.models import (
    Component,
    Label,
    ProducerRun,
    Project,
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
    Unit,
)
from weblate.trans.models.llm_usage import LLMUsageLog, recent_cost_range
from weblate.trans.repeat_bulk import (
    ATTENTION_APPROVED,
    ATTENTION_DISAGREES,
    ATTENTION_FLAGGED,
    ATTENTION_NEW_TRANSLATION,
    ATTENTION_NOT_OPEN,
    ATTENTION_UNCHECKED,
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
    review_target,
    start_bulk,
    start_undo,
)
from weblate.trans.repeat_judge import (
    CHOOSE,
    READY,
    REPEAT_JUDGE_QUERY,
    REWRITE,
    UNCHECKED,
    judge_groups,
    judge_launch_url,
    latest_repeat_judge_run,
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
    ensure_default_policy,
    policy_units,
    preview_group,
    preview_keep_group,
    repeat_queue_groups,
    undo_event,
)
from weblate.trans.util import join_plural
from weblate.trans.views.judge import user_can_view_producer_run

# Review rows shown at once; every page stays inside the one apply form.
REVIEW_PAGE_SIZE = 50


def _selected_ids(request, name: str) -> set[int]:
    """Parse repeated GET selector values without trusting malformed values."""
    result = set()
    for value in request.GET.getlist(name):
        try:
            result.add(int(value))
        except ValueError:
            continue
    return result


def _queue_groups(request, policy):
    """Build filtered, permission-safe view data for the repeat queue."""
    groups = repeat_queue_groups(
        policy,
        user=request.user,
        component_ids=_selected_ids(request, "component"),
        label_ids=_selected_ids(request, "label"),
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


def _judge_panel(request, obj, target_language, groups, policy, current):
    """Classify open groups and summarize the current repeat judge run."""
    open_groups = [item for item in groups if item["status"] == "open"]
    judgements = judge_groups(
        ((item["group"].pk, item["variants"]) for item in open_groups), current
    )
    buckets = dict.fromkeys((READY, CHOOSE, REWRITE, UNCHECKED), 0)
    for item in open_groups:
        item["judge"] = judgements[item["group"].pk]
        recommendation = current.get(item["group"].pk)
        for variant in item["variants"]:
            variant["judge"] = item["judge"].variants[variant["target"]]
            variant["model_pick"] = (
                recommendation is not None
                and recommendation.action == "use_existing"
                and tuple(recommendation.target) == variant["target"]
            )
        item["model_on_variant"] = any(
            variant["model_pick"] for variant in item["variants"]
        )
        recommended = item["judge"].recommended
        single_form = len(item["variants"][0]["target"]) == 1
        # Every group preselects its best available choice (D17).
        item["preselect"] = recommended is not None and single_form
        item["preselect_keep"] = item["judge"].rule == 3 and single_form
        item["preselect_custom"] = item["judge"].rule == 5 and single_form
        if recommended is not None:
            item["variants"].sort(
                key=lambda variant, target=recommended: variant["target"] != target
            )
        buckets[item["judge"].bucket] += 1
    project_language = obj.project_languages[target_language]
    drift_ids = set(
        Unit.objects.filter(
            translation__component__project=obj,
            translation__language=target_language,
        )
        .filter_access(request.user)
        .search(REPEAT_JUDGE_QUERY)
        .values_list("pk", flat=True)
    )
    queue_places = sum(len(item["units"]) for item in open_groups)
    relaunch_places = outside_places = 0
    for item in open_groups:
        if item["judge"].bucket != UNCHECKED:
            continue
        judged_ids = {unit_id for unit_id, _, _ in item["judge"].evidence}
        for variant in item["variants"]:
            for unit in variant["units"]:
                if unit.pk in judged_ids:
                    continue
                if unit.pk in drift_ids:
                    relaunch_places += 1
                else:
                    outside_places += 1
    run = latest_repeat_judge_run(project_language)
    running = run is not None and run.status in {
        ProducerRun.Status.QUEUED,
        ProducerRun.Status.RUNNING,
        ProducerRun.Status.CANCEL_REQUESTED,
    }
    coverage = run.get_coverage() if running else {}
    total = coverage.get("total") or 0
    queue_url = reverse(
        "repeat-queue", kwargs={"project": obj.slug, "language": target_language.code}
    )
    return {
        "state": (
            "running"
            if running
            else "ready"
            if any(buckets[bucket] for bucket in (READY, CHOOSE, REWRITE))
            else "start"
        ),
        "places": len(drift_ids),
        "queue_places": queue_places,
        "buckets": buckets,
        "run": run,
        "can_view_run": run is not None
        and user_can_view_producer_run(request.user, obj, run),
        "checked": max(0, total - coverage.get("pending", 0)),
        "total": total,
        "stopped": run is not None
        and run.status
        in {
            ProducerRun.Status.FAILED,
            ProducerRun.Status.CANCELLED,
            ProducerRun.Status.PARTIAL,
        },
        "relaunch_places": relaunch_places,
        "outside_places": outside_places,
        "comparing": sum(
            sum(
                bool(group.get("sendable", True))
                for group in snapshot.get("groups", ())
            )
            for snapshot in RepeatRecommendationRun.objects.filter(
                policy=policy,
                status__in={
                    RepeatRecommendationRun.Status.QUEUED,
                    RepeatRecommendationRun.Status.RUNNING,
                },
            ).values_list("snapshot", flat=True)
        ),
        "launch_url": judge_launch_url(project_language, queue_url),
        "can_launch": request.user.has_perm("translation.auto", project_language)
        and request.user.has_perm("unit.review", obj)
        and judge_configuration_ready(),
    }


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
    current = (
        current_recommendations(policy, actor=request.user)
        if policy is not None
        else {}
    )
    judge_panel = (
        _judge_panel(request, obj, target_language, groups, policy, current)
        if policy is not None
        else None
    )
    judge_buckets = judge_panel["buckets"] if judge_panel is not None else {}
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
    requested_judge = request.GET.get("judge")
    if requested_judge in judge_buckets:
        filtered_groups = [
            item
            for item in filtered_groups
            if item["status"] == "open" and item["judge"].bucket == requested_judge
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
    if requested_judge not in judge_buckets:
        query.pop("judge", None)
    page_obj = Paginator(filtered_groups, 20).get_page(request.GET.get("page"))
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
        # The banner counts what the review page preselects: ready groups
        # with a current result, so it matches the ready tile. A ready group
        # applies the judge's variant whatever the model said (D17).
        bulk_ready = sum(
            item["status"] == "open"
            and item["judge"].bucket == READY
            and item["group"].pk in current
            for item in groups
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
            "judge_panel": judge_panel,
            "judge_filter": requested_judge
            if requested_judge in judge_buckets
            else None,
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
        retry_unknown = request.GET.get("retry_unknown") == "1"
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
                retry_unknown=retry_unknown,
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
                "unknown_count": plan.unknown if plan else 0,
                "retry_unknown": retry_unknown,
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
    try:
        run = prepare_run(
            policy=policy,
            actor=request.user,
            request_cap=request_cap,
            retry_unknown=request.POST.get("retry_unknown") == "1",
        )
    except JudgeError as error:
        messages.error(request, str(error))
        return redirect("repeat-recommend", project=project, language=language)
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


def _attention_label(code: str) -> str:
    """Map one stable attention code to why the group is not preselected."""
    return {
        ATTENTION_NOT_OPEN: gettext("The queue does not list this group as open."),
        ATTENTION_NEW_TRANSLATION: gettext(
            "The model proposes a new translation that the judge has not checked."
        ),
        ATTENTION_FLAGGED: gettext("The judge flagged the variant the model picked."),
        ATTENTION_UNCHECKED: gettext(
            "The judge has not checked every variant of this group."
        ),
        ATTENTION_APPROVED: gettext(
            "Some places are approved, so the choice stays with you."
        ),
        ATTENTION_DISAGREES: gettext(
            "The judge and the model disagree about the best variant."
        ),
    }.get(code, code)


def _row_rationale(row) -> str:
    """Say plainly when the judge's variant replaces the model's advice."""
    if not row.judge_override:
        return row.rationale
    if not row.rationale:
        return gettext("The judge passed only this variant.")
    return gettext(
        "The judge passed only this variant. The model advised: %(rationale)s"
    ) % {"rationale": row.rationale}


def _review_rows_display(review, *, project: str, language: str) -> list[dict]:
    """Render compact review rows; per-place detail is fetched on demand."""
    return [
        {
            "result_id": row.result_id,
            "source_forms": list(row.source_forms),
            "target_forms": list(row.target),
            "action": row.action,
            "action_label": _action_label(row.action),
            "rationale": _row_rationale(row),
            "places": len(row.members),
            "writable": row.writable,
            "attention": row.attention,
            "attention_label": _attention_label(row.attention),
            "places_url": reverse(
                "repeat-bulk-places",
                kwargs={
                    "project": project,
                    "language": language,
                    "result_id": row.result_id,
                },
            ),
        }
        for row in review.rows
    ]


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
    rows = _review_rows_display(review, project=project, language=language)
    ready = [row for row in rows if not row["attention"]]
    return {
        "project": policy.project,
        "language": policy.target_language,
        "policy": policy,
        "rows": rows,
        "ready_rows": ready,
        "attention_rows": [row for row in rows if row["attention"]],
        "selected_places": sum(row["writable"] for row in ready),
        "page_size": REVIEW_PAGE_SIZE,
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


@login_required
def repeat_bulk_places(request, project: str, language: str, result_id: int):
    """Show one reviewed group's places on demand; GET never writes anything."""
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
    result = get_object_or_404(
        RepeatRecommendationResult.objects.select_related("group__policy"),
        pk=result_id,
        group__policy=policy,
    )
    target = review_target(result, actor=request.user)
    if target is None:
        raise Http404
    preview = preview_group(group=result.group, target=list(target), actor=request.user)
    excluded = set(result.exclusions)
    members = []
    for member in preview.members:
        unit = member.unit
        if unit is None:
            continue
        will_change = member.eligible and member.unit_id not in excluded
        members.append(
            {
                "unit_id": member.unit_id,
                "url": unit.get_absolute_url(),
                "key": unit.context,
                "component": str(unit.translation.component),
                "current_target": _forms_text(unit.get_target_plurals()),
                "will_change": will_change,
                "reason_label": (
                    ""
                    if will_change
                    else gettext("Excluded by the model")
                    if member.unit_id in excluded
                    else _member_reason_label(member.reason)
                ),
            }
        )
    return render(
        request,
        "repeat_bulk_places.html",
        {
            "project": policy.project,
            "language": policy.target_language,
            "source_text": _forms_text(result.group.source_forms),
            "target_text": _forms_text(target),
            "members": members,
            "review_url": reverse(
                "repeat-bulk-review", kwargs={"project": project, "language": language}
            ),
            "queue_url": reverse(
                "repeat-queue", kwargs={"project": project, "language": language}
            ),
        },
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
