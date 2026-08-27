# Plan: harden judge request reliability

**Date:** 2026-08-27. **Status:** proposed, not started. Implementation needs
explicit approval. **Owner decision required before live probes:** the probes
spend provider credits but do not write production translations.

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
an otherwise parseable body. `Retry-After` is captured before the response
context closes, parsed only as a positive delta-seconds value, and capped at
60 seconds; an absent, invalid, date-form or over-cap value falls back to the
normal bounded delay.

Existing `JudgeResult` and the final `on_batch` callback remain
verdict-facing. An internal immutable attempt event carries diagnostic data to
persistence. For a producer run, `request_verdicts()` emits the event but does
not write Django models itself. The serial orchestration caller persists it on
its own thread; the future parallel-seat implementation forwards it through
its caller-owned queue before persistence. Direct callers without a producer
run retain today's local `_record_usage()` behavior and do not create an
attempt row. For a producer run, the caller creates the attempt and any usage
row together in one small transaction. If that transaction fails, it logs the
loss and continues judging, rather than converting a provider reply into a
failed judge task. The run report exposes a persistence-loss counter; a canary
with any such loss fails promotion.

Add nullable provenance fields to new `JudgeVerdict` rows:

- `judge_run`, the producer run that issued this fresh evidence;
- `request_attempt`, the terminal `JudgeRequestAttempt` for the batch;
- `judge_provider`, matching the field already required by the failover plan;
- `profile_fingerprint`; and
- `request_fingerprint`; and
- `round_kind`.

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
database row into a dictionary attack on internal endpoint URLs.

This makes a report reproducible and prevents the mistake made in the French
measurement, where settings had to be reconstructed after the run.

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
`JUDGE_REASONING_EFFORT` exactly. `default` means send no reasoning parameter.
For LiteLLM, `none` uses the existing admitted model-specific vendor mapping;
for OpenRouter, existing allowed effort values remain valid. Invalid
provider/profile combinations fail before a paid request. `max_tokens` is an
integer from 1 through 8,192 when present; zero is the only omission value,
and booleans or malformed environment values are rejected. No default cap is
introduced.

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
fields, `max_tokens`, batch width, prompt/schema version, and an effective
project-context digest. Legacy verdicts with no fingerprint are not reused
after this feature lands. This is intentionally conservative: a cached answer
from the same model but a different proxy, thinking mode, cap, project context
or response contract is not a valid measurement or a reliable result.

### D4. Retries are classified, bounded, and same-seat

Keep the existing `JUDGE_TRANSPORT_RETRIES` behavior. Add three disabled-by-
default budgets:

| setting | default | eligible terminal failure |
|---|---:|---|
| `JUDGE_PROTOCOL_RETRIES` | 0 | `empty-response`, `invalid-json`, `invalid-envelope`, `segment-count`, `invalid-segment`, `unknown` |
| `JUDGE_TRANSIENT_HTTP_RETRIES` | 0 | `http-server` |
| `JUDGE_MAX_UNPARSED_RETRY_ROUNDS` | 0 | a unit where every seat is final `unparsed` |

The retry ladder is deliberate:

1. Transport failures use the existing transport budget and exponential
   backoff.
2. `429` keeps its existing one bounded retry, using `Retry-After` when
   present and a capped jittered backoff otherwise. `403` keeps its existing
   one bounded retry but is classified as an entitlement/configuration signal,
   not a stable retry mechanism.
3. A `5xx` may use `JUDGE_TRANSIENT_HTTP_RETRIES`.
4. A protocol failure may use `JUDGE_PROTOCOL_RETRIES`.
5. A deadline, over-size response, `401`, an exhausted `403`, or
   `finish_reason=length` has no same-provider retry. A larger output cap is
   an explicit measured profile decision, not an automatic reaction.

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
2. For a terminal transport failure, exhausted 429, 401, exhausted 403, or
   exhausted 5xx, call the configured fallback exactly once for that seat and
   logical batch.
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

