# LiteLLM Judge stability: root cause and stabilization research

**Date:** 2026-08-28. **Status:** completed research. **Scope:** the two Judge
seats `deepseek-v4-pro` and `qwen3.8-max` through the corporate LiteLLM proxy at
`hcbifrost.herocraft.com`.

Implementation decisions based on this research are recorded in
`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md`.

## Executive finding

The models are capable of judging and regularly return semantically useful
verdicts. The instability is not one model defect. Two confirmed failure
mechanisms and one confirmed compatibility risk were collapsed into the same
operational problem:

1. A hidden client-side contract was introduced for the model-generated
   `instruction` field. The field became required and semantically constrained,
   but the constraint was never added to the Judge prompt or JSON Schema.
2. Non-streaming requests that produce no first byte for roughly 30-31 seconds
   are reset before Weblate's own 120-second timeout. Streaming the same payload
   starts returning bytes early and completes beyond that boundary.
3. The corporate proxy does not apply the two models' reasoning controls
   symmetrically. Qwen reliably honours `enable_thinking=false`; DeepSeek still
   reports reasoning tokens after `thinking.disabled` was sent. This is a
   measured latency and cost risk, not yet a proven cause of any reset.

These mechanisms are amplified by an all-or-nothing batch parser: one malformed
segment turns every unit in that batch into `unparsed`. The current log calls an
HTTP 200 response `ok` before parsing it, so protocol failures are not named in
production diagnostics.

The recommended architecture is therefore not a larger undifferentiated retry
budget. It is:

- remove model-generated repair instructions from the verdict wire contract;
- validate a small provider-neutral evidence contract in Weblate;
- use immutable, provider-specific profiles for each seat;
- fix the gateway's first-byte envelope and admit DeepSeek streaming only after
  reproducing the first-byte experiment on that route;
- classify every attempt and apply bounded same-seat recovery;
- defer unresolved units durably instead of silently abandoning them.

## Method

This research combines:

- source and history inspection of the Judge client, parser, prompt and repair
  path;
- the dated LiteLLM measurements and raw run summaries already committed under
  `docs/llm-first/measurements/` and `analysis/data/`;
- four independent read-only research tracks on Luna covering run history,
  parser taxonomy, upstream documentation and reliability design;
- sanitized inspection of the corporate proxy's `/models` and `/model/info`
  endpoints;
- four new paid control calls on a neutral `Save` -> `Speichern` segment, with
  only response shape, timing and usage metadata retained;
- official LiteLLM, Qwen and DeepSeek documentation current on 2026-08-28.

No production configuration was changed during the investigation. API keys,
prompts, translation text and raw reasoning were not stored in this document.

## The data flow and where `unparsed` originates

`request_verdicts()` in `weblate/trans/judge.py` builds one chat-completions
request per batch. Each response crosses four boundaries:

1. HTTP transport and bounded body read;
2. outer chat-completions envelope;
3. `segments` extraction and batch alignment;
4. strict per-segment evidence validation.

Any failure returns the same `UNPARSED` value. In particular:

- `_post_batch()` catches every transport exception and loses the exception
  type;
- `_extract_segments()` returns `None` for several distinct envelope failures;
- `_parse_reply()` returns `None` for both batch-level and segment-level
  failures;
- `request_verdicts()` replaces the whole batch with
  `[UNPARSED] * len(batch)`.

The log line `judge batch ... ok` is emitted after an HTTP body is received but
before `_parse_reply()` runs. It therefore means HTTP success, not a usable
Judge opinion.

At the collegium layer, one parsed seat masks the other seat's `unparsed`; a unit
is finally unparsed only when both seats fail in the same round. This explains
why the dev workflow could look functional while carrying a high raw seat
failure rate.

## Confirmed root cause 1: a hidden `instruction` contract

Commit `082ec67` added machine-only repair instructions. It changed the response
contract in three ways:

- `instruction` became a required JSON Schema field;
- the parser required a string no longer than 1,000 characters;
- the parser required `instruction` to be non-empty exactly when `errors` was
  non-empty.

The Judge prompt in `weblate/trans/judge_prompts/verdict.txt` was not changed to
define the field. The JSON Schema declared only a string with `maxLength`; it did
not communicate the parser's `errors`/`instruction` equivalence. A more complex
conditional schema could express that relationship, but the shipped schema
contained no such condition and provider support for that JSON Schema subset
was not tested. The model was therefore judged against a semantic rule it had
never received.

