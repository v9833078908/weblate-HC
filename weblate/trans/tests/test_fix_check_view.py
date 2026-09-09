# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Task 5 of the mass-fix-failing-checks plan.

Covers the `fix-check` view/URL (`weblate.trans.views.search.fix_check`):
scope resolution, permissions, tier rendering, apply flow, and the three
entry points' checklist/template contract.
"""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse

from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, User
from weblate.checks.models import CHECKS
from weblate.trans.fix_check import (
    FixCandidates,
    FixPreviewRow,
    dump_fix_check_cohort,
    load_fix_check_cohort,
)
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_TRANSLATED


class FixCheckViewTest(ViewTestCase):
    """Task 5: view permissions, tier rendering, and the apply flow."""

    def setUp(self) -> None:
        super().setUp()
        self.double_space_check = CHECKS["double_space"]
        self.end_stop_check = CHECKS["end_stop"]

    def _grant_full_access(self, user: User | None = None) -> None:
        user = user or self.user
        group = Group.objects.create(
            name="Fix check group", language_selection=SELECTION_ALL
        )
        group.roles.add(
            Role.objects.get(name="Bulk editing"), Role.objects.get(name="Translate")
        )
        group.components.add(self.component)
        user.groups.add(group)
        user.clear_permissions_cache()

    def _fail_double_space(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, "a  b\n", STATE_TRANSLATED)
        unit.refresh_from_db()

    def _fail_end_stop(self):
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Dekuji", STATE_TRANSLATED)
        unit.refresh_from_db()
        return unit

    def _url(self, name: str, path=None) -> str:
        return reverse(
            "fix-check",
            kwargs={"name": name, "path": path or self.translation.get_url_path()},
        )

    # -- permissions and URL contract -----------------------------------

    def test_reverse_has_trailing_slash(self) -> None:
        url = self._url("double_space")
        self.assertTrue(url.endswith("/"))
        self.assertIn("/fix-check/double_space/", url)

    def test_get_requires_login(self) -> None:
        self.client.logout()
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_get_denied_without_bulk_edit_permission(self) -> None:
        # The default "Users" group has unit.edit but not unit.bulk_edit.
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 403)

    def test_get_denied_with_only_bulk_edit_permission(self) -> None:
        limited = User.objects.create_user(
            "limited-bulk-only", "limited-bulk-only@example.com", "x"
        )
        limited.groups.clear()
        group = Group.objects.create(name="Bulk only", language_selection=SELECTION_ALL)
        group.roles.add(Role.objects.get(name="Bulk editing"))
        group.components.add(self.component)
        limited.groups.add(group)
        limited.clear_permissions_cache()
        self.client.login(username="limited-bulk-only", password="x")
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 403)

    def test_unknown_check_404(self) -> None:
        self._grant_full_access()
        response = self.client.get(self._url("does-not-exist-check"))
        self.assertEqual(response.status_code, 404)

    def test_untiered_check_404(self) -> None:
        self._grant_full_access()
        # "same" is never tiered (no deterministic fix).
        response = self.client.get(self._url("same"))
        self.assertEqual(response.status_code, 404)

    def test_glossary_component_404(self) -> None:
        self._grant_full_access()
        self.component.is_glossary = True
        self.component.save(update_fields=["is_glossary"])
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 404)

    def test_aggregate_and_unsupported_scopes_rejected(self) -> None:
        """Only Translation, Component and Project reach the view body."""
        self._grant_full_access()
        for label, path in (
            ("project-language", f"-/{self.project.slug}/cs/"),
            ("language", f"languages/cs/{self.project.slug}/"),
            ("workspace", "workspaces/1/"),
            ("category", f"{self.project.slug}/nonexistent-category/"),
        ):
            with self.subTest(scope=label):
                response = self.client.get(f"/fix-check/double_space/{path}")
                self.assertIn(response.status_code, {403, 404})

    # -- safe tier ---------------------------------------------------

    def test_safe_tier_get_shows_eligible_count(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["check"].id, "double_space")
        self.assertEqual(response.context["candidates"].total_eligible, 1)

    def test_safe_tier_post_applies_and_redirects(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.post(self._url("double_space"), {})
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit = self.get_unit()
        unit.refresh_from_db()
        self.assertEqual(unit.target, "a b\n")

    # -- review tier ---------------------------------------------------

    def test_review_tier_get_shows_preview_row(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.get(self._url("end_stop"))
        self.assertEqual(response.status_code, 200)
        candidates = response.context["candidates"]
        self.assertEqual(candidates.total_eligible, 1)
        self.assertEqual(len(candidates.shown), 1)
        self.assertEqual(candidates.shown[0].unit.pk, unit.pk)
        self.assertEqual(candidates.shown[0].final_target_value, "Dekuji.")

    def test_unfixable_direction_is_explained_on_the_screen(self) -> None:
        # A scope where every failure is the direction this feature does
        # not own must say so, instead of only reporting an unexplained
        # "needs manual review" with an empty table.
        self._grant_full_access()
        unit = self.get_unit(source="Hello, world!\n")
        unit.source = "Hello world"
        unit.save(update_fields=["source"])
        unit.translate(self.user, "Ahoj svete.", STATE_TRANSLATED)
        unit.refresh_from_db()

        response = self.client.get(self._url("end_stop"))
        self.assertEqual(response.context["candidates"].manual_no_fixup, 1)
        self.assertContains(response, "no fixup for this policy")
        self.assertContains(response, "No applicable strings to fix right now.")

        # The fixable direction never triggers the note.
        self._fail_end_stop()
        unit.translate(self.user, "Ahoj svete", STATE_TRANSLATED)
        response = self.client.get(self._url("end_stop"))
        self.assertEqual(response.context["candidates"].manual_no_fixup, 0)
        self.assertNotContains(response, "no fixup for this policy")

    def _cohort(self, units, name: str = "end_stop", scope_type: str = "translation"):
        """Sign a preview cohort the way the GET screen renders it."""
        scope_pk = {
            "translation": self.translation.pk,
            "component": self.component.pk,
            "project": self.project.pk,
        }[scope_type]
        return dump_fix_check_cohort(
            [unit.pk for unit in units],
            user_id=self.user.id,
            check_id=name,
            scope_type=scope_type,
            scope_pk=scope_pk,
        )

    def test_review_tier_post_applies_only_selected(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.post(
            self._url("end_stop"),
            {"unit_ids": [str(unit.pk)], "cohort": self._cohort([unit])},
        )
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji.")

    def test_review_tier_get_renders_the_signed_cohort(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.get(self._url("end_stop"))
        content = response.content.decode()
        self.assertIn('name="cohort"', content)
        self.assertIn('type="hidden"', content)
        # The rendered cohort verifies for this actor, check and scope and
        # carries exactly the previewed row.
        cohort = response.context["form"].initial["cohort"]
        self.assertEqual(
            load_fix_check_cohort(
                cohort,
                user_id=self.user.id,
                check_id="end_stop",
                scope_type="translation",
                scope_pk=self.translation.pk,
            ),
            {unit.pk},
        )

    def test_review_tier_post_refuses_an_id_outside_the_cohort(self) -> None:
        """A crafted submit cannot repair a row that was never previewed."""
        self._grant_full_access()
        unit = self._fail_end_stop()
        other = self.get_unit(source="Hello, world!\n")
        other.translate(self.user, "Ahoj svete", STATE_TRANSLATED)
        other.refresh_from_db()
        untouched_target = other.target
        response = self.client.post(
            self._url("end_stop"),
            # The cohort covers only `unit`, the submit asks for `other`.
            {"unit_ids": [str(other.pk)], "cohort": self._cohort([unit])},
        )
        self.assertRedirects(response, self.translation.get_absolute_url())
        other.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(other.target, untouched_target)
        self.assertEqual(unit.target, "Dekuji")

    def test_review_tier_post_refuses_a_tampered_or_missing_cohort(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        for cohort in ("", "not-a-signed-payload", f"{self._cohort([unit])}x"):
            with self.subTest(cohort=cohort):
                response = self.client.post(
                    self._url("end_stop"),
                    {"unit_ids": [str(unit.pk)], "cohort": cohort},
                )
                self.assertRedirects(response, self.translation.get_absolute_url())
                unit.refresh_from_db()
                self.assertEqual(unit.target, "Dekuji")

    def test_review_tier_post_refuses_a_foreign_scope_cohort(self) -> None:
        """A cohort signed for another scope does not verify here."""
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.post(
            self._url("end_stop"),
            {
                "unit_ids": [str(unit.pk)],
                "cohort": self._cohort([unit], scope_type="component"),
            },
        )
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji")

    def test_review_tier_post_without_selection_is_noop(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.post(self._url("end_stop"), {})
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji")

    # -- scopes ----------------------------------------------------------

    def test_component_scope_applies_across_translations(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        url = self._url("double_space", path=self.component.get_url_path())
        response = self.client.post(url, {})
        self.assertRedirects(response, self.component.get_absolute_url())
        unit = self.get_unit()
        unit.refresh_from_db()
        self.assertEqual(unit.target, "a b\n")

    def test_project_scope_applies_project_wide(self) -> None:
        # A project-scoped `unit.bulk_edit` check needs a group scoped via
        # `group.projects`, not `group.components`.
        group = Group.objects.create(
            name="Project fix check group", language_selection=SELECTION_ALL
        )
        group.roles.add(
            Role.objects.get(name="Bulk editing"), Role.objects.get(name="Translate")
        )
        group.projects.add(self.project)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()
        self._fail_double_space()
        url = self._url("double_space", path=self.project.get_url_path())
        response = self.client.post(url, {})
        self.assertRedirects(response, self.project.get_absolute_url())
        unit = self.get_unit()
        unit.refresh_from_db()
        self.assertEqual(unit.target, "a b\n")

    # -- checklist / entry points -----------------------------------

    def test_checklist_item_carries_check_id_and_mass_fixup(self) -> None:
        self._fail_double_space()
        items = {
            item.check_id: item
            for item in self.translation.list_translation_checks
            if item.check_id is not None
        }
        self.assertIn("double_space", items)
        self.assertEqual(items["double_space"].mass_fixup, "safe")

    def test_translation_status_table_renders_fix_link(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(self.translation.get_absolute_url())
        self.assertContains(
            response,
            reverse(
                "fix-check",
                kwargs={
                    "name": "double-space",
                    "path": self.translation.get_url_path(),
                },
            ),
        )

    def test_check_list_component_scope_renders_fix_link(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(
            reverse(
                "checks",
                kwargs={
                    "name": "double_space",
                    "path": self.component.get_url_path(),
                },
            )
        )
        self.assertContains(
            response,
            reverse(
                "fix-check",
                kwargs={
                    "name": "double-space",
                    "path": self.translation.get_url_path(),
                },
            ),
        )

    def test_check_list_unscoped_page_has_no_fix_link(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(reverse("checks", kwargs={"name": "double_space"}))
        self.assertNotContains(response, "fix-check/double-space/")

    # -- rendered contract -------------------------------------------

    def test_confirmation_form_is_not_nested(self) -> None:
        """A nested form would push the submit button outside any form."""
        self._grant_full_access()
        self._fail_double_space()
        self._fail_end_stop()
        for name in ("double_space", "end_stop"):
            with self.subTest(check=name):
                content = self.client.get(self._url(name)).content.decode()
                # The page's own confirmation form starts after the check
                # heading; crispy must not open a second one inside it
                # (AGENTS.md: `form_tag = False` when the template owns
                # the form).
                region = content[content.index("<h2>") :]
                opening = region.index("<form")
                closing = region.index("</form>", opening)
                self.assertNotIn("<form", region[opening + len("<form") : closing])
                self.assertIn("csrfmiddlewaretoken", region[opening:closing])
                self.assertIn('<button type="submit"', region[opening:closing])

    def test_review_screen_markup_supports_selection_and_sticky_column(
        self,
    ) -> None:
        self._grant_full_access()
        self._fail_end_stop()
        response = self.client.get(self._url("end_stop"))
        content = response.content.decode()
        # The select-all control and the row class the bootstrap script
        # binds to (`loader-bootstrap.js`), and the `actions` class the
        # `.table-scroll` CSS needs to keep the first column sticky.
        self.assertIn('id="fix-check-select-all"', content)
        self.assertIn('class="fix-check-row"', content)
        self.assertIn('<td class="actions">', content)
        self.assertIn('class="table-scroll"', content)
        # Rows start unchecked.
        self.assertNotIn('class="fix-check-row" checked', content)

    def test_heading_names_the_check(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(self._url("double_space"))
        self.assertContains(response, "<h2>")
        self.assertContains(response, str(self.double_space_check.name))

    def test_manual_bucket_is_counted_and_offers_browse(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        # A target that already ends with a conflicting terminal mark
        # cannot be repaired by adding the source's mark: `manual`.
        unit.translate(self.user, "Dekuji?", STATE_TRANSLATED)
        unit.refresh_from_db()
        response = self.client.get(self._url("end_stop"))
        self.assertEqual(response.context["candidates"].manual, 1)
        self.assertEqual(response.context["candidates"].total_eligible, 0)
        self.assertContains(response, f"?q={self.end_stop_check.url_id}")

    def test_judge_counter_is_rendered(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        JudgeVerdict.objects.create(
            unit=unit,
            judge_model="test-model",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="ctx",
        )
        response = self.client.get(self._url("end_stop"))
        self.assertEqual(response.context["candidates"].verdicts_no_longer_current, 1)
        self.assertContains(response, "no longer be current")

    def test_review_notice_reports_the_batch_boundary(self) -> None:
        """`remaining > 0` renders the first-N-of-M notice (Task 5 step 4)."""
        self._grant_full_access()
        unit = self._fail_end_stop()
        truncated = FixCandidates(
            shown=[FixPreviewRow(unit=unit, final_target=["Dekuji."])],
            total_eligible=300,
        )
        self.assertEqual(truncated.remaining, 299)
        with patch(
            "weblate.trans.fix_check.collect_fix_candidates",
            return_value=truncated,
        ):
            response = self.client.get(self._url("end_stop"))
        self.assertContains(response, "Showing the first")
        self.assertContains(response, "300")


class FixCheckSourceTemplateViewTest(ViewTestCase):
    """A source-check (`ellipsis`) mass-fix over a component, end to end."""

    def create_component(self):
        return self.create_po_mono()

    def setUp(self) -> None:
        super().setUp()
        group = Group.objects.create(
            name="Fix check source group", language_selection=SELECTION_ALL
        )
        group.roles.add(
            Role.objects.get(name="Bulk editing"), Role.objects.get(name="Edit source")
        )
        group.components.add(self.component)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

        self.source_translation = self.component.source_translation
        self.source_unit = self.get_unit(source="Hello, world!\n", language="en")
        self.source_unit.translate(self.user, "Wait...", STATE_TRANSLATED)
        self.source_unit.refresh_from_db()

    def _url(self) -> str:
        return reverse(
            "fix-check",
            kwargs={"name": "ellipsis", "path": self.component.get_url_path()},
        )

    def test_review_tier_get_shows_ellipsis_candidate(self) -> None:
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        candidates = response.context["candidates"]
        self.assertEqual(candidates.total_eligible, 1)
        self.assertEqual(candidates.shown[0].final_target_value, "Wait…")

    def test_review_tier_post_fixes_source_without_fuzzing_siblings(self) -> None:
        sibling = self.get_unit("Wait...", "cs")
        sibling.translate(self.user, "Čekejte...", STATE_TRANSLATED)
        sibling.refresh_from_db()
        expected_state = sibling.state

        response = self.client.post(
            self._url(), {"unit_ids": [str(self.source_unit.pk)]}
        )
        self.assertRedirects(response, self.component.get_absolute_url())

        self.source_unit.refresh_from_db()
        self.assertEqual(self.source_unit.target, "Wait…")
        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Wait…")
        self.assertEqual(sibling.state, expected_state)


class ExplicitPolicyViewTest(ViewTestCase):
    """
    End-to-end `fix_check` coverage for the `explicit` tier (Task C/D).

    `terminal-source` and one mechanical group, the all/page choice, and
    the edge-space cross-link.
    """

    def _grant_full_access(self, user: User | None = None) -> None:
        user = user or self.user
        group = Group.objects.create(
            name="Explicit policy group", language_selection=SELECTION_ALL
        )
        group.roles.add(
            Role.objects.get(name="Bulk editing"), Role.objects.get(name="Translate")
        )
        group.components.add(self.component)
        user.groups.add(group)
        user.clear_permissions_cache()

    def _url(self, name: str, path=None) -> str:
        return reverse(
            "fix-check",
            kwargs={"name": name, "path": path or self.translation.get_url_path()},
        )

    def test_terminal_source_get_shows_subtotals_and_all_button(self) -> None:
        self._grant_full_access()
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        unit.refresh_from_db()
        response = self.client.get(self._url("terminal-source"))
        self.assertEqual(response.status_code, 200)
        candidates = response.context["candidates"]
        self.assertEqual(candidates.total_eligible, 1)
        self.assertEqual(candidates.by_operation["replace"], 1)
        content = response.content.decode()
        self.assertIn("mismatched mark replaced", content)
        self.assertIn('value="all"', content)
        self.assertIn("Apply to all 1 matching string", content)

    def test_terminal_source_post_selection_all_applies_full_scope(self) -> None:
        self._grant_full_access()
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        unit.refresh_from_db()
        response = self.client.post(self._url("terminal-source"), {"selection": "all"})
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Diky.")

    def test_terminal_source_post_selection_page_applies_only_cohort(self) -> None:
        self._grant_full_access()
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        unit.refresh_from_db()
        cohort = dump_fix_check_cohort(
            [unit.pk],
            user_id=self.user.id,
            check_id="terminal-source",
            scope_type="translation",
            scope_pk=self.translation.pk,
        )
        response = self.client.post(
            self._url("terminal-source"),
            {"selection": "page", "unit_ids": [str(unit.pk)], "cohort": cohort},
        )
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Diky.")

    def test_explicit_tier_post_without_selection_is_rejected(self) -> None:
        self._grant_full_access()
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        unit.refresh_from_db()
        response = self.client.post(self._url("terminal-source"), {})
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        # Neither selection was recognised, so nothing was queued/applied.
        self.assertEqual(unit.target, "Diky!")

    def test_mechanical_group_post_selection_all_applies_full_scope(self) -> None:
        self._grant_full_access()
        unit = self.get_unit(source="Hello, world!\n")
        unit.translate(self.user, "Ahoj  svete!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        response = self.client.post(self._url("double-space"), {"selection": "all"})
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Ahoj svete!\n")

    def test_unavailable_mechanical_group_is_404(self) -> None:
        self._grant_full_access()
        response = self.client.get(self._url("line-separator-spacing"))
        self.assertEqual(response.status_code, 404)

    def test_edge_space_policies_cross_link_each_other(self) -> None:
        self._grant_full_access()
        remove_response = self.client.get(self._url("edge-space-remove"))
        self.assertContains(
            remove_response, self._url("edge-space-source"), status_code=200
        )
        source_response = self.client.get(self._url("edge-space-source"))
        self.assertContains(
            source_response, self._url("edge-space-remove"), status_code=200
        )
