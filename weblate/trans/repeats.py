# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable scopes and freshness checks for explicitly managed exact repeats."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext

from weblate.trans.actions import ActionEvents
from weblate.trans.models import Unit
from weblate.trans.util import split_plural
from weblate.utils.state import STATE_APPROVED, STATE_READONLY, STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.auth.models import User
    from weblate.trans.models import Component, Label
    from weblate.trans.models.repeat import RepeatGroup, RepeatMembership, RepeatPolicy


def fingerprint(value: object) -> str:
    """Return a stable digest for a trusted, structured repeat snapshot."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_fingerprint(source_forms: list[str], plural_number: int) -> str:
    return fingerprint({"source_forms": source_forms, "plural_number": plural_number})


def unit_constraints(unit: Unit) -> dict[str, object]:
    """Record every Unit property that can make a shared target unsafe."""
    return {
        "flags": unit.all_flags.format(),
        "max_length": unit.get_max_length(),
        "plural_number": unit.translation.plural.number,
        "component_id": unit.translation.component_id,
        "restricted": unit.translation.component.restricted,
    }


def unit_fingerprint(unit: Unit) -> str:
    return fingerprint(
        {
            "translation_id": unit.translation_id,
            "id_hash": unit.id_hash,
            "source_forms": unit.get_source_plurals(),
            "context": unit.context,
            "target_forms": unit.get_target_plurals(),
            "state": unit.state,
            "constraints": unit_constraints(unit),
        }
    )


def policy_units(policy: RepeatPolicy):
    """Return the current writable-by-policy rows, without materializing membership."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit

    result = Unit.objects.filter(
        translation__component__project=policy.project,
        translation__component__in=policy.components.all(),
        translation__component__source_language=policy.source_language,
        translation__language=policy.target_language,
        translation__component__allow_translation_propagation=True,
        translation__component__is_glossary=False,
        state__lt=STATE_READONLY,
    )
    label_ids = list(policy.source_labels.values_list("id", flat=True))
    if label_ids:
        result = result.filter(source_unit__labels__in=label_ids).distinct()
    return result.select_related("translation__component", "translation__plural")


def unit_matches_policy(unit: Unit, policy: RepeatPolicy) -> bool:
    """Check scope from current data, never from a membership snapshot."""
    return policy_units(policy).filter(pk=unit.pk).exists()


def validate_policy_scope(policy: RepeatPolicy) -> None:
    """Reject empty and cross-project policy selectors before they become active."""
    policy.full_clean()
    if not policy.components.exists():
        raise ValidationError({"components": "Select at least one component."})


def policy_overlaps(policy: RepeatPolicy, *, exclude_policy_id: int | None = None):
    """Return active policies that currently select at least one same Unit."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatPolicy

    candidate_ids = policy_units(policy).values("pk")
    others = RepeatPolicy.objects.filter(
        project=policy.project,
        target_language=policy.target_language,
        enabled=True,
    )
    if exclude_policy_id is not None:
        others = others.exclude(pk=exclude_policy_id)
    return [
        other
        for other in others.prefetch_related("components", "source_labels")
        if policy_units(other).filter(pk__in=candidate_ids).exists()
    ]


def unit_has_policy_conflict(unit: Unit, policy: RepeatPolicy) -> bool:
    """Tell whether selector edits have made a formerly valid scope ambiguous."""
    # Rules are dynamic: a later source-label edit can overlap two policies
    # without either policy itself being saved again.
    return any(
        unit_matches_policy(unit, other)
        for other in policy_overlaps(policy, exclude_policy_id=policy.pk)
    )


def save_policy(
    *,
    policy: RepeatPolicy,
    components: Iterable[Component],
    labels: Iterable[Label],
    actor: User | None,
) -> RepeatPolicy:
    """Atomically persist a policy while preventing current scope overlap."""
    component_ids = {component.pk for component in components}
    label_ids = {label.pk for label in labels}
    if None in component_ids or None in label_ids:
        msg = "Policy selectors must be saved first."
        raise ValidationError(msg)
    with transaction.atomic():
        if policy.pk is None:
            policy.author = actor
            policy.save()
        policy.components.set(component_ids)
        policy.source_labels.set(label_ids)
        validate_policy_scope(policy)
        overlaps = policy_overlaps(policy, exclude_policy_id=policy.pk)
        if overlaps:
            raise ValidationError(
                {
                    "components": "This scope overlaps active repeat policies: {}.".format(
                        ", ".join(str(other.pk) for other in overlaps)
                    )
                }
            )
        policy.revision += 1
        policy.save(update_fields=["revision", "updated"])
    return policy


def get_or_create_group(policy: RepeatPolicy, unit: Unit) -> RepeatGroup:
    """Find the durable group identity for an exact source/plural shape."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatGroup

    source_forms = unit.get_source_plurals()
    plural_number = unit.translation.plural.number
    digest = source_fingerprint(source_forms, plural_number)
    group, created = RepeatGroup.objects.get_or_create(
        policy=policy,
        source_hash=digest,
        plural_number=plural_number,
        defaults={"source_forms": source_forms},
    )
    if not created and group.source_forms != source_forms:
        # A hash collision must never join unrelated sources.
        msg = "Repeat group fingerprint collision."
        raise ValidationError(msg)
    return group


