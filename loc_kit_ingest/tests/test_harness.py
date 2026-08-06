# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
import sys
from pathlib import Path


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
