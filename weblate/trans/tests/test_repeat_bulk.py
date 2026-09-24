# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for durable bulk runs and their immutable per-group items."""

from __future__ import annotations

import threading
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from django.core import signing

from weblate.auth.models import Group, User, setup_project_groups
from weblate.lang.models import Language
from weblate.trans import repeat_bulk
from weblate.trans.models import (
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatGroup,
    RepeatPolicy,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_bulk import (
    CODE_ITEM_ERROR,
    CODE_NO_WRITE,
    CODE_STALE,
    CODE_UNDONE,
    REPEAT_BULK_MANIFEST_SALT,
    plan_bulk,
    process_apply_items,
    process_next_apply_item,
    process_next_undo_item,
    process_undo_items,
    resume_bulk,
    start_bulk,
    start_undo,
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
from weblate.trans.tasks import process_repeat_bulk_apply, process_repeat_bulk_undo
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.trans.util import join_plural
from weblate.utils.celery import INTERACTIVE_TASK_PRIORITY
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED


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


class RepeatBulkServiceFixtures:
    """Borrow RepeatBulkModelTest's fixture builders without copying them."""

    add_group = RepeatBulkModelTest.add_group
    make_group = RepeatBulkModelTest.make_group
    make_manager = RepeatBulkModelTest.make_manager
    make_policy = RepeatBulkModelTest.make_policy
    make_recommendation_run = RepeatBulkModelTest.make_recommendation_run
    make_result = RepeatBulkModelTest.make_result
    make_run = RepeatBulkModelTest.make_run
    group_identity = staticmethod(RepeatBulkModelTest.group_identity)

    def setUp(self) -> None:
        super().setUp()
        self.translation = self.component.translation_set.get(language_code="cs")
        self.group_units = {}
        self.recommendation_run = None
        # Every service under test gates on project.edit.
        self.make_manager()

    @staticmethod
    def write_snapshot() -> tuple[int, int, int]:
        """Count durable batch writes so rejections can prove they wrote nothing."""
        return (
            RepeatBulkRun.objects.count(),
            RepeatBulkItem.objects.count(),
            RepeatDecisionEvent.objects.count(),
        )

    def make_applied_run(self, *sources: str):
        """Create one completed apply batch with a committed event per source."""
        policy = self.make_policy()
        groups = [
            self.make_group(policy, source, ["Old one", "Old two"]) for source in sources
        ]
        results = [
            self.make_result(self.make_run(policy), group, ["New shared"]).result
            for group in groups
        ]
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk for result in results],
        )
        process_apply_items(run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        return run, groups


class RepeatBulkReviewBindingTest(RepeatBulkServiceFixtures, ViewTestCase):
    """A confirmation binds the reviewed content, not whatever is newest."""

    def test_review_binds_exact_result_despite_newer_recommendation(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A reviewed repeat", ["Old one", "Old two"])
        result_a = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        self.assertEqual([row.result_id for row in review.rows], [result_a.pk])

        # A newer recommendation for the same group must not replace the review.
        self.recommendation_run = self.make_recommendation_run(policy)
        result_b = self.make_result(self.make_run(policy), group, ["Other shared"]).result
        self.assertNotEqual(result_a.run_id, result_b.run_id)
        refreshed = plan_bulk(policy=policy, actor=self.user)
        self.assertEqual([row.result_id for row in refreshed.rows], [result_b.pk])

        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result_a.pk],
        )
        item = run.items.get()
        self.assertEqual(item.result_id, result_a.pk)
        self.assertEqual(item.decision["target"], ["New shared"])
        self.assertEqual(
            item.decision["result_fingerprint"], result_fingerprint(result_a)
        )
        self.assertNotEqual(
            item.decision["result_fingerprint"], result_fingerprint(result_b)
        )

    def test_review_collects_results_across_recommendation_runs(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "First reviewed repeat", ["Old one", "Old two"])
        second = self.make_group(
            policy, "Second reviewed repeat", ["Old one", "Old two"]
        )
        result_first = self.make_result(self.make_run(policy), first, ["New shared"]).result
        self.recommendation_run = self.make_recommendation_run(policy)
        result_second = self.make_result(
            self.make_run(policy), second, ["Other shared"]
        ).result
        self.assertNotEqual(result_first.run_id, result_second.run_id)

        review = plan_bulk(policy=policy, actor=self.user)
        self.assertEqual(
            {row.result_id for row in review.rows},
            {result_first.pk, result_second.pk},
        )
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result_first.pk, result_second.pk],
        )
        self.assertEqual(run.total, 2)
        self.assertEqual(
            {item.result_id for item in run.items.all()},
            {result_first.pk, result_second.pk},
        )

    def test_plural_target_and_exact_exclusion_survive_apply(self) -> None:
        policy = self.make_policy()
        count = self.translation.plural.number
        excluded_forms = [f"Excluded {index}" for index in range(count)]
        other_forms = [f"Other {index}" for index in range(count)]
        shared_forms = [f"Shared {index}" for index in range(count)]
        group = self.make_group(
            policy,
            "A plural repeat",
            [join_plural(excluded_forms), join_plural(other_forms)],
        )
        excluded, included = self.group_units[group.pk]
        result = self.make_result(self.make_run(policy), group, shared_forms).result

        review = plan_bulk(policy=policy, actor=self.user)
        row = review.rows[0]
        self.assertEqual(row.target, tuple(shared_forms))
        self.assertEqual(row.exclusions, (excluded.pk,))

        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        process_apply_items(run_id=run.pk)
        excluded.refresh_from_db()
        included.refresh_from_db()
        self.assertEqual(excluded.target, join_plural(excluded_forms))
        self.assertEqual(included.get_target_plurals(), shared_forms)
        item = run.items.get()
        self.assertEqual(item.status, RepeatBulkItem.Status.APPLIED)
        self.assertEqual(item.outcome["exclusions"], [excluded.pk])
        self.assertEqual(
            [entry["unit"] for entry in item.outcome["written"]], [included.pk]
        )


