#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ruff: file-ignore[private-member-access]

"""Measure one production judge seat without persisting application rows."""

from __future__ import annotations

import math
import os
import statistics

from django.conf import settings

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation

TRANSLATION_ID = int(os.environ["PROBE_TRANSLATION_ID"])
UNIT_IDS = [int(value) for value in os.environ["PROBE_UNIT_IDS"].split(",")]
SEAT = int(os.environ.get("PROBE_SEAT", "2"))
DEADLINE = float(os.environ.get("PROBE_DEADLINE", "300"))

if not 10 <= len(UNIT_IDS) <= 25:
    msg = "PROBE_UNIT_IDS must contain 10-25 exact IDs"
    raise SystemExit(msg)
if not math.isfinite(DEADLINE) or DEADLINE <= 0:
    msg = "PROBE_DEADLINE must be positive and finite"
    raise SystemExit(msg)

settings.JUDGE_REQUEST_DEADLINE = DEADLINE
setattr(settings, f"JUDGE_REQUEST_DEADLINE_SEAT_{SEAT}", str(DEADLINE))
translation = Translation.objects.get(pk=TRANSLATION_ID)
units_by_id = {
    unit.pk: unit
    for unit in translation.unit_set.filter(pk__in=UNIT_IDS).order_by("pk")
}
if set(units_by_id) != set(UNIT_IDS):
    msg = "Every probe unit must belong to the selected translation"
    raise SystemExit(msg)
profile = judge.resolve_judge_seat_profile(SEAT)
if getattr(profile, "request_deadline", DEADLINE) != DEADLINE:
    msg = "The resolved diagnostic deadline does not match PROBE_DEADLINE"
    raise SystemExit(msg)
project_context = judge_project_context(translation.component.project)

elapsed: list[int] = []
for index, unit_id in enumerate(UNIT_IDS, start=1):
    request = build_request(units_by_id[unit_id])
    response = judge._post_batch(
        judge._payload([request], profile, project_context),
        profile,
    )
    outcome = judge._parse_reply(response.payload, 1)
    parsed = outcome.results is not None and not outcome.failure_kind
    print(
        f"sample={index}/{len(UNIT_IDS)} unit={unit_id} "
        f"status={response.status_code} transport={response.failure_kind or 'ok'} "
        f"parse={outcome.failure_kind or 'ok'} elapsed_ms={response.elapsed_ms}"
    )
    if response.status_code != 200 or response.failure_kind or not parsed:
        msg = "probe stopped on the first failed request"
        raise SystemExit(msg)
    elapsed.append(response.elapsed_ms)

ordered = sorted(elapsed)
nearest_rank_95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
print(f"count={len(ordered)}")
print(f"min_ms={ordered[0]}")
print(f"median_ms={statistics.median(ordered):.0f}")
print(f"p95_ms={nearest_rank_95}")
print(f"max_ms={ordered[-1]}")
