# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Text-free adapter to native recommendation, signed bulk review and audit."""

from __future__ import annotations

from operator import itemgetter
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DomainValidationError
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from weblate.api.producer.quality import collect_quality_snapshot, quality_repeat_groups
from weblate.trans.judge import JudgeError
from weblate.trans.judge_quality import (
    automatic_candidate_allowed,
    candidate_quality,
    current_quality_round,
    evaluate_target_quality,
    repeat_quality_revision,
)
from weblate.trans.models import Unit
from weblate.trans.models.judge import (
    JudgeApplication,
    JudgeVerdict,
    compute_decision_revision,
    compute_target_hash,
)
from weblate.trans.models.producer_quality import ProducerQualityOperation
from weblate.trans.models.repeat import (
    RepeatBulkRun,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_bulk import (
    REPEAT_BULK_MANIFEST_MAX_AGE,
    load_manifest,
    plan_bulk,
    start_bulk,
)
from weblate.trans.repeat_recommendations import (
    context_fingerprint,
    current_recommendations,
    live_group_contexts,
    prepare_run,
    reconcile_expired_attempts,
)
from weblate.trans.repeats import (
    ensure_default_policy,
    fingerprint,
    get_or_create_group,
    policy_units,
    unit_matches_policy,
)
from weblate.utils.state import STATE_APPROVED


class RepeatPlanConflict(APIException):
    status_code = 409
    default_detail = "The repeat plan is not ready or has expired."
    default_code = "repeat-plan-not-ready"


def _passed_targets(members):
    grouped: dict[tuple[str, ...], dict[str, bool]] = {}
    for unit in members:
        target = tuple(unit.get_target_plurals())
        rows = current_quality_round(unit)
        quality = candidate_quality(unit, target, evidence=rows)
        previous = grouped.setdefault(target, {"passed": False, "flagged": False})
        previous["passed"] |= quality.acceptable
        previous["flagged"] |= any(
            not row.unparsed
            and row.verdict in {JudgeVerdict.Verdict.FLAG, JudgeVerdict.Verdict.REJECT}
            for row in rows
        )
    return {
        target
        for target, mark in grouped.items()
        if mark["passed"] and not mark["flagged"]
    }


def select_canonical_candidate(*, members, preferred_applications, recommendation=None):
    """Prefer proven judge repairs; never resolve conflicting anchors by row order."""
    approved = {
        tuple(unit.get_target_plurals())
        for unit in members
        if unit.state == STATE_APPROVED
    }
    if len(approved) > 1:
        return None, "approved_conflict"
    passed = _passed_targets(members)
    preferred = {
        tuple(unit.get_target_plurals())
        for unit in members
        if unit.pk in preferred_applications
        and preferred_applications[unit.pk].applied_revision
        == compute_decision_revision(unit)
        and preferred_applications[unit.pk].candidate_target_hash
        == compute_target_hash(unit.get_target_plurals())
    }
    if preferred and not preferred <= passed:
        return None, "judge_anchor_not_passed"
    chosen = (
        tuple(recommendation.target)
        if recommendation is not None
        and recommendation.action == "use_existing"
        and tuple(recommendation.target) in passed
        else None
    )
    if len(preferred) == 1:
        target = next(iter(preferred))
        if approved and target not in approved:
            return None, "approved_conflict"
        return target, "judge_application"
    if len(preferred) > 1:
        if chosen is not None and (not approved or chosen in approved):
            return chosen, "recommendation"
        return None, "conflicting_judge_applications"
    if approved:
        target = next(iter(approved))
        return (
            (target, "approved")
            if target in passed
            else (None, "approved_not_verified")
        )
    if len(passed) == 1:
        return next(iter(passed)), "dual_judge"
    if chosen is not None:
        return chosen, "recommendation"
    return None, "no_verified_canonical"


def _make_ready(
    entry, *, members, target, origin, actor, scope_ids, recommendation=None
):
    group = entry["native_group"]
    policy = group.policy
    contexts = live_group_contexts(policy, actor=actor)
    context = contexts.get((tuple(group.source_forms), group.plural_number))
    if context is None:
        entry.update(outcome="stale", reason="group_changed")
        return
    native_units = list(
        policy_units(policy)
        .filter_access(actor)
        .filter(pk__in=context["unit_ids"])
        .prefetch()
        .prefetch_source()
        .order_by("pk")
    )
    selected_member_ids = {unit.pk for unit in members}
    current_members = [unit for unit in native_units if unit.pk in selected_member_ids]
    if (
        not members
        or {unit.pk for unit in current_members} != selected_member_ids
        or repeat_quality_revision(group, current_members)
        != entry["selection_revision"]
    ):
        entry.update(outcome="stale", reason="canonical_selection_changed")
        return
    frozen_revision = repeat_quality_revision(group, native_units)
    eligible = {
        unit.pk
        for unit in native_units
        if unit.pk in scope_ids
        and unit.get_target_plurals() != list(target)
        and automatic_candidate_allowed(unit, actor)
        and evaluate_target_quality(unit, target).clean
    }
    if not eligible:
        entry.update(outcome="needs_review", reason="no_safe_recipient")
        return
    if recommendation is None or tuple(recommendation.target) != target:
        # This is a local policy result, NOT a provider response or invented
        # judge vote. Native recommendations explicitly support attempt=None.
        snapshot = {
            "local_policy": "producer-quality-v2",
            "origin": origin,
            "groups": [context],
        }
        run = RepeatRecommendationRun.objects.create(
            policy=policy,
            actor=actor,
            snapshot=snapshot,
            snapshot_fingerprint=fingerprint(snapshot),
            profile_fingerprint=fingerprint(["local-policy", origin]),
            prompt_fingerprint=fingerprint(["producer-quality-v2"]),
            request_cap=0,
            status=RepeatRecommendationRun.Status.COMPLETED,
            finished_at=timezone.now(),
        )
        recommendation = RepeatRecommendationResult.objects.create(
            run=run,
            group=group,
            group_revision=group.revision,
            snapshot_fingerprint=run.snapshot_fingerprint,
            context_fingerprint=context_fingerprint(context),
            action="use_existing",
            target=list(target),
            exclusions=sorted(set(context["unit_ids"]) - eligible),
            rationale=f"Local producer canonical policy: {origin}",
        )
    review = plan_bulk(
        policy=policy,
        actor=actor,
        recipient_ids=sorted(scope_ids),
        automatic=True,
        group_ids=[group.pk],
    )
    row = next((row for row in review.rows if row.result_id == recommendation.pk), None)
    if row is None or row.target != target:
        entry.update(outcome="needs_review", reason="native_review_disagrees")
        return
    manifest = load_manifest(policy=policy, actor=actor, manifest=review.manifest)
    if (
        manifest["results"][str(recommendation.pk)].get("producer_revision")
        != frozen_revision
    ):
        entry.update(outcome="stale", reason="canonical_selection_changed")
        return
    entry.update(
        outcome="ready",
        reason="",
        preferred_origin=origin,
        manifest=review.manifest,
        result_id=recommendation.pk,
        revision=frozen_revision,
    )


def build_producer_repeat_plan(
    *, project, language, scope, issue_snapshot_hash, application_ids, actor
):
    if not actor.has_perm("project.edit", project):
        raise PermissionDenied
    scope_ids = set(scope["unit_ids"])
    snapshot = collect_quality_snapshot(
        project=project, language=language, unit_ids=sorted(scope_ids), user=actor
    )
    if snapshot["snapshot_hash"] != issue_snapshot_hash:
        raise ValidationError(
            {"issue_snapshot_hash": "The snapshot is no longer current."}
        )
    applications = list(
        JudgeApplication.objects.filter(
            pk__in=application_ids,
            actor=actor,
            unit_id__in=scope_ids,
            unit__translation__component__project=project,
            unit__translation__language__code=language,
            undone_at__isnull=True,
        )
    )
    if {app.pk for app in applications} != set(application_ids):
        raise ValidationError(
            {
                "application_ids": "Only current applications in this actor's scope are allowed."
            }
        )
    preferred = {app.unit_id: app for app in applications}
    units = quality_repeat_groups(
        project=project, language=language, user=actor, unit_ids=sorted(scope_ids)
    )
    if units:
        target_language = next(iter(units.values()))[0].translation.language
        ensure_default_policy(
            project=project, target_language=target_language, actor=actor
        )
    entries = []
    for issue in snapshot["issues"]:
        if issue["kind"] != "repeat-drift":
            continue
        members = units.get(issue["group_id"], [])
        entry = {
            "group_id": issue["group_id"],
            "unit_ids": issue["unit_ids"],
            "revision": fingerprint(issue),
            "outcome": "needs_review",
            "reason": "policy_unavailable",
            "preferred_origin": "",
        }
        entries.append(entry)
        if not members:
            entry.update(outcome="stale", reason="group_changed")
            continue
        policies = [
            policy
            for policy in RepeatPolicy.objects.filter(
                project=project, target_language__code=language, enabled=True
            ).select_related("project")
            if all(unit_matches_policy(unit, policy) for unit in members)
        ]
        if len(policies) != 1:
            continue
        group = get_or_create_group(policies[0], members[0])
        entry["native_group"] = group
        recommendation = current_recommendations(group.policy, actor=actor).get(
            group.pk
        )
        entry["selection_revision"] = repeat_quality_revision(group, members)
        target, origin = select_canonical_candidate(
            members=members,
            preferred_applications=preferred,
            recommendation=recommendation,
        )
        if target is not None:
            _make_ready(
                entry,
                members=members,
                target=target,
                origin=origin,
                actor=actor,
                scope_ids=scope_ids,
                recommendation=recommendation,
            )
        elif origin in {
            "approved_conflict",
            "approved_not_verified",
            "judge_anchor_not_passed",
        }:
            entry.update(
                outcome="approved_conflict"
                if origin == "approved_conflict"
                else "needs_review",
                reason=origin,
            )
        else:
            try:
                run = prepare_run(
                    policy=group.policy,
                    actor=actor,
                    request_cap=1,
                    group_ids=[group.pk],
                )
                entry.update(
                    outcome="pending", reason=origin, recommendation_run=run.pk
                )
            except (JudgeError, PermissionDenied):
                entry.update(
                    outcome="needs_review", reason="recommendation_unavailable"
                )
    for entry in entries:
        group = entry.pop("native_group", None)
        if group is not None:
            entry["native_group_id"] = group.pk
    return {
        "language": language,
        "unit_ids": sorted(scope_ids),
        "application_ids": application_ids,
        "groups": entries,
        "scope_revision": snapshot["scope_revision"],
    }


def project_repeat_plan(operation, actor):
    """Poll native recommendation work; never resend uncertain provider requests."""
    data = operation.private_data
    if (
        not data.get("applied_token")
        and (timezone.now() - operation.created).total_seconds()
        > REPEAT_BULK_MANIFEST_MAX_AGE
    ):
        return {
            "token": str(operation.pk),
            "status": "failed",
            "groups": [
                {
                    "group_id": entry["group_id"],
                    "revision": entry["revision"],
                    "unit_ids": entry["unit_ids"],
                    "outcome": "stale",
                    "reason": "plan_expired",
                    "preferred_origin": entry["preferred_origin"],
                }
                for entry in data["groups"]
            ],
        }
    if not actor.has_perm("project.edit", operation.project):
        return {
            "token": str(operation.pk),
            "status": "ready",
            "groups": [
                {
                    "group_id": entry["group_id"],
                    "revision": entry["revision"],
                    "unit_ids": entry["unit_ids"],
                    "outcome": "forbidden",
                    "reason": "permission_changed",
                    "preferred_origin": entry["preferred_origin"],
                }
                for entry in data["groups"]
            ],
        }
    dirty = False
    for entry in data["groups"]:
        if entry["outcome"] != "pending":
            continue
        run = RepeatRecommendationRun.objects.filter(
            pk=entry["recommendation_run"]
        ).first()
        if run is None:
            entry.update(outcome="failed", reason="recommendation_missing")
            dirty = True
            continue
        reconcile_expired_attempts(run=run, actor=actor)
        run.refresh_from_db()
        if run.status in {
            RepeatRecommendationRun.Status.QUEUED,
            RepeatRecommendationRun.Status.RUNNING,
        }:
            continue
        group = (
            RepeatGroup.objects.select_related("policy__project")
            .filter(pk=entry["native_group_id"])
            .first()
        )
        if group is None:
            entry.update(outcome="stale", reason="group_missing")
            dirty = True
            continue
        groups = quality_repeat_groups(
            project=operation.project,
            language=data["language"],
            user=actor,
            unit_ids=data["unit_ids"],
        )
        members = groups.get(entry["group_id"], [])
        preferred = {
            app.unit_id: app
            for app in JudgeApplication.objects.filter(
                pk__in=data["application_ids"], actor=actor, undone_at__isnull=True
            )
        }
        recommendation = current_recommendations(group.policy, actor=actor).get(
            group.pk
        )
        entry["selection_revision"] = repeat_quality_revision(group, members)
        target, origin = select_canonical_candidate(
            members=members,
            preferred_applications=preferred,
            recommendation=recommendation,
        )
        entry["native_group"] = group
        if target is None:
            entry.update(outcome="needs_review", reason="recommendation_not_acceptable")
        else:
            _make_ready(
                entry,
                members=members,
                target=target,
                origin=origin,
                actor=actor,
                scope_ids=set(data["unit_ids"]),
                recommendation=recommendation,
            )
        entry.pop("native_group", None)
        dirty = True
    if dirty:
        operation.private_data = data
        operation.save(update_fields=["private_data", "updated"])
    groups = [
        {
            key: entry[key]
            for key in (
                "group_id",
                "revision",
                "unit_ids",
                "outcome",
                "reason",
                "preferred_origin",
            )
        }
        for entry in data["groups"]
    ]
    return {
        "token": str(operation.pk),
        "status": "queued"
        if any(group["outcome"] == "pending" for group in groups)
        else "ready",
        "groups": groups,
    }


def _plan_recommendation(entry):
    result = (
        RepeatRecommendationResult.objects.select_related("group__policy")
        .filter(pk=entry["result_id"])
        .first()
    )
    if result is None:
        msg = "The recommendation no longer exists."
        raise DomainValidationError(msg)
    return result


def apply_producer_repeat_plan(*, plan, operation, actor):
    """Create native bulk resources once, bound to the original signed scope."""
    plan = ProducerQualityOperation.objects.select_for_update().get(pk=plan.pk)
    if not actor.has_perm("project.edit", plan.project):
        raise PermissionDenied
    previous = plan.private_data.get("applied_token")
    if previous:
        return ProducerQualityOperation.objects.get(pk=previous, actor=actor).result
    state = project_repeat_plan(plan, actor)
    if state["status"] != "ready":
        raise RepeatPlanConflict
    resources = []
    failures: list[dict[str, Any]] = []
    for entry in plan.private_data["groups"]:
        if entry["outcome"] != "ready":
            failures.extend(
                {"unit": unit_id, "outcome": "needs_review", "revision": ""}
                for unit_id in entry["unit_ids"]
            )
            continue
        try:
            result = _plan_recommendation(entry)
            run = start_bulk(
                policy=result.group.policy,
                actor=actor,
                manifest=entry["manifest"],
                result_ids=[result.pk],
            )
            resources.append(
                {
                    "run": run.pk,
                    "group_id": entry["group_id"],
                    "unit_ids": entry["unit_ids"],
                }
            )
        except DomainValidationError:
            failures.extend(
                {"unit": unit_id, "outcome": "stale", "revision": ""}
                for unit_id in entry["unit_ids"]
            )
    operation.private_data = {
        "runs": resources,
        "failures": failures,
        "plan": str(plan.pk),
    }
    operation.save(update_fields=["private_data", "updated"])
    plan.private_data["applied_token"] = str(operation.pk)
    plan.save(update_fields=["private_data", "updated"])
    return project_repeat_run(operation, actor)


def project_repeat_run(operation, actor):
    results = list(operation.private_data["failures"])
    running = False
    for resource in operation.private_data["runs"]:
        run = RepeatBulkRun.objects.filter(pk=resource["run"], actor=actor).first()
        if run is None:
            results.extend(
                {
                    "unit": unit_id,
                    "outcome": "failed",
                    "revision": "",
                    "reason": "bulk_run_missing",
                }
                for unit_id in resource["unit_ids"]
            )
            continue
        if run.status in {RepeatBulkRun.Status.QUEUED, RepeatBulkRun.Status.RUNNING}:
            running = True
            continue
        item = run.items.select_related("decision_event").first()
        event = item.decision_event if item is not None else None
        written = (
            {row["unit"]: row for row in event.result.get("written", [])}
            if event
            else {}
        )
        current = {
            unit.pk: unit
            for unit in Unit.objects.filter_access(actor).filter(
                pk__in=resource["unit_ids"]
            )
        }
        for unit_id in resource["unit_ids"]:
            unit = current.get(unit_id)
            revision = compute_decision_revision(unit) if unit is not None else ""
            if event is not None and unit_id in written:
                results.append(
                    {
                        "unit": unit_id,
                        "outcome": "applied",
                        "revision": written[unit_id].get("applied_revision", revision),
                        "event_id": str(event.token),
                    }
                )
            elif event:
                reason = next(
                    (
                        row["reason"]
                        for row in event.result.get("skipped", [])
                        if row["unit"] == unit_id
                    ),
                    "",
                )
                results.append(
                    {
                        "unit": unit_id,
                        "revision": revision,
                        "outcome": {
                            "already-matches": "no_change",
                            "protected": "forbidden",
                            "locked": "forbidden",
                            "approved": "approved",
                            "independent": "independent",
                        }.get(reason, "needs_review"),
                        "reason": reason or "excluded",
                    }
                )
            else:
                results.append(
                    {
                        "unit": unit_id,
                        "revision": revision,
                        "outcome": "stale"
                        if item and item.failure_code == "stale"
                        else "failed",
                    }
                )
    results.sort(key=itemgetter("unit"))
    return {
        "token": str(operation.pk),
        "status": "running"
        if running
        else "partial"
        if any(row["outcome"] not in {"applied", "no_change"} for row in results)
        else "completed",
        "changed_unit_ids": sorted(
            row["unit"] for row in results if row["outcome"] == "applied"
        ),
        "results": results,
    }
