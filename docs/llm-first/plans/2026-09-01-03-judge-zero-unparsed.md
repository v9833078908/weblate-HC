# Prevent false and terminal retriable judge failures

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** A producer-launched judge run must not turn an endpoint refusal into
an opinion about a translation, and every nonterminal unparsed result must
remain durably queued until a later drain can judge it. This is deliberately
not a promise that the database contains zero historical `unparsed` rows:
a timed-out live request records one provisional unparsed result before its
`JudgeDeferral` is drained, and `finish-length` remains a terminal case. It is
a plan for no **false** unparsed verdict and no silently terminal retriable
failure.

1. **A refused request must never become a verdict.** No `JudgeVerdict` may be
   written for the refused request. A permanent refusal stops the run immediately
   with an operator-visible error, exactly as `http-auth` already does, instead
   of being paid for once per batch and recorded as an opinion. Tasks 1-5.
2. **A nonterminal unparsed result must be retried durably.** After the refusal
   path is closed, enable the existing `JudgeDeferral` queue. It queues every
   unparsed result except `finish-length`, including deadline, transport,
   rate-limit, server and parser failures. The producer run honestly records its
   provisional unparsed result; the queue keeps the missing seat retriable until
   a later drain writes a parsed result or the operator investigates a `slow`
   record. Tasks 6-7.

**Architecture:** Reuse the closed `failure_kind` taxonomy, the attempt ledger,
the retry budget, the adaptive batch state and the existing `JudgeDeferral`
queue with its drain. Split the one kind that today conflates two unrelated
things - a request the endpoint will refuse every time, and a request that is
merely too large - and route the first to the existing fail-fast path. Before
enabling the queue, add bounded cleanup for closed queue rows and document every
operator setting.

**Tech stack:** Python 3.14, Django settings and migrations, Celery periodic
tasks, httpx2 streaming, pytest.

**Status:** Tasks 1-6 implemented and verified on branch `feat/judge-zero-unparsed`
(`0f83dc5`..`4f47faa`); see
`docs/llm-first/measurements/2026-09-01-05-judge-refused-request-fail-fast.md`.
The Task 5 dev-container arms and Task 7 change a running instance and still
need separate explicit approval.

**Evidence this is needed:**
`docs/llm-first/measurements/2026-09-01-04-judge-unparsed-attribution.md`
identifies HTTP 400/401 refusals as the dominant diagnosable incident and records
the 05:59 run (`48bfbd72`) completing after 50 refused batches. Its aggregate
mixes attempt and verdict facts: it also records that the 401 attempt at 06:05
created no verdict. Task 4 therefore derives the cleanup count from its guarded
production dry-run rather than asserting the contradictory historic total.

---

## Starting state, measured 2026-09-01

| Fact | Evidence |
|---|---|
| `http-auth` (401/403) raises before any retry, and wrote zero verdicts | `weblate/trans/judge.py:1516-1517`; the 401 attempt at 06:05 has no verdict row |
| `http-other` (every other 4xx) marks the batch unparsed and the run continues | `weblate/trans/judge.py:1105-1114`, `:1520-1559`; 50 verdicts from one run |
| `http-other` counts toward the endpoint circuit breaker as an availability failure | `_AVAILABILITY_FAILURE_KINDS` (`weblate/trans/judge_loop.py:632-634`) |
| The deferral queue enqueues on `result.unparsed` without looking at the kind | `weblate/trans/judge_loop.py:779-795` |
| The verdict card shows a refused request as "The latest judge answer was not parsed" | `weblate/templates/snippets/judge-verdict.html:12-22` |

Two contradictions follow from one kind carrying two meanings. A 400 is a
configuration defect, yet it opens an availability circuit; and the availability
fallback of
`docs/llm-first/plans/2026-09-01-02-judge-openrouter-availability-fallback.md`
deliberately refuses to fail over on it, so the same kind is simultaneously
treated as an endpoint-health signal and as a request defect.

## Non-goals

- Any change to provider models, prompt, JSON schema, severity rubric, batch
  sizes or per-seat deadlines.
