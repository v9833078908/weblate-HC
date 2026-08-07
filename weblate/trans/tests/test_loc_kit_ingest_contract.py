# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Weblate format contract tests for loc_kit_ingest output.

Generated PO/TBX files are loaded through Weblate's real format classes and,
for the glossary, through an actual glossary upload so the resulting database
units can be inspected. This verifies the generated files satisfy the Weblate
component/glossary contract end to end.

Run via: ./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import SimpleTestCase
from django.test.utils import modify_settings, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from translate.storage.pypo import pofile

from weblate.auth.models import User
from weblate.formats.models import FILE_FORMATS
from weblate.lang.models import Language
from weblate.machinery.llm import BaseLLMTranslation
from weblate.trans.models import Category, Component, Project
from weblate.trans.models.loc_kit import LOC_KIT_DRAFT_STORAGE, LocKitImportDraft
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.tests import http_mock
from weblate.utils.views import create_component_from_kit
from weblate.vcs.git import LocalRepository

# loc_kit_ingest is a standalone package at the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: ignore[module-import-not-at-top-of-file]
from loc_kit_ingest.model import GlossaryTerm, ParseResult, StringUnit

# ruff: ignore[module-import-not-at-top-of-file]
from loc_kit_ingest.profile import (
    ComponentProfile,
    KeyColumn,
    KeyedGrammar,
    LanguageColumn,
    MetadataColumn,
    PairRegion,
    PairsGrammar,
)

# ruff: ignore[module-import-not-at-top-of-file]
from loc_kit_ingest.writer import render_component

# --------------------------------------------------------------------------- #
# Shared component profiles
# --------------------------------------------------------------------------- #


def make_po_component(source_lang: str, target_lang: str) -> ComponentProfile:
    return ComponentProfile(
        sheet="temple",
        component="Temple",
        kind="po",
        source_lang=source_lang,
        header_row=0,
        first_data_row=2,
        languages=(
            LanguageColumn(source_lang, source_lang, 2, source_lang),
            LanguageColumn(target_lang, target_lang, 3, target_lang),
        ),
        key=KeyColumn(0, "id"),
        comments=(MetadataColumn(1, "Character", "Character"),),
        references=(MetadataColumn(4, "Id", "Id"),),
        grammar=KeyedGrammar(skip_rows=(1,), allow_blank_rows=True),
        key_language=None,
        initial_target_languages=(),
    )


def make_tbx_component(source_lang: str, target_lang: str) -> ComponentProfile:
    return ComponentProfile(
        sheet="terms",
        component="Terms",
        kind="tbx",
        source_lang=source_lang,
        header_row=0,
        first_data_row=None,
        languages=(
            LanguageColumn(source_lang, source_lang, 0, source_lang),
            LanguageColumn(target_lang, target_lang, 1, target_lang),
        ),
        key=None,
        comments=(),
        references=(),
        grammar=PairsGrammar(skip_rows=(1,), regions=(PairRegion(2, 3, 4),)),
        key_language=target_lang,
        initial_target_languages=(target_lang,),
    )


# --------------------------------------------------------------------------- #
# Format-level contract (no database needed)
# --------------------------------------------------------------------------- #


