# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for automatic translation."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import threading
import time
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import mock
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import override_settings
from django.urls import reverse

from weblate.addons.autotranslate import AutoTranslateAddon
from weblate.addons.events import AddonEvent
from weblate.addons.models import AddonActivityLog
from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, TeamMembership, User, setup_project_groups
from weblate.checks.chars import MaxLengthCheck
from weblate.checks.models import CHECKS
from weblate.configuration.models import Setting, SettingCategory
from weblate.lang.models import Language, Plural
from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
    MachineTranslationServiceError,
)
from weblate.machinery.dummy import DummyTranslation
from weblate.trans import tasks
from weblate.trans.actions import ActionEvents
from weblate.trans.autotranslate import AutoTranslate, BatchAutoTranslate
from weblate.trans.forms import AutoForm
from weblate.trans.judge_loop import build_request
from weblate.trans.machinery import MachineryBatchOutcome, fetch_machinery_matches
from weblate.trans.models import (
    Change,
    Component,
    PendingUnitChange,
    ProducerRun,
    Project,
    Translation,
    Unit,
    WorkflowSetting,
)
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tasks import (
    JudgeExecutionGuardError,
    auto_translate,
    auto_translate_component,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.trans.util import split_plural
from weblate.utils.celery import (
    PENDING_TASK_MAX_AGE,
    add_user_task,
    delete_task_liveness,
    get_task_liveness,
    get_task_liveness_key,
    get_task_metadata,
    get_user_tasks,
    get_user_tasks_key,
    register_task_liveness,
)
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_READONLY,
    STATE_TRANSLATED,
)
from weblate.utils.stats import ProjectLanguage
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Callable


def _noop_preload_workflow_settings(_self) -> None:
    pass


def _fake_current_task(task_id: str) -> Mock:
    task = Mock()
    task.request = SimpleNamespace(id=task_id)
    return task


_GUARD_RETRY_DELIVERY = object()


def _run_guarded_auto_translate_process(
    task_id: str,
    component_id: int,
    user_id: int,
    hold_after: int,
    unit_ids: list[int],
) -> None:
    """
    Run one real ``auto_translate`` delivery against a patched judge boundary.

    Only the external provider is replaced: the guard, the durable producer
    run, its rows and the finalization all execute their production code.
    With ``hold_after`` below the persisted unit count, the process exits
    right after that many durable rows, before the run is finalized.
    """
    # A forked child inherits the parent's socket: abandon the connection
    # object without closing it, so the parent's own connection survives.
    for connection in connections.all(initialized_only=True):
        connection.connection = None
    seen: list[int] = []

    def fake_judge_batch(
        units, *, writable_ids, user, on_batch=None, run=None, **kwargs
    ):
        # PENDING placeholders must already be durably reserved for every
        # unit about to be judged before any provider call.
        pending = set(
            JudgeRunUnit.objects.filter(
                run_id=run.pk if run else None,
                outcome=JudgeRunUnit.Outcome.PENDING,
            ).values_list("unit_id_snapshot", flat=True)
        )
        all_rows = sorted(
            JudgeRunUnit.objects.filter(run_id=run.pk if run else None).values_list(
                "unit_id_snapshot", "outcome", "skip_reason"
            )
        )
        assert {unit.id for unit in units} <= pending, (
            f"pending={sorted(pending)} units={sorted(unit.id for unit in units)} "
            f"run={run.pk if run else None} all={all_rows}"
        )
        out = {}
        for unit in units:
            request = build_request(unit)
            out[unit.id] = JudgeVerdict.objects.create(
                unit=unit,
                max_severity="none",
                model_verdict=JudgeVerdict.Verdict.PASS,
                judge_model="vendor-a/model",
                seat=1,
                target_hash=compute_target_hash(request.target_plurals),
                context_hash=compute_context_hash(
                    source=request.source,
                    note=request.note,
                    explanation=request.explanation,
                    glossary_terms=request.glossary_terms,
                ),
            )
            seen.append(unit.id)
        if on_batch is not None:
            on_batch([], [])
        return out

    real_process_judge = AutoTranslate.process_judge

    def stop_after_first_translation(self, **kwargs):
        # Block before the batch advances to the next translation or
        # finalizes the run: the durable judge rows of the first
        # translation stay, the producer run is left RUNNING.
        result = real_process_judge(self, **kwargs)
        if len(seen) >= hold_after:
            os._exit(0)
        return result

    try:
        with (
            mock.patch(
                "weblate.trans.autotranslate.current_task", _fake_current_task(task_id)
            ),
            mock.patch.object(tasks, "current_task", _fake_current_task(task_id)),
            mock.patch.object(
                tasks, "heartbeat_task", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "touch_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "delete_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "register_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "_preload_workflow_settings",
                _noop_preload_workflow_settings,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge_batch,
            ),
            mock.patch.object(
                AutoTranslate,
                "process_judge",
                autospec=True,
                side_effect=stop_after_first_translation,
            ),
            mock.patch.object(
                tasks, "get_auto_translate_target", autospec=True
            ) as get_target,
            mock.patch.object(
                tasks, "store_auto_translate_activity_log", autospec=True
            ) as store_log,
        ):
            get_target.return_value = (Component.objects.get(pk=component_id), {})
            store_log.side_effect = lambda _log, result, **_kwargs: result
            output = tasks.auto_translate._orig_run.__func__(  # ruff: ignore[private-member-access]
                _ProcessTask(task_id, _GUARD_RETRY_DELIVERY),
                user_id=user_id,
                mode="judge",
                q="",
                auto_source="mt",
                source_component_id=None,
                engines=[],
                threshold=80,
                unit_ids=unit_ids,
                translation_id=None,
                component_id=None,
                category_id=None,
                project_id=None,
                language_id=None,
                workspace_id=None,
                activity_log_id=None,
                activity_log_task_count=None,
                enforce_permissions=False,
                overwrite_existing=False,
                producer_run_id=None,
                judge_pretranslate=False,
                judge_mutating_repairs=True,
                judge_candidate_severities=("critical", "major"),
                judge_proposal_only=False,
            )
        message = output["message"] if isinstance(output, dict) else str(output)
    finally:
        connections.close_all()
    assert seen, message


def _run_held_auto_translate_process(
    task_id: str, component_id: int, user_id: int, ready, unit_ids: list[int]
) -> None:
    """
    Hold a real delivery's execution guard at the provider boundary.

    The producer run is created and RUNNING when ``ready`` fires; the
    process then sleeps with the lock held until terminated, so a sibling
    delivery with a different task ID can prove the guard scopes to the
    delivery, not to the scope.
    """
    for connection in connections.all(initialized_only=True):
        connection.connection = None

    def fake_judge_batch(units, **kwargs):
        ready.set()
        time.sleep(30)
        return {
            unit.id: JudgeVerdict.objects.create(
                unit=unit,
                max_severity="none",
                model_verdict=JudgeVerdict.Verdict.PASS,
                judge_model="vendor-a/model",
                seat=1,
                target_hash=compute_target_hash(build_request(unit).target_plurals),
                context_hash="held-boundary",
            )
            for unit in units
        }

    try:
        with (
            mock.patch(
                "weblate.trans.autotranslate.current_task", _fake_current_task(task_id)
            ),
            mock.patch.object(tasks, "current_task", _fake_current_task(task_id)),
            mock.patch.object(
                tasks, "heartbeat_task", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "touch_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "delete_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "register_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "_preload_workflow_settings",
                _noop_preload_workflow_settings,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge_batch,
            ),
            mock.patch.object(
                tasks, "get_auto_translate_target", autospec=True
            ) as get_target,
            mock.patch.object(
                tasks, "store_auto_translate_activity_log", autospec=True
            ) as store_log,
        ):
            get_target.return_value = (Component.objects.get(pk=component_id), {})
            store_log.side_effect = lambda _log, result, **_kwargs: result
            tasks.auto_translate._orig_run.__func__(  # ruff: ignore[private-member-access]
                _ProcessTask(task_id, _GUARD_RETRY_DELIVERY),
                user_id=user_id,
                mode="judge",
                q="",
                auto_source="mt",
                source_component_id=None,
                engines=[],
                threshold=80,
                unit_ids=unit_ids,
                translation_id=None,
                component_id=None,
                category_id=None,
                project_id=None,
                language_id=None,
                workspace_id=None,
                activity_log_id=None,
                activity_log_task_count=None,
                enforce_permissions=False,
                overwrite_existing=False,
                producer_run_id=None,
                judge_pretranslate=False,
                judge_mutating_repairs=True,
                judge_candidate_severities=("critical", "major"),
                judge_proposal_only=False,
            )
    finally:
        connections.close_all()


