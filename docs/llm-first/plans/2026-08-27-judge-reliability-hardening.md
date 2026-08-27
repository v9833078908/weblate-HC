# Plan: harden judge request reliability

**Date:** 2026-08-27. **Status:** proposed, not started; revised 2026-08-27
after review. Implementation needs explicit approval. **Owner decision required
before live probes:** the probes spend provider credits but do not write
production translations.

Review revision, in one place so a reader of the earlier draft knows what moved:

- `403` is now terminal for the same endpoint in D4, D5 and Task 4; the earlier
  draft kept a retry in D4 and removed it in Task 4.
- `unknown` is terminal instead of protocol-retry eligible (D4).
- The promotion gate is split into a product gate and a seat-health finding,
  because a 1% per-seat terminal target is a model/profile statement that
  same-seat retries arithmetically cannot reach.
- D7 reports single-seat decisions: at the measured rates most judged strings
  were decided by one seat, and nothing surfaced that.
- D6 states the loop structure - a bounded phase around the existing repair
  loop, with `attempt` preserved and no recursion - instead of leaving it to the
  cost formula.
- Progress extends `progress_steps` when the retry phase starts, rather than
  deriving a denominator from the paid-request ceiling.
- The one-time cost of dropping legacy cache eligibility, and the `SECRET_KEY`
  rotation consequence, are stated rather than implied.
- Attempt and usage rows are written by one persistence function; the terminal
  attempt reaches the verdict writer without widening `OnBatch`.
- `judge_provider` ownership, retention, per-seat provider validation and the
  `max_tokens` truncation hazard are settled explicitly.
- Implementation runs in two waves: evidence and profiles first, retry policy
  only after the probe shows the terminal-failure mix.

## Goal

Make a two-seat judge explainable and bounded when an endpoint is flaky, while
preserving the rule that an unavailable seat is never an opinion. The completed
`col4/common/fr` LiteLLM run measured in
`docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md`
had 24 final double-unparsed units out of 82. The immediate product failure is
not only the rate: the system cannot distinguish a reset, a deadline, a
truncated response, a parser rejection, or an ignored thinking setting after
the fact.

The target behavior is:

1. Every judge request issued by a producer `JudgeRun` has an immutable,
   safe-to-display technical record tied to that run. Direct test and analysis
   calls remain possible, but do not silently create producer-run evidence.
2. Each configured seat has an explicit request profile, including reasoning
   behavior and an optional output cap, without changing old deployments by
   default.
3. A small, explicitly configured retry budget can retry transient
   transport/protocol failures without treating them as `pass` or silently
   changing provider.
4. A bounded retry round can rejudge only units where **both** seats were
   unparsable, keeping their evidence and final state in the same producer run.
5. LiteLLM seat parallelism remains blocked until a canary shows that
   concurrent requests do not make the reliability metrics worse.

This plan is reliability work. It does not select models, alter the rubric,
change the prompt, or claim that DeepSeek must remain in the pair.

## Basis and constraints

The measured French run had these characteristics:

| measure | result |
|---|---:|
| units selected | 82 |
| final double-unparsed units | 24 (29.3%) |
| DeepSeek raw unparsed | 66/82 (80.5%) |
| Qwen raw unparsed | 28/82 (34.1%) |
| DeepSeek usage rows with reasoning tokens | 37/37 |
| Qwen usage rows with reasoning tokens | 0/41 |

The evidence establishes that the current
`{"thinking": {"type": "disabled"}}` request profile does not suppress
reported DeepSeek reasoning on this proxy. It does not establish that reasoning
tokens cause every failure. The first task must retain this distinction in the
data model.

The existing contracts are non-negotiable:

- `JudgeVerdict.unparsed` remains a transport/protocol state, never a
  severity or a `pass`.
- `collegium_verdict()` ignores an unparsed seat when the other seat parsed;
  only an all-unparsed round is final `unparsed`.
- A parsed verdict is never retried merely because it has an inconvenient
  severity.
- A `200` rejected by the parser is never sent to another provider as though
  the replacement were a second opinion.
- A retry uses the same source, target, glossary/context and seat profile.
  A changed target is stale, not a candidate for a delayed request.
- Request logs contain no API key, prompt, target text, raw response or
  reasoning trace.

`docs/llm-first/plans/2026-08-26-judge-provider-failover.md` remains the plan
for endpoint failover. Its safety line, no fallback on a parser-invalid `200`,
continues to apply here. This plan adds the failure evidence and same-seat
retry semantics that its implementation can later reuse. It also supersedes
that plan's model-only cache identity: the endpoint, resolved request profile,
effective prompt context, and request shape must become cache identity before
either failover or per-seat profiles land.

`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` remains proposed,
but its live canary is gated by Task 6 below. Parallelism is not part of this
implementation.

## Design decisions

### D1. Persist attempt evidence, not opaque log text

Add an immutable `JudgeRequestAttempt` model. One row represents one HTTP POST
attempt for one seat and one logical batch, whether it receives a response or
not. Producer calls link it to the exact `JudgeRun`, not a reconstructed
timestamp. Direct `request_verdicts()` callers do not persist this model: they
retain the current best-effort usage behavior and write a separate measurement
artifact when they need evidence.

It contains only technical metadata:

