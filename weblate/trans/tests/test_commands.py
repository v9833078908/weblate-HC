# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for management commands."""

import sys
from io import StringIO
from typing import cast
from unittest import SkipTest
from unittest.mock import Mock, patch

import httpx2
from django.core.management import call_command
from django.core.management.base import CommandError, SystemCheckError
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings

from weblate.accounts.models import Profile
from weblate.runner import main
from weblate.trans.actions import ActionEvents
from weblate.trans.file_format_params import (
    FILE_FORMATS_PARAMS,
    BaseFileFormatParam,
    register_file_format_param,
)
from weblate.trans import defaults as trans_defaults
from weblate.trans.models import Change, Component, Unit
from weblate.trans.models.pending import PendingUnitChange
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
from weblate.vcs.mercurial import HgRepository
from weblate.utils.state import STATE_READONLY, STATE_TRANSLATED

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
        Unit.objects.filter(pk=self.unit.pk).update(
            target="Merci\u202f!", state=STATE_TRANSLATED
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
        self.assertIn("removed-final-stop", result)
        self.assertIn("Merci", result)
        self.assertIn("--apply", result)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci\u202f!")
        self.assertEqual(Change.objects.count(), changes)
        self.assertEqual(PendingUnitChange.objects.count(), pending)

    def test_apply_repairs_and_keeps_state(self) -> None:
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")
        self.assertEqual(self.unit.state, STATE_TRANSLATED)
        self.assertTrue(
            self.unit.change_set.filter(action=ActionEvents.AUTO).exists()
        )

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
        self.assertEqual(self.unit.target, "Merci\u202f!")

    def test_glossary_component_is_skipped(self) -> None:
        self.component.create_glossary()
        glossary = Component.objects.get(project=self.project, is_glossary=True)
        result = self.run_command_for(glossary)
        self.assertIn("skipped (glossary)", result)

    def test_diff_examples_are_capped(self) -> None:
        index = 0
        for translation in self.component.translation_set.all():
            for unit in translation.unit_set.all():
                Unit.objects.filter(pk=unit.pk).update(
                    target=f"Merci {index}\u202f!"
                )
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
        from weblate.trans.management.commands.reapply_autofixes import Command

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
        self.assertEqual(captured, [["Merci\u202f!"]])
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
        Unit.objects.filter(pk=other.pk).update(target="Merci\u202f!")
        self.component.allow_translation_propagation = True
        self.component.save()
        self.run_command("--apply")
        other.refresh_from_db()
        self.assertEqual(other.target, "Merci\u202f!")

    def test_apply_commits_the_repository_once(self) -> None:
        with patch.object(Component, "commit_pending", return_value=True) as commit:
            self.run_command("--apply")
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(commit.call_args.kwargs["skip_push"], True)

    def test_foreign_pending_changes_block_the_commit(self) -> None:
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        with patch.object(Component, "commit_pending") as commit:
            with self.assertRaises(CommandError):
                self.run_command("--apply")
        commit.assert_not_called()