def create_membership(
    *,
    group: RepeatGroup,
    unit: Unit,
    mode: str,
    reason: str = "",
):
    """Store only an explicit decision/exception, not every detected repeat."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatMembership

    snapshot = unit_constraints(unit)
    return RepeatMembership.objects.update_or_create(
        group=group,
        unit=unit,
        defaults={
            "translation_id_snapshot": unit.translation_id,
            "id_hash_snapshot": unit.id_hash,
            "source_forms_snapshot": unit.get_source_plurals(),
            "context_snapshot": unit.context,
            "constraints_snapshot": snapshot,
            "fingerprint": unit_fingerprint(unit),
            "mode": mode,
            "reason": reason,
            "stale_at": None,
        },
    )[0]


def reconcile_membership(membership: RepeatMembership) -> bool:
    """Mark a decision stale if its current identity/scope no longer agrees."""
    if membership.unit_id is None:
        if membership.stale_at is None:
            membership.stale_at = timezone.now()
            membership.save(update_fields=["stale_at", "updated"])
        return False
    unit = membership.unit
    current = (
        membership.translation_id_snapshot == unit.translation_id
        and membership.id_hash_snapshot == unit.id_hash
        and membership.fingerprint == unit_fingerprint(unit)
        and unit_matches_policy(unit, membership.group.policy)
    )
    if current != (membership.stale_at is None):
        membership.stale_at = None if current else timezone.now()
        membership.revision += 1
        membership.save(update_fields=["stale_at", "revision", "updated"])
    return current


def reconcile_unit(unit_id: int) -> None:
    """Reconcile all durable links for one Unit after the surrounding commit."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatMembership

    for membership in RepeatMembership.objects.filter(unit_id=unit_id).select_related(
        "unit__translation__component", "unit__translation__plural", "group__policy"
    ):
        reconcile_membership(membership)


def mark_unit_deleted(unit_id: int) -> None:
    """Preserve the membership row but prevent reuse of a removed Unit identity."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatMembership

    RepeatMembership.objects.filter(unit_id=unit_id, stale_at__isnull=True).update(
        stale_at=timezone.now()
    )


def schedule_unit_reconciliation(unit_id: int) -> None:
    """Defer the read/reconcile work until Unit writes are durable."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.tasks import reconcile_repeat_unit

    reconcile_repeat_unit.delay_on_commit(unit_id)


def current_shared_membership(unit: Unit):
    """Return a current explicit shared decision for one Unit, if any."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatMembership

    membership = (
        RepeatMembership.objects.filter(
            unit=unit,
            mode=RepeatMembership.Mode.SHARED,
            stale_at__isnull=True,
        )
        .select_related("group__policy")
        .first()
    )
    if membership is not None and not reconcile_membership(membership):
        return None
    if membership is not None and unit_has_policy_conflict(
        unit, membership.group.policy
    ):
        return None
    return membership


def accepted_shared_group(unit: Unit):
    """Resolve an accepted group for an existing or newly imported occurrence."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatGroup, RepeatMembership, RepeatPolicy

    explicit = (
        RepeatMembership.objects.filter(unit=unit, stale_at__isnull=True)
        .select_related("group__policy")
        .first()
    )
    if explicit is not None:
        if not reconcile_membership(explicit):
            return None
        if explicit.mode == RepeatMembership.Mode.INDEPENDENT:
            return None
        if explicit.group.shared_target and not unit_has_policy_conflict(
            unit, explicit.group.policy
        ):
            return explicit.group
        return None

    policies = RepeatPolicy.objects.filter(
        project=unit.translation.component.project,
        target_language=unit.translation.language,
        components=unit.translation.component,
        enabled=True,
    ).prefetch_related("components", "source_labels")
    matching = [policy for policy in policies if unit_matches_policy(unit, policy)]
    if len(matching) != 1:
        return None
    policy = matching[0]
    if unit_has_policy_conflict(unit, policy):
        return None
    return (
        RepeatGroup.objects.filter(
            policy=policy,
            source_hash=source_fingerprint(
                unit.get_source_plurals(), unit.translation.plural.number
            ),
            plural_number=unit.translation.plural.number,
        )
        .exclude(shared_target=[])
        .first()
    )


