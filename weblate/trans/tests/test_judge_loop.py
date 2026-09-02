# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import threading
import uuid
from unittest import mock

from django.conf import settings
from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
)
from weblate.trans.judge import (
    JudgeRequest,
    JudgeResult,
    RetryBudget,
    _write_llm_usage,
    resolve_judge_seat_profile,
)
from weblate.trans.judge_loop import (
    _has_active_check,
    _run_seats,
    _SeatJob,
    _select_repair_texts,
    _write_verdict,
    active_judge_candidate,
    build_request,
    repair_targets,
    run_judge_batch,
)
from weblate.trans.models.judge import (
    JudgeRequestAttempt,
    JudgeRun,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_FUZZY, STATE_TRANSLATED


def result(severity, verdict, **kw):
    errs = (
        []
        if severity == "none"
        else [
            {
                "span": "x",
                "category": "terminology",
                "severity": severity,
                "description": "d",
            }
        ]
    )
    return JudgeResult(
        max_severity=severity,
        model_verdict=verdict,
        errors=errs,
        back_translation=kw.get("bt", ""),
        unparsed=kw.get("unparsed", False),
    )


PASS = result("none", "pass")
MAJOR = result("major", "flag")
MINOR = result("minor", "pass")
CRITICAL = result("critical", "reject")
DEAD = JudgeResult("none", "", [], "", unparsed=True)


class MockRequestVerdicts:
    def __init__(self, batches):
        models = (
            settings.JUDGE_MODEL_SEAT_1,
            settings.JUDGE_MODEL_SEAT_2,
        )
        if models[0] == models[1]:
            msg = "mock request verdicts requires distinct configured models"
            raise ValueError(msg)
        self._lock = threading.Lock()
        self._results = {
            model: iter(batches[index::2]) for index, model in enumerate(models)
        }
        self._recorder = mock.Mock()

    def __call__(self, requests, *, model, on_batch, **kwargs):
        with self._lock:
            batch_results = next(self._results[model])
            self._recorder(requests, model=model, on_batch=on_batch, **kwargs)
        batch_size = resolve_judge_seat_profile(kwargs["seat"]).batch_size
        for index in range(0, len(requests), batch_size):
            on_batch(
                requests[index : index + batch_size],
                batch_results[index : index + batch_size],
            )
        return batch_results

    @property
    def call_count(self):
        return self._recorder.call_count

    @property
    def call_args_list(self):
        return self._recorder.call_args_list

    def assert_not_called(self):
        self._recorder.assert_not_called()


def mock_request_verdicts(batches):
    return MockRequestVerdicts(batches)


class JudgeRetryBudgetTest(SimpleTestCase):
    def test_spend_never_exceeds_the_shared_maximum(self) -> None:
        barrier = threading.Barrier(2)

        class SynchronizedCounter(int):
            def __iadd__(self, other):
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait(timeout=0.1)
                return int.__add__(self, other)

        budget = RetryBudget(maximum=1, used=SynchronizedCounter(0))
        results = []
        workers = [
            threading.Thread(target=lambda: results.append(budget.spend()))
            for _ in range(2)
        ]

        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(budget.used, 1)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeSeatConnectionCleanupTest(TransactionTestCase):
    def _run_seats(self, worker_error=None):
        request = JudgeRequest(
            unit_key="cleanup",
            source="source",
            target="target",
            source_language="en",
            target_language="cs",
            note="",
            explanation="",
            glossary_terms=(),
            project_id_snapshot=1,
            component_id_snapshot=1,
            component_slug="cleanup",
        )
        caller_id = threading.get_ident()
        barrier = threading.Barrier(2, timeout=5)
        worker_ids = set()
        wrappers = []
        persisted_ids = []
        attempt_ids = []
        finished = set()
        lock = threading.Lock()

        def persist(batch_requests, batch_results):
            persisted_ids.append(threading.get_ident())

        def request_verdicts(requests, *, model, on_batch, seat, **kwargs):
            try:
                wrapper = connections["default"]
                profile = resolve_judge_seat_profile(seat)
                attempt = JudgeRequestAttempt.objects.create(
                    seat=seat,
                    endpoint_fingerprint=profile.endpoint_fingerprint,
                    model=profile.model,
                    profile_fingerprint=profile.profile_fingerprint,
                    prompt_schema_version=profile.prompt_schema_version,
                    batch_digest=f"{seat:064d}",
                    batch_size=1,
                )
                _write_llm_usage(
                    {
                        "id": f"cleanup-{seat}",
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                    profile.provider,
                    model,
                    "judge-seat-cleanup",
                    [request],
                    attempt,
                )
                with lock:
                    worker_ids.add(threading.get_ident())
                    wrappers.append(wrapper)
                    attempt_ids.append(attempt.pk)
                barrier.wait()
                if worker_error is not None and seat == 2:
                    raise worker_error
                on_batch(requests, [PASS])
                return [PASS]
            finally:
                with lock:
                    finished.add(seat)

        jobs = [
            _SeatJob(
                seat=seat,
                model=f"vendor-{seat}/model",
                requests=[request],
                persist=persist,
                run=None,
                retry_budget=RetryBudget(),
                attempt=0,
                retry_deadline=None,
            )
            for seat in (1, 2)
        ]
        with mock.patch(
            "weblate.trans.judge_loop.request_verdicts",
            new=request_verdicts,
        ):
            if worker_error is None:
                _run_seats(
                    jobs,
                    project_slug="judge-seat-cleanup",
                    project_context="",
                    run_id=uuid.uuid4(),
                )
            else:
                with self.assertRaisesRegex(RuntimeError, str(worker_error)):
                    _run_seats(
                        jobs,
                        project_slug="judge-seat-cleanup",
                        project_context="",
                        run_id=uuid.uuid4(),
                    )
        return {
            "attempt_ids": attempt_ids,
            "caller_id": caller_id,
            "finished": finished,
            "persisted_ids": persisted_ids,
            "expected_persisted": 2 if worker_error is None else 1,
            "worker_ids": worker_ids,
            "wrappers": wrappers,
        }

    def assert_worker_connections_closed(self, outcome) -> None:
        self.assertEqual(len(outcome["worker_ids"]), 2)
        self.assertNotIn(outcome["caller_id"], outcome["worker_ids"])
        self.assertEqual(outcome["finished"], {1, 2})
        self.assertEqual(
            set(outcome["persisted_ids"]),
            {outcome["caller_id"]},
        )
        self.assertEqual(
            len(outcome["persisted_ids"]),
            outcome["expected_persisted"],
        )
        self.assertTrue(
            all(wrapper.connection is None for wrapper in outcome["wrappers"])
        )
        self.assertEqual(
            JudgeRequestAttempt.objects.filter(pk__in=outcome["attempt_ids"]).count(),
            2,
        )
        self.assertEqual(
            LLMUsageLog.objects.filter(
                project_slug="judge-seat-cleanup",
                request_attempt_id__in=outcome["attempt_ids"],
            ).count(),
            2,
        )

    def test_workers_close_database_connections_after_success(self) -> None:
        self.assert_worker_connections_closed(self._run_seats())

    def test_workers_close_database_connections_after_failure(self) -> None:
        self.assert_worker_connections_closed(self._run_seats(RuntimeError("worker")))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
)
class JudgeLoopTest(ViewTestCase):
    def enable_repair_engine(self) -> None:
        # The routed repair engine resolves the candidate metadata engine.
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])

    def run_batch(self, seat_results, repair=None, writable=True):
        # seat_results: list of results, consumed in order, one per call.
        client = mock_request_verdicts([[result] for result in seat_results])
        unit = self.get_unit()
        writable_ids = {unit.id} if writable else set()
        repair_mock = mock.Mock(
            return_value={} if repair is None else {unit.id: repair}
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            verdicts = run_judge_batch(
                [unit], writable_ids=writable_ids, user=self.user
            )
        return unit, verdicts[unit.id], client

    def test_both_seats_judge_every_string(self) -> None:
        unit, verdict, client = self.run_batch([PASS, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_both_seats_are_asked_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=1)
        worker_ids = []
        worker_lock = threading.Lock()
        unit = self.get_unit()

        def request(requests, *, on_batch, **kwargs):
            with worker_lock:
                worker_ids.append(threading.get_ident())
            barrier.wait()
            results = [PASS] * len(requests)
            on_batch(requests, results)
            return results

        with mock.patch("weblate.trans.judge_loop.request_verdicts", new=request):
            run_judge_batch([unit], writable_ids=set(), user=self.user)

        self.assertEqual(len(set(worker_ids)), 2)
        self.assertNotIn(threading.get_ident(), worker_ids)

    def test_verdicts_and_progress_run_on_the_calling_thread(self) -> None:
        caller_id = threading.get_ident()
        worker_ids = set()
        write_ids = set()
        progress_ids = set()
        original_write = _write_verdict
        unit = self.get_unit()

        def request(requests, *, on_batch, **kwargs):
            worker_ids.add(threading.get_ident())
            results = [PASS] * len(requests)
            on_batch(requests, results)
            return results

        def write_verdict(*args, **kwargs):
            write_ids.add(threading.get_ident())
            return original_write(*args, **kwargs)

        def progress(*args, **kwargs):
            progress_ids.add(threading.get_ident())

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", new=request),
            mock.patch(
                "weblate.trans.judge_loop._write_verdict",
                side_effect=write_verdict,
            ),
        ):
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                on_batch=progress,
            )

        self.assertEqual(write_ids, {caller_id})
        self.assertEqual(progress_ids, {caller_id})
        self.assertEqual(len(worker_ids), 2)
        self.assertNotIn(caller_id, worker_ids)

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=1, JUDGE_BATCH_SIZE_SEAT_2=2)
    def test_each_seat_advances_in_lockstep_and_keeps_order_and_model(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        first_batch_ready = threading.Event()
        seat_two_persisted = threading.Event()
        seat_one_advanced_early = threading.Event()
        original_write = _write_verdict

        def write_verdict(*args, **kwargs):
            verdict = original_write(*args, **kwargs)
            if kwargs["seat"] == 2:
                seat_two_persisted.set()
            return verdict

        def request(requests, *, on_batch, seat, **kwargs):
            if seat == 1:
                first_batch_ready.set()
                on_batch(requests[:1], [MAJOR])
                if not seat_two_persisted.is_set():
                    seat_one_advanced_early.set()
                on_batch(requests[1:], [PASS])
                return [MAJOR, PASS]
            first_batch_ready.wait(timeout=1)
            on_batch(requests, [CRITICAL, PASS])
            return [CRITICAL, PASS]

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", new=request),
            mock.patch(
                "weblate.trans.judge_loop._write_verdict",
                side_effect=write_verdict,
            ),
        ):
            run_judge_batch(
                [first, second],
                writable_ids=set(),
                user=self.user,
            )

        self.assertFalse(seat_one_advanced_early.is_set())
        self.assertEqual(
            set(
                JudgeVerdict.objects.filter(unit__in=[first, second]).values_list(
                    "unit_id",
                    "seat",
                    "judge_model",
                    "max_severity",
                )
            ),
            {
                (first.id, 1, "vendor-a/model", "major"),
                (first.id, 2, "vendor-b/model", "critical"),
                (second.id, 1, "vendor-a/model", "none"),
                (second.id, 2, "vendor-b/model", "none"),
            },
        )

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=1, JUDGE_BATCH_SIZE_SEAT_2=1)
    def test_a_failing_seat_keeps_the_other_seats_completed_verdicts(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        first_persisted = threading.Event()
        seat_two_started = threading.Event()
        batch_attempts = {1: 0, 2: 0}
        finished = set()
        finished_lock = threading.Lock()
        original_write = _write_verdict

        def write_verdict(*args, **kwargs):
            verdict = original_write(*args, **kwargs)
            if kwargs["seat"] == 1:
                first_persisted.set()
            return verdict

        def request(requests, *, on_batch, seat, **kwargs):
            try:
                if seat == 1:
                    batch_attempts[seat] += 1
                    on_batch(requests[:1], [PASS])
                    seat_two_started.wait(timeout=1)
                    batch_attempts[seat] += 1
                    on_batch(requests[1:], [PASS])
                    return [PASS, PASS]
                seat_two_started.set()
                batch_attempts[seat] += 1
                first_persisted.wait(timeout=1)
                msg = "simulated worker failure"
                raise RuntimeError(msg)
            finally:
                with finished_lock:
                    finished.add(seat)

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", new=request),
            mock.patch(
                "weblate.trans.judge_loop._write_verdict",
                side_effect=write_verdict,
            ),
            self.assertRaisesRegex(RuntimeError, "simulated worker failure"),
        ):
            run_judge_batch(
                [first, second],
                writable_ids=set(),
                user=self.user,
            )

        self.assertEqual(batch_attempts, {1: 1, 2: 1})
        self.assertEqual(finished, {1, 2})
        self.assertEqual(
            JudgeVerdict.objects.filter(unit__in=[first, second], seat=1).count(),
            1,
        )

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=1, JUDGE_BATCH_SIZE_SEAT_2=1)
    def test_caller_failure_stops_seats_after_their_in_flight_batch(self) -> None:
        for failure in ("persistence", "progress"):
            with self.subTest(failure=failure):
                self.assert_caller_failure(failure)

    def assert_caller_failure(self, failure) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        run = JudgeRun.objects.create(
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(first.translation_id),
            scope_label=str(first.translation),
            scope_path=first.translation.get_absolute_url(),
            requested_mode="judge",
            cap=2,
        )
        barrier = threading.Barrier(2, timeout=1)
        batch_attempts = {1: 0, 2: 0}
        finished = set()
        finished_lock = threading.Lock()
        caller_error = RuntimeError(f"{failure} failure")
        failed = False
        original_write = _write_verdict

        def request(requests, *, on_batch, seat, **kwargs):
            try:
                barrier.wait()
                batch_attempts[seat] += 1
                on_batch(requests[:1], [PASS])
                batch_attempts[seat] += 1
                on_batch(requests[1:], [PASS])
                return [PASS, PASS]
            finally:
                with finished_lock:
                    finished.add(seat)

        def write_verdict(*args, **kwargs):
            nonlocal failed
            if failure == "persistence" and not failed:
                failed = True
                raise caller_error
            return original_write(*args, **kwargs)

        def progress(*args, **kwargs):
            nonlocal failed
            if failure == "progress" and not failed:
                failed = True
                raise caller_error

        with (
            mock.patch(
                "weblate.trans.judge_loop.request_verdicts",
                new=request,
            ),
            mock.patch(
                "weblate.trans.judge_loop._write_verdict",
                side_effect=write_verdict,
            ),
            self.assertRaisesRegex(RuntimeError, str(caller_error)),
        ):
            run_judge_batch(
                [first, second],
                writable_ids=set(),
                user=self.user,
                on_batch=progress,
                use_cache=False,
                run=run,
            )

        self.assertEqual(batch_attempts, {1: 1, 2: 1})
        self.assertEqual(finished, {1, 2})
        self.assertEqual(
            JudgeVerdict.objects.filter(run_id=run.id).count(),
            1 if failure == "persistence" else 2,
        )

    @override_settings(
        JUDGE_MODEL_SEAT_1="vendor/model",
        JUDGE_MODEL_SEAT_2="vendor/model",
    )
    def test_equal_model_ids_are_supported(self) -> None:
        barrier = threading.Barrier(2, timeout=1)
        unit = self.get_unit()

        def request(requests, *, on_batch, **kwargs):
            barrier.wait()
            results = [PASS] * len(requests)
            on_batch(requests, results)
            return results

        with mock.patch("weblate.trans.judge_loop.request_verdicts", new=request):
            run_judge_batch([unit], writable_ids=set(), user=self.user)

        self.assertEqual(
            set(unit.judge_verdicts.values_list("seat", "judge_model", "max_severity")),
            {
                (1, "vendor/model", "none"),
                (2, "vendor/model", "none"),
            },
        )

    def test_shared_judge_run_allocates_request_rounds_monotonically(self) -> None:
        unit = self.get_unit()
        run = JudgeRun.objects.create(
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(unit.translation_id),
            scope_label=str(unit.translation),
            scope_path=unit.translation.get_absolute_url(),
            requested_mode="judge",
            cap=1,
        )
        client = mock_request_verdicts([[PASS], [PASS], [PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                run=run,
                use_cache=False,
            )
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                run=run,
                use_cache=False,
            )

        rows = list(unit.judge_verdicts.order_by("request_round", "seat"))
        self.assertEqual({row.run_id for row in rows}, {run.id})
        self.assertEqual([row.request_round for row in rows], [0, 0, 1, 1])
        self.assertEqual(
            unit.judge_verdicts.first().target_storage_hash,
            compute_target_storage_hash(unit.target),
        )

    def test_cache_invalidation_failure_does_not_erase_verdicts(self) -> None:
        unit = self.get_unit()
        with mock.patch(
            "weblate.trans.models.translation.Translation.invalidate_cache",
            side_effect=RuntimeError("cache unavailable"),
        ):
            self.run_batch([PASS, PASS])
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_verdict_stores_raw_target_hash(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS])
        expected = compute_target_storage_hash(unit.target)
        self.assertTrue(
            all(
                verdict.target_storage_hash == expected
                for verdict in unit.judge_verdicts.all()
            )
        )

    def test_verdict_hashes_request_target_snapshot(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        unit.target = "newer target"
        _write_verdict(
            unit,
            request,
            seat=1,
            attempt=0,
            run_id=uuid.uuid4(),
            result=PASS,
            profile=resolve_judge_seat_profile(1),
            project_context="",
        )
        self.assertEqual(
            unit.judge_verdicts.get().target_storage_hash,
            compute_target_storage_hash(request.target),
        )

    def test_request_carries_source_explanation(self) -> None:
        unit = self.get_unit()
        unit.source_unit.explanation = "Shown on the locked-door screen."
        unit.source_unit.save(update_fields=["explanation"])

        request = build_request(unit)

        self.assertEqual(request.explanation, "Shown on the locked-door screen.")

    def test_source_explanation_change_aborts_repair(self) -> None:
        unit = self.get_unit()
        original = unit.target
        client = mock.Mock(side_effect=[[MAJOR], [MAJOR]])

        def change_context(units, _user):
            unit.source_unit.explanation = "Changed while the Judge was running."
            unit.source_unit.save(update_fields=["explanation"])
            return {unit.id: ["must not be applied"]}

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets", side_effect=change_context
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertNotIn(unit.id, verdicts)
        self.assertEqual(self.get_unit().target, original)

    def test_run_and_seat_are_logged(self) -> None:
        with self.assertLogs("weblate.trans.judge_loop", level="INFO") as logs:
            self.run_batch([PASS, PASS])
        joined = "\n".join(logs.output)
        self.assertIn("judge run", joined)
        self.assertIn("seat", joined)

    def make_openrouter(self, engine):
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        return mock.patch("weblate.trans.judge_loop.MACHINERY", {"openrouter": engine})

    def test_selection_takes_the_best_candidate_per_plural_form(self) -> None:
        unit = self.get_unit()
        self.assertEqual(
            _select_repair_texts(
                unit,
                [
                    [
                        {"text": "lower quality", "quality": 50},
                        {"text": "fixed text", "quality": 100},
                    ]
                ],
            ),
            ["fixed text"],
        )

    def test_repair_targets_asks_a_batch_engine_once(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["units"], [unit])
        self.assertEqual(
            fetch.call_args.kwargs["threshold"], MACHINERY_DEFAULT_THRESHOLD
        )
        engine.return_value.translate.assert_not_called()

    def test_repair_targets_skips_a_unit_without_a_usable_candidate(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": [""],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_skips_a_result_whose_lists_disagree(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text", "extra form"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_keeps_one_request_per_unit_for_a_single_string_engine(
        self,
    ) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 1
        engine.return_value.translate.return_value = [
            [{"text": "fixed text", "quality": 100}]
        ]
        fetch = mock.Mock()
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_not_called()
        engine.return_value.translate.assert_called_once()

    def test_repair_targets_uses_litellm_when_only_litellm_is_configured(
        self,
    ) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 1
        engine.return_value.translate.return_value = [
            [{"text": "litellm fix", "quality": 100}]
        ]
        self.component.project.machinery_settings = {"litellm": {"key": "ll-key"}}
        self.component.project.save(update_fields=["machinery_settings"])
        with mock.patch("weblate.trans.judge_loop.MACHINERY", {"litellm": engine}):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["litellm fix"]}
            )

    def test_repair_targets_prefers_openrouter_when_both_are_configured(
        self,
    ) -> None:
        unit = self.get_unit()
        openrouter_engine = mock.Mock()
        openrouter_engine.return_value.batch_size = 1
        openrouter_engine.return_value.translate.return_value = [
            [{"text": "openrouter fix", "quality": 100}]
        ]
        litellm_engine = mock.Mock()
        self.component.project.machinery_settings = {
            "openrouter": {"key": "or-key"},
            "litellm": {"key": "ll-key"},
        }
        self.component.project.save(update_fields=["machinery_settings"])
        with mock.patch(
            "weblate.trans.judge_loop.MACHINERY",
            {"openrouter": openrouter_engine, "litellm": litellm_engine},
        ):
            self.assertEqual(
                repair_targets([unit], self.user),
                {unit.id: ["openrouter fix"]},
            )
        litellm_engine.assert_not_called()

    def test_no_seat_may_lower_the_other(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_the_same_holds_when_the_strict_seat_votes_second(self) -> None:
        _, verdict, _ = self.run_batch([PASS, MAJOR])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_verdict_takes_the_higher_severity(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_flag_stores_a_candidate_without_a_second_round(self) -> None:
        # The flagged round generates one candidate and ends: the judged
        # text is never mutated, so there is nothing to re-judge.
        self.enable_repair_engine()
        original = self.get_unit().target
        unit, verdict, client = self.run_batch([MAJOR, MAJOR], repair=["fixed text"])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.attempt, 0)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(self.get_unit().target, original)
        candidate = unit.suggestion_set.get(userdetails__kind="judge-repair")
        self.assertEqual(candidate.target.strip(), "fixed text")

    def test_one_flag_round_ends_after_storing_the_candidate(self) -> None:
        self.enable_repair_engine()
        unit, verdict, client = self.run_batch([MAJOR, MAJOR], repair=["still wrong"])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.attempt, 0)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(unit.suggestion_set.count(), 1)

    def test_repair_fetch_failure_does_not_crash_the_batch(self) -> None:
        # 2026-08-25 judge-repair-loop measurement: a malformed producer reply
        # must not lose the verdicts that both seats already wrote.
        unit = self.get_unit()
        original_target = unit.target
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        client = mock_request_verdicts([[MAJOR], [MAJOR]])
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.fetch_machinery_matches",
                side_effect=MachineTranslationError("boom"),
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(self.get_unit().target, original_target)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_a_negative_round_fetches_every_candidate_in_one_call(self) -> None:
        self.enable_repair_engine()
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        first_target = first.target
        repair_mock = mock.Mock(
            return_value={
                first.id: ["first repaired target"],
                second.id: ["second repaired target"],
            }
        )
        client = mock_request_verdicts(
            [
                [MAJOR, MAJOR],
                [MAJOR, MAJOR],
            ]
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        repair_mock.assert_called_once()
        self.assertEqual(
            [unit.id for unit in repair_mock.call_args.args[0]],
            [first.id, second.id],
        )
        first.refresh_from_db()
        second.refresh_from_db()
        # One paid generation for both, no re-judge round, no mutation.
        self.assertEqual(client.call_count, 2)
        self.assertEqual(first.target, first_target)
        self.assertEqual(second.target, "second original target")
        self.assertEqual(
            {verdicts.repair_status[pk] for pk in (first.id, second.id)},
            {"candidate-stored"},
        )
        self.assertEqual(
            first.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "first repaired target",
        )
        self.assertEqual(
            second.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "second repaired target",
        )

    def test_a_missing_candidate_output_leaves_its_sibling_final(self) -> None:
        self.enable_repair_engine()
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        original_first_target = first.target
        original_second_target = second.target
        repair_mock = mock.Mock(return_value={first.id: ["first repaired target"]})
        client = mock_request_verdicts(
            [
                [MAJOR, MAJOR],
                [MAJOR, MAJOR],
            ]
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        repair_mock.assert_called_once()
        self.assertEqual(client.call_count, 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, original_first_target)
        self.assertEqual(second.target, original_second_target)
        self.assertEqual(verdicts[second.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdicts.repair_status[first.id], "candidate-stored")
        self.assertEqual(verdicts.repair_status[second.id], "no-candidate")
        self.assertEqual(second.judge_verdicts.count(), 2)

    def test_a_failed_repair_batch_leaves_every_unit_final(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        originals = {first.id: first.target, second.id: second.target}
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(side_effect=MachineTranslationError("not text"))
        client = mock_request_verdicts([[MAJOR, MAJOR], [MAJOR, MAJOR]])
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        fetch.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, originals[first.id])
        self.assertEqual(second.target, originals[second.id])
        self.assertEqual(
            {verdicts[first.id].verdict, verdicts[second.id].verdict},
            {JudgeVerdict.Verdict.FLAG},
        )
        self.assertEqual(first.judge_verdicts.count(), 2)
        self.assertEqual(second.judge_verdicts.count(), 2)

    def test_a_partial_candidate_fetch_stores_only_answered_units(self) -> None:
        # The repair call answers only the first unit. The new semantics do
        # not re-judge a candidate, so the round ends after one fetch: the
        # answered unit gets a stored candidate, its sibling stays final.
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        original_first_target = first.target
        original_second_target = second.target
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                first.id: {
                    "translation": ["first repaired target"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        client = mock_request_verdicts(
            [
                [MAJOR, MAJOR],
                [MAJOR, MAJOR],
            ]
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        fetch.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, original_first_target)
        self.assertEqual(second.target, original_second_target)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(verdicts[second.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(
            first.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "first repaired target",
        )
        self.assertFalse(
            second.suggestion_set.filter(userdetails__kind="judge-repair").exists()
        )

    def test_each_seat_uses_its_configured_model(self) -> None:
        unit, _, client = self.run_batch([PASS, PASS])
        models = [call.kwargs["model"] for call in client.call_args_list]
        self.assertEqual(models, ["vendor-a/model", "vendor-b/model"])
        self.assertEqual(
            set(unit.judge_verdicts.values_list("seat", "judge_model")),
            {(1, "vendor-a/model"), (2, "vendor-b/model")},
        )

    def test_every_seat_bills_the_units_project(self) -> None:
        # Without this the paid judge requests land in LLMUsageLog with a
        # blank project and llm_usage_report cannot attribute the spend.
        unit, _, client = self.run_batch([PASS, PASS])
        slugs = {c.kwargs["project_slug"] for c in client.call_args_list}
        self.assertEqual(slugs, {unit.translation.component.project.slug})

    def test_request_carries_the_units_scope_identity(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        self.assertEqual(request.project_id_snapshot, self.component.project_id)
        self.assertEqual(request.component_id_snapshot, self.component.pk)
        self.assertEqual(request.component_slug, self.component.slug)
        self.assertEqual(request.target_language, unit.translation.language.code)

    def test_every_seat_gets_the_projects_own_context(self) -> None:
        # The judge must describe the game from the same configuration the
        # translator uses, or it argues from a setting nobody configured.
        project = self.component.project
        project.machinery_settings = {
            "openrouter": {
                "persona": "You judge a dark fantasy game.",
                "style": "Preserve profanity.",
            }
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(
            contexts, {"You judge a dark fantasy game.\n\nPreserve profanity."}
        )

    def test_litellm_only_project_still_gets_its_own_context(self) -> None:
        project = self.component.project
        project.machinery_settings = {
            "litellm": {
                "persona": "You judge a sci-fi game.",
                "style": "Keep it terse.",
            }
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {"You judge a sci-fi game.\n\nKeep it terse."})

    def test_openrouter_context_wins_when_both_engines_are_configured(self) -> None:
        project = self.component.project
        project.machinery_settings = {
            "openrouter": {"persona": "OpenRouter persona."},
            "litellm": {"persona": "LiteLLM persona."},
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {"OpenRouter persona."})

    def test_an_unconfigured_project_sends_no_context(self) -> None:
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {""})

    def test_unparsed_neither_raises_nor_lowers_the_other_seat(self) -> None:
        _, verdict, _ = self.run_batch([CRITICAL, DEAD], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_clean_seat_next_to_an_unparsed_one_still_passes(self) -> None:
        _, verdict, _ = self.run_batch([PASS, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)

    def test_unparsed_from_both_seats_is_unparsed(self) -> None:
        _, verdict, _ = self.run_batch([DEAD, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)

    def test_current_all_unparsed_round_does_not_repair_from_history(self) -> None:
        unit = self.get_unit()
        old_request = build_request(unit)
        old_run = uuid.uuid4()
        for seat, model in enumerate(("vendor-a/model", "vendor-b/model"), start=1):
            JudgeVerdict.objects.create(
                unit=unit,
                max_severity="critical",
                model_verdict="reject",
                judge_model=model,
                seat=seat,
                run_id=old_run,
                target_hash=compute_target_hash(old_request.target_plurals),
                context_hash=compute_context_hash(
                    source=old_request.source,
                    note=old_request.note,
                    explanation=old_request.explanation,
                    glossary_terms=old_request.glossary_terms,
                ),
            )
        client = mock_request_verdicts([[DEAD], [DEAD]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop._cached_verdict", return_value=None),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={},
            ) as repair,
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.UNPARSED)
        repair.assert_not_called()

    def test_historical_verdict_without_metadata_is_not_reused(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        run = uuid.uuid4()
        for seat, model in enumerate(("vendor-a/model", "vendor-b/model"), start=1):
            JudgeVerdict.objects.create(
                unit=unit,
                max_severity="none",
                model_verdict="pass",
                judge_model=model,
                seat=seat,
                run_id=run,
                target_hash=compute_target_hash(request.target_plurals),
                context_hash=compute_context_hash(
                    source=request.source,
                    note=request.note,
                    explanation=request.explanation,
                    glossary_terms=request.glossary_terms,
                ),
            )
        client = mock_request_verdicts([[PASS], [PASS]])
        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            verdicts = run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)

    def test_newer_unparsed_evidence_disables_cache_reuse(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        parsed = unit.judge_verdicts.get(seat=1)
        JudgeVerdict.objects.create(
            unit=unit,
            model_verdict=JudgeVerdict.Verdict.UNPARSED,
            max_severity="none",
            unparsed=True,
            judge_model=parsed.judge_model,
            seat=parsed.seat,
            attempt=parsed.attempt,
            request_round=parsed.request_round,
            target_hash=parsed.target_hash,
            target_storage_hash=parsed.target_storage_hash,
            context_hash=parsed.context_hash,
            request_identity=parsed.request_identity,
            project_context_hash=parsed.project_context_hash,
            source_language=parsed.source_language,
            target_language=parsed.target_language,
            profile_fingerprint=parsed.profile_fingerprint,
            prompt_schema_version=parsed.prompt_schema_version,
        )
        client = mock_request_verdicts([[PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            run_judge_batch([unit], writable_ids=set(), user=self.user)

        self.assertEqual(client.call_count, 2)

    def test_profile_change_invalidates_cached_verdict(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        client = mock_request_verdicts([[PASS], [PASS]])
        with (
            override_settings(JUDGE_TEMPERATURE_SEAT_1=0.25),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
        ):
            run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(client.call_count, 2)

    def test_prompt_schema_change_invalidates_cached_verdict(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        client = mock_request_verdicts([[PASS], [PASS]])
        with (
            mock.patch(
                "weblate.trans.judge._prompt_schema_version",
                return_value="changed-prompt-schema",
            ),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
        ):
            run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(client.call_count, 2)

    def test_verdict_stores_request_attempt_and_empty_instruction(self) -> None:
        unit = self.get_unit()
        profile = resolve_judge_seat_profile(1)
        request = build_request(unit)
        attempt = JudgeRequestAttempt.objects.create(
            seat=1,
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            profile_fingerprint=profile.profile_fingerprint,
            prompt_schema_version=profile.prompt_schema_version,
            batch_digest="a" * 64,
            batch_size=1,
        )
        _write_verdict(
            unit,
            request,
            seat=1,
            attempt=0,
            run_id=uuid.uuid4(),
            result=JudgeResult(
                "none",
                "pass",
                [],
                "",
                instruction="must not persist",
                request_attempt_id=attempt.pk,
            ),
            profile=profile,
            project_context="",
        )
        verdict = unit.judge_verdicts.get()
        self.assertEqual(verdict.request_attempt_id, attempt.pk)
        self.assertEqual(verdict.instruction, "")

    def test_confirmed_defect_stores_a_candidate_without_rewriting(self) -> None:
        # A confirmed defect gets one candidate, not an applied rewrite:
        # the round ends there and a human accepts it through the UI.
        self.enable_repair_engine()
        original = self.get_unit().target
        unit, verdict, client = self.run_batch(
            [CRITICAL, CRITICAL], repair=["fixed text"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(verdict.attempt, 0)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(self.get_unit().target, original)
        self.assertEqual(
            unit.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "fixed text",
        )

    def test_one_negative_round_ends_after_storing_the_candidate(self) -> None:
        self.enable_repair_engine()
        _, verdict, client = self.run_batch(
            [CRITICAL, CRITICAL], repair=["still wrong"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(client.call_count, 2)

    def test_repair_that_changes_nothing_stops_the_loop(self) -> None:
        _, _verdict, client = self.run_batch([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(client.call_count, 2)

    # -- review remediation: candidate storage must not lie or overpay ------

    def _candidates(self, unit):
        return unit.suggestion_set.filter(userdetails__kind="judge-repair")

    def test_a_refused_repair_is_not_audited_as_a_stored_candidate(self) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        unit.translate(self.user, ["Identical target"], STATE_TRANSLATED)
        unit = self.get_unit()
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        repair_mock = mock.Mock(return_value={unit.id: ["Identical target"]})
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            verdicts = run_judge_batch([unit], writable_ids=set(), user=self.user)
        # The manager refuses a repair identical to the live target, so no
        # row exists and the audit must not claim one.
        self.assertEqual(self._candidates(unit).count(), 0)
        self.assertEqual(verdicts.repair_status[unit.id], "no-candidate")
        # A refusal is not drift: the critical verdict still stands.
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_fresh_verdict_rebinds_an_identical_candidate(self) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        first_client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        repair_mock = mock.Mock(return_value={unit.id: ["same repair text"]})
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", first_client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            first = run_judge_batch([unit], writable_ids=set(), user=self.user)
        first_verdict = first[unit.id]
        second_client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", second_client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
            mock.patch("weblate.trans.judge_loop._cached_verdict", return_value=None),
        ):
            second = run_judge_batch(
                [self.get_unit()], writable_ids=set(), user=self.user
            )
        second_verdict = second[unit.id]
        self.assertNotEqual(second_verdict.pk, first_verdict.pk)
        # Byte-identical repair text must still be rebound to the current
        # verdict, otherwise the producer sees no candidate at all.
        self.assertEqual(self._candidates(unit).count(), 1)
        self.assertIsNotNone(active_judge_candidate(self.get_unit(), second_verdict))

    def test_a_cached_negative_verdict_reuses_its_candidate(self) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        repair_mock = mock.Mock(return_value={unit.id: ["stored repair"]})
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            first = run_judge_batch([unit], writable_ids=set(), user=self.user)
        stored_verdict = first[unit.id]
        self.assertEqual(repair_mock.call_count, 1)
        second_client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", second_client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
            mock.patch(
                "weblate.trans.judge_loop._cached_verdict",
                return_value=stored_verdict,
            ),
        ):
            verdicts = run_judge_batch(
                [self.get_unit()], writable_ids=set(), user=self.user
            )
        # The verdict was reused from cache, so no seat was asked again and
        # the still-active candidate must not be regenerated either.
        second_client.assert_not_called()
        self.assertEqual(repair_mock.call_count, 1)
        self.assertEqual(self._candidates(unit).count(), 1)
        self.assertEqual(verdicts.repair_status[unit.id], "candidate-stored")

    def test_an_empty_candidate_severity_set_stores_nothing(self) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        repair_mock = mock.Mock(return_value={unit.id: ["drain repair"]})
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                candidate_severities=(),
            )
        # A read-only drain neither pays for a repair nor stores a preview.
        repair_mock.assert_not_called()
        self.assertEqual(self._candidates(unit).count(), 0)

    def test_active_max_length_stores_no_candidate_when_not_writable(self) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"])
        unit = self.get_unit()
        unit.translate(self.user, ["a much longer raw target"], STATE_TRANSLATED)
        unit = self.get_unit()
        unit.run_checks()
        self.assertTrue(_has_active_check(unit, "max-length"))
        repair_mock = mock.Mock(return_value={unit.id: ["also far too long"]})
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            run_judge_batch([unit], writable_ids=set(), user=self.user)
        # max-length precedence is absolute: a non-writable unit stays on the
        # deterministic path instead of falling through to a preview.
        repair_mock.assert_not_called()
        self.assertEqual(self._candidates(unit).count(), 0)

    def test_active_max_length_stores_no_candidate_without_mutating_repairs(
        self,
    ) -> None:
        self.enable_repair_engine()
        unit = self.get_unit()
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"])
        unit = self.get_unit()
        unit.translate(self.user, ["a much longer raw target"], STATE_TRANSLATED)
        unit = self.get_unit()
        unit.run_checks()
        repair_mock = mock.Mock(return_value={unit.id: ["still far too long"]})
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            run_judge_batch(
                [unit],
                writable_ids={unit.id},
                user=self.user,
                mutating_repairs=False,
                candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
            )
        # A producer re-check neither mutates nor stores while max-length is
        # active, exactly as _prepare_round_unit's comment promises.
        repair_mock.assert_not_called()
        self.assertEqual(self._candidates(unit).count(), 0)
        self.assertEqual(self.get_unit().target.strip(), "a much longer raw target")

    def test_repair_is_rolled_back_when_it_adds_a_deterministic_check(self) -> None:
        # Only the max-length path mutates the target; the rollback guard
        # belongs to that path now that REJECT stores a candidate instead.
        unit = self.change_unit("a much longer raw target")
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"], same_content=True)
        original = self.get_unit().target
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["new but invalid"]},
            ),
            mock.patch(
                "weblate.trans.judge_loop._deterministic_checks",
                side_effect=[set(), {"game-markup"}],
            ),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(self.get_unit().target, original)

    def test_rollback_is_recorded_in_the_batch_repair_status(self) -> None:
        unit = self.change_unit("a much longer raw target")
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"], same_content=True)
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["new but invalid"]},
            ),
            mock.patch(
                "weblate.trans.judge_loop._deterministic_checks",
                side_effect=[set(), {"game-markup"}],
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts.repair_status[unit.id], "rolled-back")

    def test_applied_repair_is_recorded_in_the_batch_repair_status(self) -> None:
        unit = self.change_unit("a much longer raw target")
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"], same_content=True)
        client = mock_request_verdicts([[CRITICAL], [CRITICAL], [PASS], [PASS]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["fixed"]},
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts.repair_status[unit.id], "applied")

    def test_a_human_string_is_not_repaired_when_not_writable(self) -> None:
        # D3/A3: overwrite off => the unit is not in writable_ids => a
        # false critical never rewrites the human translation. Candidates
        # are still offered for human review - they never touch the target.
        self.enable_repair_engine()
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        _, _verdict, _client = self.run_batch(
            [CRITICAL, CRITICAL], repair=["MACHINE OVERWRITE"], writable=False
        )
        self.assertNotEqual(self.get_unit().target, "MACHINE OVERWRITE")
        self.assertEqual(
            unit.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "MACHINE OVERWRITE",
        )

    def test_repair_sees_the_round_verdict_projected(self) -> None:
        # Ordering guard: run_checks() projects the round's Check row before
        # repair_targets builds its prompt from failing_checks.
        seen = []

        def spy(units, _user):
            seen.extend({check.name for check in unit.all_checks} for unit in units)
            return {unit.id: ["fixed text"] for unit in units}

        client = mock_request_verdicts(
            [[result] for result in (CRITICAL, CRITICAL, PASS, PASS)]
        )
        unit = self.get_unit()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", side_effect=spy),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(seen, [{"judge-reject"}])

    def test_every_verdict_of_one_run_shares_the_run_id(self) -> None:
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL], repair=["fixed"])
        self.assertEqual(
            len(set(unit.judge_verdicts.values_list("run_id", flat=True))), 1
        )

    def test_each_seat_votes_once_per_round(self) -> None:
        # The negative round ends with the stored candidate: one paid vote
        # per seat, no mutating re-judge attempt.
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL], repair=["fixed"])
        self.assertEqual(
            set(unit.judge_verdicts.values_list("attempt", "seat")),
            {(0, 1), (0, 2)},
        )

    def test_the_judges_own_projection_is_not_sent_back_as_evidence(self) -> None:
        # Observed live on dev 2026-08-20: a seat justified a major with
        # "the judge-flag check indicating non-conformance" - its own
        # previous opinion, handed back to it as proven fact.
        unit = self.get_unit()
        request = build_request(unit)
        JudgeVerdict.objects.create(
            unit=unit,
            run_id=uuid.uuid4(),
            attempt=0,
            seat=1,
            judge_model="vendor-a/model",
            max_severity="major",
            model_verdict="flag",
            target_hash=compute_target_hash(request.target_plurals),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            ),
        )
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-flag", unit.all_checks_names)
        self.assertNotIn("judge-flag", build_request(unit).failing_checks)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=1,
    JUDGE_RETRY_BUDGET_RATIO=1.0,
)
class JudgeUnparsedRetryRoundTest(ViewTestCase):
    def test_all_unparsed_round_is_retried_without_overwriting_history(self) -> None:
        unit = self.get_unit()
        client = mock_request_verdicts([[DEAD], [DEAD], [PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertEqual(client.call_count, 4)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.PASS)
        # Transport recovery uses a new request round without consuming a
        # repair-attempt coordinate.
        rows = list(unit.judge_verdicts.order_by("request_round", "seat"))
        self.assertEqual(len(rows), 4)
        self.assertEqual({row.attempt for row in rows}, {0})
        self.assertEqual(
            [row.request_round for row in rows],
            [0, 0, 1, 1],
        )
        self.assertTrue(all(row.unparsed for row in rows[:2]))
        self.assertTrue(all(not row.unparsed for row in rows[2:]))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
)
class JudgeGlossaryRepairLockTest(ViewTestCase):
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        super().setUp()
        glossary_component = self.project.glossaries[0]
        glossary = glossary_component.translation_set.get(
            language=self.translation.language
        )
        id_hash = calculate_hash("Hello", "")
        self.source_term = glossary_component.source_translation.unit_set.create(
            source="Hello",
            target="Hello",
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            explanation="A greeting, not a character name.",
        )
        glossary.unit_set.create(
            source="Hello",
            target="Ahoj",
            context="",
            source_unit=self.source_term,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        glossary.invalidate_cache()

    def test_glossary_explanation_change_aborts_candidate_storage(self) -> None:
        # Context drift during the repair call: the candidate is discarded
        # and the round's verdict is no longer trusted.
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        unit = self.get_unit()
        original = unit.target
        client = mock_request_verdicts([[MAJOR], [MAJOR]])

        def change_context(units, _user):
            self.source_term.explanation = "Changed while the judge was running."
            self.source_term.save(update_fields=["explanation"])
            return {unit.id: ["must not be applied"] for unit in units}

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets", side_effect=change_context
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertNotIn(unit.id, verdicts)
        self.assertEqual(self.get_unit().target, original)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeIncrementalPersistenceTest(ViewTestCase):
    def test_completed_request_batches_survive_a_mid_seat_crash(self) -> None:
        units = list(self.get_translation().unit_set.order_by("pk")[:2])
        self.assertEqual(len(units), 2)

        def crash(requests, *, on_batch=None, **kwargs):
            if on_batch is not None:
                on_batch(requests[:1], [PASS])
                on_batch(requests[1:], [PASS])
            msg = "simulated worker loss"
            raise RuntimeError(msg)

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", new=crash),
            self.assertRaisesRegex(RuntimeError, "simulated worker loss"),
        ):
            run_judge_batch(
                units,
                writable_ids={unit.id for unit in units},
                user=self.user,
            )

        self.assertEqual(
            JudgeVerdict.objects.filter(unit__in=units, seat=1).count(),
            2,
        )


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
)
class JudgeMaxLengthRepairTest(ViewTestCase):
    """An active `max-length` check keeps a PASS verdict repairable."""

    def run_gated_batch(
        self,
        seat_results,
        *,
        max_length: int,
        target: str,
        repair: list[str] | None = None,
        writable: bool = True,
    ):
        client = mock_request_verdicts([[result] for result in seat_results])
        unit = self.change_unit(target)
        unit.extra_flags = f"max-length:{max_length}"
        unit.save(update_fields=["extra_flags"], same_content=True)
        writable_ids = {unit.id} if writable else set()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={} if repair is None else {unit.id: repair},
            ) as repair_mock,
        ):
            verdicts = run_judge_batch(
                [unit], writable_ids=writable_ids, user=self.user
            )
        return self.get_unit(), verdicts[unit.id], client, repair_mock

    def test_pass_with_active_max_length_still_triggers_one_repair(self) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["short"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(repair_mock.call_count, 1)
        self.assertEqual(client.call_count, 4)

    def test_unparsed_never_triggers_repair_even_with_active_max_length(
        self,
    ) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [DEAD, DEAD],
            max_length=5,
            target="a much longer raw target",
            repair=["short"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(repair_mock.call_count, 0)
        self.assertEqual(client.call_count, 2)

    def test_repair_clearing_max_length_drops_the_check(self) -> None:
        unit, verdict, _client, _repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["tiny"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertFalse(unit.has_check("max-length"))

    def test_repair_leaving_max_length_exhausts_attempts_and_stays_fuzzy(
        self,
    ) -> None:
        unit, verdict, _client, repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["still much too long to fit the budget"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(repair_mock.call_count, 1)
        self.assertTrue(unit.has_check("max-length"))
        self.assertEqual(unit.state, STATE_FUZZY)

    def test_unit_without_max_length_retains_pass_does_not_repair(self) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [PASS, PASS], max_length=1000, target="short", repair=["unused"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(repair_mock.call_count, 0)
        self.assertEqual(client.call_count, 2)