class LocKitFormatContractTest(SimpleTestCase):
    """Generated files load through Weblate's real format classes."""

    def setUp(self) -> None:
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def render_po(self) -> Path:
        component = make_po_component("ru", "en")
        result = ParseResult(
            component="Temple",
            kind="po",
            units=(
                StringUnit(
                    key="dialog.intro",
                    values={
                        "ru": " Привет, [shake]путник[/shake]!\r\n",
                        "en": "<color=#E3BA59>Hello, traveler!</color>",
                    },
                    comments=("Character: Sage",),
                    references=("101",),
                    row=4,
                ),
                StringUnit(
                    key="dialog.bye",
                    values={"ru": "Прощай.", "en": "Farewell."},
                    comments=("Character: Sage",),
                    references=("102",),
                    row=5,
                ),
            ),
            diagnostics=(),
            skipped_rows=(),
        )
        render_component(component, result, self.tmp)
        return self.tmp / "Temple"

    def render_tbx(self) -> Path:
        component = make_tbx_component("ru", "en")
        result = ParseResult(
            component="Terms",
            kind="tbx",
            units=(
                GlossaryTerm(
                    context="characters.sage",
                    values={"ru": "Мудрец", "en": "Sage"},
                    source_explanation="Источник описание",
                    target_explanations={"en": "Wise person"},
                    section="Characters",
                    term_row=4,
                    note_rows=(5,),
                ),
            ),
            diagnostics=(),
            skipped_rows=(),
        )
        render_component(component, result, self.tmp)
        return self.tmp / "Terms" / "tbx"

    @staticmethod
    def parse_mono(path: Path, template: Path):
        """Monolingual formats need an explicit template store."""
        cls = FILE_FORMATS["po-mono"]
        return cls(path, template_store=cls(template, is_template=True))

    def test_po_mono_template_maps_key_to_context_and_source(self) -> None:
        po_dir = self.render_po()
        store = self.parse_mono(po_dir / "ru.po", po_dir / "ru.po")

        contexts = {unit.context for unit in store.all_units if unit.context}
        self.assertEqual(contexts, {"dialog.intro", "dialog.bye"})

        unit = next(u for u in store.all_units if u.context == "dialog.intro")
        self.assertEqual(unit.source, " Привет, [shake]путник[/shake]!\r\n")

    def test_po_mono_translation_keeps_markup_and_source(self) -> None:
        po_dir = self.render_po()
        store = self.parse_mono(po_dir / "en.po", po_dir / "ru.po")

        unit = next(u for u in store.all_units if u.context == "dialog.intro")
        self.assertEqual(unit.target, "<color=#E3BA59>Hello, traveler!</color>")

    def test_po_mono_keeps_developer_comment_and_location(self) -> None:
        po_dir = self.render_po()
        store = self.parse_mono(po_dir / "ru.po", po_dir / "ru.po")

        unit = next(u for u in store.all_units if u.context == "dialog.intro")
        self.assertIn("Character: Sage", unit.notes)
        self.assertIn("101", unit.locations)

    def test_tbx_is_bilingual_with_both_explanations(self) -> None:
        tbx_dir = self.render_tbx()
        # Weblate always supplies the configured languages when parsing.
        store = FILE_FORMATS["tbx"](
            tbx_dir / "en.tbx", source_language="ru", language_code="en"
        )

        units = [u for u in store.all_units if u.context]
        self.assertEqual(len(units), 1)
        unit = units[0]
        self.assertEqual(unit.context, "characters.sage")
        self.assertEqual(unit.source, "Мудрец")
        self.assertEqual(unit.target, "Sage")
        self.assertEqual(unit.source_explanation, "Источник описание")
        self.assertEqual(unit.explanation, "Wise person")

    def test_tbx_never_written_for_source_language(self) -> None:
        tbx_dir = self.render_tbx()
        self.assertFalse((tbx_dir / "ru.tbx").exists())
        self.assertTrue((tbx_dir / "en.tbx").is_file())

    def test_tbx_uses_profile_xml_lang_not_weblate_code(self) -> None:
        # The Weblate code zh_Hans must be emitted as the BCP-47 tag zh-Hans.
        component = replace(
            make_tbx_component("ru", "zh_Hans"),
            languages=(
                LanguageColumn("ru", "ru", 0, "ru"),
                LanguageColumn("zh_Hans", "zh-Hans", 1, "zh_Hans"),
            ),
        )
        result = ParseResult(
            component="Terms",
            kind="tbx",
            units=(
                GlossaryTerm(
                    context="characters.sage",
                    values={"ru": "Мудрец", "zh_Hans": "贤者"},
                    source_explanation="Источник",
                    target_explanations={"zh_Hans": "解释"},
                    section="Characters",
                    term_row=4,
                    note_rows=(5,),
                ),
            ),
            diagnostics=(),
            skipped_rows=(),
        )
        render_component(component, result, self.tmp)
        xml = (self.tmp / "Terms" / "tbx" / "zh_Hans.tbx").read_text(encoding="utf-8")
        self.assertIn('xml:lang="zh-Hans"', xml)
        self.assertNotIn('xml:lang="zh_Hans"', xml)


# --------------------------------------------------------------------------- #
# Glossary import contract (real upload, real database units)
# --------------------------------------------------------------------------- #


