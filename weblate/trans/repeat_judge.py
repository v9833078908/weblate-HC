# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Judge repeat queue variants from stored verdicts for their current targets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil
from operator import itemgetter
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db import transaction

from weblate.trans.judge import JudgeError
from weblate.trans.models import RepeatPolicy
from weblate.trans.models.judge import (
    JUDGE_CATEGORY_LABELS,
    SEVERITY_RANK,
    JudgeVerdict,
    ProducerRun,
    active_verdicts,
)
from weblate.trans.repeat_recommendations import (
    REPEAT_RECOMMENDATION_BATCH_SIZE,
    prepare_run,
)
from weblate.trans.repeats import policy_units, repeat_queue_groups
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.trans.models import RepeatRecommendationResult

LOGGER = logging.getLogger(__name__)
READY, CHOOSE, REWRITE, UNCHECKED = "ready", "choose", "rewrite", "unchecked"
REPEAT_JUDGE_QUERY = "check:repeat-drift"
COMPARISON_KEY = "repeat_comparison"


def latest_repeat_judge_run(project_language) -> ProducerRun | None:
    """Find the newest proposal-only run launched for this language's queue."""
    return (
        ProducerRun.objects.filter(
            scope_type=ProducerRun.ScopeType.PROJECT,
            scope_id=str(project_language.project.pk),
            requested_mode="judge",
            scope_path=project_language.get_absolute_url(),
            requested_query__startswith=REPEAT_JUDGE_QUERY,
            execution_options__judge_proposal_only=True,
        )
        .order_by("-created")
        .first()
    )


def is_repeat_judge_run(run: ProducerRun) -> bool:
    """Tell from its request whether a run is a verdict-only repeat queue check."""
    return (
        run.requested_mode == "judge"
        and run.scope_type == ProducerRun.ScopeType.PROJECT
        and run.execution_options.get("judge_proposal_only") is True
        and run.requested_query.startswith(REPEAT_JUDGE_QUERY)
    )


def repeat_judge_policy(run: ProducerRun) -> RepeatPolicy | None:
    """Return the enabled repeat policy whose queue launched this run."""
    if not run.scope_id.isdigit():
        return None
    for policy in RepeatPolicy.objects.filter(
        project_id=int(run.scope_id), enabled=True
    ).select_related("project", "target_language"):
        project_language = policy.project.project_languages[policy.target_language]
        if project_language.get_absolute_url() == run.scope_path:
            return policy
    return None


def comparison_upper_bound(project_language, user) -> dict[str, int] | None:
    """Bound the follow-up comparison before any verdict of the check exists."""
    policy = RepeatPolicy.objects.filter(
        project=project_language.project,
        target_language=project_language.language,
        enabled=True,
    ).first()
    if policy is None:
        return None
    # Only a group translated in two ways can be left to choose, so the
    # diverging groups bound the comparison without building the queue.
    targets: dict[tuple[str, int], set[str]] = {}
    for source, plural_number, target in (
        policy_units(policy)
        .filter_access(user)
        .filter(state__gte=STATE_TRANSLATED)
        .order_by()
        .values_list("source", "translation__plural__number", "target")
        .distinct()
    ):
        targets.setdefault((source, plural_number), set()).add(target)
    groups = sum(len(values) > 1 for values in targets.values())
    return {
        "groups": groups,
        "requests": ceil(groups / REPEAT_RECOMMENDATION_BATCH_SIZE),
    }


def schedule_repeat_comparison(run: ProducerRun) -> None:
    """Publish the variant comparison once a completed queue check commits."""
    if run.status != ProducerRun.Status.COMPLETED or not is_repeat_judge_run(run):
        return
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import compare_repeat_variants

    run_id = str(run.pk)

    def publish() -> None:
        try:
            compare_repeat_variants.delay(run_id)
        except Exception:
            # The judge run is final; a lost publication only loses the
            # follow-up, which the queue can start again from a new check.
            LOGGER.exception("Failed to publish the repeat comparison for %s", run_id)

    transaction.on_commit(publish)


