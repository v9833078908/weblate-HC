# Judge seat latency: the thinking toggle never reached the models

**Date:** 2026-09-03. **Status:** measured; seat 2 changed, seat 1 deliberately
not. **Probes:** `dev-docker/data/thinking_probe*.py` (throwaway, not tracked).

A producer reported that the judge run on `need-for-greed/orders/ru` was very
slow and asked for the highest-leverage safe speed-up. It is one setting on one
seat: `atlas/qwen3.8-max` was spending 92% of its output tokens on reasoning
because no reasoning-off request the product can send ever reached it.

## Result in one sentence

On the corporate LiteLLM proxy the thinking toggle is a **passthrough**
question, not an effort level: only `extra_body.enable_thinking=false` reaches
the upstream deployment, and applying it to seat 2 cut that seat's output from
945 to 72 tokens and its latency from 34.8 s to 2.9 s per string with
equal-or-better defect recall, while the same shape on seat 1 is fast but
loses recall and was therefore not adopted.

## Baseline: the run the producer was watching

| field | value |
|---|---|
| `JudgeRun` | `f9423ea4-f364-40d6-93cd-4f8b048fed56` |
| scope | `need-for-greed/orders/ru` (`Need for Greed/Orders — Русский`) |
| selection / cap | `NOT has:judge`, 2,000 |
| started / finished | 2026-09-03 07:40:17 / 08:39:15 UTC |
| wall clock | **58 min 58 s for 102 strings** (34.7 s per string) |
| producer summary | 102 evaluated, 73 nothing blocking, 10 minor, 11 major, 8 critical, 0 repaired, 0 unparsed |

Per-seat request profile from `JudgeRequestAttempt` for that run:

| seat | model | batch | requests | mean | p50 | p95 | max | seat total | completion | reasoning | share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `deepseek-v4-pro` | 2 | 51 | 20.8 s | 20.8 s | 36.1 s | 39.1 s | 1,060 s | 1,074 | 942 | **87.7%** |
| 2 | `atlas/qwen3.8-max` | 1 | 100 | 29.9 s | 17.8 s | 98.7 s | 130.6 s | **2,992 s** | 1,039 | 991 | **95.3%** |

Both seats run in parallel (`_run_seats`), released by a per-offset
acknowledgement barrier, so the run's wall clock is the slower seat's serial
chain, not the sum. Seat 2 owned it: 2,992 s against seat 1's 1,060 s, which
means seat 1 sat at the barrier for roughly two thirds of the run. Retries were
not a factor - every stored verdict was `attempt = 0` in a single request round.

Latency on both seats is pure decode: 28 ms per token on seat 2, 19 ms on
seat 1, against a first-byte time of about 2 s. Batch width therefore cannot
buy anything here - a wider batch emits the same total tokens - which is why
the tempting `JUDGE_BATCH_SIZE_SEAT_2` change is not the lever.

## Why "off" was not off

The configuration read as if reasoning were already suppressed:

```text
seat 1  reasoning 'thinking.disabled'  payload {'thinking': {'type': 'disabled'}}
seat 2  reasoning ''                   payload {}
```

Seat 2's empty value means "send no reasoning field", so the model was on its
own default, which for Qwen is thinking on. Seat 1 did send a disable field and
still reported 87.7% reasoning tokens - the leak first recorded in
`docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md`,
whose root cause is established here.

`GET /model/info` explains it. All 23 proxy aliases declare their thinking mode
inside `litellm_params`, not per request:

| alias | upstream | declared thinking |
|---|---|---|
| `atlas/qwen3.8-max` | `qwen/qwen3.8-max` | **absent** |
| `deepseek-v4-pro` | `openai/deepseek-ai/deepseek-v4-pro` | **absent** |
| `MiniMax-M2.7-highspeed` | `MiniMax-M2.7-highspeed` | `{"thinking": {"type": "disabled"}}` |
| `Kimi K2.7` | `moonshot/kimi-k2.7` | `{"thinking": {"type": "enabled"}}` |

Both judge aliases are OpenAI-compatible passthroughs to
`https://api.atlascloud.ai/v1` (`custom_llm_provider` `custom_openai` and
`openai`) and neither lists any reasoning parameter in
`supported_openai_params`. The `qwen3.8-max` alias that the 2026-08-27
complement smoke measured with a working `enable_thinking=false` **no longer
exists**; `atlas/qwen3.8-max` replaced it, which is why the value that worked
then returns HTTP 500 now.

## Which request shape actually arrives

Seat 2, two units, one request per cell, rate limits retried:

