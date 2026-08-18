# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import load_profile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temple_component(tmp_path):
    profile_path = Path(__file__).parent / "fixtures" / "temple.loc-ingest.json"
    profile = load_profile(profile_path)
    return profile.components[0]


@pytest.fixture
def temple_rows():
    return [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        [
            "sample_key",
            "Character: Sample",
            " leading [shake]текст[/shake] ",
            "<color=#E3BA59>Text &#13;{value:cond:1}</color>",
            "42",
        ],
    ]


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------


def test_keyed_parser_keeps_markup_whitespace_comments_and_references(
    temple_component, temple_rows
):
    result = parse_component(temple_component, temple_rows)
    unit = result.units[0]
    assert unit.key == "sample_key"
    assert unit.values["ru"] == " leading [shake]текст[/shake] "
    assert unit.values["en"] == "<color=#E3BA59>Text &#13;{value:cond:1}</color>"
    assert unit.comments == ("Character: Sample",)
    assert unit.references == ("42",)
    errors = [d for d in result.diagnostics if d.severity.name == "ERROR"]
    assert errors == []


def test_keyed_parser_records_explicit_and_blank_skips(temple_component, temple_rows):
    result = parse_component(temple_component, temple_rows)
    assert {(skip.row, skip.reason) for skip in result.skipped_rows} == {
        (2, "profile_skip"),
        (3, "blank"),
    }


def test_duplicate_key_blocks_the_component(temple_component, temple_rows):
    temple_rows.append(list(temple_rows[3]))
    result = parse_component(temple_component, temple_rows)
    assert any(item.code == "po.duplicate_key" for item in result.diagnostics)


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


def test_missing_source_with_translations_is_warning_and_imports(
    temple_component, temple_rows
):
    """A key the kit only translated keeps its translations and loses the source."""
    temple_rows[3][2] = ""  # blank the source cell
    result = parse_component(temple_component, temple_rows)
    assert [
        d.severity.name for d in result.diagnostics if d.code == "po.missing_source"
    ] == ["WARNING"]
    assert [d for d in result.diagnostics if d.severity.name == "ERROR"] == []
    unit = result.units[0]
    assert unit.key == "sample_key"
    assert not unit.values["ru"]
    assert unit.values["en"] == "<color=#E3BA59>Text &#13;{value:cond:1}</color>"


def test_key_without_any_text_is_error(temple_component, temple_rows):
    """A key with no text in any language would import as an empty unit."""
    temple_rows[3][2] = ""
    temple_rows[3][3] = ""
    result = parse_component(temple_component, temple_rows)
    assert any(
        d.code == "po.key_without_content" and d.severity.name == "ERROR"
        for d in result.diagnostics
    )
    assert result.units == ()


def test_short_row_is_error(temple_component, temple_rows):
    temple_rows.append(["only_key"])  # too short for source column
    result = parse_component(temple_component, temple_rows)
    assert any(item.code == "po.short_row" for item in result.diagnostics)


def test_unexpected_nonblank_unkeyed_row_is_error(temple_component):
    """Without allow_blank_rows, a blank row is an error."""
    # Replace grammar with one that disallows blanks
    component = _rebuild_component(temple_component, allow_blank_rows=False)
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["", "", "", "", ""],  # header row
        ["", "", "", "", ""],
        [
            "sample_key",
            "Character: Sample",
            "text",
            "text",
            "42",
        ],
    ]
    # Actually temple_component.header_row=0, first_data_row=2, skip_rows=[1]
    # We need proper layout. Let's set up cleanly.
    rows = [
        ["id", "ru", "en"],
        ["id-ignore", "ru", "en"],  # skip row 2 (0-based index 1)
        ["", "", ""],  # blank at first_data_row - error when allow_blank_rows=False
        ["k1", "v1", "v1"],
    ]
    component = _make_simple_po_component(tmp_allow_blank=False)
    result = parse_component(component, rows)
    assert any(item.code == "grammar.unexpected_row" for item in result.diagnostics)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_empty_target_is_warning(temple_component):
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        ["k1", "Character: Sample", "текст", "", "42"],
    ]
    result = parse_component(temple_component, rows)
    warnings = [d for d in result.diagnostics if d.severity.name == "WARNING"]
    assert any(w.code == "po.empty_target" for w in warnings)


