# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest

from loc_kit_ingest.infer import (
    _MAX_REGIONS,
    _MAX_SKIPPED_ROWS,
    InferenceError,
    infer_glossary_profile,
)
from loc_kit_ingest.profile import parse_profile

# Standard language-only kit: header codes, a caption row with language
# names, a section row (imported as an ordinary term), a blank row, a
# partially filled language (ja), and an empty language (fr).
STANDARD = [
    ["ru", "en", "fr", "ja"],
    ["Russian", "English", "french", "japanese"],
    ["Персонажи", "Characters", "", "キャラクター"],
    ["Герой", "Hero", "", "ヒーロー"],
    [],
    ["Меч", "Sword", "", ""],
]


def test_standard_layout_infers_v2_record_map() -> None:
    document, _notes = infer_glossary_profile("Terms", STANDARD, component="terms")
    assert document["schema_version"] == 2
    (comp,) = document["components"]
    assert comp["kind"] == "tbx"
    assert comp["sheet"] == "Terms"
    assert comp["source_lang"] == "ru"
    assert comp["header_row"] == 1
    codes = [lang["code"] for lang in comp["languages"]]
    assert codes == ["ru", "en", "ja"]  # fr пустой - исключён
    grammar = comp["grammar"]
    assert grammar["type"] == "record-map"
    assert grammar["term_row_offset"] == 0
    assert grammar["skip_rows"] == [2]  # caption-строка
    assert grammar["regions"] == [
        {"first_record_row": 3, "last_record_row": 4, "record_stride": 1},
        {"first_record_row": 6, "last_record_row": 6, "record_stride": 1},
    ]


def test_partially_filled_language_is_not_an_initial_target() -> None:
    document, notes = infer_glossary_profile("Terms", STANDARD, component="terms")
    (comp,) = document["components"]
    # ja is empty on row 6 -> recognised but not imported.
    assert comp["initial_target_languages"] == ["en"]
    assert any("ja" in note and "6" in note for note in notes)


def test_row_without_source_term_is_skipped_with_note() -> None:
    rows = [
        ["ru", "en"],
        ["Герой", "Hero"],
        ["", "Stray"],
        ["Меч", "Sword"],
    ]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["skip_rows"] == [3]
    assert comp["grammar"]["regions"] == [
        {"first_record_row": 2, "last_record_row": 2, "record_stride": 1},
        {"first_record_row": 4, "last_record_row": 4, "record_stride": 1},
    ]
    assert any("row 3" in note for note in notes)


def test_populated_non_language_column_is_refused() -> None:
    rows = [
        ["domain", "ru", "en"],
        ["Оружие", "Меч", "Sword"],
    ]
    with pytest.raises(InferenceError, match="column 1"):
        infer_glossary_profile("S", rows, component="s")


def test_no_language_header_is_refused() -> None:
    with pytest.raises(InferenceError):
        infer_glossary_profile("S", [["key", "value"], ["a", "b"]], component="s")


def test_no_fully_filled_target_is_refused() -> None:
    rows = [
        ["ru", "en"],
        ["Герой", "Hero"],
        ["Меч", ""],
    ]
    with pytest.raises(InferenceError, match="target"):
        infer_glossary_profile("S", rows, component="s")


def test_document_survives_parse_profile() -> None:
    """The inferred document must be a valid profile without edits."""
    document, _ = infer_glossary_profile("Terms", STANDARD, component="terms")
    profile = parse_profile(document)
    assert profile.components[0].source_lang == "ru"


def test_wide_blank_row_is_ignored_and_costs_nothing() -> None:
    """
    A blank row may be far wider than the data.

    It must not register as an unmapped populated column, and it must not
    drive the column scan: iterating every column up to the widest row and
    rescanning all rows per column is quadratic in an uploaded file.
    """
    rows = [["ru", "en"], ["Герой", "Hero"], ["Меч", "Sword"]]
    rows.append([""] * 4000)  # blank, but 2000x wider than the content

    document, _notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert [lang["code"] for lang in comp["languages"]] == ["ru", "en"]
    assert comp["grammar"]["regions"] == [
        {"first_record_row": 2, "last_record_row": 3, "record_stride": 1}
    ]


def test_too_many_skipped_rows_is_refused() -> None:
    """Every skipped row emits a note, so the count must be bounded."""
    rows = [["ru", "en"]]
    for index in range(_MAX_SKIPPED_ROWS + 5):
        rows.append([f"термин{index}", f"term{index}"])
        rows.append(["", f"stray{index}"])  # no source term -> skipped

    with pytest.raises(InferenceError, match="fragmented"):
        infer_glossary_profile("S", rows, component="s")


def test_too_many_regions_is_refused() -> None:
    """
    Regions and skip rows are cross-multiplied by the profile validator.

    A sheet that alternates term / blank-separator rows splits into one
    region per term, so the region count has to be bounded too.
    """
    rows = [["ru", "en"]]
    for index in range(_MAX_REGIONS + 5):
        rows.append([f"термин{index}", f"term{index}"])
        rows.append([])  # blank row: splits the region, is not "skipped"

    with pytest.raises(InferenceError, match="fragmented"):
        infer_glossary_profile("S", rows, component="s")


