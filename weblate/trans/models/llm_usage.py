# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db import models

from weblate.trans.defines import COMPONENT_NAME_LENGTH, LANGUAGE_CODE_LENGTH

COST_USD_MAX_DIGITS = 24
COST_USD_DECIMAL_PLACES = 18
_cost_usd_validator = DecimalValidator(
    max_digits=COST_USD_MAX_DIGITS,
    decimal_places=COST_USD_DECIMAL_PLACES,
)


class LLMUsageLog(models.Model):
    """
    One record per LLM chat-completions request.

    Written at the raw-response seam for OpenAI-compatible, LiteLLM, and judge
    requests. Token counts and cost are the provider's reported values, not a
    local price-table estimate. ``cost_usd`` is null when the provider reports
    no cost or reports a value that cannot be stored without rounding.

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
    #: Stable machinery or judge endpoint ID, for example ``openrouter`` or
    #: ``litellm``. Machinery derives it from its class; judge derives it from
    #: a recognized endpoint host and stores ``unknown`` for other hosts. Blank
    #: is a row written before this migration.
    service = models.CharField(max_length=200, blank=True)
    #: Immutable identities used for current-slug report filters. They are
    #: deliberately scalar snapshots, not FKs: a deleted component must not
    #: rewrite the historical financial row.
    project_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    component_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    #: Human-readable labels at billing time, retained through rename/delete.
    component_slug = models.CharField(max_length=COMPONENT_NAME_LENGTH, blank=True)
    target_language_code = models.CharField(
        max_length=LANGUAGE_CODE_LENGTH, blank=True
    )
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(
        max_digits=COST_USD_MAX_DIGITS,
        decimal_places=COST_USD_DECIMAL_PLACES,
        null=True,
        blank=True,
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
    request_attempt = models.ForeignKey(
        "trans.JudgeRequestAttempt",
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_logs",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["operation", "-created_at"],
                name="llm_usage_operation_recent_idx",
            ),
            models.Index(
                fields=["request_attempt", "-created_at"],
                name="llm_usage_attempt_recent_idx",
            ),
            models.Index(
                fields=[
                    "project_id_snapshot",
                    "component_id_snapshot",
                    "target_language_code",
                    "service",
                    "operation",
                    "-created_at",
                ],
                name="llm_usage_scope_recent_idx",
            ),
        ]

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
def parse_provider_cost(value: object) -> Decimal | None:
    """Return a cost that the ledger can store without rounding."""
    if value is None:
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not cost.is_finite() or cost < 0:
        return None
    for candidate in (cost, cost.normalize()):
        try:
            _cost_usd_validator(candidate)
        except ValidationError:
            continue
        return candidate
    return None
