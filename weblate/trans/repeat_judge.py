# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Judge repeat queue variants from stored verdicts for their current targets."""

from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING

from weblate.trans.models.judge import (
    JUDGE_CATEGORY_LABELS,
    SEVERITY_RANK,
    JudgeVerdict,
    active_verdicts,
)
from weblate.utils.state import STATE_APPROVED

if TYPE_CHECKING:
    from collections.abc import Iterable

READY, CHOOSE, REWRITE, UNCHECKED = "ready", "choose", "rewrite", "unchecked"
REPEAT_JUDGE_QUERY = "check:repeat-drift"


@dataclass(frozen=True)
class VariantJudgement:
    mark: str  # "passed" | "flagged" | "unchecked"
    back_translation: str
    reason: str


@dataclass(frozen=True)
class GroupJudgement:
    bucket: str
    recommended: tuple[str, ...] | None
    variants: dict[tuple[str, ...], VariantJudgement]
    # (unit id, verdict pk, target hash) for every judged place, sorted.
    evidence: tuple[tuple[int, int, str], ...]


def judge_group(
    variants: list[dict], verdicts: dict[int, JudgeVerdict | None]
) -> GroupJudgement:
    """Classify one repeat group using current, collegium-reduced verdicts."""
    judgements: dict[tuple[str, ...], VariantJudgement] = {}
    evidence: list[tuple[int, int, str]] = []
    approved = False

    for variant in variants:
        passed: list[tuple[int, JudgeVerdict]] = []
        flagged: list[tuple[int, JudgeVerdict]] = []
        for unit in variant["units"]:
            approved |= unit.state == STATE_APPROVED
            verdict = verdicts.get(unit.pk)
            if verdict is None or verdict.verdict == JudgeVerdict.Verdict.UNPARSED:
                continue
            evidence.append((unit.pk, verdict.pk, verdict.target_hash))
            if verdict.verdict in {
                JudgeVerdict.Verdict.FLAG,
                JudgeVerdict.Verdict.REJECT,
            }:
                flagged.append((unit.pk, verdict))
            elif verdict.verdict == JudgeVerdict.Verdict.PASS:
                passed.append((unit.pk, verdict))

        mark = "flagged" if flagged else "passed" if passed else "unchecked"
        back_translation = (
            min(passed, key=itemgetter(0))[1].back_translation if passed else ""
        )
        reason = ""
        if flagged:
            _, strictest = max(
                flagged,
                key=lambda item: (SEVERITY_RANK[item[1].effective_severity], -item[0]),
            )
            primary = strictest.primary_error
            if primary is not None:
                category = primary.get("category", "")
                label = JUDGE_CATEGORY_LABELS.get(category, category)
                reason = f"{label}: {primary.get('description', '')}"
        judgements[variant["target"]] = VariantJudgement(mark, back_translation, reason)

    passed_targets = [
        target for target, judgement in judgements.items() if judgement.mark == "passed"
    ]
    if len(passed_targets) >= 2:
        bucket = CHOOSE
    elif any(judgement.mark == "unchecked" for judgement in judgements.values()):
        bucket = UNCHECKED
    elif len(passed_targets) == 1 and approved:
        bucket = CHOOSE
    elif len(passed_targets) == 1:
        bucket = READY
    elif judgements and all(
        judgement.mark == "flagged" for judgement in judgements.values()
    ):
        bucket = REWRITE
    else:
        bucket = UNCHECKED

    return GroupJudgement(
        bucket=bucket,
        recommended=passed_targets[0] if bucket == READY else None,
        variants=judgements,
        evidence=tuple(sorted(evidence)),
    )


def judge_groups(groups: Iterable[tuple[int, list[dict]]]) -> dict[int, GroupJudgement]:
    """Judge many groups with one active-verdict query for all their places."""
    grouped = list(groups)
    units = [
        unit
        for _, variants in grouped
        for variant in variants
        for unit in variant["units"]
    ]
    verdicts = active_verdicts(units)
    return {group_id: judge_group(variants, verdicts) for group_id, variants in grouped}
