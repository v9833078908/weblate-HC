# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from oauth2_provider.oauth2_validators import OAuth2Validator

from weblate.wizard_auth.grants import checksum, family_for, subject_for
from weblate.wizard_auth.models import DelegationFamily, TokenBinding


class WizardValidator(OAuth2Validator):
    """A single registered client, immutable subject, absolute grant lifetime."""

    def validate_client_id(self, client_id, request, *args, **kwargs):
        return (
            client_id == settings.WIZARD_SSO_CLIENT_ID
            and super().validate_client_id(client_id, request, *args, **kwargs)
        )

    def validate_response_type(
        self, client_id, response_type, client, request, *args, **kwargs
    ):
        return bool(
            client_id == settings.WIZARD_SSO_CLIENT_ID
            and response_type == "code"
            and getattr(request, "nonce", None)
            and getattr(request, "code_challenge_method", None) == "S256"
            and client.client_type == "confidential"
            and client.algorithm == "RS256"
            and super().validate_response_type(
                client_id, response_type, client, request, *args, **kwargs
            )
        )

    def validate_grant_type(
        self, client_id, grant_type, client, request, *args, **kwargs
    ):
        return (
            client_id == settings.WIZARD_SSO_CLIENT_ID
            and grant_type in {"authorization_code", "refresh_token"}
            and client.client_type == "confidential"
            and client.algorithm == "RS256"
            and super().validate_grant_type(
                client_id, grant_type, client, request, *args, **kwargs
            )
        )

    def get_additional_claims(self, request):
        return {
            "sub": subject_for(request.user),
            "preferred_username": request.user.username,
            "name": request.user.full_name,
        }

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        family = family_for(refresh_token, "refresh")
        if (
            not family
            or family.client_id != client.client_id
            or not family.user.is_active
        ):
            return False
        valid = super().validate_refresh_token(
            refresh_token, client, request, *args, **kwargs
        )
        if not valid:
            DelegationFamily.objects.filter(pk=family.pk).update(revoked=True)
        return valid

    def _save_bearer_token(self, token, request, *args, **kwargs):
        # Called inside the toolkit's atomic issuance transaction.
        family = None
        old_refresh = getattr(request, "refresh_token", None)
        if request.grant_type == "refresh_token":
            family = family_for(old_refresh, "refresh")
        if family is None:
            if request.grant_type != "authorization_code" or not request.user.is_active:
                msg = "Invalid Wizard delegation"
                raise ValueError(msg)
            family = DelegationFamily.objects.create(
                user=request.user,
                client_id=request.client.client_id,
                expires=timezone.now() + timedelta(hours=24),
            )
        family = DelegationFamily.objects.select_for_update().get(pk=family.pk)
        if family.revoked or family.expires <= timezone.now():
            msg = "Expired Wizard delegation"
            raise ValueError(msg)
        super()._save_bearer_token(token, request, *args, **kwargs)
        for field, kind in (("access_token", "access"), ("refresh_token", "refresh")):
            if token.get(field):
                TokenBinding.objects.get_or_create(
                    checksum=checksum(token[field]),
                    defaults={"family": family, "kind": kind},
                )

    def revoke_token(self, token, token_type_hint, request, *args, **kwargs):
        binding = TokenBinding.objects.filter(checksum=checksum(token)).first()
        if binding and binding.family.client_id == request.client.client_id:
            DelegationFamily.objects.filter(pk=binding.family_id).update(revoked=True)
        return super().revoke_token(token, token_type_hint, request, *args, **kwargs)
