# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pipeline and CLI tests for loc_kit_ingest."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from translate.storage.tbx import tbxfile

from loc_kit_ingest.cli import main

# ---------------------------------------------------------------------------
# Success tests
# ---------------------------------------------------------------------------


def test_cli_publishes_all_components_and_archives(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
                "--zip",
            ]
        )
        == 0
    )
    assert (output / "Temple" / "ru.po").is_file()
    assert (output / "Temple" / "en.po").is_file()
    assert (output / "Terms" / "tbx" / "en.tbx").is_file()
    assert (output / "Terms" / "tbx" / "ja.tbx").is_file()
    assert (output / "Temple.zip").is_file()
    assert (output / "Terms.zip").is_file()
    assert (output / "report.txt").is_file()


def test_cli_success_without_zip(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "Temple" / "ru.po").is_file()
    assert (output / "Terms" / "tbx" / "en.tbx").is_file()
    assert not (output / "Temple.zip").exists()
    assert (output / "report.txt").is_file()


def test_zip_content_at_root(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    main(
        [
            str(temple_csv),
            str(terms_csv),
            "--profile",
            str(profile),
            "--out",
            str(output),
            "--zip",
        ]
    )
    with zipfile.ZipFile(output / "Temple.zip") as zf:
        names = zf.namelist()
        assert "ru.po" in names
        assert "en.po" in names
    with zipfile.ZipFile(output / "Terms.zip") as zf:
        names = zf.namelist()
        assert "tbx/en.tbx" in names
        assert "tbx/ja.tbx" in names


def test_report_on_stdout(tmp_path, kit_with_profile, capsys):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "report" in captured.out.lower() or "unit" in captured.out.lower()


# ---------------------------------------------------------------------------
# Atomic failure tests
# ---------------------------------------------------------------------------


def test_late_component_error_leaves_no_artifacts(tmp_path, kit_with_profile):
    """Corrupt the profile so the Terms component's header doesn't match."""
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    # Corrupt the Terms header to cause a header.mismatch error
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            '"header": "ru"', '"header": "corrupted"', 1
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_existing_output_is_never_replaced(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_missing_profile_exits_2(tmp_path, kit_with_profile):
    kits, _ = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(tmp_path / "nope.json"),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_invalid_profile_exits_2(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    profile.write_text("{bad json", encoding="utf-8")
    output = tmp_path / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_no_report_on_failure(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    # Corrupt the profile to cause a failure
    profile.write_text("{bad", encoding="utf-8")
    main(
        [
            str(temple_csv),
            str(terms_csv),
            "--profile",
            str(profile),
            "--out",
            str(output),
        ]
    )
    assert not output.exists()


def test_diagnostics_on_stderr(tmp_path, kit_with_profile, capsys):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    profile.write_text("{bad", encoding="utf-8")
    output = tmp_path / "out"
    main(
        [
            str(temple_csv),
            str(terms_csv),
            "--profile",
            str(profile),
            "--out",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    assert captured.err.strip()


def test_missing_output_parent_exits_2(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "nonexistent" / "out"
    assert (
        main(
            [
                str(temple_csv),
                str(terms_csv),
                "--profile",
                str(profile),
                "--out",
                str(output),
            ]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# Profile v2 record-map, end to end
# ---------------------------------------------------------------------------


FIXTURES = Path(__file__).parent / "fixtures"


def _run_record_map(tmp_path, rows=None, profile_document=None):
    """Run the full local pipeline over the record-map fixture."""
    kit = FIXTURES / "glossary-record-map.csv"
    profile_path = FIXTURES / "glossary-record-map.loc-ingest.json"
    if rows is not None:
        kit = tmp_path / "glossary-record-map.csv"
        with kit.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
    if profile_document is not None:
        profile_path = tmp_path / "custom.loc-ingest.json"
        profile_path.write_text(json.dumps(profile_document), encoding="utf-8")
    output = tmp_path / "out"
    code = main([str(kit), "--profile", str(profile_path), "--out", str(output)])
    return code, output


def _fixture_rows():
    with (FIXTURES / "glossary-record-map.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return [row for row in csv.reader(handle)]


def test_record_map_renders_and_parses_back(tmp_path):
    code, output = _run_record_map(tmp_path)
    assert code == 0

    tbx_dir = output / "Glossary" / "tbx"
    assert sorted(p.name for p in tbx_dir.glob("*.tbx")) == ["en.tbx"]
    # The source language never gets its own file.
    assert not (tbx_dir / "ru.tbx").exists()

    store = tbxfile.parsestring((tbx_dir / "en.tbx").read_bytes())
    by_context = {unit.getid(): unit for unit in store.units}
    assert '["Персонажи","Герой"]' in by_context

    hero = by_context['["Персонажи","Герой"]']
    assert hero.source == "Герой"
    assert hero.target == "Hero"
    assert "Главный протагонист" in hero.getnotes("definition")
    assert "Main protagonist" in hero.getnotes("translator")


def test_record_map_language_pair_direction_is_source_to_target(tmp_path):
    _code, output = _run_record_map(tmp_path)
    xml = (output / "Glossary" / "tbx" / "en.tbx").read_text(encoding="utf-8")
    assert 'xml:lang="ru"' in xml
    assert 'xml:lang="en"' in xml


def test_record_map_unmapped_status_column_publishes_nothing(tmp_path):
    rows = _fixture_rows()
    rows[1].append("status")
    rows[2].append("approved")
    code, output = _run_record_map(tmp_path, rows=rows)
    assert code == 2
    assert not output.exists()


def test_record_map_target_note_for_a_non_target_language_publishes_nothing(tmp_path):
    document = json.loads(
        (FIXTURES / "glossary-record-map.loc-ingest.json").read_text(encoding="utf-8")
    )
    document["components"][0]["grammar"]["notes"][1]["language"] = "ru"
    code, output = _run_record_map(tmp_path, profile_document=document)
    assert code == 2
    assert not output.exists()


def test_record_map_populated_footer_publishes_nothing(tmp_path):
    rows = _fixture_rows()
    rows.append(["Всего", "8", "", "", ""])
    code, output = _run_record_map(tmp_path, rows=rows)
    assert code == 2
    assert not output.exists()


def test_record_map_range_not_divisible_by_stride_publishes_nothing(tmp_path):
    document = json.loads(
        (FIXTURES / "glossary-record-map.loc-ingest.json").read_text(encoding="utf-8")
    )
    document["components"][0]["grammar"]["regions"][0]["record_stride"] = 4
    code, output = _run_record_map(tmp_path, profile_document=document)
    assert code == 2
    assert not output.exists()


def test_record_map_missing_target_term_publishes_nothing(tmp_path):
    rows = _fixture_rows()
    rows[2][2] = ""
    code, output = _run_record_map(tmp_path, rows=rows)
    assert code == 2
    assert not output.exists()
