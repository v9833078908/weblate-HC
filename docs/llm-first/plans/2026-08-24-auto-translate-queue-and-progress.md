# Auto-translate: bound the judge run, keep what it paid for, stop lying about the queue

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task. Use `test-driven-development` and
> `verification-before-completion` where specified below.

**Status:** implemented, merged to `main`, deployed to production 2026-08-24 (rollout steps 1-2). Post-deploy confirmation on the running container and the first real-run log reading (rollout steps 3-5) are still open. Amended after the 2026-08-24 review round: Redis `visibility_timeout` added to Task 8, redelivery-cost claims made honest in Task 8, `JUDGE_SEATS` placement fixed in Task 4, judge copy fixed in Task 10, verification extended.

**Goal:** A judge batch request always ends. A judge run that ends early keeps the verdicts it already paid for. One judge run never starves automatic translation in other projects. The UI says whether a run is queued, running, how far along it is, and whether it survived a restart.

**Architecture:** One new per-batch seam inside `request_verdicts()` carries three consumers that all previously wanted their own: verdict persistence, progress ticks, and per-request logging. The HTTP body is read under an absolute wall-clock deadline we hold ourselves, because `httpx`'s `read` timeout bounds one read operation and a drip-feeding peer resets it forever. Progress reuses the existing `set_progress`/`get_task_progress`/`TaskSerializer`/`loader-bootstrap.js` chain, with a split `progress_range` because `set_progress` has no clamp. Queue starvation is configuration, not code.

**Tech Stack:** Django, `httpx2` 2.9.1, Celery (Redis broker), PostgreSQL, crispy-forms (bootstrap5), pytest, `weblate.utils.tests.http_mock`, Ruff.

---

## Provenance

This plan is the merge of two independent investigations of the same 2026-08-24 production incident. The second is archived at `docs/llm-first/archive/2026-08-24-judge-run-resilience.md`; its deadline work, per-batch persistence, decisions, and hang-mechanism evidence are folded in below as Tasks 1-3.

Two corrections the merge forced, both against this document's earlier draft:

1. **The hang mechanism was wrong.** The earlier draft read the 24-minute silence as "~12 silent 120 s timeouts". That is impossible - see "Mechanism" below. It was one stall inside a single request.
2. **Task 8's cost claim was overstated.** `_cached_verdict` does not make redelivery free; verdicts are written per *seat*, so a restart mid-seat re-pays every batch of that seat. Task 3 is what actually makes Task 8 cheap, and the two now ship together.

`docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md` Task 6 was amended in the same change: the seam signature gained arguments, and the progress plumbing this plan ships is removed from its scope. That task retains the shared preview scope, the global cap, cap-aware `progress_steps`, and the completion summary.

---

## Incident

Production, `l10n.herocraft.com`, 2026-08-24, all times UTC. Read-only investigation.

### Defect A: a judge run wedged and blocked the whole site

Run `feca65be`, `victory-banner/common/fr`, 448 strings.

| Phase | Model | Window | Requests | Cost |
| --- | --- | --- | --- | --- |
| Pre-translation | `google/gemini-2.5-flash` | 09:12:11-09:50:32 | 106 | $0.486 |
| Seat 1 | `deepseek/deepseek-v4-pro` | 09:50:46-10:23:57 | 90 of 90 | $0.218 |
| Seat 2 | `qwen/qwen3-235b-a22b-2507` | 10:24:08-10:32:41 | 47 of 90 | $0.018 |

Seat 1 wrote all 448 verdicts in a single 0.3-second burst at 10:23:57 - the signature of one transaction after the last batch (`judge_loop.py:393-397`). Seat 2 then stopped for 26 minutes and was revoked manually at 10:59:19. Its 47 completed batches, roughly 235 paid model answers, existed only in worker memory and were discarded. The `LLMUsageLog` rows remain, so the money is recorded and the work is gone.

### Defect B: a container restart silently truncated a different run

| Time | Event |
| --- | --- |
| 09:45:57.98 | `need-for-greed/glossary/nl` auto-translate commits 20 of 302 strings. |
| 09:46:00.71 | The container is recreated. The Celery message was already acked, so it is never redelivered. 282 strings never translated, no error surfaced. |

The producer read 20/302 as "the Dutch glossary didn't work". It worked; it was killed.

### Defect C: a queued run was indistinguishable from a running one

| Time | Event |
| --- | --- |
| 09:56:07 | `need-for-greed/ui/pl` auto-translate is enqueued. `CELERY_TASK_ROUTES` (`weblate/settings_docker.py:1440`) sends every `auto_translate*` task to the `translate` queue, which runs `--concurrency 1` and was held by Defect A. |
| 09:56:08-10:32 | The browser polls `/api/tasks/45343f20-.../` roughly once a second for 34 minutes. The response is a 55-byte PENDING body the whole time. The UI says "Automatic translation in progress". |
| 10:59:31 | The Polish run finally starts, 12 seconds after the revoke. |

The producer's complaint - "Polish from UI didn't work either" - is this: a 34-minute spinner for a task that had not started, with nothing distinguishing it from one that had.

### Mechanism of the 26-minute hang

`timeout=JUDGE_REQUEST_TIMEOUT` (`judge.py:395`, constant `120` at `judge.py:40`) does reach the request, verified directly:

```console
$ uv run --no-sync python -c "..."
extensions: {'timeout': {'connect': 120, 'read': 120, 'write': 120, 'pool': 120}}
httpx2 version: 2.9.1
```

`read` bounds **one read operation**, not the request. Any byte resets it.

Three facts rule out the "sequence of silent timeouts" reading:

1. A silent peer trips the 120 s read timeout. Twelve of those would each end their batch as `UNPARSED` and the loop would march on through batches 48-90, completing the seat. The seat never completed in 26 minutes.
2. Measured during the hang: worker state `S (sleeping)`, kernel `wchan` `do_sys_poll`, exactly one outbound socket to `104.18.3.115:443` (Cloudflare in front of OpenRouter), `ESTABLISHED`, send and receive queues both `00000000`, **no retransmit and no keepalive timer armed**, unchanged across six samples spanning 6.5 minutes. A pending read timeout implies an armed timer.
3. `LLMUsageLog` gained zero rows across four samples spanning 10:38-10:43, then ten rows at once around 10:56. `_record_usage` runs only when a payload arrived (`judge.py:492-494`), so absence of rows alone does not discriminate - but the burst does: twelve consecutive failures followed by ten consecutive successes is not the shape of a per-request timeout.

