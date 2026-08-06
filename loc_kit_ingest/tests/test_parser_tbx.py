# Copyright (C) HCGameLoc
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
def terms_component(tmp_path):
    profile_path = Path(__file__).parent / "fixtures" / "terms.loc-ingest.json"
    profile = load_profile(profile_path)
    return profile.components[0]


@pytest.fixture
def terms_rows():
    """Rows matching the terms fixture CSV.

    Row layout (1-based):
      1: header: ru,en,ja
      2: skip: ru,en,ja
      3: section: Characters
      4: term:  Герой,Hero,ヒーロー
      5: desc:  Источник описание,Target explanation,Target explanation
      6: term:  Враг,Enemy,敵
      7: desc:  Второй источник,Second source,Second source
      8: section: Weapons
      9: term:  Меч,Sword,剣
     10: desc:  Источник меч,Source sword,Source sword
    """
    return [
        ["ru", "en", "ja"],
        ["ru", "en", "ja"],
        ["Characters"],
        ["Герой", "Hero", "ヒーロー"],
        ["Источник описание", "Target explanation", "Target explanation"],
        ["Враг", "Enemy", "敵"],
        ["Второй источник", "Second source", "Second source"],
        ["Weapons"],
        ["Меч", "Sword", "剣"],
        ["Источник меч", "Source sword", "Source sword"],
    ]


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------


def test_glossary_pairs_preserve_source_and_target_explanations(
    terms_component, terms_rows
):
    result = parse_component(terms_component, terms_rows)
    term = result.units[0]
    assert term.context == "characters.hero"
    assert term.values == {"ru": "Герой", "en": "Hero", "ja": "ヒーロー"}
    assert term.explanations["ru"] == "Источник описание"
    assert term.explanations["en"] == "Target explanation"
    assert term.term_row == 4 and term.description_row == 5


def test_second_region_parsed(terms_component, terms_rows):
    result = parse_component(terms_component, terms_rows)
    assert len(result.units) == 3
    term = result.units[2]
    assert term.context == "weapons.sword"
    assert term.values["en"] == "Sword"
    assert term.section == "Weapons"
    assert term.term_row == 9 and term.description_row == 10


def test_orphan_pair_and_uncovered_nonblank_row_are_errors(
    terms_component, terms_rows
):
    # Wipe the term row for the second pair in region 1.
    # The description still has data -> orphan_description.
    terms_rows[5] = [""]  # wipe term row 6 (0-based 5)
    # Actually we need to wipe the term but keep desc non-empty.
    # But _is_blank_row checks all cells. Let's set term cells to empty.
    terms_rows[5] = ["", "", ""]
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.orphan_description" for d in result.diagnostics)
    # Also test uncovered nonblank row
    terms_rows.append(["extra", "uncovered", "row"])
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "grammar.uncovered_row" for d in result.diagnostics)


def test_missing_section_is_error(terms_component, terms_rows):
    terms_rows[2] = [""]  # blank section row
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_section" for d in result.diagnostics)


def test_missing_source_term_is_error(terms_component, terms_rows):
    terms_rows[3][0] = ""  # blank ru term
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_term" for d in result.diagnostics)


def test_missing_source_explanation_is_error(terms_component, terms_rows):
    terms_rows[4][0] = ""  # blank ru explanation
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_explanation" for d in result.diagnostics)


def test_missing_initial_target_term_is_error(terms_component, terms_rows):
    terms_rows[3][1] = ""  # blank en term (en is in initial_target_languages)
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_duplicate_context_is_error(terms_component, terms_rows):
    # Make the second term in region 1 have same key_language term as first.
    # Region 1 has two pairs: rows 4-5 and rows 6-7. Both in section "Characters".
    terms_rows[5][1] = "Hero"  # key_language is en; first was "Hero"
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.duplicate_context" for d in result.diagnostics)


def test_unknown_extra_nonblank_row_is_error(terms_component, terms_rows):
    terms_rows.append(["unexpected", "data", "here"])
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "grammar.uncovered_row" for d in result.diagnostics)


def test_outer_whitespace_in_term_is_error(terms_component, terms_rows):
    terms_rows[3][0] = " Герой"  # leading space
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.unsupported_outer_whitespace" for d in result.diagnostics)


def test_outer_whitespace_in_explanation_is_error(terms_component, terms_rows):
    terms_rows[4][1] = "Target explanation "  # trailing space
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.unsupported_outer_whitespace" for d in result.diagnostics)


def test_preserves_internal_newlines_and_markup(terms_component):
    """Internal newlines, markup, and entities are preserved in TBX values."""
    rows = [
        ["ru", "en", "ja"],
        ["ru", "en", "ja"],
        ["Section"],
        ["терм<b>1</b>", "Term<b>1</b>", "用語"],
        ["опи\nсание", "descrip\ntion", "説明"],
    ]
    # Adjust component to match this 2-row region
    from loc_kit_ingest.profile import PairRegion, PairsGrammar
    from dataclasses import replace

    component = replace(
        terms_component,
        grammar=PairsGrammar(
            skip_rows=(1,),
            regions=(
                PairRegion(
                    section_row=2,
                    first_term_row=3,
                    last_description_row=4,
                ),
            ),
        ),
    )
    result = parse_component(component, rows)
    assert len(result.units) == 1
    term = result.units[0]
    assert "<b>1</b>" in term.values["ru"]
    assert "\n" in term.explanations["en"]


def test_empty_key_language_cell_is_error(terms_component, terms_rows):
    terms_rows[3][1] = ""  # blank key_language term (en)
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_skip_rows_recorded(terms_component, terms_rows):
    result = parse_component(terms_component, terms_rows)
    assert {(skip.row, skip.reason) for skip in result.skipped_rows} == {
        (2, "profile_skip"),
    }
