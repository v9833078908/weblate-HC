# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def _digest(parts: Sequence[str]) -> str:
    """Hash a sequence unambiguously.

    JSON encoding of the whole list keeps element boundaries, so a form
    containing the separator cannot forge another form's digest.
    """
    payload = json.dumps(list(parts), ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_target_hash(target: Sequence[str]) -> str:
    """Hash every plural form of a target."""
    return _digest(target)


def compute_context_hash(
    *, source: str, note: str, glossary_terms: Iterable[tuple[str, str]]
) -> str:
    """Hash what the judge was told besides the target.

    Glossary order is not context, so terms are sorted; a reordered
    glossary must not invalidate a verdict.
    """
    terms = sorted(f"{term}\x1f{translation}" for term, translation in glossary_terms)
    return _digest([source, note, *terms])


class JudgeVerdict(models.Model):
    """One judge opinion about one version of one unit.

    Verdicts are never overwritten: they accumulate per
    ``(unit, run_id, attempt, seat)`` so the collegium and its repair
    loop stay auditable. Only ``max_severity`` and ``unparsed`` are
    stored; the verdict is derived (severity->verdict mapping was
    reopened by measurement R3 and must stay changeable without a data
    migration).
    """

    # Stored and API-facing values: deliberately not localized.
    class Verdict(models.TextChoices):
        PASS = "pass"
        FLAG = "flag"
        REJECT = "reject"
        # Transport failure, never an opinion. Architecture invariant 4.3.
        UNPARSED = "unparsed"

    class Severity(models.TextChoices):
        # Declaration order IS strictness order (see SEVERITY_RANK, task 2).
        NONE = "none"
        MINOR = "minor"
        MAJOR = "major"
        CRITICAL = "critical"

    class Resolution(models.TextChoices):
        ACCEPTED_AS_IS = "accepted_as_is"
        SENT_BACK = "sent_back"
        ESCALATED = "escalated"

    unit = models.ForeignKey(
        "trans.Unit", on_delete=models.deletion.CASCADE, related_name="judge_verdicts"
    )
    # The model's own pass/flag/reject choice, kept as evidence (measured
    # arm D returns it per segment). The gate uses max_severity, not this.
    model_verdict = models.CharField(max_length=10, choices=Verdict, blank=True)
    max_severity = models.CharField(
        max_length=10, choices=Severity, default=Severity.NONE
    )
    # A transport failure is not a severity: it is a separate axis so an
    # unparsed row can never read as a real "none"/pass (invariant 4.3).
    unparsed = models.BooleanField(default=False)
    errors = models.JSONField(default=list, blank=True)
    back_translation = models.TextField(blank=True)
    judge_model = models.CharField(max_length=200)
    # Place in the collegium, not seniority: seat 2 may not lower seat 1.
    seat = models.SmallIntegerField()
    attempt = models.SmallIntegerField(default=0)
    target_hash = models.CharField(max_length=64)
    context_hash = models.CharField(max_length=64)
    run_id = models.UUIDField(default=uuid.uuid4)
    timestamp = models.DateTimeField(auto_now_add=True)

    resolution = models.CharField(max_length=20, choices=Resolution, blank=True)
    resolution_reason = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="judge_resolutions",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge verdict")
        verbose_name_plural = gettext_lazy("Judge verdicts")
        # No default ordering: it would force a sort on every queryset of
        # a table that accumulates. Callers order explicitly.
        indexes = [
            models.Index(fields=["unit", "-timestamp"], name="judge_unit_recent_idx"),
            models.Index(
                fields=["unit", "target_hash", "-timestamp"],
                name="judge_unit_target_idx",
            ),
            models.Index(fields=["run_id"], name="judge_run_idx"),
        ]
        constraints = [
            # One vote per seat per round: a round is reduced to its
            # strictest seat and must not see a seat twice.
            models.UniqueConstraint(
                fields=["unit", "run_id", "attempt", "seat"],
                name="judge_one_vote_per_seat",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit_id}: {self.verdict} (seat {self.seat})"

    @property
    def verdict(self) -> str:
        """Derived, never stored: the severity->verdict mapping is
        reopened by R3 and must change without a data migration (D4)."""
        if self.unparsed:
            return self.Verdict.UNPARSED
        return verdict_for_severity(self.max_severity)

    def is_stale(self, target: Sequence[str]) -> bool:
        """Whether the judged text differs from the text stored now."""
        return self.target_hash != compute_target_hash(target)


from weblate.utils.state import STATE_APPROVED, STATE_FUZZY, STATE_TRANSLATED

# Design "Гейт по severity выражается штатными настройками". minor is a
# pass: the errors are recorded, but they do not hold the string back.
_SEVERITY_VERDICT = {
    "none": JudgeVerdict.Verdict.PASS,
    "minor": JudgeVerdict.Verdict.PASS,
    "major": JudgeVerdict.Verdict.FLAG,
    "critical": JudgeVerdict.Verdict.REJECT,
}

# Strictness order, so a round reduces to its strictest seat without a
# seat ever lowering another. Derived from the declared scale: Severity
# is ordered by definition (task 2 pins this with a test).
SEVERITY_RANK = {name: rank for rank, name in enumerate(JudgeVerdict.Severity.values)}


def verdict_for_severity(max_severity: str) -> str:
    """Derive the verdict from the worst error the judge reported."""
    return _SEVERITY_VERDICT[max_severity]


def state_for_verdict(
    verdict: str, *, enable_review: bool, may_approve: bool
) -> int | None:
    """Target state for a verdict, or None when the state must not move.

    ``critical`` lands on STATE_FUZZY, which the project-level
    ``WITHOUT_NEEDS_EDITING`` commit policy already excludes from export.
    ``pass`` stops at STATE_TRANSLATED unless the site opts into judge
    approval (JUDGE_MAY_APPROVE) AND the project has review: measurement
    shows pass misses real criticals, so the judge does not hand out the
    top trust state by default (review D2).
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.PASS and enable_review and may_approve:
        return STATE_APPROVED
    return STATE_TRANSLATED