# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone

from weblate.auth.models import User
from weblate.checks.tasks import finalize_component_checks
from weblate.trans.models import Category, Component, PendingUnitChange, Suggestion
from weblate.trans.models.judge import JudgeDeferral
from weblate.trans.models.project import CommitPolicyChoices
from weblate.trans.tasks import (
    cleanup_judge_observability,
    cleanup_repos,
    cleanup_stale_repos,
    cleanup_suggestions,
    commit_pending,
    component_alerts,
    daily_update_checks,
    perform_commit,
    update_checks,
    update_remotes,
)
from weblate.trans.tests.test_views import ComponentTestCase
from weblate.utils.files import remove_tree
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.state import STATE_FUZZY, STATE_TRANSLATED
from weblate.utils.tasks import (
    update_language_stats_parents,
    update_project_stats_link,
    update_translation_stats_parents,
)
from weblate.utils.version import GIT_VERSION


class CleanupTest(ComponentTestCase):
    def test_cleanup_suggestions_case_sensitive(self) -> None:
        request = self.get_request()
        unit = self.get_unit()

        # Add two suggestions
        Suggestion.objects.add(unit, ["Zkouška\n"], request)
        Suggestion.objects.add(unit, ["zkouška\n"], request)
        # This should be ignored
        Suggestion.objects.add(unit, ["zkouška\n"], request)
        self.assertEqual(len(self.get_unit().suggestions), 2)

        # Perform cleanup, no suggestions should be deleted
        cleanup_suggestions()
        self.assertEqual(len(self.get_unit().suggestions), 2)

        # Translate string to one of suggestions
        unit.translate(self.user, "zkouška\n", STATE_TRANSLATED)

        # The cleanup should remove one
        cleanup_suggestions()
        self.assertEqual(len(self.get_unit().suggestions), 1)

    def test_cleanup_suggestions_duplicate(self) -> None:
        request = self.get_request()
        unit = self.get_unit()

        # Add two suggestions
        Suggestion.objects.add(unit, ["Zkouška"], request)
        Suggestion.objects.add(unit, ["zkouška"], request)
        self.assertEqual(len(self.get_unit().suggestions), 2)

        # Perform cleanup, no suggestions should be deleted
        cleanup_suggestions()
        self.assertEqual(len(self.get_unit().suggestions), 2)

        # Create two suggestions with same target
        unit.suggestions.update(target="zkouška")

        # The cleanup should remove one
        cleanup_suggestions()
        self.assertEqual(len(self.get_unit().suggestions), 1)