- The availability fallback. A refused request must not be sent anywhere else.
- Making `finish-length` retriable. It remains a terminal unparsed outcome until
  separately designed. Parser failures are not terminal: the existing queue
  retries them like every other unparsed result.
- Claiming a historical database with zero `unparsed` rows. The queue preserves
  provisional history while a retriable request is pending.

## Task 1: Separate a permanent refusal from a size-dependent one

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0114_judge_request_invalid_kind.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write failing tests

`_failure_for_http` must map, and the mapping is the whole safety argument, so
it is a table of tests:

| Status | Kind | Why |
|---|---|---|
| 400, 404, 405, 406, 415, 422 | `http-request-invalid` | the endpoint rejects this request shape, model name or parameter; retrying or resending it anywhere is waste |
| 413, 431 | `http-other` | size-dependent: adaptive halving can still succeed |
| 402, 409, and any other 4xx | `http-other` | unclassified; keep today's behaviour rather than guessing |
| 401, 403 | `http-auth` | unchanged |
| 429 | `http-rate-limit` | unchanged |
| >= 500 | `http-server` | unchanged |

Also assert `http-request-invalid` is in `FAILURE_KINDS` and in
`JudgeRequestAttempt.FailureKind`, and that it is **not** in
`_AVAILABILITY_FAILURE_KINDS`: a rejected request is not evidence about endpoint
health, and must not open the shared circuit.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k failure_for_http
```

### Step 3: Implement

Add the kind to `FAILURE_KINDS` (`weblate/trans/judge.py:49-67`) and to
`JudgeRequestAttempt.FailureKind`. Generate every migration operation implied by
that enum: `JudgeRequestAttempt.failure_kind`,
`JudgeAdaptiveState.last_failure_kind`, and `JudgeDeferral.last_failure_kind`
all use its choices. Do not hand-write a partial one-field migration.

Remove nothing from `_AVAILABILITY_FAILURE_KINDS` except the newly split
statuses' effect: `http-other` stays in that set, since 413 and an unclassified
4xx remain plausible transient endpoint states.

### Step 4: Verify GREEN

```text
uv run pytest weblate/trans/tests/test_judge_client.py
```

### Step 5: Commit

```text
feat(judge): classify a refused request separately
```

## Task 2: Fail fast without writing a refused verdict

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write failing tests

1. One `http-request-invalid` response raises `JudgeError` from `_run_batch`
   before the retry branch, with no second HTTP call for that seat: assert that
   seat's request count is exactly one.
2. The `JudgeRequestAttempt` row is still persisted, with
   `failure_kind="http-request-invalid"`, `parsed=False`,
   `transport_succeeded=False` and the HTTP status - diagnostics must survive
   the abort, because that row is how an operator learns what was refused.
3. **No `JudgeVerdict` points at the refused attempt**, and no
   `JudgeDeferral` is created for that seat even with
   `JUDGE_DEFERRAL_ENABLED=True`.
4. A parsed peer-seat result may already have reached the main persistence
   thread before the refusal arrives. Preserve that valid evidence; assert only
   that it cannot make the producer run completed, repair a translation, or
   satisfy `has_complete_current_evidence()` after the run aborts.
5. The error propagates out of the seat worker and fails the run, releasing the
   parallel barrier exactly as the `http-auth` path already does.
6. The message names the status and no credential:
   `The LLM judge endpoint refused the request (HTTP 400).` It is translatable
   and must not contain the base URL, key or model.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k refused
uv run pytest weblate/trans/tests/test_judge_loop.py -k refused
```

### Step 3: Implement

Extend the existing fail-fast branch at `weblate/trans/judge.py:1516-1517` to
cover both kinds. Keep it after `_persist_attempt` and before the retry
decision, so the ledger row exists and no paid retry follows. Do not add a
cross-seat rollback: `_run_seats` intentionally persists a valid peer result
when it arrives, and a refusal must not erase evidence from a different,
successful request. Use a distinct message from the configuration error: "not
configured" is wrong when the endpoint is reachable, authenticated and simply
refusing this request.