def _run_guarded_component_process(
    task_id: str, component_id: int, user_id: int
) -> None:
    """Run one ``auto_translate_component`` delivery against the same boundary."""
    for connection in connections.all(initialized_only=True):
        connection.connection = None

    def fake_judge_batch(units, **kwargs):
        return {
            unit.id: JudgeVerdict.objects.create(
                unit=unit,
                max_severity="none",
                model_verdict=JudgeVerdict.Verdict.PASS,
                judge_model="vendor-a/model",
                seat=1,
                target_hash=compute_target_hash(build_request(unit).target_plurals),
                context_hash="component-replay",
            )
            for unit in units
        }

    try:
        with (
            mock.patch(
                "weblate.trans.autotranslate.current_task", _fake_current_task(task_id)
            ),
            mock.patch.object(tasks, "current_task", _fake_current_task(task_id)),
            mock.patch.object(
                tasks, "heartbeat_task", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "touch_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "delete_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                tasks, "register_task_liveness", side_effect=lambda *_a, **_k: None
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "_preload_workflow_settings",
                _noop_preload_workflow_settings,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge_batch,
            ),
            mock.patch.object(
                tasks, "store_auto_translate_activity_log", autospec=True
            ) as store_log,
        ):
            store_log.side_effect = lambda _log, result, **_kwargs: result
            tasks.auto_translate_component._orig_run.__func__(  # ruff: ignore[private-member-access]
                _ProcessTask(task_id, _GUARD_RETRY_DELIVERY),
                component_id,
                mode="judge",
                q="",
                auto_source="mt",
                engines=[],
                threshold=80,
                user_id=user_id,
                enforce_permissions=False,
            )
    finally:
        connections.close_all()


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_UNITS_PER_RUN=2000,
    JUDGE_MAY_APPROVE=False,
    JUDGE_GUARD_WAIT_RETRIES=3,
)
class PersistedProducerRunRecoveryTest(RepoTestMixin, TransactionTestCase):
    """
    Two independent processes race one Celery delivery through a crash.

    The first worker persists one judge outcome and dies before the run is
    finalized. The second delivery must adopt the same durable run, skip
    the already recorded unit and finish the rest with the full summary.
    """

    def setUp(self) -> None:
        self.clone_test_repos()
        super().setUp()
        self.component = self.create_component()
        self.component.create_path()
        self.project = self.component.project
        setup_project_groups(self, self.project)
        self.translation = self.component.translation_set.get(language__code="cs")
        self.user = create_test_user()
        self.user.groups.add(Group.objects.get(name="Users"))
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    def get_unit(self, language: str, source: str):
        translation = self.component.translation_set.get(language__code=language)
        return translation.unit_set.get(source__startswith=source)

    def _mark_judgeable(self, language: str, source: str, target: str) -> int:
        unit = self.get_unit(language, source)
        unit.translate(self.user, [target], STATE_TRANSLATED)
        return unit.pk

    def _run_delivery(
        self,
        task_id: str,
        hold_after: int,
        unit_ids: list[int],
        timeout: int = 120,
    ) -> None:
        context = multiprocessing.get_context("fork")
        process = context.Process(
            target=_run_guarded_auto_translate_process,
            args=(task_id, self.component.pk, self.user.pk, hold_after, unit_ids),
        )
        # Never let a child inherit a live parent connection.
        connections.close_all()
        process.start()
        process.join(timeout=timeout)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            self.fail("guarded delivery exceeded its test deadline")

    def _judged_rows(self, run):
        return list(
            JudgeRunUnit.objects.filter(run=run)
            .exclude(
                outcome__in=(
                    JudgeRunUnit.Outcome.SKIPPED,
                    JudgeRunUnit.Outcome.PENDING,
                )
            )
            .order_by("unit_id_snapshot")
        )

    def test_killed_owner_leaves_persisted_rows_and_recovery_finishes_run(self) -> None:
        # One judgeable string per translation, so the batch needs two
        # provider calls and can be interrupted between them.
        scope = [
            self._mark_judgeable("cs", "Hello, world!\n", "Ahoj, světe!"),
            self._mark_judgeable("de", "Hello, world!\n", "Hallo, Welt!"),
        ]

        task_id = "persisted-crash-delivery"
        # First worker: persists the first translation's outcome and dies
        # before the producer run is finalized.
        self._run_delivery(task_id, hold_after=1, unit_ids=scope)

        first_run = ProducerRun.objects.get(task_id=task_id)
        self.assertEqual(first_run.status, ProducerRun.Status.RUNNING)
        self.assertIsNone(first_run.finished)
        rows = self._judged_rows(first_run)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].outcome, JudgeRunUnit.Outcome.PASSED)
        self.assertEqual(rows[0].verdict.max_severity, "none")
        # The unjudged scope unit keeps its durable PENDING reservation
        # across the crash: honest coverage instead of empty evidence.
        self.assertCountEqual(
            JudgeRunUnit.objects.filter(
                run=first_run, outcome=JudgeRunUnit.Outcome.PENDING
            ).values_list("unit_id_snapshot", flat=True),
            [pk for pk in scope if pk != rows[0].unit_id_snapshot],
        )
        persisted_verdict_id = rows[0].verdict_id
        persisted_unit_id = rows[0].unit_id_snapshot

        # Redelivery after the crash: same task ID, same durable run.
        self._run_delivery(task_id, hold_after=99, unit_ids=scope)

        recovered = ProducerRun.objects.get(task_id=task_id)
        self.assertEqual(recovered.pk, first_run.pk)
        self.assertEqual(recovered.status, ProducerRun.Status.COMPLETED)
        self.assertIsNotNone(recovered.finished)
        recovered_rows = self._judged_rows(recovered)
        self.assertEqual(len(recovered_rows), 2)
        self.assertEqual(
            {row.outcome for row in recovered_rows},
            {JudgeRunUnit.Outcome.PASSED},
        )
        # The already recorded unit keeps its first attempt's evidence: it
        # was never sent to the provider again.
        persisted = next(
            row for row in recovered_rows if row.unit_id_snapshot == persisted_unit_id
        )
        self.assertEqual(persisted.verdict_id, persisted_verdict_id)
        self.assertEqual(
            JudgeVerdict.objects.filter(unit_id=persisted_unit_id).count(), 1
        )
        # The summary counts the whole journal, not the last attempt.
        self.assertEqual(recovered.summary["evaluated"], 2)
        self.assertEqual(recovered.summary["nothing_blocking"], 2)

    def test_distinct_run_ids_run_independently(self) -> None:
        # While A holds its execution guard at the provider boundary, a
        # delivery with a different task ID judges and completes its own
        # run: the guard scopes to the delivery, not to the scope.
        scope = [
            self._mark_judgeable("cs", "Hello, world!\n", "Ahoj, světe!"),
            self._mark_judgeable("de", "Hello, world!\n", "Hallo, Welt!"),
        ]
        context = multiprocessing.get_context("fork")
        held = context.Event()
        owner_a = context.Process(
            target=_run_held_auto_translate_process,
            args=("run-a", self.component.pk, self.user.pk, held, scope),
        )
        # Never let a child inherit a live parent connection.
        connections.close_all()
        try:
            owner_a.start()
            self.assertTrue(held.wait(timeout=30))
            # A's guard is held: B proceeds on its own key.
            self._run_delivery("run-b", hold_after=99, unit_ids=scope)
        finally:
            if owner_a.is_alive():
                owner_a.terminate()
            owner_a.join(timeout=10)

        run_b = ProducerRun.objects.get(task_id="run-b")
        self.assertEqual(run_b.status, ProducerRun.Status.COMPLETED)
        self.assertEqual(len(self._judged_rows(run_b)), 2)
        self.assertEqual(run_b.summary["evaluated"], 2)
        # B never touched A's still-open run.
        run_a = ProducerRun.objects.get(task_id="run-a")
        self.assertNotEqual(run_a.pk, run_b.pk)
        self.assertEqual(run_a.status, ProducerRun.Status.RUNNING)
        self.assertIsNone(run_a.finished)

    def _run_component_delivery(self, task_id: str, timeout: int = 120) -> None:
        context = multiprocessing.get_context("fork")
        process = context.Process(
            target=_run_guarded_component_process,
            args=(task_id, self.component.pk, self.user.pk),
        )
        connections.close_all()
        process.start()
        process.join(timeout=timeout)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            self.fail("component delivery exceeded its test deadline")

    def test_component_task_terminal_redelivery_is_read_only(self) -> None:
        # One judgeable string is enough: the guard, not the batch size,
        # decides the terminal-replay behavior here.
        self._mark_judgeable("cs", "Hello, world!\n", "Ahoj, světe!")
        # The delivery runs over the whole component, and the mandatory
        # barrier refuses to judge an empty string that no engine can prepare,
        # so every other string already carries text.
        for scope_unit in Unit.objects.filter(
            translation__component=self.component
        ).select_related("translation"):
            if scope_unit.translation.is_source:
                continue
            if not any(scope_unit.get_target_plurals()):
                forms = (
                    scope_unit.translation.plural.number if scope_unit.is_plural else 1
                )
                scope_unit.translate(self.user, ["Text"] * forms, STATE_TRANSLATED)
        task_id = "component-terminal-replay"
        self._run_component_delivery(task_id)

        first_run = ProducerRun.objects.get(task_id=task_id)
        self.assertEqual(first_run.status, ProducerRun.Status.COMPLETED)
        first_finished = first_run.finished
        self.assertIsNotNone(first_finished)
        verdict_count = JudgeVerdict.objects.count()
        row_count = JudgeRunUnit.objects.filter(run=first_run).count()

        # The same delivery arriving again after completion must not judge
        # again, must not create a second run and must not touch the report.
        self._run_component_delivery(task_id)

        self.assertEqual(ProducerRun.objects.filter(task_id=task_id).count(), 1)
        first_run.refresh_from_db()
        self.assertEqual(first_run.finished, first_finished)
        self.assertEqual(JudgeVerdict.objects.count(), verdict_count)
        self.assertEqual(JudgeRunUnit.objects.filter(run=first_run).count(), row_count)


class _ProcessTask:
    def __init__(self, task_id: str, retry_result) -> None:
        self.request = SimpleNamespace(id=task_id)
        self._retry_result = retry_result

    def retry(self, **_kwargs):
        return self._retry_result


class _ProcessBatch:
    counter = None
    ready = None
    hold = False

    def __init__(self, *_args, **_kwargs) -> None:
        self.active_producer_run = None

    def perform(self, **_kwargs) -> str:
        self.counter.value += 1
        if self.hold:
            self.ready.set()
            time.sleep(30)
        return "provider called"

    def get_warnings(self) -> list[str]:
        return []


def _run_auto_translate_process(
    task_id: str, counter, ready, hold: bool, result
) -> None:

    _ProcessBatch.counter = counter
    _ProcessBatch.ready = ready
    _ProcessBatch.hold = hold
    tasks.get_auto_translate_target = lambda **_kwargs: (object(), {})
    tasks.BatchAutoTranslate = _ProcessBatch
    task = _ProcessTask(task_id, "retried")
    output = tasks.auto_translate._orig_run.__func__(  # ruff: ignore[private-member-access]
        task,
        user_id=None,
        mode="judge",
        q="",
        auto_source="mt",
        source_component_id=None,
        engines=[],
        threshold=80,
    )
    message = output["message"] if isinstance(output, dict) else output
    for index, character in enumerate(message):
        result[index] = character


