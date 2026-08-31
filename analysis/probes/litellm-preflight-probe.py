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
Live preflight for the LiteLLM provider and the configurable judge endpoint.

Realises the local arm of plan Task 7.

Sends real, paid requests to the corporate proxy. Exercises the merged code,
not a reimplementation:

* ``RoutedLiteLLMTranslation`` - default base URL, per-language route
  resolution, and the chat payload it builds; that exact payload is then
  posted to the proxy so the provider contract is checked end to end.
* ``weblate.trans.judge`` - ``get_judge_base_url`` / provider identity, the
  reasoning-effort gate, and ``request_verdicts`` across both seats.

Usage:
    LITELLM_API_KEY=... uv run python analysis/probes/litellm-preflight-probe.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from django.conf import settings
from weblate_customization.machinery import (
    LITELLM_DEFAULT_BASE_URL,
    RoutedLiteLLMTranslation,
)

from weblate.trans import (
    judge as judge_mod,
)
from weblate.trans.judge import (
    JudgeError,
    JudgeRequest,
    get_judge_base_url,
    get_judge_chat_completions_url,
    request_verdicts,
    validate_request_settings,
)

KEY = os.environ.get("LITELLM_API_KEY", "").strip()
if not KEY:
    sys.exit("LITELLM_API_KEY is required")

# The configuration under test, not a preference. Seat 2 defaults to an
# available qwen candidate because the configured
# `qwen/qwen3-235b-a22b-2507` does not exist on this proxy at all. Overriding
# these is for an R3 eval; plan Task 7.4 forbids substituting a seat to make
# the preflight pass.
SEAT_1 = os.environ.get("PREFLIGHT_SEAT_1", "deepseek-v4-pro")
SEAT_2 = os.environ.get("PREFLIGHT_SEAT_2", "qwen3.8-max")
ROUTING = {"ja": SEAT_1, "de": SEAT_2, "*": SEAT_1}

MARK_OK = "PASS"
MARK_BAD = "FAIL"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{MARK_OK if ok else MARK_BAD}] {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def post(url: str, payload: dict, timeout: int = 120) -> tuple[int, dict | str]:
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # ruff: ignore[suspicious-url-open-usage]
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:400].decode(errors="replace")


print("=" * 72)
print("ARM 1 - RoutedLiteLLMTranslation (machinery)")
print("=" * 72)

machine = RoutedLiteLLMTranslation(
    {
        "key": KEY,
        "routing": ROUTING,
        "persona": "You translate a grim fantasy strategy game.",
        "style": "Keep it terse and punchy.",
    }
)

check(
    "default base URL is the corporate proxy",
    machine.get_runtime_base_url() == LITELLM_DEFAULT_BASE_URL,
    machine.get_runtime_base_url(),
)
check("service slug is litellm", machine.get_identifier() == "litellm")
check("display name is LiteLLM", machine.name == "LiteLLM")
check(
    "trusted error host is the proxy",
    machine.trusted_error_hosts == {"hcbifrost.herocraft.com"},
)
check("exact route resolves", machine.resolve_model("ja") == SEAT_1)
check("regional route falls back to base", machine.resolve_model("de_AT") == SEAT_2)
check("unknown language uses * fallback", machine.resolve_model("ko") == SEAT_1)
check("request timeout stays under the gateway limit", machine.request_timeout == 55)

payload = machine.get_chat_payload(
    SEAT_1,
    "Translate the game string from English into Russian. "
    "Reply with the translation only.",
    "Hold the gate!",
    "",
    "",
)
check(
    "payload drops the OpenRouter-only provider field",
    "provider" not in payload,
    f"keys={sorted(payload)}",
)
check("payload carries the resolved model", payload.get("model") == SEAT_1)

url = f"{LITELLM_DEFAULT_BASE_URL}/chat/completions"
started = time.time()
status, body = post(url, payload)
elapsed = time.time() - started
ok = status == 200 and isinstance(body, dict)
content = ""
if ok:
    content = (body["choices"][0]["message"].get("content") or "").strip()
