# LiteLLM judge smoke: who can actually run, and who complements whom

**Date:** 2026-08-27. **Status:** measured, n = 1 - a smoke, not a measurement.
**Probe:** `analysis/probes/litellm-complement-smoke.py`.
**Corpus:** 15 units from the **dev** split of `analysis/data/col4-judge-golden.json`,
ru->fr, interleaved 5 critical / 5 major / 5 clean. The `test` split (433) and
`st2-zh-groundtruth.json` were not touched: both are reserved for decisions, and
exposing them to candidate selection is the post-selection bias the two-stage
design in `docs/llm-first/plans/2026-08-27-judge-set-ab-openrouter-vs-litellm.md`
exists to prevent.

Production path throughout: payload built with the judge's own `_segment`,
`_response_schema` and `_load_prompt`, posted through `_post_batch`, accepted
only when `_parse_reply` admits the whole batch. Three attempts per batch with
linear backoff. Vendor thinking toggle applied per model.

## The finding that reframes the question

At `JUDGE_BATCH_SIZE = 5`, `deepseek-v4-pro` never once completed a batch
containing defect units - **0 of 16** across seven runs and both
configurations - while all-clean batches completed **6 of 7**. That asymmetry,
not an unconditional failure, is the finding. The exact record:

| run | batching | `JUDGE_REASONING_EFFORT` | attempts/batch | batches parsed | failure times |
|---|---|---|---|---|---|
| 1 | homogeneous: criticals, majors, clean | `""` | 2 | **1/3** - the all-clean batch | 30.8, 30.8 s |
| 2 | interleaved: 1 critical + 1 major + 1 clean each | `""` | 3 | 0/3 | 30.6, 30.7, 31.0 s |
| 3 | interleaved | `"none"` | 3 | 0/3 | 31.2, 30.7, 30.5 s |
| 4 | **grouped, controlled** | `""` | 3 | **1/3** - the all-clean batch, 18.8 s | 30.7, 30.8 s |
| 5 | **grouped, controlled** | `"none"` | 3 | **1/3** - the all-clean batch, 29.0 s | 30.9, 30.6 s |
| 6 | **matched, input-controlled** | `""` | 3 | **1/2** - the clean batch, 31.3 s | 30.8 s |
| 6 | **matched, input-controlled** | `"none"` | 3 | **1/2** - the clean batch, 28.7 s | 30.9 s |
| 7 | **matched, order reversed** | `""` | 3 | **0/2** - defect and clean both failed | 31.0, 30.8 s |
| 7 | **matched, order reversed** | `"none"` | 3 | **1/2** - the clean batch, 30.9 s | 30.7 s |

### The controlled comparison

Runs 1-3 cannot separate batch composition from run time: batching, attempt
budget and proxy load all moved between them. Runs 4 and 5 were built to fix
that - **one invocation, one time window, one slice**, batches made homogeneous
so that an all-clean batch of five and a defect-bearing batch of five differ in
how much the model must report and in nothing else.

Both configurations gave the same result inside the same run:

| batch | composition | `""` | `"none"` |
|---|---|---|---|
| 0 | 5 clean | **OK, 18.8 s** | **OK, 29.0 s** |
| 1 | 5 `cyrillic-fragment` | failed ×3, 30.7 s | failed ×3, 30.9 s |
| 2 | mixed defect classes | failed ×3, 30.8 s | failed ×3, 30.6 s |

Four defect-bearing batches failed every attempt while the clean batch completed
twice, with run time controlled. Batch composition and outcome are therefore
associated within a single run, which cross-run comparison could not show. It
still does not isolate why.

Grouped batches still differ in **input**, though: the mutation records are
different base units with different text, so latency could move for that reason
alone. That is what run 6 removes.

### The matched comparison

The corpus derives every mutation from a base unit, so `clean-177682` and
`mut-177682-cyrillic-fragment` share a byte-identical source and a target
differing only by the injection. Run 6 uses that: batch 0 is the clean variant
of five base units, batch 1 the defect variant of the **same five**, one
invocation, `JUDGE_BATCH_SIZE = 5`.

Input is matched to 1.3%: 6 731 against 6 819 characters, 1 967 against
~1 768-1 800 prompt tokens.

