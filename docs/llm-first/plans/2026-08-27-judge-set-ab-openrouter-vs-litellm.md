# Judge set A/B: incumbent on OpenRouter vs new set on LiteLLM

**Date:** 2026-08-27. **Status:** proposed. Arm B's models are chosen by stage
1, not by this document; the per-model reasoning prerequisite below is not yet
built.
**Scope:** scores judges, not translators. Read-only. No repair, no production write.
**Rule:** R3 — the prompt is a fixed constant. Only model and gateway vary.

## What this decides

Two things, in order and on separate corpora:

1. **Which LiteLLM model takes seat 2 beside `deepseek-v4-pro`** — the seat the
   incumbent Qwen model occupies today, chosen by a registered rule on a
   screening corpus.
2. **Whether that pair can replace the incumbent OpenRouter pair** — decided on
   the 244 units below, with seat 2 already frozen.

The run produces two artefacts for post-hoc reading in a Claude Opus chat; it
does not decide anything by itself.

## The confound this design cannot remove, and what it can

The original design carried a third arm — the incumbent pair replayed on
LiteLLM — so that `A vs C` would isolate the gateway and `C vs B` the models.
**That arm is not executable.** Arm A's seat 2,
`qwen/qwen3-235b-a22b-2507`, is not served by the corporate proxy: the live
`GET /v1/models` returns 22 IDs and no Qwen 235B among them. The incumbent pair
cannot be replayed on LiteLLM, and no run design recovers it.

So the LiteLLM arm differs from every OpenRouter arm in two things at once —
and in exactly two, because seat 1 is held identical:

| arm | seat 1 | seat 2 | gateway |
|---|---|---|---|
| A1 | `deepseek/deepseek-v4-pro` | `qwen/qwen3-235b-a22b-2507` | OpenRouter |
| A2 | `deepseek/deepseek-v4-pro` | `openai/gpt-5.4-mini` | OpenRouter |
| A3 | `deepseek/deepseek-v4-pro` | `anthropic/claude-haiku-4.5` | OpenRouter |
| B | `deepseek-v4-pro` | stage-1 winner, mode per stage 1 | LiteLLM |

**Seat 1 sends no reasoning field in any arm**, so the model runs its own
default thinking on both gateways. That is the configuration the 12-of-12,
17–23 s result was measured under
(`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md:141-146`:
`EFFORT=""` sends no reasoning field and the default applies). Writing "reasoning
on" for seat 1 would describe the same request while implying an explicit
setting, and an explicit setting on one side only would add a third varying
factor. Seat 2 is the only seat with a mode of its own, and it is logged.

**State this plainly: at pair level, model and gateway remain confounded.**
Every decision-relevant metric in this plan is pair-level — critical recall of
the two-seat union, false-flag of the union, the collegium's flip rate. A
pair-level difference between B and any A arm cannot be attributed to the
seat-2 model or to the LiteLLM request path, because both changed. The
registered decision rule is still answerable, because "can arm B replace the
incumbent as deployed" does not require the decomposition. What the run cannot
answer is *why* arm B differs, and no amount of scoring makes it answer that.

The three OpenRouter arms are mutually clean, though: A1, A2 and A3 share the
gateway and seat 1, so differences among *them* are seat-2 model effects with
nothing else moving.

### The one decomposition that is available

`deepseek-v4-pro` is measured on both gateways in any case — it is a member of
the incumbent OpenRouter pair and a LiteLLM candidate — so its two
**per-model** verdict vectors differ only in gateway. That comparison is free:
per-model rows are stored anyway, so it is a scoring pass, not extra calls.

What it is worth, and its limits:

- It measures the deployed LiteLLM path on one model —
  `RoutedLiteLLMTranslation` drops the `provider` object (so no
  `require_parameters`), caps an attempt at 55 s, and resolves models through
  its own routing. A large shift is evidence the gateway moves verdicts at all;
  a null result is evidence it does not, for this model.
- It is **not** a control for the pair. A one-model comparison holds nothing
  else fixed at pair level and cannot be subtracted from a pair difference to
  leave a clean model effect.
- It says nothing about the gateway's effect on any other model.

Report it as a per-model diagnostic under its own heading. Do not label it an
arm, and do not use it to reinterpret the pair-level decision.

## There is no first or second seat