class LocKitGlossaryImportContractTest(ViewTestCase):
    """A generated TBX imports into a real glossary and drives the LLM payload."""

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]
        self.glossary = self.glossary_component.translation_set.get(
            language=self.get_translation().language
        )
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def generate_tbx(self) -> Path:
        """Render a TBX matching the test project's source/target languages."""
        source_code = self.component.source_language.code
        target_code = self.glossary.language.code
        component = make_tbx_component(source_code, target_code)
        result = ParseResult(
            component="Terms",
            kind="tbx",
            units=(
                GlossaryTerm(
                    context="characters.sage",
                    values={source_code: "Sage", target_code: "Mudrc"},
                    source_explanation="A wise character",
                    target_explanations={target_code: "Moudra postava"},
                    section="Characters",
                    term_row=4,
                    note_rows=(5,),
                ),
            ),
            diagnostics=(),
            skipped_rows=(),
        )
        render_component(component, result, self.tmp)
        return self.tmp / "Terms" / "tbx" / f"{target_code}.tbx"

    def upload_tbx(self, path: Path):
        with path.open("rb") as handle:
            return self.client.post(
                reverse("upload", kwargs={"path": self.glossary.get_url_path()}),
                {"file": handle, "method": "add"},
            )

    def test_generated_tbx_imports_term_with_context_and_target(self) -> None:
        response = self.upload_tbx(self.generate_tbx())
        self.assertRedirects(response, self.glossary.get_absolute_url())

        unit = self.glossary.unit_set.get(context="characters.sage")
        self.assertEqual(unit.source, "Sage")
        self.assertEqual(unit.target, "Mudrc")

    def test_translation_upload_does_not_carry_explanations(self) -> None:
        """
        Documents a Weblate upload-path limitation, not a writer defect.

        The translation-upload view imports source/target only. Explanations
        reach the database through repository synchronisation instead, which is
        why the runbook attaches Terms.zip as component files. The generated
        file does carry both explanations - see
        LocKitFormatContractTest.test_tbx_is_bilingual_with_both_explanations.
        """
        self.upload_tbx(self.generate_tbx())

        unit = self.glossary.unit_set.get(context="characters.sage")
        self.assertEqual(unit.explanation, "")

    def test_imported_unit_produces_full_llm_glossary_entry(self) -> None:
        """A real database unit with explanations yields all payload fields."""
        self.upload_tbx(self.generate_tbx())
        unit = self.glossary.unit_set.get(context="characters.sage")

        # Apply the explanations that repository synchronisation would set.
        unit.explanation = "Moudra postava"
        unit.save(update_fields=["explanation"])
        unit.source_unit.explanation = "A wise character"
        unit.source_unit.save(update_fields=["explanation"])
        unit.refresh_from_db()

        # ruff: ignore[private-member-access]
        entry = BaseLLMTranslation._get_glossary_entry(unit)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["source"], "Sage")
        self.assertEqual(entry["target"], "Mudrc")
        self.assertEqual(entry["source_explanation"], "A wise character")
        self.assertEqual(entry["target_explanation"], "Moudra postava")


# --------------------------------------------------------------------------- #
# Universal component upload: table kits through the real create view
# --------------------------------------------------------------------------- #


