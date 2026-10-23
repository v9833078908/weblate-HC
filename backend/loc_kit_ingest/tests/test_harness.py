# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: file-ignore[suspicious-subprocess-import]
# - the harness's whole purpose is running the package as a subprocess CLI.

import json
import subprocess
import sys
from pathlib import Path


PRODUCER_FIXTURE_DIR = (
    Path(__file__).parents[2] / "weblate" / "trans" / "tests" / "data" / "producer"
)


def test_module_entrypoint_exists():
    result = subprocess.run(
        [sys.executable, "-m", "loc_kit_ingest", "--help"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--profile" in result.stdout


def test_fixture_content_is_anonymized():
    fixture_text = (Path(__file__).parent / "fixtures" / "temple.csv").read_text(
        encoding="utf-8"
    )
    assert "sample_key" in fixture_text
    assert "Heart Abyss" not in fixture_text


def test_raw_producer_workbook_fails_on_unconfirmed_formula_column(tmp_path):
    """Accepting the raw helper formula before a role decision would be unsafe."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loc_kit_ingest",
            str(PRODUCER_FIXTURE_DIR / "ws-localizations-structure.xlsx"),
            "--source-lang",
            "en",
            "--out",
            str(tmp_path / "raw-output"),
        ],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Units!C2 contains a formula" in result.stderr
    assert not (tmp_path / "raw-output").exists()


def test_prepared_producer_csv_passes_with_source_first_and_no_skips(tmp_path):
    """Leaving a quarantined row in the prepared table must break this contract."""
    output = tmp_path / "prepared-output"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loc_kit_ingest",
            str(PRODUCER_FIXTURE_DIR / "ws-localizations-structure.csv"),
            "--source-lang",
            "en",
            "--include-lang",
            "es",
            "--out",
            str(output),
        ],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    profile = json.loads(
        (output / "profile.loc-ingest.json").read_text(encoding="utf-8")
    )
    component = profile["components"][0]
    assert component["source_lang"] == "en"
    assert component["languages"][0]["code"] == "en"
    assert "4 units, 0 skipped" in (output / "report.txt").read_text(
        encoding="utf-8"
    )
