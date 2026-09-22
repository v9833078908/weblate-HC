# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for durable, explicitly managed exact-repeat membership."""

from __future__ import annotations

from weblate.trans.models import RepeatMembership, RepeatPolicy
from weblate.trans.repeats import (
    apply_preview,
    create_membership,
    detect_policy_groups,
    get_or_create_group,
    reconcile_unit,
    save_policy,
    preview_group,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_TRANSLATED


class RepeatModelTest(ViewTestCase):
    """Exercise live scope detection and stale identity protection."""

    def setUp(self) -> None:
        super().setUp()
        self.translation = self.component.translation_set.get(language_code="cs")

    def add_repeat(self, context: str, target: str):
        source = "An exact repeat"
        source_unit = self.component.source_translation.unit_set.create(
            id_hash=calculate_hash(source, context),
            position=1000,
            context=context,
            source=source,
            target=source,
            state=STATE_TRANSLATED,
        )
        return self.translation.unit_set.create(
            id_hash=calculate_hash(source, context),
            position=1000,
            source_unit=source_unit,
            context=context,
            source=source,
            target=target,
            state=STATE_TRANSLATED,
        )

    def make_policy(self) -> RepeatPolicy:
        policy = RepeatPolicy(
            project=self.project,
            source_language=self.component.source_language,
            target_language=self.translation.language,
        )
        return save_policy(
            policy=policy,
            components=[self.component],
            labels=[],
            actor=self.user,
        )

    def test_detects_only_exact_live_repeats_and_stales_changed_identity(self) -> None:
        first = self.add_repeat("first", "Prvni")
        second = self.add_repeat("second", "Druhy")
        policy = self.make_policy()

        candidates = detect_policy_groups(policy)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(set(candidates[0].unit_ids), {first.pk, second.pk})

        group = get_or_create_group(policy, first)
        membership = create_membership(
            group=group,
            unit=first,
            mode=RepeatMembership.Mode.INDEPENDENT,
            reason="test",
        )
        first.context = "renamed"
        with self.captureOnCommitCallbacks(execute=True):
            first.save(update_fields=["context"])
        reconcile_unit(first.pk)
        membership.refresh_from_db()
        self.assertTrue(membership.is_stale)

    def test_preview_applies_only_nonapproved_recipients(self) -> None:
        first = self.add_repeat("first", "Old")
        second = self.add_repeat("second", "Approved")
        second.state = 30
        second.save(update_fields=["state"])
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        preview = preview_group(group=group, target=["Shared"], actor=self.user)
        event = apply_preview(token=preview.token, actor=self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, "Shared")
        self.assertEqual(second.target, "Approved")
        self.assertEqual(len(event.result["written"]), 1)
