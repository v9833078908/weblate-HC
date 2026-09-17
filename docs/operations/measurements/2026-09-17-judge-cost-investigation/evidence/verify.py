# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Standalone offline CLI, not an importable Python package.
# ruff: file-ignore[implicit-namespace-package, print]

"""Verify this dated research archive offline; never contact production or an LLM."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANVIL = (
    "4736c44d-67e1-4e0d-8fb6-7f71c80050aa",
    "7567049b-0dae-4bed-8aa8-a0e68b1c208b",
)
AGENTS = (
    "JudgeExecution",
    "JudgePayload",
    "JudgeQualityEvidence",
    "JudgeBestPractices",
    "TMSBenchmark",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load(name: str):
    path = ROOT / name
    data = (
        gzip.decompress(path.read_bytes())
        if name.endswith(".gz")
        else path.read_bytes()
    )
    return json.loads(data)


def main() -> None:
    manifest = load("manifest.json")
    actual = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    expected = {row["path"] for row in manifest["files"]}
    require(actual == expected, "Archive contains missing or unmanifested files")
    for row in manifest["files"]:
        data = (ROOT / row["path"]).read_bytes()
        require(len(data) == row["bytes"], f"Size mismatch: {row['path']}")
        require(
            hashlib.sha256(data).hexdigest() == row["sha256"],
            f"Checksum mismatch: {row['path']}",
        )
    for agent in AGENTS:
        require(bool(load(f"subagents/{agent}.json")), f"Missing result: {agent}")
        text = (ROOT / "subagents" / f"{agent}.md").read_text()
        require("Дополнительные сообщения" in text, f"Missing correspondence: {agent}")
    require(load("subagents/JudgeExecution-initial.json"), "Missing initial conclusion")
    prod = load("evidence/production-usage.json.gz")
    details = load("evidence/overlap-payload.json.gz")
    rates = load("evidence/proxy-rate-metadata.json")
    require(len(prod["runs"]) == 19, "Run count mismatch")
    require(len(prod["attempts"]) == 12329, "Attempt count mismatch")
    require(len(prod["usage"]) == 12070, "Usage count mismatch")
    require(
        len({row["id"] for row in prod["attempts"]}) == len(prod["attempts"]),
        "Duplicate attempt ID",
    )
    require(
        len({row["id"] for row in prod["usage"]}) == len(prod["usage"]),
        "Duplicate usage ID",
    )
    usage = [row for row in prod["usage"] if row["run_id"] in ANVIL]
    require(len(usage) == 5997, "Anvil usage count mismatch")
    require(
        all(row["cost_usd"] is None for row in usage), "Unexpected priced Anvil row"
    )
    require(
        sum(row["total_tokens"] for row in usage) == 16574284,
        "Anvil total tokens mismatch",
    )
    for row in usage:
        require(
            row["prompt_tokens"] + row["completion_tokens"] == row["total_tokens"],
            "Token sum mismatch",
        )
        require(
            0 <= row["cached_tokens"] <= row["prompt_tokens"], "Invalid cache subset"
        )
        require(
            0 <= row["reasoning_tokens"] <= row["completion_tokens"],
            "Invalid reasoning subset",
        )
    covered = {row["request_attempt_id"] for row in usage}
    uncovered = Counter(
        (row["provider"], row["failure_kind"])
        for row in prod["attempts"]
        if row["run_id"] in ANVIL and row["id"] not in covered
    )
    require(
        uncovered == {("litellm", "deadline"): 19, ("openrouter", "http-auth"): 19},
        "Unmetered attempt attribution mismatch",
    )
    require(len(details["overlap"]) == 291, "Overlap count mismatch")
    require(len(details["payload_sample"]) == 24, "Payload sample count mismatch")
    price_map = {row["model"]: row["entries"][0]["prices"] for row in rates}
    for run_id in ANVIL:
        run = next(row for row in prod["runs"] if row["id"] == run_id)
        require(
            [row["entries"][0]["alias_revision"] for row in rates]
            == run["configuration_snapshot"]["alias_revision"],
            "Current rates use a different alias revision",
        )
        estimate = Decimal(0)
        for row in usage:
            if row["run_id"] != run_id:
                continue
            price = price_map[row["model"]]
            estimate += (
                (row["prompt_tokens"] - row["cached_tokens"])
                * Decimal(str(price["input_cost_per_token"]))
                + row["cached_tokens"]
                * Decimal(str(price["cache_read_input_token_cost"]))
                + row["completion_tokens"]
                * Decimal(str(price["output_cost_per_token"]))
            )
        print(f"{run_id}: metered-response estimate USD {estimate:.4f}; NOT an invoice")
    print(f"Verified {len(expected)} files, five agent outputs and numerical evidence.")
    print(
        "Anvil: 16,574,284 recorded tokens; 5,997 unpriced rows; 38 unmetered attempts."
    )


if __name__ == "__main__":
    main()
