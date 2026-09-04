# Judge segment-count cardinality canary

Date: 2026-09-04.

## Goal

Check whether the production LiteLLM seat-2 route accepts exact JSON Schema
array cardinality before deploying
``docs/llm-first/plans/2026-09-04-judge-segment-count-recovery.md``.

This is a compatibility smoke test, not an estimate of the post-deployment
failure rate.

## Method

The canary ran inside ``hcgameloc-weblate-1`` against the unchanged production
profile:

- seat 2, ``atlas/qwen3.8-max`` through LiteLLM;
- streaming ``json_schema`` response;
- ``extra_body.enable_thinking=false``;
- batch size 1;
- ``segments.minItems == segments.maxItems == 1`` injected process-locally.

The sample was ten units whose final seat-2 result in JudgeRun
``36a5be4a-b2d8-42a7-a934-23eb76800737`` was ``segment-count``. The probe called
the raw judge transport and parser directly. It did not call
``run_judge_batch``, persist an attempt, invoke fallback, create a verdict or
change a translation.

## Result

| Metric | Result |
|---|---:|
| Requests | 10 |
| HTTP 200 | 10 |
| ``finish_reason=stop`` | 10 |
| Parsed with one segment | 10 |
| ``segment-count`` | 0 |
| Other failures | 0 |
| Latency p50 / p95 | 3.714 s / 4.814 s |
| TTFB p50 / p95 | 1.200 s / 1.828 s |
| Prompt / completion tokens | 36,999 / 1,346 |
| Reported cost | unavailable |

The before/after row counts were identical:

| Table | Before | After |
|---|---:|---:|
| ``JudgeRequestAttempt`` | 1,315 | 1,315 |
| ``JudgeVerdict`` | 4,663 | 4,663 |
| ``LLMUsageLog`` | 7,704 | 7,704 |
| ``JudgeRunUnit`` | 1,691 | 1,691 |

The selected units' targets and states were also unchanged.

## Decision

The production LiteLLM route accepts the bounded schema, and the ten targeted
responses complied with it. The pre-deployment compatibility gate passes.

This result does not prove a zero production failure rate and does not exercise
the fallback branch. Effectiveness still has to be measured on the next full
run after a separately approved deployment.
