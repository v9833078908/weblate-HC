# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report how much of a project's glossary matches its source strings."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.models import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = "report glossary term coverage over the source strings of a project"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument("project", help="project slug")
        parser.add_argument(
            "--suffix",
            type=int,
            default=3,
            help=(
                "how many trailing characters an inflected form may add to a "
                "term and still be reported as a candidate (default 3)"
            ),
        )

    def handle(self, *args, **options) -> None:
        try:
            project = Project.objects.get(slug=options["project"])
        except Project.DoesNotExist as error:
            msg = f"No such project: {options['project']}"
            raise CommandError(msg) from error

        glossaries = project.glossaries
        terms = {
            source.strip().lower()
            for glossary in glossaries
            for source in glossary.source_translation.unit_set.values_list(
                "source", flat=True
            )
            if source.strip()
        }
        if not terms:
            self.stdout.write(f"{project.slug}: no glossary terms")
            return

        # Only components sharing a glossary's source language can ever match;
        # see get_glossary_units in weblate/glossary/models.py.
        source_languages = {glossary.source_language_id for glossary in glossaries}
        sources = [
            source.lower()
            for component in project.component_set.filter(
                is_glossary=False, source_language__in=source_languages
            )
            for source in component.source_translation.unit_set.values_list(
                "source", flat=True
            )
        ]

        matched: Counter[str] = Counter()
        candidates: Counter[str] = Counter()
        suffix = options["suffix"]

        for term in sorted(terms):
            escaped = re.escape(term)
            # exact mirrors the word-boundary rule of fetch_glossary_terms.
            # inflected is deliberately looser than that rule: it is a report
            # of near misses, not a matcher, and \w{1,N} is greedy so a form
            # is only reported once, at its longest.
            exact = re.compile(rf"(?<!\w){escaped}(?!\w)")
            inflected = re.compile(rf"(?<!\w)({escaped}\w{{1,{suffix}}})(?!\w)")
            matched[term] = 0
            for source in sources:
                matched[term] += len(exact.findall(source))
                for form in inflected.findall(source):
                    candidates[form] += 1

        self.stdout.write(
            f"{project.slug}: {len(terms)} terms, {len(sources)} source strings, "
            f"{sum(matched.values())} matches"
        )

        self.stdout.write("")
        self.stdout.write("matched terms:")
        for term, count in matched.most_common():
            if count:
                self.stdout.write(f"  {count:6}  {term}")

        unmatched = sorted(term for term, count in matched.items() if not count)
        if unmatched:
            self.stdout.write("")
            self.stdout.write(f"never matched ({len(unmatched)}):")
            for term in unmatched:
                self.stdout.write(f"          {term}")

        if candidates:
            self.stdout.write("")
            self.stdout.write(
                "forms a term nearly matched - candidates for a glossary entry:"
            )
            for form, count in candidates.most_common():
                self.stdout.write(f"  {count:6}  {form}")
