# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Release legacy judge advisory holds stuck at needs-checking.

Before the current judge architecture (``state_for_verdict``, review D1), a
major finding automatically forced a unit to ``STATE_NEEDS_CHECKING`` (12).
The current policy no longer does this: a major stops at ``STATE_TRANSLATED``
and ships with judge-flag evidence attached, and holding a unit is now an
explicit producer decision (``resolve_verdict`` with
``JudgeVerdict.Resolution.ESCALATED``). That leaves a backlog of units held by
the old automatic behaviour that nobody has revisited.

This command finds and, only with ``--write``, releases exactly that backlog:
a needs-checking unit whose newest ``Change`` is the automatic transition into
that state (``ActionEvents.AUTO``, recorded via
``weblate.trans.autotranslate.AutoTranslate.update``) with nothing later, a
fresh parsed verdict still describing the current text as major, no recorded
resolution (a human explicitly escalating it goes through the normal
accept/escalate UI instead, never through this command), and no currently
failing deterministic enforced check. Anything else, including every unit
held by a real human escalation, is reported under "needs review" and left
untouched: the fallback is re-judging the string, not widening this predicate.

Dry-run by default. Requires an explicit component or project scope
(``--all``/``--file-format`` are refused): this is an operator cleanup tool
for a known scope, not an instance-wide sweep.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.db import transaction

from weblate.auth.models import User
from weblate.checks.judge import JUDGE_CHECKS
from weblate.checks.models import Check
from weblate.trans.actions import ActionEvents
from weblate.trans.models import Change, Unit
from weblate.trans.models.judge import JudgeVerdict, current_verdict
from weblate.utils.management.base import WeblateComponentCommand
from weblate.utils.state import STATE_NEEDS_CHECKING, STATE_TRANSLATED

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

WRITABLE = "writable"
NEEDS_REVIEW = "needs-review"


class Command(WeblateComponentCommand):
    help = "releases legacy judge advisory holds still stuck at needs-checking"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--write",
            action="store_true",
            default=False,
            help="release the writable units; without it nothing is written",
        )

    def get_user(self) -> User:
        return User.objects.get_or_create_bot(
            scope="weblate",
            name="judge-release",
            verbose="Judge advisory hold release",
        )

    @staticmethod
    def candidate_units(components):
        """Every unit currently held at needs-checking in scope."""
        return (
            Unit.objects.filter(
                translation__component__in=components, state=STATE_NEEDS_CHECKING
            )
            .prefetch()
            .prefetch_source()
            .order_by("pk")
        )

    @staticmethod
    def legacy_hold_change(unit: Unit) -> Change | None:
        """
        Return the newest Change, only if it proves an untouched automatic hold.

        None when the newest Change is anything else: a human escalation
        (``ActionEvents.JUDGE_RESOLUTION``), a later edit, or a state that was
        already 12 before this Change (not a transition into it).
        """
        newest = unit.change_set.order_by("-timestamp", "-pk").first()
        if (
            newest is None
            or newest.action != ActionEvents.AUTO
            or newest.details.get("state") != STATE_NEEDS_CHECKING
            or newest.details.get("old_state") == STATE_NEEDS_CHECKING
        ):
            return None
        return newest

    @staticmethod
    def failing_enforced_checks(unit: Unit) -> set[str]:
        """Deterministic (non-judge) enforced checks currently failing."""
        component = unit.translation.component
        if not component.enforced_checks:
            return set()
        return (unit.all_checks_names & set(component.enforced_checks)) - JUDGE_CHECKS

    def classify(self, unit: Unit) -> tuple[str, str]:
        """Return (bucket, reason). Read-only; never writes."""
        if self.legacy_hold_change(unit) is None:
            return (
                NEEDS_REVIEW,
                "the newest change is not an untouched automatic hold",
            )
        verdict = current_verdict(unit)
        if verdict is None or verdict.unparsed:
            return NEEDS_REVIEW, "no fresh parsed verdict for the current text"
        if verdict.verdict != JudgeVerdict.Verdict.FLAG:
            return (
                NEEDS_REVIEW,
                f"representative verdict is {verdict.verdict}, not major",
            )
        if verdict.resolution:
            return NEEDS_REVIEW, f"a resolution already exists ({verdict.resolution})"
        failing = self.failing_enforced_checks(unit)
        if failing:
            checks = ", ".join(sorted(failing))
            return NEEDS_REVIEW, f"enforced check(s) failing: {checks}"
        return WRITABLE, ""

    def release(self, unit_id: int, user: User) -> bool:
        """
        Re-take the whole decision under a row lock, then write.

        The classification ran without a lock; a concurrent edit, a new
        resolution, or a deleted unit between selection and write must not be
        trusted. Returns False when the unit is no longer safe to release.
        """
        with transaction.atomic():
            try:
                unit = (
                    Unit.objects.select_for_update()
                    .prefetch()
                    .prefetch_source()
                    .get(pk=unit_id)
                )
            except Unit.DoesNotExist:
                return False
            if unit.state != STATE_NEEDS_CHECKING:
                return False
            bucket, _reason = self.classify(unit)
            if bucket != WRITABLE:
                return False
            unit.translate(
                user,
                unit.get_target_plurals(),
                STATE_TRANSLATED,
                change_action=ActionEvents.AUTO,
                propagate=False,
                select_for_update=False,
                change_details={"judge_advisory_hold_release": True},
            )
        return True

    def handle(self, *args, **options) -> None:
        if options["all"] or options["file_format"]:
            msg = (
                "This command requires an explicit component or project scope; "
                "--all and --file-format are refused."
            )
            raise CommandError(msg)
        components = self.get_components(**options)

        buckets: dict[tuple[int, str], dict[str, list[int]]] = defaultdict(
            lambda: {WRITABLE: [], NEEDS_REVIEW: []}
        )
        reasons: dict[int, str] = {}
        for unit in self.candidate_units(components):
            translation = unit.translation
            bucket, reason = self.classify(unit)
            buckets[translation.component_id, translation.language.code][bucket].append(
                unit.pk
            )
            if reason:
                reasons[unit.pk] = reason

        total_writable = 0
        total_needs_review = 0
        for (component_id, language_code), group in sorted(buckets.items()):
            writable_ids = group[WRITABLE]
            review_ids = group[NEEDS_REVIEW]
            total_writable += len(writable_ids)
            total_needs_review += len(review_ids)
            self.stdout.write(
                f"component {component_id}/{language_code}: "
                f"writable={writable_ids} needs-review={review_ids}"
            )
            for unit_id in review_ids:
                if unit_id in reasons:
                    self.stdout.write(f"  unit {unit_id}: {reasons[unit_id]}")

        dismissed_judge_checks = Check.objects.filter(
            name__in=JUDGE_CHECKS,
            dismissed=True,
            unit__translation__component__in=components,
        ).count()
        if dismissed_judge_checks:
            self.stdout.write(
                f"{dismissed_judge_checks} dismissed judge check(s) found in scope; "
                "left untouched (this command never undismisses)."
            )

        self.stdout.write(
            f"{total_writable} writable, {total_needs_review} needs review"
        )

        if not options["write"]:
            self.stdout.write("Dry run: nothing written. Use --write to release.")
            return

        released = 0
        stale = 0
        user = self.get_user()
        for group in buckets.values():
            for unit_id in group[WRITABLE]:
                if self.release(unit_id, user):
                    released += 1
                else:
                    stale += 1
        self.stdout.write(f"{released} released, {stale} stale")
