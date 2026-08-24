# Bound a judge run and keep the verdicts it paid for (superseded)

**Date:** 2026-08-24. **Status:** superseded and absorbed into
`docs/llm-first/plans/2026-08-24-auto-translate-queue-and-progress.md`,
Tasks 1-3 and the Decisions section. Do not implement this document
independently.

## Reason for consolidation

Two plans described one incident, so they described one change. The merged
plan is broader: it also covers the container restart that truncated
`need-for-greed/glossary/nl`, the queued-versus-running UI lie, the nested
form question, and the progress bar. This document's distinct contributions -
the total-deadline mechanism, per-batch verdict persistence, the seam
signature `on_batch(requests, results)`, and the evidence refuting the
"sequence of silent timeouts" reading of the hang - moved across intact.

The original text follows for history.

---

**Incident:** production run `feca65be` on `victory-banner/common/fr` hung for
26 minutes inside one HTTP request, blocked automatic translation for every
project, and was revoked manually at 10:59:19 UTC.

**Goal:** A judge batch request always ends. A judge run that ends early keeps
the verdicts it already paid for. One judge run never starves the machine
translation of other projects.

**Architecture:** Read the judge HTTP response body ourselves under an
absolute wall-clock deadline instead of trusting `httpx`'s per-read timeout,
which a drip-feeding peer resets forever. Persist each completed seat batch as
it arrives instead of holding a whole seat in memory until its last batch.
Both changes land on one new per-batch seam inside `request_verdicts()`.
Queue starvation is configuration, not code.

**Tech Stack:** Django, `httpx2` 2.9.1, Celery, PostgreSQL, pytest,
`weblate.utils.tests.http_mock`.

---

## Relationship to Plan 02

`docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md` Task 6
("Share preview scope, cap, and judge progress reporting") threads a progress
callback through exactly the seam this plan creates:

```text
request_verdicts()  ->  run_judge_batch()  ->  AutoTranslate.process_judge()
```

Ordering and division of ownership:

1. **This plan lands first.** It is an incident fix for a feature that is on
   in production and currently unusable for a large run. Plan 02 is a nine-task
   feature that has not started.
2. **This plan owns the seam.** It introduces the per-batch callback in
   `request_verdicts()` and the per-batch write in `run_judge_batch()`.
3. **Plan 02 Task 6 keeps everything else**: cap, scope slicing, preview,
   `progress_steps`, the range split, and the completion summary. After this
   plan, Task 6 Step 4 adds only the counter increment on an existing hook.

**Required amendment to Plan 02, needs your acknowledgement.** Task 6 Step 4
specifies `Callable[[], None]`. Persistence needs the batch's requests and
results, so this plan defines the seam as:

```python
OnBatch = Callable[[Sequence[JudgeRequest], Sequence[JudgeResult]], None]
```

Task 6's progress tick then ignores both arguments. This is a one-line change
to an approved plan; nothing else in Task 6 moves. Do not start Task 6 against
the old signature.

`docs/llm-first/plans/2026-08-24-judge-progress-reporting.md` is a superseded
stub and stays superseded. This plan does not revive it.

---

## Evidence

Production, 2026-08-24, run `feca65be`, `victory-banner/common/fr`, 448
strings, all times UTC.

| Phase | Model | Window | Requests | Cost |
| --- | --- | --- | --- | --- |
| Pre-translation | `google/gemini-2.5-flash` | 09:12:11-09:50:32 | 106 | $0.486 |
| Seat 1 | `deepseek/deepseek-v4-pro` | 09:50:46-10:23:57 | 90 of 90 | $0.218 |
| Seat 2 | `qwen/qwen3-235b-a22b-2507` | 10:24:08-10:32:41 | 47 of 90 | $0.018 |

Seat 1 wrote all 448 verdicts in a single 0.3-second burst at 10:23:57 - the
signature of one transaction after the last batch.

Seat 2 then stopped. Measured during the hang:

