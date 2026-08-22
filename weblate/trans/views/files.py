# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from weblate.formats.models import EXPORTERS
from weblate.trans.exceptions import (
    FailedCommitError,
    FileParseError,
    PluralFormsMismatchError,
)
from weblate.trans.forms import DownloadForm, get_upload_form
from weblate.trans.models import (
    Category,
    Component,
    ComponentList,
    Project,
    Translation,
    Unit,
)
from weblate.trans.models.multilingual_spreadsheet import ComponentSpreadsheetImportDraft
from weblate.trans.multilingual_spreadsheet import (
    build_preview,
    export_component,
    parse_upload,
)
from weblate.utils.state import STATE_EMPTY, STATE_TRANSLATED
from weblate.utils import messages
from weblate.utils.errors import report_error
from weblate.utils.files import get_upload_message
from weblate.utils.stats import CategoryLanguage, ProjectLanguage
from weblate.utils.views import (
    download_translation_file,
    parse_path,
    show_form_errors,
    zip_download,
)
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Iterator

    from weblate.auth.models import AuthenticatedHttpRequest, User


def iter_workspace_download_components(
    user: User, workspace: Workspace
) -> Iterator[Component]:
    """Yield accessible workspace components the user is allowed to download."""
    components = (
        Component.objects.filter(project__workspace=workspace)
        .filter_access(user)
        .select_related("project")
    )
    for component in components.iterator(chunk_size=1):
        if user.has_perm("translation.download", component):
            yield component


def get_workspace_download_components(
    user: User, workspace: Workspace
) -> list[Component]:
    """Return accessible workspace components the user is allowed to download."""
    return list(iter_workspace_download_components(user, workspace))


def can_download_workspace(user: User, workspace: Workspace) -> bool:
    """Return whether the user can download any workspace component."""
    return next(iter_workspace_download_components(user, workspace), None) is not None


def download_multi(
    request: AuthenticatedHttpRequest,
    translations,
    commit_objs,
    fmt=None,
    name="translations",
):
    filenames = set()
    components = set()
    component_roots = set()
    extra: dict[str, str | bytes] = {}

    for obj in commit_objs:
        try:
            obj.commit_pending("download", None)
        except Exception:
            if isinstance(obj, Project):
                report_error("Download commit", project=obj)
            else:
                report_error("Download commit", project=obj.project)

    if fmt and fmt.startswith("zip:"):
        exporter_format = fmt[4:]
        try:
            exporter_cls = EXPORTERS[exporter_format]
        except KeyError as exc:
            msg = f"Conversion to {exporter_format} is not supported"
            raise Http404(msg) from exc

        for translation in translations:
            exporter = exporter_cls(translation=translation)
            filename = exporter.get_filename()
            if not exporter_cls.supports(translation):
                extra[f"{filename}.skipped"] = (
                    "File format is not compatible with this translation"
                )
            else:
                units = translation.unit_set.prefetch_full().order_by("position")
                exporter.add_units(units)
                extra[filename] = exporter.serialize()
    else:
        for translation in translations:
            component_roots.add(translation.component.full_path)
            # Add translation files
            if translation.filename:
                filenames.add(translation.get_filename())
            # Add templates for all components
            if translation.component_id in components:
                continue
            components.add(translation.component_id)
            for getter in (
                translation.component.get_template_filename,
                translation.component.get_new_base_filename,
                translation.component.get_intermediate_filename,
            ):
                try:
                    fullname = getter()
                except ValidationError:
                    continue
                if fullname and os.path.exists(fullname):
                    filenames.add(fullname)

    return zip_download(
        data_dir("vcs"),
        sorted(filenames),
        name,
        extra=extra,
        allowed_roots=sorted(component_roots),
    )


def download_component_list(request: AuthenticatedHttpRequest, name):
    obj = get_object_or_404(
        ComponentList.objects.filter_access(request.user), slug__iexact=name
    )
    if not request.user.has_perm("translation.download", obj):
        raise PermissionDenied
    components = obj.components.filter_access(request.user)
    return download_multi(
        request,
        Translation.objects.filter(component__in=components),
        components,
        request.GET.get("format"),
        name=obj.slug,
    )


