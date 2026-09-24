# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, bounded preparation of read-only repeat recommendations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from importlib import resources
from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from weblate.trans.judge import (
    judge_primary_endpoint,
    post_chat_completion,
    reasoning_payload,
    resolve_judge_seat_profile,
)
from weblate.trans.models import (
    LLMUsageLog,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeats import (
    detect_policy_groups,
    fingerprint,
    get_or_create_group,
    policy_units,
    unit_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.auth.models import User
    from weblate.trans.judge import JudgeSeatProfile
    from weblate.trans.models import Unit


LOGGER = logging.getLogger(__name__)
# Above the configured transport timeout plus its retries: a send that did not
# finish by then is reconciled as unknown instead of staying active forever.
REPEAT_ATTEMPT_DEADLINE = timedelta(minutes=30)


MAX_REPEAT_GROUP_PAYLOAD_BYTES = 128 * 1024

REPEAT_RECOMMENDATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["group", "action"],
                "properties": {
                    "group": {"type": "integer"},
                    "action": {
                        "type": "string",
                        "enum": [
                            "use_existing",
                            "propose_new",
                            "keep_independent",
                            "needs_human",
                        ],
                    },
                    "target": {"type": "array", "items": {"type": "string"}},
                    "exclusions": {"type": "array", "items": {"type": "integer"}},
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}


REPEAT_RECOMMENDATION_BATCH_SIZE = 25
MAX_REPEAT_REQUEST_BYTES = 128 * 1024


def queue_importance(source: str, place_count: int) -> tuple[bool, int]:
    """Order by what players see: short strings first, then the widest groups."""
    return (len(source.split()) > 3, -place_count)


def build_group_context(
    *, policy: RepeatPolicy, group: RepeatGroup, units: Iterable[Unit]
) -> dict[str, Any]:
    """Freeze every model input and scope field for one exact repeat group."""
    variants: dict[tuple[str, ...], int] = {}
    members = []
    for member in units:
        target = tuple(member.get_target_plurals())
        variants[target] = variants.get(target, 0) + 1
        members.append(
            {
                "unit": member.pk,
                "id_hash": member.id_hash,
                "component": member.translation.component.slug,
                "context": member.context,
                "explanation": member.source_unit.explanation,
                "labels": sorted(
                    member.source_unit.labels.values_list("name", flat=True)
                ),
                "source_forms": list(member.get_source_plurals()),
                "target_forms": list(target),
                "state": member.state,
                "unit_fingerprint": unit_fingerprint(member),
                "constraints": {
                    "flags": member.all_flags.format(),
                    "max_length": member.get_max_length(),
                },
            }
        )
    return {
        "policy_revision": policy.revision,
        "policy_enabled": policy.enabled,
        "group": group.pk,
        "group_revision": group.revision,
        "source_forms": list(group.source_forms),
        "plural_number": group.plural_number,
        "source_language": policy.source_language.code,
        "target_language": policy.target_language.code,
        "shared_target": list(group.shared_target),
        "decision_origin": group.decision_origin,
        "unit_ids": sorted(member.pk for member in units),
        "variants": [
            {"target_forms": list(target), "count": count}
            for target, count in sorted(
                variants.items(), key=lambda value: (-value[1], value[0])
            )
        ],
        "members": members,
    }


def context_fingerprint(context: dict[str, Any]) -> str:
    """Hash the canonical frozen context instead of trusting hand-set revisions."""
    return fingerprint(context)


def result_fingerprint(result: RepeatRecommendationResult) -> str:
    """Identify the reviewed decision content independently of its run."""
    return fingerprint(
        {
            "group": result.group_id,
            "action": result.action,
            "target": list(result.target),
            "exclusions": sorted(result.exclusions),
            "rationale": result.rationale,
        }
    )


def live_group_contexts(
    policy: RepeatPolicy, *, actor: User
) -> dict[tuple[tuple[str, ...], int], dict[str, Any]]:
    """Rebuild every visible group's context from current data in one pass."""
    visible_units = (
        policy_units(policy)
        .filter_access(actor)
        .select_related("source_unit", "translation__component")
        .prefetch_related("source_unit__labels")
    )
    groups = {
        (tuple(group.source_forms), group.plural_number): group
        for group in RepeatGroup.objects.filter(policy=policy)
    }
    contexts = {}
    for candidate in detect_policy_groups(policy, user=actor):
        identity = (tuple(candidate.source_forms), candidate.plural_number)
        units = list(visible_units.filter(pk__in=candidate.unit_ids).order_by("pk"))
        if not units:
            continue
        group = groups.get(identity)
        if group is None or list(group.source_forms) != list(candidate.source_forms):
            group = get_or_create_group(policy, units[0])
        contexts[identity] = build_group_context(
            policy=policy, group=group, units=units
        )
    return contexts


def recommendation_is_current(
    result: RepeatRecommendationResult,
    *,
    actor: User,
    contexts: dict | None = None,
) -> bool:
    """Tell whether one stored result still describes the group it points at."""
    # Legacy results carry no context fingerprint. They need a refresh instead
    # of being trusted as current.
    if not result.context_fingerprint:
        return False
    policy = result.group.policy
    if not policy.enabled:
        return False
    if contexts is None:
        contexts = live_group_contexts(policy, actor=actor)
    identity = (tuple(result.group.source_forms), result.group.plural_number)
    context = contexts.get(identity)
    return (
        context is not None
        and context["group"] == result.group_id
        and context_fingerprint(context) == result.context_fingerprint
    )


def current_recommendations(
    policy: RepeatPolicy, *, actor: User
) -> dict[int, RepeatRecommendationResult]:
    """Return each group's newest still-current result across all runs."""
    contexts = live_group_contexts(policy, actor=actor)
    current: dict[int, RepeatRecommendationResult] = {}
    for result in (
        RepeatRecommendationResult.objects.filter(group__policy=policy)
        .select_related("group", "group__policy", "attempt")
        .order_by("-run__created_at", "-run_id", "-id")
    ):
        if result.group_id in current:
            continue
        if (
            result.attempt_id is not None
            and result.attempt.status != RepeatRecommendationAttempt.Status.COMPLETED
        ):
            continue
        if recommendation_is_current(result, actor=actor, contexts=contexts):
            current[result.group_id] = result
    return current


@dataclass(frozen=True)
class RecommendationPlan:
    """The bounded outcome of one candidate selection and packing pass."""

    contexts: tuple[dict[str, Any], ...]
    requests: tuple[tuple[dict[str, Any], ...], ...]
    oversized: tuple[dict[str, Any], ...]
    unsent: int


def repeat_recommendation_prompt() -> str:
    """Load the packaged decision prompt from the trans/prompts package data."""
    return (
        resources.files("weblate.trans.prompts")
        .joinpath("repeat_recommendation.txt")
        .read_text(encoding="utf-8")
    )


def prompt_fingerprint() -> str:
    """Bind runs to the shipped prompt and schema, not to a revision string."""
    return fingerprint(
        {
            "prompt": repeat_recommendation_prompt(),
            "schema": REPEAT_RECOMMENDATION_RESPONSE_SCHEMA,
        }
    )


def request_payload(
    profile: JudgeSeatProfile, request_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Build one complete request body so packing can bound its serialized size."""
    response_format: dict[str, Any] = {"type": "json_object"}
    if profile.response_format == "json_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "repeat_recommendations",
                "strict": True,
                "schema": REPEAT_RECOMMENDATION_RESPONSE_SCHEMA,
            },
        }
    payload: dict[str, Any] = {
        "model": profile.model,
        "stream": False,
        "temperature": profile.temperature,
        "response_format": response_format,
        "messages": [
            {
                "role": "system",
                "content": repeat_recommendation_prompt(),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"untrusted_repeat_groups": request_snapshot},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    payload.update(reasoning_payload(profile))
    return payload


def request_size(profile: JudgeSeatProfile, groups: list[dict[str, Any]]) -> int:
    """Return the UTF-8 size of the complete request body for these groups."""
    return len(
        json.dumps(
            request_payload(profile, {"groups": groups}), ensure_ascii=False
        ).encode()
    )


def _reserved_contexts(policy: RepeatPolicy, statuses: set) -> set[tuple[int, str]]:
    """Return (group, context fingerprint) pairs covered by attempts in statuses."""
    pairs: set[tuple[int, str]] = set()
    runs = (
        RepeatRecommendationRun.objects.filter(
            policy=policy, attempts__status__in=statuses
        )
        .distinct()
        .prefetch_related("attempts")
    )
    for run in runs:
        frozen = {
            item["group"]: item.get("context_fingerprint", "")
            for item in run.snapshot.get("groups", [])
        }
        for attempt in run.attempts.all():
            if attempt.status not in statuses:
                continue
            pairs.update(
                (group["group"], frozen.get(group["group"], ""))
                for group in attempt.request_snapshot.get("groups", [])
            )
    return pairs


def plan_recommendations(
    *,
    policy: RepeatPolicy,
    actor: User,
    profile: JudgeSeatProfile,
    request_cap: int,
    refresh_group_ids: Iterable[int] = (),
) -> RecommendationPlan:
    """Select and pack paid candidates; shared by the preview page and the run."""
    refresh = set(refresh_group_ids)
    current = current_recommendations(policy, actor=actor)
    active = _reserved_contexts(
        policy,
        {
            RepeatRecommendationAttempt.Status.RESERVED,
            RepeatRecommendationAttempt.Status.SENT,
        },
    )
    unknown = _reserved_contexts(policy, {RepeatRecommendationAttempt.Status.UNKNOWN})
    candidates = []
    for context in live_group_contexts(policy, actor=actor).values():
        group_id = context["group"]
        identity = (group_id, context_fingerprint(context))
        if (
            len(context["variants"]) < 2
            or context["shared_target"]
            or context["decision_origin"]
        ):
            # Consistent and resolved groups never need a paid decision.
            continue
        if identity in active:
            # An active reservation already covers this unchanged context.
            continue
        if group_id not in refresh and (group_id in current or identity in unknown):
            # Current results are never repurchased and an unknown paid send is
            # never replayed without explicit consent.
            continue
        candidates.append(context)
    candidates.sort(
        key=lambda context: queue_importance(
            context["source_forms"][0], len(context["unit_ids"])
        )
    )
    oversized = tuple(
        context
        for context in candidates
        if request_size(profile, [context]) > MAX_REPEAT_REQUEST_BYTES
    )
    oversized_ids = {context["group"] for context in oversized}
    sendable = [
        context for context in candidates if context["group"] not in oversized_ids
    ]
    requests: list[tuple[dict[str, Any], ...]] = []
    pending: list[dict[str, Any]] = []
    unsent = 0
    for context in sendable:
        over_batch = len(pending) >= REPEAT_RECOMMENDATION_BATCH_SIZE
        over_bytes = (
            request_size(profile, [*pending, context]) > MAX_REPEAT_REQUEST_BYTES
        )
        if pending and (over_batch or over_bytes):
            if len(requests) < request_cap:
                requests.append(tuple(pending))
                pending = []
            else:
                unsent += len(pending)
                pending = []
        if len(requests) >= request_cap:
            unsent += 1
            continue
        pending = [*pending, context]
    if pending:
        if len(requests) < request_cap:
            requests.append(tuple(pending))
        else:
            unsent += len(pending)
    return RecommendationPlan(
        contexts=tuple(candidates),
        requests=tuple(requests),
        oversized=oversized,
        unsent=unsent,
    )


def prepare_run(
    *,
    policy: RepeatPolicy,
    actor: User,
    request_cap: int,
    refresh_group_ids: Iterable[int] = (),
) -> RepeatRecommendationRun:
    """Freeze a visible policy scope and reserve its bounded paid requests."""
    if request_cap < 1:
        msg = "The recommendation request cap must be positive."
        raise ValidationError(msg)
    if not actor.has_perm("project.edit", policy.project):
        raise PermissionDenied
    # Passing the primary endpoint avoids resolve_judge_seat_profile()'s
    # pair-wide validation and therefore never consults/falls back to seat 2.
    profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
    with transaction.atomic():
        # Concurrent preparations serialize on this lock, and each one rechecks
        # results and reservations it could not have seen before taking it.
        policy = RepeatPolicy.objects.select_for_update().get(pk=policy.pk)
        plan = plan_recommendations(
            policy=policy,
            actor=actor,
            profile=profile,
            request_cap=request_cap,
            refresh_group_ids=refresh_group_ids,
        )
        oversized_ids = {context["group"] for context in plan.oversized}
        groups = [
            {
                **context,
                "sendable": context["group"] not in oversized_ids,
                "context_fingerprint": context_fingerprint(context),
            }
            for context in (*plan.contexts, *plan.oversized)
        ]
        snapshot = {"policy_revision": policy.revision, "groups": groups}
        run = RepeatRecommendationRun.objects.create(
            policy=policy,
            actor=actor,
            snapshot=snapshot,
            snapshot_fingerprint=fingerprint(snapshot),
            profile_fingerprint=profile.profile_fingerprint,
            prompt_fingerprint=prompt_fingerprint(),
            request_cap=request_cap,
            unsent_groups=plan.unsent,
        )
        for context in plan.oversized:
            RepeatRecommendationResult.objects.create(
                run=run,
                group_id=context["group"],
                group_revision=context["group_revision"],
                snapshot_fingerprint=run.snapshot_fingerprint,
                context_fingerprint=context_fingerprint(context),
                action="needs_human",
                rationale="The complete group context exceeds the recommendation limit.",
            )
        # All reservations are made inside this one transaction; publishing
        # happens only after it commits.
        attempts = [
            reserve_attempt(run=run, request_snapshot={"groups": list(contexts)})
            for contexts in plan.requests
        ]
        if not attempts:
            run.status = RepeatRecommendationRun.Status.COMPLETED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
    for attempt in attempts:
        queue_attempt(attempt=attempt)
    return run


def reserve_attempt(*, run: RepeatRecommendationRun, request_snapshot: dict):
    """Reserve one request before I/O; unknown sends are deliberately not replayed."""
    with transaction.atomic():
        run = RepeatRecommendationRun.objects.select_for_update().get(pk=run.pk)
        if run.status in {
            RepeatRecommendationRun.Status.CANCELLED,
            RepeatRecommendationRun.Status.COMPLETED,
            RepeatRecommendationRun.Status.FAILED,
        }:
            msg = "This recommendation run cannot send another request."
            raise ValidationError(msg)
        if run.requests_reserved >= run.request_cap:
            msg = "The recommendation request cap has been reached."
            raise ValidationError(msg)
        run.requests_reserved += 1
        run.status = RepeatRecommendationRun.Status.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["requests_reserved", "status", "started_at"])
        return RepeatRecommendationAttempt.objects.create(
            run=run,
            ordinal=run.requests_reserved,
            request_snapshot=request_snapshot,
        )


def queue_attempt(*, attempt: RepeatRecommendationAttempt) -> None:
    """Publish one pre-reserved attempt after commit at interactive priority."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import execute_repeat_recommendation_attempt

    # ruff: ignore[import-outside-top-level]
    from weblate.utils.celery import INTERACTIVE_TASK_PRIORITY

    def publish() -> None:
        try:
            execute_repeat_recommendation_attempt.apply_async(
                args=[attempt.pk], priority=INTERACTIVE_TASK_PRIORITY
            )
        except Exception:
            # A publication failure keeps the attempt RESERVED: the same
            # reservation is re-enqueued later without paying for a new one.
            LOGGER.exception(
                "Failed to publish repeat recommendation attempt %s", attempt.pk
            )

    transaction.on_commit(publish)


def cancel_run(*, run: RepeatRecommendationRun, actor: User) -> None:
    """Prevent future requests while preserving every durable attempt/result."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    run.status = RepeatRecommendationRun.Status.CANCELLED
    run.cancelled_at = timezone.now()
    run.save(update_fields=["status", "cancelled_at"])


def _response_content(payload: dict[str, Any]) -> str:
    """Extract the one structured assistant message from an OpenAI envelope."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        msg = "The recommendation response has no assistant content."
        raise ValidationError(msg) from error
    if not isinstance(content, str):
        msg = "The recommendation response content is invalid."
        raise ValidationError(msg)
    return content


def parse_results(*, attempt: RepeatRecommendationAttempt, content: str) -> list[dict]:
    """Accept only result objects that point into this attempt's frozen groups."""
    try:
        decoded = json.loads(content)
    except ValueError as error:
        msg = "The recommendation response is not JSON."
        raise ValidationError(msg) from error
    if not isinstance(decoded, dict) or set(decoded) != {"results"}:
        msg = "The recommendation response has an invalid schema."
        raise ValidationError(msg)
    results = decoded["results"]
    if not isinstance(results, list):
        msg = "The recommendation results must be a list."
        raise ValidationError(msg)
    groups = {
        item["group"]: item
        for item in attempt.request_snapshot.get("groups", [])
        if item.get("sendable", True)
    }
    accepted = []
    seen: set[int] = set()
    actions = {"use_existing", "propose_new", "keep_independent", "needs_human"}
    for result in results:
        if not isinstance(result, dict) or set(result) - {
            "group",
            "action",
            "target",
            "exclusions",
            "rationale",
        }:
            continue
        group_id = result.get("group")
        action = result.get("action")
        target = result.get("target", [])
        exclusions = result.get("exclusions", [])
        rationale = result.get("rationale", "")
        is_valid_shape = (
            isinstance(group_id, int)
            and action in actions
            and isinstance(target, list)
            and all(isinstance(value, str) for value in target)
            and isinstance(exclusions, list)
            and all(isinstance(value, int) for value in exclusions)
            and isinstance(rationale, str)
        )
        if not is_valid_shape or group_id not in groups or group_id in seen:
            continue
        if any(member not in groups[group_id]["unit_ids"] for member in exclusions):
            continue
        group = (
            RepeatGroup.objects.filter(
                pk=group_id,
                policy_id=attempt.run.policy_id,
                revision=groups[group_id]["group_revision"],
            )
            .only("plural_number", "source_forms")
            .first()
        )
        if group is None:
            continue
        if action == "propose_new":
            expected_forms = group.plural_number if len(group.source_forms) > 1 else 1
            if len(target) != expected_forms:
                continue
        if action == "use_existing" and (
            not target
            or not any(
                target == variant["target_forms"]
                for variant in groups[group_id]["variants"]
            )
        ):
            continue
        accepted.append(result)
        seen.add(group_id)
    return accepted


def execute_attempt(*, attempt: RepeatRecommendationAttempt) -> None:
    """Claim, send and persist one attempt without ever replaying a paid send."""
    with transaction.atomic():
        attempt = (
            RepeatRecommendationAttempt.objects.select_for_update(of=("self",))
            .select_related("run__policy__project", "run__actor")
            .get(pk=attempt.pk)
        )
        # A duplicate delivery of a claimed or terminal attempt never issues
        # another request.
        if attempt.status != RepeatRecommendationAttempt.Status.RESERVED:
            return
        run = attempt.run
        if run.cancelled_at is not None:
            _fail_attempt(attempt, "cancelled")
            finalize_run(run.pk)
            return
        if run.actor is None or not bool(
            run.actor.has_perm("project.edit", run.policy.project)
        ):
            _fail_attempt(attempt, "permission-changed")
            finalize_run(run.pk)
            return
        requested_unit_ids = {
            unit_id
            for group in attempt.request_snapshot.get("groups", [])
            for unit_id in group["unit_ids"]
        }
        visible_unit_ids = set(
            policy_units(run.policy)
            .filter_access(run.actor)
            .filter(pk__in=requested_unit_ids)
            .values_list("pk", flat=True)
        )
        if visible_unit_ids != requested_unit_ids:
            _fail_attempt(attempt, "access-changed")
            finalize_run(run.pk)
            return
        profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
        if profile.profile_fingerprint != run.profile_fingerprint:
            _fail_attempt(attempt, "profile-changed")
            finalize_run(run.pk)
            return
        attempt.status = RepeatRecommendationAttempt.Status.SENT
        attempt.sent_at = timezone.now()
        attempt.deadline_at = attempt.sent_at + REPEAT_ATTEMPT_DEADLINE
        attempt.save(update_fields=["status", "sent_at", "deadline_at"])
        run_id = run.pk
        attempt_id = attempt.pk
        request_snapshot = attempt.request_snapshot
    payload = request_payload(profile, request_snapshot)
    try:
        response = post_chat_completion(
            payload, profile, title="HCGameLoc Weblate - Repeat recommendations"
        )
    except Exception as error:
        # The provider may have received the request even though the worker did
        # not obtain a response. Its reservation is intentionally not replayed.
        _store_attempt_outcome(
            attempt_id,
            status=RepeatRecommendationAttempt.Status.UNKNOWN,
            failure=type(error).__name__,
        )
        finalize_run(run_id)
        return
    usage = (response.payload or {}).get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    LLMUsageLog.objects.create(
        model=profile.model,
        service=profile.provider,
        project_slug=run.policy.project.slug,
        project_id_snapshot=run.policy.project_id,
        target_language_code=run.policy.target_language.code,
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        cost_usd=response.provider_cost,
        operation=LLMUsageLog.Operation.REPEAT_RECOMMEND,
        batch_size=len(request_snapshot.get("groups", [])),
        repeat_recommendation_run=run,
        outcome=(
            LLMUsageLog.Outcome.APPLIED
            if response.transport_succeeded
            else LLMUsageLog.Outcome.REFUSED
        ),
    )
    if not response.transport_succeeded:
        _store_attempt_outcome(
            attempt_id,
            status=RepeatRecommendationAttempt.Status.UNKNOWN,
            failure=response.failure_kind or "transport",
        )
        finalize_run(run_id)
        return
    try:
        accepted = parse_results(
            attempt=attempt, content=_response_content(response.payload or {})
        )
    except ValidationError as error:
        _store_attempt_outcome(
            attempt_id,
            status=RepeatRecommendationAttempt.Status.FAILED,
            failure=str(error),
        )
        finalize_run(run_id)
        return
    _store_attempt_outcome(
        attempt_id,
        status=RepeatRecommendationAttempt.Status.COMPLETED,
        response={"accepted": len(accepted)},
        accepted=accepted,
    )
    finalize_run(run_id)


def _fail_attempt(attempt: RepeatRecommendationAttempt, code: str) -> None:
    """Record a pre-flight refusal as a terminal failed attempt."""
    attempt.status = RepeatRecommendationAttempt.Status.FAILED
    attempt.failure = code
    attempt.completed_at = timezone.now()
    attempt.save(update_fields=["status", "failure", "completed_at"])


def _store_attempt_outcome(
    attempt_id: int,
    *,
    status: str,
    failure: str = "",
    response: dict[str, Any] | None = None,
    accepted: list[dict] | None = None,
) -> bool:
    """Finalize one claimed attempt and its results in a single transaction."""
    with transaction.atomic():
        attempt = (
            RepeatRecommendationAttempt.objects.select_for_update(of=("self",))
            .select_related("run")
            .get(pk=attempt_id)
        )
        # Terminal attempts are immutable: an expiry reconciliation and a late
        # response can never both finalize the same attempt.
        if attempt.status != RepeatRecommendationAttempt.Status.SENT:
            return False
        run = attempt.run
        attempt.status = status
        attempt.failure = failure
        attempt.completed_at = timezone.now()
        fields = ["status", "failure", "completed_at"]
        if response is not None:
            attempt.response = response
            fields.append("response")
        attempt.save(update_fields=fields)
        frozen = {
            item["group"]: item for item in attempt.request_snapshot.get("groups", [])
        }
        # The frozen context fingerprint lives on the run's snapshot: the
        # request snapshot stays exactly the body the provider received.
        context_fingerprints = {
            item["group"]: item.get("context_fingerprint", "")
            for item in run.snapshot.get("groups", [])
        }
        returned = set()
        for result in accepted or []:
            group_item = frozen[result["group"]]
            returned.add(result["group"])
            RepeatRecommendationResult.objects.create(
                run=run,
                attempt=attempt,
                group_id=result["group"],
                group_revision=group_item["group_revision"],
                snapshot_fingerprint=run.snapshot_fingerprint,
                context_fingerprint=context_fingerprints.get(result["group"], ""),
                action=result["action"],
                target=result.get("target", []),
                exclusions=result.get("exclusions", []),
                rationale=result.get("rationale", ""),
            )
        if status == RepeatRecommendationAttempt.Status.COMPLETED:
            for group_id, group_item in frozen.items():
                if group_id in returned:
                    continue
                # A provider omission is a durable result of its own attempt,
                # never a reason to pay for that group again automatically.
                RepeatRecommendationResult.objects.create(
                    run=run,
                    attempt=attempt,
                    group_id=group_id,
                    group_revision=group_item["group_revision"],
                    snapshot_fingerprint=run.snapshot_fingerprint,
                    context_fingerprint=context_fingerprints.get(group_id, ""),
                    action="needs_human",
                    rationale="The recommendation response did not include this group.",
                )
    return True


def finalize_run(run_id: int) -> None:
    """Recompute one run's state from its durable attempts under its lock."""
    with transaction.atomic():
        run = RepeatRecommendationRun.objects.select_for_update().get(pk=run_id)
        attempts = list(run.attempts.order_by("pk").only("status", "failure"))
        statuses = {attempt.status for attempt in attempts}
        active = {
            RepeatRecommendationAttempt.Status.RESERVED,
            RepeatRecommendationAttempt.Status.SENT,
        }
        if run.cancelled_at is not None:
            status = RepeatRecommendationRun.Status.CANCELLED
        elif statuses & active:
            status = RepeatRecommendationRun.Status.RUNNING
        elif statuses & {RepeatRecommendationAttempt.Status.UNKNOWN}:
            status = RepeatRecommendationRun.Status.UNKNOWN
        elif statuses & {RepeatRecommendationAttempt.Status.FAILED}:
            status = RepeatRecommendationRun.Status.FAILED
        else:
            # All attempts completed, or the run only has local outcomes.
            status = RepeatRecommendationRun.Status.COMPLETED
        fields = ["status"]
        run.status = status
        terminal = status not in {
            RepeatRecommendationRun.Status.QUEUED,
            RepeatRecommendationRun.Status.RUNNING,
        }
        if terminal:
            failures = [attempt.failure for attempt in attempts if attempt.failure]
            if failures:
                run.failure = failures[-1]
                fields.append("failure")
        if terminal and run.finished_at is None:
            run.finished_at = timezone.now()
            fields.append("finished_at")
        elif not terminal and run.finished_at is not None:
            run.finished_at = None
            fields.append("finished_at")
        run.save(update_fields=fields)


def reconcile_expired_attempts(*, run: RepeatRecommendationRun, actor: User) -> int:
    """Mark expired in-flight sends as unknown without any network I/O."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    changed = 0
    with transaction.atomic():
        for attempt in run.attempts.select_for_update().filter(
            status=RepeatRecommendationAttempt.Status.SENT,
            deadline_at__lt=timezone.now(),
        ):
            attempt.status = RepeatRecommendationAttempt.Status.UNKNOWN
            attempt.failure = "expired"
            attempt.completed_at = timezone.now()
            attempt.save(update_fields=["status", "failure", "completed_at"])
            changed += 1
    if changed:
        finalize_run(run.pk)
    return changed


def requeue_reserved_attempts(*, run: RepeatRecommendationRun, actor: User) -> int:
    """Re-enqueue reserved but unpublished attempts without new reservations."""
    if not actor.has_perm("project.edit", run.policy.project):
        raise PermissionDenied
    attempts = list(
        run.attempts.filter(status=RepeatRecommendationAttempt.Status.RESERVED)
    )
    for attempt in attempts:
        queue_attempt(attempt=attempt)
    return len(attempts)
