# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class SubjectIdentity(models.Model):
    """Random non-reusable OIDC identity independent of login spelling."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self) -> str:
        return str(self.subject)


class DelegationFamily(models.Model):
    """Absolute authorization lifetime survives refresh-token rotation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=255)
    expires = models.DateTimeField()
    revoked = models.BooleanField(default=False)

    def __str__(self) -> str:
        return str(self.pk)


class TokenBinding(models.Model):
    checksum = models.CharField(max_length=64, primary_key=True)
    family = models.ForeignKey(DelegationFamily, on_delete=models.CASCADE)
    kind = models.CharField(max_length=8)

    def __str__(self) -> str:
        return str(self.family_id)
