# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import ClassVar

from django.db import models


class LLMUsageLog(models.Model):
    """
    One record per LLM chat-completions request.

    Written at the single seam where the response body is parsed
    (``BaseOpenAITranslation.fetch_llm_translations``), so the token counts and
    the cost are exactly what OpenRouter billed, not a local price-table
    estimate. ``cost_usd`` is null when the provider reports no cost (observed
    for the gpt-5.4 tiers); tokens are always stored so the cost can be
    reconstructed from the OpenRouter price list later.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    model = models.CharField(max_length=200, db_index=True)
    project_slug = models.CharField(max_length=200, blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(
        max_digits=12, decimal_places=8, null=True, blank=True
    )
    response_id = models.CharField(max_length=255, blank=True)
    cached_tokens = models.IntegerField(default=0)
    reasoning_tokens = models.IntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.model} {self.total_tokens} tokens ${self.cost_usd}"
