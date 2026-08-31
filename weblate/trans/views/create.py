# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
import tempfile
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import BadZipfile

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.http import urlencode
from django.utils.translation import gettext, ngettext
from django.views.generic.base import TemplateView, View
from django.views.generic.edit import CreateView

from weblate.glossary.tasks import flag_glossary_terminology
from weblate.lang.models import Language
from weblate.trans.backups import ProjectBackup
from weblate.trans.forms import (
    ComponentBranchForm,
    ComponentCreateForm,
    ComponentDiscoverForm,
    ComponentDocCreateForm,
    ComponentInitCreateForm,
    ComponentScratchCreateForm,
    ComponentSelectForm,
    ComponentZipCreateForm,
    LocKitGlossaryUpdateForm,
    LocKitProfileCorrectionForm,
    LocKitSheetSelectForm,
    ProjectCreateForm,
    ProjectImportCreateForm,
    ProjectImportForm,
)
from weblate.trans.inherited_settings import (
    INHERITABLE_COMPONENT_FLAGS,
    INHERITABLE_COMPONENT_SETTINGS,
    get_inherit_field_name,
)
from weblate.trans.loc_kit import (
    GlossaryAppendCollisionError,
    GlossaryAppendResult,
    GlossaryProfileError,
    ProfileProposalError,
    SampleTooLargeError,
    append_glossary_terms,
    build_glossary_structure_sample,
    cap_preview_warnings,
    profile_document_from_envelope,
    request_profile_proposal,
    validate_glossary_profile,
)
from weblate.trans.models import Category, Component, Project
from weblate.trans.models.loc_kit import LocKitImportDraft
from weblate.trans.tasks import import_project_backup, perform_update
from weblate.utils import messages
from weblate.utils.celery import store_task_metadata
from weblate.utils.licenses import LICENSE_URLS, detect_license
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.ratelimit import check_rate_limit, session_ratelimit_post
from weblate.utils.views import (
    KIT_TABLE_SUFFIXES,
    create_component_from_doc,
    create_component_from_kit,
    parse_path,
)
from weblate.vcs.base import RepositoryError
from weblate.vcs.git import LocalRepository
from weblate.vcs.github import (
    GitHubInstallation,
    get_github_app_configurations,
    get_github_repository_import_url,
    github_app_is_configured,
)
from weblate.vcs.models import VCS_REGISTRY
from weblate.vcs.permissions import github_app_installation_workspaces
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.forms import Form

    from weblate.auth.models import AuthenticatedHttpRequest
    from weblate.trans.forms import (
        ComponentProjectForm,
    )
    from weblate.trans.models.component import ComponentQuerySet

SESSION_CREATE_KEY = "session_component"
INTEGRATION_IMPORT_VCS_KEY = "integration_import_vcs"


def get_creatable_projects(request: AuthenticatedHttpRequest):
    """
    Projects this user may create a component in.

    Single source of truth for the component-creation gate. Every entry
    point - the ordinary wizard and the glossary draft endpoints alike -
    must go through here, so a new entry point cannot accidentally be more
    permissive than the wizard (notably by skipping the billing check).
    """
    if request.user.is_superuser:
        return Project.objects.order()
    if "weblate.billing" in settings.INSTALLED_APPS:
        # ruff: ignore[import-outside-top-level]
        from weblate.billing.models import Billing

        return request.user.managed_projects.filter(
            workspace__billing__in=Billing.objects.get_valid()
        ).order()
    return request.user.managed_projects


class BaseCreateView(CreateView):
    request: AuthenticatedHttpRequest

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.has_billing = "weblate.billing" in settings.INSTALLED_APPS

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_invalid(self, form):
        messages.error(
            self.request,
            gettext(
                "The supplied configuration is incorrect. Please check the errors below.",
            ),
        )
        return super().form_invalid(form)


@method_decorator(login_required, name="dispatch")
@method_decorator(session_ratelimit_post("project"), name="dispatch")
class CreateProject(BaseCreateView):
    model = Project
    object: Project
    form_class: type[Form] = ProjectCreateForm
    workspaces = Workspace.objects.none()

    def get_billing(self, workspace: Workspace | None):
        if workspace is None or not self.has_billing:
            return None
        with suppress(ObjectDoesNotExist):
            return workspace.billing
        return None

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if "workspace" in form.fields:
            workspace_field = form.fields["workspace"]
            workspace_field.queryset = self.workspaces
            with suppress(ValueError, KeyError):
                workspace_field.initial = self.request.GET["workspace"]
            workspace_field.required = False
            if self.request.user.has_perm("project.add"):
                workspace_field.empty_label = gettext("No workspace")
            else:
                workspace_field.required = True
        return form

    @transaction.atomic
    def form_valid(self, form):
        workspace = form.cleaned_data["workspace"]
        if workspace is None and not self.request.user.has_perm("project.add"):
            form.add_error(
                "workspace",
                gettext("Creating a project without a workspace is not allowed."),
            )
            return self.form_invalid(form)
        for field in INHERITABLE_COMPONENT_FLAGS:
            setattr(form.instance, field, workspace is not None)
        license_code = form.cleaned_data.get("license")
        if workspace is None:
            form.instance.inherit_license = False
        elif license_code:
            if not workspace.license:
                if self.request.user.has_perm("workspace.edit", workspace):
                    workspace.license = license_code
                    workspace.acting_user = self.request.user
                    workspace.save(update_fields=["license"])
                    form.instance.inherit_license = True
                else:
                    form.instance.inherit_license = False
            else:
                form.instance.inherit_license = workspace.license == license_code
        result = super().form_valid(form)
        billing = self.get_billing(workspace)
        self.object.post_create(self.request.user, billing)
        return result

    def can_create(self):
        return self.workspaces.exists() or self.request.user.has_perm("project.add")

    def post(self, request: AuthenticatedHttpRequest, *args, **kwargs):  # type: ignore[override]
        if not self.can_create():
            return redirect("create-project")
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs = super().get_context_data(**kwargs)
        kwargs["can_create"] = self.can_create()
        kwargs["import_form"] = self.get_form(ProjectImportForm)
        if self.has_billing:
            # ruff: ignore[import-outside-top-level]
            from weblate.billing.models import Billing

            kwargs["user_billings"] = Billing.objects.for_user(
                self.request.user
            ).exists()
        return kwargs

    def dispatch(self, request: AuthenticatedHttpRequest, *args, **kwargs):  # type: ignore[override]
        self.workspaces = request.user.workspaces_with_perm("workspace.add_project")
        if self.has_billing:
            # ruff: ignore[import-outside-top-level]
            from weblate.billing.models import Billing

            valid_billing_workspaces = Billing.objects.for_user_within_limits(
                request.user
            ).values("workspace")
            self.workspaces = self.workspaces.filter(
                Q(billing__isnull=True) | Q(pk__in=valid_billing_workspaces)
            )
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self) -> str:
        return f"{super().get_success_url()}#components"


