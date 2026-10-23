# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Confirmed bulk application and undo of reviewed repeat recommendations."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone

from weblate.trans.models import (
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatGroup,
    RepeatRecommendationResult,
)
from weblate.trans.repeat_judge import READY, judge_groups
from weblate.trans.repeat_recommendations import (
    build_group_context,
    context_fingerprint,
    current_recommendations,
    live_group_contexts,
    queue_importance,
    recommendation_is_current,
    result_fingerprint,
)
from weblate.trans.repeats import (
    apply_preview,
    blocked_reason,
    policy_overlaps,
    policy_units,
    preview_group,
    repeat_queue_groups,
    share_translations,
    undo_event,
)
from weblate.trans.util import join_plural
from weblate.utils.state import STATE_APPROVED

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.trans.models import Unit
    from weblate.trans.models.repeat import RepeatPolicy
    from weblate.trans.repeat_judge import GroupJudgement


LOGGER = logging.getLogger(__name__)
REPEAT_BULK_MANIFEST_SALT = "weblate.repeat-bulk-review"
# A one-to-two-hour review must fit comfortably inside the confirmation window.
REPEAT_BULK_MANIFEST_MAX_AGE = 4 * 60 * 60

# Stable, nonlocalized outcome codes; the UI maps them to translated reasons.
CODE_STALE = "stale"
CODE_NO_WRITE = "no-write"
CODE_ITEM_ERROR = "item-error"
CODE_PERMISSION_CHANGED = "permission-changed"
CODE_ACTOR_MISSING = "actor-missing"
CODE_POLICY_DISABLED = "policy-disabled"
CODE_LATER_DECISION = "later-decision"
CODE_UNDONE = "undone"

# Why an applicable result is outside the queue's ready bucket.
ATTENTION_NOT_OPEN = "not-open"
ATTENTION_NEW_TRANSLATION = "new-translation"
ATTENTION_FLAGGED = "flagged"
ATTENTION_UNCHECKED = "unchecked"
ATTENTION_APPROVED = "approved"
ATTENTION_DISAGREES = "disagrees"


@dataclass(frozen=True)
class BulkReviewRow:
    """One applicable recommendation rendered for review before any write."""

    result_id: int
    group_id: int
    source_forms: tuple[str, ...]
    target: tuple[str, ...]
    action: str
    rationale: str
    exclusions: tuple[int, ...]
    members: tuple[dict[str, Any], ...]
    writable: int
    already_matching: int
    blocked: dict[str, int]
    # "" when the queue shows the group as ready, else an ATTENTION_* code.
    attention: str
    # The judge's only passed variant replaces the model's result (D17 rule 2).
    judge_override: bool = False


@dataclass(frozen=True)
class BulkReview:
    """The frozen review table and its signed, expiring confirmation."""

    rows: tuple[BulkReviewRow, ...]
    independent: tuple[RepeatRecommendationResult, ...]
    needs_human: tuple[RepeatRecommendationResult, ...]
    stale: int
    manifest: str
    nonce: str
    expires_at: datetime


def _attention(
    action: str,
    target: tuple[str, ...],
    judgement: GroupJudgement | None,
    units: list[Unit],
) -> str:
    """Tell why a row is outside the queue's ready bucket, if it is."""
    if judgement is None:
        return ATTENTION_NOT_OPEN
    if judgement.bucket == READY:
        return ""
    if action == "propose_new":
        return ATTENTION_NEW_TRANSLATION
    picked = judgement.variants.get(target)
    if picked is not None and picked.mark == "flagged":
        return ATTENTION_FLAGGED
    if picked is None or any(
        variant.mark == "unchecked" for variant in judgement.variants.values()
    ):
        # A partly checked group is never ready, whatever it picks (D9).
        return ATTENTION_UNCHECKED
    if any(unit.state == STATE_APPROVED for unit in units):
        return ATTENTION_APPROVED
    return ATTENTION_DISAGREES


