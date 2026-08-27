# Plan: judge both seats in parallel

Date: 2026-08-27. Status: **proposed, not started.** Needs approval before
implementation.

Genre: implementation. Output: one fan-out helper in `judge_loop.py`, a
deterministic test harness, and four tests.

Rule R3 (`docs/llm-first/vision/llm-first-product-architecture.md:674`) is not
engaged: this plan changes neither the prompt, nor the model, nor the batch
size, nor the number of requests. The measured noise/precision/recall/cost
numbers stay valid, because every string is still judged by the same two seats
with the same `JUDGE_BATCH_SIZE` segments per call. Only the wall-clock order of
the HTTP calls changes.

## Basis

Measured on production during run `24023684-5dc3-4fa4-8f6c-e9ecdc4b3b23`
(Celery task `4e910bef-7427-48bc-8bc7-7d483c009d6e`, translation 53 =
`strategy-and-tactics-2/glosssary/ru`, 557 strings, `mode=judge`,
`engines=['openrouter']`):

- `JUDGE_BATCH_SIZE=5`, `JUDGE_MAX_REPAIR_ATTEMPTS=1`,
  `JUDGE_REQUEST_SLEEP=0.0`, read from `django.conf.settings` inside the
  running container. 557 strings therefore give 112 batches per seat,
  `initial_calls=224`, `worst_case_calls=448`
  (`weblate/trans/autotranslate.py:693-698`).
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

Turn the judging phase from `seat_1 + seat_2` into `max(seat_1, seat_2)`.
On the measured run that is ~39 min instead of ~77 min, for the same number of
requests, the same spend, and the same verdicts.

## Why this is safe

Seat order is already irrelevant to every consumer of the rows:

- `current_round()` (`weblate/trans/models/judge.py:291-298`),
  `active_round()` (`:325-343`) and `_cached_verdict()`
  (`weblate/trans/judge_loop.py:276`) all re-read the round with
  `.order_by("seat")`. Row creation order is never observed.
- `collegium_verdict()` (`weblate/trans/models/judge.py:346-359`) is a `max()`
  over the round by `(SEVERITY_RANK, -seat)`. It is order-independent by
  construction, and two existing tests already assert exactly that:
  `test_no_seat_may_lower_the_other` and
  `test_the_same_holds_when_the_strict_seat_votes_second`
  (`weblate/trans/tests/test_judge_loop.py:299-305`).
- Everything that reads a round - `_prepare_round_unit`, `_round_verdict`,
  `repair_targets` - runs after the seat loop has finished
  (`weblate/trans/judge_loop.py:548-562`), not between the seats.

Two concurrent LLM requests against this provider are already production
behaviour, not a new risk: `weblate/machinery/llm.py:355` sets
`batch_concurrency = 2`, and the judge's own repair fetch goes through that
path (`judge_loop.py:39` imports `fetch_machinery_matches`). A 429/403 is
already retried once with a doubled sleep (`weblate/trans/judge.py:685-688`).

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

### D2. Workers do network only; persistence and progress stay on the caller

A worker thread must not run the `on_batch` chain, because
`_persist_verdict_batches` writes `JudgeVerdict` rows and then calls the
progress callback, which ends in `current_task.update_state()`
(`weblate/trans/autotranslate.py:260-269`). Celery keeps the current task in
thread-local storage, so from a worker thread `current_task` is absent and
`set_progress` silently does nothing - the run would look frozen at 10% for its
whole duration. `weblate/trans/machinery.py:112-113` already records this trap
in a comment and solves it the same way.

Mechanism: the worker passes `on_batch=lambda ...: completed.put(...)` into
`request_verdicts()`, and the calling thread drains that `queue.Queue`, calling
the seat's own `persist` closure. Progress stays incremental (per batch, as
today), not per seat.

### D3. Each worker releases its own database connection

`request_verdicts()` writes usage accounting on the worker thread:
`_record_usage()` -> `_write_llm_usage()` (`weblate/trans/judge.py:389-402`,
`:360`). Django only closes connections it opened for a request or a task, so
the worker closes its own in a `finally`, exactly as
`weblate/trans/machinery.py:57-61` does. `LLMUsageLog` rows (worker connections)
and `JudgeVerdict` rows (caller connection) are different tables, so the
concurrent writes do not contend.

### D4. Per-seat batch order is preserved

`_persist_verdict_batches` (`weblate/trans/judge_loop.py:419-424`) pairs results
to units through a `cursor` that advances by batch length. Each seat gets its
own closure, and a single `queue.Queue` preserves FIFO order, so each closure
still receives its own batches in the order that seat produced them. Batches
from the two seats interleave in the queue; they never interleave inside one
closure. This is the invariant to test, because it is the project's known
positional-alignment hazard.

