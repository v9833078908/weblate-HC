# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for management commands."""

import json
import sys
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import cast
from unittest import SkipTest
from unittest.mock import Mock, patch

import httpx2
from django.core.management import call_command
from django.core.management.base import CommandError, SystemCheckError
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.utils import timezone

from weblate.accounts.models import Profile
from weblate.checks.models import Check
from weblate.runner import main
from weblate.trans import defaults as trans_defaults
from weblate.trans.actions import ActionEvents
from weblate.trans.file_format_params import (
    FILE_FORMATS_PARAMS,
    BaseFileFormatParam,
    register_file_format_param,
)
from weblate.trans.judge_loop import build_request
from weblate.trans.judge_workflow import TARGET_PROJECT_SLUGS
from weblate.trans.management.commands.judge_release_advisory_holds import (
    Command as JudgeReleaseAdvisoryHoldsCommand,
)
from weblate.trans.management.commands.reapply_autofixes import Command
from weblate.trans.models import Change, Component, Project, Translation, Unit
from weblate.trans.models.judge import (
    JudgeRequestAttempt,
    JudgeRun,
    JudgeRunUnit,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.models.pending import PendingUnitChange
from weblate.trans.models.project import CommitPolicyChoices
from weblate.trans.tests.test_models import RepoTestCase
from weblate.trans.tests.test_views import (
    ComponentTestCase,
    FixtureComponentTestCase,
    ViewTestCase,
)
from weblate.trans.tests.utils import (
    create_another_user,
    create_test_user,
    get_test_file,
    require_github,
)
from weblate.utils.state import STATE_NEEDS_CHECKING, STATE_READONLY, STATE_TRANSLATED
from weblate.vcs.mercurial import HgRepository

# The test settings use the default AUTOFIX_LIST, which has none of the custom
# fixes; the command fails closed without its required fix, so every test of it
# runs against the default list extended exactly like WEBLATE_ADD_AUTOFIX does -
# additions at the head, as modify_env_list() inserts them.
AUTOFIX_LIST_WITH_CUSTOM = [
    "weblate_customization.autofixes.LineSeparatorSpacing",
    "weblate_customization.autofixes.RemoveAddedFinalStop",
    "weblate_customization.autofixes.AddFrenchPunctuationSpacing",
    *trans_defaults.DEFAULT_AUTOFIX_LIST,
]

TEST_PO = get_test_file("cs.po")
TEST_COMPONENTS = get_test_file("components.json")
TEST_COMPONENTS_INVALID = get_test_file("components-invalid.json")


class RunnerTest(SimpleTestCase):
    def test_help(self) -> None:
        restore = sys.stdout
        try:
            sys.stdout = StringIO()
            main(["help"])
            self.assertIn("list_versions", sys.stdout.getvalue())
        finally:
            sys.stdout = restore


class AnalyzeTranslatorWorkTest(ComponentTestCase):
    def create_change(self, author, user) -> None:
        unit = self.get_unit()
        Change.objects.create(
            action=ActionEvents.ACCEPT,
            author=author,
            user=user,
            unit=unit,
            translation=self.translation,
            component=self.component,
            project=self.project,
            language=self.translation.language,
        )

    def test_accepted_suggestions_are_grouped_by_author(self) -> None:
        reviewer = self.user
        first_author = create_another_user("-first")
        second_author = create_another_user("-second")
        for _unused in range(3):
            self.create_change(first_author, reviewer)
            self.create_change(second_author, reviewer)

        output = StringIO()
        call_command(
            "analyze_translator_work",
            days=1,
            min_changes=1,
            stdout=output,
        )

        result = output.getvalue()
        self.assertIn("User days: 2 included, 0 excluded", result)
        self.assertIn("  median: 3", result)

    def test_component_filter_uses_full_path(self) -> None:
        category = self.create_category(self.project)
        self.component.category = category
        self.component.save(update_fields=["category"])
        for _unused in range(3):
            self.create_change(self.user, self.user)

        output = StringIO()
        call_command(
            "analyze_translator_work",
            component="/".join(self.component.get_url_path()),
            days=1,
            min_changes=1,
            stdout=output,
        )

        result = output.getvalue()
        self.assertIn("User days: 1 included, 0 excluded", result)
        self.assertIn("  median: 3", result)


class ImportProjectTest(RepoTestCase):
    def do_import(self, path=None, **kwargs) -> None:
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path if path is None else path,
                "main",
                "**/*.po",
                **kwargs,
            )

    def test_import(self) -> None:
        project = self.create_project()
        self.do_import()
        self.assertEqual(project.component_set.count(), 5)

    def test_import_deep(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "deep/*/locales/*/LC_MESSAGES/**.po",
            )
        self.assertEqual(project.component_set.count(), 1)

    def test_import_ignore(self) -> None:
        project = self.create_project()
        self.do_import()
        self.do_import()
        self.assertEqual(project.component_set.count(), 5)

    def test_import_duplicate(self) -> None:
        project = self.create_project()
        self.do_import()
        self.do_import(path="weblate://test/po")
        self.assertEqual(project.component_set.count(), 5)

    def test_import_main_1(self, name="po-mono") -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/*.po",
                main_component=name,
            )
        non_linked = project.component_set.with_repo()
        self.assertEqual(non_linked.count(), 1)
        self.assertEqual({c.slug for c in non_linked}, {name})

    def test_import_main_2(self) -> None:
        self.test_import_main_1("second-po")

    def test_import_main_invalid(self) -> None:
        with self.assertRaises(CommandError):
            self.test_import_main_1("x-po")

    def test_import_filter(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/*.po",
                language_regex="cs",
            )
        self.assertEqual(project.component_set.count(), 5)
        for component in project.component_set.filter(is_glossary=False).iterator():
            self.assertEqual(component.translation_set.count(), 2)

    def test_import_re(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                r"(?P<component>[^/-]*)/(?P<language>[^/]*)\.po",
            )
        self.assertEqual(project.component_set.count(), 1)

    def test_import_name(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                r"(?P<component>[^/-]*)/(?P<language>[^/]*)\.po",
                name_template="Test name",
            )
        self.assertEqual(project.component_set.count(), 1)
        self.assertTrue(project.component_set.filter(name="Test name").exists())

    def test_import_re_missing(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                r"(?P<name>[^/-]*)/.*\.po",
            )

    def test_import_re_wrong(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                r"(?P<name>[^/-]*",
            )

    def test_import_po(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/*.po",
                file_format="po",
            )
        self.assertEqual(project.component_set.count(), 5)

    def test_import_invalid(self) -> None:
        project = self.create_project()
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/*.po",
                file_format="INVALID",
            )
        self.assertEqual(project.component_set.count(), 0)

    def test_import_aresource(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/values-*/strings.xml",
                file_format="aresource",
                base_file_template="android/values/strings.xml",
            )
        self.assertEqual(project.component_set.count(), 2)

    def test_import_aresource_format(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/values-*/strings.xml",
                file_format="aresource",
                base_file_template="%s/values/strings.xml",
            )
        self.assertEqual(project.component_set.count(), 2)

    def test_re_import(self) -> None:
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project", "test", self.git_repo_path, "main", "**/*.po"
            )
        self.assertEqual(project.component_set.count(), 5)

        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project", "test", self.git_repo_path, "main", "**/*.po"
            )
        self.assertEqual(project.component_set.count(), 5)

    def test_import_against_existing(self) -> None:
        """Test importing with a weblate:// URL."""
        android = self.create_android()
        project = android.project
        self.assertEqual(project.component_set.count(), 1)
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                project.slug,
                f"weblate://{project.slug!s}/{android.slug!s}",
                "main",
                "**/*.po",
            )
        self.assertEqual(project.component_set.count(), 6)

    def test_import_missing_project(self) -> None:
        """Test of correct handling of missing project."""
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project", "test", self.git_repo_path, "main", "**/*.po"
            )

    def test_import_missing_wildcard(self) -> None:
        """Test of correct handling of missing wildcard."""
        self.create_project()
        with self.assertRaises(CommandError):
            call_command("import_project", "test", self.git_repo_path, "main", "*/*.po")

    def test_import_wrong_vcs(self) -> None:
        """Test of correct handling of wrong vcs."""
        self.create_project()
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project",
                "test",
                self.git_repo_path,
                "main",
                "**/*.po",
                vcs="nonexisting",
            )

    def test_import_mercurial(self) -> None:
        """Test importing Mercurial project."""
        if not HgRepository.is_supported():
            self.skipTest("Mercurial not available!")
        project = self.create_project()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_project",
                "test",
                self.mercurial_repo_path,
                "default",
                "**/*.po",
                vcs="mercurial",
            )
        self.assertEqual(project.component_set.count(), 5)

    def test_import_mercurial_mixed(self) -> None:
        """Test importing Mercurial project with mixed component/lang."""
        if not HgRepository.is_supported():
            self.skipTest("Mercurial not available!")
        self.create_project()
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_project",
                "test",
                self.mercurial_repo_path,
                "default",
                "*/**.po",
                vcs="mercurial",
            )


