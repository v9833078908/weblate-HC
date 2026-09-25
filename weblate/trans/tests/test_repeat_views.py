# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Views for the managed repeat queue."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from weblate.auth.models import Group
from weblate.checks.models import CHECKS
from weblate.trans.models import (
    JudgeRunUnit,
    JudgeVerdict,
    ProducerRun,
    RepeatBulkItem,
    RepeatBulkRun,
    RepeatDecisionEvent,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationResult,
    RepeatRecommendationRun,
)
from weblate.trans.models.judge import compute_target_hash
from weblate.trans.repeat_bulk import (
    process_apply_items,
    process_next_apply_item,
    process_undo_items,
)
from weblate.trans.repeat_judge import REPEAT_JUDGE_QUERY
from weblate.trans.repeat_recommendations import (
    build_group_context,
    context_fingerprint,
    current_recommendations,
    execute_attempt,
    result_fingerprint,
)
from weblate.trans.repeats import fingerprint, get_or_create_group, save_policy
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import join_plural
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

    def test_queue_without_policy_skips_the_judge_panel(self) -> None:
        with patch("weblate.trans.views.repeats._judge_panel") as judge_panel:
            response = self.client.get(
                reverse(
                    "repeat-queue",
                    kwargs={"project": self.project.slug, "language": "cs"},
                )
                + "?status=open&judge=ready"
            )

        self.assertEqual(response.status_code, 200)
        judge_panel.assert_not_called()
        self.assertIsNone(response.context["judge_panel"])
        self.assertIsNone(response.context["judge_filter"])
        self.assertNotIn("judge=", response.context["query_string"])

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

    def test_queue_renders_preselected_decision_and_preview_selection(self) -> None:
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
        # Nothing is judged yet: the first of the equally used variants is
        # preselected (D17), and the decision still goes through the preview.
        self.assertContains(response, 'aria-disabled="false"')
        self.assertRegex(
            response.content.decode(),
            r'name="target"\s+value="One"\s+data-choice="variant"\s+checked',
        )
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

    @override_settings(
        JUDGE_ENABLED=True, JUDGE_API_KEY="", JUDGE_MODEL_SEAT_1="test-model"
    )
    def test_missing_api_key_disables_start_and_refuses_stale_post(self) -> None:
        self.make_group("Sword", ["Blade", "Sabre"])

        page = self.client.get(self.recommend_url)
        self.assertContains(page, "Recommendations are unavailable")
        self.assertNotContains(page, "Start paid recommendation run")

        with patch("weblate.trans.repeat_recommendations.queue_attempt") as queue:
            response = self.client.post(
                self.recommend_url, {"action": "start", "request_cap": "1"}
            )

        self.assertRedirects(response, self.recommend_url)
        self.assertFalse(RepeatRecommendationRun.objects.exists())
        queue.assert_not_called()

    def test_unknown_delivery_is_resent_only_after_explicit_consent(self) -> None:
        group, _units = self.make_group("Sword", ["Blade", "Sabre"])
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
            patch("weblate.trans.repeat_recommendations.queue_attempt"),
        ):
            self.client.post(
                self.recommend_url, {"action": "start", "request_cap": "1"}
            )
            with patch(
                "weblate.trans.repeat_recommendations.post_chat_completion",
                side_effect=RuntimeError("connection dropped"),
            ):
                execute_attempt(attempt=RepeatRecommendationAttempt.objects.get())

            held = self.client.get(self.recommend_url)
            self.assertEqual(held.context["candidate_count"], 0)
            self.assertEqual(held.context["unknown_count"], 1)
            self.assertContains(held, 'name="retry_unknown"')
            self.assertNotContains(held, "Start paid recommendation run")

            consented = self.client.get(self.recommend_url, {"retry_unknown": "1"})
            self.assertEqual(consented.context["candidate_count"], 1)
            self.assertContains(consented, "Start paid recommendation run")

            self.client.post(
                self.recommend_url,
                {"action": "start", "request_cap": "1", "retry_unknown": "1"},
            )

        resent = RepeatRecommendationRun.objects.order_by("-pk").first()
        self.assertEqual(
            resent.attempts.get().request_snapshot["groups"][0]["group"], group.pk
        )

    def test_queue_card_and_banner_discard_changed_recommendation(self) -> None:
        """An ordinary Unit edit invalidates both displays without a group bump."""
        group, units = self.make_group("Sword", ["Blade", "Sabre"])
        # The banner counts ready groups: the judge passed the model's pick.
        for unit in units:
            self.make_judge_verdict(unit)
        result = self.make_recommendation(
            self.make_recommendation_run(
                status=RepeatRecommendationRun.Status.COMPLETED
            ),
            group,
            units,
            target=["Blade"],
            action="use_existing",
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

    def places_url(self, result) -> str:
        return reverse(
            "repeat-bulk-places",
            kwargs={
                "project": self.project.slug,
                "language": "cs",
                "result_id": result.pk,
            },
        )

    def assert_unchecked(self, response, value: int) -> None:
        content = " ".join(response.content.decode().split())
        self.assertIn(f'value="{value}"', content)
        self.assertNotIn(f'value="{value}" checked', content)

    def test_review_renders_compact_rows_and_places_on_demand(self) -> None:
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
        self.assertContains(response, 'name="manifest"')
        self.assertContains(response, 'name="result"', count=1)
        # One compact row: source, target, action, rationale and the count.
        self.assertContains(response, "A shared sword name")
        self.assertContains(response, "Propose a new translation")
        self.assertContains(response, "The key names this exact meaning.")
        self.assertContains(response, "<code>Shared</code>")
        self.assertContains(response, "1 place change")
        self.assertContains(response, "of 4 places")
        # Per-place detail is not rendered until it is asked for.
        self.assertContains(response, self.places_url(result))
        self.assertContains(response, f'data-url="{self.places_url(result)}?inline=1"')
        self.assertContains(response, f'aria-controls="places-{result.pk}"')
        self.assertContains(response, f'id="places-{result.pk}"')
        self.assertNotContains(response, "A shared sword name-1000")
        self.assertNotContains(response, "<code>Oddest</code>")

        places = self.client.get(self.places_url(result))

        self.assertEqual(places.status_code, 200)
        self.assertContains(places, 'class="rq-places-detail"')
        for unit in units:
            self.assertContains(places, unit.get_absolute_url())
        self.assertContains(places, '<code lang="cs">Oddest</code>', html=True)
        self.assertContains(places, "Approved, not changed")
        self.assertContains(places, "Already translated this way")
        self.assertContains(places, "Excluded by the model")
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)

    def test_places_inline_fragment_lists_changes_and_counts_the_rest(
        self,
    ) -> None:
        group, units = self.make_group(
            "An inline sword name",
            ["Shared", "Old", "Older", "Oldest", "Odd"],
            start=1600,
        )
        units[4].state = STATE_APPROVED
        units[4].save(update_fields=["state"])
        result = self.make_recommendation(
            self.make_recommendation_run(), group, units, target=["Shared"]
        )
        url = self.places_url(result)

        with patch("weblate.trans.views.repeats.INLINE_PLACES", 2):
            inline = self.client.get(url, {"inline": "1"})

        self.assertEqual(inline.status_code, 200)
        self.assertTemplateNotUsed(inline, "base.html")
        # Only places that change are listed; the rest link to the full list.
        self.assertEqual(
            [member["key"] for member in inline.context["members"]],
            [units[1].context, units[2].context],
        )
        self.assertContains(inline, 'class="rq-places-detail"')
        self.assertContains(inline, f'<a href="{url}">Show the remaining 1</a>')
        for unit in (units[0], units[3], units[4]):
            self.assertNotContains(inline, unit.context)
        # Unchanged places are only counted, in one line.
        self.assertContains(inline, "1 more place is already translated this way.")
        self.assertContains(inline, "Approved, not changed: 1.")
        self.assertNotContains(inline, "Will change")
        # One component only, so no column repeats it on every place.
        self.assertFalse(inline.context["show_component"])
        self.assertNotContains(inline, '<th scope="col">Component</th>', html=True)

        full = self.client.get(url)

        self.assertTemplateUsed(full, "base.html")
        self.assertEqual(
            [member["key"] for member in full.context["members"]],
            [unit.context for unit in (units[1], units[2], units[3], units[0], units[4])],
        )
        self.assertContains(full, "Will change", count=3)
        self.assertContains(full, "Already translated this way")
        self.assertContains(full, "Approved, not changed")
        self.assertNotContains(full, '<th scope="col">Component</th>', html=True)
        self.assertNotContains(full, "Show the remaining")
        self.assertEqual(RepeatDecisionEvent.objects.count(), 0)

    def test_places_refuse_manual_results_and_other_policies(self) -> None:
        group, units = self.make_group("Manual places", ["M1", "M2"], start=1500)
        manual = self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            target=[],
            action="needs_human",
        )
        self.assertEqual(self.client.get(self.places_url(manual)).status_code, 404)
        other = reverse(
            "repeat-bulk-places",
            kwargs={"project": self.project.slug, "language": "de", "result_id": 1},
        )
        self.assertEqual(self.client.get(other).status_code, 404)

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
            self.make_recommendation_run(),
            stale_group,
            stale_units,
            action="use_existing",
            target=["S1"],
        )
        stale_units[0].target = "Human rewrite"
        stale_units[0].save(update_fields=["target"])

        response = self.client.get(self.review_url)

        self.assertContains(response, 'name="result"', count=1)
        self.assert_unchecked(response, current.pk)
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
        self.assert_unchecked(response, first.pk)
        self.assert_unchecked(response, second.pk)
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

        self.assert_unchecked(response, result.pk)
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
        # The banner counts ready groups: the judge passed the model's pick.
        for unit in (*first_units, *second_units, *human_units, *stale_units):
            self.make_judge_verdict(unit)
        self.make_recommendation(
            self.make_recommendation_run(),
            first_group,
            first_units,
            action="use_existing",
            target=["B1"],
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            second_group,
            second_units,
            action="use_existing",
            target=["C1"],
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
        for unit in units:
            self.make_judge_verdict(unit)
        self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            action="use_existing",
            target=["BC1"],
        )

        with CaptureQueriesContext(connection) as capture:
            response = self.client.get(self.queue_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "1 current recommendation is ready to review and apply."
        )
        self.assertLessEqual(len(capture.captured_queries), 60)

    def make_judge_run(self, **fields) -> ProducerRun:
        project_language = self.project.project_languages[self.translation.language]
        values = {
            "actor": self.user,
            "scope_type": ProducerRun.ScopeType.PROJECT,
            "scope_id": str(self.project.pk),
            "scope_label": str(project_language),
            "scope_path": project_language.get_absolute_url(),
            "requested_query": REPEAT_JUDGE_QUERY,
            "requested_mode": "judge",
            "execution_options": {"judge_proposal_only": True},
            "cap": 3,
            "status": ProducerRun.Status.RUNNING,
        }
        values.update(fields)
        return ProducerRun.objects.create(**values)

    def make_judge_verdict(
        self,
        unit,
        severity=JudgeVerdict.Severity.NONE,
        *,
        back_translation="",
        description="Wrong meaning",
    ):
        return JudgeVerdict.objects.create(
            unit=unit,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="repeat-context",
            judge_model="vendor/model-a",
            seat=1,
            unparsed=False,
            max_severity=severity,
            back_translation=back_translation,
            errors=(
                [
                    {
                        "severity": severity,
                        "category": "mistranslation",
                        "description": description,
                    }
                ]
                if severity
                in {
                    JudgeVerdict.Severity.MAJOR,
                    JudgeVerdict.Severity.CRITICAL,
                }
                else []
            ),
        )

    def test_queue_cards_show_judge_evidence_and_preselect_the_best_variant(
        self,
    ) -> None:
        ready_group, ready = self.make_group(
            "Judge checked ready", ["Recommandé", "À éviter"], start=26320
        )
        flagged_group, flagged = self.make_group(
            "Judge checked flagged",
            ["<b>x</b><script>y</script>", "<color=#FF0000>"],
            start=26330,
        )
        _passed_group, passed = self.make_group(
            "Judge checked passed", ["Pass one", "Pass two"], start=26335
        )
        unchecked_group, _ = self.make_group(
            "Judge unchecked", ["One", "Two"], start=26340
        )
        self.make_judge_verdict(ready[0], back_translation="The checked translation")
        self.make_judge_verdict(
            ready[1], JudgeVerdict.Severity.MAJOR, description="Wrong source meaning"
        )
        self.make_judge_verdict(
            flagged[0], JudgeVerdict.Severity.MAJOR, description="Other reason"
        )
        self.make_judge_verdict(
            flagged[1],
            JudgeVerdict.Severity.CRITICAL,
            description="<color=#FF0000>",
        )
        for unit in passed:
            self.make_judge_verdict(
                unit,
                back_translation=(
                    "<b>x</b><script>y</script>" if unit == passed[0] else "Pass two"
                ),
            )
        self.make_recommendation(
            self.make_recommendation_run(),
            ready_group,
            ready,
            target=["Recommandé"],
            action="use_existing",
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            flagged_group,
            flagged,
            target=["<b>x</b><script>y</script>"],
        )

        response = self.client.get(self.queue_url)

        self.assertContains(
            response, '<span class="badge text-bg-success">Recommended</span>'
        )
        self.assertContains(response, "The checked translation")
        self.assertContains(
            response,
            "Back-translation: &lt;b&gt;x&lt;/b&gt;&lt;script&gt;y&lt;/script&gt;",
        )
        # A passed variant carries no badge of its own; only the
        # recommendation and a found error are marked.
        self.assertNotContains(response, "Checked by the judge")
        # A back-translation that only echoes its variant is not shown.
        self.assertNotContains(response, "Back-translation: Pass two")
        self.assertContains(response, "The judge found an error")
        self.assertContains(response, "&lt;color=#FF0000&gt;")
        self.assertNotContains(response, "<script>y</script>")

        rendered_groups = {
            item["group"].pk: item for item in response.context["groups"]
        }
        ready_item = rendered_groups[ready_group.pk]
        self.assertEqual(ready_item["variants"][0]["target"], ("Recommandé",))
        self.assertTrue(ready_item["preselect"])
        ready_card = (
            response.content.decode()
            .split(f'id="g-{ready_group.pk}"', 1)[1]
            .split("</li>", 1)[0]
        )
        self.assertRegex(
            ready_card,
            r'name="target"\s+value="Recommandé"\s+data-choice="variant"\s+checked',
        )
        self.assertRegex(ready_card, r'aria-disabled="false"')
        self.assertIn("Recommended: Recommandé", ready_card)
        self.assertNotRegex(
            ready_card,
            r'name="target"\s+value="À éviter"\s+data-choice="variant"\s+checked',
        )
        flagged_item = rendered_groups[flagged_group.pk]
        self.assertFalse(flagged_item["preselect"])
        flagged_card = (
            response.content.decode()
            .split(f'id="g-{flagged_group.pk}"', 1)[1]
            .split("</li>", 1)[0]
        )
        # A proposal equal to a flagged variant is never preselected (D17).
        self.assertNotRegex(flagged_card, r'(?s)<input[^>]*\schecked(?:="checked")?')
        self.assertNotIn("Recommended: ", flagged_card)
        # Nothing checked yet: the most-used variant is checked, but only a
        # variant the judge passed is marked as recommended.
        unchecked_item = rendered_groups[unchecked_group.pk]
        self.assertTrue(unchecked_item["preselect"])
        unchecked_card = self.card(response, unchecked_group)
        self.assertRegex(
            unchecked_card,
            r'name="target"\s+value="One"\s+data-choice="variant"\s+checked',
        )
        self.assertNotIn("Recommended", unchecked_card)
        self.assertIn("Different translations", unchecked_card)

    def test_queue_plural_ready_group_is_not_preselected(self) -> None:
        source = join_plural(["Gate", "Gates"])
        units = []
        for position, forms in enumerate(
            (["Brána", "Brány", "Bran"], ["Vrata", "Vrat", "Vrat"]), start=27300
        ):
            context = f"plural-gate-{position}"
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
                    target=join_plural(forms),
                    state=STATE_TRANSLATED,
                )
            )
        group = get_or_create_group(self.policy, units[0])
        self.make_judge_verdict(units[0])
        self.make_judge_verdict(units[1], JudgeVerdict.Severity.MAJOR)

        response = self.client.get(self.queue_url)

        item = next(
            item for item in response.context["groups"] if item["group"] == group
        )
        self.assertEqual(item["judge"].bucket, "ready")
        self.assertEqual(
            item["judge"].recommended, tuple(units[0].get_target_plurals())
        )
        self.assertGreater(len(item["judge"].recommended), 1)
        self.assertFalse(item["preselect"])
        card = (
            response.content.decode()
            .split(f'id="g-{group.pk}"', 1)[1]
            .split("</li>", 1)[0]
        )
        self.assertNotRegex(card, r'(?s)<input[^>]*\schecked(?:="checked")?')
        self.assertNotIn("Recommended: ", card)

    def test_queue_judge_launch_url_and_no_paid_recommendation_link(self) -> None:
        self.make_group("Judge start", ["One", "Two"], start=26000)
        project_language = self.project.project_languages[self.translation.language]
        user_class = type(self.user)
        original_has_perm = user_class.has_perm

        def has_perm(user, perm, obj=None):
            return perm in {"translation.auto", "unit.review"} or original_has_perm(
                user, perm, obj
            )

        with (
            patch.object(user_class, "has_perm", autospec=True, side_effect=has_perm),
            patch(
                "weblate.trans.views.repeats.judge_configuration_ready",
                return_value=True,
            ),
        ):
            response = self.client.get(self.queue_url)

        self.assertEqual(response.context["judge_panel"]["state"], "start")
        self.assertTrue(response.context["judge_panel"]["can_launch"])
        self.assertEqual(
            response.context["judge_panel"]["launch_url"],
            f"{project_language.get_absolute_url()}?mode=judge&q=check%3Arepeat-drift"
            "&judge_proposal_only=1&overwrite_existing="
            f"&next=%2Frepeats%2F{self.project.slug}%2Fcs%2F#auto",
        )
        self.assertContains(
            response, escape(response.context["judge_panel"]["launch_url"])
        )
        self.assertNotContains(
            response,
            reverse(
                "repeat-recommend",
                kwargs={"project": self.project.slug, "language": "cs"},
            ),
        )

    def test_queue_judge_running_uses_coverage_not_recorded(self) -> None:
        self.make_group("Judge progress", ["One", "Two"], start=26100)
        run = self.make_judge_run(execution_version=1)
        user_class = type(self.user)
        original_has_perm = user_class.has_perm

        def has_perm(user, perm, obj=None):
            return perm in {"translation.auto", "unit.review"} or original_has_perm(
                user, perm, obj
            )

        with (
            override_settings(JUDGE_ENABLED=True),
            patch.object(user_class, "has_perm", autospec=True, side_effect=has_perm),
            patch.object(
                ProducerRun,
                "get_coverage",
                return_value={"total": 1022, "pending": 602},
            ),
        ):
            response = self.client.get(self.queue_url)

        self.assertEqual(response.context["judge_panel"]["state"], "running")
        self.assertContains(response, "Checked 420 of 1022 places")
        self.assertContains(response, 'aria-valuenow="420"')
        self.assertContains(response, 'aria-valuemin="0"')
        self.assertContains(response, 'aria-valuemax="1022"')
        self.assertContains(response, 'aria-label="Judge check progress"')
        self.assertContains(response, "width: 41%")
        self.assertContains(
            response,
            "You can leave this page: the check runs in the background "
            "and results appear here.",
        )
        self.assertTrue(response.context["judge_panel"]["can_view_run"])
        self.assertContains(response, run.get_absolute_url())

        with patch.object(
            ProducerRun, "get_coverage", return_value={"total": 1, "pending": 1}
        ):
            response = self.client.get(self.queue_url)
        self.assertContains(response, "Checked 0 of 1 place.")

    def test_queue_hides_running_report_without_report_permission(self) -> None:
        self.make_group("Hidden report", ["One", "Two"], start=26110)
        run = self.make_judge_run(execution_version=1)
        user_class = type(self.user)
        original_has_perm = user_class.has_perm
        for denied in ("translation.auto", "unit.review"):
            with self.subTest(denied=denied):

                def has_perm(user, perm, obj=None, *, denied_permission=denied):
                    return perm != denied_permission and original_has_perm(
                        user, perm, obj
                    )

                with (
                    override_settings(JUDGE_ENABLED=True),
                    patch.object(
                        user_class, "has_perm", autospec=True, side_effect=has_perm
                    ),
                ):
                    response = self.client.get(self.queue_url)
                self.assertEqual(response.context["judge_panel"]["state"], "running")
                self.assertFalse(response.context["judge_panel"]["can_view_run"])
                self.assertNotContains(response, run.get_absolute_url())
        with override_settings(JUDGE_ENABLED=False):
            response = self.client.get(self.queue_url)
        self.assertFalse(response.context["judge_panel"]["can_view_run"])
        self.assertNotContains(response, run.get_absolute_url())

    def test_queue_shows_report_when_launch_configuration_unavailable(self) -> None:
        self.make_group("Report without launch", ["One", "Two"], start=26120)
        run = self.make_judge_run(execution_version=1)
        user_class = type(self.user)
        original_has_perm = user_class.has_perm

        def has_perm(user, perm, obj=None):
            return perm in {"translation.auto", "unit.review"} or original_has_perm(
                user, perm, obj
            )

        with (
            override_settings(JUDGE_ENABLED=True),
            patch.object(user_class, "has_perm", autospec=True, side_effect=has_perm),
            patch(
                "weblate.trans.views.repeats.judge_configuration_ready",
                return_value=False,
            ),
        ):
            response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["state"], "running")
        self.assertFalse(response.context["judge_panel"]["can_launch"])
        self.assertTrue(response.context["judge_panel"]["can_view_run"])
        self.assertContains(response, run.get_absolute_url())

    def test_queue_reserved_pending_rows_are_not_counted_as_checked(self) -> None:
        _, units = self.make_group(
            "Reserved places", ["One", "Two", "Three"], start=26150
        )
        run = self.make_judge_run(
            scope_snapshot=[unit.pk for unit in units], execution_version=1
        )
        for unit in units:
            JudgeRunUnit.objects.create(
                run=run,
                unit=unit,
                unit_id_snapshot=unit.pk,
                translation_id=unit.translation_id,
                component_id=self.component.pk,
                project_id=self.project.pk,
                input_target=unit.get_target_plurals(),
                input_target_hash=compute_target_hash(unit.get_target_plurals()),
                context_hash="repeat-context",
                outcome=JudgeRunUnit.Outcome.PENDING,
            )

        response = self.client.get(self.queue_url)
        self.assertContains(response, "Checked 0 of 3 places")
        self.assertEqual(response.context["judge_panel"]["checked"], 0)

    def test_queue_ignores_non_proposal_run_and_reports_stopped_run(self) -> None:
        self.make_group("Judge status", ["One", "Two"], start=26200)
        self.make_judge_run(execution_options={"judge_proposal_only": False})
        response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["state"], "start")
        run = self.make_judge_run(status=ProducerRun.Status.FAILED)
        response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["run"], run)
        self.assertEqual(response.context["judge_panel"]["state"], "start")
        self.assertContains(response, "The last check stopped before it finished.")

    def test_queue_stopped_run_with_verdicts_is_ready(self) -> None:
        _, units = self.make_group("Stopped with verdicts", ["One", "Two"], start=26250)
        self.make_judge_verdict(units[0])
        self.make_judge_verdict(units[1], JudgeVerdict.Severity.MAJOR)
        for status in (
            ProducerRun.Status.FAILED,
            ProducerRun.Status.CANCELLED,
            ProducerRun.Status.PARTIAL,
        ):
            with self.subTest(status=status):
                self.make_judge_run(status=status)
                response = self.client.get(self.queue_url)
                self.assertEqual(response.context["judge_panel"]["state"], "ready")
                self.assertTrue(response.context["judge_panel"]["stopped"])
                self.assertContains(
                    response, "The last check stopped before it finished."
                )

    def test_queue_latest_run_requires_exact_scope_path_id_and_query(self) -> None:
        self.make_group("Exact run", ["One", "Two"], start=26280)
        matching = self.make_judge_run(status=ProducerRun.Status.COMPLETED)
        self.make_judge_run(scope_id="other")
        self.make_judge_run(scope_path="/other/language/")
        self.make_judge_run(requested_query="check:other")

        response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["run"], matching)
        self.assertEqual(response.context["judge_panel"]["state"], "start")

    def test_queue_judge_filter_ignores_unknown_value(self) -> None:
        self.make_group("Judge filter", ["One", "Two"], start=26300)
        response = self.client.get(self.queue_url + "?status=open&judge=unchecked")
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["judge_panel"]["buckets"]["unchecked"], 1)
        response = self.client.get(self.queue_url + "?status=open&judge=unknown")
        self.assertEqual(response.context["total_count"], 1)

    def test_queue_judge_buckets_and_ready_filter(self) -> None:
        _, ready = self.make_group("Ready bucket", ["R1", "R2"], start=26400)
        _, choose = self.make_group("Choose bucket", ["C1", "C2"], start=26500)
        _, rewrite = self.make_group("Rewrite bucket", ["W1", "W2"], start=26600)
        self.make_group("Unchecked bucket", ["U1", "U2"], start=26700)
        self.make_judge_verdict(ready[0])
        self.make_judge_verdict(ready[1], JudgeVerdict.Severity.MAJOR)
        for unit in choose:
            self.make_judge_verdict(unit)
        for unit in rewrite:
            self.make_judge_verdict(unit, JudgeVerdict.Severity.MAJOR)

        response = self.client.get(self.queue_url)
        self.assertEqual(
            response.context["judge_panel"]["buckets"],
            {"ready": 1, "choose": 1, "rewrite": 1, "unchecked": 1},
        )
        self.assertEqual(response.context["judge_panel"]["state"], "ready")
        self.assertNotContains(response, "judge-bulk-review")
        response = self.client.get(self.queue_url + "?status=open&judge=ready")
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(
            response.context["groups"][0]["group"].source_forms, ["Ready bucket"]
        )

    def test_queue_ready_state_shows_tiles_instead_of_launch(self) -> None:
        _, ready = self.make_group("Tile ready", ["R1", "R2"], start=26410)
        _, choose = self.make_group("Tile choose", ["C1", "C2"], start=26510)
        self.make_judge_verdict(ready[0])
        self.make_judge_verdict(ready[1], JudgeVerdict.Severity.MAJOR)
        for unit in choose:
            self.make_judge_verdict(unit)
        user_class = type(self.user)
        original_has_perm = user_class.has_perm

        def has_perm(user, perm, obj=None):
            return perm in {"translation.auto", "unit.review"} or original_has_perm(
                user, perm, obj
            )

        with (
            patch.object(user_class, "has_perm", autospec=True, side_effect=has_perm),
            patch(
                "weblate.trans.views.repeats.judge_configuration_ready",
                return_value=True,
            ),
        ):
            response = self.client.get(self.queue_url + "?status=open&judge=choose")

        panel = response.context["judge_panel"]
        self.assertEqual(panel["state"], "ready")
        self.assertTrue(panel["can_launch"])
        content = response.content.decode()
        self.assertRegex(
            content,
            r'(?s)judge=ready"\s*>\s*<span class="d-block fs-3 fw-semibold">1</span>'
            r"\s*group: a recommended variant is ready</a>",
        )
        self.assertRegex(
            content,
            r'(?s)judge=choose"\s+aria-current="page">'
            r'<span class="d-block fs-3 fw-semibold">1</span>'
            r"\s*group: no single safe variant, your choice</a>",
        )
        self.assertContains(
            response, "groups: errors in every variant, a new translation is needed"
        )
        self.assertContains(response, "groups the judge could not check")
        self.assertNotContains(response, "Check variants with the judge</a>")
        self.assertNotContains(response, "The repeat check covers")
        self.assertNotContains(response, "the cost estimate appears")
        self.assertNotContains(response, "Review and apply at once")

    def test_queue_ready_tile_offers_bulk_review(self) -> None:
        group, units = self.make_group("Tile review", ["R1", "R2"], start=26420)
        self.make_judge_verdict(units[0])
        self.make_judge_verdict(units[1], JudgeVerdict.Severity.MAJOR)
        self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            action="use_existing",
            target=["R1"],
        )

        response = self.client.get(self.queue_url)

        self.assertEqual(response.context["bulk_ready"], 1)
        self.assertContains(
            response,
            f'<a class="btn btn-primary btn-sm d-block mt-2" '
            f'href="{response.context["bulk_review_url"]}">'
            "Review and apply at once</a>",
            html=True,
        )

    def test_queue_unchecked_places_inside_check_offer_relaunch(self) -> None:
        _, units = self.make_group("Remaining places", ["One", "Two"], start=26800)
        _, ready_units = self.make_group("Ready places", ["Yes", "No"], start=26850)
        self.project.check_flags = "repeat-drift"
        self.project.save(update_fields=["check_flags"])
        CHECKS["repeat-drift"].perform_batch(self.component)
        self.make_judge_verdict(units[0])
        self.make_judge_verdict(ready_units[0])
        self.make_judge_verdict(ready_units[1], JudgeVerdict.Severity.MAJOR)

        user_class = type(self.user)
        original_has_perm = user_class.has_perm

        def has_perm(user, perm, obj=None):
            return perm in {"translation.auto", "unit.review"} or original_has_perm(
                user, perm, obj
            )

        with (
            patch.object(user_class, "has_perm", autospec=True, side_effect=has_perm),
            patch(
                "weblate.trans.views.repeats.judge_configuration_ready",
                return_value=True,
            ),
        ):
            response = self.client.get(self.queue_url)

        panel = response.context["judge_panel"]
        self.assertEqual(panel["state"], "ready")
        self.assertTrue(panel["can_launch"])
        self.assertEqual(panel["buckets"]["ready"], 1)
        self.assertEqual(panel["buckets"]["unchecked"], 1)
        self.assertEqual(panel["relaunch_places"], 1)
        self.assertEqual(panel["outside_places"], 0)
        self.assertContains(response, "Check the remaining places")
        self.assertContains(response, escape(panel["launch_url"]), count=1)

    def test_queue_ignored_place_is_outside_check_and_counts_differ(self) -> None:
        _, units = self.make_group(
            "Excluded place", ["One", "Two", "Three"], start=26900
        )
        units[2].extra_flags = "ignore-repeat-drift"
        units[2].save(update_fields=["extra_flags"])
        self.project.check_flags = "repeat-drift"
        self.project.save(update_fields=["check_flags"])
        CHECKS["repeat-drift"].perform_batch(self.component)
        self.make_judge_verdict(units[0])
        self.make_judge_verdict(units[1], JudgeVerdict.Severity.MAJOR)

        response = self.client.get(self.queue_url)
        panel = response.context["judge_panel"]
        self.assertEqual(panel["places"], 2)
        self.assertEqual(panel["queue_places"], 3)
        self.assertEqual(panel["relaunch_places"], 0)
        self.assertEqual(panel["outside_places"], 1)
        self.assertContains(response, "The open queue contains 3 places.")
        self.assertContains(
            response,
            "1 place is outside the repeat check; the judge does not check it.",
        )
        self.assertNotContains(response, "Check the remaining places")

    def test_queue_launch_requires_both_permissions_and_judge_configuration(
        self,
    ) -> None:
        self.make_group("Launch permissions", ["One", "Two"], start=27000)
        user_class = type(self.user)
        original_has_perm = user_class.has_perm
        for denied in ("unit.review", "translation.auto"):
            with self.subTest(denied=denied):

                def has_perm(user, perm, obj=None, *, denied_permission=denied):
                    return perm != denied_permission and original_has_perm(
                        user, perm, obj
                    )

                with (
                    patch.object(
                        user_class, "has_perm", autospec=True, side_effect=has_perm
                    ),
                    patch(
                        "weblate.trans.views.repeats.judge_configuration_ready",
                        return_value=True,
                    ),
                ):
                    response = self.client.get(self.queue_url)
                self.assertFalse(response.context["judge_panel"]["can_launch"])
                self.assertNotContains(response, "Check variants with the judge</a>")
        with patch(
            "weblate.trans.views.repeats.judge_configuration_ready", return_value=False
        ):
            response = self.client.get(self.queue_url)
        self.assertFalse(response.context["judge_panel"]["can_launch"])
        self.assertNotContains(response, "Check variants with the judge</a>")

    @staticmethod
    def card(response, group) -> str:
        return (
            response.content.decode()
            .split(f'id="g-{group.pk}"', 1)[1]
            .split("</li>", 1)[0]
        )

    def judged_group(self, source: str, severities: list[str], start: int):
        group, units = self.make_group(
            source, [f"{source} {index}" for index in range(len(severities))], start
        )
        for unit, severity in zip(units, severities, strict=True):
            self.make_judge_verdict(unit, severity)
        return group, units

    def test_queue_model_comparison_settles_judge_buckets(self) -> None:
        none = JudgeVerdict.Severity.NONE
        major = JudgeVerdict.Severity.MAJOR
        picked, picked_units = self.judged_group("Model picked", [none, none], 28000)
        keep, keep_units = self.judged_group("Model keeps", [none, none], 28010)
        human, human_units = self.judged_group("Model unsure", [none, none], 28020)
        disagree, disagree_units = self.judged_group(
            "Model disagrees", [none, major], 28030
        )
        results: tuple[tuple[object, list, str, list[str], str], ...] = (
            (picked, picked_units, "use_existing", ["Model picked 1"], "Picked why"),
            (keep, keep_units, "keep_independent", [], "Keep why"),
            (human, human_units, "needs_human", [], "Human why"),
            (
                disagree,
                disagree_units,
                "use_existing",
                ["Model disagrees 1"],
                "Disagree why",
            ),
        )
        for group, units, action, target, rationale in results:
            self.make_recommendation(
                self.make_recommendation_run(),
                group,
                units,
                action=action,
                target=target,
                rationale=rationale,
            )

        response = self.client.get(self.queue_url)

        self.assertEqual(
            response.context["judge_panel"]["buckets"],
            {"ready": 2, "choose": 2, "rewrite": 0, "unchecked": 0},
        )
        picked_card = self.card(response, picked)
        self.assertRegex(
            picked_card,
            r'name="target"\s+value="Model picked 1"\s+data-choice="variant"\s+checked',
        )
        # A preselected pick carries one badge; the rationale explains it.
        self.assertNotIn("Model recommendation", picked_card)
        self.assertIn(">Recommended</span>", picked_card)
        self.assertIn("Model rationale: Picked why", picked_card)
        # The model's pick is its own variant row, not a second radio.
        self.assertEqual(picked_card.count('value="Model picked 1"'), 1)
        keep_card = self.card(response, keep)
        self.assertRegex(keep_card, r'value="keep"\s+data-choice="keep"\s+checked')
        self.assertIn("Model rationale: Keep why", keep_card)
        self.assertIn('aria-disabled="false"', keep_card)
        # Two passed variants and an unsure model: the first of the equally
        # used passed variants is recommended, outside the ready bucket (D17).
        human_card = self.card(response, human)
        self.assertRegex(
            human_card,
            r'name="target"\s+value="Model unsure 0"\s+data-choice="variant"\s+checked',
        )
        self.assertIn("Model rationale: Human why", human_card)
        self.assertIn("Recommended: Model unsure 0", human_card)
        # The judge passed only one variant: it beats the model's flagged pick.
        disagree_card = self.card(response, disagree)
        self.assertRegex(
            disagree_card,
            r'name="target"\s+value="Model disagrees 0"\s+data-choice="variant"\s+checked',
        )
        self.assertIn("Recommended: Model disagrees 0", disagree_card)
        self.assertEqual(disagree_card.count(" checked"), 1)
        # The model's pick of the flagged variant shows the judge's error once
        # and the model's rationale beside it.
        self.assertIn("The judge found an error", disagree_card)
        self.assertIn("Model rationale: Disagree why", disagree_card)
        self.assertNotIn("Model recommendation", disagree_card)
        for group in (picked, keep, human, disagree):
            with self.subTest(header=group.source_forms[0]):
                header = self.card(response, group).split("</button>", 1)[0]
                # The toggle grid holds one badge column.
                self.assertEqual(header.count('class="badge'), 1)

    def test_queue_plural_model_results_preselect_nothing(self) -> None:
        source = join_plural(["Gate", "Gates"])
        targets = [
            join_plural(["Brána", "Brány", "Bran"]),
            join_plural(["Vrata", "Vrat", "Vrat"]),
        ]
        picked, picked_units = self.make_group(source, targets, start=28100)
        for unit in picked_units:
            self.make_judge_verdict(unit)
        keep_source = join_plural(["Door", "Doors"])
        keep, keep_units = self.make_group(
            keep_source,
            [
                join_plural(["Dveře", "Dveří", "Dveří"]),
                join_plural(["Vrátka", "Vrátek", "Vrátek"]),
            ],
            start=28110,
        )
        for unit in keep_units:
            self.make_judge_verdict(unit)
        self.make_recommendation(
            self.make_recommendation_run(),
            picked,
            picked_units,
            action="use_existing",
            target=["Vrata", "Vrat", "Vrat"],
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            keep,
            keep_units,
            action="keep_independent",
            target=[],
        )

        response = self.client.get(self.queue_url)

        items = {item["group"].pk: item for item in response.context["groups"]}
        self.assertEqual(items[picked.pk]["judge"].bucket, "ready")
        self.assertEqual(
            items[picked.pk]["judge"].recommended, ("Vrata", "Vrat", "Vrat")
        )
        self.assertFalse(items[picked.pk]["preselect"])
        self.assertEqual(items[keep.pk]["judge"].bucket, "choose")
        self.assertFalse(items[keep.pk]["preselect_keep"])
        for group in (picked, keep):
            self.assertNotRegex(self.card(response, group), r"(?s)<input[^>]*\schecked")

    def test_queue_card_preselects_the_judge_variant_over_keep_independent(
        self,
    ) -> None:
        # Основание / fr: only "Base" passed; the model said the places differ.
        group, units = self.make_group(
            "Основание", ["Armature", "Base", "Monture"], start=28150
        )
        for unit in units:
            self.make_judge_verdict(
                unit,
                JudgeVerdict.Severity.NONE
                if unit.target == "Base"
                else JudgeVerdict.Severity.MAJOR,
            )
        self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            action="keep_independent",
            target=[],
            rationale="Different contexts suggest different referents.",
        )

        response = self.client.get(self.queue_url)

        item = next(
            item for item in response.context["groups"] if item["group"] == group
        )
        self.assertEqual(item["judge"].bucket, "ready")
        self.assertFalse(item["preselect_keep"])
        card = self.card(response, group)
        self.assertRegex(
            card, r'name="target"\s+value="Base"\s+data-choice="variant"\s+checked'
        )
        self.assertEqual(card.count(" checked"), 1)
        self.assertIn("Recommended: Base", card)
        self.assertIn(
            "Model rationale: Different contexts suggest different referents.", card
        )
        self.assertEqual(response.context["bulk_ready"], 1)

    def test_queue_card_prefills_the_model_text_when_every_variant_is_flagged(
        self,
    ) -> None:
        major = JudgeVerdict.Severity.MAJOR
        group, units = self.judged_group("All wrong", [major, major], 28160)
        self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            target=["Socle"],
            rationale="Neither variant names a base.",
        )

        response = self.client.get(self.queue_url)

        card = self.card(response, group)
        self.assertRegex(card, r'value="custom"\s+data-choice="custom"\s+checked')
        self.assertRegex(card, r'name="custom_target"[^>]*value="Socle"')
        self.assertNotRegex(card, r'name="custom_target"[^>]*hidden')
        self.assertIn('aria-disabled="false"', card)
        self.assertIn("Model rationale: Neither variant names a base.", card)
        # The model's text lives in the custom field, not in a second radio.
        self.assertNotIn("Model recommendation", card)
        self.assertEqual(card.count("Socle"), 1)
        self.assertEqual(response.context["bulk_ready"], 0)

    def test_review_places_follow_the_judge_override(self) -> None:
        none = JudgeVerdict.Severity.NONE
        major = JudgeVerdict.Severity.MAJOR
        group, units = self.judged_group("Override places", [none, major], 28170)
        result = self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            action="keep_independent",
            target=[],
        )

        places = self.client.get(self.places_url(result))

        self.assertEqual(places.status_code, 200)
        self.assertEqual(places.context["target_text"], "Override places 0")
        changing = [
            member for member in places.context["members"] if member["will_change"]
        ]
        self.assertEqual([member["unit_id"] for member in changing], [units[1].pk])

    def test_queue_card_checks_the_approved_variant_without_a_mark(self) -> None:
        group, units = self.make_group(
            "Approved wording", ["Common", "Common", "Approved"], start=28180
        )
        units[2].state = STATE_APPROVED
        units[2].save(update_fields=["state"])

        response = self.client.get(self.queue_url)

        card = self.card(response, group)
        # Human approval outranks the majority when the judge has nothing.
        self.assertRegex(
            card, r'name="target"\s+value="Approved"\s+data-choice="variant"\s+checked'
        )
        self.assertEqual(card.count(" checked"), 1)
        self.assertNotIn("Recommended", card)
        self.assertEqual(response.context["bulk_ready"], 0)

    def test_queue_ready_bucket_equals_bulk_banner(self) -> None:
        none = JudgeVerdict.Severity.NONE
        major = JudgeVerdict.Severity.MAJOR
        judge_ready, judge_units = self.judged_group(
            "Judge ready", [none, major], 28200
        )
        model_ready, model_units = self.judged_group("Model ready", [none, none], 28210)
        self.judged_group("Needs rewrite", [major, major], 28220)
        self.make_recommendation(
            self.make_recommendation_run(),
            judge_ready,
            judge_units,
            action="use_existing",
            target=["Judge ready 0"],
        )
        self.make_recommendation(
            self.make_recommendation_run(),
            model_ready,
            model_units,
            action="use_existing",
            target=["Model ready 0"],
        )
        # The judge passed only one variant: whatever the model said, the
        # group stays ready with that variant, in the tile and the banner (D17).
        for source, start, action, target in (
            ("Model picks flagged", 28230, "use_existing", ["Model picks flagged 1"]),
            ("Model keeps apart", 28240, "keep_independent", []),
        ):
            group, units = self.judged_group(source, [none, major, major], start)
            self.make_recommendation(
                self.make_recommendation_run(),
                group,
                units,
                action=action,
                target=target,
            )
        # Two passed variants and an unsure model: preselected on the card,
        # but neither ready nor a review row.
        unsure, unsure_units = self.judged_group("Model unsure", [none, none], 28250)
        self.make_recommendation(
            self.make_recommendation_run(),
            unsure,
            unsure_units,
            action="needs_human",
            target=[],
        )
        # The model picks one of two passed variants, but a third variant is
        # unchecked: not ready until every variant is checked (D9).
        partial, partial_units = self.make_group(
            "Partly checked", ["Partly 0", "Partly 1", "Partly 2"], start=28270
        )
        for unit in partial_units[:2]:
            self.make_judge_verdict(unit)
        partial_pick = self.make_recommendation(
            self.make_recommendation_run(),
            partial,
            partial_units,
            action="use_existing",
            target=["Partly 1"],
        )
        # Every variant flagged, a new text proposed: an attention row only.
        rewrite, rewrite_units = self.judged_group(
            "Model rewrites", [major, major], 28260
        )
        proposal = self.make_recommendation(
            self.make_recommendation_run(),
            rewrite,
            rewrite_units,
            target=["Fresh wording"],
        )

        response = self.client.get(self.queue_url)

        self.assertEqual(response.context["judge_panel"]["buckets"]["ready"], 4)
        self.assertEqual(response.context["bulk_ready"], 4)
        review = self.client.get(self.review_url)
        self.assertContains(review, 'name="result"', count=6)
        content = " ".join(review.content.decode().split())
        self.assertEqual(content.count('" checked'), 4)
        self.assert_unchecked(review, proposal.pk)
        self.assert_unchecked(review, partial_pick.pk)
        self.assertIn("The judge has not checked every variant of this group.", content)

    def ready_recommendation(self, source: str, start: int):
        """One queue-ready group: the model picks the only passed variant."""
        group, units = self.judged_group(
            source, [JudgeVerdict.Severity.NONE, JudgeVerdict.Severity.MAJOR], start
        )
        return self.make_recommendation(
            self.make_recommendation_run(),
            group,
            units,
            action="use_existing",
            target=[f"{source} 0"],
        )

    def test_review_selects_exactly_the_queue_ready_groups(self) -> None:
        none = JudgeVerdict.Severity.NONE
        major = JudgeVerdict.Severity.MAJOR
        judge_ready = self.ready_recommendation("Judge ready", 29000)
        model_group, model_units = self.judged_group("Model ready", [none, none], 29010)
        flagged_group, flagged_units = self.judged_group(
            "Flagged pick", [none, none, major], 29020
        )
        approved_group, approved_units = self.judged_group(
            "Approved place", [none, none], 29030
        )
        approved_units[1].state = STATE_APPROVED
        approved_units[1].save(update_fields=["state"])
        unchecked_group, unchecked_units = self.make_group(
            "Unchecked pick", ["U1", "U2"], start=29040
        )
        new_group, new_units = self.judged_group("New wording", [major, major], 29050)
        run = self.make_recommendation_run()
        model_ready = self.make_recommendation(
            run,
            model_group,
            model_units,
            action="use_existing",
            target=["Model ready 1"],
        )
        # The judge passed only one variant; the model's pick or new text is
        # replaced by it, so these rows are ready too (D17).
        overrides = [
            self.make_recommendation(
                run,
                *self.judged_group(source, [none, major], start),
                action=action,
                target=target,
            )
            for source, start, action, target in (
                ("Override pick", 29060, "use_existing", ["Override pick 1"]),
                ("Override wording", 29070, "propose_new", ["Other wording"]),
            )
        ]
        attention = {
            "The judge flagged the variant the model picked.": self.make_recommendation(
                run,
                flagged_group,
                flagged_units,
                action="use_existing",
                target=["Flagged pick 2"],
            ),
            "Some places are approved, so the choice stays with you.": (
                self.make_recommendation(
                    run,
                    approved_group,
                    approved_units,
                    action="use_existing",
                    target=["Approved place 0"],
                )
            ),
            "The judge has not checked every variant of this group.": (
                self.make_recommendation(
                    run,
                    unchecked_group,
                    unchecked_units,
                    action="use_existing",
                    target=["U1"],
                )
            ),
            "The model proposes a new translation that the judge has not checked.": (
                self.make_recommendation(
                    run, new_group, new_units, target=["Fresh wording"]
                )
            ),
        }

        queue = self.client.get(self.queue_url)
        review = self.client.get(self.review_url)

        ready_count = queue.context["judge_panel"]["buckets"]["ready"]
        self.assertEqual(ready_count, 4)
        content = " ".join(review.content.decode().split())
        self.assertEqual(content.count('name="result"'), 8)
        self.assertEqual(content.count('" checked'), ready_count)
        self.assert_checked(review, judge_ready.pk)
        self.assert_checked(review, model_ready.pk)
        for result in overrides:
            self.assert_checked(review, result.pk)
        self.assertEqual(
            content.count("<code>Override pick 0</code>")
            + content.count("<code>Override wording 0</code>"),
            2,
        )
        self.assertNotIn("<code>Other wording</code>", content)
        self.assertEqual(content.count("The judge passed only this variant."), 2)
        self.assertIn(
            "The judge passed only this variant. The model advised: "
            "The key names this exact meaning.",
            content,
        )
        attention_html = content[content.index('id="bulk-attention"') :]
        self.assertIn('<details class="rq-attention rq-collapsed">', attention_html)
        for reason, result in attention.items():
            with self.subTest(reason=reason):
                self.assert_unchecked(review, result.pk)
                self.assertIn(f'value="{result.pk}"', attention_html)
                self.assertIn(reason, attention_html)
        self.assertIn("4 groups selected, 4 places will change.", content)

    def test_review_first_screen_leads_with_apply_and_summary(self) -> None:
        self.ready_recommendation("First screen", 29100)

        response = self.client.get(self.review_url)

        content = " ".join(response.content.decode().split())
        button = content.index(
            '<button class="btn btn-primary" type="submit">'
            "Apply the selected decisions</button>"
        )
        self.assertEqual(content.count("Apply the selected decisions"), 1)
        self.assertLess(button, content.index('name="result"'))
        self.assertIn(
            '<p class="rq-bulk-summary" id="bulk-summary" aria-live="polite"> '
            "1 group selected, 1 place will change. </p>",
            content,
        )
        self.assertIn('data-select="ready">Select all ready</button>', content)
        self.assertIn('data-select="none">Clear all</button>', content)

    def test_review_pages_stay_in_one_form_and_apply_only_selected(self) -> None:
        results = [
            self.ready_recommendation(f"Paged {index}", 29200 + index * 10)
            for index in range(3)
        ]

        with patch("weblate.trans.views.repeats.REVIEW_PAGE_SIZE", 2):
            response = self.client.get(self.review_url)

        content = response.content.decode()
        form = content[content.index('class="rq-bulk"') : content.index("</form>")]
        # Pages only hide rows, so every row stays a field of the one form.
        self.assertEqual(form.count('name="result"'), 3)
        self.assertIn('data-page-size="2"', form)
        self.assertIn('class="rq-bulk-pager"', form)

        response = self.client.post(
            self.review_url,
            {
                "action": "apply",
                "manifest": response.context["manifest"],
                "result": [str(results[0].pk), str(results[2].pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        run = RepeatBulkRun.objects.get()
        self.assertEqual(
            list(run.items.order_by("ordinal").values_list("result_id", flat=True)),
            [results[0].pk, results[2].pk],
        )

    def test_review_get_queries_do_not_grow_per_row(self) -> None:
        self.ready_recommendation("Cost row 0", 29300)
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(self.review_url)
        for index in range(1, 6):
            self.ready_recommendation(f"Cost row {index}", 29300 + index * 10)
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.review_url)

        self.assertContains(response, 'name="result"', count=6)
        # A per-row preview or place lookup would add queries for every row.
        self.assertLessEqual(
            len(expanded.captured_queries) - len(baseline.captured_queries), 2
        )

    def test_queue_shows_comparing_variants_while_a_run_is_active(self) -> None:
        self.make_group("Comparing", ["One", "Two"], start=28300)
        snapshot = {"groups": [{"group": 1, "sendable": True}, {"group": 2}]}
        oversized = {"groups": [{"group": 3, "sendable": False}]}
        runs = {
            RepeatRecommendationRun.Status.RUNNING: snapshot,
            RepeatRecommendationRun.Status.QUEUED: oversized,
            RepeatRecommendationRun.Status.COMPLETED: snapshot,
        }
        for status, frozen in runs.items():
            run = self.make_recommendation_run(status=status)
            RepeatRecommendationRun.objects.filter(pk=run.pk).update(snapshot=frozen)
        active = RepeatRecommendationRun.objects.get(
            status=RepeatRecommendationRun.Status.RUNNING
        )

        response = self.client.get(self.queue_url)

        self.assertEqual(response.context["judge_panel"]["comparing"], 2)
        self.assertContains(response, "Comparing variants: 2 groups")

        RepeatRecommendationRun.objects.filter(pk=active.pk).update(
            status=RepeatRecommendationRun.Status.COMPLETED
        )
        response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["comparing"], 0)
        self.assertNotContains(response, "Comparing variants")

    def test_queue_model_results_do_not_grow_queries_per_group(self) -> None:
        def compared_group(source: str, start: int) -> None:
            group, units = self.judged_group(
                source, [JudgeVerdict.Severity.NONE] * 2, start
            )
            self.make_recommendation(
                self.make_recommendation_run(),
                group,
                units,
                action="use_existing",
                target=[f"{source} 0"],
            )

        def result_queries(capture) -> int:
            return sum(
                "trans_repeatrecommendationresult" in query["sql"]
                for query in capture.captured_queries
            )

        def measure() -> tuple[int, int, int]:
            # The engine's own currency check reads each group's live context;
            # the queue must add nothing per group on top of it.
            with CaptureQueriesContext(connection) as engine:
                current_recommendations(self.policy, actor=self.user)
            with CaptureQueriesContext(connection) as page:
                response = self.client.get(self.queue_url)
            self.assertEqual(response.status_code, 200)
            return (
                len(page.captured_queries),
                len(engine.captured_queries),
                result_queries(page),
            )

        compared_group("Compared baseline", 28400)
        baseline = measure()
        for index in range(4):
            compared_group(f"Compared {index}", 28500 + index * 10)
        expanded = measure()

        response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["buckets"]["ready"], 5)
        # Current results are read once, before judging, for the whole queue.
        self.assertEqual(baseline[2], expanded[2])
        # Existing card rendering reads a few rows per group (17 for four
        # groups, measured before and after the queue judged with results).
        self.assertLessEqual(
            (expanded[0] - baseline[0]) - (expanded[1] - baseline[1]), 20
        )

    def test_queue_judge_query_growth_is_bounded(self) -> None:
        def judged_group(source: str, start: int) -> None:
            _, units = self.make_group(source, ["One", "Two"], start=start)
            self.make_judge_verdict(units[0])
            self.make_judge_verdict(units[1], JudgeVerdict.Severity.MAJOR)

        def verdict_queries(capture) -> int:
            return sum(
                "trans_judgeverdict" in query["sql"]
                for query in capture.captured_queries
            )

        judged_group("Cost baseline", 27100)
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(self.queue_url)
        for index in range(4):
            judged_group(f"Cost group {index}", 27200 + index * 10)
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.queue_url)
        self.assertEqual(response.context["judge_panel"]["buckets"]["ready"], 5)
        # One active-verdict read serves every group on the page.
        self.assertEqual(verdict_queries(baseline), 1)
        self.assertEqual(verdict_queries(expanded), 1)
        # Existing group rendering has per-group reads; the judge panel must
        # not add another query for each verdict or place.
        self.assertLessEqual(
            len(expanded.captured_queries) - len(baseline.captured_queries), 12
        )
