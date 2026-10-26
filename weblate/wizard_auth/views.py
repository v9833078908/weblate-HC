# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django_otp import DEVICE_ID_SESSION_KEY
from oauth2_provider.models import get_application_model  # type: ignore[import-untyped]
from oauth2_provider.views import (  # type: ignore[import-untyped]
    AuthorizationView,
    ConnectDiscoveryInfoView,
)


class WizardDiscoveryView(ConnectDiscoveryInfoView):
    """Advertise the registered client's revocation endpoint explicitly."""

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        if response.status_code != 200:
            return response
        document = json.loads(response.content)
        document["revocation_endpoint"] = (
            settings.WIZARD_SSO_ISSUER.rstrip("/") + "/revoke_token/"
        )
        return JsonResponse(document)


class WizardAuthorizationView(AuthorizationView):
    template_name = "wizard_auth/authorize.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not request.user.is_active:
                return HttpResponseForbidden()
            if (
                request.user.profile.has_2fa
                and DEVICE_ID_SESSION_KEY not in request.session
            ):
                return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        client_id = (
            request.POST.get("client_id")
            if request.method == "POST"
            else request.GET.get("client_id")
        )
        if client_id != settings.WIZARD_SSO_CLIENT_ID:
            return HttpResponseForbidden()
        application = (
            get_application_model().objects.filter(client_id=client_id).first()
        )
        # Browsers enforce form-action across the authorization POST's redirect
        # to Wizard as well. Extend only this response, using registered origins,
        # never an origin taken from a user-supplied redirect parameter.
        if application:
            origins = []
            for uri in application.redirect_uris.split():
                parsed = urlsplit(uri)
                if (
                    parsed.scheme == "https"
                    and parsed.netloc
                    and not parsed.username
                    and not parsed.fragment
                ):
                    origins.append(f"https://{parsed.netloc}")
            request.csp_form_action_sources = tuple(origins)
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
