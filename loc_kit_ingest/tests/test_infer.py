# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Regression tests for current keyed PO inference in ``loc_kit_ingest.infer``.

These describe the behavior of column 0 as an unconditional PO key column,
before Task A2 teaches ``infer_profile`` to also recognise it as a language
column in narrow, explicit conditions.
"""

from __future__ import annotations

import pytest

from loc_kit_ingest.infer import InferenceError, infer_component, infer_profile
from loc_kit_ingest.profile import SCHEMA_VERSION


def test_regular_key_header_uses_column_zero_as_key():
    rows = [
        ["key", "ru", "en"],
        ["greeting", "Привет", "Hello"],
        ["farewell", "Пока", "Bye"],
    ]
    document, _notes = infer_component("Sheet1", rows, component="Test")

    assert document["key"] == {"column": 1, "header": "key"}
    assert [lang["code"] for lang in document["languages"]] == ["ru", "en"]
    assert document["source_lang"] == "ru"


def test_id_header_in_key_column_stays_a_string_id_not_a_language():
    # "id" is a registered Indonesian language code, but column 0 is never
    # scanned for language candidates: it is always the PO key column.
    rows = [
        ["id", "ru", "en"],
        ["1001", "Привет", "Hello"],
        ["1002", "Пока", "Bye"],
    ]
    document, _notes = infer_component("Sheet1", rows, component="Test")

    assert document["key"] == {"column": 1, "header": "id"}
    codes = [lang["code"] for lang in document["languages"]]
    assert "id" not in codes
    assert codes == ["ru", "en"]


def test_numeric_only_column_is_demoted_with_a_note():
    rows = [
        ["key", "ru", "en"],
        ["a", "Привет", "1001"],
        ["b", "Пока", "1002"],
    ]
    document, notes = infer_component("Sheet1", rows, component="Test")

    codes = [lang["code"] for lang in document["languages"]]
    assert codes == ["ru"]
    assert any("holds only numbers" in note and "language en" in note for note in notes)


def test_empty_column_is_demoted_with_a_note():
    rows = [
        ["key", "ru", "en"],
        ["a", "Привет", ""],
        ["b", "Пока", ""],
    ]
    document, notes = infer_component("Sheet1", rows, component="Test")

    codes = [lang["code"] for lang in document["languages"]]
    assert codes == ["ru"]
    assert any("is empty; excluded" in note for note in notes)


def test_underfilled_column_is_demoted_with_a_note():
    header = ["key", "ru", "de"]
    data_rows = [[f"k{i}", "Привет", ""] for i in range(4)]
    data_rows[0][2] = "Hallo"  # 1 of 4 rows filled: 25% share
    rows = [header, *data_rows]

    document, notes = infer_component("Sheet1", rows, component="Test", min_fill=50.0)

    assert any("under the 50% threshold" in note and "-> de" in note for note in notes)


def test_caption_row_becomes_an_explicit_skip_rows_entry():
    rows = [
        ["key", "ru", "en"],
        ["", "Russian", "English"],
        ["greeting", "Привет", "Hello"],
    ]
    document, _notes = infer_component("Sheet1", rows, component="Test")

    assert document["grammar"]["skip_rows"] == [2]
    assert document["first_data_row"] == 3


def test_absent_keys_fail():
    rows = [
        ["key", "ru", "en"],
        ["", "Привет", "Hello"],
        ["", "Пока", "Bye"],
    ]
    with pytest.raises(InferenceError, match="no keys in column 1"):
        infer_component("Sheet1", rows, component="Test")


def test_infer_profile_produces_one_v1_component_per_sheet():
    sheet_one = [
        ["key", "ru", "en"],
        ["greeting", "Привет", "Hello"],
    ]
    sheet_two = [
        ["key", "ru", "en"],
        ["farewell", "Пока", "Bye"],
    ]
    document, _notes = infer_profile(
        {"Sheet One": sheet_one, "Sheet Two": sheet_two}, kit_stem="Kit"
    )

    assert document["schema_version"] == SCHEMA_VERSION
    assert len(document["components"]) == 2
    assert {c["sheet"] for c in document["components"]} == {"Sheet One", "Sheet Two"}


def test_keyless_header_promotes_column_zero_to_a_language():
    rows = [
        ["ru", "en", "ja"],
        ["Привет", "Hello", "こんにちは"],
        ["Пока", "Bye", "さようなら"],
    ]
    document, notes = infer_component("Sheet1", rows, component="Test")

    codes = [lang["code"] for lang in document["languages"]]
    assert codes == ["ru", "en", "ja"]
    assert document["key"] == {"column": 1, "header": "ru"}
    assert document["source_lang"] == "ru"
    assert any("is both the PO key and a language column" in note for note in notes)


def test_denylisted_id_header_is_not_promoted_even_with_nonnumeric_data():
    rows = [
        ["id", "ru", "en"],
        ["player_1", "Привет", "Hello"],
        ["player_2", "Пока", "Bye"],
    ]
    document, notes = infer_component("Sheet1", rows, component="Test")

    codes = [lang["code"] for lang in document["languages"]]
    assert "id" not in codes
    assert codes == ["ru", "en"]
    assert document["key"] == {"column": 1, "header": "id"}
    assert not any("is both the PO key and a language column" in note for note in notes)