The drip cannot be in the response body: the payload sets `"stream": false` (`judge.py:452`), `_parse_reply` consumes one JSON document, and 137 earlier batches in the same run parsed fine. So the keepalives are transport level - chunked-encoding or TLS-layer traffic that never reaches `response.json()` but resets every read timer between the kernel and `httpx`.

Nothing in the judge path holds a clock a peer cannot reset.

**One honest gap.** The socket survey printed only the remote address, not the local port or inode, so "the same socket throughout" is observed, not proven. Task 1 exists to settle this from logs rather than inference, and it runs first. The deadline in Task 2 is correct under either reading - harmless if the timeouts theory were right, essential if it is wrong.

### What is deliberately NOT in scope

- **Switching the judge to SSE.** Streaming would also stop billing on an aborted request, which is real money, but it changes the parse path, the usage path and the provider contract (`response_format` + `require_parameters` + streaming) at once, on the only semantic validator in the product. The measured defect is a missing clock, not the response format.
- **A dedicated `judge` Celery queue.** Worker `--queues` arguments are generated by `/app/bin/start` inside the upstream image (`deploy/Dockerfile:15` reuses `weblate/weblate:2026.8.0.0` and only replaces Python code). The image exposes `CELERY_TRANSLATE_OPTIONS` (`docs/admin/install/docker.rst:394-432`) but no `CELERY_JUDGE_OPTIONS`. Task 9 buys the same relief with one env var.
- **A judge cost warning.** Already shipped: `autoform.html:32-45` renders the matched row count and `judge_request_estimate`, `:46-50` warns when verdicts already exist. Task 10 adds only wall-clock time.
- **Retry/backoff policy for judge HTTP calls.** `judge.py:501-502` already retries once on 429/403. Task 1 makes failures visible; changing the policy is a separate measurable decision.
- **The cap, the shared preview scope, and the completion summary.** Owned by Plan 02 Task 6.
- **Anything touching production.** The rollout section at the end needs its own explicit approval.

---

## Decisions

1. **Keep `"stream": false` in the payload.** Read the response as an HTTP stream anyway: owning the read loop is the only way to hold a clock the peer cannot reset. The body is still one JSON document; only who concatenates it changes.
2. **One new operator setting, `JUDGE_REQUEST_DEADLINE`, default 300 s.** Observed healthy batches finish in 7-16 s. 300 s is a hard ceiling, not a target. `JUDGE_REQUEST_TIMEOUT = 120` stays as the stall timeout for a genuinely silent peer.
3. **A deadline hit is a transport failure, not an opinion.** It marks the batch `UNPARSED`, exactly like an HTTP 500 does today. No new verdict semantics, no new state, nothing for a producer to interpret.
4. **Persist per batch, not per seat.** Once a batch's results exist they are paid for. A run that dies keeps them. This is also what makes `acks_late` (Task 8) cheap rather than expensive.
5. **Queue isolation is configuration.** Raise `translate` concurrency and say what it costs.
6. **Progress gets its own range slice.** `set_progress` has no clamp; monotonicity in this codebase comes from non-overlapping ranges (`autotranslate.py:290-292`). Phase 1 and phase 2 of a judge run therefore cannot share `(0, 100)`.
7. **`acks_late` never ships without a matching Redis `visibility_timeout`.** The broker default is 3600 s (`settings_docker.py` sets no `broker_transport_options` anywhere - verified by grep). A judge run measured 50 minutes for 448 strings; at the 2000-string cap it runs for hours. With early acks the message is deleted on delivery and the timeout never matters; Task 8 makes it matter, and without a raised timeout a *healthy* long run would be silently double-delivered at the one-hour mark - duplicate concurrent execution, the worst failure mode this plan touches.

### Residual risk, stated

A hang **before response headers arrive** is bounded only by the 120 s stall timeout, not by the deadline, because `client.send(stream=True)` returns after headers and no clock can be checked while blocked inside it. Acceptable: transport keepalives accompany a response already in flight, and 120 s of true silence does trip. If measurement later shows a pre-header hang, the fix is a watchdog that closes the client - a bigger change needing its own plan.

---

## The seam, decided once

Three plans independently proposed a callback at the same place with three incompatible signatures. It is settled here, before anyone writes code:

```text
request_verdicts()  ->  run_judge_batch()  ->  AutoTranslate.process_judge()
```

```python
OnBatch = Callable[[Sequence[JudgeRequest], Sequence[JudgeResult]], None]
```

| Concern | Owner | Uses the arguments? |
| --- | --- | --- |
| Per-batch verdict persistence | Task 3, `run_judge_batch` | Yes - both |
| Progress ticks | Task 4, `process_judge` | No - `lambda *_: tick()` |
| Per-request logging | Task 1, inside `_post_batch` | N/A, not on this seam |
| Cap, scope, preview, summary | Plan 02 Task 6 | No |

Contract, asserted by tests in Tasks 1 and 3:

- `request_verdicts()` calls `on_batch` exactly once per **completed** batch, in input order, whether the batch parsed or became `UNPARSED`.
- A 403/429 retry is one completed batch and therefore one call, not two.
- A cached unit makes no client call and therefore produces no call.

---

## Task 1: Log every judge request's outcome and duration

Runs first: it settles the mechanism dispute from evidence instead of inference, and it is the cheapest task here. `judge_loop.py` contains **zero** log statements; `judge.py` has one `LOGGER.exception` at `:294`. A 120 s timeout logs nothing, which is why 26 minutes of production silence looked identical to a hang.

