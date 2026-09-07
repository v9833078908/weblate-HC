# Unparsed attribution across both judge providers

**Date:** 2026-09-01. **Status:** completed, read-only.

## Purpose

Two questions, answered from the full production record rather than from one run:
did the LiteLLM stabilization work remove unparsed, and is the claim "OpenRouter
produced zero unparsed, LiteLLM produces unparsed" true? The answer decides whether
migrating to LiteLLM trades stability away and what a durable "zero unparsed"
guarantee still requires.

Read-only: aggregates over `JudgeVerdict`, `JudgeRequestAttempt` and
`JudgeRun.configuration_snapshot` inside `hcgameloc-weblate-1`. No unit, verdict, run
or setting was modified. Probe preserved at
`analysis/probes/judge-unparsed-attribution.py`.

## Environment

- Production checkout `ffb6693`, container `hcgameloc-weblate-1`, healthy.
- `WEBLATE_JUDGE_BASE_URL=https://hcbifrost.herocraft.com/litellm/v1`,
  `WEBLATE_JUDGE_MODEL_SEAT_1=deepseek-v4-pro`,
  `WEBLATE_JUDGE_MODEL_SEAT_2=atlas/qwen3.8-max`,
  `WEBLATE_JUDGE_DEFERRAL_ENABLED=0`, `WEBLATE_JUDGE_MAY_APPROVE=0`.
- Verdict window 2026-08-24 10:23 to 2026-09-01 11:58 UTC, 4721 verdicts.
- `JudgeRequestAttempt` rows exist only from 2026-08-31 12:15 UTC (507 rows), so
  1670 of 4721 verdicts carry per-call diagnostics and the rest carry none. Every
  pre-08-31 unparsed verdict is uncaused in the record, not proven benign.

## Result 1: the stabilization work did help, and the record shows where

The mechanisms specified by
`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md` are in the code and
are what makes this attribution possible at all:

| Plan task | State in code | Evidence |
|---|---|---|
| 1. Judge output contract, `instruction` decoupled from parsing | implemented | `JudgeResult.instruction` is historical-only (`weblate/trans/judge.py:166-168`), tolerated in a segment (`:711`), written empty (`weblate/trans/judge_loop.py:275`) |
| 2. Typed diagnostics and safe provenance | implemented | closed `FAILURE_KINDS` (`weblate/trans/judge.py:49-67`), `JudgeRequestAttempt` with `provider`, `endpoint_fingerprint`, `failure_kind` (`weblate/trans/models/judge.py:231-308`) |
| 3. Immutable per-seat request profiles | implemented | `JudgeSeatProfile` (`weblate/trans/judge.py:99-119`), `_resolve_profile` with `inherit` (`:358-460`) |
| 4. Streaming and gateway transport | implemented, later extended | `_read_sse` with absolute and idle bounds (`weblate/trans/judge.py:901-1001`, idle at `:912-914`), then per-seat deadlines from `docs/llm-first/plans/2026-09-01-judge-per-seat-deadline.md` |
| 5. Classified recovery and adaptive batching | implemented | kind-driven retries (`weblate/trans/judge.py:1520-1559`), halving and recovery (`:1416-1432`), width-one isolation (`:1561-1593`) |
| 6. Durable deferred queue | in code, **off in production** | `JudgeDeferral`, `_sync_deferral` (`weblate/trans/judge_loop.py:747-834`), `drain_judge_deferrals` (`:1636-1681`), `WEBLATE_JUDGE_DEFERRAL_ENABLED=0` |
| 7. Credential rotation | outside this measurement | security runbook, not observable from the database |

The plan's document header still says "approved, not started", which is stale for
tasks 1-5. What the plan did **not** cover is stated in Result 4.

## Result 2: the premise about providers is inverted over lifetime

| Model | Provider | Parsed | Unparsed | Rate |
|---|---|---:|---:|---:|
| `deepseek/deepseek-v4-pro` | OpenRouter | 2285 | **335** | 12.79% |
| `qwen/qwen3-235b-a22b-2507` | OpenRouter | 1386 | **421** | 23.30% |
| `deepseek-v4-pro` | LiteLLM | 97 | **0** | 0.00% |
| `atlas/qwen3.8-max` | LiteLLM | 96 | 51 | 34.69% |
| `weblate-judge-deepseek-v4-pro` | rollout alias, never in service | 0 | 50 | 100.00% |

OpenRouter carries 756 unparsed verdicts. Zero unparsed on OpenRouter was one day,
2026-08-31: 1376 verdicts, zero unparsed. By day:

| Day | Model | Unparsed |
|---|---|---:|
| 2026-08-24 | `deepseek/deepseek-v4-pro` | 5 |
| 2026-08-27 | `deepseek/deepseek-v4-pro` | 5 |
| 2026-08-27 | `qwen/qwen3-235b-a22b-2507` | 30 |
| 2026-08-28 | `deepseek/deepseek-v4-pro` | 325 |
| 2026-08-28 | `qwen/qwen3-235b-a22b-2507` | 391 |
| 2026-09-01 | `atlas/qwen3.8-max` | 51 |
| 2026-09-01 | `weblate-judge-deepseek-v4-pro` | 50 |

The comparison is not clean in either direction: the pairs differ by model
(`qwen/qwen3-235b-a22b-2507` versus `atlas/qwen3.8-max`), batch width and deadline,
so R3 forbids reading the difference as a provider result
(`docs/llm-first/vision/llm-first-product-architecture.md:674`).

