# Judge seat pair search on LiteLLM (two corpora, complementary recall)

**Date:** 2026-08-26. **Status:** awaiting approval.
**Supersedes:** `docs/llm-first/plans/2026-08-26-litellm-judge-seat-r3-eval.md`,
which searched for a replacement for one seat against a single corpus. The
objective is now the *pair*, measured on ru->zh_Hans and en->fr.
**Rule:** R3 - changing the prompt or the model invalidates the measurement
(`docs/llm-first/vision/llm-first-product-architecture.md:674`).

## What changed and why

Three findings force a different design than the superseded plan.

**1. The current pair is not complementary on zh, and the seat we lost is the
one that worked.** Running the committed arm-H run files through the existing
scorer reproduces the published collegium numbers exactly, and prints the
per-seat breakdown the measurement document never published:

| arm H, zh_Hans, n=5 | missed_crit | false_crit | REAL@14 | REAL@24 | FP | noise |
|---|---|---|---|---|---|---|
| `deepseek-v4-pro` | 6/7 | 1 | 4/14 | 5/24 | 4/83 | 14/124 |
| `qwen3-235b-a22b-2507` | **0/7** | 6 | 10/14 | 16/24 | 13/83 | 23/124 |
| collegium | 0/7 | 6 | 11/14 | 19/24 | 13/83 | 26/124 |

`qwen` alone equals the collegium on every gate. Decomposed by majority vote
across the 5 repeats, at the `>= major` boundary on the 24 true major+ defects:
both seats 1, `deepseek` only 2, `qwen` only 15, neither 6. On the 7 critical units:
`qwen` catches 7, `deepseek` 1, and the pair gains nothing over `qwen` alone.
So on this corpus the second family adds 2 defects out of 24. Its cost depends
on which aggregation is used, and the two must not be blurred: by the scorer's
medians above, `false_crit` and `FP` are identical for `qwen` alone and for the
pair (6 and 13/83), so `deepseek` adds no false flag at all; by majority vote
across the 5 repeats, the union of flagged clean strings is 11 against `qwen`'s
10, so it adds one. Both are reported here because a pair-selection rule that
silently picks the kinder aggregation is how a weak seat gets justified.
The repository already recorded this asymmetry -
`docs/llm-first/measurements/judge-measurements-index.md:614` states the unique
second-family contribution was measured on fr and was zero on zh.

That is the case for two corpora: a pair chosen on zh alone would be chosen
where complementarity does not show up.

**2. Pairs can be searched offline, so the search is affordable.** The scorer's
`collegium()` is a per-unit maximum over two stored error lists
(`analysis/probes/st2-zh-score.py:62-68`), and `metrics()` aggregates those
labels deterministically (`:78-85`). No pair-level request exists anywhere in
the scorer. Verified empirically, not only by reading: the arm-H reproduction
above was computed from committed files with zero API calls. Therefore measuring
K models individually yields all K(K-1)/2 pairs for free, and the cost of the
search is linear in K.

**3. My earlier disqualifications were mostly integration artifacts.** The
preflight of 2026-08-26 eliminated candidates on evidence that does not survive
scrutiny:

