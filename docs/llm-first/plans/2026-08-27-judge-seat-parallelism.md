# Plan: judge both seats in parallel

Date: 2026-08-27. Status: **deployed; dev canary failed; production rollout
blocked.** Revised after engineering review on 2026-08-27; the caller-failure
protocol, provider-load gate and thread-aware test layout below replace the
unsafe first draft. Revised again on 2026-08-31 against the post-remediation
judge loop (c979bbc, 011c95d, cc3111d, 75ce50f): see "Revision: 2026-08-31
codebase drift". R1 and R3 there are blocking corrections to the original
design; every task was implemented as amended.

Genre: implementation. Output: one two-seat fan-out helper in
`judge_loop.py`, a deterministic thread-safe test harness, and focused tests
split between the existing `ViewTestCase` fixture and a dedicated
`TransactionTestCase`.

Rule R3 (`docs/llm-first/vision/llm-first-product-architecture.md:686`) is not
engaged: this plan changes neither the prompt nor either model. The logical
batch plan also stays fixed: every uncached string is offered to the same two
seats with each seat's own configured batch size. Actual POST attempts, spend
and parsed
coverage are acceptance measurements, not invariants, because simultaneous
requests can change provider refusals and retries.

## Basis

Measured on production during run `24023684-5dc3-4fa4-8f6c-e9ecdc4b3b23`
(Celery task `4e910bef-7427-48bc-8bc7-7d483c009d6e`, translation 53 =
`strategy-and-tactics-2/glosssary/ru`, 557 strings, `mode=judge`,
`engines=['openrouter']`):

- `JUDGE_BATCH_SIZE=5`, `JUDGE_MAX_REPAIR_ATTEMPTS=1`,
  `JUDGE_TRANSPORT_RETRIES=1`, `JUDGE_REQUEST_SLEEP=0.0`, read from
  `django.conf.settings` inside the running container. 557 strings give 112
  logical batches per seat. The progress model therefore calls these
  `initial_calls=224` and `worst_case_calls=448`
  (`weblate/trans/autotranslate.py:693-698`), but 448 is only the logical
  no-retry ceiling. One logical batch can execute at most
  `1 + JUDGE_TRANSPORT_RETRIES + 1` POSTs: initial attempt, configured
  transport repeats, and one 403/429 repeat (`weblate/trans/judge.py:677-688`).
  With the observed settings the hard full-run POST ceiling is
  `448 * (1 + 1 + 1) = 1344`; the no-repair initial-pass ceiling is
  `224 * 3 = 672`.
- Seat 1 (`deepseek/deepseek-v4-pro`): first batch logged 11:38:23, batch
  112/112 logged 12:16:46, seat-done logged 12:16:46. **38 min 39 s for one
  seat.**
- Seat 2 (`qwen/qwen3-235b-a22b-2507`) started 12:16:54, i.e. only after seat 1
  had fully finished.
- Per-batch latency from `judge batch N/112 ok ... elapsed=` lines: 5841, 6398,
  9379, 13464, 18915, 7665, 7525, 9488, 21855, 39824 ms. The cost is remote
  latency, not local work and not a configured delay.

Code that makes the two seats serial:

- `weblate/trans/judge_loop.py:519-546` - `for seat, model in seats:` calls
  `request_verdicts()` to completion before the next seat starts.
- `weblate/trans/judge.py:590-696` - inside one seat, `for position, batch in
  enumerate(batches)` posts one batch and waits for it. This stays sequential
  in this plan.

## Goal

Turn the judging phase from `seat_1 + seat_2` into approximately
`max(seat_1, seat_2)` without changing the request inputs or the collegium
contract. On the production observation this targets roughly 39 minutes
instead of roughly 77 minutes.

The speedup is accepted only if a bounded live canary shows no transport
degradation: no 403/429, no exhausted transport retry, no new `UNPARSED`
result, progress continues per persisted batch, and both model streams overlap
in time. Request count, cost and output verdicts are recorded; they are not
claimed to be byte-for-byte identical because retries and LLM output are not
deterministic.

## Why the design is bounded

Seat completion order is already irrelevant to every consumer of the rows:

- `current_round()` (`weblate/trans/models/judge.py:291-298`),
  `active_round()` (`:325-343`) and `_cached_verdict()`
  (`weblate/trans/judge_loop.py:276`) all re-read the round with
  `.order_by("seat")`. Row creation order is never observed.
- `collegium_verdict()` (`weblate/trans/models/judge.py:346-359`) is a `max()`
  over the round by `(SEVERITY_RANK, -seat)`. It is order-independent by
  construction, and `test_no_seat_may_lower_the_other` plus
  `test_the_same_holds_when_the_strict_seat_votes_second`
  (`weblate/trans/tests/test_judge_loop.py:299-305`) already assert that.
- `_prepare_round_unit`, `_round_verdict` and `repair_targets` run only after
  both seat jobs terminate (`weblate/trans/judge_loop.py:548-562`).

`BaseLLMTranslation.batch_concurrency = 2`
(`weblate/machinery/llm.py:349-355`) is a useful implementation precedent, not
proof that judge traffic cannot be refused. The machinery path has its own
rate-limit stop/wait (`weblate/trans/machinery.py:43-46,65-82`); the judge
instead retries a 403/429 once and converts an exhausted batch to `UNPARSED`
(`weblate/trans/judge.py:643-695`). The dev and production canary gates below
therefore remain mandatory.

## Design decisions

### D1. The seat is the unit of parallelism, with a fixed pool

`ThreadPoolExecutor(max_workers=len(seat_jobs))`, one worker per seat that has
work. No new setting, no new environment variable, no default.

Rejected alternatives:

