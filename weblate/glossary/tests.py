# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test for glossary manipulations."""

from __future__ import annotations

import csv
import json
import tempfile
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.urls import reverse
from lxml import etree

from weblate.glossary.models import (
    fetch_glossary_terms,
    get_glossary_terms,
    get_glossary_tsv,
    glossary_matcher_fingerprint,
)
from weblate.glossary.tasks import (
    cleanup_stale_glossaries,
    get_stale_glossary_translations,
    sync_terminology,
)
from weblate.lang.models import Language
from weblate.trans.alerts.registry import update_alerts
from weblate.trans.models import PendingUnitChange, Project, Unit, Variant
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import get_test_file
from weblate.utils.hash import calculate_hash
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.state import STATE_EMPTY, STATE_READONLY, STATE_TRANSLATED
from weblate.utils.xml import PARSER

if TYPE_CHECKING:
    from weblate.trans.models import Translation

TEST_TBX = get_test_file("terms.tbx")
TEST_CSV = get_test_file("terms.csv")
TEST_CSV_HEADER = get_test_file("terms-header.csv")
TEST_PO = get_test_file("terms.po")

LONG = """

<div><b>Game Settings</b> can be found by pressing your device's
Menu Button.</div>

<p>________________</p>
<h1>Interface Icons</h1>

<div><b>The Chest</b><img alt=chest src=chest.png /></div>
<p>Quickslots [Long press the pouches inside to assign items for instant
use]</p>

<div><b>The Hero</b><img alt=hero src=char_hero.png /></div>
<p>Menu [Overview, Quests, Skills &amp; Inventory *]</p>
<p>* (While in inventory, press an item for information &amp; long press for
more options)</p>

<div><b>The Enemy</b><img alt=monster src=monster.png /></div>
<p>Information [Appears during Combat]</p>



<p>________________</p>
<h1>Combat</h1>

<p>Actions taken during battle cost AP...</p>

<div><b>Attacking</b> - [3AP] *</div>
<img alt=attacking src=doubleattackexample.png />
<p>* (Equipping Gear &amp; Using Items may alter AP &amp; usage cost)</p>

<div><b>Using Items</b> - [5AP]</div>
<div><b>Fleeing</b> - [6AP]</div>



<p>________________</p>
<h1>Advanced Combat</h1>

<div>During Combat, long press a tile adjacent to the Hero...</div>

<div><b>To Flee</b></div>
<p>(chosen tile is highlighted - Attack Button changes to Move)</p>
<img alt=flee src=flee_example.png />
<p>[flee mode activated - Long press enemy to re-enter combat]</p>

<div><b>To Change Targets</b></div>
<p>(the red target highlight shifts between enemies)</p>
<p>[the target has been changed]</p>

"""


def unit_sources_and_positions(units):
    return {(unit.source, unit.glossary_positions) for unit in units}


def tbx_source_contexts(filename: str) -> list[tuple[str, str | None]]:
    """Return stored TBX source terms grouped by termEntry id."""
    root = etree.parse(filename, PARSER).getroot()
    xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"
    result = []
    for term_entry in root.findall(".//termEntry"):
        context = term_entry.get("id") or ""
        source = None
        for lang_set in term_entry.findall("langSet"):
            if lang_set.get(xml_lang) != "en":
                continue
            term = lang_set.find(".//term")
            if term is not None:
                source = term.text
                break
        result.append((context, source))
    return result


def duplicate_tbx_source_term(filename: str, source: str) -> None:
    tree = etree.parse(filename, PARSER)
    root = tree.getroot()
    body = root.find("./text/body")
    if body is None:
        msg = "TBX body is missing"
        raise AssertionError(msg)
    xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"
    snippet = None
    for term_entry in body.findall("termEntry"):
        for lang_set in term_entry.findall("langSet"):
            term = lang_set.find(".//term")
            if (
                lang_set.get(xml_lang) == "en"
                and term is not None
                and term.text == source
            ):
                snippet = term_entry
                break
        if snippet is not None:
            break
    if snippet is None:
        msg = f"{source} term is missing"
        raise AssertionError(msg)
    for _unused in range(2):
        body.insert(0, deepcopy(snippet))
    tree.write(filename, encoding="utf-8", xml_declaration=True)