**Files:**
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Test: `weblate/trans/tests/test_judge_client.py`, `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the failing tests

In `test_judge_client.py`, using the existing `http_mock.register("POST", CHAT_URL, ...)` API (`weblate/utils/tests/http_mock.py:309` for `register_callback`):

```python
class JudgeRequestLoggingTest(SimpleTestCase):
    def test_batch_outcome_is_logged_with_elapsed(self) -> None:
        with self.assertLogs("weblate.trans.judge", level="INFO") as logs:
            ...  # one 200 batch via the module's existing helper
        joined = "\n".join(logs.output)
        self.assertIn("batch", joined)
        self.assertIn("ms", joined)

    def test_failed_batch_is_logged_at_warning(self) -> None:
        with self.assertLogs("weblate.trans.judge", level="WARNING") as logs:
            ...  # register status_code=500
        self.assertTrue(any("500" in line for line in logs.output))
```

In `test_judge_loop.py`:

```python
class JudgeLoopLoggingTest(ViewTestCase):
    def test_run_and_seat_are_logged(self) -> None:
        with self.assertLogs("weblate.trans.judge_loop", level="INFO") as logs:
            self.run_batch(...)  # reuse this module's existing helper
        joined = "\n".join(logs.output)
        self.assertIn("seat", joined)
```

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py -p no:randomly --no-cov -q
```

Expected: `AssertionError: no logs of level INFO or higher triggered`.

### Step 3: Instrument `_post_batch`

In `weblate/trans/judge.py`, wrap the `_post_batch` call site in the retry loop (`:492`) so every attempt records model, batch position, status and wall-clock duration:

```python
            started = monotonic()
            response = _post_batch(payload, model)
            elapsed_ms = int((monotonic() - started) * 1000)
            if response.payload is None or (
                response.status_code is not None and response.status_code >= 400
            ):
                LOGGER.warning(
                    "judge batch %d/%d failed: model=%s status=%s elapsed=%dms",
                    position + 1, len(batches), model, response.status_code, elapsed_ms,
                )
            else:
                LOGGER.info(
                    "judge batch %d/%d ok: model=%s strings=%d elapsed=%dms",
                    position + 1, len(batches), model, len(batch), elapsed_ms,
                )
```

Add `from time import monotonic` and a module `LOGGER = logging.getLogger(__name__)` if absent.

This is the measurement that resolves the dispute: under the timeouts reading, production will show repeated `failed ... elapsed=120000ms` lines; under the stall reading, one line with an elapsed far above that, or none at all until the deadline from Task 2 fires.

### Step 4: Instrument `run_judge_batch`

In `weblate/trans/judge_loop.py`, add a module logger and three statements - run start (after `run_id` is assigned at `:344`), seat completion (in the seat loop), and each repair attempt:

```python
    LOGGER.info(
        "judge run %s: %d strings, %d writable, %d cached",
        run_id, len(units), len(writable_ids), len(cached_ids),
    )
```

```python
            LOGGER.info(
                "judge run %s: seat %d done, %d strings judged with %s",
                run_id, seat, len(request_units), model,
            )
```

These are operational messages, not user-facing strings, so they are not wrapped in `gettext` - consistent with the audit/add-on log convention in `AGENTS.md`.

### Step 5: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py -p no:randomly --no-cov -q
git add weblate/trans/judge.py weblate/trans/judge_loop.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): log every batch request outcome, model and duration"
```

---

## Task 2: Bound one judge batch request by a total deadline

**Files:**
- Modify: `weblate/utils/requests.py`, `weblate/utils/tests/test_requests.py`
- Modify: `weblate/trans/defaults.py`, `weblate/settings_docker.py`, `weblate/settings_example.py`
- Modify: `weblate/trans/judge.py`, `weblate/trans/tests/test_judge_client.py`

### Step 1: Write the failing deadline tests

In `test_judge_client.py`, using `http_mock.register_callback()` (`weblate/utils/tests/http_mock.py:309`) to return an `httpx2.Response` whose stream is a generator sleeping between chunks:

- a peer that drips forever with `JUDGE_REQUEST_DEADLINE` set to `0.3` produces `[UNPARSED] * len(batch)` and returns in well under a second; **assert the elapsed wall time is bounded**, because a per-read timeout makes this test hang rather than fail;
- the same drip inside a two-batch run marks only the affected batch unparsed and still processes the next batch;
- a body delivered in several chunks inside the deadline parses normally, so chunked transfer alone is not a failure;
- a deadline hit records **no** `LLMUsageLog` row, because no usage block ever arrived;
- a deadline hit is not a 403/429 and consumes no retry: assert exactly one HTTP call.

In `weblate/utils/tests/test_requests.py`, cover the helper directly: it yields a streamed response, it applies `RuntimeRedirectValidators`, and it does not follow redirects when asked not to.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
```

Expected: `stream_validated_url` and `JUDGE_REQUEST_DEADLINE` do not exist.

### Step 3: Add the streaming helper

`weblate/utils/requests.py` already has the private `_open_url()` context manager (line 850) and `create_http_client()` (line 584). Add a public sibling of `fetch_validated_url()` (line 800) that yields an unread streamed response:

```python
@contextmanager
def stream_validated_url(
    method: str,
    url: str,
    *,
    follow_redirects: bool = True,
    allow_private_targets: bool = False,
    private_allowlist: list[str] | tuple[str, ...] = (),
    **kwargs,
) -> Generator[httpx2.Response, None, None]:
```

It delegates to `_open_url()` with `RuntimeRedirectValidators`, mirroring how `fetch_validated_url()` builds validators. It never calls `raise_for_status()`: the judge inspects `status_code` itself. Do not change `fetch_validated_url()`; other callers keep the buffered path.

Docstring constraint, written down because it is not obvious: the caller's deadline only applies while reading the body. With `follow_redirects=True` each redirect hop is a separate `send` bounded only by the connect/read timeouts, so a redirect chain can outlast the caller's budget. The judge passes `follow_redirects=False`; state in the docstring that callers holding their own deadline must do the same.

**Do not reuse `open_restricted_asset_url()` (`:879`) instead.** It is already a
public `@contextmanager` over `_open_url()` and looks like a free substitute,
but it passes `RestrictedAssetRedirectValidators` (`:892`) where
`fetch_validated_url()` passes `RuntimeRedirectValidators` (`:813`). Swapping
the redirect validator on an outbound path the threat model documents is a
security change disguised as a simplification.