DeepSeek returned this error-free pass shape through LiteLLM:

```json
{
  "id": 0,
  "verdict": "pass",
  "errors": [],
  "back_translation": "",
  "instruction": "None"
}
```

The JSON was valid and all evidence fields were usable. The parser nevertheless
evaluated `bool(errors) != bool(instruction.strip())` as `False != True` and
discarded the batch.

Commit `841d6eb` added a narrow normalization from `"None"` to `""` when there
are no errors, with a regression test. That fixes the observed sentinel but not
the root design error: optional repair metadata still controls whether valid
Judge evidence exists.

The instruction is also redundant. `describe_latest_verdict()` already exposes
the validated severity, category and English error description to the repair
machinery. A deterministic repair wrapper can use those fields without asking
the Judge model to generate a second account of the same defect.

**Conclusion:** remove `instruction` from the model response contract. Keep the
database field only for migration compatibility, store it blank for new rows,
and construct repair guidance deterministically from validated errors.

## Observed Qwen non-streaming reset and streaming workaround

The strongest transport experiment is recorded in
`docs/llm-first/measurements/2026-08-26-litellm-stage0-compatibility.md` and
`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`.

When the blanket exception handler was removed from the diagnostic path, the
failure chain was:

```text
ReadError <- ReadError <- ConnectionResetError
ReadError('[Errno 54] Connection reset by peer')
after 30.8s, first_byte=None, bytes=0
```

The same Qwen payload behaved differently depending only on streaming:

| Mode | First byte | Total | Outcome |
| --- | ---: | ---: | --- |
| `stream: false`, run 1 | never | 30.8 s | connection reset |
| `stream: false`, run 2 | never | 30.7 s | connection reset |
| `stream: true`, run 1 | 8.5 s | 39.0 s | HTTP 200, complete |
| `stream: true`, run 2 | 7.1 s | 43.8 s | HTTP 200, complete |

Weblate's transport timeout is 120 seconds, so its own timeout does not explain
the 30-second cut. The experiment establishes a time-to-first-byte failure
envelope somewhere between the client and the upstream model. It does not
identify the exact owner among nginx, LiteLLM, an intermediate gateway and the
upstream route; that requires server-side timing logs.

Batch size affects this mechanism only indirectly. Larger and defect-bearing
batches require more back-translation, error descriptions and reasoning before
a non-streaming response is released. A fixed count of strings is therefore a
poor control variable compared with predicted output and measured first-byte
time.

The public endpoint resolves directly to `89.19.213.124` and identifies its
front end as nginx. That narrows the public path but does not prove that nginx
created the reset: a client-side TCP reset cannot distinguish the front nginx,
LiteLLM, another internal hop, or the upstream route. The owner requires one
correlation ID joined across nginx and LiteLLM timing logs.

**Conclusion:** Qwen non-streaming is not production-admitted on the present
evidence. In two paired observations, Qwen streaming was a successful workaround
for the measured first-byte case; it is the primary Qwen production candidate,
but the sample does not establish production stability. HCBifrost must localize
and remove or explain the reset with server-side timing evidence. Repeat both
modes across the registered production-shaped envelope corpus and test DeepSeek
independently. Adaptive batching is a safety layer, not a substitute for fixing
the failing transport path.

### Evidence from the Game Pulse client repository

The separate `game_pulse_saas` repository is another production consumer of
the same corporate endpoint. It does not contain HCBifrost server manifests,
LiteLLM `config.yaml`, or the HCBifrost nginx virtual host, so it cannot name
the reset owner. It does provide three useful controls:

- its primary OpenAI client has a 55-second application timeout and two SDK
  retries, neither of which explains a reset at 30.7-30.8 seconds;
- its streaming path sends `stream=true` and
  `stream_options.include_usage=true`, and treats reasoning chunks as upstream
  activity rather than user-visible content;
- its earlier HCBifrost measurement observed an nginx 504 at 60.2-60.3 seconds,
  a different signature and boundary from the Qwen TCP reset measured here.

Game Pulse also translates a Qwen `[no-think]` model suffix into
`extra_body.enable_thinking=false` and suppresses that field for non-Qwen
models because the corporate proxy rejected the incompatible parameter. This
independently supports model-specific request profiles, but does not establish
that HCBifrost forwarded the field to the upstream deployment.

