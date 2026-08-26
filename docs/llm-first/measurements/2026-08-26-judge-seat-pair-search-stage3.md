# Stage 3 smoke: reasoning-off LiteLLM candidates

**Date:** 2026-08-26. **Status:** stopped. No candidate passed the Stage 3 gate.

## Method

The smoke used the production `weblate.trans.judge.request_verdicts` path with
`JUDGE_REASONING_EFFORT="none"`, the fixed LiteLLM endpoint, and production
batch width of five segments. The client retried one failed batch as normal.
No production Weblate records were read or written during this run.

The zh_Hans slice contained all seven sealed critical units plus eight
known-clean units:

```text
24130, 24180, 24181, 24182, 24207, 24208, 24221,
24131, 24132, 24133, 24134, 24135, 24136, 24137, 24138
```

The en->fr slice contained 15 construction-labelled critical mutations from
`analysis/data/nfg-ui-fr-golden.json`: two number losses, six antonym swaps,
six negated infinitives, and one inserted negation. An injected critical counts
as missed unless the returned maximum error severity is `critical`, matching the
sealed zh scorer's `missed_crit` definition.

The seven-route roster includes every Stage 0 survivor:
`qwen3.8-max`, `QWEN3.7-plus`, `deepseek-v4-pro`,
`deepseek-ai/deepseek-v4-flash`, `atlas/deepseek-v4-pro-0813`,
`atlas_glm-5.1`, and `Kimi K2.6`. Stage 1 could not prove the Atlas DeepSeek
route is an alias, so it must be measured independently.

## Results

| model | zh elapsed | zh unparsed | zh missed critical | fr elapsed | fr unparsed | fr missed critical | result |
|---|---:|---:|---:|---:|---:|---:|---|
| `qwen3.8-max` | 22.79 s | 0/15 | 6/7 | 26.97 s | 0/15 | 1/15 | disqualified: zh recall |
| `QWEN3.7-plus` | 48.13 s | 5/15 | 6/7 | 32.93 s | 0/15 | 1/15 | disqualified: unparsed, zh recall |
| `deepseek-v4-pro` | 91.78 s | 15/15 | 7/7 | 92.35 s | 10/15 | 10/15 | disqualified: unparsed |
| `deepseek-ai/deepseek-v4-flash` | 92.31 s | 15/15 | 7/7 | 69.96 s | 5/15 | 6/15 | disqualified: unparsed |
| `atlas/deepseek-v4-pro-0813` | 93.27 s | 15/15 | 7/7 | 91.86 s | 15/15 | 15/15 | disqualified: unparsed |
| `atlas_glm-5.1` | 91.87 s | 15/15 | 7/7 | not run | - | - | disqualified after zh unparsed batches |
| `Kimi K2.6` | 68.89 s | 15/15 | 7/7 | 87.85 s | 10/15 | 10/15 | disqualified: unparsed |

The `deepseek-v4-pro` mapping smoke immediately before Stage 3 had one accepted
five-segment call at 12.06 s after a preceding 30.8 s reset. The Stage 3 sample
shows that one success is not enough to establish batch reliability.

## Decision

Stage 3's explicit gate disqualifies a candidate on any unparsed batch or a
missed planted critical. It leaves no candidate eligible for Stage 4. Therefore
no repeated per-model runs, offline pair search, confirmation run, or seat
configuration change is justified. The LiteLLM provider and reasoning-off
mapping stay available, but the judge seats remain on OpenRouter.
