# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import uuid

from django.db import models


class ProducerLLMExchange(models.Model):
    """Durable outbound intent. Expired leases never prove a request unpaid."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_digest = models.CharField(max_length=64)
    request_codec = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=[
            ("", "Unclassified"),
            ("opaque-v1", "Opaque v1"),
            ("routed-parts-v1", "Routed parts v1"),
            ("routed-slots-v2", "Routed slots v2"),
            ("routed-slots-v3", "Routed slots v3"),
            ("routed-slots-v4", "Routed slots v4"),
        ],
    )
    state = models.CharField(max_length=20, default="reserved")
    owner = models.CharField(max_length=32)
    lease_until = models.DateTimeField()
    response_path = models.CharField(max_length=200, blank=True)
    response_digest = models.CharField(max_length=64, blank=True)
    response_id = models.CharField(max_length=200, blank=True)
    usage_recorded = models.BooleanField(default=False)
    judge_attempt = models.OneToOneField(
        "trans.JudgeRequestAttempt",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivery_exchange",
    )
    #: The audited fallback authorization this exchange was reserved for.
    mt_authorization = models.ForeignKey(
        "trans.ProducerMTFallbackAuthorization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="exchanges",
    )
    mt_stage = models.CharField(
        max_length=20,
        choices=[
            ("primary", "Primary"),
            ("fallback", "Fallback"),
        ],
        blank=True,
        default="",
    )
    #: Private, secret-free snapshot of the request actually sent: method, URL,
    #: payload, codec and occurrence mapping. Never contains auth headers or a
    #: key; it is the same private class of data as the raw reply.
    request_snapshot = models.JSONField(null=True, blank=True)
    run = models.ForeignKey(
        "trans.ProducerRun", null=True, blank=True, on_delete=models.PROTECT
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trans"

    def __str__(self) -> str:
        return str(self.pk)


class ProducerLLMWork(models.Model):
    """Permanent semantic spend fence, independent of model/request/chunk keys."""

    semantic_key = models.CharField(max_length=64, unique=True)
    identity = models.CharField(max_length=500)
    position = models.PositiveSmallIntegerField()
    exchange = models.ForeignKey(
        ProducerLLMExchange, on_delete=models.PROTECT, related_name="work"
    )

    class Meta:
        app_label = "trans"

    def __str__(self) -> str:
        return self.semantic_key
