# OpenRouter availability fallback for the LiteLLM judge seats

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Keep both judge seats on the corporate LiteLLM proxy, and let a batch
that LiteLLM cannot serve be served once by OpenRouter, so that a proxy outage
degrades latency and provenance instead of losing verdicts. Combined with the
durable deferral queue, every selected unit ends a run with a parsed verdict,
`skipped`, `stale-conflict` or `deferred`, and the queue drains to zero.

**Architecture:** Promote the judge's single implicit endpoint into an explicit
`JudgeEndpoint` value object, resolve one `JudgeSeatProfile` per (endpoint,
seat), and add exactly one fallback attempt per batch per seat after the
primary's existing retry budgets are spent on an availability failure. A
protocol failure never fails over. Provenance is denormalized onto every
verdict. Nothing about models, prompts, schema, severity rubric or cached
verdict identity changes.

**Tech stack:** Python 3.14, Django settings and migrations, httpx2 streaming,
Celery, pytest, Docker Compose.

**Supersedes:** `docs/llm-first/plans/2026-08-26-judge-provider-failover.md`
Stages B and C, and the design decisions D1-D4, D6 it delegated to
`docs/llm-first/plans/2026-08-27-judge-reliability-hardening.md` (which was
never started; its harness deliverables landed instead through the parallelism
and deadline plans). It also overrides one sentence of
`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:453-456`
("there is no automatic OpenRouter fallback in the code") by owner decision on
2026-09-01. The rest of that plan's safety argument is kept verbatim: OpenRouter
is not a quality resolver, and a disliked verdict can never be replaced by a
second opinion.

**Status:** proposed, awaiting approval. This is a multi-module feature change,
so `AGENTS.md` requires explicit approval before any code is edited; the owner
decisions of 2026-09-01 settled the design questions below but did not authorise
implementation. Every production step in Rollout then needs its own separate
approval on top of that.

---

## Starting state, measured 2026-09-01

Production checkout `ffb6693` already runs LiteLLM as the only judge endpoint
for both seats, in parallel, and it is stable today:

| Fact | Evidence |
|---|---|
| Both seats served by `hcbifrost.herocraft.com/litellm/v1` | production container environment |
| Seat 1 `deepseek-v4-pro`, batch 2, stream, 120 s, `thinking.disabled`, `json_schema` | production container environment |
| Seat 2 `atlas/qwen3.8-max`, batch 1, stream, 150 s, `json_schema` | production container environment |
| 38/38 attempts HTTP 200, zero failure kinds, zero retries, 50 verdicts over 25 units, zero unparsed, 50.15% overlap | `docs/llm-first/measurements/2026-09-01-03-judge-litellm-nfg-es-canary.md` |
| First-byte p95 5.705 s / 2.746 s against a 20 s envelope | same |
| `JUDGE_DEFERRAL_ENABLED=0`, so no unit is ever queued and an unparsed unit is terminally unjudged | production container environment, `weblate/trans/judge_loop.py:754-756` |
| No fallback exists: no `JUDGE_FALLBACK_*` setting, no second-endpoint resend, no `judge_provider` on `JudgeVerdict` | repo-wide search; `weblate/trans/judge.py:173-197` is the only endpoint resolver |

Already implemented and reused unchanged by this plan, so none of it is work
here: typed `failure_kind` taxonomy (`weblate/trans/judge.py:49-69`),
`JudgeRequestAttempt` per HTTP call including `provider` and
`endpoint_fingerprint` (`weblate/trans/models/judge.py:231-308`), SSE reader
with per-seat absolute and idle deadlines (`weblate/trans/judge.py:735-824`),
bounded transport/transient/protocol retries and width-one isolation
(`weblate/trans/judge.py:1280-1432`), database-backed adaptive batch budget and
circuit breaker (`weblate/trans/models/judge.py:312-351`), per-seat immutable
profiles with `inherit` semantics (`weblate/trans/judge.py:329-450`), the
parallel seat barrier (`weblate/trans/judge_loop.py:918-1068`), and the durable
`JudgeDeferral` queue with its read-only drain
(`weblate/trans/judge_loop.py:754-825`, `:1569-1580`).

