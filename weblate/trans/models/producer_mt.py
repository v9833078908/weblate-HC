# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import uuid

from django.conf import settings
from django.db import models


def unit_revision(unit) -> str:
    value = {
        "id": unit.pk,
        "source": unit.get_source_plurals(),
        "context": unit.context,
        "target": unit.get_target_plurals(),
        "state": int(unit.state),
    }
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


class ProducerMTOperation(models.Model):
    """Actor-bound acceptance receipt; provider work never belongs to HTTP."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    project = models.ForeignKey("trans.Project", on_delete=models.CASCADE)
    translation = models.ForeignKey("trans.Translation", on_delete=models.CASCADE)
    run = models.OneToOneField("trans.ProducerRun", on_delete=models.CASCADE)
    key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    state = models.CharField(max_length=20, default="accepted")
    lease_owner = models.CharField(max_length=32, blank=True)
    lease_until = models.DateTimeField(null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trans"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["actor", "project", "key"], name="producer_mt_key_unique"
            ),
        ]

    def __str__(self) -> str:
        return str(self.key)


class ProducerMTUnitAttempt(models.Model):
    """A revision can have at most one paid intent, including across request keys."""

    operation = models.ForeignKey(
        ProducerMTOperation, on_delete=models.CASCADE, related_name="units"
    )
    unit = models.ForeignKey("trans.Unit", on_delete=models.CASCADE)
    revision = models.CharField(max_length=64)
    state = models.CharField(max_length=20, default="pending")
    answer = models.TextField(blank=True)
    after_revision = models.CharField(max_length=64, blank=True)

    class Meta:
        app_label = "trans"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["unit", "revision"], name="producer_mt_unit_revision_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit_id}:{self.revision}"
