# LiteLLM stage 0: which models can judge at all, and the ~30.5 s reset

**Date:** 2026-08-26. **Status:** measured.
**Covers:** stage 0 and stage 1 of
`docs/llm-first/plans/2026-08-26-judge-seat-pair-search.md`.
**Probes:** `analysis/probes/litellm-model-compat.py`,
`analysis/probes/litellm-cut-diagnostic.py`.

Every result below comes from the production path: the payload is built with the
judge's own `_segment`, `_response_schema` and `_load_prompt`, posted through
`_post_batch` (httpx via `stream_validated_url`), and accepted only when the
judge's own `_parse_reply` returns verdicts for all five segments. A batch of 5
is used because that is `JUDGE_BATCH_SIZE` and because segment dropping does not
appear at a trivial batch size. Segment 1 carries a planted number defect
(250 -> 150), so a model that parses is also checked for actually reading.

## The finding that reframes everything: a time-to-first-byte reset

Requests were failing with no HTTP status at 30.4-32.2 s. `_post_batch` catches
every exception and returns `(None, None)` (`weblate/trans/judge.py:490-501`), so
that failure carried no provenance at all. Re-issuing the identical request
without the blanket except names it:

```text
ReadError <- ReadError <- ConnectionResetError
ReadError('[Errno 54] Connection reset by peer')
after 30.8s, first_byte=None, bytes=0
```

The peer resets the connection after ~30.5 s **during which no byte has
arrived**. Our own limits are nowhere near: the transport timeout is 120 s
(`weblate/trans/judge.py:45`) and the body-read deadline was 300 s.

The same payload with `stream: true` behaves completely differently:

| `qwen3.8-max`, same payload | first byte | total | outcome |
|---|---|---|---|
| `stream: false`, run 1 | never | 30.8 s | `ConnectionResetError` |
| `stream: false`, run 2 | never | 30.7 s | `ConnectionResetError` |
| `stream: true`, run 1 | 8.5 s | **39.0 s** | HTTP 200, 101756 bytes |
| `stream: true`, run 2 | 7.1 s | **43.8 s** | HTTP 200, 121521 bytes |

A model that failed 8 times out of 8 non-streaming completes in 39-44 s when
streaming - well past the 30.5 s mark. So the budget is on **silence**, not on
total duration, and the judge's hardcoded `"stream": False`
(`weblate/trans/judge.py:542`) is what exposes us to it.

This single mechanism explains three earlier mysteries at once, and corrects a
retraction made in `docs/llm-first/measurements/2026-08-26-litellm-preflight.md`:

1. The four preflight failures with `status=None` at ~30.5 s.
2. The `ConnectionResetError`s from the `urllib` harness that were written off as
   a client artifact. They were the same reset, at the same 30.6 s
   (30601/30637/30661 ms in the stability log). The variable was never the
   client library; it was response latency.
3. Why `qwen3.8-max` looked hopeless while its own vendor documents a hybrid
   thinking mode.

## Turning reasoning off changes availability by a factor of six

The judge cannot do this today: `validate_request_settings` refuses a non-empty
`JUDGE_REASONING_EFFORT` for LiteLLM hosts (`weblate/trans/judge.py:130-147`) and
the payload carries no vendor toggle. Measured by sending one anyway.

Qwen uses `enable_thinking`, not the `thinking.type` spelling used by DeepSeek,
GLM and Kimi. The first pass sent the wrong vendor's spelling, which silently
left reasoning on and mismeasured the whole family.

| Model | default (reasoning on) | reasoning off | latency change |
|---|---|---|---|
| `qwen3.8-max` | 30.7-31.3 s, 1 accept in 12 attempts | **4.4-4.8 s, 3/3** | ~6.5x faster |
| `QWEN3.7-plus` | 27.6-31.9 s, ~50% reset | **5.4-6.8 s, 3/3** | ~4.5x faster |

Both still flag the planted defect as `critical`. Both placements work -
top-level `enable_thinking` and inside `extra_body` - so the proxy forwards
either.

Applied across the pool with each vendor's own spelling:

