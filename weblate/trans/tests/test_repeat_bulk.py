# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for durable bulk runs and their immutable per-group items."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from weblate.trans.models import (
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_recommendations import (
    build_group_context,
    context_fingerprint,
    result_fingerprint,
)
from weblate.trans.repeats import (
    fingerprint,
    get_or_create_group,
    save_policy,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_TRANSLATED


class RepeatBulkModelTest(ViewTestCase):
    """Persist bulk runs and their copied per-group decisions."""

    def setUp(self) -> None:
        super().setUp()
        self.translation = self.component.translation_set.get(language_code="cs")
        self.group_units: dict[int, list] = {}
        self.recommendation_run: RepeatRecommendationRun | None = None

    def add_group(self, source: str, targets: list[str], start: int = 1000):
        """Create one diverging exact-repeat group and return its units."""
        units = []
        for position, target in enumerate(targets, start=start):
            context = f"{source}-{position}"
            source_unit = self.component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            units.append(
                self.translation.unit_set.create(
                    id_hash=calculate_hash(source, context),
                    position=position,
                    source_unit=source_unit,
                    context=context,
                    source=source,
                    target=target,
                    state=STATE_TRANSLATED,
                )
            )
        return units

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

    def make_group(
        self, policy: RepeatPolicy, source: str, targets: list[str]
    ) -> RepeatGroup:
        """Durable group identity for real diverging repeat units."""
        units = self.add_group(source, targets)
        group = get_or_create_group(policy, units[0])
        self.group_units[group.pk] = units
        return group

    @staticmethod
    def group_identity(group: RepeatGroup) -> dict:
        return {
            "source_forms": list(group.source_forms),
            "plural_number": group.plural_number,
        }

    def make_run(self, policy: RepeatPolicy, **fields) -> RepeatBulkRun:
        fields.setdefault("actor", self.user)
        fields.setdefault("action", RepeatBulkRun.Action.APPLY)
        return RepeatBulkRun.objects.create(policy=policy, **fields)

    def make_recommendation_run(self, policy: RepeatPolicy) -> RepeatRecommendationRun:
        return RepeatRecommendationRun.objects.create(
            policy=policy,
            actor=self.user,
            snapshot={"policy_revision": policy.revision},
            snapshot_fingerprint=fingerprint({"policy_revision": policy.revision}),
            profile_fingerprint=fingerprint(
                {"model": "fixture-model", "temperature": 0}
            ),
            prompt_fingerprint=fingerprint("repeat-recommendation-prompt"),
            request_cap=1,
        )

    def make_result(
        self,
        run: RepeatBulkRun,
        group: RepeatGroup,
        target: list[str],
        action: str = "propose_new",
    ) -> RepeatBulkItem:
        """Copy one confirmed recommendation into an immutable bulk item."""
        units = self.group_units[group.pk]
        context = build_group_context(policy=run.policy, group=group, units=units)
        if self.recommendation_run is None:
            self.recommendation_run = self.make_recommendation_run(run.policy)
        source = RepeatRecommendationResult.objects.create(
            run=self.recommendation_run,
            group=group,
            group_revision=group.revision,
            snapshot_fingerprint=context_fingerprint(context),
            context_fingerprint=context_fingerprint(context),
            action=action,
            target=list(target),
            exclusions=[units[0].pk],
            rationale="The key names this exact meaning.",
        )
        return RepeatBulkItem.objects.create(
            run=run,
            ordinal=run.items.count(),
            group=group,
            group_identity=self.group_identity(group),
            result=source,
            decision={
                "action": source.action,
                "target": list(source.target),
                "exclusions": list(source.exclusions),
                "rationale": source.rationale,
                "result_fingerprint": result_fingerprint(source),
            },
            context_fingerprint=context_fingerprint(context),
        )

    def test_apply_run_review_nonce_is_unique(self) -> None:
        policy = self.make_policy()
        self.make_run(policy, review_nonce="signed-review")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_run(policy, review_nonce="signed-review")

    def test_undo_run_is_unique_per_apply_run(self) -> None:
        policy = self.make_policy()
        apply_run = self.make_run(policy, review_nonce="signed-review")
        self.make_run(
            policy,
            action=RepeatBulkRun.Action.UNDO,
            apply_run=apply_run,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_run(
                policy,
                action=RepeatBulkRun.Action.UNDO,
                apply_run=apply_run,
            )
        # A second apply run is fine with a fresh or empty nonce.
        self.make_run(policy, review_nonce="signed-review-again")
        self.make_run(policy)
        self.make_run(policy)

    def test_item_ordinal_is_unique_per_run(self) -> None:
        policy = self.make_policy()
        first_group = self.make_group(policy, "An exact repeat", ["Old", "Older"])
        second_group = self.make_group(policy, "Another exact repeat", ["Stale", "Staler"])
        run = self.make_run(policy, review_nonce="signed-review")
        self.make_result(run, first_group, ["Shared meaning"], action="propose_new")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RepeatBulkItem.objects.create(
                run=run,
                ordinal=0,
                group=second_group,
                group_identity=self.group_identity(second_group),
            )

    def test_item_group_identity_is_unique_per_run(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "An exact repeat", ["Old", "Older"])
        run = self.make_run(policy, review_nonce="signed-review")
        self.make_result(run, group, ["Shared meaning"], action="propose_new")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RepeatBulkItem.objects.create(
                run=run,
                ordinal=1,
                group=group,
                group_identity=self.group_identity(group),
            )

    def test_deleting_result_keeps_item_and_copied_decision(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "An exact repeat", ["Old", "Older"])
        run = self.make_run(policy, review_nonce="signed-review")
        item = self.make_result(run, group, ["Shared meaning"], action="propose_new")
        source = item.result
        decision = dict(item.decision)
        expected_fingerprint = result_fingerprint(source)
        source.delete()
        item.refresh_from_db()
        self.assertIsNone(item.result_id)
        self.assertEqual(item.decision, decision)
        self.assertEqual(item.decision["result_fingerprint"], expected_fingerprint)

    def test_several_results_in_one_run_reuse_run_object(self) -> None:
        policy = self.make_policy()
        first_group = self.make_group(policy, "An exact repeat", ["Old", "Older"])
        second_group = self.make_group(policy, "Another exact repeat", ["Stale", "Staler"])
        run = self.make_run(policy, review_nonce="signed-review")
        items = [
            self.make_result(run, first_group, ["Shared meaning"], action="propose_new"),
            self.make_result(run, second_group, ["Kept wording"], action="use_existing"),
        ]
        for item in items:
            self.assertIs(item.run, run)
            self.assertIs(item.result.run, self.recommendation_run)
        self.assertEqual(run.items.count(), 2)
        self.assertEqual(len({item.result_id for item in items}), 2)

    def test_separate_runs_keep_counter_defaults(self) -> None:
        policy = self.make_policy()
        first = self.make_run(policy, review_nonce="signed-review")
        second = self.make_run(policy, review_nonce="signed-review-again")
        third = self.make_run(policy)
        self.assertEqual(len({first.pk, second.pk, third.pk}), 3)
        for run in (first, second, third):
            for field in ("total", "done", "written", "restored", "conflict", "failed"):
                self.assertEqual(getattr(run, field), 0)
        first.done = 1
        first.save(update_fields=["done"])
        second.refresh_from_db()
        self.assertEqual(second.done, 0)