class ImportProject(CreateProject):
    form_class = ProjectImportForm
    template_name = "trans/project_import.html"

    def setup(self, request: AuthenticatedHttpRequest, *args, **kwargs) -> None:  # type: ignore[override]
        if "import_project" in request.session and os.path.exists(
            request.session["import_project"]
        ):
            if "zipfile" in request.FILES:
                # Delete previous (stale) import data
                del request.session["import_project"]
                request.session.pop("import_workspace", None)
                self.projectbackup = None
            else:
                self.projectbackup = ProjectBackup(request.session["import_project"])
                # The backup is already validated at this point,
                # but we need to load the info.
                self.projectbackup.validate()
        else:
            request.session.pop("import_project", None)
            request.session.pop("import_workspace", None)
            self.projectbackup = None
        super().setup(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if "workspace" in form.fields:
            workspace = self.request.session.get("import_workspace")
            if workspace:
                form.fields["workspace"].initial = Workspace.objects.get(pk=workspace)
        return form

    def get_form_class(self):
        """Return the form class to use."""
        if self.projectbackup:
            return ProjectImportCreateForm
        return self.form_class

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.projectbackup:
            kwargs["projectbackup"] = self.projectbackup
        return kwargs

    def post(self, request: AuthenticatedHttpRequest, *args, **kwargs):  # type: ignore[override]
        if "zipfile" in request.FILES and self.projectbackup:
            # Delete previous (stale) import data
            os.unlink(self.projectbackup.filename)
            del self.request.session["import_project"]
            self.request.session.pop("import_workspace", None)
            self.projectbackup = None
        return super().post(request, *args, **kwargs)

    @transaction.atomic
    def form_valid(self, form):
        if isinstance(form, ProjectImportForm):
            # Save current zip to the import dir
            self.request.session["import_project"] = form.cleaned_data[
                "projectbackup"
            ].store_for_import()
            if form.cleaned_data["workspace"]:
                self.request.session["import_workspace"] = str(
                    form.cleaned_data["workspace"].pk
                )
            return redirect("create-project-import")
        workspace = form.cleaned_data["workspace"]
        billing = self.get_billing(workspace)
        task = import_project_backup.delay(
            project_name=form.cleaned_data["name"],
            project_slug=form.cleaned_data["slug"],
            user_id=self.request.user.id,
            filename=self.projectbackup.filename,
            billing_id=billing.pk if billing else None,
            workspace_id=str(workspace.pk) if workspace else None,
        )
        store_task_metadata(task.id, user_id=self.request.user.id)
        messages.success(
            self.request,
            gettext("Project backup import in progress"),
            f"task:{task.id}",
        )
        del self.request.session["import_project"]
        self.request.session.pop("import_workspace", None)
        return redirect("home")


@method_decorator(login_required, name="dispatch")
class CreateComponent(BaseCreateView):
    model = Component
    projects = None
    stage = None
    selected_project = None
    selected_category = None
    basic_fields = ("repo", "name", "slug", "vcs", "source_language")
    passthrough_fields = (
        "category",
        "is_glossary",
        "repository_redirect_proof",
        "source_component",
        *ComponentCreateForm.CREATE_INHERITABLE_SETTINGS,
        *(
            get_inherit_field_name(field)
            for field in ComponentCreateForm.CREATE_INHERITABLE_SETTINGS
        ),
    )
    initial_fields = (*basic_fields, "branch", *passthrough_fields)
    empty_form = False
    form_class: type[ComponentProjectForm] = ComponentInitCreateForm
    origin = "vcs"
    object: Component
    duplicate_existing_component: int | None = None
    integration_import_vcs = ""

    def get_form_class(self):
        """Return the form class to use."""
        if self.stage == "create":
            return ComponentCreateForm
        if self.stage == "discover":
            return ComponentDiscoverForm
        return self.form_class

    def get_form_kwargs(self):
        result = super().get_form_kwargs()
        if self.request.method != "POST":
            if self.initial:
                # When going from other form (for example ZIP import)
                result.pop("data", None)
                result.pop("files", None)
            if self.has_all_fields() and not self.empty_form:
                if SESSION_CREATE_KEY in self.request.session:
                    result["data"] = self.request.session[SESSION_CREATE_KEY]
                else:
                    result["data"] = self.request.GET
        return result

    def get_success_url(self):
        return reverse("show_progress", kwargs={"path": self.object.get_url_path()})

    def warn_outdated(self, form) -> None:
        linked = form.instance.linked_component
        if linked:
            perform_update.delay("Component", linked.pk, auto=True)
            if linked.repo_needs_merge():
                messages.warning(
                    self.request,
                    gettext(
                        "The repository is outdated, you might not get "
                        "expected results until you update it."
                    ),
                )

    def detect_license(self, form) -> None:
        """Automatic license detection based on licensee."""
        detected_license = detect_license(Path(form.instance.full_path))

        if detected_license and detected_license in LICENSE_URLS:
            self.initial["license"] = detected_license
            self.initial["detected_license"] = detected_license
            messages.info(
                self.request,
                gettext("Detected license as %s, please check whether it is correct.")
                % detected_license,
            )

    @transaction.atomic
    def form_valid(self, form):
        if self.stage == "create":
            lock = (
                nullcontext()
                if form.instance.is_repo_link
                else form.instance.repository.lock
            )
            with lock:
                for field in INHERITABLE_COMPONENT_FLAGS:
                    setattr(form.instance, field, True)
                for field in ("license", "new_lang", "language_code_style"):
                    if form.disables_inheritance_for_explicit_setting(field):
                        setattr(form.instance, f"inherit_{field}", False)
                form.instance.manage_units = (
                    bool(form.instance.template) or form.instance.file_format == "tbx"
                )
                if source_component := form.cleaned_data.get("source_component"):
                    create_fields = set(ComponentCreateForm.CREATE_INHERITABLE_SETTINGS)
                    fields_to_duplicate = [
                        "merge_style",
                        *(
                            field
                            for field in INHERITABLE_COMPONENT_SETTINGS
                            if field not in create_fields
                        ),
                        *(
                            get_inherit_field_name(field)
                            for field in INHERITABLE_COMPONENT_SETTINGS
                            if field not in create_fields
                        ),
                    ]
                    for field in fields_to_duplicate:
                        setattr(form.instance, field, getattr(source_component, field))

                result = super().form_valid(form)
                self.object.post_create(self.request.user, origin=self.origin)
                return result
        if self.stage == "discover":
            # Move to create
            self.update_initial(form.cleaned_data)
            self.stage = "create"
            self.request.method = "GET"
            self.warn_outdated(form)
            self.detect_license(form)
            return self.get(self.request)
        # Move to discover
        self.stage = "discover"
        self.request.method = "GET"
        self.update_initial(form.cleaned_data)
        self.warn_outdated(form)
        return self.get(self.request)

    def get_form(self, form_class=None, empty=False):
        self.empty_form = empty
        form = super().get_form(form_class)
        self.patch_integration_vcs_choice(form)
        if "project" in form.fields:
            project_field = form.fields["project"]
            category_field = form.fields["category"]
            project_field.queryset = self.projects
            category_field.queryset = Category.objects.filter(project__in=self.projects)
            project_field.empty_label = None
            if self.selected_project:
                project_field.initial = self.selected_project
                with suppress(IndexError):
                    form.fields["source_language"].initial = Component.objects.filter(
                        project=self.selected_project
                    )[0].source_language_id
                if self.selected_category:
                    category_field.initial = self.selected_category
        self.empty_form = False
        if "source_component" in form.fields and self.duplicate_existing_component:
            components = Component.objects.filter_access(self.request.user).filter(
                pk=self.duplicate_existing_component
            )
            if components.exists():
                form.fields["source_component"].queryset = components
                form.initial["source_component"] = self.duplicate_existing_component
        return form

    def patch_integration_vcs_choice(self, form) -> None:
        vcs_field = form.fields.get("vcs")
        if vcs_field is None:
            return

        integration_choices = [
            choice
            for choice in VCS_REGISTRY.get_choices(exclude={"local"})
            if not VCS_REGISTRY[choice[0]].manual_component_creation
        ]
        vcs_field.choices = [
            choice
            for choice in vcs_field.choices
            if VCS_REGISTRY[choice[0]].manual_component_creation
        ]
        if (
            self.integration_import_vcs
            and self.integration_import_vcs == self.initial.get("vcs")
        ):
            vcs_backend = VCS_REGISTRY.get(self.integration_import_vcs)
            vcs_field.choices = [
                *vcs_field.choices,
                *(
                    choice
                    for choice in integration_choices
                    if choice[0] == self.integration_import_vcs
                ),
            ]
            if vcs_backend is not None:
                for field in vcs_backend.component_lock_fields:
                    if field in form.fields:
                        form.fields[field].disabled = True
                for field in vcs_backend.component_clear_fields:
                    if field in form.fields:
                        form.initial[field] = ""
                        form.fields[field].initial = ""

    def get_context_data(self, **kwargs):
        kwargs = super().get_context_data(**kwargs)
        kwargs["projects"] = self.projects
        kwargs["stage"] = self.stage
        return kwargs

    def fetch_params(self, request: AuthenticatedHttpRequest) -> None:
        try:
            self.selected_project = int(
                request.POST.get("project", request.GET.get("project", ""))
            )
        except ValueError:
            self.selected_project = None
        try:
            self.selected_category = int(
                request.POST.get("category", request.GET.get("category", ""))
            )
        except ValueError:
            self.selected_category = None
        self.projects = get_creatable_projects(request)
        self.initial = {}
        session_data = {}
        if SESSION_CREATE_KEY in request.GET and SESSION_CREATE_KEY in request.session:
            session_data = request.session[SESSION_CREATE_KEY]
        self.integration_import_vcs = session_data.get(INTEGRATION_IMPORT_VCS_KEY, "")
        for field in self.initial_fields:
            if field in session_data:
                self.initial[field] = session_data[field]
            elif field in request.GET:
                self.initial[field] = request.GET[field]

        try:
            self.duplicate_existing_component = int(
                request.POST.get(
                    "source_component",
                    request.GET.get(
                        "source_component", session_data.get("source_component", "")
                    ),
                )
            )
        except (ValueError, TypeError):
            self.duplicate_existing_component = None

    def update_initial(self, cleaned_data: dict) -> None:
        self.initial = {**self.initial, **cleaned_data}

    def has_all_fields(self):
        session_data = {}
        if (
            SESSION_CREATE_KEY in self.request.GET
            and SESSION_CREATE_KEY in self.request.session
        ):
            session_data = self.request.session[SESSION_CREATE_KEY]
        return self.stage == "init" and all(
            field in session_data or field in self.request.GET
            for field in self.basic_fields
        )

    def dispatch(self, request: AuthenticatedHttpRequest, *args, **kwargs):  # type: ignore[override]
        if "new_base" in request.POST:
            self.stage = "create"
        elif "discovery" in request.POST:
            self.stage = "discover"
        else:
            self.stage = "init"

        self.fetch_params(request)

        # Proceed to post if all params are present
        if self.has_all_fields():
            return self.post(request, *args, **kwargs)

        return super().dispatch(request, *args, **kwargs)


class CreateFromZip(CreateComponent):
    form_class = ComponentZipCreateForm
    origin = "zip"

    @transaction.atomic
    def form_valid(self, form):
        if self.stage != "init":
            return super().form_valid(form)

        uploaded = form.cleaned_data["zipfile"]
        suffix = os.path.splitext(getattr(uploaded, "name", "") or "")[1].lower()
        if suffix in KIT_TABLE_SUFFIXES and form.cleaned_data.get("is_glossary"):
            # An explicit glossary intent on a table takes the analysis path:
            # the file stays local in a temporary draft until the operator has
            # seen a locally validated preview.
            return self._start_glossary_draft(form, uploaded)

        try:
            _fake, kit_info = create_component_from_kit(form.cleaned_data, uploaded)
        except ValidationError as error:
            form.add_error("zipfile", error)
            return self.form_invalid(form)
        except (BadZipfile, OSError, RepositoryError):
            form.add_error("zipfile", gettext("Could not parse uploaded ZIP file."))
            return self.form_invalid(form)

        # ZIP moves to the discover phase; a converted kit goes straight to
        # create: its layout is already decided, discovery would only offer
        # guesses that drop the template.
        self.stage = "discover" if kit_info is None else "create"
        self.update_initial(form.cleaned_data)
        self.initial["vcs"] = "local"
        self.initial["repo"] = "local:"
        self.initial["branch"] = "main"
        self.initial.pop("zipfile")
        if kit_info is not None:
            # The kit itself decided the layout; prefill it so the user only
            # confirms instead of typing masks by hand.
            self.initial["file_format"] = kit_info["file_format"]
            self.initial["filemask"] = kit_info["filemask"]
            self.initial["template"] = kit_info["template"]
            self.initial["new_base"] = kit_info["template"]
            with suppress(Language.DoesNotExist):
                self.initial["source_language"] = Language.objects.get(
                    code=kit_info["source_lang"]
                )
            messages.info(
                self.request,
                gettext(
                    "Loc-kit converted: %(units)d strings, languages: "
                    "%(languages)s, source language %(source)s."
                )
                % {
                    "units": kit_info["units"],
                    "languages": ", ".join(kit_info["languages"]),
                    "source": kit_info["source_lang"],
                },
            )
            if kit_info["sourceless"]:
                messages.warning(
                    self.request,
                    gettext(
                        "%d strings were imported without a source string; they "
                        "show up as untranslated in the source language."
                    )
                    % kit_info["sourceless"],
                )
            for note in kit_info["notes"]:
                messages.info(self.request, note)
            warnings = kit_info["warnings"]
            for warning in warnings[:5]:
                messages.warning(self.request, warning)
            if len(warnings) > 5:
                messages.warning(
                    self.request,
                    gettext("… and %d more warnings.") % (len(warnings) - 5),
                )
        self.request.method = "GET"
        return self.get(self.request)

    def _start_glossary_draft(self, form, uploaded):
        """Store the upload as a temporary draft and go to sheet selection."""
        # ruff: ignore[import-outside-top-level]
        from loc_kit_ingest.reader import ReaderError, read_sheets

        filename = os.path.basename(getattr(uploaded, "name", "") or "")
        uploaded.seek(0)
        payload = uploaded.read()

        # Read locally first: an unreadable file must fail here, before a
        # draft exists and long before anything could leave the host.
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / filename
            local.write_bytes(payload)
            try:
                sheets = read_sheets(local)
            except ReaderError as error:
                form.add_error(
                    "zipfile", gettext("Could not read the table: %s") % error
                )
                return self.form_invalid(form)
        if not sheets:
            form.add_error("zipfile", gettext("The table holds no worksheets."))
            return self.form_invalid(form)

        if not self.request.session.session_key:
            self.request.session.create()
        session_key = self.request.session.session_key or ""

        draft = LocKitImportDraft(
            owner=self.request.user,
            session_key=session_key,
            project=form.cleaned_data["project"],
            category=form.cleaned_data.get("category"),
            slug=form.cleaned_data["slug"],
            name=form.cleaned_data["name"],
            source_filename=filename,
        )
        # Storage is not transactional: the file lands on disk before the row
        # is inserted, so a failure would strand it where the row-driven
        # cleanup task can never find it. Undo the write by hand. The guard
        # spans the mapping too: that parses the sheet, renders TBX and parses
        # it back, so a MemoryError, a tempdir OSError or a worker killed
        # mid-parse would roll the row back and leave the file behind.
        draft.uploaded.save(filename, ContentFile(payload), save=False)
        try:
            return _insert_draft_and_map(self.request, draft, sheets)
        except Exception:
            draft.delete_storage()
            raise


def _insert_draft_and_map(request: AuthenticatedHttpRequest, draft, sheets):
    """Insert the draft row and, for a single sheet, map it locally."""
    draft.save()
    if len(sheets) > 1:
        # Several worksheets: the operator has to choose one, and that
        # POST runs the full analysis path.
        return redirect("loc-kit-sheet-select", token=draft.token)
    # A CSV/TSV always has exactly one sheet; the selection screen is
    # noise. Only the deterministic step may run here: this POST is
    # atomic, a provider call inside it would hold a transaction open
    # for the whole network timeout.
    sheet_name, rows = next(iter(sheets.items()))
    draft.sheet = sheet_name
    draft.state = LocKitImportDraft.State.SHEET_SELECTED
    draft.save(update_fields=["sheet", "state"])
    infer_error = _infer_draft_profile(draft, rows)
    if infer_error is None:
        return redirect("loc-kit-glossary-preview", token=draft.token)
    messages.info(request, infer_error)
    return redirect("loc-kit-sheet-select", token=draft.token)


class CreateFromDoc(CreateComponent):
    form_class = ComponentDocCreateForm
    origin = "document"

    @transaction.atomic
    def form_valid(self, form):
        if self.stage != "init":
            return super().form_valid(form)

        fake = create_component_from_doc(
            form.cleaned_data,
            form.cleaned_data.pop("docfile"),
            form.cleaned_data.pop("target_language", None),
        )
        # Move to discover phase
        self.stage = "discover"
        self.update_initial(form.cleaned_data)
        self.initial["vcs"] = "local"
        self.initial["repo"] = "local:"
        self.initial["branch"] = "main"
        self.initial["template"] = fake.template
        self.initial["filemask"] = fake.filemask

        self.request.method = "GET"
        return self.get(self.request)


class CreateComponentSelection(CreateComponent):
    template_name = "trans/component_create.html"

    components: ComponentQuerySet
    origin: str | None = None
    duplicate_existing_component: int | None = None

    @cached_property
    def branch_data(self):
        result = {}
        components = list(self.components)
        repos = {component.repo for component in components}
        existing_branches: dict[str, set[str]] = {repo: set() for repo in repos}
        remote_branches: dict[str, list[str]] = {}

        for repo, branch in Component.objects.filter(repo__in=repos).values_list(
            "repo", "branch"
        ):
            existing_branches[repo].add(branch)

        for component in components:
            repo = component.repo
            if repo not in remote_branches:
                try:
                    remote_branches[repo] = component.repository.list_remote_branches()
                except RepositoryError:
                    # Ignore error, use no branches
                    remote_branches[repo] = []

            branches = [
                branch
                for branch in remote_branches[repo]
                if branch != component.branch and branch not in existing_branches[repo]
            ]
            if branches:
                result[component.pk] = branches
        return result

    def fetch_params(self, request: AuthenticatedHttpRequest) -> None:
        super().fetch_params(request)
        self.components = (
            Component.objects.filter_access(request.user)
            .with_repo()
            .prefetch()
            .filter(project__in=self.projects)
            .order_project()
        )
        if self.selected_project:
            self.components = self.components.filter(project__pk=self.selected_project)
        self.origin = request.POST.get("origin")

        try:
            self.duplicate_existing_component = int(request.GET.get("component"))
        except (ValueError, TypeError):
            self.duplicate_existing_component = None
        self.initial = {}
        if self.duplicate_existing_component:
            source_component = self.components.filter(
                pk=self.duplicate_existing_component
            ).first()
            if source_component is not None:
                self.initial |= {
                    "component": source_component,
                    "is_glossary": source_component.is_glossary,
                }

    def get_context_data(self, **kwargs):
        kwargs = super().get_context_data(**kwargs)
        kwargs["components"] = self.components
        kwargs["selected_project"] = self.selected_project
        kwargs["existing_form"] = self.get_form(ComponentSelectForm, empty=True)
        kwargs["branch_form"] = self.get_form(ComponentBranchForm, empty=True)
        kwargs["branch_data"] = json.dumps(self.branch_data)
        kwargs["full_form"] = self.get_form(ComponentInitCreateForm, empty=True)
        if "local" in VCS_REGISTRY:
            kwargs["zip_form"] = self.get_form(ComponentZipCreateForm, empty=True)
            kwargs["scratch_form"] = self.get_form(
                ComponentScratchCreateForm, empty=True
            )
            kwargs["doc_form"] = self.get_form(ComponentDocCreateForm, empty=True)
        if self.origin == "branch":
            kwargs["branch_form"] = kwargs["form"]
        elif self.origin == "scratch":
            kwargs["scratch_form"] = kwargs["form"]
        else:
            kwargs["existing_form"] = kwargs["form"]
        workspace_ids = list(
            self.projects.filter(workspace__isnull=False)
            .values_list("workspace_id", flat=True)
            .distinct()
        )
        configured_hosts = set(get_github_app_configurations())
        installations = GitHubInstallation.objects.filter(
            enabled=True,
            hostname__in=configured_hosts,
            workspace_id__in=workspace_ids,
        ).select_related("workspace")
        selected_project_obj = None
        if self.selected_project:
            selected_project_obj = self.projects.filter(
                pk=self.selected_project
            ).first()
            if selected_project_obj is not None and selected_project_obj.workspace_id:
                installations = installations.filter(
                    workspace_id=selected_project_obj.workspace_id
                )
            else:
                installations = installations.none()
        installations = installations.order_by(
            "workspace__name", "target_login", "hostname"
        )
        kwargs["github_app_available"] = github_app_is_configured() or (
            installations.exists()
        )
        repositories: list[dict] = []
        for installation in installations:
            for repo in installation.repositories:
                if repo.get("archived", False):
                    continue
                entry = dict(repo)
                entry["account_name"] = installation.target_login
                entry["workspace_id"] = str(installation.workspace_id)
                entry["workspace_name"] = installation.workspace.name
                entry["import_url"] = get_github_repository_import_url(
                    entry,
                    installation_id=installation.pk,
                    project_id=(
                        selected_project_obj.pk
                        if selected_project_obj is not None
                        else None
                    ),
                    category_id=self.selected_category,
                )
                repositories.append(entry)
        kwargs["github_app_repositories"] = repositories
        install_workspace_id = None
        installation_workspaces = github_app_installation_workspaces(self.request.user)
        if (
            github_app_is_configured()
            and selected_project_obj is not None
            and selected_project_obj.workspace_id
            and installation_workspaces.filter(
                pk=selected_project_obj.workspace_id
            ).exists()
        ):
            install_workspace_id = selected_project_obj.workspace_id
        elif (
            github_app_is_configured()
            and selected_project_obj is None
            and len(workspace_ids) == 1
            and installation_workspaces.filter(pk=workspace_ids[0]).exists()
        ):
            install_workspace_id = workspace_ids[0]

        if install_workspace_id is not None:
            kwargs["github_app_install_url"] = (
                reverse("github-app-install")
                + "?"
                + urlencode(
                    {
                        "next": f"{self.request.get_full_path()}#github",
                        "workspace": install_workspace_id,
                    }
                )
            )
        return kwargs

    def get_form(self, form_class=None, empty=False):
        form = super().get_form(form_class, empty=empty)
        if isinstance(form, ComponentBranchForm):
            form.fields["component"].queryset = Component.objects.filter(
                pk__in=self.branch_data.keys()
            ).order_project()
            form.branch_data = self.branch_data
        elif isinstance(form, ComponentSelectForm):
            if self.duplicate_existing_component:
                self.components |= Component.objects.filter_access(
                    self.request.user
                ).filter(pk=self.duplicate_existing_component)
            form.fields["component"].queryset = self.components
        return form

    def get_form_class(self):
        if self.origin == "branch":
            return ComponentBranchForm
        if self.origin == "scratch":
            return ComponentScratchCreateForm
        return ComponentSelectForm

    def redirect_create(self, **kwargs):
        vcs = kwargs.get("vcs")
        if vcs:
            vcs_backend = VCS_REGISTRY.get(vcs)
            if vcs_backend is not None and not vcs_backend.manual_component_creation:
                kwargs.setdefault(INTEGRATION_IMPORT_VCS_KEY, vcs)

        # Store params in session
        self.request.session[SESSION_CREATE_KEY] = kwargs

        return redirect(
            f"{reverse('create-component-vcs')}?{urlencode({SESSION_CREATE_KEY: 1})}"
        )

    @transaction.atomic
    def form_valid(self, form):
        if self.origin == "scratch":
            project = form.cleaned_data["project"]
            component = project.scratch_create_component(**form.cleaned_data)
            component.post_create(self.request.user, origin="scratch")
            return redirect(
                reverse("show_progress", kwargs={"path": component.get_url_path()})
            )
        component = form.cleaned_data["component"]
        if self.origin == "existing":
            kwargs = {
                "repo": component.repo or component.get_repo_link_url(),
                "project": component.project.pk,
                "category": component.category.pk if component.category else "",
                "name": form.cleaned_data["name"],
                "slug": form.cleaned_data["slug"],
                "is_glossary": form.cleaned_data["is_glossary"],
                "vcs": component.vcs,
                "source_language": component.source_language.pk,
                "source_component": component.pk,
            }
            for field in ComponentCreateForm.CREATE_INHERITABLE_SETTINGS:
                kwargs[field] = getattr(component, field)
                inherit_field = get_inherit_field_name(field)
                kwargs[inherit_field] = getattr(component, inherit_field)
            return self.redirect_create(**kwargs)
        if self.origin == "branch":
            form.instance.save()
            form.instance.post_create(self.request.user, origin="branch")
            return redirect(
                reverse("show_progress", kwargs={"path": form.instance.get_url_path()})
            )

        return redirect("create-component")

    def post(self, request: AuthenticatedHttpRequest, *args, **kwargs):  # type: ignore[override]
        if self.origin == "vcs":
            kwargs = {}
            if self.selected_project:
                kwargs["project"] = self.selected_project
            return self.redirect_create(**kwargs)
        return super().post(request, *args, **kwargs)


# --------------------------------------------------------------------------- #
# Loc-kit glossary intake: sheet selection, preview, confirmation
# --------------------------------------------------------------------------- #


class LocKitDraftMixin(View):
    """
    Resolve a temporary glossary draft, or 404.

    Every draft endpoint re-checks the same three things: the token belongs to
    this user, it was created in this session, and the user still holds the
    project-level component-creation permission. A revoked permission makes
    the draft unavailable exactly like an expired one - it must not expose
    staged files, trigger analysis, or create a component.
    """

    def get_draft(self, token: str) -> LocKitImportDraft:
        draft = LocKitImportDraft.get_active(
            token=token,
            owner=self.request.user,
            session_key=self.request.session.session_key or "",
        )
        if draft is None:
            raise Http404
        # An update draft is bound to an existing glossary component and
        # is gated by upload access on that component, not by the
        # component-creation wizard. translation.add is deliberately not
        # required here: without it an operator can still add data to the
        # languages that already exist.
        if draft.target_component_id is not None:
            component = draft.target_component
            if (
                not component.is_glossary
                or component.locked
                or not self.request.user.has_perm("upload.perform", component)
            ):
                raise Http404
            return draft
        # Exactly the wizard's gate, billing included. A permission revoked
        # (or billing lapsed) mid-flight makes the draft unavailable.
        if (
            not get_creatable_projects(self.request)
            .filter(pk=draft.project_id)
            .exists()
        ):
            raise Http404
        return draft

    def read_draft_sheets(self, draft: LocKitImportDraft) -> dict:
        # ruff: ignore[import-outside-top-level]
        from loc_kit_ingest.reader import ReaderError, read_sheets

        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / draft.source_filename
            with draft.uploaded.open("rb") as handle:
                local.write_bytes(handle.read())
            try:
                return read_sheets(local)
            except ReaderError as error:
                raise Http404 from error


@method_decorator(login_required, name="dispatch")
class LocKitSheetSelectView(LocKitDraftMixin, TemplateView):
    """Pick the single worksheet that becomes a glossary component."""

    template_name = "trans/loc_kit_sheet_select.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        draft = self.get_draft(self.kwargs["token"])
        sheets = self.read_draft_sheets(draft)
        choices = [
            (
                name,
                gettext("%(sheet)s (%(rows)d rows, %(columns)d columns)")
                % {
                    "sheet": name,
                    "rows": len(rows),
                    "columns": max((len(row) for row in rows), default=0),
                },
            )
            for name, rows in sheets.items()
        ]
        context["draft"] = draft
        context["form"] = kwargs.get("form") or LocKitSheetSelectForm(
            sheet_choices=choices,
            initial={"sheet": next(iter(sheets), None)},
        )
        context["analysis_enabled"] = settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED
        return context

    def post(self, request: AuthenticatedHttpRequest, **kwargs):
        draft = self.get_draft(kwargs["token"])
        sheets = self.read_draft_sheets(draft)
        form = LocKitSheetSelectForm(
            request.POST, sheet_choices=[(name, name) for name in sheets]
        )
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, **kwargs))

        # A draft that already holds a validated preview must not be pushed
        # back through the pipeline. Re-selecting the same sheet is free for
        # the caller but re-runs the full parse, TBX render and parse-back,
        # so without this an attacker replays one stored upload for the
        # draft's whole lifetime at no cost.
        if (
            draft.state == LocKitImportDraft.State.PREVIEW_READY
            and draft.sheet == form.cleaned_data["sheet"]
        ):
            return redirect("loc-kit-glossary-preview", token=draft.token)

        # Changing the sheet invalidates any previously accepted mapping:
        # leaving it behind would show the old sheet's terms next to the new
        # sheet's name and keep the create button live.
        draft.sheet = form.cleaned_data["sheet"]
        draft.state = LocKitImportDraft.State.SHEET_SELECTED
        draft.profile_json = ""
        draft.preview_json = ""
        draft.save(update_fields=["sheet", "state", "profile_json", "preview_json"])

        error = _analyze_draft_sheet(request, draft, sheets[draft.sheet])
        if error is not None:
            messages.error(request, error)
        return redirect("loc-kit-glossary-preview", token=draft.token)