class RepeatBulkStaleConfirmationTest(RepeatBulkServiceFixtures, ViewTestCase):
    """One stale row rejects the whole confirmation without any durable writes."""

    def test_stale_boundaries_reject_whole_confirmation(self) -> None:
        policy = self.make_policy()

        def target_edit(_policy, group) -> None:
            self.group_units[group.pk][1].translate(
                self.user, ["Edited by hand"], STATE_TRANSLATED, propagate=False
            )

        def added_member(_policy, group) -> None:
            self.add_group(group.source_forms[0], ["A new place"], start=9000)

        def renamed_member(_policy, group) -> None:
            unit = self.group_units[group.pk][1]
            unit.context = "a renamed place"
            unit.save(update_fields=["context"])

        def deleted_member(_policy, group) -> None:
            self.group_units[group.pk][1].delete()

        def policy_resave(policy_to_save, _group) -> None:
            save_policy(
                policy=policy_to_save,
                components=[self.component],
                labels=[],
                actor=self.user,
            )

        boundaries = [
            ("target edit", target_edit, "A recommendation changed since review"),
            ("added member", added_member, "A recommendation changed since review"),
            ("renamed member", renamed_member, "A recommendation changed since review"),
            ("deleted member", deleted_member, "A recommendation changed since review"),
            ("policy re-save", policy_resave, "The repeat policy changed"),
        ]
        for label, mutate, message in boundaries:
            with self.subTest(boundary=label):
                stale = self.make_group(policy, f"Stale {label}", ["Old one", "Old two"])
                fresh = self.make_group(policy, f"Fresh {label}", ["Old one", "Old two"])
                stale_result = self.make_result(
                    self.make_run(policy), stale, ["New shared"]
                ).result
                fresh_result = self.make_result(
                    self.make_run(policy), fresh, ["New shared"]
                ).result
                review = plan_bulk(policy=policy, actor=self.user)
                reviewed_ids = {row.result_id for row in review.rows}
                self.assertIn(stale_result.pk, reviewed_ids)
                self.assertIn(fresh_result.pk, reviewed_ids)
                before = self.write_snapshot()
                mutate(policy, stale)
                with self.assertRaises(ValidationError) as cm:
                    start_bulk(
                        policy=policy,
                        actor=self.user,
                        manifest=review.manifest,
                        result_ids=[stale_result.pk, fresh_result.pk],
                    )
                self.assertIn(message, str(cm.exception))
                self.assertEqual(self.write_snapshot(), before)

    def test_permission_revocation_rejects_with_permission_denied(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A revoked grant", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        before = self.write_snapshot()

        self.user.groups.clear()
        revoked = User.objects.get(pk=self.user.pk)
        self.assertFalse(revoked.has_perm("project.edit", policy.project))
        with self.assertRaises(PermissionDenied):
            start_bulk(
                policy=policy,
                actor=revoked,
                manifest=review.manifest,
                result_ids=[result.pk],
            )
        self.assertEqual(self.write_snapshot(), before)


class RepeatBulkManifestAbuseTest(RepeatBulkServiceFixtures, ViewTestCase):
    """Tampered, foreign, expired and smuggled confirmations never write."""

    def test_manifest_and_selection_abuse_write_nothing(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A guarded repeat", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result

        other_language = (
            Language.objects.exclude(pk=policy.target_language_id)
            .exclude(pk=policy.source_language_id)
            .first()
        )
        other_policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=other_language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        other_group = self.make_group(
            other_policy, "A foreign policy repeat", ["Old one", "Old two"]
        )
        other_result = self.make_result(
            self.make_run(other_policy), other_group, ["Foreign shared"]
        ).result

        review = plan_bulk(policy=policy, actor=self.user)
        self.assertEqual([row.result_id for row in review.rows], [result.pk])
        real_payload = signing.loads(review.manifest, salt=REPEAT_BULK_MANIFEST_SALT)

        def forged(**changes) -> str:
            return signing.dumps(
                {**real_payload, **changes},
                salt=REPEAT_BULK_MANIFEST_SALT,
                compress=True,
            )

        # A real result that the reviewed manifest never listed.
        late_group = self.make_group(policy, "A late repeat", ["Old one", "Old two"])
        late_result = self.make_result(
            self.make_run(policy), late_group, ["Late shared"]
        ).result

        tampered = review.manifest[:-2] + (
            "AA" if review.manifest[-2:] != "AA" else "BB"
        )
        cases = [
            (
                "tampered bytes",
                tampered,
                [result.pk],
                self.user,
                ValidationError,
                "The review confirmation is invalid.",
            ),
            (
                "foreign actor",
                review.manifest,
                [result.pk],
                self.anotheruser,
                PermissionDenied,
                None,
            ),
            (
                "expired",
                forged(expires_at=timezone.now().timestamp() - 1),
                [result.pk],
                self.user,
                ValidationError,
                "The review confirmation expired",
            ),
            (
                "duplicate ids",
                review.manifest,
                [result.pk, result.pk],
                self.user,
                ValidationError,
                "A recommendation can only be selected once.",
            ),
            (
                "absent ids",
                review.manifest,
                [result.pk, late_result.pk],
                self.user,
                ValidationError,
                "Only reviewed recommendations can be applied.",
            ),
            (
                "cross-policy smuggle",
                forged(
                    nonce="forged-review",
                    results={
                        str(other_result.pk): {
                            "result_fingerprint": "0" * 64,
                            "context_fingerprint": "0" * 64,
                        }
                    },
                ),
                [other_result.pk],
                self.user,
                PermissionDenied,
                None,
            ),
        ]
        for label, manifest, selected, actor, error, message in cases:
            with self.subTest(abuse=label):
                before = self.write_snapshot()
                with self.assertRaises(error) as cm:
                    start_bulk(
                        policy=policy,
                        actor=actor,
                        manifest=manifest,
                        result_ids=selected,
                    )
                if message is not None:
                    self.assertIn(message, str(cm.exception))
                self.assertEqual(self.write_snapshot(), before)


class RepeatBulkCrashBoundaryTest(RepeatBulkServiceFixtures, ViewTestCase):
    """Failpoints inside the service prove commit atomicity and recovery."""

    def test_apply_crash_before_commit_rolls_back_writes_and_item(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A crashing repeat", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        item = run.items.get()
        units = self.group_units[group.pk]
        old_targets = [unit.target for unit in units]
        real_apply = repeat_bulk.apply_preview

        def crash_after_item_writes(**kwargs):
            real_apply(**kwargs)
            raise RuntimeError("failpoint after item writes, before commit")

        with patch(
            "weblate.trans.repeat_bulk.apply_preview",
            side_effect=crash_after_item_writes,
        ):
            process_apply_items(run_id=run.pk)

        run.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.FAILED)
        self.assertEqual(run.failure_code, CODE_ITEM_ERROR)
        self.assertEqual(item.status, RepeatBulkItem.Status.PENDING)
        for unit in units:
            unit.refresh_from_db()
        self.assertEqual([unit.target for unit in units], old_targets)
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)
        # The failure is visible and explicitly retryable.
        resume_bulk(run=run, actor=self.user)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.QUEUED)

    def test_apply_crash_after_commit_resumes_next_item_once(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "First committed", ["Old one", "Old two"])
        second = self.make_group(policy, "Second committed", ["Old one", "Old two"])
        first_result = self.make_result(self.make_run(policy), first, ["New shared"]).result
        second_result = self.make_result(
            self.make_run(policy), second, ["Other shared"]
        ).result
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[first_result.pk, second_result.pk],
        )
        first_item = run.items.get(ordinal=1)
        second_item = run.items.get(ordinal=2)

        # Exactly one item commits, then the worker dies.
        self.assertTrue(process_next_apply_item(run.pk))
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.APPLY
            ).count(),
            1,
        )
        first_item.refresh_from_db()
        self.assertEqual(first_item.status, RepeatBulkItem.Status.APPLIED)
        committed_event_id = first_item.decision_event_id

        # Redelivery resumes at the next item without duplicating the first.
        process_apply_items(run_id=run.pk)
        first_item.refresh_from_db()
        second_item.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(first_item.decision_event_id, committed_event_id)
        self.assertEqual(second_item.status, RepeatBulkItem.Status.APPLIED)
        self.assertNotEqual(second_item.decision_event_id, committed_event_id)
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.APPLY
            ).count(),
            2,
        )
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)

    def test_undo_crash_before_commit_rolls_back_restores(self) -> None:
        run, groups = self.make_applied_run("An undo crash")
        group = groups[0]
        unit = self.group_units[group.pk][1]
        undo_run = start_undo(run=run, actor=self.user)
        undo_item = undo_run.items.get()
        real_undo = repeat_bulk.undo_event

        def crash_after_restores(**kwargs):
            real_undo(**kwargs)
            raise RuntimeError("failpoint after restores, before commit")

        with patch(
            "weblate.trans.repeat_bulk.undo_event", side_effect=crash_after_restores
        ):
            process_undo_items(run_id=undo_run.pk)

        undo_run.refresh_from_db()
        undo_item.refresh_from_db()
        self.assertEqual(undo_run.status, RepeatBulkRun.Status.FAILED)
        self.assertEqual(undo_run.failure_code, CODE_ITEM_ERROR)
        self.assertEqual(undo_item.status, RepeatBulkItem.Status.PENDING)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "New shared")
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.UNDO
            ).count(),
            0,
        )

    def test_undo_crash_after_commit_resumes_next_item_once(self) -> None:
        run, _groups = self.make_applied_run("An undo first", "An undo second")
        undo_run = start_undo(run=run, actor=self.user)
        first_item = undo_run.items.get(ordinal=1)
        second_item = undo_run.items.get(ordinal=2)

        self.assertTrue(process_next_undo_item(undo_run.pk))
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.UNDO
            ).count(),
            1,
        )
        first_item.refresh_from_db()
        self.assertEqual(first_item.status, RepeatBulkItem.Status.UNDONE)
        committed_event_id = first_item.decision_event_id

        process_undo_items(run_id=undo_run.pk)
        first_item.refresh_from_db()
        second_item.refresh_from_db()
        undo_run.refresh_from_db()
        self.assertEqual(first_item.decision_event_id, committed_event_id)
        self.assertEqual(second_item.status, RepeatBulkItem.Status.UNDONE)
        self.assertNotEqual(second_item.decision_event_id, committed_event_id)
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.UNDO
            ).count(),
            2,
        )
        self.assertEqual(undo_run.status, RepeatBulkRun.Status.COMPLETED)


