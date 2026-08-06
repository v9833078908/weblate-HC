# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pipeline and CLI tests for loc_kit_ingest."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

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
    main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output), "--zip"])
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
        main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
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
        main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
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
        main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
        == 2
    )
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_missing_profile_exits_2(tmp_path, kit_with_profile):
    kits, _ = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    assert (
        main([str(temple_csv), str(terms_csv), "--profile", str(tmp_path / "nope.json"), "--out", str(output)])
        == 2
    )
    assert not output.exists()


def test_invalid_profile_exits_2(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    profile.write_text("{bad json", encoding="utf-8")
    output = tmp_path / "out"
    assert (
        main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
        == 2
    )
    assert not output.exists()


def test_no_report_on_failure(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "out"
    # Corrupt the profile to cause a failure
    profile.write_text("{bad", encoding="utf-8")
    main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
    assert not output.exists()


def test_diagnostics_on_stderr(tmp_path, kit_with_profile, capsys):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    profile.write_text("{bad", encoding="utf-8")
    output = tmp_path / "out"
    main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
    captured = capsys.readouterr()
    assert captured.err.strip()


def test_missing_output_parent_exits_2(tmp_path, kit_with_profile):
    kits, profile = kit_with_profile
    temple_csv, terms_csv = kits
    output = tmp_path / "nonexistent" / "out"
    assert (
        main([str(temple_csv), str(terms_csv), "--profile", str(profile), "--out", str(output)])
        == 2
    )
