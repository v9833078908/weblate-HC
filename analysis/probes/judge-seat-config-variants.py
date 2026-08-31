#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ruff: file-ignore[private-member-access]

"""
Test candidate dev seat configurations against the live LiteLLM proxy.

The 2026-08-31 dev canary failed for two configuration reasons:

* seat 2 sent a top-level ``enable_thinking`` key, which the proxy's model
  group ``atlas/qwen3.8-max`` rejects with HTTP 500;
* seat 1 asked for ``json_object`` instead of ``json_schema``, so the model was
  free to omit ``span`` from an error object and the reply failed
  ``invalid-segment``.

This probe posts one real two-string batch per variant and reports whether the
reply parses with the judge's own parser. It writes nothing.

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/judge-seat-config-variants.py').read())"
"""

from __future__ import annotations

from dataclasses import replace

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

translation = Translation.objects.get(pk=182)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("pk")[:2]
)
batch = [build_request(unit) for unit in units]
project_context = judge_project_context(translation.component.project)

VARIANTS = [
    ("seat1 json_object (canary value)", 1, {"response_format": "json_object"}),
    ("seat1 inherit response_format", 1, {"response_format": "json_schema"}),
    (
        "seat2 enable_thinking (canary value)",
        2,
        {"reasoning": "enable_thinking=false"},
    ),
    ("seat2 inherit reasoning", 2, {"reasoning": ""}),
]

for label, seat, overrides in VARIANTS:
    profile = judge.resolve_judge_seat_profile(seat)
    if overrides:
        profile = replace(profile, **overrides)
    payload = judge._payload(batch, profile, project_context)
    response = judge._post_batch(payload, profile)
    outcome = judge._parse_reply(response.payload, len(batch))
    verdicts = ""
    if outcome.results:
        verdicts = ",".join(result.model_verdict for result in outcome.results)
    print(
        f"{label:38s} model={profile.model:20s} "
        f"rf={profile.response_format:12s} reasoning={profile.reasoning:18s} "
        f"status={response.status_code} transport={response.failure_kind or 'ok':18s} "
        f"parse={(outcome.failure_kind or 'ok'):16s} shape={outcome.shape:12s} "
        f"verdicts={verdicts}"
    )