class LocKitUniversalUploadContractTest(ViewTestCase):
    """
    A table kit uploaded in the create UI becomes a live component.

    Covers the N-language contract: every populated language column arrives
    as a translation, regardless of how many there are.
    """

    KIT_CSV = (
        "id,,ru,en,ja,ko,de\n"
        "id-ignore,Actor,Russian ,English ,,,\n"
        "line_1,Ann,Привет,Hello,こんにちは,안녕,Hallo\n"
        "line_2,Bob,Пока,Bye,さようなら,잘 가,Tschüss\n"
    )

    def _upload(self, name: str, body: str):
        return SimpleUploadedFile(name, body.encode(), content_type="text/csv")

    def _kit_data(self, slug: str) -> dict:
        return {"project": self.project, "slug": slug, "name": slug.title()}

    def test_five_language_kit_renders_every_language(self) -> None:
        fake, info = create_component_from_kit(
            self._kit_data("dialogs"),
            self._upload("Space Kit - Dialogs.csv", self.KIT_CSV),
        )
        self.assertEqual(info["languages"], ["ru", "en", "ja", "ko", "de"])
        self.assertEqual(info["source_lang"], "ru")
        self.assertEqual(info["units"], 2)
        self.assertEqual(info["template"], "ru.po")
        repo = Path(fake.full_path)
        self.assertEqual(
            sorted(path.name for path in repo.glob("*.po")),
            ["de.po", "en.po", "ja.po", "ko.po", "ru.po"],
        )
        shutil.rmtree(fake.full_path, ignore_errors=True)

    def test_keyless_kit_uses_source_term_as_key(self) -> None:
        # No dedicated key column: the ru column is both the PO key and the
        # source language. The "Use as glossary" checkbox does not exist at
        # this call boundary, so it cannot influence this ordinary PO path.
        keyless_csv = "ru,en,ja\nПривет,Hello,こんにちは\nПока,Bye,さようなら\n"
        fake, info = create_component_from_kit(
            self._kit_data("keyless"), self._upload("Dialogs.csv", keyless_csv)
        )
        self.assertEqual(info["source_lang"], "ru")
        self.assertEqual(info["languages"], ["ru", "en", "ja"])
        self.assertEqual(info["template"], "ru.po")
        self.assertEqual(info["units"], 2)
        self.assertTrue(
            any(
                "is both the PO key and a language column" in note
                for note in info["notes"]
            )
        )

        repo = Path(fake.full_path)
        self.assertEqual(
            sorted(path.name for path in repo.glob("*.po")),
            ["en.po", "ja.po", "ru.po"],
        )

        ru_units = {u.getid(): u for u in pofile.parsefile(str(repo / "ru.po")).units}
        en_units = {u.getid(): u for u in pofile.parsefile(str(repo / "en.po")).units}
        self.assertEqual(ru_units["Привет"].target, "Привет")
        self.assertEqual(en_units["Привет"].target, "Hello")
        self.assertEqual(en_units["Пока"].target, "Bye")

        shutil.rmtree(fake.full_path, ignore_errors=True)

    def test_zip_upload_keeps_historical_behavior(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("cs.po", 'msgid "hello"\nmsgstr "ahoj"\n')
        fake, info = create_component_from_kit(
            self._kit_data("zipped"),
            SimpleUploadedFile(
                "kit.zip", buffer.getvalue(), content_type="application/zip"
            ),
        )
        self.assertIsNone(info)
        self.assertTrue((Path(fake.full_path) / "cs.po").exists())
        shutil.rmtree(fake.full_path, ignore_errors=True)

    def test_duplicate_keys_block_intake(self) -> None:
        broken = "id,ru,en\na,Один,One\na,Два,Two\n"
        with self.assertRaises(ValidationError) as ctx:
            create_component_from_kit(
                self._kit_data("broken"), self._upload("Broken.csv", broken)
            )
        self.assertIn("duplicate key", "".join(ctx.exception.messages))

    def test_unsupported_suffix_is_rejected(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            create_component_from_kit(
                self._kit_data("plain"), self._upload("notes.txt", "hello")
            )
        self.assertIn(".csv", "".join(ctx.exception.messages))

    def test_create_view_builds_component_from_csv(self) -> None:
        self.user.is_superuser = True
        self.user.save()

        with modify_settings(INSTALLED_APPS={"remove": "weblate.billing"}):
            response = self.client.post(
                reverse("create-component-zip"),
                {
                    "zipfile": self._upload("Space Kit - Dialogs.csv", self.KIT_CSV),
                    "name": "Dialogs",
                    "slug": "dialogs",
                    "project": self.project.pk,
                    "source_language": self.component.source_language.pk,
                },
            )
            # The kit skips discovery: the create form arrives prefilled.
            self.assertContains(response, "Loc-kit converted")
            self.assertContains(response, "ru.po")

            form = response.context["form"]
            params = {field: form[field].value() or "" for field in form.fields}
            params.pop("inherit_new_lang", None)
            params["new_lang"] = "none"
            response = self.client.post(
                reverse("create-component-zip"), params, follow=True
            )

        component = Component.objects.get(slug="dialogs")
        self.assertEqual(component.file_format, "po-mono")
        self.assertEqual(component.template, "ru.po")
        self.assertEqual(component.source_language.code, "ru")
        codes = sorted(
            component.translation_set.values_list("language__code", flat=True)
        )
        self.assertEqual(codes, ["de", "en", "ja", "ko", "ru"])
        self.assertEqual(component.source_translation.unit_set.count(), 2)

        # Unit identity: the game key is the context, texts are source/target.
        ja_unit = component.translation_set.get(language__code="ja").unit_set.get(
            context="line_1"
        )
        self.assertEqual(ja_unit.source, "Привет")
        self.assertEqual(ja_unit.target, "こんにちは")

        # The Character column survives as a developer comment on the unit.
        source_unit = component.source_translation.unit_set.get(context="line_1")
        self.assertIn("Ann", source_unit.note)


# --------------------------------------------------------------------------- #
# Glossary intake UI: sheet selection, preview, correction, confirmation
# --------------------------------------------------------------------------- #

GLOSSARY_CSV = (
    "domain,ru,en,note_ru,note_en\n"
    "Персонажи,Герой,Hero,Главный протагонист,Main protagonist\n"
    "Оружие,Меч,Sword,Ближний бой,Melee weapon\n"
)


def _glossary_profile(sheet: str, *, source_lang: str = "ru") -> dict:
    return {
        "schema_version": 2,
        "components": [
            {
                "sheet": sheet,
                "component": "ignored-by-server",
                "kind": "tbx",
                "source_lang": source_lang,
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 3, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": 3,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "section_field": {
                        "column": 1,
                        "header": "domain",
                        "row_offset": 0,
                    },
                    "notes": [
                        {
                            "scope": "source",
                            "column": 4,
                            "header": "note_ru",
                            "row_offset": 0,
                        },
                        {
                            "scope": "target",
                            "language": "en" if source_lang == "ru" else "ru",
                            "column": 5,
                            "header": "note_en",
                            "row_offset": 0,
                        },
                    ],
                },
                "initial_target_languages": ["en" if source_lang == "ru" else "ru"],
            }
        ],
    }


