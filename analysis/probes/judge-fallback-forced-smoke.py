#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The probe deliberately reuses the judge's own request_verdicts() entry
# point: a measurement of the fallback wiring is only valid if it goes
# through the same retry/failover decision production does.

r"""
Prove the OpenRouter availability fallback against the live LiteLLM proxy.

Three arms, each judging the same small real scope with both seats and
reporting per-seat attempt count, failure kind, provider and whether the
result parsed. The arms are read-only with respect to translation content and
are **not** read-only with respect to the database. Every arm calls
``request_verdicts(..., persist_attempts=True)`` directly: it writes
``JudgeRequestAttempt`` rows and, for a parsed response with usage, one
``LLMUsageLog`` row. Those rows are the evidence this plan proves, so they are
deliberately kept rather than cleaned up; expect the ledger to grow by one row
per attempt per run. No arm touches a ``Unit`` or writes a ``JudgeVerdict`` -
``request_verdicts()`` has no access to either.

1. **Forced arm**: an invalid primary key maps to ``http-auth``, a permitted
   failover trigger. Expect one primary attempt per seat (``http-auth``,
   ``provider=litellm``) followed by exactly one fallback attempt per seat
   (``provider=openrouter``), and a parsed result for both seats.
2. **Negative arm**: an unknown primary model name maps to ``http-other``,
   which Task 4 deliberately excludes from failover. Expect exactly one
   attempt on seat 1 and an unparsed result; seat 2 is unaffected.
3. **Healthy control arm**: the unmodified live configuration. Expect zero
   fallback attempts and a parsed result for both seats.

Settings are restored after each arm, so a shared container is left exactly
as it was found.

Run inside the dev container, where the primary judge settings already live:

    docker compose exec -T weblate weblate shell -c \\
        "exec(open('/app/src/analysis/probes/judge-fallback-forced-smoke.py').read())"

The fallback endpoint is not assumed to be already configured: this probe
sets it for the duration of the run from ``JUDGE_FALLBACK_PROBE_BASE_URL``,
``JUDGE_FALLBACK_PROBE_API_KEY``, ``JUDGE_FALLBACK_PROBE_MODEL_SEAT_1`` and
``JUDGE_FALLBACK_PROBE_MODEL_SEAT_2`` (all required; never hardcode a key).
Export them in the shell that runs ``docker compose exec``, not inside this
file, so no secret is staged on disk.

This is Task 7 evidence-gathering only. Recording the measurement, and any
Rollout step that changes what a running instance is configured with (the
dev-docker environment block, or later production), needs its own explicit
approval per the repository's AGENTS.md - this probe does not perform either.
"""

from __future__ import annotations

import contextlib
import os

from django.conf import settings
from django.db.models import Max

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation
from weblate.trans.models.judge import JudgeRequestAttempt
from weblate.utils.state import STATE_TRANSLATED

# The canary scope: any translation with at least two translated units works.
TRANSLATION_ID = 182
BATCH_UNITS = 2


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        msg = f"{name} is required (export it before `docker compose exec`)"
        raise SystemExit(msg)
    return value


@contextlib.contextmanager
def _overridden_settings(**overrides: object):
    """Temporarily set settings and restore the previous values afterward."""
    previous = {name: getattr(settings, name) for name in overrides}
    for name, value in overrides.items():
        setattr(settings, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


def _run_arm(label: str, *, seats: tuple[int, ...] = (1, 2)) -> None:
    print(f"=== {label}")
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
    for seat in seats:
        # A max-pk watermark, not a full-table pk set: this runs against a
        # live instance whose ledger is large, and the rows this arm creates
        # are always above the watermark.
        watermark = JudgeRequestAttempt.objects.aggregate(top=Max("pk"))["top"] or 0
        try:
            results = judge.request_verdicts(
                batch,
                seat=seat,
                project_slug=translation.component.project.slug,
                project_context=project_context,
                persist_attempts=True,
            )
        except judge.JudgeError as error:
            print(f"    seat {seat}: raised {error}")
            continue
        attempts = list(
            JudgeRequestAttempt.objects.filter(pk__gt=watermark).order_by("pk")
        )
        for attempt in attempts:
            print(
                f"    seat {seat} attempt: provider={attempt.provider} "
                f"failure_kind={attempt.failure_kind or 'ok'} "
                f"http_status={attempt.http_status} parsed={attempt.parsed}"
            )
        unparsed = any(result.unparsed for result in results)
        served = {(result.served_provider, result.served_model) for result in results}
        print(
            f"    seat {seat} result: unparsed={unparsed} served={served or {'(none)'}}"
        )


def main() -> None:
    fallback_base_url = _required_env("JUDGE_FALLBACK_PROBE_BASE_URL")
    fallback_api_key = _required_env("JUDGE_FALLBACK_PROBE_API_KEY")
    fallback_model_1 = _required_env("JUDGE_FALLBACK_PROBE_MODEL_SEAT_1")
    fallback_model_2 = _required_env("JUDGE_FALLBACK_PROBE_MODEL_SEAT_2")
    fallback_settings = {
        "JUDGE_FALLBACK_BASE_URL": fallback_base_url,
        "JUDGE_FALLBACK_API_KEY": fallback_api_key,
        "JUDGE_FALLBACK_MODEL_SEAT_1": fallback_model_1,
        "JUDGE_FALLBACK_MODEL_SEAT_2": fallback_model_2,
        "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1": "",
        "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2": "",
        "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1": "json_schema",
        "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2": "json_schema",
    }

    # Arm 3 first, on the fully unmodified live configuration: the fallback
    # setting alone must never change a healthy run's call count or provider.
    _run_arm("Arm 3: healthy control, no fallback configured")

    with _overridden_settings(**fallback_settings):
        with _overridden_settings(JUDGE_API_KEY="sk-deliberately-invalid"):
            _run_arm("Arm 1: forced primary http-auth, fallback must complete it")

        with _overridden_settings(
            JUDGE_MODEL_SEAT_1="model-that-does-not-exist-on-this-endpoint"
        ):
            _run_arm("Arm 2: forced primary http-other, must NOT fail over", seats=(1,))

        _run_arm("Arm 3b: healthy control with fallback configured but idle")


main()