def _review_row(
    result: RepeatRecommendationResult,
    *,
    units: list[Unit],
    judgement: GroupJudgement | None,
    overlapping,
) -> BulkReviewRow:
    """Count one result's places with exactly the eligibility rules execution uses."""
    override = judgement.judge_only if judgement is not None else None
    target = list(override or result.target)
    action = "use_existing" if override else result.action
    exclusions = tuple(sorted(result.exclusions))
    excluded = set(exclusions)
    members = []
    blocked: dict[str, int] = {}
    already = writable = 0
    for unit in units:
        reason = blocked_reason(unit, target, overlapping)
        eligible = not reason and unit.get_target_plurals() != target
        members.append(
            {
                "unit_id": unit.pk,
                "eligible": eligible,
                "reason": reason or "already-matches",
                "excluded": unit.pk in excluded,
            }
        )
        if eligible:
            writable += unit.pk not in excluded
        elif reason:
            blocked[reason] = blocked.get(reason, 0) + 1
        else:
            already += 1
    return BulkReviewRow(
        result_id=result.pk,
        group_id=result.group_id,
        source_forms=tuple(result.group.source_forms),
        target=tuple(target),
        action=action,
        rationale=result.rationale,
        exclusions=exclusions,
        members=tuple(members),
        writable=writable,
        already_matching=already,
        blocked=blocked,
        attention=_attention(action, tuple(target), judgement, units),
        judge_override=bool(override),
    )


def review_target(
    result: RepeatRecommendationResult, *, actor: User
) -> tuple[str, ...] | None:
    """Return the target the review row of one result applies, if any."""
    item = next(
        (
            item
            for item in repeat_queue_groups(result.group.policy, user=actor)
            if item["group"].pk == result.group_id and item["status"] == "open"
        ),
        None,
    )
    if item is not None:
        judgement = judge_groups(
            [(result.group_id, item["variants"])], {result.group_id: result}
        )[result.group_id]
        if judgement.judge_only:
            return judgement.judge_only
    if result.action in {"use_existing", "propose_new"}:
        return tuple(result.target)
    return None


def plan_bulk(*, policy: RepeatPolicy, actor: User) -> BulkReview:
    """Freeze current recommendations into a review table and confirmation."""
    if not actor.has_perm("project.edit", policy.project):
        raise PermissionDenied
    current = current_recommendations(policy, actor=actor)
    # The same groups and judge buckets the queue shows, read once for every
    # row instead of one preview per group; apply still re-previews each one.
    queue = (
        {item["group"].pk: item for item in repeat_queue_groups(policy, user=actor)}
        if current
        else {}
    )
    judgements = judge_groups(
        (
            (group_id, item["variants"])
            for group_id, item in queue.items()
            if item["status"] == "open"
        ),
        current,
    )
    applicable = []
    independent = []
    needs_human = []
    for result in current.values():
        judgement = judgements.get(result.group_id)
        if judgement is not None and judgement.judge_only:
            # The card preselects the judge's only passed variant (D17).
            applicable.append(result)
        elif result.action == "keep_independent":
            independent.append(result)
        elif result.action == "needs_human":
            needs_human.append(result)
        elif result.action in {"use_existing", "propose_new"}:
            applicable.append(result)
    units = {
        result.group_id: queue[result.group_id]["units"]
        if result.group_id in queue
        else []
        for result in applicable
    }
    loaded = [unit for group_units in units.values() for unit in group_units]
    prefetch_related_objects(loaded, "source_unit")
    share_translations(loaded)
    overlapping = policy_overlaps(policy, exclude_policy_id=policy.pk)
    rows = [
        _review_row(
            result,
            units=units[result.group_id],
            judgement=judgements.get(result.group_id),
            overlapping=overlapping,
        )
        for result in applicable
    ]
    rows.sort(key=lambda row: queue_importance(row.source_forms[0], len(row.members)))
    stale = (
        RepeatRecommendationResult.objects.filter(group__policy=policy)
        .exclude(pk__in=[result.pk for result in current.values()])
        .order_by()
        .values("group_id")
        .distinct()
        .count()
    )
    nonce = uuid.uuid4().hex
    issued_at = timezone.now()
    expires_at = issued_at + timedelta(seconds=REPEAT_BULK_MANIFEST_MAX_AGE)
    payload = {
        "actor": actor.pk,
        "policy": policy.pk,
        "policy_revision": policy.revision,
        "nonce": nonce,
        "issued_at": issued_at.timestamp(),
        "expires_at": expires_at.timestamp(),
        "results": {
            str(row.result_id): {
                "result_fingerprint": result_fingerprint(current[row.group_id]),
                "context_fingerprint": current[row.group_id].context_fingerprint,
                # Only a judge override differs from the result's own target.
                **({"target": list(row.target)} if row.judge_override else {}),
            }
            for row in rows
        },
    }
    manifest = signing.dumps(payload, salt=REPEAT_BULK_MANIFEST_SALT, compress=True)
    return BulkReview(
        rows=tuple(rows),
        independent=tuple(independent),
        needs_human=tuple(needs_human),
        stale=stale,
        manifest=manifest,
        nonce=nonce,
        expires_at=expires_at,
    )


