# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from loc_kit_ingest.model import Severity
from loc_kit_ingest.profile import load_profile
from loc_kit_ingest.reader import read_sheets, validate_sheet_headers


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------


def test_csv_preserves_quoted_newlines_bom_and_trailing_spaces(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(b"\xef\xbb\xbfid,ru,en\r\nkey,\" x\r\ny \",text \r\n")
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", " x\r\ny ", "text "]


def test_tsv_uses_tab_delimiter(tmp_path):
    path = tmp_path / "Kit.tsv"
    path.write_bytes(b"id\tru\ten\nkey\tval\ttext")
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", "val", "text"]


def test_unsupported_suffix_raises(tmp_path):
    path = tmp_path / "Kit.txt"
    path.write_text("dummy")
    with pytest.raises(ValueError, match="unsupported"):
        read_sheets(path)


def test_csv_decode_error_raises(tmp_path):
    path = tmp_path / "Bad.csv"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(Exception):
        read_sheets(path)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


@pytest.fixture
def ui_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "UI"
    ws.append(["Key", "ru", "en"])
    ws.append(["row-ignore", "ignore", "ignore"])
    ws.append(["ui_key", "Текст", "Line 1\nLine 2"])
    path = tmp_path / "UI.xlsx"
    wb.save(path)
    return path


def test_xlsx_preserves_multiline_text(ui_xlsx):
    rows = read_sheets(ui_xlsx)["UI"]
    assert rows[2][2] == "Line 1\nLine 2"


def test_corrupt_xlsx_raises(tmp_path):
    path = tmp_path / "Bad.xlsx"
    path.write_bytes(b"not xlsx")
    with pytest.raises(Exception):
        read_sheets(path)


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------


@pytest.fixture
def temple_component(tmp_path):
    profile_path = tmp_path / "temple.loc-ingest.json"
    profile_path.write_text(
        (Path(__file__).parent / "fixtures" / "temple.loc-ingest.json").read_text(
            encoding="utf-8"
        )
    )
    profile = load_profile(profile_path)
    return profile.components[0]


@pytest.fixture
def temple_rows():
    """Rows matching the temple fixture CSV."""
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


def test_header_mismatch_is_an_error(temple_component, temple_rows):
    temple_rows[0][2] = "Russian"
    diagnostics = validate_sheet_headers(temple_component, temple_rows)
    assert [(item.code, item.row) for item in diagnostics] == [("header.mismatch", 1)]


def test_expected_blank_header_passes(temple_component, temple_rows):
    diagnostics = validate_sheet_headers(temple_component, temple_rows)
    assert diagnostics == ()


def test_header_validation_severity_is_error(temple_component, temple_rows):
    temple_rows[0][1] = "Wrong"
    diagnostics = validate_sheet_headers(temple_component, temple_rows)
    assert diagnostics[0].severity is Severity.ERROR


def test_all_headers_match(temple_component, temple_rows):
    diagnostics = validate_sheet_headers(temple_component, temple_rows)
    assert diagnostics == ()
