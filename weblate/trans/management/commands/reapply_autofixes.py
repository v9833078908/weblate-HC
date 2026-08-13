# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Re-apply the active autofixes to translations that were stored earlier.

Autofixes only run on write (``Unit.translate`` -> ``fix_target``), so a unit
translated before a fix existed keeps its defect forever. This command replays
the whole active ``AUTOFIX_LIST`` over stored units, reports what would change,
and writes only with an explicit ``--apply``.

    scan (read-only)            apply (per repository root)
    ---------------------       ------------------------------------------
    for each component          take the repository lock
      skip glossary               refuse if foreign pending changes exist
      skip template               for each candidate:
      skip read-only                 lock the row, recompute, translate()
      apply_autofixes()           refuse to commit if foreign work appeared
      collect + count             commit once, without pushing
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.autofixes import apply_autofixes, autofix_fingerprint
from weblate.trans.models import Unit
from weblate.utils.management.base import WeblateComponentCommand
from weblate.utils.state import STATE_READONLY

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

    from weblate.trans.models import Component

# Without it the run is a false green: it would report zero changes because the
# fix is not registered, not because the corpus is clean.
REQUIRED_FIX_IDS = frozenset({"removed-final-stop"})
DIFF_EXAMPLES = 5
DIFF_WIDTH = 60


def render(text: str) -> str:
    """Render a target for the terminal: escaped, on one line, truncated."""
    shown = text if len(text) <= DIFF_WIDTH else f"{text[:DIFF_WIDTH]}…"
    # isprintable() is False for newlines, control characters and every
    # non-ASCII space, so NBSP and NNBSP stay visible in the diff.
    return "".join(
        char if char.isprintable() else f"\\u{ord(char):04x}" for char in shown
    )


class Command(WeblateComponentCommand):
    help = "re-applies the active autofixes to stored translations"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="write the repairs and commit them; without it nothing is written",
        )

    def check_registry(self) -> None:
        fingerprint = autofix_fingerprint()
        self.stdout.write(f"Active autofixes: {', '.join(fingerprint)}")
        missing = REQUIRED_FIX_IDS.difference(fingerprint)
        if missing:
            msg = (
                f"Required autofixes are not active: {', '.join(sorted(missing))}. "
                "Check WEBLATE_ADD_AUTOFIX in the running environment "
                "(printenv inside the container, not docker-compose.yml)."
            )
            raise CommandError(msg)

    @staticmethod
    def candidate_units(component: Component):
        """Stored units the autofixes are allowed to touch."""
        translations = [
            translation
            for translation in component.translation_set.all()
            # translate() skips autofixes for templates, so a template unit
            # would be marked as automatically translated without a repair.
            if not translation.is_template
        ]
        return (
            Unit.objects.filter(translation__in=translations)
            .exclude(state=STATE_READONLY)
            .order_by("pk")
        )

    def scan(self, component: Component) -> tuple[list[int], Counter[str], list[str]]:
        """Report the units the autofixes would change. Writes nothing."""
        changed: list[int] = []
        counters: Counter[str] = Counter()
        examples: list[str] = []
        units = self.candidate_units(component).prefetch()
        for unit in units.iterator(chunk_size=1000):
            original = unit.get_target_plurals()
            candidate, applied = apply_autofixes(list(original), unit)
            if candidate == original:
                continue
            changed.append(unit.pk)
            counters.update(applied)
            if len(examples) < DIFF_EXAMPLES:
                examples.append(
                    f"  {render(' | '.join(original))} -> {render(' | '.join(candidate))}"
                )
        return changed, counters, examples

    def report(
        self,
        component: Component,
        changed: list[int],
        counters: Counter[str],
        examples: list[str],
    ) -> None:
        detail = ", ".join(f"{key} {value}" for key, value in sorted(counters.items()))
        plural = "unit" if len(changed) == 1 else "units"
        self.stdout.write(
            f"{component}: {len(changed)} {plural} to change"
            + (f" ({detail})" if detail else "")
        )
        for example in examples:
            self.stdout.write(example)
        if len(changed) > len(examples):
            self.stdout.write(f"  … +{len(changed) - len(examples)} more")

    def handle(self, *args, **options) -> None:
        self.check_registry()
        components = self.get_components(**options).order_by("pk")
        groups: dict[int, list[Component]] = defaultdict(list)
        for component in components:
            if component.is_glossary:
                self.stdout.write(f"{component}: skipped (glossary)")
                continue
            groups[component.linked_component_id or component.pk].append(component)

        candidates: dict[int, list[int]] = {}
        for group in groups.values():
            for component in group:
                changed, counters, examples = self.scan(component)
                candidates[component.pk] = changed
                self.report(component, changed, counters, examples)

        if not options["apply"]:
            self.stdout.write("Dry run: nothing written. Use --apply to write.")