| field | purpose |
|---|---|
| `judge_run` | exact producer launch |
| `logical_run_id` | current model-call round ID (`JudgeVerdict.run_id`) |
| `round_kind` | `initial`, `repair`, or `unparsed-retry` |
| `seat`, `batch_index`, `attempt_index`, `unit_count` | position and bounded retry history |
| `provider`, `model`, `profile_fingerprint`, `request_fingerprint` | request provenance without a credential |
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

The response reader and parser return typed failures instead of only `None`.
`_read_batch_response()` must distinguish a local deadline from an oversized
body before `_post_batch()` collapses either to the current bare response. An
empty body is distinct from invalid JSON, and a `finish_reason=length` rejects
an otherwise parseable body. `finish_reason` is observable only when the outer
body parses; a mid-stream connection break stays `transport`, which is what the
broad catch at `weblate/trans/judge.py:550-551` already produces, and needs no
separate class. `Retry-After` is read from the response headers, which `httpx2`
keeps readable after the stream context exits, parsed only as a positive
delta-seconds value, and capped at 60 seconds; an absent, invalid, date-form or
over-cap value falls back to the normal bounded delay.

Existing `JudgeResult` and the final `on_batch` callback remain
verdict-facing. An internal immutable attempt event carries diagnostic data to
persistence. For a producer run, `request_verdicts()` emits the event but does
not write Django models itself. The serial orchestration caller persists it on
its own thread; the future parallel-seat implementation forwards it through
its caller-owned queue before persistence. One persistence function serves both
callers: given the attempt event and an optional producer run, it writes the
attempt row only when a run is present and writes the usage row either way, so
judge accounting never forks into two implementations. It also fills
`LLMUsageLog.batch_size` and `LLMUsageLog.outcome`, which the judge writer
leaves unset today (`weblate/trans/models/llm_usage.py:58-66` against
`weblate/trans/judge.py:381-393`) and which exist for exactly this question. A
direct caller passes no run: it gets today's best-effort usage row and no
attempt row. For a producer run, the attempt and its usage row are created
together in one small transaction. If that transaction fails, it logs the loss
and continues judging, rather than converting a provider reply into a failed
judge task. The run report exposes a persistence-loss counter; a canary with
any such loss fails promotion.

`on_batch` keeps its current signature and its "exactly once per completed
batch" contract: the progress tick (`weblate/trans/autotranslate.py:757-773`)
and the seat-parallelism plan's `_BatchReady` barrier both depend on it. The
terminal attempt therefore reaches the verdict writer through state shared
between the two closures in `weblate/trans/judge_loop.py`, keyed
`(seat, batch_index)`; the parallel implementation carries the same key through
its queue. Widening the `OnBatch` alias is not an option.

`JudgeRequestAttempt` is append-only and grows with every POST: a 2000-string
run at width five is 800 rows before retries. Like `JudgeVerdict` and
`LLMUsageLog`, it is retained indefinitely; neither has a pruning task today
and this plan adds none. The documentation says so rather than leaving it to be
discovered from table size.

Add nullable provenance fields to new `JudgeVerdict` rows:

- `judge_run`, the producer run that issued this fresh evidence;
- `request_attempt`, the terminal `JudgeRequestAttempt` for the batch;
- `judge_provider`, `profile_fingerprint`, `request_fingerprint`; and
- `round_kind`.

`judge_provider` is the same field
`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:110-117` introduces.
Both plans are unstarted, so two migrations would collide: this plan owns the
field and its migration, and that plan is edited to consume it. The batch width
of a request is both attempt provenance (`unit_count`) and part of request
identity - see D3.

Old rows remain valid historical evidence with blank nullable provenance. A
new result from a retry supersedes the earlier unparsed round by timestamp as
today; no evidence is overwritten.

`LLMUsageLog` gains a nullable, one-to-one link to `JudgeRequestAttempt`.
Payload-bearing responses can then link their billed tokens to one exact
attempt. A reset has an attempt row and no usage row, which is the required
distinction. Do not store provider response bodies merely to make this join
possible.

### D2. Snapshot only safe effective configuration

Add `configuration` to `JudgeRun` as a JSON snapshot written at producer-run
creation. It records:

- provider identity, model and profile fingerprint for each seat;
- `JUDGE_BATCH_SIZE`, request deadline and configured retry budgets;
- whether an unparsed retry round is enabled; and
- the prompt/schema profile version.

It never records `JUDGE_API_KEY`, headers, a complete URL, prompt text,
translation text or raw provider payload. The endpoint fingerprint is an
HMAC-SHA-256 of the normalized endpoint using the deployment's Django
`SECRET_KEY`; it permits equality checks on one deployment without turning a
database row into a dictionary attack on internal endpoint URLs. Because that
fingerprint also enters cache identity (D3), rotating `SECRET_KEY` invalidates
every stored fingerprint and therefore every cached verdict. That is an
acceptable, documented consequence of a key rotation, not a silent one.

This makes a report reproducible and prevents the mistake made in the French
measurement, where settings had to be reconstructed after the run.

`JudgeRun` is created with `status=RUNNING` in
`BatchAutoTranslate._create_judge_run`
(`weblate/trans/autotranslate.py:1150-1179`), before `run_judge_batch()`
resolves the seats (`weblate/trans/judge_loop.py:490-496`). The profile
resolver therefore moves ahead of run creation and must be importable from
`autotranslate.py` without a cycle. A resolver failure then happens before any
row exists: it raises out of the producer path exactly as
`validate_judge_configuration()` does today and creates no `JudgeRun` at all,
never a `RUNNING` row nobody will finish.

