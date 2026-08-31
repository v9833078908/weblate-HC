#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The probe deliberately reuses the judge's own private helpers: a measurement
# of production parsing is only valid if it parses exactly like production.
# ruff: file-ignore[private-member-access]

"""
Reproduce the 2026-08-31 dev canary seat failures.

The probe posts one real batch to the live LiteLLM proxy and prints the raw
reply each seat actually sends.

Run inside the dev container, where the judge settings and key already live:

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/judge-seat-parse-diagnostic.py').read())"

It writes nothing: no verdict, no unit, no usage row is persisted.
"""

from __future__ import annotations

import json
import time

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

TRANSLATION_ID = 182
BATCH_UNITS = 2

translation = Translation.objects.get(pk=TRANSLATION_ID)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("pk")[
        :BATCH_UNITS
    ]
)
batch = [build_request(unit) for unit in units]
project_context = judge_project_context(translation.component.project)

for seat in (1, 2):
    profile = judge.resolve_judge_seat_profile(seat)
    payload = judge._payload(batch, profile, project_context)
    started = time.monotonic()
    response = judge._post_batch(payload, profile)
    print("=" * 72)
    print(
        f"seat={seat} model={profile.model} stream={profile.stream} "
        f"batch={len(batch)} status={response.status_code} "
        f"failure={response.failure_kind!r} finish={response.finish_reason!r} "
        f"bytes={response.response_bytes} elapsed_ms={int((time.monotonic() - started) * 1000)}"
    )
    outcome = judge._parse_reply(response.payload, len(batch))
    print(
        f"parse: kind={outcome.failure_kind!r} shape={outcome.shape!r} "
        f"segments={outcome.segment_count}"
    )
    if not isinstance(response.payload, dict):
        print("payload:", repr(response.payload)[:2000])
        continue
    choices = response.payload.get("choices")
    content = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
    if not isinstance(content, str):
        print("payload:", json.dumps(response.payload, ensure_ascii=False)[:2000])
        continue
    print("content head:", content[:600])
    print("content tail:", content[-600:])
    try:
        decoded = json.loads(content)
    except ValueError as error:
        print("content is not JSON:", error)
        continue
    print(
        "top-level keys:",
        sorted(decoded) if isinstance(decoded, dict) else type(decoded),
    )
    if isinstance(decoded, dict) and "segments" in decoded:
        segments = decoded["segments"]
        print("segments type:", type(segments).__name__)
        if isinstance(segments, list):
            print("segments len:", len(segments))
            for index, segment in enumerate(segments):
                if isinstance(segment, dict):
                    print(f"  [{index}] keys:", sorted(segment))
                else:
                    print(f"  [{index}] type:", type(segment).__name__)
        else:
            print("segments value:", json.dumps(segments, ensure_ascii=False)[:1200])
