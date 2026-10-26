# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Durable, actor-bound recovery receipts for the producer quality API."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class ProducerQualityOperation(models.Model):
    """One atomic local operation; private manifests never leave Weblate."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="producer_quality_operations",
    )
    project = models.ForeignKey(
        "trans.Project", on_delete=models.CASCADE, related_name="quality_operations"
    )
    key = models.UUIDField()
    action = models.CharField(max_length=40)
    request_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default="pending")
    response_status = models.PositiveSmallIntegerField(default=200)
    result = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    private_data = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["actor", "project", "key"],
                name="producer_quality_operation_key_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action}: {self.pk}"
