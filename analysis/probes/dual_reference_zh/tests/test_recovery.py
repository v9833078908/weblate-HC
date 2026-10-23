# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fault injection for durable screening execution; never calls model services."""

from __future__ import annotations

import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dual_reference_zh import remote_screening as runner


def test_terminal_failure_is_serializable() -> None:
    def request():
        msg = "synthetic"
        raise runner.RequestError(msg, {"status": 403})

    with pytest.raises(runner.RequestError) as raised:
        runner.run_with_retry(request, label={"arm": "A"})
    result = json.loads(json.dumps(raised.value.as_result()))
    # ruff: ignore[assert]
    assert len(result["attempts"]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("secret"),
        runner.RequestError("invalid JSON", {"status": 200}),
        runner.RequestError("gateway", {"status": 504}),
    ],
)
def test_recoverable_failure_retries(monkeypatch, failure) -> None:
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    calls = []

    def request():
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return {"items": {}}, {"status": 200}

    _, _, attempts = runner.run_with_retry(request, label={"arm": "A"})
    # ruff: ignore[assert]
    assert len(calls) == len(attempts) == 2
    # ruff: ignore[assert]
    assert "secret" not in json.dumps(attempts)


def test_completed_batch_survives_later_interruption(tmp_path) -> None:
    journal = runner.Journal(tmp_path / "events.jsonl")
    runner.run_parallel(
        [{"key": "A:0"}],
        lambda _: {"items": {"one": {"translation": "一"}}},
        journal=journal,
        group="generate",
    )
    # A new reader can recover the completed batch before a final artifact exists.
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    # ruff: ignore[assert]
    assert events[0]["result"]["items"]["one"]["translation"] == "一"
    # ruff: ignore[assert]
    assert events[0]["group"] == "generate"


def test_journal_survives_process_exit(tmp_path) -> None:
    path = tmp_path / "crashed.jsonl"
    script = (
        "import os,sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from dual_reference_zh.remote_screening import Journal; "
        "Journal(Path(sys.argv[2])).append({'kind':'batch','result':{'translation':'saved'}}); "
        "os._exit(17)"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(Path(__file__).resolve().parents[2]),
            str(path),
        ],
        check=False,
    )
    # ruff: ignore[assert]
    assert result.returncode == 17
    # ruff: ignore[assert]
    assert json.loads(path.read_text())["result"]["translation"] == "saved"


def test_final_artifact_is_readable_and_not_overwritten(tmp_path) -> None:
    path = tmp_path / "result.json"
    runner.save_result(path, {"value": "original"})
    with pytest.raises(FileExistsError):
        runner.save_result(path, {"value": "replacement"})
    # ruff: ignore[assert]
    assert json.loads(path.read_text()) == {"value": "original"}


@pytest.mark.parametrize(
    "key,item",
    [
        ("translations", {"translation": ""}),
        ("reviews", {}),
        ("ratings", {"unusable": "false", "severity": "pass", "categories": []}),
        ("ratings", {"unusable": False, "severity": "major", "categories": []}),
    ],
)
def test_malformed_item_is_not_a_success(key, item) -> None:
    with pytest.raises((ValueError, TypeError)):
        runner.parse_items({key: [{"record_id": "one", **item}]}, key, ("one",))
