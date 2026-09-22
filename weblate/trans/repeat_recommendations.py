# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, bounded preparation of read-only repeat recommendations."""

from __future__ import annotations

import json
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
    RepeatRecommendationAttempt,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeats import (
    detect_policy_groups,
    fingerprint,
    get_or_create_group,
    policy_units,
)

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.trans.models.repeat import RepeatPolicy


REPEAT_RECOMMENDATION_PROMPT_REVISION = "repeat-recommendation-v1"


def prepare_run(*, policy: RepeatPolicy, actor: User, request_cap: int):
    """Freeze a visible policy scope and its sole primary-seat profile."""
    if request_cap < 1:
        msg = "The recommendation request cap must be positive."
        raise ValidationError(msg)
    if not actor.has_perm("project.edit", policy.project):
        raise PermissionDenied
    # Passing the primary endpoint avoids resolve_judge_seat_profile()'s
    # pair-wide validation and therefore never consults/falls back to seat 2.
    profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
    groups = []
    for candidate in detect_policy_groups(policy):
        unit = policy_units(policy).get(pk=candidate.unit_ids[0])
        group = get_or_create_group(policy, unit)
        groups.append(
            {
                "group": group.pk,
                "group_revision": group.revision,
                "source_forms": list(candidate.source_forms),
                "unit_ids": list(candidate.unit_ids),
            }
        )
    snapshot = {"policy_revision": policy.revision, "groups": groups}
    return RepeatRecommendationRun.objects.create(
        policy=policy,
        actor=actor,
        snapshot=snapshot,
        snapshot_fingerprint=fingerprint(snapshot),
        profile_fingerprint=profile.profile_fingerprint,
        prompt_fingerprint=fingerprint(REPEAT_RECOMMENDATION_PROMPT_REVISION),
        request_cap=request_cap,
    )


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
    """Publish a pre-reserved attempt only after its transaction commits."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import execute_repeat_recommendation_attempt

    execute_repeat_recommendation_attempt.delay_on_commit(attempt.pk)


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


def parse_results(*, run: RepeatRecommendationRun, content: str) -> list[dict]:
    """Accept only result objects that point into this frozen run snapshot."""
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
    groups = {item["group"]: item for item in run.snapshot.get("groups", [])}
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
        if action == "propose_new" and not target:
            continue
        accepted.append(result)
        seen.add(group_id)
    return accepted


def execute_attempt(*, attempt: RepeatRecommendationAttempt) -> None:
    """Send one explicit request and persist only validated, read-only results."""
    attempt = RepeatRecommendationAttempt.objects.select_related("run__policy").get(
        pk=attempt.pk
    )
    if attempt.status != RepeatRecommendationAttempt.Status.RESERVED:
        return
    run = attempt.run
    if run.status == RepeatRecommendationRun.Status.CANCELLED:
        attempt.status = RepeatRecommendationAttempt.Status.FAILED
        attempt.failure = "cancelled"
        attempt.save(update_fields=["status", "failure"])
        return
    profile = resolve_judge_seat_profile(1, endpoint=judge_primary_endpoint())
    if profile.profile_fingerprint != run.profile_fingerprint:
        attempt.status = RepeatRecommendationAttempt.Status.FAILED
        attempt.failure = "profile-changed"
        attempt.save(update_fields=["status", "failure"])
        return
    payload: dict[str, Any] = {
        "model": profile.model,
        "stream": False,
        "temperature": profile.temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Return only JSON repeat recommendations."},
            {
                "role": "user",
                "content": json.dumps(attempt.request_snapshot, ensure_ascii=False),
            },
        ],
    }
    payload.update(reasoning_payload(profile))
    attempt.status = RepeatRecommendationAttempt.Status.SENT
    attempt.save(update_fields=["status"])
    response = post_chat_completion(
        payload, profile, title="HCGameLoc Weblate - Repeat recommendations"
    )
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
        batch_size=len(attempt.request_snapshot.get("groups", [])),
        repeat_recommendation_run=run,
        outcome=(
            LLMUsageLog.Outcome.APPLIED
            if response.transport_succeeded
            else LLMUsageLog.Outcome.REFUSED
        ),
    )
    if not response.transport_succeeded:
        attempt.status = RepeatRecommendationAttempt.Status.UNKNOWN
        attempt.failure = response.failure_kind or "transport"
        attempt.save(update_fields=["status", "failure"])
        return
    try:
        accepted = parse_results(
            run=run, content=_response_content(response.payload or {})
        )
    except ValidationError as error:
        attempt.status = RepeatRecommendationAttempt.Status.FAILED
        attempt.failure = str(error)
        attempt.save(update_fields=["status", "failure"])
        return
    for result in accepted:
        group_item = next(
            item for item in run.snapshot["groups"] if item["group"] == result["group"]
        )
        RepeatRecommendationResult.objects.update_or_create(
            run=run,
            group_id=result["group"],
            defaults={
                "group_revision": group_item["group_revision"],
                "snapshot_fingerprint": run.snapshot_fingerprint,
                "action": result["action"],
                "target": result.get("target", []),
                "exclusions": result.get("exclusions", []),
                "rationale": result.get("rationale", ""),
            },
        )
    attempt.status = RepeatRecommendationAttempt.Status.COMPLETED
    attempt.response = {"accepted": len(accepted)}
    attempt.completed_at = timezone.now()
    attempt.save(update_fields=["status", "response", "completed_at"])