class LocKitGlossaryUploadUITest(ViewTestCase):
    """The glossary intake stages, end to end, through the real views."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        # Component repositories live under a DATA_DIR path that xdist
        # workers share, so two tests creating the same slug race on disk
        # even though their databases are separate.
        self.slug = f"gloss-{uuid.uuid4().hex[:8]}"

    def _csv(self, name: str = "Glossary.csv", body: str = GLOSSARY_CSV):
        return SimpleUploadedFile(name, body.encode(), content_type="text/csv")

    def _start(self, upload=None, slug: str | None = None):
        """Upload a table with the glossary checkbox set."""
        slug = slug or self.slug
        with modify_settings(INSTALLED_APPS={"remove": "weblate.billing"}):
            return self.client.post(
                reverse("create-component-zip"),
                {
                    "zipfile": upload or self._csv(),
                    "name": slug.title(),
                    "slug": slug,
                    "project": self.project.pk,
                    "source_language": self.component.source_language.pk,
                    "is_glossary": "1",
                },
            )

    def _draft(self):
        return LocKitImportDraft.objects.get()

    def _profile_upload(self, document: dict, name: str = "fix.loc-ingest.json"):
        return SimpleUploadedFile(
            name, json.dumps(document).encode(), content_type="application/json"
        )

    def _upload_profile(self, draft, document: dict):
        return self.client.post(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
            {"action": "upload-profile", "profile": self._profile_upload(document)},
            follow=True,
        )

    def _select_sheet(self, draft, sheet: str = "Glossary"):
        return self.client.post(
            reverse("loc-kit-sheet-select", kwargs={"token": draft.token}),
            {"sheet": sheet},
        )

    def _confirm(self):
        """Drive preview confirmation and the final component form."""
        draft = self._draft()
        with modify_settings(INSTALLED_APPS={"remove": "weblate.billing"}):
            response = self.client.post(
                reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
                {"action": "confirm"},
                follow=True,
            )
            form = response.context["form"]
            params = {field: form[field].value() or "" for field in form.fields}
            params.pop("inherit_new_lang", None)
            params["new_lang"] = "none"
            return self.client.post(
                reverse("loc-kit-glossary-confirm", kwargs={"token": draft.token}),
                params,
                follow=True,
            )

    # ----------------------------------------------------------------- #
    # Happy path
    # ----------------------------------------------------------------- #

    def test_glossary_upload_creates_a_draft_and_asks_for_a_sheet(self) -> None:
        response = self._start()

        draft = self._draft()
        self.assertRedirects(
            response,
            reverse("loc-kit-sheet-select", kwargs={"token": draft.token}),
        )
        self.assertEqual(draft.owner, self.user)
        self.assertEqual(draft.state, LocKitImportDraft.State.UPLOADED)
        # No component exists yet.
        self.assertFalse(Component.objects.filter(slug=self.slug).exists())

    def test_failed_draft_insert_leaves_no_orphaned_file(self) -> None:
        """
        Storage is not transactional.

        The upload lands on disk before the row is inserted, so a failed
        insert must not strand a file the row-driven cleanup can never see.
        """
        storage = LOC_KIT_DRAFT_STORAGE

        def before() -> set[str]:
            try:
                return set(storage.listdir("drafts")[1])
            except FileNotFoundError:
                return set()

        with (
            patch.object(LocKitImportDraft, "save", side_effect=DatabaseError("boom")),
            self.assertRaises(DatabaseError),
        ):
            self._start()

        self.assertFalse(LocKitImportDraft.objects.exists())
        self.assertEqual(before(), set())

    def test_disabled_analyzer_offers_manual_profile_upload(self) -> None:
        self._start()
        draft = self._draft()
        response = self._select_sheet(draft, "Glossary")

        draft.refresh_from_db()
        self.assertEqual(draft.sheet, "Glossary")
        self.assertEqual(draft.state, LocKitImportDraft.State.SHEET_SELECTED)
        # Analysis is off by default, so no profile was produced.
        self.assertEqual(draft.profile_json, "")
        page = self.client.get(response["Location"])
        self.assertContains(page, "Upload corrected profile")

    def test_stage_templates_never_nest_a_form(self) -> None:
        """
        Crispy must not emit a form tag of its own.

        Both stage templates supply their own <form> and submit button. When
        the helper wraps another one the source stays balanced, so only a
        real parser shows the damage: it closes the outer form at crispy's
        </form> and every later control - including submit - lands outside
        any form. The page renders normally and no click can post it.
        """
        self._start()
        draft = self._draft()

        pages = {
            "sheet": self.client.get(
                reverse("loc-kit-sheet-select", kwargs={"token": draft.token})
            ),
            "preview": self.client.get(
                self._select_sheet(draft, "Glossary")["Location"]
            ),
        }
        for stage, page in pages.items():
            depth = 0
            for tag in re.finditer(r"</?form\b", page.content.decode()):
                depth += -1 if tag.group().startswith("</") else 1
                self.assertLessEqual(
                    depth, 1, f"{stage}: a form is nested inside another form"
                )

    @override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
    def test_sheet_selection_is_not_rate_limited_without_analysis(self) -> None:
        """
        Picking a worksheet must stay free.

        A multi-sheet workbook needs several POSTs before the operator even
        reaches the sheet they want, and with the analyzer off none of them
        can reach a provider. Spending the analysis budget here locked the
        operator out of their own upload on the default configuration.
        """
        self._start()
        draft = self._draft()
        # Superusers bypass check_rate_limit, so the regression is only
        # visible as an ordinary user. The permission gate has its own test.
        self.user.is_superuser = False
        self.user.save()

        # Comfortably more than RATELIMIT_LOC_KIT_ANALYSIS_ATTEMPTS (3).
        with patch(
            "weblate.trans.views.create.get_creatable_projects",
            return_value=Project.objects.filter(pk=self.project.pk),
        ):
            preview = reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
            for attempt in range(6):
                response = self._select_sheet(draft, "Glossary")
                # A throttled POST also answers 302, but back to itself.
                # Only the destination distinguishes accepted from refused.
                self.assertEqual(response["Location"], preview, f"attempt {attempt}")

        draft.refresh_from_db()
        self.assertEqual(draft.sheet, "Glossary")
        self.assertEqual(draft.state, LocKitImportDraft.State.SHEET_SELECTED)

    @override_settings(
        LOC_KIT_PROFILE_ANALYSIS_ENABLED=True,
        LOC_KIT_PROFILE_OPENROUTER_KEY="sk-test-secret-do-not-leak",
        LOC_KIT_PROFILE_OPENROUTER_MODEL="openai/gpt-4o",
    )
    @http_mock.activate
    def test_analysis_attempts_are_capped_per_session(self) -> None:
        """
        The provider budget is bounded and the lockout stays recoverable.

        Superusers bypass check_rate_limit entirely, so the cap can only be
        observed as an ordinary user.
        """
        http_mock.register(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            json={"choices": [{"message": {"content": "not json"}}]},
        )
        self._start()
        draft = self._draft()
        # Demote only once the draft exists: the upload gate itself needs a
        # user the wizard would accept. The gate is covered separately, so
        # hold it open here and let the cap be the only thing under test.
        self.user.is_superuser = False
        self.user.save()

        attempts = settings.RATELIMIT_LOC_KIT_ANALYSIS_ATTEMPTS
        with patch(
            "weblate.trans.views.create.get_creatable_projects",
            return_value=Project.objects.filter(pk=self.project.pk),
        ):
            for _ in range(attempts):
                self._select_sheet(draft)
            self.assertEqual(len(http_mock.calls), attempts)

            # The next attempt is refused before any request leaves the
            # server, and the message keeps the manual profile route open.
            response = self._select_sheet(draft)
            self.assertEqual(len(http_mock.calls), attempts)
            page = self.client.get(response["Location"])
        self.assertContains(page, "Upload a profile to continue")

    @http_mock.activate
    def test_manual_profile_produces_a_preview_without_any_outbound_request(
        self,
    ) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)

        response = self._upload_profile(draft, _glossary_profile("Glossary"))
        # A correction is revalidated locally; the analyzer is never called.
        self.assertEqual(len(http_mock.calls), 0)

        draft.refresh_from_db()
        self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
        preview = json.loads(draft.preview_json)
        self.assertEqual(preview["source_language"], "ru")
        self.assertEqual(preview["target_languages"], ["en"])
        self.assertEqual(preview["term_count"], 2)
        self.assertContains(response, "Герой")

    def test_confirmed_glossary_becomes_a_live_tbx_component(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        self._upload_profile(draft, _glossary_profile("Glossary"))
        self._confirm()

        component = Component.objects.get(slug=self.slug)
        self.assertEqual(component.file_format, "tbx")
        self.assertEqual(component.filemask, "tbx/*.tbx")
        self.assertEqual(component.template, "")
        self.assertTrue(component.is_glossary)
        self.assertEqual(component.source_language.code, "ru")

        # A glossary is created for every project language; the two the kit
        # actually carries must be among them.
        codes = set(component.translation_set.values_list("language__code", flat=True))
        self.assertLessEqual({"en", "ru"}, codes)

        unit = component.translation_set.get(language__code="en").unit_set.get(
            context='["Персонажи","Герой"]'
        )
        self.assertEqual(unit.source, "Герой")
        self.assertEqual(unit.target, "Hero")

        # The draft and its file are gone once the component exists.
        self.assertFalse(LocKitImportDraft.objects.exists())

    def test_created_glossary_matches_a_component_with_the_same_source(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        self._upload_profile(draft, _glossary_profile("Glossary"))
        self._confirm()

        glossary = Component.objects.get(slug=self.slug)
        self.assertTrue(glossary.is_glossary)
        # Glossary matching keys off the source language.
        self.assertEqual(
            glossary.source_language.code,
            "ru",
            "a glossary only helps components sharing its source language",
        )

    # ----------------------------------------------------------------- #
    # Multi-sheet
    # ----------------------------------------------------------------- #

    def test_multiple_worksheets_require_an_explicit_choice(self) -> None:
        buffer = io.BytesIO()
        workbook = Workbook()
        first = workbook.active
        first.title = "Glossary"
        for row in csv.reader(io.StringIO(GLOSSARY_CSV)):
            if row:
                first.append(row)
        second = workbook.create_sheet("Other")
        second.append(["domain", "ru", "en", "note_ru", "note_en"])
        second.append(["X", "Щит", "Shield", "", ""])
        workbook.save(buffer)

        self._start(
            upload=SimpleUploadedFile(
                "Book.xlsx",
                buffer.getvalue(),
                content_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            )
        )
        draft = self._draft()
        page = self.client.get(
            reverse("loc-kit-sheet-select", kwargs={"token": draft.token})
        )
        self.assertContains(page, "Glossary")
        self.assertContains(page, "Other")

        # A profile naming the unselected sheet is refused.
        self._select_sheet(draft, "Glossary")
        self._upload_profile(draft, _glossary_profile("Other"))
        draft.refresh_from_db()
        self.assertNotEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)

    # ----------------------------------------------------------------- #
    # Rejections: nothing is ever created
    # ----------------------------------------------------------------- #

    def test_draft_endpoints_enforce_the_same_gate_as_the_wizard(self) -> None:
        """
        A draft must never be a laxer way in than the ordinary wizard.

        The wizard gates component creation on get_creatable_projects(), which
        is managed_projects intersected with valid billing. A draft endpoint
        that only checked "project.edit" would let a user whose billing has
        lapsed create a component the wizard refuses.
        """
        self._start()
        draft = self._draft()

        # Drop the user out of the creatable set the wizard itself uses.
        self.user.is_superuser = False
        self.user.save()

        with patch(
            "weblate.trans.views.create.get_creatable_projects",
            return_value=Project.objects.none(),
        ):
            for name in (
                "loc-kit-sheet-select",
                "loc-kit-glossary-preview",
                "loc-kit-glossary-confirm",
            ):
                response = self.client.get(reverse(name, kwargs={"token": draft.token}))
                self.assertEqual(response.status_code, 404, name)

        self.assertFalse(Component.objects.filter(slug=self.slug).exists())

    def test_confirm_never_writes_into_another_components_repository(self) -> None:
        """
        The staged repo must land on the component's own path.

        LocalRepository.from_files removes an existing target before cloning.
        Building full_path from the draft's category while the form saves a
        different one aimed the write at another component's VCS directory:
        slug uniqueness is per (project, category), so a slug that collides
        at the draft's category level is perfectly valid at another one.
        """
        victim = self.component
        victim_path = victim.full_path

        # A second category makes the victim's slug reusable, so the final
        # form accepts it while the draft still carries category=None.
        category = Category.objects.create(
            project=self.project, name="Elsewhere", slug="elsewhere"
        )

        self._start()
        draft = self._draft()
        self.assertIsNone(draft.category)
        self._select_sheet(draft)
        self._upload_profile(draft, _glossary_profile("Glossary"))

        targets: list[str] = []
        real_from_files = LocalRepository.from_files

        def spy(target, files):
            targets.append(target)
            return real_from_files(target, files)

        with modify_settings(INSTALLED_APPS={"remove": "weblate.billing"}):
            response = self.client.post(
                reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
                {"action": "confirm"},
                follow=True,
            )
            form = response.context["form"]
            params = {field: form[field].value() or "" for field in form.fields}
            params.pop("inherit_new_lang", None)
            params["new_lang"] = "none"
            # Aim the write at the victim: its slug, but another category.
            params["slug"] = victim.slug
            params["category"] = category.pk
            with patch("weblate.trans.views.create.LocalRepository.from_files", spy):
                self.client.post(
                    reverse("loc-kit-glossary-confirm", kwargs={"token": draft.token}),
                    params,
                    follow=True,
                )

        self.assertNotIn(
            victim_path, targets, "the staged repo was written over another component"
        )

    def test_invalid_profile_creates_no_component(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        broken = _glossary_profile("Glossary")
        broken["components"][0]["grammar"]["notes"][0]["header"] = "wrong_header"
        self._upload_profile(draft, broken)

        draft.refresh_from_db()
        self.assertEqual(draft.profile_json, "")
        self.assertFalse(Component.objects.filter(slug=self.slug).exists())

    def test_source_only_profile_creates_no_component(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        source_only = _glossary_profile("Glossary")
        source_only["components"][0]["initial_target_languages"] = []
        self._upload_profile(draft, source_only)

        draft.refresh_from_db()
        self.assertEqual(draft.profile_json, "")

    def test_confirm_before_a_preview_exists_is_refused(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        response = self.client.post(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
            {"action": "confirm"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Component.objects.filter(slug=self.slug).exists())

    def test_cancel_deletes_the_draft_and_creates_nothing(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        self._upload_profile(draft, _glossary_profile("Glossary"))

        self.client.post(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
            {"action": "cancel"},
            follow=True,
        )
        self.assertFalse(LocKitImportDraft.objects.exists())
        self.assertFalse(Component.objects.filter(slug=self.slug).exists())

    def test_another_user_cannot_touch_the_draft(self) -> None:
        self._start()
        draft = self._draft()

        other = User.objects.create_user("intruder", "intruder@example.com", "x")
        other.is_superuser = True
        other.save()
        self.client.force_login(other)

        for name in (
            "loc-kit-sheet-select",
            "loc-kit-glossary-preview",
            "loc-kit-glossary-confirm",
        ):
            response = self.client.get(reverse(name, kwargs={"token": draft.token}))
            self.assertEqual(response.status_code, 404, name)

    def test_expired_draft_is_unavailable(self) -> None:
        self._start()
        draft = self._draft()
        draft.expires_at = timezone.now() - timedelta(minutes=1)
        draft.save(update_fields=["expires_at"])

        response = self.client.get(
            reverse("loc-kit-sheet-select", kwargs={"token": draft.token})
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_token_is_unavailable(self) -> None:
        response = self.client.get(
            reverse("loc-kit-sheet-select", kwargs={"token": uuid.uuid4()})
        )
        self.assertEqual(response.status_code, 404)

    # ----------------------------------------------------------------- #
    # The final form cannot repoint the component
    # ----------------------------------------------------------------- #

    def test_tampered_final_post_is_overridden_by_the_draft(self) -> None:
        self._start()
        draft = self._draft()
        self._select_sheet(draft)
        self._upload_profile(draft, _glossary_profile("Glossary"))

        with modify_settings(INSTALLED_APPS={"remove": "weblate.billing"}):
            response = self.client.post(
                reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
                {"action": "confirm"},
                follow=True,
            )
            form = response.context["form"]
            params = {field: form[field].value() or "" for field in form.fields}
            params.pop("inherit_new_lang", None)
            params["new_lang"] = "none"
            # Try to repoint every conversion-derived field.
            params["file_format"] = "po-mono"
            params["filemask"] = "evil/*.po"
            params["template"] = "evil.po"
            params["is_glossary"] = ""
            params["source_language"] = Language.objects.get(code="en").pk
            self.client.post(
                reverse("loc-kit-glossary-confirm", kwargs={"token": draft.token}),
                params,
                follow=True,
            )

        component = Component.objects.get(slug=self.slug)
        self.assertEqual(component.file_format, "tbx")
        self.assertEqual(component.filemask, "tbx/*.tbx")
        self.assertEqual(component.template, "")
        self.assertTrue(component.is_glossary)
        self.assertEqual(component.source_language.code, "ru")
