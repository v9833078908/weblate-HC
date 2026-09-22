# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline job inspection for the dual-reference zh-Hans study."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def run_dry_run(study_directory: Path) -> dict[str, object]:
    """
    Summarize scheduled work using local files only.

    This function intentionally has no transport implementation.  It cannot
    send inference requests and is therefore safe before G2.
    """
    manifest = json.loads((study_directory / "manifest.json").read_text("utf-8"))
    gates = manifest["gates"]
    if gates.get("G0") != "closed" or gates.get("G1") != "closed":
        msg = "Dry-run requires closed G0 and G1 gates."
        raise ValueError(msg)

    inputs = _read_jsonl(study_directory / "inputs.jsonl")
    input_ids = {str(record["record_id"]) for record in inputs}
    jobs = _read_jsonl(study_directory / "jobs.jsonl")
    for job in jobs:
        record_id = str(job["record_id"])
        if record_id not in input_ids:
            msg = f"Unknown record_id in job graph: {record_id}"
            raise ValueError(msg)
    summary: dict[str, object] = {
        "job_count": len(jobs),
        "stages": dict(sorted(Counter(job["stage"] for job in jobs).items())),
        "network_requests": 0,
        "projected_tokens": manifest.get("projected_tokens"),
        "budget_cap": manifest.get("budget_cap"),
        "max_attempts_per_job": manifest.get("max_attempts_per_job", 1),
    }
    (study_directory / "dry-run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