After the ordinary two-seat round and any content repair round complete,
select only units whose current round has an unparsed result from every
configured seat. Do not requeue an already parsed single-seat result, a cached
result, a skipped unit, or a unit whose target/context hash has changed.

When `JUDGE_MAX_UNPARSED_RETRY_ROUNDS` is positive:

1. Wait one bounded jittered delay inside the same producer task.
2. Refresh and hash-check only the selected units.
3. Rejudge them under a new `JudgeVerdict.run_id` tagged
   `round_kind=unparsed-retry`, with derived one-unit retry profiles. Those
   profiles retain each seat's model, endpoint, reasoning fields and
   `max_tokens`, but have a distinct batch-width/request fingerprint.
4. Force this isolated retry round to one unit per request. This prevents one
   malformed batch reply from making an otherwise unrelated peer unparsed.
5. Apply the normal collegium and repair logic to a newly parsed retry result.
   A retry is not a lower-trust second path.
6. Return retry/stale metadata in `JudgeBatchResult`; the existing
   `BatchAutoTranslate` persistence boundary then creates or updates the one
   final `JudgeRunUnit` row, including the count of completed unparsed retry
   rounds. If every seat still fails, the final outcome stays `unparsed` and
   the report says which terminal reasons dominated.

The one-unit retry width is a reliability isolation mechanism, not an
unmeasured global `JUDGE_BATCH_SIZE` change. Its additional cost is bounded by:

```text
number of initially double-unparsed units
  × configured retry rounds
  × two seats
  × (initial request + transport budget + 429 retry
     + transient-HTTP budget + protocol budget)
  × (1 + JUDGE_MAX_REPAIR_ATTEMPTS)
```

This is intentionally a conservative ceiling: a unit stops its unparsed retry
rounds after the first parseable collegium result, so it normally cannot use
every factor. Progress accounting adds an explicit “unparsed retry” phase with
a maximum derived from this same ceiling; it must not reuse the original
initial/repair-only denominator.

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
- the number of responses with reported reasoning tokens; and
- a scope-authorized, paginated technical attempt table with no prompt or raw
  response.

The run report uses the same scope/actor access checks as today. It must never
leak an attempt from an inaccessible run, even when the same `Unit` is
visible elsewhere.

The producer completion message remains short. It adds a factual warning when
terminal double-unparsed units remain, for example “24 strings left unjudged:
18 transport, 6 invalid JSON.” It does not claim that a model made a quality
decision.

## Implementation tasks

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

1. Add `JudgeRequestAttempt`, the nullable provenance fields and the safe
   `JudgeRun.configuration` snapshot described in D1 and D2.
2. Introduce the internal `JudgeRequestProfile` resolver for the existing
   global settings only. It must resolve and fingerprint the two current slots
   before `JudgeRun` creation; Task 3 extends this resolver rather than
   replacing it.
3. Give new `LLMUsageLog` records a one-to-one attempt link when an attempt
   has usage data. The attempt still persists when the usage field is absent.
4. Thread the producer run, logical round ID, slot and batch index from
   `run_judge_batch()` into the attempt observer and verdict persistence.
5. Move producer-run usage writes from the network client to that observer.
   Direct callers without a `JudgeRun` keep the current `_record_usage()` path
   and create no attempt record.
6. Make all observability writes best effort. A failed attempt-log insert must
   not suppress an otherwise parseable verdict or change the retry decision.
   Increment the run's persistence-loss counter instead.
7. Add indexes for `(judge_run, seat, batch_index, attempt_index)` and
   `(judge_run, failure_kind)`. Do not index raw or user-originated text.
8. Add a data migration only for schema defaults. Do not fabricate provider or
   run links for historical `JudgeVerdict` or `LLMUsageLog` rows.

Tests:

- a parseable reply, parser-rejected payload and transport reset each create
  the expected attempt record;
- a billed parser failure links one usage row to its attempt;
- an unbilled reset has no usage row but remains reportable;
- one producer run with multiple translations links fresh verdicts to its own
  run, not merely to a timestamp window;
- snapshot data contains no key, authorization header, prompt, target or raw
  response;
