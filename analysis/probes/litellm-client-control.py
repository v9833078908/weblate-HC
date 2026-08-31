#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Every weblate import must follow django.setup(), so it cannot sit at the top.
# ruff: file-ignore[module-import-not-at-top-of-file]

"""
Control: whether ConnectionResetError is a proxy fact or a probe artifact.

The stability harness posts with ``urllib``; the product posts through
``weblate.trans.judge._post_batch`` (httpx, its own timeouts and retry). A
seat must not be dismissed on evidence from a client the product never uses,
so this runs the same models through ``request_verdicts`` and reports the
unparsed rate of the real path, with the transport error surfaced from the
judge's own logger.

Usage:
    LITELLM_API_KEY=... uv run python analysis/probes/litellm-client-control.py [runs] [models...]
"""

from __future__ import annotations

import logging
import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_NAME", "weblate")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from django.conf import settings

from weblate.trans.judge import (
    JudgeRequest,
    request_verdicts,
)

KEY = os.environ.get("LITELLM_API_KEY", "").strip()
if not KEY:
    sys.exit("LITELLM_API_KEY is required")

BASE = "https://hcbifrost.herocraft.com/litellm/v1"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
MODELS = sys.argv[2:] or [
    "deepseek-v4-pro",
    "qwen3.8-max",
    "Kimi K2.7",
    "atlas_glm-5.1",
]

settings.JUDGE_ENABLED = True
settings.JUDGE_API_KEY = KEY
settings.JUDGE_BASE_URL = BASE
settings.JUDGE_BATCH_SIZE = 5
settings.JUDGE_REQUEST_SLEEP = 0.0
settings.JUDGE_REQUEST_DEADLINE = 300.0
settings.JUDGE_REASONING_EFFORT = ""

# Surface the judge's own transport diagnostics instead of guessing.
captured: list[str] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        captured.append(record.getMessage()[:200])


judge_logger = logging.getLogger("weblate.trans.judge")
judge_logger.setLevel(logging.DEBUG)
judge_logger.addHandler(Capture())

# Five units: the production JUDGE_BATCH_SIZE. Segment dropping only showed up
# above a two-unit batch, so a seat must be judged at the real width.
REQUESTS = [
    JudgeRequest(
        unit_key="probe.clean",
        source="Hold the gate!",
        target="Держите ворота!",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    JudgeRequest(
        unit_key="probe.number",
        source="Deals 250 damage over 3 seconds.",
        target="Наносит 150 урона за 3 секунды.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    JudgeRequest(
        unit_key="probe.markup_ok",
        source="Restore <color=#00FF00>{0}</color> HP.",
        target="Восстанавливает <color=#00FF00>{0}</color> HP.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    JudgeRequest(
        unit_key="probe.placeholder_lost",
        source="Reinforcements arrive in {0} turns.",
        target="Подкрепление прибудет через несколько ходов.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    JudgeRequest(
        unit_key="probe.clean_long",
        source="The garrison will hold until dawn, but no longer.",
        target="Гарнизон продержится до рассвета, но не дольше.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
]

# Units whose defect a usable seat must not miss.
MUST_FLAG = {1, 3}

rows: list[tuple[str, int, int, int, int]] = []
for model in MODELS:
    print(f"=== {model} ({RUNS} runs through request_verdicts)")
    ok = bad = missed = false_alarm = 0
    for run in range(1, RUNS + 1):
        captured.clear()
        started = time.time()
        results = request_verdicts(
            REQUESTS, model=model, project_slug="control", project_context=""
        )
        elapsed = time.time() - started
        unparsed = [r for r in results if r.unparsed]
        if unparsed:
            bad += 1
            print(
                f"  run {run}: UNPARSED {len(unparsed)}/{len(results)} ({elapsed:.1f}s)"
            )
            for line in captured[-3:]:
                print(f"       log: {line}")
            continue
        ok += 1
        flagged = {
            index
            for index, result in enumerate(results)
            if result.max_severity in {"minor", "major", "critical"}
        }
        run_missed = sorted(MUST_FLAG - flagged)
        run_false = sorted(flagged - MUST_FLAG)
        missed += len(run_missed)
        false_alarm += len(run_false)
        print(
            f"  run {run}: ok ({elapsed:.1f}s) "
            f"severities={[r.max_severity for r in results]}"
        )
        if run_missed:
            print(
                f"       MISSED defect at {run_missed} "
                f"({[REQUESTS[i].unit_key for i in run_missed]})"
            )
        if run_false:
            print(
                f"       flagged clean unit {run_false} "
                f"({[REQUESTS[i].unit_key for i in run_false]})"
            )
    rows.append((model, ok, bad, missed, false_alarm))
    print()

print("=" * 80)
print(f"{'model':<28} {'clean':>6} {'unparsed':>9} {'missed':>7} {'flagged clean':>14}")
for model, ok, bad, missed, false_alarm in rows:
    print(f"{model:<28} {ok:>6} {bad:>9} {missed:>7} {false_alarm:>14}")
print("=" * 80)
print("missed = a planted defect a seat let through; both counts are over all runs")
