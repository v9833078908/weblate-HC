# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable scopes and freshness checks for explicitly managed exact repeats."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from weblate.utils.state import STATE_READONLY, STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.auth.models import User
    from weblate.trans.models import Component, Label, Unit
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
        translation__language=policy.target_language,
        translation__component__allow_translation_propagation=True,
        translation__component__is_glossary=False,
        state__lt=STATE_READONLY,
    ).exclude(translation__language=policy.source_language)
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


@dataclass(frozen=True)
class RepeatCandidate:
    """A live detection group, with durable state looked up separately."""

    source_forms: tuple[str, ...]
    plural_number: int
    unit_ids: tuple[int, ...]


def detect_policy_groups(policy: RepeatPolicy) -> list[RepeatCandidate]:
    """Build exact repeat groups from live scope; no normalization is applied."""
    grouped: dict[tuple[tuple[str, ...], int], list[int]] = {}
    for unit in policy_units(policy).filter(state__gte=STATE_TRANSLATED):
        key = (tuple(unit.get_source_plurals()), unit.translation.plural.number)
        grouped.setdefault(key, []).append(unit.pk)
    return [
        RepeatCandidate(source_forms=key[0], plural_number=key[1], unit_ids=tuple(ids))
        for key, ids in grouped.items()
        if len(ids) > 1
    ]