def load_manifest(*, policy: RepeatPolicy, actor: User, manifest: str) -> dict:
    """Verify the signed review confirmation and its actor/scope/expiry."""
    try:
        payload = signing.loads(manifest, salt=REPEAT_BULK_MANIFEST_SALT)
    except signing.BadSignature as error:
        msg = "The review confirmation is invalid."
        raise ValidationError(msg) from error
    if payload.get("actor") != actor.pk or payload.get("policy") != policy.pk:
        raise PermissionDenied
    if payload.get("policy_revision") != policy.revision:
        msg = "The repeat policy changed; refresh the review."
        raise ValidationError(msg)
    if timezone.now().timestamp() >= payload.get("expires_at", 0):
        msg = "The review confirmation expired; refresh the review."
        raise ValidationError(msg)
    return payload


def start_bulk(
    *,
    policy: RepeatPolicy,
    actor: User,
    manifest: str,
    result_ids: list[int],
) -> RepeatBulkRun:
    """Create one confirmed batch from exactly the reviewed results."""
    payload = load_manifest(policy=policy, actor=actor, manifest=manifest)
    if not actor.has_perm("project.edit", policy.project):
        raise PermissionDenied
    nonce = str(payload["nonce"])
    existing = RepeatBulkRun.objects.filter(review_nonce=nonce).first()
    if existing is not None:
        if existing.policy_id != policy.pk or existing.actor_id != actor.pk:
            raise PermissionDenied
        # A double click resolves to the already accepted batch.
        return existing
    selected = list(result_ids)
    if not selected:
        msg = "Select at least one recommendation to apply."
        raise ValidationError(msg)
    if len(set(selected)) != len(selected):
        msg = "A recommendation can only be selected once."
        raise ValidationError(msg)
    entries = payload["results"]
    if any(str(pk) not in entries for pk in selected):
        msg = "Only reviewed recommendations can be applied."
        raise ValidationError(msg)
    results = {
        result.pk: result
        for result in RepeatRecommendationResult.objects.filter(
            pk__in=selected
        ).select_related("group", "group__policy", "run", "attempt")
    }
    if set(results) != set(selected):
        msg = "A reviewed recommendation no longer exists; refresh the review."
        raise ValidationError(msg)
    contexts = live_group_contexts(policy, actor=actor)
    for pk in selected:
        result = results[pk]
        entry = entries[str(pk)]
        if result.group.policy_id != policy.pk:
            raise PermissionDenied
        if (
            result_fingerprint(result) != entry["result_fingerprint"]
            or result.context_fingerprint != entry["context_fingerprint"]
            or not recommendation_is_current(result, actor=actor, contexts=contexts)
        ):
            msg = "A recommendation changed since review; refresh the review."
            raise ValidationError(msg)
    try:
        with transaction.atomic():
            run = RepeatBulkRun.objects.create(
                policy=policy,
                actor=actor,
                action=RepeatBulkRun.Action.APPLY,
                review_nonce=nonce,
                total=len(selected),
                status=RepeatBulkRun.Status.QUEUED,
            )
            for ordinal, pk in enumerate(selected, start=1):
                result = results[pk]
                group = result.group
                RepeatBulkItem.objects.create(
                    run=run,
                    ordinal=ordinal,
                    group=group,
                    group_identity={
                        "source_forms": list(group.source_forms),
                        "plural_number": group.plural_number,
                    },
                    result=result,
                    decision={
                        "action": result.action,
                        # A judge override was frozen in the signed manifest.
                        "target": entries[str(pk)].get("target", list(result.target)),
                        "exclusions": list(result.exclusions),
                        "rationale": result.rationale,
                        "result_fingerprint": result_fingerprint(result),
                        **({"source": "judge"} if "target" in entries[str(pk)] else {}),
                    },
                    context_fingerprint=result.context_fingerprint,
                )
    except IntegrityError:
        # A concurrent double submission already created this batch.
        existing = RepeatBulkRun.objects.get(review_nonce=nonce)
        if existing.policy_id != policy.pk or existing.actor_id != actor.pk:
            raise PermissionDenied from None
        return existing
    queue_bulk_run(run)
    return run


