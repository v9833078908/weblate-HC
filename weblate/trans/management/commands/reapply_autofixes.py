# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Re-apply the active autofixes to translations that were stored earlier.

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
from django.db import transaction

from weblate.auth.models import User
from weblate.trans.actions import ActionEvents
from weblate.trans.autofixes import apply_autofixes, autofix_fingerprint
from weblate.trans.models import Component, Unit
from weblate.trans.models.pending import PendingUnitChange
from weblate.utils.management.base import WeblateComponentCommand
from weblate.utils.state import STATE_READONLY

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

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
        """Select the stored units the autofixes are allowed to touch."""
        translations = [
            translation
            for translation in component.translation_set.all()
            # translate() skips autofixes for templates, and source units
            # define the canonical source text rather than translations.
            if not translation.is_template and not translation.is_source
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

    def get_user(self) -> User:
        return User.objects.get_or_create_bot(
            scope="weblate", name="autofix", verbose="Autofix backfill"
        )

    def repair(self, unit_id: int, user: User) -> tuple[list[str], int] | None:
        """
        Repair one unit, or report that it no longer needs one.

        The whole decision is retaken inside the row lock: the scan ran
        without one, and a translator or an import may have changed the unit
        since. ``translate`` receives the freshly read target, applies the
        autofixes itself (unit.py:2404) and records the fixups.
        """
        with transaction.atomic():
            unit = Unit.objects.select_for_update().prefetch().get(pk=unit_id)
            if (
                unit.state == STATE_READONLY
                or unit.translation.is_template
                or unit.translation.is_source
            ):
                return None
            original = unit.get_target_plurals()
            candidate, applied = apply_autofixes(list(original), unit)
            if candidate == original:
                return None
            unit.translate(
                user,
                original,
                unit.state,
                change_action=ActionEvents.AUTO,
                propagate=False,
                select_for_update=False,
            )
            pending_change = unit.pending_unit_change
            if pending_change is None or pending_change.pk is None:
                msg = "Autofix repair did not create a pending change."
                raise RuntimeError(msg)
            return applied, pending_change.pk

    @staticmethod
    def foreign_pending(root: Component, pending_change_ids: set[int]) -> bool:
        """Any pending change in this repository family that is not ours."""
        return (
            PendingUnitChange.objects.for_component(
                root, apply_filters=False, include_linked=True
            )
            .exclude(pk__in=pending_change_ids)
            .exists()
        )

    def apply_group(
        self,
        root: Component,
        group: list[Component],
        candidates: dict[int, list[int]],
    ) -> bool:
        user = self.get_user()
        with root.repository.lock, transaction.atomic():
            if self.foreign_pending(root, set()):
                self.stderr.write(
                    f"{root}: foreign pending changes, refusing to touch this repository"
                )
                return False
            pending_change_ids: set[int] = set()
            stale = 0
            for component in group:
                for unit_id in candidates[component.pk]:
                    repaired = self.repair(unit_id, user)
                    if repaired is None:
                        stale += 1
                        continue
                    _applied, pending_change_id = repaired
                    pending_change_ids.add(pending_change_id)
            if not pending_change_ids:
                self.stdout.write(f"{root}: 0 written, {stale} stale")
                return True
            if self.foreign_pending(root, pending_change_ids):
                self.stderr.write(
                    f"{root}: foreign pending changes appeared during the run; "
                    "repairs stay pending and are not committed"
                )
                return False
            if not root.commit_pending_subset(
                "autofix backfill",
                user,
                pending_change_ids,
                skip_push=True,
            ):
                self.stderr.write(
                    f"{root}: failed to commit autofix repairs; repairs stay pending"
                )
                return False
        self.stdout.write(f"{root}: {len(pending_change_ids)} written, {stale} stale")
        return True

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
            return

        results: list[tuple[Component, bool]] = []
        for root_id, group in groups.items():
            root = next((c for c in group if c.pk == root_id), None)
            if root is None:
                root = Component.objects.get(pk=root_id)
            results.append((root, self.apply_group(root, group, candidates)))

        failures = [root for root, ok in results if not ok]
        if failures:
            msg = "Some repositories were not committed: " + ", ".join(
                str(root) for root in failures
            )
            raise CommandError(msg)