class GlossaryTest(ViewTestCase):
    """Testing of glossary manipulations."""

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]
        self.glossary = self.glossary_component.translation_set.get(
            language=self.get_translation().language
        )

    def import_file(self, filename, **kwargs):
        with open(filename, "rb") as handle:
            params = {"file": handle, "method": "add"}
            params.update(kwargs)
            return self.client.post(
                reverse("upload", kwargs={"path": self.glossary.get_url_path()}),
                params,
            )

    def add_term(self, source, target, context="") -> None:
        id_hash = calculate_hash(source, context)
        source_unit = self.glossary_component.source_translation.unit_set.create(
            source=source,
            target=source,
            context=context,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        self.glossary.unit_set.create(
            source=source,
            target=target,
            context=context,
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        self.glossary.invalidate_cache()

    def make_glossary_language_stale(
        self, language_code: str, source: str | None = None
    ) -> Translation:
        language = Language.objects.get(code=language_code)
        self.component.translation_set.filter(language=language).delete()
        glossary = self.glossary_component.translation_set.get(language=language)
        if source is not None:
            with self.captureOnCommitCallbacks(execute=True):
                glossary.add_unit(None, "", source, source, author=self.user)
        return glossary

    def assert_unused_glossary_language_alert(self, *language_codes: str) -> None:
        if not language_codes:
            self.assertFalse(
                self.glossary_component.alert_set.filter(
                    name="UnusedGlossaryLanguage"
                ).exists()
            )
            return

        alert = self.glossary_component.alert_set.get(name="UnusedGlossaryLanguage")
        self.assertEqual(
            {
                occurrence["language_code"]
                for occurrence in alert.details["occurrences"]
            },
            set(language_codes),
        )

    def test_import(self) -> None:
        """Test for importing of TBX into glossary."""

        def change_term() -> None:
            term = self.glossary.unit_set.get(target="podpůrná vrstva")
            term.target = "zkouška sirén"
            term.save()

        show_url = self.glossary.get_absolute_url()

        # Import file
        response = self.import_file(TEST_TBX)

        # Check correct response
        self.assertRedirects(response, show_url)

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 164)

        # Change single term
        change_term()

        # Import file again with orverwriting
        self.import_file(TEST_TBX, method="translate", conflicts="replace-translated")

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 164)
        self.assertTrue(
            self.glossary.unit_set.filter(target="podpůrná vrstva").exists()
        )

        # Change single term
        change_term()

        # Import file again with adding
        self.import_file(TEST_TBX)

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 164)

        self.assertFalse(
            self.glossary.unit_set.filter(target="podpůrná vrstva").exists()
        )

    def test_import_csv(self) -> None:
        # Import file
        response = self.import_file(TEST_CSV)

        # Check correct response
        self.assertRedirects(response, self.glossary.get_absolute_url())

        self.client.get(self.glossary.get_absolute_url())

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 163)

    def test_import_csv_header(self) -> None:
        # Import file
        response = self.import_file(TEST_CSV_HEADER)

        # Check correct response
        self.assertRedirects(response, self.glossary.get_absolute_url())

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 163)

    def test_import_po(self) -> None:
        # Import file
        response = self.import_file(TEST_PO)

        # Check correct response
        self.assertRedirects(response, self.glossary.get_absolute_url())

        # Check number of imported objects
        self.assertEqual(self.glossary.unit_set.count(), 164)

    def test_get_terms(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("hello", "ahoj")
            self.add_term("thank", "děkujeme")

        unit = self.get_unit("Thank you for using Weblate.")
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)), {("thank", ((0, 5),))}
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("thank", "díky", "other")
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)), {("thank", ((0, 5),))}
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("thank you", "děkujeme vám")
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {
                ("thank", ((0, 5),)),
                ("thank you", ((0, 9),)),
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term(
                "thank you for using Weblate", "děkujeme vám za použití Weblate"
            )
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {
                ("thank", ((0, 5),)),
                ("thank you", ((0, 9),)),
                ("thank you for using Weblate", ((0, 27),)),
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("web", "web")
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {
                ("thank", ((0, 5),)),
                ("thank you", ((0, 9),)),
                ("thank you for using Weblate", ((0, 27),)),
            },
        )

    def test_substrings(self) -> None:
        self.add_term("reach", "dojet")
        self.add_term("breach", "prolomit")
        unit = self.get_unit()
        unit.source = "Reach summit"
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)), {("reach", ((0, 5),))}
        )

    def test_phrases(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("Destructive Breach", "x")
            self.add_term("Flame Breach", "x")
            self.add_term("Frost Breach", "x")
            self.add_term("Icereach", "x")
            self.add_term("Reach", "x")
            self.add_term("Reachable", "x")
            self.add_term("Skyreach", "x")
        unit = self.get_unit()
        unit.source = "During invasion from the Reach. Town burn, prior records lost.\n"
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {("Reach", ((25, 30),))},
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("Town", "x")
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {
                ("Town", ((32, 36),)),
                ("Reach", ((25, 30),)),
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.add_term("The Reach", "x")
        unit.glossary_terms = None
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {("Town", ((32, 36),)), ("Reach", ((25, 30),)), ("The Reach", ((21, 30),))},
        )

    def get_long_unit(self):
        unit = self.get_unit()
        unit.source = LONG
        unit.save()
        return unit

    def test_get_long(self) -> None:
        """Test parsing long source string."""
        unit = self.get_long_unit()
        self.assertEqual(unit_sources_and_positions(get_glossary_terms(unit)), set())

    def test_stoplist(self) -> None:
        unit = self.get_long_unit()
        self.add_term("the blue", "modrý")
        self.add_term("the red", "červený")
        unit.glossary_terms = None

        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {("the red", ((1287, 1294),))},
        )

    def test_get_dash(self) -> None:
        unit = self.get_unit("Thank you for using Weblate.")
        unit.source = "Nordrhein-Westfalen"
        self.add_term("Nordrhein-Westfalen", "Northrhine Westfalia")
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {("Nordrhein-Westfalen", ((0, 19),))},
        )

    def test_get_single(self) -> None:
        unit = self.get_unit("Thank you for using Weblate.")
        unit.source = "thank"
        self.add_term("thank", "díky")
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)), {("thank", ((0, 5),))}
        )

    def test_get_newline(self) -> None:
        unit = self.get_unit("Thank you for using Weblate.")
        unit.source = "Thank you for using Weblate.\nThank you again."
        self.add_term("thank", "díky")
        self.assertEqual(
            unit_sources_and_positions(get_glossary_terms(unit)),
            {("thank", ((0, 5), (29, 34)))},
        )

    def do_add_unit(
        self, language: str = "cs", expected_status: int = 200, **kwargs
    ) -> None:
        unit = self.get_unit("Thank you for using Weblate.", language=language)
        glossary = self.glossary_component.translation_set.get(
            language=unit.translation.language
        )
        # Add term
        response = self.client.post(
            reverse("js-add-glossary", kwargs={"unit_id": unit.pk}),
            {
                "context": "context",
                "source_0": "source",
                "target_0": "překlad",
                "translation": glossary.pk,
                "auto_context": 1,
                **kwargs,
            },
        )
        content = response.json()
        self.assertEqual(content["responseCode"], expected_status)

    def test_add(self) -> None:
        """Test for adding term from translate page."""
        start = Unit.objects.count()
        self.do_add_unit()
        # Should be added to the source and translation only
        self.assertEqual(Unit.objects.count(), start + 2)

    def test_add_existing(self) -> None:
        """Test for adding term from translate page while there is existing one."""
        glossary = self.glossary_component.translation_set.get(
            language=self.translation.language
        )
        glossary.add_unit(None, "", "Thank", "Díky", author=self.user)
        start = Unit.objects.count()
        self.do_add_unit()
        # Should be added to the source and translation only
        self.assertEqual(Unit.objects.count(), start + 2)

    def test_add_terminology(self) -> None:
        start = Unit.objects.count()
        self.do_add_unit(expected_status=403, terminology=1)
        self.make_manager()
        self.do_add_unit(terminology=1)
        # Should be added to all languages
        self.assertEqual(Unit.objects.count(), start + 4)

    def test_add_untranslatable(self) -> None:
        start = Unit.objects.count()
        self.do_add_unit(read_only=1)
        # Should be added to all languages
        self.assertEqual(Unit.objects.count(), start + 2)
        unit = Unit.objects.get(source="source", translation__language__code="cs")
        self.assertEqual(unit.state, STATE_READONLY)
        self.assertEqual(unit.target, "")

    def test_add_terminology_existing(self) -> None:
        self.make_manager()
        start = Unit.objects.count()
        # Add unit to other translation
        self.do_add_unit(language="it")
        # Add terminology to translation where unit does not exist
        self.do_add_unit(terminology=1)
        # Should be added to all languages
        self.assertEqual(Unit.objects.count(), start + 4)

    def test_add_duplicate(self) -> None:
        self.do_add_unit()
        self.do_add_unit()

    def test_managed_tbx_remove_duplicate_terms_operation(self) -> None:
        self.make_manager()
        self.do_add_unit(
            language="it",
            context="",
            source_0="Snippet",
            target_0="Snippet",
            terminology=1,
        )
        self.glossary_component.commit_pending("test", None)

        filename = self.glossary.get_filename()
        if filename is None:
            self.fail("Glossary translation file is missing")
        other_glossary = self.glossary_component.translation_set.get(
            language__code="it"
        )
        other_filename = other_glossary.get_filename()
        if other_filename is None:
            self.fail("Italian glossary translation file is missing")

        duplicate_tbx_source_term(filename, "Snippet")
        duplicate_tbx_source_term(other_filename, "Snippet")

        self.glossary_component.create_translations_immediate(
            force=True, request=self.get_request()
        )
        alert = self.glossary_component.alert_set.get(name="DuplicateString")
        rendered = alert.render(self.user)
        cleanup_url = reverse(
            "remove_duplicate_units", kwargs={"path": self.glossary.get_url_path()}
        )
        repository_response = self.client.get(
            reverse("git_status", kwargs={"path": self.glossary.get_url_path()})
        )
        self.assertContains(repository_response, "File management")
        self.assertContains(repository_response, cleanup_url)
        self.assertIn(cleanup_url, rendered)

        unit = self.glossary.unit_set.get(source="Snippet")
        self.assertTrue(unit.translate(self.user, "Úryvek", STATE_TRANSLATED))
        self.assertEqual(
            PendingUnitChange.objects.for_translation(
                self.glossary, apply_filters=False
            ).count(),
            1,
        )
        pending = PendingUnitChange.objects.for_translation(
            self.glossary, apply_filters=False
        ).get()
        self.assertEqual(pending.target, "Úryvek")
        self.assertEqual(
            PendingUnitChange.objects.for_translation(
                self.glossary, apply_filters=True
            ).count(),
            1,
        )
        self.do_add_unit(
            context="",
            source_0="Pending snippet",
            target_0="Čekající",
            terminology=1,
        )
        pending_add_unit = self.glossary.unit_set.get(source="Pending snippet")
        self.assertTrue(
            PendingUnitChange.objects.filter(
                unit=pending_add_unit, add_unit=True
            ).exists()
        )
        duplicate_language_occurrence = {
            "language_code": self.glossary.language.code,
            "codes": f"{self.glossary.language_code}, duplicate",
            "filenames": f"{self.glossary.filename}, duplicate.tbx",
        }
        self.glossary_component.add_alert(
            "DuplicateLanguage", occurrences=[duplicate_language_occurrence]
        )

        response = self.client.post(cleanup_url)
        self.assertRedirects(response, f"{self.glossary.get_absolute_url()}#repository")
        alert = self.glossary_component.alert_set.get(name="DuplicateString")
        self.assertEqual(
            {
                occurrence["language_code"]
                for occurrence in alert.details["occurrences"]
            },
            {other_glossary.language.code},
        )
        duplicate_language_alert = self.glossary_component.alert_set.get(
            name="DuplicateLanguage"
        )
        self.assertEqual(
            duplicate_language_alert.details["occurrences"],
            [duplicate_language_occurrence],
        )
        self.assertEqual(
            PendingUnitChange.objects.for_translation(
                self.glossary, apply_filters=False
            ).count(),
            2,
        )
        self.assertTrue(Unit.objects.filter(pk=pending_add_unit.pk).exists())
        self.assertTrue(
            PendingUnitChange.objects.filter(
                unit=pending_add_unit, add_unit=True
            ).exists()
        )
        self.assertEqual(
            [
                context
                for context, source in tbx_source_contexts(filename)
                if source == "Snippet"
            ],
            [""],
        )

        self.glossary_component.commit_pending("test", self.user)
        self.assertEqual(
            PendingUnitChange.objects.for_translation(
                self.glossary, apply_filters=False
            ).count(),
            0,
        )

        self.assertEqual(
            [
                context
                for context, source in tbx_source_contexts(filename)
                if source == "Snippet"
            ],
            [""],
        )

        file_content = Path(filename).read_text(encoding="utf-8")
        self.assertEqual(file_content.count("<term>Snippet</term>"), 1)
        self.assertEqual(file_content.count("<term>Úryvek</term>"), 1, file_content)
        self.assertEqual(file_content.count("<term>Pending snippet</term>"), 1)
        self.assertEqual(file_content.count("<term>Čekající</term>"), 1, file_content)

    def test_file_cleanup_skips_alert_refresh_without_reparse(self) -> None:
        store = self.glossary.store
        with (
            patch.object(store, "remove_duplicate_units", return_value=[]),
            patch.object(store, "save"),
            patch.object(self.glossary, "git_commit", return_value=True),
            patch.object(self.glossary, "handle_store_change", return_value=False),
            patch.object(self.glossary, "update_single_file_import_alerts") as update,
        ):
            self.assertTrue(self.glossary.do_remove_duplicate_units(self.get_request()))
        update.assert_not_called()

    def test_file_cleanup_keeps_parse_alert_after_failed_reparse(self) -> None:
        store = self.glossary.store
        occurrence = {
            "language_code": self.glossary.language.code,
            "error": "Broken file",
            "filename": self.glossary.filename,
        }
        self.glossary_component.alerts_trigger = {"ParseError": [occurrence]}
        with (
            patch.object(store, "remove_duplicate_units", return_value=[]),
            patch.object(store, "save"),
            patch.object(self.glossary, "git_commit", return_value=True),
            patch.object(self.glossary, "handle_store_change", return_value=False),
        ):
            self.assertTrue(self.glossary.do_remove_duplicate_units(self.get_request()))

        alert = self.glossary_component.alert_set.get(name="ParseError")
        self.assertEqual(alert.details["occurrences"], [occurrence])

    def test_duplicate_pending_add_entries_commit_once(self) -> None:
        self.do_add_unit(context="")
        pending = PendingUnitChange.objects.get(
            unit__translation=self.glossary, unit__source="source"
        )
        self.assertEqual(pending.unit.context, "")

        PendingUnitChange.objects.create(
            unit=pending.unit,
            author=pending.author,
            target=pending.target,
            explanation=pending.explanation,
            source_unit_explanation=pending.source_unit_explanation,
            state=pending.state,
            add_unit=True,
        )

        self.glossary_component.commit_pending("test", None)

        filename = self.glossary.get_filename()
        if filename is None:
            self.fail("Glossary translation file is missing")
        file_content = Path(filename).read_text(encoding="utf-8")
        self.assertEqual(file_content.count("<term>source</term>"), 1)
        self.assertEqual(file_content.count("<term>překlad</term>"), 1)

    def test_pending_add_replay_commits_once(self) -> None:
        self.do_add_unit(context="")
        pending = PendingUnitChange.objects.get(
            unit__translation=self.glossary, unit__source="source"
        )
        self.assertEqual(pending.unit.context, "")

        # Simulate a retry after the pending add was already written to the file,
        # but the pending row was not deleted yet.
        self.glossary.update_units(
            [pending], self.glossary.store, self.user.get_author_name()
        )
        self.glossary.drop_store_cache()

        self.glossary_component.commit_pending("test", None)

        filename = self.glossary.get_filename()
        if filename is None:
            self.fail("Glossary translation file is missing")
        file_content = Path(filename).read_text(encoding="utf-8")
        self.assertEqual(file_content.count("<term>source</term>"), 1)
        self.assertEqual(file_content.count("<term>překlad</term>"), 1)

    def test_add_locked(self) -> None:
        unit = self.get_unit("Thank you for using Weblate.")
        with patch(
            "weblate.trans.models.translation.Translation.add_unit",
            side_effect=WeblateLockTimeoutError("locked", lock=self.component.lock),
        ):
            response = self.client.post(
                reverse("js-add-glossary", kwargs={"unit_id": unit.pk}),
                {
                    "context": "context",
                    "source_0": "source",
                    "target_0": "překlad",
                    "translation": self.glossary.pk,
                    "auto_context": 1,
                },
            )

        content = response.json()
        self.assertEqual(content["responseCode"], 423)
        self.assertIn("another background operation", content["responseDetails"])

    def test_add_result_escapes_html(self) -> None:
        unit = self.get_unit("Thank you for using Weblate.")
        response = self.client.post(
            reverse("js-add-glossary", kwargs={"unit_id": unit.pk}),
            {
                "context": "context",
                "source_0": "<script>alert(1)</script>",
                "target_0": '<img/src=x/onerror=1>"x="y',
                "translation": self.glossary.pk,
                "auto_context": 1,
            },
        )
        content = response.json()
        self.assertEqual(content["responseCode"], 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content["results"])
        self.assertIn("&lt;img/src=x/onerror=1&gt;&quot;x=&quot;y", content["results"])
        self.assertNotIn("<script>", content["results"])
        self.assertNotIn('<img/src=x/onerror=1>"x="y', content["results"])

    def test_terminology(self) -> None:
        start = Unit.objects.count()

        # Add single term
        with self.captureOnCommitCallbacks(execute=True):
            self.do_add_unit()

        # Verify it has been added to single language (+ source)
        unit = self.glossary_component.source_translation.unit_set.get(source="source")
        self.assertEqual(Unit.objects.count(), start + 2)
        self.assertEqual(unit.unit_set.count(), 2)

        # Enable language consistency
        self.assertEqual(unit.unit_set.count(), 2)
        self.assertEqual(Unit.objects.count(), start + 2)

        # Make it terminology
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            unit.translation.component.unload_sources()
            unit.extra_flags = "terminology"
            unit.save()

        # Verify it has been added to all languages
        self.assertEqual(Unit.objects.count(), start + 4)
        self.assertEqual(unit.unit_set.count(), 4)

        # Verify stats have been updated
        glossary_component = type(self.glossary_component).objects.get(
            pk=self.glossary_component.pk
        )
        with self.captureOnCommitCallbacks(execute=True):
            glossary_component.invalidate_cache()
        translation = glossary_component.translation_set.get(language_code="de")
        self.assertEqual(translation.stats.all, translation.unit_set.count())

        # Terminology sync should be no-op now
        sync_terminology(unit.translation.component.id, unit.translation.component)
        self.assertEqual(Unit.objects.count(), start + 4)
        self.assertEqual(unit.unit_set.count(), 4)

    def test_terminology_explanation_sync(self) -> None:
        self.make_manager()
        unit = self.get_unit("Thank you for using Weblate.")
        # Add terms
        response = self.client.post(
            reverse("js-add-glossary", kwargs={"unit_id": unit.pk}),
            {
                "source_0": "source 1",
                "target_0": "target 1",
                "translation": self.glossary.pk,
                "explanation": "explained 1",
                "terminology": "1",
                "auto_context": 1,
            },
        )
        content = json.loads(response.content.decode())
        self.assertEqual(content["responseCode"], 200)

        response = self.client.post(
            reverse("js-add-glossary", kwargs={"unit_id": unit.pk}),
            {
                "source_0": "source 2",
                "target_0": "target 2",
                "translation": self.glossary.pk,
                "explanation": "explained 2",
                "terminology": "1",
                "auto_context": 1,
            },
        )
        content = json.loads(response.content.decode())
        self.assertEqual(content["responseCode"], 200)

        glossary_units = Unit.objects.filter(
            translation__component=self.glossary.component
        )

        self.assertEqual(self.glossary.unit_set.count(), 2)
        self.assertEqual(
            glossary_units.count(), 2 * self.glossary.component.translation_set.count()
        )

        self.assertEqual(
            set(
                glossary_units.filter(translation__language_code="cs").values_list(
                    "explanation", flat=True
                )
            ),
            {"explained 1", "explained 2"},
        )
        self.assertEqual(
            set(
                glossary_units.filter(translation__language_code="en").values_list(
                    "explanation", flat=True
                )
            ),
            {""},
        )

    def test_tsv(self) -> None:
        # Import file
        self.import_file(TEST_CSV)

        tsv_data = get_glossary_tsv(self.get_translation())

        handle = StringIO(tsv_data)

        reader = csv.reader(handle, "excel-tab")
        lines = list(reader)
        self.assertEqual(len(lines), 163)
        self.assertTrue(all(len(line) == 2 for line in lines))

    def test_stale_glossaries_cleanup(self) -> None:
        # setup: make glossary managed outside weblate
        self.glossary_component.repo = "git://example.com/test/project.git"
        self.glossary_component.save()

        initial_count = self.glossary_component.translation_set.count()

        # check glossary not deleted because it has a valid translation
        cleanup_stale_glossaries(self.project.id)
        self.assertEqual(self.glossary_component.translation_set.count(), initial_count)

        # delete translation: should trigger cleanup_stale_glossary task
        german = Language.objects.get(code="de")
        self.component.translation_set.get(language=german).remove(self.user)

        cleanup_stale_glossaries(self.project.id)
        self.assertEqual(self.glossary_component.translation_set.count(), initial_count)

        # make glossary managed by weblate
        self.glossary_component.repo = "local:"
        self.glossary_component.save()

        # check that one glossary has been deleted
        cleanup_stale_glossaries(self.project.id)
        self.assertEqual(
            self.glossary_component.translation_set.count(), initial_count - 1
        )

    def test_stale_glossary_translations(self) -> None:
        self.assertFalse(get_stale_glossary_translations(self.project).exists())

        self.make_glossary_language_stale("de")

        self.assertEqual(
            list(
                get_stale_glossary_translations(self.project).values_list(
                    "language_code", flat=True
                )
            ),
            ["de"],
        )

        self.component.delete()

        self.assertFalse(get_stale_glossary_translations(self.project).exists())

    def test_unused_glossary_language_alert(self) -> None:
        glossary = self.make_glossary_language_stale("de", "unused de")

        cleanup_stale_glossaries(self.project.id)

        self.assertTrue(
            self.glossary_component.translation_set.filter(pk=glossary.pk).exists()
        )
        self.assert_unused_glossary_language_alert()

        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})

        self.assert_unused_glossary_language_alert("de")
        alert = self.glossary_component.alert_set.get(name="UnusedGlossaryLanguage")
        removal_url = f"{glossary.get_absolute_url()}#organize"

        self.assertFalse(self.user.has_perm("translation.delete", glossary))
        self.assertNotIn(removal_url, alert.render(self.user))

        self.make_manager()
        self.user.clear_permissions_cache()
        self.assertTrue(self.user.has_perm("translation.delete", glossary))
        self.assertIn(removal_url, alert.render(self.user))

    def test_unused_glossary_language_alert_missing_translation(self) -> None:
        glossary = self.make_glossary_language_stale("de", "unused de")
        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})
        alert = self.glossary_component.alert_set.get(name="UnusedGlossaryLanguage")
        removal_url = f"{glossary.get_absolute_url()}#organize"

        glossary.delete()
        self.make_manager()
        self.user.clear_permissions_cache()

        rendered = alert.render(self.user)
        self.assertIn("<td>de</td>", rendered)
        self.assertNotIn(removal_url, rendered)

    def test_unused_glossary_language_alert_ignores_blank_local_glossary(self) -> None:
        self.make_glossary_language_stale("de")

        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})

        self.assert_unused_glossary_language_alert()

    def test_unused_glossary_language_alert_ignores_matching_language(self) -> None:
        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})

        self.assert_unused_glossary_language_alert()

    def test_unused_glossary_language_alert_ignores_glossary_only_project(self) -> None:
        self.component.delete()

        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})

        self.assert_unused_glossary_language_alert()

    def test_unused_glossary_language_alert_updates_on_removal(self) -> None:
        czech_glossary = self.make_glossary_language_stale("cs", "unused cs")
        german_glossary = self.make_glossary_language_stale("de", "unused de")
        update_alerts(self.glossary_component, {"UnusedGlossaryLanguage"})
        self.assert_unused_glossary_language_alert("cs", "de")

        with self.captureOnCommitCallbacks(execute=True):
            german_glossary.remove(self.user)

        self.assert_unused_glossary_language_alert("cs")

        with self.captureOnCommitCallbacks(execute=True):
            czech_glossary.remove(self.user)

        self.assert_unused_glossary_language_alert()

    def test_prohibited_initial_character(self) -> None:
        """Test that a prohibited initial character in views."""
        self.make_manager()
        response = self.client.post(
            reverse("new-unit", kwargs={"path": self.glossary.get_url_path()}),
            {
                "source_0": "=prohibited",
                "target_0": "target",
                "terminology": "on",
                "new-unit-form-type": "singular",
            },
            follow=True,
        )
        self.assertContains(response, "Prohibited initial character")
        self.assertContains(response, "New string has been added.")

        # add ignore flag, check warning is gone
        unit = self.glossary.unit_set.get(source="=prohibited")
        response = self.client.post(
            reverse("edit_context", kwargs={"pk": unit.pk}),
            {
                "next": reverse("translate", kwargs={"path": unit.get_url_path()}),
                "explanation": "",
                "extra_flags": "terminology,ignore-prohibited-initial-character",
            },
            follow=True,
        )
        self.assertNotContains(response, "Prohibited initial character")

    def removal_test(
        self,
        translation: Translation,
        *,
        commit: bool = False,
        expected_source: int = 0,
        **kwargs,
    ) -> None:
        self.make_manager()
        self.assertEqual(translation.unit_set.count(), 0)
        self.do_add_unit(**kwargs)
        if commit:
            self.glossary_component.commit_pending("test", None)
        self.assertEqual(translation.unit_set.count(), 1)
        unit = translation.unit_set.get(source="source")
        translation.delete_unit(None, unit)
        self.assertEqual(translation.unit_set.count(), 0)
        self.assertEqual(
            self.glossary_component.source_translation.unit_set.count(), expected_source
        )

        # Verify that reparsing will not bring the unit back
        self.glossary_component.create_translations_immediate(force=True)
        # For terminology strings, the string will reappear here
        self.assertEqual(translation.unit_set.count(), expected_source)
        self.assertEqual(
            self.glossary_component.source_translation.unit_set.count(), expected_source
        )

    def test_string_removal(self) -> None:
        self.removal_test(self.glossary)

    def test_source_string_removal(self) -> None:
        self.removal_test(self.glossary_component.source_translation)

    def test_string_removal_terminology(self) -> None:
        self.removal_test(self.glossary, terminology=1, expected_source=1)

    def test_source_string_removal_terminology(self) -> None:
        self.removal_test(self.glossary_component.source_translation, terminology=1)

    def test_string_removal_commit(self) -> None:
        self.removal_test(self.glossary, commit=True)

    def test_source_string_removal_commit(self) -> None:
        self.removal_test(self.glossary_component.source_translation, commit=True)

    def test_exact_and_not_applicable_are_per_language(self) -> None:
        """Задача 1: exact/not-applicable land on the target unit, not shared."""
        with self.captureOnCommitCallbacks(execute=True):
            self.glossary.add_unit(
                None,
                "",
                "hello",
                "ahoj",
                author=self.user,
                extra_flags="exact,terminology",
            )
        de_glossary = self.glossary_component.translation_set.get(language_code="de")
        de_unit = de_glossary.unit_set.get(source="hello")
        with self.captureOnCommitCallbacks(execute=True):
            de_unit.translate(self.user, "hallo", STATE_TRANSLATED)
        de_unit.update_extra_flags("not-applicable", self.user)

        cs_unit = self.glossary.unit_set.get(source="hello")
        de_unit = de_glossary.unit_set.get(source="hello")

        self.assertEqual(cs_unit.extra_flags, "exact")
        self.assertEqual(de_unit.extra_flags, "not-applicable")
        # Only one glossary entry exists: the modes did not fork the term.
        self.assertEqual(
            self.glossary_component.source_translation.unit_set.filter(
                source="hello"
            ).count(),
            1,
        )

    def test_edit_context_sets_per_language_flag_on_target_unit(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.add_term("hello", "ahoj")
        unit = self.glossary.unit_set.get(source="hello")

        response = self.client.post(
            reverse("edit_context", kwargs={"pk": unit.pk}), {"addflag": "exact"}
        )
        self.assertRedirects(response, unit.get_absolute_url())
        unit = self.glossary.unit_set.get(source="hello")
        self.assertIn("exact", unit.get_unit_flags())
        source_unit = self.glossary_component.source_translation.unit_set.get(
            source="hello"
        )
        self.assertNotIn("exact", source_unit.get_unit_flags())

    def test_edit_context_rejects_per_language_flag_on_source_unit(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.add_term("hello", "ahoj")
        source_unit = self.glossary_component.source_translation.unit_set.get(
            source="hello"
        )

        response = self.client.post(
            reverse("edit_context", kwargs={"pk": source_unit.pk}),
            {"addflag": "not-applicable"},
            follow=True,
        )
        # A bad request, not a missing page: the translator is redirected back
        # with an explanation and the flag is not stored.
        self.assertRedirects(response, source_unit.get_absolute_url())
        self.assertContains(response, "can only be set on a translation")
        source_unit.refresh_from_db()
        self.assertNotIn("not-applicable", source_unit.get_unit_flags())

    def test_per_language_flags_are_left_alone_outside_a_glossary(self) -> None:
        """A glossary mode carries no meaning on a regular component."""
        translation = self.component.translation_set.get(language_code="cs")
        with self.captureOnCommitCallbacks(execute=True):
            translation.add_unit(
                None,
                "regular-context",
                "regular source",
                "regular target",
                author=self.user,
                extra_flags="exact",
            )
        source_unit = self.component.source_translation.unit_set.get(
            context="regular-context"
        )
        target_unit = translation.unit_set.get(context="regular-context")
        # No relocation to the target unit happens outside a glossary.
        self.assertEqual(source_unit.extra_flags, "exact")
        self.assertEqual(target_unit.extra_flags, "")


class GlossaryCoverageCommandTest(ViewTestCase):
    """The coverage report names what matched, what did not, and writes nothing."""

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]

    def add_source_term(self, source: str) -> None:
        # The report only reads source-language glossary units, so unlike
        # GlossaryTest.add_term no target-language unit is needed.
        self.glossary_component.source_translation.unit_set.create(
            source=source,
            target=source,
            context="",
            id_hash=calculate_hash(source, ""),
            position=1,
            state=STATE_TRANSLATED,
        )

    def run_command(self) -> str:
        output = StringIO()
        call_command("glossary_coverage", self.project.slug, stdout=output)
        return output.getvalue()

    def test_reports_a_term_present_in_the_source_strings(self) -> None:
        self.add_source_term("world")

        report = self.run_command()

        self.assertIn("matched terms:", report)
        self.assertIn("world", report)

    def test_reports_a_term_absent_from_the_source_strings(self) -> None:
        self.add_source_term("dragon")

        report = self.run_command()

        self.assertIn("never matched", report)
        self.assertIn("dragon", report)

    def test_project_without_glossary_terms_says_so(self) -> None:
        self.assertIn("no glossary terms", self.run_command())

    def test_unknown_project_fails_loudly(self) -> None:
        with self.assertRaises(CommandError):
            call_command("glossary_coverage", "no-such-project", stdout=StringIO())

    def test_the_report_writes_nothing(self) -> None:
        self.add_source_term("world")
        before = Unit.objects.count()

        self.run_command()

        self.assertEqual(Unit.objects.count(), before)


class AuditGlossaryCommandTest(GlossaryTest):
    def run_command(self, **kwargs) -> str:
        output = StringIO()
        call_command("audit_glossary", stdout=output, **kwargs)
        return output.getvalue()

    def run_command_expecting_findings(self, **kwargs) -> str:
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("audit_glossary", stdout=output, **kwargs)
        return output.getvalue()

    def test_duplicate_term_with_diverging_targets(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Yashchik", context="ui")

        output = self.run_command_expecting_findings()

        self.assertIn("duplicate-term", output)

    def test_duplicate_term_with_one_target_is_clean(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Sunduk", context="ui")

        self.assertIn("no findings", self.run_command())

    def test_one_target_for_two_terms(self) -> None:
        self.add_term("Dealer", "Trader", context="buyer")
        self.add_term("Merchant", "Trader", context="ui")

        output = self.run_command_expecting_findings()

        self.assertIn("collapsed-terms", output)

    def test_baseline_accepts_a_known_finding(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Yashchik", context="ui")
        output = self.run_command_expecting_findings()
        key = next(
            line[2:].rsplit("\t", 1)[0]
            for line in output.splitlines()
            if line.startswith("! duplicate-term")
        )

        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "accepted.baseline"
            baseline.write_text(f"# deliberate homonym\n{key}\n", encoding="utf-8")

            self.assertIn("duplicate-term", self.run_command(baseline=baseline))

    def test_case_only_difference_is_not_a_finding(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "sunduk", context="ui")

        self.assertIn("no findings", self.run_command())

    def test_untranslated_term_is_not_a_finding(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Yashchik", context="ui")
        self.glossary.unit_set.filter(context="ui").update(state=STATE_EMPTY)

        self.assertIn("no findings", self.run_command())

    def test_unknown_project_fails_loudly(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command(project="no-such-project")

    def test_project_limits_the_audit(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Yashchik", context="ui")
        other = Project.objects.create(
            name="Other",
            slug="other",
            web="https://nonexisting.weblate.org/",
        )
        other.scratch_create_component(
            name="Other glossary",
            slug="other-glossary",
            source_language=self.component.source_language,
            file_format="po",
            is_glossary=True,
        )

        self.assertIn("no findings", self.run_command(project=other.slug))

    def test_the_audit_writes_nothing(self) -> None:
        self.add_term("Chest", "Sunduk", context="loot")
        self.add_term("Chest", "Sunduk", context="ui")
        before = Unit.objects.count()

        self.run_command()

        self.assertEqual(Unit.objects.count(), before)


class GlossaryStemMatcherTest(ViewTestCase):
    """
    Задача 2: source-side stem matcher, fetch_glossary_terms.

    Uses a dedicated Russian-source project (the default ViewTestCase
    component/glossary are English-source) so the matcher can recover
    inflected Russian source forms per
    docs/llm-first/plans/2026-08-11-glossary-morphological-enforcement.md.
    """

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.ru_project = self.create_project(name="Ru Source", slug="ru-source")
        self.ru_component = self.create_po(
            project=self.ru_project,
            source_language=Language.objects.get(code="ru"),
        )
        self.ru_glossary_component = self.ru_project.glossaries[0]
        self.ru_glossary = self.ru_glossary_component.translation_set.get(
            language_code="cs"
        )
        self.ru_translation = self.ru_component.translation_set.get(language_code="cs")

    def add_ru_term(self, source: str, flags: str = "") -> None:
        id_hash = calculate_hash(source, "")
        source_unit = self.ru_glossary_component.source_translation.unit_set.create(
            source=source,
            target=source,
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        self.ru_glossary.unit_set.create(
            source=source,
            target=source,
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            extra_flags=flags,
        )
        self.ru_glossary.invalidate_cache()

    def matched_sources(self, probe_source: str) -> set[str]:
        unit = Unit(
            translation=self.ru_translation,
            id_hash=1,
            source=probe_source,
            target="",
            context="",
            position=1,
            state=STATE_EMPTY,
        )
        fetch_glossary_terms([unit])
        return {term.source for term in unit.glossary_terms}

    def test_matched_prompt_entries_carry_context(self) -> None:
        """The judge accessor returns the neutral prompt-entry contract."""
        # ruff: ignore[import-outside-top-level]
        from weblate.glossary.models import get_matched_glossary_prompt_entries

        id_hash = calculate_hash("Захват", "")
        source_unit = self.ru_glossary_component.source_translation.unit_set.create(
            source="Захват",
            target="Захват",
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            explanation="Название игрового режима.",
            extra_flags="terminology",
        )
        self.ru_glossary.unit_set.create(
            source="Захват",
            target="Assaut",
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            explanation="Nom français du mode.",
        )
        self.ru_glossary.invalidate_cache()

        unit = Unit(
            translation=self.ru_translation,
            id_hash=1,
            source="Захватите ключевые позиции врага",
            target="",
            context="",
            position=1,
            state=STATE_EMPTY,
        )

        self.assertEqual(
            get_matched_glossary_prompt_entries(unit),
            [
                {
                    "source": "Захват",
                    "target": "Assaut",
                    "source_explanation": "Название игрового режима.",
                    "target_explanation": "Nom français du mode.",
                    "flags": ["terminology"],
                }
            ],
        )

    def test_stem_fallback_recovers_inflected_forms(self) -> None:
        for term in ("Гигахрущ", "ликвидатор", "ячейка", "блок", "община"):
            self.add_ru_term(term)
        cases = {
            "Гигахруща": "Гигахрущ",
            "ликвидаторов": "ликвидатор",
            "ячейку": "ячейка",
            "блока": "блок",
            "Общину": "община",
        }
        for inflected, canonical in cases.items():
            with self.subTest(inflected=inflected):
                self.assertIn(
                    canonical, self.matched_sources(f"Текст про {inflected} тут.")
                )

    def test_stem_fallback_excludes_exact_flagged_terms(self) -> None:
        self.add_ru_term("НИИ", flags="exact")
        self.add_ru_term("Партия", flags="exact")
        self.add_ru_term("Чистые", flags="exact")
        for probe_source in ("ни тут нет", "парту купили", "чистить полы"):
            with self.subTest(probe_source=probe_source):
                self.assertEqual(self.matched_sources(probe_source), set())

    def test_stem_fallback_excludes_acronyms_without_any_flag(self) -> None:
        """
        An abbreviation must not be stem-matched even with no flag set.

        Measured on production: Russian Snowball turns ``НИИ`` into ``ни``, a
        very common particle, which produced 34 false matches on col4 before
        the guard. The exact matcher still finds the abbreviation itself.
        """
        self.add_ru_term("НИИ")
        self.assertEqual(self.matched_sources("ни тут нет и ни там"), set())
        self.assertEqual(self.matched_sources("НИИ работает"), {"НИИ"})

    def test_stem_fallback_excludes_not_applicable_terms(self) -> None:
        self.add_ru_term("ликвидатор", flags="not-applicable")
        self.assertEqual(self.matched_sources("ликвидаторов было много"), set())

    def test_stem_fallback_never_matches_substrings(self) -> None:
        self.add_ru_term("концентрат")
        self.add_ru_term("блок")
        self.assertEqual(self.matched_sources("пищеконцентрат прибыл"), set())
        self.assertEqual(self.matched_sources("началась блокировка"), set())

    def test_matcher_fingerprint_changes_with_glossary_and_modes(self) -> None:
        """Задача 5: the fingerprint tracks Snowball, allowlists and content."""
        source_language = self.ru_component.source_language
        target_language = self.ru_translation.language

        baseline = glossary_matcher_fingerprint(
            self.ru_project, source_language, target_language
        )
        self.assertEqual(baseline["source_algorithm"], "russian")
        self.assertIsNone(baseline["target_algorithm"])
        self.assertIn("ru", baseline["source_stem_allowlist"])
        self.assertEqual(baseline["glossary_term_count"], 0)
        self.assertEqual(baseline["exact_only_term_count"], 0)
        self.assertEqual(baseline["not_applicable_term_count"], 0)

        self.add_ru_term("ликвидатор")
        after_add = glossary_matcher_fingerprint(
            self.ru_project, source_language, target_language
        )
        self.assertEqual(after_add["glossary_term_count"], 1)
        self.assertNotEqual(after_add["glossary_hash"], baseline["glossary_hash"])

        self.add_ru_term("НИИ", flags="exact")
        after_exact = glossary_matcher_fingerprint(
            self.ru_project, source_language, target_language
        )
        self.assertEqual(after_exact["exact_only_term_count"], 1)
        self.assertNotEqual(after_exact["glossary_hash"], after_add["glossary_hash"])

    def test_matcher_fingerprint_contract(self) -> None:
        """
        Задача 5: the probes in analysis/probes read these keys by name.

        Probe scripts are not exercised by CI, so the key set and the value
        types are pinned here: renaming a field must fail this test rather
        than a measurement run months later.
        """
        fingerprint = glossary_matcher_fingerprint(
            self.ru_project,
            self.ru_component.source_language,
            self.ru_translation.language,
        )
        self.assertEqual(
            set(fingerprint),
            {
                "snowball_version",
                "source_algorithm",
                "target_algorithm",
                "source_stem_allowlist",
                "target_morphology_allowlist",
                "llm_full_glossary_limit",
                "exact_only_term_count",
                "not_applicable_term_count",
                "glossary_term_count",
                "glossary_hash",
            },
        )
        self.assertIsInstance(fingerprint["snowball_version"], str)
        self.assertIsInstance(fingerprint["source_stem_allowlist"], list)
        self.assertIsInstance(fingerprint["target_morphology_allowlist"], list)
        self.assertIsInstance(fingerprint["llm_full_glossary_limit"], int)
        # A sha256 hex digest, so a probe can compare runs by string equality
        self.assertRegex(str(fingerprint["glossary_hash"]), r"\A[0-9a-f]{64}\Z")

    def test_stem_fallback_disabled_for_non_stem_source_language(self) -> None:
        """English source is not in SOURCE_STEM_LANGUAGES: no stem recovery."""
        en_project = self.create_project(name="En Source", slug="en-source")
        en_component = self.create_po(project=en_project)
        en_glossary_component = en_project.glossaries[0]
        en_glossary = en_glossary_component.translation_set.get(language_code="cs")
        en_translation = en_component.translation_set.get(language_code="cs")

        id_hash = calculate_hash("ship", "")
        source_unit = en_glossary_component.source_translation.unit_set.create(
            source="ship",
            target="ship",
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        en_glossary.unit_set.create(
            source="ship",
            target="ship",
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        en_glossary.invalidate_cache()

        unit = Unit(
            translation=en_translation,
            id_hash=1,
            source="ships arrived",
            target="",
            context="",
            position=1,
            state=STATE_EMPTY,
        )
        fetch_glossary_terms([unit])
        self.assertEqual({term.source for term in unit.glossary_terms}, set())


class GlossarySelectionCacheTest(ViewTestCase):
    """The cached term list must answer the selection its caller asked for."""

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]
        self.glossary = self.glossary_component.translation_set.get(
            language=self.get_translation().language
        )
        self.add_grouped_terms()

    def add_grouped_terms(self) -> None:
        """Add a matched term plus a sibling only the wide selection reaches."""
        variant = Variant.objects.create(
            component=self.glossary_component, variant_regex="", key="greeting"
        )
        for position, (source, target) in enumerate(
            (("Hello", "Ahoj"), ("Greeting", "Zdravím")), start=1
        ):
            id_hash = calculate_hash(source, "")
            source_unit = self.glossary_component.source_translation.unit_set.create(
                source=source,
                target=source,
                context="",
                id_hash=id_hash,
                position=position,
                state=STATE_TRANSLATED,
            )
            self.glossary.unit_set.create(
                source=source,
                target=target,
                context="",
                source_unit=source_unit,
                id_hash=id_hash,
                position=position,
                state=STATE_TRANSLATED,
                variant=variant,
            )
        self.glossary.invalidate_cache()

    def test_narrow_after_wide_excludes_the_variant(self) -> None:
        """A caller excluding variants must not inherit a wider selection."""
        unit = self.get_unit()
        self.assertEqual(len(get_glossary_terms(unit, include_variants=True)), 2)
        self.assertEqual(len(get_glossary_terms(unit, include_variants=False)), 1)

    def test_wide_after_narrow_includes_the_variant(self) -> None:
        """A caller wanting variants must not inherit a check's selection."""
        unit = self.get_unit()
        self.assertEqual(len(get_glossary_terms(unit, include_variants=False)), 1)
        self.assertEqual(len(get_glossary_terms(unit, include_variants=True)), 2)

    def test_assigned_terms_are_served_to_every_selection(self) -> None:
        """A list supplied from outside is the answer: matching is off."""
        unit = self.get_unit()
        unit.glossary_terms = []
        self.assertEqual(get_glossary_terms(unit, include_variants=False), [])
        self.assertEqual(get_glossary_terms(unit, include_variants=True), [])

    def test_full_upgrades_and_then_serves_a_shallow_caller(self) -> None:
        """``full`` is prefetch depth: upgrade refetches, downgrade reuses."""
        unit = self.get_unit()
        shallow = get_glossary_terms(unit, full=False)
        upgraded = get_glossary_terms(unit, full=True)
        self.assertIsNot(upgraded[0], shallow[0])
        self.assertIs(get_glossary_terms(unit, full=False)[0], upgraded[0])