### D5. A failing seat must not lose the other seat's persisted work

The caller drains every sentinel before touching `future.result()`, so all
batches that completed are persisted first, and only then does the first
exception propagate. This preserves the contract of
`JudgeIncrementalPersistenceTest.test_completed_request_batches_survive_a_mid_seat_crash`
(`weblate/trans/tests/test_judge_loop.py:792-808`) with two seats in flight.

## Change

### Task 1: deterministic seat dispatch in the test harness

`weblate/trans/tests/test_judge_loop.py:63-71`. `mock_request_verdicts` feeds
one shared `iter(batches)` consumed in call order, so `run_batch([MAJOR, PASS])`
means "seat 1 gets MAJOR" only because seat 1 calls first. Under a pool that
pairing becomes a race.

Rework it to dispatch per seat model: keep one iterator per `model` kwarg
(`"vendor-a/model"` -> seat 1, `"vendor-b/model"` -> seat 2, from the
`@override_settings` block at `:74-80`), so `run_batch([MAJOR, PASS])` keeps its
current meaning deterministically and multi-round cases keep advancing per seat.

Then `test_each_seat_uses_its_configured_model` (`:494-497`) must stop asserting
call order: compare `sorted(models)` instead of the list.

Verification: the whole file passes **before** the production code changes, with
the reworked harness. That proves the harness rework alone is behaviour
preserving.

### Task 2: the fan-out helper

`weblate/trans/judge_loop.py`. Extend the import at `:25` to
`from django.db import connections, transaction`, and add `import queue`,
`from concurrent.futures import ThreadPoolExecutor` and
`from functools import partial` (the last two mirror
`weblate/trans/machinery.py:8-9`).

Add a frozen dataclass beside `_PreparedRound` (`:308-316`):

```python
@dataclass(frozen=True)
class _SeatJob:
    seat: int
    model: str
    request_units: list[Unit]
    requests: list[JudgeRequest]
    persist: OnBatch
```

Add the helper, with `_SEAT_DONE = object()` at module level:

```python
def _run_seats(
    seat_jobs: list[_SeatJob],
    *,
    project_slug: str,
    project_context: str,
    run_id: uuid.UUID,
) -> None:
    """Judge every seat, in parallel, persisting on the calling thread."""
    if not seat_jobs:
        return
    ask = partial(
        request_verdicts,
        project_slug=project_slug,
        project_context=project_context,
    )
    if len(seat_jobs) < 2:
        for job in seat_jobs:
            ask(job.requests, model=job.model, on_batch=job.persist)
            _log_seat_done(run_id, job)
        return

    completed: queue.Queue = queue.Queue()

    def judge(index: int, job: _SeatJob) -> None:
        try:
            ask(
                job.requests,
                model=job.model,
                on_batch=lambda batch_requests, batch_results: completed.put(
                    (index, batch_requests, batch_results)
                ),
            )
        finally:
            completed.put((index, _SEAT_DONE, None))
            # Django only closes connections it opened for a request or a
            # task, so a worker thread has to release its own.
            connections.close_all()

    with ThreadPoolExecutor(
        max_workers=len(seat_jobs), thread_name_prefix="judge-seat"
    ) as pool:
        futures = [
            pool.submit(judge, index, job) for index, job in enumerate(seat_jobs)
        ]
        open_seats = len(seat_jobs)
        while open_seats:
            index, batch_requests, batch_results = completed.get()
            if batch_requests is _SEAT_DONE:
                open_seats -= 1
                continue
            # Verdict rows and progress are written here: Celery keeps the
            # current task in thread-local storage, so a worker thread cannot
            # report progress at all.
            seat_jobs[index].persist(batch_requests, batch_results)
        # Every completed batch is persisted above before any failure is
        # raised, so one dead seat cannot discard the other seat's rows.
        for future in futures:
            future.result()
    for job in seat_jobs:
        _log_seat_done(run_id, job)
```

`_log_seat_done` carries the existing message from `:540-546` unchanged. Note
the ordering change: with two seats the two "seat N done" lines are emitted
after both seats finish, not interleaved with the run. Content is unchanged, so
`test_run_and_seat_are_logged` (`:146-152`) still holds.

### Task 3: use the helper in `run_judge_batch`

`weblate/trans/judge_loop.py:519-546`. Replace the body of the seat loop with
job construction, then one call:

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
                    request_units=request_units,
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

Everything before (`_refresh_unit`, `build_request`, `_cached_verdict`, the
`judge run ...` log) and everything after (`_prepare_round_unit`,
`repair_targets`, `_apply_repair`, the repair `while` loop) is untouched. The
repair round re-enters this same code, so repair rounds get the same speedup for
free.

## What does not change

