# Judge seat selection on LiteLLM (R3 eval)

**Date:** 2026-08-26. **Status:** awaiting approval.
**Continues:** `docs/llm-first/plans/2026-08-23-litellm-provider-and-judge-endpoint.md`,
task 7 step 4 - "If any route or seat fails, stop. Do not substitute a model or
weaken the schema: record the result and start the R3 eval path".
**Rule:** R3 - changing the prompt or the model invalidates the measurement
(`docs/llm-first/vision/llm-first-product-architecture.md:674`). Seat models are
chosen by an eval on the S&T2 corpus, not by reasoning about model kinship.

## Why

The LiteLLM preflight
(`docs/llm-first/measurements/2026-08-26-litellm-preflight.md`) established that
the configured second seat `qwen/qwen3-235b-a22b-2507` does not exist on the
corporate proxy. Seat 2 is not a configuration detail. The accepted two-model
collegium works because the two models fail on substantially different strings -
flagged by both 228, `deepseek` only 16, `qwen` only 34
(`docs/llm-first/measurements/judge-measurements-index.md:90`).

On this specific language pair the missing seat is the one that does the work:

| Configuration (arm A, zh_Hans, n=5) | missed_crit | REAL@14 | REAL@24 |
|---|---|---|---|
| `deepseek` alone | 6/7 | 2/14 | 4/24 |
| `qwen` alone | 2/7 | 11/14 | 17/24 |
| collegium | 1/7 | 11/14 | 18/24 |

