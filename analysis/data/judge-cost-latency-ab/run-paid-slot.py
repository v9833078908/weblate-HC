#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Temporary paid-slot wrapper for judge-cost-latency-ab (iteration 2 plan,
# section 4). Mirrors the dev-docker container's judge configuration into a
# QA-settings process (settings_test + CI_DB_*), then runs one manifest slot
# through the probe's cmd_execute. The API key is read from the container
# into process memory only; it is never printed or written anywhere.
#
# Usage: uv run python analysis/data/judge-cost-latency-ab/run-paid-slot.py \
#            <manifest.json> <slot-id> [--resume]
#
# Preflight (no POST, no ledger writes): pass slot id "preflight" to resolve
# and print the seat profiles once (alias capability GET only, $0).

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from argparse import Namespace

REPO = pathlib.Path(__file__).resolve().parents[3]

os.environ["DJANGO_SETTINGS_MODULE"] = "weblate.settings_test"
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5434")
os.environ.setdefault("CI_DB_NAME", "weblate")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

_spec = importlib.util.spec_from_file_location(
    "judge_cost_latency_ab",
    REPO / "analysis" / "probes" / "judge-cost-latency-ab.py",
)
probe = importlib.util.module_from_spec(_spec)
sys.modules["judge_cost_latency_ab"] = probe  # dataclasses needs it registered
_spec.loader.exec_module(probe)  # runs django.setup()

from django.conf import settings  # noqa: E402

_key = subprocess.run(
    ["docker", "exec", "dev-docker-weblate-1", "printenv", "WEBLATE_JUDGE_API_KEY"],
    capture_output=True,
    text=True,
    check=True,
    timeout=30,
).stdout.strip()
if not _key:
    msg = "WEBLATE_JUDGE_API_KEY is empty in the container"
    raise SystemExit(msg)

# Mirror the container's judge configuration (iteration-2 plan, section 4).
settings.JUDGE_ENABLED = True
settings.JUDGE_API_KEY = _key  # process memory only, never printed
settings.JUDGE_BASE_URL = "https://hcbifrost.herocraft.com/litellm/v1"
settings.JUDGE_MODEL_SEAT_1 = "deepseek-v4-pro"
settings.JUDGE_MODEL_SEAT_2 = "atlas/qwen3.8-max"
settings.JUDGE_BATCH_SIZE = 2
settings.JUDGE_BATCH_SIZE_SEAT_1 = "inherit"
settings.JUDGE_BATCH_SIZE_SEAT_2 = "inherit"  # the arm override applies 5
settings.JUDGE_STREAM = True
settings.JUDGE_STREAM_SEAT_1 = "inherit"
settings.JUDGE_STREAM_SEAT_2 = "inherit"
settings.JUDGE_REASONING_EFFORT = ""
settings.JUDGE_REASONING_EFFORT_SEAT_1 = ""  # the arm override applies extra_body…
settings.JUDGE_REASONING_EFFORT_SEAT_2 = "extra_body.enable_thinking=false"
settings.JUDGE_RESPONSE_FORMAT = "json_schema"
settings.JUDGE_RESPONSE_FORMAT_SEAT_1 = "inherit"
settings.JUDGE_RESPONSE_FORMAT_SEAT_2 = "inherit"
settings.JUDGE_REQUEST_DEADLINE = 120.0
settings.JUDGE_REQUEST_SLEEP = 0.0
settings.JUDGE_DEFERRAL_ENABLED = False
settings.JUDGE_FALLBACK_BASE_URL = ""
settings.JUDGE_FALLBACK_API_KEY = ""
# Retries/unparsed rounds/temperature/max_tokens stay at production defaults.

_manifest = sys.argv[1]
_slot = sys.argv[2]
_resume = "--resume" in sys.argv[3:]

if _slot == "preflight":
    probe.judge.validate_request_settings()
    for profile in probe.judge.judge_seat_profiles():
        print(probe.json.dumps(probe.profile_snapshot(profile), sort_keys=True))
    sys.exit(0)

sys.exit(
    probe.cmd_execute(
        Namespace(
            manifest=_manifest,
            slot=_slot,
            resume=_resume,
            allow_out_of_order=False,
        )
    )
)