- median inter-request gap over its 47 completed batches: 7.4 s, maximum 13.8 s;
- observed silence: 1231 s and still rising when sampled, 26 minutes total;
- worker process state `S (sleeping)`, kernel `wchan` `do_sys_poll`;
- the worker owned exactly one outbound socket, `104.18.3.115:443`
  (Cloudflare in front of OpenRouter), state `ESTABLISHED`, send and receive
  queues both `00000000`, no retransmit and no keepalive timer armed;
- the socket was unchanged across six samples spanning 6.5 minutes.

Why the configured timeout did not fire, verified directly:

```console
$ uv run --no-sync python -c "..."
extensions: {'timeout': {'connect': 120, 'read': 120, 'write': 120, 'pool': 120}}
httpx2 version: 2.9.1
```

`timeout=JUDGE_REQUEST_TIMEOUT` (`weblate/trans/judge.py:395`, constant `120`
at `judge.py:40`) does reach the request. `read` in `httpx` bounds **one read
operation**, not the request. Any byte resets it.

That the peer was dripping bytes rather than silent is not a guess. Two
observations force it:

1. A truly silent peer would have tripped the 120 s read timeout at 10:34:41.
   It did not fire for 26 minutes.
2. The drip cannot be in the response body: the payload sets `"stream": false`
   (`judge.py:452`) and `_parse_reply` consumes one JSON document, and 137
   earlier batches in this same run parsed successfully. Body-level keepalive
   text would have broken every one of them.

So the keepalives are transport level - chunked-encoding or TLS-layer traffic
that never reaches `response.json()` but does reset every read timer between
the kernel and `httpx`. Nothing in the judge path holds a clock that a peer
cannot reset. OpenRouter documents this behaviour for long generations and
recommends a total timeout plus a per-chunk stale detector.

Cost of the revoke: seat 2's 47 batches, roughly 235 model answers, existed
only in the worker's memory. `run_judge_batch()` calls `request_verdicts()`
for a whole seat (`judge_loop.py:387`) and writes only afterwards
(`judge_loop.py:393-397`). Killing the task discarded them; their
`LLMUsageLog` rows remain. Here that was $0.018. At the 2000-string cap it is
the whole seat.

Blast radius beyond this component: `CELERY_TASK_ROUTES`
(`weblate/settings_docker.py:1440`) sends every `auto_translate*` task to the
`translate` queue, and that queue runs
`--pool=prefork --prefetch-multiplier=1 --concurrency 1`. Three tasks were
queued behind the wedged one. `need-for-greed/ui/pl` started 12 seconds after
the revoke, having waited through the whole hang.

---

## Decisions

1. **Keep `"stream": false` in the payload.** Switching the judge to SSE would
   also stop billing on an aborted request, which is real money, but it
   changes the parse path, the usage path and the provider contract
   (`response_format` + `require_parameters` + streaming) all at once, on the
   only semantic validator in the product. The measured defect is a missing
   clock, not the response format. Streaming is a separate, later question.
2. **Read the response as an HTTP stream anyway.** Owning the read loop is the
   only way to hold a clock the peer cannot reset. The body is still one JSON
   document; only who concatenates it changes.
3. **One new operator setting, `JUDGE_REQUEST_DEADLINE`, default 300 s.**
   Observed healthy batches finish in 7-16 s. 300 s is a hard ceiling, not a
   target. The existing `JUDGE_REQUEST_TIMEOUT = 120` stays as the stall
   timeout for a genuinely silent peer.
4. **A deadline hit is a transport failure, not an opinion.** It marks the
   batch `UNPARSED`, exactly like an HTTP 500 today. No new verdict semantics,
   no new state, nothing for a producer to interpret.
5. **Persist per batch, not per seat.** Once a batch's results exist they are
   paid for. A run that dies keeps them.
6. **Queue isolation is configuration.** True isolation needs a dedicated
   worker, which means overriding the upstream image's supervisor wiring
   (`deploy/Dockerfile:15` reuses `weblate/weblate:2026.8.0.0`). Out of scope
   here; raise `translate` concurrency instead and say what it costs.

