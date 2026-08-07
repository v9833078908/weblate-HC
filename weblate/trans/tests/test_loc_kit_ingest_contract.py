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

import io
import shutil
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.test.utils import modify_settings
from django.urls import reverse
from translate.storage.pypo import pofile
from weblate.formats.models import FILE_FORMATS
from weblate.machinery.llm import BaseLLMTranslation
from weblate.trans.models import Component
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.views import create_component_from_kit

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
                    explanations={"ru": "Источник описание", "en": "Wise person"},
                    section="Characters",
                    term_row=4,
                    description_row=5,
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
                    explanations={"ru": "Источник", "zh_Hans": "解释"},
                    section="Characters",
                    term_row=4,
                    description_row=5,
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
                    explanations={
                        source_code: "A wise character",
                        target_code: "Moudra postava",
                    },
                    section="Characters",
                    term_row=4,
                    description_row=5,
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