- *A `JUDGE_SEAT_CONCURRENCY` setting.* Configurability that was not asked for
  is against the repository rule, and every judge env var added so far has to
  be mirrored into `deploy/environment.example` and the production `.env` by
  hand - the exact class of drift that silently disabled the judge once
  already. Rollback for this change is `git revert` plus a deploy, which is the
  same lever the setting would have provided.
- *Parallelising the batches inside a seat.* Up to 112 concurrent requests per
  seat, a real rate-limit exposure, and it puts the positional-alignment
  contract of `_persist_verdict_batches` under concurrent pressure. Out of
  scope here; worth its own plan once this one is measured.

### D2. Successful acknowledgements form a cross-seat batch barrier

Workers perform HTTP and usage accounting only. `JudgeVerdict` persistence and
the external progress callback stay on the calling Celery thread.

A fire-and-forget queue is insufficient. If caller-side persistence or
`current_task.update_state()` raises, leaving the drain loop would make
`ThreadPoolExecutor.__exit__()` wait while workers continue paid requests and
enqueue results nobody persists. Immediate per-event acknowledgement is also
insufficient: the faster seat could start batch N+1 before the caller observes
that its peer failed in batch N.

Each `_BatchReady` therefore carries a per-seat `batch_index` and an
acknowledgement. The caller persists each arrival immediately but sends
successful acknowledgements only after every active seat has delivered and
persisted the same batch index:

```text
seat 1: HTTP N -> event N ----+              +-> success ack -> HTTP N+1
                              |              |
                              v              |
                         caller persists both
                              ^              |
                              |              |
seat 2: HTTP N -> event N ----+              +-> success ack -> HTTP N+1
```

`_run_seats` validates before starting workers that every job carries the same
ordered request list. Because `request_verdicts()` applies the same
`JUDGE_BATCH_SIZE`, both seats then have the same batch indices and the barrier
cannot wait for a batch that its peer will never produce. A one-job direct test
uses quorum one; production creates either two jobs or none.

The barrier adds the faster seat's wait for its peer, so the precise target is
the sum of `max(seat_1_batch_N, seat_2_batch_N)`, not mathematically guaranteed
`max(total_seat_1, total_seat_2)`. It still overlaps every remote request pair;
the dev canary decides whether the measured wall-clock benefit is sufficient.

### D3. Any fatal path releases the barrier and aborts later paid work

The caller stores the first genuine `BaseException` raised by persistence,
progress or a worker but does not leave the drain loop:

1. A caller persistence/progress failure sets `fatal_error`.
2. A worker publishes `_SeatDone(error)` for an exception before or after a
   callback; the caller sets `fatal_error` when it observes that terminal
   event.
3. Setting `fatal_error` releases every pending batch acknowledgement with that
   error. Any later completed event is still offered to its persistence closure
   and then acknowledged with the same error.
4. Every worker waiting in its callback raises private `_SeatAborted`; no worker
   can begin batch N+1 until both batch-N results or a fatal terminal event have
   reached the caller.
5. The caller continues draining until every `_SeatDone` arrives, resolves all
   futures, then re-raises the first genuine fatal exception.

This ordering prevents both failure modes: raising before all acknowledgements
would deadlock executor shutdown, while acknowledging one seat before its peer
would permit another paid batch after the peer had already failed.

Persistence semantics remain explicit:

- A database error inside `transaction.atomic()` rolls back that batch.
- The caller still attempts to persist every batch that had already completed.
- The external progress callback runs after the transaction. If it fails, that
  batch's verdict rows remain committed.
- The first genuine fatal event observed by the caller wins. Private
  `_SeatAborted` control flow never replaces it.

### D4. Worker failure preserves completed work and stops both seats at the

barrier

If seat 2 fails in batch N, seat 1 can complete and persist batch N but cannot
start N+1: its success acknowledgement is waiting for seat 2, and seat 2's
terminal error converts that pending acknowledgement into `_SeatAborted`.
The caller resolves every future before propagating the genuine worker
exception.

This preserves
`JudgeIncrementalPersistenceTest.test_completed_request_batches_survive_a_mid_seat_crash`
(`weblate/trans/tests/test_judge_loop.py:792-817`) without paying for the whole
other seat after the run has already become unusable.

### D5. Each worker releases its own database connection

`request_verdicts()` records usage from the worker thread:
`_record_usage()` -> `_write_llm_usage()` (`weblate/trans/judge.py:360-402`).
Django database wrappers belong to the thread that created them. Each worker
calls `connections.close_all()` in a nested `finally`, and publishes its
terminal event even if connection cleanup itself raises. This mirrors
`weblate/trans/machinery.py:57-61`.

The real worker-connection path needs a dedicated `TransactionTestCase`.
`ViewTestCase` inherits Django's outer test transaction, which a second
thread's connection cannot see; the existing explanation is
`JudgeResolutionRealConcurrencyTest`
(`weblate/trans/tests/test_judge.py:593-600`).

### D6. Per-seat batch order, lockstep and seat/model identity are preserved

`_persist_verdict_batches` (`weblate/trans/judge_loop.py:419-424`) pairs results
to units through a cursor that advances by batch length. Each seat keeps its
own closure. `_run_seats` rejects unequal ordered request lists before opening
the pool. A worker cannot publish batch N+1 until the caller has persisted
batch N from both seats, so each closure receives exactly that seat's batches
in request order.

The invariant includes model identity, not merely the set of requested models:

```text
seat 1 -> JUDGE_MODEL_SEAT_1
seat 2 -> JUDGE_MODEL_SEAT_2
```

`_cached_verdict()` enforces that exact mapping
(`weblate/trans/judge_loop.py:286-290`), so tests must assert persisted
`(seat, judge_model)` pairs. Sorting the request models alone would miss a
seat/model swap.

## Execution environment

