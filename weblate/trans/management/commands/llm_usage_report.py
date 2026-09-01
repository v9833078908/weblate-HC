# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from weblate.trans.models.component import Component
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.project import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

HEADER = [
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
]

GROUP_FIELDS = [
    "service",
    "model",
    "project_id_snapshot",
    "project_slug",
    "component_id_snapshot",
    "component_slug",
    "target_language_code",
    "operation",
]

TOTALS = {
    "requests": Count("id"),
    "strings": Sum("batch_size"),
    "prompt": Sum("prompt_tokens"),
    "completion": Sum("completion_tokens"),
    "cost": Sum("cost_usd"),
    "unpriced": Count("id", filter=Q(cost_usd__isnull=True)),
}


def _display(value: str) -> str:
    return value or "-"


def _row(
    *,
    service: str,
    model: str,
    project: str,
    component: str,
    language: str,
    operation: str,
    totals: dict,
    unattributed_requests: int | None = None,
) -> list[str | int]:
    cost = totals["cost"]
    unpriced = totals["unpriced"] or 0
    attribution_complete = (
        ""
        if unattributed_requests is None
        else "yes"
        if unattributed_requests == 0
        else "unknown"
    )
    return [
        _display(service),
        _display(model),
        _display(project),
        _display(component),
        _display(language),
        _display(operation),
        totals["requests"] or 0,
        totals["strings"] or 0,
        totals["prompt"] or 0,
        totals["completion"] or 0,
        format(cost, "f") if cost is not None else "",
        unpriced,
        "yes" if not unpriced else "no",
        "" if unattributed_requests is None else unattributed_requests,
        attribution_complete,
    ]


class Command(BaseCommand):
    help = (
        "reports LLM token usage and cost by service, model, project, component, "
        "target language and operation; attribution completeness counts only rows "
        "that could belong to the selected scope (matching or blank identity); pass "
        "--service and --days so unfiltered dimensions cannot widen it."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days", type=int, default=None, help="only the last N days"
        )
        parser.add_argument("--service", default=None, help="only this service ID")
        parser.add_argument("--model", default=None, help="only this model")
        parser.add_argument("--project", default=None, help="current project slug")
        parser.add_argument(
            "--component",
            default=None,
            help="current component slug; requires --project",
        )
        parser.add_argument(
            "--language", default=None, help="only this target language code"
        )
        parser.add_argument(
            "--operation",
            choices=LLMUsageLog.Operation.values,
            default=None,
            help="only this operation",
        )
        parser.add_argument(
            "--summary",
            action="store_true",
            help="emit one total after filters instead of model-level rows",
        )
        parser.add_argument("--format", choices=["table", "csv"], default="table")

    def handle(self, *args, **options) -> None:
        logs = LLMUsageLog.objects.all()
        days = options["days"]
        if days is not None:
            if days < 1:
                raise CommandError("--days must be at least 1.")
            logs = logs.filter(created_at__gte=timezone.now() - timedelta(days=days))
        for option, field in (
            ("service", "service"),
            ("model", "model"),
            ("operation", "operation"),
        ):
            if options[option]:
                logs = logs.filter(**{field: options[option]})

        scope_logs = logs
        project = None
        if options["project"]:
            try:
                project = Project.objects.only("id").get(slug=options["project"])
            except Project.DoesNotExist as error:
                raise CommandError(
                    f'Project "{options["project"]}" does not exist.'
                ) from error
            logs = logs.filter(project_id_snapshot=project.pk)
            scope_logs = scope_logs.filter(
                Q(project_id_snapshot=project.pk) | Q(project_id_snapshot__isnull=True)
            )
        if options["component"]:
            if project is None:
                raise CommandError("--component requires an existing --project.")
            try:
                component = Component.objects.only("id").get(
                    project_id=project.pk,
                    slug=options["component"],
                )
            except Component.DoesNotExist as error:
                raise CommandError(
                    f'Component "{options["component"]}" does not exist.'
                ) from error
            logs = logs.filter(component_id_snapshot=component.pk)
            scope_logs = scope_logs.filter(
                Q(component_id_snapshot=component.pk)
                | Q(component_id_snapshot__isnull=True)
            )
        if options["language"]:
            logs = logs.filter(target_language_code=options["language"])
            scope_logs = scope_logs.filter(
                Q(target_language_code=options["language"]) | Q(target_language_code="")
            )

        if options["summary"]:
            unattributed_requests = scope_logs.filter(
                Q(project_id_snapshot__isnull=True)
                | Q(component_id_snapshot__isnull=True)
                | Q(target_language_code="")
            ).count()
            totals = logs.aggregate(**TOTALS)
            data = [
                _row(
                    service=options["service"] or "*",
                    model=options["model"] or "*",
                    project=options["project"] or "*",
                    component=options["component"] or "*",
                    language=options["language"] or "*",
                    operation=options["operation"] or "*",
                    totals=totals,
                    unattributed_requests=unattributed_requests,
                )
            ]
        else:
            rows = logs.values(*GROUP_FIELDS).annotate(**TOTALS).order_by(*GROUP_FIELDS)
            data = [
                _row(
                    service=row["service"],
                    model=row["model"],
                    project=row["project_slug"],
                    component=row["component_slug"],
                    language=row["target_language_code"],
                    operation=row["operation"],
                    totals=row,
                )
                for row in rows
            ]
        if options["format"] == "csv":
            writer = csv.writer(self.stdout)
            writer.writerow(HEADER)
            writer.writerows(data)
            return
        widths = [
            max(len(str(line[index])) for line in [HEADER, *data])
            for index in range(len(HEADER))
        ]
        for line in [HEADER, *data]:
            self.stdout.write(
                "  ".join(
                    str(cell).ljust(widths[index]) for index, cell in enumerate(line)
                )
            )