def download(request: AuthenticatedHttpRequest, path):
    """Download translation."""
    obj = parse_path(
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
        components = get_workspace_download_components(request.user, obj)
        if not components:
            raise PermissionDenied
        return download_multi(
            request,
            Translation.objects.filter(component__in=components).prefetch(),
            components,
            request.GET.get("format"),
            name=f"workspace-{obj.pk}",
        )

    if not request.user.has_perm("translation.download", obj):
        raise PermissionDenied

    if isinstance(obj, Translation):
        kwargs = {}

        if "format" in request.GET or "q" in request.GET:
            form = DownloadForm(obj, request.GET)
            if not form.is_valid():
                show_form_errors(request, form)
                return redirect(obj)

            kwargs["query_string"] = form.cleaned_data.get("q", "")
            kwargs["fmt"] = form.cleaned_data["format"]

        return download_translation_file(request, obj, **kwargs)
    if isinstance(obj, ProjectLanguage):
        components = obj.project.component_set.filter_access(request.user)
        return download_multi(
            request,
            Translation.objects.filter(
                component__in=components, language=obj.language
            ).prefetch(),
            [obj.project],
            request.GET.get("format"),
            name=f"{obj.project.slug}-{obj.language.code}",
        )
    if isinstance(obj, Project):
        components = obj.component_set.filter_access(request.user)
        return download_multi(
            request,
            Translation.objects.filter(component__in=components).prefetch(),
            [obj],
            request.GET.get("format"),
            name=obj.slug,
        )
    if isinstance(obj, CategoryLanguage):
        components = obj.category.project.component_set.filter_access(
            request.user
        ).filter(pk__in=obj.category.all_component_ids)
        return download_multi(
            request,
            Translation.objects.filter(
                component__in=components, language=obj.language
            ).prefetch(),
            [obj.category.project],
            request.GET.get("format"),
            name=f"{obj.category.slug}-{obj.language.code}",
        )
    if isinstance(obj, Category):
        components = obj.project.component_set.filter_access(request.user).filter(
            pk__in=obj.all_component_ids
        )
        return download_multi(
            request,
            Translation.objects.filter(component__in=components).prefetch(),
            [obj.project],
            request.GET.get("format"),
            name=obj.slug,
        )
    if isinstance(obj, Component):
        return download_multi(
            request,
            obj.translation_set.prefetch_meta(),
            [obj],
            request.GET.get("format"),
            name=obj.full_slug.replace("/", "-"),
        )
    msg = f"Unsupported download: {obj}"
    raise TypeError(msg)



def multilingual_download(request: AuthenticatedHttpRequest, path, format_name: str):
    """Download one component as a multilingual CSV or XLSX table."""
    component = parse_path(request, path, (Component,))
    if not request.user.has_perm("translation.download", component):
        raise PermissionDenied
    if format_name not in {"csv", "xlsx"}:
        raise Http404
    content = export_component(component, format_name)
    content_type = (
        "text/csv; charset=utf-8"
        if format_name == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="{component.slug}-multilingual.{format_name}"'
    )
    return response



def multilingual_upload(request: AuthenticatedHttpRequest, path):
    component = parse_path(request, path, (Component,))
    if not request.user.has_perm("upload.perform", component):
        raise PermissionDenied
    if request.method == "POST":
        if component.locked:
            messages.error(request, gettext("Access denied."))
            return redirect(component)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            messages.error(request, gettext("Please select a file."))
        else:
            try:
                parsed = parse_upload(component, uploaded)
                preview = build_preview(component, parsed)
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            else:
                draft = ComponentSpreadsheetImportDraft(
                    owner=request.user,
                    session_key=request.session.session_key,
                    component=component,
                    source_filename=uploaded.name,
                    preview_json=json.dumps(
                        {
                            "headers": preview.parsed.headers,
                            "rows": [row.values for row in preview.parsed.rows],
                        }
                    ),
                    baseline_json=json.dumps(
                        {
                            str(unit.pk): [unit.target, unit.state]
                            for unit in Unit.objects.filter(
                                translation__component=component
                            ).exclude(translation=component.source_translation)
                        }
                    ),
                )
                draft.uploaded.save(uploaded.name, uploaded, save=False)
                draft.save()
                return render(
                    request,
                    "multilingual_spreadsheet_import.html",
                    {"component": component, "draft": draft, "preview": preview},
                )
    return render(request, "multilingual_spreadsheet_import.html", {"component": component})


@require_POST
def multilingual_confirm(request: AuthenticatedHttpRequest, token):
    draft = ComponentSpreadsheetImportDraft.get_active(
        token=token, owner=request.user, session_key=request.session.session_key
    )
    if draft is None:
        raise Http404
    component = draft.component
    if not request.user.has_perm("upload.perform", component):
        raise PermissionDenied
    with transaction.atomic():
        baseline = json.loads(draft.baseline_json)
        current = {
            str(unit.pk): [unit.target, unit.state]
            for unit in Unit.objects.select_for_update().filter(
                translation__component=component
            ).exclude(translation=component.source_translation)
        }
        if current != baseline:
            raise ValidationError("The component changed after preview.")
        parsed = parse_upload(component, draft.uploaded)
        build_preview(component, parsed)
        source_units = component.source_translation.unit_set.select_for_update()
        key_field = "context" if component.has_template() else "source"
        source_by_key = {getattr(unit, key_field): unit for unit in source_units}
        for row in parsed.rows:
            source = source_by_key[row.values[0]]
            for language, target in zip(parsed.headers[1:], row.values[1:], strict=True):
                if language in {"context", component.source_language.code}:
                    continue
                unit = Unit.objects.select_for_update().get(
                    translation__component=component,
                    translation__language__code=language,
                    source_unit=source,
                )
                if unit.target != target:
                    unit.translate(
                        request.user,
                        target,
                        STATE_TRANSLATED if target else STATE_EMPTY,
                        propagate=False,
                        select_for_update=False,
                    )
        draft.state = ComponentSpreadsheetImportDraft.State.CONSUMED
        draft.save(update_fields=["state"])
    messages.success(request, gettext("Multilingual spreadsheet imported."))
    return redirect(component)


@require_POST
def multilingual_cancel(request: AuthenticatedHttpRequest, token):
    draft = ComponentSpreadsheetImportDraft.get_active(
        token=token, owner=request.user, session_key=request.session.session_key
    )
    if draft is None:
        raise Http404
    component = draft.component
    if not request.user.has_perm("upload.perform", component):
        raise PermissionDenied
    draft.delete()
    return redirect(component)

@require_POST
def upload(request: AuthenticatedHttpRequest, path):
    """Handle translation upload."""
    obj = parse_path(request, path, (Translation,))

    if not request.user.has_perm("upload.perform", obj):
        raise PermissionDenied

    # Check method and lock
    if obj.component.locked:
        messages.error(request, gettext("Access denied."))
        return redirect(obj)

    # Get correct form handler based on permissions
    form = get_upload_form(request.user, obj, request.POST, request.FILES)

    # Check form validity
    if not form.is_valid():
        messages.error(request, gettext("Please fix errors in the form."))
        show_form_errors(request, form)
        return redirect(obj)

    # Create author name
    author_name = None
    author_email = None
    if request.user.has_perm("upload.authorship", obj):
        author_name = form.cleaned_data["author_name"]
        author_email = form.cleaned_data["author_email"]

    # Check for overwriting
    conflicts = ""
    if request.user.has_perm("upload.overwrite", obj):
        conflicts = form.cleaned_data["conflicts"]

    # Do actual import
    try:
        not_found, skipped, accepted, total = obj.handle_upload(
            request,
            request.FILES["file"],
            conflicts,
            author_name,
            author_email,
            method=form.cleaned_data["method"],
            fuzzy=form.cleaned_data["fuzzy"],
        )
    except PluralFormsMismatchError:
        messages.error(
            request,
            gettext(
                "Plural forms in the uploaded file do not match current translation."
            ),
        )
    except FileParseError as error:
        messages.error(
            request,
            get_upload_error_message(
                error,
                repo_urls=(obj.component.repo, obj.component.push),
                extra_paths=(obj.component.full_path,),
            ),
        )
    except FailedCommitError as error:
        messages.error(
            request,
            get_upload_error_message(
                error,
                repo_urls=(obj.component.repo, obj.component.push),
                extra_paths=(obj.component.full_path,),
            ),
        )
        report_error("Upload error", project=obj.component.project)
    except DatabaseError as error:
        messages.error(
            request,
            get_upload_error_message(
                error,
                repo_urls=(obj.component.repo, obj.component.push),
                extra_paths=(obj.component.full_path,),
            ),
        )
        report_error("Upload error", project=obj.component.project)
    except Exception as error:
        messages.error(
            request,
            get_upload_error_message(
                error,
                repo_urls=(obj.component.repo, obj.component.push),
                extra_paths=(obj.component.full_path,),
            ),
        )
        report_error("Upload error", project=obj.component.project)
    else:
        message = get_upload_message(not_found, skipped, accepted, total)
        if accepted == 0:
            messages.warning(request, message)
        else:
            messages.success(request, message)

    return redirect(obj)