The dev Docker stack is shared rather than worktree-isolated. `rundev.sh`
changes into `dev-docker/`, and `docker compose exec` runs in the already
existing `dev-docker` compose project whose `/app/src` mount was fixed when the
container was created (`rundev.sh:23,49-59`;
`dev-docker/docker-compose.yml:46-49`). Running `./rundev.sh test` from another
worktree can therefore execute the main checkout's mounted code, not the
worktree under review.

Execute this plan on a feature branch in the main checkout so container tests
and the dev canary exercise the edited files. Preserve unrelated working-tree
changes and use path-specific staging for only
`weblate/trans/judge_loop.py` and
`weblate/trans/tests/test_judge_loop.py`. Do not recreate the shared stack if it
is absent or stale: `./rundev.sh` rebuild/start requires separate approval
under the repository working agreement.

## Change

### Task 1: deterministic thread-safe request fake

**File:** `weblate/trans/tests/test_judge_loop.py:7-84`

The current `mock_request_verdicts` feeds one shared iterator through an
ordinary `mock.Mock`. Both are wrong once two threads call it:

- result ownership depends on call order;
- Python 3.12 `Mock` mutates `call_count` and `call_args_list` without a lock.

The default test settings use distinct seat models. Keep the production
`request_verdicts(requests, *, model, ...)` interface unchanged and replace
only the harness with a callable fake keyed by those configured model strings:

```text
[seat1/round0, seat2/round0, seat1/round1, seat2/round1]
                    |
                    +-- model 1 iterator: calls[0::2]
                    +-- model 2 iterator: calls[1::2]
```

For each call, the fake:

1. Validates that the configured model keys are distinct. The equal-model
   integration test below deliberately bypasses this fake.
2. Selects and advances the iterator for `model` under one `threading.Lock`.
3. Records the call in an internal `mock.Mock` under the same lock.
4. Releases the lock before invoking `on_batch`.
5. Splits requests and the flat result list by `settings.JUDGE_BATCH_SIZE`,
   calling `on_batch` once per batch in request order.
6. Delegates `call_count`, `call_args_list` and `assert_not_called` to its
   internally locked recorder.

Migrate every current concurrent patch:

- Replace the three local `round_results` iterators at
  `test_judge_loop.py:360-367`, `:394-401` and `:466-473` with the same fake.
- In `JudgeIncrementalPersistenceTest`, patch the raw `crash` callable with
  `new=crash`, not `side_effect=crash`, so an unnecessary `Mock` is not shared
  by worker threads.
- Replace `JudgeGlossaryRepairLockTest`'s direct
  `mock.Mock(side_effect=[[MAJOR], [MAJOR]])` at `:767-775`.
- The cached-verdict `mock.Mock` at `:615` is never invoked and can remain: its
  purpose is `assert_not_called`, not concurrent recording.

Keep the configured-model assertion on persisted rows:

```python
self.assertEqual(
    set(unit.judge_verdicts.values_list("seat", "judge_model")),
    {(1, "vendor-a/model"), (2, "vendor-b/model")},
)
```

Run the whole file before production changes. It must remain green, proving
that only the test harness changed.

Commit:

```text
test(judge): make request harness thread-safe
```

### Task 2: write the fan-out and failure-contract tests

**File:** `weblate/trans/tests/test_judge_loop.py`

Add six tests to `JudgeLoopTest(ViewTestCase)`. Their worker stubs must not
touch the database; all ORM writes remain on the test/caller thread.

1. `test_both_seats_are_asked_concurrently`
   - Both request stubs wait on `threading.Barrier(2, timeout=5)`.
   - The test fails on the current serial loop and passes only when both seats
     are in flight.
2. `test_verdicts_and_progress_run_on_the_calling_thread`
   - Record the two request thread IDs.
   - Record the thread ID inside `_write_verdict`.
   - Pass an external `on_batch` spy to `run_judge_batch` and record its thread.
   - Assert both persistence and external progress run on the test thread and
     differ from both worker IDs.
3. `test_each_seat_advances_in_lockstep_and_keeps_order_and_model`
   - Two units, `JUDGE_BATCH_SIZE=1`.
   - Seat 1 publishes batch 0 first; an event proves it cannot begin batch 1
     until seat 2 has published and the caller has persisted batch 0.
   - Force a known cross-seat arrival order rather than trusting scheduler
     timing.
   - Assert every `(unit, seat, judge_model, result)` row, including the exact
     seat/model mapping.
4. `test_a_failing_seat_keeps_the_other_seats_completed_verdicts`
   - Seat 1 publishes its first batch and waits for proof that seat 2 started.
   - Seat 2 waits for that first persisted batch, then raises.
   - Assert seat 1's already completed rows survive, both seats stop before a
     second HTTP batch, and the genuine worker exception is propagated.
   - The synchronization makes this fail on the serial implementation instead
     of accidentally passing it.
5. `test_caller_failure_stops_seats_after_their_in_flight_batch`
   - Parameterize two caller failures: `_write_verdict` raises before commit,
     and the external progress callback raises after commit.
   - A barrier proves both first HTTP batches started before the failure.
   - Each fake seat offers several batches, but assert only its first in-flight
     batch ran.
   - Assert every acknowledgement was released, both workers emitted terminal
     events, the original caller exception won, and the DB rows match the
     explicit D3 commit/rollback contract.
6. `test_equal_model_ids_are_supported`
   - Override both seat model settings with the same model ID.
   - Patch `request_verdicts` with a raw callable, not the model-keyed fake.
     Both calls wait on a barrier and return the same deterministic `PASS`
     results, because distinct per-seat result assignment is unobservable when
     the production call carries only `model`.
   - Assert two rows persist with seats `{1, 2}`, the same `judge_model`, and
     `PASS` verdicts.

Failure-proof matrix before production code:

| Test | Current serial code |
|------|---------------------|
| both seats concurrent | FAIL |
| caller thread ownership | FAIL |
| lockstep, order and identity | FAIL |
| one seat fails while other is live | FAIL |
| caller failure stops both seats | FAIL |
| equal model IDs | FAIL |

After implementation, prove the lockstep/order test's sensitivity with two
targeted mutations: acknowledge seat 1 before seat 2's same-index event, then
route one event to the other seat's persistence closure. The test must fail for
each mutation.

### Task 3: add the acknowledged fan-out helper

**File:** `weblate/trans/judge_loop.py`

Imports:

```python
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from django.db import connections, transaction
```

Add `JudgeResult` to the existing `weblate.trans.judge` import. Under
`TYPE_CHECKING`, import `Sequence` from `collections.abc`.

Add the job and event types beside `_PreparedRound`:

```python
@dataclass(frozen=True)
class _SeatJob:
    seat: int
    model: str
    requests: list[JudgeRequest]
    persist: OnBatch


@dataclass
class _BatchReady:
    seat_index: int
    batch_index: int
    requests: Sequence[JudgeRequest]
    results: Sequence[JudgeResult]
    acknowledged: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass(frozen=True)
class _SeatDone:
    seat_index: int
    error: BaseException | None = None


class _SeatAborted(Exception):
    """Private control flow after another seat or caller failed."""
```

Add `_log_seat_done(run_id, job)` with the existing log message. Use
`len(job.requests)` for the string count.

Implement `_run_seats` with this control flow:

```python
def _run_seats(
    seat_jobs: list[_SeatJob],
    *,
    project_slug: str,
    project_context: str,
    run_id: uuid.UUID,
) -> None:
    if not seat_jobs:
        return
    if any(job.requests != seat_jobs[0].requests for job in seat_jobs[1:]):
        msg = "judge seats must receive the same ordered requests"
        raise ValueError(msg)

    events: queue.Queue[_BatchReady | _SeatDone] = queue.Queue()

    def judge(index: int, job: _SeatJob) -> None:
        error: BaseException | None = None
        batch_index = 0

        def publish(
            batch_requests: Sequence[JudgeRequest],
            batch_results: Sequence[JudgeResult],
        ) -> None:
            nonlocal batch_index
            event = _BatchReady(
                index,
                batch_index,
                tuple(batch_requests),
                tuple(batch_results),
            )
            batch_index += 1
            events.put(event)
            event.acknowledged.wait()
            if event.error is not None:
                raise _SeatAborted from event.error

        try:
            request_verdicts(
                job.requests,
                model=job.model,
                project_slug=project_slug,
                project_context=project_context,
                on_batch=publish,
            )
        except BaseException as caught:
            error = caught
        try:
            connections.close_all()
        except BaseException as caught:
            if error is None:
                error = caught
        finally:
            events.put(_SeatDone(index, error))
        if error is not None:
            raise error

    def release(
        batch_events: list[_BatchReady], error: BaseException | None = None
    ) -> None:
        for batch_event in batch_events:
            batch_event.error = error
            batch_event.acknowledged.set()

    fatal_error: BaseException | None = None
    successful: list[int] = []
    waiting: dict[int, list[_BatchReady]] = {}
    quorum = len(seat_jobs)

    with ThreadPoolExecutor(
        max_workers=quorum, thread_name_prefix="judge-seat"
    ) as pool:
        futures = [
            pool.submit(judge, index, job) for index, job in enumerate(seat_jobs)
        ]
        open_seats = quorum
        while open_seats:
            event = events.get()
            if isinstance(event, _SeatDone):
                open_seats -= 1
                if (
                    event.error is not None
                    and not isinstance(event.error, _SeatAborted)
                    and fatal_error is None
                ):
                    fatal_error = event.error
                if event.error is None:
                    successful.append(event.seat_index)
                if fatal_error is not None:
                    for batch_events in waiting.values():
                        release(batch_events, fatal_error)
                    waiting.clear()
                continue

            try:
                seat_jobs[event.seat_index].persist(event.requests, event.results)
            except BaseException as error:
                if fatal_error is None:
                    fatal_error = error

            batch_events = waiting.setdefault(event.batch_index, [])
            batch_events.append(event)
            if fatal_error is not None:
                for pending_events in waiting.values():
                    release(pending_events, fatal_error)
                waiting.clear()
            elif len(batch_events) == quorum:
                release(waiting.pop(event.batch_index))

        for future in futures:
            try:
                future.result()
            except _SeatAborted:
                pass
            except BaseException as error:
                if fatal_error is None:
                    fatal_error = error

    for index in successful:
        _log_seat_done(run_id, seat_jobs[index])
    if fatal_error is not None:
        raise fatal_error
```

The implementation must retain all six ordering properties:

1. Jobs are rejected before the pool if their ordered requests differ.
2. A successful acknowledgement waits for the same `batch_index` from every
   seat.
3. A fatal error releases every pending and future batch acknowledgement.
4. The caller never raises from inside the drain loop.
5. Every worker publishes `_SeatDone`, including its own error, after attempting
   `connections.close_all()`.
6. Futures are resolved only after every terminal event was drained.

### Task 4: wire `run_judge_batch`

**File:** `weblate/trans/judge_loop.py:519-546`

Replace the serial seat loop with job construction and one helper call:

```python
seat_jobs = []
for seat, model in seats:
    request_units = [unit for unit in pending if unit.id not in cached_ids]
    if not request_units:
        continue
    seat_jobs.append(
        _SeatJob(
            seat=seat,
            model=model,
            requests=[round_requests[unit.id] for unit in request_units],
            persist=_persist_verdict_batches(
                request_units,
                seat=seat,
                attempt=attempt,
                run_id=run_id,
                model=model,
                on_batch=on_batch,
            ),
        )
    )

_run_seats(
    seat_jobs,
    project_slug=project_slug,
    project_context=project_context,
    run_id=run_id,
)
```

