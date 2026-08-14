# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal

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
