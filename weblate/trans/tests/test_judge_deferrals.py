# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from weblate.trans.judge import JudgeResult, resolve_judge_seat_profile
from weblate.trans.judge_loop import (
    _claim_judge_deferrals,
    _record_deferral_circuit_outcome,
    _release_judge_deferrals,
    _reserve_deferral_requests,
    _sync_deferral,
    build_request,
    drain_judge_deferrals,
)
from weblate.trans.models.judge import (
    JudgeAdaptiveState,
    JudgeDeferral,
    JudgeRequestAttempt,
)
from weblate.trans.tests.test_views import ViewTestCase

DEAD = JudgeResult("none", "", [], "", unparsed=True, failure_kind="transport")


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_DEFERRAL_ENABLED=True,
    JUDGE_DEFERRAL_MIN_INTERVAL=10,
    JUDGE_DEFERRAL_MAX_INTERVAL=40,
    JUDGE_DEFERRAL_SLOW_AFTER=2,
    JUDGE_DEFERRAL_MAX_UNITS_PER_PASS=20,
)
class JudgeDeferralTest(ViewTestCase):
    def defer(self, unit, result=DEAD):
        _sync_deferral(
            unit,
            build_request(unit),
            seat=1,
            profile=resolve_judge_seat_profile(1),
            project_context="",
            result=result,
        )

    def test_repeated_failure_deduplicates_and_applies_backoff(self) -> None:
        unit = self.get_unit()
        self.defer(unit)
        first = unit.judge_deferrals.get()
        self.defer(unit)
        second = unit.judge_deferrals.get()

        self.assertEqual(unit.judge_deferrals.count(), 1)
        self.assertEqual(second.consecutive_failures, 2)
        self.assertEqual(second.state, JudgeDeferral.State.SLOW)
        self.assertGreater(second.next_attempt_at, first.next_attempt_at)

    def test_success_without_a_previous_failure_creates_no_queue_row(self) -> None:
        unit = self.get_unit()
        self.defer(unit, JudgeResult("none", "pass", [], ""))

        self.assertFalse(unit.judge_deferrals.exists())

    def test_changed_request_closes_the_stale_deferral(self) -> None:
        unit = self.get_unit()
        self.defer(unit)
        unit.target = "updated target"
        self.defer(unit)
        rows = list(unit.judge_deferrals.order_by("pk"))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].state, JudgeDeferral.State.CLOSED)
        self.assertEqual(rows[1].state, JudgeDeferral.State.QUEUED)

    def test_late_response_does_not_close_newer_different_identity(self) -> None:
        unit = self.get_unit()
        profile = resolve_judge_seat_profile(1)
        old_request = build_request(unit)
        attempt = JudgeRequestAttempt.objects.create(
            seat=1,
            provider=profile.provider,
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            model_fingerprint=profile.model_fingerprint,
            profile_fingerprint=profile.profile_fingerprint,
            prompt_schema_version=profile.prompt_schema_version,
            batch_digest="0" * 64,
            batch_size=1,
            failure_kind="transport",
            elapsed_ms=1000,
        )
        JudgeRequestAttempt.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(seconds=20)
        )

        unit.target = "new target"
        self.defer(unit)
        newer = unit.judge_deferrals.get(state=JudgeDeferral.State.QUEUED)

        _sync_deferral(
            unit,
            old_request,
            seat=1,
            profile=profile,
            project_context="",
            result=JudgeResult(
                "none",
                "",
                [],
                "",
                unparsed=True,
                failure_kind="transport",
                request_attempt_id=attempt.pk,
            ),
            attempt_started_at=timezone.now() - timedelta(seconds=19),
        )

        newer.refresh_from_db()
        self.assertEqual(newer.state, JudgeDeferral.State.QUEUED)

    def test_finish_length_is_terminal_not_requeued(self) -> None:
        unit = self.get_unit()
        self.defer(
            unit,
            JudgeResult(
                "none", "", [], "", unparsed=True, failure_kind="finish-length"
            ),
        )
        deferral = unit.judge_deferrals.get()
        self.assertEqual(deferral.state, JudgeDeferral.State.CLOSED)
        self.assertIsNotNone(deferral.closed_at)

    def test_claims_are_exclusive_until_released(self) -> None:
        unit = self.get_unit()
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        token, claimed = _claim_judge_deferrals()
        self.assertEqual(len(claimed), 1)
        _second_token, second = _claim_judge_deferrals()
        self.assertEqual(second, [])
        _release_judge_deferrals(token)

    def test_drain_requests_only_missing_seat_without_writing(self) -> None:
        unit = self.get_unit()
        original = (unit.target, unit.state)
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        with mock.patch("weblate.trans.judge_loop.run_judge_batch") as run_judge_batch:
            self.assertEqual(drain_judge_deferrals(), 1)

        run_judge_batch.assert_called_once()
        self.assertEqual(run_judge_batch.call_args.kwargs["writable_ids"], set())
        self.assertIsNone(run_judge_batch.call_args.kwargs["user"])
        self.assertEqual(run_judge_batch.call_args.kwargs["seats"], (1,))
        self.assertFalse(run_judge_batch.call_args.kwargs["use_cache"])
        unit.refresh_from_db()
        self.assertEqual((unit.target, unit.state), original)

    @override_settings(JUDGE_DEFERRAL_TOKEN_BUCKET_CAPACITY=1)
    def test_token_bucket_reserves_bounded_requests(self) -> None:
        profile = resolve_judge_seat_profile(1)

        self.assertEqual(_reserve_deferral_requests(profile, 2), 1)
        self.assertEqual(_reserve_deferral_requests(profile, 1), 0)

    @override_settings(
        JUDGE_DEFERRAL_CIRCUIT_FAILURE_THRESHOLD=2,
        JUDGE_DEFERRAL_CIRCUIT_OPEN_SECONDS=60,
    )
    def test_circuit_opens_after_repeated_failures_then_half_opens(self) -> None:
        profile = resolve_judge_seat_profile(1)
        _record_deferral_circuit_outcome(profile, DEAD)
        _record_deferral_circuit_outcome(profile, DEAD)
        state = JudgeAdaptiveState.objects.get(
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            seat=profile.seat,
        )
        self.assertEqual(state.circuit_state, JudgeAdaptiveState.CircuitState.OPEN)
        self.assertEqual(_reserve_deferral_requests(profile, 1), 0)

        state.circuit_open_until = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["circuit_open_until"])

        self.assertEqual(_reserve_deferral_requests(profile, 2), 1)
        state.refresh_from_db()
        self.assertEqual(
            state.circuit_state,
            JudgeAdaptiveState.CircuitState.HALF_OPEN,
        )
