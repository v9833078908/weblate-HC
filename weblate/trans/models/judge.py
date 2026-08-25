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
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Exists,
    IntegerField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import MD5
from django.utils.html import escape
from django.utils.translation import gettext_lazy

from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_TRANSLATED,
    StringState,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from weblate.glossary.models import GlossaryPromptEntry
    from weblate.trans.models.unit import Unit

JUDGE_ERROR_SEPARATOR = " | "


def _digest(parts: Sequence[str]) -> str:
    """
    Hash a sequence unambiguously.

    JSON encoding of the whole list keeps element boundaries, so a form
    containing the separator cannot forge another form's digest.
    """
    payload = json.dumps(list(parts), ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_target_hash(target: Sequence[str]) -> str:
    """Hash every plural form of a target."""
    return _digest(target)


def compute_target_storage_hash(target: str) -> str:
    return hashlib.md5(target.encode(), usedforsecurity=False).hexdigest()


def compute_context_hash(
    *, source: str, note: str, glossary_terms: Iterable[Mapping[str, object]]
) -> str:
    """
    Hash source, note and every prompt-visible glossary-entry field.

    Neither mapping key order nor glossary order is context, so keys and
    serialized entries are both sorted: a reordered glossary must not
    invalidate a verdict. Entry content and multiplicity are context.
    """
    terms = sorted(
        json.dumps(
            dict(entry),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for entry in glossary_terms
    )
    return _digest([source, note, *terms])


class JudgeVerdict(models.Model):
    """
    One judge opinion about one version of one unit.

    Verdicts are never overwritten: they accumulate per
    ``(unit, run_id, attempt, seat)`` so the collegium and its repair
    loop stay auditable. Only ``max_severity`` and ``unparsed`` are
    stored; the verdict is derived (severity->verdict mapping was
    reopened by measurement R3 and must stay changeable without a data
    migration).
    """

    # Stored and API-facing values: deliberately not localized.
    class Verdict(models.TextChoices):
        PASS = "pass"  # ruff: ignore[hardcoded-password-string]
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
    target_storage_hash = models.CharField(  # ruff: ignore[django-nullable-model-string-field]
        max_length=32, null=True, db_index=True
    )
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
        # ruff: ignore[mutable-class-default]
        indexes = [
            models.Index(fields=["unit", "-timestamp"], name="judge_unit_recent_idx"),
            models.Index(
                fields=["unit", "target_hash", "-timestamp"],
                name="judge_unit_target_idx",
            ),
            models.Index(fields=["run_id"], name="judge_run_idx"),
        ]
        # ruff: ignore[mutable-class-default]
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
        """
        Derive the verdict, never stored.

        The severity->verdict mapping is reopened by R3 and must change
        without a data migration (D4).
        """
        if self.unparsed:
            return self.Verdict.UNPARSED
        return verdict_for_severity(self.max_severity)

    def is_stale(self, target: Sequence[str]) -> bool:
        """Whether the judged text differs from the text stored now."""
        return self.target_hash != compute_target_hash(target)


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
) -> StringState | None:
    """
    Target state for a verdict, or None when the state must not move.

    ``major`` lands on STATE_NEEDS_CHECKING and ``critical`` lands on
    STATE_FUZZY, which the project-level
    ``WITHOUT_NEEDS_EDITING`` commit policy already excludes from export.
    ``pass`` stops at STATE_TRANSLATED unless the site opts into judge
    approval (JUDGE_MAY_APPROVE) AND the project has review: measurement
    shows pass misses real critical defects, so the judge does not hand out the
    top trust state by default (review D2).
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.FLAG:
        return STATE_NEEDS_CHECKING
    if verdict == JudgeVerdict.Verdict.PASS and enable_review and may_approve:
        return STATE_APPROVED
    return STATE_TRANSLATED


def latest_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Return every seat of the newest round, stale or not.

    For the card's 'previous version' note. Not for projection.
    """
    newest = unit.judge_verdicts.order_by("-timestamp", "-pk").first()
    if newest is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            run_id=newest.run_id, attempt=newest.attempt
        ).order_by("seat")
    )