# Term/description kit: every term row is followed by a prose row, exactly
# like the real Heart Abyss terminology export.
DESC_RU = "Леон - дзинко, главный герой игры. " * 5
DESC_EN = "Leon is a jinko, the main character of the game. " * 5
PAIRS = [
    ["ru", "en"],
    ["Russian", "English"],
    ["Леон", "Leon"],
    [DESC_RU, DESC_EN],
    ["Аки", "Aki"],
    [DESC_RU, DESC_EN],
]


def test_pairs_layout_yields_stride_two() -> None:
    document, _notes = infer_glossary_profile("Terms", PAIRS, component="terms")
    grammar = document["components"][0]["grammar"]
    assert grammar["regions"] == [
        {"first_record_row": 3, "last_record_row": 6, "record_stride": 2}
    ]
    assert grammar["term_row_offset"] == 0
    assert grammar["notes"] == [
        {"scope": "source", "column": 1, "header": "ru", "row_offset": 1},
        {
            "scope": "target",
            "column": 2,
            "header": "en",
            "row_offset": 1,
            "language": "en",
        },
    ]


def test_section_row_above_pairs_is_declared() -> None:
    rows = [PAIRS[0], PAIRS[1], ["Персонажи", "Characters"], *PAIRS[2:]]
    document, _notes = infer_glossary_profile("Terms", rows, component="terms")
    grammar = document["components"][0]["grammar"]
    assert grammar["regions"] == [
        {
            "first_record_row": 4,
            "last_record_row": 7,
            "record_stride": 2,
            "section_row": 3,
            "section_column": 1,
        }
    ]


def test_flat_sheet_keeps_stride_one_and_declares_no_notes() -> None:
    document, _notes = infer_glossary_profile("Terms", STANDARD, component="terms")
    grammar = document["components"][0]["grammar"]
    assert all(region["record_stride"] == 1 for region in grammar["regions"])
    assert "notes" not in grammar


def test_long_rows_without_alternation_stay_flat_with_a_note() -> None:
    rows = [
        ["ru", "en"],
        ["Леон", "Leon"],
        [DESC_RU, DESC_EN],
        [DESC_RU, DESC_EN],
        ["Аки", "Aki"],
    ]
    document, notes = infer_glossary_profile("Terms", rows, component="terms")
    grammar = document["components"][0]["grammar"]
    assert grammar["regions"][0]["record_stride"] == 1
    assert any("switch the layout" in note for note in notes)


def test_mixed_layout_blocks_are_refused() -> None:
    rows = [
        ["ru", "en"],
        ["Леон", "Leon"],
        [DESC_RU, DESC_EN],
        [],
        ["Меч", "Sword"],
        ["Щит", "Shield"],
    ]
    with pytest.raises(InferenceError, match="mixes"):
        infer_glossary_profile("Terms", rows, component="terms")


def test_target_language_without_descriptions_is_still_a_target() -> None:
    rows = [
        ["ru", "en", "ja"],
        ["Леон", "Leon", "レオン"],
        [DESC_RU, DESC_EN, ""],
        ["Аки", "Aki", "アキ"],
        [DESC_RU, DESC_EN, ""],
    ]
    document, _notes = infer_glossary_profile("Terms", rows, component="terms")
    comp = document["components"][0]
    assert comp["initial_target_languages"] == ["en", "ja"]


def test_language_with_descriptions_but_missing_terms_is_refused() -> None:
    rows = [
        ["ru", "en", "ja"],
        ["Леон", "Leon", "レオン"],
        [DESC_RU, DESC_EN, DESC_EN],
        ["Аки", "Aki", ""],
        [DESC_RU, DESC_EN, DESC_EN],
    ]
    with pytest.raises(InferenceError, match="descriptions"):
        infer_glossary_profile("Terms", rows, component="terms")


def test_explicit_pairs_layout_overrides_detection() -> None:
    rows = [
        ["ru", "en"],
        ["Леон", "Leon"],
        ["дзинко, герой", "a jinko, the hero"],
    ]
    document, _notes = infer_glossary_profile(
        "Terms", rows, component="terms", layout="pairs"
    )
    grammar = document["components"][0]["grammar"]
    assert grammar["regions"][0]["record_stride"] == 2
    assert len(grammar["notes"]) == 2


def test_explicit_flat_layout_overrides_detection() -> None:
    document, _notes = infer_glossary_profile(
        "Terms", PAIRS, component="terms", layout="flat"
    )
    grammar = document["components"][0]["grammar"]
    assert grammar["regions"][0]["record_stride"] == 1
    assert "notes" not in grammar


def test_explicit_pairs_layout_on_a_lone_row_is_refused() -> None:
    rows = [["ru", "en"], ["Леон", "Leon"]]
    with pytest.raises(InferenceError, match="stands alone"):
        infer_glossary_profile("Terms", rows, component="terms", layout="pairs")


def test_unknown_layout_is_refused() -> None:
    with pytest.raises(InferenceError, match="unknown layout"):
        infer_glossary_profile("Terms", PAIRS, component="terms", layout="guess")


def test_paired_document_survives_parse_profile() -> None:
    document, _notes = infer_glossary_profile("Terms", PAIRS, component="terms")
    profile = parse_profile(document)
    grammar = profile.components[0].grammar
    assert grammar.regions[0].record_stride == 2
    assert [note.row_offset for note in grammar.notes] == [1, 1]