def test_target_equals_source_is_warning(temple_component):
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        ["k1", "Character: Sample", "same text", "same text", "42"],
    ]
    result = parse_component(temple_component, rows)
    warnings = [d for d in result.diagnostics if d.severity.name == "WARNING"]
    assert any(w.code == "po.target_equals_source" for w in warnings)


def test_cyrillic_majority_english_is_warning(temple_component):
    """En column with mostly Cyrillic gets a warning."""
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        ["k1", "Character: Sample", "текст на русском", "текст на русском too", "42"],
    ]
    result = parse_component(temple_component, rows)
    warnings = [d for d in result.diagnostics if d.severity.name == "WARNING"]
    assert any(w.code == "po.wrong_script" for w in warnings)


def test_latin_majority_russian_is_warning(temple_component):
    """Ru column with mostly Latin gets a warning."""
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        [
            "k1",
            "Character: Sample",
            "this is mostly latin text",
            "mostly latin text",
            "42",
        ],
    ]
    result = parse_component(temple_component, rows)
    warnings = [d for d in result.diagnostics if d.severity.name == "WARNING"]
    assert any(w.code == "po.wrong_script" for w in warnings)


def test_no_double_report_wrong_script(temple_component):
    """If both target=source and wrong script, only one warning per cell."""
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        # en column: Cyrillic-majority AND equals source
        ["k1", "Character: Sample", "кириллица", "кириллица", "42"],
    ]
    result = parse_component(temple_component, rows)
    wrong_script = [d for d in result.diagnostics if d.code == "po.wrong_script"]
    assert len(wrong_script) == 1  # one for en, not for ru


def test_source_empty_note_like_target_is_warning(temple_component):
    """A row with no source but a target keeps the target and warns."""
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        ["k1", "", "", "[note: TODO]", "42"],
    ]
    result = parse_component(temple_component, rows)
    assert [
        d.severity.name for d in result.diagnostics if d.code == "po.missing_source"
    ] == ["WARNING"]
    assert result.units[0].values["en"] == "[note: TODO]"


def test_key_is_not_trimmed(temple_component):
    """Keys are stored verbatim, not stripped."""
    rows = [
        ["id", "Character", "ru", "en", "Id"],
        ["id-ignore", "label", "label", "label", "label"],
        ["", "", "", "", ""],
        ["  padded_key  ", "Character", "текст", "text", "42"],
    ]
    result = parse_component(temple_component, rows)
    assert result.units[0].key == "  padded_key  "


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rebuild_component(component, *, allow_blank_rows):
    """Return a copy of a component with allow_blank_rows changed."""
    from loc_kit_ingest.profile import KeyedGrammar

    new_grammar = KeyedGrammar(
        skip_rows=component.grammar.skip_rows,
        allow_blank_rows=allow_blank_rows,
    )
    from dataclasses import replace

    return replace(component, grammar=new_grammar)


def _make_simple_po_component(*, tmp_allow_blank):
    from loc_kit_ingest.profile import (
        ComponentProfile,
        KeyColumn,
        KeyedGrammar,
        LanguageColumn,
    )

    return ComponentProfile(
        sheet="test",
        component="Test",
        kind="po",
        source_lang="ru",
        header_row=0,
        first_data_row=2,
        languages=(
            LanguageColumn("ru", "ru", 1, "ru"),
            LanguageColumn("en", "en", 2, "en"),
        ),
        key=KeyColumn(0, "id"),
        comments=(),
        references=(),
        grammar=KeyedGrammar(skip_rows=(1,), allow_blank_rows=tmp_allow_blank),
        key_language=None,
        initial_target_languages=(),
    )