def _infer_draft_profile(
    draft: LocKitImportDraft, rows: list, layout: str = "auto"
) -> str | None:
    """Deterministic local mapping. Returns None on success, else the reason."""
    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.infer import InferenceError, infer_glossary_profile

    try:
        document, notes = infer_glossary_profile(
            draft.sheet, rows, component=draft.slug, layout=layout
        )
    except InferenceError as error:
        return str(error)
    return _store_validated_profile(draft, document, rows, extra_warnings=notes)


def _analyze_draft_sheet(
    request: AuthenticatedHttpRequest, draft: LocKitImportDraft, rows: list
) -> str | None:
    """
    Ask OpenRouter for a candidate profile and validate it locally.

    Returns None on success, or a user-facing message explaining why the
    operator has to supply a profile by hand. Never raises for a provider
    problem: an unavailable analyzer is a manual-profile outcome, not an
    error page.
    """
    # Deterministic first: local, free, offline. The analyzer is a fallback
    # for layouts the header-driven inference refuses.
    infer_reason = _infer_draft_profile(draft, rows)
    if infer_reason is None:
        return None

    if not settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED:
        return (
            gettext(
                "Automatic mapping did not recognize this sheet (%s) "
                "and analysis is disabled. Upload a profile to continue."
            )
            % infer_reason
        )
    # Spend an attempt only for a call that can actually reach the provider.
    # Selecting a worksheet is free: a multi-sheet workbook needs several
    # POSTs before the operator even sees the sheet they want.
    if not check_rate_limit("loc_kit_analysis", request):
        return gettext(
            "Too many automatic analysis requests. "
            "Upload a profile to continue, or retry later."
        )
    try:
        sample = build_glossary_structure_sample(
            rows, draft.sheet, settings.LOC_KIT_PROFILE_SAMPLE_MAX_BYTES
        )
    except SampleTooLargeError:
        return gettext(
            "This worksheet is too large to analyze automatically. "
            "Upload a profile to continue."
        )
    try:
        envelope = request_profile_proposal(sample)
        document = profile_document_from_envelope(envelope)
    except (ProfileProposalError, GlossaryProfileError) as error:
        return str(error)

    return _store_validated_profile(draft, document, rows)


