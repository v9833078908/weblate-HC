# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from weblate.trans.llm_usage_report import LLMReportTemporaryError
from weblate.trans.tasks import (
    _report_llm_cost_failure,
    send_llm_cost_report,
    setup_periodic_tasks,
)


@override_settings(LLM_COST_REPORT_RECIPIENTS=["recipient@example.com"])
class CostReportScheduleTest(SimpleTestCase):
    def run_task(self, now, *, expires=None, retries=0, month=None):
        send_llm_cost_report.push_request(
            expires=expires.isoformat() if expires else None, retries=retries
        )
        try:
            with patch("weblate.trans.tasks.timezone.now", return_value=now):
                return send_llm_cost_report.run(month=month)
        finally:
            send_llm_cost_report.pop_request()

    def test_schedule_is_utc_with_expiry_and_notify_route(self):
        sender = Mock()
        setup_periodic_tasks(sender)
        entry = next(
            c
            for c in sender.add_periodic_task.call_args_list
            if c.kwargs.get("name") == "monthly-llm-cost-report"
        )
        schedule = entry.args[0]
        self.assertEqual(str(schedule.tz), "UTC")
        self.assertEqual(schedule.hour, {6})
        self.assertEqual(schedule.day_of_month, {1})
        self.assertEqual(entry.kwargs["expire_seconds"], 48 * 3600)
        self.assertEqual(
            settings.CELERY_TASK_ROUTES["weblate.trans.tasks.send_llm_cost_report"],
            {"queue": "notify"},
        )

    @override_settings(LLM_COST_REPORT_RECIPIENTS=[])
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_disabled_stale_delivery_does_not_collect(self, build):
        self.run_task(datetime(2027, 3, 1, tzinfo=UTC))
        build.assert_not_called()

    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_fresh_delivery_uses_leap_february(self, build, send):
        now = datetime(2028, 3, 1, 6, tzinfo=UTC)
        self.run_task(now, expires=now + timedelta(hours=48))
        start, end = build.call_args.args
        self.assertEqual(start, datetime(2028, 2, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2028, 3, 1, tzinfo=UTC))
        send.assert_called_once_with(start, end, report=build.return_value)

    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_expired_or_unverifiable_delivery_is_rejected(self, build):
        now = datetime(2027, 3, 1, 6, tzinfo=UTC)
        with patch("weblate.trans.tasks._report_llm_cost_failure"):
            self.run_task(now)
            self.run_task(now, expires=now - timedelta(seconds=1))
        build.assert_not_called()

    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_retry_carries_month_and_never_sends_on_collection_error(self, build, send):
        now = datetime(2027, 2, 1, 6, tzinfo=UTC)
        build.side_effect = LLMReportTemporaryError("unavailable")
        with (
            patch.object(send_llm_cost_report, "retry", side_effect=Retry()) as retry,
            self.assertRaises(Retry),
        ):
            self.run_task(now, expires=now + timedelta(hours=48))
        self.assertEqual(retry.call_args.kwargs["kwargs"], {"month": "2027-01"})
        send.assert_not_called()

    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_retry_keeps_original_month(self, build, send):
        published = datetime(2027, 2, 1, 6, tzinfo=UTC)
        self.run_task(
            published + timedelta(hours=30),
            expires=published + timedelta(hours=48),
            retries=1,
            month="2027-01",
        )
        self.assertEqual(
            build.call_args.args,
            (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)),
        )
        send.assert_called_once()

    @patch(
        "weblate.trans.llm_usage_report.send_monthly_llm_usage_report",
        side_effect=OSError("secret"),
    )
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_smtp_failure_never_retries(self, build, send):
        now = datetime(2027, 2, 1, 6, tzinfo=UTC)
        with (
            patch.object(send_llm_cost_report, "retry") as retry,
            patch("weblate.trans.tasks._report_llm_cost_failure") as report,
        ):
            self.run_task(now, expires=now + timedelta(hours=48))
        retry.assert_not_called()
        report.assert_called_once()
        self.assertNotIn("secret", str(report.call_args))

    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_retry_month_mismatch_and_late_publication_fail_closed(self, build):
        published = datetime(2027, 2, 1, 6, tzinfo=UTC)
        with patch("weblate.trans.tasks._report_llm_cost_failure"):
            self.run_task(
                published,
                expires=published + timedelta(hours=48),
                retries=1,
                month="2026-12",
            )
            self.run_task(
                published + timedelta(minutes=2),
                expires=published + timedelta(hours=48, minutes=2),
            )
        build.assert_not_called()

    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_fresh_redelivery_is_not_deduplicated(self, build, send):
        published = datetime(2027, 2, 1, 6, tzinfo=UTC)
        expires = published + timedelta(hours=48)
        self.run_task(published, expires=expires)
        self.run_task(published + timedelta(minutes=1), expires=expires)
        self.assertEqual(send.call_count, 2)

    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_exhausted_collection_failure_notifies_without_delivery(self, build, send):
        published = datetime(2027, 2, 1, 6, tzinfo=UTC)
        build.side_effect = LLMReportTemporaryError("secret")
        with patch("weblate.trans.tasks._report_llm_cost_failure") as notify:
            self.run_task(
                published,
                expires=published + timedelta(hours=48),
                retries=3,
                month="2027-01",
            )
        send.assert_not_called()
        self.assertNotIn("secret", str(notify.call_args))

    @patch("weblate.trans.tasks.mail_admins", side_effect=OSError("secret"))
    @patch("weblate.trans.tasks.report_error")
    @patch("weblate.trans.tasks.LOGGER.error")
    def test_administrator_mail_failure_logs_without_exception(
        self, logger, report, mail
    ):
        try:
            Mock(side_effect=OSError("provider-secret"))()
        except OSError:
            _report_llm_cost_failure("Report collection failed.")
        mail.assert_called_once()
        self.assertEqual(report.call_count, 2)
        self.assertNotIn("secret", str(logger.call_args_list))
        self.assertNotIn("secret", str(report.call_args_list))
        for call in report.call_args_list:
            self.assertTrue(call.kwargs["message"])
            self.assertIsInstance(call.kwargs["exception"], RuntimeError)
            self.assertIsNone(call.kwargs["exception"].__context__)


class CostReportCommandTest(SimpleTestCase):
    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_dry_run_collects_without_sending(self, build, send):
        call_command(
            "send_llm_cost_report", "--month", "2020-02", "--dry-run", stdout=StringIO()
        )
        self.assertEqual(
            build.call_args.args,
            (datetime(2020, 2, 1, tzinfo=UTC), datetime(2020, 3, 1, tzinfo=UTC)),
        )
        send.assert_not_called()

    @override_settings(LLM_COST_REPORT_RECIPIENTS=[])
    @patch("weblate.trans.llm_usage_report.send_monthly_llm_usage_report")
    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_test_recipient_excludes_normal_recipients(self, build, send):
        call_command(
            "send_llm_cost_report",
            "--month",
            "2020-01",
            "--test-recipient",
            "test@example.com",
            stdout=StringIO(),
        )
        self.assertEqual(send.call_args.kwargs["recipients"], ["test@example.com"])

    @patch("weblate.trans.llm_usage_report.build_monthly_report")
    def test_invalid_month_and_recipient_rejected_before_collection(self, build):
        for arguments in (
            ("--month", "2020-1"),
            ("--month", "9999-12"),
            ("--month", "2020-01", "--test-recipient", "bad"),
        ):
            with self.assertRaises(CommandError):
                call_command("send_llm_cost_report", *arguments, stdout=StringIO())
        build.assert_not_called()
