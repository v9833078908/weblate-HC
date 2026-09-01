# A refused judge request must stop the run, not become a verdict

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** No `JudgeVerdict` may ever be written for a request the endpoint
refused. A permanent refusal stops the run immediately with an operator-visible
error, exactly as `http-auth` already does, instead of being paid for once per
batch and recorded as an opinion about a translation.

**Architecture:** Keep the closed `failure_kind` taxonomy, the attempt ledger,
the retry budget, the adaptive batch state and the deferral queue. Split the one
kind that today conflates two unrelated things - a request the endpoint will
refuse every time, and a request that is merely too large - and route the first
to the existing fail-fast path.

**Tech stack:** Python 3.14, Django settings, httpx2 streaming, pytest.

**Status:** proposed, awaiting approval. This edits `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py` and adds a migration, so `AGENTS.md` requires
explicit approval before any code is edited.

**Evidence this is needed:**
`docs/llm-first/measurements/2026-09-01-04-judge-unparsed-attribution.md`. 101 of
the 102 diagnosable unparsed verdicts in production came from HTTP 400 or 401.
The run of 2026-09-01 05:59 (`48bfbd72`) *completed successfully* after 50
consecutive refused batches, writing 50 unparsed verdicts across two request
rounds.

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

- Any change to models, prompt, JSON schema, severity rubric, batch sizes or
  deadlines.
- The availability fallback. A refused request must not be sent anywhere else.
- Enabling the deferral queue. This plan only unblocks that decision.
- Making `deadline` or `transport` unparsed impossible. That is response length
  and endpoint health, tracked by the per-seat deadline work and the queue.

## Task 1: Separate a permanent refusal from a size-dependent one

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/<next>_judge_request_invalid_kind.py`
- Modify: `weblate/trans/tests/test_judge_client.py`

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
`JudgeRequestAttempt.FailureKind`, extend `_failure_for_http`, and generate the
choices-only migration. Remove nothing from `_AVAILABILITY_FAILURE_KINDS`
except the newly split statuses' effect: `http-other` stays in that set, since
413 and an unclassified 4xx remain plausible transient endpoint states.

### Step 4: Verify GREEN

```text
uv run pytest weblate/trans/tests/test_judge_client.py
```

### Step 5: Commit

```text
feat(judge): classify a refused request separately
```

## Task 2: Fail fast, before any verdict is written

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write failing tests

1. One `http-request-invalid` response raises `JudgeError` from `_run_batch`
   before the retry branch, with no second HTTP call: assert the request count
   is exactly one.
2. The `JudgeRequestAttempt` row is still persisted, with
   `failure_kind="http-request-invalid"`, `parsed=False`,
   `transport_succeeded=False` and the HTTP status - diagnostics must survive
   the abort, because that row is how an operator learns what was refused.
3. **Zero `JudgeVerdict` rows** are created for the batch, and zero
   `JudgeDeferral` rows even with `JUDGE_DEFERRAL_ENABLED=True`. This is the
   test that would have failed on 2026-09-01: the incident wrote 50 verdicts.
4. The error propagates out of the seat worker and fails the run, releasing the
   parallel barrier exactly as the `http-auth` path already does, and the run
   ends `failed` rather than `completed`.
5. The message names the status and no credential:
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
decision, so the ledger row exists and no paid retry follows. Use a distinct
message from the configuration error: "not configured" is wrong when the
endpoint is reachable, authenticated and simply refusing this request.

### Step 4: Verify GREEN

```text
uv run pytest weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
```

### Step 5: Commit

```text
fix(judge): stop a run on a refused request
```

## Task 3: Make the producer-visible outcome honest

**Files:**

- Modify: `weblate/trans/views/judge.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing tests

A run that aborted on a refusal must present the reason, not silence: the run
report shows the run as failed with the refusal message, and a unit touched by
that run shows **no** verdict card change - no "answer was not parsed" banner,
because nothing was judged. Assert against a unit that has no prior verdict and
against one that has a valid historical verdict; neither may gain an unparsed
round.

### Step 2: Verify RED, Step 3: Implement, Step 4: Verify GREEN

```text
uv run pytest weblate/trans/tests/test_judge_views.py -k refused
```

### Step 5: Commit

```text
fix(judge): report a refused run without a fake verdict
```

## Task 4: Decide the 101 existing polluted rows

**Files:**

- Create: `weblate/trans/management/commands/judge_close_refused_verdicts.py`
- Create: `weblate/trans/tests/test_commands.py` additions

The 101 unparsed verdicts already in production were written for refused
requests against dead profiles. They are not evidence about any translation and
they drive the verdict card. A guarded, explicit command - never automatic, never
part of a migration - deletes exactly the verdict rows whose linked
`JudgeRequestAttempt` has a refusal kind, requires `--confirm`, prints the
count per model and profile fingerprint first, and refuses to touch a row with
no attempt row (the 756 OpenRouter ones, whose cause cannot be established).

Rationale for deleting rather than keeping: `JudgeVerdict` is the record of
opinions about translations, and these rows are not opinions. The attempt ledger
keeps the diagnostic history, so nothing is lost.

### Verify

```text
uv run pytest weblate/trans/tests/test_commands.py -k refused
```

### Commit

```text
feat(judge): add a guarded refused-verdict cleanup command
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
   `failure_kind="http-request-invalid"`, run `failed`, zero verdicts, zero
   deferrals, the message visible to the producer.
2. **Size arm.** Force a 413 if the proxy can produce one, or assert by test
   double if it cannot. Expected: unchanged behaviour - `http-other`, adaptive
   halving, no abort.
3. **Healthy control.** Correct configuration, same scope. Expected: zero
   refusals, verdicts for every unit, byte-for-byte today's call counts.

### Commit

```text
test(judge): record the refused-request fail-fast
```

## Rollout

Each step needs its own explicit approval.

1. Deploy the code and the choices-only migration.
2. Run the dev three-arm proof and record it.
3. Run one bounded read-only production canary on a component that already has
   parsed verdicts; assert zero refusals and unchanged call counts.
4. Run the cleanup command with `--confirm` for the 101 rows, after reading its
   dry-run output.
5. Only then reconsider `WEBLATE_JUDGE_DEFERRAL_ENABLED=1`, which this plan
   unblocks: see Rollout step 4 of
   `docs/llm-first/plans/2026-09-01-02-judge-openrouter-availability-fallback.md`.

**Stop conditions:** any refusal in the healthy control arm; any run that aborts
on a 413 or on an unclassified 4xx; any verdict row written for a refused
request; the cleanup command reporting a count other than the 101 rows its
dry-run showed.

## Risks

- **The status list is a judgement call.** 422 is classified permanent because a
  proxy uses it for an unprocessable request body, but a gateway could use it
  transiently. The mitigation is that the taxonomy is one function with one test
  table, so a correction is one line and one row.
- **Fail-fast turns a partial run into no run.** Today a refusal costs one batch
  and the rest of the run proceeds; after this change the run stops. That is the
  intent - the alternative is paying for every batch and recording 50 false
  unparsed verdicts - but a producer who previously got 90% of a run will now
  get an error, which is why Task 3 exists.
- **The cleanup is a production deletion.** It is guarded, dry-run first,
  scoped by linked attempt kind, and never touches a row whose cause is unknown.