| model | batch | input chars | wall | completion tokens | outcome |
|---|---|---|---|---|---|
| `deepseek-v4-pro` `""` | clean | 6 731 | 31.3 s | **1 931** | OK |
| `deepseek-v4-pro` `""` | defect | 6 819 | 30.8 s | - | failed ×3 |
| `deepseek-v4-pro` `"none"` | clean | 6 731 | 28.7 s | **1 955** | OK |
| `deepseek-v4-pro` `"none"` | defect | 6 819 | 30.9 s | - | failed ×3 |
| `qwen3.8-max` `"none"` | clean | 6 731 | 5.6 s | **284** | OK |
| `qwen3.8-max` `"none"` | defect | 6 819 | 9.8 s | **652** | OK |

### Order eliminated, and what survives it

Run 6 sent the clean batch first, so composition was still confounded with
request order: a first-request effect or a load change between the two calls
would explain it equally. Run 7 repeats the same five pairs with the **defect
batch first**.

| model | first batch | second batch |
|---|---|---|
| `deepseek-v4-pro` `""` | defect, 31.0 s, failed ×3 | clean, 30.8 s, **failed ×3** |
| `deepseek-v4-pro` `"none"` | defect, 30.7 s, failed ×3 | clean, 30.9 s, OK, 1 929 tokens |
| `qwen3.8-max` `"none"` | defect, 11.3 s, OK, 620 tokens | clean, 4.1 s, OK, 192 tokens |

Two things follow, and one of them costs me a claim.

**Order does not explain the defect failures.** The defect batch failed as the
*first* request in both configurations, so no warm-up or second-call effect is
doing the work.

**A clean batch is not reliably completable either.** Under `""` the clean batch
failed all three attempts when sent second. So "clean completes, defect does
not" was too strong: clean batches sit **inside** the band and win most of the
time (6 of 7), defect batches sit past it and have never won (0 of 16).

**What is measured is an association, not a cause.** Two facts stand on their
own. First, on byte-matched input the two models differ enormously in output
volume: `deepseek-v4-pro` emits 1 929, 1 931 and 1 955 completion tokens on a
clean batch of five - three measurements across both configurations and both
orders, spread under 1.5% - landing at 28.7-31.3 s, inside the band, while
`qwen3.8-max` emits 192-284 tokens in 4.1-5.6 s on the same input and 620-652
tokens in 9.8-11.3 s on the defect batch. Second, defect batches never
completed for DeepSeek and clean batches usually did.

Those two facts are consistent with output volume driving the reset, and that
reading is the reason the probe logs tokens at all. They do not establish it.
DeepSeek's defect-batch output was **never observed** - a call that does not
return carries no token count - so any statement about how much it would have
emitted is an assumption, not a measurement, and the failure could equally sit
in a path this probe cannot see.

What can be said without overreaching: at batch 5 on this proxy,
`deepseek-v4-pro` is verbose enough on trivial input to land inside the reset
band, and it has not once returned a verdict for a defect-bearing batch. Both
are decision-relevant on their own. Identifying the cause would need a
different instrument - server-side timing, or streaming to observe partial
output - and is out of scope here.

### Correction to the ceiling claim

The clean batch under `""` completed at **31.3 s**. An earlier version of this
file said nothing above 30 s ever returned; that is false. Failures cluster
tightly at 30.6-31.0 s and one success landed at 31.3 s, so the reset is a
**narrow band around ~30-31 s, not a hard cut**. The client is still not the
cause: `JUDGE_REQUEST_TIMEOUT = 120` and the probe's deadline was 300 s.

The earlier "12 of 12 batches at 17-23 s" for this model was measured on short
zh strings, which produce far less output - consistent with everything above.

At `SMOKE_BATCH = 1` the model is a **strong judge**:

| batch size | parsed | defects caught | false flags | wall clock |
|---|---|---|---|---|
| 5 | 3/15 batches, all-clean only | 0 - it judged no defect batch | - | - |
| 1 | 14/15 units | **9/9 of the units it saw** | 0/5 | 509 s |

The one unit it did not judge is `mut-177723-glossary-substituted`, whose three
attempts all hit the ceiling - a transport loss, not a judgment miss. Of the 14
units it did see, it caught every defect and flagged no clean unit.