(`docs/llm-first/measurements/2026-08-18-severity-recalibration-partial.md:65-69`,
whose conclusion at lines 91-92 is explicit: "`deepseek` - слабое место на этой
паре языков".)

So replacing seat 2 is replacing the seat that catches most defects on zh_Hans.
A "similar" model cannot be picked by name. It has to be measured.

## Established inputs (not the subject of this plan)

1. **The accepted arm is H**, and H is what production runs
   (`docs/llm-first/measurements/2026-08-20-judge-prompt-universalization-run.md:66-69`).
   Collegium medians over 5 repeats:

   | Arm | missed_crit | false_crit | REAL@14 | REAL@24 | FP | noise >=flag |
   |---|---|---|---|---|---|---|
   | H | 0/7 | 6 | 11/14 | 19/24 | 13/83 | 26/124 |

   The only hard gate is `missed_crit` = 0 in **every** run; H gives
   `0, 0, 0, 0, 0` and was the only arm to pass it (same document, lines 44-54).

2. **The corpus is sealed and committed:** `analysis/data/st2-zh-units.jsonl`
   (124 units, ru->zh_Hans), `analysis/data/st2-zh-groundtruth.json`
   (7 `critical`, 17 `major`, 17 `minor`, 83 clean),
   `analysis/data/st2-summer-glossary-zh.json` (30 pairs),
   `analysis/data/st2-zh-glossary-checks.json` (offline `check_glossary`
   firings). No input is recomputed.

3. **The scorer exists and does not change:** `analysis/probes/st2-zh-score.py`
   computes `missed_crit`, `false_crit`, `REAL@14/@24`, `FP`, the noise floor
   and the 4x4 confusion matrix; it is parameterised by
   `--arms/--seat1/--seat2/--repeats` and reads `arm{X}-{seat}-run{k}.json`.
   The metric must stay identical or the comparison is meaningless.

4. **The proxy exposes 22 models** with non-OpenRouter identifiers
   (`deepseek-v4-pro`, not `deepseek/deepseek-v4-pro`). From the preflight:
   `deepseek-v4-pro` clean 5/5 and 3/3 at the production batch;
   `atlas_glm-5.1` clean 5/5; `deepseek-ai/deepseek-v3.2` 3/5;
   `Kimi K2.7` 2/5 (drops segments at batch 5); `qwen3.8-max` 0/5;
   the MiniMax models return empty `content`.

## Three decisions to settle before spending anything

### D1. The eval runs through the product path, not `st2-zh-recalibration.py`

That harness posts with `urllib` to a hardcoded OpenRouter URL (its docstring:
"only the judge calls hit OpenRouter"). Against LiteLLM, `urllib` invents
failures: the control probe recorded 2/3 resets for `Kimi K2.7` and 2/5 for
`atlas_glm-5.1` over `urllib`, while the product's `httpx` path was clean on the
same models in the same window (`analysis/probes/litellm-client-control.py`).
An eval on a transport that drops its own requests measures the transport.

**Decision:** run through `weblate.trans.judge.request_verdicts` - the same
code, prompt, schema and client as production.

### D2. The anchor is re-measured in this session, not taken from 2026-08-20

The production payload is not byte-for-byte arm H. The 2026-08-20 measurement
states that H/I carry arm D's request form while E/F/G carry the production form
(lines 20-22), and today's `weblate/trans/judge.py` confirms the production
form: the glossary goes as a list of dicts (`judge.py:295`), `rendered_target`
is added (`judge.py:291`), and `back_translation` is required (`judge.py:184`).
Arm H's recorded numbers therefore cannot serve as the comparison target for a
run through the product path - both the model and the request form would differ,
which is two R3 invalidations at once.

**Decision:** the anchor is `deepseek-v4-pro` (the same model as production
seat 1, and it exists on the proxy), measured **in the same session, by the same
path, on the same corpus**. Candidates are compared against that. Arm H's
numbers stay in the report as a historical reference point, not as a gate.

### D3. Seat 2 comes from a different family than seat 1

The value of the second seat is decorrelated errors, not a second opinion from
the same model (`judge-measurements-index.md:90`; and the cascade arithmetic at
lines 44-51: a stage B allowed to clear a flag caps end-to-end recall). Two
consequences:

- `deepseek-ai/deepseek-v3.2` is a poor seat-2 candidate next to
  `deepseek-v4-pro` by construction, and is screened last.
- Screening ranks candidates by **critical defects caught**, because that is the
  property the missing seat supplied on this corpus.

## Held fixed

The prompt (production, arm H), the corpus, the glossary, the offline checks,
`JUDGE_BATCH_SIZE` = 5 (the production default,
`weblate/trans/defaults.py:60`), 5 repeats, and the scorer. Only the seat model
identifier changes. That is exactly what an R3 eval is for.

## Gates

Hard, from the D6 gates of the 2026-08-20 measurement, and applied **to the
pair**, since the collegium is what production runs:

- `missed_crit` = 0 in **every** one of the 5 collegium runs. Not the median.

Relative, against the re-measured anchor (D2):

- `false_crit` no worse than the anchor by median;
- `REAL@14` and `REAL@24` no lower than the anchor;
- `FP` no higher than the anchor;
- the noise floor is reported, and is not a threshold.

Specific to a paid path:

- `unparsed` > 0 in any run disqualifies the candidate. On a paid proxy an
  unparsed batch is both burned money and withheld verdicts, and the preflight
  already showed unstable parsing is real here.

A single seat is **not** expected to pass the hard gate: `deepseek` alone missed
6 of the 7 critical units on this corpus. Solo numbers are diagnostic, not a
verdict.

## Tasks

### Task 1: the runner

Write `analysis/probes/st2-zh-r3-litellm.py`: read the committed inputs, build a
`JudgeRequest` per unit (the 30 glossary pairs as `GlossaryPromptEntry`,
`failing_checks` from the offline file), run through `request_verdicts` in
batches of 5, and write `armR3-<model>-run<k>.json` in the shape the scorer
already reads (`{verdict, errors, model_verdict, unparsed}` per unit).
Parameters: `--model`, `--runs`, `--slice`, `--out-dir`.

Acceptance: `--dry-run` prints the prompt and payload without any call; on a
3-unit slice the output file is read by the unmodified scorer.

### Task 2: screening (cheap)

A stratified 15-unit slice that must include several of the 7 `critical` units
and several clean ones; 1 run per candidate: `deepseek-v4-pro`,
`atlas_glm-5.1`, and up to three other families on the proxy. Disqualify on
`unparsed` > 0 or on missing a `critical` in the slice.

Acceptance: a table of model / unparsed / critical defects caught / latency,
and the list of survivors ranked by critical defects caught. Order of 15-20
requests.

### Task 3: the anchor

`deepseek-v4-pro`, 5 repeats, the full 124 units. This is the reference point
for every comparison.

Acceptance: 5 run files, `unparsed` = 0, scorer output.

### Task 4: seat 2

The best surviving candidate **outside the deepseek family**, 5 repeats, the
full 124 units. The scorer computes the collegium (max severity across the pair)
via `--seat1`/`--seat2`. If the pair fails the hard gate, take the next
survivor. At most three candidates; after the third, stop and report.

Acceptance: collegium numbers against the anchor on every gate.

### Task 5: measurement document and decision

`docs/llm-first/measurements/2026-08-26-litellm-judge-seat-r3.md`: per-seat and
per-pair tables, `missed_crit` **per run** and not only the median, the 4x4
matrix, noise, tokens, and the comparison with historical arm H as a reference.
The decision is binary: there is a usable pair, or there is not.

Only then may `WEBLATE_JUDGE_MODEL_SEAT_1/2` change and the phase-4 increment be
marked. This plan does not edit that configuration.

## Stop conditions

1. No candidate reaches `missed_crit` = 0 across all 5 runs - **stop**. Do not
   lower the gate, do not change the prompt (that is a second R3 invalidation),
   do not ship one seat instead of two. Report that a judge cannot be assembled
   from this proxy's models, and leave OpenRouter in place.
2. The anchor produces `unparsed` > 0, or its solo numbers land far from the
   2026-08-18 zh baseline (`missed_crit` 6/7, `REAL@14` 2/14) - **stop**: the
   path or the proxy is the variable, not the model, and there is nothing to
   compare yet.
3. Runs hit the no-HTTP-status failures at ~30.5 s seen in the preflight -
   **stop** and establish that failure's cause first, instead of selecting
   models around it.

## Cost and accounting

Order of magnitude: screening ~20 requests, the anchor 125, each candidate 125 -
about 400 requests at batch 5. The proxy does not return `usage.cost` (shadow
pricing, recorded in the vision at lines 670-672), so the report gives **tokens**
from `LLMUsageLog`, not dollars. Before the full task 3 run, do one trial run to
see real tokens per run and confirm the cost order.

## Out of scope

- Any change to the prompt, schema, rubric, or `JUDGE_BATCH_SIZE`.
- Any change to `st2-zh-score.py`.
- The container arm of the LiteLLM plan's task 7 (`./rundev.sh`) - that is
  deployment and needs separate approval per `AGENTS.md`.
- Production rollout and any production environment change.
- Other language pairs: the S&T2 corpus is ru->zh_Hans and rule R3 is stated
  about it. Cross-language transfer remains a recorded gap
  (`judge-measurements-index.md:537`).
