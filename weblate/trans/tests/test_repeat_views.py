# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Views for the managed repeat queue."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse

from weblate.checks.models import CHECKS
from weblate.trans.models import RepeatPolicy
from weblate.trans.repeats import get_or_create_group, save_policy
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_TRANSLATED


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
            reverse("repeat-queue", kwargs={"project": self.project.slug, "language": "cs"})
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