Two facts that shape the design and must not be re-litigated during
implementation:

1. **Cache identity already separates endpoints.** `profile_fingerprint` is
   computed from `endpoint_fingerprint` first
   (`weblate/trans/judge.py:422-427`), so a fallback verdict can never satisfy a
   primary-configured cache lookup. D5 of the superseded plan needs no work; add
   a regression test, not a mechanism.
2. **Provenance by join is not guaranteed.** `JudgeVerdict.request_attempt` is
   `null=True` with `SET_NULL` (`weblate/trans/models/judge.py:582-589`), so a
   denormalized provider column is required to answer "which endpoint produced
   this verdict" after attempt retention expires.

## Non-goals

- Any change to models, prompt, JSON schema, severity rubric, batch sizes or
  per-seat deadlines.
- Quality scoring of the LiteLLM pair. No LiteLLM seat pair has ever been scored
  against ground truth
  (`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:44-52`); by owner
  decision on 2026-09-01 this remains outside this plan and keeps its own
  tracking document.
- A third endpoint, provider weighting or load balancing. This is a fallback,
  not a router.
- Clearing the 275-unit `need-for-greed/ui/es` backlog. That is a writing run
  and a separate decision.
- Cost reconciliation. The proxy reports no cost at all (38/38 rows
  `cost_usd=None`); OpenRouter does. The fallback therefore improves cost
  visibility by accident and worsens nothing, but reconciliation stays with the
  LLM usage cost attribution plan.

---

## Task 1: Make the endpoint explicit

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/judge.py`

### Step 1: Write failing tests

Assert that an endpoint is a value, not a global read:

```python
primary = judge_primary_endpoint()
self.assertEqual(primary.role, "primary")
self.assertEqual(primary.provider, "litellm")
profile = resolve_judge_seat_profile(1, endpoint=primary)
self.assertEqual(profile.base_url, primary.base_url)
```

Cover: `judge_seat_profiles()` still returns the primary pair unchanged; two
endpoints with different `base_url` produce different `endpoint_fingerprint` and
therefore different `profile_fingerprint`; the legacy no-seat profile still
resolves the global values.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k endpoint
```

Expected: `judge_primary_endpoint` and the `endpoint=` parameter do not exist.

### Step 3: Implement

- Add a frozen slotted `JudgeEndpoint` dataclass carrying `role`, `base_url`,
  `api_key` and `provider`, with `provider` derived by the existing
  `_judge_provider`.
- Add `judge_primary_endpoint()` reading today's globals through the existing
  `get_judge_base_url`.
- Give `_resolve_profile` and `resolve_judge_seat_profile` a keyword-only
  `endpoint` argument defaulting to the primary. Every read of
  `settings.JUDGE_BASE_URL` and `settings.JUDGE_API_KEY` inside profile
  resolution and request posting comes from the endpoint instead.
- No behaviour change: this task is pure refactor and its tests are structural.

### Step 4: Verify GREEN

Run the Task 1 selection plus the existing profile and configuration tests.

### Step 5: Commit

```text
refactor(judge): resolve seat profiles from an explicit endpoint
```

