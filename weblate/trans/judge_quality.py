# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only quality gates shared by producer decisions and atomic acceptance."""

from __future__ import annotations

import hashlib
import json
from copy import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Max

from weblate.checks.glossary import GLOSSARY_CHECK_ID, evaluate_glossary_terms
from weblate.checks.judge import JUDGE_CHECKS
from weblate.checks.models import CHECKS
from weblate.trans.models.judge import (
    JudgeVerdict,
    _complete_primary_evidence,
    collegium_verdict,
    compute_decision_revision,
    compute_target_hash,
    current_round,
)
from weblate.trans.util import join_plural
from weblate.utils.state import STATE_APPROVED, STATE_NEEDS_CHECKING, STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Sequence

    from weblate.auth.models import User
    from weblate.trans.models import Unit


@dataclass(frozen=True)
class TargetQuality:
    """Local checks for a target, without persisting text or Check rows."""

    checks: tuple[str, ...]
    glossary_hard: bool
    glossary_advisory: bool

    @property
    def clean(self) -> bool:
        return not self.checks and not self.glossary_hard


@dataclass(frozen=True)
class CandidateQuality:
    two_seat_verified: bool
    collegium_pass: bool
    target: TargetQuality

    @property
    def acceptable(self) -> bool:
        return self.two_seat_verified and self.collegium_pass and self.target.clean


def evaluate_target_quality(unit: Unit, targets: Sequence[str]) -> TargetQuality:
    """Evaluate normal technical checks and mandatory glossary rules offline."""
    probe = copy(unit)
    probe.target = join_plural(targets)
    probe.state = STATE_TRANSLATED
    probe.check_cache = {}
    sources = probe.get_source_plurals()
    target_forms = list(targets)
    failing = []
    for name, check in CHECKS.target.items():
        # Group comparisons belong to the subsequent repeat phase. Judge
        # projections describe stored evidence, not the proposed target.
        if name in JUDGE_CHECKS or name == GLOSSARY_CHECK_ID or check.skip_suggestions:
            continue
        if check.check_target_with_flags(sources, target_forms, probe, probe.all_flags):
            failing.append(name)
    hard: set[str] = set()
    advisory: set[str] = set()
    for index, target in enumerate(target_forms):
        source = sources[min(index, len(sources) - 1)]
        pair_hard, pair_advisory = evaluate_glossary_terms(probe, source, target)
        hard.update(pair_hard)
        advisory.update(pair_advisory)
    return TargetQuality(tuple(sorted(failing)), bool(hard), bool(advisory - hard))


def candidate_quality(
    unit: Unit, targets: Sequence[str], *, evidence: Sequence[JudgeVerdict]
) -> CandidateQuality:
    """Require parsed evidence, a collegial PASS and clean local checks."""
    subject = evidence[0].subject if evidence else JudgeVerdict.Subject.CANDIDATE
    verified = subject in {
        JudgeVerdict.Subject.LIVE,
        JudgeVerdict.Subject.CANDIDATE,
    } and _complete_primary_evidence(unit, targets, evidence, subject=subject)
    verdict = collegium_verdict(evidence) if verified else None
    return CandidateQuality(
        verified,
        verdict is not None and verdict.verdict == JudgeVerdict.Verdict.PASS,
        evaluate_target_quality(unit, targets),
    )


def automatic_candidate_allowed(unit: Unit, user: User) -> bool:
    """Do not silently change protected translations or glossary definitions."""
    return bool(
        unit.state != STATE_APPROVED
        and not unit.readonly
        and not unit.translation.component.locked
        and not unit.translation.component.is_glossary
        and user.has_perm("unit.review", unit)
        and user.has_perm("translation.auto", unit.translation)
        and user.has_perm("unit.edit", unit)
        and not unit.repeat_memberships.filter(
            mode="independent", stale_at__isnull=True
        ).exists()
    )