The relevant local evidence is
`/Users/eli/Documents/PythonProjects/gamedev tools/game_pulse_saas/backend/app/ai_pipeline/llm_client.py`,
`/Users/eli/Documents/PythonProjects/gamedev tools/game_pulse_saas/backend/app/config.py`,
and
`/Users/eli/Documents/PythonProjects/gamedev tools/game_pulse_saas/docs/LiteLLM/00-architecture-and-roadmap.md`.
No secret values were read into this report.

## Confirmed compatibility risk: asymmetric reasoning controls

Qwen and DeepSeek use different vendor controls:

```json
{"enable_thinking": false}
```

and:

```json
{"thinking": {"type": "disabled"}}
```

The controls cannot be represented correctly by one provider-neutral global
setting without resolving the model family first.

The measured behaviour is also asymmetric:

- Qwen with thinking enabled failed all 8 of 8 batches in one control, taking
  about 63 seconds per batch after a retry.
- Qwen with `enable_thinking=false` failed 0 of 8 batches and returned in about
  5.6 seconds per batch.
- In the 82-unit French run, all 41 Qwen usage rows had zero reasoning tokens.
- DeepSeek received `thinking.disabled`, but all 37 recorded usage rows still
  carried reasoning tokens, with a mean of 722.95 and a range of 201-1,649.

The four new controls on 2026-08-28 reproduced the same distinction:

| Model | Parsed | Latency | Reported reasoning |
| --- | ---: | --- | --- |
| `deepseek-v4-pro` | 2/2 | 7.6-11.0 s | 284 and 502 tokens |
| `qwen3.8-max` | 2/2 | 2.7-3.0 s | none |

All four responses passed the current parser. This proves current capability,
not long-run stability. It also proves that the DeepSeek disable parameter still
does not suppress reported reasoning on the corporate route.

Official DeepSeek v4 documentation defines `thinking.type` values `enabled` and
`disabled`, with thinking enabled by default. The unresolved question is
therefore where the field is lost or ignored on the corporate path, not whether
the vendor has such a control.

**Conclusion:** resolve immutable profiles per seat before any paid request and
verify the effective outbound wire payload at the proxy. A DeepSeek alias that
does not honour `thinking.disabled` is not admitted to production. The causal
link between leaked reasoning and transport reset remains a hypothesis requiring
an intervention that holds the request and proxy load fixed while changing only
the effective thinking mode.

## Structured output is not one portable capability

The corporate `/model/info` response exposes inconsistent metadata for the two
aliases:

| Alias | Configured provider model | Relevant metadata |
| --- | --- | --- |
| `deepseek-v4-pro` | `openai/deepseek-ai/deepseek-v4-pro` through the custom `openai` provider | `supports_response_schema=true`, native structured output unset, `max_retries=0` |
| `qwen3.8-max` | `openai/qwen3.8-max` | `response_format` listed as a supported OpenAI parameter, `supports_response_schema` unset |

These values establish proxy configuration, not successful enforcement by the
upstream provider.

LiteLLM distinguishes:

- `json_object`, which guarantees JSON syntax where the provider supports it;
- `json_schema`, which requests a particular schema from models that support
  structured output;
- optional LiteLLM-side JSON Schema validation after the provider returns.

Qwen's official documentation lists the Qwen3.8-Max series among models that
support strict JSON Schema output. DeepSeek's official Chat Completions API,
however, currently documents only `text` and `json_object` response formats.
The corporate alias may provide an extended compatibility layer, but that must
be demonstrated rather than inferred from its name.

The appropriate profiles are therefore:

- Qwen: native `json_schema`, `enable_thinking=false`, local semantic
  validation;
- DeepSeek: `json_object`, explicit JSON output contract in the prompt,
  `thinking.disabled`, local schema and semantic validation.

Application validation remains authoritative. Enabling validation in LiteLLM
can improve diagnostics, but it cannot enforce Judge-specific relationships
that are absent from the JSON Schema and must not create hidden provider retries.

## Parser rejection taxonomy

The current parser rejects these classes without naming them:

### Envelope failures