class BasicCommandTest(FixtureComponentTestCase):
    def test_versions(self) -> None:
        output = StringIO()
        call_command("list_versions", stdout=output)
        self.assertIn("Weblate", output.getvalue())

    def test_check(self) -> None:
        with self.assertRaises(SystemCheckError):
            call_command("check", "--deploy")


class JudgeWorkflowCommandTest(ComponentTestCase):
    def create_rollout_projects(self) -> list[Project]:

        projects = []
        for slug in TARGET_PROJECT_SLUGS:
            project, _created = Project.objects.get_or_create(name=slug, slug=slug)
            projects.append(project)
        return projects

    def test_enable_review_workflow_updates_only_rollout_projects(self) -> None:
        projects = self.create_rollout_projects()
        other = Project.objects.create(name="Other", slug="other")
        call_command("enable_review_workflow")
        for project in projects:
            project.refresh_from_db()
            self.assertTrue(project.translation_review)
            self.assertEqual(
                project.commit_policy, CommitPolicyChoices.WITHOUT_NEEDS_EDITING
            )
        other.refresh_from_db()
        self.assertFalse(other.translation_review)

    def test_enable_review_workflow_dry_run_and_idempotency(self) -> None:
        projects = self.create_rollout_projects()
        output = StringIO()
        call_command("enable_review_workflow", "--dry-run", stdout=output)
        self.assertTrue(output.getvalue())
        for project in projects:
            project.refresh_from_db()
            self.assertFalse(project.translation_review)
        call_command("enable_review_workflow")
        output = StringIO()
        call_command("enable_review_workflow", stdout=output)
        self.assertEqual(output.getvalue(), "")

    def test_enable_review_workflow_missing_project_writes_nothing(self) -> None:
        projects = self.create_rollout_projects()
        projects[0].delete()
        with self.assertRaises(CommandError):
            call_command("enable_review_workflow")
        for project in projects[1:]:
            project.refresh_from_db()
            self.assertFalse(project.translation_review)

    def test_check_judge_repair_routes_uses_explicit_project_scope(self) -> None:
        self.project.machinery_settings = {"openrouter": {"key": "test-key"}}
        self.project.save(update_fields=["machinery_settings"])
        engine = Mock()
        engine.return_value.resolve_model.return_value = "vendor/model"
        output = StringIO()
        with patch("weblate.trans.judge_workflow.MACHINERY", {"openrouter": engine}):
            call_command(
                "check_judge_repair_routes",
                "--project",
                self.project.slug,
                stdout=output,
            )
        self.assertIn(f"{self.project.slug}/", output.getvalue())

    def test_check_judge_repair_routes_fails_for_missing_route(self) -> None:
        self.project.machinery_settings = {"openrouter": {"key": "test-key"}}
        self.project.save(update_fields=["machinery_settings"])
        engine = Mock()
        engine.return_value.resolve_model.return_value = None
        with (
            patch("weblate.trans.judge_workflow.MACHINERY", {"openrouter": engine}),
            self.assertRaises(CommandError),
        ):
            call_command("check_judge_repair_routes", "--project", self.project.slug)

    def test_check_judge_repair_routes_fails_without_engine_setting(self) -> None:
        with self.assertRaises(CommandError):
            call_command("check_judge_repair_routes", "--project", self.project.slug)

    def test_check_judge_repair_routes_uses_litellm_only_project(self) -> None:
        self.project.machinery_settings = {"litellm": {"key": "ll-key"}}
        self.project.save(update_fields=["machinery_settings"])
        engine = Mock()
        engine.return_value.resolve_model.return_value = "vendor/model"
        output = StringIO()
        with patch("weblate.trans.judge_workflow.MACHINERY", {"litellm": engine}):
            call_command(
                "check_judge_repair_routes",
                "--project",
                self.project.slug,
                stdout=output,
            )
        self.assertIn(f"{self.project.slug}/", output.getvalue())

    def test_check_judge_repair_routes_prefers_openrouter_over_litellm(self) -> None:
        self.project.machinery_settings = {
            "openrouter": {"key": "or-key"},
            "litellm": {"key": "ll-key"},
        }
        self.project.save(update_fields=["machinery_settings"])
        openrouter_engine = Mock()
        openrouter_engine.return_value.resolve_model.return_value = "openrouter/model"
        litellm_engine = Mock()
        litellm_engine.return_value.resolve_model.return_value = "litellm/model"
        output = StringIO()
        with patch(
            "weblate.trans.judge_workflow.MACHINERY",
            {"openrouter": openrouter_engine, "litellm": litellm_engine},
        ):
            call_command(
                "check_judge_repair_routes",
                "--project",
                self.project.slug,
                stdout=output,
            )
        self.assertIn("openrouter/model", output.getvalue())
        litellm_engine.assert_not_called()

    def test_check_judge_repair_routes_missing_key_does_not_fall_through(
        self,
    ) -> None:
        self.project.machinery_settings = {
            "openrouter": {"routing": {"*": "vendor/model"}},
            "litellm": {"key": "ll-key"},
        }
        self.project.save(update_fields=["machinery_settings"])
        litellm_engine = Mock()
        litellm_engine.return_value.resolve_model.return_value = "vendor/model"
        with (
            patch(
                "weblate.trans.judge_workflow.MACHINERY",
                {"openrouter": Mock(), "litellm": litellm_engine},
            ),
            self.assertRaises(CommandError),
        ):
            call_command("check_judge_repair_routes", "--project", self.project.slug)
        litellm_engine.assert_not_called()

    def test_check_judge_repair_routes_missing_route_does_not_fall_through(
        self,
    ) -> None:
        self.project.machinery_settings = {
            "openrouter": {"key": "or-key"},
            "litellm": {"key": "ll-key"},
        }
        self.project.save(update_fields=["machinery_settings"])
        openrouter_engine = Mock()
        openrouter_engine.return_value.resolve_model.return_value = None
        litellm_engine = Mock()
        litellm_engine.return_value.resolve_model.return_value = "vendor/model"
        with (
            patch(
                "weblate.trans.judge_workflow.MACHINERY",
                {"openrouter": openrouter_engine, "litellm": litellm_engine},
            ),
            self.assertRaises(CommandError),
        ):
            call_command("check_judge_repair_routes", "--project", self.project.slug)
        litellm_engine.assert_not_called()

    def test_check_judge_repair_routes_unregistered_engine_does_not_fall_through(
        self,
    ) -> None:
        self.project.machinery_settings = {
            "openrouter": {"key": "or-key"},
            "litellm": {"key": "ll-key"},
        }
        self.project.save(update_fields=["machinery_settings"])
        litellm_engine = Mock()
        litellm_engine.return_value.resolve_model.return_value = "vendor/model"
        with (
            patch(
                "weblate.trans.judge_workflow.MACHINERY", {"litellm": litellm_engine}
            ),
            self.assertRaises(CommandError),
        ):
            call_command("check_judge_repair_routes", "--project", self.project.slug)
        litellm_engine.assert_not_called()


