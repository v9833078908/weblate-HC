# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from weblate.wizard_auth.grants import checksum, family_for
from weblate.wizard_auth.permissions import required_scope


class WizardOAuthAuthentication(BaseAuthentication):
    """Recognize only our token prefix; leave existing API credentials alone."""

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if (
            len(header) != 2
            or header[0].lower() != b"bearer"
            or not header[1].startswith(b"wzo_")
        ):
            return None
        if not getattr(settings, "WIZARD_SSO_ENABLED", False):
            msg = "Wizard SSO is disabled"
            raise AuthenticationFailed(msg)
        try:
            raw = header[1].decode("ascii")
        except UnicodeDecodeError as error:
            msg = "Invalid Wizard token"
            raise AuthenticationFailed(msg) from error
        family = family_for(raw, "access")
        token = (
            apps.get_model("oauth2_provider", "AccessToken")
            .objects.select_related("user", "application")
            .filter(token_checksum=checksum(raw))
            .first()
        )
        if not token or not family:
            msg = "Wizard authorization expired or was revoked"
            raise AuthenticationFailed(msg)
        if (
            token.expires <= timezone.now()
            or not token.user
            or not token.user.is_active
            or token.user_id != family.user_id
            or not token.application
            or token.application.client_id != settings.WIZARD_SSO_CLIENT_ID
            or family.client_id != token.application.client_id
        ):
            msg = "Wizard authorization expired or was revoked"
            raise AuthenticationFailed(msg)
        prefix = "/" + getattr(settings, "URL_PREFIX", "").strip("/")
        prefix = prefix.rstrip("/")
        path = (
            request.path[len(prefix) :]
            if prefix and request.path.startswith(prefix + "/")
            else request.path
        )
        scope = required_scope(request.method, path)
        if scope not in token.scope.split():
            msg = "Wizard scope is not granted"
            raise PermissionDenied(msg)
        # The native Unit API also accepts flags/state. A delegated importer is
        # allowed to update only text/context, never grant itself review state.
        if (
            request.method == "PATCH"
            and path.startswith("/api/units/")
            and (
                not isinstance(request.data, dict)
                or not set(request.data) <= {"target", "explanation"}
            )
        ):
            msg = "Wizard cannot change these unit fields"
            raise PermissionDenied(msg)
        request.wizard_delegation = family
        return token.user, token

    def authenticate_header(self, request):
        header = get_authorization_header(request).split()
        if (
            len(header) == 2
            and header[0].lower() == b"bearer"
            and header[1].startswith(b"wzo_")
        ):
            return "Bearer"
        return "Token"
