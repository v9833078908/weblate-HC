# LiteLLM preflight: failed at the judge seat, provider contract passed

**Date:** 2026-08-26. **Status:** measured; **preflight FAILED**.
**Covers:** the local arm of Task 7 of
`docs/llm-first/plans/2026-08-23-litellm-provider-and-judge-endpoint.md`.
**Outcome under Task 7.4:** stop, do not substitute a model, schedule an R3
eval. No seat was swapped to make this pass.
**Probes:** `analysis/probes/litellm-preflight-probe.py`,
`analysis/probes/litellm-seat-stability.py`,
`analysis/probes/litellm-seat-diagnostic.py`,
`analysis/probes/litellm-client-control.py`.

All numbers come from real, paid requests to
`https://hcbifrost.herocraft.com/litellm/v1` against the merged code on
`feat/litellm-provider-and-judge-endpoint`. The proxy answered directly; no VPN
was needed.

## Verdict

The machinery provider and the configurable endpoint are sound. The two-seat
judge is not runnable on this proxy as configured, because the configured
second seat does not exist here and an available qwen candidate fails.

|Task 7 step|Result|
|---|---|
|1. deploy to the shared dev stack and rebuild|**not performed as written**|
|2. real suggestion per route model|**partial** - the provider contract holds, the per-project route enumeration was not done|
|3. judge payload per seat, then a two-seat batch|**FAILED** on seat 2|
|4. on failure stop, no substitution, record, start R3|applied|
|5. mark the phase-4 increment implemented|**not done**, correctly|

## What passed

Every check in `litellm-preflight-probe.py` covering the provider and the
endpoint passed:

* the service defaults to the corporate proxy, is slugged `litellm`, is named
  **LiteLLM**, and trusts only `hcbifrost.herocraft.com`;
* per-language routing resolves exactly (`ja`), by base language (`de_AT` ->
  `de`) and through the `*` fallback (`ko`);
* the chat payload omits the OpenRouter-only `provider` field, and the live
  proxy accepts that exact payload with `temperature = 0` (HTTP 200, 25 prompt
  tokens, 204 completion tokens; the proxy reports no `cost`);
* the judge's chat-completions URL is derived from `JUDGE_BASE_URL` instead of
  being hardcoded, and the hostname resolves to the `litellm` provider identity
  while OpenRouter still resolves to `openrouter`;
* the reasoning-effort gate refuses a LiteLLM base URL combined with a
  non-empty `JUDGE_REASONING_EFFORT`, and a clean configuration passes.

`_record_usage` was confirmed to swallow its own failure: before the throwaway
database was migrated, `trans_llmusagelog` did not exist, the insert raised, and
the verdicts were still returned intact. After migrating, 27 usage rows were
written across the runs, including `reasoning_tokens`, so accounting works end
to end.

## Why the preflight failed

Seat identity, taken from the committed `dev-docker/docker-compose.yml`:

|Configured seat|On this proxy|
|---|---|
|`deepseek/deepseek-v4-pro`|present as `deepseek-v4-pro` - the same model under the identifier this proxy exposes|
|`qwen/qwen3-235b-a22b-2507`|**absent entirely**|

Seat 1 is an identifier translation and it passes: 5/5 parseable batches at the
production `JUDGE_BATCH_SIZE` of 5, both planted defects caught every run, no
clean string ever flagged, 15.7-23.7 s.

Seat 2 is absent, and no claim is made here about which model would be equivalent.
An available qwen candidate, `qwen3.8-max`, fails: it produced an unparsed batch on its first two-unit run and, through the
product's own HTTP path, failed 1 of 3 runs with no HTTP status after 30.5 s.
The other qwen models the proxy exposes fail too -
`qwen3.6-flash` returns an array form and drops a segment, `QWEN3.7-plus` and
`Qwen3.5-plus` return HTTP 400 from Dashscope over `max_tokens`, which the judge
payload never sets.

Under Task 7.4 that ends the preflight. Nothing was substituted; the isolated
stack still carries `qwen3.8-max` as the recorded failure.

## Four failures with no HTTP status at about 30.5 seconds

Through the product's own path, four batches across two models failed at 30601,
30637, 30661 and 30525 ms. `_post_batch` swallows the transport exception, so
what is recorded is `status=None` - no HTTP status was ever received.

What is established: the cut is not ours. `_post_batch` passes
`JUDGE_REQUEST_TIMEOUT` of 120 s and `stream_validated_url` forwards it to httpx
without a default of its own, so nothing in our configuration expires at 30 s.