def _store_validated_profile(
    draft: LocKitImportDraft,
    document: dict,
    rows: list,
    extra_warnings: Sequence[str] = (),
) -> str | None:
    """Validate a candidate locally and persist the preview, or explain why not."""
    try:
        preview = validate_glossary_profile(
            profile_document=document,
            rows=rows,
            sheet_name=draft.sheet,
            component_name=draft.slug,
        )
    except GlossaryProfileError as error:
        detail = " ".join(error.details)
        return f"{error.message} {detail}".strip()

    draft.profile_json = preview.profile_json
    draft.preview_json = json.dumps(
        {
            "source_language": preview.source_language,
            "target_languages": list(preview.target_languages),
            "term_count": preview.term_count,
            "note_count": preview.note_count,
            "warnings": cap_preview_warnings([*extra_warnings, *preview.warnings]),
            "terms": [
                {
                    "section": term.section,
                    "source": term.source,
                    "targets": term.targets,
                    "source_explanation": term.source_explanation,
                    "target_explanations": term.target_explanations,
                    "source_flags": list(term.source_flags),
                }
                for term in preview.terms
            ],
        },
        ensure_ascii=False,
    )
    draft.state = LocKitImportDraft.State.PREVIEW_READY
    draft.save(update_fields=["profile_json", "preview_json", "state"])
    return None


