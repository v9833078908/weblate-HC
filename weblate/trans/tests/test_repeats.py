# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for durable, explicitly managed exact-repeat membership."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse

from weblate.checks.consistency import RepeatDriftCheck
from weblate.trans.autotranslate import AutoTranslate
from weblate.trans.models import (
    Label,
    RepeatMembership,
    RepeatPolicy,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_recommendations import (
    execute_attempt,
    parse_results,
    prepare_run,
    reserve_attempt,
)
from weblate.trans.repeats import (
    apply_preview,
    create_membership,
    current_shared_membership,
    detect_policy_groups,
    get_or_create_group,
    preview_group,
    reconcile_unit,
    save_policy,
    undo_event,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash, hash_to_checksum
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

    def test_apply_recounts_translation_stats_once(self) -> None:
        """Every written place must not trigger its own full stats recount."""
        first = self.add_repeat("first", "Old")
        self.add_repeat("second", "Older")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        preview = preview_group(group=group, target=["Shared"], actor=self.user)

        with self.captureOnCommitCallbacks() as callbacks:
            apply_preview(token=preview.token, actor=self.user)

        recounts = [
            callback
            for callback in callbacks
            if getattr(callback, "__name__", "") == "_invalidate_trigger"
        ]
        self.assertEqual(len(recounts), 1)

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

    def test_guarded_undo_preserves_later_human_edit(self) -> None:
        self.make_manager()
        first = self.add_repeat("first", "Old")
        second = self.add_repeat("second", "Other")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        event = apply_preview(
            token=preview_group(group=group, target=["Shared"], actor=self.user).token,
            actor=self.user,
        )
        first.translate(self.user, "Later", STATE_TRANSLATED, propagate=False)
        undo = undo_event(token=event.token, actor=self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, "Later")
        self.assertEqual(second.target, "Other")
        self.assertEqual(
            undo.result["conflicts"], [{"unit": first.pk, "reason": "changed"}]
        )

    def test_independent_membership_is_excluded_from_repeat_check(self) -> None:
        first = self.add_repeat("first", "One")
        second = self.add_repeat("second", "Two")
        policy = self.make_policy()
        create_membership(
            group=get_or_create_group(policy, first),
            unit=first,
            mode=RepeatMembership.Mode.INDEPENDENT,
            reason="intentional",
        )
        remaining = RepeatDriftCheck().get_repeat_members(second.repeat_units)
        self.assertEqual(remaining, [])

    def test_later_label_overlap_disables_existing_shared_reuse(self) -> None:
        """Selector changes must never choose a winner between active rules."""
        first = self.add_repeat("first", "One")
        first_label = Label.objects.create(
            project=self.project, name="first", color="blue"
        )
        second_label = Label.objects.create(
            project=self.project, name="second", color="green"
        )
        first.source_unit.save_labels([first_label], self.user)
        policy = RepeatPolicy(
            project=self.project,
            source_language=self.component.source_language,
            target_language=self.translation.language,
        )
        policy = save_policy(
            policy=policy,
            components=[self.component],
            labels=[first_label],
            actor=self.user,
        )
        save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=self.translation.language,
            ),
            components=[self.component],
            labels=[second_label],
            actor=self.user,
        )
        create_membership(
            group=get_or_create_group(policy, first),
            unit=first,
            mode=RepeatMembership.Mode.SHARED,
        )

        first.source_unit.save_labels([first_label, second_label], self.user)

        self.assertIsNone(current_shared_membership(first))

    def test_recommendation_attempt_cap_is_reserved_before_send(self) -> None:
        run = RepeatRecommendationRun.objects.create(
            policy=self.make_policy(),
            actor=self.user,
            snapshot={},
            snapshot_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_fingerprint="c" * 64,
            request_cap=1,
        )
        attempt = reserve_attempt(run=run, request_snapshot={"groups": []})
        self.assertEqual(attempt.ordinal, 1)
        with self.assertRaisesMessage(ValidationError, "request cap"):
            reserve_attempt(run=run, request_snapshot={"groups": []})

    def test_recommendation_snapshot_contains_complete_untrusted_context(self) -> None:
        self.make_manager()
        first = self.add_repeat("first-context", "One")
        self.add_repeat("second-context", "Two")
        first.source_unit.explanation = "Shown in the inventory"
        first.source_unit.save(update_fields=["explanation"])
        policy = self.make_policy()
        profile = SimpleNamespace(profile_fingerprint="p" * 64)
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=profile,
            ),
        ):
            run = prepare_run(policy=policy, actor=self.user, request_cap=1)

        group = run.snapshot["groups"][0]
        self.assertTrue(group["sendable"])
        self.assertEqual(group["source_forms"], ["An exact repeat"])
        self.assertEqual(
            {member["context"] for member in group["members"]},
            {"first-context", "second-context"},
        )
        self.assertEqual(group["members"][0]["explanation"], "Shown in the inventory")
        first.refresh_from_db()
        self.assertEqual(first.target, "One")

    def test_recommendation_parser_rejects_unknown_group_and_extra_fields(self) -> None:
        first = self.add_repeat("first", "One")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        run = RepeatRecommendationRun.objects.create(
            policy=policy,
            actor=self.user,
            snapshot={
                "groups": [
                    {
                        "group": group.pk,
                        "group_revision": group.revision,
                        "unit_ids": [first.pk],
                    }
                ]
            },
            snapshot_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_fingerprint="c" * 64,
            request_cap=1,
        )
        target = ", ".join('"X"' for _ in range(group.plural_number))
        result = parse_results(
            run=run,
            content=(
                f'{{"results":[{{"group":{group.pk},"action":"propose_new","target":[{target}]}},'
                '{"group":999,"action":"needs_human"},'
                f'{{"group":{group.pk},"action":"needs_human","extra":true}}]}}'
            ),
        )
        self.assertEqual(
            result,
            [
                {
                    "group": group.pk,
                    "action": "propose_new",
                    "target": ["X"] * group.plural_number,
                }
            ],
        )

    def test_recommendation_parser_rejects_incomplete_plural_target(self) -> None:
        first = self.add_repeat("first", "One")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        run = RepeatRecommendationRun.objects.create(
            policy=policy,
            actor=self.user,
            snapshot={
                "groups": [
                    {
                        "group": group.pk,
                        "group_revision": group.revision,
                        "unit_ids": [first.pk],
                    }
                ]
            },
            snapshot_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_fingerprint="c" * 64,
            request_cap=1,
        )

        result = parse_results(
            run=run,
            content=(
                f'{{"results":[{{"group":{group.pk},"action":"propose_new",'
                '"target":["X"]}]}'
            ),
        )

        self.assertEqual(result, [])

    def test_recommendation_send_exception_is_unknown_and_not_replayed(self) -> None:
        """A lost response consumes its reservation without leaving a sent run."""
        self.make_manager()
        run = RepeatRecommendationRun.objects.create(
            policy=self.make_policy(),
            actor=self.user,
            snapshot={"groups": []},
            snapshot_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_fingerprint="c" * 64,
            request_cap=1,
        )
        attempt = reserve_attempt(run=run, request_snapshot={"groups": []})
        profile = SimpleNamespace(
            profile_fingerprint="b" * 64,
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
            patch(
                "weblate.trans.repeat_recommendations.post_chat_completion",
                side_effect=RuntimeError("connection dropped"),
            ),
        ):
            execute_attempt(attempt=attempt)

        attempt.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.UNKNOWN)
        self.assertEqual(run.status, run.Status.UNKNOWN)
        execute_attempt(attempt=attempt)
        self.assertEqual(attempt.status, attempt.Status.UNKNOWN)

    def test_mt_fetch_reuses_accepted_target_before_machinery(self) -> None:
        unit = self.add_repeat("first", "Old")
        policy = self.make_policy()
        group = get_or_create_group(policy, unit)
        group.shared_target = ["Shared"]
        group.save(update_fields=["shared_target"])
        create_membership(group=group, unit=unit, mode=RepeatMembership.Mode.SHARED)
        auto = AutoTranslate(
            translation=self.translation,
            user=self.user,
            q=f"id:{unit.pk}",
            mode="translate",
        )
        with patch("weblate.trans.autotranslate.fetch_machinery_matches") as fetch:
            auto.fetch_mt(["missing"], 0)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Shared")
        fetch.assert_not_called()

    def test_mt_reuses_decision_for_occurrence_imported_later(self) -> None:
        first = self.add_repeat("first", "One")
        self.add_repeat("second", "Two")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        apply_preview(
            token=preview_group(group=group, target=["Shared"], actor=self.user).token,
            actor=self.user,
        )
        imported = self.add_repeat("imported-later", "")
        auto = AutoTranslate(
            translation=self.translation,
            user=self.user,
            q=f"id:{imported.pk}",
            mode="translate",
        )

        with patch("weblate.trans.autotranslate.fetch_machinery_matches") as fetch:
            auto.fetch_mt(["missing"], 0)

        imported.refresh_from_db()
        self.assertEqual(imported.target, "Shared")
        self.assertEqual(
            imported.repeat_memberships.get().mode, RepeatMembership.Mode.SHARED
        )
        fetch.assert_not_called()

    def test_api_requires_and_accepts_explicit_independent_decision(self) -> None:
        self.make_manager()
        unit = self.add_repeat("first", "Old")
        policy = self.make_policy()
        create_membership(
            group=get_or_create_group(policy, unit),
            unit=unit,
            mode=RepeatMembership.Mode.SHARED,
        )
        url = reverse("api:unit-detail", kwargs={"pk": unit.pk})

        response = self.client.patch(
            url,
            {"state": STATE_TRANSLATED, "target": ["New"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

        response = self.client.patch(
            url,
            {
                "state": STATE_TRANSLATED,
                "target": ["New"],
                "repeat_decision": "independent",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "New")
        self.assertEqual(
            unit.repeat_memberships.get().mode, RepeatMembership.Mode.INDEPENDENT
        )

    def test_zen_returns_repeat_choice_without_saving_shared_edit(self) -> None:
        unit = self.add_repeat("first", "Old")
        policy = self.make_policy()
        create_membership(
            group=get_or_create_group(policy, unit),
            unit=unit,
            mode=RepeatMembership.Mode.SHARED,
        )
        params = {
            "checksum": unit.checksum,
            "contentsum": hash_to_checksum(unit.content_hash),
            "translationsum": hash_to_checksum(unit.get_target_hash()),
            "target_0": "New",
            "review": str(STATE_TRANSLATED),
            "repeat_decision": "shared",
        }

        response = self.client.post(
            reverse("save_zen", kwargs={"path": self.translation.get_url_path()}),
            params,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["repeat_decision_required"])
        self.assertEqual(
            response.json()["repeat_decision_choices"], ["shared", "independent"]
        )
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Old")
