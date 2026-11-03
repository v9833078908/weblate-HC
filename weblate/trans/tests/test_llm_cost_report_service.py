# Copyright © Weblate contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import hashlib
import io
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from weblate.trans.llm_usage_report import (
    LLMReportDataError,
    LLMReportTemporaryError,
    build_monthly_report,
    collect_usage_rows,
    fetch_litellm_key_spend,
    previous_utc_month,
    render_litellm_spend_csv,
    render_usage_csv,
    send_monthly_llm_usage_report,
)
from weblate.trans.models import LLMUsageLog, Project

START = datetime(2024, 2, 1, tzinfo=UTC)
END = datetime(2024, 3, 1, tzinfo=UTC)


class MonthlyReportTest(TestCase):
    def test_previous_month_includes_leap_day(self):
        self.assertEqual(previous_utc_month(END), (START, END))

    def test_ledger_boundaries_and_unpriced(self):
        row = LLMUsageLog.objects.create(
            service="openrouter",
            model="=model",
            project_slug="old",
            operation="judge",
            cost_usd=None,
        )
        LLMUsageLog.objects.filter(pk=row.pk).update(created_at=START)
        outside = LLMUsageLog.objects.create(
            service="openrouter", model="outside", cost_usd=2
        )
        LLMUsageLog.objects.filter(pk=outside.pk).update(created_at=END)
        rows = collect_usage_rows(START, END)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["cost"])
        self.assertEqual(rows[0]["unpriced"], 1)
        parsed = list(csv.DictReader(io.StringIO(render_usage_csv(rows))))
        self.assertEqual(parsed[0]["model"], "'=model")
        self.assertEqual(parsed[0]["attribution_complete"], "no")

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_spend_contract_and_csv(self):
        key_hash = hashlib.sha256(b"test-key").hexdigest()
        payload = [
            {
                "startTime": "2024-02-29",
                "spend": 1.25,
                "models": {"m": 1.25},
                key_hash: 1.25,
            },
            {"startTime": "2024-03-01", "spend": 0, "models": {}, key_hash: 0},
        ]

        class Response:
            status_code = 200

            def iter_bytes(self):
                yield json.dumps(payload).encode()

        with patch(
            "weblate.trans.llm_usage_report.stream_validated_url",
            return_value=nullcontext(Response()),
        ) as request:
            spend = fetch_litellm_key_spend(START, END)
        self.assertEqual(spend[0]["cost"], Decimal("1.25"))
        self.assertEqual(request.call_args.kwargs["params"]["api_key"], key_hash)
        self.assertEqual(
            next(csv.DictReader(io.StringIO(render_litellm_spend_csv(spend))))["scope"],
            "entire-key",
        )
        payload[-1]["spend"] = 1
        with (
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                return_value=nullcontext(Response()),
            ),
            self.assertRaises(LLMReportDataError),
        ):
            fetch_litellm_key_spend(START, END)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_spend_rejects_missing_and_other_key_digest(self):
        other = hashlib.sha256(b"other-key").hexdigest()
        payload = [{"startTime": "2024-02-01", "spend": 1, "models": {"m": 1}}]

        class Response:
            status_code = 200

            def iter_bytes(self):
                yield json.dumps(payload).encode()

        for extra in ({}, {other: 1}):
            with self.subTest(extra=extra):
                payload[0].update(extra)
                with (
                    patch(
                        "weblate.trans.llm_usage_report.stream_validated_url",
                        return_value=nullcontext(Response()),
                    ),
                    self.assertRaises(LLMReportDataError),
                ):
                    fetch_litellm_key_spend(START, END)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_zero_spend_validates_present_digest(self):
        digest = hashlib.sha256(b"test-key").hexdigest()
        payload = [{"startTime": "2024-03-01", "spend": 0, "models": {}, digest: 1}]

        class Response:
            status_code = 200

            def iter_bytes(self):
                yield json.dumps(payload).encode()

        with (
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                return_value=nullcontext(Response()),
            ),
            self.assertRaises(LLMReportDataError),
        ):
            fetch_litellm_key_spend(START, END)
        payload[0][digest] = 0
        with patch(
            "weblate.trans.llm_usage_report.stream_validated_url",
            return_value=nullcontext(Response()),
        ):
            self.assertEqual(fetch_litellm_key_spend(START, END), [])

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_spend_does_not_follow_redirects(self):
        class Response:
            status_code = 302

            def iter_bytes(self):
                msg = "Redirect response must not be read"
                raise AssertionError(msg)

        with (
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                return_value=nullcontext(Response()),
            ) as request,
            self.assertRaises(LLMReportDataError),
        ):
            fetch_litellm_key_spend(START, END)
        self.assertFalse(request.call_args.kwargs["follow_redirects"])

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_spend_includes_month_edges_and_rejects_outside_days(self):
        digest = hashlib.sha256(b"test-key").hexdigest()
        payload = [
            {"startTime": "2024-02-01", "spend": 1, "models": {"m": 1}, digest: 1},
            {"startTime": "2024-02-29", "spend": 2, "models": {"m": 2}, digest: 2},
        ]

        class Response:
            status_code = 200

            def iter_bytes(self):
                yield json.dumps(payload).encode()

        with patch(
            "weblate.trans.llm_usage_report.stream_validated_url",
            return_value=nullcontext(Response()),
        ):
            rows = fetch_litellm_key_spend(START, END)
        self.assertEqual([row["cost"] for row in rows], [Decimal(1), Decimal(2)])
        for outside in ("2024-01-31", "2024-03-01", "2024-03-02"):
            payload.append(
                {"startTime": outside, "spend": 1, "models": {"m": 1}, digest: 1}
            )
            with (
                self.subTest(day=outside),
                patch(
                    "weblate.trans.llm_usage_report.stream_validated_url",
                    return_value=nullcontext(Response()),
                ),
                self.assertRaises(LLMReportDataError),
            ):
                fetch_litellm_key_spend(START, END)
            payload.pop()

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_float_rounding_difference_is_explicit_and_reconciles(self):
        digest = hashlib.sha256(b"test-key").hexdigest()
        payload = (
            '[{"startTime":"2024-02-29","spend":1.0000000000000016,'
            '"models":{"m":1.0},"' + digest + '":1.0000000000000016}]'
        )

        class Response:
            status_code = 200

            def iter_bytes(self):
                yield payload.encode()

        with patch(
            "weblate.trans.llm_usage_report.stream_validated_url",
            return_value=nullcontext(Response()),
        ):
            rows = fetch_litellm_key_spend(START, END)
        csv_rows = list(csv.DictReader(io.StringIO(render_litellm_spend_csv(rows))))
        self.assertEqual(csv_rows[1]["row_type"], "reconciliation-difference")
        self.assertEqual(csv_rows[1]["scope"], "entire-key")
        self.assertEqual(
            sum(Decimal(row["cost_usd"]) for row in csv_rows),
            Decimal("1.0000000000000016"),
        )
        payload = payload.replace("1.0000000000000016", "0.9999999999999984")
        with patch(
            "weblate.trans.llm_usage_report.stream_validated_url",
            return_value=nullcontext(Response()),
        ):
            negative = fetch_litellm_key_spend(START, END)
        csv_negative = list(
            csv.DictReader(io.StringIO(render_litellm_spend_csv(negative)))
        )
        self.assertLess(Decimal(csv_negative[1]["cost_usd"]), 0)
        self.assertEqual(
            sum(Decimal(row["cost_usd"]) for row in csv_negative),
            Decimal("0.9999999999999984"),
        )
        payload = payload.replace("0.9999999999999984", "1.01")
        with (
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                return_value=nullcontext(Response()),
            ),
            self.assertRaises(LLMReportDataError),
        ):
            fetch_litellm_key_spend(START, END)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_SUBJECT_PREFIX="[WL] ",
        LLM_COST_REPORT_RECIPIENTS=["report@example.com"],
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_email_uses_bcc_and_two_attachments(self):
        with patch(
            "weblate.trans.llm_usage_report.fetch_litellm_key_spend",
            return_value=[{"day": START.date(), "model": "m", "cost": Decimal("1.0")}],
        ):
            report = build_monthly_report(START, END)
        send_monthly_llm_usage_report(START, END, report=report)
        message = mail.outbox[0]
        self.assertEqual(message.to, [])
        self.assertEqual(message.bcc, ["report@example.com"])
        self.assertTrue(message.subject.startswith("[WL] "))
        parts = list(message.message().walk())
        csv_parts = [part for part in parts if part.get_content_type() == "text/csv"]
        self.assertEqual(len(csv_parts), 2)
        inline = {
            part["Content-ID"]
            for part in parts
            if part.get_content_disposition() == "inline"
        }
        self.assertEqual(
            inline,
            {
                "<email-logo.png@cid.weblate.org>",
                "<email-logo-footer.png@cid.hcgameloc>",
            },
        )
        self.assertEqual(len(message.alternatives), 1)
        html = message.alternatives[0].content
        self.assertIn('lang="ru"', html)
        self.assertIn("cid:email-logo.png@cid.weblate.org", html)
        self.assertIn("cid:email-logo-footer.png@cid.hcgameloc", html)
        self.assertNotIn("report@example.com", message.body)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="test-key",
    )
    def test_stream_exceeding_total_deadline_is_retryable(self):
        class Response:
            status_code = 200

            def iter_bytes(self):
                yield b"[]"

        with (
            patch("weblate.trans.llm_usage_report.time.monotonic", side_effect=[0, 31]),
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                return_value=nullcontext(Response()),
            ),
            self.assertRaises(LLMReportTemporaryError),
        ):
            fetch_litellm_key_spend(START, END)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="secret",
    )
    def test_no_data_and_oversized_response_fail_closed(self):
        class Response:
            status_code = 200

            def __init__(self, body):
                self.body = body

            def iter_bytes(self):
                yield self.body

        for body in (b"[]", b"{", b"x" * (2 * 1024 * 1024 + 1)):
            with (
                self.subTest(size=len(body)),
                patch(
                    "weblate.trans.llm_usage_report.stream_validated_url",
                    return_value=nullcontext(Response(body)),
                ),
                self.assertRaises(LLMReportDataError),
            ):
                fetch_litellm_key_spend(START, END)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_API_KEY="secret",
    )
    def test_network_errors_do_not_include_secrets(self):
        with (
            patch(
                "weblate.trans.llm_usage_report.stream_validated_url",
                side_effect=OSError("secret https://example.invalid/?key=secret"),
            ),
            self.assertRaises(LLMReportTemporaryError) as caught,
        ):
            fetch_litellm_key_spend(START, END)
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_unknown_project_does_not_merge_with_known_id(self):
        project = Project.objects.create(name="Report project", slug="report-project")
        for identity in (None, project.pk):
            row = LLMUsageLog.objects.create(
                service="openrouter",
                model="m",
                project_slug="report-project",
                project_id_snapshot=identity,
                cost_usd=Decimal(1),
            )
            LLMUsageLog.objects.filter(pk=row.pk).update(created_at=START)
        with patch(
            "weblate.trans.llm_usage_report.fetch_litellm_key_spend", return_value=[]
        ):
            report = build_monthly_report(START, END)
        self.assertEqual(len(report.context["projects"]), 2)
        self.assertEqual(report.context["openrouter_total"], "$2.00")

    def test_project_rename_and_delete_preserve_snapshot_identity(self):
        project = Project.objects.create(name="Report project", slug="old-name")
        identity = project.pk
        usage = LLMUsageLog.objects.create(
            service="openrouter",
            model="m",
            project_id_snapshot=identity,
            project_slug="old-name",
            cost_usd=Decimal(1),
        )
        LLMUsageLog.objects.filter(pk=usage.pk).update(created_at=START)
        project.slug = "new-name"
        project.save(update_fields=["slug"])
        row = collect_usage_rows(START, END)[0]
        self.assertEqual(row["project_id_snapshot"], identity)
        self.assertEqual(row["project"], "new-name")
        self.assertEqual(row["project_slug"], "old-name")
        project.delete()
        row = collect_usage_rows(START, END)[0]
        self.assertEqual(row["project_id_snapshot"], identity)
        self.assertEqual(row["project"], "old-name")
        self.assertEqual(row["cost"], Decimal(1))

    def test_known_totals_and_unmatched_models(self):
        common = {
            "project_id_snapshot": 1,
            "project_slug": "game",
            "component_id_snapshot": 2,
            "component_slug": "ui",
            "target_language_code": "ru",
            "project": "game",
            "component": "ui",
            "attribution_complete": True,
            "requests": 1,
            "strings": 1,
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "unpriced": 0,
        }
        rows = [
            {
                **common,
                "service": "openrouter",
                "operation": "translation",
                "model": "or-model",
                "cost": Decimal("182.4435373775475"),
            },
            {
                **common,
                "service": "litellm",
                "operation": "judge",
                "model": "judge-only",
                "cost": None,
                "prompt": 100000000,
                "completion": 11749402,
                "total": 111749402,
            },
        ]
        spend = [
            {"day": START.date(), "model": "key-only", "cost": Decimal("19.2547279295")}
        ]
        with (
            patch(
                "weblate.trans.llm_usage_report.collect_usage_rows", return_value=rows
            ),
            patch(
                "weblate.trans.llm_usage_report.fetch_litellm_key_spend",
                return_value=spend,
            ),
        ):
            report = build_monthly_report(START, END)
        self.assertEqual(report.context["openrouter_total"], "$182.44")
        self.assertEqual(report.context["judge_total"], 111749402)
        self.assertEqual(report.context["key_total"], "$19.25")
        models = {row["name"]: row for row in report.context["key_models"]}
        self.assertEqual(models["judge-only"]["cost"], "—")
        self.assertEqual(models["key-only"]["total"], "—")

    def test_verified_aliases_combine_only_exact_models(self):
        common = {
            "service": "litellm",
            "operation": "judge",
            "prompt": 3,
            "completion": 2,
            "total": 5,
        }
        usage = [
            {**common, "model": "deepseek-v4-pro"},
            {**common, "model": "atlas/qwen3.8-max"},
            {**common, "model": "prefix/deepseek-v4-pro"},
        ]
        spend = [
            {
                "day": START.date(),
                "model": "openai/deepseek-ai/deepseek-v4-pro",
                "cost": Decimal(1),
            },
            {"day": START.date(), "model": "qwen/qwen3.8-max", "cost": Decimal(2)},
        ]
        with (
            patch(
                "weblate.trans.llm_usage_report.collect_usage_rows", return_value=usage
            ),
            patch(
                "weblate.trans.llm_usage_report.fetch_litellm_key_spend",
                return_value=spend,
            ),
        ):
            report = build_monthly_report(START, END)
        models = {row["name"]: row for row in report.context["key_models"]}
        self.assertEqual(models["deepseek-v4-pro"]["cost"], "$1.00")
        self.assertEqual(models["atlas/qwen3.8-max"]["cost"], "$2.00")
        self.assertEqual(models["deepseek-v4-pro"]["total"], 5)
        self.assertEqual(models["atlas/qwen3.8-max"]["total"], 5)
        self.assertEqual(models["prefix/deepseek-v4-pro"]["cost"], "—")
        self.assertEqual(len(models), 3)
        self.assertEqual(
            [
                row["model"]
                for row in csv.DictReader(io.StringIO(render_litellm_spend_csv(spend)))
            ],
            ["openai/deepseek-ai/deepseek-v4-pro", "qwen/qwen3.8-max"],
        )

    def test_csv_escapes_formula_prefixes(self):
        rows = [
            {"day": START.date(), "model": name, "cost": Decimal(1)}
            for name in ("=x", "+x", "-x", "@x")
        ]
        result = list(csv.DictReader(io.StringIO(render_litellm_spend_csv(rows))))
        self.assertEqual([row["model"] for row in result], ["'=x", "'+x", "'-x", "'@x"])
