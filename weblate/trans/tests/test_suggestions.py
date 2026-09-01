# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for suggestion views."""

from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from weblate.auth.models import Group, Permission, Role
from weblate.auth.results import Denied
from weblate.trans.judge_loop import build_request
from weblate.trans.models import Suggestion, Vote, WorkflowSetting
from weblate.trans.models.judge import (
    JudgeCandidateError,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_FUZZY, STATE_READONLY, STATE_TRANSLATED


class SuggestionsTest(ViewTestCase):
    def add_suggestion_1(self):
        return self.edit_unit("Hello, world!\n", "Nazdar svete!\n", suggest="yes")

    def add_suggestion_2(self):
        return self.edit_unit("Hello, world!\n", "Ahoj svete!\n", suggest="yes")

    def test_add(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Try empty suggestion (should not be added)
        response = self.edit_unit("Hello, world!\n", "", suggest="yes")
        # We should stay on same message
        self.assert_redirects_offset(response, translate_url, 1)

        # Add first suggestion
        response = self.add_suggestion_1()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)

        # Add second suggestion
        response = self.add_suggestion_2()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)
        self.assert_backend(0)

        # Unit should not be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertFalse(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(len(self.get_unit().suggestions), 2)

    def test_add_same(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Add first suggestion
        response = self.add_suggestion_1()
        # We should get to second message
        self.assert_redirects_offset(response, translate_url, 2)
        # Add first suggestion
        response = self.add_suggestion_1()
        # We should stay on same message
        self.assert_redirects_offset(response, translate_url, 1)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")

        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)
        self.assert_backend(0)

        # Unit should not be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertFalse(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(len(self.get_unit().suggestions), 1)

    def test_delete(self, **kwargs) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create two suggestions
        self.add_suggestion_1()
        self.add_suggestion_2()

        # Get ids of created suggestions
        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 2)

        # Delete one of suggestions
        response = self.edit_unit(
            "Hello, world!\n", "", delete=suggestions[0], **kwargs
        )
        self.assert_redirects_offset(response, translate_url, 1)

        # Ensure we have just one
        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 1)

    def test_change_diff_for_deleted_suggestion(self, **kwargs) -> None:
        """
        Check that the diff for deleted suggestion is correctly displayed.

        When a suggestion is deleted, the diff for the deleted suggestion
        should be the same as the corresponding "Suggestion added"
        """
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.edit_unit("Hello, world!\n", "Nazdar!\n")
        self.add_suggestion_1()
        response = self.client.get(translate_url)
        self.assertNotContains(response, "Suggestion removed")
        # 1st diff occurrence is in the "Suggestions" tab, the 2nd in "History" tab
        self.assertContains(
            response,
            """Nazdar<ins><span class="hlspace"><span class="space-space"> </span></span>svete</ins>!""",
            count=2,
        )

        suggestions = self.get_unit().suggestions.values_list("pk", flat=True)
        self.assertEqual(len(suggestions), 1)
        response = self.edit_unit(
            "Hello, world!\n", "", delete=suggestions[0], **kwargs
        )

        response = self.client.get(translate_url)
        self.assertContains(response, "Suggestion removed", count=1)
        self.assertContains(response, "Suggestion added", count=1)
        # both diff occurrence are in "History" tab,
        # as suggestion is no longer visible in the "Suggestion" tab
        self.assertContains(
            response,
            """Nazdar<ins><span class="hlspace"><span class="space-space"> </span></span>svete</ins>!""",
            count=2,
        )

    def test_delete_spam(self) -> None:
        self.test_delete(spam="1")

    def test_accept_edit(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create suggestion
        self.add_suggestion_1()

        # Get ids of created suggestions
        suggestion = self.get_unit().suggestions[0].pk

        # Accept one of suggestions
        response = self.edit_unit("Hello, world!\n", "", accept_edit=suggestion)
        self.assert_redirects_offset(response, translate_url, 1)

    def test_accept(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        # Create two suggestions
        self.add_suggestion_1()
        self.add_suggestion_2()

        # Get ids of created suggestions
        suggestions = self.get_unit().suggestions
        self.assertEqual(suggestions.count(), 2)

        # Accept one of suggestions
        response = self.edit_unit(
            "Hello, world!\n", "", accept=suggestions.get(target="Ahoj svete!\n").pk
        )
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 1)

        # Unit should be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertTrue(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(unit.target, "Ahoj svete!\n")
        self.assert_backend(1)
        self.assertEqual(len(self.get_unit().suggestions), 1)

    def test_accept_anonymous(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.client.logout()
        # Create suggestions
        self.add_suggestion_1()

        self.client.login(username="testuser", password="testpassword")

        # Get ids of created suggestion
        suggestions = list(self.get_unit().suggestions)
        self.assertEqual(len(suggestions), 1)

        suggestion = suggestions[0]
        if suggestion.user is None:
            msg = "Suggestion user should not be None"
            raise AssertionError(msg)

        user = suggestion.user
        self.assertEqual(user.username, settings.ANONYMOUS_USER_NAME)

        # Accept one of suggestions
        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", accept=suggestions[0].pk)
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 0)

        # Unit should be translated
        self.assertEqual(unit.target, "Nazdar svete!\n")

    def test_vote_language(self) -> None:
        WorkflowSetting.objects.create(
            project=self.project,
            language=self.translation.language,
            enable_suggestions=True,
            suggestion_voting=True,
            suggestion_autoaccept=0,
        )

        self.assert_vote()

    def test_restrict_direct_editing(self) -> None:
        WorkflowSetting.objects.create(
            project=self.project,
            language=self.translation.language,
            enable_suggestions=True,
            restrict_direct_editing=True,
        )
        unit = self.get_unit()

        permission = self.user.has_perm("unit.edit", unit)
        self.assertIsInstance(permission, Denied)
        self.assertEqual(
            permission.reason,
            "Only privileged users can edit strings directly in this language because "
            "of its translation workflow. Add a suggestion instead.",
        )
        self.assertTrue(self.user.has_perm("suggestion.add", unit))

        response = self.client.get(self.translation_url)
        self.assertContains(
            response,
            "Direct translation editing is restricted to privileged users.",
        )
        self.assertContains(response, "Translation suggestions can be made.")
        self.assertNotContains(response, "Translations can be made directly.")
        self.assertContains(self.client.get(unit.get_absolute_url()), permission.reason)

        self.add_suggestion_1()
        self.assertEqual(unit.suggestion_set.count(), 1)
        self.assertFalse(self.get_unit().translated)

        self.project.add_user(self.user, "Administration")
        self.user.clear_permissions_cache()
        self.assertTrue(self.user.has_perm("unit.edit", unit))

    def test_restrict_direct_editing_without_suggestions(self) -> None:
        WorkflowSetting.objects.create(
            project=self.project,
            language=self.translation.language,
            enable_suggestions=False,
            restrict_direct_editing=True,
        )
        unit = self.get_unit()

        permission = self.user.has_perm("unit.edit", unit)
        self.assertIsInstance(permission, Denied)
        self.assertEqual(
            permission.reason,
            "Only privileged users can edit strings directly in this language because "
            "of its translation workflow. Ask a project administrator for access.",
        )
        suggestion_permission = self.user.has_perm("suggestion.add", unit)
        self.assertIsInstance(suggestion_permission, Denied)
        self.assertEqual(
            suggestion_permission.reason,
            "Suggestions are turned off for this language. Ask a project administrator "
            "to enable them.",
        )

        response = self.client.get(self.translation_url)
        self.assertContains(
            response,
            "Direct translation editing is restricted to privileged users.",
        )
        self.assertContains(response, "Translation suggestions are turned off.")
        self.assertNotContains(response, "Translations can be made directly.")
        self.assertContains(self.client.get(unit.get_absolute_url()), permission.reason)

        response = self.edit_unit(
            "Hello, world!\n", "Nazdar svete!\n", suggest="yes", follow=True
        )
        self.assertContains(response, suggestion_permission.reason)
        self.assertEqual(unit.suggestion_set.count(), 0)

    def test_suggestion_readonly_denials(self) -> None:
        unit = self.get_unit()
        original_state = unit.state
        unit.state = STATE_READONLY
        unit.save(update_fields=["state"])

        permission = self.user.has_perm("suggestion.add", unit)
        self.assertIsInstance(permission, Denied)
        self.assertEqual(permission.reason, "The string is read-only.")

        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, 'title="The string is read-only."')

        unit.state = original_state
        unit.save(update_fields=["state"])
        translation = unit.translation
        translation.check_flags = "read-only"
        translation.save(update_fields=["check_flags"])
        unit = self.get_unit()

        permission = self.user.has_perm("suggestion.add", unit)
        self.assertIsInstance(permission, Denied)
        self.assertEqual(permission.reason, "The translation is read-only.")

    def test_restrict_direct_editing_allows_voting(self) -> None:
        WorkflowSetting.objects.create(
            project=self.project,
            language=self.translation.language,
            enable_suggestions=True,
            restrict_direct_editing=True,
            suggestion_voting=True,
            suggestion_autoaccept=0,
        )

        self.assert_vote()

    def test_vote(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        self.assert_vote()

    def test_vote_without_autoaccept_keeps_direct_translation(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")

        unit = self.get_unit()
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assert_backend(1)

    def test_vote_autoaccept_direct_translation_denied(self) -> None:
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 1
        self.component.save()

        response = self.edit_unit("Hello, world!\n", "Nazdar svete!\n", follow=True)

        self.assertContains(
            response,
            "This translation is configured for suggestion voting. Add a suggestion instead of saving a direct translation.",
        )
        self.assertFalse(self.get_unit().translated)
        self.assert_backend(0)

    def test_vote_when_voting_disabled(self) -> None:
        self.add_suggestion_1()
        suggestion_id = self.get_unit().suggestions[0].pk

        response = self.edit_unit(
            "Hello, world!\n", "", upvote=suggestion_id, follow=True
        )

        self.assertContains(response, "Suggestion voting is disabled.")
        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), 0)

    def assert_vote(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.add_suggestion_1()

        suggestion_id = self.get_unit().suggestions[0].pk

        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", upvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 2)

        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), 1)

        response = self.edit_unit("Hello, world!\n", "", downvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 1)

        suggestion = Suggestion.objects.get(pk=suggestion_id)
        self.assertEqual(suggestion.get_num_votes(), -1)

    def test_vote_autoaccept(self) -> None:
        self.add_suggestion_1()

        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 1
        self.component.save()

        suggestion_id = self.get_unit().suggestions[0].pk

        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit_unit("Hello, world!\n", "", upvote=suggestion_id)
        self.assert_redirects_offset(response, translate_url, 2)

        # Reload from database
        unit = self.get_unit()
        translation = self.component.translation_set.get(language_code="cs")
        # Check number of suggestions
        self.assertEqual(translation.stats.suggestions, 0)

        # Unit should be translated
        self.assertEqual(len(unit.all_checks), 0)
        self.assertTrue(unit.translated)
        self.assertFalse(unit.fuzzy)
        self.assertEqual(unit.target, "Nazdar svete!\n")
        self.assert_backend(1)

    def test_vote_when_same_suggestion(self) -> None:
        translate_url = reverse("translate", kwargs=self.kw_translation)
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 0
        self.component.save()

        # Add the first suggestion as default test-user
        response = self.add_suggestion_1()
        suggestion_id = self.get_unit().suggestions[0].pk
        suggestion = Suggestion.objects.get(pk=suggestion_id)

        # Suggestion get vote from the user that makes suggestion
        self.assertEqual(suggestion.get_num_votes(), 1)

        # Add suggestion as second user
        self.log_as_jane()
        response = self.add_suggestion_1()

        # When adding the same suggestion, we stay on the same page
        self.assert_redirects_offset(response, translate_url, 1)
        suggestion = Suggestion.objects.get(pk=suggestion_id)

        # and the suggestion gets an upvote
        self.assertEqual(suggestion.get_num_votes(), 2)

    def test_rendering_check_isolation(self) -> None:
        """Verify that suggestion checks do not reuse the dirty cache of the unit."""
        # Force SameCheck to ensure the suggestion system actually runs checks.
        self.component.checks = "weblate.checks.same.SameCheck"  # type: ignore[attr-defined]
        self.component.save()

        unit = self.get_unit()

        # Set dirty cache in memory
        unit.check_cache = {"render": "DIRTY_PARENT_CACHE"}

        # Create suggestion
        suggestion = Suggestion.objects.create(
            unit=unit,
            target=unit.source,
            user=self.user,
        )

        # We explicitly assign the dirty unit to the suggestion.
        suggestion.unit = unit

        # Explicitly trigger check calculation
        checks = list(suggestion.get_checks())
        self.assertTrue(checks, "Setup Error: No checks were generated.")

        check_obj = next((check for check in checks if hasattr(check, "unit")), None)
        if check_obj is None:
            msg = "No check with a 'unit' attribute found."
            raise AssertionError(msg)

        fake_unit = check_obj.unit

        # Verification of Isolation
        self.assertTrue(
            hasattr(fake_unit, "check_cache"),
            "Test Failure: Fake unit does not expose 'check_cache' attribute.",
        )

        # We do not check for empty cache ({}) because running checks fills it.
        # We check that the parent's dirt is gone.
        self.assertIsNot(
            fake_unit, unit, "Fake unit should be a different instance/copy"
        )

        self.assertIsNot(
            fake_unit.check_cache,
            unit.check_cache,
            "Isolation Failure: check_cache dictionary is shared by reference.",
        )

        self.assertNotEqual(fake_unit.check_cache.get("render"), "DIRTY_PARENT_CACHE")

        # Ensure the process didn't accidentally wipe the parent's cache
        self.assertEqual(
            unit.check_cache.get("render"),
            "DIRTY_PARENT_CACHE",
            "Integrity Failure: Parent unit cache was wiped during check execution.",
        )