### D3. Request profiles belong to seats, and profiles invalidate cache reuse

Introduce a small immutable `JudgeRequestProfile` value object resolved before
any POST. It carries the provider, model, effective reasoning behavior,
optional `max_tokens`, and a stable fingerprint. `run_judge_batch()` resolves
one profile per configured slot and passes the profile, including its seat
number, into `request_verdicts()`.

New settings:

| setting | default | behavior |
|---|---|---|
| `JUDGE_REASONING_EFFORT_SEAT_1` | `inherit` | seat 1 profile override |
| `JUDGE_REASONING_EFFORT_SEAT_2` | `inherit` | seat 2 profile override |
| `JUDGE_MAX_TOKENS_SEAT_1` | `0` | omitted from the request when zero |
| `JUDGE_MAX_TOKENS_SEAT_2` | `0` | omitted from the request when zero |

`inherit` is an explicit sentinel, not an empty value: it preserves
`JUDGE_REASONING_EFFORT` exactly, so with the shipped global default `""` both
`inherit` and `default` send no reasoning parameter and differ only in stated
intent. `default` means send no reasoning parameter whatever the global value
is. For LiteLLM, `none` uses the existing admitted model-specific vendor
mapping; for OpenRouter, existing allowed effort values remain valid.

A per-seat value passes the same provider gate the global value passes today:
`validate_request_settings()` rejects a LiteLLM endpoint for any effort outside
`{"", "none"}` (`weblate/trans/judge.py:169-174`), and
`_litellm_reasoning_disable_payload()` raises for a model outside its allowlist
(`weblate/trans/judge.py:143-150`). That second check fires only while a
request is being built; the resolver hoists it into configuration validation,
so an inadmissible seat/model pair fails at run creation instead of after the
other seat has already been billed.

`max_tokens` is an integer from 1 through 8,192 when present; zero is the only
omission value, and booleans or malformed environment values are rejected. No
default cap is introduced. The cap is a footgun on exactly the seat that
motivates it: while a model's reasoning is not actually suppressed, the cap is
consumed by reasoning and the reply returns `finish_reason=length`, which D4
makes terminal with no retry - so enabling it can raise the terminal unparsed
rate. The 8,192 bound guards against a typo; it is not a measured width-five
reply size, and whether a gateway honours `max_tokens` or
`max_completion_tokens` for a reasoning model is a measurement question, like
the DeepSeek disable-thinking wire format.

The current configuration continues to behave identically:

```text
JUDGE_REASONING_EFFORT_SEAT_1=inherit
JUDGE_REASONING_EFFORT_SEAT_2=inherit
JUDGE_MAX_TOKENS_SEAT_1=0
JUDGE_MAX_TOKENS_SEAT_2=0
```

`_cached_verdict()` must require the stored profile and request fingerprints
for each slot, not only the model strings. The request fingerprint covers the
provider identity, endpoint fingerprint, model, resolved reasoning request
fields, `max_tokens`, batch width, the prompt/schema version, and an effective
project-context digest. The project-context digest closes a live correctness
hole rather than only tightening a measurement: `compute_context_hash()` covers
source, note and glossary only (`weblate/trans/models/judge.py:71-90`), while
`judge_project_context()` (`weblate/trans/judge_loop.py:187-205`) goes into the
prompt, so editing a project's judge persona or style currently leaves stale
verdicts reusable.

Batch width belongs in the fingerprint because it is literally part of the
request: `request_verdicts()` serializes the whole batch's `segments` into one
user message (`weblate/trans/judge.py:605-642`), so a verdict about one string
at width five was produced from a prompt that also carried four other strings.
A width-one retry answer and a width-five answer are different requests, and
treating them as interchangeable would let cache reuse cross a prompt boundary.

The accepted cost is stated rather than avoided: a verdict earned in a
width-one retry round is not reused by a later ordinary run, so that unit is
judged again as part of a normal batch - one extra string in one batch, not a
multiplied cost - and a change to `JUDGE_BATCH_SIZE` invalidates the cache
wholesale. Both are cheaper than reusing an answer produced under a different
prompt.

Legacy verdicts with no fingerprint are not reused after this feature lands,
and the cost of that is explicit and one-time: on a deployment holding existing
verdicts, the first judge run after this change re-judges its whole scope at two
paid calls per batch. A legacy row cannot prove which endpoint, thinking mode or
project context produced it - the French measurement had to reconstruct exactly
that - so backfilling a fingerprint would mean assuming the current
configuration produced old rows, which is the assumption that measurement
disproved. The re-judge is accepted rather than engineered around.

### D4. Retries are classified, bounded, and same-seat

Keep the existing `JUDGE_TRANSPORT_RETRIES` behavior. Add three disabled-by-
default budgets:

| setting | default | eligible terminal failure |
|---|---:|---|
| `JUDGE_PROTOCOL_RETRIES` | 0 | `empty-response`, `invalid-json`, `invalid-envelope`, `segment-count`, `invalid-segment` |
| `JUDGE_TRANSIENT_HTTP_RETRIES` | 0 | `http-server` |
| `JUDGE_MAX_UNPARSED_RETRY_ROUNDS` | 0 | a unit where every seat is final `unparsed` |

The retry ladder is deliberate:

1. Transport failures use the existing transport budget and exponential
   backoff.
2. `429` keeps its existing one bounded retry, using `Retry-After` when
   present and a capped jittered backoff otherwise.
3. A `5xx` may use `JUDGE_TRANSIENT_HTTP_RETRIES`.
4. A protocol failure may use `JUDGE_PROTOCOL_RETRIES`.
5. A deadline, over-size response, `401`, `403`, `unknown`, or
   `finish_reason=length` has no same-provider retry. A larger output cap is
   an explicit measured profile decision, not an automatic reaction.

Two of those are changes to current behavior, both deliberate. `403` is retried
once today (`weblate/trans/judge.py:699-702`); it is an entitlement or
configuration signal, a repeat buys nothing but a second billed refusal, and
the failover plan wants it as a trigger, so it becomes terminal here and that
plan's trigger list is updated in the same series. `unknown` is terminal
because a closed vocabulary with an escape bucket that automatically spends a
budget would let any future unclassified failure double the bill in silence; an
unclassified failure must surface in the report instead.

Every retry reuses the same profile and batch data. It has a unique
`JudgeRequestAttempt` row and consumes a documented budget. The new defaults
are zero because automatically multiplying paid requests on all existing
deployments would be an unsafe rollout.

The retry helper takes an injected sleeper/jitter source in tests. Production
uses capped exponential backoff with jitter, preventing synchronized retry
storms when the proxy is unhealthy.

### D5. Failover consumes a terminal classification, it does not race it

Provider failover remains outside this implementation, but the two plans share
the request boundary and must not create competing retry loops. When the
separate failover plan is approved, its order is:

1. Spend only the same-provider budgets from D4.
2. For a terminal transport failure, exhausted 429, 401, 403, or exhausted
   5xx, call the configured fallback exactly once for that seat and logical
   batch.
3. Record the primary and fallback attempts separately, with their own
   providers and profile fingerprints.
4. Treat a `200` parser failure, deadline, oversized reply and
   `finish_reason=length` as final unparsed outcomes. They never reach a
   fallback provider.

The eventual failover change must consume `JudgeRequestProfile`,
`JudgeRequestAttempt`, `judge_provider`, and the cache fingerprints from this
plan. It does not add parallel migrations or a second request-profile
resolver.

### D6. Requeue only double-unparsed units

The retry phase is a bounded phase **around** the existing repair loop, never a
recursive branch inside it. `run_judge_batch()` already runs
`while True: judge -> prepare -> repair` with `attempt` bounded by
`JUDGE_MAX_REPAIR_ATTEMPTS` (`weblate/trans/judge_loop.py:499-611`). The phase
wraps that loop:

```text
for round in 1 .. JUDGE_MAX_UNPARSED_RETRY_ROUNDS:
    select units whose current round is all-unparsed and unchanged
    stop when the selection is empty
    wait one bounded jittered delay inside the same producer task
    rejudge the selection at width one, both seats
    re-enter the existing repair loop with the units whose fresh verdict is
      repairable, carrying each unit's already-spent `attempt`
```

Selection reads the current round only. A unit qualifies when every seat row of
that round is unparsed; through `collegium_verdict()` this also matches a round
whose second seat row is missing
(`weblate/trans/models/judge.py:507-511`), which is intended - a unit with no
parsed opinion is the case being fixed. Do not requeue an already parsed
single-seat result, a cached result, a skipped unit, or a unit whose
target/context hash changed: `current_round()` returns empty for a changed unit
(`weblate/trans/models/judge.py:423-451`), which is how a stale unit is told
apart from a transport-dead one.

Within a round:

1. Refresh and hash-check only the selected units. A target/context race is a
   `stale-conflict` outcome, which `JudgeRunUnit.Outcome` already carries
   (`weblate/trans/models/judge.py:161`), and is not rejudged.
2. Rejudge under a new `JudgeVerdict.run_id` tagged
   `round_kind=unparsed-retry`, keeping the unit's existing `attempt`.
   `attempt` keeps its single meaning, repair progression; the fresh `run_id`
   satisfies the `(unit, run_id, attempt, seat)` constraint
   (`weblate/trans/models/judge.py:333-336`), and because `current_round()`
   orders by timestamp the retry round becomes the unit's current round.
3. Force width one, one unit per request. This prevents one malformed batch
   reply from making an otherwise unrelated peer unparsed. The retry profile
   keeps each seat's model, endpoint, reasoning fields and `max_tokens`, and
   carries a distinct request fingerprint because width is part of request
   identity (D3): the verdict it earns is final evidence for this run, and a
   later ordinary run judges that string again rather than reusing an answer
   produced from a one-segment prompt.
4. A retry is not a lower-trust second path: a parsed retry verdict enters the
   normal collegium, and a repairable one re-enters the repair loop with the
   unit's already-spent `attempt`, so the existing `attempt > attempts` bound
   terminates it exactly as today. A unit that already spent its repair budget
   gets no extra attempt from having been retried.
5. A re-judge triggered by such a repair is an ordinary `repair` round. If it
   comes back double-unparsed it may be selected by the **next** unparsed-retry
   round, never within the current one. No recursion, no third loop.
6. Return retry/stale metadata in `JudgeBatchResult`; the existing
   `BatchAutoTranslate` persistence boundary then creates or updates the one
   final `JudgeRunUnit` row, including the count of completed unparsed retry
   rounds. If every seat still fails, the final outcome stays `unparsed` and
   the report says which terminal reasons dominated.