class JudgeReleaseAdvisoryHoldsCommandTest(ComponentTestCase):
    """
    Task 4 of docs/llm-first/plans/2026-08-25-02-judge-run-history-and-resolution-follow-up.md.

    Builds the exact "legacy advisory hold" shape by hand: a unit at
    STATE_NEEDS_CHECKING whose newest Change is the automatic transition
    into it (ActionEvents.AUTO, details state=12/old_state!=12), matching
    ``weblate.trans.autotranslate.AutoTranslate.update`` under the pre-D1
    judge policy. A real human escalation instead writes
    ActionEvents.JUDGE_RESOLUTION and a resolution value; tests 4 and 8
    build that shape explicitly to prove the command leaves it alone.
    """

    def hold_unit(
        self,
        unit: Unit,
        *,
        hold_action: int = ActionEvents.AUTO,
        old_state=STATE_TRANSLATED,
    ) -> Change:
        # A hold always covers real translated text; seed it directly
        # (bypassing translate()) so the only Change this helper adds is
        # the hold Change itself.
        Unit.objects.filter(pk=unit.pk).update(
            target="Ahoj svete!", state=STATE_NEEDS_CHECKING
        )
        unit.target = "Ahoj svete!"
        unit.state = STATE_NEEDS_CHECKING
        return Change.objects.create(
            unit=unit,
            translation=unit.translation,
            component=unit.translation.component,
            project=unit.translation.component.project,
            language=unit.translation.language,
            action=hold_action,
            target=unit.target,
            details={"state": STATE_NEEDS_CHECKING, "old_state": old_state},
        )

    def create_verdict(
        self,
        unit: Unit,
        *,
        max_severity: str = "major",
        model_verdict: str = JudgeVerdict.Verdict.FLAG,
        unparsed: bool = False,
        resolution: str = "",
        target_hash: str | None = None,
    ) -> JudgeVerdict:
        request = build_request(unit)
        return JudgeVerdict.objects.create(
            unit=unit,
            max_severity=max_severity,
            model_verdict=model_verdict,
            unparsed=unparsed,
            judge_model="vendor-a/model",
            seat=1,
            target_hash=target_hash or compute_target_hash(request.target_plurals),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            ),
            resolution=resolution,
        )

    def build_writable_unit(self) -> Unit:
        unit = self.get_unit()
        self.hold_unit(unit)
        self.create_verdict(unit)
        return unit

    def test_dry_run_lists_writable_unit_without_writing(self) -> None:
        unit = self.build_writable_unit()
        change_count = unit.change_set.count()
        output = StringIO()
        call_command("judge_release_advisory_holds", self.project.slug, stdout=output)
        self.assertIn(f"writable=[{unit.pk}]", output.getvalue())
        self.assertIn("1 writable, 0 needs review", output.getvalue())
        self.assertIn("Dry run: nothing written.", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)
        self.assertEqual(unit.change_set.count(), change_count)

    def test_write_releases_writable_unit(self) -> None:
        unit = self.build_writable_unit()
        output = StringIO()
        call_command(
            "judge_release_advisory_holds",
            self.project.slug,
            "--write",
            stdout=output,
        )
        self.assertIn("1 released, 0 stale", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)
        newest = unit.change_set.order_by("-timestamp", "-pk").first()
        assert newest is not None
        self.assertEqual(newest.action, ActionEvents.AUTO)
        self.assertTrue(newest.details.get("judge_advisory_hold_release"))

    def test_write_is_idempotent(self) -> None:
        unit = self.build_writable_unit()
        call_command("judge_release_advisory_holds", self.project.slug, "--write")
        change_count = unit.change_set.count()
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn("0 writable, 0 needs review", output.getvalue())
        self.assertIn("0 released, 0 stale", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)
        self.assertEqual(unit.change_set.count(), change_count)

    def test_missing_hold_change_needs_review(self) -> None:
        unit = self.get_unit()
        Unit.objects.filter(pk=unit.pk).update(state=STATE_NEEDS_CHECKING)
        unit.state = STATE_NEEDS_CHECKING
        self.create_verdict(unit)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn(
            "not provably this verdict's own automatic hold", output.getvalue()
        )
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_human_escalation_hold_needs_review(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit, hold_action=ActionEvents.JUDGE_RESOLUTION)
        self.create_verdict(unit, resolution=JudgeVerdict.Resolution.ESCALATED)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_stale_verdict_needs_review(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit)
        self.create_verdict(unit, target_hash=compute_target_hash(["something else"]))
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn("no fresh parsed verdict", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_hold_change_far_from_the_verdict_needs_review(self) -> None:
        # Task 4 review: ActionEvents.AUTO plus state=12 alone does not
        # prove judge origin (every automatic-translation path shares the
        # action code). A hold Change far outside the round's own voting
        # window must not be trusted even though the action/state pair
        # matches, exactly as an unrelated coincidence would.
        unit = self.get_unit()
        change = self.hold_unit(unit)
        self.create_verdict(unit)
        Change.objects.filter(pk=change.pk).update(
            timestamp=timezone.now() + timedelta(hours=3)
        )
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn(
            "not provably this verdict's own automatic hold", output.getvalue()
        )
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_hold_change_close_to_the_verdict_is_writable(self) -> None:
        # The positive counterpart: comfortably inside the window (a large
        # run's own repair/finalization trailing its round) stays writable.
        unit = self.get_unit()
        change = self.hold_unit(unit)
        self.create_verdict(unit)
        Change.objects.filter(pk=change.pk).update(
            timestamp=timezone.now() + timedelta(hours=1)
        )
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"writable=[{unit.pk}]", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)

    def test_unparsed_verdict_needs_review(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit)
        self.create_verdict(unit, unparsed=True)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_critical_verdict_needs_review(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit)
        self.create_verdict(
            unit, max_severity="critical", model_verdict=JudgeVerdict.Verdict.REJECT
        )
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn("not major", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_existing_resolution_needs_review(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit)
        self.create_verdict(unit, resolution=JudgeVerdict.Resolution.ESCALATED)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn("a resolution already exists", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_failing_enforced_check_needs_review(self) -> None:
        unit = self.build_writable_unit()
        component = unit.translation.component
        component.enforced_checks = ["same"]
        component.save(update_fields=["enforced_checks"])
        Check.objects.create(unit=unit, name="same", dismissed=False)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn(f"needs-review=[{unit.pk}]", output.getvalue())
        self.assertIn("enforced check(s) failing: same", output.getvalue())
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_dismissed_judge_checks_are_reported_but_not_touched(self) -> None:
        unit = self.get_unit()
        check = Check.objects.create(unit=unit, name="judge-flag", dismissed=True)
        output = StringIO()
        call_command(
            "judge_release_advisory_holds", self.project.slug, "--write", stdout=output
        )
        self.assertIn("1 dismissed judge check(s) found", output.getvalue())
        check.refresh_from_db()
        self.assertTrue(check.dismissed)

    def test_disappeared_unit_is_stale(self) -> None:
        unit = self.build_writable_unit()
        pk = unit.pk
        Unit.objects.filter(pk=pk).delete()
        command = JudgeReleaseAdvisoryHoldsCommand()
        released = command.release(pk, self.user)
        self.assertFalse(released)

    def test_concurrent_state_change_before_write_is_stale(self) -> None:
        unit = self.build_writable_unit()
        Unit.objects.filter(pk=unit.pk).update(state=STATE_TRANSLATED)
        command = JudgeReleaseAdvisoryHoldsCommand()
        released = command.release(unit.pk, self.user)
        self.assertFalse(released)
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)

    def test_concurrent_resolution_before_write_is_stale(self) -> None:
        unit = self.get_unit()
        self.hold_unit(unit)
        verdict = self.create_verdict(unit)
        command = JudgeReleaseAdvisoryHoldsCommand()
        # A producer escalates between listing and write: the classification
        # must be retaken under the lock, not trusted from the earlier scan.
        verdict.resolution = JudgeVerdict.Resolution.ESCALATED
        verdict.save(update_fields=["resolution"])
        released = command.release(unit.pk, self.user)
        self.assertFalse(released)
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_NEEDS_CHECKING)

    def test_requires_component_or_project_scope(self) -> None:
        with self.assertRaises(CommandError):
            call_command("judge_release_advisory_holds")

    def test_rejects_all_scope(self) -> None:
        with self.assertRaises(CommandError):
            call_command("judge_release_advisory_holds", "--all", "--write")


