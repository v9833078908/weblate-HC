# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Backfill stored judge repair candidates for the existing REJECT/FLAG backlog.

A unit judged REJECT or FLAG before the candidate-preview card existed (or
whose candidate expired without anyone generating a fresh one) has no
suggested fix to preview on the translate page: the producer sees only
"Generate suggested fix" and has to spend the paid call themselves. This
command finds that backlog and, only with ``--write``, spends exactly one
paid repair call per unit to store a fresh candidate.

Dry-run by default: prints the classification of every current, unresolved
REJECT/FLAG unit in scope, and stores nothing. A unit that already has an
active candidate is reported as ``existing``; a unit still on the
deterministic max-length repair path is reported as ``max-length``; both
cost no call either way. A resolved verdict or a minor/pass unit is not
part of the backlog and is not listed at all. ``--write`` then spends one
call per remaining (``pending``) unit, in the same order, stopping after
``--limit`` paid attempts if given.

Requires an explicit component or project scope (``--all``/``--file-format``
are refused): this is an operator backlog tool for a known scope, not an
instance-wide sweep that could spend an unbounded amount on one run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.auth.models import User
from weblate.trans.judge_loop import (
    active_judge_candidate,
    generate_candidate_for_verdict,
)
from weblate.trans.models import JudgeVerdict, Unit
from weblate.trans.models.judge import current_verdict
from weblate.utils.management.base import WeblateComponentCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


EXISTING = "existing"
MAX_LENGTH = "max-length"
PENDING = "pending"

# Outcomes generate_candidate_for_verdict() can return that spend a paid
# repair-engine call; everything else (existing, stale, resolved,
# max-length, busy, denied, no-engine, invalid-verdict) is free.
PAID_OUTCOMES = frozenset({"generated", "failed", "drift"})


class Command(WeblateComponentCommand):
    help = (
        "backfills stored judge repair candidates for the current REJECT/FLAG backlog"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--write",
            action="store_true",
            default=False,
            help="generate the pending candidates; without it nothing is written",
        )
        parser.add_argument(
            "--user",
            default=None,
            help="username to attribute generated candidates to (required with --write)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="stop after this many paid repair attempts",
        )

    @staticmethod
    def candidate_units(components):
        """Every unit with a currently active REJECT/FLAG judge check in scope."""
        return (
            Unit.objects.filter(
                translation__component__in=components,
                check__name__in=("judge-reject", "judge-flag"),
                check__dismissed=False,
            )
            .distinct()
            .prefetch()
            .prefetch_source()
            .order_by("pk")
        )

    @staticmethod
    def classify(unit: Unit, verdict: JudgeVerdict) -> str:
        """Classify a unit already known to carry a current, unresolved REJECT/FLAG verdict."""
        if any(check.name == "max-length" for check in unit.active_checks):
            # That unit stays on the deterministic mutating-repair path
            # (invariant 9 in judge_loop.generate_candidate_for_verdict): no
            # candidate preview is generated while it is active.
            return MAX_LENGTH
        if active_judge_candidate(unit, verdict) is not None:
            return EXISTING
        return PENDING

    def handle(self, *args, **options) -> None:
        if options["all"] or options["file_format"]:
            msg = (
                "This command requires an explicit component or project scope; "
                "--all and --file-format are refused."
            )
            raise CommandError(msg)
        if options["write"] and not options["user"]:
            msg = "--write requires --user."
            raise CommandError(msg)
        components = self.get_components(**options)

        pending: list[tuple[Unit, int]] = []
        for unit in self.candidate_units(components):
            verdict = current_verdict(unit)
            if verdict is None or verdict.verdict not in {
                JudgeVerdict.Verdict.REJECT,
                JudgeVerdict.Verdict.FLAG,
            }:
                # Transport failure, stale round, or the text has since
                # been re-judged clean: not part of the backlog.
                continue
            if verdict.resolution:
                # A producer already decided; re-generating a preview for
                # a closed decision is not this command's job.
                continue
            bucket = self.classify(unit, verdict)
            self.stdout.write(f"{unit.pk} {unit.context} {bucket}")
            if bucket == PENDING:
                pending.append((unit, verdict.pk))

        self.stdout.write(f"{len(pending)} pending")

        if not options["write"]:
            self.stdout.write("Dry run: nothing written. Use --write to generate.")
            return

        try:
            user = User.objects.get(username=options["user"])
        except User.DoesNotExist as error:
            msg = f"User {options['user']!r} does not exist!"
            raise CommandError(msg) from error

        limit = options["limit"]
        tally: dict[str, int] = {}
        paid_attempts = 0
        for unit, verdict_id in pending:
            if limit is not None and paid_attempts >= limit:
                break
            try:
                outcome = generate_candidate_for_verdict(
                    unit_id=unit.pk, verdict_id=verdict_id, user=user, replace=False
                )
            except Exception as error:
                # One unit must not strand the rest of the backlog, and the
                # tally of what was already paid for must still be printed.
                outcome = "error"
                self.stderr.write(f"{unit.pk} {unit.context} error: {error}")
                paid_attempts += 1
            else:
                self.stdout.write(f"{unit.pk} {unit.context} {outcome}")
                if outcome in PAID_OUTCOMES:
                    paid_attempts += 1
            tally[outcome] = tally.get(outcome, 0) + 1

        summary = ", ".join(
            f"{count} {outcome}" for outcome, count in sorted(tally.items())
        )
        self.stdout.write(summary or "0 processed")
