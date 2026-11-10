# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from weblate.trans.delivery_policy import WIZARD_DELIVERY_POLICY
from weblate.wizard_auth.models import DelegationFamily, SubjectIdentity, TokenBinding

if TYPE_CHECKING:
    from collections.abc import Sequence

    from weblate.auth.models import User
    from weblate.trans.models import Component, Project, Unit
    from weblate.trans.models.judge import ProducerRun


class WizardAuthorizationRequired(PermissionDenied):
    """The current transport was refused before its paid delivery reservation."""


def subject_for(user) -> str:
    identity, _ = SubjectIdentity.objects.get_or_create(user=user)
    return str(identity.subject)


def access_token(request=None) -> str:
    return "wzo_" + secrets.token_urlsafe(48)


def refresh_token(request=None) -> str:
    return "wzr_" + secrets.token_urlsafe(48)


def checksum(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def family_for(token: str, kind: str):
    binding = (
        TokenBinding.objects.select_related("family")
        .filter(checksum=checksum(token), kind=kind)
        .first()
    )
    if (
        not binding
        or binding.family.revoked
        or binding.family.expires <= timezone.now()
    ):
        return None
    return binding.family


def delegation_active(family_id, user_id) -> bool:
    return DelegationFamily.objects.filter(
        pk=family_id,
        user_id=user_id,
        user__is_active=True,
        revoked=False,
        expires__gt=timezone.now(),
    ).exists()


def request_binding(request) -> dict:
    family = getattr(request, "wizard_delegation", None)
    return (
        {
            "_wizard_delegation": str(family.pk),
            "delivery_policy": WIZARD_DELIVERY_POLICY,
        }
        if family
        else {}
    )


def delegated_project_actor(
    actor: User | None, project: Project, family_id: str | None
) -> User | None:
    """Resolve a persisted Wizard binding without falling back to native privileges."""
    if family_id is None:
        return actor
    if (
        actor is None
        or not getattr(settings, "WIZARD_SSO_ENABLED", False)
        or not delegation_active(family_id, actor.pk)
    ):
        return None
    # ruff: ignore[import-outside-top-level]
    from weblate.wizard_auth.policy import (
        wizard_principal,
        wizard_project_profile_allowed,
        wizard_project_role,
    )

    actor = wizard_principal(actor)
    return (
        actor
        if wizard_project_role(actor, project) == "owner"
        and wizard_project_profile_allowed(actor, project)
        else None
    )


def _may_use_component(actor: User, project: Project, component: Component) -> bool:
    # ruff: ignore[import-outside-top-level]
    from weblate.wizard_auth.policy import wizard_can_write_component

    return component.project_id == project.pk and wizard_can_write_component(
        actor, component
    )


def delegated_run_actor(
    run: ProducerRun | None, actor: User | None, *, component: Component | None = None
) -> User | None:
    """Restore the request's Wizard permission boundary for a background worker."""
    if run is None or not run.execution_options.get("_wizard_delegation"):
        return actor
    if actor is None or actor.pk != run.actor_id:
        return None

    # Keep the policy and model imports outside the authorization module's
    # bootstrap path.
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.views.judge import _get_scope

    # ruff: ignore[import-outside-top-level]
    from weblate.wizard_auth.policy import wizard_scope_project

    try:
        project = wizard_scope_project(_get_scope(run))
    except Http404:
        return None
    if project is None:
        return None
    actor = delegated_project_actor(
        actor, project, run.execution_options["_wizard_delegation"]
    )
    if actor is None:
        return None
    if component is not None and not _may_use_component(actor, project, component):
        return None
    return actor


def guard_run_result(
    run: ProducerRun | None,
    actor: User | None,
    *,
    unit: Unit | None = None,
    review: bool = False,
) -> User | None:
    """Recheck before applying an answer that may have arrived after revocation."""
    if run is None or not run.execution_options.get("_wizard_delegation"):
        return actor
    current = (
        apps.get_model("weblate_auth", "User").objects.filter(pk=actor.pk).first()
        if actor is not None
        else None
    )
    component = unit.translation.component if unit is not None else None
    permitted = delegated_run_actor(run, current, component=component)
    if permitted is None or (
        unit is not None
        and (
            not permitted.has_perm("translation.auto", unit.translation)
            or (review and not permitted.has_perm("unit.review", unit))
        )
    ):
        msg = "Wizard project permission changed"
        raise DjangoPermissionDenied(msg)
    return permitted


def _may_send_units(
    actor: User, project: Project, unit_ids: Sequence[int | None], *, review: bool
) -> bool:
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit

    if not unit_ids or None in unit_ids:
        return False
    units = list(
        Unit.objects.filter(pk__in=unit_ids).select_related(
            "translation__component", "translation__component__linked_component"
        )
    )
    return len(units) == len(set(unit_ids)) and all(
        _may_use_component(actor, project, unit.translation.component)
        and actor.has_perm("translation.auto", unit.translation)
        and (not review or actor.has_perm("unit.review", unit))
        for unit in units
    )


def guard_paid_run(
    run_id, *, unit_ids: Sequence[int | None] | None = None, review: bool = False
) -> None:
    """Check authorization before any new paid transport, including judge repair."""
    if not run_id:
        return
    run = apps.get_model("trans", "ProducerRun").objects.filter(pk=run_id).first()
    if run and run.execution_options.get("_wizard_delegation"):
        # Reuse the native scope/mode permission policy with a fresh actor,
        # rather than the worker's potentially cached authorization state.
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.views.judge import _get_scope, user_can_view_producer_run

        # ruff: ignore[import-outside-top-level]
        from weblate.wizard_auth.policy import wizard_scope_project

        actor = (
            apps.get_model("weblate_auth", "User")
            .objects.filter(pk=run.actor_id)
            .first()
        )
        restricted = delegated_run_actor(run, actor)
        try:
            scope = _get_scope(run)
        except Http404:
            scope = None
        project = wizard_scope_project(scope)
        permitted = (
            restricted is not None
            and project is not None
            and user_can_view_producer_run(restricted, scope, run)
            and (
                unit_ids is None
                or _may_send_units(restricted, project, unit_ids, review=review)
            )
        )
        if not permitted:
            msg = "Wizard scope permission changed; no paid request sent"
            raise WizardAuthorizationRequired(msg)