The one-unit retry width is a reliability isolation mechanism, not an
unmeasured global `JUDGE_BATCH_SIZE` change. The extra paid requests of the
phase are bounded by:

```text
number of double-unparsed units
  × configured retry rounds
  × two seats
  × (initial request + transport budget + 429 retry
     + transient-HTTP budget + protocol budget)
```

Repair rounds are not an additional factor: they sit inside the existing
`(1 + JUDGE_MAX_REPAIR_ATTEMPTS)` ceiling that the phase re-enters rather than
extends. The ceiling stays conservative because a unit leaves the phase after
its first parseable collegium result.

Progress must not use the paid-request ceiling as a denominator. Ticks happen
once per completed batch through `on_batch`
(`weblate/trans/autotranslate.py:757-773`) against
`progress_steps = batches × seats × (JUDGE_MAX_REPAIR_ATTEMPTS + 1)`
(`weblate/trans/autotranslate.py:701-705`), and a retry inside a batch never
ticks, so folding transport/429/protocol budgets into the denominator would
freeze the bar below its real completion. The double-unparsed set is also
unknown until the initial rounds finish, so it cannot enter the pre-run
preview. Instead `progress_steps` is extended when the phase begins, by
`selected units × configured rounds × seats` - one tick per unit per seat at
width one - and `_judge_phase()` gains an explicit `unparsed retry` phase
reporting the real selected count.

`judge_request_upper_bound()` (`weblate/trans/judge.py:49-64`) and
`JudgeScopePreview` keep their formulas, so the estimate shown before a run
(`weblate/trans/views/basic.py:901-909`) does not change. Both gain one sentence
recording that the number excludes retry budgets and unparsed-retry rounds; the
run report is where actual counts are read.

No full Celery-task autoretry is added. An exception raised within the
producer's requeue path is handled by the existing `BatchAutoTranslate`
failure finalization and marks that `JudgeRun` failed. A process kill remains
an operational recovery problem: this plan does not claim to heal a
`RUNNING` row left by a terminated worker. Retrying the outer task would create
a new `JudgeRun`, repeat healthy units, and make the existing durable report
ambiguous. A future delayed cross-task retry queue needs its own idempotency
and status design; it is outside this plan.

### D7. Reports show reliability without exposing translation data

Extend the existing `JudgeRun` report with:

- the safe configuration snapshot;
- final outcomes before and after unparsed retry rounds;
- per-seat counts by `failure_kind`, attempt count and elapsed-time percentile;
- the number of responses with reported reasoning tokens;
- per-seat parse rate, and the count of rounds decided by a **single** parsed
  seat; and
- a scope-authorized, paginated technical attempt table with no prompt or raw
  response.

The single-seat count is not decoration. At the measured rates the probability
that exactly one seat parses is `0.805 × 0.659 + 0.195 × 0.341 ≈ 0.60`: most
judged strings in that run were decided by one seat, and `collegium_verdict()`
reports such a round as an ordinary verdict with no marker
(`weblate/trans/models/judge.py:499-512`). A report counting only unparsed
units therefore shows a healthy run while the two-seat collegium has quietly
collapsed into a one-seat judge. That is the quality consequence of a flaky
endpoint and it must be visible; making it visible changes no quality policy,
and this plan still adds no circuit breaker.

The run report uses the same scope/actor access checks as today. It must never
leak an attempt from an inaccessible run, even when the same `Unit` is
visible elsewhere.

The producer completion message remains short. It adds a factual warning when
terminal unjudged units remain, for example “24 strings left unjudged: 18
transport, 6 invalid JSON”, and a second when a large share of the run was
decided by one seat, for example “49 of 82 strings judged by one seat only”. It
does not claim that a model made a quality decision.

## Implementation tasks

The order is two waves, not six independent steps.

**Wave A - evidence, no new spend policy:** Tasks 1, 2 and 3, plus Task 6's
preflight probe and the report aggregates that do not depend on retries. This
wave is worth landing whatever the probe finds, because today the system cannot
name its own failures.

**Wave B - retry policy:** Tasks 4 and 5, plus the retry-specific report rows.
Wave B spends three settings across five configuration surfaces, a migration
and a bounded outer loop on the assumption that retries are the right lever.
The probe at the end of wave A is exactly what tests that assumption, so
re-confirm wave B against its terminal-failure mix before implementing it: if
the dominant terminal class is a seat profile fault, the remedy is the model
work, not `JUDGE_PROTOCOL_RETRIES`.

### Task 1: Introduce testable failure classification

**Files:** `weblate/trans/judge.py`,
`weblate/trans/tests/test_judge_client.py`.

1. Replace the parser's untyped `None` result with a private typed parse
   outcome carrying either aligned results or one D1 failure kind.
2. Extend `_BatchResponse` with safe response metadata needed for D1:
   response byte count, finish reason where a payload exposes one, and the
   already known status/transport state.
3. Build a `JudgeAttemptEvent` for each HTTP POST and add a private observer
   separate from the existing final-batch `on_batch` callback.
4. Keep `request_verdicts()` return type and its all-or-nothing batch
   behavior unchanged until Tasks 4 and 5 opt into new retries.
