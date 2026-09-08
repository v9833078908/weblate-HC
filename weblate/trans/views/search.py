# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext, ngettext
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from weblate.checks.models import CHECKS
from weblate.lang.models import Language
from weblate.trans.actions import ActionEvents
from weblate.trans.bulk import bulk_perform
from weblate.trans.fix_check import (
    acquire_fix_check_lock,
    collect_fix_candidates,
    fix_check_lock_key,
    release_fix_check_lock,
)
from weblate.trans.forms import (
    BulkEditForm,
    FixCheckConfirmForm,
    ReplaceConfirmForm,
    ReplaceForm,
    SearchForm,
)
from weblate.trans.models import Category, Component, Project, Translation, Unit
from weblate.trans.models.unit import fill_in_source_translation
from weblate.trans.tasks import fix_failing_checks
from weblate.trans.util import render
from weblate.utils import messages
from weblate.utils.celery import add_user_task, store_task_metadata
from weblate.utils.ratelimit import check_rate_limit
from weblate.utils.stats import CategoryLanguage, ProjectLanguage
from weblate.utils.views import (
    get_paginator,
    import_message,
    parse_path_units,
    show_form_errors,
)
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from weblate.auth.models import AuthenticatedHttpRequest


SEARCH_SUMMARY_MAX_STRINGS = 1_000
SEARCH_REPLACE_PREVIEW_LIMIT = 250


@login_required
@require_POST
def search_replace(request: AuthenticatedHttpRequest, path):
    obj, unit_set, context = parse_path_units(
        request,
        path,
        (
            Translation,
            Component,
            Project,
            ProjectLanguage,
            Category,
            CategoryLanguage,
            Workspace,
        ),
    )

    if isinstance(obj, Workspace):
        unit_set = unit_set.filter_editable_scope(request.user)
        if not unit_set.exists():
            raise PermissionDenied
    elif not request.user.has_perm("unit.edit", obj):
        raise PermissionDenied

    form = ReplaceForm(obj=obj, data=request.POST)

    if not form.is_valid():
        messages.error(request, gettext("Could not process form!"))
        show_form_errors(request, form)
        return redirect(obj)

    search_text = form.cleaned_data["search"]
    replacement = form.cleaned_data["replacement"]
    query = form.cleaned_data.get("q")

    matching = unit_set.filter(target__contains=search_text)
    if query:
        matching = matching.search(query)

    updated = 0

    editable_units = (
        unit
        for unit in matching.order_by("id").iterator(
            chunk_size=SEARCH_REPLACE_PREVIEW_LIMIT + 1
        )
        if request.user.has_perm("unit.edit", unit)
    )
    matching_ids = [
        unit.id for unit in islice(editable_units, SEARCH_REPLACE_PREVIEW_LIMIT + 1)
    ]

    if matching_ids:
        if len(matching_ids) > SEARCH_REPLACE_PREVIEW_LIMIT:
            matching_ids = matching_ids[:SEARCH_REPLACE_PREVIEW_LIMIT]
            limited = True
        else:
            limited = False

        matching = Unit.objects.filter(id__in=matching_ids).prefetch()

        confirm_initial = {
            "q": query,
            "path": form.cleaned_data.get("path", ""),
            "search": search_text,
            "replacement": replacement,
        }
        confirm = ReplaceConfirmForm(matching, request.POST)

        if not confirm.is_valid():
            for unit in matching:
                # This is rendered using format_unit_target which does split_plurals
                unit.replacement = unit.target.replace(search_text, replacement)
            context.update(
                {
                    "matching": matching,
                    "search_query": search_text,
                    "replacement": replacement,
                    "form": form,
                    "limited": limited,
                    "confirm": ReplaceConfirmForm(matching, initial=confirm_initial),
                }
            )
            return render(request, "replace.html", context)

        matching = confirm.cleaned_data["units"]

        with transaction.atomic():
            for unit in matching.select_for_update():
                if not request.user.has_perm("unit.edit", unit):
                    continue
                unit.translate(
                    request.user,
                    [
                        plural.replace(search_text, replacement)
                        for plural in unit.get_target_plurals()
                    ],
                    unit.state,
                    change_action=ActionEvents.REPLACE,
                )
                updated += 1

    import_message(
        request,
        updated,
        gettext("Search and replace completed, no strings were updated."),
        ngettext(
            "Search and replace completed, %d string was updated.",
            "Search and replace completed, %d strings were updated.",
            updated,
        ),
    )

    return redirect(obj)