| request shape | status | wall | completion | reasoning |
|---|---|---|---:|---:|
| baseline (no field) | 200, 200 | 10.2, 13.1 s | 319, 428 | 291, 397 |
| `thinking: {type: disabled}` | 200, 200 | 6.5, 14.8 s | 123, 453 | 95, 423 |
| `enable_thinking: false` | **500, 500** | 5.3, 3.0 s | - | - |
| `chat_template_kwargs` | **500, 500** | 5.3, 3.0 s | - | - |
| `reasoning_effort: none` | 200, 200 | 12.4, 19.2 s | 383, 524 | 348, 494 |
| `extra_body: {chat_template_kwargs: …}` | 200, 200 | 15.9, 21.8 s | 493, 713 | 465, 675 |
| **`extra_body: {enable_thinking: false}`** | **200, 200** | **2.1, 1.9 s** | **32, 34** | **0, 0** |

Three outcome classes, and the middle one is the trap: a rejected field is
loud, a **dropped** field is silent. `thinking` is accepted and eaten, so a
seat configured "off" looks configured and behaves as if it were not. Only the
`extra_body` form drives reasoning tokens to zero.

An earlier three-request cell had suggested `thinking.disabled` cut tokens 42%.
The paired A/B below refutes it: that was variance. This model emits 32 to 3,432
completion tokens on the *same* unit and prompt at temperature 0, so no
three-sample cell on this endpoint can carry a claim.

`docs/llm-first/research/2026-08-28-litellm-judge-stability-root-cause.md`
already noted that Game Pulse translates its Qwen `[no-think]` suffix into
`extra_body.enable_thinking=false` against this same proxy. That note was
correct and was not acted on.

## Does it still judge? Seat 2: yes

Paired and interleaved per unit, 8 units (1 stored `reject`, 3 `flag`,
4 `pass`), 2 repeats per arm, 16 requests per arm, compared against the verdict
the baseline run stored for the same unit and seat.

| arm | parsed | mean completion | mean reasoning | share | mean wall | matches stored |
|---|---|---:|---:|---:|---:|---:|
| baseline | 15/16 | 945.1 | 870.3 | 92.1% | **34.8 s** | 11/15 |
| `extra_body` no-think | **16/16** | **71.8** | **0.0** | **0%** | **2.9 s** | 12/16 |

Per unit, the interesting rows:

| unit | stored | baseline | `extra_body` no-think |
|---|---|---|---|
| 141944 | `reject` | `reject`, `reject` | `reject`, `reject` |
| 141811 | `flag` | `pass`, `flag` | `flag`, `pass` |
| 141815 | `flag` | **unparsed (150.2 s)**, `pass` | `reject`, `reject` |
| 141817 | `flag` | `pass`, `pass` | `flag`, `pass` |
| 4 clean | `pass` | `pass` ×8 | `pass` ×8 |

No-think is better on the two units that matter: `141815` and `141817` are
stored majors that the thinking arm missed in every attempt, and no-think
flagged both. It false-flagged no clean string in 8 attempts, and it was the
only arm that parsed every request - the baseline's single failure was a
150.2 s reply that blew the envelope. Both arms flip verdicts between repeats on
`141811` and `141817`; that non-determinism is the model's, present either way,
and is not created by this change.

## Does it still judge? Seat 1: no

Same design on seat 1, 10 units (3 stored `reject`, 3 `flag`, 4 `pass`),
20 requests per arm.

| arm | parsed | mean completion | mean reasoning | share | mean wall | matches stored |
|---|---|---:|---:|---:|---:|---:|
| baseline | 20/20 | 628.7 | 549.1 | 87.3% | 14.4 s | **13/20** |
| `extra_body` no-think | 20/20 | 59.8 | 0.0 | 0% | **3.8 s** | **9/20** |

The speed is there and the recall is not. No-think turned six stored detections
into `pass`, including both attempts on `141823` (`flag`) and on `141817`
(`reject`), which the thinking arm caught. Clean strings stayed clean in both
arms. DeepSeek is evidently doing its detection inside the reasoning trace, so
its reasoning is load-bearing and stays on.

This is also why the leak on seat 1 is now a documented no-op rather than a bug
to fix: the effective mode is thinking on, that is the mode with the better
recall, and the setting is changed to `""` so the configuration states it.

## What changed

- `weblate/trans/judge.py`: `extra_body.enable_thinking=false` added to the
  LiteLLM reasoning allowlist, which is now the single constant
  `_LITELLM_REASONING_VALUES` instead of the same literal set written at the
  primary and fallback resolvers; `_reasoning_payload` maps it to
  `{"extra_body": {"enable_thinking": False}}`.