def _create_undo_run(*, apply_run: RepeatBulkRun, actor: User) -> RepeatBulkRun:
    """Create one undo batch and its items in their own savepoint."""
    with transaction.atomic():
        undo_run = RepeatBulkRun.objects.create(
            policy=apply_run.policy,
            actor=actor,
            action=RepeatBulkRun.Action.UNDO,
            apply_run=apply_run,
            status=RepeatBulkRun.Status.QUEUED,
        )
        applied = list(apply_run.items.exclude(decision_event=None).order_by("ordinal"))
        for ordinal, item in enumerate(applied, start=1):
            RepeatBulkItem.objects.create(
                run=undo_run,
                ordinal=ordinal,
                group=item.group,
                group_identity=item.group_identity,
                apply_event=item.decision_event,
                decision=item.decision,
                context_fingerprint=item.context_fingerprint,
            )
        undo_run.total = len(applied)
        undo_run.save(update_fields=["total"])
    return undo_run


def start_undo(*, run: RepeatBulkRun, actor: User) -> RepeatBulkRun:
    """Create (or return) the single undo batch for one apply batch."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    with transaction.atomic():
        apply_run = RepeatBulkRun.objects.select_for_update().get(pk=run.pk)
        if apply_run.action != RepeatBulkRun.Action.APPLY:
            msg = "Only an apply batch can be undone."
            raise ValidationError(msg)
        if apply_run.status in {
            RepeatBulkRun.Status.QUEUED,
            RepeatBulkRun.Status.RUNNING,
        }:
            msg = "The batch is still being processed."
            raise ValidationError(msg)
        if not apply_run.items.exclude(decision_event__isnull=True).exists():
            msg = "This batch has no committed decisions to undo."
            raise ValidationError(msg)
        try:
            undo_run = _create_undo_run(apply_run=apply_run, actor=actor)
        except IntegrityError:
            # Concurrent callers get the one undo batch of this apply batch.
            undo_run = RepeatBulkRun.objects.get(apply_run=run)
    queue_bulk_run(undo_run)
    return undo_run


def resume_bulk(*, run: RepeatBulkRun, actor: User) -> None:
    """Requeue one batch's pending items without reinterpreting selections."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    if run.action == RepeatBulkRun.Action.APPLY and run.undo_runs.exists():
        msg = "An undone batch cannot be resumed."
        raise ValidationError(msg)
    if not run.items.filter(status=RepeatBulkItem.Status.PENDING).exists():
        msg = "This batch has no pending items."
        raise ValidationError(msg)
    if run.status == RepeatBulkRun.Status.FAILED:
        run.status = RepeatBulkRun.Status.QUEUED
        run.failure_code = ""
        run.save(update_fields=["status", "failure_code"])
    queue_bulk_run(run)


def queue_bulk_run(run: RepeatBulkRun) -> None:
    """Publish one batch after commit at interactive priority."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import process_repeat_bulk_apply, process_repeat_bulk_undo

    # ruff: ignore[import-outside-top-level]
    from weblate.utils.celery import INTERACTIVE_TASK_PRIORITY

    task = (
        process_repeat_bulk_undo
        if run.action == RepeatBulkRun.Action.UNDO
        else process_repeat_bulk_apply
    )

    def publish() -> None:
        try:
            task.apply_async(args=[run.pk], priority=INTERACTIVE_TASK_PRIORITY)
        except Exception:
            # The durable batch stays addressable: resume re-publishes it.
            LOGGER.exception("Failed to publish repeat bulk run %s", run.pk)

    transaction.on_commit(publish)


def _item_context(
    *, policy: RepeatPolicy, group: RepeatGroup, actor: User
) -> dict[str, Any]:
    """Rebuild one group's frozen context from current, visible data."""
    units = [
        unit
        for unit in policy_units(policy)
        .filter_access(actor)
        .filter(source=join_plural(group.source_forms))
        .select_related("source_unit", "translation__component")
        .prefetch_related("source_unit__labels")
        .order_by("pk")
        if tuple(unit.get_source_plurals()) == tuple(group.source_forms)
    ]
    return build_group_context(policy=policy, group=group, units=units)


