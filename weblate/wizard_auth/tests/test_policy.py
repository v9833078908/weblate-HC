# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import os

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from weblate.wizard_auth.api import WizardOAuthAuthentication
from weblate.wizard_auth.permissions import required_scope


class DelegationPolicyTest(SimpleTestCase):
    def test_optional_sso_test_bootstrap_matches_available_dependencies(self):
        enabled = (
            os.environ.get("CI_TEST_WIZARD_SSO", "1") != "0"
            and importlib.util.find_spec("oauth2_provider") is not None
        )
        self.assertEqual(settings.WIZARD_SSO_ENABLED, enabled)
        self.assertEqual("oauth2_provider" in settings.INSTALLED_APPS, enabled)
        if enabled:
            self.assertIn("PRIVATE KEY", settings.OAUTH2_PROVIDER["OIDC_RSA_PRIVATE_KEY"])

    def test_existing_api_authentication_challenge_is_preserved(self):
        factory = RequestFactory()
        authentication = WizardOAuthAuthentication()
        self.assertEqual(
            authentication.authenticate_header(factory.get("/api/")), "Token"
        )
        self.assertEqual(
            authentication.authenticate_header(
                factory.get("/api/", HTTP_AUTHORIZATION="Bearer wzo_test")
            ),
            "Bearer",
        )

    def test_read_scope_is_not_a_write_scope(self):
        self.assertEqual(required_scope("GET", "/api/projects/game/"), "wizard.read")
        self.assertEqual(required_scope("POST", "/api/projects/"), "wizard.import")
        self.assertEqual(
            required_scope("POST", "/api/producer/projects/game/mt/batches/"),
            "wizard.translate",
        )

    def test_unknown_routes_and_secret_configuration_are_closed(self):
        for method, path in [
            ("GET", "/api/users/"),
            ("GET", "/api/projects/game/machinery_settings/"),
            ("PATCH", "/api/projects/game/machinery_settings/"),
            ("DELETE", "/api/projects/game/"),
            ("GET", "/api/producer/new-admin-action/"),
            ("POST", "/api/translations/game/component/pl/autotranslate/"),
        ]:
            with (
                self.subTest(method=method, path=path),
                self.assertRaises(PermissionDenied),
            ):
                required_scope(method, path)

    def test_task_and_profile_have_explicit_permissions(self):
        self.assertEqual(
            required_scope("PATCH", "/api/producer/projects/game/translation-profile/"),
            "wizard.import",
        )
        with self.assertRaises(PermissionDenied):
            required_scope("POST", "/api/units/3/")
