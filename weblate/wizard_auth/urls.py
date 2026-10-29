# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.urls import path
from oauth2_provider import views  # type: ignore[import-untyped]

from weblate.wizard_auth.views import WizardAuthorizationView, WizardDiscoveryView

app_name = "oauth2_provider"
urlpatterns = [
    path("authorize/", WizardAuthorizationView.as_view(), name="authorize"),
    path("token/", views.TokenView.as_view(), name="token"),
    path("revoke_token/", views.RevokeTokenView.as_view(), name="revoke-token"),
    path("userinfo/", views.UserInfoView.as_view(), name="user-info"),
    path(
        ".well-known/openid-configuration",
        WizardDiscoveryView.as_view(),
        name="oidc-connect-discovery-info",
    ),
    path(".well-known/jwks.json", views.JwksInfoView.as_view(), name="jwks-info"),
]
