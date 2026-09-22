# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404, JsonResponse
from django.http.response import HttpResponseServerError
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_http_methods, require_POST

from weblate.checks.flags import GLOSSARY_LANGUAGE_SCOPED_FLAGS, Flags
from weblate.trans.forms import ContextForm, MatrixLanguageForm
from weblate.trans.models import Component, Unit
from weblate.trans.models.translation import ConfirmedMove, ConfirmedRename
from weblate.trans.util import redirect_next, render
from weblate.utils import messages
from weblate.utils.views import parse_path, show_form_errors

if TYPE_CHECKING:
    from weblate.auth.models import AuthenticatedHttpRequest


@require_POST
@login_required
@transaction.atomic
def edit_context(request: AuthenticatedHttpRequest, pk):
    unit = get_object_or_404(Unit.objects.filter_access(request.user), pk=pk)
    if not unit.is_source and not unit.translation.component.is_glossary:
        msg = "Non source unit!"
        raise Http404(msg)

    do_add = "addflag" in request.POST
    if do_add or "removeflag" in request.POST:
        if not request.user.has_perm("meta:unit.flag", unit.translation):
            raise PermissionDenied
        flag = request.POST.get("addflag", request.POST.get("removeflag"))
        flags = unit.get_unit_flags()
        if (
            flag in {"terminology", "forbidden", "read-only"}
            and not unit.is_source
            and flag not in flags
        ):
            unit = unit.source_unit
            flags = Flags(unit.extra_flags)
        elif flag in GLOSSARY_LANGUAGE_SCOPED_FLAGS and unit.is_source:
            # A per-language mode has no meaning on the source string, which is
            # shared by every language. This is a bad request, not a missing
            # page, so the translator is told why nothing changed.
            messages.error(
                request,
                gettext(
                    "Glossary flags for a single language can only be set on a "
                    "translation, not on the source string."
                ),
            )
            return redirect_next(request.POST.get("next"), unit.get_absolute_url())
        if do_add:
            flags.merge(flag)
            new_flags = flags.format()
            if new_flags != unit.extra_flags:
                unit.update_extra_flags(new_flags, request.user)
        elif flag == "read-only":
            unlocked = unit.unmark_string_read_only(request.user)
            if unlocked:
                messages.success(
                    request,
                    ngettext(
                        "Unlocked the string in one language.",
                        "Unlocked the string in %(count)d languages.",
                        unlocked,
                    )
                    % {"count": unlocked},
                )
        else:
            flags.remove(flag)
            new_flags = flags.format()
            if new_flags != unit.extra_flags:
                unit.update_extra_flags(new_flags, request.user)
    else:
        if not request.user.has_perm("source.edit", unit.translation):
            raise PermissionDenied

        form = ContextForm(request.POST, instance=unit, user=request.user)

        if form.is_valid():
            form.save()
        else:
            messages.error(request, gettext("Could not change additional string info!"))
            show_form_errors(request, form)

    return redirect_next(request.POST.get("next"), unit.get_absolute_url())


def _get_structural_source(request: AuthenticatedHttpRequest, pk: int) -> Unit:
    unit = get_object_or_404(Unit.objects.filter_access(request.user), pk=pk)
    component = unit.translation.component
    if (
        not unit.is_source
        or not component.has_template()
        or component.is_glossary
        or not component.manage_units
        or not component.edit_template
    ):
        raise Http404
    if not request.user.has_perm("component.edit", component):
        raise PermissionDenied
    return unit


@require_POST
@login_required
def rename_key(request: AuthenticatedHttpRequest, pk: int) -> JsonResponse:
    """Preview or apply a source-key rename through a signed UI payload."""
    unit = _get_structural_source(request, pk)
    source = unit.translation
    if source.component.locked:
        return JsonResponse({"error": gettext("This component is locked.")}, status=409)
    # ruff: ignore[too-many-statements-in-try-clause]
    try:
        if not source.component.file_format_supports_key_rename:
            raise Http404
        if request.POST.get("stage") == "preview":
            preview = source.get_rename_preview(
                unit.pk, request.POST.get("new_key", "")
            )
            return JsonResponse(
                {
                    "preview": asdict(preview),
                    "token": signing.dumps(asdict(preview), salt="rename-key"),
                }
            )
        payload = signing.loads(
            request.POST["token"], salt="rename-key", max_age=15 * 60
        )
        change = ConfirmedRename(**payload)
        try:
            renamed = source.rename_unit_key(change=change, user=request.user)
        except DatabaseError:
            try:
                renamed = source.rename_unit_key(change=change, user=request.user)
            except DatabaseError:
                return JsonResponse(
                    {
                        "error": gettext(
                            "The key was committed to the repository, but could not "
                            "be synchronized. Create a new preview and try again."
                        )
                    },
                    status=409,
                )
    except PermissionError as error:
        return JsonResponse({"error": str(error)}, status=403)
    except (KeyError, signing.BadSignature, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=409)
    return JsonResponse({"url": renamed.get_absolute_url(), "unit_id": renamed.pk})


