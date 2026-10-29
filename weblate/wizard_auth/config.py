# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def configure(namespace: dict) -> None:
    """Enable the optional provider only from operator-controlled settings."""
    if "weblate.wizard_auth" not in namespace["INSTALLED_APPS"]:
        namespace["INSTALLED_APPS"] = [
            *namespace["INSTALLED_APPS"],
            "weblate.wizard_auth",
        ]
    if not namespace.get("WIZARD_SSO_ENABLED"):
        return
    issuer = namespace.get("WIZARD_SSO_ISSUER", "").rstrip("/")
    parts = urlsplit(issuer)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.query
        or parts.fragment
        or parts.username
    ):
        msg = "WIZARD_SSO_ISSUER must be an exact HTTPS issuer"
        raise ImproperlyConfigured(msg)
    if not namespace.get("WIZARD_SSO_CLIENT_ID"):
        msg = "WIZARD_SSO_CLIENT_ID is required"
        raise ImproperlyConfigured(msg)
    namespace["INSTALLED_APPS"] = list(
        dict.fromkeys([*namespace["INSTALLED_APPS"], "oauth2_provider"])
    )
    namespace["OAUTH2_PROVIDER"] = {
        "OIDC_ENABLED": True,
        "OIDC_ISS_ENDPOINT": issuer,
        "OIDC_RSA_PRIVATE_KEY": Path(
            namespace["WIZARD_SSO_PRIVATE_KEY_FILE"]
        ).read_text(encoding="utf-8"),
        "OIDC_RSA_PRIVATE_KEYS_INACTIVE": namespace.get("WIZARD_SSO_INACTIVE_KEYS", []),
        "OAUTH2_VALIDATOR_CLASS": "weblate.wizard_auth.oidc.WizardValidator",
        "ACCESS_TOKEN_GENERATOR": "weblate.wizard_auth.grants.access_token",
        "REFRESH_TOKEN_GENERATOR": "weblate.wizard_auth.grants.refresh_token",
        "ACCESS_TOKEN_EXPIRE_SECONDS": 300,
        "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
        "REFRESH_TOKEN_EXPIRE_SECONDS": 86400,
        "ROTATE_REFRESH_TOKEN": True,
        "REFRESH_TOKEN_REUSE_PROTECTION": True,
        "PKCE_REQUIRED": True,
        "ALLOWED_REDIRECT_URI_SCHEMES": ["https"],
        "SCOPES": {
            name: name
            for name in (
                "openid",
                "profile",
                "wizard.read",
                "wizard.import",
                "wizard.translate",
                "wizard.quality",
            )
        },
    }
