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

from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Case, CharField, Count, F, Q, Sum, Value, When
from django.db.models.functions import MD5, Cast
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext, gettext_lazy, pgettext_lazy

from weblate.lang.models import Language
from weblate.trans.models import (
    Category,
    Component,
    JudgeRunUnit,
    ProducerRun,
    Project,
    Translation,
)
from weblate.trans.models.judge import (
    RUN_KIND_LABELS,
    SEVERITY_RANK,
    JudgeVerdict,
    compute_target_storage_hash,
)
from weblate.trans.models.llm_usage import LLMUsageLog, run_spend
from weblate.utils.stats import ProjectLanguage
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import Model, QuerySet

    from weblate.auth.models import AuthenticatedHttpRequest

_SCOPE_MODELS: dict[str, type[Model]] = {
    ProducerRun.ScopeType.TRANSLATION: Translation,
    ProducerRun.ScopeType.COMPONENT: Component,
    ProducerRun.ScopeType.CATEGORY: Category,
    ProducerRun.ScopeType.PROJECT: Project,
    ProducerRun.ScopeType.WORKSPACE: Workspace,
}

_OUTCOME = JudgeRunUnit.Outcome
_REPAIR = JudgeRunUnit.RepairStatus
_RESOLUTION = JudgeVerdict.Resolution

# The launch modes the per-scope run history lists. A producer launch from
# the automatic translation form is "judge"; ``queue_judge_recheck`` writes
# "recheck" and the deferral drain pass writes "drain". An allowlist rather
# than an exclusion list: a mode added later must opt into the menu
# explicitly instead of silently competing with real launches for its rows.
HISTORY_MODES = ("judge", "translate", "suggest", "fuzzy", "approved")

# Modes that contain verdict data and must retain the review gate.
JUDGE_MODES = ("judge", "recheck", "drain")
# Modes produced by AutoForm that contain only the launcher-visible receipt.
MT_LAUNCH_MODES = ("translate", "suggest", "fuzzy", "approved")
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
    "actionable": gettext_lazy("Run outcomes needing attention"),
    "critical": gettext_lazy("Critical in this run"),
    "major": gettext_lazy("Major in this run"),
    "minor": gettext_lazy("Minor in this run"),
    "unparsed": gettext_lazy("Unparsed"),
    "stale-conflict": gettext_lazy("Stale conflict"),
    "changed-since-run": gettext_lazy("Changed since this run"),
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


def _changed_since_run(rows: QuerySet) -> QuerySet:
    """Rows whose current target differs from this report's judged target."""
    return rows.filter(
        unit__isnull=False,
        verdict__target_storage_hash__isnull=False,
    ).exclude(verdict__target_storage_hash=MD5(F("unit__target")))


def _filter_outcome(rows: QuerySet, key: str) -> QuerySet:
    """Apply one report bucket's filter. ``key`` must be pre-validated."""
    if key == "actionable":
        return rows.filter(outcome__in=_ACTIONABLE_OUTCOMES)
    if key == "changed-since-run":
        return _changed_since_run(rows)
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


def _annotate_row(row: JudgeRunUnit) -> None:
    """
    Compute the template-facing row fields.

    Adds no queries: the page's select_related covers unit, translation,
    component, project and verdict.
    """
    unit = row.unit
    verdict = row.verdict
    if unit is None:
        row.current_target_matches = False  # type: ignore[attr-defined]
    elif verdict is not None and verdict.target_storage_hash:
        row.current_target_matches = (  # type: ignore[attr-defined]
            verdict.target_storage_hash == compute_target_storage_hash(unit.target)
        )
    else:
        row.current_target_matches = (  # type: ignore[attr-defined]
            unit.get_target_plurals() == row.after_target
        )
    row.editor_url = unit.get_absolute_url() if unit is not None else ""  # type: ignore[attr-defined]
    if unit is None:
        row.source_text = ""  # type: ignore[attr-defined]
        row.target_text = " / ".join(row.input_target)  # type: ignore[attr-defined]
    else:
        row.source_text = " / ".join(unit.get_source_plurals())  # type: ignore[attr-defined]
        row.target_text = " / ".join(unit.get_target_plurals())  # type: ignore[attr-defined]
    primary = verdict.primary_error if verdict else None
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
    elif not row.current_target_matches:
        row.action = gettext_lazy("Check the current verdict")  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.CANDIDATE_STORED:
        row.action = gettext_lazy("Review the suggested fix")  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.APPLIED:
        row.action = gettext_lazy("See the applied fix")  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.NO_ENGINE_FOR_LANGUAGE:
        # No candidate was ever attempted for this row: the run-level
        # warning explains why once, and the row must not promise a fix
        # that cannot arrive.
        row.action = gettext_lazy("Fix by hand")  # type: ignore[attr-defined]
    else:
        row.action = _ACTION_BY_OUTCOME.get(  # type: ignore[attr-defined]
            row.outcome, gettext_lazy("View")
        )