| Observation | What it actually was |
|---|---|
| `ConnectionResetError` on most models | an artifact of the probe's `urllib` client; the product's `httpx` path was clean on the same models in the same window |
| `Qwen3.5-plus` / `QWEN3.7-plus` HTTP 400 on `max_tokens` | our judge never sends `max_tokens` (absent from `weblate/trans/judge.py`), so the constraint is imposed upstream; `~/.omp/agent/models.yml` carries a `compat: maxTokensField` entry for `QWEN3.7-plus`, so another client already had to work around it |
| every MiniMax model returning empty `content` | LiteLLM issue [#38197](https://github.com/BerriAI/litellm/issues/38197) (open, filed 2026-08-25): when the answer sits entirely inside `<think>`, the adapter sets `content=''` and keeps the answer in `reasoning_content`. PR [#38212](https://github.com/BerriAI/litellm/pull/38212) is open and **not merged** |

A model that cannot be called correctly is not a model that judges badly. Any
eval that skips a compatibility step measures the proxy's adapter, so
establishing *how to call each candidate* becomes stage 0 rather than an
assumption.

## Candidate pool

Our judge requires strict `json_schema` output
(`weblate/trans/judge.py:241-276`, `strict=True` at `:540-578`), and the parser
rejects a whole batch on any deviation: wrong length, a duplicate or missing id,
an unexpected key, a severity outside the enum (`:397-441`). That contract is
stricter than what most vendors document, so the pool splits by measured
behaviour first and documentation second.

| Tier | Models | Basis |
|---|---|---|
| 1 - proven against our parser | `deepseek-v4-pro`, `atlas_glm-5.1` | 5/5 parseable in the preflight, product path, production batch size |
| 2 - strict schema documented, blocked by a parameter here | `qwen3.8-max`, `QWEN3.7-plus` | the only two Qwen models whose docs list strict JSON Schema; both failed on the `max_tokens` convention or on strict parsing |
| 3 - untested against our parser | `bytedance/doubao-seed-2.1-turbo-260628`, `deepseek-ai/deepseek-v4-flash` | doubao's strict-schema support is reported only by a third-party integrator; DeepSeek documents `json_object`, so v4-flash inherits nothing but family resemblance to tier 1 |
| 4 - `json_object` only, syntax guaranteed but not structure | `Qwen3.5-plus`, `qwen3.6-flash`, `Kimi K2.6`, `Kimi K2.7`, `mimo-v2.5`, `mimo-v2.5-pro` | matches the array-shaped and segment-dropping replies observed here; Xiaomi's docs say syntax only, Kimi's guarantee an object |
| 5 - blocked by an open upstream bug | all six MiniMax | LiteLLM #38197, PR #38212 unmerged |

Note that tier 1 is an empirical result, not a documented one: DeepSeek's own
JSON guide promises `json_object`, so `deepseek-v4-pro` satisfying our stricter
contract 5/5 is measured luck rather than a vendor commitment. That is another
reason stage 0 measures every candidate instead of trusting a capability table.

Shortlist of five to carry into the eval, with the reason each earns a slot:

1. `deepseek-v4-pro` - the only seat proven end to end here; DeepSeek V4 Pro GA
   2026-08-13, 1M context, JSON + tools, thinking toggle. Weak on zh in our own
   data (5/24), which is exactly why it needs a complement.
2. `atlas_glm-5.1` - proven parseable here, and the only proven candidate that
   is not DeepSeek, which is the whole point of a second seat. Its mapping to
   Z.ai's GLM-5.1 is `[INFERENCE]`: no public source matches the exact string,
   AtlasCloud publishes `zai-org/glm-5.1`, and LiteLLM's public provider prefix
   is `atlas_cloud/`, not `atlas/`. If it is GLM-5.1, that model documents
   strict structured output and a disableable thinking mode, and its card
   advertises en and zh only. No benchmark row for this exact ID exists, so it
   is a candidate on measured parseability and family diversity, not on
   published scores.
3. `qwen3.8-max` - the family that carried recall on zh historically; Qwen's
   strongest current model, one of only two on this proxy documented for strict
   JSON Schema, 1M context. Currently failing here; a candidate only if stage 0
   unblocks it.
4. `QWEN3.7-plus` - the other documented strict-schema Qwen, cheaper than Max,
   IFEval 94.6 by vendor-sourced figures. Failed only on the `max_tokens`
   convention. Same conditional.
5. `bytedance/doubao-seed-2.1-turbo-260628` - a fourth family, Seed 2.1 released
   2026-06-23 with an explicit multilingual claim, 262K context, reasoning
   toggle and `response_format`. Strict-schema support is reported by a
   third-party integrator, not by ByteDance, so stage 0 decides it.

`Kimi K2.6` is held as a sixth candidate, and its supposed disqualifier does not
apply: its JSON mode guarantees an object rather than a bare array, and our
schema *is* an object with a `segments` array
(`weblate/trans/judge.py:241-276`). It is also the only model on this proxy with
independent, non-vendor multilingual MT numbers (FLORES-200 and WMT25 in
arXiv:2605.22064). Its exact alias to `kimi-k2.6` is unresolved.

**No candidate has a published ru->zh_Hans or en->fr score.** Both research
passes state this explicitly: vendor multilingual aggregates exist, per-language
rows do not. Language competence is therefore something this eval measures, not
something the shortlist can inherit.

Three pool hazards to settle before any pairing:

- The `atlas/` prefix is unresolved. DeepSeek's changelog names V4-Pro-0813 and
  V4-Flash-0731 as the GA versions the bare IDs point at, which makes a re-host
  plausible, but no public source maps this proxy's `atlas/` namespace. Treat
  equivalence as unproven and settle it by measurement in stage 1, since a pair
  of two aliases of one model is not a collegium.
- `seed-2.1` matches no official SKU - the Seed 2.1 family ships Pro and Turbo -
  so it stays out until the proxy resolves it.
- Thinking modes are a live latency risk, not a detail. Qwen's own docs report
  `qwen3.7-plus` spending over 60% of output on reasoning, warn that long
  prompts with thinking may time out, and recommend streaming or a timeout above
  180 s; Z.ai's docs recommend streaming for the same reason. Our judge reads
  non-streaming with a 120 s transport timeout (`weblate/trans/judge.py:45`).
  This is a candidate explanation for the ~30.5 s no-status failures and must be
  tested in stage 0 rather than assumed away. Separately, DeepSeek's own JSON
  guide warns that JSON output can come back empty or truncated when
  `max_tokens` is left too low - and our judge sends no `max_tokens` at all,
  which makes the incumbent seat 1 subject to the same risk.
- **Thinking cannot be turned off through configuration on this proxy.**
  `validate_request_settings` refuses any non-empty `JUDGE_REASONING_EFFORT`
  when the host is LiteLLM (`weblate/trans/judge.py:130-147`), and the payload
  sends no vendor thinking toggle
  (`:540-578`). Qwen, DeepSeek and GLM all document thinking as **on by
  default**. So every candidate will be measured in its default thinking mode,
  and "just disable thinking" is a product change, not a setting - it lands in
  stop condition 3. This is the most likely way stage 0 ends for a strong
  candidate, so it is stated up front rather than discovered later.

## Ground truth, stated honestly

This is the weakest part of the whole exercise and must not be papered over.

**zh_Hans (S&T2, 124 units, committed and sealed).** 7 critical, 17 major, 17
minor, 83 clean. Only **14** defects are hand-confirmed - that is what `REAL@14`
means and why it exists beside `REAL@24`
(`analysis/probes/st2-zh-score.py:9-20,36-52`). The remaining labels are
rubric-derived.

**fr (need-for-greed/ui, 466 units, en->fr, not yet fetched).** No labels of any
kind exist. Worse, the precedent set by the existing fr golden set is explicitly
disowned in this repository: `docs/llm-first/plans/2026-08-25-judge-repair-loop-measurement.md:74-85`
records 266 defects from the synthetic generator, 167 passes labelled by two
LLMs, **zero human labels**, and calls that corpus unsuitable for safety
measurement.

So the fr arm is designed to measure only what construction can support:

| Question | fr corpus can answer? |
|---|---|
| does a seat catch a defect that is known to be there | yes - injected, label by construction |
| do two seats catch *different* injected defects | yes - this is the pair signal we want |
| what is the true false-positive rate | **no** - the originals are unlabelled, and `approved` is 0 for all 466 |
| recall on natural defects, idioms, puns, register | **no** - `docs/llm-first/measurements/2026-08-12-col4-judge-annotation.md:129-150` states idiom and pun classes cannot be synthesized |

On the originals we therefore report **relative flag volume** (how loudly each
model fires on unlabelled production strings) and treat it as a cost signal, not
a false-positive rate. A string flagged by every candidate is evidence of a real
defect; a string flagged by one is evidence about that model.

The 17 units that already fail deterministic checks are excluded from the clean
pool, since they are known-suspect.

## Objective function

For each candidate pair (a, b), at the `>= major` boundary for detection and the
`critical` boundary for rejection, using majority vote across repeats:

- `union_recall` = defects caught by a or b, over defects present
- `gain` = `union_recall` - max(recall(a), recall(b)) - the complementarity that
  justifies a second seat at all
- `flag_volume` = unlabelled originals flagged by a or b (fr), and `FP` on the
  83 known-clean (zh)
- `missed_crit` per run, per pair

Selection: maximise `union_recall` on both corpora subject to the hard gate,
then prefer the larger `gain`, then the lower `flag_volume`. Report the Pareto
front rather than a single winner, because a pair that wins on zh and loses on
fr is a decision for a human, not for an argmax.

## Gates

Hard, applied to the pair, carried over from the D6 gates of
`docs/llm-first/measurements/2026-08-20-judge-prompt-universalization-run.md:44-54`:

- zh: `missed_crit` = 0 in **every** repeat. This is the gate only arm H passed,
  and the migration must not silently give it up.
- both corpora: `unparsed` = 0 in every repeat, after stage 0 has settled how to
  call the model. On a paid path an unparsed batch is burned money and a
  withheld verdict.

Relative, against the re-measured anchor:

- `false_crit` (zh) no worse than the anchor by median.
- `REAL@14` (zh, hand-confirmed) no lower than the anchor. This one outranks
  `REAL@24`, because 14 defects are confirmed by a human and the other 10 are
  not.
- fr injected-defect recall no lower than the anchor.

The anchor is `deepseek-v4-pro` measured in the same session over the same path,
not the published arm-H numbers: production's payload is not byte-for-byte arm H
(the glossary goes as dicts, `weblate/trans/judge.py:295`; `rendered_target` is
added, `weblate/trans/judge.py:291`;
`back_translation` is required, `:184`), so reusing those numbers would compare
across two R3 invalidations at once.

A single seat is expected to fail the hard gate - `deepseek` alone misses 6 of 7.
Solo numbers are diagnostic.

## Stages

### Stage 0: compatibility, not quality

`analysis/probes/litellm-model-compat.py` (written, not yet run) sends the
judge's payload shape to each candidate and reports where the answer landed:
`content`, `reasoning_content`, a reply the parser refuses, or nowhere. Variants tried per
model: plain, explicit `max_tokens`, `thinking: {type: disabled}`,
`reasoning_split`. Roughly 4 requests per model, about 50 in total.

Output: for each candidate, either "callable, with these parameters" or "not
callable through this proxy today, for this reason". A model whose answer only
appears in `reasoning_content` is reported separately, because unlocking it
would require a product change and that is a decision, not a workaround.

### Stage 1: duplicate detection

Same prompt, `temperature=0`, one request each to the bare and `atlas/`-prefixed
DeepSeek IDs; compare replies and reported model metadata. Any two IDs that
behave identically are collapsed to one candidate, so no "pair" is a model with
itself. About 6 requests.

### Stage 2: smoke on both corpora

15 zh units (including several of the 7 critical units) and 15 fr units (including
several injected defects), one repeat per surviving candidate. Disqualify on
`unparsed` > 0 or a missed planted critical. About 12 requests per candidate.

### Stage 3: per-model runs

Surviving candidates, 5 repeats each, on both corpora. zh is the full 124 units
(25 requests per repeat). fr is a stratified 150-unit slice, not all 466, to
keep the bill proportionate: all injected defects plus a clean sample (30
requests per repeat). So 275 requests per model.

### Stage 4: offline pair search

A new corpus-agnostic scorer, `analysis/probes/judge-pair-search.py`, reading a
truth file, an anchor list and the stored run files, computing the objective
above for every pair. Zero API calls.

Acceptance, and this is the test that makes the new scorer trustworthy: on the
zh corpus with arm-H run files it must reproduce `st2-zh-score.py` exactly -
collegium `missed_crit` 0/7, `false_crit` 6, `REAL@14` 11/14, `REAL@24` 19/24,
`FP` 13/83, noise 26/124, and the per-seat rows above. `st2-zh-score.py` itself
is not modified.

### Stage 5: confirmation and decision

The selected pair is re-run on the **full** 466-unit fr corpus and the full zh
corpus, 5 repeats, to confirm the choice was not an artifact of the 150-unit
slice. Then
`docs/llm-first/measurements/2026-08-26-judge-seat-pair-search.md`: per-model and
per-pair tables, `missed_crit` per run, the Pareto front, complementarity
decomposition, tokens, and the decision.

Only then may `WEBLATE_JUDGE_MODEL_SEAT_1/2` change.

## Cost

| Stage | Requests |
|---|---|
| 0 compatibility | ~50 |
| 1 duplicates | ~6 |
| 2 smoke | ~12 per candidate, ~70 |
| 3 per-model runs | 275 per surviving model |
| 5 confirmation | ~600 for the chosen pair |
| **total, 5 survivors** | **~2100** |

The proxy returns no `usage.cost` (shadow pricing, vision:670-672), so tokens
from `LLMUsageLog` are reported instead of dollars. Published list prices, for
an order-of-magnitude sanity check only, span $0.14/M input (mimo-v2.5) to
$2/M (qwen3.8-max). Stage 3 starts with one model, whose real token count is
reported before the rest proceed.

## Stop conditions

1. No pair reaches zh `missed_crit` = 0 in all repeats: **stop**. Do not lower
   the gate, do not change the prompt, do not ship one seat. Report that the
   judge cannot be assembled from this proxy and leave the judge on OpenRouter -
   the LiteLLM provider and configurable endpoint already work and are already
   merged, and they do not depend on the seats.
2. Only same-family models survive stage 0: **stop and report**, since a
   same-family pair is not a collegium.
3. Unlocking a candidate would require changing the judge's payload or parser
   (the `reasoning_content` case, or sending `max_tokens` for every model):
   **stop and ask**. That is a product change, it applies to all models, and it
   invalidates prior measurements under R3.
4. fr injected-defect recall and zh recall disagree on the winner: **stop and
   present both**, do not average them into a single score.

## Out of scope

- Any change to the prompt, schema, rubric or `JUDGE_BATCH_SIZE`.
- Any modification of `analysis/probes/st2-zh-score.py`.
- Human annotation of the fr corpus, and therefore any claim about the true
  false-positive rate or natural-defect recall on fr.
- The container arm of the LiteLLM plan's task 7 (`./rundev.sh`) - deployment,
  separate approval.
- Production rollout, and any write to the production instance.

## Approvals this plan needs

1. **Read the fr corpus from production.** Fetching the 466 units and the
   302-term glossary of `need-for-greed/ui/fr` is a read-only GET, but
   `AGENTS.md` classes any command against `l10n.herocraft.com` under
   deployment, so it needs a word.
2. **The spend**, on the order of 2100 requests as tabulated.
3. **Whether stage 0 may propose a product change**, and which. Three are
   already foreseeable: sending an explicit `max_tokens` (unblocks the Qwen
   400s, and guards DeepSeek against silent truncation), sending a vendor
   thinking toggle for LiteLLM hosts (the only way to control reasoning cost
   and the likely cause of the ~30.5 s failures), and falling back to
   `reasoning_content` when `content` is empty (unblocks MiniMax while LiteLLM
   PR #38212 stays unmerged). Each applies to every model and so invalidates
   prior measurements under R3. Default without approval: any candidate that
   needs one is recorded as blocked and excluded, and the eval proceeds with
   whoever answers as-is.
