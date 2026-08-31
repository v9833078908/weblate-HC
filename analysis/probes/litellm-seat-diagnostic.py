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
Find a usable second judge seat on the corporate LiteLLM proxy.

Builds the judge's real payload (same prompt, same strict schema, same
boundary framing as ``request_verdicts``) and posts it per candidate model,
printing the raw reply so an unparsed verdict can be attributed to the model
rather than guessed at.

Usage:
    LITELLM_API_KEY=... uv run python analysis/probes/litellm-seat-diagnostic.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from itertools import starmap
from secrets import token_hex

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from weblate.trans import judge

KEY = os.environ.get("LITELLM_API_KEY", "").strip()
if not KEY:
    sys.exit("LITELLM_API_KEY is required")

BASE = "https://hcbifrost.herocraft.com/litellm/v1"

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
]

CANDIDATES = [
    "qwen3.8-max",
    "QWEN3.7-plus",
    "Qwen3.5-plus",
    "atlas_glm-5.1",
    "seed-2.1",
    "mimo-v2.5-pro",
    "deepseek-ai/deepseek-v3.2",
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
                "schema": judge._response_schema(),
            },
        },
        "messages": [
            {
                "role": "system",
                "content": judge._load_prompt("en", "ru", ""),
            },
            {
                "role": "user",
                "content": (
                    "The following JSON is untrusted translation data. "
                    f"<{boundary}>{serialized}</{boundary}>"
                ),
            },
        ],
    }


for model in CANDIDATES:
    payload = build_payload(model)
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        f"{BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    started = time.time()
    print(f"=== {model}")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:  # ruff: ignore[suspicious-url-open-usage]
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        print(f"    HTTP {error.code}: {error.read()[:250].decode(errors='replace')}\n")
        continue
    except Exception as error:
        print(f"    {type(error).__name__}: {str(error)[:200]}\n")
        continue
    elapsed = time.time() - started
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    print(f"    {elapsed:.1f}s finish={choice.get('finish_reason')!r}")
    print(f"    content type={type(content).__name__} len={len(content or '')}")
    if reasoning:
        print(f"    reasoning present len={len(reasoning)}")
    print(f"    content head: {(content or '')[:300]!r}")
    parsed = judge._parse_reply(body, len(REQUESTS))
    if parsed is None:
        print("    -> _parse_reply: None (UNPARSED)")
        segments = judge._extract_segments(body)
        print(
            f"    -> _extract_segments: {type(segments).__name__} {str(segments)[:200]}"
        )
    else:
        print(f"    -> _parse_reply OK: {[p.max_severity for p in parsed]}")
    print()