- missing or malformed `choices[0].message`;
- `content=null`;
- malformed JSON;
- top-level JSON that is not an object;
- missing or non-list `segments`;
- answer present only in `reasoning_content`.

### Batch failures

- segment count differs from the request count;
- missing, duplicate, non-integer or out-of-range IDs;
- one segment omitted while the rest are valid.

### Segment failures

- unknown verdict;
- missing or additional keys;
- non-string back-translation;
- malformed errors array;
- unknown category or severity;
- wrong `span` or `description` type;
- instruction type, length or presence mismatch.

### Structurally plausible but deliberately unsafe normalizations

The following should remain rejected rather than guessed into the production
contract:

- JSON embedded in reasoning;
- Markdown-fenced JSON;
- a bare segment array;
- an object keyed by segment IDs;
- incomplete batches;
- extra confidence, reasoning or key fields.

Only an equivalence that is both semantically unambiguous and covered by a
regression test should be normalized. Once model-generated `instruction` is
removed, the known `"None"` exception is no longer needed.

## What the historical runs establish

The most useful operating-point measurements are:

| Model/profile | Batch | Result | Interpretation |
| --- | ---: | --- | --- |
| Qwen, thinking off | 5 | 3/3 parsed, 10/10 planted defects, 0/5 false flags | viable small smoke point |
| Qwen, thinking off | 3 | 5/5 parsed, 10/10 defects | transport-stable on one 15-unit slice |
| Qwen, thinking off | 2 | 8/8 parsed, 9/10 defects | lower width did not improve recall in this one run |
| DeepSeek, width 1 | 1 | 14/15 units; one unit lost after three transport attempts | singleton is not a guarantee |
| DeepSeek, thinking off requested | 2 | 8/8 batches, but 12 attempts | viable only with substantial recovery on the small slice |
| DeepSeek, clean and defect-bearing groups | 5 | 6/7 clean batches, 0/16 defect-bearing batches | output work, not count alone, drives the envelope |

The larger 82-unit French run used LiteLLM, DeepSeek, Qwen, batch 2, reasoning
`none` and one transport retry. Its raw seat results were:

- DeepSeek: 66 of 82 unparsed;
- Qwen: 28 of 82 unparsed;
- both seats unparsed: 24 units;
- final uncovered units: 24 of 82, or 29.3%.

A previously unparsed DeepSeek unit parsed when replayed unchanged. This rules
out a deterministic bad-string explanation, while leaving proxy load, output
length, reasoning and route state as possible changing variables.

Small smoke runs and the full French run are therefore not contradictory. They
show that a profile can work at one corpus and hour without defining a stable
production envelope.

## Hypotheses not yet proven

The following statements are plausible but not established:

- the 30-second reset is specifically an nginx `proxy_read_timeout` rather than
  another gateway or upstream limit;
- DeepSeek reasoning leakage alone causes every reset;
- `temperature=0` will materially reduce schema failures without changing Judge
  quality;
- the proxy's `supports_response_schema=true` flag for DeepSeek corresponds to
  native upstream enforcement;
- one parse retry is sufficient under correlated proxy load;
- batch width 2 is a universal stable point for DeepSeek.

The implementation and release gates must measure these instead of converting
them into configuration assumptions.

## Stabilization approaches considered

### Narrow normalization and more retries

This is the smallest change: recognize harmless sentinels, reduce batch size and
repeat failures. It recovers some calls cheaply, but it leaves the hidden
contract, the first-byte reset and unobservable failure classes intact. It also
raises spend without defining a stable boundary.

**Verdict:** useful only as a temporary safety layer, not the architecture.

### Proxy-only correction

Increasing timeouts, disabling buffering, pinning the LiteLLM version and
correcting alias metadata address the transport root cause. They do not remove
the invalid `instruction` contract or teach Weblate why a protocol response was
rejected.

**Verdict:** necessary but insufficient.

### Hybrid application and proxy hardening

The selected approach:

- shrink the model response to validated Judge evidence;
- derive repair guidance in code;
- use per-seat response, reasoning, streaming, temperature and batch profiles;
- make transport and protocol failures typed and persisted safely;
- let Weblate own bounded retries so spend is visible;
- use adaptive output-aware batching;
- queue unresolved units durably;
- correct and pin the gateway separately.

The implementation plan fixes the initial production profiles rather than
leaving them to the implementer:

| Seat | Response mode | Reasoning | Streaming | Temperature | Batch ceiling |
| --- | --- | --- | ---: | ---: | ---: |
| DeepSeek | `json_object` plus Weblate validation | `thinking.disabled` | true only if its streaming gate passes; otherwise non-streaming must independently pass or the seat is NO-GO | 0 | 2 |
| Qwen | native strict `json_schema` | `enable_thinking=false` | true only if its streaming gate passes; non-streaming is a control arm and currently NO-GO | 0 | 5 |

`max_tokens` is initially omitted to avoid turning leaked reasoning into
`finish_reason=length`. The HCGameLoc Weblate maintainer owns semantic
validation, attempt persistence, bounded retries and batch adaptation. The
Weblate Celery deployment owner owns the durable queue and its periodic
consumer. The named HeroCraft Bifrost service owner owns dedicated aliases,
effective-parameter wire verification, image pinning, buffering and layered
timeouts. The HCGameLoc production operator owns credential rotation and the
production environment. Those three accountable roles must be named in the
rollout ticket before gateway or production mutation begins. Gateway and
provider retries are set to zero so that Weblate remains the single visible
owner of paid repeats.

The streaming decision is binary and seat-specific. For each seat, interleave
30 streaming and 30 non-streaming requests for every production-shaped cell:
clean and controlled-defect workloads at width one and at that seat's ceiling.
Safe requests remain a capability smoke only. Streaming is selected only if it
has zero transport/protocol failures, first-byte p95 below 20 seconds and no
quality regression. Non-streaming may be selected only if it independently
satisfies the same gates after the HCBifrost investigation. Qwen non-streaming
already fails the current evidence and must be requalified after any provider
change. If neither arm passes for a seat, that seat remains NO-GO; results from
the other model cannot admit it.

The retry classes are alternatives, not stackable budgets. One logical batch
has this exact immediate ladder:

1. Send the initial request.
2. If it fails with transport, `429`, `5xx` or a retryable protocol failure,
   repeat it once using the policy for that one terminal class. A second failure
   cannot consume another class's quota, so the same-shape ceiling is two calls.
3. If the result is still invalid and the batch contains more than one unit,
   send each unit once at width one, with no further immediate retry.
4. A failed width-one opinion becomes deferred. It is never relabelled or sent
   to the other model.

A deferred pass starts after 900 seconds and treats each item as a width-one
logical request with the same initial-plus-one-class-retry ceiling. Backoff uses
full jitter up to a 24-hour ceiling and moves the row to `slow` after five
consecutive failed passes. `slow` is a visibility state, not a lifetime retry
cap: the queue is eventual by design and never silently gives up a live unit.

Spend is rate-bounded instead. For an ordinary run, the retry bucket has
capacity `ceil(initial_planned_provider_calls × 0.2)` and never refills during
that run. A deferred tick selects at most 200 seat-unit opinions, calculates a
fresh bucket as `ceil(selected_initial_calls × 0.2)`, and receives no additional
refill until the next scheduled tick. An empty bucket leaves the item deferred.
Thus lifetime calls are not finitely capped, but one outage has a fixed maximum
spend per run and per periodic tick, while exponential backoff bounds the rate
for repeatedly failing units.

**Verdict:** addresses every confirmed mechanism without weakening verdict
safety or silently borrowing another model's opinion.

## Current decision: NO-GO

The current LiteLLM pair does not satisfy the admission criteria below:

- the 82-unit French run produced 66 of 82 raw DeepSeek failures and 28 of 82
  raw Qwen failures;
- 24 units ended with both seats unparsed;
- DeepSeek still reports reasoning tokens after the disable request;
- the exact gateway owner of the 30-second reset has not been confirmed;
- credentials exposed during the earlier environment incident have not been
  rotated.

The four successful controls on 2026-08-28 prove that both aliases can answer.
They do not overturn the NO-GO status or satisfy a stability gate.

## Production admission criteria

A successful HTTP call or a usable collegium result is not sufficient. Each
seat must pass independently.

Before production switch:

1. A proxy capability smoke sends 30 identical safe requests to each dedicated
   alias with zero resets, empty responses or malformed envelopes. This smoke
   is necessary but cannot admit a production profile.
2. Qwen reports zero reasoning tokens with `enable_thinking=false`.
3. DeepSeek reports zero reasoning tokens with `thinking.disabled`; otherwise
   the alias is not admitted.
