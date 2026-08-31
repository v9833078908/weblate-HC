#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The probe deliberately reuses the judge's own private helpers: a measurement
# of production parsing is only valid if it builds and parses exactly like
# production.
# ruff: file-ignore[private-member-access]

"""
Diagnose one judge seat's request contract against the live LiteLLM proxy.

The probe judges real units with the seat's own resolved profile, so the
request it sends is the request the judge sends: same prompt, same schema,
same reasoning control, same streaming mode and batch size. A raw replay with
hand-written settings cannot do that, and would blame the model for a
request-contract mismatch.

Each row reports the transport result and the judge parser's own
``_ParseOutcome``. Override a single profile field to test a candidate
configuration; ``VARIANTS`` below carries the settings that produced the
2026-08-31 dev canary failure next to the settings that fix it:

* seat 2 sent a top-level ``enable_thinking`` key, which the proxy's model
  group ``atlas/qwen3.8-max`` rejects with HTTP 500;
* seat 1 asked for ``json_object``, which leaves schema adherence to the model:
  ``segments`` arrived as an object keyed by index instead of an array
  (``invalid-envelope``), or an error object omitted ``span``
  (``invalid-segment``). Adherence is probabilistic, so a single green row here
  does not clear ``json_object``.

Run inside the dev container, where the judge settings and the key already
live, so no secret has to be staged on the host:

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/litellm-seat-diagnostic.py').read())"

It writes nothing: no verdict, no unit and no usage row is persisted.
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx2
from django.conf import settings

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

# The canary scope: col4/data/fr. Any translation with translated units works.
TRANSLATION_ID = 182
BATCH_UNITS = 2

VARIANTS: list[tuple[str, int, dict[str, object]]] = [
    ("seat 1 as configured", 1, {}),
    ("seat 1 json_object (canary value)", 1, {"response_format": "json_object"}),
    ("seat 1 json_schema (inherit)", 1, {"response_format": "json_schema"}),
    ("seat 2 as configured", 2, {}),
    (
        "seat 2 enable_thinking (canary value)",
        2,
        {"reasoning": "enable_thinking=false"},
    ),
    ("seat 2 no reasoning key (inherit)", 2, {"reasoning": ""}),
]


def raw_body(payload: dict, profile: judge.JudgeSeatProfile) -> str:
    """
    Show the proxy's own error text for a request the judge could not parse.

    The payload, endpoint and timeouts are the seat's own, so the request is
    the one the judge sends. It is nevertheless a *second* request: the proxy
    may answer it differently from the attempt above, and it does - a seat
    whose streamed attempt returned 500 has answered this replay with 429 when
    the model group cooled down in between. Read the status printed here as
    belonging to this replay, not to the attempt above.
    """
    url = f"{profile.base_url.rstrip('/')}/chat/completions"
    with httpx2.Client(timeout=judge._request_timeout(profile)) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {settings.JUDGE_API_KEY}"},
            json=payload,
        )
    return f"replay HTTP {response.status_code}: {response.text[:600]}"


def reply_content(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else None


def report_segments(content: str) -> None:
    """Show why a 200 reply failed the parser, without guessing."""
    try:
        decoded = json.loads(content)
    except ValueError as error:
        print(f"        content is not JSON: {error}")
        return
    if not isinstance(decoded, dict):
        print(f"        content is {type(decoded).__name__}, not an object")
        return
    print(f"        content keys: {sorted(decoded)}")
    segments = decoded.get("segments")
    if not isinstance(segments, list):
        print(f"        segments is {type(segments).__name__}: {str(segments)[:300]}")
        return
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            print(f"        [{index}] is {type(segment).__name__}")
            continue
        print(f"        [{index}] keys: {sorted(segment)}")
        for error in segment.get("errors") or ():
            if isinstance(error, dict):
                print(f"        [{index}] error keys: {sorted(error)}")


translation = Translation.objects.get(pk=TRANSLATION_ID)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("pk")[
        :BATCH_UNITS
    ]
)
if len(units) < BATCH_UNITS:
    msg = f"translation {TRANSLATION_ID} has too few translated units"
    raise SystemExit(msg)
batch = [build_request(unit) for unit in units]
project_context = judge_project_context(translation.component.project)

for label, seat, overrides in VARIANTS:
    profile = judge.resolve_judge_seat_profile(seat)
    if overrides:
        profile = replace(profile, **overrides)
    payload = judge._payload(batch, profile, project_context)
    response = judge._post_batch(payload, profile)
    outcome = judge._parse_reply(response.payload, len(batch))
    print(f"=== {label}")
    print(
        f"    model={profile.model} stream={profile.stream} "
        f"response_format={profile.response_format} "
        f"reasoning={profile.reasoning!r} batch={len(batch)}"
    )
    print(
        f"    status={response.status_code} "
        f"transport={response.failure_kind or 'ok'} "
        f"finish={response.finish_reason!r} bytes={response.response_bytes} "
        f"elapsed_ms={response.elapsed_ms}"
    )
    print(
        f"    parse={outcome.failure_kind or 'ok'} shape={outcome.shape!r} "
        f"segments={outcome.segment_count}"
    )
    if outcome.results:
        print(
            "    verdicts: "
            + ", ".join(
                f"{result.model_verdict}/{result.max_severity}"
                for result in outcome.results
            )
        )
        continue
    content = reply_content(response.payload)
    if content is None:
        print(f"    {raw_body(payload, profile)}")
        continue
    report_segments(content)
