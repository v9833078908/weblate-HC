# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from weblate.trans.autotranslate import BatchAutoTranslate
from weblate.trans.models.judge import JudgeRunUnit, ProducerRun
from weblate.trans.models.unit import Unit
from weblate.trans.tasks import (
    StaleProducerTaskError,
    drain_producer_run_dispatches,
    producer_execution_guard,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_TRANSLATED


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAY_APPROVE=False,
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
    ),
)
class JudgeFullScopeContinuationTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(
            update_fields=["translation_review", "machinery_settings"]
        )
        self.translation = self.get_translation()
        Unit.objects.filter(translation__component=self.component).delete()
        self.units = [
            Unit.objects.create(
                translation=self.translation,
                id_hash=i + 5000,
                source=f"chunk source {i}",
                target=f"chunk target {i}",
                state=STATE_TRANSLATED,
                position=i,
            )
            for i in range(4)
        ]

    def _make_run(self, **kwargs) -> ProducerRun:
        defaults = {
            "actor": self.user,
            "scope_type": ProducerRun.ScopeType.PROJECT,
            "scope_id": str(self.project.pk),
            "scope_label": str(self.project),
            "scope_path": self.project.get_absolute_url(),
            "requested_query": "",
            "requested_mode": "judge",
            "cap": len(self.units),
            "execution_version": 1,
            "scope_cursor": 0,
            "scope_snapshot": [u.pk for u in self.units],
            "dispatch_task_id": uuid4(),
            "dispatch_phase": "judge-project",
            "dispatch_requested_at": timezone.now(),
            "preparation_snapshot": {"missing": []},
            "preparation_phase": "ready",
            "status": ProducerRun.Status.QUEUED,
            "execution_options": {
                "mode": "judge",
                "q": "",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
                "judge_proposal_only": True,
                "judge_pretranslate": False,
                "judge_mutating_repairs": False,
            },
        }
        defaults.update(kwargs)
        return ProducerRun.objects.create(**defaults)

    def test_stale_task_uuid_guard_exits_without_lock(self) -> None:
        run = self._make_run(status=ProducerRun.Status.RUNNING)
        superseding_task_id = str(uuid4())
        run.dispatch_task_id = superseding_task_id
        run.save(update_fields=["dispatch_task_id"])

        old_task_id = str(uuid4())
        with (
            self.assertRaises(StaleProducerTaskError),
            producer_execution_guard(producer_run_id=str(run.pk), task_id=old_task_id),
        ):
            pass

    def test_adopt_fails_foreign_uuid_for_running_run(self) -> None:
        valid_uuid = str(uuid4())
        run = self._make_run(
            status=ProducerRun.Status.RUNNING,
            dispatch_task_id=valid_uuid,
            task_id=valid_uuid,
        )
        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            producer_run_id=str(run.pk),
            enforce_permissions=False,
        )
        foreign_uuid = str(uuid4())
        with patch("weblate.trans.autotranslate.current_task") as mock_task:
            mock_task.request.id = foreign_uuid
            with self.assertRaises(ValueError):
                batch._adopt_producer_run()  # ruff: ignore[private-member-access]

        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertIn("superseded", run.failure)

    def test_drain_republishes_running_continuation(self) -> None:
        run = self._make_run(
            status=ProducerRun.Status.RUNNING,
            execution_version=1,
            dispatch_published_at=None,
        )
        with patch("weblate.trans.tasks.auto_translate.apply_async") as mock_apply:
            drain_producer_run_dispatches()
            mock_apply.assert_called_once()
            self.assertEqual(
                mock_apply.call_args.kwargs["task_id"], str(run.dispatch_task_id)
            )
        run.refresh_from_db()
        self.assertIsNotNone(run.dispatch_published_at)

    def test_drain_finalizes_cancel_requested(self) -> None:
        run = self._make_run(
            status=ProducerRun.Status.CANCEL_REQUESTED,
        )
        drain_producer_run_dispatches()
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.CANCELLED)
        self.assertIsNotNone(run.finished)

    def test_drain_finalizes_cancel_requested_with_results_to_partial(self) -> None:

        run = self._make_run(
            status=ProducerRun.Status.CANCEL_REQUESTED,
        )
        JudgeRunUnit.objects.create(
            run=run,
            unit=self.units[0],
            unit_id_snapshot=self.units[0].pk,
            translation_id=self.translation.pk,
            component_id=self.component.pk,
            project_id=self.project.pk,
            outcome=JudgeRunUnit.Outcome.PASSED,
        )
        drain_producer_run_dispatches()
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.PARTIAL)
        self.assertIsNotNone(run.finished)

    def test_multi_chunk_run_progression(self) -> None:
        task_id_1 = str(uuid4())
        run = self._make_run(
            status=ProducerRun.Status.QUEUED,
            dispatch_task_id=task_id_1,
        )
        with (
            patch("weblate.trans.autotranslate.JUDGE_CHUNK_SIZE", 2),
            patch("weblate.trans.autotranslate.current_task") as mock_task,
            patch("weblate.trans.tasks.publish_producer_run_dispatch") as mock_pub,
            patch("weblate.trans.autotranslate.AutoTranslate.perform") as mock_perform,
        ):
            mock_task.request.id = task_id_1
            batch_1 = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
                producer_run_id=str(run.pk),
                enforce_permissions=False,
            )
            msg_1 = batch_1.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
            self.assertIn("chunk", msg_1.lower())

            run.refresh_from_db()
            self.assertEqual(run.scope_cursor, 2)
            self.assertEqual(run.status, ProducerRun.Status.RUNNING)
            task_id_2 = str(run.dispatch_task_id)
            self.assertNotEqual(task_id_2, task_id_1)
            mock_pub.assert_called_once_with(run_id=run.pk)
            mock_perform.assert_called()

            # Second chunk picks up from cursor 2
            mock_pub.reset_mock()
            mock_task.request.id = task_id_2
            batch_2 = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
                producer_run_id=str(run.pk),
                enforce_permissions=False,
            )
            msg_2 = batch_2.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
            self.assertIn("completed", msg_2.lower())

            run.refresh_from_db()
            self.assertEqual(run.scope_cursor, 4)
            self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
            self.assertIsNotNone(run.finished)

    def test_cancellation_between_chunks_stops_continuation(self) -> None:
        task_id = str(uuid4())
        run = self._make_run(
            status=ProducerRun.Status.QUEUED,
            dispatch_task_id=task_id,
        )

        def simulate_cancel(*args, **kwargs):
            run.status = ProducerRun.Status.CANCEL_REQUESTED
            run.save(update_fields=["status"])

        with (
            patch("weblate.trans.autotranslate.JUDGE_CHUNK_SIZE", 2),
            patch("weblate.trans.autotranslate.current_task") as mock_task,
            patch("weblate.trans.tasks.publish_producer_run_dispatch") as mock_pub,
            patch(
                "weblate.trans.autotranslate.AutoTranslate.perform",
                side_effect=simulate_cancel,
            ),
        ):
            mock_task.request.id = task_id
            batch = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
                producer_run_id=str(run.pk),
                enforce_permissions=False,
            )
            msg = batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
            self.assertIn("cancelled", msg.lower())

            run.refresh_from_db()
            self.assertEqual(run.status, ProducerRun.Status.CANCELLED)
            mock_pub.assert_not_called()

    def test_provider_error_leaves_run_running_for_version_1(self) -> None:
        task_id = str(uuid4())
        run = self._make_run(
            status=ProducerRun.Status.QUEUED,
            dispatch_task_id=task_id,
            execution_version=1,
        )
        with (
            patch("weblate.trans.autotranslate.JUDGE_CHUNK_SIZE", 2),
            patch("weblate.trans.autotranslate.current_task") as mock_task,
            patch(
                "weblate.trans.autotranslate.AutoTranslate.perform",
                side_effect=RuntimeError("Provider 503 unavailable"),
            ),
        ):
            mock_task.request.id = task_id
            batch = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
                producer_run_id=str(run.pk),
                enforce_permissions=False,
            )
            with self.assertRaises(RuntimeError):
                batch.perform(
                    auto_source="mt",
                    engines=[],
                    threshold=80,
                    source_component_ids=None,
                )

            run.refresh_from_db()
            self.assertEqual(run.status, ProducerRun.Status.RUNNING)

    def test_provider_refusal_stops_chunk_loop_immediately(self) -> None:
        task_id = str(uuid4())
        run = self._make_run(
            status=ProducerRun.Status.QUEUED,
            dispatch_task_id=task_id,
            execution_version=1,
        )

        def fail_perform(*args, **kwargs):
            batch._finish_translation(  # ruff: ignore[private-member-access]
                auto_translate=SimpleNamespace(
                    judge_units_processed=1,
                    failure_message="Provider quota exceeded",
                    updated=0,
                    get_warnings=list,
                    judge_summary=None,
                ),
                judge_remaining=None,
            )

        with (
            patch("weblate.trans.autotranslate.JUDGE_CHUNK_SIZE", 2),
            patch("weblate.trans.autotranslate.current_task") as mock_task,
            patch("weblate.trans.tasks.publish_producer_run_dispatch") as mock_pub,
            patch(
                "weblate.trans.autotranslate.AutoTranslate.perform",
                side_effect=fail_perform,
            ),
        ):
            mock_task.request.id = task_id
            batch = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
                producer_run_id=str(run.pk),
                enforce_permissions=False,
            )
            msg = batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
            self.assertEqual(msg, "Provider quota exceeded")
            run.refresh_from_db()
            self.assertEqual(run.status, ProducerRun.Status.FAILED)
            self.assertEqual(run.failure, "Provider quota exceeded")
            self.assertEqual(run.scope_cursor, 0)
            mock_pub.assert_not_called()
