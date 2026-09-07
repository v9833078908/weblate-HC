# Parallel judge seats production rollout

**Date:** 2026-09-01. **Status:** deployed and verified in production.

## Outcome

Parallel judge seats are live on production checkout `7ce79a9` with image revision `53f2ac1`. The image contains the concurrency implementation and per-seat request deadlines; the later checkout changes only deployment examples and documentation. The running profiles are:

| Seat | Model route | Resolved upstream | Batch | Absolute deadline |
|---|---|---|---:|---:|
| 1 | `deepseek-v4-pro` | `openai/deepseek-ai/deepseek-v4-pro` | 2 | 120 s |
| 2 | `atlas/qwen3.8-max` | `qwen/qwen3.8-max` | 1 | 150 s |

The strict bounded canary and a separate comparable-size production run both completed with exactly 38 successful attempts, 50 parsed verdicts over 25 units, no retries, and no unparsed result. Their seat windows overlapped for effectively the whole run.

## Deployment corrections

The first production attempts exposed three rollout assumptions, not fan-out defects:

1. Production `.env` did not carry `WEBLATE_JUDGE_BASE_URL`, so the first aliases went to the default OpenRouter endpoint and returned HTTP 400.
2. The stored judge key was an OpenRouter key. Replacing it in memory-safe fashion with the existing LiteLLM key fixed the endpoint authentication.
3. That team key cannot access the custom `weblate-judge-deepseek-v4-pro` alias. LiteLLM explicitly allowed `deepseek-v4-pro`, which resolves to the same DeepSeek V4 Pro upstream. Commit `b123919` updated both deployment templates to the authorized alias.

With endpoint, key, and alias corrected, one seat-2 single-string stream exceeded the global 120-second deadline at 120.095 seconds. The runbook stopped the canary immediately. The run was revoked, its durable `JudgeRun` was marked failed, and no further opportunistic retry was used to claim success.

Commits `53f2ac1` and `7ce79a9` added and deployed independent request deadlines. The supporting 25-request production measurement is recorded in `docs/llm-first/measurements/2026-09-01-judge-seat-deadline.md`: all requests parsed, median 16.740 seconds, nearest-rank p95 100.594 seconds, and maximum 108.473 seconds under a 300-second diagnostic ceiling. The measured maximum plus 25%, rounded up to the next 30 seconds, set seat 2 to 150 seconds. Seat 1 stayed at 120 seconds.

## Strict bounded canary

```text
environment: production
project/component: col4/data
translation_id: 32
translation_path: col4/data/fr
target_language: fr
unit_ids: 14556-14580 (25 exact IDs)
overwrite_existing: false
operator: i.efimov@herocraft.com
approval/date: owner / 2026-09-01
usage_log_start_pk: 5653
task_id: 120a2d85-4958-47f7-9547-289c7ddb92ff
judge_run: d9c92765-d655-4552-998c-b2c4719f8517
start_utc: 2026-09-01T07:25:51Z
```

Preview: 25 matched, 25 processed, zero remaining, zero writable, 38 initial calls.

| Acceptance check | Result |
|---|---|
| Actual attempts | 38/38 planned |
| HTTP/failure kinds | 38 HTTP 200; no failure kind |
| Attempts by seat | seat 1: 13; seat 2: 25 |
| Verdict rows | 50 |
| Distinct units | 25 |
| Verdicts per unit/seat | exactly one |
| Unparsed | 0 |
| Models | 25 `deepseek-v4-pro`; 25 `atlas/qwen3.8-max` |
| Usage rows | 38 (13/25 by seat model) |
| Reported cost | USD 0; proxy returned no cost value |
| Final unit outcomes | 19 pass, 2 minor, 2 major, 2 critical |
| Warnings | none |

Seat 1 ran from `07:25:53.131Z` to `07:42:46.752Z` (1013.621 s). Seat 2 ran from `07:25:53.132Z` to `07:44:39.313Z` (1126.181 s). Combined judging wall time was 1126.182 s, or 52.63% of the two spans summed, below the 75% threshold. The slowest seat-2 batch completed in 131.013 seconds, inside its 150-second absolute deadline. Task progress advanced through all 38 initial callbacks and completed at 100% without a freeze or deadlock.

Result: **strict production canary passed**.

## Comparable production measurement

```text
environment: production
project/component: col4/data
translation_id: 32
translation_path: col4/data/fr
target_language: fr
unit_ids: 14581-14605 (25 exact IDs)
overwrite_existing: false
operator: i.efimov@herocraft.com
approval/date: owner / 2026-09-01
usage_log_start_pk: 5691
task_id: 96832efc-a998-4525-b5bf-d8e6b3acdd76
judge_run: a8cb36ad-a77f-4969-8e55-c4d932c7fd95
start_utc: 2026-09-01T07:47:11Z
```

Preview again reported 25 matched/processed, zero remaining/writable, and 38 initial calls.

| Measurement | Result |
|---|---|
| Actual attempts | 38/38 planned |
| HTTP/failure kinds | 38 HTTP 200; no failure kind |
| Attempts by seat | seat 1: 13; seat 2: 25 |
| Verdict rows | 50 across 25 units |
| Unparsed | 0 |
| Usage rows | 38 (13/25 by seat model) |
| Reported cost | USD 0; proxy returned no cost value |
| Final unit outcomes | 20 pass, 2 minor, 3 major, 0 critical |
| Warnings | none |
| Slowest request | seat 1: 54.548 s; seat 2: 115.005 s |

Seat 1 span was 819.450 seconds; seat 2 span was 819.993 seconds. Combined judging wall time was 819.994 seconds, or 50.02% of the summed spans. This independently reproduces the expected two-seat overlap and stays inside both measured deadlines.

Result: **comparable production measurement passed**.

## Verification

- Full judge regression after the deadline implementation: 312 passed in 272.31 seconds.
- Scoped `prek` passed for Python, YAML, RST, and measurement Markdown; the unrelated pre-existing `rst-bullet-stop` failure at `docs/changes.rst:43` was excluded and remains unchanged.
- Production deploy health: container healthy, login page HTTP 200, checkout `7ce79a9`, code image `53f2ac1`.
- Production Celery active, reserved, and scheduled lists were empty before both launches.
- Automatic deferred draining remains disabled; this rollout did not expand its scope.

## Verdict

All implementation, deployment, bounded-canary, and comparable-measurement gates are closed. Parallel judge seats are production-ready under the recorded 120/150-second per-seat deadlines and the existing 2/1 batch sizes.