### Step 4: Add the setting

Follow the existing judge settings exactly (`JUDGE_BATCH_SIZE` is the closest model):

- `weblate/trans/defaults.py`: `DEFAULT_JUDGE_REQUEST_DEADLINE = 300.0`;
- `weblate/settings_docker.py`: `JUDGE_REQUEST_DEADLINE = get_env_float(...)` keyed `WEBLATE_JUDGE_REQUEST_DEADLINE`, with the other `JUDGE_*` entries;
- `weblate/settings_example.py`: the same default in the surrounding comment style;
- extend `validate_judge_configuration()` (`judge.py:85`) to reject a non-positive deadline **before any paid call**.

### Step 5: Read the body under the deadline

Rewrite `_post_batch()` (`judge.py:382-408`). It keeps its signature and its `_BatchResponse` return, so `request_verdicts()` and every retry decision are untouched:

```python
deadline = monotonic() + settings.JUDGE_REQUEST_DEADLINE
with stream_validated_url(
    "POST",
    OPENROUTER_CHAT_COMPLETIONS_URL,
    headers={...},           # built inline, unchanged
    json=payload,
    timeout=JUDGE_REQUEST_TIMEOUT,
    follow_redirects=False,
) as response:
    buffer = bytearray()
    for chunk in response.iter_bytes():
        if monotonic() > deadline:
            return _BatchResponse(None, None)
        buffer += chunk
```

Then decode `buffer` as JSON with the existing tolerance: an unreadable body becomes `body = None`, exactly as today. Keep the comment explaining why the bearer token is built inline. Keep the blanket `except Exception` so a transport error still yields `_BatchResponse(None, None)`; the deadline path must produce the same shape so `judge.py:495-505` marks the batch `UNPARSED` with no new branch.

Log the abort at warning level with the model and elapsed time - it is the only trace a silent abort leaves, and it composes with Task 1's per-batch line.

### Step 6: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
git add weblate/utils/requests.py weblate/utils/tests/test_requests.py weblate/trans/defaults.py weblate/settings_docker.py weblate/settings_example.py weblate/trans/judge.py weblate/trans/tests/test_judge_client.py
git commit -m "feat(judge): bound a batch request by a total wall-clock deadline"
```

---

## Task 3: Persist verdicts per batch, not per seat

This creates the seam. `run_judge_batch()` calls `request_verdicts()` for a whole seat (`judge_loop.py:387`) and writes only afterwards (`judge_loop.py:393-397`), so killing the task discards every completed batch of the seat in flight. In the incident that was 47 batches, ~235 paid answers, $0.018 - at the 2000-string cap it is a whole seat.

**Files:**
- Modify: `weblate/trans/judge.py` (add `on_batch` to `request_verdicts`)
- Modify: `weblate/trans/judge_loop.py` (write per batch)
- Test: `weblate/trans/tests/test_judge_client.py`, `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the failing tests

Assert the seam contract from "The seam, decided once":

```python
class JudgeOnBatchTest(SimpleTestCase):
    def test_called_once_per_completed_batch_in_order(self) -> None:
        seen: list[tuple[int, int]] = []
        ...  # 3 batches via JUDGE_BATCH_SIZE, on_batch appends (len(req), len(res))
        self.assertEqual(len(seen), 3)
        for requests_len, results_len in seen:
            self.assertEqual(requests_len, results_len)

    def test_retry_is_one_tick(self) -> None:
        # register 429 then 200, mock the sleep
        # assert two HTTP calls but exactly one on_batch call
        ...

    def test_unparsed_batch_still_ticks(self) -> None:
        # register status_code=500; assert one call whose results are all UNPARSED
        ...
```

And in `test_judge_loop.py`, the durability property that is the point of the task:

```python
class JudgeIncrementalPersistenceTest(ViewTestCase):
    def test_verdicts_from_completed_batches_survive_a_mid_seat_crash(self) -> None:
        # make the third batch raise inside request_verdicts
        # assert JudgeVerdict rows exist for the first two batches' units
        ...
```

That last test is the one that must fail before the change and pass after. Verify it does both.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py -p no:randomly --no-cov -q
```

### Step 3: Add `on_batch` to `request_verdicts`

```python
on_batch: Callable[[Sequence[JudgeRequest], Sequence[JudgeResult]], None] | None = None
```

Call it after the batch's results are appended at `judge.py:505` and before the inter-batch sleep at `:506`, with that batch's own requests and results - never the accumulated lists.

### Step 4: Write per batch in `run_judge_batch`

Add the same `on_batch` parameter to `run_judge_batch`. Replace the post-loop `transaction.atomic()` block at `judge_loop.py:393-397` with a per-batch writer that relies on the documented input-order guarantee:

```python
            cursor = 0

            def persist(batch_requests, batch_results) -> None:
                nonlocal cursor
                batch_units = request_units[cursor : cursor + len(batch_requests)]
                cursor += len(batch_requests)
                with transaction.atomic():
                    for unit, request, result in zip(
                        batch_units, batch_requests, batch_results, strict=True
                    ):
                        _write_verdict(
                            unit, request, seat, attempt, run_id, result, model
                        )
                if on_batch is not None:
                    on_batch(batch_requests, batch_results)

            results = request_verdicts(
                requests,
                model=model,
                project_slug=project_slug,
                project_context=project_context,
                on_batch=persist,
            )
```

`persist` is called synchronously inside the iteration, so capturing `seat`, `model` and `attempt` from the enclosing loop is correct. Delete the old post-loop write; leaving both double-writes every verdict.

The cache reuses these rows only when **both** seats wrote for the same `(run_id, attempt)` (`_cached_verdict`, `judge_loop.py:199-206`). This is why per-batch persistence pays off in the redelivery scenario Task 8 cares about - the common crash point is mid-seat-2, and seat 1 is then fully cached. A crash mid-seat-1 still re-pays seat 1 from its interruption point; nothing avoids that, and nothing here pretends to.

### Step 5: Update existing fakes

Every patched `run_judge_batch` fake in `test_judge_autotranslate.py` must accept `on_batch=None`. Keep their existing behaviour assertions.

### Step 6: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_autotranslate.py -p no:randomly --no-cov -q
git add weblate/trans/judge.py weblate/trans/judge_loop.py weblate/trans/tests/
git commit -m "fix(judge): persist verdicts per batch so a killed run keeps them"
```