### Step 4: Verify GREEN

```text
uv run pytest weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
```

### Step 5: Commit

```text
fix(judge): stop a run on a refused request
```

## Task 3: Prove the existing producer report stays honest

**Files:**

- Modify: `weblate/trans/tests/test_judge_autotranslate.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

No production view change is expected. `BatchAutoTranslate.perform()` already
stores an exception as a failed `JudgeRun`
(`weblate/trans/autotranslate.py:1271-1284`) and `judge-run.html` already
renders `run.failure` for a failed run (`weblate/templates/judge-run.html:61-65`).

Add an end-to-end regression with the refusal `JudgeError`: the run is failed,
the stored message is shown in its report, and the refusal itself contributes no
unparsed verdict. If a peer seat had already persisted a valid result, the test
must accept that row but prove it does not project a two-seat decision.

### Verify

```text
uv run pytest weblate/trans/tests/test_judge_autotranslate.py -k refused
uv run pytest weblate/trans/tests/test_judge_views.py -k refused
```

### Commit

```text
test(judge): report a refused run without a fake verdict
```

## Task 4: Correct the historical refused outcomes without over-deleting

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0115_judge_run_unit_refused_outcome.py`
- Create: `weblate/trans/management/commands/judge_close_refused_verdicts.py`
- Modify: `weblate/trans/tests/test_commands.py`

The new `http-request-invalid` kind is not retroactive: the historical HTTP 400
attempts remain `http-other` and the 401 attempt remains `http-auth`. Therefore
the command must select legacy rows through their linked attempt, never through
the new kind:

```text
JudgeVerdict.unparsed=True
request_attempt.http_status IN (400, 401)
```

It requires `--expected-count=N` and a separate `--confirm`. Dry-run prints the
candidate count per HTTP status, model and profile fingerprint; `--confirm`
refuses to run unless its count still equals `N`. The dry-run count is the
production authority, not the stale "101" figure in earlier prose: the measured
401 attempt itself wrote no verdict, while the 120-second deadline verdict must
remain.

For every deleted verdict, reclassify each linked
`JudgeRunUnit(outcome="unparsed")` as a new explicit `refused` outcome. Otherwise
the report would retain a dangling, misleading "Unparsed" row after the verdict
was removed. The attempt ledger remains the diagnostic record. Do not touch a
row with no attempt, `unparsed=False`, status 413/431, any other `http-other`
status, or a deadline/transport attempt.

Tests cover dry-run, confirmation, expected-count mismatch, and every exclusion
above, including an unparsed 413 and the historical deadline control. Assert the
linked run-unit outcome becomes `refused`. Run the command before the 90-day
attempt cleanup can null the foreign-key evidence.

### Verify

```text
uv run pytest weblate/trans/tests/test_commands.py -k refused
```

### Commit

```text
fix(judge): remove false refused verdicts from history
```

## Task 5: Document and prove it on dev

**Files:**

- Modify: `docs/admin/config.rst`
- Modify: `docs/changes.rst`
- Create: `docs/llm-first/measurements/<date>-judge-refused-request-fail-fast.md`

Documentation: one changelog entry in the unreleased section, and the new kind
in the judge settings prose where the failure taxonomy is described. No new
setting is introduced, so no `.. setting::` block is needed.

Dev proof, three arms on the dev container, all read-only
(`writable_ids=set()`):

1. **Refused arm.** Set `JUDGE_MODEL_SEAT_1` to a model the endpoint does not
   serve so it answers 400 or 404. Expected: exactly one attempt for that seat,
   `failure_kind="http-request-invalid"`, a failed run, no verdict linked to the
   refused attempt, and no deferral for that seat. A valid peer-seat verdict may
   exist if it arrived before the refusal.
2. **Size arm.** Force a 413 if the proxy can produce one, or assert by test
   double if it cannot. Expected: unchanged behaviour - `http-other`, adaptive
   halving, no abort.
3. **Healthy control.** Correct configuration and the same fixed scope.
   Expected: zero refusals and the previously measured number of calls for that
   scope. Do not compare incidental bytes, timestamps or model text.