class SuggestionModelTest(TestCase):
    def test_target_list(self):
        """Test that target_list property correctly splits plurals."""
        sep = "\x1e\x1e"

        suggestion = Suggestion(target="Hello world")
        self.assertEqual(suggestion.target_list, ["Hello world"])

        target_string = f"One apple{sep}Two apples{sep}Five apples"
        suggestion_plural = Suggestion(target=target_string)

        self.assertEqual(
            suggestion_plural.target_list, ["One apple", "Two apples", "Five apples"]
        )

        suggestion_empty = Suggestion(target="")
        self.assertEqual(suggestion_empty.target_list, [""])


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeCandidateSuggestionTest(ViewTestCase):
    """Task 4: Suggestion.accept() delegates for stored judge candidates."""

    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def grant(self, codenames) -> None:
        role = Role.objects.create(name="Custom accept")
        for codename in codenames:
            role.permissions.add(Permission.objects.get(codename=codename))
        group = Group.objects.create(name="Custom accepters")
        group.roles.add(role)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

    def make_verdict(self, unit, severity="critical"):
        request = build_request(unit)
        return JudgeVerdict.objects.create(
            unit=unit,
            max_severity=severity,
            seat=1,
            judge_model="vendor/model-a",
            target_hash=compute_target_hash(unit.get_target_plurals()),
            target_storage_hash=compute_target_storage_hash(unit.target),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                explanation=request.explanation,
                glossary_terms=request.glossary_terms,
            ),
        )

    def make_candidate(self, unit, verdict, target="Better translation"):
        request = build_request(unit)
        suggestion, _result = Suggestion.objects.add(
            unit,
            [target],
            request=None,
            vote=False,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": compute_target_hash(unit.get_target_plurals()),
                "context_hash": compute_context_hash(
                    source=request.source,
                    note=request.note,
                    explanation=request.explanation,
                    glossary_terms=request.glossary_terms,
                ),
                "engine": "openrouter",
            },
        )
        return suggestion

    def test_is_judge_candidate_true_for_metadata_tagged_row(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit)
        candidate = self.make_candidate(unit, verdict)
        self.assertTrue(candidate.is_judge_candidate)

    def test_is_judge_candidate_false_for_human_suggestion(self) -> None:
        unit = self.get_unit()
        suggestion = Suggestion.objects.create(
            unit=unit, target="Human text", user=self.user
        )
        self.assertFalse(suggestion.is_judge_candidate)

    def test_is_judge_candidate_true_even_when_metadata_is_malformed(self) -> None:
        unit = self.get_unit()
        suggestion = Suggestion.objects.create(
            unit=unit, target="Broken", userdetails={"kind": "judge-repair"}
        )
        self.assertTrue(suggestion.is_judge_candidate)

    def test_accept_delegates_to_the_guarded_service(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit)
        candidate = self.make_candidate(unit, verdict)
        request = self.get_request()
        request.user = self.user
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            candidate.accept(request)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        self.assertFalse(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_without_review_permission_raises_and_is_untouched(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit)
        candidate = self.make_candidate(unit, verdict)
        request = self.get_request()
        request.user = self.user
        with self.assertRaises(JudgeCandidateError):
            candidate.accept(request)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_classic_accept_by_id_view_reports_the_typed_error(self) -> None:
        # suggestion.accept alone is not enough for a judge candidate
        # (invariant 5): the classic per-suggestion accept form must
        # surface the guard's message instead of crashing.
        self.grant(["suggestion.accept"])
        unit = self.get_unit()
        verdict = self.make_verdict(unit)
        candidate = self.make_candidate(unit, verdict)
        response = self.edit_unit(
            unit.source, unit.target, accept=candidate.pk, follow=True
        )
        self.assertContains(response, "permission", status_code=200)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertNotEqual(self.get_unit().state, STATE_FUZZY)

    def test_vote_never_autoaccepts_a_judge_candidate(self) -> None:
        self.enable_review()
        self.component.suggestion_voting = True
        self.component.suggestion_autoaccept = 1
        self.component.save(
            update_fields=["suggestion_voting", "suggestion_autoaccept"]
        )
        unit = self.get_unit()
        verdict = self.make_verdict(unit)
        candidate = self.make_candidate(unit, verdict)
        request = self.get_request()
        request.user = self.user
        candidate.add_vote(request, Vote.POSITIVE)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertEqual(candidate.get_num_votes(override=True), 1)
        self.assertNotEqual(self.get_unit().state, STATE_FUZZY)
