# Plan: a completion contract for the judge

**Date:** 2026-08-27. **Status:** proposed, not started; rewritten 2026-08-27
after review to target zero unjudged strings rather than a lower failure rate.
Implementation needs explicit approval. **Owner decision required before live
probes:** they spend provider credits but write no production translations.

This plan absorbs the *mechanism* of
`docs/llm-first/plans/2026-08-26-judge-provider-failover.md` - its two-endpoint
configuration, its per-seat/per-batch fallback, its availability-only trigger
list, its `judge_provider` provenance field and its parser-invalid-`200` safety
rule (that plan's D1-D4, D6, D7 and Stages B-C). That mechanism is the only
measured terminal machine resolver we have, so it cannot stay a separate future
step. Its Stage A - scoring LiteLLM candidates against ground truth - is *not*
absorbed: model selection stays with
`docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md`.

## What "zero" means here

`unparsed` is two different things in this system, and only one of them can go
to zero.

1. **A failed request.** `JudgeVerdict.unparsed` records that one POST for one
   seat produced no usable answer (`weblate/trans/models/judge.py:286-288`).
   This cannot be driven to zero: it describes someone else's endpoint. It stays
   as evidence, and this plan makes it explainable instead of anonymous.
2. **A unit that ends without an opinion.** Today this is a terminal outcome:
   `JudgeRunUnit.Outcome.UNPARSED`, written both when a unit has no verdict at
   all and when its round is all-unparsed
   (`weblate/trans/autotranslate.py:858-879`, `:887-895`), with a warning that
   says "N strings were left unjudged" (`:839-847`) and nothing that owns them.

**This is the goal: outcome 2 stops existing.** Not as a lower rate - as a
contract. No selected unit may leave a producer run in an unowned, unexplained
absence of a verdict. Every unit ends in exactly one of:

| terminal state | meaning | who owns it next |
|---|---|---|
| a parsed verdict | machine opinion recorded | the existing resolution workflow |
| `skipped` | policy: permission or cap | the operator who set the policy |
| `stale-conflict` | the judged text changed under us | nobody: the next run re-selects it |
| `deferred`, open | machine could not judge it yet | a bounded machine ladder, then a named human |

`deferred` is not a rename of `unparsed`. It is a durable row that stays open
until something terminal actually happens to it (D7), and while it is open the
unit carries a visible check (D8). A run that produces open deferrals says so.

Two claims follow, and they are deliberately different in kind:

- **Structural (provable, gated by tests):** zero units end a run unowned.
- **Empirical (measured, gated by canary):** the number of units that need the
  human resolver at all is ~zero, because each seat is placed where it is
  measured healthy (D5).

## Why zero is reachable: the measured cause

The 24 double-unparsed units of the French run were not a judge-code defect.

| measurement | result | source |
|---|---|---|
| LiteLLM, DeepSeek seat, batch 2, reasoning `none` | 66/82 unparsed (80.5%) | `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md:20-68` |
| LiteLLM, Qwen seat, same run | 28/82 unparsed (34.1%) | same, `:20-68` |
| DeepSeek usage rows reporting reasoning tokens | 37/37 | same, `:113-126` |
| LiteLLM, `qwen3.8-max`, reasoning **on** | 8/8 batches failed | `docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md:109-116` |
| LiteLLM, `qwen3.8-max`, reasoning **off** | 0/8 batches failed | same, `:109-116` |
| OpenRouter, both seat models, 124 units | 0 unparsed | `docs/llm-first/measurements/2026-08-14-st2-zh-judge-run.md:38-55` |
| OpenRouter, dev screen, 376 records | 0 unparsed for six models | `docs/llm-first/measurements/2026-08-13-phase0-measurements.md:119-190` |
| a previously unparsed unit, replayed unchanged | parsed | `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md:146-152` |

Read together they give a causal chain rather than a rate: **the LiteLLM proxy
resets connections when reasoning is on**, and DeepSeek's reasoning is not
suppressible there - `{"thinking": {"type": "disabled"}}` still returned
reasoning tokens on 37 of 37 billed rows. A model whose reasoning cannot be
turned off, placed on an endpoint that breaks when reasoning is on, is a
**placement error**. The same two models on OpenRouter measured 0 unparsed over
124 and 376 records.

The replay result matters just as much: the failures are transient, not
string-deterministic. There is no recorded case of a specific string
deterministically breaking the parser. That is what makes a bounded ladder
converge instead of looping forever on the same input.

So the ladder below is not hope. It is: put each seat where it is measured
healthy, retry the residual transients, fail over on availability faults, and
keep the rare remainder owned.

## Non-negotiable contracts

Unchanged from the current system and not up for renegotiation here:

- `JudgeVerdict.unparsed` is a transport/protocol state, never a severity and
  never a `pass`.
- `collegium_verdict()` ignores an unparsed seat when the other parsed; only an
  all-unparsed round reads as unparsed
  (`weblate/trans/models/judge.py:499-512`).
- A parsed verdict is never retried because its severity is inconvenient.
- **A `200` rejected by the parser is never sent to another provider as a
  replacement opinion.** Shopping for a parseable answer is shopping for a
  verdict. A repeated parser-invalid `200` is a seat-health fault: it goes to
  the deferral queue, not to a second provider.
- A transport failure never moves a unit's state: `state_for_verdict()` returns
  `None` for unparsed (`weblate/trans/models/judge.py:398-399`), and the
  deferral machinery must not change that. Our proxy's health is not the
  translator's problem.
- A retry reuses the same source, target, glossary/context and seat profile. A
  changed target is stale, not a delayed request.
- Request logs contain no API key, prompt, target text, raw response, reasoning
  trace, or any unkeyed derivation of them.

`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` stays proposed and
gated: parallelism may not be used to compensate for anything here.

## Design decisions

### D1. Persist attempt evidence, not opaque log text

Add an immutable `JudgeRequestAttempt`. One row is one HTTP POST for one seat
and one logical batch, whether or not a response arrives. Producer calls link it
to the exact `JudgeRun`; direct `request_verdicts()` callers (the probes under
`analysis/probes/`) create no attempt rows and keep today's usage behaviour.

| field | purpose |
|---|---|
| `judge_run` | exact producer launch |
| `logical_run_id` | current model-call round ID (`JudgeVerdict.run_id`) |
| `round_kind` | `initial`, `repair`, `unparsed-retry`, or `deferred-pass` |
| `seat`, `batch_index`, `attempt_index`, `unit_count` | position and bounded retry history |
| `batch_digest` | keyed digest of the exact serialized batch: evidence for the peer question in D3, never a cache predicate |
| `provider`, `model`, `profile_fingerprint`, `request_fingerprint` | request provenance without a credential |
| `endpoint_role` | `primary` or `fallback` (D5) |
| `status_code`, `finish_reason`, `response_id`, `retry_after_seconds` | safe peer outcome metadata |
| `failure_kind`, `parsed`, `will_retry` | normalized result and decision |
| `elapsed_ms`, `response_bytes` | latency and size diagnosis |
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
name its own failures. An empty body is distinct from invalid JSON, and
`finish_reason=length` rejects an otherwise parseable body. `finish_reason` is
observable only when the outer body parses; a mid-stream break stays
`transport`, which the broad catch at `weblate/trans/judge.py:550-551` already
produces. `Retry-After` is read from response headers, which `httpx2` keeps
readable after the stream context exits, parsed only as positive delta-seconds
and capped at 60 s; anything absent, invalid, date-form or over cap falls back
to the normal bounded delay.

`on_batch` keeps its signature and its "exactly once per completed batch"
contract: the progress tick (`weblate/trans/autotranslate.py:757-773`) and the
parallelism plan's `_BatchReady` barrier both depend on it. The attempt observer
is a second, private callback; the terminal attempt reaches the verdict writer
through state shared between the two closures in `weblate/trans/judge_loop.py`,
keyed `(seat, batch_index)`. Widening the `OnBatch` alias is not an option.

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
`round_kind`. Old rows stay valid historical evidence with blank provenance.
`LLMUsageLog` gains a nullable one-to-one link to the attempt, so billed tokens
join to one exact request; a reset has an attempt row and no usage row, which is
the required distinction. Provider response bodies are never stored to make that
join possible.

`JudgeRequestAttempt` is append-only: a 2000-string run at width five is 800
rows before retries. Like `JudgeVerdict` and `LLMUsageLog` it is retained
indefinitely; no pruning task exists for either and this plan adds none. The
documentation says so rather than leaving it to be discovered from table size.

### D2. Snapshot only safe effective configuration

`JudgeRun` gains `configuration`, a JSON snapshot written at creation: per-seat
provider identity, model, profile fingerprint and endpoint role; primary and
fallback endpoint fingerprints; `JUDGE_BATCH_SIZE`, deadline and every retry and
deferral budget; whether the unparsed-retry round is enabled; the prompt/schema
profile version. Never the key, headers, a complete URL, prompt text,
translation text or a raw payload.

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
`max_tokens` and a stable fingerprint. `run_judge_batch()` resolves one profile
per configured slot and passes it, including the seat number, into
`request_verdicts()`.

| setting | default | behaviour |
|---|---|---|
| `JUDGE_REASONING_EFFORT_SEAT_1` | `inherit` | seat 1 profile override |
| `JUDGE_REASONING_EFFORT_SEAT_2` | `inherit` | seat 2 profile override |
| `JUDGE_MAX_TOKENS_SEAT_1` | `0` | omitted from the request when zero |
| `JUDGE_MAX_TOKENS_SEAT_2` | `0` | omitted from the request when zero |

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

`max_tokens` is 1..8,192 when present; zero is the only omission value, and
booleans or malformed values are rejected. No default cap is introduced. The cap
is a footgun on the seat that motivates it: while reasoning is not actually
suppressed the cap is consumed by reasoning and the reply returns
`finish_reason=length`, which D4 makes terminal - enabling it can *raise* the
unparsed rate. 8,192 guards a typo; it is not a measured width-five reply size,
and whether a gateway honours `max_tokens` or `max_completion_tokens` for a
reasoning model is a measurement question.

`_cached_verdict()` must require matching profile and request fingerprints per
slot, not only model strings. The request fingerprint covers provider identity,
endpoint fingerprint, model, resolved reasoning fields, `max_tokens`, the
configured batch size, the prompt/schema version and an effective
project-context digest. That last item closes a live correctness hole:
`compute_context_hash()` covers source, note and glossary only
(`weblate/trans/models/judge.py:71-90`) while `judge_project_context()`
(`weblate/trans/judge_loop.py:187-205`) goes into the prompt, so editing a
project's judge persona currently leaves stale verdicts reusable.

Every fingerprint this plan stores - profile, request, endpoint, batch - uses one
keyed construction, HMAC-SHA-256 with the deployment's `SECRET_KEY` and a
distinct domain label per kind. The serialized batch contains source and target
text, so a plain hash in an append-only, report-displayable table would be a
confirmation oracle for guessed translations. Rotating `SECRET_KEY` invalidates
every stored fingerprint and therefore the cache: a documented consequence of a
key rotation, not a silent one. The unkeyed staleness hashes that already exist
(`JudgeVerdict.target_hash`, `target_storage_hash`,
`weblate/trans/models/judge.py:51-68`) are a pre-existing exposure of the same
shape; this plan does not widen that class and does not migrate it.

Batch shape is part of identity because it is part of the request:
`request_verdicts()` serializes the whole batch's `segments` into one user
message (`weblate/trans/judge.py:605-642`). What enters identity is the
*configured* width - `JUDGE_BATCH_SIZE`, or one for a D6 round - because that is
what a deployment chooses. The realized batch (`unit_count`, `batch_digest`) is
recorded as evidence only.

Peer *content* is an open question, neither asserted away nor settled here.
Equal-width batches carry different neighbours, so width does not establish
request equality. A realized-membership digest cannot be the predicate: the
cache decision runs per unit before any batch exists
(`weblate/trans/judge_loop.py:507-514`) and batches are cut from whatever the
cache did not skip (`weblate/trans/judge.py:600-603`), so membership would
depend on the cache result that depends on membership. The predicate therefore
compares a stored row's production conditions against current configuration -
never against a hypothetical batch. Reuse across different realized peers stays
an explicit bounded assumption, and it is not new: today's predicate matches
model names alone (`weblate/trans/judge_loop.py:268-273`), already reusing
across peers, widths, endpoints, thinking modes and project contexts. The probe
protocol gains an arm that varies only neighbours; until it is published, no
report or measurement may claim peer-crossing equivalence. If that arm shows
severity moving beyond the fixed-peer noise floor, the fallback is a stored-row
property - require a realized `unit_count` of one for reuse - whose cost is that
cross-run reuse effectively ends above width one. That decision belongs to the
measurement and is not in the tasks below.

Legacy verdicts with no fingerprint are not reused once this lands. The cost is
explicit and one-time: the first run over an existing scope re-judges it. A
legacy row cannot prove which endpoint, thinking mode or project context
produced it - the French measurement had to reconstruct exactly that - so
backfilling would assume what that measurement disproved.

### D4. Retries are classified, bounded, and same-seat

Keep `JUDGE_TRANSPORT_RETRIES`. Add two disabled-by-default budgets:

| setting | default | eligible terminal failure |
|---|---:|---|
| `JUDGE_PROTOCOL_RETRIES` | 0 | `empty-response`, `invalid-json`, `invalid-envelope`, `segment-count`, `invalid-segment` |
| `JUDGE_TRANSIENT_HTTP_RETRIES` | 0 | `http-server` |

The ladder inside one seat and one batch:

1. Transport failures use the transport budget with capped exponential backoff
   and jitter.
2. `429` keeps its one bounded retry, using `Retry-After` when present.
3. A `5xx` may use `JUDGE_TRANSIENT_HTTP_RETRIES`.
4. A protocol failure may use `JUDGE_PROTOCOL_RETRIES`.
5. A deadline, over-size response, `401`, `403`, `unknown` or
   `finish_reason=length` gets no same-endpoint retry.

Two of those change current behaviour deliberately. `403` is retried once today
(`weblate/trans/judge.py:699-702`); it is an entitlement or configuration
signal, a repeat buys a second billed refusal, and D5 wants it as a failover
trigger. `unknown` is terminal because a closed vocabulary with an escape bucket
that spends a budget automatically would let any future unclassified failure
double the bill in silence.

Every retry reuses the same profile and batch data, gets its own attempt row and
consumes a documented budget. Defaults are zero because multiplying paid
requests on existing deployments without measurement is an unsafe rollout. The
retry helper takes an injected sleeper/jitter source in tests.

### D5. Seat placement is the precondition; fallback is the machine resolver

This is the decision the previous draft pushed out of scope, and without it
nothing else reaches zero.

**Placement precondition.** A seat may be configured on an endpoint only where
that seat's model is measured healthy on that endpoint. The measured fact is
that DeepSeek on LiteLLM cannot suppress reasoning (37/37 rows) while the proxy
resets on reasoning (8/8 versus 0/8), and that both seat models on OpenRouter
measured 0 unparsed over 124 and 376 records. A configuration that violates the
precondition is a misconfiguration, and `validate_judge_configuration()` cannot
detect it - so the preflight command of Task 8 exists to answer it before a run,
and the run report shows the per-seat parse rate that would reveal a drift.

**Fallback.** This plan takes over the failover settings
(`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:53-66`):
`JUDGE_FALLBACK_BASE_URL` (empty disables everything), its own
`JUDGE_FALLBACK_API_KEY`, `JUDGE_FALLBACK_MODEL_SEAT_1`,
`JUDGE_FALLBACK_MODEL_SEAT_2`, `JUDGE_FALLBACK_REASONING_EFFORT`. Fallback is
per seat and per batch: a failed primary seat 1 resends only seat 1, and seat 2
is untouched.

Order, once the D4 budgets are spent:

1. For a terminal transport failure, exhausted `429`, `401`, `403`, or exhausted
   `5xx`, call the fallback exactly once for that seat and logical batch.
2. Record primary and fallback attempts separately, each with its own provider,
   profile fingerprint and `endpoint_role`.
3. A `200` parser failure, deadline, oversized reply, `unknown` and
   `finish_reason=length` never reach the fallback. They are final for the
   round and feed D6, then D7.

`get_judge_chat_completions_url()` re-reads `settings.JUDGE_BASE_URL` on every
POST (`weblate/trans/judge.py:121-123`, called from `:535-547`), and
`request_verdicts()` takes only `model` (`:574-583`). Both must become explicit
per-request inputs carried by the resolved profile; an endpoint may not be an
ambient setting read deep inside a POST once two endpoints exist.

A fallback answer is a full opinion of that seat, not a lesser one. It is
labelled by `endpoint_role` so a report can separate them, and the promotion
gate reads terminal outcomes, not "primary-only" outcomes.

This also fixes the live bug the failover plan identified in its D6. Today the
reasoning payload branch and its validation both read the *primary* provider
(`weblate/trans/judge.py:643-654`, `:169-174`), so the moment a second endpoint
exists a fallback POST would carry the primary's reasoning decision - a LiteLLM
vendor toggle sent to OpenRouter, or an OpenRouter effort value sent to a proxy
that rejects it. Because provider, endpoint and reasoning fields all live in the
resolved profile (D3), each request carries its own, and
`JUDGE_FALLBACK_REASONING_EFFORT` is validated against the fallback provider.

### D6. One width-one isolation round

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

`JUDGE_MAX_UNPARSED_RETRY_ROUNDS` defaults to 0.

Selection reads the current round only. A unit qualifies when every seat row of
that round is unparsed; through `collegium_verdict()` that also matches a round
whose second seat row is missing (`weblate/trans/models/judge.py:507-511`),
which is intended. A changed unit is excluded because `current_round()` returns
empty for it (`:423-451`) - that is how a stale unit is told apart from a
transport-dead one, and it becomes `stale-conflict`
(`JudgeRunUnit.Outcome.STALE_CONFLICT` already exists, `:161`).

Within a round: refresh and hash-check the selection; rejudge under a new
`JudgeVerdict.run_id` tagged `round_kind=unparsed-retry`, keeping the unit's
existing `attempt` so that field keeps its single meaning while the fresh
`run_id` satisfies the `(unit, run_id, attempt, seat)` constraint (`:333-336`);
force width one so one malformed batch reply cannot make an unrelated peer
unparsed; feed a parsed result through the normal collegium, and a repairable
one back into the repair loop with the already-spent `attempt`, so the existing
`attempt > attempts` bound terminates it and a retried unit gets no extra repair
attempt. A re-judge triggered by such a repair is an ordinary `repair` round; if
it comes back double-unparsed it may be selected by the **next** retry round,
never inside the current one. No recursion, no third loop.

Extra paid requests of the phase are bounded by:

```text
double-unparsed units × rounds × two seats
  × (initial + transport budget + 429 retry + transient-HTTP budget
     + protocol budget + one fallback)
```

Repair rounds are not an extra factor: they sit inside the existing
`(1 + JUDGE_MAX_REPAIR_ATTEMPTS)` ceiling the phase re-enters.

Progress must not use that ceiling as a denominator. Ticks happen once per
completed batch (`weblate/trans/autotranslate.py:757-773`) against
`progress_steps = batches × seats × (JUDGE_MAX_REPAIR_ATTEMPTS + 1)`
(`:701-705`), and retries inside a batch never tick. The double-unparsed set is
unknown until the initial rounds finish, so it cannot enter the pre-run preview;
`progress_steps` is extended when the phase begins, by
`selected × rounds × seats`, and `_judge_phase()` gains an explicit
`unparsed retry` phase. `judge_request_upper_bound()`
(`weblate/trans/judge.py:49-64`) and `JudgeScopePreview` keep their formulas, so
the estimate at `weblate/trans/views/basic.py:901-909` does not change; both
gain one sentence saying it excludes retry budgets, fallback and deferred
passes.

### D7. The deferral queue, and what actually terminates it

Add `JudgeDeferral`, following the durable-queue precedent of
`PendingUnitChange` (`weblate/trans/models/pending.py:416-449`, consumed by
`commit_pending`, `weblate/trans/tasks.py:229-247`):

| field | purpose |
|---|---|
| `unit` (FK, `SET_NULL`), `unit_id_snapshot` | subject, surviving deletion |
| `translation_id`, `component_id`, `project_id` | scope for selection and permissions |
| `target_hash`, `context_hash` | what it was deferred about |
| `origin_run` (FK), `origin_actor` (FK, `SET_NULL`) | who asked, so the row has an owner |
| `first_deferred`, `last_attempted` | age and pacing |
| `passes_done` | bounded machine ladder |
| `last_failure_kind`, `last_endpoint_role` | why it is still here |
| `state` | `open`, or one of the terminal reasons below |
| `closed_reason`, `closed_by` (FK, `SET_NULL`), `closed_at`, `closed_note` | the disposition |

Uniqueness is `(unit_id_snapshot, target_hash, context_hash)` among `open` rows,
so re-deferring is idempotent and a re-run never duplicates work.

**A row is created** when a producer run finishes a unit with no parsed opinion
after the full D4-D6 ladder. That replaces writing
`JudgeRunUnit.Outcome.UNPARSED`: the run unit's outcome becomes `deferred`, and
the unparsed evidence stays where it belongs, on the `JudgeVerdict` and attempt
rows.

**A row closes only on something terminal:**

| `closed_reason` | trigger | who |
|---|---|---|
| `resolved` | a later pass produced a parsed collegium verdict for the same hashes | machine |
| `stale` | target or context hash changed: the text it was about no longer exists | machine |
| `unit-gone` | the unit was deleted | machine |
| `out-of-scope` | the component or project no longer has the judge configured | machine |
| `accepted-as-is` | a human decided the string ships unjudged | human |
| `escalated` | a human routed it for rework | human |
| `unjudgeable` | a human recorded that the judge cannot handle this string | human |

**Exhausting `JUDGE_DEFERRAL_MAX_PASSES` does not close the row.** It only stops
the machine from spending more money on it: the row stays `open`, keeps its
check (D8), and waits for a human disposition. This is the point of the whole
design - a bounded machine budget must not become a silent bucket, and a
non-blocking check on its own is visibility, not a resolver. The human
dispositions reuse the vocabulary of the existing resolution workflow
(`JudgeVerdict.Resolution`, `resolve_verdict()`), and the same permission that
resolves a verdict records a deferral disposition.

**Consumer.** A periodic task `judge_deferred_units`, registered like
`cleanup_loc_kit_drafts` (`weblate/trans/tasks.py:1348-1363`, registered at
`:1523-1525` through `setup_periodic_tasks`, `:1493-1496`), groups open rows
whose `passes_done` is below the budget and whose `last_attempted` is older than
`JUDGE_DEFERRAL_PASS_INTERVAL`, then launches an ordinary judge run per
translation through the existing task: `auto_translate` already accepts
`unit_ids: list[int] | None` and threads it end to end
(`weblate/trans/tasks.py:975-996`, `:1021-1031`;
`weblate/trans/autotranslate.py:207-224`, `:276-310`, `:321-328`,
`:1300-1320`). So a deferred pass is a first-class `JudgeRun` with its own
report and its own `JudgeRunUnit` rows - the unique constraint is
`(run, unit_id_snapshot)` (`weblate/trans/models/judge.py:235-238`), so a later
run never collides - and there is still **no Celery autoretry of a judge task**,
which would create an ambiguous duplicate run. `round_kind=deferred-pass` marks
its attempts.

New settings, all conservative:

| setting | default | meaning |
|---|---:|---|
| `JUDGE_DEFERRAL_ENABLED` | `False` | off: units keep today's `unparsed` outcome |
| `JUDGE_DEFERRAL_MAX_PASSES` | `3` | machine passes before it waits for a human |
| `JUDGE_DEFERRAL_PASS_INTERVAL` | `1800` | seconds between passes for one row |
| `JUDGE_DEFERRAL_MAX_UNITS_PER_PASS` | `200` | cost ceiling per periodic tick |

With `JUDGE_DEFERRAL_ENABLED=False` nothing changes anywhere: same outcomes,
same reports, same spend. The contract turns on with one setting, after the
gate.

### D8. Visibility that cannot be missed, without touching unit state

While a deferral is open the unit carries `judge-unavailable`, a new advisory
check beside the existing projections (`judge-flag`, `judge-reject`,
`judge-note`, `weblate/checks/judge.py:108-132`). It follows their rules
exactly: not enforceable, not dismissible, cleared automatically when a verdict
arrives, excluded from the prompt's `failing_checks` like every other `judge-*`
check (`weblate/trans/judge_loop.py:82-84`).

It never changes unit state. A proxy reset must not mark a translator's string
fuzzy, and `state_for_verdict()` already returns `None` for unparsed
(`weblate/trans/models/judge.py:398-399`).

The check makes the queue navigable through the ordinary filters, which is what
gives a human the work list. It is *not* the resolver - the open `JudgeDeferral`
row with its owner is.

### D9. Reports state the contract, not a rate

The `JudgeRun` report gains: the safe configuration snapshot; outcomes before
and after the retry phase; per-seat counts by `failure_kind`, attempt count and
elapsed-time percentile, split by `endpoint_role`; responses reporting reasoning
tokens; **per-seat parse rate and the count of rounds decided by a single parsed
seat**; deferrals created, resolved by a later pass, and still open; and a
scope-authorized, paginated technical attempt table with no prompt or raw
response, reusing the existing `Paginator` pattern
(`weblate/trans/views/judge.py:160-170`,
`weblate/templates/judge-run.html:123-125`) and the existing scope/actor recheck
(`weblate/trans/views/judge.py:91-109`, `:149-160`).

The single-seat count is not decoration. At the measured rates the chance that
exactly one seat parses is `0.805 × 0.659 + 0.195 × 0.341 ≈ 0.60`: most judged
strings in that run were decided by one seat, and `collegium_verdict()` reports
such a round as an ordinary verdict with no marker
(`weblate/trans/models/judge.py:499-512`). A report counting only unjudged units
shows a healthy run while the two-seat collegium has quietly collapsed into a
one-seat judge.

The completion message replaces today's "N strings were left unjudged"
(`weblate/trans/autotranslate.py:839-847`) with statements that carry an owner:
"12 strings deferred: 9 transport, 3 invalid JSON - they will be retried
automatically, 0 waiting for a decision", and, when open deferrals exceed a
quarter of the run, "the judge is effectively unavailable for this scope". It
never claims a model made a quality decision.

### D10. Stability of the run itself

Three failure modes are not about requests and must not be left implicit.

1. **A killed worker.** A run left `RUNNING` by a terminated process is not
   healed by this plan's ladder; nothing in-process can. A periodic reaper marks
   runs `RUNNING` past `JUDGE_RUN_STALE_AFTER` (default 6 h) as `FAILED` with a
   stated reason, so the report is never indefinitely ambiguous. Their units
   simply were not recorded and the next ordinary run re-selects them by content
   - judge scope is a search query, not a run-derived set
   (`weblate/trans/autotranslate.py:674-699`).
2. **An exception during the retry phase.** Handled by the existing
   `BatchAutoTranslate` finalization, which marks that `JudgeRun` `FAILED`
   (`weblate/trans/autotranslate.py:1265-1273`, `:1429-1438`). Deferrals already
   written stay open and are picked up by the periodic consumer, which is the
   point of persisting them.
3. **Queue growth.** Open deferrals are bounded by scope size and visible in
   both the report and the check filter; the periodic consumer is bounded by
   `JUDGE_DEFERRAL_MAX_UNITS_PER_PASS`. A permanently broken endpoint therefore
   produces a bounded, named backlog with an owner - not an unbounded retry
   storm, and not a silent hole.

## Implementation tasks

Three waves. Wave A is worth landing whatever the probes say; wave B is what the
zero contract needs; wave C is the contract itself.

**Wave A - evidence:** Tasks 1, 2, 3.
**Wave B - resolvers:** Tasks 4, 5, 6.
**Wave C - contract:** Tasks 7, 8.

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
   until Tasks 4-6 opt in.
5. Fixtures for every closed failure type: reset, deadline, oversized body,
   empty body, invalid JSON, wrong envelope, missing and duplicate segment,
   invalid segment, `length` finish reason, `401`, `403`, `429`, `5xx`, another
   `4xx`, and an unrecognized shape as `unknown`.

Tests assert category and metadata, not only `result.unparsed`. No fixture
carries a production token or a user translation.

Commit: `feat(judge): classify judge request failures`

### Task 2: Attempt provenance and configuration snapshot

**Files:** `weblate/trans/models/judge.py`,
`weblate/trans/models/llm_usage.py`, a migration, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `weblate/trans/autotranslate.py`, and the judge
client/loop/autotranslate/migration tests.

1. Add `JudgeRequestAttempt`, the nullable `JudgeVerdict` provenance fields, and
   `JudgeRun.configuration` plus its persistence-loss counter - `JudgeRun` has
   neither field today (`weblate/trans/models/judge.py:108-132`).
   `judge_provider` lands here; the failover plan is marked superseded in the
   same commit so no second migration claims it.
2. Introduce the `JudgeRequestProfile` resolver for the existing global settings
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
9. Schema defaults only. Do not fabricate provider or run links for historical
   rows.

Tests: a parseable reply, a parser-rejected payload and a reset each create the
expected attempt; a billed parser failure links one usage row; an unbilled reset
has an attempt and no usage row; one producer run links its fresh verdicts to
itself rather than a timestamp window; snapshots contain no key, header, prompt,
target or raw response; an accounting error changes neither verdict nor retry;
a transaction failure increments the counter with no orphaned FK; `on_batch`
still fires exactly once per completed batch so the progress-tick count is
unchanged; migration defaults keep old rows renderable.

Commit: `feat(judge): record request attempt diagnostics`

### Task 3: Per-seat profiles and keyed cache identity

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`, a migration,
`docs/admin/config.rst`, `docs/admin/install/docker.rst`, and the judge
client/loop tests.

1. Add the four D3 settings to every surface, and repair the existing drift in
   the same pass: `weblate/trans/models/_conf.py:102-112` omits
   `JUDGE_REQUEST_DEADLINE`, `JUDGE_TRANSPORT_RETRIES` and
   `JUDGE_REASONING_EFFORT`; `deploy/environment.example:116-129` omits the
   deadline and transport retries and only comments the reasoning setting;
   `docs/admin/install/docker.rst` documents no environment variable for either.
   Drop the stale claim at `deploy/environment.example:128` that reasoning "must
   stay empty" on LiteLLM - `none` is admitted.
2. Extend the resolver and its validation before the first paid POST; it is the
   only place allowed to decide whether a vendor thinking field, a generic
   reasoning field, or no field is sent, and it hoists the LiteLLM allowlist
   check into configuration validation.
3. Build the payload from the resolved profile, adding `max_tokens` only when
   non-zero.
4. Compute every fingerprint through one keyed helper (HMAC-SHA-256,
   `SECRET_KEY`, one domain label per kind), never a bare `hashlib` call over
   request content. Update `_cached_verdict()` to require matching
   slot/profile/request fingerprints including the project-context digest and
   the configured batch size, comparing stored production conditions against
   current configuration only.
5. Record the realized `unit_count` and keyed batch digest on fresh verdicts and
   attempts as evidence; nothing in these tasks reads them as a predicate.

Tests: all-inherit reproduces today's OpenRouter and LiteLLM payloads; Qwen
`none` emits `enable_thinking: false`; DeepSeek `none` emits only its admitted
vendor control; `default` emits nothing; per-seat settings express DeepSeek
default plus Qwen disabled; `max_tokens` bounds, zero omission, booleans and
malformed values behave predictably; a different profile, provider, project
context or configured batch size misses cache; an inadmissible seat/model pair
fails before any POST; matching profiles retain reuse; no stored fingerprint
equals the unkeyed hash of its input and two domain labels differ; blank-profile
evidence never satisfies the predicate.

No live model setting changes here. Neither the DeepSeek wire format nor peer
sensitivity is provable by a unit test against an HTTP fake, and no test is
written to suggest otherwise.

Commit: `feat(judge): support per-seat request profiles`

### Task 4: Bounded classified retries

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/tests/test_judge_client.py`.

1. Add `JUDGE_PROTOCOL_RETRIES` and `JUDGE_TRANSIENT_HTTP_RETRIES`, both zero.
2. Replace the ad-hoc loop with one classifier-driven decision, preserving
   transport and `429` semantics, making `403` terminal (removing the retry at
   `weblate/trans/judge.py:699-702`) and keeping `unknown` terminal.
3. Record `will_retry`, retry ordinal and elapsed time on every attempt before
   sleeping.
4. Capped exponential backoff with jitter from an injectable source.
5. A parsed response ends the loop immediately; a permanent failure ends it
   without an extra paid POST.

Tests: exact call counts for every eligible and ineligible category;
`max_tokens` truncation, oversized and deadline responses, `403` and `unknown`
never retry; a parser failure recovers only with a positive protocol budget;
retry events share logical batch, seat and profile; a successful retry writes
only final verdict rows but keeps all attempt evidence; exhausting every budget
yields `unparsed`, never an exception and never a pass.

Commit: `feat(judge): retry classified transient failures`

### Task 5: Explicit endpoints and per-seat fallback

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `docs/admin/config.rst`,
`docs/admin/install/docker.rst`, `docs/security/threat-model.rst`, and the judge
client/loop tests.

1. Make the endpoint an explicit per-request input carried by the profile.
   `get_judge_chat_completions_url()` re-reads the setting on every POST today
   (`weblate/trans/judge.py:121-123` from `:535-547`); an ambient endpoint is
   not admissible once two exist.
2. Add the five `JUDGE_FALLBACK_*` settings, empty base URL disabling the whole
   path, with their own key and per-seat models, validated before any request.
3. Trigger the fallback exactly once per seat and logical batch, only on D5's
   availability list. Never on a parser-invalid `200`, deadline, oversized
   reply, `unknown` or `finish_reason=length`.
4. Record `endpoint_role` on attempts and fresh verdicts, and add the endpoint
   fingerprints to the D2 snapshot.
5. Update `docs/security/threat-model.rst`: a second outbound endpoint and a
   second credential are a change to the outbound integration surface.

Tests: no fallback without configuration; one fallback attempt per seat and
batch and no more; a parser-invalid `200` never reaches the fallback; a fallback
answer is a normal opinion of that seat and enters the collegium unchanged;
primary and fallback attempts are separate rows with different providers and
roles; a fallback verdict does not satisfy the cache predicate of a
primary-configured run; the fallback key never appears in a snapshot, log or
exception.

Commit: `feat(judge): fail over per seat on availability faults`

### Task 6: The width-one isolation round

**Files:** `weblate/trans/defaults.py`, settings surfaces,
`weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/models/judge.py`, `weblate/trans/autotranslate.py`, a migration,
and the judge loop/autotranslate tests.

1. Add `JUDGE_MAX_UNPARSED_RETRY_ROUNDS`, default zero.
2. Add `round_kind` and the retry-round count to producer-visible persistence.
   Do not overload `JudgeVerdict.attempt`.
3. Implement the D6 phase as the outer bounded loop, preserving `attempt`, with
   no recursion.
4. Refresh before the delayed call; a target/context race is `stale-conflict`.
5. Feed parsed results through the existing collegium and repair path, carrying
   the already-spent repair budget.
6. Extend progress at phase start; add the `unparsed retry` phase to
   `_judge_phase()`; add the exclusion sentence to
   `judge_request_upper_bound()` and `JudgeScopePreview`.

Tests: a one-seat loss does not enter the phase; a double-unparsed unit is
retried once per configured round and no more, at width one; retry success
replaces the run outcome without deleting prior unparsed evidence; a retried
unit that already spent `JUDGE_MAX_REPAIR_ATTEMPTS` gets no extra attempt; a
repair-triggered round that comes back double-unparsed waits for the next retry
round; a changed unit is not sent; cached, cap-skipped, permission-skipped and
deleted units are never retried; no duplicate `(run, unit)`, verdict or attempt
row; progress never exceeds its total and the pre-run estimate is unchanged; an
exception during the phase leaves a correctly `FAILED` run.

Commit: `feat(judge): isolate double-unparsed units at width one`

### Task 7: The deferral queue and its dispositions

**Files:** `weblate/trans/models/judge.py`, a migration,
`weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`,
`weblate/checks/judge.py`, `weblate/trans/views/judge.py`,
`weblate/templates/judge-run.html`, `weblate/trans/defaults.py`, settings
surfaces, and the judge model/loop/autotranslate/view/check tests.

1. Add `JudgeDeferral` with the D7 fields, the open-row uniqueness constraint,
   and indexes on `(state, last_attempted)` and
   `(component_id, state)`.
2. Add `JudgeRunUnit.Outcome.DEFERRED` and write it instead of `UNPARSED` at
   both current sites (`weblate/trans/autotranslate.py:858-879`, `:887-895`)
   when `JUDGE_DEFERRAL_ENABLED`. With the flag off, behaviour is byte-identical
   to today.
3. Create or refresh a deferral for every unit finishing without a parsed
   opinion, idempotently by `(unit, target_hash, context_hash)`.
4. Close rows on the machine reasons: a parsed verdict for the same hashes, a
   hash change, a deleted unit, a scope that no longer has the judge.
   Exhausting `JUDGE_DEFERRAL_MAX_PASSES` closes nothing.
5. Add `judge-unavailable` beside the existing judge checks, projected from an
   open deferral, not enforceable, not dismissible, excluded from
   `failing_checks`, cleared when a verdict lands.
6. Add the disposition entry point, permission-checked like `resolve_verdict()`,
   recording `accepted-as-is`, `escalated` or `unjudgeable` with actor, time and
   note.
7. Add `judge_deferred_units`, registered through `setup_periodic_tasks`, which
   selects due open rows, respects `JUDGE_DEFERRAL_MAX_UNITS_PER_PASS`, groups
   by translation, and launches `auto_translate` with `mode="judge"` and
   explicit `unit_ids`, incrementing `passes_done` and `last_attempted`.
8. Add the D10 reaper for runs stuck in `RUNNING`.

Tests: the flag off changes nothing; a unit with no opinion produces exactly one
open deferral and a `deferred` run outcome; re-running does not duplicate the
row; a later pass with a verdict closes it `resolved` and clears the check; a
target edit closes it `stale`; a deleted unit closes it `unit-gone`; exhausting
the passes leaves it `open` with the check still on the unit; a human
disposition closes it and is recorded with actor and reason; an unauthorized
actor cannot dispose; the periodic task respects the interval and the per-pass
cap and creates a real `JudgeRun` per translation; the check is neither
enforceable nor dismissible and never changes unit state; the reaper fails only
genuinely stale runs.

Commit: `feat(judge): keep unjudged strings owned until resolved`

### Task 8: Report, preflight, and the contract test

**Files:** `weblate/trans/views/judge.py`,
`weblate/templates/judge-run.html`, `weblate/trans/autotranslate.py`,
`weblate/trans/management/commands/` (one new preflight command),
`docs/admin/config.rst`, `docs/admin/install/docker.rst`,
`docs/admin/checks.rst`, `docs/changes.rst`, and the judge view/report tests.

1. Add the D9 aggregates, the paginated permission-checked attempt table, and
   the deferral counters. No global search filter, no payload text.
2. Replace the completion warning with the D9 wording, including the
   scope-unavailable statement.
3. Add the preflight command: it uses the production parser, profile resolver
   and endpoints, judges a caller-supplied frozen slice, records no
   translations, takes an explicit model and profile and never an API key from
   the command line, and writes a dated measurement artifact. This is what
   answers the D5 placement question before a run.
4. Add the contract test: for a synthetic run in which every request fails, no
   selected unit ends with an outcome outside
   `{deferred, skipped, stale-conflict}`, every deferred unit has an open row
   with an owner and a `judge-unavailable` check, and no unit's state changed.
5. Document the retention rule: attempts, verdicts, usage and closed deferrals
   are retained; pruning is out of scope.
6. Changelog entry for the user-visible parts: the new check, the deferral
   behaviour and the fallback endpoint.
7. Run the focused test files, migration checks, `uv run prek run --all-files`,
   and the narrowest practical Django report tests. Do not restart `dev-docker`
   for this plan's tests.

Commit: `feat(judge): report and prove the completion contract`

## Live measurement and rollout gates

Live probes and deployment are separate approvals.

Before enabling any non-zero retry, fallback or deferral setting, run read-only
paid probes:

1. **Placement.** For each seat model, measure the unparsed rate on each
   configured endpoint at production width, prompt, schema and parser, three
   time-separated repeats. This is the arm that decides where a seat belongs and
   it must be published before any deployment change.
2. **Profile.** Hold Qwen at its known disabled-thinking profile; compare
   DeepSeek's default profile, any confirmed vendor-specific disabled profile,
   and one bounded `max_tokens` candidate. One variable per arm.
3. **Fallback.** Verify the fallback endpoint answers the same prompt and schema
   for both seat models, and that a forced primary availability fault produces
   exactly one fallback attempt.
4. **Peers.** One arm that holds profile and width fixed and varies only the
   neighbours of a frozen set of strings, against repeats with neighbours held
   fixed. Width and membership are otherwise recorded and held fixed inside
   every arm.
5. Publish everything in `docs/llm-first/measurements/` before changing a
   deployed profile.

**Structural gate** (must pass before `JUDGE_DEFERRAL_ENABLED` is turned on
anywhere, and provable by tests rather than by a run):

- no selected unit can end a run with an unowned absence of verdict: the Task 8
  contract test passes with a 100%-failing endpoint;
- `JudgeRunUnit.Outcome.UNPARSED` is no longer written by any code path;
- exhausting the machine budget never closes a deferral;
- no deferral path changes a unit's state or dismisses a check;
- every open deferral has an owner and a visible check;
- zero persistence losses, and every attempt and usage row joins its `JudgeRun`;
- no retry for a parsed verdict, deadline, oversized reply, `401`, `403`,
  `unknown` or `finish_reason=length`; no fallback on a parser-invalid `200`;
- retry and fallback call counts inside the D6 arithmetic bound;
- no credential or translation text in snapshots or attempt rows, and no stored
  fingerprint is an unkeyed hash of its input.

**Empirical gate** (decides whether the configuration is good enough to run
unattended):

- across at least three canary repeats of at least 100 units per seat, zero
  units reach the human resolver - every deferral closes `resolved` or `stale`
  within the configured passes;
- per-seat terminal parse rate at or above 99% on the endpoint each seat is
  placed on, which the placement probe must already have shown;
- the single-seat share is reported and does not silently exceed the share the
  placement probe predicted;
- the deferral queue is empty at the end of each repeat;
- a report reader can explain every deferral and every single-seat decision from
  stored metadata without worker-log access.

If the empirical gate fails, the structural one still holds - nothing is lost or
silent - and the answer is placement or model choice, not wider retries. Model
choice stays with
`docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md`, whose
admission gate is "Unparsed rate <= 5%" in every repeat (`:207-210`); a seat at
the measured 80.5% cannot be rescued by same-endpoint retries at all, since
`0.805 ** n <= 0.01` needs `n >= 21` paid attempts.

Only after both gates pass may
`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` receive a live
serial-versus-parallel canary approval, holding profiles, budgets and endpoints
fixed and comparing terminal outcomes.

## Out of scope

- Choosing the permanent seat pair or replacing a model: the A/B plan owns it.
- Prompt, rubric, schema, severity or repair-policy changes.
- A circuit breaker that intentionally degrades a two-seat judge to one seat.
  The single-seat count is now reported precisely so that decision can be made
  on evidence later.
- Parallel requests within or across seats.
- Recording raw prompts, completions or reasoning traces.
- Pruning attempts, verdicts, usage rows or closed deferrals.
- Healing a run whose worker was killed beyond marking it `FAILED`.
- Production deployment, configuration changes, or paid probes without their
  explicit approvals.

## Expected files

- `weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
  `weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`
- `weblate/trans/models/judge.py`, `weblate/trans/models/llm_usage.py`
- `weblate/checks/judge.py`
- new migrations after `weblate/trans/migrations/0108_judge_verdict_instruction.py`
- `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
  `weblate/settings_example.py`, `deploy/environment.example`,
  `weblate/trans/models/_conf.py`
- `weblate/trans/views/judge.py`, `weblate/templates/judge-run.html`
- one new management command under `weblate/trans/management/commands/`
- `docs/admin/config.rst`, `docs/admin/install/docker.rst`,
  `docs/admin/checks.rst`, `docs/security/threat-model.rst`,
  `docs/changes.rst`
- existing judge client, loop, round, autotranslate, migration, check and view
  tests
