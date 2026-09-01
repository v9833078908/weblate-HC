# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Delete historical false-unparsed verdicts caused by refused judge requests.

Before the fail-fast fix (``http-request-invalid``), an HTTP 4xx refusal was
classified ``http-other``: the judge client wrote an ``unparsed`` verdict and
the run continued. Those verdicts are not opinions - the endpoint never
answered - and they pollute the unparsed statistics and the producer verdict
card (plan docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md, Task 4).

The new kind is not retroactive: the historical 400 attempts remain
``http-other`` and the 401 attempt remains ``http-auth``, so selection is
through the linked attempt's stored HTTP status only, never through the new
kind, message text, or a date window:

    JudgeVerdict.unparsed=True AND request_attempt.http_status IN (400, 401)

Rows with no attempt, ``unparsed=False``, any other status (413/431 are
size-dependent, 5xx are availability), or a deadline/transport attempt (no
HTTP status at all) are never touched.

For every deleted verdict, each linked ``JudgeRunUnit`` whose outcome is
``unparsed`` is reclassified to the explicit ``refused`` outcome first - the
verdict FK is ``SET_NULL``, so reclassifying after deletion would lose the
evidence of which row to correct. The attempt ledger is kept: it is the
diagnostic record of what was refused.

Dry-run by default: it prints the candidate count grouped by HTTP status,
model, and profile fingerprint. Writing requires ``--confirm`` together with
``--expected-count=N``: the command aborts when the live count differs from
``N``, so the operator approves exactly the number they reviewed. Run this
before the 90-day attempt cleanup can null the foreign-key evidence.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.db import transaction

from weblate.trans.models.judge import JudgeRunUnit, JudgeVerdict
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

REFUSED_STATUSES = (400, 401)


class Command(BaseCommand):
    help = "removes historical false-unparsed verdicts left by refused requests"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--expected-count",
            type=int,
            required=True,
            help="number of candidates reviewed in a dry run; writing aborts on mismatch",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="actually delete the verdicts; without it the command only reports",
        )

    def candidates(self):
        """Legacy refused verdicts: unparsed rows whose attempt answered 400/401."""
        return JudgeVerdict.objects.filter(
            unparsed=True,
            request_attempt__http_status__in=REFUSED_STATUSES,
        )

    def handle(self, *args, **options) -> None:
        expected = options["expected_count"]
        if expected < 0:
            msg = "--expected-count must not be negative"
            raise CommandError(msg)
        rows = list(
            self.candidates().values_list(
                "pk",
                "request_attempt__http_status",
                "request_attempt__model",
                "request_attempt__profile_fingerprint",
            )
        )
        count = len(rows)

        if not options["confirm"]:
            self.stdout.write(f"total: {count}")
            grouped: defaultdict = defaultdict(int)
            for _pk, status, model, fingerprint in rows:
                grouped[status, model, fingerprint] += 1
            for (status, model, fingerprint), number in sorted(
                grouped.items(), key=lambda item: str(item[0])
            ):
                self.stdout.write(
                    f"  HTTP {status} model={model} "
                    f"profile={fingerprint[:12]} count={number}"
                )
            self.stdout.write("Dry run: nothing deleted. Pass --confirm to write.")
            return

        if count != expected:
            msg = (
                f"candidate count changed: expected {expected}, found {count}. "
                "Nothing was deleted; re-run the dry run and retry."
            )
            raise CommandError(msg)

        verdict_ids = [pk for pk, _status, _model, _fingerprint in rows]
        with transaction.atomic():
            # Reclassify before deleting: the verdict FK is SET_NULL and would
            # silently drop the link identifying which unparsed rows to fix.
            reclassified = JudgeRunUnit.objects.filter(
                verdict_id__in=verdict_ids, outcome=JudgeRunUnit.Outcome.UNPARSED
            ).update(outcome=JudgeRunUnit.Outcome.REFUSED)
            _total, by_model = JudgeVerdict.objects.filter(pk__in=verdict_ids).delete()
        # Report what the database actually removed, never the pre-transaction
        # snapshot: an operator auditing a destructive run must not be told a
        # count that a concurrent delete already made wrong.
        deleted = by_model.get(JudgeVerdict._meta.label, 0)  # ruff: ignore[private-member-access]
        self.stdout.write(
            f"{deleted} verdicts deleted, {reclassified} run-unit rows reclassified "
            "to refused (attempt ledger kept)"
        )
