# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report glossary terms that contradict each other."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.models import Component
from weblate.utils.management.base import BaseCommand
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


@dataclass(frozen=True, order=True)
class Finding:
    kind: str
    component: str
    language: str
    subject: str
    detail: str

    @property
    def key(self) -> str:
        return f"{self.kind}\t{self.component}\t{self.language}\t{self.subject}"

    def format(self) -> str:
        return f"{self.key}\t{self.detail}"


class Command(BaseCommand):
    help = "report glossary duplicates and collapsed terms"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument("--project", help="limit the audit to one project slug")
        parser.add_argument(
            "--baseline",
            type=Path,
            help="file of accepted finding keys; one per line, '#' starts a comment",
        )

    def handle(self, *args, **options) -> None:
        components = Component.objects.filter(is_glossary=True).order_by(
            "project__slug", "slug"
        )
        if options["project"]:
            components = components.filter(project__slug=options["project"])
            if not components.exists():
                msg = f"No glossary in project: {options['project']}"
                raise CommandError(msg)

        findings: list[Finding] = []
        for component in components:
            findings.extend(self.audit(component))
        findings.sort()

        accepted = self.read_baseline(options["baseline"])
        unaccepted = [finding for finding in findings if finding.key not in accepted]

        for finding in findings:
            marker = " " if finding.key in accepted else "!"
            self.stdout.write(f"{marker} {finding.format()}")
        if not findings:
            self.stdout.write("no findings")

        if unaccepted:
            msg = f"{len(unaccepted)} unaccepted glossary findings"
            raise CommandError(msg)

    def read_baseline(self, path: Path | None) -> set[str]:
        if path is None:
            return set()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            msg = f"Cannot read baseline: {path}"
            raise CommandError(msg) from error
        return {
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        }

    def audit(self, component: Component) -> list[Finding]:
        label = f"{component.project.slug}/{component.slug}"
        terms = {
            unit.pk: unit.source.strip()
            for unit in component.source_translation.unit_set.all()
            if unit.source.strip()
        }
        by_source: dict[str, list[int]] = defaultdict(list)
        for pk, source in terms.items():
            by_source[source.lower()].append(pk)

        findings: list[Finding] = []
        for translation in component.translation_set.exclude_source().order_by(
            "language__code"
        ):
            language = translation.language.code
            targets = {
                unit.source_unit_id: unit.target.strip()
                for unit in translation.unit_set.filter(state__gte=STATE_TRANSLATED)
                if unit.target.strip()
            }
            for source, pks in sorted(by_source.items()):
                if len(pks) < 2:
                    continue
                values = sorted({targets[pk].lower() for pk in pks if pk in targets})
                if len(values) > 1:
                    findings.append(
                        Finding(
                            "duplicate-term",
                            label,
                            language,
                            source,
                            " | ".join(values),
                        )
                    )
            by_target: dict[str, set[str]] = defaultdict(set)
            for source_unit_id, target in targets.items():
                source = terms.get(source_unit_id)
                if source:
                    by_target[target.lower()].add(source.lower())
            for target, sources in sorted(by_target.items()):
                if len(sources) > 1:
                    findings.append(
                        Finding(
                            "collapsed-terms",
                            label,
                            language,
                            target,
                            " | ".join(sorted(sources)),
                        )
                    )
        return findings