The collegium verdict is a per-unit **maximum** over the two members' error
lists (`analysis/probes/st2-zh-score.py:71-77`), and `max` is commutative. The
loop's own contract says the same: "both seats judge EVERY string
independently (no cascade, no seat may lower another)"
(`weblate/trans/judge_loop.py:6-9`). `JUDGE_MODEL_SEAT_1` and `_SEAT_2` are
configuration slots, not ranks — there is no primary judge, no reviewer, and no
order to optimise.

Two consequences, and the second is the one that reshapes this plan:

1. A pair is an **unordered set of two models** *for scoring*. "Which model
   takes seat 2" is not a question the metrics can distinguish. The **request**
   is a different matter: the product's reasoning knobs are per slot, so the
   unordered framing only holds once the mode map binds each mode to its model
   and a canonical slot order is fixed. See "Prerequisite: reasoning mode
   belongs to the model, not the slot".
2. **No model's membership is assumed, including `deepseek-v4-pro`'s.** On
   zh_Hans it is the weaker member by a wide margin
   (`docs/llm-first/plans/2026-08-26-judge-seat-pair-search.md:24-33`, arm H,
   n = 5):

| arm H, zh_Hans | missed_crit | REAL@14 | FP | noise |
|---|---:|---:|---:|---:|
| `deepseek-v4-pro` | 6/7 | 4/14 | 4/83 | 14/124 |
| `qwen3-235b-a22b-2507` | **0/7** | 10/14 | 13/83 | 23/124 |
| collegium | 0/7 | 11/14 | 13/83 | 26/124 |

On the 7 criticals Qwen catches 7 and DeepSeek 1, and the pair gains nothing
over Qwen alone. Keeping DeepSeek in the LiteLLM pair because it is in the
OpenRouter pair would be exactly the reasoning R3 exists to prevent. It enters
the search as one candidate among several and may end up outside the winning
pair; if it does, the per-model gateway diagnostic above still exists, because
that only needs it measured on both gateways, not selected.

## Stage 1: choose arm B, on a corpus the decision never sees

Selection and decision cannot share a corpus. If the pair were chosen on the
same 244 units that later decide the migration, its margin would include
whatever it won by chance during selection, and arm B would not have been fixed
before the comparison. So stage 1 runs on a disjoint corpus and stage 2 never
revisits the choice.

| stage | corpus | decides |
|---|---|---|
| 1 — screen | `col4-judge-screen-dev.json`, 376 records, en→fr, binary `defect`/`pass` | which LiteLLM **pair** becomes arm B |
| 2 — confirm | the 244 units below | whether arm B replaces the incumbent |

`col4-judge-SEALED-test.json` and `col4-judge-golden.json` stay sealed through
both stages. The stage-1 corpus is a dev split with synthetic and LLM-assigned
labels: adequate for ranking pairs on parseability and gross recall, not
adequate for a safety claim, and it carries no severity labels and no zh. What
stage 1 cannot screen for is zh recall — that is exactly what stage 2 measures.

**Pairs cost nothing extra.** Because the collegium is composed offline from
per-model rows, measuring K models yields all K(K−1)/2 pairs for free. Stage 1
is therefore a search over pairs at the price of K models.

### Candidates

Live `GET /v1/models` returns 22 IDs (checked 2026-08-27). Admissible, with the
evidence class stated — none of it substitutes for stage 1:

| candidate | transport evidence | judgment evidence | reasoning |
|---|---|---|---|
| `deepseek-v4-pro` | 12/12 batches, 17–23 s, production prompt | weak member on zh in arm H (above); never scored on LiteLLM | default (no field) |
| `qwen3.8-max` | 8 batches, 0 unparsed, 5.6 s | 1 run, 15 units: 0/15 unparsed, `missed_defect` 1/6, `sev_under` 1, `sev_over` 1, fr 1/15 missed | off |
| `QWEN3.7-plus` | stage 3: 5/15 unparsed, 1 reset of 6 batches | 1 run: `missed_defect` 5/6, transport-contaminated | off |
| `atlas_glm-5.1` | stage 0 3/3 at 7.4–15.0 s; stage 3 2 parsed / 3 reset | **none** | off |

Four candidates give six pairs. Sources:
`docs/llm-first/measurements/2026-08-26-litellm-stage0-compatibility.md`,
`…-judge-seat-pair-search-stage3.md:68-84,119-133`,
`…-litellm-transport-reset-rate.md:61-146`.

Three unresolved qualifications:

- The `qwen3.8-max` judgment row is a **single unrepeated run on a 15-unit
  slice**, and its source document explicitly refuses to read it as a
  clearance. An earlier sample recorded the same model at 0/5 parseable
  (`docs/llm-first/archive/2026-08-26-litellm-judge-seat-r3-eval.md:69-70`);
  that row measured its default thinking mode, which resets at ~30.5 s, so it
  is superseded rather than contradictory.
