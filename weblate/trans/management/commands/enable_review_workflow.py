# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Enable the project settings required by the LLM judge review workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.db import transaction

from weblate.trans.judge_workflow import TARGET_PROJECT_SLUGS
from weblate.trans.models import Project
from weblate.trans.models.project import CommitPolicyChoices
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = "enables review and commit gates for the judge workflow projects"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run", action="store_true", help="show changes without saving them"
        )

    def handle(self, *args, **options) -> None:
        projects = {
            project.slug: project
            for project in Project.objects.filter(slug__in=TARGET_PROJECT_SLUGS)
        }
        missing = set(TARGET_PROJECT_SLUGS) - projects.keys()
        if missing:
            msg = f"Project(s) not found: {', '.join(sorted(missing))}."
            raise CommandError(msg)
        changed = [
            projects[slug]
            for slug in TARGET_PROJECT_SLUGS
            if not projects[slug].translation_review
            or projects[slug].commit_policy != CommitPolicyChoices.WITHOUT_NEEDS_EDITING
        ]
        for project in changed:
            self.stdout.write(project.slug)
        if options["dry_run"]:
            return
        with transaction.atomic():
            for project in changed:
                project.translation_review = True
                project.commit_policy = CommitPolicyChoices.WITHOUT_NEEDS_EDITING
                project.save(update_fields={"translation_review", "commit_policy"})
