# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Weblate format contract tests for loc_kit_ingest output.

These tests generate PO/TBX files through loc_kit_ingest.writer and load them
through Weblate's real format classes (PoMonoFormat, TBXFormat). They verify
the generated files satisfy the Weblate component/glossary contract.

Run via: ./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py
"""

from __future__ import annotations

import io
from pathlib import Path

from weblate.trans.tests.utils import RepoTestMixin
from weblate.trans.models import Unit

# loc_kit_ingest is on sys.path as a standalone package (parent of weblate/).
import sys

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from loc_kit_ingest.model import GlossaryTerm, ParseResult, StringUnit
from loc_kit_ingest.profile import (
    ComponentProfile,
    KeyColumn,
    KeyedGrammar,
    LanguageColumn,
    MetadataColumn,
    PairRegion,
    PairsGrammar,
)
from loc_kit_ingest.writer import render_component


# ---------------------------------------------------------------------------
# PO contract test
# ---------------------------------------------------------------------------


PO_COMPONENT = ComponentProfile(
    sheet="temple",
    component="Temple",
    kind="po",
    source_lang="ru",
    header_row=0,
    first_data_row=2,
    languages=(
        LanguageColumn("ru", "ru", 2, "ru"),
        LanguageColumn("en", "en", 3, "en"),
    ),
    key=KeyColumn(0, "id"),
    comments=(
        MetadataColumn(1, "Character", "Character"),
    ),
    references=(
        MetadataColumn(4, "Id", "Id"),
    ),
    grammar=KeyedGrammar(skip_rows=(1,), allow_blank_rows=True),
    key_language=None,
    initial_target_languages=(),
)

PO_UNITS = (
    StringUnit(
        key="dialog.intro",
        values={
            "ru": "Привет, путник!",
            "en": "Hello, traveler!",
        },
        comments=("Character: Sage",),
        references=("101",),
        row=4,
    ),
    StringUnit(
        key="dialog.bye",
        values={
            "ru": "Прощай.",
            "en": "Farewell.",
        },
        comments=("Character: Sage",),
        references=("102",),
        row=5,
    ),
)


def _render_po(tmp_path: Path) -> tuple[Path, ComponentProfile]:
    result = ParseResult(
        component="Temple",
        kind="po",
        units=PO_UNITS,
        diagnostics=(),
        skipped_rows=(),
    )
    render_component(PO_COMPONENT, result, tmp_path)
    return tmp_path, PO_COMPONENT


class LocKitPOContractTest(RepoTestMixin):
    """Verify generated PO files load through PoMonoFormat."""

    def test_po_source_template_loads_correctly(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        out_dir, comp = _render_po(tmp)

        from weblate.formats.models import FILE_FORMATS

        fmt = FILE_FORMATS["po-mono"]
        store = fmt.parse(out_dir / "Temple" / "ru.po")

        # Two units (plus header).
        data_units = [u for u in store.all_units if not u.isheader()]
        assert len(data_units) == 2

        unit = next(u for u in data_units if u.context == "dialog.intro")
        assert unit.source == "Привет, путник!"
        assert unit.target == "Привет, путник!"

    def test_po_translation_loads_correctly(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        out_dir, comp = _render_po(tmp)

        from weblate.formats.models import FILE_FORMATS

        fmt = FILE_FORMATS["po-mono"]
        store = fmt.parse(out_dir / "Temple" / "en.po")

        data_units = [u for u in store.all_units if not u.isheader()]
        unit = next(u for u in data_units if u.context == "dialog.intro")
        assert unit.source == "Привет, путник!"
        assert unit.target == "Hello, traveler!"

    def test_po_preserves_comments_and_references(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        out_dir, comp = _render_po(tmp)

        from weblate.formats.models import FILE_FORMATS

        fmt = FILE_FORMATS["po-mono"]
        store = fmt.parse(out_dir / "Temple" / "ru.po")

        data_units = [u for u in store.all_units if not u.isheader()]
        unit = next(u for u in data_units if u.context == "dialog.intro")
        # Developer comment
        assert "Character: Sage" in unit.notes
        # Reference location
        assert "101" in unit.locations


# ---------------------------------------------------------------------------
# TBX contract test
# ---------------------------------------------------------------------------


TBX_COMPONENT = ComponentProfile(
    sheet="terms",
    component="Terms",
    kind="tbx",
    source_lang="ru",
    header_row=0,
    first_data_row=None,
    languages=(
        LanguageColumn("ru", "ru", 0, "ru"),
        LanguageColumn("en", "en", 1, "en"),
        LanguageColumn("ja", "ja", 2, "ja"),
    ),
    key=None,
    comments=(),
    references=(),
    grammar=PairsGrammar(
        skip_rows=(1,),
        regions=(
            PairRegion(2, 3, 4),
        ),
    ),
    key_language="en",
    initial_target_languages=("en", "ja"),
)

TBX_UNITS = (
    GlossaryTerm(
        context="characters.sage",
        values={"ru": "Мудрец", "en": "Sage", "ja": "賢者"},
        explanations={
            "ru": "Источник описание",
            "en": "Wise person",
            "ja": "賢い人",
        },
        section="Characters",
        term_row=4,
        description_row=5,
    ),
)


def _render_tbx(tmp_path: Path) -> Path:
    result = ParseResult(
        component="Terms",
        kind="tbx",
        units=TBX_UNITS,
        diagnostics=(),
        skipped_rows=(),
    )
    render_component(TBX_COMPONENT, result, tmp_path)
    return tmp_path / "Terms" / "tbx"


class LocKitTBXContractTest(RepoTestMixin):
    """Verify generated TBX files load through TBXFormat."""

    def test_tbx_loads_source_and_target(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        tbx_dir = _render_tbx(tmp)

        from weblate.formats.models import FILE_FORMATS

        fmt = FILE_FORMATS["tbx"]
        store = fmt.parse(tbx_dir / "en.tbx")

        data_units = [u for u in store.all_units if not u.isheader()]
        assert len(data_units) == 1
        unit = data_units[0]
        assert unit.source == "Мудрец"
        assert unit.target == "Sage"

    def test_tbx_preserves_explanations(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        tbx_dir = _render_tbx(tmp)

        from weblate.formats.models import FILE_FORMATS

        fmt = FILE_FORMATS["tbx"]
        store = fmt.parse(tbx_dir / "en.tbx")

        data_units = [u for u in store.all_units if not u.isheader()]
        unit = data_units[0]
        # The definition note (source explanation)
        assert "Источник описание" in unit.notes

    def test_tbx_no_source_language_file(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        tbx_dir = _render_tbx(tmp)
        assert not (tbx_dir / "ru.tbx").exists()

    def test_tbx_ja_file_exists(self, tmp_path=None):
        import tempfile

        tmp = Path(tmp_path or tempfile.mkdtemp())
        tbx_dir = _render_tbx(tmp)
        assert (tbx_dir / "ja.tbx").is_file()


# ---------------------------------------------------------------------------
# LLM glossary payload contract
# ---------------------------------------------------------------------------


class LocKitLLMGlossaryContractTest(RepoTestMixin):
    """Verify glossary payload structure from imported TBX units."""

    def test_glossary_entry_has_source_and_target_explanations(self):
        """_get_glossary_entry should include both explanations from TBX."""
        from weblate.machinery.llm import BaseLLMTranslation

        # Build a mock-like Unit with the right attributes.
        # In a real test, this unit would come from a loaded TBX component.
        # Here we verify the _get_glossary_entry code path directly.

        class MockUnit:
            all_flags = {"read-only", "terminology"}
            translated = True
            source = "Мудрец"
            target = "Sage"
            explanation = "Wise person"
            source_unit = type("S", (), {"explanation": "Источник описание"})()

        entry = BaseLLMTranslation._get_glossary_entry(MockUnit())
        assert entry is not None
        assert entry["source"] == "Мудрец"
        assert entry["target"] == "Sage"
        assert entry["source_explanation"] == "Источник описание"
        assert entry["target_explanation"] == "Wise person"

    def test_glossary_entry_omits_empty_explanations(self):
        """When explanations are empty, they should be omitted."""
        from weblate.machinery.llm import BaseLLMTranslation

        class MockUnit:
            all_flags = {"read-only", "terminology"}
            translated = True
            source = "Термин"
            target = "Term"
            explanation = ""
            source_unit = type("S", (), {"explanation": ""})()

        entry = BaseLLMTranslation._get_glossary_entry(MockUnit())
        assert entry is not None
        assert "source_explanation" not in entry
        assert "target_explanation" not in entry