class TasksTest(ComponentTestCase):
    def test_component_alerts_processes_canonical_component_first(self) -> None:
        second = self.create_po(project=self.project, name="Second")
        processed: list[int] = []

        with patch.object(
            Component,
            "update_alerts",
            autospec=True,
            side_effect=lambda component: processed.append(component.pk),
        ):
            component_alerts([second.pk, self.component.pk])

        self.assertEqual(processed, [self.component.pk, second.pk])

    def test_daily_update_checks(self) -> None:
        daily_update_checks()

    def test_update_checks_uses_narrow_prefetches(self) -> None:
        category = Category.objects.create(
            project=self.project, name="WorkshopApp", slug="workshopapp"
        )
        self.component.category = category
        self.component.save(update_fields=["category"])

        with (
            patch.object(Component, "run_batched_checks", autospec=True) as batched,
            CaptureQueriesContext(connection) as queries,
        ):
            update_checks(self.component.pk, "update-token")

        batched.assert_called_once()
        sql_queries = [query["sql"] for query in queries]

        def count_relation_prefetches(table: str) -> int:
            marker = f'FROM "{table}" WHERE ("{table}"."id") IN'
            return sum(marker in sql for sql in sql_queries)

        self.assertLessEqual(count_relation_prefetches("trans_project"), 1)
        self.assertLessEqual(count_relation_prefetches("trans_category"), 1)
        self.assertLessEqual(count_relation_prefetches("trans_component"), 1)

    def test_cleanup_repos(self) -> None:
        cleanup_repos()

    def test_cleanup_stale_repos_keeps_category_with_stale_git_dir(self) -> None:
        category = Category.objects.create(
            project=self.project, name="WorkshopApp", slug="workshopapp"
        )
        component = self.create_po(
            project=self.project, category=category, name="startup", vcs="local"
        )
        stale_git = Path(category.full_path) / ".git"
        stale_git.mkdir()
        (stale_git / "config").write_text("[core]\n", encoding="utf-8")

        old_timestamp = time.time() - 2 * 86400
        os.utime(category.full_path, (old_timestamp, old_timestamp))
        os.utime(component.full_path, (old_timestamp, old_timestamp))

        cleanup_stale_repos()

        self.assertTrue(os.path.isdir(category.full_path))
        self.assertTrue(os.path.isdir(component.full_path))
        self.assertTrue(
            os.path.isfile(os.path.join(component.full_path, ".git", "config"))
        )

    def test_cleanup_stale_repos_keeps_empty_component_dir(self) -> None:
        component = self.create_po(project=self.project, name="empty", vcs="local")
        component_path = Path(component.full_path)

        for entry in component_path.iterdir():
            if entry.is_dir():
                remove_tree(entry)
            else:
                entry.unlink()

        old_timestamp = time.time() - 2 * 86400
        os.utime(component_path, (old_timestamp, old_timestamp))

        cleanup_stale_repos()

        self.assertTrue(component_path.is_dir())

    def test_update_remotes(self) -> None:
        update_remotes()

    def test_commit_pending(self) -> None:
        self.component.commit_pending_age = 1
        self.component.save()

        component2 = self.create_ftl(name="Component 2", project=self.project)
        component2.commit_pending_age = 3
        component2.save()

        translation = self.component.translation_set.get(language_code="cs")
        unit = translation.unit_set.get(source="Hello, world!\n")
        unit.translate(self.user, "Nazdar svete!\n", STATE_TRANSLATED)

        translation2 = component2.translation_set.get(language_code="cs")
        unit2 = translation2.unit_set.get(source="Hello, ${ name }!")
        unit2.translate(self.user, "Ahoj ${ name }!\n", STATE_TRANSLATED)

        self.assertEqual(self.component.count_pending_units, 1)
        self.assertEqual(component2.count_pending_units, 1)

        PendingUnitChange.objects.update(timestamp=timezone.now() - timedelta(hours=2))

        commit_pending()
        self.assertEqual(self.component.count_pending_units, 0)
        self.assertEqual(component2.count_pending_units, 1)

        commit_pending(hours=1)
        self.assertEqual(component2.count_pending_units, 0)

    def test_perform_commit_keeps_commit_task_on_pending_lock_retry(self) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        task = SimpleNamespace(
            request=SimpleNamespace(id="commit-task-id", retries=2), max_retries=3
        )

        with (
            patch("weblate.trans.models.component.current_task", task),
            patch("weblate.trans.tasks.current_task", task),
            patch.object(Component, "commit_pending", side_effect=lock_timeout),
            self.assertRaises(WeblateLockTimeoutError),
        ):
            perform_commit.run(self.component.pk, "commit")

        self.assertEqual(cache.get(self.component.commit_task_key), "commit-task-id")
        cache.delete(self.component.commit_task_key)

    def test_perform_commit_clears_commit_task_on_exhausted_lock_retry(self) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        task = SimpleNamespace(
            request=SimpleNamespace(id="commit-task-id", retries=3), max_retries=3
        )

        with (
            patch("weblate.trans.models.component.current_task", task),
            patch("weblate.trans.tasks.current_task", task),
            patch.object(Component, "commit_pending", side_effect=lock_timeout),
            self.assertRaises(WeblateLockTimeoutError),
        ):
            perform_commit.run(self.component.pk, "commit")

        self.assertIsNone(cache.get(self.component.commit_task_key))

    def test_perform_commit_keeps_other_commit_task_on_exhausted_lock_retry(
        self,
    ) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        task = SimpleNamespace(
            request=SimpleNamespace(id="background-task-id", retries=3), max_retries=3
        )

        with (
            patch("weblate.trans.models.component.current_task", task),
            patch("weblate.trans.tasks.current_task", task),
            patch.object(Component, "commit_pending", side_effect=lock_timeout),
            self.assertRaises(WeblateLockTimeoutError),
        ):
            perform_commit.run(self.component.pk, "commit")

        self.assertEqual(cache.get(self.component.commit_task_key), "commit-task-id")
        cache.delete(self.component.commit_task_key)

    def test_perform_commit_schedules_deferred_commit_on_exhausted_lock_retry(
        self,
    ) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        cache.set(
            self.component.commit_task_reschedule_key,
            {
                "reason": "commit",
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
        )
        lock_timeout = WeblateLockTimeoutError("locked", lock=self.component.lock)
        task = SimpleNamespace(
            request=SimpleNamespace(id="commit-task-id", retries=3), max_retries=3
        )

        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            patch("weblate.trans.models.component.current_task", task),
            patch("weblate.trans.tasks.current_task", task),
            patch.object(Component, "commit_pending", side_effect=lock_timeout),
            patch("weblate.trans.models.component.uuid", return_value="next-task-id"),
            patch.object(perform_commit, "apply_async") as apply_async,
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaises(WeblateLockTimeoutError),
        ):
            perform_commit.run(self.component.pk, "commit")

        apply_async.assert_called_once_with(
            args=(self.component.pk, "commit"),
            kwargs={
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
            task_id="next-task-id",
        )
        self.assertEqual(cache.get(self.component.commit_task_key), "next-task-id")
        self.assertIsNone(cache.get(self.component.commit_task_reschedule_key))
        self.component.delete_commit_task()

    def test_perform_commit_schedules_deferred_commit_request(self) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        cache.set(
            self.component.commit_task_reschedule_key,
            {
                "reason": "commit",
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
        )

        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            patch(
                "weblate.trans.models.component.current_task",
                SimpleNamespace(request=SimpleNamespace(id="commit-task-id")),
            ),
            patch(
                "weblate.trans.tasks.current_task",
                SimpleNamespace(request=SimpleNamespace(id="commit-task-id")),
            ),
            patch.object(Component, "commit_pending", return_value=True),
            patch("weblate.trans.models.component.uuid", return_value="next-task-id"),
            patch.object(perform_commit, "apply_async") as apply_async,
            self.captureOnCommitCallbacks(execute=True),
        ):
            perform_commit.run(self.component.pk, "commit_pending")

        apply_async.assert_called_once_with(
            args=(self.component.pk, "commit"),
            kwargs={
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
            task_id="next-task-id",
        )
        self.assertEqual(cache.get(self.component.commit_task_key), "next-task-id")
        self.assertIsNone(cache.get(self.component.commit_task_reschedule_key))
        self.component.delete_commit_task()

    def test_perform_commit_keeps_other_commit_task_on_success(self) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        cache.set(
            self.component.commit_task_reschedule_key,
            {
                "reason": "commit",
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
        )

        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            patch(
                "weblate.trans.models.component.current_task",
                SimpleNamespace(request=SimpleNamespace(id="background-task-id")),
            ),
            patch(
                "weblate.trans.tasks.current_task",
                SimpleNamespace(request=SimpleNamespace(id="background-task-id")),
            ),
            patch.object(Component, "commit_pending", return_value=True),
            patch.object(perform_commit, "apply_async") as apply_async,
        ):
            perform_commit.run(self.component.pk, "commit")

        apply_async.assert_not_called()
        self.assertEqual(cache.get(self.component.commit_task_key), "commit-task-id")
        self.assertEqual(
            cache.get(self.component.commit_task_reschedule_key),
            {
                "reason": "commit",
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
        )
        self.component.delete_commit_task()

    def test_perform_commit_clears_commit_task_on_missing_user(self) -> None:
        cache.set(self.component.commit_task_key, "commit-task-id")
        cache.set(
            self.component.commit_task_reschedule_key,
            {
                "reason": "commit",
                "user_id": self.user.id,
                "force_scan": False,
                "previous_head": None,
            },
        )
        task = SimpleNamespace(request=SimpleNamespace(id="commit-task-id"))

        with (
            patch("weblate.trans.models.component.current_task", task),
            patch("weblate.trans.tasks.current_task", task),
            patch.object(Component, "commit_pending") as commit_pending_mock,
            self.assertRaises(User.DoesNotExist),
        ):
            perform_commit.run(self.component.pk, "commit", user_id=-1)

        commit_pending_mock.assert_not_called()
        self.assertIsNone(cache.get(self.component.commit_task_key))
        self.assertIsNone(cache.get(self.component.commit_task_reschedule_key))

    @patch("weblate.trans.tasks.perform_commit")
    def test_commit_pending_with_ineligible_changes(self, mock_perform_commit) -> None:
        """Test that perform_commit is not called when all changes are ineligible."""
        mock_perform_commit.delay.return_value.id = "commit-task-id"
        self.project.commit_policy = CommitPolicyChoices.WITHOUT_NEEDS_EDITING
        self.project.save()

        self.component.commit_pending_age = 1
        self.component.save()

        translation = self.component.translation_set.get(language_code="cs")
        unit = translation.unit_set.get(source="Hello, world!\n")
        unit.translate(self.user, "Nazdar svete!\n", STATE_FUZZY)

        component2 = self.create_ftl(name="Component 2", project=self.project)
        component2.commit_pending_age = 1
        component2.save()

        translation2 = component2.translation_set.get(language_code="cs")
        unit2 = translation2.unit_set.get(source="Hello, ${ name }!")
        unit2.translate(self.user, "Ahoj ${ name }!\n", STATE_TRANSLATED)

        pending_change = unit2.pending_changes.first()
        pending_change.metadata = {
            "last_failed": timezone.now().isoformat(),
            "failed_revision": translation2.revision,
            "weblate_version": GIT_VERSION,
            "blocking_unit": True,
        }
        pending_change.save()

        self.assertEqual(self.component.count_pending_units, 0)
        self.assertEqual(component2.count_pending_units, 0)
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                self.component, apply_filters=False
            ).count(),
            1,
        )
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                component2, apply_filters=False
            ).count(),
            1,
        )

        PendingUnitChange.objects.update(timestamp=timezone.now() - timedelta(hours=2))

        commit_pending()
        mock_perform_commit.delay.assert_not_called()

        self.assertEqual(self.component.count_pending_units, 0)
        self.assertEqual(component2.count_pending_units, 0)
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                self.component, apply_filters=False
            ).count(),
            1,
        )
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                component2, apply_filters=False
            ).count(),
            1,
        )

        unit.translate(self.user, "Nazdar svete!\n", STATE_TRANSLATED)
        PendingUnitChange.objects.update(timestamp=timezone.now() - timedelta(hours=2))

        self.assertEqual(self.component.count_pending_units, 1)
        self.assertEqual(component2.count_pending_units, 0)
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                self.component, apply_filters=False
            ).count(),
            2,
        )
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                component2, apply_filters=False
            ).count(),
            1,
        )

        commit_pending()
        mock_perform_commit.delay.assert_called_with(
            self.component.pk,
            "commit_pending",
            user_id=None,
            force_scan=False,
            previous_head=None,
        )

        # actually call commit_pending on the component to test count_pending_units is updated
        self.component.commit_pending("commit_pending", None)
        component2.commit_pending("commit_pending", None)

        self.assertEqual(self.component.count_pending_units, 0)
        self.assertEqual(component2.count_pending_units, 0)
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                self.component, apply_filters=False
            ).count(),
            0,
        )
        self.assertEqual(
            PendingUnitChange.objects.for_component(
                component2, apply_filters=False
            ).count(),
            1,
        )

    def test_update_translation_stats_parents_missing_translation(self) -> None:
        update_translation_stats_parents(-1)

    def test_update_language_stats_parents_missing_component(self) -> None:
        update_language_stats_parents(-1)

    def test_update_project_stats_link_missing_project(self) -> None:
        update_project_stats_link(-1)

    def test_finalize_component_checks_missing_component(self) -> None:
        finalize_component_checks(-1, [], ["same"], batch_mode=True)

    def test_finalize_component_checks_missing_source_translation(self) -> None:
        source_translation = self.component.get_source_translation()
        self.assertIsNotNone(source_translation)
        source_translation.delete()
        self.component.__dict__.pop("source_translation", None)

        finalize_component_checks(
            self.component.id, [], ["multiple_failures"], batch_mode=True
        )

        self.assertFalse(
            self.component.translation_set.filter(
                language=self.component.source_language
            ).exists()
        )


