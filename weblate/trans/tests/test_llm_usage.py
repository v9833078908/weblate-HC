# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from weblate.trans.models.llm_usage import (
    LLMUsageLog,
    parse_provider_cost,
    recent_cost_range,
)
from weblate.trans.tests.test_views import ComponentTestCase


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
        field = LLMUsageLog._meta.get_field(  # ruff: ignore[private-member-access]
            "cost_usd"
        )
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
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            batch_size=10,
            cost_usd=Decimal("0.001000000000000001"),
        )
        LLMUsageLog.objects.create(
            model="m1",
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=4,
            completion_tokens=1,
            total_tokens=5,
            batch_size=5,
        )
        LLMUsageLog.objects.create(
            model="m3",
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
            batch_size=2,
            cost_usd=Decimal("0.000000000123456789"),
        )
        LLMUsageLog.objects.create(
            model="m2",
            service="litellm",
            project_id_snapshot=3,
            project_slug="st2",
            component_id_snapshot=4,
            component_slug="hub-1",
            target_language_code="de",
            operation=LLMUsageLog.Operation.JUDGE,
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            batch_size=2,
            cost_usd=Decimal("0.000000500000000000"),
        )

    def test_csv_report_groups_by_service_component_and_language(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--format", "csv", stdout=out)
        rows = list(csv.reader(out.getvalue().strip().splitlines()))

        self.assertEqual(
            rows[0],
            [
                "service",
                "model",
                "project",
                "component",
                "target_language",
                "operation",
                "requests",
                "strings_asked",
                "prompt_tokens",
                "completion_tokens",
                "cost_usd",
                "unpriced",
                "priced_complete",
                "unattributed_requests",
                "attribution_complete",
            ],
        )
        self.assertEqual(
            rows[1],
            [
                "litellm",
                "m2",
                "st2",
                "hub-1",
                "de",
                "judge",
                "1",
                "2",
                "7",
                "3",
                "0.000000500000000000",
                "0",
                "yes",
                "",
                "",
            ],
        )

    def test_summary_reports_pricing_and_scope_completeness(self) -> None:
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(
            rows[1],
            [
                "openrouter",
                "*",
                "*",
                "*",
                "*",
                "translation",
                "3",
                "17",
                "17",
                "8",
                "0.001000000123456790",
                "1",
                "no",
                "0",
                "yes",
            ],
        )

    def test_summary_marks_blind_scope_unknown(self) -> None:
        LLMUsageLog.objects.create(
            model="m4",
            service="openrouter",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=3,
        )
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(row[-2:], ["1", "unknown"])

    def test_summary_marks_a_fully_priced_total_complete(self) -> None:
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "litellm",
            "--operation",
            "judge",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(
            row[10:],
            ["0.000000500000000000", "0", "yes", "0", "yes"],
        )

    def test_service_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--service", "litellm", stdout=out)
        self.assertIn("hub-1", out.getvalue())
        self.assertNotIn("col4", out.getvalue())

    def test_component_requires_project(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "--component requires an existing --project.",
        ):
            call_command("llm_usage_report", "--component", "ui")

    def test_days_must_be_positive(self) -> None:
        with self.assertRaisesMessage(CommandError, "--days must be at least 1."):
            call_command("llm_usage_report", "--days", "0")

    def test_unattributed_rows_stay_visible(self) -> None:
        LLMUsageLog.objects.create(
            model="m4",
            service="openrouter",
            project_slug="col4",
            prompt_tokens=3,
        )
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--model",
            "m4",
            "--format",
            "csv",
            stdout=out,
        )
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(rows[1][2:6], ["col4", "-", "-", "-"])

    def test_legacy_rows_do_not_block_attribution_complete(self) -> None:
        LLMUsageLog.objects.create(
            model="m5",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=3,
        )
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(row[-2:], ["0", "yes"])


