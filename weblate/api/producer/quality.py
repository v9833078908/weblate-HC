# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only projections for the external producer quality cycle."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from operator import itemgetter
from typing import TYPE_CHECKING, cast

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F
from rest_framework.exceptions import ValidationError

from weblate.checks.consistency import RepeatDriftCheck
from weblate.checks.glossary import GLOSSARY_CHECK_ID, evaluate_glossary_terms
from weblate.checks.models import Check
from weblate.trans.judge import JUDGE_SEATS
from weblate.trans.judge_loop import active_judge_candidate
from weblate.trans.judge_quality import (
    automatic_candidate_allowed,
    candidate_quality,
    current_quality_round,
    evaluate_target_quality,
    glossary_quality_revision,
    project_quality_context_hash,
)
from weblate.trans.models import Project, Unit
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    candidate_round,
    collegium_verdict,
    compute_decision_revision,
    compute_target_hash,
)
from weblate.trans.models.repeat import RepeatGroup, RepeatMembership, RepeatPolicy
from weblate.utils.state import STATE_APPROVED, STATE_READONLY, STATE_TRANSLATED

if TYPE_CHECKING:
    from weblate.auth.models import User


def _seat_results(evidence: list[JudgeVerdict]) -> dict[str, str]:
    by_seat = {row.seat: row for row in evidence}
    return {
        str(seat): (
            "missing"
            if seat not in by_seat
            else "unparsed"
            if by_seat[seat].unparsed
            else "parsed"
        )
        for seat in JUDGE_SEATS
    }


def _issue_categories(evidence: list[JudgeVerdict]) -> list[str]:
    return sorted(
        {
            error["category"]
            for row in evidence
            for error in row.errors
            if isinstance(error, dict) and isinstance(error.get("category"), str)
        }
    )


def project_judge_decision(run_unit: JudgeRunUnit, user: User) -> dict[str, object]:
    """Project one run unit without returning source, target, or judge prompts."""
    unit = run_unit.unit
    if unit is None:
        return {
            "unit_id": run_unit.unit_id_snapshot,
            "run_unit_outcome": run_unit.outcome,
            "severity": run_unit.final_severity or run_unit.initial_severity,
            "seat_results": {str(seat): "missing" for seat in JUDGE_SEATS},
            "disagreement": False,
            "issue_categories": [],
            "candidate_id": None,
            "candidate_verified": False,
            "candidate_acceptable": False,
            "candidate_glossary_status": "",
            "live_seat_results": {str(seat): "missing" for seat in JUDGE_SEATS},
            "approval_loss": False,
            "terminology_conflict": False,
            "generation_outcome": "",
            "applicability": "stale",
            "revision": "",
        }

    live_evidence = list(
        JudgeVerdict.objects.filter(
            unit=unit,
            run_id=run_unit.run_id,
            subject=JudgeVerdict.Subject.LIVE,
        ).order_by("seat", "timestamp", "pk")
    )
    representative = run_unit.verdict
    candidate = active_judge_candidate(unit, representative)
    candidate_evidence = (
        candidate_round(unit, candidate.target_list) if candidate else []
    )
    candidate_seats = _seat_results(candidate_evidence)
    candidate_verified = all(
        candidate_seats[str(seat)] == "parsed" for seat in JUDGE_SEATS
    )
    quality = (
        candidate_quality(unit, candidate.target_list, evidence=candidate_evidence)
        if candidate
        else None
    )
    categories = _issue_categories(live_evidence)
    terminology_conflict = "terminology" in categories
    may_apply = user.has_perm("unit.review", unit) and user.has_perm(
        "translation.auto", unit.translation
    )

    if candidate is None:
        applicability = (
            "stale"
            if run_unit.repair_status == JudgeRunUnit.RepairStatus.CANDIDATE_STORED
            else "no_candidate"
        )
    elif not may_apply:
        applicability = "forbidden"
    elif not candidate_verified or quality is None or not quality.acceptable:
        applicability = "not_verified"
    elif unit.state == STATE_APPROVED:
        applicability = "needs_individual_confirmation"
    elif not automatic_candidate_allowed(unit, user):
        applicability = "forbidden"
    else:
        applicability = "ready"

    parsed_severities = {row.max_severity for row in live_evidence if not row.unparsed}
    return {
        "unit_id": unit.pk,
        "run_unit_outcome": run_unit.outcome,
        "severity": run_unit.final_severity or run_unit.initial_severity,
        "seat_results": candidate_seats,
        "disagreement": len(parsed_severities) > 1,
        "issue_categories": categories,
        "candidate_id": candidate.pk if candidate is not None else None,
        "candidate_verified": candidate_verified,
        "candidate_acceptable": applicability == "ready",
        "candidate_glossary_status": (
            "hard"
            if quality and quality.target.glossary_hard
            else "advisory"
            if quality and quality.target.glossary_advisory
            else "clean"
            if quality
            else ""
        ),
        "live_seat_results": _seat_results(current_quality_round(unit)),
        "approval_loss": unit.state == STATE_APPROVED,
        "terminology_conflict": terminology_conflict,
        "generation_outcome": (
            representative.generation_outcome if representative is not None else ""
        ),
        "applicability": applicability,
        "revision": compute_decision_revision(unit),
    }


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            cls=DjangoJSONEncoder,
        ).encode()
    ).hexdigest()