Everything before job construction (`_refresh_unit`, `build_request`,
`_cached_verdict`, the run log) and everything after `_run_seats`
(`_prepare_round_unit`, repair, final projection) stays unchanged. Repair
rounds re-enter the same helper.

Run `./rundev.sh test weblate/trans/tests/test_judge_loop.py`. The six Task 2
tests must now pass. Commit Tasks 2-4 together so no commit contains tests that
claim fan-out while `run_judge_batch` still uses the serial path:

```text
feat(judge): run judge seats in parallel
```

### Task 5: verify worker connection cleanup with real thread-local DB state

**File:** `weblate/trans/tests/test_judge_loop.py`

Add imports for `threading`, `connections`, `TransactionTestCase`,
`JudgeRequest`, `_SeatJob`, `_run_seats` and
`weblate.trans.judge._write_llm_usage`.

Create a database-backed helper test with no Weblate fixture:

```python
class JudgeSeatConnectionCleanupTest(TransactionTestCase): ...
```

It calls `_run_seats` directly with two `_SeatJob` instances sharing the same
one-item `JudgeRequest` list. Each has a caller-side persistence spy; no
`Unit`, repository, component, user or permission setup is needed.
`TransactionTestCase` is still
required because worker-side usage inserts use independent committed database
connections. A `ViewTestCase` outer transaction would be invisible from the
workers, as explained by `JudgeResolutionRealConcurrencyTest`
(`weblate/trans/tests/test_judge.py:593-600`).

Patch `request_verdicts` with a faithful worker stub that:

1. obtains `connections["default"]`;
2. inserts one real `LLMUsageLog` through `_write_llm_usage`;
3. records the wrapper and worker thread ID under a lock;
4. waits at a two-worker barrier before either path completes;
5. either calls `on_batch`, or raises the test's worker exception.

Use separate tests for success and exception. After `_run_seats` settles,
assert:

- both observed IDs differ from the caller thread;
- every captured worker wrapper has `connection is None`;
- two `LLMUsageLog` rows are committed before cleanup;
- the persistence spies ran only on the caller thread;
- cleanup happened on success and exception;
- in the exception case both worker stubs reached their cleanup path, the other
  seat stopped after its in-flight batch, and `_run_seats` returned by raising
  the original worker exception rather than deadlocking.

Before keeping these tests, temporarily remove `connections.close_all()` from
the helper and run the two class tests: both must fail on the open-wrapper
assertion. Restore cleanup, rerun the whole class, then commit:

```text
test(judge): cover judge worker connection cleanup
```

## What does not change

- `JUDGE_BATCH_SIZE`, prompts, models, response schema, repair attempts,
  request sleep and retry policy.
- The logical number and contents of judge batches.
- Batches inside one seat remain sequential.
- `_persist_verdict_batches`, `_write_verdict`, `_cached_verdict`,
  `collegium_verdict`, `current_round` and `active_round`.
- Progress still advances once per successfully persisted batch.
- No setting, environment variable, production toggle or within-seat
  concurrency is added.

The progress fields count logical callbacks, not POST attempts. Actual attempts
are bounded by
`logical_batches * (2 + JUDGE_TRANSPORT_RETRIES)` and can increase only when
the provider retries. Spend can also vary with output tokens. Parsed verdict
equality is not promised; the acceptance contract is unchanged inputs plus no
transport degradation.

## Canary manifest and runbook

No canary starts from an open-ended query. The operator first records this
manifest; any blank field blocks the run:

```text
environment: dev | production
project/component:
translation_id:
translation_path:
target_language:
unit_ids: 10-25 exact Unit primary keys
q: id:<pk> OR id:<pk> ...
overwrite_existing: false
operator:
approval/date: required for production only
start_utc:
usage_log_start_pk:
```

### Select an exact judge-only scope

Use the connected Weblate MCP first for production project, component,
translation and candidate-unit discovery (`listProjects`, `listComponents`,
`searchUnitsWithFilters`). The MCP and REST API do not expose
`JudgeVerdict`, `LLMUsageLog`, Celery's global active-task list or container
logs. The production live-run approval must therefore explicitly authorize the
read-only shell and log commands below for only this canary; without that
authorization, the production canary is blocked rather than approximated.

Use the following read-only Django query to exclude cached judge rows,
substituting only `TRANSLATION_ID`. In dev run it through
`docker compose exec` from `dev-docker/`. In production run the same snippet
only through the approved
`./deploy/vps.sh ssh "docker exec hcgameloc-weblate-1 weblate shell -c ..."`
path.

```python
from weblate.trans.models import Translation
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.utils.state import STATE_TRANSLATED

t = Translation.objects.get(pk=TRANSLATION_ID)
ids = list(
    t.unit_set.filter(
        state__gte=STATE_TRANSLATED,
        judge_verdicts__isnull=True,
    )
    .order_by("position", "pk")
    .values_list("pk", flat=True)[:25]
)
print("translation_id:", t.pk)
print("translation_path:", "/".join(t.get_url_path()))
print("component:", "/".join(t.component.get_url_path()))
print("target_language:", t.language.code)
print("unit_ids:", ",".join(map(str, ids)))
print("q:", " OR ".join(f"id:{unit_id}" for unit_id in ids))
print(
    "usage_log_start_pk:",
    LLMUsageLog.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
)
```

Require 10-25 IDs. Fewer than 10 cannot demonstrate several paired batches;
more than 25 violates the bounded canary. `judge_verdicts__isnull=True`
guarantees that the selected units cannot hit `_cached_verdict()`.
`state__gte=STATE_TRANSLATED` plus `overwrite_existing=false` makes
`writable=0`, so the canary performs no pretranslation or repair and measures
only the initial two-seat judging pass.