What is **not** established: who cuts it. The exception type was not captured on
the product path, and the `urllib` arm that did report `ConnectionResetError` is
the same arm shown below to be unreliable, so it cannot carry the attribution.
The proxy, an intermediate network element and the client library all remain
candidates. Capturing the exception type in `_post_batch`, or reproducing the
failure against a second network path, would settle it.

Two consequences hold regardless of the cause:

1. [INFERENCE] Seat latency behaves as a correctness property: every observed
   failure clustered just past 30 s while every success stayed under it, so a
   slow model looks unusable as a seat. Seat 1 peaked at 23.7 s at a batch of 5,
   which leaves little margin; lowering `JUDGE_BATCH_SIZE` buys headroom if the
   prompt grows. This is an inference from 4 failures and 13 successes, not a
   measured threshold.
2. The comment on `RoutedLiteLLMTranslation.request_timeout = 55` asserts "the
   proxy's hard ~60s gateway timeout (nginx 504 past that)". No 504 was observed
   and nothing survived to 55 s, so that comment is unverified by this run. The
   value itself stays harmless.

## A client artifact that nearly produced a wrong conclusion

The first stability harness posted with `urllib` and reported
`ConnectionResetError` for most models: `qwen3.8-max` 5/5, `bytedance/doubao`
3/3, `atlas/deepseek-v4-pro-0813` 3/3, `Kimi K2.7` 2/3, `atlas_glm-5.1` 2/5,
while `deepseek-v4-pro` never reset. The per-model consistency read like
unprovisioned model groups, and that reading was wrong.

Re-running the same models through `request_verdicts`, the path the product
actually uses, contradicted it: `Kimi K2.7` went 3/3 clean and `atlas_glm-5.1`
3/3 clean at a two-unit batch.

Any future claim about model availability on this proxy must be made through
`request_verdicts` or the machinery class, never through a hand-rolled client.

## Input for the R3 eval

This is candidate data, not an adopted seat. Rule R3 voids a measurement when
the prompt or the model changes, so replacing seat 2 requires an eval on the
S&T2 corpus first. Measured through `request_verdicts` at a batch of 5, five
runs each, over a fixture holding two planted defects (250 -> 150, a dropped
`{0}` placeholder) and three clean strings:

|Model|Clean batches|Unparsed|Missed defects|Clean strings flagged|Latency|
|---|---|---|---|---|---|
|`deepseek-v4-pro` (seat 1)|5/5|0|0|0|15.7-23.7 s|
|`atlas_glm-5.1`|5/5|0|0|0|9.5-23.8 s|
|`Kimi K2.7`|2/5|3|0|0|17.6-21.4 s when it answered|

`atlas_glm-5.1` matched seat 1's severity vector
`['none', 'critical', 'none', 'critical', 'none']` on every successful run and
is from a different model family, which is what a second seat needs. It is the
obvious candidate to put through R3, and nothing more than a candidate here.

Models that fail the schema regardless of client, with the reason measured:

|Model|Why|
|---|---|
|`MiniMax-M3`|3/3 unparsed: a bare array inside a ```json fence, not `{"segments": [...]}`|
|`Kimi K2.6`|puts the whole answer in `reasoning_content`, leaves `content` empty|
|`deepseek-ai/deepseek-v3.2`|2/5 unparsed at a three-unit batch: drops a segment|
|`MiniMax-M2.7`, `MiniMax-M2.5-highspeed`|return empty content|
|`mimo-v2.5-pro`|replies with `{"0": {...}}`, keyed by index|

The proxy passes `response_format` through without enforcing it, so strict-schema
compliance is a property of each model and has to be measured per model.

## Not covered

The containerised dev deployment did not run. Task 7.1 asks for a rebuild of the
shared stack on port 3001; that stack is bound to the main checkout, which has
diverged from `origin/main` and holds another session's uncommitted work, so it
was left untouched. An isolated stack was prepared instead and its configuration
validated - project `litellm-preflight` on `127.0.0.1:3003`, database `5438`,
maildev `1082`, bound to this worktree, `RoutedLiteLLMTranslation` registered -
but the container was killed repeatedly by the OOM killer while building its own
dependencies (`oom=true`, 3 restarts, 7 attempts at `lxml`). Docker's 7.65 GiB
is shared with three other Weblate stacks.

Unverified as a result: the `litellm` service appearing and being configurable
on `/machinery/<project>/`, a suggestion requested from the web UI, a judge run
driven from the UI, and the per-language route enumeration Task 7.2 asks for
against a real target project.