## Candidates at production batch size, in both available configurations

`JUDGE_REASONING_EFFORT` is a **single global setting**, and on a LiteLLM host
it admits exactly two values (`judge.py:630-640`, `validate_request_settings`):
`""` sends no reasoning field, `"none"` applies
`_litellm_reasoning_disable_payload` - the vendor toggle plus its admitted-model
allowlist. The probe calls that same function, so both columns are real product
configurations rather than hand-built payloads.

| model | `""` (thinking on) | `"none"` (thinking off) |
|---|---|---|
| `qwen3.8-max` | **0/3, reset band, 30.6-31.1 s** | **3/3, 10/10 caught, 0/5 false flag, ~10 s/batch** |
| `atlas_glm-5.1` | 3/3, 10/10, 0/5, 98 s | 3/3, 10/10, 1/5, 68 s |
| `deepseek-v4-pro` | 0/3 interleaved, reset band | 0/3 interleaved, reset band |
| `qwen3.6-flash` | 200, parser refused all three | **not in the allowlist** - production raises |

`qwen3.8-max`'s false-flag rate is **not** a stable 0, and the instability
appeared on the same five clean units in the same configuration:

| run | `qwen3.8-max` `"none"` on the 5 matched clean units |
|---|---|
| 6, clean batch first | flagged `clean-177701` - 1/5 |
| 7, clean batch second | flagged nothing - 0/5 |

Same units, same prompt, same model, same configuration, two runs, two answers.
`deepseek-v4-pro` under `"none"` flagged **that same unit** in run 7, so
`clean-177701` is plausibly arguable rather than a plain error. Either way this
is the flip noise the registered plan budgets n = 3 repeats for: one run cannot
separate a 1/5 false-flag rate from 0/5.

Three things follow, and none of them was visible before both columns existed.

**Thinking is output, and models with it on stayed in the reset band.**
`qwen3.8-max` is the clearest case: dead in every batch with thinking on, 10/10
with it off. This is the same direction as the published "fails 8 of 8 with
reasoning on, holds at 5.6 s with it off", now seen on the col4 corpus at
batch 5. It is a correlation across two toggle states, not an isolated cause.

**No global configuration completes a defect-bearing batch of five with
`deepseek-v4-pro`.** It failed every interleaved batch in both columns. Since
the setting is global, a DeepSeek + Qwen pair is unrunnable in either: `""`
kills Qwen outright, and `"none"` rescues Qwen while leaving DeepSeek at the
ceiling. That pair needs both the per-model reasoning knob the plan lists as a
prerequisite **and** a batch size DeepSeek can finish inside 30 s - which
batch 1 demonstrably is, at 20x the wall clock.

**`atlas_glm-5.1` is the only candidate that survives both columns.** It does
not depend on a product feature that does not exist yet, which is a robustness
property none of the recall numbers show.

## What this says about complementarity

Nothing measurable at batch 5, and that is itself the answer: the anchor
produces no usable rows on defect-bearing batches in either configuration, so
there is no vector to compare against. `qwen3.8-max` alone caught **10/10**
with zero false flags, so any
union containing it is also 10/10, and adding `atlas_glm-5.1` contributes no
catch and one false flag. This repeats the pattern recorded for arm H, where
`qwen` alone equalled the collegium on every gate.

The runnable pair **at batch 5**, needing no new product code, is
`qwen3.8-max` + `atlas_glm-5.1` under `"none"`. On this slice it is strictly
worse than `qwen3.8-max` alone: same recall, one extra false flag, three times
the wall clock. The next section supersedes this as the recommended
configuration: at batch 2 the cross-vendor pair becomes available and is no
longer redundant.

## The batch size is the lever, and batch 2 is where the anchor returns

Everything above holds `JUDGE_BATCH_SIZE=5`. Sweeping it down on the same
15-unit dev slice, same interleaved order, both models under `"none"`:

|batch|model|parsed|caught|false flags|wall clock|attempts/batches|
|---|---|---|---|---|---|---|
|2|`deepseek-v4-pro`|**8/8**|**10/10**|0/5|328.9 s|12/8|
|2|`qwen3.8-max`|8/8|9/10|0/5|34.7 s|8/8|
|3|`deepseek-v4-pro`|2/5|4/10|0/5|513.3 s|13/5|
|3|`qwen3.8-max`|5/5|10/10|0/5|33.6 s|5/5|

At batch 2 the anchor completes every batch, including every defect-bearing
one, at 20.1-30.5 s and 812-1925 completion tokens. At batch 3 three batches
return no HTTP response at all - `transport_failed`, `status None` - after
30.5, 30.6 and 30.9 s. The usable threshold on this proxy therefore sits
between 2 and 3.

What is established here is an intervention, not a mechanism. Batch size was
varied with the model, gateway, prompt, units and reasoning mode held fixed, so
the change in outcome is attributable to batch size. Why a larger batch fails
is not: no proxy timeout setting, gateway configuration or server-side log was
inspected, and a call that returns nothing carries no token count, so the
output length of the failing calls is unknown. The recurring 30-31 s timing of
failures across models is an observed regularity in this dataset and nothing
more.

Batch 2 has little headroom. Four of eight batches needed a transport retry -
12 attempts for 8 batches - and one succeeded at 30.5 s, overlapping the
timings at which batch-3 batches failed. The configured transport retry absorbs
this today. Whether batch 2 stays viable under different proxy conditions is
untested.

**This is the first measured complementarity on the proxy.** `qwen3.8-max`
missed `mut-177708-cyrillic-fragment` at batch 2, which `deepseek-v4-pro`
caught. The same model caught 10/10 at batch 3 and at batch 5, so recall
interacts with batch size in an unmeasured direction. One missed defect out of
ten is n = 1 and cannot carry a recall claim, but it does mean the cross-vendor
union is no longer provably redundant, which is what every batch-5 run had
shown.

Cost of the operating point, extrapolated from per-batch means to the 557-string
production run used as the parallelism plan's basis - arithmetic on this slice,
not a measurement at that scale:

- 279 batches per seat at batch 2, against 112 at batch 5.
- Anchor at 41.1 s per batch: about 3 h 11 m for its seat alone.
- `qwen3.8-max` at 4.3 s per batch: about 20 m.
- Serial seats: roughly 3 h 31 m. With per-batch-barrier seat parallelism the
  anchor still dominates, so roughly 3 h 11 m.
- The incumbent OpenRouter pair did the same run in 38 m 39 s per seat, 77 m
  serial.

So the cross-vendor pair is available on LiteLLM, at roughly 2.5x the wall clock
of today's OpenRouter configuration even after seat parallelism, and at 2.5x the
request count.

## Limits - read these before quoting any number above

- **n = 1, 15 units, one language pair.** Two models scoring 10/10 with 0-1
  false flags means the slice lacks discriminating power, not that the models
  are perfect. `cyrillic-fragment` and `obscenity-injected` mutations are
  blatant by construction.
- **zh is untested.** The incumbent's weakness lives there (REAL@14 2/14 on
  zh_Hans against 11/14 on fr), and this smoke says nothing about it.
- **"Reset band" is a descriptive label, not a diagnosis.** Throughout this
  file it names one observed class of outcome: the client received no HTTP
  response - `httpx` raised, `transport_failed` is true, `status` is `None` -
  and the failures cluster at roughly 30-31 s. Whether a proxy timeout, an
  upstream limit, connection handling or something else produces that outcome
  was not investigated. No gateway configuration or server-side log was read
  in any run recorded here.
- **Lowering the batch size is not a free fix, at 1 or at 2.** It multiplies
  requests and cost per unit with them. It also changes `JUDGE_BATCH_SIZE`,
  which the registered A/B plan holds as a fixed constant, so a batch-1 or
  batch-2 number is not comparable with any published batch-5 measurement.
  Adopting batch 2 for a LiteLLM arm adds batch size as a **second confound**
  alongside the gateway: an arm that differs in both cannot attribute a
  difference to either. Either both arms move to batch 2, or the plan declares
  the second confound the way it already declares the model/gateway one.
- Two runs of the anchor at batch 5 were taken an hour apart with the same
  result, so proxy load at one moment does not explain it. That is two
  observations, not a rate.