## Result 3: LiteLLM's 51 are a ten-minute rollout window, not LiteLLM behaviour

Every non-parsed attempt on a LiteLLM model name, lifetime:

| n | Model | Profile fp | Kind | HTTP | Transport ok | Batch |
|---:|---|---|---|---:|---|---:|
| 50 | `atlas/qwen3.8-max` | `b7849441` | `http-other` | 400 | no | 1 |
| 1 | `atlas/qwen3.8-max` | `78928314` | `http-auth` | 401 | no | 1 |
| 1 | `atlas/qwen3.8-max` | `fe5a5c77` | `deadline` | 200 | yes | 1 |

Both failing fingerprints existed only during rollout: `b7849441` from 05:59 to
06:00, `78928314` at 06:05. All 76 HTTP 400 attempts ever recorded (50 on
`atlas/qwen3.8-max`, 26 on the alias `weblate-judge-deepseek-v4-pro`) fall between
05:59 and 06:09 on 2026-09-01, and every one records `provider="openrouter"` while
carrying a LiteLLM model name: the serving endpoint was OpenRouter's while the
configured models were LiteLLM's. That is deployment correction 1 of
`docs/llm-first/measurements/2026-09-01-02-judge-seat-parallelism-production.md`
(production `.env` lacked `WEBLATE_JUDGE_BASE_URL`), not a proxy or model property.

Per running profile:

| Model | Profile fp | Attempts | Parsed | Unparsed | Rate | Window (UTC) |
|---|---|---:|---:|---:|---:|---|
| `atlas/qwen3.8-max` | `fe5a5c77` | 98 | 97 | 1 | 1.02% | 06:11-11:58 |
| `deepseek-v4-pro` | `37a37f8a` | 24 | 24 | 0 | 0.00% | 06:16-08:00 |
| `deepseek-v4-pro` | `54dfd18e` | 26 | 26 | 0 | 0.00% | 07:26-11:58 |

The one remaining failure was the old shared deadline:

```text
created_at   2026-09-01 06:29:04 UTC     seat 2   atlas/qwen3.8-max (litellm)
batch_size   1                           attempt 0
http_status  200                         transport_succeeded True
first_byte   1769 ms                     elapsed 120095 ms
response     345355 bytes                shape stream:raw
run          494ff4a1  started 06:15:42   configuration_snapshot request_deadline: ABSENT
```

That run's snapshot has no `request_deadline` key at all, while the 07:25 and 07:47
runs carry `request_deadline: [120.0, 150.0]`. The attempt ran under the old shared
120 s bound and was truncated at 120.095 s mid-stream, after a 1.8 s first byte and
345 KB delivered. It is a truncation at the bound, not a refusal or a malformed
answer, and after seat 2 was raised to 150 s the seat recorded 97 attempts with zero
unparsed.

Only four attempts ever returned HTTP 200 with an unusable body: two `invalid-json`
and two `invalid-segment`, all `provider="openrouter"`. LiteLLM has never returned a
malformed envelope or a wrong segment count in the diagnostic era.

## Result 4: what the stabilization plan did not cover

101 of the 102 diagnosable unparsed verdicts came from a refused request - HTTP 400
or 401 - not from a model answer. Those refusals say nothing about a translation, yet
they were written as `JudgeVerdict.unparsed=True` against 101 units and the verdict
card renders them as "The latest judge answer was not parsed".

The plan specified fail-fast for `401/403` only
(`docs/llm-first/plans/2026-08-28-litellm-judge-stabilization.md:262`). That is
implemented: `http-auth` raises immediately (`weblate/trans/judge.py:1516-1517`).
`http-other` has no equivalent rule, so the 05:59 run **completed** after 50
consecutive HTTP 400 batches across two request rounds of one run (`48bfbd72`),
writing 50 unparsed verdicts instead of stopping after the first refusal.

## Conclusions

1. The migration does not trade stability away. Under the running profiles LiteLLM is
   at 0 unparsed for seat 1 (97 verdicts) and 1 in 98 attempts for seat 2, and that
   one is the pre-fix deadline. OpenRouter's lifetime rates are 12.79% and 23.30%.
2. Unparsed today is dominated by configuration, not by models. The remaining
   engineering gap is a run that keeps paying for a request the endpoint refuses
   every time, and records the refusal as an opinion about a translation.
3. **The durable queue must not be enabled before that gap is closed.**
   `_sync_deferral` queues on `result.unparsed` regardless of failure kind
   (`weblate/trans/judge_loop.py:779-795`), so with `JUDGE_DEFERRAL_ENABLED=1` the
   05:59 incident would have become a scheduled retry loop against an endpoint that
   refuses the request every time, ending in 51 `slow` rows rather than 51 terminal
   ones.
4. The residual runtime risk is response length, not correctness. Seat 2 streamed
   345 KB for a single string. The mitigations that exist - the 150 s seat deadline,
   adaptive halving, one unparsed retry round - are bounds, not a guarantee, and no
   measurement yet bounds seat 2's response-size distribution.
5. The 756 OpenRouter unparsed verdicts predate attempt recording, so their kinds
   cannot be named from the database. The 716 from 2026-08-28 are already attributed
   to a model and alias misconfiguration in
   `docs/llm-first/research/2026-08-28-litellm-judge-stability-root-cause.md`.
