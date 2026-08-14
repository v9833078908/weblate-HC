# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from weblate.trans.models.llm_usage import LLMUsageLog


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