def _draft_layout(draft: LocKitImportDraft) -> str:
    """Which table shape the stored profile describes: ``flat`` or ``pairs``."""
    if not draft.profile_json:
        return "flat"
    try:
        document = json.loads(draft.profile_json)
        regions = document["components"][0]["grammar"]["regions"]
        strides = {region.get("record_stride", 1) for region in regions}
    except (ValueError, LookupError, TypeError):
        # An operator-uploaded profile may use another grammar entirely.
        return "flat"
    return "pairs" if strides == {2} else "flat"


@method_decorator(login_required, name="dispatch")
class LocKitGlossaryPreviewView(LocKitDraftMixin, TemplateView):
    """Show the validated preview, accept a correction, cancel, or confirm."""

    template_name = "trans/loc_kit_glossary_preview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        draft = self.get_draft(self.kwargs["token"])
        context["draft"] = draft
        context["preview"] = (
            json.loads(draft.preview_json) if draft.preview_json else None
        )
        context["profile_form"] = kwargs.get("profile_form") or (
            LocKitProfileCorrectionForm()
        )
        context["layout"] = _draft_layout(draft)
        return context

    def post(self, request: AuthenticatedHttpRequest, **kwargs):
        draft = self.get_draft(kwargs["token"])
        action = request.POST.get("action")

        if action == "cancel":
            draft.delete_storage()
            draft.delete()
            messages.info(request, gettext("Glossary import cancelled."))
            return redirect("create-component-zip")

        if action == "download-profile":
            response = HttpResponse(
                draft.profile_json, content_type="application/json; charset=utf-8"
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{draft.slug}.loc-ingest.json"'
            )
            return response

        if action == "upload-profile":
            form = LocKitProfileCorrectionForm(request.POST, request.FILES)
            if not form.is_valid():
                return self.render_to_response(
                    self.get_context_data(profile_form=form, **kwargs)
                )
            sheets = self.read_draft_sheets(draft)
            if draft.sheet not in sheets:
                raise Http404
            # A correction is revalidated against the same worksheet, locally.
            # The analyzer is never called again.
            error = _store_validated_profile(
                draft, form.cleaned_data["profile"], sheets[draft.sheet]
            )
            if error is not None:
                messages.error(request, error)
            else:
                messages.info(request, gettext("Profile accepted."))
            return redirect("loc-kit-glossary-preview", token=draft.token)

        if action == "relayout":
            # The operator saw the preview and disagrees with the detected
            # shape. Re-running the local inference costs exactly what an
            # uploaded correction costs, and the same gate decides.
            layout = request.POST.get("layout", "")
            if layout not in {"flat", "pairs"}:
                raise Http404
            sheets = self.read_draft_sheets(draft)
            if draft.sheet not in sheets:
                raise Http404
            error = _infer_draft_profile(draft, sheets[draft.sheet], layout)
            if error is not None:
                messages.error(request, error)
            return redirect("loc-kit-glossary-preview", token=draft.token)

        if action == "apply":
            return self._apply_update(request, draft)

        if action == "confirm":
            if draft.state != LocKitImportDraft.State.PREVIEW_READY:
                raise Http404
            return redirect("loc-kit-glossary-confirm", token=draft.token)

        raise Http404

    def _apply_update(
        self, request: AuthenticatedHttpRequest, draft: LocKitImportDraft
    ):
        """Append brand-new terms to the draft's existing glossary."""
        component = draft.target_component
        if component is None or draft.state != LocKitImportDraft.State.PREVIEW_READY:
            raise Http404
        sheets = self.read_draft_sheets(draft)
        if draft.sheet not in sheets:
            raise Http404
        # The stored profile is re-validated against the same worksheet,
        # exactly like the creation confirm: the preview JSON is display
        # data, never the application input.
        try:
            preview = validate_glossary_profile(
                profile_document=json.loads(draft.profile_json),
                rows=sheets[draft.sheet],
                sheet_name=draft.sheet,
                component_name=component.slug,
            )
        except GlossaryProfileError as error:
            messages.error(
                request, f"{error.message} {' '.join(error.details)}".strip()
            )
            return redirect("loc-kit-glossary-preview", token=draft.token)
        if preview.source_language != component.source_language.code:
            messages.error(
                request,
                gettext(
                    "The table's source language does not match the glossary's "
                    "source language."
                ),
            )
            return redirect("loc-kit-glossary-preview", token=draft.token)
        try:
            result = append_glossary_terms(request, component, preview)
        except GlossaryAppendCollisionError as error:
            messages.error(request, str(error))
            for source, existing_context, incoming_context in error.conflicts:
                messages.error(
                    request,
                    gettext(
                        "“%(source)s” is already a glossary term under "
                        "“%(existing)s”, but the table places it under "
                        "“%(incoming)s”."
                    )
                    % {
                        "source": source,
                        "existing": existing_context,
                        "incoming": incoming_context,
                    },
                )
            return redirect("loc-kit-glossary-preview", token=draft.token)
        except WeblateLockTimeoutError:
            messages.error(
                request,
                gettext(
                    "The glossary is busy right now; nothing was changed. "
                    "Please retry in a moment."
                ),
            )
            return redirect("loc-kit-glossary-preview", token=draft.token)
        if self._report_append_outcome(request, result):
            draft.delete_storage()
            draft.delete()
            return redirect(component)
        # Nothing applicable: the outcome is shown as information and the
        # draft stays so the operator can review the table.
        return redirect("loc-kit-glossary-preview", token=draft.token)

    @staticmethod
    def _report_append_outcome(
        request: AuthenticatedHttpRequest, result: GlossaryAppendResult
    ) -> bool:
        """Message the per-language outcome; True when terms were added."""
        if result.added_terms:
            messages.success(
                request,
                ngettext(
                    "%d new glossary term added.",
                    "%d new glossary terms added.",
                    result.added_terms,
                )
                % result.added_terms,
            )
        else:
            messages.info(
                request,
                gettext(
                    "No new glossary terms were added; the draft is kept so "
                    "you can review the table."
                ),
            )
        for code, language in result.languages.items():
            parts = [
                ngettext("%d added", "%d added", language.added) % language.added
                if language.added
                else "",
                ngettext("%d existing", "%d existing", language.existing)
                % language.existing
                if language.existing
                else "",
                ngettext("%d blank", "%d blank", language.blank) % language.blank
                if language.blank
                else "",
                gettext("no column in the table") if language.absent else "",
                language.unavailable,
            ]
            detail = ", ".join(part for part in parts if part)
            if detail:
                messages.info(request, f"{code}: {detail}")
        return bool(result.added_terms)


