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

from django.urls import reverse

from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, User
from weblate.checks.models import CHECKS
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
        group = Group.objects.create(
            name="Bulk only", language_selection=SELECTION_ALL
        )
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

    def test_project_language_scope_rejected(self) -> None:
        self._grant_full_access()
        url = f"/fix-check/double_space/-/{self.project.slug}/cs/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # -- safe tier ---------------------------------------------------

    def test_safe_tier_get_shows_eligible_count(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(self._url("double_space"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["check"].check_id, "double_space")
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

    def test_review_tier_post_applies_only_selected(self) -> None:
        self._grant_full_access()
        unit = self._fail_end_stop()
        response = self.client.post(
            self._url("end_stop"), {"unit_ids": [str(unit.pk)]}
        )
        self.assertRedirects(response, self.translation.get_absolute_url())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji.")

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
                    "name": "double_space",
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
                    "name": "double_space",
                    "path": self.translation.get_url_path(),
                },
            ),
        )

    def test_check_list_unscoped_page_has_no_fix_link(self) -> None:
        self._grant_full_access()
        self._fail_double_space()
        response = self.client.get(
            reverse("checks", kwargs={"name": "double_space"})
        )
        self.assertNotContains(response, "fix-check/double_space/")


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

        response = self.client.post(self._url(), {"unit_ids": [str(self.source_unit.pk)]})
        self.assertRedirects(response, self.component.get_absolute_url())

        self.source_unit.refresh_from_db()
        self.assertEqual(self.source_unit.target, "Wait…")
        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Wait…")
        self.assertEqual(sibling.state, expected_state)