- `dev-docker/docker-compose.yml` and `deploy/environment.example`: seat 2 to
  `extra_body.enable_thinking=false`, seat 1 to `""` with the measured reason.
- Batch widths, deadlines, response formats, streaming, temperature,
  `max_tokens` and the prompt are untouched.

`docs/admin/config.rst` now documents the LiteLLM value set, which it did not
before: the setting was described only as an OpenRouter effort level, so an
operator had no way to learn that the accepted values differ by provider or
that an accepted value can be silently dropped.

## Verification: one run through the product path

`JudgeRun 296064e3-957f-4805-9fdc-ff3ce8767ce4`, nine strings of
`need-for-greed/orders/ru` (2 stored `reject`, 3 `flag`, 4 `pass`), launched
through `AutoTranslate(mode="judge")` after the dev stack was recreated.

| seat | requests | mean | max | seat total | mean completion | mean reasoning | share | parsed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 `deepseek-v4-pro` | 5 | 17.4 s | 29.1 s | 87 s | 957 | 828 | 86.5% | 5/5 |
| 2 `atlas/qwen3.8-max` | 9 | **3.0 s** | **7.3 s** | **27 s** | **62** | **0** | **0%** | 9/9 |

Seat 2 went from 29.9 s to 3.0 s per string, a 10x cut, and its reasoning
share is zero on every request. The predicted reversal happened: seat 1 now
owns the critical path at 87 s against seat 2's 27 s. Producer result: 9
evaluated, 5 with no blocking concern, 2 major not fixed, 2 critical held,
0 unparsed. The four clean strings all stayed `pass`, the stored critical was
held again, `141815` escalated from `flag` to `reject`, and `141811`/`141817`
landed on `pass` - the same two units that flip between repeats in either arm.

Wall clock was 121.3 s for 9 strings (13.5 s per string) against the baseline
run's 34.7 s per string, so 2.6x on a deliberately defect-heavy slice where
four of nine strings also went through a repair round. On a
representative mix the baseline run's shape implies roughly 3x, bounded now by
seat 1. Going further is a seat-1 model choice question, not a settings
question.

## Limits - read before quoting any number

- **Verdict counts are n = 2 per unit per arm on a single component and
  language pair.** They are enough to show that no-think does not silently
  stop judging, and to prefer no-think on seat 2. They are not a recall rate,
  and the flip noise on `141811` and `141817` means one repeat could have
  reversed either arm's score.
- **The comparison target is a stored verdict, not ground truth.** "Matches
  stored" measures agreement with one earlier thinking-arm answer from the same
  non-deterministic model, so it understates no-think whenever the stored
  verdict was itself wrong. Unit `141815` is exactly that case.
- **Nothing here is a golden-set evaluation.** Adopting no-think on seat 2 is
  justified as a strict improvement in latency, cost and parse rate at
  non-inferior observed recall; a recall *claim* needs
  `analysis/data/col4-judge-golden.json` and the registered A/B design.
- **The proxy rate-limited the probes.** Cells after an HTTP 500 answered 429
  until a backoff was added, and probe v2's later rows are void for that
  reason. Only the retried runs are quoted.
- **`extra_body` support is a property of the current aliases.** The alias that
  accepted a top-level `enable_thinking` was removed once already; a future
  alias revision can change the answer, and `resolve_judge_alias` hashes the
  alias configuration into `profile_fingerprint` so such a change invalidates
  cached verdicts rather than silently altering them.
- **This change invalidates the verdict cache.** `profile_fingerprint` includes
  `reasoning`, and `_cached_verdict` requires a match on every seat, so
  re-judging already-judged strings re-pays both seats. A `NOT has:judge`
  selection is unaffected because it never selects them.

## Evidence sources

- `JudgeRun`, `JudgeVerdict` and `JudgeRequestAttempt` rows for
  `f9423ea4-f364-40d6-93cd-4f8b048fed56` in the dev database.
- `GET /model/info` on `https://hcbifrost.herocraft.com/litellm/v1` for all 23
  aliases and for both judge aliases individually.
- Production code paths: `_reasoning_payload`, `_payload`, `_post_batch`,
  `_parse_reply`, `_resolve_profile` in `weblate/trans/judge.py`;
  `_run_seats`, `_request_identity`, `_cached_verdict` in
  `weblate/trans/judge_loop.py`.
- Prior records this supersedes or explains:
  `docs/llm-first/measurements/2026-08-27-litellm-seat1-reasoning-leak-and-unparsed-rate.md`,
  `docs/llm-first/measurements/2026-08-27-litellm-complement-smoke.md`,
  `docs/llm-first/research/2026-08-28-litellm-judge-stability-root-cause.md`.
