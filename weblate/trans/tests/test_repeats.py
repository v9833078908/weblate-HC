# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for durable, explicitly managed exact-repeat membership."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from weblate.auth.models import setup_project_groups
from weblate.checks.consistency import RepeatDriftCheck
from weblate.trans.autotranslate import AutoTranslate
from weblate.trans.models import (
    Label,
    RepeatMembership,
    RepeatPolicy,
    RepeatRecommendationAttempt,
    RepeatRecommendationRun,
)
from weblate.trans.repeat_recommendations import (
    REPEAT_ATTEMPT_DEADLINE,
    REPEAT_RECOMMENDATION_RESPONSE_SCHEMA,
    _store_attempt_outcome,
    cancel_run,
    current_recommendations,
    execute_attempt,
    live_group_contexts,
    parse_results,
    prepare_run,
    prompt_fingerprint,
    queue_attempt,
    reconcile_expired_attempts,
    request_payload,
    request_size,
    requeue_reserved_attempts,
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
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.trans.util import join_plural
from weblate.utils.celery import INTERACTIVE_TASK_PRIORITY
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

    def recommendation_profile(self, **overrides) -> SimpleNamespace:
        values = {
            "profile_fingerprint": "p" * 64,
            "model": "test-model",
            "temperature": 0,
            "response_format": "json_object",
            "provider": "test",
            "reasoning": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def provider_result(self, group_id: int, action: str, target: list[str]):
        content = json.dumps(
            {
                "results": [
                    {
                        "group": group_id,
                        "action": action,
                        "target": target,
                        "exclusions": [],
                        "rationale": "The key names this exact meaning.",
                    }
                ]
            }
        )
        return SimpleNamespace(
            payload={"choices": [{"message": {"content": content}}], "usage": {}},
            transport_succeeded=True,
            provider_cost=None,
            failure_kind="",
        )

    def paying_run(self, policy, **kwargs):
        """Prepare a run with the provider mocked and nothing published."""
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=self.recommendation_profile(),
            ),
            patch("weblate.trans.repeat_recommendations.queue_attempt"),
        ):
            return prepare_run(policy=policy, actor=self.user, **kwargs)

    def complete_attempt(self, attempt, response) -> None:
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=self.recommendation_profile(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.post_chat_completion",
                return_value=response,
            ),
        ):
            execute_attempt(attempt=attempt)

    @staticmethod
    def sent_groups(run) -> set[int]:
        return {
            item["group"]
            for attempt in run.attempts.all()
            for item in attempt.request_snapshot["groups"]
        }

    def test_capped_runs_reserve_disjoint_attempts_and_keep_all_results(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        groups = [
            get_or_create_group(
                policy, self.add_group(f"Diverging source {index}", ["One", "Two"])[0]
            )
            for index in range(3)
        ]

        with patch(
            "weblate.trans.repeat_recommendations.REPEAT_RECOMMENDATION_BATCH_SIZE", 1
        ):
            first_run = self.paying_run(policy=policy, request_cap=2)

        self.assertEqual(first_run.attempts.count(), 2)
        self.assertEqual(first_run.unsent_groups, 1)
        first_sent = self.sent_groups(first_run)
        self.assertEqual(len(first_sent), 2)
        missing = next(group.pk for group in groups if group.pk not in first_sent)
        for attempt in first_run.attempts.all():
            self.complete_attempt(
                attempt,
                self.provider_result(
                    attempt.request_snapshot["groups"][0]["group"],
                    "use_existing",
                    ["One"],
                ),
            )

        with patch(
            "weblate.trans.repeat_recommendations.REPEAT_RECOMMENDATION_BATCH_SIZE", 1
        ):
            second_run = self.paying_run(policy=policy, request_cap=2)

        self.assertEqual(self.sent_groups(second_run), {missing})
        self.assertEqual(second_run.unsent_groups, 0)
        for attempt in second_run.attempts.all():
            self.complete_attempt(
                attempt,
                self.provider_result(
                    attempt.request_snapshot["groups"][0]["group"],
                    "use_existing",
                    ["One"],
                ),
            )

        current = current_recommendations(policy, actor=self.user)
        self.assertEqual(set(current), {group.pk for group in groups})

    def test_paid_candidates_exclude_resolved_consistent_and_current(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        diverging = self.add_group("Diverging source", ["One", "Two"])[0]
        self.add_group("Consistent source", ["Same", "Same"], start=2000)
        resolved = self.add_group("Resolved source", ["One", "Two"], start=3000)[0]
        diverging_group = get_or_create_group(policy, diverging)
        resolved_group = get_or_create_group(policy, resolved)
        apply_preview(
            token=preview_group(
                group=resolved_group, target=["Shared"], actor=self.user
            ).token,
            actor=self.user,
        )

        first = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(self.sent_groups(first), {diverging_group.pk})
        self.complete_attempt(
            first.attempts.get(),
            self.provider_result(diverging_group.pk, "use_existing", ["One"]),
        )

        second = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(second.attempts.count(), 0)

        # An ordinary edit makes the stored result stale and regenerable, while
        # an unchanged context with an active reservation is never paid twice.
        diverging.target = "Edited"
        diverging.save(update_fields=["target"])
        third = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(self.sent_groups(third), {diverging_group.pk})
        fourth = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(fourth.attempts.count(), 0)

    def test_decided_results_are_not_retried_without_explicit_refresh(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        human = self.add_group("Human source", ["One", "Two"])[0]
        independent = self.add_group("Independent source", ["One", "Two"], start=2000)[
            0
        ]
        human_group = get_or_create_group(policy, human)
        independent_group = get_or_create_group(policy, independent)

        run = self.paying_run(policy=policy, request_cap=5)
        for attempt in run.attempts.all():
            group_id = attempt.request_snapshot["groups"][0]["group"]
            action = "needs_human" if group_id == human_group.pk else "keep_independent"
            self.complete_attempt(attempt, self.provider_result(group_id, action, []))

        again = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(again.attempts.count(), 0)

        refreshed = self.paying_run(
            policy=policy, request_cap=5, refresh_group_ids=[human_group.pk]
        )
        self.assertEqual(self.sent_groups(refreshed), {human_group.pk})
        self.assertNotIn(independent_group.pk, self.sent_groups(refreshed))

    def test_unknown_delivery_requires_explicit_retry_consent(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        unknown = self.add_group("Unknown source", ["One", "Two"])[0]
        delivered = self.add_group("Delivered source", ["One", "Two"], start=2000)[0]
        unknown_group = get_or_create_group(policy, unknown)
        delivered_group = get_or_create_group(policy, delivered)

        with patch(
            "weblate.trans.repeat_recommendations.REPEAT_RECOMMENDATION_BATCH_SIZE", 1
        ):
            run = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(run.attempts.count(), 2)
        for attempt in run.attempts.all():
            group_id = attempt.request_snapshot["groups"][0]["group"]
            if group_id == delivered_group.pk:
                self.complete_attempt(
                    attempt, self.provider_result(group_id, "use_existing", ["One"])
                )
            else:
                with (
                    patch(
                        "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                        return_value=SimpleNamespace(),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                        return_value=self.recommendation_profile(),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.post_chat_completion",
                        side_effect=RuntimeError("connection dropped"),
                    ),
                ):
                    execute_attempt(attempt=attempt)

        run.refresh_from_db()
        self.assertEqual(run.status, run.Status.UNKNOWN)
        current = current_recommendations(policy, actor=self.user)
        self.assertEqual(set(current), {delivered_group.pk})

        denied = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(denied.attempts.count(), 0)

        retried = self.paying_run(
            policy=policy, request_cap=5, refresh_group_ids=[unknown_group.pk]
        )
        self.assertEqual(self.sent_groups(retried), {unknown_group.pk})

    def test_empty_candidates_and_oversized_groups_never_reserve(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        self.add_group("Consistent source", ["Same", "Same"])

        empty = self.paying_run(policy=policy, request_cap=2)
        self.assertEqual(empty.attempts.count(), 0)

        self.add_group("Huge source", ["One", "Two"], start=2000)
        with patch("weblate.trans.repeat_recommendations.MAX_REPEAT_REQUEST_BYTES", 10):
            run = self.paying_run(policy=policy, request_cap=2)
        self.assertEqual(run.attempts.count(), 0)
        local = run.results.get()
        self.assertEqual(local.action, "needs_human")
        self.assertIsNone(local.attempt)
        self.assertTrue(local.context_fingerprint)
        self.assertIn("exceeds", local.rationale)

    def test_request_byte_bound_keeps_whole_groups(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        self.add_group("Меч героя", ["Эпе", "Глефа"])
        self.add_group("Щит героя", ["Эпе", "Глефа"], start=2000)
        profile = self.recommendation_profile()
        contexts = live_group_contexts(policy, actor=self.user)
        context = next(iter(contexts.values()))
        body = request_payload(profile, {"groups": [context]})
        self.assertIn("untrusted_repeat_groups", body["messages"][1]["content"])
        exact = request_size(profile, [context])
        self.assertEqual(exact, len(json.dumps(body, ensure_ascii=False).encode()))

        with patch(
            "weblate.trans.repeat_recommendations.MAX_REPEAT_REQUEST_BYTES", exact
        ):
            fitted = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(fitted.attempts.count(), 2)
        self.assertEqual(fitted.unsent_groups, 0)

        self.add_group("Меч чудовища", ["Эпе", "Глефа"], start=3000)
        self.add_group("Щит чудовища", ["Эпе", "Глефа"], start=4000)
        with patch(
            "weblate.trans.repeat_recommendations.MAX_REPEAT_REQUEST_BYTES", exact - 1
        ):
            oversized = self.paying_run(policy=policy, request_cap=5)
        self.assertEqual(oversized.attempts.count(), 0)
        self.assertEqual(oversized.results.filter(action="needs_human").count(), 2)

    def test_request_contract_states_decisions_and_untrusted_data(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        unit = self.add_group("Request contract source", ["One", "Two"])[0]
        group = get_or_create_group(policy, unit)

        for response_format in ("json_object", "json_schema"):
            with self.subTest(response_format=response_format):
                run = self.paying_run(
                    policy=policy, request_cap=1, refresh_group_ids=[group.pk]
                )
                attempt = run.attempts.get()
                with (
                    patch(
                        "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                        return_value=SimpleNamespace(),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                        return_value=self.recommendation_profile(
                            response_format=response_format
                        ),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.post_chat_completion",
                        return_value=self.provider_result(
                            group.pk, "use_existing", ["One"]
                        ),
                    ) as post,
                ):
                    execute_attempt(attempt=attempt)

                payload = post.call_args.args[0]
                if response_format == "json_object":
                    self.assertEqual(
                        payload["response_format"], {"type": "json_object"}
                    )
                else:
                    self.assertEqual(
                        payload["response_format"]["json_schema"]["schema"],
                        REPEAT_RECOMMENDATION_RESPONSE_SCHEMA,
                    )
                system = payload["messages"][0]["content"]
                self.assertIn('"use_existing"', system)
                self.assertIn('"propose_new"', system)
                self.assertIn('"keep_independent"', system)
                self.assertIn('"needs_human"', system)
                self.assertIn('{"results": [', system)
                user = json.loads(payload["messages"][1]["content"])
                context = user["untrusted_repeat_groups"]["groups"][0]
                self.assertEqual(context["group"], group.pk)
                self.assertEqual(
                    context["plural_number"], self.translation.plural.number
                )
                self.assertEqual(
                    context["source_language"], self.component.source_language.code
                )
                self.assertEqual(
                    context["target_language"], self.translation.language.code
                )

    def test_prompt_fingerprint_tracks_prompt_and_schema(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        self.add_group("Fingerprint source", ["One", "Two"])
        run = self.paying_run(policy=policy, request_cap=1)
        self.assertEqual(run.prompt_fingerprint, prompt_fingerprint())
        baseline = prompt_fingerprint()
        with patch(
            "weblate.trans.repeat_recommendations.repeat_recommendation_prompt",
            return_value="A different prompt.",
        ):
            self.assertNotEqual(prompt_fingerprint(), baseline)

    def fail_attempt(self, attempt) -> None:
        """Deliver unparsable content: a terminal failed attempt."""
        self.complete_attempt(
            attempt,
            SimpleNamespace(
                payload={
                    "choices": [{"message": {"content": "not json"}}],
                    "usage": {},
                },
                transport_succeeded=True,
                provider_cost=None,
                failure_kind="",
            ),
        )

    def drop_attempt(self, attempt) -> None:
        """Lose the response: an unknown delivery that is never replayed."""
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=self.recommendation_profile(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.post_chat_completion",
                side_effect=RuntimeError("connection dropped"),
            ),
        ):
            execute_attempt(attempt=attempt)

    def test_completion_order_never_changes_run_outcome(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        first_group = get_or_create_group(
            policy, self.add_group("First outcome source", ["One", "Two"])[0]
        )
        second_group = get_or_create_group(
            policy,
            self.add_group("Second outcome source", ["One", "Two"], start=2000)[0],
        )
        refresh = [first_group.pk, second_group.pk]

        for failure_mode in ("failure", "unknown"):
            outcomes = {first_group.pk: "success", second_group.pk: failure_mode}
            for reverse_order in (False, True):
                with (
                    self.subTest(
                        failure_mode=failure_mode, reverse_order=reverse_order
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.REPEAT_RECOMMENDATION_BATCH_SIZE",
                        1,
                    ),
                ):
                    run = self.paying_run(
                        policy=policy, request_cap=2, refresh_group_ids=refresh
                    )
                    attempts = list(run.attempts.order_by("pk"))
                    self.assertEqual(len(attempts), 2)
                    if reverse_order:
                        attempts.reverse()
                    for attempt in attempts:
                        group_id = attempt.request_snapshot["groups"][0]["group"]
                        if outcomes[group_id] == "success":
                            self.complete_attempt(
                                attempt,
                                self.provider_result(group_id, "use_existing", ["One"]),
                            )
                        elif outcomes[group_id] == "failure":
                            self.fail_attempt(attempt)
                        else:
                            self.drop_attempt(attempt)
                    run.refresh_from_db()
                    self.assertTrue(run.finished_at)
                    if failure_mode == "failure":
                        self.assertEqual(run.status, run.Status.FAILED)
                        self.assertIn("not JSON", run.failure)
                    else:
                        self.assertEqual(run.status, run.Status.UNKNOWN)
                    current = current_recommendations(policy, actor=self.user)
                    self.assertEqual(set(current), {first_group.pk})

    def test_cancel_during_inflight_keeps_cancelled_run_and_results(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        unit = self.add_group("Cancelled source", ["One", "Two"])[0]
        group = get_or_create_group(policy, unit)
        run = self.paying_run(policy=policy, request_cap=1)
        attempt = run.attempts.get()

        def cancel_then_answer(*_args, **_kwargs):
            cancel_run(run=run, actor=self.user)
            return self.provider_result(group.pk, "use_existing", ["One"])

        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=self.recommendation_profile(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.post_chat_completion",
                side_effect=cancel_then_answer,
            ),
        ):
            execute_attempt(attempt=attempt)

        attempt.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.COMPLETED)
        self.assertEqual(run.status, run.Status.CANCELLED)
        self.assertTrue(run.finished_at)
        self.assertIsNotNone(
            current_recommendations(policy, actor=self.user).get(group.pk)
        )

    def test_publication_failure_keeps_the_reservation_requeueable(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        self.add_group("Unpublished source", ["One", "Two"])
        run = self.paying_run(policy=policy, request_cap=1)
        attempt = run.attempts.get()

        with (
            patch(
                "weblate.trans.tasks.execute_repeat_recommendation_attempt.apply_async",
                side_effect=RuntimeError("broker down"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            queue_attempt(attempt=attempt)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.RESERVED)

        with (
            patch(
                "weblate.trans.tasks.execute_repeat_recommendation_attempt.apply_async"
            ) as published,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(requeue_reserved_attempts(run=run, actor=self.user), 1)
        published.assert_called_once_with(
            args=[attempt.pk], priority=INTERACTIVE_TASK_PRIORITY
        )

    def test_send_expiry_reconciliation_and_late_response(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        unit = self.add_group("Expired source", ["One", "Two"])[0]
        group = get_or_create_group(policy, unit)
        run = self.paying_run(policy=policy, request_cap=1)
        attempt = run.attempts.get()
        # Simulate an in-flight send that crossed its execution deadline.
        attempt.status = attempt.Status.SENT
        attempt.sent_at = timezone.now() - 2 * REPEAT_ATTEMPT_DEADLINE
        attempt.deadline_at = timezone.now() - REPEAT_ATTEMPT_DEADLINE
        attempt.save(update_fields=["status", "sent_at", "deadline_at"])

        self.assertEqual(reconcile_expired_attempts(run=run, actor=self.user), 1)
        attempt.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.UNKNOWN)
        self.assertEqual(attempt.failure, "expired")
        self.assertEqual(run.status, run.Status.UNKNOWN)

        # A late response can never finalize an already reconciled attempt.
        stored = _store_attempt_outcome(
            attempt.pk,
            status=attempt.Status.COMPLETED,
            accepted=[{"group": group.pk, "action": "use_existing", "target": ["One"]}],
        )
        self.assertFalse(stored)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.UNKNOWN)
        self.assertFalse(run.results.exists())

    def test_reconcile_keeps_live_sends_active(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        self.add_group("Live source", ["One", "Two"])
        run = self.paying_run(policy=policy, request_cap=1)
        attempt = run.attempts.get()
        attempt.status = attempt.Status.SENT
        attempt.sent_at = timezone.now()
        attempt.deadline_at = timezone.now() + REPEAT_ATTEMPT_DEADLINE
        attempt.save(update_fields=["status", "sent_at", "deadline_at"])

        self.assertEqual(reconcile_expired_attempts(run=run, actor=self.user), 0)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.SENT)
        run.refresh_from_db()
        self.assertEqual(run.status, run.Status.RUNNING)

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

    def test_undo_recounts_translation_stats_once(self) -> None:
        self.make_manager()
        first = self.add_repeat("first", "Old")
        self.add_repeat("second", "Older")
        policy = self.make_policy()
        group = get_or_create_group(policy, first)
        event = apply_preview(
            token=preview_group(group=group, target=["Shared"], actor=self.user).token,
            actor=self.user,
        )

        with self.captureOnCommitCallbacks() as callbacks:
            undo_event(token=str(event.token), actor=self.user)

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
        profile = self.recommendation_profile()
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
        target = '"X"'
        attempt = run.attempts.create(ordinal=1, request_snapshot=run.snapshot)
        result = parse_results(
            attempt=attempt,
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
                    "target": ["X"],
                }
            ],
        )

    def test_recommendation_parser_rejects_incomplete_plural_target(self) -> None:
        units = self.add_group(
            join_plural(["One exact repeat", "Several exact repeats"]),
            [
                join_plural(["One", "Few", "Many"]),
                join_plural(["Two", "Several", "Many"]),
            ],
        )
        first = units[0]
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

        attempt = run.attempts.create(ordinal=1, request_snapshot=run.snapshot)
        result = parse_results(
            attempt=attempt,
            content=(
                f'{{"results":[{{"group":{group.pk},"action":"propose_new",'
                '"target":["X"]}]}'
            ),
        )

        self.assertEqual(result, [])
        complete = json.dumps(
            {
                "results": [
                    {
                        "group": group.pk,
                        "action": "propose_new",
                        "target": ["X", "Y", "Z"],
                    }
                ]
            }
        )
        self.assertEqual(
            parse_results(attempt=attempt, content=complete)[0]["target"],
            ["X", "Y", "Z"],
        )

    def test_recommendation_parser_accepts_singular_target_in_plural_language(
        self,
    ) -> None:
        self.make_manager()
        policy = self.make_policy()
        units = self.add_group("Singular source", ["One", "Two"])
        group = get_or_create_group(policy, units[0])
        attempt = self.paying_run(policy, request_cap=1).attempts.get()

        def response(target: list[str]) -> str:
            return json.dumps(
                {
                    "results": [
                        {
                            "group": group.pk,
                            "action": "propose_new",
                            "target": target,
                            "exclusions": [],
                            "rationale": "A corrected translation.",
                        }
                    ]
                }
            )

        self.assertEqual(
            parse_results(attempt=attempt, content=response(["Corrected"]))[0][
                "target"
            ],
            ["Corrected"],
        )
        self.assertEqual(
            parse_results(attempt=attempt, content=response(["Extra", "Forms"])),
            [],
        )

    def test_use_existing_requires_an_exact_frozen_variant(self) -> None:
        self.make_manager()
        policy = self.make_policy()
        units = self.add_group("Existing variant", ["One", "Two"])
        group = get_or_create_group(policy, units[0])
        attempt = self.paying_run(policy, request_cap=1).attempts.get()

        valid_target = units[1].get_target_plurals()
        for target in (
            [],
            ["Invented"],
            [*valid_target, "extra"],
        ):
            with self.subTest(target=target):
                content = json.dumps(
                    {
                        "results": [
                            {
                                "group": group.pk,
                                "action": "use_existing",
                                "target": target,
                                "exclusions": [],
                                "rationale": "Claimed existing variant",
                            }
                        ]
                    }
                )
                self.assertEqual(parse_results(attempt=attempt, content=content), [])

        valid = json.dumps(
            {
                "results": [
                    {
                        "group": group.pk,
                        "action": "use_existing",
                        "target": valid_target,
                        "exclusions": [],
                        "rationale": "This variant fits the context.",
                    }
                ]
            }
        )
        self.assertEqual(
            parse_results(attempt=attempt, content=valid)[0]["target"], valid_target
        )

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


class RepeatRecommendationConcurrencyTest(RepoTestMixin, TransactionTestCase):
    """Two independent DB connections racing repeat recommendation traffic."""

    def setUp(self) -> None:
        self.clone_test_repos()
        super().setUp()
        component = self.create_component()
        component.create_path()
        self.project = component.project
        setup_project_groups(self, self.project)
        self.translation = component.translation_set.get(language_code="cs")
        self.user = create_test_user()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=component.source_language,
                target_language=self.translation.language,
            ),
            components=[component],
            labels=[],
            actor=self.user,
        )
        source = "A concurrent repeat"
        for position, target in ((1000, "One"), (1001, "Two")):
            context = f"key{position}"
            source_unit = component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            self.translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=position,
                source_unit=source_unit,
                context=context,
                source=source,
                target=target,
                state=STATE_TRANSLATED,
            )
        self.group = get_or_create_group(
            self.policy, self.translation.unit_set.get(context="key1000")
        )
        self.profile = SimpleNamespace(
            profile_fingerprint="p" * 64,
            model="test-model",
            temperature=0,
            response_format="json_object",
            provider="test",
            reasoning="",
        )
        connection.close()

    def test_simultaneous_preparations_pay_each_group_once(self) -> None:
        barrier = threading.Barrier(2)
        runs: list[int] = []
        lock = threading.Lock()

        def prepare() -> None:
            try:
                with (
                    patch(
                        "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                        return_value=SimpleNamespace(),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                        return_value=self.profile,
                    ),
                    patch("weblate.trans.repeat_recommendations.queue_attempt"),
                ):
                    barrier.wait(timeout=10)
                    run = prepare_run(
                        policy=self.policy, actor=self.user, request_cap=1
                    )
                with lock:
                    runs.append(run.pk)
            finally:
                connection.close()

        threads = [threading.Thread(target=prepare) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(runs), 2, "both preparations must finish")
        self.assertEqual(RepeatRecommendationRun.objects.count(), 2)
        # The policy lock serializes the recheck: exactly one preparation pays.
        self.assertEqual(RepeatRecommendationAttempt.objects.count(), 1)

    def test_duplicate_delivery_sends_one_request(self) -> None:
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=self.profile,
            ),
            patch("weblate.trans.repeat_recommendations.queue_attempt"),
        ):
            run = prepare_run(policy=self.policy, actor=self.user, request_cap=1)
        attempt = run.attempts.get()
        connection.close()

        sends: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def send(*_args, **_kwargs):
            with lock:
                sends.append(1)
            content = json.dumps(
                {
                    "results": [
                        {
                            "group": self.group.pk,
                            "action": "use_existing",
                            "target": ["One"],
                            "exclusions": [],
                            "rationale": "The key names this exact meaning.",
                        }
                    ]
                }
            )
            return SimpleNamespace(
                payload={"choices": [{"message": {"content": content}}], "usage": {}},
                transport_succeeded=True,
                provider_cost=None,
                failure_kind="",
            )

        def deliver() -> None:
            try:
                with (
                    patch(
                        "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                        return_value=SimpleNamespace(),
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                        return_value=self.profile,
                    ),
                    patch(
                        "weblate.trans.repeat_recommendations.post_chat_completion",
                        side_effect=send,
                    ),
                ):
                    barrier.wait(timeout=10)
                    execute_attempt(attempt=attempt)
            finally:
                connection.close()

        threads = [threading.Thread(target=deliver) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(sends), 1, "a claimed attempt sends exactly once")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, attempt.Status.COMPLETED)
        self.assertEqual(run.results.count(), 1)
