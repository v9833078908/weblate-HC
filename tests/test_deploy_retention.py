# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("health", ["healthy", "unhealthy"])
@pytest.mark.parametrize("pin_failure", [False, True])
def test_generated_deploy_script_pins_actual_image_and_never_prunes(
    tmp_path, health, pin_failure
):
    """Execute only the generated shell with fake Docker/git/curl, never SSH."""
    text = (Path(__file__).parents[1] / "deploy/vps.sh").read_text()
    script = text.split('cat > "$tmp" << EOS\nset -euo pipefail\ncd $REPO_DIR', 1)[
        1
    ].split("\nEOS", 1)[0]
    script = "set -euo pipefail\ncd $REPO_DIR" + script
    for key, value in {
        "REPO_DIR": str(tmp_path),
        "WEBLATE_CONTAINER": "fake-weblate",
        "target": "abc",
        "action": "build",
    }.items():
        script = script.replace("$" + key, value)
    script = script.replace("\\$", "$")
    (tmp_path / "deploy").mkdir()
    record = tmp_path / "docker.log"
    stub = r"""
docker() {
    printf '%s\n' "$*" >> "$RECORD"
    case "$*" in
      *'{{.Image}}'*) printf 'sha256:%064d\n' 1 ;;
      tag*) [ "$PIN_FAILURE" = 0 ] ;;
      *Health.Status*) echo "$HEALTH" ;;
      *org.opencontainers.image.revision*) echo abc ;;
      *) : ;;
    esac
}
git() { :; }
curl() { echo 200; }
sleep() { :; }
"""
    result = subprocess.run(
        ["/bin/bash", "-c", stub + script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RECORD": str(record),
            "HEALTH": health,
            "PIN_FAILURE": str(int(pin_failure)),
        },
        check=False,
    )
    calls = record.read_text()
    assert "prune" not in calls
    assert "tag sha256:" + "0" * 63 + "1 hcgameloc:previous" in calls
    if pin_failure:
        assert result.returncode != 0
        assert "compose up" not in calls
    else:
        assert "compose up -d --build weblate" in calls
        assert ("DEPLOY-OK" in result.stdout) == (health == "healthy")