@never_cache
def search(request: AuthenticatedHttpRequest, path=None):
    """Perform site-wide search on units."""
    is_ratelimited = not check_rate_limit("search", request)
    obj, unit_set, context = parse_path_units(
        request,
        path,
        (
            Component,
            Project,
            ProjectLanguage,
            Translation,
            Category,
            CategoryLanguage,
            Workspace,
            Language,
            None,
        ),
    )

    search_form = SearchForm(request=request, data=request.GET, obj=obj)
    context["search_form"] = search_form
    context["back_url"] = obj.get_absolute_url() if obj is not None else None

    if not is_ratelimited and request.GET and search_form.is_valid():
        # This is ugly way to hide query builder when showing results
        search_form = SearchForm(
            request=request, data=request.GET, show_builder=False, obj=obj
        )
        search_form.is_valid()
        search_units = unit_set.prefetch_bulk().search(
            search_form.cleaned_data.get("q", ""), project=context.get("project")
        )
        ordered_units = search_units.order_by_request(search_form.cleaned_data, obj)
        units = get_paginator(request, ordered_units)
        total_strings = units.paginator.count
        total_words = None
        if total_strings <= SEARCH_SUMMARY_MAX_STRINGS:
            total_words = search_units.aggregate(total_words=Sum("num_words"))[
                "total_words"
            ]

        # Make sure source translation is available
        fill_in_source_translation(units)
        # Rebuild context from scratch here to get new form
        context.update(
            {
                "search_form": search_form,
                "show_results": True,
                "page_obj": units,
                "path_object": obj,
                "title": gettext("Search for %s") % (search_form.cleaned_data["q"]),
                "query_string": search_form.urlencode(),
                "search_url": search_form.urlencode(),
                "search_query": search_form.cleaned_data["q"],
                "search_items": search_form.items(),
                "total_strings": total_strings,
                "total_words": total_words,
            }
        )
    elif is_ratelimited:
        messages.error(
            request, gettext("Too many search queries, please try again later.")
        )
    elif request.GET:
        messages.error(request, gettext("Invalid search query!"))
        show_form_errors(request, search_form)

    return render(request, "search.html", context)


@login_required
@require_POST
@never_cache
def bulk_edit(request: AuthenticatedHttpRequest, path):
    obj, unit_set, context = parse_path_units(
        request,
        path,
        (
            Translation,
            Component,
            Project,
            ProjectLanguage,
            Category,
            CategoryLanguage,
            Workspace,
        ),
    )

    if not request.user.has_perm("unit.bulk_edit", obj) or not request.user.has_perm(
        "unit.edit", obj
    ):
        raise PermissionDenied

    form = BulkEditForm(request.user, obj, request.POST, project=context.get("project"))

    if not form.is_valid():
        messages.error(request, gettext("Could not process form!"))
        show_form_errors(request, form)
        return redirect(obj)

    updated = bulk_perform(
        request.user,
        unit_set,
        query=form.cleaned_data["q"],
        target_state=form.cleaned_data["state"],
        add_flags=form.cleaned_data["add_flags"],
        remove_flags=form.cleaned_data["remove_flags"],
        add_labels=form.cleaned_data["add_labels"],
        remove_labels=form.cleaned_data["remove_labels"],
        project=context.get("project"),
        components=context["components"],
    )

    import_message(
        request,
        updated,
        gettext("Bulk edit completed, no strings were updated."),
        ngettext(
            "Bulk edit completed, %d string was updated.",
            "Bulk edit completed, %d strings were updated.",
            updated,
        ),
    )

    return redirect(obj)