class RepeatBulkResumabilityTest(RepeatBulkServiceFixtures, ViewTestCase):
    """RUNNING is resumable; failures stay visible and never lose progress."""

    def test_running_run_with_pending_items_continues(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "A running first", ["Old one", "Old two"])
        second = self.make_group(policy, "A running second", ["Old one", "Old two"])
        results = [
            self.make_result(self.make_run(policy), group, ["New shared"]).result
            for group in (first, second)
        ]
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk for result in results],
        )

        self.assertTrue(process_next_apply_item(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.RUNNING)
        self.assertEqual(
            run.items.filter(status=RepeatBulkItem.Status.PENDING).count(), 1
        )

        process_apply_items(run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        self.assertEqual((run.total, run.done, run.written), (2, 2, 2))
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.APPLY
            ).count(),
            2,
        )

    def test_resume_processes_only_pending_items_after_failure(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "A first pending", ["Old one", "Old two"])
        middle = self.make_group(policy, "A stale middle", ["Old one", "Old two"])
        last = self.make_group(policy, "A crashing last", ["Old one", "Old two"])
        results = [
            self.make_result(self.make_run(policy), group, ["New shared"]).result
            for group in (first, middle, last)
        ]
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk for result in results],
        )
        self.group_units[middle.pk][1].translate(
            self.user, ["Edited by hand"], STATE_TRANSLATED, propagate=False
        )
        real_apply = repeat_bulk.apply_preview
        calls = 0

        def flaky(**kwargs):
            nonlocal calls
            calls += 1
            # The stale middle item never reaches the write boundary, so the
            # second call is the last item's turn and dies there.
            if calls == 2:
                raise RuntimeError("failpoint on the last item")
            return real_apply(**kwargs)

        with patch("weblate.trans.repeat_bulk.apply_preview", side_effect=flaky):
            process_apply_items(run_id=run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.FAILED)
        self.assertEqual(run.failure_code, CODE_ITEM_ERROR)
        first_item = run.items.get(ordinal=1)
        middle_item = run.items.get(ordinal=2)
        last_item = run.items.get(ordinal=3)
        self.assertEqual(first_item.status, RepeatBulkItem.Status.APPLIED)
        self.assertEqual(middle_item.status, RepeatBulkItem.Status.SKIPPED)
        self.assertEqual(middle_item.failure_code, CODE_STALE)
        self.assertEqual(last_item.status, RepeatBulkItem.Status.PENDING)
        middle_snapshot = (
            middle_item.status,
            middle_item.failure_code,
            dict(middle_item.outcome),
            middle_item.updated_at,
        )

        resume_bulk(run=run, actor=self.user)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.QUEUED)

        with patch("weblate.trans.repeat_bulk.apply_preview", wraps=real_apply) as wrapped:
            process_apply_items(run_id=run.pk)

        self.assertEqual(wrapped.call_count, 1)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        middle_item.refresh_from_db()
        self.assertEqual(
            (
                middle_item.status,
                middle_item.failure_code,
                dict(middle_item.outcome),
                middle_item.updated_at,
            ),
            middle_snapshot,
        )
        last_item.refresh_from_db()
        self.assertEqual(last_item.status, RepeatBulkItem.Status.APPLIED)
        self.assertEqual((run.total, run.done, run.failed), (3, 3, 0))
        self.assertEqual(run.written, 2)

    def test_broker_failure_leaves_recoverable_run_republished_by_resume(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A broker outage", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)

        with (
            self.assertLogs("weblate.trans.repeat_bulk", level="ERROR"),
            patch.object(
                process_repeat_bulk_apply,
                "apply_async",
                side_effect=RuntimeError("broker down"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            run = start_bulk(
                policy=policy,
                actor=self.user,
                manifest=review.manifest,
                result_ids=[result.pk],
            )

        before = self.write_snapshot()
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.QUEUED)
        self.assertEqual(run.items.get().status, RepeatBulkItem.Status.PENDING)

        with (
            patch.object(process_repeat_bulk_apply, "apply_async") as published,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resume_bulk(run=run, actor=self.user)

        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.QUEUED)
        published.assert_called_once_with(
            args=[run.pk], priority=INTERACTIVE_TASK_PRIORITY
        )
        # Resume re-publishes the same durable batch; it creates nothing new.
        self.assertEqual(self.write_snapshot(), before)
        self.assertEqual(
            RepeatBulkRun.objects.filter(review_nonce=review.nonce).count(), 1
        )
        self.assertEqual(run.items.count(), 1)


class RepeatBulkUndoSemanticsTest(RepeatBulkServiceFixtures, ViewTestCase):
    """Undo restores what it can and keeps every conflict visible and linked."""

    def test_undo_restores_eligible_and_reports_recipient_conflicts(self) -> None:
        policy = self.make_policy()
        group = self.make_group(
            policy,
            "A conflict-aware repeat",
            ["Old one", "Old two", "Old three", "Old four"],
        )
        excluded, edited, approved, untouched = self.group_units[group.pk]
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        process_apply_items(run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.written, 3)
        excluded.refresh_from_db()
        self.assertEqual(excluded.target, "Old one")

        edited.translate(
            self.user, ["Edited since"], STATE_TRANSLATED, propagate=False
        )
        approved.translate(self.user, ["New shared"], STATE_APPROVED, propagate=False)

        undo_run = start_undo(run=run, actor=self.user)
        process_undo_items(run_id=undo_run.pk)

        undo_item = undo_run.items.get()
        undo_item.refresh_from_db()
        self.assertEqual(undo_item.status, RepeatBulkItem.Status.UNDONE)
        event = undo_item.decision_event
        self.assertEqual(
            event.result,
            {
                "restored": [{"unit": untouched.pk}],
                "conflicts": [
                    {"unit": edited.pk, "reason": "changed"},
                    {"unit": approved.pk, "reason": "missing-or-approved"},
                ],
            },
        )
        self.assertEqual(undo_item.outcome, {**event.result, "code": CODE_UNDONE})
        undo_run.refresh_from_db()
        self.assertEqual((undo_run.restored, undo_run.conflict, undo_run.written), (1, 2, 0))
        untouched.refresh_from_db()
        self.assertEqual(untouched.target, "Old four")
        edited.refresh_from_db()
        self.assertEqual(edited.target, "Edited since")

    def test_partially_completed_batch_can_be_undone_but_not_resumed(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "A completed item", ["Old one", "Old two"])
        second = self.make_group(policy, "A skipped item", ["Old one", "Old two"])
        first_result = self.make_result(self.make_run(policy), first, ["New shared"]).result
        second_result = self.make_result(
            self.make_run(policy), second, ["Other shared"]
        ).result
        review = plan_bulk(policy=policy, actor=self.user)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[first_result.pk, second_result.pk],
        )
        self.group_units[second.pk][1].translate(
            self.user, ["Edited by hand"], STATE_TRANSLATED, propagate=False
        )
        process_apply_items(run_id=run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        applied_item = run.items.get(group=first)
        skipped_item = run.items.get(group=second)
        self.assertEqual(skipped_item.status, RepeatBulkItem.Status.SKIPPED)
        self.assertEqual(skipped_item.failure_code, CODE_STALE)
        self.assertEqual((run.total, run.done, run.written, run.failed), (2, 2, 1, 0))

        undo_run = start_undo(run=run, actor=self.user)
        self.assertEqual(undo_run.total, 1)
        process_undo_items(run_id=undo_run.pk)
        undo_item = undo_run.items.get()
        undo_item.refresh_from_db()
        self.assertEqual(undo_item.status, RepeatBulkItem.Status.UNDONE)
        self.assertEqual(undo_item.apply_event_id, applied_item.decision_event_id)
        restored = self.group_units[first.pk][1]
        restored.refresh_from_db()
        self.assertEqual(restored.target, "Old two")

        with self.assertRaises(ValidationError) as cm:
            resume_bulk(run=run, actor=self.user)
        self.assertIn("An undone batch cannot be resumed", str(cm.exception))


class RepeatBulkProtectedPlacesTest(RepeatBulkServiceFixtures, ViewTestCase):
    """Protected places stay unchanged and no-ops stay honest."""

    def test_approved_recipient_is_blocked_and_stays_unchanged(self) -> None:
        policy = self.make_policy()
        group = self.make_group(
            policy,
            "An approved recipient",
            ["Old one", "Old two", "Old three"],
        )
        excluded, written, approved = self.group_units[group.pk]
        approved.translate(
            self.user, ["Old three"], STATE_APPROVED, propagate=False
        )
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result

        review = plan_bulk(policy=policy, actor=self.user)
        row = review.rows[0]
        self.assertEqual(row.writable, 1)
        self.assertEqual(row.blocked, {"approved": 1})
        self.assertEqual(row.already_matching, 0)
        members = {member["unit_id"]: member for member in row.members}
        self.assertTrue(members[excluded.pk]["excluded"])
        self.assertFalse(members[approved.pk]["eligible"])
        self.assertEqual(members[approved.pk]["reason"], "approved")

        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        process_apply_items(run_id=run.pk)
        item = run.items.get()
        self.assertEqual(item.status, RepeatBulkItem.Status.APPLIED)
        # apply_preview marks every place outside the selected set as
        # not-selected; the review row above is where the "approved" reason
        # shows. Either way the place is skipped, never counted writable.
        self.assertEqual(
            item.outcome,
            {
                "written": [
                    {"unit": written.pk, "old": ["Old two"], "new": ["New shared"]}
                ],
                "skipped": [
                    {"unit": excluded.pk, "reason": "not-selected"},
                    {"unit": approved.pk, "reason": "not-selected"},
                ],
                "exclusions": [excluded.pk],
            },
        )
        approved.refresh_from_db()
        self.assertEqual(approved.target, "Old three")

    def test_locked_component_recipients_are_all_blocked_no_write(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A locked repeat", ["Old one", "Old two"])
        units = self.group_units[group.pk]
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        self.component.locked = True
        self.component.save(update_fields=["locked"])

        review = plan_bulk(policy=policy, actor=self.user)
        row = review.rows[0]
        self.assertEqual(row.writable, 0)
        self.assertEqual(row.blocked, {"locked": 2})
        self.assertEqual(row.already_matching, 0)
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        process_apply_items(run_id=run.pk)

        item = run.items.get()
        self.assertEqual(item.status, RepeatBulkItem.Status.SKIPPED)
        self.assertEqual(item.failure_code, CODE_NO_WRITE)
        self.assertEqual(item.outcome, {"exclusions": [units[0].pk]})
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)
        group.refresh_from_db()
        self.assertEqual(group.shared_target, [])
        run.refresh_from_db()
        self.assertEqual((run.done, run.written, run.failed), (1, 0, 0))
        for unit in units:
            unit.refresh_from_db()
        self.assertEqual([unit.target for unit in units], ["Old one", "Old two"])

    def test_all_matching_group_is_honest_no_write_without_event(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "An already matching repeat", ["Same", "Same"])
        units = self.group_units[group.pk]
        result = self.make_result(self.make_run(policy), group, ["Same"]).result

        review = plan_bulk(policy=policy, actor=self.user)
        row = review.rows[0]
        self.assertEqual(row.writable, 0)
        self.assertEqual(row.already_matching, 2)
        self.assertEqual(row.blocked, {})
        run = start_bulk(
            policy=policy,
            actor=self.user,
            manifest=review.manifest,
            result_ids=[result.pk],
        )
        process_apply_items(run_id=run.pk)

        item = run.items.get()
        self.assertEqual(item.status, RepeatBulkItem.Status.SKIPPED)
        self.assertEqual(item.failure_code, CODE_NO_WRITE)
        self.assertEqual(item.outcome, {"exclusions": [units[0].pk]})
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)
        group.refresh_from_db()
        self.assertEqual(group.shared_target, [])
        run.refresh_from_db()
        self.assertEqual((run.done, run.written, run.failed), (1, 0, 0))


class RepeatBulkConcurrencyTest(RepeatBulkServiceFixtures, RepoTestMixin, TransactionTestCase):
    """
    Two independent DB connections racing the bulk services.

    A plain TestCase runs inside one outer transaction, so a second thread's
    connection could never see the fixture at all. This needs real commits,
    hence TransactionTestCase.
    """

    def setUp(self) -> None:
        self.clone_test_repos()
        self.component = self.create_component()
        self.component.create_path()
        self.project = self.component.project
        setup_project_groups(self, self.project)
        self.user = create_test_user()
        self.user.groups.add(Group.objects.get(name="Users"))
        super().setUp()
        connection.close()  # this connection must not straddle the threads

    def test_two_connections_process_one_run_with_one_event_per_item(self) -> None:
        policy = self.make_policy()
        first = self.make_group(policy, "Concurrent first", ["Old one", "Old two"])
        second = self.make_group(policy, "Concurrent second", ["Old one", "Old two"])
        results = [
            self.make_result(self.make_run(policy), group, ["New shared"]).result
            for group in (first, second)
        ]
        review = plan_bulk(policy=policy, actor=self.user)
        with patch.object(process_repeat_bulk_apply, "apply_async"):
            run = start_bulk(
                policy=policy,
                actor=self.user,
                manifest=review.manifest,
                result_ids=[result.pk for result in results],
            )
        connection.close()

        barrier = threading.Barrier(2)
        errors: list = []
        lock = threading.Lock()

        def process() -> None:
            try:
                barrier.wait(timeout=10)
                process_apply_items(run_id=run.pk)
            except Exception as error:
                with lock:
                    errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=process) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [], f"both workers must finish: {errors}")
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        self.assertEqual((run.total, run.done, run.written, run.failed), (2, 2, 2, 0))
        items = list(run.items.order_by("ordinal"))
        event_ids = {item.decision_event_id for item in items}
        self.assertEqual(len(event_ids), 2)
        self.assertEqual(
            event_ids,
            {
                event.pk
                for event in RepeatDecisionEvent.objects.filter(
                    action=RepeatDecisionEvent.Action.APPLY
                )
            },
        )
        for item in items:
            self.assertEqual(item.status, RepeatBulkItem.Status.APPLIED)

    def test_racing_threads_apply_a_single_item_once(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A single racy item", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        with patch.object(process_repeat_bulk_apply, "apply_async"):
            run = start_bulk(
                policy=policy,
                actor=self.user,
                manifest=review.manifest,
                result_ids=[result.pk],
            )
        connection.close()

        barrier = threading.Barrier(2)
        outcomes: list = []
        errors: list = []
        lock = threading.Lock()

        def race() -> None:
            try:
                barrier.wait(timeout=10)
                processed_one = process_next_apply_item(run.pk)
                with lock:
                    outcomes.append(processed_one)
            except Exception as error:
                with lock:
                    errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=race) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [], f"both racers must finish: {errors}")
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(
            RepeatDecisionEvent.objects.filter(
                action=RepeatDecisionEvent.Action.APPLY
            ).count(),
            1,
        )
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        self.assertEqual((run.total, run.done, run.written), (1, 1, 1))

    def test_concurrent_double_submission_creates_one_apply_run(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A double submit", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        connection.close()

        barrier = threading.Barrier(2)
        runs: list = []
        errors: list = []
        lock = threading.Lock()

        def submit() -> None:
            try:
                barrier.wait(timeout=10)
                created = start_bulk(
                    policy=policy,
                    actor=self.user,
                    manifest=review.manifest,
                    result_ids=[result.pk],
                )
                with lock:
                    runs.append(created)
            except Exception as error:
                with lock:
                    errors.append(error)
            finally:
                connection.close()

        with patch.object(process_repeat_bulk_apply, "apply_async"):
            threads = [threading.Thread(target=submit) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(errors, [], f"both submissions must resolve: {errors}")
        self.assertEqual(len(runs), 2)
        self.assertEqual(len({run.pk for run in runs}), 1)
        self.assertEqual(
            RepeatBulkRun.objects.filter(review_nonce=review.nonce).count(), 1
        )
        run = runs[0]
        self.assertEqual(run.items.count(), 1)
        self.assertEqual(run.total, 1)

    def test_concurrent_double_undo_creates_one_undo_run(self) -> None:
        policy = self.make_policy()
        group = self.make_group(policy, "A double undo", ["Old one", "Old two"])
        result = self.make_result(self.make_run(policy), group, ["New shared"]).result
        review = plan_bulk(policy=policy, actor=self.user)
        with patch.object(process_repeat_bulk_apply, "apply_async"):
            run = start_bulk(
                policy=policy,
                actor=self.user,
                manifest=review.manifest,
                result_ids=[result.pk],
            )
        process_apply_items(run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        connection.close()

        barrier = threading.Barrier(2)
        undos: list = []
        errors: list = []
        lock = threading.Lock()

        def undo() -> None:
            try:
                barrier.wait(timeout=10)
                created = start_undo(run=run, actor=self.user)
                with lock:
                    undos.append(created)
            except Exception as error:
                with lock:
                    errors.append(error)
            finally:
                connection.close()

        with patch.object(process_repeat_bulk_undo, "apply_async"):
            threads = [threading.Thread(target=undo) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(errors, [], f"both undo calls must resolve: {errors}")
        self.assertEqual(len({undo_run.pk for undo_run in undos}), 1)
        self.assertEqual(
            RepeatBulkRun.objects.filter(action=RepeatBulkRun.Action.UNDO).count(), 1
        )
        undo_run = undos[0]
        self.assertEqual(undo_run.apply_run_id, run.pk)
        self.assertEqual(undo_run.total, 1)