def _refresh_counters(run: RepeatBulkRun) -> None:
    """Derive the parent counters from the authoritative item rows."""
    counters = {"done": 0, "written": 0, "restored": 0, "conflict": 0, "failed": 0}
    for item in run.items.all():
        if item.status != RepeatBulkItem.Status.PENDING:
            counters["done"] += 1
        if item.status == RepeatBulkItem.Status.FAILED:
            counters["failed"] += 1
        outcome = item.outcome or {}
        counters["written"] += len(outcome.get("written", []))
        counters["restored"] += len(outcome.get("restored", []))
        counters["conflict"] += len(outcome.get("conflicts", []))
    run.done = counters["done"]
    run.written = counters["written"]
    run.restored = counters["restored"]
    run.conflict = counters["conflict"]
    run.failed = counters["failed"]
    run.save(update_fields=["done", "written", "restored", "conflict", "failed"])


def _finalize_run(run: RepeatBulkRun) -> None:
    """Close one batch under its lock once every item is terminal."""
    if run.items.filter(status=RepeatBulkItem.Status.PENDING).exists():
        return
    _refresh_counters(run)
    run.status = RepeatBulkRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])


def _fail_run(run: RepeatBulkRun, code: str) -> None:
    """Stop one batch with a visible, resumable failure reason."""
    _refresh_counters(run)
    run.status = RepeatBulkRun.Status.FAILED
    run.failure_code = code
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "failure_code", "finished_at"])


def _load_actor(run: RepeatBulkRun) -> tuple[User | None, str]:
    """Reload the actor fresh: permission caches must not leak across items."""
    # ruff: ignore[import-outside-top-level]
    from weblate.auth.models import User

    if run.actor_id is None:
        return None, CODE_ACTOR_MISSING
    actor = User.objects.filter(pk=run.actor_id).first()
    if actor is None:
        return None, CODE_ACTOR_MISSING
    if not actor.has_perm("project.edit", run.policy.project):
        return None, CODE_PERMISSION_CHANGED
    if not run.policy.enabled:
        return None, CODE_POLICY_DISABLED
    return actor, ""


def process_apply_items(run_id: int) -> None:
    """Apply confirmed decisions item by item with resumable progress."""
    try:
        while process_next_apply_item(run_id):
            pass
    except Exception:
        LOGGER.exception("Repeat bulk apply %s failed", run_id)
        with transaction.atomic():
            run = RepeatBulkRun.objects.select_for_update().get(pk=run_id)
            if run.status in {
                RepeatBulkRun.Status.QUEUED,
                RepeatBulkRun.Status.RUNNING,
            }:
                _fail_run(run, CODE_ITEM_ERROR)


def process_undo_items(run_id: int) -> None:
    """Undo committed events item by item with resumable progress."""
    try:
        while process_next_undo_item(run_id):
            pass
    except Exception:
        LOGGER.exception("Repeat bulk undo %s failed", run_id)
        with transaction.atomic():
            run = RepeatBulkRun.objects.select_for_update().get(pk=run_id)
            if run.status in {
                RepeatBulkRun.Status.QUEUED,
                RepeatBulkRun.Status.RUNNING,
            }:
                _fail_run(run, CODE_ITEM_ERROR)


def process_next_apply_item(run_id: int) -> bool:
    """Apply exactly one pending item in one transaction; False when done."""
    with transaction.atomic():
        run = RepeatBulkRun.objects.select_for_update().get(pk=run_id)
        if run.action != RepeatBulkRun.Action.APPLY or run.status not in {
            RepeatBulkRun.Status.QUEUED,
            RepeatBulkRun.Status.RUNNING,
        }:
            return False
        item = (
            run.items.select_for_update()
            .filter(status=RepeatBulkItem.Status.PENDING)
            .order_by("ordinal")
            .first()
        )
        if item is None:
            _finalize_run(run)
            return False
        run.status = RepeatBulkRun.Status.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["status", "started_at"])
        actor, failure_code = _load_actor(run)
        if actor is None:
            _fail_run(run, failure_code)
            return False
        _apply_one_item(run=run, item=item, actor=actor)
        _refresh_counters(run)
    return True


