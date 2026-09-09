# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Task 4 of the mass-fix-failing-checks plan.

Covers the `fix-check-lock-*` concurrency guard
(`weblate.trans.fix_check.acquire_fix_check_lock`/`refresh_fix_check_lock`/
`release_fix_check_lock`) on both the LocMem and Redis cache backends, and
the `fix_failing_checks` Celery task's scope resolution and lifecycle
payloads.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django_redis import get_redis_connection

from weblate.trans import fix_check
from weblate.trans.fix_check import (
    acquire_fix_check_lock,
    fix_check_lock_key,
    refresh_fix_check_lock,
    release_fix_check_lock,
)
from weblate.trans.tasks import _release_fix_check_lock_reporting, fix_failing_checks
from weblate.trans.templatetags.translations import show_message
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.celery import add_user_task, get_user_tasks_key
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.state import STATE_TRANSLATED


class FixCheckLockKeyTest(SimpleTestCase):
    def test_lock_key_format(self) -> None:
        self.assertEqual(
            fix_check_lock_key("end_stop", "component", 42),
            "fix-check-lock-end_stop-component-42",
        )


class FixCheckLocMemLockTest(SimpleTestCase):
    """The degraded, non-Redis guard (Task 4 step 5, non-Redis branch)."""

    def setUp(self) -> None:
        super().setUp()
        self.key = fix_check_lock_key("end_stop", "component", 101)
        self.addCleanup(cache.delete, self.key)

    def test_acquire_refuses_second_submit(self) -> None:
        self.assertTrue(acquire_fix_check_lock(self.key, "task-a"))
        self.assertFalse(acquire_fix_check_lock(self.key, "task-b"))

    def test_refresh_always_reports_success(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertTrue(refresh_fix_check_lock(self.key, "task-a"))
        # No cross-process compare-and-set on this backend: even a foreign
        # token reports success, per the documented degraded contract.
        self.assertTrue(refresh_fix_check_lock(self.key, "task-b"))

    def test_release_never_deletes_the_key(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertTrue(release_fix_check_lock(self.key, "task-a"))
        # No early release: a second submit for the same scope still finds
        # the reservation held until it naturally expires.
        self.assertFalse(acquire_fix_check_lock(self.key, "task-b"))


@unittest.skipUnless("CI_REDIS_HOST" in os.environ, "Requires CI_REDIS_HOST")
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": (
                f"redis://{os.environ.get('CI_REDIS_HOST', '')}:"
                f"{os.environ.get('CI_REDIS_PORT', '6379')}/5"
            ),
        }
    }
)
class FixCheckRedisLockTest(SimpleTestCase):
    """The token-safe guard (Task 4 step 5, Redis branch)."""

    def setUp(self) -> None:
        super().setUp()
        self.key = fix_check_lock_key("end_stop", "component", 202)
        # The guard talks to the raw redis-py client (Task 4 step 5), not
        # Django's key-prefixed `cache` API, so inspection and cleanup use
        # the same raw connection the implementation itself uses.
        self.redis = get_redis_connection("default")
        self.addCleanup(self.redis.delete, self.key)

    def test_acquire_refuses_second_submit(self) -> None:
        self.assertTrue(acquire_fix_check_lock(self.key, "task-a"))
        self.assertFalse(acquire_fix_check_lock(self.key, "task-b"))

    def test_refresh_extends_the_lease_for_the_owning_token(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertTrue(refresh_fix_check_lock(self.key, "task-a"))
        self.assertEqual(self.redis.get(self.key), b"task-a")

    def test_refresh_rejects_a_stale_token_without_touching_the_lease(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertFalse(refresh_fix_check_lock(self.key, "task-b"))
        self.assertEqual(self.redis.get(self.key), b"task-a")
        ttl = self.redis.ttl(self.key)
        self.assertGreater(ttl, 0)

    def test_release_rejects_a_stale_token(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertFalse(release_fix_check_lock(self.key, "task-b"))
        self.assertEqual(self.redis.get(self.key), b"task-a")

    def test_release_deletes_the_key_for_the_owning_token(self) -> None:
        acquire_fix_check_lock(self.key, "task-a")
        self.assertTrue(release_fix_check_lock(self.key, "task-a"))
        self.assertIsNone(self.redis.get(self.key))


class FixFailingChecksTaskTest(ViewTestCase):
    """Task 4 steps 1-2: scope resolution and lifecycle payloads."""

    def setUp(self) -> None:
        super().setUp()
        self.end_stop_unit = self.get_unit(source="Thank you for using Weblate.")
        self.end_stop_unit.translate(self.user, "Dekuji", STATE_TRANSLATED)
        self.end_stop_unit.refresh_from_db()
        self.translation = self.get_translation()
        self.lock_key = fix_check_lock_key(
            "end_stop", "translation", self.translation.pk
        )

    def assert_counters_present(self, result: dict) -> None:
        """Task 4 step 2: every payload carries the five run counters."""
        for counter in (
            "fixed",
            "denied",
            "manual",
            "stale_or_no_change",
            "verdicts_no_longer_current",
        ):
            self.assertIn(counter, result)
            self.assertIsInstance(result[counter], int)

    def test_translation_scope_completes_and_releases_the_lock(self) -> None:
        self.assertTrue(acquire_fix_check_lock(self.lock_key, "task-1"))
        with patch("weblate.trans.tasks.release_fix_check_lock") as release_mock:
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["fixed"], 1)
        self.end_stop_unit.refresh_from_db()
        self.assertEqual(self.end_stop_unit.target, "Dekuji.")
        # A completed run releases its own reservation.
        release_mock.assert_called_once_with(self.lock_key, "")

    def test_component_scope_resolves_metadata(self) -> None:
        key = fix_check_lock_key("end_stop", "component", self.component.pk)
        result = fix_failing_checks.run(
            user_id=self.user.id,
            check_id="end_stop",
            lock_key=key,
            component_id=self.component.pk,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["fixed"], 1)

    def test_project_scope_resolves_metadata(self) -> None:
        key = fix_check_lock_key("end_stop", "project", self.project.pk)
        result = fix_failing_checks.run(
            user_id=self.user.id,
            check_id="end_stop",
            lock_key=key,
            project_id=self.project.pk,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["fixed"], 1)

    def test_missing_scope_returns_failed(self) -> None:
        # Task 4 step 2: a terminal failure is returned, never raised - a
        # raised exception becomes the Celery result and hides `status`.
        result = fix_failing_checks.run(
            user_id=self.user.id,
            check_id="end_stop",
            lock_key="x",
        )
        self.assertEqual(result["status"], "failed")
        self.assert_counters_present(result)

    def test_unknown_scope_object_returns_failed(self) -> None:
        for kwargs in (
            {"translation_id": -1},
            {"component_id": -1},
            {"project_id": -1},
            {"translation_id": self.translation.pk, "user_id": -1},
        ):
            with self.subTest(scope=kwargs):
                result = fix_failing_checks.run(
                    **{
                        "user_id": self.user.id,
                        "check_id": "end_stop",
                        "lock_key": self.lock_key,
                        **kwargs,
                    }
                )
                self.assertEqual(result["status"], "failed")
                self.assert_counters_present(result)

    def test_unit_ids_reaches_the_engine(self) -> None:
        result = fix_failing_checks.run(
            user_id=self.user.id,
            check_id="end_stop",
            lock_key=self.lock_key,
            translation_id=self.translation.pk,
            unit_ids=[self.end_stop_unit.pk],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["fixed"], 1)

    def test_ineligible_check_fails_without_running_and_releases_the_lock(
        self,
    ) -> None:
        self.assertTrue(acquire_fix_check_lock(self.lock_key, "task-1"))
        with patch("weblate.trans.tasks.release_fix_check_lock") as release_mock:
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="same",  # never tiered
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "failed")
        self.assert_counters_present(result)
        self.end_stop_unit.refresh_from_db()
        self.assertEqual(self.end_stop_unit.target, "Dekuji")
        release_mock.assert_called_once_with(self.lock_key, "")

    def test_lock_timeout_with_retries_remaining_refreshes_and_reraises(
        self,
    ) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=0), max_retries=3
        )
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch("weblate.trans.fix_check.perform_fix", side_effect=lock_timeout),
            patch("weblate.trans.tasks.refresh_fix_check_lock") as refresh,
            patch("weblate.trans.tasks.release_fix_check_lock") as release,
            self.assertRaises(WeblateLockTimeoutError),
        ):
            fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        refresh.assert_called_once_with(self.lock_key, "t1")
        release.assert_not_called()

    def test_lock_timeout_exhausted_reports_and_returns_without_raising(
        self,
    ) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=3), max_retries=3
        )
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch("weblate.trans.fix_check.perform_fix", side_effect=lock_timeout),
            patch("weblate.trans.tasks.report_error") as report_error_mock,
        ):
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "failed")
        self.assert_counters_present(result)
        report_error_mock.assert_called_once()
        # The lock was released: a fresh submit can acquire it again.
        self.assertTrue(acquire_fix_check_lock(self.lock_key, "task-2"))

    def test_unexpected_exception_reports_and_returns_failed(self) -> None:
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=0), max_retries=3
        )
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch(
                "weblate.trans.fix_check.perform_fix", side_effect=RuntimeError("boom")
            ),
            patch("weblate.trans.tasks.report_error") as report_error_mock,
        ):
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "failed")
        self.assert_counters_present(result)
        report_error_mock.assert_called_once()
        self.assertTrue(acquire_fix_check_lock(self.lock_key, "task-2"))

    def test_lock_refresh_failure_aborts_and_does_not_release(self) -> None:
        self.assertTrue(acquire_fix_check_lock(self.lock_key, "t1"))
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=0),
            max_retries=3,
            update_state=lambda **_kwargs: None,
        )
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch(
                "weblate.trans.tasks.refresh_fix_check_lock", return_value=False
            ) as refresh,
            patch("weblate.trans.tasks.release_fix_check_lock") as release,
        ):
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "failed")
        refresh.assert_called_once()
        release.assert_not_called()

    def test_lock_timeout_with_lost_lease_is_terminal(self) -> None:
        """A refused refresh on the retry path stops the run for good."""
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=0), max_retries=3
        )
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch("weblate.trans.fix_check.perform_fix", side_effect=lock_timeout),
            patch(
                "weblate.trans.tasks.refresh_fix_check_lock", return_value=False
            ) as refresh,
            patch("weblate.trans.tasks.release_fix_check_lock") as release,
        ):
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        # Returned, not raised: retrying would re-enter a scope another
        # run already owns, and the key is not ours to release.
        self.assertEqual(result["status"], "failed")
        self.assert_counters_present(result)
        refresh.assert_called_once_with(self.lock_key, "t1")
        release.assert_not_called()

    def test_progress_publishes_done_and_total_and_refreshes_the_lease(self) -> None:
        second = self.get_unit(source="Hello, world!\n")
        second.translate(self.user, "Ahoj svete!\n", STATE_TRANSLATED)
        states: list[dict] = []
        task = SimpleNamespace(
            request=SimpleNamespace(id="t1", retries=0),
            max_retries=3,
            update_state=lambda **kwargs: states.append(kwargs["meta"]),
        )
        with (
            patch("weblate.trans.tasks.current_task", task),
            patch(
                "weblate.trans.tasks.refresh_fix_check_lock", return_value=True
            ) as refresh,
        ):
            result = fix_failing_checks.run(
                user_id=self.user.id,
                check_id="end_stop",
                lock_key=self.lock_key,
                translation_id=self.translation.pk,
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(states)
        last = states[-1]
        # Task 4 step 1: an X / Y count, not only a percentage.
        self.assertEqual(last["done"], last["total"])
        self.assertGreaterEqual(last["total"], 1)
        self.assertEqual(last["progress"], 100)
        # The lease is re-armed on every progress tick, so a run longer
        # than the lease keeps its reservation.
        self.assertEqual(refresh.call_count, len(states))


class FixCheckLockReportingTest(SimpleTestCase):
    """Task 4 step 5: a lapsed lease is recorded, never retried."""

    def test_false_release_is_logged_once_and_not_retried(self) -> None:
        with (
            patch(
                "weblate.trans.tasks.release_fix_check_lock", return_value=False
            ) as release,
            self.assertLogs("weblate", level="WARNING") as logs,
        ):
            self.assertFalse(_release_fix_check_lock_reporting("key", "token"))
        release.assert_called_once_with("key", "token")
        self.assertEqual(len(logs.records), 1)

    def test_successful_release_is_silent(self) -> None:
        with patch(
            "weblate.trans.tasks.release_fix_check_lock", return_value=True
        ) as release:
            self.assertTrue(_release_fix_check_lock_reporting("key", "token"))
        release.assert_called_once_with("key", "token")


class FixCheckUserTaskFlashTest(SimpleTestCase):
    """The strict result mapping survives a page reload (Task 4 step 2)."""

    def test_status_contract_is_persisted_and_restored_as_a_tag(self) -> None:
        user_id = 4242
        key = get_user_tasks_key(user_id)
        cache.delete(key)
        self.addCleanup(cache.delete, key)
        add_user_task(
            user_id,
            "task-with-status",
            text="Mass fix queued.",
            label="scope",
            url="/",
            status_contract=True,
        )
        add_user_task(
            user_id,
            "ordinary-task",
            text="Something else queued.",
            label="scope",
            url="/",
        )
        stored = {task["id"]: task for task in cache.get(key)}
        self.assertTrue(stored["task-with-status"]["status_contract"])
        self.assertFalse(stored["ordinary-task"]["status_contract"])
        # `context_processors` turns that flag into the `task-status` tag,
        # which is what `show_message` converts into the strict poller
        # contract; without it a restored flash would fall back to the
        # generic mapping and render a failure as a success.
        self.assertTrue(show_message("info task:x task-status", "m")["task_status"])
        self.assertFalse(show_message("info task:x", "m")["task_status"])
        # The marker is consumed, never leaked as a CSS class.
        self.assertEqual(show_message("info task:x task-status", "m")["tags"], "info")


class FixCheckRedisScriptRegistrationTest(SimpleTestCase):
    """Task 4 step 5: each Lua script is registered once, not per tick."""

    def test_scripts_are_registered_once_per_source(self) -> None:
        registered: list[str] = []

        class FakeClient:
            def register_script(self, source: str):
                registered.append(source)
                return lambda *_args, **_kwargs: 1

        client = FakeClient()
        # The registration cache is module-private on purpose; this test
        # is what pins "registered once" and has to reach it.
        script_cache = fix_check._LUA_SCRIPTS  # ruff: ignore[private-member-access]
        script_cache.clear()
        self.addCleanup(script_cache.clear)
        with (
            patch("weblate.trans.fix_check.is_redis_cache", return_value=True),
            patch("weblate.trans.fix_check._get_redis_client", return_value=client),
        ):
            for _unused in range(5):
                self.assertTrue(refresh_fix_check_lock("key", "token"))
                self.assertTrue(release_fix_check_lock("key", "token"))
        # Two distinct sources (reacquire, release), registered once each.
        self.assertEqual(len(registered), 2)
        self.assertEqual(len(set(registered)), 2)