5. Cover each closed failure type with response fixtures, including a reset,
   timeout/deadline, oversized body, empty body, invalid JSON, wrong envelope,
   missing/duplicate segment, invalid segment, length finish reason, 401/403,
   429, 5xx and another 4xx.

Tests must assert category and metadata, not only `result.unparsed`. No test
fixture may contain a production token or user translation.

### Task 2: Persist attempt provenance and existing-profile snapshots

**Files:** `weblate/trans/models/judge.py`,
`weblate/trans/models/llm_usage.py`, a new migration after `0108`,
`weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/autotranslate.py`,
`weblate/trans/tests/test_judge_client.py`,
`weblate/trans/tests/test_judge_loop.py`,
`weblate/trans/tests/test_judge_autotranslate.py`, and migration tests.

1. Add `JudgeRequestAttempt`, the nullable `JudgeVerdict` provenance fields,
   and the safe `JudgeRun.configuration` snapshot plus its persistence-loss
   counter described in D1 and D2. `JudgeRun` has neither field today
   (`weblate/trans/models/judge.py:108-132`). `judge_provider` lands here, so
   `docs/llm-first/plans/2026-08-26-judge-provider-failover.md:110-117` must be
   edited to consume it instead of introducing a competing migration.
2. Introduce the internal `JudgeRequestProfile` resolver for the existing
   global settings only. It must resolve and fingerprint the two current slots
   before `JudgeRun` creation; Task 3 extends this resolver rather than
   replacing it.
3. Give new `LLMUsageLog` records a one-to-one attempt link when an attempt
   has usage data. The attempt still persists when the usage field is absent.
4. Thread the producer run, logical round ID, slot and batch index from
   `run_judge_batch()` into the attempt observer and verdict persistence. The
   `run` argument is accepted and never used today
   (`weblate/trans/judge_loop.py:466` against its body), so this is its first
   use.
5. Write attempt and usage rows through one persistence function taking an
   optional producer run: with a run it writes both in one small transaction,
   without a run it writes only the usage row, as `_record_usage()` does today.
   Do not fork judge accounting into two implementations. Fill
   `LLMUsageLog.batch_size` and `LLMUsageLog.outcome` from the same event.
6. Hand the terminal attempt to the verdict writer through state shared between
   the attempt observer and the `on_batch` closure in `judge_loop.py`, keyed
   `(seat, batch_index)`. `on_batch` keeps its signature and its exactly-once
   contract; the progress tick and the seat-parallelism barrier depend on it.
7. Make all observability writes best effort. A failed attempt-log insert must
   not suppress an otherwise parseable verdict or change the retry decision.
   Increment the run's persistence-loss counter instead.
8. Add indexes for `(judge_run, seat, batch_index, attempt_index)` and
   `(judge_run, failure_kind)`. Do not index raw or user-originated text.
9. Add a data migration only for schema defaults. Do not fabricate provider or
   run links for historical `JudgeVerdict` or `LLMUsageLog` rows. Each task in
   this plan adds its own migration; a committed migration is never amended.

Tests:

- a parseable reply, parser-rejected payload and transport reset each create
  the expected attempt record;
- a billed parser failure links one usage row to its attempt;
- an unbilled reset has no usage row but remains reportable;
- one producer run with multiple translations links fresh verdicts to its own
  run, not merely to a timestamp window;
- snapshot data contains no key, authorization header, prompt, target or raw
  response;
- an accounting error does not change verdict or retry behavior;
- an attempt/usage transaction failure increments the persistence-loss counter
  without producing an orphaned foreign key;
- `on_batch` still fires exactly once per completed batch, so the existing
  progress-tick count for a run is unchanged;
- a judge usage row carries `batch_size` and `outcome`, not only the fields the
  current writer sets; and
- migration defaults preserve old rows and report rendering remains safe for
  their null provenance.

Commit: `feat(judge): record request attempt diagnostics`

### Task 3: Extend request profiles with per-seat controls and cache identity

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`,
`weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`,
the migration from Task 2 or a successor, and
`weblate/trans/tests/test_judge_client.py` /
`weblate/trans/tests/test_judge_loop.py`.

1. Add the four D3 settings to every documented/default/environment surface,
   `docs/admin/config.rst`, and `docs/admin/install/docker.rst`. Repair the
   existing drift at the same time: `weblate/trans/models/_conf.py:102-112`
   omits `JUDGE_REQUEST_DEADLINE`, `JUDGE_TRANSPORT_RETRIES` and
   `JUDGE_REASONING_EFFORT`; `deploy/environment.example:116-129` omits the
   deadline and transport retries and only comments the reasoning setting;
   `docs/admin/install/docker.rst` documents no environment variable for the
   deadline or transport retries. While there, drop the stale claim at
   `deploy/environment.example:128` that reasoning "must stay empty" on the
   LiteLLM proxy - `none` is admitted (`weblate/trans/judge.py:169-174`).
2. Extend Task 2's `JudgeRequestProfile` resolver and validation before the
   first paid POST. The resolver is the sole place allowed to decide whether
   to send a vendor-specific thinking field, generic OpenRouter reasoning
   field, or no field.
3. Build the payload from the resolved profile, adding `max_tokens` only when
   non-zero. Never put an unset value into the payload.
4. Compute and persist the safe profile and request fingerprints. Update
   `_cached_verdict()` to require one matching slot/profile/request fingerprint
   per cached verdict, including the effective project-context digest and the
   batch width.
5. Give every fresh verdict and attempt its resolved profile fingerprint.

Tests:

- all-inherit settings reproduce existing OpenRouter and LiteLLM payloads;
- Qwen `none` emits `enable_thinking: false`;
- DeepSeek `none` emits only its admitted vendor control;
- `default` emits no reasoning control;
- per-seat settings can express DeepSeek default plus Qwen disabled;
- valid 1–8,192 values, zero omission, booleans and invalid `max_tokens`
  values behave predictably;
- same models with a different profile, provider, project context or batch
  width miss cache;
- an inadmissible seat/model reasoning pair fails validation before any POST;
- matching profiles retain cache reuse; and
- old blank-profile evidence never satisfies the new cache predicate.

No live model setting is changed in this task. The correct DeepSeek
disable-thinking wire format remains a measurement question, not something a
unit test against an HTTP fake can prove.

Commit: `feat(judge): support per-seat request profiles`

### Task 4: Add bounded request-level retries

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/models/_conf.py`, `weblate/trans/judge.py`, and
`weblate/trans/tests/test_judge_client.py`.

