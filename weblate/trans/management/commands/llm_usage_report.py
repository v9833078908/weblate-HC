# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Count, Q, Sum
from django.utils import timezone

from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

HEADER = [
    "model",
    "project",
    "requests",
    "prompt_tokens",
    "completion_tokens",
    "cost_usd",
    "unpriced",
]


class Command(BaseCommand):
    help = "reports LLM token usage and cost grouped by model and project"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=None, help="only the last N days")
        parser.add_argument("--model", default=None, help="only this model")
        parser.add_argument(
            "--project", default=None, help="only this project slug"
        )
        parser.add_argument("--format", choices=["table", "csv"], default="table")

    def handle(self, *args, **options) -> None:
        logs = LLMUsageLog.objects.all()
        if options["days"]:
            cutoff = timezone.now() - timedelta(days=options["days"])
            logs = logs.filter(created_at__gte=cutoff)
        if options["model"]:
            logs = logs.filter(model=options["model"])
        if options["project"]:
            logs = logs.filter(project_slug=options["project"])
        rows = list(
            logs.values("model", "project_slug")
            .annotate(
                requests=Count("id"),
                prompt=Sum("prompt_tokens"),
                completion=Sum("completion_tokens"),
                cost=Sum("cost_usd"),
                unpriced=Count("id", filter=Q(cost_usd__isnull=True)),
            )
            .order_by("model", "project_slug")
        )
        data = [
            [
                row["model"],
                row["project_slug"] or "-",
                row["requests"],
                row["prompt"] or 0,
                row["completion"] or 0,
                f"{row['cost']:.8f}" if row["cost"] is not None else "",
                row["unpriced"],
            ]
            for row in rows
        ]
        if options["format"] == "csv":
            writer = csv.writer(self.stdout)
            writer.writerow(HEADER)
            writer.writerows(data)
            return
        widths = [
            max(len(str(line[i])) for line in [HEADER, *data])
            for i in range(len(HEADER))
        ]
        for line in [HEADER, *data]:
            self.stdout.write(
                "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(line))
            )