@login_required
@never_cache
def fix_check(request: AuthenticatedHttpRequest, name, path):
    """
    Mass-fix one failing check over a translation/component/project scope.

    GET renders the tier-appropriate confirmation screen (safe: a count;
    review: a preview with checkboxes); POST re-validates the selection
    against the live query and queues `fix_failing_checks`
    (docs/product/plans/2026-08-25-mass-fix-failing-checks.md, Task 5).
    """
    obj, unit_set, context = parse_path_units(
        request, path, (Translation, Component, Project)
    )

    if not request.user.has_perm("unit.bulk_edit", obj) or not request.user.has_perm(
        "unit.edit", obj
    ):
        raise PermissionDenied

    check_obj = CHECKS.get(name)
    if check_obj is None or check_obj.mass_fixup is None:
        raise Http404

    component = context.get("component")
    if component is not None and component.is_glossary:
        raise Http404

    project = context.get("project")

    if isinstance(obj, Translation):
        scope_type = "translation"
    elif isinstance(obj, Component):
        scope_type = "component"
    else:
        scope_type = "project"
    lock_key = fix_check_lock_key(check_obj.check_id, scope_type, obj.pk)

    if request.method == "POST":
        form = FixCheckConfirmForm(request.POST)
        if not form.is_valid():
            messages.error(request, gettext("Could not process form!"))
            show_form_errors(request, form)
            return redirect(obj)

        unit_ids = None if check_obj.mass_fixup == "safe" else form.get_unit_ids()

        task_id = str(uuid4())
        if not acquire_fix_check_lock(lock_key, task_id):
            messages.error(
                request,
                gettext(
                    "This check is already being fixed for this scope; "
                    "wait for that run to finish before trying again."
                ),
            )
            return redirect(obj)

        translation_id: int | None = None
        component_id: int | None = None
        project_id: int | None = None
        if isinstance(obj, Translation):
            translation_id = obj.pk
        elif isinstance(obj, Component):
            component_id = obj.pk
        else:
            project_id = obj.pk

        task_kwargs = {
            "user_id": request.user.id,
            "check_id": check_obj.check_id,
            "lock_key": lock_key,
            "unit_ids": unit_ids,
            "translation_id": translation_id,
            "component_id": component_id,
            "project_id": project_id,
        }

        try:
            task = fix_failing_checks.apply_async(kwargs=task_kwargs, task_id=task_id)
        except Exception:
            release_fix_check_lock(lock_key, task_id)
            raise

        if task.ready():
            # Eager execution (CELERY_TASK_ALWAYS_EAGER): the result is
            # already available, so flash it directly instead of a polling
            # widget with nothing left to poll.
            task_result = task.result
            if (
                isinstance(task_result, dict)
                and task_result.get("status") == "completed"
            ):
                messages.success(
                    request,
                    task_result.get("message") or gettext("Mass fix completed."),
                )
            else:
                message = (
                    task_result.get("message")
                    if isinstance(task_result, dict)
                    else str(task_result)
                )
                messages.error(request, message or gettext("Mass fix failed."))
            return redirect(obj)

        store_task_metadata(
            task.id,
            translation_id=translation_id,
            component_id=component_id,
            user_id=request.user.id if isinstance(obj, Project) else None,
        )
        message = gettext("Mass fix queued. You can close this page.")
        add_user_task(
            request.user.id,
            task.id,
            text=message,
            label=str(obj),
            url=obj.get_absolute_url(),
        )
        messages.success(request, message, f"task:{task.id}")
        return redirect(obj)

    candidates = collect_fix_candidates(request.user, unit_set, project, check_obj)
    context.update(
        {
            "check": check_obj,
            "candidates": candidates,
            "form": FixCheckConfirmForm(),
        }
    )
    return render(request, "fix_check.html", context)