Before opening the form, inspect active, reserved and scheduled Celery tasks.
Block the canary if any entry has `mode='judge'`; otherwise batch logs and
usage rows cannot be attributed to this run:

```text
dev:  docker compose exec -T weblate celery -A weblate.utils.celery inspect active --json
      docker compose exec -T weblate celery -A weblate.utils.celery inspect reserved --json
      docker compose exec -T weblate celery -A weblate.utils.celery inspect scheduled --json
prod: run the same three inspect commands in hcgameloc-weblate-1 through
      ./deploy/vps.sh ssh under the live-run approval
```

### Preview and launch through the real UI

1. Open the manifest's exact target translation.
2. In `Automatic translation`, select `AI judge`, `Machine translation` and
   the configured routed engine.
3. Paste the manifest's exact `id:... OR id:...` query.
4. Leave `Overwrite the existing translation` unchecked.
5. Do not submit until `/auto-translate-preview/<translation-path>/` reports:
   - `matched == processed == len(unit_ids)`;
   - `remaining == 0`;
   - `writable == 0`;
   - `judge_calls_initial == 2 * ceil(len(unit_ids) / JUDGE_BATCH_SIZE)`.
6. Record `start_utc`, submit once, and capture the Celery task UUID plus the
   `judge run <run_id>` UUID from the application log.

The production approval names the component, language, exact IDs/query and
operator from this manifest. A general deploy approval is insufficient.

### Collect the acceptance evidence

From `GET /api/tasks/<uuid>/`, record `phase`, `phase_current`, `phase_total`
and final result. From application logs beginning at `start_utc`, isolate lines
matching:

```text
judge batch N/M ok: model=...
judge batch N/M failed: model=...
judge run <run_id>: seat ...
```

Dev logs:

```text
./rundev.sh logs --since <start_utc> weblate
```

Production logs, only after the live-run approval:

```text
./deploy/vps.sh ssh "docker logs --since <start_utc> hcgameloc-weblate-1 2>&1"
```

Because the no-other-judge-task preflight is mandatory, the matching batch
lines are this canary's actual POST attempts. Count every `ok` and `failed`
line, including repeated `N/M` positions.

After completion, run the exact `run_id` and usage-marker query below locally
in dev. In production, run it only through the read-only shell command named in
the live-run approval:

```python
from collections import Counter
from decimal import Decimal
from uuid import UUID

from weblate.trans.models.judge import JudgeVerdict
from weblate.trans.models.llm_usage import LLMUsageLog

run_id = UUID("RUN_ID")
verdicts = JudgeVerdict.objects.filter(run_id=run_id)
usage = LLMUsageLog.objects.filter(
    pk__gt=USAGE_LOG_START_PK,
    operation=LLMUsageLog.Operation.JUDGE,
    project_slug="PROJECT_SLUG",
)
print("verdict_rows:", verdicts.count())
print("distinct_units:", verdicts.values("unit_id").distinct().count())
print("unparsed:", verdicts.filter(unparsed=True).count())
print("severity:", Counter(verdicts.values_list("max_severity", flat=True)))
print("unit_seats:", Counter(verdicts.values_list("unit_id", "seat")))
print("models:", Counter(verdicts.values_list("judge_model", flat=True)))
print("usage_rows:", usage.count())
print(
    "cost_usd:",
    sum((row.cost_usd or Decimal(0)) for row in usage),
)
```

Require `verdict_rows == 2 * len(unit_ids)`,
`distinct_units == len(unit_ids)`, `unparsed == 0`, exactly one row per
`(unit_id, seat)`, and only the two configured seat models. Attach the manifest,
preview response, task result, filtered batch log, verdict snapshot and usage
snapshot to the measurement record.

## Verification

1. Harness-only checkpoint:

   ```text
   ./rundev.sh test weblate/trans/tests/test_judge_loop.py
   ```

   Green before production changes.
2. Add the six Task 2 tests and run them against the serial implementation.
   Record the failure-proof matrix from Task 2; all six tests must fail for
   their named reason.
3. After Tasks 3-4:

   ```text
   ./rundev.sh test weblate/trans/tests/test_judge_loop.py
   ```

   All six Task 2 tests pass.
4. Add Task 5's two `TransactionTestCase` tests. First remove
   `connections.close_all()` temporarily and run:

   ```text
   ./rundev.sh test weblate/trans/tests/test_judge_loop.py::JudgeSeatConnectionCleanupTest
   ```

   Both tests fail on the open worker connection. Restore cleanup and rerun;
   both pass.
5. Run the related regression suites:

   ```text
   ./rundev.sh test weblate/trans/tests/test_judge_loop.py
   ./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_round.py
   ```

   Repeat the Task 2 targeted mutations for early acknowledgement, queue
   routing and caller-failure acknowledgement; the named tests must fail.
6. Scoped lint:

   ```text
   uv run prek run --files weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py --skip typos --skip reuse --skip kingfisher-auto
   ```

7. Dev canary on at most 25 uncached strings:
   - verify batch lines for both models overlap before either model finishes;
   - verify task `phase_current` advances after every persisted batch;
   - count planned logical batches, actual POST attempts, retries,
     403/429/resets and `UNPARSED`;
   - record `LLMUsageLog` count/cost and per-seat first/last timestamps.

   Acceptance thresholds:

   - actual POST attempts never exceed
     `logical_batches * (2 + JUDGE_TRANSPORT_RETRIES)`; exceeding this is an
     accounting/loop bug;
   - acceptance still requires actual POST attempts to equal logical batches
     exactly;
   - zero 403, 429 and transport-reset log entries;
   - zero new transport/parse `UNPARSED` results;
   - both seat windows overlap, and judging wall time is no more than 75% of
     the sum of the two seat spans;
   - no progress freeze, worker leak or deadlock.

   Any failed threshold blocks merge. Do not tune retries, batch size, models or
   thresholds inside this change.

