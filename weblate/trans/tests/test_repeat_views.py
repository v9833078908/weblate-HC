# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Views for the managed repeat queue."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from weblate.auth.models import Group
from weblate.checks.models import CHECKS
from weblate.trans.models import (
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_bulk import (
    process_apply_items,
    process_next_apply_item,
    process_undo_items,
)
from weblate.trans.repeat_recommendations import (
    build_group_context,
    context_fingerprint,
    current_recommendations,
    result_fingerprint,
)
from weblate.trans.repeats import fingerprint, get_or_create_group, save_policy
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED


class RepeatQueueViewTest(ViewTestCase):
    """The queue remains project/language scoped even without a policy."""

    def test_queue_requires_project_access_and_renders_empty_scope(self) -> None:
        response = self.client.get(
            reverse(
                "repeat-queue",
                kwargs={"project": self.project.slug, "language": "cs"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repeat queue")
        self.assertContains(response, "No active repeat rule exists")

    def add_repeat(self, source: str, targets: list[str], start: int = 1000):
        translation = self.component.translation_set.get(language_code="cs")
        for position, target in enumerate(targets, start=start):
            context = f"key{position}"
            source_unit = self.component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                source_unit=source_unit,
                context=context,
                source=source,
                target=target,
                state=STATE_TRANSLATED,
            )
        return translation

    def test_queue_shows_short_strings_before_long_sentences(self) -> None:
        translation = self.add_repeat(
            "A long sentence that players rarely see twice", ["One", "Two", "Three"]
        )
        self.add_repeat("Sword", ["Epee", "Glaive"], start=2000)
        save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )

        response = self.client.get(
            reverse(
                "repeat-queue", kwargs={"project": self.project.slug, "language": "cs"}
            )
        )

        content = response.content.decode()
        self.assertLess(content.index("Sword"), content.index("A long sentence"))

    def test_queue_separates_diverging_and_consistent_groups(self) -> None:
        translation = self.add_repeat("Drifting text", ["One", "Two"])
        self.add_repeat("Consistent text", ["Same", "Same"], start=2000)
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        url = reverse(
            "repeat-queue", kwargs={"project": self.project.slug, "language": "cs"}
        )

        default = self.client.get(url)
        consistent = self.client.get(url, {"status": "consistent"})

        self.assertContains(default, "Drifting text")
        self.assertNotContains(default, "Consistent text")
        self.assertContains(consistent, "Consistent text")
        self.assertContains(consistent, "Same translation")
        self.assertNotContains(consistent, "Drifting text")

        group = next(
            group
            for group in policy.groups.all()
            if group.source_forms[0] == "Consistent text"
        )
        preview = self.client.post(
            reverse("repeat-preview", kwargs={"group_id": group.pk}),
            {"choice": "variant", "target": "Same"},
        )
        self.assertContains(preview, "No translation changes.")
        self.assertContains(preview, "Pin as the shared translation")

    def test_manager_first_visit_creates_default_rule(self) -> None:
        self.make_manager()
        self.add_repeat("Drifting text", ["One", "Two"])
        url = reverse(
            "repeat-queue", kwargs={"project": self.project.slug, "language": "cs"}
        )

        response = self.client.get(url)
        self.client.get(url)

        policy = RepeatPolicy.objects.get(project=self.project)
        self.assertEqual(list(policy.components.all()), [self.component])
        self.assertEqual(policy.source_language, self.component.source_language)
        self.assertEqual(policy.author, self.user)
        self.assertContains(response, "Drifting text")

    def test_default_rule_respects_disabled_rule(self) -> None:
        self.make_manager()
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=self.add_repeat(
                    "Drifting text", ["One", "Two"]
                ).language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        RepeatPolicy.objects.filter(pk=policy.pk).update(enabled=False)

        response = self.client.get(
            reverse(
                "repeat-queue",
                kwargs={"project": self.project.slug, "language": "cs"},
            )
        )

        self.assertEqual(RepeatPolicy.objects.count(), 1)
        self.assertContains(response, "No active repeat rule exists")

    def test_project_language_page_links_repeat_drift_to_queue(self) -> None:
        self.project.check_flags = "repeat-drift"
        self.project.save()
        translation = self.add_repeat("A drifting repeat", ["One", "Two"])
        CHECKS["repeat-drift"].perform_batch(self.component)
        translation.invalidate_cache()

        response = self.client.get(
            reverse(
                "show",
                kwargs={"path": [self.project.slug, "-", "cs"]},
            )
        )

        self.assertContains(
            response,
            reverse(
                "repeat-queue",
                kwargs={"project": self.project.slug, "language": "cs"},
            ),
        )

    def test_project_manager_can_open_repeat_rule_form(self) -> None:
        self.make_manager()
        response = self.client.get(
            reverse(
                "repeat-rule",
                kwargs={"project": self.project.slug, "language": "cs"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save repeat rule")

    def test_queue_renders_unselected_decision_and_preview_selection(self) -> None:
        """The Variant-C queue always sends a decision through the preview."""
        translation = self.component.translation_set.get(language_code="cs")
        source = "A repeat that needs a decision"
        for position, (context, target) in enumerate(
            [("dialogue", "One"), ("menu", "Two")], start=1000
        ):
            source_unit = self.component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                source_unit=source_unit,
                context=context,
                source=source,
                target=target,
                state=STATE_TRANSLATED,
            )
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        response = self.client.get(
            reverse(
                "repeat-queue",
                kwargs={"project": self.project.slug, "language": "cs"},
            )
        )

        self.assertContains(response, "What to do with this group?")
        self.assertContains(response, 'aria-disabled="true"')
        self.assertNotContains(response, 'checked="checked"')
        group = policy.groups.get()

        preview = self.client.post(
            reverse("repeat-preview", kwargs={"group_id": group.pk}),
            {"choice": "variant", "target": "One"},
        )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Nothing is saved yet")
        self.assertContains(preview, "1 of 2 places will change.")
        self.assertContains(preview, 'name="unit"')
        self.assertContains(preview, "<code>menu</code>")
        self.assertContains(preview, "<code>Two</code>")
        self.assertContains(preview, "Already translated this way")
        self.assertNotContains(preview, "already-matches")
        self.assertContains(preview, f'action="{reverse("repeat-apply")}"')

    def test_keep_different_requires_a_preview_before_recording_decision(self) -> None:
        """Keeping variants independent must not mutate the group from the queue."""
        self.make_manager()
        translation = self.component.translation_set.get(language_code="cs")
        source = "A repeat that remains different"
        first = None
        for position, context in enumerate(("dialogue", "menu"), start=1100):
            source_unit = self.component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            unit = translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                source_unit=source_unit,
                context=context,
                source=source,
                target=context,
                state=STATE_TRANSLATED,
            )
            if first is None:
                first = unit
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        group = get_or_create_group(policy, first)

        preview = self.client.post(
            reverse("repeat-preview", kwargs={"group_id": group.pk}),
            {"choice": "keep"},
        )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Nothing is saved yet")
        group.refresh_from_db()
        self.assertEqual(group.decision_origin, "")

        applied = self.client.post(
            reverse("repeat-apply"),
            {"token": preview.context["preview"].token, "group": group.pk},
            follow=True,
        )
        self.assertContains(applied, "the translations stay different")
        group.refresh_from_db()
        self.assertEqual(group.decision_origin, "independent")

    def test_apply_returns_to_queue_with_summary_and_undo(self) -> None:
        self.make_manager()
        translation = self.add_repeat("Shared text", ["One", "One", "Two"])
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        first = translation.unit_set.get(context="key1000")
        group = get_or_create_group(policy, first)
        preview = self.client.post(
            reverse("repeat-preview", kwargs={"group_id": group.pk}),
            {"choice": "variant", "target": "One"},
        )
        units = [member.unit_id for member in preview.context["preview"].changing]

        applied = self.client.post(
            reverse("repeat-apply"),
            {
                "token": preview.context["preview"].token,
                "group": group.pk,
                "unit": units,
            },
        )

        self.assertEqual(applied.status_code, 302)
        queue = self.client.get(applied.url)
        self.assertContains(queue, '"Shared text" now has one shared translation')
        self.assertContains(queue, "1 place was changed.")
        self.assertContains(queue, "2 more already had this translation.")
        self.assertContains(queue, reverse("repeat-undo"))
        self.assertEqual(translation.unit_set.get(context="key1002").target, "One")

        undone = self.client.post(
            reverse("repeat-undo"),
            {"token": queue.context["decision"]["token"]},
            follow=True,
        )

        self.assertContains(undone, "was undone")
        self.assertContains(undone, "1 place got its previous translation back.")
        self.assertEqual(translation.unit_set.get(context="key1002").target, "Two")

    def test_stale_apply_returns_to_queue_with_error(self) -> None:
        translation = self.add_repeat("Stale text", ["One", "Two"])
        policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        group = get_or_create_group(policy, translation.unit_set.get(context="key1000"))

        response = self.client.post(
            reverse("repeat-apply"),
            {"token": "broken", "group": group.pk},
            follow=True,
        )

        self.assertContains(response, "Nothing was changed")

    def test_recommendation_paid_trigger_has_free_confirmation(self) -> None:
        self.make_manager()
        translation = self.component.translation_set.get(language_code="cs")
        save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        profile = SimpleNamespace(
            model="test-model", provider="test", profile_fingerprint="p" * 64
        )

        with (
            patch(
                "weblate.trans.views.repeats.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.views.repeats.resolve_judge_seat_profile",
                return_value=profile,
            ),
        ):
            response = self.client.get(
                reverse(
                    "repeat-recommend",
                    kwargs={"project": self.project.slug, "language": "cs"},
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estimated cost")
        self.assertContains(response, "Unknown")
        self.assertContains(response, "Maximum paid requests")


class RepeatBulkViewsTest(ViewTestCase):
    """Review, status and recovery screens over confirmed bulk batches."""

    def setUp(self) -> None:
        super().setUp()
        self.make_manager()
        self.translation = self.component.translation_set.get(language_code="cs")
        self.group_units: dict[int, list] = {}
        self.policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=self.translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )

    def add_group(self, source: str, targets: list[str], start: int = 1000):
        """Create one diverging exact-repeat group's units with real contexts."""
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

    def make_group(self, source: str, targets: list[str], start: int = 1000):
        """Durable group identity plus its live units, in pk order."""
        units = self.add_group(source, targets, start=start)
        group = get_or_create_group(self.policy, units[0])
        self.group_units[group.pk] = units
        return group, units

    def make_recommendation_run(self, **fields) -> RepeatRecommendationRun:
        return RepeatRecommendationRun.objects.create(
            policy=self.policy,
            actor=self.user,
            snapshot={"policy_revision": self.policy.revision},
            snapshot_fingerprint=fingerprint({"policy_revision": self.policy.revision}),
            profile_fingerprint=fingerprint(
                {"model": "fixture-model", "temperature": 0}
            ),
            prompt_fingerprint=fingerprint("repeat-recommendation-prompt"),
            request_cap=1,
            **fields,
        )

    def make_attempt(
        self, run: RepeatRecommendationRun, *, status: str, deadline_at=None
    ) -> RepeatRecommendationAttempt:
        return RepeatRecommendationAttempt.objects.create(
            run=run,
            ordinal=run.attempts.count() + 1,
            request_snapshot={"groups": []},
            status=status,
            deadline_at=deadline_at,
        )

    def make_recommendation(
        self,
        run: RepeatRecommendationRun,
        group,
        units: list,
        *,
        target: list,
        action: str = "propose_new",
        exclusions=(),
        attempt: RepeatRecommendationAttempt | None = None,
        rationale: str = "The key names this exact meaning.",
    ) -> RepeatRecommendationResult:
        """Store a result frozen against a real, current group context."""
        context = build_group_context(policy=self.policy, group=group, units=units)
        return RepeatRecommendationResult.objects.create(
            run=run,
            attempt=attempt,
            group=group,
            group_revision=group.revision,
            snapshot_fingerprint=context_fingerprint(context),
            context_fingerprint=context_fingerprint(context),
            action=action,
            target=list(target),
            exclusions=list(exclusions),
            rationale=rationale,
        )

    @property
    def review_url(self) -> str:
        return reverse(
            "repeat-bulk-review",
            kwargs={"project": self.project.slug, "language": "cs"},
        )

    @property
    def recommend_url(self) -> str:
        return reverse(
            "repeat-recommend",
            kwargs={"project": self.project.slug, "language": "cs"},
        )

    def test_start_recommendations_reserves_only_the_capped_requests(self) -> None:
        """The POST must not reserve the same groups again after preparation."""
        self.make_group("Sword", ["Blade", "Sabre"])
        profile = SimpleNamespace(
            profile_fingerprint="p" * 64,
            model="test-model",
            temperature=0,
            response_format="json_object",
            provider="test",
            reasoning="",
        )
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=profile,
            ),
            patch("weblate.trans.repeat_recommendations.queue_attempt"),
        ):
            response = self.client.post(
                self.recommend_url, {"action": "start", "request_cap": "1"}
            )

        self.assertEqual(response.status_code, 302)
        run = RepeatRecommendationRun.objects.get()
        self.assertEqual(run.requests_reserved, 1)
        self.assertEqual(run.attempts.count(), 1)
        self.assertEqual(
            run.attempts.get().request_snapshot["groups"][0]["group"],
            self.policy.groups.get().pk,
        )

    def test_free_preview_matches_capped_paid_selection(self) -> None:
        groups = [
            self.make_group(
                f"Preview source {index}", ["One", "Two"], start=21000 + index * 10
            )[0]
            for index in range(3)
        ]
        consistent, _ = self.make_group(
            "Already consistent", ["Same", "Same"], start=21100
        )
        profile = SimpleNamespace(
            profile_fingerprint="p" * 64,
            model="test-model",
            temperature=0,
            response_format="json_object",
            provider="test",
            reasoning="",
        )
        with (
            patch(
                "weblate.trans.views.repeats.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.views.repeats.resolve_judge_seat_profile",
                return_value=profile,
            ),
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=profile,
            ),
            patch(
                "weblate.trans.repeat_recommendations.REPEAT_RECOMMENDATION_BATCH_SIZE",
                1,
            ),
            patch("weblate.trans.repeat_recommendations.queue_attempt"),
        ):
            preview = self.client.get(self.recommend_url, {"request_cap": "2"})
            self.assertEqual(preview.context["candidate_count"], 3)
            self.assertEqual(preview.context["request_count"], 2)
            self.assertEqual(preview.context["unsent_count"], 1)
            self.assertContains(preview, "3 candidate groups")
            self.assertContains(preview, "2 paid requests")
            self.assertContains(preview, "1 group outside the limit")
            self.assertEqual(RepeatRecommendationRun.objects.count(), 0)

            started = self.client.post(
                self.recommend_url, {"action": "start", "request_cap": "2"}
            )

        self.assertEqual(started.status_code, 302)
        run = RepeatRecommendationRun.objects.get()
        self.assertEqual(run.attempts.count(), 2)
        self.assertEqual(run.unsent_groups, 1)
        sent = {
            group["group"]
            for attempt in run.attempts.all()
            for group in attempt.request_snapshot["groups"]
        }
        self.assertEqual(len(sent), 2)
        self.assertTrue(sent <= {group.pk for group in groups})
        self.assertNotIn(consistent.pk, sent)

    def test_queue_card_and_banner_discard_changed_recommendation(self) -> None:
        """An ordinary Unit edit invalidates both displays without a group bump."""
        group, units = self.make_group("Sword", ["Blade", "Sabre"])
        result = self.make_recommendation(
            self.make_recommendation_run(
                status=RepeatRecommendationRun.Status.COMPLETED
            ),
            group,
            units,
            target=["Blade"],
        )
        before = self.client.get(self.queue_url)
        self.assertEqual(before.context["bulk_ready"], 1)
        self.assertEqual(before.context["groups"][0]["recommendation"].pk, result.pk)

        units[0].target = "Dagger"
        units[0].save(update_fields=["target"])
        group.refresh_from_db()
        self.assertEqual(group.revision, result.group_revision)

        after = self.client.get(self.queue_url)
        self.assertEqual(after.context["bulk_ready"], 0)
        self.assertIsNone(after.context["groups"][0]["recommendation"])

    @property
    def queue_url(self) -> str:
        return reverse(
            "repeat-queue",
            kwargs={"project": self.project.slug, "language": "cs"},
        )

    def status_url(self, run: RepeatBulkRun) -> str:
        return reverse(
            "repeat-bulk-status",
            kwargs={"project": self.project.slug, "language": "cs", "token": run.token},
        )

    def review_manifest(self) -> str:
        response = self.client.get(self.review_url)
        self.assertEqual(response.status_code, 200)
        return response.context["manifest"]

    def apply_via_view(self, *results) -> RepeatBulkRun:
        manifest = self.review_manifest()
        response = self.client.post(
            self.review_url,
            {
                "action": "apply",
                "manifest": manifest,
                "result": [str(result.pk) for result in results],
            },
        )
        self.assertEqual(response.status_code, 302)
        return RepeatBulkRun.objects.get()

    def revoke_edit(self) -> None:
        self.user.groups.remove(Group.objects.get(name="Managers"))
        self.project.remove_user(self.user)
        self.user.clear_permissions_cache()

    def attempt_fixture(self):
        """One partially failed run plus one run with overdue/reserved sends."""
        group, units = self.make_group("Attempt count line", ["T1", "T2"], start=22000)
        done_run = self.make_recommendation_run()
        completed = self.make_attempt(
            done_run, status=RepeatRecommendationAttempt.Status.COMPLETED
        )
        self.make_attempt(done_run, status=RepeatRecommendationAttempt.Status.FAILED)
        self.make_recommendation(
            done_run, group, units, target=["Att"], attempt=completed
        )
        pending_run = self.make_recommendation_run()
        overdue = self.make_attempt(
            pending_run,
            status=RepeatRecommendationAttempt.Status.SENT,
            deadline_at=timezone.now() - timedelta(hours=1),
        )
        reserved = self.make_attempt(
            pending_run, status=RepeatRecommendationAttempt.Status.RESERVED
        )
        live = self.make_attempt(
            pending_run,
            status=RepeatRecommendationAttempt.Status.SENT,
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        return done_run, pending_run, overdue, reserved, live

    def assert_checked(self, response, value: int) -> None:
        """Match a checked checkbox across formatter-normalized HTML whitespace."""
        content = " ".join(response.content.decode().split())
        self.assertIn(f'value="{value}" checked', content)

    def test_review_renders_rows_places_and_counts(self) -> None:
        group, units = self.make_group(
            "A shared sword name", ["Old", "Older", "Shared", "Oddest"]
        )
        units[1].state = STATE_APPROVED
        units[1].save(update_fields=["state"])
        result = self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            target=["Shared"],
            exclusions=[units[0].pk],
        )

        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        # The manifest and exactly the reviewed result as a checked form field.
        self.assertContains(response, 'name="manifest"')
        self.assertContains(response, 'name="result"', count=1)
        self.assert_checked(response, result.pk)
        # Every source/target plural form, the action and the rationale.
        self.assertContains(response, "A shared sword name")
        self.assertContains(response, "Propose a new translation")
        self.assertContains(response, "The key names this exact meaning.")
        self.assertContains(response, "<code>Shared</code>")
        # Conservative writable count; approved is neither writable nor just
        # labeled excluded.
        self.assertContains(response, "1 place can change.")
        self.assertContains(response, "1 place already has this translation.")
        self.assertContains(response, "1 place: Approved, not changed")
        self.assertContains(response, "Explicit exceptions (stay unchanged):")
        self.assertContains(response, f"#{units[0].pk}")
        self.assertContains(response, "Excluded", count=1)
        # Per-place rows show key, component and current target with unit ids.
        self.assertContains(response, "A shared sword name-1000")
        self.assertContains(response, "A shared sword name-1003")
        self.assertContains(response, "<code>Oddest</code>")
        self.assertContains(response, "<code>Older</code>")

    def test_review_lists_needs_human_and_keep_independent(self) -> None:
        human_group, human_units = self.make_group(
            "Unclear battle line", ["Q1", "Q2"], start=2000
        )
        own_group, own_units = self.make_group(
            "Two meanings here", ["M1", "M2"], start=3000
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            human_group,
            human_units,
            target=[],
            action="needs_human",
            rationale="The context is ambiguous.",
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            own_group,
            own_units,
            target=[],
            action="keep_independent",
            rationale="Distinct meanings in context.",
        )

        response = self.client.get(self.review_url)

        self.assertContains(response, "Needs a human decision")
        self.assertContains(response, "Kept independent")
        self.assertContains(response, "Unclear battle line")
        self.assertContains(response, "Two meanings here")
        self.assertContains(response, "The context is ambiguous.")
        self.assertContains(response, "Distinct meanings in context.")
        # Links to the individual units and their queue groups.
        for unit in [*human_units, *own_units]:
            self.assertContains(response, unit.get_absolute_url())
        self.assertContains(response, f"?group={human_group.pk}")
        self.assertContains(response, f"?group={own_group.pk}")
        # These manual decisions never enter the apply form.
        self.assertNotContains(response, 'name="result"')

    def test_review_shows_stale_count_with_explanation(self) -> None:
        current_group, current_units = self.make_group(
            "Current line", ["C1", "C2"], start=4000
        )
        stale_group, stale_units = self.make_group(
            "Stale line", ["S1", "S2"], start=5000
        )
        current = self.make_recommendation(
            self.make_recommendation_run(), current_group, current_units, target=["Cur"]
        )
        stale = self.make_recommendation(
            self.make_recommendation_run(), stale_group, stale_units, target=["Sta"]
        )
        stale_units[0].target = "Human rewrite"
        stale_units[0].save(update_fields=["target"])

        response = self.client.get(self.review_url)

        self.assertContains(response, 'name="result"', count=1)
        self.assert_checked(response, current.pk)
        self.assertNotContains(response, f'value="{stale.pk}"')
        self.assertContains(
            response,
            "1 recommendation is out of date and needs a refresh before it can be applied.",
        )

    def test_review_get_makes_no_durable_changes(self) -> None:
        group, units = self.make_group("Untouched line", ["U1", "U2"], start=6000)
        self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Uni"]
        )
        before = [unit.target for unit in units]

        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RepeatBulkRun.objects.count(), 0)
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)
        for unit, target in zip(units, before, strict=True):
            unit.refresh_from_db()
            self.assertEqual(unit.target, target)

    def test_apply_binds_displayed_result_and_redirects_to_status(self) -> None:
        group, units = self.make_group(
            "Wording to unify", ["Alpha", "Beta"], start=7000
        )
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Unified"]
        )

        run = self.apply_via_view(result)

        self.assertEqual(run.action, RepeatBulkRun.Action.APPLY)
        self.assertEqual(run.policy, self.policy)
        self.assertEqual(run.actor, self.user)
        self.assertEqual(run.total, 1)
        item = run.items.get()
        self.assertEqual(item.result_id, result.pk)
        self.assertEqual(item.decision["target"], ["Unified"])
        self.assertEqual(
            item.decision["result_fingerprint"], result_fingerprint(result)
        )
        response = self.client.get(self.status_url(run))
        self.assertContains(response, "Wording to unify")
        self.assertContains(response, "Queued")

    def test_post_binds_reviewed_result_not_newer_run(self) -> None:
        group, units = self.make_group("Frozen decision", ["F1", "F2"], start=8000)
        old = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Old ways"]
        )
        manifest = self.review_manifest()
        newer = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["New ways"]
        )

        response = self.client.post(
            self.review_url,
            {"action": "apply", "manifest": manifest, "result": [str(old.pk)]},
        )

        self.assertEqual(response.status_code, 302)
        item = RepeatBulkRun.objects.get().items.get()
        self.assertEqual(item.result_id, old.pk)
        self.assertEqual(item.decision["target"], ["Old ways"])
        self.assertNotEqual(item.decision["target"], newer.target)
        self.assertEqual(item.decision["result_fingerprint"], result_fingerprint(old))

    def test_review_table_collects_results_across_two_runs(self) -> None:
        first_group, first_units = self.make_group(
            "Sword", ["Epee", "Glaive"], start=9000
        )
        second_group, second_units = self.make_group(
            "A long sentence that players rarely see twice",
            ["Long one", "Long two"],
            start=9500,
        )
        first = self.make_recommendation(
            self.make_recommendation_run(), first_group, first_units, target=["Blade"]
        )
        second = self.make_recommendation(
            self.make_recommendation_run(),
            second_group,
            second_units,
            target=["Long shared"],
        )

        response = self.client.get(self.review_url)
        content = response.content.decode()

        self.assertContains(response, 'name="result"', count=2)
        self.assert_checked(response, first.pk)
        self.assert_checked(response, second.pk)
        # Stable queue order: short strings first.
        self.assertLess(content.index("Sword"), content.index("A long sentence"))

    def test_partial_provider_failure_keeps_successful_results_reviewable(self) -> None:
        group, units = self.make_group(
            "Partial provider line", ["P1", "P2"], start=10000
        )
        run = self.make_recommendation_run()
        completed = self.make_attempt(
            run, status=RepeatRecommendationAttempt.Status.COMPLETED
        )
        self.make_attempt(run, status=RepeatRecommendationAttempt.Status.FAILED)
        result = self.make_recommendation(
            run, group, units, target=["Par"], attempt=completed
        )

        response = self.client.get(self.review_url)

        self.assert_checked(response, result.pk)
        self.assertContains(response, "Partial provider line")

    def test_stale_post_explains_and_refreshes_without_writes(self) -> None:
        group, units = self.make_group("Edited after review", ["E1", "E2"], start=11000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Est"]
        )
        manifest = self.review_manifest()
        units[0].target = "Human edit"
        units[0].save(update_fields=["target"])

        response = self.client.post(
            self.review_url,
            {"action": "apply", "manifest": manifest, "result": [str(result.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This review confirmation is no longer valid: a string, the repeat "
            "rule or your permissions changed after the review. Nothing was "
            "written; the review below was refreshed and can be selected again.",
        )
        # The refreshed review carries a new manifest and wrote nothing.
        self.assertContains(response, 'name="manifest"')
        self.assertNotEqual(response.context["manifest"], manifest)
        self.assertEqual(RepeatBulkRun.objects.count(), 0)
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)
        units[0].refresh_from_db()
        self.assertEqual(units[0].target, "Human edit")

    def test_unknown_actions_are_rejected(self) -> None:
        group, units = self.make_group("Unknown action line", ["K1", "K2"], start=12000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Unk"]
        )
        manifest = self.review_manifest()

        self.assertEqual(
            self.client.post(
                self.review_url, {"action": "destroy", "manifest": manifest}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(self.review_url, {"manifest": manifest}).status_code, 400
        )
        self.assertEqual(
            self.client.post(self.recommend_url, {"action": "destroy"}).status_code, 400
        )
        run = self.apply_via_view(result)
        self.assertEqual(
            self.client.post(self.status_url(run), {"action": "destroy"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(self.status_url(run), {"action": "apply"}).status_code, 400
        )
        # The rejected posts changed nothing.
        self.assertEqual(run.items.count(), 1)
        self.assertEqual(run.items.get().status, RepeatBulkItem.Status.PENDING)

    def test_disabled_policy_keeps_status_and_undo_reachable(self) -> None:
        group, units = self.make_group("Historic wording", ["One", "Two"], start=13000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Shared"]
        )
        run = self.apply_via_view(result)
        process_apply_items(run.pk)
        RepeatPolicy.objects.filter(pk=self.policy.pk).update(enabled=False)

        response = self.client.get(self.status_url(run))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historic wording")
        self.assertContains(
            response, "The batch finished: every group was processed successfully."
        )
        self.assertContains(response, "2 places changed.")
        self.assertContains(response, 'value="undo"')

        undo_response = self.client.post(self.status_url(run), {"action": "undo"})

        self.assertEqual(undo_response.status_code, 302)
        undo_run = run.undo_runs.get()
        self.assertEqual(undo_response.url, self.status_url(undo_run))
        self.assertEqual(self.client.get(self.status_url(undo_run)).status_code, 200)

    def test_permission_revocation_blocks_mutation_without_writes(self) -> None:
        group, units = self.make_group("Revoked access line", ["R1", "R2"], start=14000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Rev"]
        )
        manifest = self.review_manifest()
        before = [unit.target for unit in units]
        self.revoke_edit()

        apply_response = self.client.post(
            self.review_url,
            {"action": "apply", "manifest": manifest, "result": [str(result.pk)]},
        )

        self.assertEqual(apply_response.status_code, 403)
        self.assertEqual(RepeatBulkRun.objects.count(), 0)
        for unit, target in zip(units, before, strict=True):
            unit.refresh_from_db()
            self.assertEqual(unit.target, target)

        run = RepeatBulkRun.objects.create(
            policy=self.policy,
            actor=self.user,
            action=RepeatBulkRun.Action.APPLY,
            status=RepeatBulkRun.Status.FAILED,
            failure_code="item-error",
        )
        resume_response = self.client.post(self.status_url(run), {"action": "resume"})

        self.assertEqual(resume_response.status_code, 403)
        self.assertEqual(self.client.get(self.status_url(run)).status_code, 403)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.FAILED)
        self.assertEqual(RepeatBulkRun.objects.count(), 1)
        self.assertFalse(run.undo_runs.exists())

    def test_failed_partial_batch_offers_resume_and_undo(self) -> None:
        first_group, _ = self.make_group("First batch line", ["1a", "1b"], start=15000)
        second_group, _ = self.make_group(
            "Second batch line", ["2a", "2b"], start=15500
        )
        first = self.make_recommendation(
            self.make_recommendation_run(),
            first_group,
            self.group_units[first_group.pk],
            target=["Shared"],
        )
        second = self.make_recommendation(
            self.make_recommendation_run(),
            second_group,
            self.group_units[second_group.pk],
            target=["Shared"],
        )
        run = self.apply_via_view(first, second)
        self.assertTrue(process_next_apply_item(run.pk))
        RepeatBulkRun.objects.filter(pk=run.pk).update(actor=None)
        self.assertFalse(process_next_apply_item(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.FAILED)
        self.assertEqual(run.failure_code, "actor-missing")
        self.assertEqual(run.items.exclude(decision_event__isnull=True).count(), 1)

        response = self.client.get(self.status_url(run))

        self.assertContains(response, "Processing stopped after 1 processed group.")
        self.assertContains(
            response, "The user who started this batch no longer exists."
        )
        self.assertContains(response, 'value="resume"')
        self.assertContains(response, 'value="undo"')
        self.assertContains(response, "Resume processing")
        self.assertContains(response, "Cancel this batch")

    def test_batch_without_committed_events_is_not_undoable(self) -> None:
        group, units = self.make_group("Stale batch line", ["T1", "T2"], start=16000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Tog"]
        )
        run = self.apply_via_view(result)
        units[0].target = "Human edit first"
        units[0].save(update_fields=["target"])
        process_apply_items(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.COMPLETED)
        self.assertEqual(run.written, 0)
        self.assertFalse(run.items.exclude(decision_event__isnull=True).exists())

        response = self.client.get(self.status_url(run))

        self.assertContains(
            response, "The batch finished, but 1 group was not changed."
        )
        self.assertContains(
            response,
            "A string changed after this decision was reviewed; refresh and decide again.",
        )
        self.assertNotContains(response, "Cancel this batch")
        self.assertNotContains(response, 'value="undo"')

        forced = self.client.post(self.status_url(run), {"action": "undo"})
        self.assertEqual(forced.status_code, 200)
        self.assertContains(forced, "This batch cannot accept that action now")
        self.assertFalse(run.undo_runs.exists())

    def test_undo_is_idempotent(self) -> None:
        group, units = self.make_group("Undo twice line", ["D1", "D2"], start=17000)
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Dun"]
        )
        run = self.apply_via_view(result)
        process_apply_items(run.pk)

        first = self.client.post(self.status_url(run), {"action": "undo"})
        second = self.client.post(self.status_url(run), {"action": "undo"})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        undo_run = run.undo_runs.get()
        self.assertEqual(first.url, self.status_url(undo_run))
        self.assertEqual(second.url, self.status_url(undo_run))
        self.assertEqual(run.undo_runs.count(), 1)

    def test_resume_is_idempotent(self) -> None:
        first_group, _ = self.make_group("Resume twice one", ["U1", "U2"], start=18000)
        second_group, _ = self.make_group("Resume twice two", ["V1", "V2"], start=18500)
        first = self.make_recommendation(
            self.make_recommendation_run(),
            first_group,
            self.group_units[first_group.pk],
            target=["Res"],
        )
        second = self.make_recommendation(
            self.make_recommendation_run(),
            second_group,
            self.group_units[second_group.pk],
            target=["Res"],
        )
        run = self.apply_via_view(first, second)
        self.assertTrue(process_next_apply_item(run.pk))
        RepeatBulkRun.objects.filter(pk=run.pk).update(actor=None)
        self.assertFalse(process_next_apply_item(run.pk))

        first_post = self.client.post(self.status_url(run), {"action": "resume"})
        second_post = self.client.post(self.status_url(run), {"action": "resume"})

        self.assertEqual(first_post.status_code, 302)
        self.assertEqual(second_post.status_code, 302)
        self.assertEqual(first_post.url, self.status_url(run))
        self.assertEqual(second_post.url, self.status_url(run))
        run.refresh_from_db()
        self.assertEqual(run.status, RepeatBulkRun.Status.QUEUED)
        self.assertEqual(
            run.items.filter(status=RepeatBulkItem.Status.PENDING).count(), 1
        )

    def test_undo_conflicts_are_listed_with_recipient_links(self) -> None:
        group, units = self.make_group(
            "Conflicted wording", ["A one", "B one", "C one"], start=19000
        )
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Shared words"]
        )
        run = self.apply_via_view(result)
        process_apply_items(run.pk)
        units[1].target = "Later human edit"
        units[1].save(update_fields=["target"])

        response = self.client.post(self.status_url(run), {"action": "undo"})
        undo_run = run.undo_runs.get()
        self.assertEqual(response.url, self.status_url(undo_run))
        process_undo_items(undo_run.pk)

        status = self.client.get(self.status_url(undo_run))

        self.assertContains(status, "2 places restored.")
        self.assertContains(status, "1 place could not be undone.")
        self.assertContains(status, "Changed after the decision")
        self.assertContains(status, units[1].get_absolute_url())
        self.assertContains(status, f"#{units[1].pk}")
        self.assertContains(status, units[1].context)

    def test_queue_banner_counts_current_applicable_results(self) -> None:
        first_group, first_units = self.make_group(
            "Banner line", ["B1", "B2"], start=20000
        )
        second_group, second_units = self.make_group(
            "Banner text here", ["C1", "C2"], start=20500
        )
        human_group, human_units = self.make_group(
            "Needs eyes", ["N1", "N2"], start=21000
        )
        stale_group, stale_units = self.make_group(
            "Stale banner", ["S1", "S2"], start=21500
        )
        self.make_recommendation(
            self.make_recommendation_run(), first_group, first_units, target=["Ban"]
        )
        self.make_recommendation(
            self.make_recommendation_run(), second_group, second_units, target=["Bax"]
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            human_group,
            human_units,
            target=[],
            action="needs_human",
        )
        self.make_recommendation(
            self.make_recommendation_run(), stale_group, stale_units, target=["Sta"]
        )
        stale_units[0].target = "Human rewrite"
        stale_units[0].save(update_fields=["target"])

        response = self.client.get(self.queue_url)

        # Two current applicable results across runs: not the needs-human one
        # and not the out-of-date one.
        self.assertContains(
            response, "2 current recommendations are ready to review and apply."
        )
        self.assertContains(response, f'href="{self.review_url}"')

    def test_recommend_page_exposes_attempt_counts_and_overdue_sends(self) -> None:
        done_run, pending_run, overdue, reserved, _live = self.attempt_fixture()
        profile = SimpleNamespace(
            model="test-model", provider="test", profile_fingerprint="p" * 64
        )

        with (
            patch(
                "weblate.trans.views.repeats.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.views.repeats.resolve_judge_seat_profile",
                return_value=profile,
            ),
        ):
            response = self.client.get(self.recommend_url)

        rows = {row["run"].pk: row for row in response.context["attempt_rows"]}
        self.assertEqual(rows[done_run.pk]["completed"], 1)
        self.assertEqual(rows[done_run.pk]["failed"], 1)
        self.assertEqual(rows[done_run.pk]["results"], 1)
        self.assertEqual(rows[pending_run.pk]["reserved"], 1)
        self.assertEqual(rows[pending_run.pk]["sent"], 2)
        self.assertEqual(rows[pending_run.pk]["overdue"], 1)
        self.assertContains(response, "Overdue sends")
        self.assertContains(
            response, "Reconcile overdue sends and re-enqueue reserved requests"
        )
        # The GET rendered overdue work and durable status without mutating it.
        overdue.refresh_from_db()
        self.assertEqual(overdue.status, RepeatRecommendationAttempt.Status.SENT)
        reserved.refresh_from_db()
        self.assertEqual(reserved.status, RepeatRecommendationAttempt.Status.RESERVED)

    def test_reconcile_action_marks_overdue_and_requeues_reserved(self) -> None:
        done_run, pending_run, overdue, reserved, live = self.attempt_fixture()
        profile = SimpleNamespace(
            model="test-model", provider="test", profile_fingerprint="p" * 64
        )

        with (
            patch(
                "weblate.trans.views.repeats.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.views.repeats.resolve_judge_seat_profile",
                return_value=profile,
            ),
            patch(
                "weblate.trans.repeat_recommendations.queue_attempt"
            ) as queue_attempt,
        ):
            response = self.client.post(
                self.recommend_url, {"action": "reconcile"}, follow=True
            )

        self.assertContains(response, "re-enqueued 1 reserved requests")
        overdue.refresh_from_db()
        self.assertEqual(overdue.status, RepeatRecommendationAttempt.Status.UNKNOWN)
        self.assertEqual(overdue.failure, "expired")
        # A still-live attempt within its deadline stays active.
        live.refresh_from_db()
        self.assertEqual(live.status, RepeatRecommendationAttempt.Status.SENT)
        reserved.refresh_from_db()
        self.assertEqual(reserved.status, RepeatRecommendationAttempt.Status.RESERVED)
        self.assertEqual(queue_attempt.call_count, 1)
        self.assertEqual(queue_attempt.call_args.kwargs["attempt"].pk, reserved.pk)
        # Recovery reuses existing reservations; it never pays for new ones.
        self.assertEqual(pending_run.attempts.count(), 3)
        self.assertEqual(done_run.attempts.count(), 2)

    def test_review_query_cost_is_bounded(self) -> None:
        first_group, _ = self.make_group("Count line one", ["Q1", "Q2"], start=23000)
        self.make_recommendation(
            self.make_recommendation_run(),
            first_group,
            self.group_units[first_group.pk],
            target=["Cnt"],
        )
        with CaptureQueriesContext(connection) as first_capture:
            response = self.client.get(self.review_url)
        self.assertEqual(response.status_code, 200)

        second_group, _ = self.make_group(
            "Count line two", [f"S{index}" for index in range(6)], start=24000
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            second_group,
            self.group_units[second_group.pk],
            target=["Cnt2"],
        )
        with CaptureQueriesContext(connection) as second_capture:
            response = self.client.get(self.review_url)

        self.assertContains(response, 'name="result"', count=2)
        # Upper bound for two rows with eight places on a full authenticated
        # page (measured 108 before the display layer warmed its FK chains).
        self.assertLessEqual(len(second_capture.captured_queries), 130)
        # Adding one more row with six places must not add a unit/URL read
        # stack per place; the bounded remainder is the service's frozen
        # context fingerprint inputs (labels and plural per member).
        self.assertLessEqual(
            len(second_capture.captured_queries) - len(first_capture.captured_queries),
            30,
        )

    def test_empty_recommendations_do_not_scan_every_repeat_group(self) -> None:
        self.make_group("First unreviewed repeat", ["One", "Two"], start=26000)
        self.make_group("Second unreviewed repeat", ["Three", "Four"], start=27000)

        with CaptureQueriesContext(connection) as capture:
            current = current_recommendations(self.policy, actor=self.user)

        self.assertEqual(current, {})
        self.assertLessEqual(len(capture.captured_queries), 2)

    def test_queue_banner_query_cost_is_bounded(self) -> None:
        group, units = self.make_group("Banner count line", ["BC1", "BC2"], start=25000)
        self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Ban"]
        )

        with CaptureQueriesContext(connection) as capture:
            response = self.client.get(self.queue_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "1 current recommendation is ready to review and apply."
        )
        self.assertLessEqual(len(capture.captured_queries), 60)