def process_next_undo_item(run_id: int) -> bool:
    """Undo exactly one committed event in one transaction; False when done."""
    with transaction.atomic():
        run = RepeatBulkRun.objects.select_for_update().get(pk=run_id)
        if run.action != RepeatBulkRun.Action.UNDO or run.status not in {
            RepeatBulkRun.Status.QUEUED,
            RepeatBulkRun.Status.RUNNING,
        }:
            return False
        item = (
            run.items.select_for_update()
            .filter(status=RepeatBulkItem.Status.PENDING)
            .order_by("ordinal")
            .first()
        )
        if item is None:
            _finalize_run(run)
            return False
        run.status = RepeatBulkRun.Status.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["status", "started_at"])
        actor, failure_code = _load_actor(run)
        if actor is None:
            _fail_run(run, failure_code)
            return False
        _undo_one_item(run=run, item=item, actor=actor)
        _refresh_counters(run)
    return True


def _record_skip(*, item: RepeatBulkItem, code: str) -> None:
    item.status = RepeatBulkItem.Status.SKIPPED
    item.failure_code = code
    item.outcome = {"exclusions": sorted(item.decision.get("exclusions", []))}
    item.save(update_fields=["status", "failure_code", "outcome", "updated_at"])


def _apply_one_item(*, run: RepeatBulkRun, item: RepeatBulkItem, actor: User) -> None:
    """Apply one frozen decision; expected staleness records a skip."""
    if item.group_id is None:
        # The group identity is gone; its decision is not silently re-targeted.
        _record_skip(item=item, code=CODE_STALE)
        return
    try:
        event, skip_code = _apply_item_transaction(run=run, item=item, actor=actor)
    except ValidationError:
        # The expected edit wins: roll back and record a skipped item instead
        # of overwriting newer human work.
        _record_skip(item=item, code=CODE_STALE)
        return
    if skip_code or event is None:
        _record_skip(item=item, code=skip_code or CODE_STALE)
        return
    item.status = RepeatBulkItem.Status.APPLIED
    item.decision_event = event
    item.outcome = {**event.result, "exclusions": sorted(item.decision["exclusions"])}
    item.save(
        update_fields=[
            "status",
            "decision_event",
            "outcome",
            "failure_code",
            "updated_at",
        ]
    )


def _apply_item_transaction(
    *, run: RepeatBulkRun, item: RepeatBulkItem, actor: User
) -> tuple[RepeatDecisionEvent | None, str]:
    """Validate the frozen context and apply one decision in its savepoint."""
    with transaction.atomic():
        group = (
            RepeatGroup.objects.select_for_update()
            .select_related("policy")
            .get(pk=item.group_id)
        )
        context = _item_context(policy=run.policy, group=group, actor=actor)
        if context_fingerprint(context) != item.context_fingerprint:
            return None, CODE_STALE
        preview = preview_group(
            group=group, target=item.decision["target"], actor=actor
        )
        frozen_members = {
            member["unit"]: member["unit_fingerprint"] for member in context["members"]
        }
        preview_members = {
            member.unit_id: member.fingerprint for member in preview.members
        }
        if preview_members != frozen_members:
            # The recipient read and the preview read must agree exactly.
            return None, CODE_STALE
        exclusions = set(item.decision["exclusions"])
        if not exclusions <= set(preview_members):
            return None, CODE_STALE
        selected = [
            member.unit_id
            for member in preview.changing
            if member.unit_id not in exclusions
        ]
        if not selected:
            return None, CODE_NO_WRITE
        event = apply_preview(token=preview.token, actor=actor, unit_ids=selected)
    return event, ""


def _undo_one_item(*, run: RepeatBulkRun, item: RepeatBulkItem, actor: User) -> None:
    """Undo one committed event; recipient conflicts stay visible and linked."""
    try:
        undo = _undo_item_transaction(item=item, actor=actor)
    except ValidationError:
        item.status = RepeatBulkItem.Status.SKIPPED
        item.failure_code = CODE_LATER_DECISION
        item.save(update_fields=["status", "failure_code", "updated_at"])
        return
    if undo is None:
        _record_skip(item=item, code=CODE_STALE)
        return
    item.status = RepeatBulkItem.Status.UNDONE
    item.decision_event = undo
    item.outcome = {**undo.result, "code": CODE_UNDONE}
    item.save(update_fields=["status", "decision_event", "outcome", "updated_at"])


def _undo_item_transaction(
    *, item: RepeatBulkItem, actor: User
) -> RepeatDecisionEvent | None:
    """Undo one committed event in its savepoint; None when not undoable."""
    with transaction.atomic():
        if item.apply_event_id is None:
            return None
        return undo_event(token=str(item.apply_event.token), actor=actor)
