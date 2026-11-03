# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.core.validators import validate_email
from django.utils import timezone

from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = (
        "Build a completed UTC month of LLM costs. Sending uses real email; "
        "--test-recipient sends only to that address, and --dry-run sends nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--month", required=True, help="completed UTC month YYYY-MM"
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--dry-run", action="store_true", help="collect without email"
        )
        group.add_argument(
            "--test-recipient", help="send real email only to this address"
        )

    def handle(self, *args, **options) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.llm_usage_report import (
            build_monthly_report,
            send_monthly_llm_usage_report,
        )

        month = options["month"]
        msg = "--month must be a completed UTC month in YYYY-MM format."
        if not isinstance(month, str) or not re.fullmatch(
            r"[0-9]{4}-(0[1-9]|1[0-2])", month
        ):
            raise CommandError(msg)
        year, number = map(int, month.split("-"))
        try:
            start = datetime(year, number, 1, tzinfo=UTC)
            end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=UTC)
        except (ValueError, OverflowError):
            raise CommandError(msg) from None
        if end > timezone.now().astimezone(UTC):
            raise CommandError(msg)

        test_recipient = options["test_recipient"]
        recipients = (
            [test_recipient]
            if test_recipient is not None
            else settings.LLM_COST_REPORT_RECIPIENTS
        )
        if not options["dry_run"]:
            if not recipients:
                msg = "No report recipients are configured."
                raise CommandError(msg)
            try:
                for address in recipients:
                    validate_email(address)
            except (TypeError, ValidationError):
                msg = "Invalid report recipient address."
                raise CommandError(msg) from None

        try:
            report = build_monthly_report(start, end)
        except Exception:
            msg = "Report collection failed; no email was sent."
            raise CommandError(msg) from None
        if options["dry_run"]:
            self.stdout.write("Report data collected; no email sent.")
            return
        try:
            send_monthly_llm_usage_report(
                start, end, report=report, recipients=recipients
            )
        except Exception:
            msg = "Report delivery failed or is uncertain; check recipients before resending."
            raise CommandError(msg) from None
        self.stdout.write("Report sent.")