### Residual risk, stated

A hang **before response headers arrive** is bounded only by the 120 s stall
timeout, not by the deadline, because `client.send(stream=True)` returns after
headers and we cannot check a clock while blocked inside it. This is
acceptable: transport keepalives accompany a response already in flight, and
120 s of true silence does trip. If measurement later shows a pre-header hang,
the fix is a watchdog that closes the client, which is a bigger change and
needs its own plan.

---

## Task 1: Bound one judge batch request by a total deadline

**Files:**

- Modify: `weblate/utils/requests.py`
- Modify: `weblate/utils/tests/test_requests.py`
- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/tests/test_judge_client.py`

### Step 1: Write the failing deadline tests

In `weblate/trans/tests/test_judge_client.py`, using the existing
`http_mock.register_callback()` API (`weblate/utils/tests/http_mock.py:309`)
to return an `httpx2.Response` whose stream is a generator that sleeps between
chunks:

- a peer that drips forever with `JUDGE_REQUEST_DEADLINE` set to `0.3`
  produces `[UNPARSED] * len(batch)` and returns in well under one second;
  assert the elapsed wall time is bounded, because a per-read timeout would
  make this test hang rather than fail;
- the same drip inside a two-batch run marks only the affected batch unparsed
  and still processes the following batch;
- a body delivered in several chunks inside the deadline parses normally, so
  chunked transfer alone is not treated as failure;
- a deadline hit records **no** `LLMUsageLog` row, because no usage block was
  ever received;
- a deadline hit is not a 403/429 and therefore consumes no retry: assert
  exactly one HTTP call.

In `weblate/utils/tests/test_requests.py`, cover the new helper directly: it
yields a streamed response, it applies `RuntimeRedirectValidators`, and it
does not follow redirects when asked not to.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
```

Expected: `stream_validated_url` and `JUDGE_REQUEST_DEADLINE` do not exist.

### Step 3: Add the streaming helper

`weblate/utils/requests.py` already has the private `_open_url()` context
manager (line 850) and `create_http_client()` (line 584). Add a public
sibling of `fetch_validated_url()` (line 800) that yields an unread streamed
response:

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

It delegates to `_open_url()` with `RuntimeRedirectValidators`, mirroring how
`fetch_validated_url()` builds validators. It never calls
`raise_for_status()`: the judge inspects `status_code` itself.

Do not change `fetch_validated_url()`. Other callers keep the buffered path.

### Step 4: Add the setting

Follow the existing judge settings exactly (`JUDGE_BATCH_SIZE` is the closest
model):

- `weblate/trans/defaults.py`: `DEFAULT_JUDGE_REQUEST_DEADLINE = 300.0`;
- `weblate/settings_docker.py`: `JUDGE_REQUEST_DEADLINE = get_env_float(...)`
  keyed `WEBLATE_JUDGE_REQUEST_DEADLINE`, placed with the other `JUDGE_*`
  entries;
- `weblate/settings_example.py`: the same default with the surrounding comment
  style;
- extend `validate_judge_configuration()` (`judge.py:85`) to reject a
  non-positive deadline before any paid call, next to the checks Plan 02
  Task 6 Step 4 also extends.

### Step 5: Read the body under the deadline

Rewrite `_post_batch()` (`judge.py:382-408`). It keeps its signature and its
`_BatchResponse` return, so `request_verdicts()` and every retry decision are
untouched:

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

Then decode `buffer` as JSON with the existing tolerance: unreadable body
becomes `body = None`, exactly as today.

Keep the comment explaining why the bearer token is built inline. Keep the
blanket `except Exception` around the request so a transport error still
yields `_BatchResponse(None, None)`; the deadline path must produce the same
shape so `judge.py:495-505` continues to mark the batch `UNPARSED` without a
new branch.

Log the deadline abort at warning level with the model and the elapsed time.
It is operator evidence for a provider problem, and it is the only trace a
silent abort leaves.