def _variants(units: list) -> list[dict[str, object]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for unit in units:
        grouped[compute_target_hash(unit.get_target_plurals())].append(unit.pk)
    return [
        {"target_hash": target_hash, "unit_ids": sorted(unit_ids)}
        for target_hash, unit_ids in sorted(grouped.items())
    ]


def _glossary_strength(unit) -> tuple[bool, bool]:
    hard: set[str] = set()
    advisory: set[str] = set()
    sources = unit.get_source_plurals()
    targets = unit.get_target_plurals()
    source = sources[0]
    pair_hard, pair_advisory = evaluate_glossary_terms(unit, source, targets[0])
    hard |= pair_hard
    advisory |= pair_advisory
    if len(sources) > 1:
        source = sources[1]
    for target in targets[1:]:
        pair_hard, pair_advisory = evaluate_glossary_terms(unit, source, target)
        hard |= pair_hard
        advisory |= pair_advisory
    return bool(hard), bool(advisory - hard)


def project_quality_issues(
    *, project, language: str, kinds: list[str], user
) -> dict[str, object]:
    """Return structured active repeat/glossary issues without translation text."""
    names = {
        "repeat-drift": "repeat-drift",
        "glossary": GLOSSARY_CHECK_ID,
    }
    requested_names = [names[kind] for kind in kinds]
    checks = (
        Check.objects.filter_access(user)
        .filter(
            dismissed=False,
            name__in=requested_names,
            unit__translation__component__project=project,
            unit__translation__language__code=language,
        )
        .select_related(
            "unit",
            "unit__translation",
            "unit__translation__component",
            "unit__source_unit",
        )
        .order_by("unit_id", "name")
    )
    repeat_groups: dict[tuple[str, int], list] = defaultdict(list)
    glossary_units = []
    for check in checks:
        if check.name == "repeat-drift":
            repeat_groups[check.unit.source, check.unit.translation.plural_id].append(
                check.unit
            )
        else:
            glossary_units.append(check.unit)

    issues: list[dict[str, object]] = []
    for (source, plural_id), units in sorted(repeat_groups.items()):
        approved_variants = {
            compute_target_hash(unit.get_target_plurals())
            for unit in units
            if unit.state == STATE_APPROVED
        }
        approved_conflict = len(approved_variants) > 1
        issues.append(
            {
                "group_id": f"repeat:{_digest([project.pk, language, plural_id, source])}",
                "kind": "repeat-drift",
                "hard": False,
                "advisory": True,
                "unit_ids": sorted(unit.pk for unit in units),
                "revisions": {
                    str(unit.pk): compute_decision_revision(unit) for unit in units
                },
                "variants": _variants(units),
                "term_mode": "",
                "approved_conflict": approved_conflict,
                "auto_repair_eligible": not approved_conflict,
            }
        )
    if "glossary" in kinds:
        glossary_units = list(
            quality_units(project=project, language=language, user=user)
        )
    for unit in glossary_units:
        hard, advisory = _glossary_strength(unit)
        if not hard and not advisory:
            continue
        issues.append(
            {
                "group_id": f"glossary:{unit.pk}",
                "kind": "glossary",
                "hard": hard,
                "advisory": advisory or not hard,
                "unit_ids": [unit.pk],
                "revisions": {str(unit.pk): compute_decision_revision(unit)},
                "variants": _variants([unit]),
                "term_mode": (
                    "mixed" if hard and advisory else "hard" if hard else "inflectable"
                ),
                "approved_conflict": False,
                "auto_repair_eligible": hard and unit.state != STATE_APPROVED,
            }
        )
    issues.sort(key=lambda issue: (str(issue["kind"]), str(issue["group_id"])))
    return {"snapshot_hash": _digest(issues), "issues": issues}


def quality_units(*, project, language: str, user):
    """Only visible target strings, never source strings or glossary definitions."""
    return (
        Unit.objects.filter_access(user)
        .filter(
            translation__component__project=project,
            translation__language__code=language,
            translation__component__is_glossary=False,
        )
        .exclude(
            translation__language_id=F("translation__component__source_language_id")
        )
        .prefetch()
        .prefetch_source()
        .order_by("pk")
    )


def repeat_issue_id(project, language: str, unit: Unit) -> str:
    return f"repeat:{_digest([project.pk, language, unit.translation.plural_id, unit.source])}"


def quality_repeat_groups(*, project, language: str, user, unit_ids: list[int]):
    """Derive exact groups directly using the native repeat-member exclusions."""
    selected = list(
        quality_units(project=project, language=language, user=user).filter(
            pk__in=unit_ids
        )
    )
    shapes = {(unit.source, unit.translation.plural_id) for unit in selected}
    candidates = quality_units(project=project, language=language, user=user).filter(
        source__in={source for source, _plural in shapes},
        state__gte=STATE_TRANSLATED,
        state__lt=STATE_READONLY,
        translation__component__allow_translation_propagation=True,
    )
    grouped = defaultdict(list)
    for unit in RepeatDriftCheck().get_repeat_members(candidates):
        shape = (unit.source, unit.translation.plural_id)
        if shape in shapes:
            grouped[repeat_issue_id(project, language, unit)].append(unit)
    selected_ids = {unit.pk for unit in selected}
    return {
        key: members
        for key, members in grouped.items()
        if any(unit.pk in selected_ids for unit in members)
    }


def _scope_revision(
    *, project, language: str, units: list[Unit], repeat_units: list[Unit]
):
    """Hash content, audit revisions and every policy that can invalidate quality."""
    # Re-read project fields and cached glossary membership for each side of
    # the barrier. Reusing the request's Project would hide a concurrent edit.
    project = Project.objects.get(pk=project.pk)
    # The first scope read must remain authoritative here: a newer peer read
    # must not hide a concurrent edit of a selected row from the A/B barrier.
    rows = [
        [
            unit.pk,
            compute_decision_revision(unit),
            unit.source,
            unit.target,
            unit.context,
            unit.state,
            unit.all_flags.format(),
            compute_decision_revision(unit.source_unit),
            unit.source_unit.note,
            unit.source_unit.explanation,
            unit.translation.component.locked,
            unit.translation.component.allow_translation_propagation,
            unit.translation.plural_id,
            project_quality_context_hash(unit.translation.component.project),
        ]
        for unit in {unit.pk: unit for unit in [*repeat_units, *units]}.values()
    ]
    glossary = glossary_quality_revision(project)
    policies = RepeatPolicy.objects.filter(
        project=project, target_language__code=language
    )
    policy_rows = [
        [
            policy.pk,
            policy.revision,
            policy.enabled,
            policy.updated,
            list(policy.components.order_by("pk").values_list("pk", flat=True)),
            list(policy.source_labels.order_by("pk").values_list("pk", flat=True)),
        ]
        for policy in policies.order_by("pk")
    ]
    groups = list(
        RepeatGroup.objects.filter(policy__in=policies)
        .order_by("pk")
        .values(
            "pk",
            "revision",
            "policy_id",
            "source_hash",
            "plural_number",
            "shared_target",
        )
    )
    memberships = list(
        RepeatMembership.objects.filter(group__policy__in=policies)
        .order_by("pk")
        .values("pk", "unit_id", "mode", "revision", "stale_at", "updated")
    )
    votes = list(
        JudgeVerdict.objects.filter(unit_id__in=[unit.pk for unit in units])
        .order_by("pk")
        .values(
            "pk",
            "unit_id",
            "subject",
            "target_hash",
            "context_hash",
            "seat",
            "max_severity",
            "unparsed",
            "resolution",
        )
    )
    return _digest(
        [
            sorted(rows),
            glossary,
            project_quality_context_hash(project),
            policy_rows,
            groups,
            memberships,
            votes,
        ]
    )


def collect_quality_snapshot(*, project, language: str, unit_ids: list[int], user):
    """Compute the whole closed scope without writing checks or invoking providers."""
    units = list(
        quality_units(project=project, language=language, user=user).filter(
            pk__in=unit_ids
        )
    )
    if {unit.pk for unit in units} != set(unit_ids):
        raise ValidationError(
            {
                "scope": "Every requested unit must be visible in this project and language."
            }
        )
    groups = quality_repeat_groups(
        project=project, language=language, user=user, unit_ids=unit_ids
    )
    peers = [unit for members in groups.values() for unit in members]
    before = _scope_revision(
        project=project, language=language, units=units, repeat_units=peers
    )
    revisions = {str(unit.pk): compute_decision_revision(unit) for unit in units}
    judged = {}
    issues = []
    selected = set(unit_ids)
    for group_id, members in sorted(groups.items()):
        if len({unit.target for unit in members}) < 2:
            continue
        scoped = [unit for unit in members if unit.pk in selected]
        approved = {unit.target for unit in members if unit.state == STATE_APPROVED}
        issues.append(
            {
                "group_id": group_id,
                "kind": "repeat-drift",
                "hard": True,
                "advisory": False,
                "unit_ids": sorted(unit.pk for unit in scoped),
                "approved_conflict": len(approved) > 1,
                "variants": _variants(scoped),
            }
        )
    for unit in units:
        if not unit.translation.component.allow_translation_propagation:
            # An excluded component is not proof of consistent repeats. Report
            # the missing prerequisite without changing its administrator policy.
            issues.append(
                {
                    "group_id": f"check:{unit.pk}:repeat-coverage",
                    "kind": "check",
                    "hard": True,
                    "advisory": False,
                    "unit_ids": [unit.pk],
                    "check_id": "repeat-coverage",
                    "reason": "translation_propagation_disabled",
                }
            )
        quality = evaluate_target_quality(unit, unit.get_target_plurals())
        if quality.glossary_hard or quality.glossary_advisory:
            issues.append(
                {
                    "group_id": f"glossary:{unit.pk}",
                    "kind": "glossary",
                    "hard": quality.glossary_hard,
                    "advisory": quality.glossary_advisory,
                    "unit_ids": [unit.pk],
                }
            )
        issues.extend(
            {
                "group_id": f"check:{unit.pk}:{name}",
                "kind": "check",
                "hard": True,
                "advisory": False,
                "unit_ids": [unit.pk],
                "check_id": name,
            }
            for name in quality.checks
        )
        evidence = current_quality_round(unit)
        verdict = collegium_verdict(evidence)
        if (
            len(evidence) == len(JUDGE_SEATS)
            and {row.seat for row in evidence} == set(JUDGE_SEATS)
            and all(not row.unparsed for row in evidence)
            and verdict is not None
            and verdict.verdict == JudgeVerdict.Verdict.PASS
        ):
            judged[str(unit.pk)] = revisions[str(unit.pk)]
    # A concurrent edit, glossary/policy change, or membership change must not
    # yield a supposedly complete snapshot assembled from different versions.
    fresh = list(
        quality_units(project=project, language=language, user=user).filter(
            pk__in=unit_ids
        )
    )
    fresh_groups = quality_repeat_groups(
        project=project, language=language, user=user, unit_ids=unit_ids
    )
    after = _scope_revision(
        project=project,
        language=language,
        units=fresh,
        repeat_units=[u for members in fresh_groups.values() for u in members],
    )
    if before != after:
        raise ValidationError(
            {
                "scope": "Quality context changed during the scan; request a fresh snapshot."
            }
        )
    issues.sort(key=itemgetter("kind", "group_id"))
    from weblate.trans.quality_execution import (  # ruff: ignore[import-outside-top-level]
        project_digest,
        protected_scope,
    )

    return {
        "complete": True,
        "unit_ids": sorted(unit_ids),
        "revisions": revisions,
        "judged_revisions": judged,
        "scope_revision": after,
        "issues": issues,
        "snapshot_hash": _digest([after, issues, judged]),
        "protected_content_digest": _digest(
            [project_digest(project), protected_scope(fresh)]
        ),
    }


def quality_recheck_snapshot(items: list[dict[str, object]]) -> str:
    """Hash the complete, ordered result of a bounded recheck request."""
    return _digest(
        sorted(items, key=lambda item: int(cast("int | str", item["unit_id"])))
    )
