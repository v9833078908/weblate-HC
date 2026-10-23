#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
A/B the judge payload against the LiteLLM proxy: strict JSON schema on/off.

Stage 3 recorded 19 connection resets, all at 30.4-32.1 s, and 17 successes,
all under 27.8 s. The neighbouring `cathedral` localizer runs the same models
on the same host with 120-420 s timeouts and no reset problem. Its payload
differs from the judge's in exactly two ways: it never sends `response_format`,
and it always sends an explicit `max_tokens`.

This probe holds the model, the prompt and the transport fixed and varies only
those two fields, so a latency difference is attributable to them.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = "https://hcbifrost.herocraft.com/litellm/v1"
ENV = Path(
    "/Users/eli/Documents/PythonProjects/gamedev tools/weblate/deploy/.env.local"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["pass", "reject"]},
                    "errors": {"type": "array", "items": {"type": "string"}},
                    "back_translation": {"type": "string"},
                },
                "required": ["id", "verdict", "errors", "back_translation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}

PROMPT = """You are a localization judge. For each segment, decide pass or reject.
Answer for every segment you were given, exactly once, keyed by its id.
Reply as JSON: {"segments": [{"id": 0, "verdict": "pass", "errors": [],
"back_translation": "..."}]}

Segments (en -> ru):
0. source: "Hold the gate!" | target: "Держите ворота!"
1. source: "Deals 250 damage over 3 seconds." | target: "Наносит 150 урона за 3 секунды."
2. source: "Restore <color=#00FF00>{0}</color> HP." | target: "Восстанавливает <color=#00FF00>{0}</color> HP."
3. source: "Loading" | target: "Загрузка"
4. source: "The ad is not ready. Try again later." | target: "Реклама не готова. Попробуйте позже."
"""


def key() -> str:
    env = os.environ.get("LITELLM_API_KEY", "").strip()
    if env:
        return env
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LITELLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("LITELLM_API_KEY is required")


def arms(model: str) -> list[tuple[str, dict]]:
    """Judge payload, then each cathedral difference in isolation."""
    messages = [{"role": "user", "content": PROMPT}]
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "verdicts", "strict": True, "schema": SCHEMA},
    }
    return [
        (
            "judge-today",
            {"model": model, "messages": messages, "response_format": schema},
        ),
        ("no-schema", {"model": model, "messages": messages}),
        (
            "schema+tokens",
            {
                "model": model,
                "messages": messages,
                "response_format": schema,
                "max_tokens": 8192,
            },
        ),
        ("cathedral", {"model": model, "messages": messages, "max_tokens": 8192}),
    ]


def describe(response: requests.Response) -> str:
    """Summarise how usable a 200 reply is to the judge's strict parser."""
    if response.status_code != 200:
        return "empty"
    body = response.json()
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    try:
        segments = json.loads(text).get("segments", [])
    except (ValueError, AttributeError):
        return f"non-json {len(text)}ch" if text else "empty"
    return f"{len(segments)}/5 segments"


def main() -> None:
    api_key = key()
    models = sys.argv[1:] or [
        "deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash",
        "qwen3.8-max",
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    print(f"{'model':32} {'arm':14} {'status':8} {'elapsed':>9}  parsed")
    for model in models:
        for name, payload in arms(model):
            started = time.monotonic()
            try:
                r = requests.post(
                    f"{BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=180.0,
                )
            except requests.RequestException as exc:
                status, parsed = type(exc).__name__, "-"
            else:
                status, parsed = str(r.status_code), describe(r)
            elapsed = time.monotonic() - started
            print(f"{model:32} {name:14} {status:8} {elapsed:8.1f}s  {parsed}")


if __name__ == "__main__":
    main()
