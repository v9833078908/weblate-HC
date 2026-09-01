# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
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
    _write_verdict,
    build_request,
    drain_judge_deferrals,
)
from weblate.trans.models.judge import (
    JudgeAdaptiveState,
    JudgeDeferral,
    JudgeRequestAttempt,
    JudgeRun,
    JudgeRunUnit,
    JudgeVerdict,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_FUZZY

DEAD = JudgeResult("none", "", [], "", unparsed=True, failure_kind="transport")


def mock_request_verdicts(results):
    """Fake ``request_verdicts``: each call consumes one single-result batch."""
    batches = iter([[item] for item in results])

    def request(requests, *, on_batch, **kwargs):
        batch = next(batches)
        on_batch(requests, batch)
        return batch

    return request


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

    def test_both_endpoints_failing_creates_one_deferral_keyed_to_primary(
        self,
    ) -> None:
        unit = self.get_unit()
        primary_profile = resolve_judge_seat_profile(1)
        fallback_also_failed = JudgeResult(
            "none",
            "",
            [],
            "",
            unparsed=True,
            failure_kind="http-server",
            served_model="fallback/model",
            served_provider="openrouter",
            served_profile_fingerprint="f" * 64,
        )
        self.defer(unit, fallback_also_failed)

        self.assertEqual(unit.judge_deferrals.count(), 1)
        deferral = unit.judge_deferrals.get()
        self.assertEqual(deferral.state, JudgeDeferral.State.QUEUED)
        # The drain must retry the primary first: a transient outage cannot
        # pin a unit to the fallback forever.
        self.assertEqual(
            deferral.profile_fingerprint, primary_profile.profile_fingerprint
        )
        self.assertNotEqual(deferral.profile_fingerprint, "f" * 64)

    def test_a_fallback_judged_unit_closes_an_existing_deferral(self) -> None:
        unit = self.get_unit()
        self.defer(unit)
        self.assertTrue(unit.judge_deferrals.exists())
        fallback_pass = JudgeResult(
            "none",
            "pass",
            [],
            "",
            served_model="fallback/model",
            served_provider="openrouter",
        )
        self.defer(unit, fallback_pass)
        self.assertEqual(
            unit.judge_deferrals.get().state, JudgeDeferral.State.CLOSED
        )

    def test_a_protocol_failure_on_the_primary_still_queues(self) -> None:
        unit = self.get_unit()
        protocol_failure = JudgeResult(
            "none", "", [], "", unparsed=True, failure_kind="invalid-json"
        )
        self.defer(unit, protocol_failure)
        deferral = unit.judge_deferrals.get()
        self.assertEqual(deferral.state, JudgeDeferral.State.QUEUED)
        self.assertEqual(deferral.last_failure_kind, "invalid-json")

    def test_drain_may_fail_over_and_stays_read_only(self) -> None:
        unit = self.get_unit()
        before_target = unit.get_target_plurals()
        before_state = unit.state
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        fallback_served = JudgeResult(
            "none",
            "pass",
            [],
            "",
            served_model="fallback/model",
            served_provider="openrouter",
        )
        with mock.patch(
            "weblate.trans.judge_loop.request_verdicts",
            mock.Mock(side_effect=mock_request_verdicts([fallback_served])),
        ):
            processed = drain_judge_deferrals()

        self.assertEqual(processed, 1)
        drained = (
            JudgeVerdict.objects.filter(unit=unit, seat=1)
            .order_by("-timestamp")
            .first()
        )
        self.assertIsNotNone(drained)
        self.assertFalse(drained.unparsed)
        self.assertEqual(drained.judge_provider, "openrouter")
        self.assertEqual(drained.judge_model, "fallback/model")
        refreshed = type(unit).objects.get(pk=unit.pk)
        self.assertEqual(refreshed.get_target_plurals(), before_target)
        self.assertEqual(refreshed.state, before_state)

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

    def test_expired_claim_lease_is_reclaimable_by_another_pass(self) -> None:
        unit = self.get_unit()
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        first_token, first_claim = _claim_judge_deferrals()
        self.assertEqual(len(first_claim), 1)
        _second_token, second_claim = _claim_judge_deferrals()
        self.assertEqual(second_claim, [])

        # The lease itself expired (a worker died mid-pass without
        # releasing): a later pass must be able to reclaim the row rather
        # than leaving it stuck until the deferral's own backoff elapses.
        JudgeDeferral.objects.filter(unit=unit).update(
            claim_expires_at=timezone.now() - timedelta(seconds=1)
        )
        third_token, third_claim = _claim_judge_deferrals()
        self.assertEqual(len(third_claim), 1)
        self.assertEqual(third_claim[0].claim_token, third_token)
        self.assertNotEqual(third_token, first_token)

    @override_settings(JUDGE_REQUEST_DEADLINE=400)
    def test_claim_that_cannot_cover_the_full_request_deadline_is_skipped(
        self,
    ) -> None:
        # The lease is max(300, JUDGE_DEFERRAL_MIN_INTERVAL) seconds; a
        # request deadline that alone exceeds the lease can never be
        # started safely, since the retried call could still be running
        # when another worker reclaims the same row.
        unit = self.get_unit()
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        with mock.patch("weblate.trans.judge_loop.run_judge_batch") as run_judge_batch:
            processed = drain_judge_deferrals()

        run_judge_batch.assert_not_called()
        self.assertEqual(processed, 0)
        deferral = unit.judge_deferrals.get()
        self.assertEqual(deferral.claim_token, "")
        self.assertIsNone(deferral.claim_expires_at)
        self.assertEqual(deferral.state, JudgeDeferral.State.QUEUED)

    @override_settings(
        JUDGE_REQUEST_DEADLINE=120,
        JUDGE_REQUEST_DEADLINE_SEAT_2="400",
    )
    def test_claim_uses_the_deferred_seats_request_deadline(self) -> None:
        unit = self.get_unit()
        _sync_deferral(
            unit,
            build_request(unit),
            seat=2,
            profile=resolve_judge_seat_profile(2),
            project_context="",
            result=DEAD,
        )
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        with mock.patch("weblate.trans.judge_loop.run_judge_batch") as run_judge_batch:
            processed = drain_judge_deferrals()

        run_judge_batch.assert_not_called()
        self.assertEqual(processed, 0)

    @override_settings(
        JUDGE_REQUEST_DEADLINE=120,
        JUDGE_REQUEST_DEADLINE_SEAT_2="200",
    )
    def test_retry_lease_reserves_the_deferred_seats_request_deadline(self) -> None:
        unit = self.get_unit()
        _sync_deferral(
            unit,
            build_request(unit),
            seat=2,
            profile=resolve_judge_seat_profile(2),
            project_context="",
            result=DEAD,
        )
        now = timezone.now()
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=now - timedelta(seconds=1)
        )

        with (
            mock.patch("weblate.trans.judge_loop.time.monotonic", return_value=100),
            mock.patch("weblate.trans.judge_loop.timezone.now", return_value=now),
            mock.patch("weblate.trans.judge_loop.run_judge_batch") as run_judge_batch,
        ):
            processed = drain_judge_deferrals()

        self.assertEqual(processed, 1)
        self.assertAlmostEqual(run_judge_batch.call_args.kwargs["retry_deadline"], 200)

    def test_availability_failure_trips_the_circuit(self) -> None:
        profile = resolve_judge_seat_profile(1)
        transport_failure = JudgeResult(
            "none", "", [], "", unparsed=True, failure_kind="transport"
        )
        with override_settings(
            JUDGE_DEFERRAL_CIRCUIT_FAILURE_THRESHOLD=1,
            JUDGE_DEFERRAL_CIRCUIT_OPEN_SECONDS=60,
        ):
            _record_deferral_circuit_outcome(profile, transport_failure)
        state = JudgeAdaptiveState.objects.get(
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            seat=profile.seat,
        )
        self.assertEqual(state.circuit_state, JudgeAdaptiveState.CircuitState.OPEN)

    def test_non_availability_failure_never_trips_the_circuit(self) -> None:
        profile = resolve_judge_seat_profile(1)
        finish_length = JudgeResult(
            "none", "", [], "", unparsed=True, failure_kind="finish-length"
        )
        with override_settings(
            JUDGE_DEFERRAL_CIRCUIT_FAILURE_THRESHOLD=1,
            JUDGE_DEFERRAL_CIRCUIT_OPEN_SECONDS=60,
        ):
            for _ in range(5):
                _record_deferral_circuit_outcome(profile, finish_length)
        state = JudgeAdaptiveState.objects.get(
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            seat=profile.seat,
        )
        self.assertEqual(state.circuit_state, JudgeAdaptiveState.CircuitState.CLOSED)
        self.assertEqual(state.failure_streak, 0)

    def test_drain_run_projects_a_recovered_critical_hold_without_mutating_target(
        self,
    ) -> None:
        unit = self.change_unit("Ahoj svete!")
        before_target = unit.get_target_plurals()
        _write_verdict(
            unit,
            build_request(unit),
            seat=2,
            attempt=0,
            run_id=uuid.uuid4(),
            result=JudgeResult("none", "pass", [], ""),
            profile=resolve_judge_seat_profile(2),
            project_context="",
        )
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        critical = JudgeResult(
            "critical",
            "reject",
            [{"span": "x", "category": "terminology", "severity": "critical"}],
            "",
        )
        with mock.patch(
            "weblate.trans.judge_loop.request_verdicts",
            mock.Mock(side_effect=mock_request_verdicts([critical])),
        ):
            processed = drain_judge_deferrals()

        self.assertEqual(processed, 1)
        self.assertEqual(
            unit.judge_deferrals.get(seat=1).state, JudgeDeferral.State.CLOSED
        )
        run = JudgeRun.objects.get()
        self.assertIsNone(run.actor)
        self.assertEqual(run.scope_type, JudgeRun.ScopeType.TRANSLATION)
        self.assertEqual(run.scope_id, str(unit.translation_id))
        self.assertEqual(run.requested_mode, "drain")
        self.assertEqual(run.status, JudgeRun.Status.COMPLETED)
        run_unit = JudgeRunUnit.objects.get(run=run, unit_id_snapshot=unit.pk)
        self.assertEqual(run_unit.outcome, JudgeRunUnit.Outcome.CRITICAL)
        self.assertTrue(run_unit.projection_succeeded)
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_FUZZY)
        self.assertEqual(unit.get_target_plurals(), before_target)

    @override_settings(JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0)
    def test_drain_run_marks_a_still_failing_unit_as_deferred_not_unparsed(
        self,
    ) -> None:
        unit = self.get_unit()
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        with mock.patch(
            "weblate.trans.judge_loop.request_verdicts",
            mock.Mock(side_effect=mock_request_verdicts([DEAD])),
        ):
            drain_judge_deferrals()

        self.assertIn(
            unit.judge_deferrals.get(seat=1).state,
            (JudgeDeferral.State.QUEUED, JudgeDeferral.State.SLOW),
        )
        run = JudgeRun.objects.get()
        run_unit = JudgeRunUnit.objects.get(run=run, unit_id_snapshot=unit.pk)
        self.assertEqual(run_unit.outcome, JudgeRunUnit.Outcome.DEFERRED)

    @override_settings(JUDGE_ENABLED=False)
    def test_drain_closes_every_deferral_on_a_disabled_judge_scope(self) -> None:
        with override_settings(JUDGE_ENABLED=True):
            unit = self.get_unit()
            self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        processed = drain_judge_deferrals()

        self.assertEqual(processed, 0)
        deferral = unit.judge_deferrals.get(seat=1)
        self.assertEqual(deferral.state, JudgeDeferral.State.CLOSED)
        self.assertIsNotNone(deferral.closed_at)
        self.assertFalse(JudgeRun.objects.exists())

    def test_drain_retains_deferrals_on_a_recoverable_configuration_failure(
        self,
    ) -> None:
        unit = self.get_unit()
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        with mock.patch(
            "weblate.trans.judge_loop.resolve_judge_seat_profile",
            side_effect=RuntimeError("boom"),
        ):
            processed = drain_judge_deferrals()

        self.assertEqual(processed, 0)
        deferral = unit.judge_deferrals.get(seat=1)
        self.assertEqual(deferral.state, JudgeDeferral.State.QUEUED)
        self.assertIsNone(deferral.closed_at)