---

## Task 4: Report progress during the judge phase

Every auto-translate path reports progress except the judge phase:

| Path | `progress_steps` | `set_progress` |
| --- | --- | --- |
| `process_others` | `autotranslate.py:602` | `:612` per unit |
| `process_mt` | `:650`, `:684` | `:657` per batch, `:735` |
| `BatchAutoTranslate.perform` | `:968` | `:1137` per translation |
| `process_judge` (`:737`) | never set | never called |

`set_progress` (`:339-346`) no-ops when `progress_steps` is 0, so the 35-minute-per-seat judge phase reports nothing.

The browser side is already complete, so this task needs **no frontend work**: `PROGRESS` state -> `get_task_progress` (`weblate/utils/celery.py:237-251`) -> `TaskSerializer.progress` (`weblate/api/serializers.py:4149-4193`) -> `/api/tasks/<id>/` (`weblate/api/views.py:4877-4983`) -> the `task:<id>` message tag from `show_message` (`weblate/trans/templatetags/translations.py:743-757`) -> `weblate/templates/message.html` -> the poller at `weblate/static/loader-bootstrap.js:1604-1683`, which reads `data.progress` and sets the bar width. `bulk_accept_user_suggestions` (`weblate/trans/tasks.py:370`, reporting via `:360`) is the closest existing example.

### The trap this task must not fall into

`set_progress` has **no clamp**. Monotonicity in this codebase comes from non-overlapping ranges, stated at `autotranslate.py:290-292`. Phase 1 of `process_judge` calls `process_mt` (`:769`), which with a single engine has `incremental=True` and `progress_base = 0` (`:648`), so its final tick at `:735` drives the bar to **100%**. Setting `progress_steps = len(units) * 2` afterwards and ticking from the first batch gives:

```text
low + (high - low) * current // progress_steps
  = 0 + 100 * 5 // 896
  = 0
```

The bar collapses from 100% to 0% and crawls up again - worse for the producer than today's freeze. A test that watches only judge-phase ticks sees `5, 10, 15, ...`, which is sorted, and passes against the bug.

**Files:**
- Modify: `weblate/trans/autotranslate.py` (`process_judge`)
- Test: `weblate/trans/tests/test_judge_autotranslate.py`

### Step 1: Write the failing test

It must observe progress across the **whole task**, phase 1 included, not just the judge ticks:

```python
class JudgeProgressTest(ViewTestCase):
    def test_progress_never_goes_backwards_across_both_phases(self) -> None:
        reported: list[int] = []

        with (
            mock.patch("weblate.trans.autotranslate.current_task") as task,
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=self.fake_judge_batch,  # ticks on_batch per batch
            ),
        ):
            task.request.id = "test-task-id"
            task.update_state.side_effect = lambda **kw: reported.append(
                kw["meta"]["progress"]
            )
            self.perform_judge_run(...)  # reuse this module's existing helper

        self.assertTrue(reported, "no progress was reported at all")
        self.assertEqual(reported, sorted(reported), "progress went backwards")
        self.assertLessEqual(max(reported), 100)
        self.assertGreater(
            len([p for p in reported if p > 10]),
            0,
            "the judge phase never reported past the MT slice",
        )
```

The `sorted()` assertion only has teeth because `reported` now spans phase 1 too. Confirm that by temporarily removing the range split from Step 2 and watching this test fail.

Also assert the slice boundary directly, matching Plan 02 Task 6's convention: set the instance's `progress_range` to `(20, 40)` and assert MT reports inside `(20, 22)` while judge progress stays inside `(22, 40)`.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py::JudgeProgressTest -p no:randomly --no-cov -q
```

### Step 3: Split the range and tick the seam

In `process_judge`, compute the split once before phase 1, give phase 1 the first tenth, and restore the original range at the end:

```python
        # set_progress has no clamp; monotonicity comes from non-overlapping
        # ranges (autotranslate.py:290-292). Phase 2 restarts its counter, so
        # sharing one range would send the bar backwards. The judge phase is
        # two LLM calls per string against MT's one batch fetch, so it takes
        # nine tenths of the bar.
        base_low, base_high = self.progress_range
        split = base_low + (base_high - base_low) // 10
```

Phase 1 (`:764-771`) runs with `self.progress_range = (base_low, split)`, restored in the existing `finally` alongside `unit_ids` and `target_state`.

Phase 2 runs with `(split, base_high)`:

```python
        judged = 0

        def tick(_requests, _results) -> None:
            nonlocal judged
            judged += len(_results)
            # Repair attempts re-judge units, so ticks can exceed the estimate.
            self.set_progress(min(judged, self.progress_steps))

        self.progress_range = (split, base_high)
        self.progress_steps = len(units) * len(JUDGE_SEATS)
        try:
            verdicts = run_judge_batch(
                units, writable_ids=writable_ids, user=self.user, on_batch=tick
            )
        finally:
            self.progress_range = (base_low, base_high)
```

Define `JUDGE_SEATS = (1, 2)` next to the other module constants in `weblate/trans/judge.py` and import it in `judge_loop.py` (which already imports from `judge`) and in `autotranslate.py` (which already imports `JudgeError` from `judge` at `:24`), rather than hardcoding `2` in two files. Do **not** define it in `judge_loop.py`: nothing in `judge.py` may import from `judge_loop` (the dependency direction is `judge_loop -> judge`), so a constant living in `judge_loop` could never be shared without an import cycle. The seat loop in `run_judge_batch` iterates `JUDGE_SEATS` instead of its literal. The `min(...)` clamp is required: a repair attempt re-judges units, and without it `judged` can exceed `progress_steps` and push the reported percentage past `base_high`. Cached units skip a seat (`judge_loop.py:384-385`) so the total can also undershoot; the bar then stops short of `base_high`, which is monotonic and acceptable.

### Step 4: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_autotranslate.py -p no:randomly --no-cov -q
```

