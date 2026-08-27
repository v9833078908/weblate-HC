# LiteLLM French judge run: DeepSeek reasoning leakage and unparsed rate

**Date:** 2026-08-27. **Status:** measured production-path run, root cause not
established.

This record covers one completed judge run on `col4/common/fr`. It explains why
the producer reported 24 unjudged strings even though both configured seats
processed every selected unit, and records the bounded evidence behind the
current DeepSeek investigation. It does not select a replacement model or
change judge configuration.

## Result in one sentence

With `deepseek-v4-pro` and `qwen3.8-max` behind the corporate LiteLLM proxy,
the 82-unit French run completed in 23 minutes, but 24 units (29.3%) had no
parseable answer from either seat; DeepSeek supplied 66 of the 94 raw
unparsed seat verdicts and continued to consume reasoning tokens despite the
configured disable-thinking request.

## Run identity and observed configuration

| field | value |
|---|---|
| producer `JudgeRun` | `5053210d-19dd-42bb-8f2e-fa45696f969c` |
| Celery task | `54251d87-0c26-466c-a55f-13fa2f2e68ec` |
| verdict round | `d546a135-b787-446b-ade2-518e5b6427c6` |
| scope | `col4/common/fr` (`CoL4/common — Французский`) |
| selection | `NOT has:judge` |
| mode and cap | `judge`, 2,000 |
| started | 2026-08-27 17:13:07 UTC |
| finished | 2026-08-27 17:36:15 UTC |
| terminal state | `completed` |
| LiteLLM base URL | `https://hcbifrost.herocraft.com/litellm/v1` |
| seat 1 | `deepseek-v4-pro` |
| seat 2 | `qwen3.8-max` |
| `JUDGE_BATCH_SIZE` | 2 |
| `JUDGE_REASONING_EFFORT` | `none` |
| `JUDGE_TRANSPORT_RETRIES` | 1 |

The run was initially asked to stop, but the persistent `JudgeRun` record
ended in `completed` and contains all expected 164 raw seat verdicts. The
database state, not the later Celery revoke acknowledgement, is the
authoritative result.

The settings in the table were read from the local instance while analysing
the completed run. They are not a configuration snapshot stored on `JudgeRun`.

## Producer result and raw seat evidence

The producer summary is internally complete:

| final outcome | units |
|---|---:|
| passed | 56 |
| minor | 0 |
| major | 1 |
| critical | 1 |
| unparsed | 24 |
| **total** | **82** |

Each of the 82 units has one raw `JudgeVerdict` per seat:

| seat | model | raw verdicts | unparsed | unparsed rate |
|---|---|---:|---:|---:|
| 1 | `deepseek-v4-pro` | 82 | 66 | **80.5%** |
| 2 | `qwen3.8-max` | 82 | 28 | **34.1%** |
| both | — | 164 | 94 | 57.3% |

The following matrix is more useful than the aggregate total. “Parsed” means
that the reply passed the production parser, not that its judgment was
correct.

| DeepSeek seat | Qwen seat | units | final effect |
|---|---|---:|---|
| parsed | parsed | 12 | collegium has two opinions |
| parsed | unparsed | 4 | DeepSeek supplies the only opinion |
| unparsed | parsed | 42 | Qwen supplies the only opinion |
| unparsed | unparsed | 24 | final outcome is `unparsed` |

Thus Qwen supplied the only parseable answer for 42 units, while DeepSeek did
so for four. Neither seat supplied an answer for the remaining 24.

## Why 94 raw losses became 24 unjudged units

`collegium_verdict()` deliberately ignores an unparsed seat whenever another
seat parsed. It returns the strictest parsed opinion; it returns an unparsed
row only when *every* seat failed. This makes an unparsed result a
transport/protocol outcome, never a “pass” opinion.

That behavior explains the producer result:

- 70 raw unparsed verdicts were masked by the other seat's parseable response.
- 24 double failures became the 24 final unparsed units.
- The producer warning, “24 strings were left unjudged (the judge did not
  answer),” is therefore accurate.