def _get_scope(run: ProducerRun):
    """Resolve the run's closed scope, or 404 when it no longer exists."""
    if run.scope_type == ProducerRun.ScopeType.PROJECT_LANGUAGE:
        try:
            project_id, language_id = map(int, run.scope_id.split("-", 1))
            return ProjectLanguage(
                Project.objects.get(pk=project_id),
                Language.objects.get(pk=language_id),
            )
        except (Project.DoesNotExist, Language.DoesNotExist, ValueError) as error:
            raise Http404 from error
    model = _SCOPE_MODELS.get(run.scope_type)
    if model is None:
        raise Http404
    try:
        return model.objects.get(pk=run.scope_id)  # type: ignore[attr-defined]
    except (ValueError, model.DoesNotExist) as error:  # type: ignore[attr-defined]
        raise Http404 from error


def producer_run_modes(user, scope) -> tuple[str, ...]:
    """Which of ``HISTORY_MODES`` this user may currently see for this scope."""
    if not user.has_perm("translation.auto", scope):
        return ()
    may_review = settings.JUDGE_ENABLED and user.has_perm("unit.review", scope)
    return tuple(
        mode
        for mode in HISTORY_MODES
        if mode in MT_LAUNCH_MODES or (may_review and mode in JUDGE_MODES)
    )


def user_can_view_producer_run(user, scope, run) -> bool:
    """Whether ``user`` currently may view this run."""
    if not user.has_perm("translation.auto", scope):
        return False
    if run.requested_mode in MT_LAUNCH_MODES:
        return True
    if run.requested_mode in JUDGE_MODES:
        return settings.JUDGE_ENABLED and user.has_perm("unit.review", scope)
    return False


def _cast_ids(queryset) -> QuerySet:
    """Return the scope ids of a nesting level, as ``scope_id`` stores them."""
    return queryset.annotate(_scope_id=Cast("pk", CharField())).values("_scope_id")


def _project_language_runs(project_ids: Iterable[int]) -> Q:
    """
    Match the project-language runs of the given projects.

    ``ProjectLanguage.pk`` is ``"<project>-<language>"``
    (``weblate/utils/stats.py``), so membership is a prefix test rather than
    a subquery, and the trailing dash keeps project 1 from matching project
    11. The ids are passed in already known: a project knows its own, and a
    workspace holds few projects.
    """
    query = Q(pk=None)
    for project_id in project_ids:
        query |= Q(
            scope_type=ProducerRun.ScopeType.PROJECT_LANGUAGE,
            scope_id__startswith=f"{project_id}-",
        )
    return query


def _scope_run_query(
    scope: Translation | Component | Category | Project | ProjectLanguage | Workspace,
) -> Q:
    """
    Match the runs launched for this scope and for everything nested in it.

    A translation and a project language match themselves alone; a category
    also matches its nested categories, their components and translations; a
    component also matches its translations; a project also matches its
    categories, project languages, components and their translations; a
    workspace also matches everything of its projects. ``scope_id`` stores
    ``str(pk)``, so each nested level's membership is matched through a
    ``Cast("pk", CharField())`` subquery over that level's own queryset: one
    SQL subquery per branch, evaluated inside the single run lookup, never a
    materialized id list per nesting level (which a project page would pay
    one query for). An unknown scope matches nothing.
    """
    match scope:
        case Translation():
            return Q(
                scope_type=ProducerRun.ScopeType.TRANSLATION, scope_id=str(scope.pk)
            )
        case ProjectLanguage():
            return Q(
                scope_type=ProducerRun.ScopeType.PROJECT_LANGUAGE,
                scope_id=str(scope.pk),
            )
        case Category():
            # A component may sit up to three category levels deep
            # (``Category.objects`` prefetches exactly that depth), and every
            # level carries its own runs.
            categories = Category.objects.filter(
                Q(pk=scope.pk) | Q(category=scope) | Q(category__category=scope)
            )
            return (
                Q(
                    scope_type=ProducerRun.ScopeType.CATEGORY,
                    scope_id__in=_cast_ids(categories),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.COMPONENT,
                    scope_id__in=_cast_ids(
                        Component.objects.filter(category__in=categories)
                    ),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.TRANSLATION,
                    scope_id__in=_cast_ids(
                        Translation.objects.filter(component__category__in=categories)
                    ),
                )
            )
        case Component():
            return Q(
                scope_type=ProducerRun.ScopeType.COMPONENT, scope_id=str(scope.pk)
            ) | Q(
                scope_type=ProducerRun.ScopeType.TRANSLATION,
                scope_id__in=_cast_ids(Translation.objects.filter(component=scope)),
            )
        case Project():
            return (
                Q(scope_type=ProducerRun.ScopeType.PROJECT, scope_id=str(scope.pk))
                | _project_language_runs([scope.pk])
                | Q(
                    scope_type=ProducerRun.ScopeType.CATEGORY,
                    scope_id__in=_cast_ids(Category.objects.filter(project=scope)),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.COMPONENT,
                    scope_id__in=_cast_ids(Component.objects.filter(project=scope)),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.TRANSLATION,
                    scope_id__in=_cast_ids(
                        Translation.objects.filter(component__project=scope)
                    ),
                )
            )
        case Workspace():
            return (
                Q(scope_type=ProducerRun.ScopeType.WORKSPACE, scope_id=str(scope.pk))
                | _project_language_runs(
                    Project.objects.filter(workspace=scope).values_list("pk", flat=True)
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.PROJECT,
                    scope_id__in=_cast_ids(Project.objects.filter(workspace=scope)),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.CATEGORY,
                    scope_id__in=_cast_ids(
                        Category.objects.filter(project__workspace=scope)
                    ),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.COMPONENT,
                    scope_id__in=_cast_ids(
                        Component.objects.filter(project__workspace=scope)
                    ),
                )
                | Q(
                    scope_type=ProducerRun.ScopeType.TRANSLATION,
                    scope_id__in=_cast_ids(
                        Translation.objects.filter(component__project__workspace=scope)
                    ),
                )
            )
        case _:
            return Q(pk=None)


