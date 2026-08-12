# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from loc_kit_ingest.model import Severity
from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import load_profile, parse_profile
from loc_kit_ingest.reader import read_sheets

FIXTURES = Path(__file__).parent / "fixtures"


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
    """
    Rows matching the terms fixture CSV.

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
    assert term.context == '["Characters","Герой"]'
    assert term.values == {"ru": "Герой", "en": "Hero", "ja": "ヒーロー"}
    assert term.source_explanation == "Источник описание"
    assert term.target_explanations["en"] == "Target explanation"
    assert term.term_row == 4 and term.note_rows == (5,)


def test_second_region_parsed(terms_component, terms_rows):
    result = parse_component(terms_component, terms_rows)
    assert len(result.units) == 3
    term = result.units[2]
    assert term.context == '["Weapons","Меч"]'
    assert term.values["en"] == "Sword"
    assert term.section == "Weapons"
    assert term.term_row == 9 and term.note_rows == (10,)


def test_orphan_pair_and_uncovered_nonblank_row_are_errors(terms_component, terms_rows):
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
    # Context is (section, source term). Two records in one section sharing a
    # source term collide; the same source term in another section does not.
    terms_rows[5][0] = "Герой"  # source language is ru; first term was "Герой"
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.duplicate_context" for d in result.diagnostics)


def test_same_source_term_in_distinct_sections_is_not_duplicate(
    terms_component, terms_rows
):
    terms_rows[8][0] = "Герой"  # "Weapons" section reuses the "Characters" term
    result = parse_component(terms_component, terms_rows)
    assert not any(d.code == "tbx.duplicate_context" for d in result.diagnostics)


def test_unknown_extra_nonblank_row_is_error(terms_component, terms_rows):
    terms_rows.append(["unexpected", "data", "here"])
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "grammar.uncovered_row" for d in result.diagnostics)


def test_outer_whitespace_in_term_is_trimmed_with_a_warning(
    terms_component, terms_rows
):
    """TBX cannot store it, so trim and warn instead of refusing the kit."""
    terms_rows[3][0] = " Герой"  # leading space
    result = parse_component(terms_component, terms_rows)

    assert [d for d in result.diagnostics if d.severity is Severity.ERROR] == []
    assert any(
        d.code == "tbx.trimmed_outer_whitespace" and d.severity is Severity.WARNING
        for d in result.diagnostics
    )
    assert result.units[0].values["ru"] == "Герой"


def test_outer_whitespace_in_explanation_is_trimmed_with_a_warning(
    terms_component, terms_rows
):
    """TBX strips it on write, so trim and warn instead of refusing the kit."""
    terms_rows[4][1] = "Target explanation "  # trailing space
    result = parse_component(terms_component, terms_rows)

    assert [d for d in result.diagnostics if d.severity is Severity.ERROR] == []
    trimmed = [
        d for d in result.diagnostics if d.code == "tbx.trimmed_outer_whitespace"
    ]
    assert len(trimmed) == 1
    assert trimmed[0].severity is Severity.WARNING
    term = result.units[0]
    assert term.target_explanations["en"] == "Target explanation"


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
    from dataclasses import replace

    from loc_kit_ingest.profile import PairRegion, PairsGrammar

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
    assert "\n" in term.source_explanation


def test_empty_initial_target_term_is_error(terms_component, terms_rows):
    terms_rows[3][1] = ""  # blank en term (en is an initial target language)
    result = parse_component(terms_component, terms_rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_skip_rows_recorded(terms_component, terms_rows):
    result = parse_component(terms_component, terms_rows)
    assert {(skip.row, skip.reason) for skip in result.skipped_rows} == {
        (2, "profile_skip"),
    }


# ---------------------------------------------------------------------------
# Record-map grammar (profile v2)
# ---------------------------------------------------------------------------


@pytest.fixture
def record_map_component():
    profile_path = FIXTURES / "glossary-record-map.loc-ingest.json"
    return load_profile(profile_path).components[0]


@pytest.fixture
def record_map_rows():
    return read_sheets(FIXTURES / "glossary-record-map.csv")["glossary-record-map"]


def test_record_map_parses_one_row_records(record_map_component, record_map_rows):
    result = parse_component(record_map_component, record_map_rows)
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    assert len(result.units) == 6

    first = result.units[0]
    assert first.section == "Персонажи"
    assert first.values == {"ru": "Герой", "en": "Hero"}
    assert first.source_explanation == "Главный протагонист"
    assert first.target_explanations == {"en": "Main protagonist"}
    assert first.context == '["Персонажи","Герой"]'
    assert first.term_row == 3


def test_record_map_allows_records_without_notes(record_map_component, record_map_rows):
    result = parse_component(record_map_component, record_map_rows)
    settings_term = next(u for u in result.units if u.values["en"] == "Settings")
    assert settings_term.source_explanation == ""
    assert settings_term.target_explanations == {"en": ""}

    quit_term = next(u for u in result.units if u.values["en"] == "Quit")
    assert quit_term.source_explanation == "Закрывает игру"
    assert quit_term.target_explanations == {"en": ""}


def test_record_map_blank_section_is_not_inherited(
    record_map_component, record_map_rows
):
    record_map_rows[3][0] = ""  # blank the domain cell of the second record
    result = parse_component(record_map_component, record_map_rows)
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    term = next(u for u in result.units if u.values["ru"] == "Враг")
    assert term.section == ""
    assert term.context == '["","Враг"]'


def test_record_map_unmapped_populated_cell_is_error(
    record_map_component, record_map_rows
):
    record_map_rows[2].append("approved")  # an undeclared status column
    result = parse_component(record_map_component, record_map_rows)
    assert any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)


def test_record_map_missing_source_term_is_error(record_map_component, record_map_rows):
    record_map_rows[2][1] = ""
    result = parse_component(record_map_component, record_map_rows)
    assert any(d.code == "tbx.missing_term" for d in result.diagnostics)


def test_record_map_missing_target_term_is_error(record_map_component, record_map_rows):
    record_map_rows[2][2] = ""
    result = parse_component(record_map_component, record_map_rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_record_map_outer_whitespace_in_note_is_trimmed_with_a_warning(
    record_map_component, record_map_rows
):
    """TBX strips it on write, so trim and warn instead of refusing the kit."""
    record_map_rows[2][3] = " Главный протагонист"
    result = parse_component(record_map_component, record_map_rows)

    assert [d for d in result.diagnostics if d.severity is Severity.ERROR] == []
    trimmed = [
        d for d in result.diagnostics if d.code == "tbx.trimmed_outer_whitespace"
    ]
    assert len(trimmed) == 1
    assert trimmed[0].severity is Severity.WARNING
    term = next(u for u in result.units if u.values["ru"] == "Герой")
    assert term.source_explanation == "Главный протагонист"


def test_record_map_note_diagnostic_names_the_column(
    record_map_component, record_map_rows
):
    """An unnamed note column must still be identifiable in the diagnostic."""
    record_map_rows[2][3] = " Главный протагонист"
    result = parse_component(record_map_component, record_map_rows)
    message = next(
        d.message
        for d in result.diagnostics
        if d.code == "tbx.trimmed_outer_whitespace"
    )
    assert "column 4" in message


def test_record_map_outer_whitespace_in_term_is_trimmed(
    record_map_component, record_map_rows
):
    """The trimmed term is what TBX stores, so it must also drive identity."""
    record_map_rows[2][1] = " Герой"
    result = parse_component(record_map_component, record_map_rows)

    assert [d for d in result.diagnostics if d.severity is Severity.ERROR] == []
    assert any(
        d.code == "tbx.trimmed_outer_whitespace" and d.severity is Severity.WARNING
        for d in result.diagnostics
    )
    assert result.units[0].values["ru"] == "Герой"
    assert "Герой" in result.units[0].context


def test_record_map_duplicate_context_is_error(record_map_component, record_map_rows):
    record_map_rows[3][0] = "Персонажи"
    record_map_rows[3][1] = "Герой"
    result = parse_component(record_map_component, record_map_rows)
    assert any(d.code == "tbx.duplicate_context" for d in result.diagnostics)


def test_record_map_uncovered_row_is_error(record_map_component, record_map_rows):
    record_map_rows.append(["extra", "данные", "data", "", ""])
    result = parse_component(record_map_component, record_map_rows)
    assert any(d.code == "grammar.uncovered_row" for d in result.diagnostics)


def test_record_map_banner_above_the_header_is_not_scanned(
    record_map_component, record_map_rows
):
    # Rows before the header row are outside the grammar's scan range, so the
    # banner is neither reported as skipped nor as an uncovered row.
    result = parse_component(record_map_component, record_map_rows)
    assert result.skipped_rows == ()
    assert not any(d.code == "grammar.uncovered_row" for d in result.diagnostics)


# --------------------------------------------------------------------------- #
# The same grammar engine, a completely different sheet shape
# --------------------------------------------------------------------------- #


STRIDE_TWO_PROFILE = {
    "schema_version": 2,
    "components": [
        {
            "sheet": "Terms",
            "component": "Terms",
            "kind": "tbx",
            "source_lang": "ru",
            "header_row": 1,
            "languages": [
                {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
                {"code": "ja", "xml_lang": "ja", "column": 2, "header": "ja"},
            ],
            "grammar": {
                "type": "record-map",
                "skip_rows": [],
                "regions": [
                    {
                        "section_row": 2,
                        "section_column": 1,
                        "first_record_row": 3,
                        "last_record_row": 6,
                        "record_stride": 2,
                    },
                    {
                        "section_row": 7,
                        "section_column": 1,
                        "first_record_row": 8,
                        "last_record_row": 9,
                        "record_stride": 2,
                    },
                ],
                "term_row_offset": 0,
                "notes": [
                    {"scope": "source", "column": 1, "header": "ru", "row_offset": 1},
                    {
                        "scope": "target",
                        "language": "ja",
                        "column": 2,
                        "header": "ja",
                        "row_offset": 1,
                    },
                ],
            },
            "initial_target_languages": ["ja"],
        }
    ],
}

STRIDE_TWO_ROWS = [
    ["ru", "ja"],
    ["Персонажи", ""],
    ["Герой", "ヒーロー"],
    ["Главный герой", "主人公"],
    ["Враг", "敵"],
    ["Противник", "対戦相手"],
    ["Оружие", ""],
    ["Меч", "剣"],
    ["Клинок", "刃"],
]


def test_stride_two_caption_regions_parse_with_the_same_engine():
    """record-map is a grammar engine, not a one-spreadsheet reader."""
    component = parse_profile(STRIDE_TWO_PROFILE).components[0]
    result = parse_component(component, [row[:] for row in STRIDE_TWO_ROWS])

    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    assert len(result.units) == 3

    hero = result.units[0]
    assert hero.section == "Персонажи"
    assert hero.values == {"ru": "Герой", "ja": "ヒーロー"}
    assert hero.source_explanation == "Главный герой"
    assert hero.target_explanations == {"ja": "主人公"}
    assert hero.context == '["Персонажи","Герой"]'
    assert hero.term_row == 3 and hero.note_rows == (4, 4)

    sword = result.units[2]
    assert sword.section == "Оружие"
    assert sword.values["ja"] == "剣"


def test_stride_two_cjk_only_terms_keep_their_unicode_context():
    profile = {
        "schema_version": 2,
        "components": [
            {
                **STRIDE_TWO_PROFILE["components"][0],
                "source_lang": "ja",
                "initial_target_languages": ["ru"],
                "grammar": {
                    **STRIDE_TWO_PROFILE["components"][0]["grammar"],
                    "notes": [
                        {
                            "scope": "source",
                            "column": 2,
                            "header": "ja",
                            "row_offset": 1,
                        },
                        {
                            "scope": "target",
                            "language": "ru",
                            "column": 1,
                            "header": "ru",
                            "row_offset": 1,
                        },
                    ],
                },
            }
        ],
    }
    component = parse_profile(profile).components[0]
    result = parse_component(component, [row[:] for row in STRIDE_TWO_ROWS])

    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors == []
    assert result.units[0].context == '["Персонажи","ヒーロー"]'
    assert result.units[0].source_explanation == "主人公"


def test_stride_two_caption_row_extra_cell_is_unmapped():
    rows = [row[:] for row in STRIDE_TWO_ROWS]
    rows[1] = ["Персонажи", "", "unexpected"]
    component = parse_profile(STRIDE_TWO_PROFILE).components[0]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)


def test_stride_two_blank_caption_is_error():
    rows = [row[:] for row in STRIDE_TWO_ROWS]
    rows[1] = ["", ""]
    component = parse_profile(STRIDE_TWO_PROFILE).components[0]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.missing_section" for d in result.diagnostics)


def _flat_record_map(*, ignored_columns=(), allow_empty_targets=False, last_row=2):
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Glossary",
                "component": "Glossary",
                "kind": "tbx",
                "source_lang": "ru",
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": last_row,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "ignored_columns": [
                        {"column": column, "header": header}
                        for column, header in ignored_columns
                    ],
                    "allow_empty_targets": allow_empty_targets,
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    return parse_profile(document).components[0]


IGNORED_ROWS = [["ru", "en", "id"], ["Леон", "Leon", "char_leon"]]


def test_record_map_ignored_column_is_not_unmapped():
    component = _flat_record_map(ignored_columns=((3, "id"),))
    result = parse_component(component, [row[:] for row in IGNORED_ROWS])
    assert [d.code for d in result.diagnostics] == []
    assert len(result.units) == 1
    assert result.units[0].values == {"ru": "Леон", "en": "Leon"}


def test_record_map_populated_column_without_a_declaration_is_unmapped():
    component = _flat_record_map()
    result = parse_component(component, [row[:] for row in IGNORED_ROWS])
    assert any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)


def test_record_map_ignored_column_is_allowed_on_a_caption_row():
    profile = deepcopy(STRIDE_TWO_PROFILE)
    profile["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 3, "header": "id"}
    ]
    rows = [[*row, f"id_{index}"] for index, row in enumerate(STRIDE_TWO_ROWS)]
    component = parse_profile(profile).components[0]
    result = parse_component(component, rows)
    assert not any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)


def test_record_map_blank_target_is_an_error_by_default():
    component = _flat_record_map()
    rows = [["ru", "en"], ["Леон", ""]]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_record_map_blank_target_is_untranslated_when_allowed():
    component = _flat_record_map(allow_empty_targets=True)
    rows = [["ru", "en"], ["Леон", ""]]
    result = parse_component(component, rows)
    assert [d.code for d in result.diagnostics] == []
    assert result.units[0].values == {"ru": "Леон", "en": ""}


def test_record_map_blank_source_stays_an_error_when_targets_are_allowed():
    component = _flat_record_map(allow_empty_targets=True)
    rows = [["ru", "en"], ["", "Leon"]]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.missing_term" for d in result.diagnostics)