def automatic_repeat_change(unit: Unit):
    """Find the last API repeat write without treating manual decisions as holds."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.actions import (
        ActionEvents,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.change import (
        Change,
    )

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import (
        RepeatDecisionEvent,
    )

    change = (
        Change.objects.filter(unit=unit, action=ActionEvents.REPEAT_APPLY)
        .order_by("-pk")
        .first()
    )
    if change is None:
        return None
    token = change.details.get("repeat_event")
    if not token:
        return None
    event = RepeatDecisionEvent.objects.filter(
        token=token, snapshot__automatic=True
    ).first()
    return (change, event) if event is not None else None


def current_quality_round(unit: Unit) -> list[JudgeVerdict]:
    """Old same-target evidence is not a post-repeat verification."""
    context_hash = project_quality_context_hash(unit.translation.component.project)
    rows = [
        row for row in current_round(unit) if row.project_context_hash == context_hash
    ]
    applied = automatic_repeat_change(unit)
    if applied is not None:
        rows = [row for row in rows if row.timestamp > applied[0].timestamp]
    return rows


def may_release_automatic_repeat_hold(unit: Unit, user: User) -> bool:
    """Release only this exact held revision after fresh dual PASS and local checks."""
    if unit.state != STATE_NEEDS_CHECKING or not automatic_candidate_allowed(
        unit, user
    ):
        return False
    applied = automatic_repeat_change(unit)
    if applied is None:
        return False
    expected = next(
        (
            row.get("applied_revision")
            for row in applied[1].result.get("written", [])
            if row["unit"] == unit.pk
        ),
        None,
    )
    if expected != compute_decision_revision(unit):
        return False
    rows = current_quality_round(unit)
    return candidate_quality(unit, unit.get_target_plurals(), evidence=rows).acceptable


def project_quality_context_hash(project) -> str:
    """Use the native prompt context, never credentials, in quality revisions."""
    # Deferred because judge_loop uses these acceptance gates.
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.judge_loop import judge_project_context

    return compute_target_hash([judge_project_context(project)])


def glossary_quality_revision(project) -> str:
    """Include glossary audit revisions, including edit-away-and-back changes."""
    from weblate.trans.models.unit import Unit  # ruff: ignore[import-outside-top-level]

    terms = list(
        Unit.objects.filter(
            translation__component_id__in=[
                component.pk for component in project.glossaries
            ]
        )
        .annotate(quality_change=Max("change__pk"))
        .order_by("pk")
        .values(
            "pk",
            "last_updated",
            "source",
            "target",
            "extra_flags",
            "explanation",
            "note",
            "state",
            "quality_change",
        )
    )
    return hashlib.sha256(
        json.dumps(terms, sort_keys=True, cls=DjangoJSONEncoder).encode()
    ).hexdigest()


def repeat_quality_revision(group, units: Sequence[Unit]) -> str:
    """Freeze automatic-repeat guards without invalidating old membership hashes."""
    rows = [
        [
            unit.pk,
            compute_decision_revision(unit),
            compute_decision_revision(unit.source_unit),
            unit.source_unit.note,
            unit.source_unit.explanation,
            unit.translation.component.locked,
            unit.translation.component.allow_translation_propagation,
            project_quality_context_hash(unit.translation.component.project),
            unit.all_flags.format(),
            list(
                unit.repeat_memberships.order_by("pk").values(
                    "pk", "mode", "revision", "stale_at", "updated"
                )
            ),
            [
                [row.pk, row.max_severity, row.unparsed, row.resolution]
                for row in current_quality_round(unit)
            ],
        ]
        for unit in sorted(units, key=lambda unit: unit.pk)
    ]
    payload = [
        group.pk,
        group.revision,
        group.policy.revision,
        group.policy.enabled,
        list(group.policy.components.order_by("pk").values_list("pk", flat=True)),
        list(group.policy.source_labels.order_by("pk").values_list("pk", flat=True)),
        glossary_quality_revision(group.policy.project),
        project_quality_context_hash(group.policy.project),
        rows,
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, cls=DjangoJSONEncoder).encode()
    ).hexdigest()