class LLMUsageReportIdentityTest(ComponentTestCase):
    def test_current_slugs_include_cost_recorded_before_rename(self) -> None:
        LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=self.project.pk,
            project_slug=self.project.slug,
            component_id_snapshot=self.component.pk,
            component_slug=self.component.slug,
            target_language_code=self.translation.language.code,
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=1,
            batch_size=1,
            cost_usd=Decimal("0.1"),
        )
        self.project.slug = "renamed-project"
        self.project.save(update_fields=["slug"])
        self.component.slug = "renamed-component"
        self.component.save(update_fields=["slug"])

        out = StringIO()
        call_command(
            "llm_usage_report",
            "--project",
            "renamed-project",
            "--component",
            "renamed-component",
            "--language",
            self.translation.language.code,
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )

        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(
            row[10:],
            ["0.100000000000000000", "0", "yes", "0", "yes"],
        )

    def _scoped_row(self) -> None:
        LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=self.project.pk,
            project_slug=self.project.slug,
            component_id_snapshot=self.component.pk,
            component_slug=self.component.slug,
            target_language_code=self.translation.language.code,
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=1,
            batch_size=1,
            cost_usd=Decimal("0.1"),
        )

    def _summary_row(self) -> list[str]:
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--project",
            self.project.slug,
            "--component",
            self.component.slug,
            "--language",
            self.translation.language.code,
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--days",
            "1",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        return list(csv.reader(out.getvalue().strip().splitlines()))[1]

    def test_another_projects_probe_is_not_a_candidate(self) -> None:
        self._scoped_row()
        LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=self.project.pk + 1000,
            project_slug="other-project",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=1,
        )

        self.assertEqual(self._summary_row()[-2:], ["0", "yes"])

    def test_own_projects_probe_keeps_the_component_unknown(self) -> None:
        self._scoped_row()
        LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=self.project.pk,
            project_slug=self.project.slug,
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=1,
        )

        self.assertEqual(self._summary_row()[-2:], ["1", "unknown"])

    def test_unknown_current_identity_is_rejected(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            'Project "missing" does not exist.',
        ):
            call_command("llm_usage_report", "--project", "missing")
        with self.assertRaisesMessage(
            CommandError,
            'Component "missing" does not exist.',
        ):
            call_command(
                "llm_usage_report",
                "--project",
                self.project.slug,
                "--component",
                "missing",
            )


class RecentCostRangeTest(TestCase):
    def _create(
        self,
        *,
        cost,
        unit_count,
        model="m1",
        project_id_snapshot=1,
        service="openrouter",
        project_slug="col4",
        operation=LLMUsageLog.Operation.TRANSLATION,
    ) -> None:
        LLMUsageLog.objects.create(
            model=model,
            project_id_snapshot=project_id_snapshot,
            project_slug=project_slug,
            service=service,
            operation=operation,
            unit_count=unit_count,
            cost_usd=cost,
            prompt_tokens=1,
        )

    def test_returns_none_below_five_samples(self) -> None:
        for _ in range(4):
            self._create(cost=Decimal("0.001"), unit_count=1)
        self.assertIsNone(
            recent_cost_range(1, "openrouter", "m1", LLMUsageLog.Operation.TRANSLATION)
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
        low, high = recent_cost_range(
            1, "openrouter", "m1", LLMUsageLog.Operation.TRANSLATION
        )
        self.assertEqual(low, Decimal("0.0005"))
        self.assertEqual(high, Decimal("0.005"))

    def test_ignores_null_cost_and_zero_or_null_unit_count(self) -> None:
        for _ in range(5):
            self._create(cost=Decimal("0.001"), unit_count=1)
        self._create(cost=None, unit_count=3)
        self._create(cost=Decimal("0.5"), unit_count=0)
        self._create(cost=Decimal("0.5"), unit_count=None)
        low, high = recent_cost_range(
            1, "openrouter", "m1", LLMUsageLog.Operation.TRANSLATION
        )
        self.assertEqual(low, Decimal("0.001"))
        self.assertEqual(high, Decimal("0.001"))

    def test_uses_only_newest_twenty_rows(self) -> None:
        self._create(cost=Decimal("50.000"), unit_count=1)
        for _ in range(20):
            self._create(cost=Decimal("0.001"), unit_count=1)
        low, high = recent_cost_range(
            1, "openrouter", "m1", LLMUsageLog.Operation.TRANSLATION
        )
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
            self._create(cost=Decimal("9.000"), unit_count=1, project_id_snapshot=2)
        low, high = recent_cost_range(
            1, "openrouter", "m1", LLMUsageLog.Operation.TRANSLATION
        )
        self.assertEqual(low, Decimal("0.001"))
        self.assertEqual(high, Decimal("0.001"))

    def test_never_mixes_service_or_project_identity(self) -> None:
        for _ in range(5):
            self._create(
                cost=Decimal("0.001"),
                unit_count=1,
                project_id_snapshot=1,
                service="openrouter",
            )
            self._create(
                cost=Decimal("9.000"),
                unit_count=1,
                project_id_snapshot=2,
                service="openrouter",
            )
            self._create(
                cost=Decimal("9.000"),
                unit_count=1,
                project_id_snapshot=1,
                service="litellm",
            )

        low, high = recent_cost_range(
            1,
            "openrouter",
            "m1",
            LLMUsageLog.Operation.TRANSLATION,
        )

        self.assertEqual((low, high), (Decimal("0.001"), Decimal("0.001")))