8. Production work requires two explicit approvals after merge:
   - deploy approval;
   - separate live-run approval naming the noncritical component, target
     language, query, cap of at most 25 uncached units, overwrite behavior and
     operator.

   Apply the same thresholds as the dev canary. Stop on the first failed
   threshold without widening the unit cap or changing judge settings. If the
   failure reproduces and is attributable to seat concurrency, revert the
   implementation commit and deploy the revert before another judge run.

9. Only after the bounded production canary passes, request approval for a
   comparable-size measurement. Record:
   - logical batches and actual attempts;
   - retries/refusals/resets/`UNPARSED`;
   - usage/cost per seat;
   - each seat's first/last batch timestamp;
   - total judging wall time and overlap;
   - final verdict distribution.

   Save the numbers under `docs/llm-first/measurements/`.

## Out of scope

- Parallelising batches inside one seat.
- A concurrency setting, production toggle or environment change.
- Any prompt, model, batch-size or retry-policy change.
- The repair engine's own concurrency.
- The run-specific `judge-monitor` helper.
- A large production measurement before the bounded canary passes and receives
  its own approval.

## Revision: 2026-08-31 codebase drift

Written against the pre-c979bbc loop; verified against the codebase at
2ed03f6. Design decisions D1-D6 stand, but the loop underneath changed
(c979bbc, 011c95d, cc3111d, 75ce50f). R1 and R3 are blocking corrections;
R2, R4 and R5 are drift the tasks must absorb.

### R1. Barrier must key on request offsets, not batch ordinals (blocking)

D2 assumed one shared `JUDGE_BATCH_SIZE`, so equal batch indices across
seats. Seats now have per-seat batch sizes (`JudgeSeatProfile.batch_size`;
`WEBLATE_JUDGE_BATCH_SIZE_SEAT_1=2` vs `..._SEAT_2=5` in both
`dev-docker/docker-compose.yml` and `deploy/environment.example`), and
`_adaptive_budget()` (`weblate/trans/judge.py:1303-1311`) can shrink a
seat's whole pass down to batch size 1 from persisted adaptive state. Over
688 requests the seats produce 344 and 138 batches: the same-index
acknowledgement barrier deadlocks - the finer-batched seat's later indices
wait for peer events that never come, and a successful `_SeatDone` releases
nothing.

Replace the ordinal barrier. `_BatchReady` carries the half-open request
offset range it covers; both seats iterate the same ordered request list,
so offsets are comparable. Release a batch's acknowledgement once every
other active seat has persisted coverage at or beyond that batch's end
offset, or has terminated. A successful `_SeatDone` must release every
acknowledgement still waiting only on that seat. Drop the equal-request
validation error only if it blocks the retry path below; the equal ordered
request list itself still holds at every dispatch site. The lockstep test
(Task 2, test 3) must configure unequal per-seat batch sizes so both the
serial loop and an ordinal barrier fail it.

### R2. Dispatch-site and signature drift

- The serial loop is now `weblate/trans/judge_loop.py:993-1002` inside
  `judge_units()`; the per-seat closure is `judge_seat_round()` (`:915-945`).
- `request_verdicts()` grew `seat=`, `run=`, `persist_attempts=`,
  `retry_budget=`, `adaptive=`, `attempt=` and `retry_deadline=`
  (`weblate/trans/judge.py:1497-1511`). `_SeatJob` carries `seat` plus these
  pass-throughs instead of a bare `model`; model identity is still asserted
  on persisted `(seat, judge_model)` rows.
- `_persist_verdict_batches` gained `request_round`, `profile`,
  `project_context` and the `_sync_deferral` call; it stays the
  caller-thread closure and is otherwise untouched.
- A second dispatch site exists: the all-seats-unparsed retry rounds
  (`weblate/trans/judge_loop.py:1003-1039`, from 011c95d). Its per-seat unit
  lists are identical by construction; route it through `_run_seats` too.
- The deferral drain path calls `judge_units` with `seats=(seat,)`
  (`weblate/trans/judge_loop.py:1368-1372`) - the quorum-1 case the plan
  already covers.

### R3. `RetryBudget.spend()` needs a lock (blocking)

`spend()` (`weblate/trans/judge.py:127-131`) is check-then-increment and one
instance is shared by both seats (`weblate/trans/judge_loop.py:959-964`).
Two workers can overspend the recovery cap. Guard `spend()` with a
`threading.Lock`; add a two-thread spend test in the Task 1 harness commit.

### R4. Worker-thread database writes broadened

Workers no longer write only `LLMUsageLog`: `persist_attempts=True` inserts
`JudgeRequestAttempt` rows and `adaptive=True` reads and writes
`JudgeAdaptiveState`. That state is concurrency-safe by construction - one
row per `(endpoint_fingerprint, model, seat)`, and circuit transitions run
under `select_for_update` (`weblate/trans/judge_loop.py:592-601`) - but Task
5 must assert `JudgeRequestAttempt` rows also commit from worker threads.
On LiteLLM endpoints the worker additionally performs alias discovery
(`resolve_judge_alias`, module cache `_ALIAS_CACHE`): dict reads and writes
are GIL-atomic; the worst case is one duplicate capability GET per seat,
which is accepted.

### R5. Endpoint move to LiteLLM is orthogonal