`test_autotranslate.py` is known flaky under xdist - compare against a baseline run on unmodified `HEAD` before blaming this change.

```bash
git add weblate/trans/autotranslate.py weblate/trans/judge_loop.py weblate/trans/tests/test_judge_autotranslate.py
git commit -m "feat(judge): report progress during the judge phase"
```

---

## Task 5: Prove or disprove the nested-form defect

`AutoForm` is the only form in `weblate/trans/forms.py` that creates a `FormHelper` without setting `form_tag = False` (~30 others do: lines 761, 872, 905, 1044, 1628, 1808, 1940, 2010, 3048, 3364, 4417, 4839). Meanwhile `autoform.html` owns the `<form>` (line 5), calls `{% crispy autoform %}` (line 51), and puts the submit button at line 54 - outside crispy's output.

Per HTML5 parsing a nested `<form>` start tag is ignored but its `</form>` closes the outer form, orphaning the Apply button. Production POSTs demonstrably succeed, so **do not assume the bug is real.** Measure first; if the test passes, delete it and skip Task 6.

**Files:**
- Test: `weblate/trans/tests/test_autotranslate.py` (append)

### Step 1: Write the test

```python
def max_form_depth(html: str) -> int:
    """Peak <form> nesting depth in rendered HTML.

    A source-level check for "is there an unclosed form" passes against
    nested forms, because the source stays balanced. Only depth catches it.
    """
    depth = 0
    peak = 0
    for match in re.finditer(r"<(/?)form\b", html, re.IGNORECASE):
        if match.group(1):
            depth -= 1
        else:
            depth += 1
            peak = max(peak, depth)
    return peak


class AutoFormRenderingTest(ViewTestCase):
    def test_autoform_does_not_nest_forms(self) -> None:
        response = self.client.get(self.translation.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("auto_translation", html)
        self.assertLessEqual(
            max_form_depth(html),
            1,
            "crispy rendered a nested <form>; the Apply button falls outside it",
        )
```

Add `import re` if absent.

### Step 2: Run it and branch

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py::AutoFormRenderingTest -v
```

- **FAIL** (depth 2): the defect is real. Commit the test, proceed to Task 6.
- **PASS** (depth 1): crispy is not emitting a form here. **Delete `AutoFormRenderingTest` and `max_form_depth`, skip Task 6,** and record the finding in this plan's status line.

```bash
git add weblate/trans/tests/test_autotranslate.py
git commit -m "test(trans): assert the automatic translation form is not nested"
```

---

## Task 6: Set `form_tag = False` on `AutoForm`

**Skip if Task 5's test passed.**

**Files:** Modify `weblate/trans/forms.py:1352`

### Step 1: Apply the fix

```python
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
```

### Step 2: Verify, then verify the test has teeth

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py::AutoFormRenderingTest -v
```

Expected: PASS. Then revert the new line, re-run, confirm FAIL, restore. A regression test that passes against the bug is worthless - two of three such tests in the loc-kit work did exactly that.

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py -v
git add weblate/trans/forms.py
git commit -m "fix(trans): stop AutoForm from rendering a nested form"
```

---

## Task 7: Tell the user their run is queued, not running

`weblate/trans/views/edit.py:1585` sets "Automatic translation in progress" unconditionally. In the incident that message was shown for 34 minutes for a task that had not started.

**Files:** Modify `weblate/trans/views/edit.py:1562-1593`; test in `weblate/trans/tests/test_autotranslate.py`

### Step 1: Write the failing test

`weblate/settings_test.py:57` sets `CELERY_TASK_ALWAYS_EAGER = True`, so force the Celery branch and fake a busy queue:

```python
class AutoTranslateQueueMessageTest(ViewTestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_queued_behind_another_task_says_so(self) -> None:
        with (
            mock.patch("weblate.trans.views.edit.auto_translate.delay") as delay,
            mock.patch("weblate.trans.views.edit.get_queue_length", return_value=3),
        ):
            delay.return_value.id = "queued-task-id"
            response = self.client.post(
                reverse(
                    "auto_translation",
                    kwargs={"path": self.translation.get_url_path()},
                ),
                {"mode": "translate", "q": "state:empty", "auto_source": "mt",
                 "engines": ["weblate"], "threshold": 80},
                follow=True,
            )
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("queue" in m.lower() for m in messages),
            f"expected a queue-position message, got {messages}",
        )
```

### Step 2: Prove RED, then implement

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py::AutoTranslateQueueMessageTest -v
```

Import the existing helper (`weblate/utils/celery.py:216`) and replace the message at `:1585`:

```python
from weblate.utils.celery import get_queue_length
```

```python
        queued_ahead = get_queue_length("translate")
        if queued_ahead > 1:
            message = ngettext(
                "Automatic translation queued: %d run is ahead of it. "
                "You can close this page.",
                "Automatic translation queued: %d runs are ahead of it. "
                "You can close this page.",
                queued_ahead - 1,
            ) % (queued_ahead - 1)
        else:
            message = gettext("Automatic translation in progress")
```

`get_queue_length` counts messages waiting, so the run just enqueued is included - hence `queued_ahead - 1`. It does not count the task a worker already holds, so this under-reports by one while a run executes; still strictly more honest than the current text. Ensure `ngettext` is imported.