## Task 2: Configure and validate the fallback endpoint

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/trans/models/_conf.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/trans/judge.py`

### Step 1: Write failing tests

Eight new settings, all empty by default:

```text
JUDGE_FALLBACK_BASE_URL
JUDGE_FALLBACK_API_KEY
JUDGE_FALLBACK_MODEL_SEAT_1
JUDGE_FALLBACK_MODEL_SEAT_2
JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1
JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2
JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1
JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2
```

Assert:

- An empty `JUDGE_FALLBACK_BASE_URL` reproduces today's behaviour exactly,
  including `judge_configuration_ready()`, `validate_judge_configuration()` and
  the call counts of an ordinary run. An unconfigured fallback is not an error.
- A non-empty `JUDGE_FALLBACK_BASE_URL` with any of the other seven blank raises
  `JudgeError` from `validate_judge_configuration()` **before** `http_mock` sees
  a request.
- A fallback base URL equal to the primary's raises `JudgeError`: a fallback to
  the same endpoint is a configuration mistake, not a fallback.
- **The D6 regression.** With `JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled"`
  on a LiteLLM primary and an OpenRouter fallback, the fallback payload must
  carry `reasoning: {"effort": "medium", "exclude": True}` from
  `JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1="medium"`, and the string
  `thinking.disabled` must appear nowhere in the fallback request body. Assert
  the same in reverse: an OpenRouter effort value must never reach a LiteLLM
  fallback payload.
- A fallback reasoning value invalid for the fallback provider raises
  `JudgeError` at validation time, reusing the existing per-provider allowlist.
- `judge_configuration_snapshot()` records the fallback endpoint hostname, both
  fallback models, both efforts and both response formats, and records **no**
  key material.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k fallback
```

### Step 3: Implement

- Add the eight `DEFAULT_JUDGE_FALLBACK_*` defaults, all `""`.
- Wire all eight through `_conf.py`, `settings_example.py` and
  `settings_docker.py` with `WEBLATE_` twins, placed with the existing `JUDGE_*`
  entries. While in `_conf.py`, add the three long-standing omissions recorded
  at `docs/llm-first/plans/2026-08-26-judge-provider-failover.md:298-302`
  (`JUDGE_REQUEST_DEADLINE`, `JUDGE_REASONING_EFFORT`,
  `JUDGE_TRANSPORT_RETRIES`) so the file stops drifting.
- Add `judge_fallback_endpoint() -> JudgeEndpoint | None`, returning `None` when
  the base URL is empty.
- Resolve a fallback seat profile from the fallback endpoint. **Inheritance
  rule, stated once and tested:** `stream`, `batch_size`, `request_deadline`,
  `temperature` and `max_tokens` inherit the primary seat's resolved value,
  because they shape transport, not provider semantics, and a fallback batch is
  never wider than the primary's. `model`, `reasoning_effort` and
  `response_format` have **no** `inherit`: they are provider-semantic and
  inheriting them across providers is exactly the D6 defect. Validation
  requires them.
- Extend `validate_judge_configuration()` to validate the fallback pair with the
  same rules as the primary, only when the fallback base URL is non-empty.

### Step 4: Verify GREEN

Run the Task 2 selection plus the full `test_judge_client.py`.

### Step 5: Commit

```text
feat(judge): configure and validate a fallback judge endpoint
```

## Task 3: Record verdict provenance

**Files:**

- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0113_judge_verdict_provider.py`
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`

### Step 1: Write failing tests

- Every new `JudgeVerdict` carries `judge_provider` equal to the serving
  endpoint's provider, for a primary-served and a fallback-served batch.
- An unparsed verdict also carries the provider of the endpoint that was asked
  last, so a post-hoc report can attribute failures.
- Existing rows keep `judge_provider=""`, which reads as "before failover
  existed"; no data migration.
- The cached-verdict predicate is unchanged and a fallback verdict is not
  reusable while the primary pair is configured. Assert this through
  `profile_fingerprint`, which already contains the endpoint
  (`weblate/trans/judge.py:422-427`) - this test documents an existing
  guarantee.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_loop.py -k provider
```

### Step 3: Implement

- Add `judge_provider = models.CharField(max_length=32, blank=True)` to
  `JudgeVerdict` with an additive migration. Do not touch the cache index: the
  fingerprint already discriminates.
- Return the serving provider inside `JudgeResult` next to the existing attempt
  id, and write it in `_write_verdict`.

### Step 4: Verify GREEN

Run the Task 3 selection plus `test_judge_loop.py` and the migration check.

### Step 5: Commit

```text
feat(judge): record the serving provider on every verdict
```

