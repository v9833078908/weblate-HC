# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The durable judge run report.

``weblate/trans/models/judge.py``'s ``JudgeRun`` and ``JudgeRunUnit``
rendered as one page addressed by run UUID.

Every count on this page is a live query against ``JudgeRunUnit`` (and, for
the resolution-derived buckets, the ``JudgeVerdict`` it references), never the
frozen ``JudgeRun.summary`` snapshot: a resolution can be recorded well after
the run finished, and the header must stay exact for the report-local rows it
links to (task 2's own test contract). No count on this page is a cost figure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Case, CharField, Q, Value, When
from django.db.models.functions import Cast
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy, pgettext_lazy

from weblate.trans.models import Component, JudgeRun, JudgeRunUnit, Project, Translation
from weblate.trans.models.judge import SEVERITY_RANK, JudgeVerdict
from weblate.trans.models.project import CommitPolicyChoices
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from django.db.models import Model, QuerySet

    from weblate.auth.models import AuthenticatedHttpRequest

_SCOPE_MODELS: dict[str, type[Model]] = {
    JudgeRun.ScopeType.TRANSLATION: Translation,
    JudgeRun.ScopeType.COMPONENT: Component,
    JudgeRun.ScopeType.PROJECT: Project,
    JudgeRun.ScopeType.WORKSPACE: Workspace,
}

_OUTCOME = JudgeRunUnit.Outcome
_REPAIR = JudgeRunUnit.RepairStatus
_RESOLUTION = JudgeVerdict.Resolution

# Buckets that make up the producer's "what to do" list. Ordered by what
# costs the producer most to leave alone: critical, major, minor, then the
# two transport/evidence buckets that need a re-check.
_ACTIONABLE_OUTCOMES = (
    _OUTCOME.CRITICAL,
    _OUTCOME.MAJOR,
    _OUTCOME.MINOR,
    _OUTCOME.UNPARSED,
    _OUTCOME.STALE_CONFLICT,
)

# Every entry here is both a valid ``?outcome=`` value and a header row;
# order is display order. The report-local list uses the identical filter,
# so a header count and its drill-down row count can never disagree.
_OUTCOME_LABELS = {
    "actionable": gettext_lazy("Needs action"),
    "critical": gettext_lazy("Critical held"),
    "major": gettext_lazy("Major not fixed"),
    "minor": gettext_lazy("Minor noted"),
    "unparsed": gettext_lazy("Unparsed"),
    "stale-conflict": gettext_lazy("Stale conflict"),
    "candidates": gettext_lazy("Suggested fixes"),
    "repaired": gettext_lazy("Repaired"),
    "rolled-back": gettext_lazy("Rolled back"),
    "accepted-as-is": gettext_lazy("Accepted as is"),
    "escalated": gettext_lazy("Escalated"),
    "skipped": gettext_lazy("Skipped"),
    "passed": gettext_lazy("Passed"),
    "matched": gettext_lazy("Matched"),
    "checked": gettext_lazy("Checked"),
    "cached": gettext_lazy("Cached"),
}

# The judge prompt's fixed error vocabulary (weblate/trans/judge.py
# CATEGORIES), shown capitalized on the Pareto table.
_CATEGORY_LABELS = {
    "terminology": gettext_lazy("Terminology"),
    "mistranslation": gettext_lazy("Mistranslation"),
    "omission": gettext_lazy("Omission"),
    "addition": gettext_lazy("Addition"),
    "fluency": gettext_lazy("Fluency"),
    "punctuation": gettext_lazy("Punctuation"),
    "markup": gettext_lazy("Markup"),
    # A plain "Register" already means the sign-up verb elsewhere in this
    # project's translations; this is the linguistic register/tone sense.
    "register": pgettext_lazy("Judge error category", "Register"),
}

_SEVERITY_LABELS = {
    "critical": gettext_lazy("Critical"),
    "major": gettext_lazy("Major"),
    "minor": gettext_lazy("Minor"),
}

# The live search vocabulary that matches each severity bucket, using the
# same ``judge:*`` field the hand-off gate uses (finding 5: the counts are
# live, so the buttons say "still blocking", never "the N from this run").
_SEVERITY_QUERY = {
    "critical": "judge:reject",
    "major": "judge:flag",
    "minor": "judge:minor",
}


def _filter_outcome(rows: QuerySet, key: str) -> QuerySet:
    """Apply one report bucket's filter. ``key`` must be pre-validated."""
    if key == "actionable":
        return rows.filter(outcome__in=_ACTIONABLE_OUTCOMES)
    if key == "passed":
        return rows.filter(outcome=_OUTCOME.PASSED)
    if key == "candidates":
        return rows.filter(repair_status=_REPAIR.CANDIDATE_STORED)
    if key == "matched":
        return rows
    if key == "checked":
        return rows.exclude(outcome=_OUTCOME.SKIPPED)
    if key == "cached":
        return rows.filter(cached=True)
    if key == "skipped":
        return rows.filter(outcome=_OUTCOME.SKIPPED)
    if key == "repaired":
        return rows.filter(repair_status=_REPAIR.APPLIED)
    if key == "rolled-back":
        return rows.filter(repair_status=_REPAIR.ROLLED_BACK)
    if key == "accepted-as-is":
        return rows.filter(verdict__resolution=_RESOLUTION.ACCEPTED_AS_IS)
    if key == "escalated":
        return rows.filter(verdict__resolution=_RESOLUTION.ESCALATED)
    # "minor"/"major"/"critical"/"unparsed"/"stale-conflict" are literal
    # JudgeRunUnit.Outcome values.
    return rows.filter(outcome=key)


def _scope_base_url(scope) -> str:
    """
    Where a ``judge:*`` query works for this run's scope.

    The editor (finding 4) for anything translation-shaped, the site-wide
    search view for Component/Project/Workspace.
    """
    if isinstance(scope, Translation):
        return scope.get_translate_url()
    return reverse("search", kwargs={"path": scope.get_url_path()})


def _review_url(scope, query: str) -> str:
    return f"{_scope_base_url(scope)}?{urlencode({'q': query})}"


def _category_rows(rows: QuerySet) -> list[dict]:
    """
    Group the actionable rows by their primary error's category.

    One query; the primary error mirrors ``JudgeVerdict.primary_error``
    (first error at the verdict's own ``max_severity``) in Python. The
    row's own ``outcome`` is the severity carrier for the worst column:
    rows here are critical/major/minor by construction of the caller.
    """
    per_category: dict[str, dict] = {}
    for outcome, max_severity, errors in rows.values_list(
        "outcome", "verdict__max_severity", "verdict__errors"
    ):
        if not isinstance(errors, list) or not errors:
            continue
        primary = None
        for error in errors:
            if isinstance(error, dict) and error.get("severity") == max_severity:
                primary = error
                break
        if primary is None:
            primary = errors[0]
        category = primary.get("category")
        if category not in _CATEGORY_LABELS:
            continue
        entry = per_category.setdefault(
            category, {"category": category, "count": 0, "worst": ""}
        )
        entry["count"] += 1
        if SEVERITY_RANK.get(str(outcome), -1) > SEVERITY_RANK.get(
            str(entry["worst"]), -1
        ):
            entry["worst"] = str(outcome)
    result = sorted(per_category.values(), key=lambda entry: -entry["count"])
    for entry in result:
        entry["label"] = _CATEGORY_LABELS[entry["category"]]
        entry["worst_label"] = _SEVERITY_LABELS.get(entry["worst"], "")
        entry["query"] = _SEVERITY_QUERY.get(entry["worst"], "")
    return result


# Per-outcome sentences for rows whose verdict is gone (or never existed):
# the table must never render an empty Problem cell.
_FALLBACK_PROBLEM = {
    _OUTCOME.PASSED: gettext_lazy("The judge found no problems."),
    _OUTCOME.SKIPPED: gettext_lazy("This string was skipped before judging."),
    _OUTCOME.UNPARSED: gettext_lazy(
        "The judge reply for this string could not be used."
    ),
    _OUTCOME.STALE_CONFLICT: gettext_lazy(
        "The text changed while this run was in progress."
    ),
    _OUTCOME.DEFERRED: gettext_lazy("This string was deferred by the judge."),
    _OUTCOME.REFUSED: gettext_lazy("The judge refused this string."),
}

_ACTION_BY_OUTCOME = {
    _OUTCOME.CRITICAL: gettext_lazy("Fix and re-check"),
    # A plain "Review" is already a noun ("Рецензирование", team-name
    # sense) in this project's translations; this needs an imperative
    # action-link verb instead.
    _OUTCOME.MAJOR: pgettext_lazy("Judge report row action", "Review"),
    _OUTCOME.MINOR: pgettext_lazy("Judge report row action", "Review"),
    _OUTCOME.UNPARSED: gettext_lazy("Re-check"),
    _OUTCOME.STALE_CONFLICT: gettext_lazy("Re-check"),
}


def _blocks_release(scope) -> bool:
    """
    Whether a held critical genuinely stops the string from shipping.

    The export writes every state by default; FUZZY is excluded only under
    WITHOUT_NEEDS_EDITING / APPROVED_ONLY (finding 2), so "will not ship"
    may be claimed only when the policy actually says that. A Workspace
    mixes projects: claim it only when every project in it blocks.
    """
    blocking_policies = {
        CommitPolicyChoices.WITHOUT_NEEDS_EDITING,
        CommitPolicyChoices.APPROVED_ONLY,
    }
    if isinstance(scope, Project):
        return scope.commit_policy in blocking_policies
    if isinstance(scope, Component):
        return scope.project.commit_policy in blocking_policies
    if isinstance(scope, Translation):
        return scope.component.project.commit_policy in blocking_policies
    policies = list(
        Project.objects.filter(workspace=scope).values_list("commit_policy", flat=True)
    )
    return bool(policies) and all(policy in blocking_policies for policy in policies)


def _annotate_row(row: JudgeRunUnit) -> None:
    """
    Compute the template-facing row fields.

    Adds no queries: the page's select_related covers unit, translation,
    component, project and verdict.
    """
    unit = row.unit
    row.current_target_matches = (  # type: ignore[attr-defined]
        unit is not None and unit.get_target_plurals() == row.input_target
    )
    row.editor_url = unit.get_absolute_url() if unit is not None else ""  # type: ignore[attr-defined]
    if unit is None:
        row.source_text = ""  # type: ignore[attr-defined]
        row.target_text = " / ".join(row.input_target)  # type: ignore[attr-defined]
    else:
        row.source_text = " / ".join(unit.get_source_plurals())  # type: ignore[attr-defined]
        row.target_text = " / ".join(unit.get_target_plurals())  # type: ignore[attr-defined]
    primary = row.verdict.primary_error if row.verdict else None
    if primary is not None:
        label = _CATEGORY_LABELS.get(
            primary.get("category"), primary.get("category", "")
        )
        row.problem = f"{label}: {primary.get('description', '')}"  # type: ignore[attr-defined]
    else:
        row.problem = str(  # type: ignore[attr-defined]
            _FALLBACK_PROBLEM.get(row.outcome, gettext_lazy("No verdict was recorded."))
        )
    if unit is None:
        row.action = ""  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.CANDIDATE_STORED:
        row.action = gettext_lazy("Review the suggested fix")  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.APPLIED:
        row.action = gettext_lazy("See the applied fix")  # type: ignore[attr-defined]
    else:
        row.action = _ACTION_BY_OUTCOME.get(  # type: ignore[attr-defined]
            row.outcome, gettext_lazy("View")
        )


def _get_scope(run: JudgeRun):
    """Resolve the run's closed scope, or 404 when it no longer exists."""
    model = _SCOPE_MODELS.get(run.scope_type)
    if model is None:
        raise Http404
    try:
        return model.objects.get(pk=run.scope_id)  # type: ignore[attr-defined]
    except (ValueError, model.DoesNotExist) as error:  # type: ignore[attr-defined]
        raise Http404 from error


def user_can_view_judge_run(user, scope) -> bool:
    """Whether ``user`` currently (not at launch time) may view a run's scope."""
    return user.has_perm("translation.auto", scope) and user.has_perm(
        "unit.review", scope
    )


def _scope_run_query(scope: Translation | Component | Project | Workspace) -> Q:
    """
    Match the runs launched for this scope and for everything nested in it.

    A translation matches itself alone; a component also matches its
    translations; a project also matches its components and their
    translations; a workspace also matches its projects, their components
    and their translations. ``scope_id`` stores ``str(pk)``, so each nested
    level's membership is matched through a ``Cast("pk", CharField())``
    subquery over that level's own queryset: one SQL subquery per branch,
    evaluated inside the single run lookup, never a materialized id list
    per nesting level (which a project page would pay one query for).
    An unknown scope matches nothing.
    """
    match scope:
        case Translation():
            return Q(scope_type=JudgeRun.ScopeType.TRANSLATION, scope_id=str(scope.pk))
        case Component():
            return Q(
                scope_type=JudgeRun.ScopeType.COMPONENT, scope_id=str(scope.pk)
            ) | Q(
                scope_type=JudgeRun.ScopeType.TRANSLATION,
                scope_id__in=Translation.objects.filter(component=scope)
                .annotate(_scope_id=Cast("pk", CharField()))
                .values("_scope_id"),
            )
        case Project():
            return (
                Q(scope_type=JudgeRun.ScopeType.PROJECT, scope_id=str(scope.pk))
                | Q(
                    scope_type=JudgeRun.ScopeType.COMPONENT,
                    scope_id__in=Component.objects.filter(project=scope)
                    .annotate(_scope_id=Cast("pk", CharField()))
                    .values("_scope_id"),
                )
                | Q(
                    scope_type=JudgeRun.ScopeType.TRANSLATION,
                    scope_id__in=Translation.objects.filter(component__project=scope)
                    .annotate(_scope_id=Cast("pk", CharField()))
                    .values("_scope_id"),
                )
            )
        case Workspace():
            return (
                Q(scope_type=JudgeRun.ScopeType.WORKSPACE, scope_id=str(scope.pk))
                | Q(
                    scope_type=JudgeRun.ScopeType.PROJECT,
                    scope_id__in=Project.objects.filter(workspace=scope)
                    .annotate(_scope_id=Cast("pk", CharField()))
                    .values("_scope_id"),
                )
                | Q(
                    scope_type=JudgeRun.ScopeType.COMPONENT,
                    scope_id__in=Component.objects.filter(project__workspace=scope)
                    .annotate(_scope_id=Cast("pk", CharField()))
                    .values("_scope_id"),
                )
                | Q(
                    scope_type=JudgeRun.ScopeType.TRANSLATION,
                    scope_id__in=Translation.objects.filter(
                        component__project__workspace=scope
                    )
                    .annotate(_scope_id=Cast("pk", CharField()))
                    .values("_scope_id"),
                )
            )
        case _:
            return Q(pk=None)


def recent_judge_runs(
    scope: Translation | Component | Project | Workspace,
    *,
    limit: int = 10,
) -> list[JudgeRun]:
    """
    Return the scope's most recent runs, newest first, as a materialized list.

    Callers take ``runs[0]`` as the newest run and iterate the remainder for
    the menu, so the list is evaluated exactly once - never ``.first()`` or
    ``[0]`` on the queryset itself, either of which would issue a second
    ``LIMIT 1`` query ahead of the menu's ``LIMIT 10`` and silently double
    the page's query budget.
    """
    return list(
        JudgeRun.objects.filter(_scope_run_query(scope))
        .order_by("-created")
        .select_related("actor")[:limit]
    )


@login_required
def judge_run(request: AuthenticatedHttpRequest, pk) -> HttpResponse:
    run = get_object_or_404(JudgeRun.objects.select_related("actor"), pk=pk)
    scope = _get_scope(run)
    # Permission is re-checked against the current user, never inferred from
    # the stored actor: a launcher can lose access after the run completes.
    if not user_can_view_judge_run(request.user, scope):
        raise Http404

    outcome = request.GET.get("outcome", "")
    if outcome and outcome not in _OUTCOME_LABELS:
        raise Http404
    # No explicit filter: the producer default. The actionable buckets are
    # the whole point of the page; everything else stays one URL away.
    effective = outcome or "actionable"
    base_rows = JudgeRunUnit.objects.filter(run=run)
    counts = {key: _filter_outcome(base_rows, key).count() for key in _OUTCOME_LABELS}
    rows = base_rows.select_related(
        "verdict",
        "unit__translation__language",
        "unit__translation__plural",
        "unit__translation__component__project",
        "unit__translation__component__category__project",
    )
    rows = _filter_outcome(rows, effective)
    # The default view reads as a to-do list: worst first.
    if not outcome:
        rows = rows.annotate(
            _severity_order=Case(
                *(
                    When(outcome=value, then=Value(rank))
                    for value, rank in (
                        (_OUTCOME.CRITICAL, 0),
                        (_OUTCOME.MAJOR, 1),
                        (_OUTCOME.MINOR, 2),
                        (_OUTCOME.UNPARSED, 3),
                        (_OUTCOME.STALE_CONFLICT, 4),
                    )
                ),
                default=Value(5),
                output_field=models.IntegerField(),
            )
        )
    page = Paginator(
        rows.order_by(
            *(["_severity_order"] if not outcome else []), "unit_id_snapshot"
        ),
        50,
    ).get_page(request.GET.get("page"))
    for row in page:
        _annotate_row(row)

    # -- triage: the one question the first screen answers -----------------
    # blocking = held critical rows that a human has not accepted as-is,
    # the same Q the hand-off gate uses, over this run's rows.
    blocking_count = (
        _filter_outcome(base_rows, "critical")
        .exclude(verdict__resolution=_RESOLUTION.ACCEPTED_AS_IS)
        .count()
    )
    needs_recheck = counts["unparsed"] + counts["stale-conflict"]
    # Does a critical actually hold the string back from export? Only under
    # a restrictive commit policy (finding 2): the default policy still
    # ships a rejected string, so the page must not claim otherwise.
    blocks_release = _blocks_release(scope)
    categories = _category_rows(
        _filter_outcome(base_rows, "actionable").filter(
            outcome__in=(_OUTCOME.CRITICAL, _OUTCOME.MAJOR, _OUTCOME.MINOR)
        )
    )
    for entry in categories:
        entry["review_url"] = (
            _review_url(scope, entry["query"]) if entry["query"] else ""
        )
    triage = {
        "blocking": blocking_count,
        "major": counts["major"],
        "minor": counts["minor"],
        "passed": counts["passed"],
        "candidates": counts["candidates"],
        "needs_recheck": needs_recheck,
        "total_actionable": blocking_count
        + counts["major"]
        + counts["minor"]
        + needs_recheck,
        "blocks_release": blocks_release,
        "top_category": categories[0] if categories else None,
    }

    return render(
        request,
        "judge-run.html",
        {
            "run": run,
            "scope": scope,
            "stats": [
                (key, label, counts[key]) for key, label in _OUTCOME_LABELS.items()
            ],
            "counts": counts,
            "triage": triage,
            "categories": categories,
            "blocking_review_url": _review_url(
                scope, "judge:reject AND NOT judge:resolved"
            ),
            "flagged_review_url": _review_url(scope, "judge:reject OR judge:flag"),
            # Covers "not blocking but still needs a look": major or minor,
            # for the hero card's middle state (finding 5: still-live query).
            "needs_review_url": _review_url(scope, "judge:flag OR judge:minor"),
            "recheck_review_url": _review_url(scope, "judge:unparsed OR judge:stale"),
            "outcome": outcome,
            "bucket": effective,
            "query_string": f"outcome={outcome}" if outcome else "",
            "page_obj": page,
        },
    )