### Step 6: Verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
```

### Step 7: Prove the tests catch the defect

Revert only the `monotonic() > deadline` check, rerun, and confirm the drip
tests fail rather than pass. A regression test for a hang that itself hangs is
worthless; if reverting makes the suite stall instead of fail, tighten the
test's own bound until it fails cleanly. Restore the check.

### Step 8: Commit

```bash
git add weblate/utils/requests.py weblate/utils/tests/test_requests.py \
  weblate/trans/defaults.py weblate/settings_docker.py \
  weblate/settings_example.py weblate/trans/judge.py \
  weblate/trans/tests/test_judge_client.py
git commit -m "fix(judge): bound a batch request by a total deadline"
```

---

## Task 2: Persist verdicts as each batch completes

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the failing persistence tests

In `test_judge_client.py`:

- `request_verdicts()` calls `on_batch(requests, results)` exactly once per
  completed batch, in input order, whether the batch parsed or became
  `UNPARSED`;
- a 403/429 retry is one completed batch and therefore one call, not two:
  register a 429 followed by a 200, mock the sleep, assert two HTTP calls and
  one callback;
- the callback receives exactly that batch's slice, never the accumulated
  list;
- no callback and no HTTP call happen when the gate raises (disabled, no key,
  no model).

In `test_judge_loop.py`:

- after a seat whose second batch fails at transport level, the first batch's
  verdicts are already in the database;
- an exception raised out of the middle of a seat leaves every completed
  batch's verdicts committed, and this is the test that would have saved the
  incident's 47 batches;
- one round still produces exactly one `JudgeVerdict` row per unit per seat -
  per-batch writes must not duplicate rows;
- cached units still make no client call and no callback.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py -p no:randomly --no-cov -q
```

### Step 3: Add the seam

In `judge.py`, give `request_verdicts()` an optional keyword:

```python
on_batch: Callable[[Sequence[JudgeRequest], Sequence[JudgeResult]], None] | None = None
```

Call it after the batch's results are appended at `judge.py:505` and before
the inter-batch sleep at `judge.py:506-507`. It must run for an `UNPARSED`
batch too, and it must not be suppressed by non-fatal usage accounting.

This is the seam Plan 02 Task 6 will reuse. Do not add a second callback.

### Step 4: Write per batch

In `judge_loop.py`, replace the seat-wide transaction at lines 393-397 with a
callback that writes each batch in its own transaction:

```python
def persist(batch_requests, batch_results) -> None:
    with transaction.atomic():
        for request, result in zip(batch_requests, batch_results, strict=True):
            _write_verdict(units_by_key[request.unit_key], request, seat,
                           attempt, run_id, result, model)
```

`request_verdicts()` receives requests in the order of `request_units`
(`judge_loop.py:383-386`), so map each request back to its unit by identity
rather than by re-zipping the full list. Build that map once per seat.

The round projection at `judge_loop.py:399-419` is unchanged and still runs
after the seat completes; only the durability of the evidence moves earlier.

### Step 5: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py -p no:randomly --no-cov -q
git add weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): persist verdicts as each batch completes"
```

---

## Task 3: Stop one run from starving every other project

**Files:**

- Modify: `deploy/environment.example`
- Modify: `docs/admin/config.rst`

No code. `CELERY_TRANSLATE_OPTIONS` is an upstream image variable; prod
currently leaves it derived from `WEBLATE_WORKERS=2`
(`deploy/environment.example:138`), which yields `--concurrency 1`, verified
on the running container.

### Step 1: Make the setting explicit

Add to `deploy/environment.example`, next to the judge block at lines 115-118:

```sh
# Concurrency of the `translate` queue, which serves every automatic
# translation and every LLM judge run for every project. At 1, one long judge
# run blocks all of them (2026-08-24: 26 minutes). This is also the ceiling on
# concurrent paid provider runs.
CELERY_TRANSLATE_OPTIONS=--concurrency 2
```

### Step 2: Document the coupling

In `docs/admin/config.rst`, in the existing `JUDGE_*` block, add one sentence
to the `JUDGE_ENABLED` or `JUDGE_MAX_UNITS_PER_RUN` prose: a judge run occupies
a `translate` worker slot for its whole duration, and the queue is shared by
all projects. Use `:envvar:` for `CELERY_TRANSLATE_OPTIONS`. Do not restructure
the section.

### Step 3: Verify

```bash
uv run prek run rumdl rumdl-fmt rst-http trailing-whitespace end-of-file-fixer \
  --files docs/admin/config.rst deploy/environment.example