- `JUDGE_BATCH_SIZE`, the prompts, the seat models, `JUDGE_MAX_REPAIR_ATTEMPTS`,
  `JUDGE_REQUEST_SLEEP`, the response schema, the retry policy.
- The number of HTTP requests and the spend: still `initial_calls` for the first
  pass, `worst_case_calls` as the ceiling.
- `request_verdicts()` itself: batches inside one seat stay sequential.
- `_persist_verdict_batches`, `_write_verdict`, `_cached_verdict`,
  `collegium_verdict`, `current_round`, `active_round`.
- Progress semantics: one tick per completed batch, `_judge_phase` boundaries
  unchanged (`weblate/trans/autotranslate.py:165-175`).
- No settings file, no `deploy/environment.example`, no production `.env`.

## Tests

All in `weblate/trans/tests/test_judge_loop.py`, mirroring the threading
conventions of `JudgeResolutionRealConcurrencyTest`
(`weblate/trans/tests/test_judge.py:593-685`), which uses `TransactionTestCase`
and closes the connection that must not straddle threads.

1. `test_both_seats_are_asked_concurrently` - a `threading.Barrier(2, timeout=…)`
   inside the patched `request_verdicts`; both seats must reach it, which is
   impossible if the calls are serial. Mirrors
   `test_autotranslate.py:1464-1466`.
2. `test_verdicts_are_persisted_on_the_calling_thread` - the patched
   `request_verdicts` records `threading.get_ident()` of its own thread; the
   `on_batch`/persist path records the thread that writes. Assert the writing
   thread is the test's own thread and differs from both worker threads. This is
   the regression guard for the silent Celery progress failure (D2). Mirrors
   `test_autotranslate.py:1552-1565`.
3. `test_each_seat_keeps_its_own_batch_order` - two units, batch size 1, so each
   seat produces two batches; assert every `JudgeVerdict` row pairs the right
   unit with the right result for both seats, i.e. the `cursor` invariant of D4
   survived interleaving.
4. `test_a_failing_seat_keeps_the_other_seats_verdicts` - seat 2 raises after
   seat 1 has persisted at least one batch; assert the exception propagates out
   of `run_judge_batch` **and** seat 1's rows are in the database (D5).

Existing tests that must keep passing unchanged in intent:
`test_both_seats_judge_every_string`, `test_no_seat_may_lower_the_other`,
`test_the_same_holds_when_the_strict_seat_votes_second`,
`test_verdict_takes_the_higher_severity`,
`test_unparsed_neither_raises_nor_lowers_the_other_seat`,
`test_every_verdict_of_one_run_shares_the_run_id`,
`test_each_seat_votes_once_per_round`,
`JudgeIncrementalPersistenceTest`, `JudgeMaxLengthRepairTest`,
`JudgeGlossaryRepairLockTest`.

## Verification

1. `./rundev.sh test weblate/trans/tests/test_judge_loop.py` - green after Task
   1 alone (harness rework is behaviour preserving), and green again after Tasks
   2-3.
2. `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_round.py`
   - the collegium, autotranslate projection and round tests are the ones that
   would notice a semantic change.
3. Scoped lint per the repository recipe: `uv run prek run --files <touched
   files> --skip typos --skip reuse --skip kingfisher-auto`.
4. Dev run against a real component with the judge enabled: confirm from
   `./rundev.sh logs -f weblate` that `judge batch N/M ok` lines for both seat
   models interleave in time, and that `GET /api/tasks/<uuid>/` keeps advancing
   `progress` during the whole judging phase. A frozen `progress` with
   advancing batch logs is the D2 failure and must block the change.
5. Production measurement after deployment, on a component of comparable size:
   record seat-1 and seat-2 first/last batch timestamps and compare judging
   wall clock against the 38 min 39 s + ~39 min baseline above. Write the
   numbers to `docs/llm-first/measurements/`.

## Out of scope

- Parallelising batches inside one seat (own plan, own rate-limit evidence).
- Any change to `JUDGE_BATCH_SIZE` or to the seat models: both invalidate the
  measurement under R3.
- The repair engine's own concurrency: already handled by
  `fetch_machinery_matches`.
- The `judge-monitor` helper used to observe the current production run.

## Task checklist

- [ ] Task 1: deterministic per-seat harness; `sorted(models)` assertion; file
      green before any production-code change
- [ ] Task 2: `_SeatJob`, `_SEAT_DONE`, `_run_seats`, imports
- [ ] Task 3: `run_judge_batch` builds jobs and calls `_run_seats`
- [ ] Tests 1-4 added and each one verified to fail with the fan-out reverted
- [ ] Verification steps 1-3 recorded
- [ ] Dev run (step 4) observed, progress advancing
- [ ] Commit and push
- [ ] Production measurement (step 5) after a separate deploy approval