class WeblateComponentCommandMixin:
    """Base class for handling tests of WeblateComponentCommand based commands."""

    command_name = "checkgit"
    expected_string = "On branch main"

    def do_test(self, *args, **kwargs) -> None:
        test_case = cast("TestCase", self)
        output = StringIO()
        call_command(self.command_name, *args, stdout=output, **kwargs)
        if self.expected_string:
            test_case.assertIn(self.expected_string, output.getvalue())
        else:
            test_case.assertEqual("", output.getvalue())

    def test_all(self) -> None:
        self.do_test(all=True)

    def test_project(self) -> None:
        self.do_test("test")

    def test_component(self) -> None:
        self.do_test("test/test")

    def test_nonexisting_project(self) -> None:
        test_case = cast("TestCase", self)
        with test_case.assertRaises(CommandError):
            self.do_test("notest")

    def test_nonexisting_component(self) -> None:
        test_case = cast("TestCase", self)
        with test_case.assertRaises(CommandError):
            self.do_test("test/notest")


class WeblateComponentCommandTestCase(ComponentTestCase, WeblateComponentCommandMixin):
    pass


class CommitPendingCommandMixin(WeblateComponentCommandMixin):
    command_name = "commit_pending"
    expected_string = ""

    def test_age(self) -> None:
        self.do_test("test", "--age", "1")


class CommitPendingTest(ComponentTestCase, CommitPendingCommandMixin):
    pass


class CommitPendingChangesTest(ViewTestCase, CommitPendingCommandMixin):
    def setUp(self) -> None:
        super().setUp()
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")


class CommitGitTest(WeblateComponentCommandTestCase):
    command_name = "commitgit"
    expected_string = ""


class PushGitTest(WeblateComponentCommandTestCase):
    command_name = "pushgit"
    expected_string = ""


class LoadTest(WeblateComponentCommandTestCase):
    command_name = "loadpo"
    expected_string = ""


class UpdateChecksTest(WeblateComponentCommandTestCase):
    command_name = "updatechecks"
    expected_string = "Processing"


class UpdateGitTest(WeblateComponentCommandTestCase):
    command_name = "updategit"
    expected_string = ""


class LockTranslationTest(WeblateComponentCommandTestCase):
    command_name = "lock_translation"
    expected_string = ""


class UnLockTranslationTest(WeblateComponentCommandTestCase):
    command_name = "unlock_translation"
    expected_string = ""


