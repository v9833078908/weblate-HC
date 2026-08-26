# The LiteLLM resets are a latency race, not a model or payload property

**Date:** 2026-08-26. **Supersedes the reading of:**
`docs/llm-first/measurements/2026-08-26-judge-seat-pair-search-stage3.md`, whose
`unparsed` column disqualified five of seven routes.

## Why this was measured

Stage 3 disqualified every DeepSeek and GLM route on unparsed batches. The
per-batch timings then showed all 19 failures clustered at 30.4-32.1 s and all
17 successes under 27.8 s, which pointed at a server-side ceiling rather than
at judgment quality.

The `cathedral` localizer on the office VPS (`/home/dev01/localization`,
container `cathedral_server`) runs against **the same host**,
`https://hcbifrost.herocraft.com/litellm/v1`, with `ACTIVE_AI_PROVIDER=litellm`,
`LITELLM_MODEL=deepseek-ai/deepseek-v4-flash` and
`LITELLM_REVIEW_MODEL=QWEN3.7-plus`. It runs DeepSeek in production where our
Stage 3 called DeepSeek unusable, so one of the two readings had to be wrong.

## What cathedral does differently

Read from `/app/litellm_api_helper.py` and `/app/.env` in the running container.

|  | judge (`weblate/trans/judge.py`) | cathedral |
|---|---|---|
| `response_format` | `json_schema`, `strict: true` | never sent |
| `max_tokens` | never sent | always, 8192-32768 per model |
| request timeout | 300 s flat | 120-420 s per model, +30 s per retry |
| retries | 1, only on `403`/`429` | up to 5, exponential backoff |
| gateway timeout | not handled | `504`/`524` retried separately |
| streaming | no | no |

Two candidate causes follow: the strict schema, and the missing retry.

## The strict schema is not the cause

One request per cell, same prompt, same five segments, same transport.

| model | arm | status | elapsed | parsed |
|---|---|---|---|---|
| `deepseek-v4-pro` | judge today | 200 | 8.9 s | 5/5 |
| `deepseek-v4-pro` | no schema | reset | 30.8 s | - |
| `deepseek-v4-pro` | schema + `max_tokens` | 200 | 15.8 s | 5/5 |
| `deepseek-v4-pro` | cathedral shape | 200 | 25.8 s | 5/5 |
| `qwen3.8-max` | judge today | 200 | 18.1 s | 5/5 |
| `qwen3.8-max` | no schema | 200 | 18.6 s | 5/5 |
| `qwen3.8-max` | schema + `max_tokens` | reset | 30.5 s | - |
| `qwen3.8-max` | cathedral shape | 200 | 21.8 s | 5/5 |

The arm that resets differs per model, and the arm without the schema resets
too. Payload shape does not predict the outcome. `deepseek-ai/deepseek-v4-flash`
returned `403` on all four arms: our key is not entitled to the route cathedral
uses as its default, which is a key scope difference, not a transport one.

The first row is the important one. **`deepseek-v4-pro` answered today's judge
payload, strict schema included, with five valid segments in 8.9 s.** Stage 0's
admission of the route was right and Stage 3's disqualification of it was not
about capability.

## The reset rate, held at today's judge payload

12 repeats per route, judge payload unchanged.

| model | ok | reset | latency of successes | reset rate |
|---|---:|---:|---|---:|
| `deepseek-v4-pro` | 11 | 0 | 10.7-18.6 s | 0% (one `429`) |
| `qwen3.8-max` | 8 | 4 | 15.7-30.6 s | 33% |

The ceiling is near 30.5 s but is not a hard wall: one `qwen3.8-max` request
returned at 30.6 s, later than three of the four resets. A route resets when its
latency approaches the ceiling, so the reset rate is a function of how fast the
route answers under the load of the moment.

That is what separates the two models. `deepseek-v4-pro` finishes with room to
spare and never raced the ceiling in 12 attempts. `qwen3.8-max` runs at
15.7-30.6 s and loses the race a third of the time.

Assuming independence, a single transport retry would take `qwen3.8-max` from
33% to 11%, and two from 33% to 4%. Independence is the optimistic case: if the
cause is proxy load, failures correlate in time and a retry recovers less.

## What this overturns

Stage 3 ran all six routes in one session and recorded resets on five of them,
including `deepseek-v4-pro` at 6 of 8 batches. Today the same route, the same
payload and the same batch width reset 0 of 12 times. The variable that changed
is not in our code. Stage 3 measured the proxy under load at that hour and
attributed the result to the models.

So Stage 3 cannot support its conclusion. It stated that no two-seat judge can
be assembled from this proxy as configured; what it actually observed was one
bad hour on a shared gateway. `deepseek-v4-pro` is a viable LiteLLM seat on
today's evidence, and `qwen3.8-max` is viable behind a transport retry.

This does not yet name a pair. Latency is not judgment: none of these requests
was scored against ground truth. It removes the transport objection that stopped
the search, and reinstates DeepSeek as a candidate.

## What is missing before a seat changes

1. `weblate/trans/judge.py:638` retries only on `403`/`429`. A connection reset
   arrives as `status_code is None` and breaks out of the loop immediately.
   Cathedral's equivalent retries the reset. This is a transport-layer change
   that leaves the prompt, the model and the batch width untouched, so R3 does
   not invalidate prior numbers, but it must be written and tested before any
   scored run.
2. A scored run. The Stage 3 gate needs recall against the sealed corpora, which
   these probes do not measure.
3. The reset rate is a single 12-repeat sample per route at one hour. If the
   cause is load, it varies by time of day and needs a repeat before the retry
   budget is fixed.

## Reproduce

```sh
uv run python analysis/probes/litellm-strict-schema-ab.py
uv run python analysis/probes/litellm-reset-rate.py 12 deepseek-v4-pro qwen3.8-max
```

Both read `LITELLM_API_KEY` from the environment, falling back to
`deploy/.env.local`. Neither writes anything.
