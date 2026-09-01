# Per-seat judge request deadlines

**Date:** 2026-09-01. **Status:** approved for implementation.

## Problem

The 2026-09-01 production canary for parallel judge seats reached the LiteLLM proxy with the intended models and request profiles. Seat 2 still produced one `deadline` result at batch size 1: the response returned HTTP 200, but its stream exceeded the 120-second absolute request deadline. The canary stopped at the first failed threshold.

A global deadline is the wrong control for this pair. Seat 1 completed its observed batches inside 120 seconds. Raising its ceiling because seat 2 has a heavier latency tail would delay detection of a genuinely stuck seat 1 request. Changing either model would invalidate the existing quality evidence.

## Decision

Add `JUDGE_REQUEST_DEADLINE_SEAT_1` and `JUDGE_REQUEST_DEADLINE_SEAT_2`. Each accepts a positive finite number or `inherit`; the default is `inherit`, which resolves to `JUDGE_REQUEST_DEADLINE`. The resolved value belongs to `JudgeSeatProfile` and is recorded in the redacted run configuration snapshot.

Every bound for a paid request uses the resolved profile value:

- the HTTP client timeout;
- the absolute streaming and non-streaming read deadline;
- the cap on `Retry-After` sleep;
- the lease margin used by a deferred retry for that seat.

The deadline is not added to `profile_fingerprint`. It changes how long the client waits, not the prompt, payload, model, or meaning of a successfully parsed verdict. Raising it therefore must not invalidate already valid cached verdicts.

The legacy no-seat request API keeps the global deadline.

## Validation

Configuration validation rejects a non-positive, non-finite, Boolean, or otherwise non-numeric resolved deadline before a paid request. Tests cover inheritance, distinct seat values, HTTP/read enforcement, audit snapshots, and seat-specific deferral lease margins.

## Production rollout

Before selecting the final value, measure 25 seat-2 single-string requests with a temporary diagnostic ceiling of 300 seconds. The probe uses the production payload and parser but persists no verdict, unit, or usage row. Set the production seat-2 deadline above the measured tail with a documented margin; leave seat 1 at 120 seconds.

Then rerun the strict 25-unit production canary without changing models, prompts, batch sizes, retry policy, or acceptance thresholds. Production remains blocked unless all 38 planned calls complete without retries or failures, all 25 units receive one parsed verdict from each seat, and the overlap/progress thresholds pass.
