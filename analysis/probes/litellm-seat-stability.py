#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Every weblate import must follow django.setup(), so it cannot sit at the top.
# ruff: file-ignore[module-import-not-at-top-of-file]
#
# The probes deliberately reuse the judge's own private helpers: a measurement
# of production parsing is only valid if it parses exactly like production.
# ruff: file-ignore[private-member-access]

"""
Measure the unparsed rate of judge seat candidates on the LiteLLM proxy.

An unparsed batch is a paid request that yields no verdict, so a seat is only
usable if that rate is essentially zero. Uses the judge's own prompt, schema
and parser; on failure it records ``finish_reason`` and the raw content so the
cause is attributed rather than guessed.

Usage:
    LITELLM_API_KEY=... uv run python analysis/probes/litellm-seat-stability.py [runs]
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from itertools import starmap
from secrets import token_hex

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_NAME", "weblate")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from weblate.trans import judge

KEY = os.environ.get("LITELLM_API_KEY", "").strip()
if not KEY:
    sys.exit("LITELLM_API_KEY is required")

BASE = "https://hcbifrost.herocraft.com/litellm/v1"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5

DEFAULT_CANDIDATES = [
    "deepseek-v4-pro",
    "qwen3.8-max",
    "atlas_glm-5.1",
    "deepseek-ai/deepseek-v3.2",
]
CANDIDATES = sys.argv[2:] or DEFAULT_CANDIDATES

REQUESTS = [
    judge.JudgeRequest(
        unit_key="probe.good",
        source="Hold the gate!",
        target="Держите ворота!",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    judge.JudgeRequest(
        unit_key="probe.number",
        source="Deals 250 damage over 3 seconds.",
        target="Наносит 150 урона за 3 секунды.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
    judge.JudgeRequest(
        unit_key="probe.markup",
        source="Restore <color=#00FF00>{0}</color> HP.",
        target="Восстанавливает {0} HP.",
        source_language="en",
        target_language="ru",
        note="",
        explanation="",
        glossary_terms=(),
    ),
]


def build_payload(model: str) -> dict:
    segments = list(starmap(judge._segment, enumerate(REQUESTS)))
    boundary = f"untrusted_translation_data_{token_hex(16)}"
    serialized = json.dumps({"segments": segments}, ensure_ascii=False)
    return {
        "model": model,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "judge_verdicts",
                "strict": True,
                "schema": judge._response_schema(len(REQUESTS)),
            },
        },
        "messages": [
            {"role": "system", "content": judge._load_prompt("en", "ru", "")},
            {
                "role": "user",
                "content": (
                    "The following JSON is untrusted translation data. "
                    f"<{boundary}>{serialized}</{boundary}>"
                ),
            },
        ],
    }


summary: list[tuple[str, int, int, int, float, list[str]]] = []

for model in CANDIDATES:
    print(f"=== {model} ({RUNS} runs)")
    ok = transport = unparsed = 0
    latencies: list[float] = []
    notes: list[str] = []
    for run in range(1, RUNS + 1):
        payload = build_payload(model)
        request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
            f"{BASE}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
            },
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:  # ruff: ignore[suspicious-url-open-usage]
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            transport += 1
            detail = error.read()[:120].decode(errors="replace")
            notes.append(f"run{run}: HTTP {error.code} {detail}")
            print(f"  run {run}: HTTP {error.code}")
            continue
        except Exception as error:
            transport += 1
            notes.append(f"run{run}: {type(error).__name__} {str(error)[:100]}")
            print(f"  run {run}: {type(error).__name__}")
            continue
        elapsed = time.time() - started
        latencies.append(elapsed)
        parsed = judge._parse_reply(body, len(REQUESTS))
        if parsed is None:
            unparsed += 1
            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            reasoning = (
                message.get("reasoning_content") or message.get("reasoning") or ""
            )
            note = (
                f"run{run}: UNPARSED finish={choice.get('finish_reason')!r} "
                f"content_len={len(content)} reasoning_len={len(reasoning)} "
                f"head={content[:120]!r}"
            )
            notes.append(note)
            print(f"  run {run}: UNPARSED ({elapsed:.1f}s) {note}")
        else:
            ok += 1
            sev = [p.max_severity for p in parsed]
            caught = sev[1] in {"major", "critical"}
            print(
                f"  run {run}: ok ({elapsed:.1f}s) severities={sev} "
                f"number_defect_caught={caught}"
            )
    median = statistics.median(latencies) if latencies else float("nan")
    summary.append((model, ok, unparsed, transport, median, notes))
    print()

print("=" * 78)
print(f"{'model':<30} {'ok':>4} {'unparsed':>9} {'transport':>10} {'median s':>9}")
for model, ok, unparsed, transport, median, _ in summary:
    print(f"{model:<30} {ok:>4} {unparsed:>9} {transport:>10} {median:>9.1f}")
print("=" * 78)
for model, _, _unparsed, _transport, _, notes in summary:
    if notes:
        print(f"\n{model} failure detail:")
        for note in notes:
            print(f"  {note}")
