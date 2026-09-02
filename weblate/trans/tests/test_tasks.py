# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone

from weblate.auth.models import User
from weblate.checks.tasks import finalize_component_checks
from weblate.trans.judge_loop import _generation_lock_key, build_request
from weblate.trans.models import (
    Category,
    Component,
    PendingUnitChange,
    Suggestion,
    Unit,
)
from weblate.trans.models.judge import (
    JudgeCandidateMetadata,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.models.project import CommitPolicyChoices
from weblate.trans.tasks import (
    cleanup_repos,
    cleanup_stale_repos,
    cleanup_suggestions,
    commit_pending,
    component_alerts,
    daily_update_checks,
    generate_judge_candidate,
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


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeCandidateGenerationTest(ComponentTestCase):
    """generate_judge_candidate outcomes for a held verdict (Task 3)."""

    def setUp(self) -> None:
        super().setUp()
        self.project.machinery_settings = {"openrouter": {"key": "test"}}
        # unit.review is gated on review being enabled for the project, so a
        # superuser actor still needs the feature switch on to hold it.
        self.project.translation_review = True
        self.project.save(update_fields=["machinery_settings", "translation_review"])
        # The worker re-checks unit.review + translation.auto before spending
        # (a revocation between enqueue and execution must stop it). These
        # outcome tests exercise an authorised actor; permission gating is
        # covered separately by the view tests and the revocation test below.
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    def test_generation_is_refused_after_permission_revocation(self) -> None:
        # The view checked permissions before queueing; a revocation between
        # enqueue and execution must still stop the spend.
        unit, verdict = self.critical_verdict()
        with (
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.pk: ["repaired"]},
            ) as repair,
            mock.patch("weblate.auth.models.User.has_perm", return_value=False),
        ):
            outcome = generate_judge_candidate(
                unit_id=unit.pk,
                verdict_id=verdict.pk,
                user_id=self.user.pk,
                replace=False,
            )
        self.assertEqual(outcome, "denied")
        repair.assert_not_called()
        self.assertEqual(
            unit.suggestion_set.filter(userdetails__kind="judge-repair").count(), 0
        )

    def critical_verdict(self, *, target="existing translation"):
        unit = self.translation.unit_set.first()
        unit.translate(self.user, [target], STATE_TRANSLATED)
        unit = Unit.objects.get(pk=unit.pk)
        request = build_request(unit)
        verdict = JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            seat=1,
            judge_model="vendor-a/model",
            target_hash=compute_target_hash(request.target_plurals),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            ),
        )
        unit.run_checks()
        return unit, verdict

    def outcome(self, unit, verdict, *, replace=False):
        return generate_judge_candidate(
            unit_id=unit.pk,
            verdict_id=verdict.pk,
            user_id=self.user.pk,
            replace=replace,
        )

    def test_generates_and_stores_a_candidate(self) -> None:
        unit, verdict = self.critical_verdict()
        with mock.patch(
            "weblate.trans.judge_loop.repair_targets",
            return_value={unit.pk: ["better translation"]},
        ):
            result = self.outcome(unit, verdict)
        self.assertEqual(result, "generated")
        refreshed = Unit.objects.get(pk=unit.pk)
        self.assertEqual(refreshed.target.strip(), "existing translation")
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        candidate = refreshed.suggestion_set.get(userdetails__kind="judge-repair")
        self.assertEqual(candidate.target.strip(), "better translation")
        self.assertEqual(
            JudgeCandidateMetadata.parse(candidate.userdetails).verdict_id, verdict.pk
        )

    def test_a_second_click_reports_existing(self) -> None:
        unit, verdict = self.critical_verdict()
        with mock.patch(
            "weblate.trans.judge_loop.repair_targets",
            return_value={unit.pk: ["better translation"]},
        ):
            self.assertEqual(self.outcome(unit, verdict), "generated")
            self.assertEqual(self.outcome(unit, verdict), "existing")

    def test_replace_regenerates_over_the_existing_candidate(self) -> None:
        unit, verdict = self.critical_verdict()
        with mock.patch(
            "weblate.trans.judge_loop.repair_targets",
            side_effect=[
                {unit.pk: ["first candidate"]},
                {unit.pk: ["second candidate"]},
            ],
        ) as repair:
            self.assertEqual(self.outcome(unit, verdict), "generated")
            self.assertEqual(self.outcome(unit, verdict, replace=True), "generated")
        self.assertEqual(repair.call_count, 2)
        candidates = Unit.objects.get(pk=unit.pk).suggestion_set.filter(
            userdetails__kind="judge-repair"
        )
        self.assertEqual(candidates.count(), 1)
        self.assertEqual(candidates.get().target.strip(), "second candidate")

    def test_in_flight_generation_is_busy(self) -> None:
        unit, verdict = self.critical_verdict()
        cache.add(_generation_lock_key(unit.pk, verdict.pk), "1", timeout=60)
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "busy")
        repair.assert_not_called()

    def test_a_stale_target_is_not_generated_for(self) -> None:
        unit, verdict = self.critical_verdict()
        unit.translate(self.user, ["someone edited"], STATE_TRANSLATED)
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "stale")
        repair.assert_not_called()

    def test_a_pass_verdict_is_not_generated_for(self) -> None:
        unit = self.translation.unit_set.first()
        unit.translate(self.user, ["fine"], STATE_TRANSLATED)
        unit = Unit.objects.get(pk=unit.pk)
        request = build_request(unit)
        verdict = JudgeVerdict.objects.create(
            unit=unit,
            max_severity="none",
            seat=1,
            judge_model="vendor-a/model",
            target_hash=compute_target_hash(request.target_plurals),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            ),
        )
        unit.run_checks()
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "invalid-verdict")
        repair.assert_not_called()

    def test_a_resolved_verdict_is_not_generated_for(self) -> None:
        unit, verdict = self.critical_verdict()
        verdict.resolution = JudgeVerdict.Resolution.ESCALATED
        verdict.save(update_fields=["resolution"])
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "resolved")
        repair.assert_not_called()

    def test_max_length_suppresses_the_candidate(self) -> None:
        unit, verdict = self.critical_verdict()
        unit = Unit.objects.get(pk=unit.pk)
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"])
        unit.run_checks()
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "max-length")
        repair.assert_not_called()

    def test_no_engine_configured_is_reported(self) -> None:
        unit, verdict = self.critical_verdict()
        self.project.machinery_settings = {}
        self.project.save(update_fields=["machinery_settings"])
        with mock.patch("weblate.trans.judge_loop.repair_targets") as repair:
            self.assertEqual(self.outcome(unit, verdict), "no-engine")
        repair.assert_not_called()

    def test_a_missing_engine_output_keeps_the_previous_candidate(self) -> None:
        unit, verdict = self.critical_verdict()
        with mock.patch(
            "weblate.trans.judge_loop.repair_targets",
            return_value={unit.pk: ["previous candidate"]},
        ):
            self.assertEqual(self.outcome(unit, verdict), "generated")
        with mock.patch("weblate.trans.judge_loop.repair_targets", return_value={}):
            self.assertEqual(self.outcome(unit, verdict, replace=True), "failed")
        candidates = Unit.objects.get(pk=unit.pk).suggestion_set.filter(
            userdetails__kind="judge-repair"
        )
        self.assertEqual(candidates.count(), 1)
        self.assertEqual(candidates.get().target.strip(), "previous candidate")

    def test_a_target_edited_during_generation_discards_the_output(self) -> None:
        unit, verdict = self.critical_verdict()

        def drift(requests, user):
            Unit.objects.filter(pk=unit.pk).update(target="concurrent edit")
            return {unit.pk: ["paid output"]}

        with mock.patch("weblate.trans.judge_loop.repair_targets", side_effect=drift):
            self.assertEqual(self.outcome(unit, verdict), "drift")
        refreshed = Unit.objects.get(pk=unit.pk)
        self.assertEqual(refreshed.target.strip(), "concurrent edit")
        self.assertEqual(refreshed.suggestion_set.count(), 0)