1. Add `JUDGE_PROTOCOL_RETRIES` and `JUDGE_TRANSIENT_HTTP_RETRIES`, both
   defaulting to zero.
2. Replace the current ad-hoc loop with one classifier-driven retry decision,
   preserving the existing bounded transport and 429 semantics. Make `403`
   terminal for the same endpoint, removing the single retry at
   `weblate/trans/judge.py:699-702`, and keep `unknown` terminal as well.
3. Record `will_retry`, retry ordinal and elapsed time on every attempt before
   sleeping.
4. Use capped exponential backoff plus jitter, with an injectable source for
   deterministic tests.
5. Guarantee that a parsed response ends the retry loop immediately and that
   a permanent failure ends it without an extra paid POST.

Tests:

- exact call count for every eligible and ineligible D4 category;
- `max_tokens` truncation, an oversized or deadline response, `403` and
  `unknown` do not retry;
- a parser failure recovers only when the explicit protocol budget is
  positive;
- retry events link to the same logical batch, seat and profile;
- a successful retry writes only the final verdict rows but retains all
  attempt evidence; and
- exhausting every budget yields `unparsed`, never an exception or a pass.

Commit: `feat(judge): retry classified transient failures`

### Task 5: Rejudge bounded double-unparsed rounds

**Files:** `weblate/trans/defaults.py`, `weblate/settings_docker.py`,
`weblate/settings_example.py`, `deploy/environment.example`,
`weblate/trans/judge.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/models/judge.py`, `weblate/trans/autotranslate.py`, a
migration, and the judge loop/autotranslate/round tests.

1. Add `JUDGE_MAX_UNPARSED_RETRY_ROUNDS`, default zero.
2. Add an explicit `round_kind` and retry-round count to producer-visible
   persistence. Do not overload `JudgeVerdict.attempt`, whose current meaning
   is repair progression.
3. Implement the D6 phase as an outer bounded loop around the existing repair
   loop in `run_judge_batch()`: select current all-unparsed units, rejudge them
   at width one under a fresh logical `run_id` with the unit's existing
   `attempt`, then re-enter the repair loop for those whose fresh verdict is
   repairable. No recursion: a repair-triggered re-judge is an ordinary round
   and can only be selected by the next retry round.
4. Refresh units before the delayed call. A target/context race is recorded as
   stale-conflict and not rejudged.
5. Feed a parsed retry result through the existing collegium and repair path,
   carrying the unit's already-spent repair budget so a retried unit gets no
   extra repair attempt. Keep the final representative verdict and retry count
   in result metadata for the `JudgeRunUnit` persistence boundary in
   `weblate/trans/autotranslate.py`, which owns state projection.
6. Extend progress at phase start rather than in the preview: add the
   `unparsed retry` phase to `_judge_phase()`, raise `progress_steps` by
   `selected × rounds × seats` when the phase begins, and add one sentence to
   `judge_request_upper_bound()` and `JudgeScopePreview` recording that the
   pre-run estimate excludes retries.
7. Surface terminal reason counts and the single-seat count in the run report
   and completion warning.

Tests:

- a one-seat loss does not enter the requeue;
- a double-unparsed unit is retried once per configured round and no more;
- only those units generate retry requests, at width one;
- retry success replaces the final run outcome without deleting prior
  unparsed evidence;
- retry major/critical uses normal repair semantics, and a unit that already
  spent `JUDGE_MAX_REPAIR_ATTEMPTS` gets no extra attempt from being retried;
- a repair-triggered round that comes back double-unparsed is picked up by the
  next retry round only, never inside the current one;
- progress never exceeds its total and the pre-run estimate is unchanged;
- a changed unit is not sent and has a safe stale outcome;
- exhausted retries remain unparsed and leave no state transition;
- cached, cap-skipped, permission-skipped and deleted units are not retried;
- no duplicate `(run, unit)` row, verdict row or attempt record is created;
  and
- an application exception during requeue leaves a correctly failed
  `JudgeRun`, not a completed one or a whole-task duplicate. A process kill is
  documented as out of scope rather than simulated as this guarantee.

Commit: `feat(judge): requeue double-unparsed units`

### Task 6: Report and validate the reliability contract

