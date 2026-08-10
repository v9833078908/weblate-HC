# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest

from loc_kit_ingest.infer import InferenceError, infer_glossary_profile
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