### Step 3: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py -v
git add weblate/trans/views/edit.py weblate/trans/tests/test_autotranslate.py
git commit -m "feat(trans): report queue position when auto-translation is queued"
```

---

## Task 8: Survive a worker restart

Celery acks on delivery by default and `weblate/settings_docker.py:1427-1450` sets no `task_acks_late`, so Defect B's message was gone three seconds before the container came back: no redelivery, no error, 282 strings silently untranslated.

**This task depends on Task 3.** Without per-batch persistence, redelivering a judge run re-pays every batch of the seat that was in flight - `_cached_verdict` (`judge_loop.py:374`) only reuses verdicts that were actually *written*, and before Task 3 a seat writes nothing until its last batch. Do not land this task alone.

### Redelivery cost, stated honestly

With Task 3 landed, redelivery is cheaper than the seat-granularity status quo, but it is **not** "re-pay only the interrupted batch":

- The cache requires verdicts from **both seats for one `(run_id, attempt)`** (`_cached_verdict`, `judge_loop.py:199-206`). A unit judged by seat 1 but not yet by seat 2 has a single row and is re-judged by **both** seats. The realistic crash shape is mid-seat-2, where seat 1 is complete and fully cached, so redelivery re-pays seat 2 from its interruption point. A crash mid-seat-1 re-pays seat 1 likewise.
- `process_judge` phase 1 writes MT strings at needs-editing, which remain "writable" (`overwrite_existing=False` skips only *translated* strings, `:743-748`), so a redelivered judge run **re-pays phase-1 MT in full**. If the re-fetched MT output differs from the first delivery, the stored verdicts' `target_hash` no longer matches and everything is re-judged too. MT output is usually deterministic enough for this not to happen, but it is not guaranteed.
- For plain MT modes redelivery was already cheap: `overwrite_existing=False` skips translated strings and `q=state:<translated` narrows to the remainder.

Cheaper, not free. The alternative is Defect B's silent loss of 282 strings, so the trade is right.

### The companion constraint: Redis `visibility_timeout`

`acks_late` activates a setting nothing currently sets. The broker is Redis (`CELERY_BROKER_URL = REDIS_URL`, `settings_docker.py:1431`), no `CELERY_BROKER_TRANSPORT_OPTIONS` exists anywhere in the tree, and the default visibility timeout is 3600 s. While a task runs unacked past that, Redis requeues the message and another worker picks it up **while the original is still running**. Judge runs exceed one hour easily: the incident run measured 50 minutes for 448 strings, and the cap is 2000. Task 9 then guarantees a second slot exists to grab the duplicate. Two concurrent judge runs on the same translation write interleaved verdicts and pay twice.

Set the timeout above the worst-case run, in the Celery block of `settings_docker.py` (after `CELERY_RESULT_BACKEND`, ~`:1434`):

```python
# Long-running auto_translate tasks hold their message unacked for hours
# (acks_late). Redis must not make them visible to another worker meanwhile.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": get_env_int("WEBLATE_CELERY_VISIBILITY_TIMEOUT", 4 * 3600)
}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = CELERY_BROKER_TRANSPORT_OPTIONS
```

The result backend needs the same value because Celery warns on asymmetric visibility timeouts between broker and result backend when both are Redis. 4 h exceeds a 2000-string judge run at observed throughput (worst observed batch ~16 s; 2 seats x 400 batches x 16 s is 3.6 h, and typical is a tenth of that). It also delays redelivery of *other* queues' genuinely lost tasks to 4 h; nothing in this deployment relies on sub-hour redelivery, and `notify`/`memory`/`backup` tasks finish in seconds, so the trade is safe. The dev stack uses `settings_docker.py` too, so it picks the same default up with no extra configuration; the env var exists for operators, mirrored in `deploy/environment.example` next to the other `WEBLATE_CELERY_*` keys.

**Files:** Modify `weblate/trans/tasks.py:965-971` and `:1066-1071`, `weblate/settings_docker.py`, `deploy/environment.example`

### Step 1: Write the failing test

```python
class AutoTranslateDurabilityTest(SimpleTestCase):
    def test_auto_translate_acks_late(self) -> None:
        from weblate.trans.tasks import auto_translate, auto_translate_component

        for task in (auto_translate, auto_translate_component):
            self.assertTrue(task.acks_late, f"{task.name} acks early")
            self.assertTrue(
                task.reject_on_worker_lost, f"{task.name} is lost on worker death"
            )

    def test_visibility_timeout_covers_long_tasks(self) -> None:
        from django.conf import settings

        options = settings.CELERY_BROKER_TRANSPORT_OPTIONS
        self.assertGreaterEqual(options.get("visibility_timeout", 0), 4 * 3600)
        self.assertEqual(
            settings.CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS, options
        )
```

### Step 2: Prove RED, then add the flags

At `weblate/trans/tasks.py:965`, and identically at `:1066`:

```python
@app.task(
    trail=False,
    autoretry_for=(WeblateLockTimeoutError,),
    retry_backoff=600,
    retry_backoff_max=3600,
    acks_late=True,
    reject_on_worker_lost=True,
)
```

Add the `CELERY_BROKER_TRANSPORT_OPTIONS` block to `settings_docker.py` as shown above, and `WEBLATE_CELERY_VISIBILITY_TIMEOUT` to `deploy/environment.example`.

### Step 3: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py::AutoTranslateDurabilityTest -v
git add weblate/trans/tasks.py weblate/settings_docker.py deploy/environment.example weblate/trans/tests/test_autotranslate.py
git commit -m "fix(trans): redeliver auto-translation when a worker dies mid-run"
```

---

## Task 9: Give the `translate` queue a second slot

The `translate` worker runs `--pool=prefork --prefetch-multiplier=1 --concurrency 1`, so one judge run starves every other project - three tasks were queued behind the wedged one. Worker arguments come from `/app/bin/start` in the upstream image; the supported override is `CELERY_TRANSLATE_OPTIONS` (`docs/admin/install/docker.rst:394-432`).

Memory budget: `hc-srv15-localizer` has 7.7 GB with roughly 5.7 GB free, and `CELERY_WORKER_MAX_MEMORY_PER_CHILD` is 250 MB in production (`weblate/settings_docker.py:1436`). One extra prefork child is affordable.

**Files:** Modify `deploy/environment.example`, `dev-docker/docker-compose.yml`

### Step 1: Add the variable to the production example

Near `WEBLATE_WORKERS` (around `:138`):

```sh
# Automatic translation runs on the "translate" queue. A judge run occupies a
# slot for tens of minutes, so a single slot lets one project block all others.
CELERY_TRANSLATE_OPTIONS=--concurrency 2 --prefetch-multiplier 1
```

### Step 2: Mirror it in the dev stack

Add the same key to the `weblate` service `environment:` block in `dev-docker/docker-compose.yml`. Per `AGENTS.md` the environment block is baked in at container creation, so this needs a full `./rundev.sh` rebuild - **which requires explicit approval. Do not run it while implementing this task.**