## Task 4: Fail over once per batch per seat

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/judge.py`
- Read only, not modified: `weblate/trans/judge_loop.py` (the symmetric-difference
  test imports `_AVAILABILITY_FAILURE_KINDS` from it; the circuit breaker keeps
  ownership of that set)

### Step 1: Write failing tests

The trigger set is the safety argument of this whole feature, so it is a table
of tests, not a comment. Define it **independently** in
`weblate/trans/judge.py` beside the taxonomy at `:49-69`:

```python
_FAILOVER_FAILURE_KINDS = frozenset(
    {"transport", "deadline", "http-rate-limit", "http-server", "http-auth"}
)
```

It is deliberately **not** derived from `_AVAILABILITY_FAILURE_KINDS`
(`weblate/trans/judge_loop.py:629-631`), which the circuit breaker owns. The two
sets differ in both directions and each difference is load-bearing:

- `http-other` is in the availability set but **not** here.
  `_failure_for_http` (`weblate/trans/judge.py:1099-1108`) maps every 4xx that
  is not 401/403/429 to `http-other`, so it means 400, 404 or 422: our request
  is wrong for that endpoint, which is a configuration defect, not
  unavailability. Failing it over would double every batch's calls while hiding
  the defect. This case is not hypothetical: the 2026-09-01 rollout produced 50
  `http-other` attempts because LiteLLM model names reached the default
  OpenRouter endpoint
  (`docs/llm-first/measurements/2026-09-01-03-judge-litellm-nfg-es-canary.md`).
  A fallback would have silently "fixed" that misconfiguration and doubled the
  bill.
- `http-auth` is here but not in the availability set. Per D3, an entitlement
  failure is unavailability **of that endpoint**, and the fallback presents a
  different key to a different provider, so exactly one fallback call is
  warranted while the primary keeps its fail-fast behaviour.
- `deadline` is in both, and it is included here on purpose: it means the
  endpoint produced no complete answer inside the seat's absolute bound, which
  is unavailability for that request. The known objection - that a deadline
  should shrink the batch rather than repeat the same shape
  (`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:257-261`) -
  still holds for the primary: the existing adaptive halving must still be
  applied to the primary's budget, and the fallback attempt is additional, not a
  substitute. Cost of a deadline failover is bounded by Task 5's demotion, so a
  stalled primary burns at most `JUDGE_FALLBACK_DEMOTE_AFTER` deadlines per seat
  per run.

A test must assert the two frozensets' exact symmetric difference, so a future
edit to either one cannot silently widen the other.

| Primary outcome | Fallback calls | Terminal result |
|---|---:|---|
| `transport`, after `JUDGE_TRANSPORT_RETRIES` is spent | 1 | fallback's result |
| `deadline`, with the primary's batch still halved | 1 | fallback's result |
| `http-server` (>=500, including 502/503/504/524), after its retry | 1 | fallback's result |
| `http-rate-limit` (429), after its retry | 1 | fallback's result |
| `http-auth` (401/403) | 1, and **zero** further primary calls | fallback's result |
| `http-other` (400/404/422) | 0 | `unparsed` |
| `invalid-json` | 0 | `unparsed` |
| `invalid-envelope` | 0 | `unparsed` |
| `segment-count` | 0 | `unparsed` |
| `invalid-segment` | 0 | `unparsed` |
| `empty-response` | 0 | `unparsed` |
| `finish-length` | 0 | `unparsed` |
| `unknown` | 0 | `unparsed` |
| any parsed verdict, any severity | 0 | that verdict |

Also assert:

- Exactly one fallback attempt per batch per seat, even when the primary failed
  every retry: no fallback retry budget of its own.
- The fallback attempt is **not** charged to `RetryBudget`
  (`weblate/trans/judge.py:88-105`, gate at `:1378-1385`), and fires even when
  that budget is exhausted. Rationale, to be stated in the code comment: that
  budget caps same-endpoint retry amplification, while the fallback is a
  different endpoint serving the batch for the first time; amplification stays
  bounded at 2x by the one-attempt cap.
- A fallback attempt writes a `JudgeRequestAttempt` with the fallback provider,
  the fallback `endpoint_fingerprint`, and `attempt` distinguishable from the
  primary's ordinals.
- Seat isolation: seat 1 failing over does not change which endpoint serves
  seat 2, and the parallel barrier still releases per batch offset.
- A fallback that itself fails yields `unparsed`, never an exception, and does
  not abort the run.
- Width-one isolation (`:1390-1432`) is never applied to a fallback batch,
  because protocol failures do not fail over.
- With `JUDGE_FALLBACK_BASE_URL` empty, call counts are byte-for-byte today's.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k failover
```