class ImportDemoTestCase(TestCase):
    def test_import(self) -> None:
        require_github("https://github.com/WeblateOrg/demo.git")
        output = StringIO()
        call_command("import_demo", stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(Component.objects.count(), 5)


class RequireGitHubTest(SimpleTestCase):
    repository = "https://github.com/WeblateOrg/demo.git"

    @patch("weblate.trans.tests.utils.fetch_url")
    def test_success(self, fetch_url: Mock) -> None:
        require_github(self.repository)

        fetch_url.assert_called_once_with("get", self.repository, timeout=1)

    @patch("weblate.trans.tests.utils.fetch_url")
    def test_request_errors(self, fetch_url: Mock) -> None:
        for exception in (
            httpx2.ConnectError,
            httpx2.TimeoutException,
            httpx2.HTTPError,
        ):
            with self.subTest(exception=exception):
                fetch_url.side_effect = exception("unavailable")
                with self.assertRaisesRegex(SkipTest, "GitHub not reachable"):
                    require_github(self.repository)

    @patch("weblate.trans.tests.utils.fetch_url")
    def test_http_error(self, fetch_url: Mock) -> None:
        fetch_url.side_effect = httpx2.HTTPStatusError(
            "unavailable",
            request=httpx2.Request("GET", self.repository),
            response=httpx2.Response(500),
        )

        with self.assertRaisesRegex(SkipTest, "GitHub not reachable"):
            require_github(self.repository)


class CleanupTestCase(TestCase):
    def test_cleanup(self) -> None:
        output = StringIO()
        call_command("cleanuptrans", stdout=output)
        self.assertEqual(output.getvalue(), "")


class ListTranslatorsTest(RepoTestCase):
    """Test translators list."""

    def setUp(self) -> None:
        super().setUp()
        self.create_component()

    def test_output(self) -> None:
        component = Component.objects.all()[0]
        output = StringIO()
        call_command(
            "list_translators",
            f"{component.project.slug}/{component.slug}",
            stdout=output,
        )
        self.assertEqual(output.getvalue(), "")


class LockingCommandTest(RepoTestCase):
    """Test locking and unlocking."""

    def setUp(self) -> None:
        super().setUp()
        self.create_component()

    def test_locking(self) -> None:
        component = Component.objects.all()[0]
        self.assertFalse(Component.objects.filter(locked=True).exists())
        call_command("lock_translation", f"{component.project.slug}/{component.slug}")
        self.assertTrue(Component.objects.filter(locked=True).exists())
        call_command(
            "unlock_translation",
            f"{component.project.slug}/{component.slug}",
        )
        self.assertFalse(Component.objects.filter(locked=True).exists())


class BenchmarkCommandTest(RepoTestCase):
    """Benchmarking test."""

    def setUp(self) -> None:
        super().setUp()
        self.create_component()

    def test_benchmark(self) -> None:
        output = StringIO()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "benchmark",
                "--project",
                "test",
                "--repo",
                "weblate://test/test",
                "--filemask",
                "po/*.po",
                stdout=output,
            )
        self.assertEqual("", output.getvalue())


class SuggestionCommandTest(RepoTestCase):
    """Test suggestion adding."""

    def setUp(self) -> None:
        super().setUp()
        self.component = self.create_component()

    def test_add_suggestions(self) -> None:
        user = create_test_user()
        call_command(
            "add_suggestions", "test", "test", "cs", TEST_PO, author=user.email
        )
        translation = self.component.translation_set.get(language_code="cs")
        self.assertEqual(translation.stats.suggestions, 1)
        profile = Profile.objects.get(user__email=user.email)
        self.assertEqual(profile.suggested, 1)

    def test_default_user(self) -> None:
        call_command("add_suggestions", "test", "test", "cs", TEST_PO)
        profile = Profile.objects.get(user__email="noreply@weblate.org")
        self.assertEqual(profile.suggested, 1)

    def test_missing_user(self) -> None:
        call_command(
            "add_suggestions", "test", "test", "cs", TEST_PO, author="foo@example.org"
        )
        profile = Profile.objects.get(user__email="foo@example.org")
        self.assertEqual(profile.suggested, 1)

    def test_missing_project(self) -> None:
        with self.assertRaises(CommandError):
            call_command("add_suggestions", "test", "xxx", "cs", TEST_PO)


