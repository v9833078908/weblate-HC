# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from weblate.trans.models.llm_usage import (
    LLMUsageLog,
    parse_provider_cost,
    recent_cost_range,
)


class LLMUsageLogModelTest(TestCase):
    def test_create_with_cost(self) -> None:
        log = LLMUsageLog.objects.create(
            model="google/gemini-2.5-flash",
            project_slug="col4",
            prompt_tokens=9,
            completion_tokens=12,
            total_tokens=21,
            cost_usd=Decimal("0.00001234"),
            response_id="chatcmpl-123",
            cached_tokens=4,
        )
        self.assertEqual(log.total_tokens, 21)
        self.assertEqual(log.cost_usd, Decimal("0.00001234"))
        self.assertEqual(log.reasoning_tokens, 0)

    def test_create_unpriced(self) -> None:
        log = LLMUsageLog.objects.create(model="gpt-5.4-nano", prompt_tokens=5)
        self.assertIsNone(log.cost_usd)
        self.assertEqual(log.project_slug, "")
        self.assertEqual(log.completion_tokens, 0)

    def test_create_with_operation_and_unit_count(self) -> None:
        log = LLMUsageLog.objects.create(
            model="google/gemini-2.5-flash",
            operation=LLMUsageLog.Operation.JUDGE,
            unit_count=5,
            prompt_tokens=9,
        )
        self.assertEqual(log.operation, LLMUsageLog.Operation.JUDGE)
        self.assertEqual(log.unit_count, 5)

    def test_operation_and_unit_count_default_blank(self) -> None:
        log = LLMUsageLog.objects.create(model="gpt-5.4-nano", prompt_tokens=5)
        self.assertEqual(log.operation, "")
        self.assertIsNone(log.unit_count)

    def test_attribution_defaults_blank(self) -> None:
        log = LLMUsageLog.objects.create(model="m", prompt_tokens=1)
        self.assertEqual(log.service, "")
        self.assertIsNone(log.project_id_snapshot)
        self.assertIsNone(log.component_id_snapshot)
        self.assertEqual(log.component_slug, "")
        self.assertEqual(log.target_language_code, "")

    def test_attribution_fields_are_stored(self) -> None:
        log = LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=7,
            project_slug="need-for-greed",
            component_id_snapshot=8,
            component_slug="ui",
            target_language_code="fr",
            prompt_tokens=1,
        )
        log.refresh_from_db()
        self.assertEqual(log.service, "openrouter")
        self.assertEqual(log.project_id_snapshot, 7)
        self.assertEqual(log.component_id_snapshot, 8)
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")

    def test_cost_preserves_provider_precision(self) -> None:
        cost = Decimal("0.123456789123456789")
        log = LLMUsageLog.objects.create(
            model="m",
            prompt_tokens=1,
            cost_usd=cost,
        )
        log.refresh_from_db()
        self.assertEqual(log.cost_usd, cost)
        field = LLMUsageLog._meta.get_field("cost_usd")
        self.assertEqual((field.max_digits, field.decimal_places), (24, 18))

    def test_cost_beyond_supported_scale_is_unpriced(self) -> None:
        self.assertIsNone(parse_provider_cost(Decimal("0.1234567891234567891")))

    def test_negative_cost_is_unpriced(self) -> None:
        self.assertIsNone(parse_provider_cost(Decimal("-0.01")))

    def test_cost_with_padded_zeros_is_stored(self) -> None:
        self.assertEqual(
            parse_provider_cost(Decimal("0.000010000000000000000000")),
            Decimal("0.00001"),
        )


class LLMUsageReportTest(TestCase):
    def setUp(self) -> None:
        LLMUsageLog.objects.create(
            model="m1",
            project_slug="col4",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=Decimal("0.001"),
        )
        LLMUsageLog.objects.create(
            model="m1",
            project_slug="col4",
            prompt_tokens=4,
            completion_tokens=1,
            total_tokens=5,
        )
        LLMUsageLog.objects.create(
            model="m2",
            project_slug="st2",
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            cost_usd=Decimal("0.0000005"),
        )

    def test_table_report(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", stdout=out)
        text = out.getvalue()
        self.assertIn("m1", text)
        self.assertIn("col4", text)
        self.assertIn("14", text)  # prompt sum for m1
        self.assertIn("0.001", text)
        self.assertIn("unpriced", text)

    def test_csv_report(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--format", "csv", stdout=out)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(
            lines[0],
            "model,project,requests,prompt_tokens,completion_tokens,cost_usd,unpriced",
        )
        self.assertEqual(len(lines), 3)

    def test_days_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--days", "1", stdout=out)
        self.assertIn("m1", out.getvalue())

    def test_model_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--model", "m2", stdout=out)
        text = out.getvalue()
        self.assertNotIn("m1", text)
        self.assertIn("st2", text)


class RecentCostRangeTest(TestCase):
    def _create(
        self,
        *,
        cost,
        unit_count,
        model="m1",
        project_slug="col4",
        operation=LLMUsageLog.Operation.TRANSLATION,
    ) -> None:
        LLMUsageLog.objects.create(
            model=model,
            project_slug=project_slug,
            operation=operation,
            unit_count=unit_count,
            cost_usd=cost,
            prompt_tokens=1,
        )

    def test_returns_none_below_five_samples(self) -> None:
        for _ in range(4):
            self._create(cost=Decimal("0.001"), unit_count=1)
        self.assertIsNone(
            recent_cost_range("col4", "m1", LLMUsageLog.Operation.TRANSLATION)
        )

    def test_returns_min_max_per_unit_at_five_samples(self) -> None:
        costs = [
            Decimal("0.001"),
            Decimal("0.002"),
            Decimal("0.003"),
            Decimal("0.004"),
            Decimal("0.010"),
        ]
        for cost in costs:
            self._create(cost=cost, unit_count=2)
        low, high = recent_cost_range("col4", "m1", LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(low, Decimal("0.0005"))
        self.assertEqual(high, Decimal("0.005"))

    def test_ignores_null_cost_and_zero_or_null_unit_count(self) -> None:
        for _ in range(5):
            self._create(cost=Decimal("0.001"), unit_count=1)
        self._create(cost=None, unit_count=3)
        self._create(cost=Decimal("0.5"), unit_count=0)
        self._create(cost=Decimal("0.5"), unit_count=None)
        low, high = recent_cost_range("col4", "m1", LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(low, Decimal("0.001"))
        self.assertEqual(high, Decimal("0.001"))

    def test_uses_only_newest_twenty_rows(self) -> None:
        self._create(cost=Decimal("50.000"), unit_count=1)
        for _ in range(20):
            self._create(cost=Decimal("0.001"), unit_count=1)
        low, high = recent_cost_range("col4", "m1", LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(low, Decimal("0.001"))
        self.assertEqual(high, Decimal("0.001"))

    def test_never_mixes_operation_model_or_project(self) -> None:
        for _ in range(5):
            self._create(cost=Decimal("0.001"), unit_count=1)
        for _ in range(5):
            self._create(
                cost=Decimal("9.000"),
                unit_count=1,
                operation=LLMUsageLog.Operation.JUDGE,
            )
        for _ in range(5):
            self._create(cost=Decimal("9.000"), unit_count=1, model="m2")
        for _ in range(5):
            self._create(cost=Decimal("9.000"), unit_count=1, project_slug="st2")
        low, high = recent_cost_range("col4", "m1", LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(low, Decimal("0.001"))
        self.assertEqual(high, Decimal("0.001"))
