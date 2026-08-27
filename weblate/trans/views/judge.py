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

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy

from weblate.trans.models import Component, JudgeRun, JudgeRunUnit, Project, Translation
from weblate.trans.models.judge import JudgeVerdict
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from django.db.models import Model, QuerySet

    from weblate.auth.models import AuthenticatedHttpRequest, User

_SCOPE_MODELS: dict[str, type[Model]] = {
    JudgeRun.ScopeType.TRANSLATION: Translation,
    JudgeRun.ScopeType.COMPONENT: Component,
    JudgeRun.ScopeType.PROJECT: Project,
    JudgeRun.ScopeType.WORKSPACE: Workspace,
}

_OUTCOME = JudgeRunUnit.Outcome
_REPAIR = JudgeRunUnit.RepairStatus
_RESOLUTION = JudgeVerdict.Resolution

# Every entry here is both a valid ``?outcome=`` value and a header row;
# order is display order. The report-local list uses the identical filter,
# so a header count and its drill-down row count can never disagree.
_OUTCOME_LABELS = {
    "matched": gettext_lazy("Matched"),
    "checked": gettext_lazy("Checked"),
    "cached": gettext_lazy("Cached"),
    "skipped": gettext_lazy("Skipped"),
    "repaired": gettext_lazy("Repaired"),
    "rolled-back": gettext_lazy("Rolled back"),
    "minor": gettext_lazy("Minor noted"),
    "major": gettext_lazy("Major not fixed"),
    "critical": gettext_lazy("Critical held"),
    "unparsed": gettext_lazy("Unparsed"),
    "stale-conflict": gettext_lazy("Stale conflict"),
    "accepted-as-is": gettext_lazy("Accepted as is"),
    "escalated": gettext_lazy("Escalated"),
}


def _filter_outcome(rows: QuerySet, key: str) -> QuerySet:
    """Apply one report bucket's filter. ``key`` must be pre-validated."""
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


def last_judge_run(
    scope: Translation | Component | Project | Workspace,
    *,
    actor: User,
) -> JudgeRun | None:
    """
    Return the requesting user's own most recent run for this exact scope.

    Filtered by actor, not just scope: get_user_tasks() forgets a settled
    task immediately (a shared, tested contract - see
    test_finished_task_is_forgotten), so a reload after completion has no
    surviving task id to look up. Scope alone would then show whichever
    run is newest regardless of who launched it, including a concurrent
    launch by someone else on a shared component; actor narrows this back
    to an exact identity match for the common case (at most one launcher
    reviewing their own reload), and "the latest of my own launches" for a
    user who launched more than once is the expected result, not an
    ambiguity.
    """
    match scope:
        case Translation():
            scope_type = JudgeRun.ScopeType.TRANSLATION
        case Component():
            scope_type = JudgeRun.ScopeType.COMPONENT
        case Project():
            scope_type = JudgeRun.ScopeType.PROJECT
        case Workspace():
            scope_type = JudgeRun.ScopeType.WORKSPACE
        case _:
            return None
    return (
        JudgeRun.objects.filter(
            scope_type=scope_type, scope_id=str(scope.pk), actor=actor
        )
        .order_by("-created")
        .first()
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
    base_rows = JudgeRunUnit.objects.filter(run=run)
    counts = {key: _filter_outcome(base_rows, key).count() for key in _OUTCOME_LABELS}
    rows = base_rows.select_related("unit", "unit__translation", "verdict")
    if outcome:
        rows = _filter_outcome(rows, outcome)
    page = Paginator(rows.order_by("unit_id_snapshot"), 50).get_page(
        request.GET.get("page")
    )
    for row in page:
        row.current_target_matches = (  # type: ignore[attr-defined]
            row.unit is not None and row.unit.get_target_plurals() == row.input_target
        )

    return render(
        request,
        "judge-run.html",
        {
            "run": run,
            "scope": scope,
            "stats": [
                (key, label, counts[key]) for key, label in _OUTCOME_LABELS.items()
            ],
            "outcome": outcome,
            "query_string": f"outcome={outcome}" if outcome else "",
            "page_obj": page,
        },
    )
