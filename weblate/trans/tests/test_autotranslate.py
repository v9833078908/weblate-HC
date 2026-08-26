# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for automatic translation."""

from __future__ import annotations

import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import reverse

from weblate.addons.autotranslate import AutoTranslateAddon
from weblate.addons.events import AddonEvent
from weblate.addons.models import AddonActivityLog
from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, TeamMembership, User
from weblate.checks.chars import MaxLengthCheck
from weblate.checks.models import CHECKS
from weblate.configuration.models import Setting, SettingCategory
from weblate.lang.models import Language, Plural
from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
)
from weblate.machinery.dummy import DummyTranslation
from weblate.trans.actions import ActionEvents
from weblate.trans.autotranslate import AutoTranslate, BatchAutoTranslate
from weblate.trans.forms import AutoForm
from weblate.trans.machinery import fetch_machinery_matches
from weblate.trans.models import (
    Change,
    Component,
    PendingUnitChange,
    Project,
    Translation,
    Unit,
    WorkflowSetting,
)
from weblate.trans.tasks import auto_translate, auto_translate_component
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import split_plural
from weblate.utils.celery import (
    PENDING_TASK_MAX_AGE,
    add_user_task,
    get_task_metadata,
    get_user_tasks,
    get_user_tasks_key,
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
                q="state:empty",
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
    ) -> None:
        super().__init__({})
        self.batch_concurrency = concurrency
        self.barrier = barrier
        self.failing_ids = failing_ids
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
            patch(
                "weblate.trans.views.edit.auto_translate.delay",
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
