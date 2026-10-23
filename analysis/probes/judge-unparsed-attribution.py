# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Read-only production probe: attribute every unparsed judge verdict to a cause.

Run inside the production container, which never modifies state:

    B64=$(base64 < analysis/probes/judge-unparsed-attribution.py | tr -d '\n')
    ./deploy/vps.sh ssh "docker exec hcgameloc-weblate-1 weblate shell \
        -c 'import base64; exec(base64.b64decode(\"$B64\").decode())'"

Sections:
1. verdicts by model, parsed versus unparsed, lifetime and per day;
2. every non-parsed attempt grouped by kind, HTTP status, provider, profile;
3. per running profile fingerprint, the parsed and unparsed counts and window;
4. the full record of every ``deadline`` attempt plus its run snapshot.
"""

from __future__ import annotations

from collections import Counter

from django.db.models import Max, Min

from weblate.trans.models.judge import JudgeRequestAttempt, JudgeVerdict

LITELLM_MODELS = {"deepseek-v4-pro", "atlas/qwen3.8-max"}


def coverage() -> None:
    print("=== attempt-row coverage ===")
    print(
        JudgeRequestAttempt.objects.aggregate(
            first=Min("created_at"), last=Max("created_at")
        )
    )
    print("attempts:", JudgeRequestAttempt.objects.count())
    print("verdicts:", JudgeVerdict.objects.count())
    print(
        "verdicts with an attempt row:",
        JudgeVerdict.objects.filter(request_attempt__isnull=False).count(),
    )
    print(
        "verdict window:",
        JudgeVerdict.objects.aggregate(first=Min("timestamp"), last=Max("timestamp")),
    )


def verdicts_by_model() -> None:
    print("\n=== verdicts by model, lifetime ===")
    counts: Counter = Counter()
    for row in JudgeVerdict.objects.values("judge_model", "unparsed"):
        counts[row["judge_model"], row["unparsed"]] += 1
    for model in sorted({model for model, _ in counts}):
        parsed, unparsed = counts[model, False], counts[model, True]
        rate = 100 * unparsed / max(1, parsed + unparsed)
        print(
            f"{model:36s} parsed={parsed:5d} unparsed={unparsed:5d} rate={rate:6.2f}%"
        )

    print("\n=== unparsed verdicts by day and model ===")
    per_day: Counter = Counter()
    for row in JudgeVerdict.objects.filter(unparsed=True).values(
        "timestamp", "judge_model"
    ):
        per_day[row["timestamp"].date(), row["judge_model"]] += 1
    for (day, model), n in sorted(per_day.items()):
        print(f"{day} {model:36s} unparsed={n}")


def failure_causes() -> None:
    print("\n=== every non-parsed attempt, grouped ===")
    counts: Counter = Counter()
    for row in JudgeRequestAttempt.objects.filter(parsed=False).values(
        "model",
        "provider",
        "profile_fingerprint",
        "failure_kind",
        "http_status",
        "transport_succeeded",
        "batch_size",
    ):
        counts[
            row["model"],
            row["provider"] or "-",
            (row["profile_fingerprint"] or "")[:8],
            row["failure_kind"] or "-",
            row["http_status"],
            row["transport_succeeded"],
            row["batch_size"],
        ] += 1
    for key, n in sorted(counts.items(), key=lambda item: -item[1]):
        model, provider, fingerprint, kind, status, transport, batch = key
        print(
            f"n={n:4d} {model:34s} provider={provider:10s} fp={fingerprint:9s} "
            f"kind={kind:16s} http={status} transport_ok={transport} batch={batch}"
        )

    print("\n=== unparsed verdicts by the failure kind of their linked attempt ===")
    linked: Counter = Counter()
    for row in JudgeVerdict.objects.filter(unparsed=True).values(
        "judge_model", "request_attempt__failure_kind", "request_attempt__http_status"
    ):
        linked[
            row["judge_model"],
            row["request_attempt__failure_kind"] or "no-attempt-row",
            row["request_attempt__http_status"],
        ] += 1
    for (model, kind, status), n in sorted(linked.items(), key=lambda item: -item[1]):
        print(f"n={n:4d} {model:36s} kind={kind:18s} http={status}")


def per_profile() -> None:
    print("\n=== per profile fingerprint ===")
    for model in sorted(LITELLM_MODELS):
        fingerprints = {
            (row["profile_fingerprint"] or "")[:8]
            for row in JudgeRequestAttempt.objects.filter(model=model).values(
                "profile_fingerprint"
            )
        }
        for fingerprint in sorted(fingerprints):
            attempts = JudgeRequestAttempt.objects.filter(
                model=model, profile_fingerprint__startswith=fingerprint
            )
            total = attempts.count()
            parsed = attempts.filter(parsed=True).count()
            first = attempts.order_by("created_at").first()
            last = attempts.order_by("-created_at").first()
            print(
                f"{model:22s} fp={fingerprint:9s} total={total:4d} parsed={parsed:4d} "
                f"unparsed={total - parsed:4d} rate={100 * (total - parsed) / total:6.2f}% "
                f"window={first.created_at:%m-%d %H:%M}..{last.created_at:%m-%d %H:%M}"
            )


def deadline_cases() -> None:
    print("\n=== every deadline attempt, in full ===")
    for attempt in JudgeRequestAttempt.objects.filter(failure_kind="deadline").order_by(
        "created_at"
    ):
        for field in (
            "created_at",
            "seat",
            "model",
            "provider",
            "batch_size",
            "elapsed_ms",
            "first_byte_ms",
            "response_bytes",
            "response_shape",
            "http_status",
            "transport_succeeded",
            "attempt",
            "profile_fingerprint",
        ):
            print(f"  {field} = {getattr(attempt, field)}")
        run = attempt.run
        print(f"  run = {run.id if run else None}")
        if run is not None:
            print(f"  run.started = {run.started}")
            snapshot = run.configuration_snapshot or {}
            print(
                f"  run.request_deadline = {snapshot.get('request_deadline', 'ABSENT')}"
            )
            print(f"  run.model = {snapshot.get('model')}")


coverage()
verdicts_by_model()
failure_causes()
per_profile()
deadline_cases()
