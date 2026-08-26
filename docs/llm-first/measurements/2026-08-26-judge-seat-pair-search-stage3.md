# Stage 3 smoke: reasoning-off LiteLLM candidates

**Date:** 2026-08-26. **Status:** superseded the same day. This run stopped
with no candidate through the gate, and that conclusion no longer stands: the
disqualifying signal was proxy load at the hour of the run. Read
`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md` first;
this file is kept for its per-seat numbers and the split-gate re-score.

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

## Re-score under the split gate (added 2026-08-26)

The table above stands: the transport numbers and the sealed-truth `missed_crit`
are what the run produced. What changed afterwards is the reading of
`missed_crit`, because the seven sealed critical labels were reviewed against
the human artifacts in
`docs/llm-first/reviews/2026-08-26-zh-critical-label-revision.md`. Six of the
seven defects are real; only `24221` survives as critical, `24130` lacks
evidence either way, and the rest are major.

`missed_crit` charges a candidate both for missing a defect and for finding it
and grading it lower. The split gate separates them. Verdicts are persisted in
`analysis/data/st2-stage3-verdicts.json` and re-scored by
`analysis/probes/st2-stage3-rescore.py`, which imports the gate logic from
`analysis/probes/st2-zh-score.py`. No judge request was repeated.

| model | unparsed | missed_crit | missed_defect | sev_under | sev_over |
|---|---:|---:|---:|---:|---:|
| `qwen3.8-max` | 0/15 | 6/7 | **1/6** | 1 | 1 |
| `QWEN3.7-plus` | 5/15 | 6/7 | 5/6 | 0 | 0 |
| `deepseek-v4-pro` | 15/15 | 7/7 | 6/6 | 0 | 0 |
| `deepseek-ai/deepseek-v4-flash` | 15/15 | 7/7 | 6/6 | 0 | 0 |
| `atlas/deepseek-v4-pro-0813` | 15/15 | 7/7 | 6/6 | 0 | 0 |
| `atlas_glm-5.1` | 15/15 | 7/7 | 6/6 | 0 | 0 |
| `Kimi K2.6` | 15/15 | 7/7 | 6/6 | 0 | 0 |

`qwen3.8-max` is the only candidate the split gate moves. It found five of the
six in-gate defects, described each mechanism correctly, and failed on one:
`24208`, where it passed a target whose rendered word order is broken. Its two
calibration entries are `24221` graded major instead of critical, and `24207`
graded critical on a defect that is major. Every other candidate still fails on
transport, and their `missed_defect` is a consequence of unparsed batches rather
than of judgment.

As a control, the same gate was run on the committed arm-H files. The incumbent
`qwen3-235b-a22b-2507` scores `missed_defect=0/6` with `sev_over=5`: its
published `missed_crit=0/7` came from grading nearly everything critical, not
from precision. `deepseek-v4-pro` on the same arm scores `missed_defect=5/6`.
That is the complementarity gap the pair search set out to close, and it is
visible in the split numbers where `missed_crit` hid it.

### What this does and does not authorise

It does not clear `qwen3.8-max`. One unpatched recall miss on a six-unit gate is
a weak result from a single unrepeated run, and a 15-unit slice cannot separate
skill from luck. It does mean the Stage 3 stop was recorded against a metric
that overstated the failure, and that `qwen3.8-max` is the one route where a
repeat run could change the answer.

Deciding that requires Stage 4 approval: repeated runs cost real requests, and
R3 forbids reusing these numbers for a differently configured judge.

## The unparsed batches are a gateway timeout, not a model property

Per-batch transport timings were extracted from the captured judge logs after
the re-score. They separate perfectly:

| outcome | batches | elapsed range |
|---|---:|---|
| parsed | 17 | 5.1 s - 27.8 s |
| reset (`status=None`) | 19 | 30.4 s - 32.1 s |

No batch failed under 30 s and none succeeded over 30 s; the gap between the
slowest success and the fastest reset is 2.7 s. The client deadline was
`JUDGE_REQUEST_DEADLINE = 300.0`, so the ceiling is server-side. This is the
same ~30.5 s reset Stage 0 recorded for doubao.

Per route, at `JUDGE_BATCH_SIZE = 5`:

| model | parsed batches | reset batches |
|---|---|---|
| `qwen3.8-max` | 6 (5.1-11.2 s) | 0 |
| `QWEN3.7-plus` | 5 (7.7-12.0 s) | 1 |
| `atlas_glm-5.1` | 2 (8.9-11.3 s) | 3 |
| `deepseek-ai/deepseek-v4-flash` | 2 (11.6-27.8 s) | 4 |
| `deepseek-v4-pro` | 2 (12.1-22.5 s) | 6, plus one 429 |
| `atlas/deepseek-v4-pro-0813` | 0 | 6 |

Every route except `atlas/deepseek-v4-pro-0813` returned at least one clean,
schema-valid five-segment batch. The DeepSeek and GLM routes are therefore not
schema-incompatible and not incapable of judging: at this batch width they
usually do not finish before the proxy closes the connection.

This invalidates the reading of the `unparsed` column for five of the seven
routes. Their `missed_defect = 6/6` is a consequence of receiving no verdict,
not of grading a verdict wrongly. Only `qwen3.8-max` was measured on judgment
throughout, and `QWEN3.7-plus` nearly so.

It does **not** rescue those routes. A judge that cannot return a verdict at the
production batch width is unusable as configured, and lowering
`JUDGE_BATCH_SIZE` is a configuration change that invalidates every number here
under R3, including the OpenRouter seat numbers this search is measured against.
What changes is the diagnosis: the blocker is latency at batch width, which is
testable, rather than model quality, which would not be.

## Decision

Stage 3's explicit gate disqualifies a candidate on any unparsed batch or a
missed planted critical. It leaves no candidate eligible for Stage 4. Therefore
no repeated per-model runs, offline pair search, confirmation run, or seat
configuration change is justified. The LiteLLM provider and reasoning-off
mapping stay available, but the judge seats remain on OpenRouter.

The split-gate re-score does not overturn this, and the timeout finding does not
either. `qwen3.8-max` still fails the recall gate, on one unit instead of six.
The other routes remain unusable at the production batch width.

What both findings do is narrow the conclusion. Stage 3 cannot support "no model
on this proxy can judge": five routes never got the chance to be judged on
judgment. It supports only the narrower and still sufficient claim that **no
two-seat judge can be assembled from this proxy as currently configured**, which
is what the seat search asked. The seats stay on OpenRouter.

## Overturned on the same day

`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`
repeated today's judge payload against `deepseek-v4-pro` twelve times and
recorded zero resets, where this run recorded six of eight. The disqualifying
signal is proxy load at the hour of the run, not a property of the route.

The decision above does not stand. DeepSeek is a live LiteLLM candidate again,
and the blocker moves to `weblate/trans/judge.py:638`, which does not retry a
connection reset.
