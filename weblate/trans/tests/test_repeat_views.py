# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Views for the managed repeat queue."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse

from weblate.trans.models import RepeatPolicy
from weblate.trans.repeats import save_policy
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
        self.assertContains(preview, "Nothing has been saved yet")
        self.assertContains(preview, 'name="unit"')

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
