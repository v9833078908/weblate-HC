# Plan: a request harness that makes the judge stable on an unstable proxy

**Date:** 2026-08-27. **Status:** proposed, not started; rewritten 2026-08-27
after review. Implementation needs explicit approval. **Owner decision required
before live probes:** they spend provider credits but write no production
translations.

This plan absorbs the *mechanism* of
`docs/llm-first/plans/2026-08-26-judge-provider-failover.md` - its two-endpoint
configuration, per-seat/per-batch fallback, availability-only trigger list,
`judge_provider` provenance field and parser-invalid-`200` safety rule (that
plan's D1-D4, D6, D7 and Stages B-C), so that no second migration claims the
same field. Its Stage A, scoring LiteLLM candidates against ground truth, is
*not* absorbed: model selection stays with
`docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md`.

## The problem, stated as it actually is

The judge client is stable against OpenRouter and unstable against the corporate
LiteLLM proxy, with the same code, the same prompt and overlapping models. So
this is not a client defect to fix and not a model quality question. It is a
request-shaping problem: the proxy answers reliably inside some envelope and
resets outside it, and our client currently has no idea that an envelope exists.

**The deliverable is a harness:** the layer that keeps every judge request inside
that envelope, discovers the envelope's edge by measurement rather than by
assumption, adapts when the edge moves, and recovers the requests that fall
outside it anyway - without asking a human for anything.

Two things follow that shape everything below.

- **Stability is engineered client-side.** Retrying harder is the last resort,
  not the mechanism. The mechanism is sizing and pacing each request so it does
  not fail.
- **No unit is ever abandoned.** A unit the harness could not judge goes into a
  durable queue that drains itself. `unparsed` remains only as evidence of a
  failed request, never as a terminal verdict for a string.

## What "zero unparsed" means here

`unparsed` is two different things, and only one can reach zero.

1. **A failed request.** `JudgeVerdict.unparsed` records that one POST for one
   seat produced no usable answer (`weblate/trans/models/judge.py:286-288`).
   This cannot be zero: it describes the proxy's behaviour. It stays as
   evidence, and this plan makes it explainable instead of anonymous.
2. **A unit that ends without an opinion.** Today this is a terminal outcome:
   `JudgeRunUnit.Outcome.UNPARSED`, written both when a unit has no verdict at
   all and when its round is all-unparsed
   (`weblate/trans/autotranslate.py:858-879`, `:887-895`), announced as "N
   strings were left unjudged" (`:839-847`) and then forgotten.

Outcome 2 stops existing. Every selected unit ends a run in exactly one of:

| terminal state | meaning |
|---|---|
| a parsed verdict | judged; the existing resolution workflow owns it |
| `skipped` | policy: permission or cap |
| `stale-conflict` | the judged text changed under us; the next run re-selects it |
| `deferred` | the harness could not judge it yet; the queue keeps working it |

The zero claim is therefore **eventual and machine-only**: at any instant some
units may be queued, over time the queue drains, and the only way a unit leaves
the queue without a verdict is that its text stopped existing. Nothing here
promises a human anything, and nothing here requires a human to act.

## Measured basis

Everything below is a recorded number. Where a mechanism is not established, it
is labelled a hypothesis and the plan does not lean on it.

| measurement | result | source |
|---|---|---|
| LiteLLM, `qwen3.8-max`, reasoning **on** | 8/8 batches failed | `docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md:109-116` |
| LiteLLM, `qwen3.8-max`, reasoning **off** | 0/8 batches failed, ~5.6-10 s | same, `:109-116` |
| LiteLLM, DeepSeek seat, `thinking: disabled` | 37/37 billed rows still carried reasoning tokens | `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md:113-126` |
| LiteLLM, batch 5, defect-bearing batches, `deepseek-v4-pro` | 0/3 in both reasoning configurations | `docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md:159-164` |
| LiteLLM, batch 2, 15-unit dev slice, `deepseek-v4-pro` | 8/8 parsed, 10/10 caught, 12 attempts for 8 batches | same, `:222-233` |
| LiteLLM, batch 3, same slice | 2/5 parsed; three requests returned no HTTP response after 30.5, 30.6, 30.9 s | same, `:222-233` |
| LiteLLM, batch 2, `col4/common/fr`, 82 units | DeepSeek 66/82 unparsed (80.5%), Qwen 28/82 (34.1%) | `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md:20-68` |
| a previously unparsed unit, replayed unchanged | parsed | same, `:146-152` |
| OpenRouter, `deepseek-v4-pro` + `qwen3-235b-a22b-2507`, 124 units | 0 unparsed | `docs/llm-first/measurements/2026-08-14-st2-zh-judge-run.md:38-55` |
| OpenRouter, 376-record screen, six models | 0 unparsed | `docs/llm-first/measurements/2026-08-13-phase0-measurements.md:119-190` |

Three readings, in decreasing confidence.

**Established.** Reasoning being on correlates with failure on this proxy across
two toggle states and two corpora. DeepSeek's reasoning is not suppressible
there. Failures are transient rather than string-deterministic: the same request
replayed parsed. The same client is stable on OpenRouter.

**Hypothesis, to be tested by the probe, never assumed.** The binding variable is
per-request *generation time*, and the proxy has a wall-clock cliff near 30 s.
It fits every row: successes at 20.1-30.5 s, failures with no HTTP response at
30.5-30.9 s, a thinking-on reset band at 30.6-31.1 s, and the same nominal
batch 2 succeeding on a short dev slice while failing on French. The source
measurement explicitly calls the batch-size result "an intervention, not a
mechanism" (`docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md:235-238`),
and nothing we have compared target lengths, token counts or gateway timeouts
across those corpora. Candidate confounds, all unmeasured: target length,
completion tokens, reasoning tokens, proxy load at the hour, gateway timeout.

**Not established at all.** That a LiteLLM-selected model would be stable on
OpenRouter, or the reverse. The OpenRouter rows cover a different model set.

**Fix zero, before any code.** `hcbifrost.herocraft.com` is HeroCraft's own
gateway. If that cliff is a request or read timeout in its configuration, the
correct fix is a configuration change by the team that runs it, and every
mechanism in this plan becomes a safety net instead of a workaround. Ask before
Task 6 starts and record the answer in the placement measurement. Building
timeout-avoidance machinery around an in-house gateway without ever asking about
its timeout would be the wrong order of work.

## Non-negotiable contracts

- `JudgeVerdict.unparsed` is a transport/protocol state, never a severity and
  never a `pass`.
- `collegium_verdict()` ignores an unparsed seat when the other parsed; only an
  all-unparsed round reads as unparsed
  (`weblate/trans/models/judge.py:499-512`).
- A parsed verdict is never retried because its severity is inconvenient.
- **A `200` rejected by the parser is never sent to another provider as a
  replacement opinion.** A repeated parser-invalid `200` is a seat-health fault:
  it goes to the queue, never to a second provider for a nicer answer.
- Seat A's batch is never sent to seat B's model to obtain "seat A's" opinion,
  and no answer is ever relabelled as another seat's. Compensation is about
  coverage, never authorship.
- A transport failure never moves a unit's state: `state_for_verdict()` returns
  `None` for unparsed (`weblate/trans/models/judge.py:398-399`), and nothing in
  the harness or the queue may change that. The proxy's health is not the
  translator's problem.
- A retry reuses the same source, target, glossary/context and seat profile. A
  changed target is stale, not a delayed request.
- Request logs contain no API key, prompt, target text, raw response, reasoning
  trace, or any unkeyed derivation of them.

`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` stays proposed and
gated: parallelism may not be used to compensate for anything here.

## Design decisions

### D1. Persist attempt evidence, not opaque log text

A harness that adapts needs per-request truth to adapt on. Add an immutable
`JudgeRequestAttempt`: one row per HTTP POST for one seat and one logical batch,
whether or not a response arrives. Producer calls link it to the exact
`JudgeRun`; direct `request_verdicts()` callers (the probes under
`analysis/probes/`) create no attempt rows and keep today's usage behaviour.

| field | purpose |
|---|---|
| `judge_run` | exact producer launch |
| `logical_run_id` | current model-call round ID (`JudgeVerdict.run_id`) |
| `round_kind` | `initial`, `repair`, `unparsed-retry`, `deferred-pass`, `probe` |
| `seat`, `batch_index`, `attempt_index`, `unit_count` | position and bounded retry history |
| `planned_output_tokens`, `planned_seconds` | what the harness predicted for this request (D5) |
| `batch_digest` | keyed digest of the exact serialized batch: evidence for the peer question in D3, never a cache predicate |
| `provider`, `model`, `profile_fingerprint`, `request_fingerprint` | request provenance without a credential |
| `endpoint_role` | `primary` or `fallback` (D6) |
| `status_code`, `finish_reason`, `response_id`, `retry_after_seconds` | safe peer outcome metadata |
| `failure_kind`, `parsed`, `will_retry` | normalized result and decision |
| `elapsed_ms`, `response_bytes` | measured cost, and the harness's feedback signal |
| `created` | stable ordering |

`failure_kind` is a closed stored vocabulary:

```text
none
transport
deadline
response-too-large
http-auth
http-rate-limit
http-server
http-other
empty-response
invalid-json
invalid-envelope
segment-count
invalid-segment
length
unknown
```

The response reader and parser return typed failures instead of bare `None`.
Today `_read_batch_response()` collapses a local deadline and an oversized body
into the same `None` (`weblate/trans/judge.py:504-529`) and `_post_batch()`
collapses that into a payload-less response (`:552-555`), so the system cannot
name its own failures - and the harness cannot tell a timeout from a broken peer,
which are opposite signals for sizing. An empty body is distinct from invalid
JSON, and `finish_reason=length` rejects an otherwise parseable body.
`finish_reason` is observable only when the outer body parses; a mid-stream break
stays `transport`, which the broad catch at `weblate/trans/judge.py:550-551`
already produces. `Retry-After` is read from response headers, which `httpx2`
keeps readable after the stream context exits, parsed only as positive
delta-seconds and capped at 60 s.

`on_batch` keeps its signature and its "exactly once per completed batch"
contract: the progress tick (`weblate/trans/autotranslate.py:757-773`) and the
parallelism plan's `_BatchReady` barrier depend on it. The attempt observer is a
second, private callback; the terminal attempt reaches the verdict writer through
state shared between the two closures in `weblate/trans/judge_loop.py`, keyed
`(seat, batch_index)`. Widening the `OnBatch` alias is not an option.

One persistence function writes both rows: given the attempt event and an
optional producer run, it writes the attempt only when a run is present and the
usage row either way, so judge accounting never forks into two implementations.
It fills `LLMUsageLog.batch_size` and `LLMUsageLog.outcome`, which the judge
writer leaves unset today (`weblate/trans/models/llm_usage.py:58-66` against
`weblate/trans/judge.py:381-393`). For a producer run both rows are created in
one small transaction; if it fails, the loss is logged, judging continues, and
the run's persistence-loss counter increments. A canary with any such loss fails
promotion.

New `JudgeVerdict` rows gain nullable provenance: `judge_run`,
`request_attempt`, `judge_provider`, `endpoint_role`, `profile_fingerprint`,
`request_fingerprint`, `batch_unit_count`, the keyed `batch_digest`, and
`round_kind`. `judge_provider` is the field
`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:110-117` introduces;
it lands here and that plan is edited to consume it. Old rows stay valid
historical evidence with blank provenance. `LLMUsageLog` gains a nullable
one-to-one link to the attempt, so billed tokens join one exact request; a reset
has an attempt row and no usage row, which is the required distinction. Provider
response bodies are never stored to make that join possible.

`JudgeRequestAttempt` is append-only: a 2000-string run at width five is 800 rows
before retries. Like `JudgeVerdict` and `LLMUsageLog` it is retained
indefinitely; no pruning task exists for either and this plan adds none. The
documentation says so rather than leaving it to be discovered from table size.

### D2. Snapshot only safe effective configuration

`JudgeRun` gains `configuration`, a JSON snapshot written at creation: per-seat
provider identity, model, profile fingerprint, endpoint role and harness
parameters; endpoint fingerprints; deadline, retry budgets, queue settings; the
prompt/schema profile version. Never the key, headers, a complete URL, prompt
text, translation text or a raw payload.

`JudgeRun` is created with `status=RUNNING` in
`BatchAutoTranslate._create_judge_run`
(`weblate/trans/autotranslate.py:1148-1179`) before `run_judge_batch()` resolves
seats (`weblate/trans/judge_loop.py:490-496`), so the profile resolver moves
ahead of run creation and must be importable from `autotranslate.py` without a
cycle. A resolver failure then raises before any row exists, exactly as
`validate_judge_configuration()` does today, and leaves no `RUNNING` row nobody
will finish.

This is what the French measurement lacked: its settings had to be reconstructed
afterwards.

### D3. Request profiles belong to seats; identity is the configured request

A small immutable `JudgeRequestProfile` is resolved before any POST, carrying
provider, endpoint role, model, effective reasoning behaviour, optional
`max_tokens`, the harness parameters of D5, and a stable fingerprint.
`run_judge_batch()` resolves one profile per configured slot and passes it,
including the seat number, into `request_verdicts()`.

| setting | default | behaviour |
|---|---|---|
| `JUDGE_REASONING_EFFORT_SEAT_1` | `inherit` | seat 1 profile override |
| `JUDGE_REASONING_EFFORT_SEAT_2` | `inherit` | seat 2 profile override |
| `JUDGE_MAX_TOKENS_SEAT_1` | `0` | omitted from the request when zero |
| `JUDGE_MAX_TOKENS_SEAT_2` | `0` | omitted from the request when zero |
| `JUDGE_ENDPOINT_SEAT_1` | `primary` | which endpoint configuration this seat starts on (D6) |
| `JUDGE_ENDPOINT_SEAT_2` | `primary` | which endpoint configuration this seat starts on (D6) |

`inherit` preserves `JUDGE_REASONING_EFFORT` exactly, so with the shipped global
default `""` both `inherit` and `default` send no reasoning parameter and differ
only in stated intent; `default` sends none whatever the global value is. For
LiteLLM, `none` uses the admitted model-specific vendor mapping; for OpenRouter,
existing effort values remain valid. A per-seat value passes the same provider
gate the global value passes today - `validate_request_settings()` rejects a
LiteLLM endpoint for any effort outside `{"", "none"}`
(`weblate/trans/judge.py:169-174`) and `_litellm_reasoning_disable_payload()`
raises for a model outside its allowlist (`:143-150`) - except that the resolver
hoists the allowlist check into configuration validation, so an inadmissible
seat/model pair fails at run creation instead of after the other seat is billed.

`max_tokens` is 1..8,192 when present; zero is the only omission value. It is a
footgun on the seat that motivates it: while reasoning is not actually
suppressed the cap is consumed by reasoning and the reply returns
`finish_reason=length`, which D4 makes terminal - enabling it can *raise* the
unparsed rate. 8,192 guards a typo; it is not a measured reply size, and whether
a gateway honours `max_tokens` or `max_completion_tokens` for a reasoning model
is a measurement question.

`_cached_verdict()` must require matching profile and request fingerprints per
slot, not only model strings. The request fingerprint covers provider identity,
endpoint fingerprint, model, resolved reasoning fields, `max_tokens`, the
harness parameters that change request shape, the prompt/schema version and an
effective project-context digest. That last item closes a live correctness hole:
`compute_context_hash()` covers source, note and glossary only
(`weblate/trans/models/judge.py:71-90`) while `judge_project_context()`
(`weblate/trans/judge_loop.py:187-205`) goes into the prompt, so editing a
project's judge persona currently leaves stale verdicts reusable.

Every fingerprint this plan stores uses one keyed construction, HMAC-SHA-256 with
the deployment's `SECRET_KEY` and a distinct domain label per kind. The
serialized batch contains source and target text, so a plain hash in an
append-only, report-displayable table would be a confirmation oracle for guessed
translations. Rotating `SECRET_KEY` invalidates every stored fingerprint and
therefore the cache: a documented consequence, not a silent one. The unkeyed
staleness hashes that already exist (`JudgeVerdict.target_hash`,
`target_storage_hash`, `weblate/trans/models/judge.py:51-68`) are a pre-existing
exposure of the same shape; this plan does not widen that class and does not
migrate it.

Batch shape is part of identity because it is part of the request:
`request_verdicts()` serializes the whole batch's `segments` into one user
message (`weblate/trans/judge.py:605-642`). Since D5 makes width dynamic, what
enters the fingerprint is the *realized* width of the request that produced the
verdict, together with the harness parameters that governed it. Peer content is
an open question: equal-width batches carry different neighbours, so width does
not establish request equality. A realized-membership digest cannot be the
predicate - the cache decision runs per unit before any batch exists
(`weblate/trans/judge_loop.py:507-514`) and batches are cut from whatever the
cache did not skip (`weblate/trans/judge.py:600-603`), so membership would depend
on the cache result that depends on membership. The predicate compares a stored
row's production conditions against current configuration, never against a
hypothetical batch. Reuse across different realized peers is an explicit bounded
assumption, and it is not new: today's predicate matches model names alone
(`weblate/trans/judge_loop.py:268-273`). The probe protocol gains an arm that
varies only neighbours; until it is published, no report may claim peer-crossing
equivalence.

Legacy verdicts with no fingerprint are not reused once this lands. The cost is
explicit and one-time: the first run over an existing scope re-judges it. A
legacy row cannot prove which endpoint, thinking mode or project context produced
it - the French measurement had to reconstruct exactly that.

### D4. Retries are classified, bounded, and same-seat

Keep `JUDGE_TRANSPORT_RETRIES`. Add two disabled-by-default budgets:

| setting | default | eligible terminal failure |
|---|---:|---|
| `JUDGE_PROTOCOL_RETRIES` | 0 | `empty-response`, `invalid-json`, `invalid-envelope`, `segment-count`, `invalid-segment` |
| `JUDGE_TRANSIENT_HTTP_RETRIES` | 0 | `http-server` |

The ladder inside one seat and one batch:

1. Transport failures use the transport budget with capped exponential backoff
   and full jitter.
2. `429` keeps its one bounded retry, using `Retry-After` when present.
3. A `5xx` may use `JUDGE_TRANSIENT_HTTP_RETRIES`.
4. A protocol failure may use `JUDGE_PROTOCOL_RETRIES`.
5. A deadline, over-size response, `401`, `403`, `unknown` or
   `finish_reason=length` gets no same-endpoint retry at the same shape. A
   deadline is the harness's signal to *resize*, not to repeat (D5).

Two of these change current behaviour deliberately. `403` is retried once today
(`weblate/trans/judge.py:699-702`); it is an entitlement or configuration signal,
a repeat buys a second billed refusal, and D6 wants it as a failover trigger.
`unknown` is terminal because a closed vocabulary with an escape bucket that
spends a budget automatically would let any future unclassified failure double
the bill in silence.

Every retry reuses the same profile, gets its own attempt row and consumes a
documented budget. Defaults are zero because multiplying paid requests on
existing deployments without measurement is an unsafe rollout. The retry helper
takes an injected sleeper/jitter source in tests.

### D5. The harness: size requests by predicted work, and adapt from feedback

This is the core of the plan. A fixed `JUDGE_BATCH_SIZE` is the wrong control
variable: the same nominal width succeeded on a short dev slice and failed on
French, and the failures cluster at a wall-clock boundary rather than at a unit
count. So the harness controls the quantity that plausibly matters - how much
the model has to generate before the reply lands - and it learns the edge instead
of being told.

**1. Token-budget batching.** Replace "fixed number of units per request" with
"as many units as fit a predicted output budget". For each unit, estimate output
tokens from the schema's fixed per-segment overhead plus a linear term in target
length (a back-translation and an instruction are both bounded by the target's
size). Fill a batch until the predicted total reaches
`JUDGE_OUTPUT_TOKEN_BUDGET`, and never exceed `JUDGE_BATCH_SIZE` as a hard
ceiling. One unit whose own estimate exceeds the budget is sent alone.

The estimator is calibrated, not guessed: every attempt already stores
`unit_count` and its usage row stores `completion_tokens`, so
`planned_output_tokens` versus actual is a stored residual, and the preflight
command reports the fit. A wrong estimator is visible rather than silent.

**2. Deadline-aware sizing.** The budget is expressed in tokens, but the
constraint is time. `JUDGE_TARGET_SECONDS` (default `20`, against a cliff
observed near 30 s) is the time the harness aims to stay under, and the
tokens-per-second rate is measured per `(endpoint, model)` from the attempt
history rather than configured. The token budget for the next request is
`rate x JUDGE_TARGET_SECONDS`, floored at one unit.

**3. AIMD width control.** Static tuning cannot track a proxy whose capacity
drifts with load, so the harness runs the standard congestion-control loop per
`(endpoint, seat)`:

- a `deadline` or `transport` terminal failure **halves** the effective budget,
  down to a floor of one unit;
- `JUDGE_HARNESS_PROBE_SUCCESSES` (default `5`) consecutive clean requests
  **increase** it by one unit, up to the configured ceiling;
- the current value lives in cache with a TTL, keyed by `(endpoint, model,
  seat)`, so a worker restart re-learns rather than inheriting a stale guess.

Additive increase with multiplicative decrease is chosen because the cost of
overshooting is a full wasted paid request, while the cost of undershooting is a
few extra cheap ones.

**4. What the harness must not do.** No hedged or speculative duplicate requests:
sending two copies of the same batch to beat a timeout doubles spend on a proxy
that resets *under load*, which makes the failure it is meant to hide more
likely. No silent widening beyond the configured ceiling. No adaptation on a
parser failure - that is a content or model problem, and shrinking a batch to
chase a bad envelope would hide it.

**5. Where the harness ends.** If the smallest possible request - one unit, at
the seat's admitted reasoning setting - still exceeds the envelope, no client-side
sizing can fix that seat on that endpoint. That is the boundary of this plan and
it is a measurement, not an opinion: the preflight command reports it per
`(model, endpoint)`, and the answer is then either the reasoning setting, the
gateway timeout, or placement (D6) - not a bigger retry budget.

All harness parameters enter the request fingerprint (D3), so a verdict produced
under a different learned width is not silently reused as if it were the same
request.

### D6. Seats compensate by shape and placement, never by lending an opinion

The current pair is unrunnable not because of the models but because one global
setting drives both seats: `""` kills Qwen and `"none"` rescues Qwen while
leaving DeepSeek at the ceiling, and the measurement concludes the pair "needs
both the per-model reasoning knob the plan lists as a prerequisite **and** a
batch size DeepSeek can finish inside 30 s"
(`docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md:188-194`).
D3 gives per-seat reasoning; D5 gives per-seat, self-adjusting size. This section
adds the third axis and the limits of all three.

**Placement.** A seat's profile carries its own endpoint, so the two seats may
sit on different providers at the same time: `JUDGE_ENDPOINT_SEAT_1` and
`JUDGE_ENDPOINT_SEAT_2` select `primary` or `fallback` from the two endpoint
configurations, with the other remaining that seat's fallback. No new credential
fields and no per-seat URL duplication - the failover plan's D1 already
established that an endpoint is a configuration object rather than a URL.

What that buys is precise and limited: it removes **one shared failure domain**,
the proxy both seats currently sit behind, so a reset confined to that gateway
can no longer fail both seats at once. It does not make double failures
impossible - a client deadline set too low, our own request-shape bug, an egress
fault, a shared upstream host behind two gateways, or both providers degrading in
the same hour remain correlated causes.

**Fallback.** From the absorbed plan
(`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:53-66`):
`JUDGE_FALLBACK_BASE_URL` (empty disables everything), its own
`JUDGE_FALLBACK_API_KEY`, `JUDGE_FALLBACK_MODEL_SEAT_1`,
`JUDGE_FALLBACK_MODEL_SEAT_2`, `JUDGE_FALLBACK_REASONING_EFFORT`. Fallback is per
seat and per batch: a failed primary seat 1 resends only seat 1, seat 2 untouched.
Order, once the D4 budgets are spent:

1. For a terminal transport failure, exhausted `429`, `401`, `403`, or exhausted
   `5xx`, call the fallback exactly once for that seat and logical batch.
2. Record primary and fallback attempts separately, each with its own provider,
   profile fingerprint and `endpoint_role`.
3. A `200` parser failure, deadline, oversized reply, `unknown` and
   `finish_reason=length` never reach the fallback. A deadline is a sizing
   signal (D5), not an availability fault.

`get_judge_chat_completions_url()` re-reads `settings.JUDGE_BASE_URL` on every
POST (`weblate/trans/judge.py:121-123`, called from `:535-547`) and
`request_verdicts()` takes only `model` (`:574-583`). Both become explicit
per-request inputs carried by the resolved profile; an endpoint may not be an
ambient setting read deep inside a POST once two exist. This also fixes the live
bug the failover plan identified in its D6: the reasoning payload branch and its
validation both read the *primary* provider (`weblate/trans/judge.py:643-654`,
`:169-174`), so a fallback POST would otherwise carry the primary's reasoning
decision.

**A single-seat round is not a defect to repair later.** A unit one seat judged
has a verdict and is not stuck, and completing that round afterwards is not
possible in the current model: when a seat fails, `_persist_verdict_batches`
(`weblate/trans/judge_loop.py:412-446`) writes that seat's *unparsed*
`JudgeVerdict` row into the round, and `judge_one_vote_per_seat`
(`weblate/trans/models/judge.py:333-336`) then forbids a second row for the same
seat in the same `(unit, run_id, attempt)`. Retro-fitting a second opinion needs
a versioned round identity and a new collegium pairing model - a separate plan.
What this plan does instead: report single-seat rounds (D10), and refuse to let a
single-seat `pass` reach `STATE_APPROVED`, because a pass is the absence of
findings and only one voter appeared
(`weblate/trans/models/judge.py:393-396`). A single-seat *finding* stands
unchanged: a defect one seat can evidence with a span and a category is a defect.

**Placement precondition.** A seat may be configured on an (endpoint, reasoning)
combination only where a preflight row shows the harness can find an envelope for
it on the corpus being judged. `validate_judge_configuration()` cannot detect a
violation, so Task 9's command answers it before a run and the run report's
per-seat parse rate reveals drift afterwards.

### D7. One width-one isolation round

After the ordinary rounds and any content repair, a bounded phase **around** the
existing repair loop - never a recursive branch inside it. `run_judge_batch()`
already runs `while True: judge -> prepare -> repair` with `attempt` bounded by
`JUDGE_MAX_REPAIR_ATTEMPTS` (`weblate/trans/judge_loop.py:499-611`).

```text
for round in 1 .. JUDGE_MAX_UNPARSED_RETRY_ROUNDS:
    select units whose current round is all-unparsed and unchanged
    stop when the selection is empty
    wait one bounded jittered delay inside the same producer task
    rejudge the selection at width one, both seats, primary then fallback
    re-enter the existing repair loop with the units whose fresh verdict is
      repairable, carrying each unit's already-spent `attempt`
```

`JUDGE_MAX_UNPARSED_RETRY_ROUNDS` defaults to 0. This round is the harness's
floor case: width one is the smallest request that exists, so a unit that fails
here has exhausted client-side sizing and belongs in the queue.

Selection reads the current round only. A unit qualifies when every seat row of
that round is unparsed; through `collegium_verdict()` that also matches a round
whose second seat row is missing (`weblate/trans/models/judge.py:507-511`), which
is intended. A changed unit is excluded because `current_round()` returns empty
for it (`:423-451`) - that is how a stale unit is told apart from a
transport-dead one, and it becomes `stale-conflict`
(`JudgeRunUnit.Outcome.STALE_CONFLICT` already exists, `:161`).

Within a round: refresh and hash-check the selection; rejudge under a new
`JudgeVerdict.run_id` tagged `round_kind=unparsed-retry`, keeping the unit's
existing `attempt` so that field keeps its single meaning while the fresh
`run_id` satisfies the `(unit, run_id, attempt, seat)` constraint (`:333-336`);
feed a parsed result through the normal collegium, and a repairable one back into
the repair loop with the already-spent `attempt`, so the existing
`attempt > attempts` bound terminates it. A re-judge triggered by such a repair is
an ordinary `repair` round; if it comes back double-unparsed it may be selected by
the **next** retry round, never inside the current one. No recursion, no third
loop.

Progress must not use a paid-request ceiling as a denominator. Ticks happen once
per completed batch (`weblate/trans/autotranslate.py:757-773`) against
`progress_steps = batches × seats × (JUDGE_MAX_REPAIR_ATTEMPTS + 1)`
(`:701-705`). With D5 the batch count is no longer `ceil(units / fixed_width)`
per seat, so `preview_judge_scope()` and `judge_request_upper_bound()`
(`weblate/trans/judge.py:49-64`) must derive an estimate from the harness's
current budget per seat and be documented as an estimate; the retry phase extends
`progress_steps` when it begins, by `selected × rounds × seats`, and
`_judge_phase()` gains an `unparsed retry` phase. The estimate shown before a run
(`weblate/trans/views/basic.py:901-909`) says it excludes retries, fallback and
deferred passes.

### D8. A durable queue that drains itself

Nobody is notified and nobody is assigned. A unit the harness could not judge is
persisted and retried on a schedule until it succeeds or its subject disappears.

Add `JudgeDeferral`, following the durable-queue precedent of
`PendingUnitChange` (`weblate/trans/models/pending.py:416-449`, consumed by
`commit_pending`, `weblate/trans/tasks.py:229-247`):

| field | purpose |
|---|---|
| `unit` (FK, `SET_NULL`), `unit_id_snapshot` | subject, surviving deletion |
| `translation_id`, `component_id`, `project_id` | scope for selection |
| `target_hash`, `context_hash` | what it was deferred about |
| `origin_run` (FK) | which run first deferred it |
| `first_deferred`, `last_attempted`, `next_attempt_after` | age and backoff schedule |
| `passes_done`, `consecutive_failures` | pacing inputs |
| `last_failure_kind`, `last_endpoint_role` | why it is still here |
| `state` | `queued`, `slow`, or closed |
| `closed_reason`, `closed_at` | how it left |

Uniqueness is `(unit_id_snapshot, target_hash, context_hash)` among rows that are
not closed, so re-deferring is idempotent and a re-run never duplicates work.

A row is created when a producer run finishes a unit with no parsed opinion after
the full ladder. That replaces writing `JudgeRunUnit.Outcome.UNPARSED`: the run
unit's outcome becomes `deferred`, and the unparsed evidence stays on the
`JudgeVerdict` and attempt rows.

**A row leaves the queue only in these ways, and none of them is "we gave up":**

| `closed_reason` | trigger |
|---|---|
| `resolved` | a later pass produced a parsed collegium verdict for the same hashes |
| `stale` | target or context hash changed: the text it was about no longer exists |
| `unit-gone` | the unit was deleted |
| `out-of-scope` | the judge is no longer configured for that component or project |

**Nothing expires.** There is no maximum pass count that discards work, and an
open breaker (D9) closes no row either - it only stops paid traffic while it is
open, and its half-open probe brings the work back automatically. A row that
keeps failing moves to `state=slow`, the same queue at the longest backoff
interval, and stays eligible indefinitely. `next_attempt_after` grows
exponentially with full jitter from `JUDGE_DEFERRAL_MIN_INTERVAL` to
`JUDGE_DEFERRAL_MAX_INTERVAL`; full jitter because a scope-wide outage would
otherwise re-send every row in the same second and rebuild the load that caused
the failure.

**Consumer.** A periodic task `judge_deferred_units`, registered like
`cleanup_loc_kit_drafts` (`weblate/trans/tasks.py:1348-1363`, registered at
`:1523-1525` through `setup_periodic_tasks`, `:1493-1496`), selects rows whose
`next_attempt_after` has passed, oldest first, up to
`JUDGE_DEFERRAL_MAX_UNITS_PER_PASS`, groups them by translation and launches an
ordinary judge run through the existing task: `auto_translate` already accepts
`unit_ids: list[int] | None` and threads it end to end
(`weblate/trans/tasks.py:975-996`, `:1021-1031`;
`weblate/trans/autotranslate.py:207-224`, `:276-310`, `:321-328`, `:1300-1320`).
Each pass is a first-class `JudgeRun` with its own report and `JudgeRunUnit`
rows, and the constraint is `(run, unit_id_snapshot)`
(`weblate/trans/models/judge.py:235-238`), so a later run never collides. And
there is still **no Celery autoretry of a judge task**, which would create an
ambiguous duplicate run.

| setting | default | meaning |
|---|---:|---|
| `JUDGE_DEFERRAL_ENABLED` | `False` | off: units keep today's `unparsed` outcome |
| `JUDGE_DEFERRAL_MIN_INTERVAL` | `900` | first backoff step, seconds |
| `JUDGE_DEFERRAL_MAX_INTERVAL` | `86400` | ceiling for `slow` rows, seconds |
| `JUDGE_DEFERRAL_SLOW_AFTER` | `5` | consecutive failures before a row is `slow` |
| `JUDGE_DEFERRAL_MAX_UNITS_PER_PASS` | `200` | cost ceiling per periodic tick |

With `JUDGE_DEFERRAL_ENABLED=False` nothing changes anywhere: same outcomes, same
reports, same spend.

### D9. Standard protections around a failing dependency

Retrying is not reliability; unbounded retrying against a sick endpoint is an
outage amplifier. Four guards, all ordinary practice, all bounded by
configuration.

**Retry budget, not just counts.** Per-request counts (D4) cap one request, not
the blast radius. A token-bucket budget per `(endpoint, seat)` is drawn by
retries, deferred passes and fallback calls, and refilled by ordinary first
attempts. Empty bucket means no retry is issued: the work returns to the queue
for its backoff. `JUDGE_RETRY_BUDGET_RATIO` (default `0.2`) is the share of total
requests that may be retries, which is what stops a 100%-failing endpoint from
tripling the bill.

**Circuit breaker per `(endpoint, model)`.** After `JUDGE_BREAKER_FAILURES`
(default `10`) consecutive terminal failures the breaker opens: no requests for
that pair for `JUDGE_BREAKER_COOLDOWN` (default `300` s), and affected work is
deferred, not failed. One half-open probe then decides: a parsed reply closes the
breaker, another failure re-opens it with a doubled cooldown up to a ceiling.
While a seat's endpoint is open, that seat uses its configured fallback if there
is one (D6) - the automatic version of one seat covering for the other.

This is a *transport* breaker. It never converts a two-seat judge into a one-seat
judge by policy: it pauses an endpoint, seats and pairing are untouched, and
whatever a paused seat could not judge is queued. The quality breaker stays out
of scope.

**Bulkheads.** `JUDGE_DEFERRAL_MAX_UNITS_PER_PASS` bounds one periodic tick,
`JUDGE_MAX_UNITS_PER_RUN` bounds one producer run, the breaker bounds a sick
endpoint, and the harness bounds one request. Nothing here can start unbounded
work, so a broken proxy produces a growing queue and a flat spend curve rather
than a spend spike.

**Poison-payload isolation.** If one unit keeps failing while its batch peers
succeed, neither batching nor the endpoint is the problem. D7 already sends it
alone; after `JUDGE_DEFERRAL_SLOW_AFTER` failures it becomes a `slow` row,
retried at the ceiling interval instead of hammered, and still never dropped - a
prompt, schema or model change may make it judgeable later, and the queue is what
remembers. The evidence says this case is rare or absent: a previously unparsed
unit replayed unchanged and parsed
(`docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md:146-152`).

### D10. Reports show the harness working, not a request for attention

The `JudgeRun` report gains: the safe configuration snapshot; outcomes before and
after the retry phase; per-seat counts by `failure_kind`, attempt count and
elapsed-time percentile, split by `endpoint_role`; responses reporting reasoning
tokens; **per-seat parse rate and the count of rounds decided by a single parsed
seat**; the harness's learned budget per seat with predicted-versus-actual output
tokens; breaker openings; deferrals created, drained and currently queued; and a
scope-authorized, paginated technical attempt table with no prompt or raw
response, reusing the existing `Paginator` pattern
(`weblate/trans/views/judge.py:160-170`,
`weblate/templates/judge-run.html:123-125`) and the existing scope/actor recheck
(`:91-109`, `:149-160`).

The single-seat count is not decoration. At the measured rates the chance that
exactly one seat parses is `0.805 × 0.659 + 0.195 × 0.341 ≈ 0.60`: most judged
strings in that run were decided by one seat, and `collegium_verdict()` reports
such a round as an ordinary verdict with no marker
(`weblate/trans/models/judge.py:499-512`).

The completion message replaces today's "N strings were left unjudged"
(`weblate/trans/autotranslate.py:839-847`) with a factual line about work in
flight: "12 strings deferred, retrying automatically; 9 transport, 3 invalid
JSON". No alert, no notification, no request for a decision - the queue is
expected to resolve them, and the report is where a curious operator looks.

### D11. Stability of the run itself

1. **A killed worker.** A run left `RUNNING` by a terminated process is not
   healed by any in-process mechanism. A periodic reaper marks runs `RUNNING`
   past `JUDGE_RUN_STALE_AFTER` (default 6 h) as `FAILED` with a stated reason.
   Their units were never recorded, and the next ordinary run re-selects them by
   content - judge scope is a search query, not a run-derived set
   (`weblate/trans/autotranslate.py:674-699`).
2. **An exception mid-run.** Handled by the existing `BatchAutoTranslate`
   finalization (`weblate/trans/autotranslate.py:1265-1273`, `:1429-1438`).
   Deferrals already written stay queued, which is why they are persisted.
3. **Queue growth.** Bounded by scope size, visible in the report, and drained
   at a bounded rate. A permanently broken endpoint yields a growing but bounded
   queue with a flat spend curve.

## Implementation tasks

Three waves. Wave A is worth landing whatever the probes say; wave B is the
harness itself; wave C is convergence.

**Wave A - evidence:** Tasks 1, 2, 3.
**Wave B - harness:** Tasks 4, 5, 6, 7.
**Wave C - convergence:** Tasks 8, 9.

Each task adds its own migration; a committed migration is never amended. The
newest today is `weblate/trans/migrations/0108_judge_verdict_instruction.py`.

### Task 1: Testable failure classification

**Files:** `weblate/trans/judge.py`,
`weblate/trans/tests/test_judge_client.py`.

1. Replace the parser's untyped `None` with a private typed outcome carrying
   either aligned results or one D1 failure kind.
2. Extend `_BatchResponse` with response byte count and finish reason, and split
   deadline from oversize inside `_read_batch_response()`.
3. Build a `JudgeAttemptEvent` per POST and add the private observer beside
   `on_batch`.
4. Keep `request_verdicts()`'s return type and all-or-nothing batch behaviour
   until later tasks opt in.
5. Fixtures for every closed failure type, including an unrecognized shape as
   `unknown`.

Tests assert category and metadata, not only `result.unparsed`. No fixture
carries a production token or a user translation.

Commit: `feat(judge): classify judge request failures`

### Task 2: Attempt provenance and configuration snapshot

**Files:** `weblate/trans/models/judge.py`,
`weblate/trans/models/llm_usage.py`, a migration, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `weblate/trans/autotranslate.py`, and the judge
client/loop/autotranslate/migration tests.

1. Add `JudgeRequestAttempt`, the nullable `JudgeVerdict` provenance fields and
   `JudgeRun.configuration` plus its persistence-loss counter - `JudgeRun` has
   neither field today (`weblate/trans/models/judge.py:108-132`).
   `judge_provider` lands here; the failover plan is edited in the same commit.
2. Introduce the `JudgeRequestProfile` resolver for existing global settings
   only, resolving and fingerprinting both slots before `JudgeRun` creation.
3. Give new `LLMUsageLog` rows the one-to-one attempt link; the attempt persists
   when usage is absent.
4. Thread producer run, logical round ID, slot and batch index from
   `run_judge_batch()` into the observer and the verdict writer. The `run`
   argument is accepted and never used today
   (`weblate/trans/judge_loop.py:466` against its body); this is its first use.
5. Write attempt and usage rows through one persistence function taking an
   optional run, filling `batch_size` and `outcome`.
6. Hand the terminal attempt to the verdict writer through the shared
   `(seat, batch_index)` state; `on_batch` keeps its signature.
7. All observability writes are best effort: a failed insert never suppresses a
   parseable verdict or changes a retry decision; it increments the counter.
8. Index `(judge_run, seat, batch_index, attempt_index)` and
   `(judge_run, failure_kind)`. Never index user-originated text.
9. Schema defaults only; no fabricated provenance for historical rows.

Tests: a parseable reply, a parser-rejected payload and a reset each create the
expected attempt; a billed parser failure links one usage row; an unbilled reset
has an attempt and no usage row; a producer run links its fresh verdicts to
itself rather than a timestamp window; snapshots contain no key, header, prompt,
target or raw response; an accounting error changes neither verdict nor retry; a
transaction failure increments the counter with no orphaned FK; `on_batch` still
fires exactly once per completed batch; migration defaults keep old rows
renderable.

Commit: `feat(judge): record request attempt diagnostics`

### Task 3: Per-seat profiles and keyed cache identity

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`, a migration,
`docs/admin/config.rst`, `docs/admin/install/docker.rst`, and the judge
client/loop tests.

1. Add the per-seat reasoning, `max_tokens` and endpoint settings to every
   surface, and repair the existing drift in the same pass:
   `weblate/trans/models/_conf.py:102-112` omits `JUDGE_REQUEST_DEADLINE`,
   `JUDGE_TRANSPORT_RETRIES` and `JUDGE_REASONING_EFFORT`;
   `deploy/environment.example:116-129` omits the deadline and transport retries
   and only comments the reasoning setting; `docs/admin/install/docker.rst`
   documents no environment variable for either. Drop the stale claim at
   `deploy/environment.example:128` that reasoning "must stay empty" on LiteLLM -
   `none` is admitted.
2. Extend the resolver and its validation before the first paid POST; it is the
   only place that decides whether a vendor thinking field, a generic reasoning
   field or no field is sent, and it hoists the LiteLLM allowlist check into
   configuration validation.
3. Build the payload from the resolved profile, adding `max_tokens` only when
   non-zero.
4. Compute every fingerprint through one keyed helper (HMAC-SHA-256,
   `SECRET_KEY`, one domain label per kind), never a bare `hashlib` call over
   request content. Update `_cached_verdict()` to require matching
   slot/profile/request fingerprints including the project-context digest,
   comparing stored production conditions against current configuration only.
5. Record the realized `unit_count` and keyed batch digest as evidence; nothing
   reads them as a cache predicate.

Tests: all-inherit reproduces today's payloads; Qwen `none` emits
`enable_thinking: false`; DeepSeek `none` emits only its admitted vendor control;
`default` emits nothing; per-seat settings express DeepSeek default plus Qwen
disabled; `max_tokens` bounds and rejections behave; a different profile,
provider or project context misses cache; an inadmissible seat/model pair fails
before any POST; matching profiles retain reuse; no stored fingerprint equals the
unkeyed hash of its input and two domain labels differ; blank-profile evidence
never satisfies the predicate.

Commit: `feat(judge): support per-seat request profiles`

### Task 4: Bounded classified retries

**Files:** `weblate/trans/defaults.py`, settings surfaces,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/tests/test_judge_client.py`.

1. Add `JUDGE_PROTOCOL_RETRIES` and `JUDGE_TRANSIENT_HTTP_RETRIES`, both zero.
2. Replace the ad-hoc loop with one classifier-driven decision, preserving
   transport and `429` semantics, making `403` terminal (removing the retry at
   `weblate/trans/judge.py:699-702`) and keeping `unknown` terminal.
3. Record `will_retry`, retry ordinal and elapsed time on every attempt before
   sleeping.
4. Capped exponential backoff with full jitter from an injectable source.
5. A parsed response ends the loop immediately; a permanent failure ends it
   without an extra paid POST.

Tests: exact call counts per category; `max_tokens` truncation, oversized and
deadline responses, `403` and `unknown` never retry at the same shape; a parser
failure recovers only with a positive protocol budget; retry events share
logical batch, seat and profile; exhausting every budget yields `unparsed`, never
an exception and never a pass.

Commit: `feat(judge): retry classified transient failures`

### Task 5: The request harness

**Files:** `weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/defaults.py`, settings surfaces,
`weblate/trans/models/_conf.py`, `weblate/trans/autotranslate.py`,
`docs/admin/config.rst`, `docs/admin/install/docker.rst`, and the judge
client/loop/autotranslate tests.

1. Add `JUDGE_OUTPUT_TOKEN_BUDGET`, `JUDGE_TARGET_SECONDS`,
   `JUDGE_HARNESS_PROBE_SUCCESSES` and a feature flag
   `JUDGE_HARNESS_ENABLED` (default `False`, which reproduces fixed-width
   batching exactly).
2. Replace fixed slicing (`weblate/trans/judge.py:600-603`) with the D5
   token-budget batcher, keeping `JUDGE_BATCH_SIZE` as the hard ceiling and one
   unit as the floor.
3. Estimate per-unit output tokens from schema overhead plus a linear term in
   target length; store `planned_output_tokens` and `planned_seconds` on the
   attempt.
4. Measure tokens per second per `(endpoint, model)` from attempt and usage
   history; derive the next budget as `rate × JUDGE_TARGET_SECONDS`.
5. Implement AIMD per `(endpoint, seat)` in cache with a TTL: halve on
   `deadline` or `transport`, additively increase after
   `JUDGE_HARNESS_PROBE_SUCCESSES` clean requests, never exceed the ceiling,
   never adapt on a parser failure.
6. Feed the harness parameters into the request fingerprint and the D2 snapshot.
7. Make `preview_judge_scope()` and `judge_request_upper_bound()` derive an
   estimate from the current budget per seat, and label it an estimate in the UI
   string.

Tests: with the flag off, batching is byte-identical to today; a batch never
exceeds the token budget or the ceiling; an oversized single unit is sent alone;
a `deadline` halves the budget and a `transport` failure does too; five clean
requests raise it by one; a parser failure changes nothing; the budget never
drops below one unit or above the ceiling; predicted and actual tokens are both
recorded; the learned value is per `(endpoint, model, seat)` and does not leak
across seats; progress totals stay consistent when width varies mid-run.

Commit: `feat(judge): size judge requests by predicted work`

### Task 6: Explicit endpoints, per-seat placement and fallback

**Files:** `weblate/trans/defaults.py`, settings surfaces,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `docs/admin/config.rst`,
`docs/admin/install/docker.rst`, `docs/security/threat-model.rst`, and the judge
client/loop tests.

1. Make the endpoint an explicit per-request input carried by the profile.
2. Add the five `JUDGE_FALLBACK_*` settings and the two
   `JUDGE_ENDPOINT_SEAT_*` selectors, validated before any request.
3. Trigger the fallback exactly once per seat and logical batch, only on D6's
   availability list; never on a parser-invalid `200`, deadline, oversized reply,
   `unknown` or `finish_reason=length`.
4. Record `endpoint_role` on attempts and fresh verdicts; add endpoint
   fingerprints to the snapshot.
5. Update `docs/security/threat-model.rst`: a second outbound endpoint and
   credential change the outbound integration surface.

Tests: no fallback without configuration; one fallback attempt per seat and
batch; a parser-invalid `200` never reaches it; a fallback answer is a normal
opinion of that seat; the two seats can run on different endpoints
simultaneously and a failure on one endpoint does not affect the other seat; a
fallback verdict does not satisfy the cache predicate of a primary-configured
run; the fallback key never appears in a snapshot, log or exception.

Commit: `feat(judge): place seats per endpoint with per-seat fallback`

### Task 7: The width-one isolation round

**Files:** `weblate/trans/defaults.py`, settings surfaces,
`weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/models/judge.py`, `weblate/trans/autotranslate.py`, a migration,
and the judge loop/autotranslate tests.

1. Add `JUDGE_MAX_UNPARSED_RETRY_ROUNDS`, default zero.
2. Add `round_kind` and the retry-round count to producer-visible persistence
   without overloading `JudgeVerdict.attempt`.
3. Implement the D7 phase as the outer bounded loop, preserving `attempt`, with
   no recursion.
4. Refresh before the delayed call; a target/context race is `stale-conflict`.
5. Extend progress at phase start and add the `unparsed retry` phase.

Tests: a one-seat loss does not enter the phase; a double-unparsed unit is
retried once per configured round at width one; retry success replaces the run
outcome without deleting prior unparsed evidence; a retried unit that already
spent `JUDGE_MAX_REPAIR_ATTEMPTS` gets no extra attempt; a repair-triggered round
that comes back double-unparsed waits for the next retry round; cached,
cap-skipped, permission-skipped and deleted units are never retried; no duplicate
`(run, unit)`, verdict or attempt row; progress never exceeds its total; an
exception during the phase leaves a correctly `FAILED` run.

Commit: `feat(judge): isolate double-unparsed units at width one`

### Task 8: The queue and its protections

**Files:** `weblate/trans/models/judge.py`, a migration,
`weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`,
`weblate/trans/judge.py`, `weblate/trans/defaults.py`, settings surfaces, and
the judge model/loop/autotranslate/task tests.

1. Add `JudgeDeferral` with the D8 fields, the not-closed uniqueness constraint,
   and indexes on `(state, next_attempt_after)` and `(component_id, state)`.
2. Add `JudgeRunUnit.Outcome.DEFERRED` and write it instead of `UNPARSED` at both
   current sites (`weblate/trans/autotranslate.py:858-879`, `:887-895`) when
   `JUDGE_DEFERRAL_ENABLED`. With the flag off, behaviour is byte-identical.
3. Create or refresh a row idempotently by `(unit, target_hash, context_hash)`;
   close only on the four machine reasons; move to `slow` after
   `JUDGE_DEFERRAL_SLOW_AFTER` consecutive failures; never expire a row.
4. Exponential backoff with full jitter between the configured bounds.
5. Add `judge_deferred_units` through `setup_periodic_tasks`, respecting the
   per-pass cap, grouping by translation and launching `auto_translate` with
   `mode="judge"` and explicit `unit_ids`.
6. Add the retry budget, the `(endpoint, model)` breaker with half-open probe,
   and the settings for both. A deferred row is never closed by either.
7. Add the D11 reaper for runs stuck in `RUNNING`.

Tests: the flag off changes nothing; a unit with no opinion produces exactly one
queued row and a `deferred` run outcome; re-running does not duplicate it; a
later pass with a verdict closes it `resolved`; a target edit closes it `stale`; a
deleted unit closes it `unit-gone`; disabling the judge for the scope closes it
`out-of-scope`; no other transition closes a row, and a row with hundreds of
failures is still `slow` and still eligible; backoff grows and is jittered; the
periodic task respects the cap and creates a real `JudgeRun` per translation; an
empty retry budget defers instead of retrying; the breaker opens after the
configured failures, sends nothing while open, defers the affected work, and
recovers on a successful half-open probe; the breaker never changes seat pairing
or unit state.

Commit: `feat(judge): drain unjudged strings with a durable retry queue`

### Task 9: Report, preflight, and the contract test

**Files:** `weblate/trans/views/judge.py`,
`weblate/templates/judge-run.html`, `weblate/trans/autotranslate.py`,
`weblate/trans/management/commands/` (one new preflight command),
`docs/admin/config.rst`, `docs/admin/install/docker.rst`,
`docs/changes.rst`, and the judge view/report tests.

1. Add the D10 aggregates and the paginated permission-checked attempt table. No
   global search filter, no payload text.
2. Replace the completion warning with the D10 wording.
3. Add the preflight command: it uses the production parser, profile resolver,
   harness and endpoints; sweeps a caller-supplied frozen slice to find the
   largest request each `(model, endpoint)` completes reliably, reporting the
   elapsed-time distribution, predicted-versus-actual tokens and the parse rate;
   records no translations; takes an explicit model and profile and never an API
   key from the command line; writes a dated measurement artifact. This is what
   answers D6's placement precondition and D5's envelope question.
4. Add the contract test: for a synthetic run in which every request fails, no
   selected unit ends with an outcome outside
   `{deferred, skipped, stale-conflict}`, every deferred unit has a queued row
   with a future `next_attempt_after`, and no unit's state changed.
5. Document the retention rule: attempts, verdicts, usage and closed deferrals
   are retained; pruning is out of scope.
6. Changelog entry for the user-visible parts: deferred judging and the fallback
   endpoint.
7. Run the focused test files, migration checks, `uv run prek run --all-files`
   and the narrowest practical Django report tests. Do not restart `dev-docker`
   for this plan's tests.

Commit: `feat(judge): report the harness and prove the completion contract`

## Live measurement and rollout gates

Live probes and deployment are separate approvals.

Before enabling the harness or any non-zero retry setting:

1. **Ask about the gateway timeout** and record the answer. If the cliff is
   configuration, fix it there first and re-measure.
2. **Envelope sweep.** For each `(seat model, endpoint, reasoning setting)`, find
   the largest request that completes reliably, three time-separated repeats, on
   the corpus that will actually be judged. Record elapsed time, completion
   tokens and target length per unit - not just a batch count, because the same
   nominal width behaved differently on two corpora.
3. **Profile.** Hold Qwen at its known disabled-thinking profile; compare
   DeepSeek's default, any confirmed vendor-specific disabled profile, and one
   bounded `max_tokens` candidate. One variable per arm.
4. **Fallback and placement.** Verify the fallback endpoint answers the same
   prompt and schema for both seat models, that a forced availability fault
   produces exactly one fallback attempt, and that split placement works.
5. **Peers.** One arm holding profile and width fixed, varying only neighbours.
6. Publish everything in `docs/llm-first/measurements/`.

**Structural gate** (provable by tests, before `JUDGE_DEFERRAL_ENABLED` or
`JUDGE_HARNESS_ENABLED` is turned on anywhere):

- the Task 9 contract test passes with a 100%-failing endpoint: no unit is
  abandoned and none changes state;
- `JudgeRunUnit.Outcome.UNPARSED` is no longer written by any code path;
- no mechanism closes a queue row except the four machine reasons;
- an open breaker and an empty retry budget both defer work instead of dropping
  it, and the half-open probe recovers it;
- the harness never exceeds `JUDGE_BATCH_SIZE`, never goes below one unit, and
  never adapts on a parser failure;
- no retry for a parsed verdict, oversized reply, `401`, `403`, `unknown` or
  `finish_reason=length`; no fallback on a parser-invalid `200`;
- zero persistence losses, and every attempt and usage row joins its `JudgeRun`;
- no credential or translation text in snapshots or attempt rows, and no stored
  fingerprint is an unkeyed hash of its input.

**Empirical gate** (decides whether the LiteLLM path may carry a seat):

- with the harness on, per-seat terminal parse rate at or above 99% on the
  endpoint each seat is placed on, across at least three repeats of at least 100
  units, on the corpus being judged;
- the queue reaches empty within a bounded number of scheduled passes in each
  repeat, and no row reaches `slow`;
- spend per judged unit stays within the pre-measured envelope: the harness must
  buy stability with small requests, not with many retries;
- the single-seat share is reported and does not exceed what the envelope sweep
  predicted.

If the empirical gate fails for a seat, the structural one still holds - nothing
is lost or silent - and the remaining levers are the gateway timeout, the
reasoning setting, or placement (D6). Model choice stays with
`docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md`, whose
admission gate is "Unparsed rate <= 5%" in every repeat (`:207-210`).

Only after both gates pass may
`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` receive a live
serial-versus-parallel canary approval, holding harness parameters, budgets and
endpoints fixed.

## Out of scope

- Choosing the permanent seat pair or replacing a model: the A/B plan owns it.
- Prompt, rubric, schema, severity or repair-policy changes.
- Completing a single-seat round retroactively: it needs a versioned round
  identity and a new collegium pairing model (D6).
- A quality circuit breaker that intentionally degrades a two-seat judge to one
  seat. The transport breaker of D9 is not that.
- Hedged or speculative duplicate requests.
- Parallel requests within or across seats.
- Recording raw prompts, completions or reasoning traces.
- Pruning attempts, verdicts, usage rows or closed deferrals.
- Alerts, notifications, assignment or any workflow that asks a human to act.
- Healing a run whose worker was killed beyond marking it `FAILED`.
- Production deployment, configuration changes, or paid probes without their
  explicit approvals.

## Expected files

- `weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
  `weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`
- `weblate/trans/models/judge.py`, `weblate/trans/models/llm_usage.py`
- new migrations after `weblate/trans/migrations/0108_judge_verdict_instruction.py`
- `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
  `weblate/settings_example.py`, `deploy/environment.example`,
  `weblate/trans/models/_conf.py`
- `weblate/trans/views/judge.py`, `weblate/templates/judge-run.html`
- one new management command under `weblate/trans/management/commands/`
- `docs/admin/config.rst`, `docs/admin/install/docker.rst`,
  `docs/security/threat-model.rst`, `docs/changes.rst`
- existing judge client, loop, round, autotranslate, migration and view tests
