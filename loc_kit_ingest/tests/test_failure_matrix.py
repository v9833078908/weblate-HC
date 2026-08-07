# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Atomic failure matrix: every fatal contract leaves output untouched."""

from __future__ import annotations

import pytest

from loc_kit_ingest.cli import main


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        ("unknown_profile_field", "profile.unknown_field"),
        ("duplicate_key", "po.duplicate_key"),
        ("orphan_description", "tbx.orphan_description"),
        ("header_mismatch", "header.mismatch"),
        ("missing_sheet", "reader.missing_sheet"),
        ("corrupt_profile", None),
    ],
)
def test_every_fatal_contract_leaves_output_untouched(
    tmp_path, mutation, expected_code, mutated_kit
):
    kits, profile = mutated_kit(mutation)
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
        == 2
    )
    assert not output.exists()


def test_fatal_diagnostics_go_to_stderr_not_stdout(tmp_path, mutated_kit, capsys):
    kits, profile = mutated_kit("duplicate_key")
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
        ]
    )
    captured = capsys.readouterr()
    # stderr has the diagnostic
    assert "po.duplicate_key" in captured.err or "duplicate" in captured.err.lower()
    # stdout is empty on failure
    assert captured.out.strip() == ""


def test_no_report_file_on_failure(tmp_path, mutated_kit):
    kits, profile = mutated_kit("orphan_description")
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
        ]
    )
    assert not (output / "report.txt").exists()


def test_existing_output_preserved_on_failure(tmp_path, kit_with_profile):
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