```

### Step 4: Commit

```bash
git add deploy/environment.example docs/admin/config.rst
git commit -m "docs(judge): document translate queue concurrency"
```

---

## Task 4: Document the deadline and verify end to end

**Files:**

- Modify: `docs/admin/config.rst`
- Modify: `docs/security/threat-model.rst`

### Step 1: Document the setting

Add `JUDGE_REQUEST_DEADLINE` to `docs/admin/config.rst` in the existing
alphabetical `JUDGE_*` run, matching the surrounding style: `.. setting::`,
`.. versionadded:: 2026.8.1`, a `:setting:` cross-reference to
`JUDGE_ENABLED`, and one sentence saying a batch that exceeds it is recorded
as unparsed rather than retried.

No `docs/changes.rst` entry. The judge is still in the unreleased section, and
AGENTS.md excludes fixes to unreleased features.

### Step 2: Update the threat model

`docs/security/threat-model.rst:241-250` already describes the judge's
outbound batch. Add that an outbound judge request is bounded by
`JUDGE_REQUEST_DEADLINE`, so a hostile or failing endpoint cannot hold a
worker indefinitely. This is a claimed security property changing, which the
"Conditions that change this model" section requires be recorded.

### Step 3: Run the affected suites once

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/utils/tests/test_requests.py -p no:randomly --no-cov -q
```

Check `docker stats --no-stream` first if the suite returns mass setup errors;
memory pressure from sibling compose projects is the usual cause, not the
change.

### Step 4: Run the hooks on the changed files only

```bash
uv run prek run ruff-check ruff-format rumdl rumdl-fmt toml-sort-fix \
  trailing-whitespace end-of-file-fixer --files <exact changed files>
```

Pass explicit hook ids. A bare `--files` run lets repo-wide hooks reformat
unrelated files.

### Step 5: Prove it against a real endpoint, in dev only

In the dev container, with the dev judge key, run one small judge batch
against a live model and confirm a normal run still parses, records usage, and
writes verdicts per batch. Then set `WEBLATE_JUDGE_REQUEST_DEADLINE=1`,
rerun, and confirm the batch is recorded unparsed, the warning is logged, the
run completes instead of hanging, and no verdict rows are invented.

No production call. No paid run against `l10n.herocraft.com`.

### Step 6: Commit and push

```bash
git add docs/admin/config.rst docs/security/threat-model.rst
git commit -m "docs(judge): document the judge request deadline"
git push
```

---

## Out of scope

- Switching the judge to SSE streaming, and the cheaper aborts it would buy.
  Decision 1.
- A whole-run time budget. Per-batch persistence makes one cheap to add later;
  Plan 02's cap already bounds run size.
- A dedicated `judge` Celery queue and worker. Needs supervisor wiring inside
  the upstream image.
- A watchdog for a pre-header hang. Residual risk, stated above.
- Everything Plan 02 owns: cap, scope slicing, preview, progress percentage,
  completion summary, readiness table, search filters.
- Re-running `victory-banner/common/fr`. Separate decision, separate approval.

---

## Deployment, separate approval

Not part of executing this plan. When approved:

1. Add `WEBLATE_JUDGE_REQUEST_DEADLINE` and `CELERY_TRANSLATE_OPTIONS` to the
   production `.env`.
2. `./deploy/vps.sh deploy` rebuilds the image; the environment block is baked
   in at container creation, so a restart alone is not enough.
3. Confirm on the running container: `translate` concurrency is 2, and
   `JUDGE_REQUEST_DEADLINE` is present in settings.
4. Only then decide whether to re-run the French judge pass.