`_run_seats` sits above the transport, which already branches per provider
(`_judge_provider`, `weblate/trans/judge.py:180-186`): LiteLLM adds alias
resolution and strict reasoning validation, OpenRouter skips both. Moving
the seats to LiteLLM aliases is configuration only; adaptive and circuit
state start fresh rows keyed by the new endpoint fingerprint. The canary
acceptance gates are per-endpoint measurements: rerun the bounded canary on
the LiteLLM endpoint before trusting the overlap numbers there.

## Task checklist

- [x] Task 1: thread-safe distinct-model harness; configured-model assertion;
      file green before concurrency changes
- [x] Task 2: six deterministic fan-out/failure tests, including equal models;
      serial failure-proof matrix recorded
- [x] Task 3: cross-seat acknowledgement barrier; drain-before-raise; worker
      cleanup
- [x] Task 4: `run_judge_batch` builds jobs and calls `_run_seats`
- [x] Task 5: dedicated `TransactionTestCase` proves worker connections close
      on success and exception
- [x] Targeted mutations prove early acknowledgement, queue routing,
      seat/model identity, caller-failure acknowledgement and worker-connection
      cleanup tests detect their intended faults
- [x] Verification steps 1-6 recorded using the isolated host test database
- [x] Dev canary passes every transport, overlap and progress threshold -
      **passed 2026-08-31** for `col4/data/fr`, units 177726-177750,
      `overwrite_existing=false`. Preview matched/processed 25 with zero
      remaining and writable units, planning 26 initial calls. Task
      `b796098d-12c7-4b40-9456-4faad6b98acc`, judge run
      `725c62d9-71b7-41b6-ab73-5c398a9fffba`: 38 attempts, every one HTTP 200,
      no transport failure kind, all parsed; 50 verdicts, 25 per seat, zero
      unparsed; all 25 units judged by both seats; run report `16 passed,
      2 minor, 5 major, 2 critical`; 51 usage rows across both models.
      Seat 2 ran at an effective batch of 1 because the previous failed
      canary had shrunk its adaptive budget, which recovered to 2 by the end
      of the run. Its measured single-string latency is heavy-tailed - median
      21s, maximum 103s against the 120s request deadline - so batch 2 would
      exceed the deadline often, and a cut batch costs every verdict in it.
      Both configuration files therefore pin seat 2 to batch 1, which is the
      configuration this canary actually exercised.
      An earlier attempt on 2026-08-31 **failed** and is kept here because it
      is what produced the diagnosis:
      for `col4/data/fr`, units 177676-177700, `overwrite_existing=false`.
      Preview matched/processed 25 with zero remaining and writable units,
      planning 18 initial calls. Task
      `a8173a85-a55d-4871-8372-9037b642c569`, judge run
      `327fb5dc-3e53-401c-a2ec-67e08a111773`: 36 attempts, including 10
      HTTP 500 responses from seat 2, and 13 final unparsed strings.
      Diagnosed on 2026-08-31 to three seat request settings, none in the
      fan-out code. Seat 2 sent a top-level `enable_thinking` key, which the
      `atlas/qwen3.8-max` model group rejects
      (`AsyncCompletions.create() got an unexpected keyword argument
      'enable_thinking'`); all 10 of its attempts failed, and the 500 is
      reproducible on demand. Seat 1 asked for `json_object`, which leaves
      schema adherence to the model instead of constraining it: 20 of its 26
      attempts failed in two distinct ways - 15 `invalid-envelope`, where
      `segments` arrived as an object keyed by index instead of an array, and
      5 `invalid-segment`, where an error object omitted `span`. Both
      reproduce under `json_object` and neither does under `json_schema`, but
      adherence is probabilistic rather than deterministic: one probe batch
      parsed under `json_object` too. `json_schema` parsed in every observed
      probe, so the seat now sets it explicitly.
      `analysis/probes/litellm-seat-diagnostic.py` reproduces
      each setting against the live proxy using the seat's own resolved
      profile and reports the parser's own outcome.
      The third fault appeared only once the first two were fixed: seat 2's
      batch of 5 was cut at the 120s request deadline on all five attempts,
      losing every verdict it should have produced. Batch size is now pinned
      per the latency measurement above.
      Seat settings are written explicitly rather than as `inherit`, because
      `inherit` resolves against a legacy global an operator can retarget: the
      dev stack's local `docker-compose.override.yml` sets
      `WEBLATE_JUDGE_REASONING_EFFORT=none`, which the LiteLLM profile
      resolver rejects outright, so `inherit` there left the judge
      unconfigured entirely.
      `dev-docker/docker-compose.yml` and `deploy/environment.example` now
      carry the corrected settings; the canary was rerun after the dev stack
      was recreated under its own approval. Each corrected setting was also
      confirmed on the wire, returning HTTP 200 and parsing into
      verdicts. The `429 No deployments available` seen while diagnosing was
      self-inflicted, not an availability fault: a rejected request puts the
      model group's deployments into the proxy's cooldown list, after which
      every later request answers 429 whatever its own settings are. The same
      request returned both 500 and 429 within one probe run, and the
      corrected seat returned 200 once the cooldown had expired and the
      rejected variant was not sent first. `PROBE_ONLY` in the probe selects
      one variant for exactly that measurement.
- [x] Commit and push implementation
- [x] Separate deploy approval obtained
- [ ] Separate bounded production-run approval obtained
- [ ] Bounded production canary passes
- [ ] Comparable production measurement approved and recorded

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | NOT RUN | Backend optimization; optional |
| Codex Review | `/codex review` | Independent second opinion | 0 | NOT RUN | Not required |
| Eng Review | `/plan-eng-review` | Architecture and tests | 5 | CLEAR | 8 issues corrected, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | N/A | No UI change |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | Not required |

**VERDICT:** Engineering cleared; implementation is verified. The dev canary
failed, so production rollout is blocked.

**PENDING GATES:**

- Diagnose and correct the dev canary failure before another canary or any
  production judge run.
