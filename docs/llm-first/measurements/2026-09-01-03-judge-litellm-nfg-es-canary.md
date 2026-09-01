# LiteLLM judge canary on need-for-greed/ui/es

**Date:** 2026-09-01. **Status:** passed, read-only.

## Purpose

Establish whether the two production judge seats, already served by the
corporate LiteLLM proxy, judge a second component and language pair without any
unparsed result, and whether the two seats really overlap in time. The scope is
deliberately the backlog left by the failed 2026-08-28 OpenRouter run on this
exact translation, so the canary answers "does LiteLLM judge what OpenRouter
could not" rather than re-judging strings that already have an opinion.

## Read-only construction

The canary called `run_judge_batch` directly with `writable_ids=set()`,
`use_cache=False` and both seats. This is the entry point the deferral drain
uses, whose docstring records that it never changes a translation
(`weblate/trans/judge_loop.py:1571-1581`).

The producer wrapper `AutoTranslate.process_judge` was deliberately **not**
used: it projects verdict severity onto unit state at
`weblate/trans/autotranslate.py:844-845`, which is a production data write and
is not what this canary measures. `JUDGE_MAY_APPROVE=0` forbids approval but
does not forbid that projection, so avoiding the wrapper is the only way to keep
the run strictly read-only.

Written rows: `JudgeVerdict`, `JudgeRequestAttempt`, `LLMUsageLog`,
`JudgeAdaptiveState`. No unit target, no unit state, no repair, no approval.

## Scope

```text
environment: production (checkout ffb6693, container healthy)
project/component: need-for-greed/ui
translation_id: 303
translation_path: /projects/need-for-greed/ui/es/
language pair: en -> es
units in translation: 466 (465 translated)
units with no parsed verdict before the run: 275
unit_ids: 25 exact IDs from the 356401-356435 range
usage_log_start_pk: 5737
attempt_start_pk: 469
judge run (model-call identity): 5d4f7727-0aa4-47c9-9006-7e8f628babf7
start_utc: 2026-09-01T11:50:22Z
elapsed: 464.154 s
```

Running profiles were the deployed ones, unchanged by the canary:

| Seat | Model route | Provider | Format | Reasoning | Stream | Batch | Deadline |
|---|---|---|---|---|---|---:|---:|
| 1 | `deepseek-v4-pro` | litellm | `json_schema` | `thinking.disabled` | true | 2 | 120 s |
| 2 | `atlas/qwen3.8-max` | litellm | `json_schema` | none | true | 1 | 150 s |

## Result

| Acceptance check | Result |
|---|---|
| Attempts | 38 |
| HTTP status | 38 x 200 |
| Failure kinds | none, 38 blank |
| Parsed attempts | 38/38 |
| Transport success | 38/38 |
| Retry ordinals | all zero; no retry consumed |
| Finish reasons | 38 x `stop`; no truncation |
| Provider recorded | `litellm` x 38 |
| Attempts by seat | seat 1: 13; seat 2: 25 |
| Batch sizes | 26 x 1, 12 x 2 |
| Verdict rows | 50 over 25 units |
| Verdicts per unit per seat | exactly one |
| Unparsed verdicts | 0 |
| Units left without a parsed verdict | 0 |
| Model verdicts | 50 x `pass` |
| Max severity | 48 `none`, 2 `minor` |
| Usage rows | 38 (13 seat 1, 25 seat 2) |
| Reported cost | none; the proxy returned no cost value on any row |
| Circuit state after run | closed on both seats, `failure_streak=0` |
| Units changed in target or state | 0 |

## Latency

| Seat | n | first byte min/med/p95 | elapsed min/med/p95/max | Deadline |
|---|---:|---|---|---:|
| 1 | 13 | 1352 / 2623 / 5705 ms | 6726 / 11882 / 64812 / 64812 ms | 120 s |
| 2 | 25 | 1222 / 1660 / 2746 ms | 4837 / 10309 / 39159 / 59206 ms | 150 s |

First-byte p95 is 5.705 s on seat 1 and 2.746 s on seat 2, against the 20 s
envelope target. The slowest single request was 64.812 s on seat 1, 54% of its
absolute deadline. No request approached its deadline.

## Parallelism

```text
seat 1 window: 11:50:33.338Z -> 11:58:05.012Z
seat 2 window: 11:50:32.214Z -> 11:58:03.340Z
combined wall time: 452.797 s
summed seat spans:  902.799 s
ratio: 50.15% (threshold 75%)
```

The two seats overlapped for effectively the whole run, reproducing the
2026-09-01 col4 result of 50.02-52.63% on a different project, component and
language pair.

## Interpretation and limits

- The seats are stable on this route today: zero failure kinds and zero retries
  over 38 consecutive paid calls.
- This measures availability and parseability only. It is **not** a quality
  measurement. 25 of 25 units returned `pass` on a component carrying 69 failing
  checks; the selected units may simply be clean, but no LiteLLM seat pair has
  ever been scored against ground truth, so this canary cannot say whether the
  verdicts are correct. See
  `docs/llm-first/plans/2026-08-26-judge-provider-failover.md:44-52`.
- Cost attribution through the corporate proxy is blind: every one of the 38
  usage rows carries `cost_usd=None`. OpenRouter reports cost; LiteLLM does not.
- The 275-unit backlog on this translation, left by the 2026-08-28 OpenRouter
  failure, is untouched. Clearing it is a writing run and needs its own
  decision.

## Historical context from the same database

Lifetime unparsed rate per judge model on production, read at the same time:

| Model | Provider | Verdicts | Unparsed | Rate |
|---|---|---:|---:|---:|
| `deepseek-v4-pro` | litellm | 72 | 0 | 0.00% |
| `atlas/qwen3.8-max` | litellm | 122 | 51 | 41.80% |
| `weblate-judge-deepseek-v4-pro` | litellm | 50 | 50 | 100.00% |
| `deepseek/deepseek-v4-pro` | openrouter | 2620 | 335 | 12.79% |
| `qwen/qwen3-235b-a22b-2507` | openrouter | 1807 | 421 | 23.30% |

The two poor LiteLLM figures are rollout misconfiguration, not model or
transport instability, and the attempt table names the cause: the 50
`weblate-judge-deepseek-v4-pro` rows are 2 `http-auth` plus 26 `http-other`
attempts against an alias the team key cannot access, and the 50 `http-other`
attempts on `atlas/qwen3.8-max` are LiteLLM model names sent to the default
OpenRouter endpoint before `WEBLATE_JUDGE_BASE_URL` was set. Both causes are
recorded in
`docs/llm-first/measurements/2026-09-01-02-judge-seat-parallelism-production.md:16-26`.
Seat 2 also carries exactly one `deadline` attempt, the 120.095 s event that
produced the 150 s deadline.

The single worst production episode belongs to OpenRouter, not LiteLLM: on
2026-08-28 the `need-for-greed/ui` run reached 69.74% unparsed on
`deepseek/deepseek-v4-pro` and 83.91% on `qwen/qwen3-235b-a22b-2507`, leaving
275 of 466 es units unjudged. On 2026-08-31 the same OpenRouter pair returned
688 + 688 verdicts with zero unparsed. This variance is the reason an OpenRouter
fallback must be scoped to availability only and must never be assumed to be the
more reliable endpoint.

## Verdict

**Passed.** Both seats served `need-for-greed/ui/es` through LiteLLM with zero
unparsed results, zero retries, true two-seat overlap, first-byte p95 far inside
the envelope, and no production data change.