def compare_after_judge(run_id) -> dict[str, object] | None:
    """Compare the variants of checked groups after a queue check, once."""
    with transaction.atomic():
        run = ProducerRun.objects.select_for_update().filter(pk=run_id).first()
        if (
            run is None
            or run.status != ProducerRun.Status.COMPLETED
            or not is_repeat_judge_run(run)
            or COMPARISON_KEY in run.summary
        ):
            # A redelivered completion finds the recorded outcome.
            return None
        policy = repeat_judge_policy(run)
        if policy is None:
            # Launched outside a queue with an enabled rule.
            return None
        outcome = _start_comparison(run, policy)
        run.summary = {**run.summary, COMPARISON_KEY: outcome}
        run.save(update_fields=["summary"])
    return outcome


def _start_comparison(run: ProducerRun, policy: RepeatPolicy) -> dict[str, object]:
    actor = run.actor
    if actor is None:
        return {"status": "skipped", "reason": "no-actor"}
    if not actor.has_perm("project.edit", policy.project):
        return {"status": "skipped", "reason": "permission"}
    open_groups = [
        item
        for item in repeat_queue_groups(policy, user=actor)
        if item["status"] == "open"
    ]
    judgements = judge_groups(
        (item["group"].pk, item["variants"]) for item in open_groups
    )
    group_ids = sorted(
        group_id
        for group_id, judgement in judgements.items()
        # A rewrite group is compared too, so the model can propose new text.
        if judgement.bucket in {READY, CHOOSE, REWRITE}
    )
    if not group_ids:
        return {"status": "none", "groups": 0}
    try:
        # One request carries at least one group, so this cap leaves no
        # sendable group unsent; current results are never bought again.
        comparison = prepare_run(
            policy=policy,
            actor=actor,
            request_cap=len(group_ids),
            group_ids=group_ids,
        )
    except JudgeError:
        return {"status": "skipped", "reason": "judge-unavailable"}
    except PermissionDenied:
        return {"status": "skipped", "reason": "permission"}
    return {
        "status": "started",
        "run": comparison.pk,
        "groups": sum(
            bool(group.get("sendable", True)) for group in comparison.snapshot["groups"]
        ),
    }


def judge_launch_url(project_language, queue_url: str) -> str:
    """Open the standard judge form with the repeat scope filled in."""
    params = urlencode(
        {
            "mode": "judge",
            "q": REPEAT_JUDGE_QUERY,
            "judge_proposal_only": "1",
            "overwrite_existing": "",
            "next": queue_url,
        }
    )
    return f"{project_language.get_absolute_url()}?{params}#auto"


@dataclass(frozen=True)
class VariantJudgement:
    mark: str  # "passed" | "flagged" | "unchecked"
    back_translation: str
    reason: str


# Why a group's choice is preselected (D17), in priority order.
PICK_MODEL = "model"  # the model's pick of a variant the judge passed
PICK_ONLY_PASSED = "only-passed"  # the only variant the judge passed
PICK_APPROVED = "approved"  # the variant of an approved place, unless flagged
PICK_KEEP = "keep"  # the model says the places differ: do not link them
PICK_MOST_PASSED = "most-passed"  # the most-used of several passed variants
PICK_NEW_TEXT = "new-text"  # the model's single-form text, prefilled
PICK_UNCHECKED = "unchecked"  # the model's or the most-used unchecked variant
PICK_NONE = ""  # every variant is flagged


@dataclass(frozen=True)
class GroupJudgement:
    bucket: str
    # The preselected existing variant, else None (D17).
    recommended: tuple[str, ...] | None
    variants: dict[tuple[str, ...], VariantJudgement]
    # (unit id, verdict pk, target hash) for every judged place, sorted.
    evidence: tuple[tuple[int, int, str], ...]
    # Why the choice is preselected: a PICK_* code.
    pick: str = PICK_NONE

    @property
    def judge_only(self) -> tuple[str, ...] | None:
        """The only passed variant when it overrides the model (D17 rule 2)."""
        return self.recommended if self.pick == PICK_ONLY_PASSED else None