**Files:** existing judge-run view/template/report tests as identified in
`weblate/trans/views/judge.py`,
`weblate/templates/judge-run.html`, and
`weblate/trans/tests/test_judge_views.py`; `docs/admin/config.rst`,
`docs/admin/install/docker.rst`; documentation and measurement artifacts.

1. Add the D7 aggregates - including per-seat parse rate and the single-seat
   round count - and a paginated, permission-checked technical attempt view
   reusing the existing `Paginator` pattern
   (`weblate/trans/views/judge.py:160-170`,
   `weblate/templates/judge-run.html:123-125`). Do not add a global search
   filter or expose payload text.
2. Test no count or metadata leaks through a report URL an actor cannot read.
   The scope/actor recheck already lives at
   `weblate/trans/views/judge.py:91-109,149-160`.
3. Add one focused management/preflight command or analysis probe that uses
   the production parser and profile resolver, records no translations, and
   writes a dated measurement artifact. It must accept an explicit model and
   profile only, never pull an API key from command arguments.
4. Document the retention rule in `docs/admin/config.rst`: attempt, verdict and
   usage rows are retained, and pruning them is out of scope here.
5. Run the normal focused test files, migration checks, `uv run prek run
   --all-files`, and the narrowest practical Django report tests. Follow the
   repository's shared-dev-stack rule; do not restart `dev-docker` merely to
   perform this plan's tests.

Commit: `feat(judge): report reliability attempts`

## Live measurement and rollout gates

Live probes and deployment are separate approvals. Before enabling a non-zero
new retry setting, run a read-only paid probe through the actual LiteLLM proxy:

1. Hold Qwen at its known disabled-thinking profile.
2. Compare DeepSeek's default profile, the confirmed vendor-specific disabled
   profile if one exists, and a bounded `max_tokens` candidate. Do not test
   several variables in one arm.
3. Use the production prompt, strict schema and parser. Preserve per-attempt
   metadata, no raw response content.
4. Run at least three time-separated repeats against a representative frozen
   French slice before interpreting a rate. Batch width is recorded and held
   fixed within a comparison.
5. Publish the result in `docs/llm-first/measurements/` before changing a
   deployed profile.

Two different questions are being answered here, and one gate must not block
the other.

**Product gate** (decides whether the new budgets may leave zero):

- zero terminal units with no parsed opinion in each canary repeat;
- retry call counts stay within the D6 arithmetic bound;
- no retry is made for a parsed verdict, deadline, oversized reply, `401`,
  `403`, `unknown` or `finish_reason=length`;
- every persisted producer-run attempt and usage row joins to its `JudgeRun`,
  with zero persistence losses in the canary;
- cache rejects a deliberately changed profile and reuses an unchanged one;
- a report reader can explain every terminal unparsed unit, and every
  single-seat decision, from stored metadata without worker-log access; and
- manual inspection finds no credential or translation text in configuration
  snapshots or technical attempt rows.

**Seat-health finding** (does not gate this plan): the per-seat raw and terminal
parse rates and the single-seat share are published in the measurement artifact
and handed to the model work, whose own admission gate already owns model
choice - "Unparsed rate <= 5%" in every repeat
(`docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md:207-210`).

The split is arithmetic, not diplomacy. Same-seat retries cannot rescue a seat
at the measured 80.5%: `0.805 ** n <= 0.01` needs `n >= 21` paid attempts per
request, so a per-seat terminal target of 1% is a statement about the model and
its profile, not about retry code. What retries do fix is the product failure,
because the two seats fail near-independently - `0.805 × 0.341 × 82 = 22.5`
predicted double failures against 24 observed - so one round removes roughly
72% of the terminal units and two rounds roughly 92%. A gate demanding 1% per
seat would freeze at zero the very budgets that fix the product failure, while
the actual remedy lived in another plan.

If the product gate fails, keep the new budgets at zero, retain the measured
evidence, and do not compensate by enabling seat parallelism or silently
widening retries. Revisit the provider/model profile or the approved
provider-failover plan instead.

Only after the product gate passes may
`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md` receive a live
serial-versus-parallel canary approval. That canary must hold profiles and
retry budgets fixed and compare terminal, not merely initial, unparsed rates.

## Out of scope

- Choosing a permanent LiteLLM pair or replacing DeepSeek.
- Provider failover implementation. Its separate plan consumes the D1
  evidence and preserves its parser-invalid-response safety rule.
- Retrying a full Celery task or creating a delayed cross-task retry queue.
- A circuit breaker that intentionally degrades a two-seat judge to one seat.
  It changes quality policy and needs its own measurement and approval.
- Prompt, rubric, schema, severity or repair-policy changes.
- Parallel requests within a seat or across seats.
- Recording raw prompts, completions or reasoning traces.
- Production deployment, configuration changes, or paid probes without their
  explicit approvals.

## Expected files

- `weblate/trans/judge.py`
- `weblate/trans/judge_loop.py`
- `weblate/trans/autotranslate.py`
- `weblate/trans/models/judge.py`
- `weblate/trans/models/llm_usage.py`
- new migrations after `weblate/trans/migrations/0108_judge_verdict_instruction.py`
- `weblate/trans/defaults.py`
- `weblate/settings_docker.py`
- `weblate/settings_example.py`
- `deploy/environment.example`
- `weblate/trans/models/_conf.py`
- existing judge client, loop, round, autotranslate, migration and view tests
- existing judge-run view/template and its tests
