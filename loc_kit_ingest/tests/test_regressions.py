# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests: boundary conditions across parsers, readers, writers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import zipfile

from loc_kit_ingest.cli import main
from loc_kit_ingest.model import Diagnostic, Severity, StringUnit
from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import load_profile
from loc_kit_ingest.reader import read_sheets
from loc_kit_ingest.writer import render_component, validate_rendered_component


# ---------------------------------------------------------------------------
# Temple-like CSV boundaries
# ---------------------------------------------------------------------------


def test_temple_fixture_has_label_row_blank_row_markup(tmp_path):
    """Verify the temple fixture exercises all markup families and row types."""
    fixture = Path(__file__).parent / "fixtures" / "temple.csv"
    rows = read_sheets(fixture)["temple"]
    assert rows[0] == ["id", "Character", "ru", "en", "Id"]  # header
    assert rows[1][0] == "id-ignore"  # label row
    assert rows[2] == []  # csv returns blank line as empty list
    data_row = rows[3]
    assert "[shake]" in data_row[2]
    assert "<color=#E3BA59>" in data_row[3]
    assert "{value:cond:1}" in data_row[3]
    assert "&#13;" in data_row[3]


def test_temple_fixture_parses_cleanly():
    """The anonymized temple fixture should parse with no ERROR diagnostics."""
    fixture_dir = Path(__file__).parent / "fixtures"
    profile = load_profile(fixture_dir / "temple.loc-ingest.json")
    comp = profile.components[0]
    rows = read_sheets(fixture_dir / "temple.csv")["temple"]
    result = parse_component(comp, rows)
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    assert len(result.units) == 1
    assert result.units[0].key == "sample_key"


# ---------------------------------------------------------------------------
# Terms-like CSV boundaries
# ---------------------------------------------------------------------------


def test_terms_fixture_parses_both_regions():
    fixture_dir = Path(__file__).parent / "fixtures"
    profile = load_profile(fixture_dir / "terms.loc-ingest.json")
    comp = profile.components[0]
    rows = read_sheets(fixture_dir / "terms.csv")["terms"]
    result = parse_component(comp, rows)
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    assert len(result.units) == 3


# ---------------------------------------------------------------------------
# Full pipeline regression: end-to-end with fixtures
# ---------------------------------------------------------------------------


def test_fixture_pipeline_produces_valid_po(tmp_path):
    """Run the full CLI with fixture data and verify PO output."""
    fixture_dir = Path(__file__).parent / "fixtures"
    output = tmp_path / "out"
    assert (
        main(
            [
                str(fixture_dir / "temple.csv"),
                "--profile",
                str(fixture_dir / "temple.loc-ingest.json"),
                "--out",
                str(output),
                "--zip",
            ]
        )
        == 0
    )
    assert (output / "Temple" / "ru.po").is_file()
    assert (output / "Temple" / "en.po").is_file()
    assert (output / "Temple.zip").is_file()
    # Verify ZIP content
    with zipfile.ZipFile(output / "Temple.zip") as zf:
        assert "ru.po" in zf.namelist()
        assert "en.po" in zf.namelist()


def test_fixture_pipeline_produces_valid_tbx(tmp_path):
    """Run the full CLI with Terms fixture and verify TBX output."""
    fixture_dir = Path(__file__).parent / "fixtures"
    output = tmp_path / "out"
    assert (
        main(
            [
                str(fixture_dir / "terms.csv"),
                "--profile",
                str(fixture_dir / "terms.loc-ingest.json"),
                "--out",
                str(output),
                "--zip",
            ]
        )
        == 0
    )
    assert (output / "Terms" / "tbx" / "en.tbx").is_file()
    assert (output / "Terms" / "tbx" / "ja.tbx").is_file()
    assert not (output / "Terms" / "tbx" / "ru.tbx").exists()
    with zipfile.ZipFile(output / "Terms.zip") as zf:
        assert "tbx/en.tbx" in zf.namelist()
        assert "tbx/ja.tbx" in zf.namelist()


# ---------------------------------------------------------------------------
# Whitespace preservation regression
# ---------------------------------------------------------------------------


def test_po_preserves_leading_trailing_whitespace(tmp_path):
    """Leading/trailing whitespace in PO values must survive render + parse-back."""
    from loc_kit_ingest.model import ParseResult

    fixture_dir = Path(__file__).parent / "fixtures"
    profile = load_profile(fixture_dir / "temple.loc-ingest.json")
    comp = profile.components[0]
    result = ParseResult(
        component="Temple",
        kind="po",
        units=(
            StringUnit(
                key="ws_key",
                values={"ru": "  spaced  ", "en": "\t tabbed \t"},
                comments=(),
                references=(),
                row=4,
            ),
        ),
        diagnostics=(),
        skipped_rows=(),
    )
    paths = render_component(comp, result, tmp_path)
    from translate.storage.pypo import pofile

    ru_store = pofile.parsestring(paths["ru"].read_bytes())
    unit = next(u for u in ru_store.units if u.getid() == "ws_key")
    assert unit.target == "  spaced  "
    en_store = pofile.parsestring(paths["en"].read_bytes())
    unit = next(u for u in en_store.units if u.getid() == "ws_key")
    assert unit.target == "\t tabbed \t"


def test_tbx_preserves_internal_newlines(tmp_path):
    """Internal newlines in TBX explanations must survive render + parse-back."""
    from loc_kit_ingest.model import GlossaryTerm, ParseResult

    fixture_dir = Path(__file__).parent / "fixtures"
    profile = load_profile(fixture_dir / "terms.loc-ingest.json")
    comp = profile.components[0]
    result = ParseResult(
        component="Terms",
        kind="tbx",
        units=(
            GlossaryTerm(
                context="test.newline",
                values={"ru": "Терм", "en": "Term", "ja": "用語"},
                explanations={"ru": "Опи\nсание", "en": "Expla\nnation", "ja": ""},
                section="Test",
                term_row=4,
                description_row=5,
            ),
        ),
        diagnostics=(),
        skipped_rows=(),
    )
    paths = render_component(comp, result, tmp_path)
    from translate.storage.tbx import tbxfile

    parsed = tbxfile.parsestring(paths["en"].read_bytes())
    unit = next(u for u in parsed.units if u.getid() == "test.newline")
    assert "\n" in unit.getnotes("definition")
