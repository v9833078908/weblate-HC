# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import secrets

from django.apps import apps
from django.conf import settings
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from weblate.trans.delivery_policy import WIZARD_DELIVERY_POLICY
from weblate.wizard_auth.models import DelegationFamily, SubjectIdentity, TokenBinding


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


def run_delegation_active(run) -> bool:
    family_id = run.execution_options.get("_wizard_delegation") if run else None
    if not family_id:
        return True
    return bool(
        getattr(settings, "WIZARD_SSO_ENABLED", False)
        and delegation_active(family_id, run.actor_id)
    )


def guard_paid_run(run_id) -> None:
    """Check authorization before any new paid transport, including judge repair."""
    if not run_id:
        return
    run = apps.get_model("trans", "ProducerRun").objects.filter(pk=run_id).first()
    if run and not run_delegation_active(run):
        msg = "Wizard authorization expired or was revoked; no paid request sent"
        raise WizardAuthorizationRequired(msg)
    if run and run.execution_options.get("_wizard_delegation"):
        # Reuse the native scope/mode permission policy with a fresh actor,
        # rather than the worker's potentially cached authorization state.
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.views.judge import _get_scope, user_can_view_producer_run

        actor = (
            apps.get_model("weblate_auth", "User")
            .objects.filter(pk=run.actor_id)
            .first()
        )
        try:
            permitted = actor and user_can_view_producer_run(
                actor, _get_scope(run), run
            )
        except Http404:
            permitted = False
        if not permitted:
            msg = "Wizard scope permission changed; no paid request sent"
            raise WizardAuthorizationRequired(msg)