def make_membership_independent(*, unit: Unit, reason: str) -> bool:
    """Convert the current shared decision to an explicit independent exception."""
    membership = current_shared_membership(unit)
    if membership is None:
        return False
    membership.mode = "independent"
    membership.reason = reason
    membership.revision += 1
    membership.save(update_fields=["mode", "reason", "revision", "updated"])
    return True


@dataclass(frozen=True)
class RepeatCandidate:
    """A live detection group, with durable state looked up separately."""

    source_forms: tuple[str, ...]
    plural_number: int
    unit_ids: tuple[int, ...]


@dataclass(frozen=True)
class RepeatPreviewMember:
    """One server-derived recipient in a signed repeat-operation preview."""

    unit_id: int
    fingerprint: str
    target: tuple[str, ...]
    eligible: bool
    reason: str


@dataclass(frozen=True)
class RepeatPreview:
    """A short-lived scope token with no browser-controlled recipient list."""

    token: str
    group_id: int
    members: tuple[RepeatPreviewMember, ...]
    action: str = "apply"


def preview_group(
    *, group: RepeatGroup, target: list[str], actor: User
) -> RepeatPreview:
    """Create a signed, current snapshot for an explicit shared target."""
    members: list[RepeatPreviewMember] = []
    target = list(target)
    for unit in (
        policy_units(group.policy)
        .filter_access(actor)
        .filter(source=group.source_forms[0])
        .order_by("pk")
    ):
        if tuple(unit.get_source_plurals()) != tuple(group.source_forms):
            continue
        reason = ""
        if unit_has_policy_conflict(unit, group.policy):
            reason = "rule-conflict"
        elif unit.state == STATE_APPROVED and unit.get_target_plurals() != target:
            reason = "approved"
        elif unit.translation.component.locked:
            reason = "locked"
        elif any(len(value) > unit.get_max_length() for value in target):
            reason = "max-length"
        members.append(
            RepeatPreviewMember(
                unit_id=unit.pk,
                fingerprint=unit_fingerprint(unit),
                target=tuple(target),
                eligible=not reason and unit.get_target_plurals() != target,
                reason=reason or "already-matches",
            )
        )
    payload = {
        "actor": actor.pk,
        "group": group.pk,
        "group_revision": group.revision,
        "policy_revision": group.policy.revision,
        "target": target,
        "members": [
            {"id": member.unit_id, "fingerprint": member.fingerprint}
            for member in members
        ],
    }
    return RepeatPreview(
        token=signing.dumps(payload, salt="weblate.repeat-preview", compress=True),
        group_id=group.pk,
        members=tuple(members),
    )


def preview_keep_group(*, group: RepeatGroup, actor: User) -> RepeatPreview:
    """Create a signed no-write preview for an independent-group decision."""
    members = tuple(
        RepeatPreviewMember(
            unit_id=unit.pk,
            fingerprint=unit_fingerprint(unit),
            target=tuple(unit.get_target_plurals()),
            eligible=True,
            reason="",
        )
        for unit in policy_units(group.policy)
        .filter_access(actor)
        .filter(source=group.source_forms[0])
        .order_by("pk")
        if tuple(unit.get_source_plurals()) == tuple(group.source_forms)
    )
    payload = {
        "actor": actor.pk,
        "action": "keep",
        "group": group.pk,
        "group_revision": group.revision,
        "policy_revision": group.policy.revision,
        "members": [
            {"id": member.unit_id, "fingerprint": member.fingerprint}
            for member in members
        ],
    }
    return RepeatPreview(
        token=signing.dumps(payload, salt="weblate.repeat-preview", compress=True),
        group_id=group.pk,
        members=members,
        action="keep",
    )


def _load_preview(token: str, actor: User) -> dict[str, Any]:
    """Validate the signed operation snapshot and its actor binding."""
    try:
        preview = signing.loads(token, salt="weblate.repeat-preview", max_age=900)
    except signing.BadSignature as error:
        msg = "The repeat preview has expired."
        raise ValidationError(msg) from error
    if preview["actor"] != actor.pk:
        msg = "The repeat preview belongs to another user."
        raise ValidationError(msg)
    return preview


