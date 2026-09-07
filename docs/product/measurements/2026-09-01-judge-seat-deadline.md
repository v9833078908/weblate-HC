# Judge seat 2 request deadline measurement

**Date:** 2026-09-01. **Status:** passed; authorizes a 150-second seat-2 deadline for the bounded production canary.

## Purpose

The first fully configured production canary for parallel judge seats stopped after one `atlas/qwen3.8-max` single-string stream exceeded the global 120-second absolute deadline. Batch size was already 1. This measurement observes the censored latency tail before changing the production bound.

## Method

The non-persisting probe `analysis/probes/judge-seat-deadline-measurement.py` ran inside the production Weblate container at checkout and image revision `53f2ac1`. It reused production `build_request`, `_payload`, `_post_batch`, and `_parse_reply`, with the configured seat-2 model and request profile. Only the process-local diagnostic deadline was raised to 300 seconds.

Scope:

```text
environment: production
project/component: col4/data
translation_id: 32
translation_path: col4/data/fr
target_language: fr
seat: 2
model: atlas/qwen3.8-max
batch_size: 1
diagnostic_deadline: 300 seconds
unit_ids: 14556,14557,14558,14559,14560,14561,14562,14563,14564,14565,14566,14567,14568,14569,14570,14571,14572,14573,14574,14575,14576,14577,14578,14579,14580
```

Celery active, reserved, and scheduled lists were empty before the probe. The probe sent the 25 units sequentially and stopped on the first non-200, transport failure, or parser failure. It wrote no `JudgeVerdict`, `JudgeRequestAttempt`, `JudgeRun`, unit, or `LLMUsageLog` row and printed no source, target, prompt, response, header, or credential.

## Results

All 25 requests returned HTTP 200 and parsed. Elapsed milliseconds, in unit order:

```text
97212, 100594, 13831, 8645, 8790,
20166, 37640, 28160, 10893, 9927,
22313, 16740, 5034, 7315, 23419,
28479, 9353, 9058, 7618, 18362,
58452, 12535, 108473, 15325, 88461
```

```text
count: 25
minimum: 5.034 s
median: 16.740 s
p95 nearest-rank: 100.594 s
maximum: 108.473 s
HTTP failures: 0
transport failures: 0
parser failures: 0
```

The earlier 120.095-second deadline result is not contradicted: it was censored at the old client ceiling, while this independent sample's longest completed request was 108.473 seconds. Together they show a heavy tail close to 120 seconds.

## Decision

The rollout rule is the slowest successful measured request plus 25%, rounded up to the next 30 seconds:

```text
108.473 s * 1.25 = 135.591 s
rounded deadline = 150 s
```

Seat 1 remains at 120 seconds. Seat 2 receives 150 seconds. The global deadline remains 120 seconds, and both explicit values are absolute ceilings rather than latency targets.

This measurement authorizes only the strict 25-unit production canary. It does not authorize a larger run, a model change, a batch-size increase, a retry-policy change, or automatic deferred draining.