class JudgeDeferralCleanupTest(ComponentTestCase):
    """The observability cleanup prunes only expired closed deferrals."""

    def make_deferral(self, state, closed_at=None, identity="a"):
        unit = self.get_unit()
        return JudgeDeferral.objects.create(
            unit=unit,
            request_identity=identity * 64,
            target_hash="t" * 64,
            context_hash="c" * 64,
            project_context_hash="p" * 64,
            source_language="en",
            target_language="cs",
            profile_fingerprint="f" * 64,
            prompt_schema_version="v1",
            seat=1,
            state=state,
            next_attempt_at=timezone.now(),
            closed_at=closed_at,
        )

    def test_only_expired_closed_rows_are_deleted(self) -> None:
        now = timezone.now()
        expired = self.make_deferral(
            JudgeDeferral.State.CLOSED,
            closed_at=now - timedelta(days=91),
            identity="1",
        )
        recent = self.make_deferral(
            JudgeDeferral.State.CLOSED,
            closed_at=now - timedelta(days=1),
            identity="2",
        )
        old_queued = self.make_deferral(JudgeDeferral.State.QUEUED, identity="3")
        old_slow = self.make_deferral(JudgeDeferral.State.SLOW, identity="4")

        cleanup_judge_observability()

        alive = set(JudgeDeferral.objects.values_list("pk", flat=True))
        self.assertNotIn(expired.pk, alive)
        self.assertEqual(alive, {recent.pk, old_queued.pk, old_slow.pk})

    @override_settings(JUDGE_DEFERRAL_CLOSED_RETENTION_DAYS=0)
    def test_zero_purges_all_closed_rows(self) -> None:
        now = timezone.now()
        closed = self.make_deferral(
            JudgeDeferral.State.CLOSED, closed_at=now, identity="5"
        )
        queued = self.make_deferral(JudgeDeferral.State.QUEUED, identity="6")

        cleanup_judge_observability()

        self.assertFalse(JudgeDeferral.objects.filter(pk=closed.pk).exists())
        self.assertTrue(JudgeDeferral.objects.filter(pk=queued.pk).exists())

    def test_closed_index_exists_in_schema(self) -> None:
        """The retention delete is index-backed; verified by DB introspection."""
        table = JudgeDeferral._meta.db_table  # ruff: ignore[private-member-access]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s", [table]
            )
            definitions = [row[0] for row in cursor.fetchall()]
        self.assertTrue(
            any(
                "judge_deferral_closed_idx" in definition
                and "(state, closed_at)" in definition
                for definition in definitions
            ),
            definitions,
        )

    @override_settings(JUDGE_DEFERRAL_CLOSED_RETENTION_DAYS="nonsense")
    def test_invalid_value_falls_back_to_default(self) -> None:
        now = timezone.now()
        within_default = self.make_deferral(
            JudgeDeferral.State.CLOSED,
            closed_at=now - timedelta(days=89),
            identity="7",
        )
        beyond_default = self.make_deferral(
            JudgeDeferral.State.CLOSED,
            closed_at=now - timedelta(days=91),
            identity="8",
        )

        cleanup_judge_observability()

        self.assertTrue(JudgeDeferral.objects.filter(pk=within_default.pk).exists())
        self.assertFalse(JudgeDeferral.objects.filter(pk=beyond_default.pk).exists())
