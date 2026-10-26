# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from jwcrypto.jwk import JWK  # type: ignore[import-untyped]
from oauth2_provider.models import get_application_model  # type: ignore[import-untyped]
from rest_framework.exceptions import PermissionDenied

from weblate.auth.models import User
from weblate.trans.llm_delivery import dispatch_exchange
from weblate.trans.models import Project
from weblate.trans.models.judge import ProducerRun
from weblate.wizard_auth.grants import family_for, guard_paid_run, subject_for
from weblate.wizard_auth.models import DelegationFamily


class WizardOIDCTest(TestCase):
    user: User
    application: Any

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="sso-alice",
            email="sso-alice@example.com",
            password="synthetic-test-password",
        )
        cls.application = get_application_model().objects.create(
            user=cls.user,
            client_id=settings.WIZARD_SSO_CLIENT_ID,
            client_secret="synthetic-client-secret",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            algorithm="RS256",
            redirect_uris="https://wizard.example/auth/weblate/callback",
            skip_authorization=True,
        )

    def issue(self, *, scope="openid profile wizard.read", verifier="a" * 64):
        self.client.force_login(
            self.user, backend="weblate.accounts.auth.WeblateUserBackend"
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        response = self.client.get(
            "/oidc/authorize/?"
            + urlencode(
                {
                    "client_id": self.application.client_id,
                    "redirect_uri": "https://wizard.example/auth/weblate/callback",
                    "response_type": "code",
                    "scope": scope,
                    "state": "synthetic-state",
                    "nonce": "synthetic-nonce",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            ),
            secure=True,
        )
        if not self.application.skip_authorization:
            self.assertEqual(response.status_code, 200)
            self.assertIn("https://wizard.example", response["Content-Security-Policy"])
            form = response.context["form"]
            fields = {field.name: field.value() or "" for field in form.hidden_fields()}
            fields["allow"] = "true"
            response = self.client.post("/oidc/authorize/", fields, secure=True)
            if response.status_code == 200:
                self.fail(str(response.context["form"].errors))
        self.assertEqual(response.status_code, 302, response.content[:200])
        params = parse_qs(urlsplit(response["Location"]).query)
        self.assertIn("code", params, response["Location"])
        code = params["code"][0]
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.application.client_id,
            "client_secret": "synthetic-client-secret",
            "redirect_uri": "https://wizard.example/auth/weblate/callback",
            "code_verifier": verifier,
        }
        token = self.client.post("/oidc/token/", payload, secure=True)
        self.assertEqual(token.status_code, 200, token.content[:200])
        return token.json(), payload

    def test_real_code_exchange_subject_and_replay_protection(self):
        token, payload = self.issue()
        self.assertIn("id_token", token)
        self.assertTrue(token["access_token"].startswith("wzo_"))
        response = self.client.get(
            "/api/producer/me/",
            headers={"authorization": "Bearer " + token["access_token"]},
            secure=True,
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response.json()["subject"], subject_for(self.user))
        self.assertEqual(
            self.client.post("/oidc/token/", payload, secure=True).status_code, 400
        )

    def test_explicit_consent_uses_the_same_verified_code_flow(self):
        self.application.skip_authorization = False
        self.application.save(update_fields=["skip_authorization"])
        token, _ = self.issue()
        self.assertTrue(token["access_token"].startswith("wzo_"))
        result = self.client.get(
            "/api/producer/me/",
            headers={"authorization": "Bearer " + token["access_token"]},
            secure=True,
        )
        self.assertNotIn("https://wizard.example", result["Content-Security-Policy"])

    def test_signing_key_overlap_publishes_only_both_public_keys(self):
        previous = self.client.get("/oidc/.well-known/jwks.json", secure=True).json()[
            "keys"
        ]
        replacement = (
            JWK.generate(kty="RSA", size=2048)
            .export_to_pem(private_key=True, password=None)
            .decode()
        )
        with override_settings(
            OAUTH2_PROVIDER={
                **getattr(settings, "OAUTH2_PROVIDER", {}),
                "OIDC_RSA_PRIVATE_KEY": replacement,
                "OIDC_RSA_PRIVATE_KEYS_INACTIVE": [
                    getattr(settings, "OAUTH2_PROVIDER", {})["OIDC_RSA_PRIVATE_KEY"]
                ],
            }
        ):
            keys = self.client.get("/oidc/.well-known/jwks.json", secure=True).json()[
                "keys"
            ]
        self.assertEqual(len(keys), 2)
        self.assertIn(previous[0]["kid"], {key["kid"] for key in keys})
        for key in keys:
            self.assertFalse({"d", "p", "q", "dp", "dq", "qi"} & key.keys())

    def test_token_cannot_read_keys_or_admin_or_write_without_scope(self):
        token, _ = self.issue()
        for method, path in (
            ("get", "/api/users/"),
            ("get", "/api/projects/private/machinery_settings/"),
            ("post", "/api/projects/"),
        ):
            result = getattr(self.client, method)(
                path, HTTP_AUTHORIZATION="Bearer " + token["access_token"], secure=True
            )
            self.assertEqual(result.status_code, 403)

    def test_deactivation_is_effective_for_existing_token(self):
        token, _ = self.issue()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.client.get(
            "/api/producer/me/",
            headers={"authorization": "Bearer " + token["access_token"]},
            secure=True,
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_does_not_extend_absolute_lifetime(self):
        token, _ = self.issue()
        family = family_for(token["refresh_token"], "refresh")
        expires = family.expires
        response = self.client.post(
            "/oidc/token/",
            {
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": self.application.client_id,
                "client_secret": "synthetic-client-secret",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        family.refresh_from_db()
        self.assertEqual(family.expires, expires)
        self.assertEqual(
            family_for(response.json()["refresh_token"], "refresh").pk, family.pk
        )
        DelegationFamily.objects.filter(pk=family.pk).update(
            expires=timezone.now() - timedelta(seconds=1)
        )
        denied = self.client.get(
            "/api/producer/me/",
            headers={"authorization": "Bearer " + response.json()["access_token"]},
            secure=True,
        )
        self.assertEqual(denied.status_code, 401)

    def test_revocation_fences_queued_paid_run_without_transport(self):
        token, _ = self.issue()
        family = family_for(token["access_token"], "access")
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type="project",
            scope_id="test",
            scope_label="Synthetic",
            requested_mode="judge",
            cap=1,
            execution_options={"_wizard_delegation": str(family.pk)},
        )
        revoked = self.client.post(
            "/oidc/revoke_token/",
            {
                "token": token["refresh_token"],
                "client_id": self.application.client_id,
                "client_secret": "synthetic-client-secret",
            },
            secure=True,
        )
        self.assertEqual(revoked.status_code, 200)
        send = Mock(return_value="never")
        with self.assertRaises(PermissionDenied):
            dispatch_exchange(["synthetic-paid-work"], "a" * 64, send, run_id=run.pk)
        send.assert_not_called()

    def test_discovery_is_usable_and_feature_off_is_closed(self):
        response = self.client.get(
            "/oidc/.well-known/openid-configuration", secure=True
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response.json()["issuer"], settings.WIZARD_SSO_ISSUER)
        self.assertEqual(
            response.json()["revocation_endpoint"],
            settings.WIZARD_SSO_ISSUER + "/revoke_token/",
        )
        token, _ = self.issue()
        with override_settings(WIZARD_SSO_ENABLED=False):
            response = self.client.get(
                "/api/producer/me/",
                headers={"authorization": "Bearer " + token["access_token"]},
                secure=True,
            )
            self.assertEqual(response.status_code, 401)

    @override_settings(JUDGE_ENABLED=True)
    def test_queued_paid_request_rechecks_native_project_permissions(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        project = Project.objects.create(
            name="Delegated permission scope",
            slug="delegated-scope",
            access_control=Project.ACCESS_PRIVATE,
            translation_review=True,
        )
        token, _ = self.issue()
        family = family_for(token["access_token"], "access")
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type="project",
            scope_id=str(project.pk),
            scope_label="Synthetic",
            requested_mode="judge",
            cap=1,
            execution_options={"_wizard_delegation": str(family.pk)},
        )
        guard_paid_run(run.pk)
        User.objects.filter(pk=self.user.pk).update(is_superuser=False)
        send = Mock(return_value="never")
        with self.assertRaises(PermissionDenied):
            dispatch_exchange(
                ["permission-revoked-work"], "b" * 64, send, run_id=run.pk
            )
        send.assert_not_called()

    @override_settings(JUDGE_ENABLED=True)
    def test_delegated_api_cannot_resume_a_legacy_service_account_run(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        project = Project.objects.create(
            name="Legacy isolated scope", slug="legacy-scope", translation_review=True
        )
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type="project",
            scope_id=str(project.pk),
            scope_label="Synthetic",
            requested_mode="judge",
            cap=1,
            status="failed",
        )
        token, _ = self.issue(scope="openid wizard.read wizard.quality")
        response = self.client.post(
            f"/api/producer/runs/{run.pk}/resume/",
            {"attempt": 1},
            content_type="application/json",
            headers={"authorization": "Bearer " + token["access_token"]},
            secure=True,
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ProducerRun.objects.filter(resumed_from=run).exists())

    def test_plain_pkce_and_unregistered_callback_never_issue_a_code(self):
        self.client.force_login(
            self.user, backend="weblate.accounts.auth.WeblateUserBackend"
        )
        base = {
            "client_id": self.application.client_id,
            "response_type": "code",
            "redirect_uri": "https://wizard.example/auth/weblate/callback",
            "scope": "openid wizard.read",
            "nonce": "synthetic",
            "state": "synthetic",
            "code_challenge": "x" * 64,
            "code_challenge_method": "S256",
        }
        for changes in (
            {"code_challenge_method": "plain"},
            {"redirect_uri": "https://attacker.example/callback"},
            {"response_type": "token"},
            {"nonce": ""},
        ):
            response = self.client.get(
                "/oidc/authorize/?" + urlencode({**base, **changes}), secure=True
            )
            self.assertNotIn("code=", response.get("Location", ""))

    def test_existing_session_without_second_factor_cannot_authorize(self):
        TOTPDevice.objects.create(user=self.user, confirmed=True)
        self.client.force_login(
            self.user, backend="weblate.accounts.auth.WeblateUserBackend"
        )
        response = self.client.get(
            "/oidc/authorize/?" + urlencode({"client_id": self.application.client_id}),
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_reuse_of_refresh_revokes_its_family(self):
        token, _ = self.issue()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": self.application.client_id,
            "client_secret": "synthetic-client-secret",
        }
        first = self.client.post("/oidc/token/", payload, secure=True)
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/oidc/token/", payload, secure=True)
        self.assertEqual(second.status_code, 400)
        self.assertIsNone(family_for(first.json()["access_token"], "access"))

    def test_project_profile_is_user_scoped_and_does_not_return_credentials(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        project = Project.objects.create(
            name="SSO private fixture",
            slug="sso-private",
            access_control=Project.ACCESS_PRIVATE,
            machinery_settings={
                "openrouter": {
                    "key": "synthetic-private-key",
                    "routing": {"pl": "synthetic-model"},
                    "persona": "Before",
                }
            },
        )
        token, _ = self.issue(scope="openid profile wizard.read wizard.import")
        url = "/api/producer/projects/sso-private/translation-profile/"
        headers = {
            "HTTP_AUTHORIZATION": "Bearer " + token["access_token"],
            "secure": True,
        }
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"openrouter": {"persona": "Before"}})
        response = self.client.patch(
            url,
            {"service": "openrouter", "configuration": {"persona": "After"}},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(
            project.machinery_settings["openrouter"]["key"], "synthetic-private-key"
        )
        self.assertEqual(project.machinery_settings["openrouter"]["persona"], "After")
        response = self.client.patch(
            url,
            {"service": "openrouter", "configuration": {"key": "forbidden"}},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 400)
        self.user = User.objects.create_user(
            username="sso-bob",
            email="sso-bob@example.com",
            password="synthetic-test-password",
        )
        other, _ = self.issue()
        response = self.client.get(
            url,
            headers={"authorization": "Bearer " + other["access_token"]},
            secure=True,
        )
        self.assertEqual(response.status_code, 404)