def current_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Return the newest round matching the current target and judge context.

    Unlike ``active_round``, this never falls back to an older parsed round.
    Orchestration must not repair or finalize a unit using evidence from a
    transport-dead current run.
    """
    target_hash = compute_target_hash(unit.get_target_plurals())
    context_hash = compute_context_hash(
        source=unit.source,
        note=unit.source_unit.note,
        glossary_terms=_glossary_prompt_entries(unit),
    )
    newest = (
        unit.judge_verdicts.filter(target_hash=target_hash, context_hash=context_hash)
        .order_by("-timestamp", "-pk")
        .first()
    )
    if newest is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            target_hash=target_hash,
            context_hash=context_hash,
            run_id=newest.run_id,
            attempt=newest.attempt,
        ).order_by("seat")
    )


def _glossary_prompt_entries(unit: Unit) -> list[GlossaryPromptEntry]:
    from weblate.glossary.models import (  # ruff: ignore[import-outside-top-level]
        get_matched_glossary_prompt_entries,
    )

    return get_matched_glossary_prompt_entries(unit)


def active_round(unit: Unit) -> list[JudgeVerdict]:
    """
    Newest parsed round that describes the current text.

    Staleness is handled by filtering on target_hash. An all-unparsed
    newest round is skipped in favour of the newest parsed one, so a
    transport failure never erases the last real verdict (D5).
    """
    current = compute_target_hash(unit.get_target_plurals())
    newest = (
        unit.judge_verdicts.filter(target_hash=current)
        .order_by("-timestamp", "-pk")
        .first()
    )
    if newest is None:
        return []
    rows = list(
        unit.judge_verdicts.filter(
            target_hash=current, run_id=newest.run_id, attempt=newest.attempt
        ).order_by("seat")
    )
    if any(not row.unparsed for row in rows):
        return rows
    parsed = (
        unit.judge_verdicts.filter(target_hash=current, unparsed=False)
        .order_by("-timestamp", "-pk")
        .first()
    )
    if parsed is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            target_hash=current, run_id=parsed.run_id, attempt=parsed.attempt
        ).order_by("seat")
    )


def collegium_verdict(rows: Sequence[JudgeVerdict]) -> JudgeVerdict | None:
    """
    Return the strictest opinion of a round. No seat may lower another.

    A transport failure is not an opinion, so an unparsed row neither
    raises nor lowers the round; only when every seat failed does the
    round read as unparsed.
    """
    if not rows:
        return None
    parsed = [row for row in rows if not row.unparsed]
    if not parsed:
        return rows[0]
    return max(parsed, key=lambda row: (SEVERITY_RANK[row.max_severity], -row.seat))


def active_verdict(unit: Unit) -> JudgeVerdict | None:
    """Return the collegium verdict that still describes the stored text."""
    return collegium_verdict(active_round(unit))


def judge_status_annotations() -> dict[str, models.Expression]:
    """Annotate a Unit queryset with target-fresh judge evidence."""
    newest_parsed = JudgeVerdict.objects.filter(
        unit_id=OuterRef(OuterRef("pk")),
        target_storage_hash=MD5(OuterRef(OuterRef("target"))),
        unparsed=False,
    ).order_by("-timestamp", "-pk")
    current_parsed_round = JudgeVerdict.objects.filter(
        unit_id=OuterRef("pk"),
        target_storage_hash=MD5(OuterRef("target")),
        run_id=Subquery(newest_parsed.values("run_id")[:1]),
        attempt=Subquery(newest_parsed.values("attempt")[:1]),
        unparsed=False,
    )
    severity_rank = Case(
        *(
            When(max_severity=severity, then=Value(rank))
            for severity, rank in SEVERITY_RANK.items()
        ),
        output_field=IntegerField(),
    )
    latest_current = JudgeVerdict.objects.filter(
        unit_id=OuterRef("pk"),
        target_storage_hash=MD5(OuterRef("target")),
    ).order_by("-timestamp", "-pk")
    newest_current = JudgeVerdict.objects.filter(
        unit_id=OuterRef(OuterRef("pk")),
        target_storage_hash=MD5(OuterRef(OuterRef("target"))),
    ).order_by("-timestamp", "-pk")
    latest_current_parsed = JudgeVerdict.objects.filter(
        unit_id=OuterRef("pk"),
        target_storage_hash=MD5(OuterRef("target")),
        run_id=Subquery(newest_current.values("run_id")[:1]),
        attempt=Subquery(newest_current.values("attempt")[:1]),
        unparsed=False,
    )

    return {
        "judge_active_severity": Subquery(
            current_parsed_round.annotate(severity_rank=severity_rank)
            .order_by("-severity_rank", "seat")
            .values("max_severity")[:1],
            output_field=CharField(),
        ),
        "judge_has_parsed_history": Exists(
            JudgeVerdict.objects.filter(unit_id=OuterRef("pk"), unparsed=False)
        ),
        "judge_latest_incomplete": Case(
            When(
                Exists(latest_current),
                then=Case(
                    When(Exists(latest_current_parsed), then=Value(False)),
                    default=Value(True),
                    output_field=BooleanField(),
                ),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    }


def current_verdict(unit: Unit) -> JudgeVerdict | None:
    """Return only the verdict from the newest current-context round."""
    return collegium_verdict(current_round(unit))


def describe_latest_verdict(unit: Unit) -> str:
    """
    Human-readable evidence for the active round, or an empty string.

    Rendered into the check description, which weblate/machinery/llm.py
    feeds to the translator as failing_checks during repair. Both seats
    are merged. Descriptions are escaped and joined with an explicit
    separator: llm.py runs the text through strip_tags().split(), which
    would otherwise eat game markup like <color=#RRGGBB> and collapse
    newlines into one blob (review Q1).
    """
    lines: list[str] = []
    for row in active_round(unit):
        for error in row.errors:
            severity = error.get("severity", "unspecified")
            category = error.get("category", "unspecified")
            description = escape(error.get("description", ""))
            line = f"{severity}/{category}: {description}"
            if line not in lines:
                lines.append(line)
    return JUDGE_ERROR_SEPARATOR.join(lines)
