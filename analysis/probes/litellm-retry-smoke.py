#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Every weblate import must follow django.setup(), so it cannot sit at the top.
# ruff: file-ignore[module-import-not-at-top-of-file]

"""
Drive the real judge against the LiteLLM proxy, varying retry and reasoning.

Unlike the sibling probes, this calls production `request_verdicts`, so the
prompt, the batch width and the per-model reasoning mapping are the real ones.

`RETRIES` overrides `JUDGE_TRANSPORT_RETRIES`. `EFFORT` overrides
`JUDGE_REASONING_EFFORT`; the default `none` disables thinking for an admitted
model, and an empty value sends no reasoning field so the model's own default
applies.

Results are recorded in
`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_NAME", "weblate")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from django.conf import settings

from weblate.trans.judge import JudgeRequest, request_verdicts

ENV = Path(
    "/Users/eli/Documents/PythonProjects/gamedev tools/weblate/deploy/.env.local"
)


def key() -> str:
    found = os.environ.get("LITELLM_API_KEY", "").strip()
    if found:
        return found
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LITELLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("LITELLM_API_KEY is required")


REQUESTS = [
    JudgeRequest(
        "probe.clean", "Hold the gate!", "Держите ворота!", "en", "ru", "", "", ()
    ),
    JudgeRequest(
        "probe.number",
        "Deals 250 damage over 3 seconds.",
        "Наносит 150 урона за 3 секунды.",
        "en",
        "ru",
        "",
        "",
        (),
    ),
    JudgeRequest(
        "probe.markup",
        "Restore <color=#00FF00>{0}</color> HP.",
        "Восстанавливает <color=#00FF00>{0}</color> HP.",
        "en",
        "ru",
        "",
        "",
        (),
    ),
    JudgeRequest("probe.load", "Loading", "Загрузка", "en", "ru", "", "", ()),
    JudgeRequest(
        "probe.ad",
        "The ad is not ready. Try again later.",
        "Реклама не готова. Попробуйте позже.",
        "en",
        "ru",
        "",
        "",
        (),
    ),
]


def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    model = sys.argv[2] if len(sys.argv) > 2 else "qwen3.8-max"
    retries = int(os.environ.get("RETRIES", "1"))

    settings.JUDGE_ENABLED = True
    settings.JUDGE_MODEL_SEAT_1 = model
    settings.JUDGE_MODEL_SEAT_2 = model
    settings.JUDGE_API_KEY = key()
    settings.JUDGE_BASE_URL = "https://hcbifrost.herocraft.com/litellm/v1"
    settings.JUDGE_BATCH_SIZE = 5
    settings.JUDGE_REQUEST_SLEEP = 0.0
    settings.JUDGE_REQUEST_DEADLINE = 300.0
    settings.JUDGE_REASONING_EFFORT = os.environ.get("EFFORT", "none")

    unparsed_batches = 0
    for index in range(runs):
        results = request_verdicts(REQUESTS, model=model, project_slug="control")
        failed = sum(r.unparsed for r in results) == len(results)
        unparsed_batches += failed
        print(f"  run {index + 1}/{runs}: {'UNPARSED' if failed else 'ok'}")
    print(
        f"\n{model}, retries={retries}: "
        f"{unparsed_batches}/{runs} batches unparsed "
        f"({unparsed_batches / runs:.0%})"
    )


if __name__ == "__main__":
    main()
