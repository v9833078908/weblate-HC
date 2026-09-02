#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Bounded read-only judge canary for the fallback rollout.

Serves phase 3 ("the refactor changed nothing", fallback unconfigured) and
phase 5 ("fallback configured and idle") of
docs/operations/plans/2026-09-02-judge-fallback-and-triage-rollout.md, and the
rollback smoke of Rollout step 6 in
docs/llm-first/plans/2026-09-01-02-judge-openrouter-availability-fallback.md.

It goes through ``run_judge_batch`` rather than ``request_verdicts``, because
the acceptance criteria are about a real run: cache behaviour, per-seat
timing, and which endpoint served each verdict.

Read-only means read-only for **translations**: ``writable_ids=set()`` and
``mutating_repairs=False`` leave every target text and unit state untouched.
It is not read-only for the audit tables, and that is deliberate - the
``JudgeVerdict``, ``JudgeRequestAttempt`` and ``LLMUsageLog`` rows it writes
are the evidence the phase is judged on.

``candidate_severities=()`` is mandatory, not cosmetic. Since the producer
triage merged, ``needs_candidate`` ignores ``writable_ids`` and the remaining
attempt budget (weblate/trans/judge_loop.py:638-642), so a default-severity
canary would call ``repair_targets`` and buy a machine-translation batch per
round for every unresolved critical and major, and store preview candidates in
production. Pinned by weblate/trans/tests/test_judge_loop.py:1443-1445 and
:1465-1482.

``use_cache=False``: a canary that is served from cache measures nothing.
Every selected unit is a paid judge call, which is why the cap is small.

Run it inside the container, so no secret is ever staged on disk:

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/judge-rollout-canary.py').read())"

Environment:

    JUDGE_CANARY_TRANSLATION_ID   required, the translation to sample
    JUDGE_CANARY_UNITS            optional, default 25, hard cap 100
"""

from __future__ import annotations

import math
import os
import statistics
import sys

from django.db.models import Max

from weblate.trans.judge import (
    judge_configuration_snapshot,
    judge_fallback_endpoint,
    resolve_judge_seat_profile,
)
from weblate.trans.judge_loop import run_judge_batch
from weblate.trans.models import Translation
from weblate.trans.models.judge import JudgeRequestAttempt, JudgeVerdict
from weblate.utils.state import STATE_TRANSLATED

HARD_CAP = 100
FIRST_BYTE_P95_LIMIT_MS = 20_000
DEADLINE_HEADROOM = 0.75


def _percentile_95(values: list[int]) -> int | None:
    """Nearest-rank p95; a single sample is its own p95."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def main() -> int:
    raw_id = os.environ.get("JUDGE_CANARY_TRANSLATION_ID", "").strip()
    if not raw_id:
        print("JUDGE_CANARY_TRANSLATION_ID is required")
        return 2
    translation = Translation.objects.get(pk=int(raw_id))

    requested = int(os.environ.get("JUDGE_CANARY_UNITS", "25").strip() or "25")
    cap = min(requested, HARD_CAP)

    units = list(
        translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("pk")[:cap]
    )
    if not units:
        print(f"translation {translation.pk} has no translated units")
        return 2

    fallback = judge_fallback_endpoint()
    snapshot = judge_configuration_snapshot()
    primary_providers = set(snapshot.get("provider") or [])
    print(f"scope: {translation} ({translation.pk}), units={len(units)}")
    print(f"primary providers: {sorted(primary_providers)}")
    print(f"fallback configured: {fallback is not None}")

    deadlines_ms = {
        seat: int(resolve_judge_seat_profile(seat).request_deadline * 1000)
        for seat in (1, 2)
    }
    print(f"per-seat request deadline (ms): {deadlines_ms}")

    # A max-PK watermark, not a full-table set: production ledgers are large.
    attempt_watermark = JudgeRequestAttempt.objects.aggregate(top=Max("pk"))["top"] or 0
    verdict_watermark = JudgeVerdict.objects.aggregate(top=Max("pk"))["top"] or 0

    verdicts = run_judge_batch(
        units,
        writable_ids=set(),
        user=None,
        use_cache=False,
        candidate_severities=(),
        mutating_repairs=False,
    )

    attempts = list(
        JudgeRequestAttempt.objects.filter(pk__gt=attempt_watermark).order_by("pk")
    )
    fresh_verdicts = list(JudgeVerdict.objects.filter(pk__gt=verdict_watermark))

    print(f"\nattempts: {len(attempts)}, verdicts written: {len(fresh_verdicts)}")

    failures = []

    unparsed = [row for row in fresh_verdicts if row.unparsed]
    print(f"terminal unparsed verdicts: {len(unparsed)}")
    if unparsed:
        failures.append(f"{len(unparsed)} unparsed verdict(s)")

    served = sorted({row.judge_provider or "(blank)" for row in fresh_verdicts})
    print(f"serving providers on verdicts: {served}")
    off_primary = [
        row.judge_provider
        for row in fresh_verdicts
        if row.judge_provider and row.judge_provider not in primary_providers
    ]
    if off_primary:
        failures.append(
            f"{len(off_primary)} verdict(s) served off-primary: {off_primary}"
        )

    fallback_attempts = [
        row
        for row in attempts
        if row.provider and row.provider not in primary_providers
    ]
    print(f"fallback attempts: {len(fallback_attempts)}")
    if fallback_attempts:
        failures.append(f"{len(fallback_attempts)} fallback attempt(s)")

    for seat in (1, 2):
        seat_attempts = [row for row in attempts if row.seat == seat]
        first_bytes = [
            row.first_byte_ms for row in seat_attempts if row.first_byte_ms is not None
        ]
        p95 = _percentile_95(first_bytes)
        worst = max(first_bytes) if first_bytes else None
        median = int(statistics.median(first_bytes)) if first_bytes else None
        print(
            f"seat {seat}: attempts={len(seat_attempts)} "
            f"first_byte median={median}ms p95={p95}ms max={worst}ms"
        )
        if p95 is not None and p95 >= FIRST_BYTE_P95_LIMIT_MS:
            failures.append(
                f"seat {seat} first-byte p95 {p95}ms >= {FIRST_BYTE_P95_LIMIT_MS}ms"
            )

        limit = deadlines_ms[seat] * DEADLINE_HEADROOM
        crowded = [
            (row.pk, row.elapsed_ms)
            for row in seat_attempts
            if row.elapsed_ms is not None and row.elapsed_ms > limit
        ]
        if crowded:
            failures.append(
                f"seat {seat}: {len(crowded)} attempt(s) within 25% of the deadline: {crowded}"
            )

    kinds = sorted({row.failure_kind for row in attempts if row.failure_kind})
    print(f"failure kinds observed: {kinds or ['(none)']}")

    print(f"\nrepair statuses: {dict(verdicts.repair_status) or '(none)'}")

    if failures:
        print("\nCANARY-FAILED")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nCANARY-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