- **The vendor documents that the mode this proxy forces is the less reliable
  one for strict schema.** Alibaba Model Studio and QwenCloud state that JSON
  Schema mode with `strict: true` is supported on selected models only, that
  enabling thinking *improves* JSON accuracy, and that non-thinking mode **may
  ignore strict structured output**
  (<https://help.aliyun.com/en/model-studio/qwen-structured-output>,
  <https://docs.qwencloud.com/developer-guides/text-generation/structured-output>).
  The ~30.5 s ceiling forces the Qwen family into exactly that non-thinking
  mode, so the local zero-unparsed observations are 23 batches against a
  documented vendor caveat.
- `atlas_glm-5.1` has **no judgment measurement at all**, its mapping to Z.ai's
  GLM-5.1 is marked `[INFERENCE]` here, and BerriAI/litellm#38199 (open, filed
  2026-08-25) reports GLM-5.1 through LiteLLM returning HTTP 200 with empty
  `content`, empty `reasoning_content` and `total_tokens = 0`, forwarded as a
  successful completion. A GLM seat can fail silently rather than visibly.

Not admissible: `deepseek-ai/deepseek-v4-flash` returns **403 on our key**
(entitlement, not transport) — and it is the route the neighbouring
`cathedral` localizer uses by default, so its absence here is a key-scope
difference, not a capability one. `atlas/deepseek-v4-pro-0813` is an unresolved
possible alias of `deepseek-v4-pro`, so a pair of the two would gain nearly
nothing by construction, and it returned 0 parsed batches; `Kimi K2.6`/`K2.7`,
`mimo-*`,
`Qwen3.5-plus` and `qwen3.6-flash` are documented for `json_object` only, and
K2.7 drops segments at batch 5; all six MiniMax are blocked upstream (#38197
open, fix #38212 unmerged); `seed-2.1` matches no official SKU.

### The screening rule, registered before stage 1 runs

Each candidate is measured **individually** at `JUDGE_BATCH_SIZE = 5`, n = 3,
on the stage-1 corpus. Every configuration is then composed offline from those
rows, at zero extra requests: **four singletons and six pairs, ten
configurations in all.** A singleton's verdict is its own max-severity label
(`run_label`, `analysis/probes/st2-zh-score.py:67-68`); a pair's is the union
(`collegium`, `:71-77`). Singletons are registered candidates, not an
afterthought — on arm H, `qwen` alone equalled the collegium on every gate, so
the question of whether the second call buys anything is live.

> **Model-level eligibility.** A model may enter any configuration only if, in
> every repeat:
>
> 1. **Unparsed rate ≤ 5%**, counting the empty-200 pattern as unparsed.
>
> **Configuration-level gate and ranking.** Among the ten configurations whose
> every member is eligible:
>
> 2. **False-flag rate ≤ 10%** in every repeat, on the corpus's `pass` records,
>    computed on the configuration's own verdict — union for a pair, own label
>    for a singleton. A pair can exceed the ceiling while neither member does,
>    and the union is what production ships.
> 3. Highest defect recall, mean over repeats — union for a pair, own for a
>    singleton.
> 4. Within one unit on recall: lower $ per 1 000 units wins, then lower p95,
>    both summed over the configuration's members. A singleton therefore beats a
>    pair it ties with, because it pays for one call instead of two; that falls
>    out of the rule and needs no special case.
>
> Gates 1 and 2 eliminate; they never rank. If no configuration passes both,
> stage 2 does not run and no substitution is improvised. If a tie survives 4,
> the plan stops and reports the tie.
>
> Arm B is whatever configuration wins — one model or two. The collegium exists
> to buy complementarity, and one that buys none is not worth its second call.

Ranking on recall without gate 2 would select the flag-everything candidate: it
maximises union recall by construction, then fails stage 2's identical
false-flag gate, and because a failed arm B concludes the run the usable
runner-up would never be measured. This is not hypothetical — it is the
documented behaviour of the seat being replaced, whose `missed_crit = 0/7` came
with `sev_over = 5`.

The stage-1 table is published beside the stage-2 result, so the selection is
auditable and not merely its outcome.

## Stage 2: the OpenRouter arms, all pre-registered

Once stage 1 names arm B's configuration, arm B is frozen. The comparison is
against
OpenRouter **pairs**, not only the incumbent one — the useful question is not
merely "is LiteLLM as good as what we run today" but also "does an OpenRouter
pair we do not currently run look better than either". Those are two different
questions with two different statuses, set out below. Every arm is named here,
before the run, and measured with the same payload, the same prompt, the same
corpus and the same repeats.

**A1 is the only baseline.** A2 and A3 are **descriptive comparators**: they
are reported, and they can never take A1's place in arm B's replacement test.
Letting whichever OpenRouter arm happens to score best stand in as the baseline
would make the migration decision post-selection — the baseline would have been
chosen after seeing the numbers, and its margin would carry whatever it won by
chance.

`deepseek/deepseek-v4-pro` is a member of all three OpenRouter arms, so
differences among A1, A2 and A3 are pure second-member effects with the gateway
and the other member held fixed. Arm B may or may not contain it — that is
stage 1's outcome, not an assumption here. Configurations compose offline from
per-model rows, so an extra OpenRouter arm costs one model's requests, not one
pair's.

| arm | members | gateway | why this one is registered |
|---|---|---|---|
| **A1** | `deepseek/deepseek-v4-pro` + `qwen/qwen3-235b-a22b-2507` | OpenRouter | the incumbent — the deployment baseline |
| **A2** | `deepseek/deepseek-v4-pro` + `openai/gpt-5.4-mini` | OpenRouter | best cost/quality non-Qwen in the index: 84.5 at $0.085 per 1 000 |
| **A3** | `deepseek/deepseek-v4-pro` + `anthropic/claude-haiku-4.5` | OpenRouter | different vendor family, the only registered second member with 100 on its recall column, at $0.142 |
| **B** | stage-1 winner | LiteLLM | the migration candidate |

A2 and A3 are drawn from the per-model table in
`docs/llm-first/measurements/judge-measurements-index.md:16-20`. Those
published numbers are what makes them worth registering; they are **not** the
comparison. Under R3 they came from a different payload, so every arm here is
re-measured in this session.

### What each comparison decides

- **B vs A1** — the registered migration gate. The decision rule below applies
  to this comparison and to no other. A1 is the baseline because it is what
  production runs; replacing it is the action under consideration.
- **B vs A2, B vs A3** — descriptive, never gating. If an OpenRouter arm looks
  better than both A1 and B, the honest conclusion is "do not migrate; change
  the second member on OpenRouter instead", which is a **different decision
  than the one registered here** and needs its own confirmation run before
  anything changes. Naming these arms in advance keeps that an observation
  rather than a search.
- **A2 vs A3 vs A1** — the same offline pair composition gives these for free;
  report them, since they cost nothing once the rows exist.

### Two rules that stay in force

- **A failed arm B concludes the migration question.** If arm B misses a
  viability gate or loses to A1 on recall, that is the result. Promoting a
  stage-1 runner-up into arm B afterwards and scoring it on these same 244
  units would be the post-selection problem the two stages exist to avoid. Any
  other LiteLLM pair needs its own registered run.
- **No arm is added after results are seen.** The four arms above are the whole
  set. An OpenRouter model that looks attractive once the numbers land is a
  candidate for the next registered run, not for this one.

## Prerequisite: reasoning mode belongs to the model, not the slot

A DeepSeek + Qwen pair is **mixed-mode** and cannot be run by the product as it
stands. `deepseek-v4-pro` holds 12 of 12 batches with reasoning on at 17–23 s;
`qwen3.8-max` fails 8 of 8 with reasoning on (63 s per batch — the transport
retry fires and is spent) and holds at 5.6 s with it off. One reasoning setting
per endpoint is therefore not enough.

This collides with the plan's own claim that a configuration is an unordered
set. The product's knobs are per **slot** — `JUDGE_MODEL_SEAT_1` / `_SEAT_2` —
so if a mode is attached to a slot, placing the same two models the other way
round produces two different requests, and "the DeepSeek + Qwen pair" names two
non-equivalent configurations. Verdict aggregation stays unordered
(`collegium` is a commutative `max`), but the request does not.

**The binding rule, fixed before stage 1:**

- A **canonical mode map** keyed by model, not by slot. One row per candidate,
  written into the manifest before any request: `deepseek-v4-pro` → reasoning
  on; `qwen3.8-max` → thinking off; `QWEN3.7-plus`, `atlas_glm-5.1` → measured
  and recorded in the same table. A model carries its mode into every
  configuration it appears in, in both stages, on both gateways.
- A **canonical slot order**: members sorted by model id, lower id to seat 1.
  Mechanical, reproducible, and it never changes which mode a model runs, only
  which slot number the log shows. `deepseek/deepseek-v4-pro` +
  `qwen/qwen3-235b-a22b-2507` therefore always occupies the same slots.
- Consequence: **a pair has exactly one configuration.** The slot assignment
  carries no meaning — no first judge, no reviewer — and swapping the order
  changes nothing observable, which is what makes the unordered framing true
  rather than merely asserted.

Two things must exist in the product before stage 1:

- **Per-slot reasoning effort**, so the mode map can be expressed at all:
  `JUDGE_REASONING_EFFORT_SEAT_1` / `_SEAT_2`, each falling back to the endpoint
  value when empty
  (`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:154-161`). The
  knob is per slot; the *value* comes from the model's row in the mode map.
- **The vendor thinking toggle for LiteLLM hosts.** It is vendor-specific —
  `enable_thinking` for Qwen, `thinking.type` for DeepSeek, GLM and Kimi — so
  it is a small mapping, not one field, and it is not in the product today.
  `validate_request_settings` currently refuses a non-empty
  `JUDGE_REASONING_EFFORT` for LiteLLM hosts outright.

Without both, neither stage is runnable and this plan stops before spending
anything.

## Sample: 244 units, both instruments, all criticals kept

| slice | source | units | criticals | defect type |
|---|---|---:|---:|---|
| ru→zh_Hans | `st2-zh-groundtruth.json`, entire set | 124 | 7 | **human inventory, real** |
| en→fr | `nfg-ui-fr-golden.json`, stratified | 120 | 27 | constructed |

The fr slice keeps **all 27 criticals**, then fills to 120 with majors and clean
in the source ratio. Criticals are the deciding number and there are few of
them; subsampling them would put the whole run inside the noise floor.

Both pairs are needed because the per-language split is the effect most likely
to decide this: the incumbent seat 1 runs REAL@14 2/14 on zh_Hans against 11/14
on fr. A single-pair run would hide that.

`col4-judge-golden.json` (919 units) is **not touched** and stays sealed to
confirm whatever the chat concludes.

## Repeats: n = 3

The measured noise floor on this project is a 20–32% flip rate at ≥flag. At
n = 1 a difference of ten points on 34 criticals is not distinguishable from
resampling the same model twice, and the post-hoc comparison would be reading
noise. Three repeats per arm is the floor at which an interval can be reported
instead of a point.

## Fixed constants

Anything in this list that varies between arms invalidates the run under R3.

- **Prompt** — `judge_prompts/verdict.txt`, byte-identical, hash recorded in the
  run manifest. No wording change for arm B, however tempting.
- **`project_context`** — same string for every arm; do not read it per project.
- **`JUDGE_BATCH_SIZE = 5`**, and **identical batch composition and order** in
  every arm and every repeat: sort by `unit_id`, take consecutive groups of
  five. Positional bias inside a batch is unmeasured on this project;
  different batching would turn it into a fake model difference.
- **`JUDGE_MAX_REPAIR_ATTEMPTS = 0`** and `writable_ids = set()`. This run scores
  judgement only. Repair would mutate the corpus between repeats.
- **Sampling parameters** — same values across arms, recorded.
- **Reasoning mode** — fixed **per model**, via the canonical mode map, and
  identical wherever that model appears: both stages, both gateways, every
  configuration. This is the one constant deliberately not uniform across
  slots, because a uniform setting makes a Qwen member fail 8 of 8 batches.
  `deepseek-v4-pro` sends no reasoning field in either arm, so it runs its own
  default thinking on both gateways — that is what makes the per-model gateway
  diagnostic a comparison of one thing. Whatever mode stage 1 records for a
  model is carried into stage 2 unchanged.
- **Slot order** — members sorted by model id, lower to seat 1. Mechanical, so
  a configuration has exactly one request form.
- **Verdict derivation** — from `max_severity` in code, per invariant 4. The
  model's own `verdict` field is logged but never read for a decision.
- **Response schema and parser** — the structured-output schema and the
  verdict parser are part of the constant too; record their versions/hashes in
  the manifest alongside the prompt hash.
- **Retry and timeout policy** — one budget for every model, configuration,
  stage and gateway, fixed before stage 1: `JUDGE_TRANSPORT_RETRIES`, the
  backoff schedule, the per-attempt timeout, and whether `504`/`524` are
  retryable. This is not a transport detail: it moves unparsed rate, p95, cost
  and flip rate, and it moves them more for a slow model than a fast one, so an
  uneven budget would silently re-rank candidates.

## Cache isolation, or n = 3 is a lie

`judge_loop._cached_verdict` reuses the newest parsed two-seat verdict whenever
target, context and the judge-model pair are unchanged. A runner built on
`run_judge_batch` therefore serves repeats 2 and 3 of an unchanged arm from
the cache of repeat 1: n = 3 collapses to n = 1, the logged requests
never happen, and the flip-rate row measures nothing. The same path also
writes `JudgeVerdict` rows, breaking the read-only scope.

Requirements on the runner that executes this plan:

- **Every batch execution in both stages resolves to at least one live LLM
  call** — 912 in stage 1, 735 or 882 in stage 2, so **1 647 or 1 794 batch
  executions in total**. That is the count of scheduled executions, **not** the
  count of provider attempts: with retries enabled a single execution can spend
  two or three. Attempts and spend are therefore ≥ these numbers and are only
  known after the run. Either the runner is offline (reads units from the JSON
  sets, writes no `JudgeVerdict` rows), or it explicitly bypasses the verdict
  cache and partitions any persistence by (stage, arm, repeat).
- **Attempt budget.** Each execution's attempts are logged and counted, and the
  run carries a hard ceiling — attempts ≤ 2 × executions, i.e. ≤ 3 588 — at
  which the run stops and reports rather than spending further. A candidate
  burning its way toward that ceiling has already failed the unparsed gate;
  reaching it is a finding, not an accident.
- Stage 1 and stage 2 are cache-isolated **from each other as well**. They
  share models and a prompt, so a stage-2 request whose target and context
  happen to match a stage-1 one would otherwise be served from it.
- Caches are disabled **symmetrically**: no arm or repeat may read a verdict
  another arm or repeat produced.
- Each request logs `cache_status: live | hit`; a single `hit` invalidates
  the repeat it appears in.
- An offline runner must build requests through the production machinery
  classes (`RoutedLLMTranslation` / `RoutedLiteLLMTranslation.get_chat_payload`)
  so schema, `provider` handling, timeouts and model resolution match
  production. If it serializes payloads itself, it must attach a snapshot of
  the production payload per arm and prove byte-level equivalence first —
  otherwise the per-model gateway diagnostic compares against a LiteLLM path
  that does not exist in production, and arm B's own numbers are not the
  deployed engine's.

## Gateway pinning, because the gateway is the thing under test

- **OpenRouter:** `allow_fallbacks: false`, `provider.order` pinned to a single
  provider, and the actual serving provider logged per request.
- **LiteLLM:** log the upstream provider and the resolved model version string
  per request.

Without this, a silent reroute to a different quantisation shows up as a model
difference and the run answers the wrong question.

## What the neighbouring localizer already proves, and what it does not

`cathedral` — the customer's previous localizer, `Answer_Machine/localization`,
on **the same VPS as this Weblate** (`/home/dev01/localization`, containers
`cathedral_server` / `cathedral_postgres`) — runs in production against **the
same proxy host** with `ACTIVE_AI_PROVIDER=litellm`,
`LITELLM_MODEL=deepseek-ai/deepseek-v4-flash` and
`LITELLM_REVIEW_MODEL=QWEN3.7-plus`
(`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md:14-19`,
read from `/app/litellm_api_helper.py` and `/app/.env` in the running
container; the system itself is analysed in
`docs/llm-first/research/2026-08-11-cathedral-localizer-analysis.md`).

**What it proves:** the proxy carries this workload in production, on a
DeepSeek + Qwen combination, continuously. The transport objection that stopped
the earlier seat search is not a property of the host.

**What it does not prove:** anything about which models to pick. Cathedral is a
translator plus a reviewer in a cascade, not a two-seat collegium of equals, and
a plan that hard-coded "DeepSeek + Qwen because cathedral uses them" would be
choosing by analogy — precisely what R3 forbids
(`docs/llm-first/plans/2026-08-26-judge-provider-failover.md:41-43`). Its
default route, `deepseek-ai/deepseek-v4-flash`, returns **403 on our key**.

**Where it is directly useful — request discipline.** The two systems differ on
the same host as follows:

| | judge (`weblate/trans/judge.py`) | cathedral |
|---|---|---|
| `response_format` | `json_schema`, `strict: true` | never sent |
| `max_tokens` | never sent | always, 8192–32768 per model |
| request timeout | 300 s flat | 120–420 s per model, +30 s per retry |
| retries | 1, only on `403`/`429` | up to 5, exponential backoff |
| gateway timeout | not handled | `504`/`524` retried separately |
| streaming | no | no |

The A/B on those differences settled that **payload shape does not predict the
~30.5 s reset**: the arm that resets differs per model, and the no-schema arm
resets too. So the schema stays — it is the invariant that keeps unparsed
verdicts from becoming findings, the failure that cost cathedral 19.6% of its
verdicts. What cathedral actually has is **retry and timeout discipline**.

**That discipline is not R3-neutral, and calling it so would be wrong.** The
retry budget directly moves four quantities this run decides on: unparsed rate
(a gate), p95 latency and cost (the tie-breakers), and flip rate. Worse, it
moves them **unevenly between candidates** — a model running close to the
~30.5 s ceiling gains far more from a second attempt than one answering in
5 s — so the retry budget changes the ranking itself.

It is therefore a **pinned constant of the run**, not an improvement adopted
mid-flight:

- One retry policy, decided before stage 1 and identical for every model, every
  configuration, both stages, both gateways. Recorded in the manifest:
  `JUDGE_TRANSPORT_RETRIES`, the backoff schedule, the per-attempt timeout, and
  whether `504`/`524` are retryable.
- **Attempts are logged separately from batches.** A batch that succeeded on
  attempt 3 cost three requests and three latencies; folding that into one row
  hides cost and flatters p95.
- Raising the budget above today's single retry is allowed, but as a decision
  taken once, up front, for everyone. Changing it after seeing a candidate's
  unparsed rate would be tuning the instrument to the result.

Streaming is not borrowed — cathedral does not stream either, and adding it
would invalidate every measurement under R3.

## Run size

Cost is per **model**, never per configuration: configurations are composed
offline from per-model rows, so every singleton and every pair is free once its
members are measured.

**Stage 1.** 376 records ÷ 5 = 76 batches per model per repeat, ×3 repeats =
**228 per model**. Four candidates = **912**, and 684 if GLM is dropped before
stage 1. All ten configurations — four singletons, six pairs — fall out of
those rows.

**Stage 2.** 244 units ÷ 5 = 49 batches per model per repeat, ×3 repeats =
**147 per model**.

| models | requests |
|---|---:|
| `deepseek/deepseek-v4-pro` @ OpenRouter, shared by A1, A2, A3 | 147 |
| `qwen/qwen3-235b-a22b-2507`, `openai/gpt-5.4-mini`, `anthropic/claude-haiku-4.5` | 441 |
| arm B on LiteLLM — 1 model | 147 |
| arm B on LiteLLM — 2 models | 294 |
| **stage 2 total** | **735** singleton / **882** pair |

**The gateway diagnostic is free only when arm B contains
`deepseek-v4-pro`.** It compares that model's OpenRouter rows against its
LiteLLM rows; the OpenRouter side always exists (A1–A3 share it), but the
LiteLLM side exists only if stage 1 put DeepSeek in arm B. If it did not, the
diagnostic either costs an extra 147 or is omitted, and omitting it is
acceptable — it is a diagnostic, not a gate.

| stage-1 outcome | stage 2 | + diagnostic | whole run |
|---|---:|---:|---:|
| singleton **with** DeepSeek | 735 | free | **1 647** |
| pair **with** DeepSeek | 882 | free | **1 794** |
| singleton **without** DeepSeek | 735 | +147 | 1 647 / **1 794** |
| pair **without** DeepSeek | 882 | +147 | 1 794 / **1 941** |

Stage 1 is 912 in every row. Dropping A2 and A3 removes 294 from stage 2.

Log per request: latency, prompt and completion tokens, cost, serving provider,
cache status, `finish_reason`, and whether the response parsed.

**A 200 is not a success.** LiteLLM forwards `content == ""` with
`finish_reason == "stop"` and `total_tokens == 0` as a completed call, raising
no error and triggering no retry (BerriAI/litellm#38199, open, filed
2026-08-25; the sibling MiniMax fix #38212 for #38197 is unmerged). The runner
must classify that triple as a **failed request** in both stages — not a parsed
PASS, not a zero-cost success. Undetected it understates the unparsed rate that
gates both stages and silently zeroes the cost that breaks ties.

For arm B, cost comes from LiteLLM's reported usage if the proxy returns it,
otherwise from the upstream price table × counted tokens; decide which before
the run, because cost is the tie-breaker and must be computable.

## Output for the Opus chat

Two files. The rollup is for orientation, the diff pack is what actually gets
read.

**1. `rollup.md`** — one table, fits in a single chat message.

Per arm, and per language pair inside each arm:

- critical recall of the arm's own verdict — union for a pair, own label for a
  singleton — mean over repeats ± range
- recall at ≥flag
- **unparsed rate** per model — a malformed answer is a miss on that unit, but
  it must be visible separately from a parsed PASS/FAIL, because a high rate
  means the arm is not viable at all rather than slightly worse
- false-flag rate on clean units
- **member-level non-overlap**: units caught by one member only. If the second
  member's unique contribution is near zero, it is not earning its cost
  regardless of the arm's total. Omitted for a singleton arm, which has no such
  row.
- flip rate across the three repeats — the noise floor re-measured on this run
- $ per 1 000 units, p95 latency

**2. `diff-pack.md`** — capped at 50 units, ordered by decision weight:
disagreements on criticals first, then on clean units, then the rest.

Per unit, side by side: `unit_id`, language pair, source, target, gold label and
severity, and for **each arm** the derived verdict plus every error row
(`severity` / `category` / `description`). Truncating the descriptions defeats
the purpose — the reason to read this in a chat is to see *why* an arm flagged
something, which no aggregate carries.

Also emit `caught.jsonl`: one row per (arm, seat, repeat) with the **set of
caught `unit_id`s**, not a count. Every derived number above is recoverable from
it; none of it is recoverable from the counts.

## Decision rule, registered before the run

> Arm B replaces arm A if all of the following hold:
>
> 1. Its unparsed rate is ≤ 5% in every repeat. Above that the arm is not
>    viable regardless of anything else.
> 2. Its critical recall is higher than arm A's on **both** language pairs.
>    With 7 criticals in zh the recall steps in 1/7 ≈ 14-point increments, so
>    "higher" means at least one whole step, 2/7; a 1/7 difference is a coin
>    flip and counts as indistinguishable.
> 3. The recall intervals over the three repeats do not overlap arm A's.
> 4. Its false-flag rate on clean units is ≤ 10% in every repeat. The ~60
>    clean units per pair carry a ±7-point binomial error, so this ceiling
>    cannot be certified — report the observed rate with its interval beside
>    the threshold. It stays in the gate anyway: without it an arm could win
>    on recall while producing an alert volume no reviewer can work through.
>
> Criteria 1 and 4 are viability gates: an arm that fails either is out and
> cannot win anything, including the cost tie-break. If both arms pass 1 and
> 4 but 2 or 3 fails, the arms are indistinguishable on this sample and the
> choice falls to $ per 1 000 units and p95 latency.
>
> If the arms are indistinguishable but the diff pack shows systematically
> different error *categories* (e.g. arm B catches terminology but misses
> mistranslation), that is evidence for the next run's design, not a reason to
> move the threshold of this one.

Fixing this now is the difference between a measurement and a post-hoc
justification. The chat reads the diff pack to understand *why* the arms
differ; it does not get to move the threshold afterwards.

If member-level non-overlap shows the second member earning nothing in the
winning arm, the migration decision is for **one model**, and the cost and
latency figures must be re-stated for that configuration before the chat
concludes. If stage 1 already returned a singleton, this is moot.

## Known limits of this run

- **The pair-level model/gateway confound is not removed, only declared.** The
  incumbent seat-2 route does not exist on the proxy, so the two arms differ in
  seat-2 model and in request path simultaneously. The run answers whether
  arm B replaces arm A as deployed; it does not attribute the difference. The
  seat-1 diagnostic covers one model and is not a pair-level control.
- Constructed fr defects measure detection of injected faults. Only the zh slice
  measures real MT errors. Report the two separately; do not pool them into one
  recall number.
- 60-odd clean units per pair cannot certify a `false-reject ≤ 2%` gate at
  the pair level, and the 10% ceiling in the decision rule carries a ±7-point
  binomial error on this sample. It remains a guardrail against unusable
  alert volume, not a certification of the rate. Likewise, 7 criticals in zh
  make the recall advantage coarse; the decision rule's 2/7 step follows
  from that.
- Stage 1 screens on en→fr only, with synthetic and LLM-assigned binary labels
  and no severity. It can eliminate a candidate on parseability or flag volume
  and rank on gross recall; it cannot predict zh recall, which is the effect
  most likely to decide stage 2. A candidate that wins stage 1 is therefore
  not expected to win stage 2 — that is the point of running stage 2 at all.
- The prompt is specialised to the incumbent models by history. Arm B is
  therefore measured as *the best pair for this prompt*, which is the correct
  question for a drop-in replacement and the wrong one for a redesign.
