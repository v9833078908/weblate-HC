# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project authorization for a current Wizard delegation."""

from __future__ import annotations

import json
from copy import copy
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from django.conf import settings
from django.http import Http404
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework.exceptions import PermissionDenied

from weblate.auth.data import SELECTION_MANUAL
from weblate.trans.models import Component, Project, Translation, Unit

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.auth.models import User

WizardProjectRole = Literal["owner", "read"]
WIZARD_ACCESS_POLICY_VERSION = 1
PROFILE_FIELDS = frozenset({"persona", "style", "language_instructions"})
PROFILE_ENGINES = frozenset({"openrouter", "litellm"})
_PROFILE_SIGNATURE = "_wizard_manual_profile_signature"


def wizard_internal(user: User) -> bool:
    group_id = getattr(settings, "WIZARD_INTERNAL_GROUP_ID", 0)
    return bool(
        user.is_active
        and type(group_id) is int
        and group_id > 0
        and user.groups.filter(pk=group_id).exists()
    )


def wizard_external(user: User) -> bool:
    return user.wizard_project_roles is not None and not wizard_internal(user)


def _profile_signature(project: Project, service: str, profile: dict) -> str:
    payload = json.dumps(
        [project.pk, service, profile], sort_keys=True, separators=(",", ":")
    )
    return salted_hmac(
        "weblate.wizard.manual-profile", payload, algorithm="sha256"
    ).hexdigest()


def _instruction_fields(configuration: dict) -> dict:
    return {key: configuration[key] for key in PROFILE_FIELDS if key in configuration}


def wizard_project_profile_allowed(user: User, project: Project) -> bool:
    """Accept external use only for empty or verified manual instructions."""
    if not wizard_external(user):
        return True
    try:
        effective = project.get_machinery_settings()
    except TypeError:
        return False
    for service, configuration in effective.items():
        if service not in PROFILE_ENGINES:
            continue
        profile = _instruction_fields(configuration)
        if not any(profile.values()):
            continue
        local = project.machinery_settings.get(service, {})
        marker = local.get(_PROFILE_SIGNATURE, "") if isinstance(local, dict) else ""
        if not isinstance(marker, str) or not constant_time_compare(
            marker, _profile_signature(project, service, profile)
        ):
            return False
    return True


def require_wizard_profile(user: User, project: Project) -> None:
    if not wizard_project_profile_allowed(user, project):
        msg = "Wizard manual profile authorization is required"
        raise PermissionDenied(msg)


def sign_wizard_profile(project: Project, service: str) -> None:
    profile = _instruction_fields(project.get_machinery_settings()[service])
    project.machinery_settings[service][_PROFILE_SIGNATURE] = _profile_signature(
        project, service, profile
    )


def external_workspace_ids() -> set[UUID]:
    try:
        return {
            UUID(str(workspace_id).strip())
            for workspace_id in getattr(settings, "WIZARD_EXTERNAL_WORKSPACE_IDS", ())
            if str(workspace_id).strip()
        }
    except (TypeError, ValueError):
        return set()


def _external_project_ids() -> set[int]:
    values = getattr(settings, "WIZARD_EXTERNAL_PROJECT_IDS", ())
    if isinstance(values, str):
        return set()
    try:
        return {
            int(str(project_id).strip())
            for project_id in values
            if str(project_id).strip()
        }
    except (TypeError, ValueError):
        return set()


def _project_roles(user: User) -> dict[int, WizardProjectRole]:
    if not user.is_active:
        return {}
    direct_project_ids: set[int] = set()
    for membership in user.cached_memberships:
        if (
            membership.project_selection != SELECTION_MANUAL
            or membership.defining_workspace_id is not None
            or membership.has_componentlists
            or membership.component_values
            or (membership.enforced_2fa and not user.profile.has_2fa)
        ):
            continue
        languages = user.get_membership_languages(membership)
        if (
            languages is not None
            and languages.membership_limited
            and not languages.language_ids
        ):
            continue
        direct_project_ids.update(membership.project_ids)

    project_ids = {
        project_id
        for project_id in direct_project_ids
        if (permissions := user.project_permissions.get(project_id))
        and permissions != [(None, None)]
    }
    if project_ids and not wizard_internal(user):
        external_projects = _external_project_ids()
        external_workspaces = external_workspace_ids()
        project_ids = {
            project_id
            for project_id, workspace_id in Project.objects.filter(
                pk__in=project_ids
            ).values_list("pk", "workspace_id")
            if project_id in external_projects or workspace_id in external_workspaces
        }
    owned_ids = set(
        user.owned_projects.filter(pk__in=project_ids).values_list("pk", flat=True)
    )
    owned_ids = {
        project_id
        for project_id in owned_ids
        if any(
            permissions is not None
            and "project.edit" in permissions
            and (languages is None or not languages.membership_limited)
            for permissions, languages in user.project_permissions[project_id]
        )
    }
    return {
        project_id: "owner" if project_id in owned_ids else "read"
        for project_id in project_ids
    }


def wizard_principal(user: User) -> User:
    """Return a restricted copy of the freshly loaded actor."""
    principal = copy(user)
    principal.clear_permissions_cache()
    principal.is_superuser = False
    principal.wizard_project_roles = _project_roles(principal)
    return principal


def wizard_project_role(user: User, project: Project) -> WizardProjectRole | None:
    roles = user.wizard_project_roles
    if roles is None:
        roles = _project_roles(user)
    return roles.get(project.pk)


def require_wizard_project(
    user: User, scope: Project | Component, *, write: bool
) -> None:
    """Apply the Wizard overlay only to an already restricted actor."""
    if user.wizard_project_roles is None:
        return
    project = scope.project if isinstance(scope, Component) else scope
    role = wizard_project_role(user, project)
    if role is None or (
        isinstance(scope, Component) and not user.can_access_component(scope)
    ):
        raise Http404
    if write and role != "owner":
        msg = "Wizard project ownership is required"
        raise PermissionDenied(msg)
    if write and isinstance(scope, Component) and scope.linked_component_id is not None:
        linked = scope.linked_component
        if linked is None or not user.can_access_component(linked):
            raise Http404
        require_wizard_project(user, linked.project, write=True)


def wizard_can_write_component(user: User, component: Component) -> bool:
    try:
        require_wizard_project(user, component, write=True)
    except (Http404, PermissionDenied):
        return False
    return True


def require_wizard_components(user: User, units: Iterable[Unit]) -> None:
    """Require writable project scope for the distinct components in a unit batch."""
    if user.wizard_project_roles is None:
        return
    components = {
        unit.translation.component_id: unit.translation.component for unit in units
    }
    for component in components.values():
        require_wizard_project(user, component, write=True)


def wizard_scope_project(scope: object) -> Project | None:
    """Resolve a run scope to one project. Workspace-wide runs are excluded."""
    if isinstance(scope, Project):
        return scope
    if isinstance(scope, Unit):
        return scope.translation.component.project
    if isinstance(scope, Translation):
        return scope.component.project
    project = getattr(scope, "project", None)
    return project if isinstance(project, Project) else None
