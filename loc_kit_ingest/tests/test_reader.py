# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from loc_kit_ingest.model import Severity
from loc_kit_ingest.profile import load_profile, parse_profile
from loc_kit_ingest.reader import read_sheets, validate_sheet_headers

# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------


def test_csv_preserves_quoted_newlines_bom_and_trailing_spaces(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(b'\xef\xbb\xbfid,ru,en\r\nkey," x\r\ny ",text \r\n')
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", " x\r\ny ", "text "]


def test_csv_detects_semicolon_with_bom_quoted_commas_and_newlines(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(
        (
            "\ufeffru;en;notes\n"
            'Леон;Leon;"Имя собственное, мужской род.\nВторая строка."\n'
            "Аки;Aki;\n"
        ).encode()
    )
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["ru", "en", "notes"]
    assert rows[1] == ["Леон", "Leon", "Имя собственное, мужской род.\nВторая строка."]
    assert rows[2] == ["Аки", "Aki", ""]


@pytest.mark.parametrize(
    "banner",
    ["Локализация Heart Abyss", "Heart Abyss, Terms", "UI,,,"],
)
def test_csv_detects_semicolon_under_a_banner_row(tmp_path, banner: str):
    path = tmp_path / "Kit.csv"
    path.write_bytes(f"{banner}\nru;en;notes\nЛеон;Leon;текст\n".encode())
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["ru", "en", "notes"]


def test_csv_keeps_comma_when_a_note_cell_is_full_of_language_codes(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(
        'id,ru,en,notes\nchar_leon,Леон,Leon,"ru; en; fr; de; it; ja"\n'.encode()
    )
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["id", "ru", "en", "notes"]


def test_csv_without_any_language_column_stays_comma(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(b"key,value\na,b\n")
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["key", "value"]


def test_csv_single_column_falls_back_to_comma(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes("ru\nЛеон\nАки\n".encode("utf-8-sig"))
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["ru"]
    assert rows[1] == ["Леон"]


def test_tsv_keeps_tab_even_when_a_cell_holds_semicolons(tmp_path):
    path = tmp_path / "Kit.tsv"
    path.write_bytes(b"id\tru\ten\nkey\ta;b;c\ttext\n")
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", "a;b;c", "text"]


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


@pytest.fixture
def unnamed_description_component():
    """A record-map glossary whose description column has no header cell."""
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Glossary",
                "component": "Glossary",
                "kind": "tbx",
                "source_lang": "en",
                "header_row": 1,
                "languages": [
                    {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
                    {"code": "ru", "xml_lang": "ru", "column": 3, "header": "ru"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": 2,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "notes": [
                        {
                            "scope": "source",
                            "column": 1,
                            "header": "",
                            "row_offset": 0,
                        }
                    ],
                },
                "initial_target_languages": ["ru"],
            }
        ],
    }
    return parse_profile(document).components[0]


def test_unnamed_note_column_matches_a_blank_header_cell(
    unnamed_description_component,
):
    rows = [["", "en", "ru"], ["%SHIP% - a ship", "Ship", "Корабль"]]
    assert validate_sheet_headers(unnamed_description_component, rows) == ()


def test_unnamed_note_column_rejects_a_named_header_cell(
    unnamed_description_component,
):
    rows = [["description", "en", "ru"], ["%SHIP% - a ship", "Ship", "Корабль"]]
    diagnostics = validate_sheet_headers(unnamed_description_component, rows)
    assert [(item.code, item.row) for item in diagnostics] == [("header.mismatch", 1)]


@pytest.fixture
def ignored_id_component():
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
                    {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 3, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": 2,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "ignored_columns": [{"column": 1, "header": "id"}],
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    return parse_profile(document).components[0]


def test_ignored_column_header_matches_the_sheet(ignored_id_component):
    rows = [["id", "ru", "en"], ["char_leon", "Леон", "Leon"]]
    assert validate_sheet_headers(ignored_id_component, rows) == ()


@pytest.mark.parametrize(
    "header",
    [["build", "ru", "en"], ["id", "ru"]],
)
def test_ignored_column_header_mismatch_is_an_error(ignored_id_component, header):
    diagnostics = validate_sheet_headers(ignored_id_component, [header])
    assert [(item.code, item.row) for item in diagnostics] == [("header.mismatch", 1)]