@login_required
@require_http_methods(["GET", "POST"])
def move_string(request: AuthenticatedHttpRequest, pk: int) -> JsonResponse:
    """Autocomplete, preview or apply a source-string move."""
    unit = _get_structural_source(request, pk)
    source = unit.translation
    if source.component.locked:
        return JsonResponse({"error": gettext("This component is locked.")}, status=409)
    if not source.component.file_format_supports_key_order:
        raise Http404
    if request.method == "GET":
        query = request.GET.get("q", "")
        matches = (
            source.unit_set.exclude(pk=unit.pk)
            .filter(context__icontains=query)
            .order_by("position")[:20]
        )
        return JsonResponse(
            {
                "results": [
                    {
                        "id": match.pk,
                        "key": match.context,
                        "source": match.source[:160],
                        "position": match.position,
                    }
                    for match in matches
                ]
            }
        )
    # ruff: ignore[too-many-statements-in-try-clause]
    try:
        if request.POST.get("stage") == "preview":
            placement = request.POST.get("placement")
            if placement not in {"before", "after"}:
                msg = gettext("Invalid string placement.")
                # ruff: ignore[raise-within-try]
                raise ValueError(msg)
            preview = source.get_move_preview(
                unit.pk,
                int(request.POST["anchor"]),
                placement,  # type: ignore[arg-type]
            )
            return JsonResponse(
                {
                    "preview": asdict(preview),
                    "token": signing.dumps(asdict(preview), salt="move-string"),
                }
            )
        payload = signing.loads(
            request.POST["token"], salt="move-string", max_age=15 * 60
        )
        change = ConfirmedMove(**payload)
        try:
            moved = source.move_unit(change=change, user=request.user)
        except DatabaseError:
            try:
                moved = source.move_unit(change=change, user=request.user)
            except DatabaseError:
                return JsonResponse(
                    {
                        "error": gettext(
                            "The string order was committed to the repository, but "
                            "could not be synchronized. Create a new preview and try "
                            "again."
                        )
                    },
                    status=409,
                )
    except PermissionError as error:
        return JsonResponse({"error": str(error)}, status=403)
    except (KeyError, signing.BadSignature, ValueError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=409)
    return JsonResponse(
        {
            "unit_id": moved.unit_id,
            "changed": moved.changed,
            "position": moved.new_position,
            "url": f"{source.get_absolute_url()}?sort_by=position#unit-{moved.unit_id}",
        }
    )


@login_required
def matrix(request: AuthenticatedHttpRequest, path):
    """Matrix view of all strings."""
    obj = parse_path(request, path, (Component,))

    show = False
    translations = None
    language_codes_url = None

    if "lang" in request.GET:
        form = MatrixLanguageForm(obj, request.GET)
        show = form.is_valid()
    else:
        form = MatrixLanguageForm(obj)

    if show:
        translations = (
            obj.translation_set.filter(language__code__in=form.cleaned_data["lang"])
            .select_related("language")
            .order()
        )
        language_codes_url = "&".join(
            f"lang={translation.language.code}" for translation in translations
        )

    return render(
        request,
        "matrix.html",
        {
            "object": obj,
            "project": obj.project,
            "component": obj,
            "translations": translations,
            "language_codes_url": language_codes_url,
            "languages_form": form,
        },
    )


@login_required
def matrix_load(request: AuthenticatedHttpRequest, path):
    """Backend for matrix view of all strings."""
    obj = parse_path(request, path, (Component,))

    try:
        offset = int(request.GET.get("offset", ""))
    except ValueError:
        return HttpResponseServerError("Missing offset")
    form = MatrixLanguageForm(obj, request.GET)
    if not form.is_valid():
        return HttpResponseServerError("Missing lang")
    language_codes = form.cleaned_data["lang"]

    translations_by_code = {
        translation.language.code: translation
        for translation in obj.translation_set.filter(
            language__code__in=language_codes
        ).select_related("language", "plural")
    }
    try:
        # The selected language order defines the matrix column order.
        translations = [translations_by_code[code] for code in language_codes]
    except KeyError as error:
        raise Http404 from error

    source_translation = obj.source_translation
    source_units = list(source_translation.unit_set.order()[offset : offset + 21])
    last = len(source_units) <= 20
    source_units = source_units[:20]
    source_ids = [unit.pk for unit in source_units]

    translations_by_id = {translation.pk: translation for translation in translations}
    translated_units = {translation.pk: {} for translation in translations}
    for unit in Unit.objects.filter(
        translation_id__in=translations_by_id,
        source_unit_id__in=source_ids,
    ).order():
        # Reuse the translations fetched above, including their related objects.
        unit.translation = translations_by_id[unit.translation_id]
        translated_units[unit.translation_id][unit.source_unit_id] = unit

    data = []
    for unit in source_units:
        # Avoid need to fetch source unit again
        unit.source_unit = unit
        units = []
        for translation in translations:
            translated_unit = translated_units[translation.pk].get(unit.pk)
            if translated_unit is not None:
                # Avoid need to fetch source unit again
                translated_unit.source_unit = unit
            units.append(translated_unit)

        data.append((unit, units))

    return render(
        request,
        "matrix-table.html",
        {
            "object": obj,
            "data": data,
            "last": last,
        },
    )