After approval, confirm:

```bash
docker exec <dev-weblate-container> ps aux | grep "queues=translate"
```

Expected: the `translate` worker line shows `--concurrency 2`.

### Step 3: Commit

```bash
git add deploy/environment.example dev-docker/docker-compose.yml
git commit -m "chore(deploy): give the translate queue a second worker slot"
```

---

## Task 10: State the judge run's duration and blast radius

`autoform.html:40-44` already shows the worst-case request count but not how long it takes. Note the wording constraint: after Task 9 the claim "serialized across the whole site" is **false**, so the copy must describe contention, not serialization.

**Files:** Modify `weblate/templates/snippets/autoform.html:40-45`

### Step 1: Extend the estimate block

Inside the existing `{% if judge_row_count is not None %}` block, after the request-estimate paragraph:

```html
        <p class="text-muted" id="id_auto_duration_estimate">
          {% translate "A judge run holds an automatic translation slot for tens of minutes per few hundred strings, so other projects may wait." %}
        </p>
```

Do not write a slot count into translatable copy: it hardcodes `CELERY_TRANSLATE_OPTIONS` from Task 9 into a string that every translator then freezes, and it lies the moment an operator changes the concurrency.

Prose rather than a computed ETA on purpose: observed throughput on production varied from 7 s to 26 min per batch, so a specific number would mislead.

### Step 2: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_form.py -v
```

Then load a translation page for a judge-enabled component and confirm the paragraph renders under the request estimate.

```bash
git add weblate/templates/snippets/autoform.html
git commit -m "docs(trans): note judge run duration and slot contention"
```

---

## Task 11: Docs, threat model, changelog, and full verification

**Files:** `docs/admin/config.rst`, `docs/security/threat-model.rst`, `docs/changes.rst`

### Step 1: Document the setting

Add `JUDGE_REQUEST_DEADLINE` to `docs/admin/config.rst` in the existing alphabetical `JUDGE_*` run, matching the surrounding style: `.. setting::`, `.. versionadded:: 2026.8.1`, a `:setting:` cross-reference to `JUDGE_ENABLED`, and one sentence saying a batch exceeding it is recorded unparsed.

### Step 2: Update the threat model

`docs/security/threat-model.rst:241` ("Judge string batch to OpenRouter") describes this outbound request, and lines 116-120 describe the same flow in the asset table. Record that an outbound judge request is now bounded by `JUDGE_REQUEST_DEADLINE`, so a hostile or failing endpoint cannot hold a worker indefinitely. This is a claimed security property changing, which the "Conditions that change this model" section requires be recorded in the same change (`AGENTS.md`).

### Step 3: Changelog

Only user-visible changes belong here - Tasks 2, 3, 4, 7, 8. Tasks 1, 5, 6, 9, 10 are logging, a test, an unreleased-path fix, configuration, and copy.

```rst
* Automatic translation now reports queue position and progress during LLM
  judge runs, bounds each judge request by a deadline, keeps verdicts from
  completed batches, and survives a worker restart instead of stopping
  silently.
```

Automatic translation has been released for a long time, so this entry is required even though the judge itself is newer.

### Step 4: Confirm the superseded plan is archived

Already done when this plan was merged: the resilience plan moved to
`docs/llm-first/archive/2026-08-24-judge-run-resilience.md`, the genre
`AGENTS.md` defines for superseded documents. Nothing to do here beyond
confirming no task above still references the old path.

### Step 5: Full verification

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
```

If failures appear, run `docker stats --no-stream` first: per `AGENTS.md` these suites return mass setup errors when the container sits at its memory ceiling, and `test_autotranslate.py` is independently flaky under xdist. Compare against a baseline on unmodified `HEAD` before attributing a failure to this work.

```bash
uv run prek run --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Pass explicit hook ids for a scoped run: bare `--files` does not restrain every hook, and `ruff-check --fix` has previously reformatted unrelated files during a supposedly scoped invocation.

### Step 6: Commit and push

```bash
git add docs/
git commit -m "docs(judge): document the request deadline and update the threat model"
git push
```

---

## Verification against the incident

Replay the incident shape on the dev stack after Tasks 1-4 and 7-9:

1. Start a `mode=judge` run on a component with a few hundred strings. Expect: the progress bar advances through phase 1 into the judge phase and **never goes backwards** (Tasks 4); each batch logs model, status and elapsed (Task 1).
2. Point the judge at a drip-feeding endpoint. Expect: the batch is abandoned at `JUDGE_REQUEST_DEADLINE`, recorded unparsed, warned in the log, and the run continues instead of hanging (Task 2).
3. Kill the worker mid-seat. Expect: verdicts from completed batches are already in the database (Task 3), and the task is redelivered rather than lost (Task 8).
4. Start a second auto-translate on a different project while the judge runs. Expect: it starts rather than waiting (Task 9); a third one reports how many runs are ahead of it (Task 7).
5. Confirm the duplicate-delivery guard holds: `weblate shell -c "from django.conf import settings; print(settings.CELERY_BROKER_TRANSPORT_OPTIONS)"` on the dev container prints the 4 h timeout. With early acks this setting was inert; from Task 8 on it is load-bearing, so verify it like code, not like documentation.

Step 1 is the acceptance test for the producer's Polish/spinner report; step 3 is the acceptance test for the Dutch glossary truncation.

## Production rollout

**Needs its own explicit approval; not part of the code deploy.**

1. Add `WEBLATE_JUDGE_REQUEST_DEADLINE`, `CELERY_TRANSLATE_OPTIONS` and `WEBLATE_CELERY_VISIBILITY_TIMEOUT` to the production `.env`.
2. `./deploy/vps.sh deploy` rebuilds the image. The environment block is baked in at container creation, so a restart alone is not enough.
3. Confirm on the running container: `translate` concurrency is 2, `JUDGE_REQUEST_DEADLINE` is present in settings, and `CELERY_BROKER_TRANSPORT_OPTIONS` reports the 4 h visibility timeout.
4. Read the new per-batch log lines from a real run to settle the mechanism question from evidence.
5. Only then decide whether to re-run the French judge pass.