def recent_producer_runs(
    scope: Translation | Component | Category | Project | ProjectLanguage | Workspace,
    *,
    user,
    limit: int = 10,
) -> list[ProducerRun]:
    """
    Return the scope's most recent producer launches, newest first, materialized.

    Callers take ``runs[0]`` as the newest run and iterate the remainder for
    the menu, so the list is evaluated exactly once - never ``.first()`` or
    ``[0]`` on the queryset itself, either of which would issue a second
    ``LIMIT 1`` query ahead of the menu's ``LIMIT 10`` and silently double
    the page's query budget.

    Only ``HISTORY_MODES`` reaches the menu. The two excluded modes are
    ``JudgeRun`` rows too, but neither is a launch a producer returns to a
    scope page to find: an editor one-unit re-check ("recheck") reports its
    outcome on that string's own verdict card, and the deferred-retry drain
    pass ("drain") has no actor at all. Both are still addressable by URL,
    and a re-check is still linked from the task alert that finishes it.
    Without this filter the cheapest action in the product evicts the most
    expensive one: ten one-string re-checks fill the whole ten-row window,
    which is exactly how a 462-string component launch became unreachable on
    production.
    """
    modes = producer_run_modes(user, scope)
    if not modes:
        return []
    return list(
        ProducerRun.objects.filter(_scope_run_query(scope), requested_mode__in=modes)
        .order_by("-created")
        .select_related("actor")[:limit]
    )


@login_required
def producer_run(request: AuthenticatedHttpRequest, pk) -> HttpResponse:
    run = get_object_or_404(ProducerRun.objects.select_related("actor"), pk=pk)
    scope = _get_scope(run)
    # Permission is re-checked against the current user, never inferred from
    # the stored actor: a launcher can lose access after the run completes.
    if not user_can_view_producer_run(request.user, scope, run):
        raise Http404

    outcome = request.GET.get("outcome", "")
    is_judge_run = run.requested_mode in JUDGE_MODES
    operation = (
        LLMUsageLog.Operation.JUDGE
        if is_judge_run
        else LLMUsageLog.Operation.TRANSLATION
    )
    spend = run_spend(run.pk, operation)
    language_spend = [
        {
            "language": row["target_language_code"],
            "service": row["service"],
            "model": row["model"],
            "requests": row["requests"],
            "strings_sent": row["strings_sent"] or 0,
            "cost_usd": row["known_cost_usd"] or Decimal(0),
            "unpriced_requests": row["unpriced_requests"],
        }
        for row in LLMUsageLog.objects.filter(
            run_id=run.pk, operation=LLMUsageLog.Operation.TRANSLATION
        )
        .values("target_language_code", "service", "model")
        .annotate(
            requests=Count("id"),
            strings_sent=Sum("batch_size"),
            known_cost_usd=Sum("cost_usd"),
            unpriced_requests=Count("id", filter=Q(cost_usd__isnull=True)),
        )
        .order_by("-known_cost_usd", "target_language_code", "service", "model")
    ]
    scope_query_url = (
        _review_url(scope, run.requested_query)
        if run.requested_query
        else scope.get_absolute_url()
    )
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
        "changed_since_run": counts["changed-since-run"],
        "top_category": categories[0] if categories else None,
    }

    return render(
        request,
        "producer-run.html",
        {
            "run": run,
            "scope": scope,
            "run_kind_label": RUN_KIND_LABELS.get(
                run.requested_mode, gettext("Producer run")
            ),
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
            "is_judge_run": is_judge_run,
            "run_spend": spend,
            "written": run.summary.get("written", 0),
            "language_spend": language_spend,
            "translation_spend": run_spend(run.pk, LLMUsageLog.Operation.TRANSLATION),
            "scope_query_url": scope_query_url,
        },
    )