# Fields the conversion decides. The operator may not change them at the final
# form: they are re-applied server-side from the draft on every POST, so a
# tampered form field cannot repoint the component at another format, mask,
# template, source language, or drop the glossary flag.
LOC_KIT_LOCKED_FIELDS = (
    "file_format",
    "filemask",
    "template",
    "new_base",
    "vcs",
    "repo",
    "branch",
    "source_language",
    "is_glossary",
)


@method_decorator(login_required, name="dispatch")
class LocKitGlossaryConfirmView(LocKitDraftMixin, CreateComponent):
    """
    Final component form for a validated glossary draft.

    Re-enters the ordinary creation flow rather than creating a Component
    directly, so every normal component setting the final form legitimately
    exposes still applies. Only the conversion-derived fields are frozen.
    """

    # mypy: the base declares type[ComponentProjectForm]; the final glossary
    # form is the ordinary component form, exactly as stage="create" implies.
    form_class = ComponentCreateForm  # type: ignore[assignment]
    origin = "zip"

    # The base class derives `stage` from raw POST fields, which the client
    # controls: omitting "new_base" would drive this request down the
    # non-creating branch while form_valid still wrote a repository and
    # destroyed the draft. This endpoint is only ever the final create step.
    @property
    def stage(self) -> str:
        return "create"

    @stage.setter
    def stage(self, value: str) -> None:
        """Ignore the base class's POST-derived stage."""

    @cached_property
    def draft(self) -> LocKitImportDraft:
        draft = self.get_draft(self.kwargs["token"])
        # get_draft() deliberately relaxes its gate for update drafts to
        # upload.perform on the existing glossary, skipping the wizard's
        # creatable-projects check entirely (see the docstring above). This
        # view creates a brand-new component, so an update draft must never
        # reach it: that would let upload.perform on one glossary create an
        # unrelated component the actor cannot otherwise create.
        if draft.target_component_id is not None:
            raise Http404
        return draft

    def locked_values(self) -> dict:
        draft = self.draft
        values = {
            "file_format": "tbx",
            "filemask": "tbx/*.tbx",
            "template": "",
            "new_base": "",
            "vcs": "local",
            "repo": "local:",
            "branch": "main",
            "is_glossary": True,
        }
        preview = json.loads(draft.preview_json)
        # An unresolvable code must not silently drop source_language out of
        # the lock: get_form_kwargs skips absent keys, so the client's posted
        # value would survive for the one field that decides which components
        # this glossary applies to.
        try:
            values["source_language"] = Language.objects.get(
                code=preview["source_language"]
            )
        except Language.DoesNotExist as error:
            raise Http404 from error
        return values

    def get_initial(self):
        draft = self.draft
        if draft.state != LocKitImportDraft.State.PREVIEW_READY:
            raise Http404
        initial = {
            **super().get_initial(),
            "project": draft.project,
            "category": draft.category,
            "name": draft.name,
            "slug": draft.slug,
            **self.locked_values(),
        }
        initial["new_lang"] = "none"
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.method == "POST":
            # Revalidate server-side, not through initial values: overwrite
            # whatever the client posted for the conversion-derived fields.
            data = kwargs.get("data")
            if data is not None:
                data = data.copy()
                locked = self.locked_values()
                for field in LOC_KIT_LOCKED_FIELDS:
                    if field not in locked:
                        continue
                    value = locked[field]
                    if isinstance(value, Language):
                        data[field] = str(value.pk)
                    elif isinstance(value, bool):
                        data[field] = "on" if value else ""
                    else:
                        data[field] = value
                data["project"] = str(self.draft.project.pk)
                kwargs["data"] = data
        return kwargs

    @transaction.atomic
    def form_valid(self, form):
        draft = self.draft
        if draft.state != LocKitImportDraft.State.PREVIEW_READY:
            raise Http404

        # Re-render from the stored profile and parse back once more before a
        # repository exists. The draft holds a profile, not staged bytes, so
        # this is the real publication gate rather than a replay of it.
        sheets = self.read_draft_sheets(draft)
        if draft.sheet not in sheets:
            raise Http404
        try:
            preview = validate_glossary_profile(
                profile_document=json.loads(draft.profile_json),
                rows=sheets[draft.sheet],
                sheet_name=draft.sheet,
                component_name=draft.slug,
            )
        except GlossaryProfileError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)

        # Build the repository at the path this component will actually
        # occupy. Taking category from the draft while the form saves a
        # different one would point full_path at somebody else's component
        # directory, and LocalRepository.from_files removes an existing
        # target before cloning - that would destroy a live repository.
        # form.instance carries exactly the validated, about-to-be-saved
        # project/category/slug.
        fake = form.instance
        fake.project = draft.project
        LocalRepository.from_files(
            fake.full_path,
            {f"tbx/{name}": data for name, data in preview.files.items()},
        )

        response = super().form_valid(form)

        # Consume the draft only once the component really exists, so a
        # non-creating code path can never destroy the operator's upload.
        if getattr(self, "object", None) is None:
            return response
        draft.state = LocKitImportDraft.State.CONSUMED
        draft.save(update_fields=["state"])
        draft.delete_storage()
        draft.delete()
        transaction.on_commit(lambda: flag_glossary_terminology.delay(self.object.pk))
        return response