class AutoTranslationTest(ViewTestCase):
    use_component_id: bool = False

    def setUp(self) -> None:
        super().setUp()
        # Need extra power
        self.user.is_superuser = True
        self.user.save()
        self.project.translation_review = True
        self.project.save()
        self.component2 = self.create_second_component()

    def create_second_component(self, project: Project | None = None) -> Component:
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            return Component.objects.create(
                name="Test 2",
                slug="test-2",
                project=self.project if project is None else project,
                repo=self.git_repo_path,
                push=self.git_repo_path,
                vcs="git",
                filemask="po/*.po",
                template="",
                file_format="po",
                new_base="",
                allow_translation_propagation=False,
            )

    def create_autotranslate_activity_log(
        self, component: Component | None = None
    ) -> AddonActivityLog:
        if component is None:
            component = self.component2
        addon = AutoTranslateAddon.create(
            component=component,
            run=False,
            configuration={
                "component": self.component.id,
                "q": "state:<translated",
                "auto_source": "others",
                "engines": [],
                "threshold": 100,
                "mode": "translate",
            },
        )
        return AddonActivityLog.objects.create(
            addon=addon.instance,
            component=component,
            event=AddonEvent.EVENT_COMPONENT_UPDATE,
            status=AddonActivityLog.Status.PENDING,
        )

    def test_none(self) -> None:
        """Test for automatic translation with no content."""
        response = self.client.post(
            reverse("auto_translation", kwargs=self.kw_translation)
        )
        self.assertRedirects(response, self.translation_url)

    def make_different(self, language: str = "cs") -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self.edit_unit("Hello, world!\n", "Nazdar svete!\n", language=language)

    def set_mismatched_plural(self) -> None:
        source_translation = self.get_translation()
        source_translation.plural = source_translation.language.plural_set.create(
            source=Plural.SOURCE_GETTEXT,
            number=2,
            formula="(n != 1)",
        )
        source_translation.save(update_fields=["plural"])

    def translate_plural_source(self) -> None:
        plural_unit = self.get_unit("Orangutan has %d banana.\n")
        plural_unit.translate(
            self.user,
            [
                "Orangutan ma %d banan.\n",
                "Orangutani maji %d banany.\n",
            ],
            STATE_TRANSLATED,
        )

    def perform_auto(
        self,
        expected=1,
        expected_count=None,
        path_params=None,
        success=True,
        prepare_source=True,
        **kwargs,
    ) -> None:
        if prepare_source:
            self.make_different()
        if path_params is None:
            path_params = {"path": [*self.component2.get_url_path(), "cs"]}
        url = reverse("auto_translation", kwargs=path_params)
        kwargs["auto_source"] = "others"
        kwargs["threshold"] = "100"
        if "q" not in kwargs:
            kwargs["q"] = "state:<translated"
        if "mode" not in kwargs:
            kwargs["mode"] = "translate"
        if self.use_component_id:
            kwargs["component"] = self.component.id
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, kwargs, follow=True)
        if expected == 0:
            expected_string = (
                "Automatic translation completed, no strings were updated."
            )
        elif expected == 1:
            expected_string = "Automatic translation completed, 1 string was updated."
        else:
            expected_string = (
                f"Automatic translation completed, {expected} strings were updated."
            )

        if success:
            self.assertRedirects(response, reverse("show", kwargs=path_params))
            self.assertContains(response, expected_string)

        # Check we've translated something
        component = Component.objects.get(pk=self.component2.pk)
        translation = component.translation_set.get(language_code="cs")
        with self.captureOnCommitCallbacks(execute=True):
            translation.invalidate_cache()
        if expected_count is None:
            expected_count = expected
        if kwargs["mode"] == "suggest":
            self.assertEqual(translation.stats.suggestions, expected_count)
        elif kwargs["mode"] == "fuzzy":
            self.assertEqual(translation.stats.fuzzy, expected_count)
        else:
            self.assertEqual(translation.stats.translated, expected_count)

    def test_different(self) -> None:
        """Test for automatic translation with different content."""
        self.perform_auto()

    def restrict_direct_editing(self) -> Translation:
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        self.user.groups.clear()
        group = Group.objects.create(
            name="Restricted automatic translation",
            language_selection=SELECTION_ALL,
        )
        group.components.add(self.component2)
        group.roles.add(
            Role.objects.get(name="Translate"),
            Role.objects.get(name="Automatic translation"),
        )
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

        translation = self.component2.translation_set.get(language_code="cs")
        WorkflowSetting.objects.create(
            project=translation.component.project,
            language=translation.language,
            restrict_direct_editing=True,
        )
        return Translation.objects.get(component=self.component2, language_code="cs")

    def test_restrict_direct_editing_blocks_automatic_translation(self) -> None:
        self.make_different()
        translation = self.restrict_direct_editing()

        self.assertTrue(self.user.has_perm("translation.auto", translation))
        self.perform_auto(expected=0, prepare_source=False)

    def test_restrict_direct_editing_allows_automatic_suggestions(self) -> None:
        self.make_different()
        translation = self.restrict_direct_editing()

        self.assertTrue(self.user.has_perm("translation.auto", translation))
        self.assertTrue(self.user.has_perm("suggestion.add", translation))
        self.perform_auto(mode="suggest", prepare_source=False)

    def test_restrict_direct_editing_in_component_batch(self) -> None:
        self.make_different()
        self.make_different("de")
        translation = self.restrict_direct_editing()
        german = self.component2.translation_set.get(language_code="de")
        initial_german_translated = german.stats.translated

        self.perform_auto(
            expected=1,
            expected_count=0,
            path_params={"path": self.component2.get_url_path()},
            prepare_source=False,
        )

        translation = Translation.objects.get(pk=translation.pk)
        german = Translation.objects.get(pk=german.pk)
        self.assertEqual(translation.stats.translated, 0)
        self.assertEqual(german.stats.translated, initial_german_translated + 1)

    def test_batch_preloads_workflow_settings(self) -> None:
        translation = self.component2.translation_set.get(language_code="cs")
        setting = WorkflowSetting.objects.create(
            project=translation.component.project,
            language=translation.language,
            restrict_direct_editing=True,
        )

        auto = BatchAutoTranslate(
            self.component2,
            user=self.user,
            q="state:<translated",
            mode="translate",
        )

        self.assertGreater(len(auto.translations), 1)
        with self.assertNumQueries(0):
            workflow_settings = [item.workflow_settings for item in auto.translations]
            restrictions = [item.restrict_direct_editing for item in auto.translations]
        self.assertIn(setting, workflow_settings)
        self.assertIn(True, restrictions)
        self.assertIn(False, restrictions)

    def test_readonly_empty_target_source_candidate(self) -> None:
        """Skip source candidates with empty targets even when read-only."""
        source_unit = self.get_unit("Hello, world!\n")
        Unit.objects.filter(pk=source_unit.pk).update(
            state=STATE_READONLY,
            target="",
        )
        translation = self.component2.translation_set.get(language_code="cs")
        target_unit = self.get_unit("Hello, world!\n", translation=translation)
        initial_pending = PendingUnitChange.objects.filter(unit=target_unit).count()

        result = auto_translate(
            translation_id=translation.id,
            user_id=self.user.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            source_component_id=self.component.id,
            engines=[],
            threshold=100,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, no strings were updated.",
        )
        target_unit.refresh_from_db()
        self.assertEqual(target_unit.target, "")
        self.assertFalse(target_unit.automatically_translated)
        self.assertEqual(
            PendingUnitChange.objects.filter(unit=target_unit).count(),
            initial_pending,
        )

    def test_plural_mismatch_warning(self) -> None:
        self.set_mismatched_plural()
        self.edit_unit("Thank you for using Weblate.", "Diky za pouzivani Weblate.")
        self.translate_plural_source()
        path_params = {"path": [*self.component2.get_url_path(), "cs"]}

        response = self.client.post(
            reverse("auto_translation", kwargs=path_params),
            {
                "auto_source": "others",
                "component": self.component.id,
                "threshold": "100",
                "q": "state:<translated",
                "mode": "translate",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("show", kwargs=path_params))
        self.assertContains(
            response,
            "Automatic translation completed, 1 string was updated.",
        )
        self.assertContains(response, "do not match the target translation")

        translation = self.component2.translation_set.get(language_code="cs")
        singular = self.get_unit(
            "Thank you for using Weblate.", translation=translation
        )
        self.assertEqual(singular.target, "Diky za pouzivani Weblate.")
        target_plural = self.get_unit(
            "Orangutan has %d banana.\n", translation=translation
        )
        self.assertEqual(target_plural.get_target_plurals(), ["", "", ""])

    def test_plural_mismatch_task_warning(self) -> None:
        self.set_mismatched_plural()
        self.edit_unit("Thank you for using Weblate.", "Diky za pouzivani Weblate.")
        self.translate_plural_source()
        activity_log = self.create_autotranslate_activity_log()

        result = auto_translate(
            translation_id=self.component2.translation_set.get(language_code="cs").id,
            user_id=self.user.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            source_component_id=self.component.id,
            engines=[],
            threshold=100,
            activity_log_id=activity_log.id,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, 1 string was updated.",
        )
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("do not match the target translation", result["warnings"][0])
        activity_log.refresh_from_db()
        self.assertEqual(activity_log.status, AddonActivityLog.Status.SUCCESS)
        self.assertEqual(
            activity_log.details["result"]["message"],
            "Automatic translation completed, 1 string was updated.",
        )
        self.assertEqual(len(activity_log.details["result"]["warnings"]), 1)
        self.assertIn(
            "do not match the target translation",
            activity_log.details["result"]["warnings"][0],
        )

    def test_autotranslate_missing_target_returns_result_dict(self) -> None:
        activity_log = self.create_autotranslate_activity_log()
        translation = self.component2.translation_set.get(language_code="cs")
        translation_id = translation.id
        translation.delete()

        result = auto_translate(
            translation_id=translation_id,
            user_id=self.user.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            source_component_id=self.component.id,
            engines=[],
            threshold=100,
            activity_log_id=activity_log.id,
        )

        self.assertEqual(
            result,
            {
                "message": "Automatic translation skipped because the target no longer exists.",
                "warnings": [],
            },
        )
        activity_log.refresh_from_db()
        self.assertEqual(activity_log.status, AddonActivityLog.Status.SKIPPED)
        self.assertEqual(activity_log.details["reason"], "target-missing")

    def test_suggest(self) -> None:
        """Test for automatic suggestion."""
        self.perform_auto(mode="suggest")
        self.perform_auto(0, 1, mode="suggest")

    def test_approved(self) -> None:
        """Test for automatic suggestion."""
        self.perform_auto(mode="approved")
        self.perform_auto(0, 1, mode="approved")

    def test_approved_requires_review_permission(self) -> None:
        limited_user = User.objects.create_user(
            "limited-auto-approve",
            "limited-auto-approve@example.com",
            "limited-auto-approve",
        )
        group = Group.objects.create(
            name="Limited automatic approval",
            language_selection=SELECTION_ALL,
        )
        group.projects.add(self.project)
        group.roles.add(Role.objects.get(name="Automatic translation"))
        limited_user.groups.add(group)
        limited_user.clear_permissions_cache()
        translation = self.component2.translation_set.get(language_code="cs")
        unit = self.get_unit("Hello, world!\n", translation=translation)
        group.projects.add(translation.component.project)
        limited_user.clear_permissions_cache()

        self.assertTrue(limited_user.has_perm("translation.auto", translation))
        self.assertFalse(limited_user.has_perm("unit.review", unit))

        self.make_different()
        result = auto_translate(
            translation_id=translation.id,
            user_id=limited_user.id,
            mode="approved",
            q="state:<translated",
            auto_source="others",
            source_component_id=self.component.id,
            engines=[],
            threshold=100,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, no strings were updated.",
        )
        unit.refresh_from_db()
        self.assertNotEqual(unit.state, STATE_APPROVED)

    def test_fuzzy(self) -> None:
        """Test for automatic suggestion in fuzzy mode."""
        self.perform_auto(mode="fuzzy")

    def test_inconsistent(self) -> None:
        self.perform_auto(0, q="check:inconsistent")

    def test_overwrite(self) -> None:
        self.perform_auto(overwrite="1")

    def test_autotranslate_component(self) -> None:
        self.make_different("de")
        de_translation = self.component2.translation_set.get(language_code="de")
        initial_stats = de_translation.stats.translated
        self.perform_auto(
            path_params={"path": self.component2.get_url_path()},
            expected=2,
            expected_count=1,  # we only expect one new translation in 'cs'
        )
        component = Component.objects.get(pk=self.component2.pk)
        de_translation = component.translation_set.get(language_code="de")
        with self.captureOnCommitCallbacks(execute=True):
            de_translation.invalidate_cache()
        self.assertEqual(de_translation.stats.translated, initial_stats + 1)

    def test_autotranslate_category(self) -> None:
        self.component.category = self.create_category(project=self.project)
        category = self.component.category
        if self.component2.project != self.project:
            category = self.create_category(project=self.component2.project)
        self.component2.category = category
        self.component.save()
        self.component2.save()

        self.make_different("de")

        self.perform_auto(
            path_params={"path": category.get_url_path()},
            expected=2,
            expected_count=1,  # we only expect one new translation in 'cs'
        )

    def test_autotranslate_project_language(self) -> None:
        project_language = ProjectLanguage(
            self.component2.project,
            language=Language.objects.get(code="cs"),
        )
        self.make_different("de")

        self.perform_auto(
            path_params={"path": project_language.get_url_path()},
            expected_count=1,
            expected=1,
        )

    def test_progress_maps_into_assigned_range(self) -> None:
        """A progress slice scales the reported percentage into itself."""
        task = SimpleNamespace(
            request=SimpleNamespace(id="task-progress"), update_state=Mock()
        )
        auto = AutoTranslate(
            user=self.user,
            translation=self.component2.translation_set.get(language_code="cs"),
            q="state:<translated",
            mode="translate",
        )
        auto.progress_steps = 4
        auto.progress_range = (20, 40)

        with patch("weblate.trans.autotranslate.current_task", task):
            auto.set_progress(2)

        self.assertEqual(
            task.update_state.call_args.kwargs["meta"]["progress"],
            30,
        )

    def test_batch_progress_never_goes_back(self) -> None:
        """Each translation of a batch reports into its own progress slice."""
        # Two languages get a source to copy, so both report progress of
        # their own units on top of the per-translation steps of the batch.
        self.make_different()
        self.make_different("de")
        task = SimpleNamespace(
            request=SimpleNamespace(id="task-progress"), update_state=Mock()
        )
        auto = BatchAutoTranslate(
            self.component2,
            user=self.user,
            q="state:<translated",
            mode="translate",
        )
        self.assertGreater(len(auto.translations), 1)

        with (
            patch("weblate.trans.autotranslate.current_task", task),
            self.captureOnCommitCallbacks(execute=True),
        ):
            auto.perform(
                auto_source="others",
                engines=[],
                threshold=100,
                source_component_ids=[self.component.id],
            )

        progress_values = [
            call.kwargs["meta"]["progress"] for call in task.update_state.call_args_list
        ]
        self.assertGreater(len(progress_values), len(auto.translations))
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertEqual(progress_values[-1], 100)
        self.assertGreaterEqual(min(progress_values), 0)

    def test_attempt_counter_reports_batch_done_total(self) -> None:
        """The progress meta carries the attempt-local updated/eligible."""
        # Two languages get a source to copy from the first component.
        self.make_different()
        self.make_different("de")
        task = SimpleNamespace(
            request=SimpleNamespace(id="task-counter"), update_state=Mock()
        )
        auto = BatchAutoTranslate(
            self.component2,
            user=self.user,
            q="state:<translated",
            mode="translate",
        )
        self.assertGreater(len(auto.translations), 1)

        with (
            patch("weblate.trans.autotranslate.current_task", task),
            patch("weblate.trans.autotranslate.touch_task_liveness"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            auto.perform(
                auto_source="others",
                engines=[],
                threshold=100,
                source_component_ids=[self.component.id],
            )

        metas = [call.kwargs["meta"] for call in task.update_state.call_args_list]
        with_totals = [meta for meta in metas if "total" in meta]
        self.assertTrue(with_totals)
        # The denominator is one snapshot for the whole batch: the units of
        # every translation, not of the one currently being processed.
        self.assertEqual(len({meta["total"] for meta in with_totals}), 1)
        total = with_totals[0]["total"]
        # Both untranslated translations of the batch are in the snapshot,
        # not just the one being processed.
        self.assertGreater(total, auto.translations[0].unit_set.count())
        # The numerator only grows, never resets per translation, and never
        # exceeds the snapshot: `done` counts stored units, which can be
        # fewer than `eligible` when a source is missing.
        dones = [meta["done"] for meta in with_totals]
        self.assertEqual(dones, sorted(dones))
        self.assertEqual(dones[-1], auto.updated)
        self.assertLessEqual(dones[-1], total)

    def test_autotranslate_project_language_limited_membership(self) -> None:
        czech = Language.objects.get(code="cs")
        project_language = ProjectLanguage(self.component2.project, language=czech)
        group = Group.objects.create(
            name="Czech automatic translation",
            language_selection=SELECTION_ALL,
        )
        group.projects.add(self.component2.project)
        group.roles.add(Role.objects.get(name="Automatic translation"))
        self.user.groups.add(group)
        TeamMembership.objects.get(user=self.user, group=group).limit_languages.add(
            czech
        )
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        self.user.clear_permissions_cache()

        response = self.client.get(
            reverse("show", kwargs={"path": project_language.get_url_path()})
        )
        self.assertContains(response, "Automatic translation")
        self.assertFalse(
            self.user.has_perm("translation.auto", self.component2.project)
        )
        self.assertTrue(self.user.has_perm("translation.auto", project_language))

        self.perform_auto(
            path_params={"path": project_language.get_url_path()},
            expected_count=1,
            expected=1,
        )

    def test_autotranslate_workspace(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        Project.objects.filter(
            pk__in={self.project.pk, self.component2.project_id}
        ).update(workspace=workspace)

        response = self.client.get(workspace.get_absolute_url())
        self.assertContains(response, "Batch automatic translation")

        self.make_different()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("auto_translation", kwargs={"path": workspace.get_url_path()}),
                {
                    "auto_source": "others",
                    "threshold": "100",
                    "q": "state:<translated",
                    "mode": "translate",
                },
                follow=True,
            )
        self.assertRedirects(response, workspace.get_absolute_url())
        self.assertContains(
            response, "Automatic translation completed, 1 string was updated."
        )
        translation = self.component2.translation_set.get(language_code="cs")
        with self.captureOnCommitCallbacks(execute=True):
            translation.invalidate_cache()
        self.assertEqual(translation.stats.translated, 1)

    @override_settings(
        WEBLATE_MACHINERY=(
            *settings.WEBLATE_MACHINERY,
            "weblate.machinery.dummy.DummyTranslation",
        )
    )
    def test_autotranslate_workspace_project_machinery_settings(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        self.project.workspace = workspace
        self.component2.project.workspace = workspace
        identifier = DummyTranslation.get_identifier()
        self.project.machinery_settings[identifier] = {}
        self.project.save(update_fields=["workspace", "machinery_settings"])
        self.component2.project.save(update_fields=["workspace"])
        Setting.objects.filter(category=SettingCategory.MT, name=identifier).delete()

        response = self.client.get(workspace.get_absolute_url())

        self.assertContains(response, f'value="{identifier}"')
        self.assertContains(response, "Dummy")

    @override_settings(
        WEBLATE_MACHINERY=(
            *settings.WEBLATE_MACHINERY,
            "weblate.machinery.dummy.DummyTranslation",
        )
    )
    def test_autotranslate_workspace_machine_translation(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        project = Project.objects.create(
            name="Machine translation project",
            slug="machine-translation-project",
            web="https://nonexisting.weblate.org/",
            workspace=workspace,
        )
        component = self.create_po_new_base(name="Machine component", project=project)
        identifier = DummyTranslation.get_identifier()
        project.machinery_settings[identifier] = {}
        project.save(update_fields=["machinery_settings"])

        result = auto_translate(
            workspace_id=str(workspace.pk),
            user_id=self.user.id,
            mode="translate",
            q="state:<translated",
            auto_source="mt",
            source_component_id=None,
            engines=[identifier],
            threshold=100,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, 2 strings were updated.",
        )
        translation = component.translation_set.get(language_code="cs")
        unit = self.get_unit("Hello, world!\n", translation=translation)
        self.assertIn(unit.target, {"Nazdar světe!\n", "Ahoj světe!\n"})

    def test_autotranslate_workspace_ignores_locked_components(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        Project.objects.filter(
            pk__in={self.project.pk, self.component2.project_id}
        ).update(workspace=workspace)
        locked_component = self.create_po_new_base(
            name="Locked component", project=self.project
        )
        locked_component.locked = True
        locked_component.save(update_fields=["locked"])

        self.make_different()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("auto_translation", kwargs={"path": workspace.get_url_path()}),
                {
                    "auto_source": "others",
                    "threshold": "100",
                    "q": "state:<translated",
                    "mode": "translate",
                },
                follow=True,
            )

        self.assertRedirects(response, workspace.get_absolute_url())
        self.assertContains(
            response, "Automatic translation completed, 1 string was updated."
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_judge_workspace_ignores_source_selection(self) -> None:
        workspace = Workspace.objects.create(name="Judge workspace")
        self.project.workspace = workspace
        self.component.source_language = Language.objects.get(code="de")
        self.project.save(update_fields=["workspace"])
        self.component.save(update_fields=["source_language"])
        self.get_unit().translate(self.user, ["Judgeable target"], STATE_TRANSLATED)

        with (
            patch.object(AutoTranslate, "process_mt"),
            patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run,
        ):
            auto_translate(
                workspace_id=str(workspace.pk),
                user_id=self.user.id,
                mode="judge",
                q="",
                auto_source="others",
                source_component_id=self.component.id,
                engines=[],
                threshold=100,
            )

        self.assertTrue(run.called)

    def test_autotranslate_workspace_skips_mismatched_selected_source(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        self.project.workspace = workspace
        self.component2.project.workspace = workspace
        self.component.source_language = Language.objects.get(code="de")
        self.project.save(update_fields=["workspace"])
        self.component2.project.save(update_fields=["workspace"])
        self.component.save(update_fields=["source_language"])

        self.make_different()
        result = auto_translate(
            workspace_id=str(workspace.pk),
            user_id=self.user.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            source_component_id=self.component.id,
            engines=[],
            threshold=100,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, no strings were updated.",
        )
        self.assertEqual(
            result["warnings"],
            [
                (
                    "Automatic translation skipped some translations because selected "
                    "source components use a different source language."
                )
            ],
        )

    def test_autotranslate_workspace_skips_target_as_source(self) -> None:
        workspace = Workspace.objects.create(name="Automatic translation workspace")
        self.project.workspace = workspace
        self.component.source_language = Language.objects.get(code="de")
        self.project.save(update_fields=["workspace"])
        self.component.save(update_fields=["source_language"])

        self.make_different()
        result = auto_translate(
            workspace_id=str(workspace.pk),
            user_id=self.user.id,
            mode="fuzzy",
            q="state:translated",
            auto_source="others",
            source_component_id=None,
            engines=[],
            threshold=100,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, no strings were updated.",
        )
        self.assertEqual(
            result["warnings"],
            [
                (
                    "Automatic translation skipped some translations because "
                    "no other source components were available."
                )
            ],
        )

    def test_autotranslate_fail(self) -> None:

        self.user.is_superuser = False
        self.user.save()

        # test missing autotranslate permission on project
        self.perform_auto(
            expected=0, path_params={"path": self.project.get_url_path()}, success=False
        )
        # test missing autotranslate permission on translation
        self.perform_auto(expected=0, success=False)

        # test missing autotranslate permission on project language
        project_language = ProjectLanguage(
            self.project,
            language=Language.objects.get(code="cs"),
        )
        self.perform_auto(
            expected=0,
            path_params={"path": project_language.get_url_path()},
            success=False,
        )

        # test missing autotranslate permission on category
        category = self.create_category(project=self.project)
        self.component.category = self.component2.category = category
        self.component.save()
        self.perform_auto(
            path_params={"path": category.get_url_path()}, expected=0, success=False
        )
        self.perform_auto(
            path_params={"path": self.component.get_url_path()},
            expected=0,
            success=False,
        )

        # test invalid arguments
        with self.assertRaises(ValueError):
            auto_translate(
                user_id=None,
                mode="suggest",
                q="state:<translated",
                auto_source="others",
                source_component_id=None,
                engines=["weblate"],
                threshold=100,
            )

    def test_auto_translate_accepts_a_project_target(self) -> None:
        with patch(
            "weblate.trans.tasks.BatchAutoTranslate.perform",
            return_value="completed",
        ) as perform:
            result = auto_translate(
                user_id=self.user.id,
                mode="judge",
                q="state:empty",
                auto_source="mt",
                source_component_id=None,
                engines=[],
                threshold=80,
                project_id=self.project.id,
            )

        perform.assert_called_once()
        self.assertEqual(result["project"], self.project.id)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_judge_task_result_and_report_url_share_the_same_run(self) -> None:
        with patch("weblate.trans.autotranslate.run_judge_batch", return_value={}):
            result = auto_translate(
                user_id=self.user.id,
                mode="judge",
                q="state:empty",
                auto_source="mt",
                source_component_id=None,
                engines=[],
                threshold=80,
                component_id=self.component.id,
                enforce_permissions=False,
            )
        run = ProducerRun.objects.get()
        self.assertIn("report_url", result)
        self.assertEqual(
            result["report_url"], reverse("judge-run", kwargs={"pk": run.pk})
        )

    def test_non_judge_task_result_links_to_its_report(self) -> None:
        result = auto_translate(
            user_id=self.user.id,
            mode="translate",
            q="",
            auto_source="mt",
            source_component_id=None,
            engines=["weblate"],
            threshold=80,
            component_id=self.component.id,
            enforce_permissions=False,
        )
        run = ProducerRun.objects.get()
        self.assertEqual(
            result["report_url"], reverse("judge-run", kwargs={"pk": run.pk})
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_auto_translate_component_task_result_carries_report_url(self) -> None:
        with patch("weblate.trans.autotranslate.run_judge_batch", return_value={}):
            result = auto_translate_component(
                self.component.id,
                mode="judge",
                q="state:empty",
                auto_source="mt",
                engines=[],
                threshold=80,
                user_id=self.user.id,
                enforce_permissions=False,
            )
        run = ProducerRun.objects.get()
        self.assertEqual(
            result["report_url"], reverse("judge-run", kwargs={"pk": run.pk})
        )

    def test_labeling(self) -> None:
        self.perform_auto(overwrite="1")
        translation = self.component2.translation_set.get(language_code="cs")
        self.assertEqual(
            translation.unit_set.filter(automatically_translated=True).count(),
            1,
        )
        self.edit_unit("Thank you for using Weblate.", "Díky za používání Weblate.")
        self.assertEqual(
            translation.unit_set.filter(automatically_translated=True).count(),
            1,
        )
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n", translation=translation)
        self.assertEqual(
            translation.unit_set.filter(automatically_translated=True).count(),
            0,
        )

    def test_automatically_translated_column(self) -> None:
        """Test that automatically_translated column is set correctly."""
        translation = self.component2.translation_set.get(language_code="cs")
        self.assertEqual(
            translation.unit_set.filter(automatically_translated=True).count(),
            0,
        )

        self.perform_auto(overwrite="1")

        auto_unit = translation.unit_set.filter(automatically_translated=True).first()
        self.assertIsNotNone(auto_unit)
        self.assertTrue(auto_unit.automatically_translated)

        auto_unit.translate(
            self.user,
            "Manually edited translation",
            auto_unit.state,
        )

        auto_unit.refresh_from_db()
        self.assertFalse(auto_unit.automatically_translated)

        self.assertEqual(
            translation.unit_set.filter(automatically_translated=True).count(),
            0,
        )

    def test_autotranslate_creates_change_and_pending(self) -> None:
        """Auto-translation creates Change and PendingUnitChange records in bulk."""
        self.make_different()
        translation = self.component2.translation_set.get(language_code="cs")

        initial_change_count = Change.objects.count()
        initial_pending_count = PendingUnitChange.objects.count()

        self.perform_auto()

        self.assertGreater(Change.objects.count(), initial_change_count)
        self.assertTrue(Change.objects.filter(action=ActionEvents.AUTO).exists())
        self.assertGreater(PendingUnitChange.objects.count(), initial_pending_count)
        auto_translated_unit = translation.unit_set.get(automatically_translated=True)
        self.assertTrue(
            PendingUnitChange.objects.filter(unit=auto_translated_unit).exists()
        )

    def test_autotranslate_component_uses_supplied_user(self) -> None:
        self.make_different()
        translation = self.component2.translation_set.get(language_code="cs")

        auto_translate_component(
            self.component2.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            engines=[],
            threshold=100,
            source_component_id=self.component.id,
            user_id=self.user.id,
        )

        auto_translated_unit = translation.unit_set.get(automatically_translated=True)
        self.assertEqual(
            auto_translated_unit.change_set.get(action=ActionEvents.AUTO).author,
            self.user,
        )
        self.assertTrue(
            PendingUnitChange.objects.filter(
                unit=auto_translated_unit,
                author=self.user,
                automatically_translated=True,
            ).exists()
        )

    def test_autotranslate_component_stores_activity_log_result(self) -> None:
        self.make_different()
        activity_log = self.create_autotranslate_activity_log()

        result = auto_translate_component(
            self.component2.id,
            mode="translate",
            q="state:<translated",
            auto_source="others",
            engines=[],
            threshold=100,
            source_component_id=self.component.id,
            user_id=self.user.id,
            activity_log_id=activity_log.id,
        )

        self.assertEqual(
            result["message"],
            "Automatic translation completed, 1 string was updated.",
        )
        self.assertEqual(result["warnings"], [])
        activity_log.refresh_from_db()
        self.assertEqual(activity_log.status, AddonActivityLog.Status.SUCCESS)
        self.assertEqual(activity_log.details["result"], result)

    def test_autotranslate_component_records_empty_engine_warning(self) -> None:
        activity_log = self.create_autotranslate_activity_log()

        result = auto_translate_component(
            self.component2.id,
            mode="translate",
            q="state:empty",
            auto_source="mt",
            engines=[],
            threshold=80,
            user_id=self.user.id,
            activity_log_id=activity_log.id,
        )

        warning = (
            "No machine translation engine was selected, so no strings were "
            "machine translated."
        )
        self.assertEqual(result["warnings"], [warning])
        activity_log.refresh_from_db()
        self.assertEqual(activity_log.details["result"]["warnings"], [warning])

    def test_autotranslate_component_failure_updates_activity_log(self) -> None:
        activity_log = self.create_autotranslate_activity_log()

        with patch("weblate.utils.errors.report_error"):
            task_result = auto_translate_component.apply(
                kwargs={
                    "component_id": 0,
                    "mode": "translate",
                    "q": "state:<translated",
                    "auto_source": "others",
                    "engines": [],
                    "threshold": 100,
                    "source_component_id": self.component.id,
                    "user_id": self.user.id,
                    "activity_log_id": activity_log.id,
                },
                throw=False,
            )

        self.assertTrue(task_result.failed())
        self.assertIsInstance(task_result.result, Component.DoesNotExist)
        activity_log.refresh_from_db()
        self.assertEqual(activity_log.status, AddonActivityLog.Status.ERROR)
        self.assertIn(
            "Component matching query does not exist", activity_log.details["result"]
        )

    def test_command(self) -> None:
        call_command("auto_translate", "test", "test", "cs")

    def test_command_add_error(self) -> None:
        with self.assertRaises(CommandError):
            call_command("auto_translate", "test", "test", "ia", add=True)

    def test_command_mt(self) -> None:
        call_command("auto_translate", "--mt", "weblate", "test", "test", "cs")

    def test_command_mt_error(self) -> None:
        with self.assertRaises(CommandError):
            call_command("auto_translate", "--mt", "invalid", "test", "test", "ia")
        with self.assertRaises(CommandError):
            call_command(
                "auto_translate", "--threshold", "invalid", "test", "test", "ia"
            )

    def test_command_add(self) -> None:
        self.component.file_format = "po"
        self.component.new_lang = "add"
        self.component.new_base = "po/cs.po"
        self.component.clean()
        self.component.save()
        call_command("auto_translate", "test", "test", "ia", add=True)
        self.assertTrue(
            self.component.translation_set.filter(language__code="ia").exists()
        )

    def test_command_different(self) -> None:
        self.make_different()
        call_command(
            "auto_translate",
            self.component2.project.slug,
            self.component2.slug,
            "cs",
            source=self.component.full_slug,
        )

    def test_command_errors(self) -> None:
        with self.assertRaises(CommandError):
            call_command("auto_translate", "test", "test", "cs", user="invalid")
        with self.assertRaises(CommandError):
            call_command("auto_translate", "test", "test", "cs", source="invalid")
        with self.assertRaises(CommandError):
            call_command("auto_translate", "test", "test", "cs", source="test/invalid")
        with self.assertRaises(CommandError):
            call_command("auto_translate", "test", "test", "xxx")


class AutoTranslationCrossProjectTest(AutoTranslationTest):
    use_component_id: bool = True

    def create_second_component(self, project: Project | None = None) -> Component:
        project = Project.objects.create(
            name="Other", slug="other", translation_review=True
        )
        return super().create_second_component(project=project)


class _FakeOpenRouterMachinery:
    name = "OpenRouter"

    def __init__(self, settings) -> None:
        pass

    @classmethod
    def get_identifier(cls) -> str:
        return "openrouter"


class _FakeLiteLLMMachinery:
    name = "LiteLLM"

    def __init__(self, settings) -> None:
        pass

    @classmethod
    def get_identifier(cls) -> str:
        return "litellm"


class AutoTranslationMtTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Need extra power
        self.user.is_superuser = True
        self.user.save()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            self.component3 = Component.objects.create(
                name="Test 3",
                slug="test-3",
                project=self.project,
                repo=self.git_repo_path,
                push=self.git_repo_path,
                vcs="git",
                filemask="po/*.po",
                template="",
                file_format="po",
                new_base="",
                allow_translation_propagation=False,
            )
        self.update_fulltext_index()
        self.configure_mt()

    def test_none(self) -> None:
        """Test for automatic translation with no content."""
        url = reverse("auto_translation", kwargs=self.kw_translation)
        response = self.client.post(url)
        self.assertRedirects(response, self.translation_url)

    def make_different(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")

    def perform_auto(self, expected=1, **kwargs) -> None:
        self.make_different()
        path_params = {"path": [*self.component3.get_url_path(), "cs"]}
        url = reverse("auto_translation", kwargs=path_params)
        kwargs["auto_source"] = "mt"
        if "q" not in kwargs:
            kwargs["q"] = "state:<translated"
        if "mode" not in kwargs:
            kwargs["mode"] = "translate"
        response = self.client.post(url, kwargs, follow=True)
        if expected == 1:
            self.assertContains(
                response, "Automatic translation completed, 1 string was updated."
            )
        else:
            self.assertContains(
                response, "Automatic translation completed, no strings were updated."
            )

        self.assertRedirects(response, reverse("show", kwargs=path_params))
        # Check we've translated something
        translation = self.component3.translation_set.get(language_code="cs")
        translation.invalidate_cache()
        self.assertEqual(translation.stats.translated, expected)

    def test_form_uses_list_initial_for_default_engine(self) -> None:
        form = AutoForm(self.component3, self.user)

        self.assertEqual(form.fields["engines"].initial, ["weblate"])

    def test_form_requires_an_engine_for_machine_translation(self) -> None:
        data = {
            "auto_source": "mt",
            "engines": [],
            "threshold": "80",
            "q": "state:empty",
            "mode": "translate",
        }
        form = AutoForm(self.component3, self.user, data)

        self.assertFalse(form.is_valid())
        self.assertIn("engines", form.errors)

        data["auto_source"] = "others"
        form = AutoForm(self.component3, self.user, data)

        self.assertTrue(form.is_valid(), form.errors)

    def test_form_preselects_openrouter_when_only_openrouter_is_configured(
        self,
    ) -> None:
        self.project.machinery_settings = {
            "openrouter": {"key": "or-key", "routing": {"*": "vendor/model"}}
        }
        self.project.save(update_fields=["machinery_settings"])
        with patch(
            "weblate.trans.forms.MACHINERY",
            {
                "openrouter": _FakeOpenRouterMachinery,
                "litellm": _FakeLiteLLMMachinery,
            },
        ):
            form = AutoForm(self.component3, self.user)
        self.assertEqual(form.fields["engines"].initial, ["openrouter"])
        self.assertEqual(form.fields["auto_source"].initial, "mt")

    def test_form_preselects_litellm_when_only_litellm_is_configured(self) -> None:
        self.project.machinery_settings = {
            "litellm": {"key": "ll-key", "routing": {"*": "vendor/model"}}
        }
        self.project.save(update_fields=["machinery_settings"])
        with patch(
            "weblate.trans.forms.MACHINERY",
            {
                "openrouter": _FakeOpenRouterMachinery,
                "litellm": _FakeLiteLLMMachinery,
            },
        ):
            form = AutoForm(self.component3, self.user)
        self.assertEqual(form.fields["engines"].initial, ["litellm"])
        self.assertEqual(form.fields["auto_source"].initial, "mt")

    def test_form_preselects_openrouter_over_litellm_when_both_are_configured(
        self,
    ) -> None:
        self.project.machinery_settings = {
            "openrouter": {"key": "or-key", "routing": {"*": "vendor/model"}},
            "litellm": {"key": "ll-key", "routing": {"*": "vendor/model"}},
        }
        self.project.save(update_fields=["machinery_settings"])
        with patch(
            "weblate.trans.forms.MACHINERY",
            {
                "openrouter": _FakeOpenRouterMachinery,
                "litellm": _FakeLiteLLMMachinery,
            },
        ):
            form = AutoForm(self.component3, self.user)
        self.assertEqual(form.fields["engines"].initial, ["openrouter"])

    def test_form_ignores_component_in_machine_translation_mode(self) -> None:
        form = AutoForm(
            self.component3,
            self.user,
            {
                "auto_source": "mt",
                "component": "missing-component",
                "engines": ["weblate"],
                "threshold": "80",
                "q": "state:empty",
                "mode": "fuzzy",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["component"])

    def test_invalid_form_shows_field_errors(self) -> None:
        path_params = {"path": [*self.component3.get_url_path(), "cs"]}
        response = self.client.post(
            reverse("auto_translation", kwargs=path_params),
            {
                "auto_source": "mt",
                "engines": ["invalid"],
                "threshold": "80",
                "q": "state:empty",
                "mode": "fuzzy",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("show", kwargs=path_params))
        self.assertContains(response, "Error in parameter engines")
        self.assertNotContains(response, "Could not process form!")

    def test_locked_target_shows_specific_error(self) -> None:
        self.component3.locked = True
        self.component3.save(update_fields=["locked"])
        path_params = {"path": [*self.component3.get_url_path(), "cs"]}
        response = self.client.post(
            reverse("auto_translation", kwargs=path_params),
            {
                "auto_source": "mt",
                "engines": ["weblate"],
                "threshold": "80",
                "q": "state:empty",
                "mode": "fuzzy",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("show", kwargs=path_params))
        self.assertContains(response, "This translation is currently locked.")
        self.assertNotContains(response, "Could not process form!")

    def test_different(self) -> None:
        """Test for automatic translation with different content."""
        self.perform_auto(engines=["weblate"], threshold=80)

    def test_mt_origin_uses_mt_user(self) -> None:
        self.perform_auto(engines=["weblate"], threshold=80)

        translation = self.component3.translation_set.get(language_code="cs")
        auto_translated_unit = translation.unit_set.get(automatically_translated=True)
        author = auto_translated_unit.change_set.get(action=ActionEvents.AUTO).author

        self.assertIsNotNone(author)
        self.assertEqual(getattr(author, "username", None), "mt:weblate")
        self.assertTrue(getattr(author, "is_bot", False))

    def test_multi(self) -> None:
        """Test for automatic translation with more providers."""
        self.perform_auto(
            engines=["weblate", "weblate-translation-memory"], threshold=80
        )

    def test_inconsistent(self) -> None:
        self.perform_auto(0, q="check:inconsistent", engines=["weblate"], threshold=80)

    def test_overwrite(self) -> None:
        self.perform_auto(overwrite="1", engines=["weblate"], threshold=80)

    def test_rate_limited_engine_reports_a_warning(self) -> None:
        """A run that skipped strings may not look like a complete one."""
        self.make_different()
        path_params = {"path": [*self.component3.get_url_path(), "cs"]}

        with patch(
            "weblate.machinery.base.InternalMachineTranslation.is_rate_limited",
            return_value=True,
        ):
            response = self.client.post(
                reverse("auto_translation", kwargs=path_params),
                {
                    "auto_source": "mt",
                    "q": "state:<translated",
                    "mode": "translate",
                    "engines": ["weblate"],
                    "threshold": 80,
                },
                follow=True,
            )

        self.assertContains(response, "left untranslated")
        translation = self.component3.translation_set.get(language_code="cs")
        translation.invalidate_cache()
        self.assertEqual(translation.stats.translated, 0)

    def test_empty_engine_selection_warns_for_a_nonempty_scope(self) -> None:
        auto = AutoTranslate(
            translation=self.component3.translation_set.get(language_code="cs"),
            user=self.user,
            q="",
            mode="translate",
        )

        auto.perform(
            auto_source="mt",
            engines=[],
            threshold=80,
            source_component_ids=None,
        )

        self.assertEqual(auto.updated, 0)
        self.assertEqual(
            auto.get_warnings(),
            [
                (
                    "No machine translation engine was selected, so no strings "
                    "were machine translated."
                )
            ],
        )

    def test_unconfigured_engine_selection_names_the_requested_engine(self) -> None:
        auto = AutoTranslate(
            translation=self.component3.translation_set.get(language_code="cs"),
            user=self.user,
            q="",
            mode="translate",
        )

        auto.perform(
            auto_source="mt",
            engines=["missing"],
            threshold=80,
            source_component_ids=None,
        )

        self.assertEqual(
            auto.get_warnings(),
            [
                (
                    "The selected machine translation engines (missing) are not "
                    "configured for this project."
                )
            ],
        )

    def test_empty_engine_selection_does_not_warn_for_an_empty_scope(self) -> None:
        auto = AutoTranslate(
            translation=self.component3.translation_set.get(language_code="cs"),
            user=self.user,
            q="context:does-not-exist",
            mode="translate",
        )

        auto.perform(
            auto_source="mt",
            engines=[],
            threshold=80,
            source_component_ids=None,
        )

        self.assertEqual(auto.get_warnings(), [])


class ProducerRunCreationTest(ViewTestCase):
    def _perform(
        self,
        mode: str,
        *,
        scope=None,
        auto_source: str = "mt",
    ) -> BatchAutoTranslate:
        auto = BatchAutoTranslate(
            self.component if scope is None else scope,
            user=self.user,
            q="",
            mode=mode,
            component_wide=True,
            # The base component fixture may have no eligible strings when the
            # built-in ``weblate`` machinery is disabled.  Pin the lifecycle
            # probe to a real unit so the child AutoTranslate is dispatched.
            unit_ids=[self.get_unit().pk],
        )
        auto.perform(
            auto_source=auto_source,
            engines=["weblate"],
            threshold=80,
            source_component_ids=None,
        )
        return auto

    def test_machine_translation_launch_records_a_run(self) -> None:
        auto = self._perform("translate")
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.requested_mode, "translate")
        self.assertEqual(run.actor, self.user)
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertIsNotNone(run.finished)

    def test_suggest_launch_records_its_own_mode(self) -> None:
        auto = self._perform("suggest")
        self.assertEqual(auto.active_producer_run.requested_mode, "suggest")

    def test_category_and_project_language_launches_record_their_own_scopes(
        self,
    ) -> None:
        category = self.create_category(project=self.component.project)
        project_language = ProjectLanguage(
            self.component.project, self.get_translation().language
        )
        for scope, scope_type in (
            (category, ProducerRun.ScopeType.CATEGORY),
            (project_language, ProducerRun.ScopeType.PROJECT_LANGUAGE),
        ):
            with self.subTest(scope=scope):
                run = self._perform("translate", scope=scope).active_producer_run
                self.assertIsNotNone(run)
                self.assertEqual(run.scope_type, scope_type)
                self.assertEqual(run.scope_id, str(scope.pk))

    def test_translation_memory_launch_records_no_run(self) -> None:
        """A launch that asks no model has no cost, so it gets no receipt."""
        auto = self._perform("translate", auto_source="others")
        self.assertIsNone(auto.active_producer_run)
        self.assertFalse(ProducerRun.objects.exists())

    def test_exception_marks_the_launch_failed(self) -> None:
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="translate",
            component_wide=True,
            unit_ids=[self.get_unit().pk],
        )
        with (
            mock.patch.object(
                BatchAutoTranslate, "_can_process_translation", return_value=True
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "_finish_translation",
                side_effect=ValueError("boom"),
            ),
            self.assertRaises(ValueError),
        ):
            auto.perform(
                auto_source="mt",
                engines=["weblate"],
                threshold=80,
                source_component_ids=None,
            )
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.failure, "boom")

    def test_swallowed_mt_failure_finalizes_the_run_as_failed(self) -> None:
        """AutoTranslate records expected provider errors instead of raising."""

        def fail(auto_translate, **_kwargs) -> str:
            auto_translate.failure_message = "provider unavailable"
            return auto_translate.failure_message

        with (
            mock.patch.object(
                BatchAutoTranslate, "_can_process_translation", return_value=True
            ),
            mock.patch.object(
                AutoTranslate, "perform", autospec=True, side_effect=fail
            ),
        ):
            auto = self._perform("translate")
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.failure, "provider unavailable")

    def test_guard_exhaustion_marks_matching_run_failed(self) -> None:
        run = ProducerRun.objects.create(
            actor=self.user,
            task_id="exhausted-redelivery",
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=1,
            status=ProducerRun.Status.RUNNING,
        )
        with (
            patch(
                "weblate.trans.tasks.producer_execution_guard",
                side_effect=JudgeExecutionGuardError,
            ),
            patch.object(auto_translate, "retry", side_effect=JudgeExecutionGuardError),
        ):
            result = auto_translate.apply(
                kwargs={
                    "user_id": self.user.id,
                    "mode": "judge",
                    "q": "",
                    "auto_source": "mt",
                    "source_component_id": None,
                    "component_id": self.component.id,
                    "engines": [],
                    "threshold": 80,
                },
                task_id="exhausted-redelivery",
            ).get()

        run.refresh_from_db()
        self.assertEqual(result["message"], "The execution lock was not released.")
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertIsNotNone(run.finished)


class ProducerRunRefusalAggregationTest(ViewTestCase):
    """Usage-log refusals must surface in the run they belong to."""

    def setUp(self) -> None:
        super().setUp()
        # The end-to-end refusal test drives a real engine, so the site-wide
        # configuration has to exist; without it ``fetch_mt`` finds no engine,
        # never calls the provider and the run legitimately completes.
        self.configure_mt()

    def _make_run(self, *, status: str = ProducerRun.Status.RUNNING) -> ProducerRun:
        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
            status=status,
        )

    def _finish(
        self, run: ProducerRun, status: str = ProducerRun.Status.COMPLETED
    ) -> None:
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="translate",
            component_wide=True,
            unit_ids=[self.get_unit().pk],
        )
        auto._finish_producer_run(run, status)  # ruff: ignore[private-member-access]

    def test_refusal_only_run_becomes_partial_with_warning(self) -> None:
        run = self._make_run()
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.TRANSLATION,
            outcome=LLMUsageLog.Outcome.REFUSED,
            refusal_reason="Mismatching assistant reply items.",
            batch_size=1,
        )
        self._finish(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.PARTIAL)
        self.assertTrue(
            any("Mismatching assistant reply items" in w for w in run.warnings)
        )

    def test_refusal_and_applied_stays_completed_with_warning(self) -> None:
        run = self._make_run()
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.TRANSLATION,
            outcome=LLMUsageLog.Outcome.APPLIED,
            batch_size=1,
        )
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.TRANSLATION,
            outcome=LLMUsageLog.Outcome.REFUSED,
            refusal_reason="Mismatching assistant reply items.",
            batch_size=1,
        )
        self._finish(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertTrue(
            any("Mismatching assistant reply items" in w for w in run.warnings)
        )

    def test_partial_outcome_only_warns_without_status_change(self) -> None:
        run = self._make_run()
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.TRANSLATION,
            outcome=LLMUsageLog.Outcome.PARTIAL,
            refusal_reason="Some items refused.",
            batch_size=2,
        )
        self._finish(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertTrue(any("Some items refused" in w for w in run.warnings))

    def test_failed_run_is_not_demoted_by_refusal_aggregation(self) -> None:
        run = self._make_run()
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.TRANSLATION,
            outcome=LLMUsageLog.Outcome.REFUSED,
            refusal_reason="Mismatching assistant reply items.",
            batch_size=1,
        )
        self._finish(run, ProducerRun.Status.FAILED)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)

    def test_judge_usage_is_not_aggregated(self) -> None:
        run = self._make_run()
        LLMUsageLog.objects.create(
            run=run,
            model="test-model",
            operation=LLMUsageLog.Operation.JUDGE,
            outcome=LLMUsageLog.Outcome.REFUSED,
            refusal_reason="Judge refusal.",
            batch_size=1,
        )
        self._finish(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertFalse(run.warnings)

    def test_regression_engine_refusal_marks_run_partial_not_completed(self) -> None:
        """Engine refusal + error must finalize as PARTIAL, not COMPLETED."""

        class RefusingTranslation(DummyTranslation):
            """Writes a refused usage record then raises, like the OpenAI layer."""

            def __init__(self, settings):
                super().__init__(settings)
                self.usage_run_id = None

            def batch_translate(self, units, user=None, threshold=75, **kwargs):
                LLMUsageLog.objects.create(
                    run_id=self.usage_run_id,
                    model="test-model",
                    operation=LLMUsageLog.Operation.TRANSLATION,
                    outcome=LLMUsageLog.Outcome.REFUSED,
                    refusal_reason="Mismatching assistant reply items.",
                    batch_size=len(units),
                )
                msg = "Mismatching assistant reply items."
                raise MachineTranslationError(msg)

        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="translate",
            component_wide=True,
            unit_ids=[self.get_unit().pk],
        )

        with (
            mock.patch.object(
                BatchAutoTranslate, "_can_process_translation", return_value=True
            ),
            mock.patch(
                "weblate.trans.autotranslate.MACHINERY",
                {"weblate": RefusingTranslation},
            ),
        ):
            auto.perform(
                auto_source="mt",
                engines=["weblate"],
                threshold=80,
                source_component_ids=None,
            )

        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.PARTIAL)
        self.assertTrue(
            any("Mismatching assistant reply items" in w for w in run.warnings)
        )


class RecordingTranslation(DummyTranslation):
    """Records received batches instead of translating them."""

    batch_size = 2

    def __init__(
        self,
        *,
        concurrency: int = 1,
        barrier: threading.Barrier | None = None,
        failing_ids: frozenset[int] = frozenset(),
        rate_limited: bool = False,
        stop_clears_after: int | None = None,
        rate_limit_period: int = 0,
        fatal_error: Exception | None = None,
        fatal_ids: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__({})
        self.batch_concurrency = concurrency
        self.barrier = barrier
        self.failing_ids = failing_ids
        self.fatal_error = fatal_error
        self.fatal_ids = fatal_ids
        self.rate_limited = rate_limited
        # A stop lifted once it has been observed this many times, standing in
        # for one that expires while a run is still going.
        self.stop_clears_after = stop_clears_after
        self.rate_limit_period = rate_limit_period
        self.stop_checks = 0
        self.lock = threading.Lock()
        self.batches: list[list[int]] = []
        self.threads: set[int] = set()

    def is_rate_limited(self) -> bool:
        if not self.rate_limited:
            return False
        with self.lock:
            self.stop_checks += 1
            if (
                self.stop_clears_after is not None
                and self.stop_checks > self.stop_clears_after
            ):
                self.rate_limited = False
                return False
        return True

    def batch_translate(
        self,
        units,
        user=None,
        threshold: int = MACHINERY_DEFAULT_THRESHOLD,
        *,
        source_language=None,
    ) -> None:
        if self.barrier is not None:
            self.barrier.wait()
        with self.lock:
            self.batches.append([unit.id for unit in units])
            self.threads.add(threading.get_ident())
        if self.failing_ids.intersection(unit.id for unit in units):
            msg = "Recorded failure"
            raise MachineTranslationError(msg)
        if self.fatal_error is not None and self.fatal_ids.intersection(
            unit.id for unit in units
        ):
            raise self.fatal_error
        for unit in units:
            unit.machinery = {
                "translation": ["translated"],
                "quality": [90],
                "origin": [self],
            }


class MachineryBatchFetchTest(SimpleTestCase):
    """Batch scheduling done by fetch_machinery_matches."""

    @staticmethod
    def make_units(count: int) -> list[Any]:
        return [SimpleNamespace(id=number, machinery={}) for number in range(count)]

    def fetch(
        self,
        service: RecordingTranslation,
        units: list[Any],
        on_batch: Callable[[list[Any]], None] | None = None,
    ) -> tuple[dict, list[int]]:
        progress: list[int] = []
        result = fetch_machinery_matches(
            units=units,
            user=None,
            services=[service],
            threshold=75,
            set_progress=progress.append,
            on_batch=on_batch,
        )
        return result, progress

    def test_serial(self) -> None:
        service = RecordingTranslation()
        result, progress = self.fetch(service, self.make_units(5))

        self.assertEqual(sorted(result), [0, 1, 2, 3, 4])
        self.assertEqual(service.batches, [[0, 1], [2, 3], [4]])
        self.assertEqual(len(service.threads), 1)
        self.assertEqual(progress, [2, 4, 5])

    def test_parallel(self) -> None:
        # The barrier makes a serial execution fail instead of just being slow.
        service = RecordingTranslation(
            concurrency=3, barrier=threading.Barrier(3, timeout=60)
        )
        result, progress = self.fetch(service, self.make_units(6))

        self.assertEqual(sorted(result), [0, 1, 2, 3, 4, 5])
        self.assertEqual(len(service.batches), 3)
        self.assertEqual(len(service.threads), 3)
        self.assertEqual(progress, [2, 4, 6])

    def test_parallel_keeps_other_batches_on_failure(self) -> None:
        service = RecordingTranslation(concurrency=3, failing_ids=frozenset({2}))
        result, progress = self.fetch(service, self.make_units(6))

        self.assertEqual(sorted(result), [0, 1, 4, 5])
        self.assertEqual(progress, [2, 4, 6])

    def test_a_malformed_reply_only_loses_its_own_batch(self) -> None:
        # LLM parsing normalizes a non-text batch reply to
        # MachineTranslationError before the shared scheduler sees it.
        service = RecordingTranslation(failing_ids=frozenset({2}))
        result, progress = self.fetch(service, self.make_units(6))

        self.assertEqual(sorted(result), [0, 1, 4, 5])
        self.assertEqual(service.batches, [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(progress, [2, 4, 6])

    def test_concurrency_limited_to_batch_count(self) -> None:
        service = RecordingTranslation(
            concurrency=8, barrier=threading.Barrier(2, timeout=60)
        )
        result, _progress = self.fetch(service, self.make_units(4))

        self.assertEqual(sorted(result), [0, 1, 2, 3])
        self.assertEqual(len(service.threads), 2)

    def test_rate_limited_service_is_not_asked(self) -> None:
        service = RecordingTranslation(concurrency=3, rate_limited=True)
        result, progress = self.fetch(service, self.make_units(6))

        self.assertEqual(result, {})
        self.assertEqual(service.batches, [])
        self.assertEqual(progress, [2, 4, 6])

    def test_batches_a_short_stop_skipped_are_asked_again(self) -> None:
        # Three batches are refused, the stop is lifted while the run is still
        # going, and the strings arrive instead of being dropped.
        service = RecordingTranslation(
            rate_limited=True, stop_clears_after=3, rate_limit_period=5
        )
        result, progress = self.fetch(service, self.make_units(6))

        self.assertEqual(sorted(result), [0, 1, 2, 3, 4, 5])
        self.assertEqual(service.batches, [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(progress, [2, 4, 6])

    def test_batch_callback_runs_once_for_a_batch_asked_again(self) -> None:
        service = RecordingTranslation(
            rate_limited=True, stop_clears_after=1, rate_limit_period=5
        )
        stored: list[list[int]] = []

        _result, progress = self.fetch(
            service,
            self.make_units(4),
            lambda batch: stored.append([unit.id for unit in batch]),
        )

        self.assertEqual(sorted(stored), [[0, 1], [2, 3]])
        self.assertEqual(progress, [2, 4])

    def test_batch_callback_receives_every_batch(self) -> None:
        service = RecordingTranslation()
        stored: list[list[int]] = []

        result, progress = self.fetch(
            service,
            self.make_units(5),
            lambda batch: stored.append([unit.id for unit in batch]),
        )

        self.assertEqual(sorted(result), [0, 1, 2, 3, 4])
        self.assertEqual(stored, [[0, 1], [2, 3], [4]])
        self.assertEqual(progress, [2, 4, 5])

    def test_batch_callback_runs_on_the_calling_thread(self) -> None:
        # The callback writes the batch to the database, and both a Django
        # connection and the Celery task of a run are thread-local.
        service = RecordingTranslation(
            concurrency=3, barrier=threading.Barrier(3, timeout=60)
        )
        callers: set[int] = set()

        result, _progress = self.fetch(
            service,
            self.make_units(6),
            lambda _batch: callers.add(threading.get_ident()),
        )

        self.assertEqual(len(result), 6)
        self.assertEqual(len(service.threads), 3)
        self.assertEqual(callers, {threading.get_ident()})

    def test_batch_callback_skipped_for_several_services(self) -> None:
        # A unit's best result is only known once every service has answered.
        first = RecordingTranslation()
        second = RecordingTranslation()
        stored: list[list[int]] = []

        fetch_machinery_matches(
            units=self.make_units(4),
            user=None,
            services=[first, second],
            threshold=75,
            on_batch=lambda batch: stored.append([unit.id for unit in batch]),
        )

        self.assertEqual(stored, [])
        self.assertEqual(len(first.batches), 2)
        self.assertEqual(len(second.batches), 2)

    def test_a_confirmed_quota_refusal_stops_new_submissions(self) -> None:
        # A parallel run keeps batches already in flight, but once a batch
        # reports a spent quota the remaining batches are never asked.
        refusal = MachineTranslationServiceError(
            "quota exhausted",
            reason_code=MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
            safe_message="The quota is exhausted.",
        )
        service = RecordingTranslation(
            concurrency=2, fatal_error=refusal, fatal_ids=frozenset({0})
        )
        failures: list[MachineryBatchOutcome] = []

        result, _progress = self.fetch_with_failures(
            service, self.make_units(10), failures.append
        )

        self.assertEqual(failures[0].status, "failed")
        self.assertEqual(
            failures[0].reason_code,
            MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
        )
        self.assertNotIn("quota exhausted", failures[0].error or "")
        # Only the first two batches were in flight; the rest never ran.
        self.assertLessEqual(len(service.batches), 2)
        asked = {unit_id for batch in service.batches for unit_id in batch}
        self.assertEqual(sorted(result), sorted(asked - {0, 1}))

    def test_serial_fetch_stops_after_a_fatal_refusal(self) -> None:
        refusal = MachineTranslationServiceError(
            "quota exhausted",
            reason_code=MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
            safe_message="The quota is exhausted.",
        )
        service = RecordingTranslation(fatal_error=refusal, fatal_ids=frozenset({0}))
        failures: list[MachineryBatchOutcome] = []

        result, progress = self.fetch_with_failures(
            service, self.make_units(6), failures.append
        )

        self.assertEqual(service.batches, [[0, 1]])
        self.assertEqual(result, {})
        self.assertEqual(len(failures), 1)
        self.assertEqual(progress, [2, 4, 6])

    def test_partial_results_survive_a_later_refusal(self) -> None:
        refusal = MachineTranslationServiceError(
            "insufficient credit",
            reason_code=MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT,
            safe_message="The credit balance is spent.",
        )
        service = RecordingTranslation(fatal_error=refusal, fatal_ids=frozenset({4}))
        failures: list[MachineryBatchOutcome] = []

        result, _progress = self.fetch_with_failures(
            service, self.make_units(6), failures.append
        )

        self.assertEqual(sorted(result), [0, 1, 2, 3])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].unit_ids, (4, 5))

    def test_a_generic_failure_is_reported_sanitized(self) -> None:
        service = RecordingTranslation(failing_ids=frozenset({2}))
        failures: list[MachineryBatchOutcome] = []

        result, _progress = self.fetch_with_failures(
            service, self.make_units(4), failures.append
        )

        self.assertEqual(sorted(result), [0, 1])
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].reason_code,
            MachineTranslationServiceError.REASON_PROVIDER_UNAVAILABLE,
        )
        # The raw provider detail ("Recorded failure") stays in the server
        # log; the outcome carries only fixed safe text.
        self.assertNotIn("Recorded failure", failures[0].error or "")

    def fetch_with_failures(
        self,
        service: RecordingTranslation,
        units: list[Any],
        on_failure: Callable[[MachineryBatchOutcome], None],
    ) -> tuple[dict, list[int]]:
        progress: list[int] = []
        result = fetch_machinery_matches(
            units=units,
            user=None,
            services=[service],
            threshold=75,
            set_progress=progress.append,
            on_failure=on_failure,
        )
        return result, progress


@override_settings(CELERY_RESULT_BACKEND="redis://localhost:6379")
class PersistentTaskProgressTest(ViewTestCase):
    """Progress of a started automatic translation survives a page reload."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.task_id = "persistent-task"
        # Every page render looks up task state; the test settings have no
        # result backend to answer that.
        patcher = patch("weblate.utils.celery.AsyncResult", self.running_task)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def running_task(task_id):
        return SimpleNamespace(
            id=task_id, result=None, state="PROGRESS", ready=lambda: False
        )

    def start_auto_translation(self, path: list[str]):
        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            # The view now generates its own task id before publication;
            # pin it to `self.task_id` so the rest of this test class keeps
            # treating that constant as the real, stored task id.
            patch("weblate.trans.views.edit.uuid4", return_value=self.task_id),
            patch(
                "weblate.trans.views.edit.auto_translate.apply_async",
                return_value=SimpleNamespace(id=self.task_id),
            ),
        ):
            return self.client.post(
                reverse("auto_translation", kwargs={"path": path}),
                {
                    "auto_source": "others",
                    "threshold": "100",
                    "q": "state:<translated",
                    "mode": "translate",
                },
                follow=True,
            )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_queued_behind_another_task_says_so(self) -> None:
        with patch("weblate.trans.views.edit.get_queue_length", return_value=3):
            response = self.start_auto_translation(self.translation.get_url_path())

        queued = "Automatic translation queued: 2 runs are ahead of it."
        messages = [str(message) for message in response.context["messages"]]
        self.assertTrue(any(queued in message for message in messages), messages)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_unreadable_queue_neither_fails_nor_claims_progress(self) -> None:
        with patch(
            "weblate.trans.views.edit.get_queue_length",
            side_effect=OSError("broker down"),
        ):
            response = self.start_auto_translation(self.translation.get_url_path())

        self.assertEqual(response.status_code, 200)
        messages = [str(message) for message in response.context["messages"]]
        self.assertIn(
            "Automatic translation queued. You can close this page.", messages
        )
        self.assertNotIn("Automatic translation in progress", messages)

    def test_project_language_task_is_authorized_and_kept(self) -> None:
        # This scope passes neither component_id nor translation_id, so the
        # task is only reachable through the user stored in its metadata.
        project_language = ProjectLanguage(
            self.project, language=Language.objects.get(code="cs")
        )
        self.start_auto_translation(project_language.get_url_path())

        self.assertEqual(
            get_task_metadata(self.task_id),
            {
                "component_id": None,
                "translation_id": None,
                "user_id": self.user.id,
            },
        )
        stored = cache.get(get_user_tasks_key(self.user.id))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], self.task_id)
        self.assertEqual(
            stored[0]["text"], "Automatic translation queued. You can close this page."
        )
        self.assertEqual(stored[0]["label"], str(project_language))
        self.assertEqual(stored[0]["url"], project_language.get_absolute_url())

        # The task detail is served instead of the 404 which hides the bar.
        with patch("weblate.api.views.AsyncResult", self.running_task):
            response = self.client.get(
                reverse("api:task-detail", kwargs={"pk": self.task_id})
            )
        self.assertEqual(response.status_code, 200)

    def test_progress_bar_is_rendered_on_an_unrelated_page(self) -> None:
        self.start_auto_translation(self.translation.get_url_path())

        # A fresh request has no flash message left, the bar comes from the
        # stored task alone.
        response = self.client.get(reverse("home"))

        task_url = reverse("api:task-detail", kwargs={"pk": self.task_id})
        self.assertContains(response, f'data-task="{task_url}"')
        self.assertEqual(response.content.count(b"data-task="), 1)
        self.assertContains(response, "Automatic translation queued.")

    def test_finished_task_is_forgotten(self) -> None:
        add_user_task(self.user.id, self.task_id, text="Work", label="Here", url="/")

        with patch(
            "weblate.utils.celery.AsyncResult",
            return_value=SimpleNamespace(ready=lambda: True, state="SUCCESS"),
        ):
            self.assertEqual(get_user_tasks(self.user.id), [])

        self.assertIsNone(cache.get(get_user_tasks_key(self.user.id)))

    def test_lost_task_is_forgotten_once_stale(self) -> None:
        add_user_task(self.user.id, self.task_id, text="Work", label="Here", url="/")
        pending = SimpleNamespace(ready=lambda: False, state="PENDING")

        with patch("weblate.utils.celery.AsyncResult", return_value=pending):
            self.assertEqual(len(get_user_tasks(self.user.id)), 1)

            key = get_user_tasks_key(self.user.id)
            tasks = cache.get(key)
            tasks[0]["started"] -= PENDING_TASK_MAX_AGE + 1
            cache.set(key, tasks, 60)

            self.assertEqual(get_user_tasks(self.user.id), [])

    def test_liveness_task_survives_stale_pending_pruning(self) -> None:
        # A liveness-enabled task whose delivery may be waiting for
        # redelivery stays in the user list past PENDING_TASK_MAX_AGE...
        register_task_liveness(self.task_id)
        add_user_task(self.user.id, self.task_id, text="Work", label="Here", url="/")
        pending = SimpleNamespace(ready=lambda: False, state="PENDING")
        with patch("weblate.utils.celery.AsyncResult", return_value=pending):
            key = get_user_tasks_key(self.user.id)
            tasks = cache.get(key)
            tasks[0]["started"] -= PENDING_TASK_MAX_AGE + 1
            cache.set(key, tasks, 60)

            self.assertEqual(len(get_user_tasks(self.user.id)), 1)
            self.assertEqual(get_task_liveness(self.task_id), "queued")

            # ...while an ordinary task without a record is still pruned
            # (the contract of test_lost_task_is_forgotten_once_stale).
            delete_task_liveness(self.task_id)
            self.assertEqual(get_user_tasks(self.user.id), [])

    def test_publish_failure_removes_registration(self) -> None:
        # The view registers metadata + liveness before apply_async; a
        # publish failure must remove both and leave no task in user list.
        captured: dict[str, str] = {}

        def failing_apply_async(*args, **kwargs):
            captured["task_id"] = kwargs["task_id"]
            msg = "broker down"
            raise OSError(msg)

        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            patch(
                "weblate.trans.views.edit.auto_translate.apply_async",
                side_effect=failing_apply_async,
            ),
            self.assertRaises(OSError),
        ):
            self.client.post(
                reverse(
                    "auto_translation",
                    kwargs={"path": self.translation.get_url_path()},
                ),
                {
                    "auto_source": "others",
                    "threshold": "100",
                    "q": "state:<translated",
                    "mode": "translate",
                },
            )

        self.assertEqual(get_user_tasks(self.user.id), [])
        self.assertIsNone(cache.get(get_user_tasks_key(self.user.id)))
        # The pre-registered records of the very task id that failed to
        # publish are gone: metadata, liveness, and with them any poll.
        self.assertIsNone(get_task_metadata(captured["task_id"]))
        self.assertIsNone(cache.get(get_task_liveness_key(captured["task_id"])))

    def test_registration_precedes_publication(self) -> None:
        seen: dict[str, bool] = {}

        def fake_apply_async(*args, **kwargs):
            # At publication time the liveness record must already exist.
            seen["liveness_at_publish"] = get_task_liveness(kwargs["task_id"])
            return SimpleNamespace(id=kwargs["task_id"])

        with (
            override_settings(CELERY_TASK_ALWAYS_EAGER=False),
            patch(
                "weblate.trans.views.edit.auto_translate.apply_async",
                side_effect=fake_apply_async,
            ),
        ):
            self.client.post(
                reverse(
                    "auto_translation",
                    kwargs={"path": self.translation.get_url_path()},
                ),
                {
                    "auto_source": "others",
                    "threshold": "100",
                    "q": "state:<translated",
                    "mode": "translate",
                },
                follow=True,
            )

        self.assertEqual(seen["liveness_at_publish"], "queued")


def max_form_depth(html: str) -> int:
    depth = 0
    peak = 0
    for match in re.finditer(r"<(/?)form\b", html, re.IGNORECASE):
        if match.group(1):
            depth -= 1
        else:
            depth += 1
            peak = max(peak, depth)

    return peak


class AutoFormRenderingTest(ViewTestCase):
    def test_autoform_does_not_nest_forms(self) -> None:
        html = render_to_string(
            "snippets/autoform.html",
            {
                "autoform": AutoForm(self.component, self.user),
                "object": self.translation,
            },
        )

        self.assertIn('data-persist="auto-translation"', html)
        self.assertLessEqual(
            max_form_depth(html),
            1,
            "crispy rendered a nested <form>; the Apply button falls outside it",
        )


class AutoTranslateDurabilityTest(SimpleTestCase):
    def test_auto_translate_acks_late(self) -> None:
        for task in (auto_translate, auto_translate_component):
            self.assertTrue(task.acks_late, f"{task.name} acks early")
            self.assertTrue(
                task.reject_on_worker_lost, f"{task.name} is lost on worker death"
            )

    def test_producer_tasks_are_bound_for_guard_retries(self) -> None:
        for task in (auto_translate, auto_translate_component):
            self.assertIsInstance(task.__header__, partial)

    def test_busy_guard_retries_before_heartbeat(self) -> None:
        retry_result = object()
        with (
            patch(
                "weblate.trans.tasks.producer_execution_guard",
                side_effect=JudgeExecutionGuardError,
            ),
            patch.object(auto_translate, "retry", return_value=retry_result) as retry,
            patch("weblate.trans.tasks.heartbeat_task") as heartbeat,
        ):
            result = auto_translate.apply(
                kwargs={
                    "user_id": None,
                    "mode": "judge",
                    "q": "",
                    "auto_source": "mt",
                    "source_component_id": None,
                    "engines": [],
                    "threshold": 80,
                },
                task_id="guarded-redelivery",
            ).get()

        self.assertIs(result, retry_result)
        retry.assert_called_once_with(
            exc=mock.ANY, countdown=60, max_retries=settings.JUDGE_GUARD_WAIT_RETRIES
        )
        heartbeat.assert_not_called()

    def test_duplicate_process_delivery_retries_without_provider_call(self) -> None:
        context = multiprocessing.get_context("fork")
        counter = context.Value("i", 0)
        ready = context.Event()
        first_result = context.Array("u", 32)
        second_result = context.Array("u", 32)
        first = context.Process(
            target=_run_auto_translate_process,
            args=("same-delivery", counter, ready, True, first_result),
        )
        first.start()
        try:
            self.assertTrue(ready.wait(timeout=5))
            second = context.Process(
                target=_run_auto_translate_process,
                args=("same-delivery", counter, ready, False, second_result),
            )
            second.start()
            second.join(timeout=5)
            self.assertFalse(second.is_alive())
            self.assertEqual(counter.value, 1)
            self.assertEqual("".join(second_result).rstrip("\x00"), "retried")
            first.terminate()
            first.join(timeout=5)
            self.assertFalse(first.is_alive())
            recovery = context.Array("u", 32)
            third = context.Process(
                target=_run_auto_translate_process,
                args=("same-delivery", counter, ready, False, recovery),
            )
            third.start()
            third.join(timeout=5)
            self.assertFalse(third.is_alive())
            self.assertEqual(counter.value, 2)
            self.assertEqual("".join(recovery).rstrip("\x00"), "provider called")
        finally:
            if first.is_alive():
                first.terminate()
                first.join(timeout=5)

    def test_visibility_timeout_covers_long_tasks(self) -> None:
        code = """
from pathlib import Path

# settings_docker reads the secret from a container path this test host does
# not have. Only that one read is intercepted; every other read is real.
_read_text = Path.read_text
Path.read_text = lambda self, *args, **kwargs: (
    "test-secret" if self.name == "secret" else _read_text(self, *args, **kwargs)
)
import json
from weblate import settings_docker
print(json.dumps(
    [
        settings_docker.CELERY_BROKER_TRANSPORT_OPTIONS,
        settings_docker.CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS,
        settings_docker.CELERY_VISIBILITY_TIMEOUT,
    ]
))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            check=False,
            env=os.environ
            | {
                "WEBLATE_DATABASES": "0",
                "WEBLATE_SITE_DOMAIN": "example.com",
            },
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        options, result_options, visibility_timeout = json.loads(result.stdout)
        self.assertGreaterEqual(options.get("visibility_timeout", 0), 4 * 3600)
        self.assertEqual(result_options, options)
        self.assertEqual(visibility_timeout, options["visibility_timeout"])


class AutoTranslateMaxLengthGateTest(ViewTestCase):
    """`AutoTranslate.update()` consults the registered max-length measurement."""

    def get_gated_unit(
        self, *, max_length: int, source: str = "Hello, world!\n"
    ) -> Unit:
        unit = self.get_unit(source)
        unit.extra_flags = f"max-length:{max_length}"
        unit.save(update_fields=["extra_flags"], same_content=True)
        return unit

    def build_auto(self, *, mode: str) -> AutoTranslate:
        return AutoTranslate(
            user=self.user,
            translation=self.get_translation(),
            q="",
            mode=mode,
        )

    def test_registered_measurement_replaces_raw_length(self) -> None:
        """A raw-long target that collapses under budget is stored, not suggested."""
        unit = self.get_gated_unit(max_length=5)

        class _StubMaxLengthCheck(MaxLengthCheck):
            def get_replacement_function(self, unit):
                return lambda _text: "x"

        with patch.dict(CHECKS.data, {"max-length": _StubMaxLengthCheck()}):
            auto = self.build_auto(mode="translate")
            auto.update(unit, STATE_TRANSLATED, ["a much longer raw target"])
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)
        self.assertFalse(unit.suggestion_set.exists())

    def test_missing_registration_falls_back_to_raw_length(self) -> None:
        """Without a registered max-length check, raw length gates as before."""
        unit = self.get_gated_unit(max_length=5)
        with patch.dict(CHECKS.data):
            del CHECKS.data["max-length"]
            auto = self.build_auto(mode="translate")
            auto.update(unit, STATE_TRANSLATED, ["a much longer raw target"])
        unit.refresh_from_db()
        self.assertNotEqual(unit.state, STATE_TRANSLATED)
        self.assertTrue(unit.suggestion_set.exists())

    def test_one_over_budget_plural_form_suggests_the_full_list(self) -> None:
        unit = self.get_gated_unit(max_length=10, source="Orangutan has %d banana.\n")
        targets = ["short\n", "this plural form is far past the budget\n"]
        auto = self.build_auto(mode="translate")
        auto.update(unit, STATE_TRANSLATED, targets)
        unit.refresh_from_db()
        self.assertNotEqual(unit.state, STATE_TRANSLATED)
        suggestion = unit.suggestion_set.get()
        self.assertEqual(split_plural(suggestion.target), targets)

    def test_judge_mode_over_budget_persists_instead_of_suggesting(self) -> None:
        """Judge mode keeps an over-budget candidate available to checks/repair."""
        unit = self.get_gated_unit(max_length=5)
        auto = self.build_auto(mode="judge")
        auto.update(unit, STATE_FUZZY, ["a much longer raw target\n"])
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_FUZZY)
        self.assertEqual(unit.target, "a much longer raw target\n")
        self.assertFalse(unit.suggestion_set.exists())

    def test_explicit_suggest_mode_always_suggests(self) -> None:
        unit = self.get_gated_unit(max_length=100)
        auto = self.build_auto(mode="suggest")
        auto.update(unit, STATE_TRANSLATED, ["short"])
        unit.refresh_from_db()
        self.assertNotEqual(unit.state, STATE_TRANSLATED)
        self.assertTrue(unit.suggestion_set.exists())