### Commit

```text
test(judge): record the refused-request fail-fast
```

## Task 6: Bound and document the durable queue

**Files:**

- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/trans/models/_conf.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0116_judge_deferral_closed_retention.py`
- Modify: `weblate/trans/tasks.py`
- Modify: `weblate/trans/tests/test_tasks.py`
- Modify: `docs/admin/config.rst`
- Modify: `docs/admin/install/docker.rst`
- Modify: `deploy/environment.example`
- Modify: `docs/changes.rst`

The existing ten `JUDGE_DEFERRAL_*` settings are present in
`weblate/trans/defaults.py:95-104` and in `deploy/environment.example`, but
none appears in `docs/admin/config.rst` or `docs/admin/install/docker.rst`.
More importantly, `cleanup_judge_observability()` only cleans attempts and
usage rows; no code deletes a closed `JudgeDeferral`. Since every changed target
closes the old identity, enabling the queue without closed-row retention makes
that table grow forever.

Add `JUDGE_DEFERRAL_CLOSED_RETENTION_DAYS=90`, parsed from
`WEBLATE_JUDGE_DEFERRAL_CLOSED_RETENTION_DAYS`, and an index on
`(state, closed_at)`. Extend `cleanup_judge_observability()` to delete only
`JudgeDeferral(state="closed", closed_at < cutoff)`. It must never delete
`queued` or `slow` rows: those are live work, not history. A value of zero
purges all closed rows at the next scheduled cleanup; an invalid value falls
back to 90, matching the existing retention helper.

Tests create old and recent closed rows plus old `queued` and `slow` controls,
then assert that only the expired closed record is deleted. Verify the index
migration through database introspection, not a source-text assertion. The
existing attempt and usage cleanup tests must remain green.

Document the eleven queue settings in the existing alphabetical `JUDGE_*` run,
with their matching `WEBLATE_*` environment variables. State:

- The drain is registered by Celery application finalization and runs only when
  the beat process starts with the flag true
  (`weblate/trans/tasks.py:1541-1586`). Changing a Docker environment variable
  requires recreating the process; a settings reload does not add the schedule.
- `JUDGE_DEFERRAL_OPERATOR_STOPPED` blocks paid drain calls and preserves queued
  rows. The periodic task still wakes, claims and releases rows; it does not
  stop running.
- Attempt and usage retention already runs independently of the queue flag;
  closed deferral retention added here does the same. No live queue row is ever
  deleted automatically.
- `_sync_deferral` does not currently filter failure kinds: it queues every
  `result.unparsed` except `finish-length`
  (`weblate/trans/judge_loop.py:779-829`). A refusal must fail fast before it
  reaches that function; parser failures remain intentionally eligible.

### Commit

```text
feat(judge): retain closed deferred judge requests
```

## Task 7: Enable the queue and prove the full lifecycle

**Files:**

- Create: `docs/llm-first/measurements/<date>-judge-deferral-queue-enabled.md`

Production change, separate explicit approval. Preconditions, each verified
before the flag moves: Tasks 1-6 deployed; the dev refusal arm proved zero
verdicts for the refused attempt and zero deferrals for its seat; the production
queue baseline is zero.

### Step 1: Dev lifecycle

Set `JUDGE_DEFERRAL_ENABLED=1` and recreate the dev Celery process so its beat
schedule includes `judge-deferral-drain`. For this single lifecycle proof, set
`JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0` and force a deadline on one seat with a
positive value safely below the measured first-byte latency. The producer run
must create exactly one provisional unparsed verdict and one
`JudgeDeferral(state="queued")`; it is not yet a `DEFERRED` producer-run outcome.

Restore the normal deadline, invoke the drain, and assert the row closes, the
fresh verdict is parsed, and the drain run is recorded under the system actor
without modifying target text. The drain may project `Unit.state` from the
recovered two-seat verdict (`weblate/trans/judge_loop.py:1431-1446`); record and
assert that intended transition. Then repeat with a refusal: zero deferrals for
the refused seat, one failed producer run, and no verdict linked to the refused
attempt.

### Step 2: Production enable and scheduler proof

Set `WEBLATE_JUDGE_DEFERRAL_ENABLED=1` and recreate the production Celery
process. Verify the beat schedule contains `judge-deferral-drain`, the initial
queue depth is zero, and record the active state-projection behavior. This does
not prove draining, because an empty queue has nothing to drain.

### Step 3: Controlled production drain proof

Requires a second, explicit production approval. Use one designated test unit
whose state may change, with a temporarily low positive deadline for one seat.
Restore the measured production deadline immediately, recreate the process, and
wait until that row's `next_attempt_at` plus one full drain interval and the
seat deadline. Expected: the row closes with a parsed verdict, the drain run is
complete, target text is unchanged, and any state transition is the documented
two-seat projection on that designated unit. No other queue row may exist. If
this cannot be approved, record the scheduler proof only and do not claim the
production drain path is verified.

### Step 4: Record

Write the measurement with the dev lifecycle, production scheduler and drain
proofs, queue depth over time, drain call counts, and the per-seat unparsed rate
separated into provisional, parsed-after-drain and terminal categories.

### Step 5: Commit

```text
test(judge): record the deferral queue rollout
```

**Rollback:** Set `WEBLATE_JUDGE_DEFERRAL_OPERATOR_STOPPED=1` and recreate the
Celery process to block further paid drain calls while preserving the queued
rows. Clearing `WEBLATE_JUDGE_DEFERRAL_ENABLED` and recreating the process
removes the periodic schedule entirely; it does not delete the rows.

## Rollout

Each production step needs its own explicit approval.

1. Deploy Tasks 1-6 and their migrations.
2. Run the dev refusal, size and healthy-control proofs and record them.
3. Run one bounded production canary on a component that already has parsed
   verdicts; assert zero refusals and the expected call count.
4. Run the historical-cleanup command dry-run. Record its count, rerun it with
   that exact `--expected-count`, then add `--confirm`.
5. Enable and verify the queue through Task 7. The controlled production drain
   proof is an additional approval, not implied by activation.

**Stop conditions:** any refusal in the healthy control arm; a run that aborts
on a 413 or unclassified 4xx; a verdict linked to a refused attempt; a cleanup
candidate outside `unparsed=True` plus HTTP 400/401; an expected-count mismatch;
a queued record created for a refusal; a controlled drain that does not close;
a target-text change during a drain; or a state change outside the designated
canary unit or not justified by its recorded two-seat verdict.

## Risks

- **The status list is a judgement call.** 422 is classified permanent because a
  proxy uses it for an unprocessable request body, but a gateway could use it
  transiently. The mitigation is that the taxonomy is one function with one test
  table, so a correction is one line and one row.
- **A refusal can race a successful peer seat.** The parallel barrier prevents
  either seat from running ahead, not from persisting a peer result already
  received by the main thread. Preserving valid peer evidence is safer than a
  cross-seat delete; it must not be projected as a complete decision.
- **The cleanup is a production deletion.** It is guarded by dry-run,
  `--expected-count`, and `--confirm`; it reclassifies run history rather than
  leaving dangling unparsed outcomes, and refuses broad or unattached rows.
- **The queue spends money without a human.** That is its purpose - a timed-out
  string has to be re-judged - but it is gated on the refusal path, closed-row
  retention, documented settings, and a dev lifecycle proof. The operator stop
  blocks paid calls but does not stop Celery wake-ups.
- **A drain is not state-read-only.** `writable_ids=set()` prevents repair text
  writes, but `_finalize_drain_run()` intentionally projects a recovered
  two-seat verdict to `Unit.state`. Production proof therefore uses a designated
  unit with an approved state transition and treats any target-text mutation as
  a stop condition.
- **A drain needs more than one scheduler interval to prove.** A newly queued
  row cannot run before `next_attempt_at`; after restoring the normal deadline,
  the acceptance window is that time plus the next scheduled drain and one seat
  deadline. An empty production queue proves scheduling only, not recovery.