def apply_preview(*, token: str, actor: User, unit_ids: Iterable[int] | None = None):
    """Apply a fresh preview atomically through Unit.translate without propagation."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatDecisionEvent, RepeatGroup

    snapshot = _load_preview(token, actor)
    if snapshot.get("action") == "keep":
        return keep_group_independent(
            group_id=snapshot["group"], actor=actor, snapshot=snapshot
        )
    selected = set(unit_ids) if unit_ids is not None else None
    with transaction.atomic():
        group = (
            RepeatGroup.objects.select_for_update()
            .select_related("policy")
            .get(pk=snapshot["group"])
        )
        if (
            group.revision != snapshot["group_revision"]
            or group.policy.revision != snapshot["policy_revision"]
        ):
            msg = "The repeat group changed; refresh the preview."
            raise ValidationError(msg)
        expected = {item["id"]: item["fingerprint"] for item in snapshot["members"]}
        units = list(
            Unit.objects.select_for_update()
            .filter(pk__in=expected)
            .select_related("translation__component", "translation__plural")
            .order_by("pk")
        )
        if set(expected) != {unit.pk for unit in units} or any(
            unit_fingerprint(unit) != expected[unit.pk] for unit in units
        ):
            msg = "A repeat recipient changed; refresh the preview."
            raise ValidationError(msg)
        if any(
            not unit_matches_policy(unit, group.policy)
            or unit_has_policy_conflict(unit, group.policy)
            for unit in units
        ):
            msg = "The repeat policy scope changed; refresh the preview."
            raise ValidationError(msg)
        event = RepeatDecisionEvent.objects.create(
            group=group,
            action="apply",
            actor=actor,
            group_revision=group.revision,
            policy_revision=group.policy.revision,
            snapshot=snapshot,
        )
        result: dict[str, list[dict[str, object]]] = {"written": [], "skipped": []}
        translations = {}
        for unit in units:
            if selected is not None and unit.pk not in selected:
                result["skipped"].append({"unit": unit.pk, "reason": "not-selected"})
            elif (
                unit.state == STATE_APPROVED
                and unit.get_target_plurals() != snapshot["target"]
            ):
                result["skipped"].append({"unit": unit.pk, "reason": "approved"})
            elif unit.translation.component.locked or not actor.has_perm(
                "unit.edit", unit
            ):
                result["skipped"].append({"unit": unit.pk, "reason": "protected"})
            else:
                old = unit.get_target_plurals()
                translations[unit.translation_id] = unit.translation
                unit.is_batch_update = True
                unit.translate(
                    actor,
                    snapshot["target"],
                    STATE_TRANSLATED,
                    change_action=ActionEvents.REPEAT_APPLY,
                    propagate=False,
                    select_for_update=False,
                    change_details={"repeat_event": str(event.token)},
                )
                create_membership(
                    group=group, unit=unit, mode="shared", reason="applied"
                )
                result["written"].append(
                    {"unit": unit.pk, "old": old, "new": unit.get_target_plurals()}
                )
        for translation in translations.values():
            translation.store_update_changes()
            translation.invalidate_cache()
        group.shared_target = snapshot["target"]
        group.decision_origin = "manual"
        group.decision_author = actor
        group.decided_at = timezone.now()
        group.revision += 1
        group.save()
        event.result = result
        event.save(update_fields=["result"])
    return event


def keep_group_independent(
    *,
    actor: User,
    group: RepeatGroup | None = None,
    group_id: int | None = None,
    snapshot: dict[str, Any] | None = None,
):
    """Record an explicit no-target decision for every visible group member."""
    # ruff: ignore[import-outside-top-level]

    from weblate.trans.models.repeat import RepeatDecisionEvent, RepeatGroup

    if group is None:
        if group_id is None:
            msg = "A repeat group is required."
            raise ValueError(msg)
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.repeat import RepeatGroup

        group = RepeatGroup.objects.select_related("policy").get(pk=group_id)
    if not actor.has_perm("project.edit", group.policy.project):
        msg = "You cannot change this repeat policy."
        raise ValidationError(msg)
    with transaction.atomic():
        group = (
            RepeatGroup.objects.select_for_update()
            .select_related("policy")
            .get(pk=group.pk)
        )
        units = list(
            policy_units(group.policy)
            .filter_access(actor)
            .filter(source=group.source_forms[0])
            .select_related("translation__component", "translation__plural")
            .order_by("pk")
        )
        if snapshot is not None:
            if (
                group.revision != snapshot["group_revision"]
                or group.policy.revision != snapshot["policy_revision"]
            ):
                raise ValidationError(
                    gettext("The repeat group changed; refresh the preview.")
                )
            expected = {item["id"]: item["fingerprint"] for item in snapshot["members"]}
            current = {
                unit.pk: unit_fingerprint(unit)
                for unit in units
                if tuple(unit.get_source_plurals()) == tuple(group.source_forms)
            }
            if current != expected:
                raise ValidationError(
                    gettext("A repeat recipient changed; refresh the preview.")
                )
        event = RepeatDecisionEvent.objects.create(
            group=group,
            action=RepeatDecisionEvent.Action.INDEPENDENT,
            actor=actor,
            group_revision=group.revision,
            policy_revision=group.policy.revision,
            snapshot=snapshot or {"unit_ids": [unit.pk for unit in units]},
        )
        independent = []
        for unit in units:
            if tuple(unit.get_source_plurals()) != tuple(group.source_forms):
                continue
            create_membership(
                group=group,
                unit=unit,
                mode="independent",
                reason="keep-different",
            )
            independent.append(unit.pk)
        group.shared_target = []
        group.decision_origin = "independent"
        group.decision_author = actor
        group.decided_at = timezone.now()
        group.revision += 1
        group.save()
        event.result = {"independent": independent}
        event.save(update_fields=["result"])
    return event


def undo_event(*, token: str, actor: User):
    """Restore only recipients still unchanged since one repeat-apply event."""
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.repeat import RepeatDecisionEvent

    with transaction.atomic():
        event = (
            RepeatDecisionEvent.objects.select_for_update()
            .select_related("group__policy")
            .get(token=token, action=RepeatDecisionEvent.Action.APPLY)
        )
        if not actor.has_perm("project.edit", event.group.policy.project):
            msg = "You cannot undo this repeat decision."
            raise ValidationError(msg)
        group = event.group
        expected_revision = event.group_revision + 1
        if group.revision != expected_revision:
            msg = "A later repeat decision prevents this undo."
            raise ValidationError(msg)
        written = event.result.get("written", [])
        units = {
            unit.pk: unit
            for unit in Unit.objects.select_for_update()
            .filter(pk__in=[item["unit"] for item in written])
            .select_related("translation__component", "translation__plural")
        }
        outcome: dict[str, list[dict[str, object]]] = {"restored": [], "conflicts": []}
        translations = {}
        for item in written:
            unit = units.get(item["unit"])
            if unit is None or unit.state == STATE_APPROVED:
                outcome["conflicts"].append(
                    {"unit": item["unit"], "reason": "missing-or-approved"}
                )
                continue
            if unit.get_target_plurals() != item["new"]:
                outcome["conflicts"].append({"unit": item["unit"], "reason": "changed"})
                continue
            translations[unit.translation_id] = unit.translation
            unit.is_batch_update = True
            unit.translate(
                actor,
                item["old"],
                STATE_TRANSLATED,
                change_action=ActionEvents.REPEAT_UNDO,
                propagate=False,
                select_for_update=False,
                change_details={"repeat_event": str(event.token)},
            )
            outcome["restored"].append({"unit": unit.pk})
        for translation in translations.values():
            translation.store_update_changes()
            translation.invalidate_cache()
        undo = RepeatDecisionEvent.objects.create(
            group=group,
            action=RepeatDecisionEvent.Action.UNDO,
            actor=actor,
            group_revision=group.revision,
            policy_revision=group.policy.revision,
            snapshot={"undo_of": str(event.token)},
            result=outcome,
        )
        group.shared_target = []
        group.decision_origin = ""
        group.decision_author = None
        group.decided_at = None
        group.revision += 1
        group.save()
    return undo


def detect_policy_groups(
    policy: RepeatPolicy, *, user: User | None = None
) -> list[RepeatCandidate]:
    """Build exact repeat groups from live scope; no normalization is applied."""
    grouped: dict[tuple[tuple[str, ...], int], list[int]] = {}
    units = policy_units(policy)
    if user is not None:
        units = units.filter_access(user)
    # Only the grouping key is needed; building full Unit rows dominates the
    # queue load time on large components.
    rows = units.filter(state__gte=STATE_TRANSLATED).values_list(
        "pk", "source", "translation__plural__number"
    )
    for pk, source, plural_number in rows:
        key = (tuple(split_plural(source)), plural_number)
        grouped.setdefault(key, []).append(pk)
    return [
        RepeatCandidate(source_forms=key[0], plural_number=key[1], unit_ids=tuple(ids))
        for key, ids in grouped.items()
        if len(ids) > 1
    ]