def _preselect(
    variants: list[dict],
    judgements: dict[tuple[str, ...], VariantJudgement],
    recommendation: RepeatRecommendationResult | None,
) -> tuple[str, tuple[str, ...] | None]:
    """Pick the best available choice for one group; the first rule wins."""
    action = recommendation.action if recommendation is not None else ""
    target = tuple(recommendation.target) if recommendation is not None else ()
    picked = judgements.get(target)
    mark = picked.mark if picked is not None else ""
    places = {variant["target"]: len(variant["units"]) for variant in variants}
    passed = [key for key, value in judgements.items() if value.mark == "passed"]
    unchecked = [key for key, value in judgements.items() if value.mark == "unchecked"]
    approved = [
        variant["target"]
        for variant in variants
        if judgements[variant["target"]].mark != "flagged"
        and any(unit.state == STATE_APPROVED for unit in variant["units"])
    ]
    # max() keeps the first of equals, so a tie follows the queue order.
    if action == "use_existing" and mark == "passed":
        return PICK_MODEL, target
    if len(passed) == 1:
        return PICK_ONLY_PASSED, passed[0]
    if approved:
        return PICK_APPROVED, max(approved, key=places.__getitem__)
    if action == "keep_independent":
        return PICK_KEEP, None
    if passed:
        return PICK_MOST_PASSED, max(passed, key=places.__getitem__)
    # The card prefills one field, so a plural proposal falls through.
    if action == "propose_new" and len(target) == 1 and mark != "flagged":
        return PICK_NEW_TEXT, None
    if action == "use_existing" and mark == "unchecked":
        return PICK_UNCHECKED, target
    if unchecked:
        return PICK_UNCHECKED, max(unchecked, key=places.__getitem__)
    # Every variant is flagged; a flagged variant is never preselected.
    return PICK_NONE, None


def settle_group(
    variants: list[dict],
    judgements: dict[tuple[str, ...], VariantJudgement],
    recommendation: RepeatRecommendationResult | None,
) -> tuple[str, str, tuple[str, ...] | None]:
    """
    Return one group's bucket, pick and preselected variant.

    The card, the bulk review and the banner all read this one answer.
    """
    approved = any(
        unit.state == STATE_APPROVED
        for variant in variants
        for unit in variant["units"]
    )
    passed_targets = [
        target for target, judgement in judgements.items() if judgement.mark == "passed"
    ]
    unchecked = any(judgement.mark == "unchecked" for judgement in judgements.values())
    if len(passed_targets) >= 2:
        bucket = CHOOSE
    elif unchecked:
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
    pick, recommended = _preselect(variants, judgements, recommendation)
    if bucket == CHOOSE and pick == PICK_MODEL and not approved and not unchecked:
        # The model picked one of several passed variants. An approved place
        # stays a human decision (D4) and every variant must be checked (D9).
        bucket = READY
    return bucket, pick, recommended


def judge_group(
    variants: list[dict],
    verdicts: dict[int, JudgeVerdict | None],
    recommendation: RepeatRecommendationResult | None = None,
) -> GroupJudgement:
    """Classify one repeat group using current, collegium-reduced verdicts."""
    judgements: dict[tuple[str, ...], VariantJudgement] = {}
    evidence: list[tuple[int, int, str]] = []

    for variant in variants:
        passed: list[tuple[int, JudgeVerdict]] = []
        flagged: list[tuple[int, JudgeVerdict]] = []
        for unit in variant["units"]:
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
        back_translation = reason = ""
        if passed:
            back_translation = min(passed, key=itemgetter(0))[1].back_translation
        if flagged:
            # A flagged variant shows what its strictest seat read, so a
            # producer can see when the rejected text is only a synonym.
            _, strictest = max(
                flagged,
                key=lambda item: (SEVERITY_RANK[item[1].effective_severity], -item[0]),
            )
            back_translation = strictest.back_translation
            primary = strictest.primary_error
            if primary is not None:
                category = primary.get("category", "")
                label = JUDGE_CATEGORY_LABELS.get(category, category)
                reason = f"{label}: {primary.get('description', '')}"
        judgements[variant["target"]] = VariantJudgement(mark, back_translation, reason)

    bucket, pick, recommended = settle_group(variants, judgements, recommendation)

    return GroupJudgement(
        bucket=bucket,
        recommended=recommended,
        variants=judgements,
        evidence=tuple(sorted(evidence)),
        pick=pick,
    )


def judge_groups(
    groups: Iterable[tuple[int, list[dict]]],
    recommendations: dict[int, RepeatRecommendationResult] | None = None,
) -> dict[int, GroupJudgement]:
    """Judge many groups with one active-verdict query for all their places."""
    grouped = list(groups)
    units = [
        unit
        for _, variants in grouped
        for variant in variants
        for unit in variant["units"]
    ]
    verdicts = active_verdicts(units)
    recommendations = recommendations or {}
    return {
        group_id: judge_group(variants, verdicts, recommendations.get(group_id))
        for group_id, variants in grouped
    }