There is no automatic second producer run for these units. The in-request
logic retries a transport failure once, and retries a `403` or `429` once; a
reply rejected by `_parse_reply()` is marked unparsed without another request.
After the completed run, an unparsed-only round leaves
`judge_active_severity` null. Since `has:judge` requires a non-null active
severity, the 24 units remain eligible for a later `NOT has:judge` selection
(subject to any intervening changes).

## Reasoning-disable evidence

When `JUDGE_REASONING_EFFORT` is `none`, the LiteLLM request builder sends a
model-specific disable request:

- `deepseek-v4-pro`: `{"thinking": {"type": "disabled"}}`
- `qwen3.8-max`: `{"enable_thinking": false}`

`LLMUsageLog` has no foreign key to `JudgeRun`, so the following evidence is
scoped by `project_slug=col4`, operation `judge`, and the producer's exact
start-to-finish time window. It is strong evidence for the observed request
profile, but it is not a per-verdict join.

| model | usage rows | rows with reasoning tokens | mean | range |
|---|---:|---:|---:|---:|
| `deepseek-v4-pro` | 37 | **37** | 722.95 | 201–1,649 |
| `qwen3.8-max` | 41 | **0** | 0 | 0 |

For every returned DeepSeek response represented in those usage records, the
proxy reported non-zero `completion_tokens_details.reasoning_tokens`. Qwen
reported zero in every corresponding row. A direct reply inspection also found
DeepSeek `reasoning_content` while the disable request was present.

The current DeepSeek disable-thinking profile therefore does **not** suppress
reported reasoning on this LiteLLM endpoint. The evidence does not identify
whether the ignored setting is in the request mapping, the proxy, or the
upstream model. It also does not establish that reasoning tokens cause the
unparsed results: the two facts coincide in this run but require a controlled
intervention to establish causality.

Usage records cannot calculate the historic reset rate. `_write_llm_usage()`
runs only after a response payload arrives with usage data, so a connection
reset without a payload creates no usage row. The relevant worker logs had
rotated before this analysis; raw `JudgeVerdict` rows are the authoritative
outcome count.

## Bounded follow-up probes

Two small live-path probes narrowed the failure mode without changing the
configured run:

1. Reducing the batch width from two to one removed the observed transport
   resets in that sample: 0 resets in 8 one-unit attempts, compared with 2
   resets in 14 two-unit attempts. It was not a cure: 4 of the 8 one-unit
   replies still became unparsed under the production parser.
2. A previously unparsed DeepSeek request for unit `181889` was replayed with
   the same request data and returned strict-schema JSON with
   `finish_reason="stop"`. The production parser accepted it.

The second observation rules out a deterministic failure of that unit/request
combination. It does not provide a general failure probability or identify the
variable that changed between attempts. The first observation suggests that
batch width affects gateway losses, but the samples are too small to define a
stable rate and parser failures remain at width one.

## Conclusion and next bounded work

The completed run is not usable as a reliable production judge configuration:
the pair left 29.3% of selected units unjudged, and the DeepSeek seat was
unparsed on 80.5% of its raw verdicts. Qwen rescued many of those failures but
could not rescue 24 double failures.

The evidence supports investigating the present DeepSeek request path before
replacing the model:

1. Verify the vendor-specific DeepSeek thinking-disable schema that the
   corporate proxy actually accepts.
2. Test a bounded `max_tokens` intervention against the same request shape,
   recording both parser acceptance and reported reasoning tokens.
3. If the model requires a different setting than Qwen, evaluate a
   per-seat reasoning configuration. A global `none` setting cannot express
   thinking enabled for DeepSeek and disabled for Qwen.

Seat parallelism is outside this measurement. It cannot turn an unparsable
reply into an opinion, and its effect on proxy contention and failure rate has
not been measured.

## Evidence sources

- `JudgeRun`, `JudgeRunUnit`, and `JudgeVerdict` rows in the local development
  database, filtered by the identifiers above.
- `LLMUsageLog` rows scoped by project, operation, and run time window as
  described above.
- Production behavior in `weblate/trans/judge.py`,
  `weblate/trans/models/judge.py`, `weblate/trans/autotranslate.py`, and
  `weblate/utils/search.py`.
- Earlier controlled LiteLLM batch measurements:
  `docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md`.
