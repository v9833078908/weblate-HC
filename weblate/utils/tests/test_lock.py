# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import multiprocessing
import os
import queue
import signal
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from django.db import DatabaseError, connections, transaction
from django.test import SimpleTestCase, TransactionTestCase
from filelock import FileLock

from weblate.trans.models import Component
from weblate.trans.tasks import JudgeExecutionGuardError, producer_execution_guard
from weblate.utils.lock import ComponentUpdateLock, WeblateLock, WeblateLockTimeoutError
from weblate.vcs.base import Repository, RepositoryLock, get_repository_lock_key


def hold_file_lock(lock_path: str, ready) -> None:
    with FileLock(lock_path):
        ready.set()
        time.sleep(30)


def hold_producer_execution_guard(task_id: str, ready) -> None:
    with producer_execution_guard(producer_run_id=None, task_id=task_id):
        ready.set()
        time.sleep(30)


class RepositoryLockTest(SimpleTestCase):
    def test_default_redis_lock_uses_scope_and_key(self) -> None:
        redis_lock_factory = MagicMock(return_value=SimpleNamespace())

        with (
            patch("weblate.utils.lock.is_redis_cache", return_value=True),
            patch(
                "weblate.utils.lock.cache",
                SimpleNamespace(lock=redis_lock_factory),
            ),
        ):
            lock = WeblateLock(
                scope="vcs:api:throttle",
                key="github",
                slug="ignored",
                timeout=5,
                origin="project/component",
            )

        redis_lock_factory.assert_called_once_with(
            key="lock:vcs:api:throttle:github",
            blocking=True,
            timeout=3600,
            blocking_timeout=5,
            thread_local=True,
        )
        self.assertEqual(lock.name, "lock:vcs:api:throttle:github")

    def test_file_only_lock_ignores_redis_cache(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            self.settings(DATA_DIR=temp_dir),
            patch("weblate.utils.lock.is_redis_cache", return_value=True),
        ):
            lock = WeblateLock(
                scope="judge",
                key="delivery",
                slug="ignored",
                file_only=True,
            )

        self.assertEqual(
            lock.name, Path(temp_dir, "locks", "judge-delivery.lock").as_posix()
        )

    def test_file_lock_releases_after_process_termination(self) -> None:
        with TemporaryDirectory() as lock_path:
            context = multiprocessing.get_context("fork")
            ready = context.Event()
            process = context.Process(
                target=hold_file_lock,
                args=(str(Path(lock_path, "judge.lock")), ready),
            )
            process.start()
            try:
                self.assertTrue(ready.wait(timeout=5))
                process.terminate()
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                with FileLock(Path(lock_path, "judge.lock"), timeout=1):
                    pass
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

    def test_producer_guard_excludes_duplicate_process_delivery(self) -> None:

        with TemporaryDirectory() as lock_path, self.settings(DATA_DIR=lock_path):
            context = multiprocessing.get_context("fork")
            ready = context.Event()
            process = context.Process(
                target=hold_producer_execution_guard,
                args=("duplicate-delivery", ready),
            )
            process.start()
            try:
                self.assertTrue(ready.wait(timeout=5))
                with (
                    self.assertRaises(JudgeExecutionGuardError),
                    producer_execution_guard(
                        producer_run_id=None, task_id="duplicate-delivery"
                    ),
                ):
                    pass
                process.terminate()
                process.join(timeout=5)
                self.assertFalse(process.is_alive())
                with producer_execution_guard(
                    producer_run_id=None, task_id="duplicate-delivery"
                ):
                    pass
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

    def test_default_file_lock_uses_locks_dir(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            self.settings(DATA_DIR=temp_dir),
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            lock = WeblateLock(
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )

        self.assertEqual(
            lock.name, Path(temp_dir, "locks", "repository-1.lock").as_posix()
        )

    def test_default_file_lock_escapes_scope_and_key(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            self.settings(DATA_DIR=temp_dir),
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            lock = WeblateLock(
                scope="vcs:api:throttle",
                key="project/main",
                slug="ignored",
                timeout=5,
                origin="project/component",
            )

        self.assertEqual(
            lock.name,
            Path(
                temp_dir, "locks", "vcs%3Aapi%3Athrottle-project%2Fmain.lock"
            ).as_posix(),
        )

    def test_reused_lock_stays_reentrant(self) -> None:
        with (
            TemporaryDirectory() as lock_path,
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
            patch.object(FileLock, "acquire", autospec=True) as acquire,
            patch.object(FileLock, "release", autospec=True) as release,
        ):
            first_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            second_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            first_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            second_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            outer_lock = RepositoryLock(first_repository, first_lock)
            inner_lock = RepositoryLock(second_repository, second_lock)
            self.assertTrue(inner_lock.replace_lock_if_matching(outer_lock))

            with outer_lock, inner_lock:
                self.assertTrue(outer_lock.is_locked)
                self.assertTrue(inner_lock.is_locked)

        acquire.assert_called_once()
        self.assertEqual(
            sum(
                call.kwargs.get("force", False) is False
                for call in release.call_args_list
            ),
            1,
        )

    def test_lock_override_is_rejected_for_different_lock_name(self) -> None:
        with (
            TemporaryDirectory() as lock_path,
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            first_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            second_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=2,
                slug="other-component",
                timeout=5,
                origin="project/other-component",
            )
            first_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            second_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            outer_lock = RepositoryLock(first_repository, first_lock)
            inner_lock = RepositoryLock(second_repository, second_lock)

        self.assertFalse(inner_lock.replace_lock_if_matching(outer_lock))
        self.assertIs(inner_lock.lock_object, second_lock)

    def test_reused_lock_preserves_pending_recovery(self) -> None:
        with (
            TemporaryDirectory() as lock_path,
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
            patch.object(FileLock, "acquire", autospec=True),
            patch.object(FileLock, "release", autospec=True),
        ):
            first_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            second_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            first_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            second_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            outer_lock = RepositoryLock(first_repository, first_lock)
            inner_lock = RepositoryLock(second_repository, second_lock)

            with outer_lock:
                self.assertTrue(inner_lock.replace_lock_if_matching(outer_lock))
                self.assertTrue(inner_lock.begin_recovery())
                inner_lock.finish_recovery()

    def test_reused_lock_shares_active_recovery_skip(self) -> None:
        with (
            TemporaryDirectory() as lock_path,
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            first_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            second_lock = WeblateLock(
                lock_path=lock_path,
                scope="repository",
                key=1,
                slug="component",
                timeout=5,
                origin="project/component",
            )
            first_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=lambda: None),
            )
            second_recovery = MagicMock()
            second_repository = cast(
                "Repository",
                SimpleNamespace(ensure_lock_session_recovered=second_recovery),
            )
            outer_lock = RepositoryLock(first_repository, first_lock)
            inner_lock = RepositoryLock(second_repository, second_lock)

            with outer_lock.without_recovery():
                self.assertTrue(inner_lock.replace_lock_if_matching(outer_lock))
                with inner_lock:
                    pass

            second_recovery.assert_not_called()

            with inner_lock:
                pass

        second_recovery.assert_called_once_with()

    def test_component_repository_lock_name_is_stable_across_path_changes(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            self.settings(DATA_DIR=temp_dir),
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            Path(temp_dir, "home").mkdir()
            first_path = Path(temp_dir, "vcs", "project", "component")
            second_path = Path(temp_dir, "vcs", "renamed-project", "renamed-component")
            first = Repository(
                first_path.as_posix(),
                branch="main",
                component=cast(
                    "Component",
                    SimpleNamespace(pk=1, full_slug="project/component"),
                ),
                local=True,
            )
            second = Repository(
                second_path.as_posix(),
                branch="main",
                component=cast(
                    "Component",
                    SimpleNamespace(pk=1, full_slug="renamed/project/component"),
                ),
                local=True,
            )

        self.assertEqual(first.lock.name, second.lock.name)

    def test_unsaved_component_repository_lock_name_uses_checkout_path(self) -> None:
        with (
            TemporaryDirectory() as temp_dir,
            self.settings(DATA_DIR=temp_dir),
            patch("weblate.utils.lock.is_redis_cache", return_value=False),
        ):
            Path(temp_dir, "home").mkdir()
            first_path = Path(temp_dir, "vcs", "project", "component")
            second_path = Path(temp_dir, "vcs", "other-project", "component")
            first = Repository(
                first_path.as_posix(),
                branch="main",
                component=cast(
                    "Component",
                    SimpleNamespace(pk=None, full_slug="project/component"),
                ),
                local=True,
            )
            second = Repository(
                second_path.as_posix(),
                branch="main",
                component=cast(
                    "Component",
                    SimpleNamespace(pk=None, full_slug="other-project/component"),
                ),
                local=True,
            )

        self.assertEqual(
            first.lock.name,
            Path(
                temp_dir,
                "locks",
                f"repository-{get_repository_lock_key(first_path.as_posix(), first.component)}.lock",
            ).as_posix(),
        )
        self.assertEqual(
            second.lock.name,
            Path(
                temp_dir,
                "locks",
                f"repository-{get_repository_lock_key(second_path.as_posix(), second.component)}.lock",
            ).as_posix(),
        )
        self.assertNotEqual(first.lock.name, second.lock.name)


def hold_component_lock(ready) -> None:
    connections.close_all()
    lock = ComponentUpdateLock(key=987654, slug="test", timeout=1)
    with lock:
        ready.set()
        time.sleep(30)


class ComponentUpdateLockTest(TransactionTestCase):
    def test_component_update_does_not_depend_on_redis(self) -> None:
        with (
            patch("weblate.utils.lock.is_redis_cache", return_value=True),
            patch("weblate.utils.lock.cache") as redis_cache,
        ):
            component = Component(pk=987654, slug="test")
            component.__dict__["full_slug"] = "test/test"
            self.assertIsInstance(component.lock, ComponentUpdateLock)
            redis_cache.lock.assert_not_called()

    def test_owner_death_releases_lock_and_paused_owner_keeps_it(self) -> None:
        connections.close_all()
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        process = context.Process(target=hold_component_lock, args=(ready,))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            assert process.pid is not None
            os.kill(process.pid, signal.SIGSTOP)
            with (
                self.assertRaises(WeblateLockTimeoutError),
                ComponentUpdateLock(key=987654, slug="test", timeout=1),
            ):
                pass
            process.kill()
            process.join(10)
            with (
                patch("weblate.utils.lock.is_redis_cache", return_value=True),
                patch("weblate.utils.lock.cache") as redis_cache,
            ):
                with ComponentUpdateLock(key=987654, slug="test", timeout=2):
                    pass
                redis_cache.lock.assert_not_called()
        finally:
            if process.is_alive():
                process.kill()
                process.join(10)


def try_component_lock(result) -> None:
    connections.close_all()
    try:
        with ComponentUpdateLock(key=987654, slug="test", timeout=1):
            result.put(True)
    except WeblateLockTimeoutError:
        result.put(False)
    finally:
        connections.close_all()


class ComponentTransactionLockTest(TransactionTestCase):
    def test_lock_survives_context_until_outer_commit(self) -> None:
        with transaction.atomic():
            lock = ComponentUpdateLock(key=987654, slug="test", timeout=1)
            with lock:
                with lock:
                    self.assertTrue(lock.is_locked)
                self.assertTrue(lock.is_locked)
            self.assertFalse(lock.is_locked)
            result: queue.Queue[bool] = queue.Queue()
            thread = threading.Thread(target=try_component_lock, args=(result,))
            thread.start()
            self.assertFalse(result.get(timeout=10))
            thread.join(10)
            self.assertFalse(thread.is_alive())
        result = queue.Queue()
        thread = threading.Thread(target=try_component_lock, args=(result,))
        thread.start()
        self.assertTrue(result.get(timeout=10))
        thread.join(10)
        self.assertFalse(thread.is_alive())
        connections.close_all()

    def test_outer_rollback_releases_lock(self) -> None:
        with self.assertRaisesMessage(ValueError, "rollback"), transaction.atomic():
            with ComponentUpdateLock(key=987654, slug="test", timeout=1):
                pass
            msg = "rollback"
            raise ValueError(msg)
        result: queue.Queue[bool] = queue.Queue()
        thread = threading.Thread(target=try_component_lock, args=(result,))
        thread.start()
        self.assertTrue(result.get(timeout=10))
        thread.join(10)
        self.assertFalse(thread.is_alive())

    def test_backend_termination_rolls_back_before_waiting_worker_resumes(self) -> None:
        ready: queue.Queue[int] = queue.Queue()
        resume = threading.Event()
        result: queue.Queue[bool] = queue.Queue()
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "CREATE TABLE component_lock_interruption_test (value integer)"
            )
        try:
            thread = threading.Thread(
                target=hold_interrupted_transaction, args=(ready, resume, result)
            )
            thread.start()
            backend_pid = ready.get(timeout=10)
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s)", [backend_pid])
                self.assertTrue(cursor.fetchone()[0])
            with (
                ComponentUpdateLock(key=987654, slug="test", timeout=2),
                connections["default"].cursor() as cursor,
            ):
                cursor.execute("SELECT count(*) FROM component_lock_interruption_test")
                self.assertEqual(cursor.fetchone()[0], 0)
            resume.set()
            self.assertTrue(result.get(timeout=10))
            thread.join(10)
            self.assertFalse(thread.is_alive())
        finally:
            resume.set()
            thread.join(10)
            with connections["default"].cursor() as cursor:
                cursor.execute("DROP TABLE component_lock_interruption_test")


def hold_interrupted_transaction(ready, resume, result) -> None:
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with (
            ComponentUpdateLock(key=987654, slug="test", timeout=1),
            connections["default"].cursor() as cursor,
        ):
            cursor.execute("INSERT INTO component_lock_interruption_test VALUES (1)")
            cursor.execute("SELECT pg_backend_pid()")
            ready.put(cursor.fetchone()[0])
            resume.wait(10)
            cursor.execute("SELECT 1")
        result.put(False)
    except DatabaseError:
        result.put(True)
    finally:
        connections.close_all()
