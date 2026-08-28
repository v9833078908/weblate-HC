# LiteLLM Judge review remediation

**Date:** 2026-08-28. **Status:** approved.

## Goal

Correct every confirmed transport, retry, durable-deferral, state-projection,
and configuration failure discovered in the post-implementation review of
`c979bbc`, without deployment, gateway changes, automatic provider fallback,
or model changes. Seat 2 remains `atlas/qwen3.8-max`.

## Architecture

Separate three identities that were conflated in the initial stabilization:

- `repair_attempt` records a translation mutation cycle.
- `request_round` records Judge transport/recovery work within one repair
  attempt and must be unique per run.
- a deferral claim token and attempt-start timestamp own one leased retry.

The client owns bounded transport and protocol behavior. The loop owns request
round persistence, retry budget, current-seat completeness, durable queue
claims, and state projection. Database updates that coordinate workers must be
conditional and atomic.

## Scope

Included:

- finite, capped client configuration and server retry values;
- absolute capability lookup deadlines and bounded response allocation;
- typed malformed-stream failures;
- cache invalidation from newest unparsed evidence;
- atomic adaptive and deferral updates;
- effective adaptive-width reservation, lease-safe draining, retry budget,
  width-one protocol recovery, jitter, and current-snapshot refresh;
- separate request-round persistence from repair attempts;
- all-seat approval gating;
- drain `JudgeRun` records and current verdict state projection;
- historical annotation compatibility and finite environment validation.

Excluded:

- deployment, Docker restart, gateway/HCBifrost modification, credentials,
  model/provider changes, and unrelated refactoring.

## Tasks

### Task 1: Lock down transport bounds

**Files:**
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/settings_docker.py`
- Test: `weblate/trans/tests/test_judge_client.py`

1. Add failing tests for non-finite request deadline, retry budget ratio, and
   `Retry-After`; test that invalid values fail configuration and that a 429
   cannot sleep beyond the bounded cap.
2. Add a monotonic absolute deadline to `/model/info` discovery and make the
   capped reader reject before allocating an over-cap chunk.
3. Convert malformed SSE UTF-8 into `invalid-envelope` or another typed
   protocol result, preserve HTTP metadata, and normalize CR/LF boundaries.
4. Make adaptive-state updates transactional with row locking.
5. Run the affected client tests serially in the dev container.

### Task 2: Introduce explicit request-round coordinates

**Files:**
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0110_judge_request_round.py`
- Modify: `weblate/trans/judge_loop.py`
- Test: `weblate/trans/tests/test_judge_round.py`
- Test: `weblate/trans/tests/test_judge_persistence.py`

1. Write failures proving that retry rounds do not collide with subsequent
   repair attempts and do not appear as repair evidence.
2. Add a nullable/defaulted request-round coordinate to new verdict rows and a
   uniqueness constraint that includes it. Preserve existing repair-attempt
   semantics and historical rows.
3. Allocate request rounds monotonically per `JudgeRun`; persist outer and
   recovery calls with the same repair attempt but distinct request rounds.
4. Update round readers, repair evidence, cache lookup, and run reporting to
   use the new coordinate deliberately rather than infer it from `attempt`.
5. Run round and persistence suites serially.

### Task 3: Make current evidence and approval complete

**Files:**
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/autotranslate.py`
- Test: `weblate/trans/tests/test_judge_round.py`
- Test: `weblate/trans/tests/test_judge_autotranslate.py`

1. Add failures for a newer unparsed seat result following an older parsed
   result and for a single-seat PASS when another configured seat is unparsed.
2. Select the newest row per configured seat before deciding cache/current
   evidence. An unparsed newest row blocks cache reuse and approval.
3. Continue to expose a parsed partial verdict for reports and holds, but
   require parsed current evidence from every configured seat to approve.
4. Make mixed per-seat repair evidence resolve against each seat's own latest
   matching request-round data.
5. Run the round and autotranslate suites serially.

### Task 4: Make deferred retry ownership and pacing durable

**Files:**
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0111_judge_deferral_claims.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/tasks.py`
- Test: `weblate/trans/tests/test_judge_deferrals.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

1. Add concurrency/clock-controlled failures for late results, duplicate
   producers, expired leases, adaptive-width reservation, retry budget, and
   finish-length terminal state.
2. Use transactional row locks and a random claim token. A completion or
   failure update must prove it still owns the claimed row; stale or superseded
   responses do nothing.
3. Reserve capacity from the effective adaptive width. Refuse or renew a
   claim that cannot cover the full request deadline.
4. Spend retry budget before every outer recovery request; add bounded full
   jitter, a deadline check, fresh-unit snapshot/hash validation, and width-one
   isolation for protocol failures.
5. Leave `finish-length` operator-visible and durable rather than silently
   closing it forever. Count only availability failures in the circuit breaker.
6. Run deferral and loop suites serially.

### Task 5: Integrate drain audit and projection safely

**Files:**
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/tasks.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/migrations/0110_judge_request_round.py`
- Test: `weblate/trans/tests/test_judge_deferrals.py`
- Test: `weblate/trans/tests/test_judge_views.py`

1. Add failing tests proving each drain pass creates `JudgeRun`/`JudgeRunUnit`
   audit evidence, reflects `DEFERRED` separately from terminal `UNPARSED`,
   and projects a recovered critical verdict under the normal unit lock.
2. Create one system-actor run per drain pass, persist deferred/recovered
   outcomes, and apply only current, complete verdict transitions. Never modify
   target text or invoke repair during a drain.
3. Close deferrals that leave a permanently disabled/invalid judge scope with
   an explicit terminal reason; retain recoverable configuration failures.
4. Backfill or consistently compare historical target storage hashes so SQL
   status annotations agree with `active_round()` after target reversion.
5. Run deferral, view, and full Judge status suites serially.

### Task 6: Verify and ship

1. Run serial container tests for client, loop, deferral, persistence, round,
   autotranslate, views, core Judge checks, and settings validation.
2. Run `git diff --check` and focused `uv run prek run --files` against only
   changed files. Record unrelated repository-wide hook failures separately.
3. Inspect staged diff. Commit Judge-only files with a Conventional Commit and
   the required Factory co-author trailer. Push `main` to `origin`.

## Acceptance criteria

- Every review finding above has a deterministic regression test.
- No retry can exceed a finite deadline, response cap, configured retry budget,
  or deferral lease.
- Concurrent workers cannot duplicate paid deferred work or overwrite newer
  deferral state.
- Repair attempts remain semantically distinct from transport recovery.
- A unit reaches approved state only with complete parsed current-seat evidence.
- Deferred recovery is auditable and projects a fresh critical hold without
  mutating target text.
- Existing LiteLLM Judge suites pass serially; no deployment occurs.
