# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the translation editor forms and widgets."""

from __future__ import annotations

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.safestring import SafeString
from lxml import html

from weblate.lang.models import Language
from weblate.trans.forms import PluralTextarea, get_inherited_settings_label
from weblate.trans.tests.test_views import ViewTestCase


class FormRenderingTest(SimpleTestCase):
    def test_icon_help_text_escapes_title_attribute(self) -> None:
        # Safe help text must still be escaped when reused in an HTML attribute.
        field = SimpleNamespace(
            auto_id="id_fuzzy",
            field=SimpleNamespace(help_as_icon=True),
            help_text=SafeString(
                'Quote "Needs editing" and <strong>HTML help text</strong>.'
            ),
        )

        rendered = render_to_string(
            "bootstrap5/layout/help_text.html", {"field": field}
        )

        self.assertIn(
            'title="Quote &quot;Needs editing&quot; and '
            '&lt;strong&gt;HTML help text&lt;/strong&gt;."',
            rendered,
        )
        self.assertNotIn('title="Quote "Needs editing"', rendered)
        self.assertNotIn("<strong>HTML help text</strong>", rendered)


class InheritedSettingsLabelTest(SimpleTestCase):
    def test_inherited_settings_labels_are_complete_translatable_strings(self) -> None:
        self.assertEqual(
            get_inherited_settings_label("workspace"), "Inherit from workspace"
        )
        self.assertEqual(
            get_inherited_settings_label("project"), "Inherit from project"
        )
        self.assertEqual(
            get_inherited_settings_label("category"), "Inherit from category"
        )


class ToolbarCollapseTest(ViewTestCase):
    """Task 11: the special-characters toolbar collapses behind one button."""

    def test_toolbar_collapsed_by_default(self) -> None:
        unit = self.get_unit()
        response = self.client.get(unit.get_absolute_url())
        tree = html.fromstring(response.content)
        toggles = tree.xpath(
            '//div[contains(concat(" ", @class, " "), " editor-toolbar ")]'
            '/button[@data-bs-toggle="collapse"]'
        )
        self.assertEqual(len(toggles), 1)
        toggle = toggles[0]
        self.assertEqual(toggle.get("aria-expanded"), "false")
        target_id = toggle.get("data-bs-target").lstrip("#")
        self.assertIn(str(unit.checksum), target_id)
        self.assertEqual(toggle.get("aria-controls"), target_id)
        panels = tree.xpath(f'//div[@id="{target_id}"]')
        self.assertEqual(len(panels), 1)
        panel_classes = panels[0].get("class", "").split()
        self.assertIn("collapse", panel_classes)
        self.assertIn("btn-toolbar", panel_classes)
        specialchar_buttons = panels[0].xpath(
            './/*[contains(concat(" ", @class, " "), " specialchar ")]'
        )
        self.assertGreater(len(specialchar_buttons), 0)

    def test_specialchar_buttons_keep_their_data_value(self) -> None:
        """Existing specialchar/data-value contract is unaffected by the collapse."""
        unit = self.get_unit()
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, ' specialchar"')
        self.assertContains(response, "data-value=")


class RtlToolbarOutsideCollapseTest(ViewTestCase):
    """RTL editor controls change the editor, not the text: never collapsed."""

    def test_rtl_toolbar_outside_collapse(self) -> None:
        unit = self.get_unit()
        widget = PluralTextarea()
        widget.profile = self.user.profile
        markup = widget.get_toolbar(
            Language(code="ar", direction="rtl"), "field", unit, 0, unit.source
        )
        tree = html.fromstring(f"<div>{markup}</div>")
        collapsed = tree.xpath(
            '//div[contains(concat(" ", @class, " "), " collapse ")]'
        )
        self.assertEqual(len(collapsed), 1)
        outside = [
            button
            for button in tree.xpath(
                '//*[contains(concat(" ", @class, " "), " specialchar ")]'
            )
            if not button.xpath(
                'ancestor::div[contains(concat(" ", @class, " "), " collapse ")]'
            )
        ]
        # The RTL mark buttons stay reachable without opening the collapse.
        self.assertTrue(outside)
        self.assertTrue(
            collapsed[0].xpath(
                './/*[contains(concat(" ", @class, " "), " specialchar ")]'
            )
        )

    def test_rtl_toggle_absent_for_ltr_language(self) -> None:
        widget = PluralTextarea()
        rtl_html = str(
            widget.get_rtl_toggle(Language(code="en", direction="ltr"), "field")
        )
        self.assertEqual(rtl_html, "")