class ImportCommandTest(RepoTestCase):
    """Import test."""

    def setUp(self) -> None:
        super().setUp()
        self.component = self.create_component()

    def test_import(self) -> None:
        output = StringIO()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_COMPONENTS,
                stdout=output,
            )
        self.assertEqual(self.component.project.component_set.count(), 3)
        self.assertEqual(Translation.objects.count(), 10)
        self.assertIn("Imported Test/Gettext PO with 4 translations", output.getvalue())

    def test_import_invalid(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command("import_json", "--project", "test", TEST_COMPONENTS_INVALID)

    def test_import_twice(self) -> None:
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_COMPONENTS,
            )
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_COMPONENTS,
            )

    def test_import_ignore(self) -> None:
        output = StringIO()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_COMPONENTS,
                stdout=output,
            )
        self.assertIn("Imported Test/Gettext PO with 4 translations", output.getvalue())
        output.truncate()
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                "--ignore",
                TEST_COMPONENTS,
                stderr=output,
            )
        self.assertIn("Component Test/Gettext PO already exists", output.getvalue())

    def test_import_update(self) -> None:
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_COMPONENTS,
            )
        with override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                "--update",
                TEST_COMPONENTS,
            )

    def test_invalid_file(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test",
                TEST_PO,
            )

    def test_nonexisting_project(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_json",
                "--main-component",
                "test",
                "--project",
                "test2",
                "/nonexisting/dfile",
            )

    def test_nonexisting_component(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command(
                "import_json",
                "--main-component",
                "test2",
                "--project",
                "test",
                "/nonexisting/dfile",
            )

    def test_missing_component(self) -> None:
        with (
            self.assertRaises(CommandError),
            override_settings(CREATE_GLOSSARIES=self.CREATE_GLOSSARIES),
        ):
            call_command("import_json", "--project", "test", "/nonexisting/dfile")


class DocumentationCommandTest(TestCase):
    def test_change_event_metadata(self) -> None:
        self.assertEqual(ActionEvents.UPDATE.value, 0)
        self.assertEqual(ActionEvents.UPDATE.label, "Resource updated")
        self.assertEqual(
            ActionEvents.UPDATE.description,
            "A translation file was synchronized with its repository.",
        )
        self.assertEqual(ActionEvents.choices[0], (0, "Resource updated"))
        self.assertTrue(all(str(event.description) for event in ActionEvents))

    def test_list_file_format_params(self) -> None:
        class TestJSONFileFormatParam(BaseFileFormatParam):
            name = "json-test"
            label = "JSONTest"
            file_formats = ("test", "json")
            help_text = "Test JSON file format parameter"

        register_file_format_param(TestJSONFileFormatParam)

        output = StringIO()
        call_command("list_file_format_params", stdout=output)
        self.assertIn("JSONTest", output.getvalue())
        self.assertIn("Test JSON file format parameter", output.getvalue())
        self.assertIn("json-test", output.getvalue())

        FILE_FORMATS_PARAMS.remove(TestJSONFileFormatParam)

    def test_list_change_events(self) -> None:
        output = StringIO()
        call_command("list_change_events", stdout=output)
        result = output.getvalue()
        self.assertIn("``83``", result)
        self.assertIn("``forced_synchronization_of_translations``", result)
        self.assertIn("Forced synchronization of translations", result)
        self.assertIn("Description", result)
        self.assertIn(
            "Translation files were forcibly synchronized with the repository.", result
        )


@override_settings(AUTOFIX_LIST=AUTOFIX_LIST_WITH_CUSTOM)
class ReapplyAutofixesCommandTest(ComponentTestCase, WeblateComponentCommandMixin):
    """Selection, validation and --all come from WeblateComponentCommand."""

    command_name = "reapply_autofixes"
    expected_string = "Active autofixes:"


@override_settings(AUTOFIX_LIST=AUTOFIX_LIST_WITH_CUSTOM)
class ReapplyAutofixesTest(ViewTestCase):
    SOURCE = "Thank you for using Weblate."

    def setUp(self) -> None:
        super().setUp()
        self.unit = self.get_unit(self.SOURCE)
        # A stray zero-width space is the fixture defect: it is repaired in
        # every language and does not depend on the terminal rule, which needs
        # a source without terminal punctuation the fixture cannot offer.
        Unit.objects.filter(pk=self.unit.pk).update(
            target="Merci\u200b", state=STATE_TRANSLATED
        )

    def run_command(self, *args: str) -> str:
        output = StringIO()
        call_command("reapply_autofixes", "test/test", *args, stdout=output)
        return output.getvalue()

    def run_command_for(self, component: Component) -> str:
        output = StringIO()
        call_command(
            "reapply_autofixes",
            "/".join(component.get_url_path()),
            stdout=output,
        )
        return output.getvalue()

    def test_dry_run_reports_without_writing(self) -> None:
        changes = Change.objects.count()
        pending = PendingUnitChange.objects.count()
        result = self.run_command()
        self.assertIn("1 unit to change", result)
        self.assertIn("zero-width-space", result)
        self.assertIn("Merci", result)
        self.assertIn("--apply", result)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci\u200b")
        self.assertEqual(Change.objects.count(), changes)
        self.assertEqual(PendingUnitChange.objects.count(), pending)

    def test_apply_repairs_and_keeps_state(self) -> None:
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")
        self.assertEqual(self.unit.state, STATE_TRANSLATED)
        self.assertTrue(self.unit.change_set.filter(action=ActionEvents.AUTO).exists())

    def test_repair_keeps_a_human_translation_human(self) -> None:
        # translate(change_action=AUTO) sets automatically_translated (unit.py:2427).
        # A punctuation repair must not relabel a translation somebody wrote by
        # hand as machine output, in the unit or in its pending change.
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")
        self.assertFalse(self.unit.automatically_translated)
        self.assertFalse(
            PendingUnitChange.objects.filter(
                unit=self.unit, automatically_translated=True
            ).exists()
        )

    def test_repair_keeps_a_machine_translation_machine(self) -> None:
        # The opposite direction: resetting the flag would erase the record that
        # the string came from a machine.
        Unit.objects.filter(pk=self.unit.pk).update(automatically_translated=True)
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")
        self.assertTrue(self.unit.automatically_translated)
        self.assertFalse(
            PendingUnitChange.objects.filter(
                unit=self.unit, automatically_translated=False
            ).exists()
        )

    def test_commit_holds_the_lock_the_translation_would_reacquire(self) -> None:
        # update_units refreshes component.repository.lock every 1000 changes
        # (translation.py:1453). The lock lives on the Component instance, so a
        # translation carrying its own instance reacquires a lock nobody holds
        # and the commit dies with "Cannot reacquire an unlocked lock" - which
        # is what happened on prod with 1372 pending changes in one language.
        locked: list[bool] = []
        original = Translation.update_units

        def spy(translation, *args, **kwargs):
            locked.append(translation.component.repository.lock.is_locked)
            return original(translation, *args, **kwargs)

        with patch.object(Translation, "update_units", autospec=True, side_effect=spy):
            self.run_command("--apply")

        self.assertEqual(locked, [True])

    def test_dump_json_writes_every_change_without_writing_to_the_database(
        self,
    ) -> None:
        # The review dump has to come from the command itself, not from a
        # separate script: only then is the reviewed before/after exactly what
        # --apply would store.
        path = Path(tempfile.mkdtemp()) / "dump.json"
        result = self.run_command("--dump-json", str(path))
        records = json.loads(path.read_text())
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0],
            {
                "unit_id": self.unit.pk,
                "component": str(self.component),
                "language": "cs",
                "context": self.unit.context,
                "source": [self.SOURCE],
                "target": ["Merci\u200b"],
                "proposed": ["Merci"],
                "fixes": ["zero-width-space"],
            },
        )
        self.assertIn(str(path), result)
        self.assertEqual(PendingUnitChange.objects.count(), 0)

    def test_second_apply_changes_nothing(self) -> None:
        self.run_command("--apply")
        changes = Change.objects.count()
        result = self.run_command("--apply")
        self.assertIn("0 units to change", result)
        self.assertEqual(Change.objects.count(), changes)

    def test_clean_unit_is_never_written(self) -> None:
        other = self.get_unit("Hello, world!\n")
        Unit.objects.filter(pk=other.pk).update(
            target="Ahoj svete!\n", automatically_translated=False
        )
        before = other.change_set.count()
        self.run_command("--apply")
        other.refresh_from_db()
        self.assertEqual(other.target, "Ahoj svete!\n")
        self.assertFalse(other.automatically_translated)
        self.assertEqual(other.change_set.count(), before)

    def test_readonly_unit_is_skipped(self) -> None:
        Unit.objects.filter(pk=self.unit.pk).update(state=STATE_READONLY)
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci\u200b")

    def test_source_translation_is_skipped(self) -> None:
        source_unit = self.component.source_translation.unit_set.get(source=self.SOURCE)
        Unit.objects.filter(pk=source_unit.pk).update(
            target="Merci\u200b", state=STATE_TRANSLATED
        )
        changes = source_unit.change_set.count()
        pending = PendingUnitChange.objects.filter(unit=source_unit).count()

        result = self.run_command()

        self.assertIn("1 unit to change", result)
        self.run_command("--apply")
        source_unit.refresh_from_db()
        self.assertEqual(source_unit.source, self.SOURCE)
        self.assertEqual(source_unit.target, "Merci\u200b")
        self.assertEqual(source_unit.change_set.count(), changes)
        self.assertEqual(
            PendingUnitChange.objects.filter(unit=source_unit).count(), pending
        )

    def test_glossary_component_is_skipped(self) -> None:
        self.component.create_glossary()
        glossary = Component.objects.get(project=self.project, is_glossary=True)
        result = self.run_command_for(glossary)
        self.assertIn("skipped (glossary)", result)

    def test_diff_examples_are_capped(self) -> None:
        index = 0
        for translation in self.component.translation_set.all():
            for unit in translation.unit_set.all():
                Unit.objects.filter(pk=unit.pk).update(target=f"Merci {index}\u200b")
                index += 1
        result = self.run_command()
        self.assertIn("more", result)
        self.assertLessEqual(
            len([line for line in result.splitlines() if " -> " in line]), 5
        )

    @override_settings(AUTOFIX_LIST=["weblate.trans.autofixes.chars.RemoveZeroSpace"])
    def test_missing_required_autofix_fails_closed(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command()

    def test_concurrent_edit_is_not_overwritten(self) -> None:
        # The edit has to land between the scan and the row lock. Hooking
        # Unit.translate would be too late: by then repair() has already read
        # the row under select_for_update and recomputed, so even a correct
        # implementation writes the scanned value and the test fails on good
        # code. Command.get_user() runs once, after the scan and before
        # apply_group takes any lock, which is exactly the window.
        original_get_user = Command.get_user

        def edit_first(command):
            Unit.objects.filter(pk=self.unit.pk).update(target="Merci beaucoup")
            return original_get_user(command)

        changes = Change.objects.count()
        with patch.object(Command, "get_user", autospec=True, side_effect=edit_first):
            self.run_command("--apply")
        self.unit.refresh_from_db()
        # Not merely "not Merci": the translator's text must survive intact,
        # and a repair that found nothing to fix must leave no trace behind.
        self.assertEqual(self.unit.target, "Merci beaucoup")
        self.assertFalse(self.unit.automatically_translated)
        self.assertEqual(Change.objects.count(), changes)

    def test_translate_receives_the_unfixed_target_and_fixes_it(self) -> None:
        # repair() hands translate() the UNFIXED target on purpose: translate()
        # runs fix_target internally (unit.py:2406) and that call is what
        # records unit.fixups. Both halves are pinned here because each can
        # break alone: pre-fixing in the command would lose the fixups, and a
        # translate() that stopped applying autofixes would turn every repair
        # into a no-op write with a Change attached.
        captured: list[list[str]] = []
        original_translate = Unit.translate

        def spy(unit, user, new_target, *args, **kwargs):
            captured.append(list(new_target))
            return original_translate(unit, user, new_target, *args, **kwargs)

        with patch.object(Unit, "translate", autospec=True, side_effect=spy):
            self.run_command("--apply")
        self.assertEqual(captured, [["Merci\u200b"]])
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")

    def test_apply_does_not_propagate_to_another_component(self) -> None:
        second = Component.objects.create(
            name="Test 2",
            slug="test-2",
            project=self.project,
            repo=self.git_repo_path,
            vcs="git",
            filemask="po/*.po",
            template="",
            file_format="po",
            new_base="",
            allow_translation_propagation=True,
        )
        other = second.translation_set.get(language_code="cs").unit_set.get(
            source=self.SOURCE
        )
        Unit.objects.filter(pk=other.pk).update(target="Merci\u200b")
        self.component.allow_translation_propagation = True
        self.component.save()
        self.run_command("--apply")
        other.refresh_from_db()
        self.assertEqual(other.target, "Merci\u200b")

    def test_apply_commits_all_translations_once(self) -> None:
        linked = self.create_link_existing(
            name="Test 2", slug="test-2", filemask="po/*.po"
        )
        other = linked.translation_set.get(language_code="cs").unit_set.get(
            source=self.SOURCE
        )
        Unit.objects.filter(pk=other.pk).update(
            target="Merci\u200b", state=STATE_TRANSLATED
        )
        revision = self.component.repository.last_revision
        output = StringIO()

        call_command(
            "reapply_autofixes",
            "test/test",
            "test/test-2",
            "--apply",
            stdout=output,
        )

        self.component.repository.clean_revision_cache()
        current = self.component.repository.last_revision
        self.assertEqual(
            self.component.repository.log_revisions(f"{revision}..{current}"),
            [current],
        )
        other.refresh_from_db()
        self.assertEqual(other.target, "Merci")

    def test_foreign_pending_changes_block_the_commit(self) -> None:
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        with (
            patch.object(Component, "commit_pending") as commit,
            self.assertRaises(CommandError),
        ):
            self.run_command("--apply")
        commit.assert_not_called()

    def test_foreign_pending_change_on_repaired_unit_blocks_the_commit(self) -> None:
        original_repair = Command.repair
        other_user = create_another_user("-foreign")
        own_pending_ids: list[int] = []

        def create_foreign_pending(command, unit_id, user):
            result = original_repair(command, unit_id, user)
            own_pending_ids.append(
                PendingUnitChange.objects.filter(unit_id=unit_id, author=user)
                .latest("pk")
                .pk
            )
            PendingUnitChange.store_unit_change(
                self.unit,
                author=other_user,
                target="Merci du traducteur",
                state=STATE_TRANSLATED,
            )
            return result

        with (
            patch.object(
                Command, "repair", autospec=True, side_effect=create_foreign_pending
            ),
            patch.object(Component, "commit_files", return_value=True) as commit,
            self.assertRaises(CommandError),
        ):
            self.run_command("--apply")

        commit.assert_not_called()
        self.assertTrue(
            PendingUnitChange.objects.filter(pk=own_pending_ids[0]).exists()
        )
        self.assertTrue(
            PendingUnitChange.objects.filter(unit=self.unit, author=other_user).exists()
        )

    def test_vcs_commit_failure_keeps_own_pending_changes(self) -> None:
        with (
            patch.object(Component, "commit_files", return_value=False) as commit,
            self.assertRaises(CommandError),
        ):
            self.run_command("--apply")

        commit.assert_called_once()
        self.assertTrue(PendingUnitChange.objects.filter(unit=self.unit).exists())

    def test_late_foreign_pending_change_stays_pending(self) -> None:
        original_foreign_pending = Command.foreign_pending
        other_user = create_another_user("-late")
        late_pending_ids: list[int] = []
        own_pending_ids: list[int] = []
        calls = 0

        def create_late_pending(root, pending_change_ids):
            nonlocal calls
            result = original_foreign_pending(root, pending_change_ids)
            calls += 1
            if calls == 2:
                own_pending_ids.extend(pending_change_ids)
                pending_change = PendingUnitChange.store_unit_change(
                    self.unit,
                    author=other_user,
                    target="Merci du traducteur",
                    state=STATE_TRANSLATED,
                )
                assert pending_change.pk is not None
                late_pending_ids.append(pending_change.pk)
            return result

        result = StringIO()
        with patch.object(Command, "foreign_pending", side_effect=create_late_pending):
            call_command("reapply_autofixes", "test/test", "--apply", stdout=result)

        self.assertEqual(calls, 2)
        self.assertIn("1 written", result.getvalue())
        self.assertFalse(
            PendingUnitChange.objects.filter(pk=own_pending_ids[0]).exists()
        )
        self.assertTrue(
            PendingUnitChange.objects.filter(pk=late_pending_ids[0]).exists()
        )


class JudgeCloseRefusedVerdictsCommandTest(ComponentTestCase):
    """The legacy refused-verdict cleanup command guards every deletion."""

    def make_attempt(self, **kwargs) -> JudgeRequestAttempt:
        kwargs.setdefault("seat", 1)
        kwargs.setdefault("endpoint_fingerprint", "e" * 64)
        kwargs.setdefault("model", "vendor/model-a")
        kwargs.setdefault("profile_fingerprint", "p" * 64)
        kwargs.setdefault("prompt_schema_version", "v1")
        kwargs.setdefault("batch_digest", "b" * 64)
        kwargs.setdefault("batch_size", 1)
        return JudgeRequestAttempt.objects.create(**kwargs)

    def make_verdict(self, unit, attempt=None, **kwargs) -> JudgeVerdict:
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        kwargs.setdefault("unparsed", True)
        return JudgeVerdict.objects.create(unit=unit, request_attempt=attempt, **kwargs)

    def make_run_unit(self, unit, verdict, outcome) -> JudgeRunUnit:
        run = JudgeRun.objects.create(
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label="test/test",
            scope_path="test/test",
            requested_mode="observe",
            cap=100,
        )
        return JudgeRunUnit.objects.create(
            run=run,
            unit=unit,
            unit_id_snapshot=unit.pk,
            translation_id=unit.translation_id,
            component_id=unit.translation.component_id,
            project_id=unit.translation.component.project_id,
            input_target=unit.get_target_plurals(),
            input_target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="c",
            outcome=outcome,
            verdict=verdict,
        )

    def test_dry_run_lists_candidates_by_status(self) -> None:
        unit = self.get_unit()
        refused_400 = self.make_attempt(http_status=400, failure_kind="http-other")
        refused_401 = self.make_attempt(http_status=401, failure_kind="http-auth")
        v400 = self.make_verdict(unit, refused_400)
        v401 = self.make_verdict(unit, refused_401, seat=2)
        run_unit = self.make_run_unit(unit, v400, JudgeRunUnit.Outcome.UNPARSED)

        output = StringIO()
        call_command(
            "judge_close_refused_verdicts",
            "--expected-count",
            "2",
            stdout=output,
        )
        text = output.getvalue()
        self.assertIn("total: 2", text)
        self.assertIn("400", text)
        self.assertIn("401", text)
        # Dry-run deletes nothing and reclassifies nothing.
        self.assertTrue(JudgeVerdict.objects.filter(pk__in=[v400.pk, v401.pk]).exists())
        run_unit.refresh_from_db()
        self.assertEqual(run_unit.outcome, JudgeRunUnit.Outcome.UNPARSED)

    def test_confirm_deletes_and_reclassifies(self) -> None:
        unit = self.get_unit()
        attempt = self.make_attempt(http_status=400, failure_kind="http-other")
        verdict = self.make_verdict(unit, attempt)
        run_unit = self.make_run_unit(unit, verdict, JudgeRunUnit.Outcome.UNPARSED)

        output = StringIO()
        call_command(
            "judge_close_refused_verdicts",
            "--expected-count",
            "1",
            "--confirm",
            stdout=output,
        )
        self.assertFalse(JudgeVerdict.objects.filter(pk=verdict.pk).exists())
        # The reported number is the database result, not the pre-transaction
        # snapshot an operator would have no way to audit.
        self.assertIn("1 verdicts deleted", output.getvalue())
        self.assertIn("1 run-unit rows reclassified", output.getvalue())
        # The attempt ledger survives as the diagnostic record.
        self.assertTrue(JudgeRequestAttempt.objects.filter(pk=attempt.pk).exists())
        run_unit.refresh_from_db()
        self.assertEqual(run_unit.outcome, JudgeRunUnit.Outcome.REFUSED)
        self.assertIsNone(run_unit.verdict_id)

    def test_confirm_refuses_on_count_mismatch(self) -> None:
        unit = self.get_unit()
        attempt = self.make_attempt(http_status=400, failure_kind="http-other")
        verdict = self.make_verdict(unit, attempt)

        with self.assertRaises(CommandError) as captured:
            call_command(
                "judge_close_refused_verdicts",
                "--expected-count",
                "7",
                "--confirm",
            )
        self.assertIn("candidate count changed", str(captured.exception))
        self.assertTrue(JudgeVerdict.objects.filter(pk=verdict.pk).exists())

    def test_exclusions_are_never_touched(self) -> None:
        unit = self.get_unit()
        # 413 is size-dependent, not a refusal: keep it (plan Task 4).
        oversized = self.make_attempt(
            http_status=413, failure_kind="response-too-large"
        )
        v_oversized = self.make_verdict(unit, oversized)
        # A real HTTP 500 under http-other: keep it.
        server = self.make_attempt(http_status=500, failure_kind="http-other")
        v_server = self.make_verdict(unit, server)
        # A 120-second deadline attempt: http_status NULL, keep it (historical control).
        deadline = self.make_attempt(failure_kind="deadline")
        v_deadline = self.make_verdict(unit, deadline)
        # A transport attempt: http_status NULL, keep it.
        transport = self.make_attempt(failure_kind="transport")
        v_transport = self.make_verdict(unit, transport)
        # No attempt at all: keep it.
        v_noattempt = self.make_verdict(unit, None)
        # Parsed verdict linked to a 400 attempt: keep it.
        ok_attempt = self.make_attempt(http_status=400, failure_kind="http-other")
        v_parsed = self.make_verdict(unit, ok_attempt, unparsed=False)
        keep = [
            v_oversized.pk,
            v_server.pk,
            v_deadline.pk,
            v_transport.pk,
            v_noattempt.pk,
            v_parsed.pk,
        ]
        # Only a 400 refusal is a candidate.
        refused = self.make_attempt(http_status=400, failure_kind="http-other")
        v_refused = self.make_verdict(unit, refused)

        output = StringIO()
        call_command(
            "judge_close_refused_verdicts",
            "--expected-count",
            "1",
            "--confirm",
            stdout=output,
        )
        self.assertFalse(JudgeVerdict.objects.filter(pk=v_refused.pk).exists())
        self.assertEqual(
            set(JudgeVerdict.objects.filter(pk__in=keep).values_list("pk", flat=True)),
            set(keep),
        )