| Model | Family | Reasoning off: latency | Accepted | Note |
|---|---|---|---|---|
| `qwen3.8-max` | Qwen | 4.4-4.8 s | 3/3 | `enable_thinking: false` |
| `QWEN3.7-plus` | Qwen | 5.4-6.8 s | 3/3 | `enable_thinking: false` |
| `atlas_glm-5.1` | GLM | 7.4-15.0 s | 3/3 | |
| `deepseek-ai/deepseek-v4-flash` | DeepSeek | 11.1-15.8 s | 3/3 | |
| `deepseek-v4-pro` | DeepSeek | 11.6-13.4 s | 2/3 | one HTTP 429 |
| `Kimi K2.6` | Moonshot | 20.5-24.1 s | 3/3 | toggle barely moved it |
| `bytedance/doubao-seed-2.1-turbo-260628` | ByteDance | 31.3-32.2 s | 0/3 | toggle ineffective; reset every time |
| `MiniMax-M3` | MiniMax | 2.8-12.9 s | 0/3 | content present, parser refuses |

`mimo-v2.5-pro` returns 19-107 byte replies that the parser refuses. Its docs
promise JSON syntax only, not schema conformance, which matches.

`MiniMax-M3` is **not** blocked by LiteLLM issue #38197: its `content` is
populated (734-1414 bytes) and still violates the schema. That is a genuine
incompatibility, unlike the empty-content defect reported for M2.7.

## Default-mode results, for the record

Before any toggle, with the payload exactly as production sends it:

| Model | Accepted | Latency | Note |
|---|---|---|---|
| `atlas_glm-5.1` | yes | 6.9 s | |
| `deepseek-ai/deepseek-v4-flash` | yes | 13.1 s | |
| `Kimi K2.6` | yes | 22.6 s | first attempt |
| `QWEN3.7-plus` | yes | 23.1-27.6 s | only on retry; first attempt reset |
| `deepseek-v4-pro` | yes | 15.2-30.4 s | **one run at 30.4 s, on the edge** |
| `atlas/deepseek-v4-pro-0813` | yes | - | duplicate check still pending |
| `qwen3.8-max` | 1 of 12 | 29.1 s when it lands | otherwise reset |
| `bytedance/doubao-...` | no | reset | `reasoning_split` returns HTTP 500 |
| `MiniMax-M3`, `mimo-v2.5-pro` | no | - | schema refused |

`deepseek-v4-pro` - today's seat 1 - completed one run at 30.4 s and was reset on
another at 30.8 s in a different probe. It sits on the edge of the wall in its
default mode. That is an operational finding independent of any seat change.

`reasoning_split: true` returns HTTP 500 on this proxy for doubao, MiniMax-M3 and
mimo, so that parameter is unsupported here regardless of what MiniMax documents.

## Candidate pool after stage 0

Four families survive, which is what a collegium needs:

- Qwen: `qwen3.8-max`, `QWEN3.7-plus` (reasoning off required)
- GLM: `atlas_glm-5.1`
- DeepSeek: `deepseek-v4-pro`, `deepseek-ai/deepseek-v4-flash`, and
  `atlas/deepseek-v4-pro-0813` pending the duplicate check
- Moonshot: `Kimi K2.6`

Excluded: all MiniMax, `mimo-v2.5-pro` (schema), `bytedance/doubao-...` (reset,
would need streaming), `Qwen3.5-plus` and `qwen3.6-flash` (documented for
`json_object` only, and previously observed returning a bare array and dropping a
segment), `seed-2.1` (unresolved SKU).

## What this costs in product terms

Two changes are now evidence-backed rather than speculative, and both are
payload-level:

1. **Send the vendor thinking toggle for LiteLLM hosts.** Without it the Qwen
   family is unusable and seat 1 runs on the edge of a reset. With it, six
   candidates answer in 4-24 s. The toggle is vendor-specific
   (`enable_thinking` for Qwen, `thinking.type` for the rest), so this is a small
   mapping, not a single field.
2. **Stream the judge request.** This removes the time-to-first-byte budget
   entirely and is the only route to any model that needs more than ~30 s, such
   as doubao. It requires parsing server-sent events, which the judge does not do
   today.

Neither is applied to the product here. The eval can proceed with the toggle
applied in the probe payload, provided the anchor is measured the same way -
which is why the plan re-measures the anchor rather than reusing published
numbers.

## Unmeasured

Turning reasoning off is an availability result, not a quality result. All that
is established is that each surviving model parses and catches one planted number
defect. Whether a non-thinking Qwen judges as well as a thinking one on the S&T2
corpus is exactly what stages 3-4 are for, and it must not be assumed from
latency.
