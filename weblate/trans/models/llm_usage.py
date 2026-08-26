# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from decimal import Decimal


class LLMUsageLog(models.Model):
    """
    One record per LLM chat-completions request.

    Written at the single seam where the response body is parsed
    (``BaseOpenAITranslation.fetch_llm_translations``), so the token counts and
    the cost are exactly what OpenRouter billed, not a local price-table
    estimate. ``cost_usd`` is null when the provider reports no cost (observed
    for the gpt-5.4 tiers); tokens are always stored so the cost can be
    reconstructed from the OpenRouter price list later.

    ``batch_size`` and ``outcome`` make the price of a run readable without the
    provider dashboard. A reply the validator refuses is billed exactly like an
    accepted one, and the machinery answers a refusal by re-asking the batch in
    halves, so a contract regression shows up only as a request count: strings
    per request collapses towards one while the fixed prompt prefix is paid
    again for every retry.
    """

    class Operation(models.TextChoices):
        TRANSLATION = "translation"
        JUDGE = "judge"

    class Outcome(models.TextChoices):
        APPLIED = "applied", "applied"
        PARTIAL = "partial", "partial"
        REFUSED = "refused", "refused"

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
    operation = models.CharField(
        max_length=20, choices=Operation, blank=True, db_index=True
    )
    unit_count = models.PositiveIntegerField(null=True, blank=True)
    #: Strings asked for in this request, so a cascade of halved retries is
    #: visible as a falling strings-per-request ratio.
    batch_size = models.IntegerField(default=0)
    #: How the reply was resolved. Blank when the request never reached
    #: validation, for instance when the transport failed after billing.
    outcome = models.CharField(
        max_length=16, choices=Outcome, blank=True, db_index=True
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.model} {self.total_tokens} tokens ${self.cost_usd}"


def recent_cost_range(
    project_slug: str, model: str, operation: str
) -> tuple[Decimal, Decimal] | None:
    """
    Observed per-unit cost range for the newest priced requests.

    Exact project/model/operation match, newest 20 rows with a stored cost
    and a positive unit count. Below 5 samples returns None so a thin
    history never implies a precision the data does not support.
    """
    per_unit = [
        cost / unit_count
        for cost, unit_count in LLMUsageLog.objects.filter(
            project_slug=project_slug,
            model=model,
            operation=operation,
            cost_usd__isnull=False,
            unit_count__gt=0,
        )
        .order_by("-created_at", "-pk")[:20]
        .values_list("cost_usd", "unit_count")
    ]
    if len(per_unit) < 5:
        return None
    return min(per_unit), max(per_unit)