4. The dev envelope test interleaves streaming and non-streaming cells at width
   1 and the intended seat ceiling on matched clean and controlled-defect units.
   Every cell receives 30 baseline requests without retries; a separately
   reported recovery arm enables bounded retries.
5. The existing `ru→zh_Hans` and `en→fr` quality corpora run three repeats per
   profile.
6. Each seat reaches at least 99% terminal parse coverage; no unit ends with
   both seats unparsed; false flags remain at or below 10%; critical recall is
   not worse than the OpenRouter control.
7. First-byte latency, retry multiplier, reasoning tokens and cost are reported
   separately rather than folded into one success rate.

The gates use these fixed samples and formulas:

- Proxy capability: 30 identical safe logical requests per seat.
- Envelope: 10 matched units (five clean and five controlled defects), width 1
  and the intended production ceiling, three repeats per seat. Retries are off
  in the baseline and enabled only in a separately reported recovery arm.
- Quality: all 124 `ru→zh_Hans` ground-truth units and a fixed 120-unit
  `en→fr` stratified set containing every critical record, three repeats.
- Raw seat parse rate is parsed initial seat-unit opinions divided by all
  requested seat-unit opinions.
- Terminal seat coverage is seat-unit opinions parsed after the immediate
  ladder and test-window deferred passes divided by all requested non-stale
  seat-unit opinions. A deferred opinion remains a failure until parsed. A
  genuinely changed target/context is reported as stale, excluded from that
  denominator and reselected as a new input.
- Pair false-flag rate is clean records whose final available collegium reports
  any defect divided by all clean records. An all-unparsed clean record is an
  admission failure, not removed from the denominator.
- Union critical recall is ground-truth critical records for which either
  attributed seat reports critical divided by all ground-truth critical
  records. An all-unparsed critical record counts as missed.
- The 99% coverage threshold applies to each seat separately in every repeat,
  never only to their union.

A single parsed seat is never relabelled as the missing seat. A finding from one
seat remains that seat's attributed evidence. A single-seat `pass` cannot
auto-approve a unit: the missing seat remains deferred until it produces its own
opinion or the target becomes stale. This preserves useful defect evidence while
preventing one model's absence from being mistaken for two-seat agreement.

OpenRouter may remain available for an operator-controlled rollback while the
canary runs. It is not an automatic per-request fallback in the selected design.

## Security note

The earlier production environment rewrite incident exposed credentials in an
internal diagnostic output. No values are reproduced here. Rotation of every
credential held in that production `.env`, including LiteLLM and OpenRouter
keys, remains a prerequisite to the final provider switch and is included in
the implementation plan.

Attempt diagnostics must contain no credential, prompt, translation, raw
response or reasoning text. Request and batch identities use domain-separated
HMACs rather than unkeyed hashes of user text.

## Local evidence

- `weblate/trans/judge.py`
- `weblate/trans/judge_prompts/verdict.txt`
- `weblate/trans/models/judge.py`
- `docs/llm-first/measurements/2026-08-26-litellm-stage0-compatibility.md`
- `docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`
- `docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md`
- `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md`
- `docs/llm-first/plans/2026-08-27-judge-reliability-hardening.md`
- commit `082ec67` — model-generated repair instruction contract
- commit `841d6eb` — narrow `"None"` sentinel normalization

## External sources

- LiteLLM, Structured Outputs:
  <https://docs.litellm.ai/docs/completion/json_mode>
- LiteLLM, Router and retry precedence:
  <https://docs.litellm.ai/docs/routing>
- LiteLLM, provider fallbacks:
  <https://docs.litellm.ai/docs/proxy/reliability>
- Qwen, Structured output and supported model series:
  <https://help.aliyun.com/en/model-studio/qwen-structured-output>
- QwenCloud, Structured output:
  <https://docs.qwencloud.com/developer-guides/text-generation/structured-output>
- DeepSeek, Chat Completions request and streaming contract:
  <https://api-docs.deepseek.com/api/create-chat-completion/>
- DeepSeek, JSON Output:
  <https://api-docs.deepseek.com/guides/json_mode/>
- DeepSeek, Thinking Mode:
  <https://api-docs.deepseek.com/guides/thinking_mode/>