- an accounting error does not change verdict or retry behavior; and
- an attempt/usage transaction failure increments the persistence-loss counter
  without producing an orphaned foreign key; and
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
   existing `_conf.py` drift and the Docker environment example by listing the
   current deadline, transport retry and global reasoning settings at the same
   time.
2. Extend Task 2's `JudgeRequestProfile` resolver and validation before the
   first paid POST. The resolver is the sole place allowed to decide whether
   to send a vendor-specific thinking field, generic OpenRouter reasoning
   field, or no field.
3. Build the payload from the resolved profile, adding `max_tokens` only when
   non-zero. Never put an unset value into the payload.
4. Compute and persist the safe profile and request fingerprints. Update
   `_cached_verdict()` to require one matching slot/profile/request fingerprint
   per cached verdict, including the effective project-context digest.
5. Give every fresh verdict and attempt its resolved profile fingerprint.

Tests:

- all-inherit settings reproduce existing OpenRouter and LiteLLM payloads;
- Qwen `none` emits `enable_thinking: false`;
- DeepSeek `none` emits only its admitted vendor control;
- `default` emits no reasoning control;
- per-seat settings can express DeepSeek default plus Qwen disabled;
- valid 1–8,192 values, zero omission, booleans and invalid `max_tokens`
  values behave predictably;
- same models with a different profile, provider, batch width or project
  context miss cache;
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
   preserving the existing bounded transport and 429 semantics. Make 403
   terminal for the same endpoint, ready for the separately planned failover
   path.
3. Record `will_retry`, retry ordinal and elapsed time on every attempt before
   sleeping.
4. Use capped exponential backoff plus jitter, with an injectable source for
   deterministic tests.
5. Guarantee that a parsed response ends the retry loop immediately and that
   a permanent failure ends it without an extra paid POST.

Tests:

- exact call count for every eligible and ineligible D4 category;
- `max_tokens` truncation and oversized/deadline response do not retry;
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
3. After the normal repair loop, select only current all-unparsed units and
   run the D6 one-unit retry round under a fresh logical `run_id`.
4. Refresh units before the delayed call. A target/context race is recorded as
   stale-conflict and not rejudged.
5. Feed a parsed retry result through the existing collegium, state projection
   and repair path. Keep the final representative verdict and retry count in
   result metadata for the eventual `JudgeRunUnit` persistence boundary.
6. Surface terminal reason counts in the run report and completion warning.

Tests:

- a one-seat loss does not enter the requeue;
- a double-unparsed unit is retried once per configured round and no more;
- only those units generate retry requests, at width one;
- retry success replaces the final run outcome without deleting prior
  unparsed evidence;
- retry major/critical uses normal repair semantics;
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

1. Add the D7 aggregates and a paginated, permission-checked technical
   attempt view. Do not add a global search filter or expose payload text.
2. Test no count or metadata leaks through a report URL an actor cannot read.
3. Add one focused management/preflight command or analysis probe that uses
   the production parser and profile resolver, records no translations, and
   writes a dated measurement artifact. It must accept an explicit model and
   profile only, never pull an API key from command arguments.
4. Run the normal focused test files, migration checks, `uv run prek run
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

Promotion gate for the selected configuration:

- zero terminal double-unparsed units in each canary repeat;
- terminal per-seat unparsed rate at or below 1% across at least 100 units per
  seat;
- every persisted producer-run attempt and usage row joins to its `JudgeRun`,
  with zero persistence losses in the canary;
- no retry is made for a parsed verdict, deadline, oversized reply or
  `finish_reason=length`;
- retry call counts stay within the configured arithmetic bound;
- cache rejects a deliberately changed profile and reuses an unchanged one;
- a report reader can explain every terminal unparsed unit from stored
  metadata without worker-log access; and
- manual inspection finds no credential or translation text in configuration
  snapshots or technical attempt rows.

If the gate fails, keep the new budgets at zero, retain the measured evidence,
and do not compensate by enabling seat parallelism or silently widening
retries. Revisit the provider/model profile or the approved provider-failover
plan instead.

Only after this gate passes may
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