@method_decorator(login_required, name="dispatch")
class LocKitGlossaryUpdateStartView(TemplateView):
    """
    Stage a loc-kit table that appends new terms to one existing glossary.

    Nothing about the glossary changes until the preview is applied: the
    upload stays in a temporary draft exactly like the creation flow, and
    the same deterministic mapping runs for a single-sheet table.
    """

    template_name = "trans/loc_kit_glossary_update.html"

    def get_component(self) -> Component:
        component = parse_path(self.request, self.kwargs["path"], (Component,))
        if (
            not component.is_glossary
            or component.locked
            or not self.request.user.has_perm("upload.perform", component)
        ):
            raise Http404
        return component

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object"] = self.get_component()
        context["form"] = kwargs.get("form") or LocKitGlossaryUpdateForm()
        return context

    @transaction.atomic
    def post(self, request: AuthenticatedHttpRequest, **kwargs):
        component = self.get_component()
        form = LocKitGlossaryUpdateForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        uploaded = form.cleaned_data["table"]
        # ruff: ignore[import-outside-top-level]
        from loc_kit_ingest.reader import ReaderError, read_sheets

        filename = os.path.basename(getattr(uploaded, "name", "") or "")
        uploaded.seek(0)
        payload = uploaded.read()

        # Read locally first: an unreadable file must fail here, before a
        # draft exists and long before anything could touch the glossary.
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / filename
            local.write_bytes(payload)
            try:
                sheets = read_sheets(local)
            except ReaderError as error:
                form.add_error("table", gettext("Could not read the table: %s") % error)
                return self.render_to_response(self.get_context_data(form=form))
        if not sheets:
            form.add_error("table", gettext("The table holds no worksheets."))
            return self.render_to_response(self.get_context_data(form=form))

        if not request.session.session_key:
            request.session.create()

        draft = LocKitImportDraft(
            owner=request.user,
            session_key=request.session.session_key or "",
            project=component.project,
            slug=component.slug,
            name=component.name,
            source_filename=filename,
            target_component=component,
        )
        # Storage is not transactional; undo the write if the row or the
        # deterministic mapping fails (see CreateFromZip._start_glossary_draft).
        draft.uploaded.save(filename, ContentFile(payload), save=False)
        try:
            return _insert_draft_and_map(request, draft, sheets)
        except Exception:
            draft.delete_storage()
            raise
