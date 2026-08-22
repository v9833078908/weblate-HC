# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check the configured machine-translation routes used by judge repair."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.judge_workflow import TARGET_PROJECT_SLUGS, repair_route
from weblate.trans.models import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = "checks machine-translation repair routes for the judge workflow"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--project", help="check one explicit project instead of the rollout set"
        )

    def handle(self, *args, **options) -> None:
        slugs = (options["project"],) if options["project"] else TARGET_PROJECT_SLUGS
        projects = {
            project.slug: project
            for project in Project.objects.filter(slug__in=slugs).prefetch_related(
                "component_set__translation_set__language"
            )
        }
        missing = set(slugs) - projects.keys()
        if missing:
            msg = f"Project(s) not found: {', '.join(sorted(missing))}."
            raise CommandError(msg)
        for slug in slugs:
            project = projects[slug]
            for component in project.component_set.all():
                for translation in component.translation_set.exclude_source():
                    model = repair_route(translation)
                    self.stdout.write(f"{slug}/{translation.language.code}: {model}")