### Step 3: Implement

In `_run_batch`, after the primary's transport, transient-HTTP and protocol
budgets are spent and the final `failure_kind` is in `_FAILOVER_FAILURE_KINDS`,
re-send the identical batch once to the fallback seat profile. Log one line with
the seat, the trigger kind, both models and the batch position. `http-auth` on
the primary keeps its existing fail-fast behaviour for the primary and gains
exactly this one fallback call.

### Step 4: Verify GREEN

Run the Task 4 selection, then all of `test_judge_client.py` and
`test_judge_loop.py`.

### Step 5: Commit

```text
feat(judge): fail over one batch per seat to the fallback endpoint
```

## Task 5: Stop paying the primary's timeout on every batch of an outage

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/trans/models/_conf.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/trans/judge.py`

### Step 1: Write failing tests

Without this task a full LiteLLM outage costs one failed primary attempt per
batch. On seat 2 at batch size 1 with a 150 s deadline, a 466-unit component
would burn up to 19 hours of wall time before the fallback ever answers.

Add `JUDGE_FALLBACK_DEMOTE_AFTER`, default `2`, `0` disables. Assert:

- After N consecutive batches on one seat failed over, the remaining batches of
  that seat **in that run** start at the fallback, with no primary attempt.
- The counter is per seat and per `request_verdicts` invocation only. It is not
  shared state, not persisted, and does not touch `JudgeAdaptiveState`, whose
  circuit is gated on `JUDGE_DEFERRAL_ENABLED`
  (`weblate/trans/judge_loop.py:621-622`) and therefore cannot carry this
  decision.
- One parsed primary batch resets the counter to zero.
- `0` disables demotion: every batch tries the primary first.
- A demoted seat still records attempts with the fallback provider, and a
  demoted batch that fails on the fallback is `unparsed`, not re-sent to the
  primary.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k demote
```

### Step 3: Implement

A single integer counter local to the seat's loop in `request_verdicts`,
consulted before choosing the endpoint for a batch.

### Step 4: Verify GREEN

Run the Task 5 selection plus the Task 4 selection, to prove demotion did not
weaken the trigger table.

### Step 5: Commit

```text
feat(judge): demote a seat to the fallback after repeated failover
```

## Task 6: Make the terminal contract hold with the fallback

**Files:**

- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/judge_loop.py`

### Step 1: Write failing tests

The queue already exists; what is untested is its interaction with a second
endpoint. Assert:

- A unit whose primary **and** fallback both failed with an availability kind
  gets exactly one `JudgeDeferral` row for that seat, in state `queued`.
- That row stores the **primary** seat's `profile_fingerprint`, so the drain
  retries the primary first and a transient outage cannot pin a unit to the
  fallback forever. This is the one place where storing the serving profile
  would be wrong.
- A unit that the fallback judged successfully creates no deferral and closes any
  existing one for that seat.
- A protocol failure on the primary, which never fails over, still queues.
- `drain_judge_deferrals` may itself fail over, and a drained verdict carries the
  serving `judge_provider` while remaining read-only: `writable_ids=set()` and no
  target or state write.
- Every selected unit of a run ends in exactly one of `passed`/`minor`/`major`/
  `critical`, `skipped`, `stale-conflict`, `deferred`; terminal `unparsed`
  requires the queue to be disabled.

### Step 2: Verify RED

```text
uv run pytest weblate/trans/tests/test_judge_loop.py -k "deferral and fallback"
```

### Step 3: Implement

Only what the tests demand. Expect the deferral identity to need the primary
profile threaded through explicitly rather than taken from the serving profile.

### Step 4: Verify GREEN

Run the Task 6 selection, then the full judge suite:

```text
uv run pytest weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/checks/tests/test_judge.py
```

### Step 5: Commit

```text
fix(judge): keep deferral identity on the primary endpoint
```

## Task 7: Document the fallback

**Files:**

- Modify: `docs/admin/config.rst`
- Modify: `docs/admin/install/docker.rst`
- Modify: `docs/security/threat-model.rst`
- Modify: `docs/changes.rst`
- Modify: `deploy/environment.example`
- Modify: `dev-docker/docker-compose.yml`

### Step 1: Settings reference

Add the nine settings to the existing alphabetical `JUDGE_*` run in
`docs/admin/config.rst` (the block around `:1760-1935`), matching the
surrounding style: `.. setting::`, `.. versionadded::`, `:setting:`
cross-references. State in prose: the fallback is an availability mechanism
only; an HTTP 200 whose body fails validation is never retried elsewhere; a
verdict is produced by exactly one endpoint and model pair; the fallback's
reasoning effort and response format are not inherited from the primary because
they are provider-specific.

Add the `WEBLATE_JUDGE_FALLBACK_*` envvars to
`docs/admin/install/docker.rst` around `:2368-2395`. While there, add the
existing undocumented `WEBLATE_JUDGE_*` per-seat entries if any are still
missing, as recorded at
`docs/llm-first/plans/2026-08-27-judge-reliability-hardening.md:789-793`.

### Step 2: Threat model

`docs/security/threat-model.rst:130-134` and `:273-276` describe the outbound
judge request as going to `JUDGE_BASE_URL`. A second outbound destination with a
second credential is a change to an outbound integration class, which
`AGENTS.md` requires be recorded in the same change. Record: a judge batch may
be sent to a second configured endpoint on availability failure only; the second
credential is never shared with the primary; attempts continue to retain no
text, prompts, completions, reasoning, headers or keys; and the fallback cannot
convert an unfavourable verdict into a favourable one.

### Step 3: Changelog

One concise entry in the unreleased section of `docs/changes.rst`, linking to
the settings documentation rather than explaining the mechanism.

### Step 4: Deployment templates

Add the nine commented `WEBLATE_JUDGE_FALLBACK_*` entries to
`deploy/environment.example` beside the existing judge block, and to the
`weblate` service environment in `dev-docker/docker-compose.yml`. Remember that
the compose environment block is baked in at container creation, so a dev
container needs a full `./rundev.sh`, not a restart.

### Step 5: Verify

```text
uv run prek run --files <changed files>
```

The pre-existing `rst-bullet-stop` failure at `docs/changes.rst:43` is unrelated
and stays unchanged.

### Step 6: Commit

```text
docs(judge): document the fallback judge endpoint
```

## Task 8: Prove it against a live endpoint in dev

**Files:**

- Modify: `analysis/probes/litellm-seat-diagnostic.py` or create a sibling probe
- Create: `docs/llm-first/measurements/<date>-judge-fallback-forced-smoke.md`

### Step 1: Forced-failover arm

On the dev container, with the fallback configured to today's OpenRouter values
and the primary's `JUDGE_MODEL_SEAT_1` set to a deliberately wrong model so the
primary returns a real `http-other` or `http-auth`, run one small judge scope.
Expected: seat 1 attempts show the primary failure followed by exactly one
fallback attempt with the OpenRouter provider; verdicts exist; `judge_provider`
is `openrouter` for seat 1 and `litellm` for seat 2; the run completes.

### Step 2: Healthy control arm

Same scope, correct primary configuration. Expected: zero fallback attempts,
zero `judge_provider="openrouter"` rows. The control matters as much as the
forced arm: a fallback that fires when it should not is a worse defect than one
that never fires.

### Step 3: OpenRouter rollback smoke

Point the primary at OpenRouter with the historical pair and run the same scope
on the new code. This is the readiness proof for the manual rollback path, and
`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:447-450` refuses
to declare rollback ready without it.

### Step 4: Record

Write the measurement with the three arms, attempt counts, failure kinds,
providers per verdict and latency. Do not claim availability improvements from a
forced arm: it proves wiring, not proxy reliability.

### Step 5: Commit

```text
test(judge): record the forced fallback smoke
```

---

## Rollout

Each numbered step needs its own explicit approval. Nothing here runs during
implementation.

1. Deploy the code and migration `0113` with **all** fallback settings empty.
   Assert on the running container that the fallback is unconfigured, and that a
   small read-only canary reproduces the 2026-09-01 numbers exactly. This proves
   the refactor changed nothing.
2. Set the fallback settings to the historical OpenRouter endpoint, key and pair,
   leaving the primary on LiteLLM. Recreate the container: the environment block
   is baked in at creation. Assert `judge_configuration_snapshot()` shows both
   endpoints and that a canary still shows zero `judge_provider="openrouter"`
   rows. The fallback is configured and idle.
3. Run a bounded read-only canary on a second component and language pair, at
   most 100 units, `JUDGE_MAY_APPROVE=0`, through `run_judge_batch` with
   `writable_ids=set()` so no unit state is projected. Accept only: zero
   terminal unparsed, zero unexpected fallback attempts, first-byte p95 under
   20 s per seat, no request within 25% of its deadline.
4. Enable the durable queue: `WEBLATE_JUDGE_DEFERRAL_ENABLED=1`. This starts a
   periodic Celery task that spends money without a human
   (`weblate/trans/tasks.py:1581-1586`), so it is the single most consequential
   flag in this plan. Before enabling, confirm the retention and index work of
   `docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:99-101` is
   done for `JudgeRequestAttempt` and `LLMUsageLog`, because a queue plus
   indefinite retries grows the database without cleanup. After enabling, watch
   one full drain interval and assert the queue reaches zero.
5. Watch the failover rate over a full production run. A high rate means the
   primary is unhealthy; the correct response is to investigate the proxy or
   revert the primary, never to widen retry budgets.
6. Manual rollback stays one setting: repoint `WEBLATE_JUDGE_BASE_URL`,
   `WEBLATE_JUDGE_API_KEY` and both `WEBLATE_JUDGE_MODEL_SEAT_*` at OpenRouter
   and recreate the container.

Stop conditions: any unknown failure kind, any terminal unparsed unit after the
queue is enabled, any fallback attempt on a failure kind outside
`_FAILOVER_FAILURE_KINDS` (a protocol failure or an `http-other` 4xx), any
`401`/`403`
that produces more than one fallback call, or a reappearance of the ~30 s
first-byte reset recorded in
`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`.

## Risks

- **The fallback may be the less reliable endpoint.** The worst production
  episode on record belongs to OpenRouter, not LiteLLM: 69.74% and 83.91%
  unparsed on 2026-08-28, three days after the same pair returned zero unparsed.
  The fallback is therefore worth having for endpoint-level outages and must
  never be presented as the safer path.
- **Quality is unmeasured on both endpoints of the collegium.** A run may now
  mix providers across its two seats. `judge_provider` makes that auditable, but
  it does not make it comparable. Any future quality measurement must filter on
  provider, or it will silently average two different configurations, which is
  what rule R3 exists to prevent
  (`docs/llm-first/vision/llm-first-product-architecture.md:674`).
- **Cost visibility is asymmetric.** LiteLLM reports no cost; OpenRouter does. A
  run that fails over will have partially priced usage rows. Report it as
  unknown rather than zero.
- **Two credentials, two rotation paths.** The credential rotation operation of
  `docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:345-358`
  remains open and now covers one more secret.