check(
    "the machinery payload is accepted by the live proxy",
    ok and bool(content),
    f"http={status} {elapsed:.1f}s content={content[:80]!r}"
    if ok
    else f"http={status} {str(body)[:200]}",
)
if ok:
    usage = body.get("usage") or {}
    print(
        f"       usage: prompt={usage.get('prompt_tokens')} "
        f"completion={usage.get('completion_tokens')} "
        f"total={usage.get('total_tokens')} cost={usage.get('cost')}"
    )

print()
print("=" * 72)
print("ARM 2 - judge endpoint configuration")
print("=" * 72)

settings.JUDGE_ENABLED = True
settings.JUDGE_API_KEY = KEY
settings.JUDGE_BASE_URL = LITELLM_DEFAULT_BASE_URL
settings.JUDGE_MODEL_SEAT_1 = SEAT_1
settings.JUDGE_MODEL_SEAT_2 = SEAT_2
settings.JUDGE_BATCH_SIZE = 5
settings.JUDGE_REQUEST_SLEEP = 0.0
settings.JUDGE_REQUEST_DEADLINE = 300.0
settings.JUDGE_REASONING_EFFORT = ""

check(
    "base URL comes from configuration",
    get_judge_base_url() == LITELLM_DEFAULT_BASE_URL,
)
check(
    "chat-completions URL is derived, not hardcoded",
    get_judge_chat_completions_url() == f"{LITELLM_DEFAULT_BASE_URL}/chat/completions",
    get_judge_chat_completions_url(),
)
check(
    "provider identity resolves from the hostname",
    judge_mod._judge_provider(LITELLM_DEFAULT_BASE_URL) == "litellm",
)
check(
    "OpenRouter still resolves to openrouter",
    judge_mod._judge_provider("https://openrouter.ai/api/v1") == "openrouter",
)

settings.JUDGE_REASONING_EFFORT = "high"
gated = False
try:
    validate_request_settings()
except JudgeError:
    gated = True
check("reasoning effort is refused on LiteLLM", gated)
settings.JUDGE_REASONING_EFFORT = ""
try:
    validate_request_settings()
    accepted = True
except JudgeError as error:
    accepted = False
    print(f"       unexpected gate: {error}")
check("clean LiteLLM configuration passes the gate", accepted)

print()
print("=" * 72)
print("ARM 3 - live two-seat judge batch")
print("=" * 72)

requests = [
    JudgeRequest(
        unit_key="probe.good",
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
]

for seat, model in ((1, SEAT_1), (2, SEAT_2)):
    print(f"-- seat {seat}: {model}")
    started = time.time()
    try:
        results = request_verdicts(
            requests, model=model, project_slug="preflight", project_context=""
        )
    except JudgeError as error:
        check(f"seat {seat} returned verdicts", False, f"JudgeError: {error}")
        continue
    elapsed = time.time() - started
    check(
        f"seat {seat} returned one verdict per request",
        len(results) == len(requests),
        f"{len(results)} in {elapsed:.1f}s",
    )
    parsed = [r for r in results if not r.unparsed]
    check(f"seat {seat} produced parseable verdicts", len(parsed) == len(requests))
    for req, res in zip(requests, results, strict=False):
        print(
            f"       {req.unit_key}: severity={res.max_severity} "
            f"unparsed={res.unparsed} errors={len(res.errors)} "
            f"back={res.back_translation[:60]!r}"
        )
    planted = results[1] if len(results) > 1 else None
    if planted is not None and not planted.unparsed:
        check(
            f"seat {seat} caught the planted 250->150 number defect",
            planted.max_severity in {"major", "critical"},
            f"severity={planted.max_severity}",
        )

print()
print("=" * 72)
print(f"RESULT: {'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
for name in failures:
    print(f"  FAILED: {name}")
print("=" * 72)
sys.exit(1 if failures else 0)
