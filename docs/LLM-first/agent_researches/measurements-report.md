# Judge Measurements — Extracted Numbers, Conclusions, and Gaps

Extracted from five documents across the LLM-as-judge calibration effort.
Every number carries `path:line` and verbatim source text.

---

## 1. Every Measured Number

### 1.1 Phase 0 — Model Screening on dev (fr, 376 records)

`docs/LLM-first/2026-08-13-phase0-measurements.md:148-161`

| Model | recall H | spec. H | recall R | false critical | unparsed | $ |
|---|---|---|---|---|---|---|
| `deepseek/deepseek-v4-pro` | **96.3** [92.5, 98.2] | **96.8** [93.2, 98.5] | **100** [92.9, 100] | 0.0 [≤2.0] | 0 | 0.145 |
| `qwen/qwen3-235b-a22b-2507` | 88.8 [83.4, 92.5] | 91.5 [86.7, 94.7] | 96.0 [86.5, 98.9] | 0.0 [≤2.0] | 0 | **0.016** |
| `cohere/command-a` | 87.7 [82.2, 91.7] | 86.2 [80.6, 90.4] | 72.0 [58.3, 82.5] | 0.0 [≤2.0] | 0 | 0.267 |
| `openai/gpt-5.4-mini` | 84.5 [78.6, 89.0] | 91.5 [86.7, 94.7] | 90.0 [78.6, 95.7] | 0.5 [≤2.9] | 0 | 0.085 |
| `anthropic/claude-haiku-4.5` | 84.0 [78.0, 88.5] | 88.4 [83.0, 92.2] | 100 [92.9, 100] | 2.6 [≤6.0] | 0 | 0.142 |
| `openai/gpt-5.4-nano` | 70.6 [63.5, 76.8] | 84.7 [78.8, 89.1] | 52.0 [38.5, 65.2] | 1.1 [≤3.8] | **10** | 0.025 |
| `anthropic/claude-sonnet-5` | 69.5 [62.6, 75.7] | **100** [98.0, 100] | 98.0 [89.5, 99.6] | 0.0 [≤2.0] | 0 | 0.550 |

> `phase0-measurements.md:163-164`: "Граница H — «нужен человек» (flag или reject против pass). Граница R — «есть critical» (reject против остального). Скобки — Wilson 95%."

### 1.2 Phase 0 — Cascade Results on train+dev (fr, 483 records)

`docs/LLM-first/2026-08-13-phase0-measurements.md:205-212`

| Arm | A | B | recall A | escalation | recall through | spec. through | false critical | $/1000 |
|---|---|---|---|---|---|---|---|---|
| single | `deepseek-v4-pro` | — | — | — | **94.0** [90.1, 96.4] | 96.8 [93.8, 98.4] | 0.4 [≤2.2] | 0.284 |
| single | `qwen3-235b` | — | — | — | 89.2 [84.6, 92.6] | 92.0 [88.0, 94.8] | 1.2 [≤3.5] | **0.046** |
| single | `gpt-5.4-mini` | — | — | — | 87.5 [82.6, 91.2] | 92.4 [88.5, 95.1] | 1.6 [≤4.0] | 0.221 |
| mirror | `qwen3-235b` | `deepseek-v4-pro` | 90.5 | 47.6% | 87.1 [82.1, 90.8] | 98.4 | 0.0 | 0.204 |
| cascade | `deepseek-v4-pro` | `qwen3-235b` | 92.7 | 45.8% | 84.9 [79.7, 88.9] | 98.8 | 0.0 | 0.306 |
| mirror | `claude-sonnet-5` | `deepseek-v4-pro` | 78.4 | 38.1% | 77.2 [71.3, 82.1] | 100 | 0.0 | 1.518 |
| cascade | `deepseek-v4-pro` | `claude-sonnet-5` | 90.5 | 43.9% | 70.7 [64.5, 76.2] | 100 | 0.0 | 1.223 |

### 1.3 Phase 0 — Overturn Analysis (Why Cascades Fail)

`docs/LLM-first/2026-08-13-phase0-measurements.md:220-227`

| Arm | overturned | correct | **wrong** |
|---|---|---|---|
| cascade `deepseek` → `sonnet-5` | 48 | 2 | **46** |
| cascade `deepseek` → `qwen` | 21 | 3 | **18** |
| mirror `sonnet-5` → `deepseek` | 5 | 2 | **3** |
| mirror `qwen` → `deepseek` | 24 | 16 | 8 |

> `phase0-measurements.md:229-231`: "Это не свойство конкретной пары, а арифметика конструкции: если ступень B имеет право снимать флаг, сквозной recall равен `recall(A) × recall(B | эскалированные)` и не может превысить recall ступени A."

### 1.4 Phase 0 — Union / Collegium on train+dev (fr, 483 records, reconstruction)

`docs/LLM-first/2026-08-13-phase0-measurements.md:305-310`

| | `deepseek` alone | union |
|---|---|---|
| recall H | 94.0 | **96.1-98.3** |
| specificity H | 96.8 | **90.8-91.2** |
| clean strings in flag, out of 251 | 6 | 22-23 |
| auto-pass on prod pool | 80.3% | **74.9-75.0%** |
| miss among auto-pass | 1.36% | **0.41-0.95%** |
| full COL4 fr cost | $1.12 | $1.30 |

> `phase0-measurements.md:316-317`: "Объединение считается с двух сторон и даёт `215 + 8 = 223` либо `210 + 18 = 228` дефектов из 232."

### 1.5 Phase 0 — Union Measured Directly on train+dev (fr, mixed split)

`docs/LLM-first/2026-08-13-phase0-measurements.md:330-335`

| | predicted by reconstruction | measured |
|---|---|---|
| union recall | 96.1-98.3% | **97.8%** |
| union specificity | 90.8-91.2% | **91.6%** |

> `phase0-measurements.md:337-338`: "Реконструкция попала по recall и была на 0.4 пункта пессимистична по специфичности."

### 1.6 Phase 0 — Sealed Test (fr, 433 records: 167 clean, 146 major, 120 critical)

`docs/LLM-first/2026-08-13-phase0-measurements.md:345-352`

| | recall H | spec. H | recall R | critical → pass | major → pass | false flag | terminology |
|---|---|---|---|---|---|---|---|
| `deepseek` alone | 90.6 [86.5, 93.6] | **98.2** [94.9, 99.4] | 95.0 [89.5, 97.7] | **3 of 120** | 22 of 146 | **1.8%** | 97.4 |
| `qwen` alone | 92.1 [88.2, 94.8] | 89.8 [84.3, 93.5] | 97.5 [92.9, 99.1] | 0 of 120 | 21 of 146 | 10.2% | 84.4 |
| union | **97.4** [94.7, 98.7] | 88.6 [82.9, 92.6] | **98.3** [94.1, 99.7] | **0 of 120** | **7 of 146** | 11.4% | **98.7** |

`phase0-measurements.md:354-357`:
> "Ошибки моделей в существенной мере не пересекаются: помечено обеими 228, только `deepseek` 16, только `qwen` 34."

### 1.7 Phase 0 — Model Selection Did Not Reproduce

`docs/LLM-first/2026-08-13-phase0-measurements.md:360-367`

| | train+dev | test |
|---|---|---|
| `deepseek` recall H | 94.8 | **90.6** |
| `qwen` recall H | 87.9 | **92.1** |
| McNemar | b=35, c=11, **p = 0.0005** | b=30, c=20, **p = 0.20** |

> `phase0-measurements.md:368-370`: "На отборочных данных `deepseek` превосходил `qwen` значимо; на запечатанных они статистически неразличимы, и по recall `qwen` формально впереди."

### 1.8 Phase 0 — Go/No-Go Thresholds Against Sealed Test

`docs/LLM-first/2026-08-13-phase0-measurements.md:388-395`

| Plan threshold | Requirement | `deepseek` alone | union | Status |
|---|---|---|---|---|
| reject-recall on critical | ≥95%, CI lower ≥88% | 95.0 [89.5, 97.7] | 98.3 [94.1, 99.7] | **passed both** |
| (flag ∪ reject)-recall on terminology | ≥80% | 97.4 [91.0, 99.3] | 98.7 | **passed both** |
| false-flag on clean | ≤10% | 1.8% [≤5.1] | 11.4% | `deepseek` passed, union **failed** |
| false-reject / false-critical on clean | ≤2% | 0 of 167, CI upper 2.2% | 0 of 167, same bound | **cannot certify either** |
| critical in auto-pass | ~0 | **3 of 120** | **0 of 120** | `deepseek` **failed**, union passed |
| auto-pass | ≥90% | 82.0% | 72.9% | **failed by all, cannot be** |
| miss-rate among auto-pass | major ≤1-2% | 2.09% [1.33, 2.88] | 0.65% [0.19, 1.18] | `deepseek` borderline, union passed |

### 1.9 Phase 0 — Auto-Pass ≥90% Is Arithmetically Impossible

`docs/LLM-first/2026-08-13-phase0-measurements.md:375-384`

| Judge | recall H | spec. H | auto-pass | miss among auto-pass |
|---|---|---|---|---|
| perfect | 100 | 100 | **81.8%** | 0 |
| `deepseek` | 90.6 | 98.2 | 82.0% | 2.09% |
| `qwen` | 92.1 | 89.8 | 74.9% | 1.92% |
| union | 97.4 | 88.6 | 72.9% | **0.65%** |

> `phase0-measurements.md:385-386`: "Формально: auto-pass >= 90% при p = 0.182 требует recall <= 54.9%, то есть судью, который **пропускает 45% дефектов**."

### 1.10 Phase 0 — Prompt/Glossary Bloat: Prod vs Mirror

`docs/LLM-first/2026-08-13-phase0-measurements.md:91-100`

| Metric | Prod, batch 10 | Mirror, 2026-08-13 |
|---|---|---|
| prompt_tokens | 67,828 | 178,038 |
| completion_tokens | 8,603 | 92,872 |
| cost_usd | 0.0348 | 0.2813 |
| requests | 15 | 17 |
| length_ratio_avg | 1.274 | 1.418 |
| glossary occurrences | 16 | 68 |

### 1.11 Phase 0 — family-bias Check (Specificity on Clean Strings by Labeler)

`docs/LLM-first/2026-08-13-phase0-measurements.md:186-193`

| Judge | Fable-labeled (n=65) | Codex-labeled (n=124) |
|---|---|---|
| `claude-haiku-4.5` | 81.5 | 91.9 |
| `claude-sonnet-5` | 100 | 100 |
| `deepseek-v4-pro` | 95.4 | 97.6 |
| `qwen3-235b` | 95.4 | 89.5 |
| `gpt-5.4-mini` | 93.8 | 90.3 |

> Hypothesis not confirmed; deviation goes the opposite direction — Anthropic judges are stricter on Anthropic-labeled strata.

### 1.12 Phase 0 — Track C: Repeat Drift (fr, 3941 units, glossary excluded)

`docs/LLM-first/2026-08-13-phase0-measurements.md:532-540`

| Metric | Value |
|---|---|
| groups with identical normalized source | 146 |
| 372 units (9.4% of corpus) | |
| diverge, raw | 106 (72.6%) |
| diverge, after normalization | 84 (**57.5%**) |
| units in diverging groups | 215 |
| **seen by stock `inconsistent` check** | **0 (0.0%)** |
| groups where ≥2 units share context | 0 |

### 1.13 Phase 0 — Token Cost per 1000 Strings

- `deepseek` single: $0.284/1000 (`phase0-measurements.md:205`)
- `qwen` single: **$0.046/1000** (`phase0-measurements.md:206`)
- `qwen` — $0.042/1000 vs `deepseek` $0.62-0.75/1000 spread (`phase0-measurements.md:418`)
- Full COL4 fr: `deepseek` $2.4-3.0, plus `qwen` $0.17 (`phase0-measurements.md:418-419`)
- Union full COL4 fr: $1.30 (`phase0-measurements.md:310`)

### 1.14 Phase 0 — Total Spend

`docs/LLM-first/2026-08-13-phase0-measurements.md:615-627`

| Article | $ |
|---|---|
| A2, two harness runs | 0.31 |
| schema probe and parameter isolation | 0.03 |
| screening 7 models on dev | 1.23 |
| single controls on train+dev | 0.27 |
| cascade and mirror `deepseek` / `sonnet-5` | 1.32 |
| cascade and mirror `deepseek` / `qwen` | 0.25 |
| mixed split train+dev, both models | 0.32 |
| sealed test, both models | 0.34 |
| track C | 0.00 |
| **total** | **4.07** |

### 1.15 Severity Recalibration — Noise Floor (zh_Hans, n=5, arm A baseline)

`docs/LLM-first/2026-08-18-severity-recalibration-measurements.md:88-96`

| Configuration | flip ≥flag | any-severity flip |
|---|---|---|
| `deepseek` | 36/124 (29%) | 45/124 |
| `qwen` | 27/124 (22%) | 35/124 |
| collegium | 39/124 (31%) | 56/124 |

> `severity-recalibration-measurements.md:98-99`: "Прежний замер (2026-08-14, n=2, только qwen) давал 16%. На n=5 видно, что нестабильность **выше**, а не ниже: 22-31% против 16%."

### 1.16 Severity Recalibration — Baseline Severity Matrix (zh_Hans, collegium, n=5)

`docs/LLM-first/2026-08-18-severity-recalibration-measurements.md:107-116`

```text
truth\judge   none  minor  major  crit
none          315    13     62     25
minor          57     2     15     11
major          29     1     32     23
critical        1     1      5     28
```

> `severity-recalibration-measurements.md:118-121`: "**Ложные critical: 25 наблюдений на чистых строках** (из 415 чистых наблюдений) плюс 11 на minor — судья регулярно ставит `critical` на корректный перевод."

> `severity-recalibration-measurements.md:122-123`: "**Заниженные critical: 7 из 35** наблюдений настоящего critical получили ниже critical."

### 1.17 Severity Recalibration — Baseline Metrics (zh_Hans, collegium, n=5)

`docs/LLM-first/2026-08-18-severity-recalibration-measurements.md:104-105`

| Configuration | missed_crit | false_crit | REAL@14 | REAL@24 | FP (none→≥major) | noise ≥flag |
|---|---|---|---|---|---|---|
| `deepseek` alone | 6/7 | 1 | 2/14 | 4/24 | 8/83 | 36/124 |
| `qwen` alone | 2/7 | 4 | 11/14 | 17/24 | 14/83 | 27/124 |
| **collegium** | **1/7** | **5** | **11/14** | **18/24** | **17/83** | 39/124 |

### 1.18 Severity Recalibration — Final Noise Floor (zh_Hans, n=5, all arms)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:72-87`

| Arm | Config | flip ≥flag | any-severity |
|---|---|---|---|
| A | `deepseek` | 36/124 (29%) | 45/124 |
| A | `qwen` | 27/124 (22%) | 35/124 |
| A | collegium | 39/124 (31%) | 56/124 |
| B | `deepseek` | 40/124 (32%) | 50/124 |
| B | `qwen` | 25/124 (20%) | 38/124 |
| B | collegium | 36/124 (29%) | 56/124 |
| C | `deepseek` | 22/124 (18%) | 32/124 |
| C | `qwen` | 31/124 (25%) | 46/124 |
| C | collegium | 30/124 (24%) | 51/124 |

> `severity-recalibration-final.md:89-90`: "Шумовой пол **20-32% по коллегии** — рубрика (C) заметно снижает нестабильность (39→30/124, и 31→24% коллегии)."

### 1.19 Severity Recalibration — Arm A Reproduces Baseline

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:93-98`

| | missed_crit | false_crit | REAL@24 | FP |
|---|---|---|---|---|
| baseline armC-fixedrule (2026-08-14, n=2) | 2 / 1 | 5 / 3 | 17 / 17 | 16 / 17 |
| arm A (2026-08-19, n=5, medians) | 1 | 5 | 18 | 17 |

### 1.20 Severity Recalibration — Collegium Comparison (zh_Hans, n=5, medians)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:100-106`

| Arm | missed_crit | false_crit | REAL@14 | REAL@24 | FP | prec@≥major | rec@≥major | noise ≥flag |
|---|---|---|---|---|---|---|---|---|
| A | 1/7 | 5 | 11/14 | 18/24 | 17/83 | 0.51 | 0.75 | 39/124 |
| B | 5/7 | 4 | 10/14 | 16/24 | 14/83 | 0.54 | 0.67 | 36/124 |
| C | 2/7 | 5 | 12/14 | 17/24 | 14/83 | 0.56 | 0.71 | 30/124 |

### 1.21 Severity Recalibration — Severity Matrices (truth × judge, 5 collegium runs = 620 observations)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:108-111`

```text
A:  none 315/13/62/25   minor 57/2/15/11   major 29/1/32/23   crit 1/1/5/28
B:  none 314/30/52/19   minor 56/2/17/10   major 24/6/38/17   crit 4/4/16/11
C:  none 321/25/43/26   minor 58/1/20/6    major 21/3/32/29   crit 4/3/5/23
```

### 1.22 Severity Recalibration — R1 Gate (arm B — mandatory `description`)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:115-120`

| | prec med | rec med |
|---|---|---|
| baseline (A) | 0.51 | 0.75 |
| arm B | 0.54 | 0.67 |

> `severity-recalibration-final.md:120-122`: "R1 не проходится: рост точности куплен падением recall — ровно тот обмен, который гейт запрещает."

### 1.23 Severity Recalibration — R2 Gate (severity rubric)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:126-133`

| | missed_crit per run | zero in all | false_crit per run | median |
|---|---|---|---|---|
| A | 1, 1, 2, 2, 1 | — | 7, 4, 5, 7, 2 | 5 |
| B | 5, 5, 4, 5, 5 | — | 2, 5, 4, 6, 2 | 4 |
| C | 2, 2, 5, 2, 1 | — | 7, 6, 4, 4, 5 | 5 |

> `severity-recalibration-final.md:134-135`: "**R2 не проходится ни в одном плече.** Ни один прогон не отловил все 7 critical; медиана ложных critical 4-5 против порога ≤2."

### 1.24 Severity Recalibration — Arm C Improvement Profile

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:148-155`

- noise ≥flag: 39→30/124 collegium (−23%)
- REAL@14: 11→12/14 (only arm that caught all 14 anchor defects in at least one run: run1 gave 14/14)
- FP deepseek: 8→4/83 (−50%)
- precision 0.51→0.56 (best in collegium)

### 1.25 Severity Recalibration — Persistent Blind Spots

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:158-163`

- **24207** — missed by all judges in all arms and all 15 runs: placeholder rendering defect
- **24208** (broken word order) — caught extremely rarely
- **24130** (disputed anchor) — unstable: 1-3 misses out of 5 per seat
- Cluster 24180-182 (fortification template mush) — qwen catches reliably, deepseek misses in 3-5 of 5 repeats

### 1.26 Severity Recalibration — Arm D (Render-Preview) Collegium C vs D

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:215-221`

| metric | C | D |
|---|---|---|
| missed_crit (median/7) | 2 | **1** |
| runs with 0 missed critical | 0/5 | **2/5** |
| false_crit (median) | 5 | 6 |
| REAL@14 | 12/14 | 12/14 |
| REAL@24 | 17/24 | 17/24 |
| noise ≥flag | 30/124 | 31/124 |
| matrix cell `critical→none` (sum 5 runs) | 4 | **0** |

### 1.27 Severity Recalibration — Arm D Render-Class Per-Unit (collegium, severity by runs)

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:227-233`

| unit | defect | C | D |
|---|---|---|---|
| 24207 | name in slot `{1}` renders wrong | minor·none·minor·major·minor | **major·CRIT·CRIT·CRIT·CRIT** |
| 24130 | role inversion `{[PARAM0]}`/`{[PARAM1]}` | CRIT·none·none·none·CRIT | **CRIT×5** |
| 24180 | omission of "fortifications" | CRIT·CRIT·major·CRIT·CRIT | major·major·CRIT·CRIT·CRIT |
| 24181 | same | CRIT·CRIT·major·CRIT·CRIT | major·major·CRIT·CRIT·CRIT |
| 24182 | same | CRIT·CRIT·major·CRIT·CRIT | major·major·major·CRIT·CRIT |

### 1.28 Severity Recalibration — Costs

- Arm D: ~$0.56 (deepseek $0.51, qwen $0.05) — `severity-recalibration-final.md:213`
- Top-up (arm B whole, arm C whole, re-shot arm B deepseek run4-5): ~$0.77 — `severity-recalibration-final.md:167`
- Total within $4 cap — `severity-recalibration-final.md:168`

### 1.29 Phase 0 — Temperature Schema Probe

`docs/LLM-first/2026-08-13-phase0-measurements.md:130-137`

| Model | schema+require+temp | schema+require | schema+temp | schema |
|---|---|---|---|---|
| `claude-sonnet-5` | **404** | OK | OK | OK |
| `gpt-5.4-mini` | **404** | OK | OK | OK |
| `gpt-5.4-nano` | **404** | OK | OK | OK |
| `claude-haiku-4.5` | OK | OK | OK | OK |

> `phase0-measurements.md:139-141`: "Reasoning-тиры не декларируют поддержку `temperature`, и при `require_parameters: true` роутер не находит эндпоинта."

### 1.30 System Prompt — Verdict-Invariant Consistency

`docs/LLM-first/2026-08-13-phase0-measurements.md:119-120`

> "Вердикт выводится из максимальной severity в коде, а не берётся из поля `verdict` модели — инвариант 4 архитектуры; расхождение между двумя считается отдельным счётчиком и оказалось пренебрежимым (0 у deepseek/haiku/command-a/gpt-5.4-mini, 1 у qwen, 8 у sonnet-5)."

---

## 2. What the Measurements Concluded

### 2.1 Which Models to Use

**Two models, collegium (max-severity union): `deepseek/deepseek-v4-pro` and `qwen/qwen3-235b-a22b-2507`.**

`phase0-measurements.md:411-412`:
> "1. **Судить двумя моделями сразу: `deepseek/deepseek-v4-pro` и `qwen/qwen3-235b-a22b-2507`, вердикт — максимум severity.**"

`phase0-measurements.md:416-417`:
> "2. **Второй судья стоит 5% бюджета первого.** `qwen` — $0.042 на 1000 строк против $0.62-0.75 у `deepseek`."

`phase0-measurements.md:420-421`:
> "За эти семнадцать центов покупается +6.8 пункта recall и закрытый релизный гейт — лучшая покупка во всём замере."

### 2.2 How Many Seats

**Two seats, no down-grading tier B.** Cascade rejected.

`phase0-measurements.md:428-429`:
> "4. **Ступень B в дизайновом виде — с правом снимать флаг — не добавлять ни при каком выборе моделей.** Замер каскадов однозначен: она снимает дефекты, а не шум."

### 2.3 Why Cascade Was Rejected

`phase0-measurements.md:44-47`:
> "**Каскад из двух ступеней проигрывает одиночному судье на всех четырёх измеренных конфигурациях, и проигрывает структурно, а не по невезению.**"

`phase0-measurements.md:216-218`:
> "Дизайн исходил из того, что ступень A настраивается агрессивно, а ложные тревоги снимает ступень B. Замер показывает, что первой посылки нет: **ступень A почти не даёт ложных тревог, которые стоило бы снимать.** Специфичность `deepseek` как ступени A — 97.6-99.2%."

`phase0-measurements.md:231-233`:
> "Каскад окупается только тогда, когда узкое место — ложные тревоги. Здесь узкое место — пропуски, и вторая ступень их усугубляет."

### 2.4 What Severity Thresholds Were Recalibrated To

**None — all gates failed.** The severity scale was not recalibrated; it was returned to design.

`severity-recalibration-final.md:137-139`:
> "R2 недостижима правкой промпта — подтверждено измеренно на полном прогоне. По правилу R3, severity-гейт **возвращается в дизайн** (больше не severity-only): либо слагается от «процентиль агрессивности» + отдельного блокер-слоя, либо пересматривается сама постановка severity."

### 2.5 What the Final Severity Scale Means

The severity scale (none/minor/major/critical) is defined by **player impact**:

`plans/2026-08-14-judge-severity-recalibration.md:153-156`:
> "Рубрика через **последствие для игрока**: `critical` = игрок не поймёт, что произошло, или получит неверную информацию; `major` = смысл искажён, но восстановим; `minor` = стиль и регистр."

But the scale **cannot be used as a gate** — the measurements show it is unreliable in both directions.

### 2.6 Model Selection Cannot Be Done on train+dev Alone

`phase0-measurements.md:422-425`:
> "3. **Не выбирать одну модель по train+dev.** Отбор давал `deepseek` значимое преимущество (p = 0.0005); на запечатанных данных различие исчезло (p = 0.20), а по recall вперёд вышел `qwen`."

### 2.7 Model Annotators Cannot Serve as Ground Truth

`severity-recalibration-measurements.md:72-79`:
> "**LLM-аннотатор как единственный эталон здесь даёт ложно-мягкую истину, согласную с судьёй** (ровно круг, о котором предупреждал план). Эталон — человеческий."

### 2.8 Render-Preview Should Be Default Judge Input

`severity-recalibration-final.md:246-247`:
> "4. **Продуктовый перенос.** Рендер-превью стоит забрать в **вход судьи по умолчанию** (дёшево, поднимает recall на плейсхолдер-critical) и независимо — в **UI юнита для продюсера**."

### 2.9 Auto-Pass ≥90% Must Be Rewritten

`phase0-measurements.md:430-431`:
> "5. **Порог auto-pass ≥90% переписать** — при доле дефектов 18.2% он требует судьи, пропускающего 45% дефектов."

### 2.10 false-reject ≤2% Cannot Be Certified with Current Stratum

`phase0-measurements.md:433-434`:
> "6. **Порог false-reject ≤2% переписать или расширить чистую страту**: 167 чистых строк не дают сертифицировать его даже при нуле ошибок."

---

## 3. Measurements That Contradict or Constrain a Design Choice

### 3.1 Cascade Design — Rejected

`phase0-measurements.md:428-429`: "Ступень B в дизайновом виде — с правом снимать флаг — не добавлять ни при каком выборе моделей."

46 of 48 overturns by tier B deleted real defects (`phase0-measurements.md:221`).

### 3.2 "Second Judge Only for Critical Escalation" Regulator — Rejected

`judge-native-ui-design.md:151-154` (via `severity-recalibration-final.md`):
> "«повышение до critical» вторым судьёй — самый шумный его сигнал (медиана ложных critical 4 за прогон), а его вклад в recall живёт в `flag`-вердиктах, которые регулятор отбрасывает. Конфигурация хуже полной коллегии по обеим осям; не включать."

### 3.3 Mandatory `description` Field — Does Not Improve Metrics

`severity-recalibration-final.md:120-122`: "R1 не проходится: рост точности куплен падением recall — ровно тот обмен, который гейт запрещает."

recall 0.75→0.67 at precision 0.51→0.54 (`severity-recalibration-final.md:118-119`).

### 3.4 Severity-as-Gate — Unreliable, Returned to Design

`severity-recalibration-final.md:137-139`: "R2 недостижима правкой промпта — подтверждено измеренно на полном прогоне."

No run caught all 7 critical; median false critical 4-5/124 against threshold ≤2 (`severity-recalibration-final.md:134-135`).

### 3.5 deepseek Is Weak on zh_Hans

`severity-recalibration-measurements.md:128-129`: "**`deepseek` — слабое место на этой паре языков**: REAL@14 2/14 против 11/14 у `qwen`."

### 3.6 Stock `inconsistent` Check Does Not Detect Repeat Drift

`phase0-measurements.md:551-552`: "Он не закрывает ничего: ноль из 84 расходящихся групп им помечены."

### 3.7 Model Annotators Reproduce Judge Blind Spots

`severity-recalibration-measurements.md:73-74`: "Они пометили **9 из 14** подтверждённых человеком дефектов ниже major."

### 3.8 Noise Floor Is 22-31%, Not 16%

`severity-recalibration-measurements.md:98-99`: "Прежний замер (2026-08-14, n=2, только qwen) давал 16%. На n=5 видно, что нестабильность **выше**, а не ниже: 22-31% против 16%."

### 3.9 deepseek Alone Misses 3 of 120 Critical on Sealed Test

`phase0-measurements.md:351`: "critical → pass: **3 из 120**" for `deepseek` alone. This is a release gate failure.

### 3.10 Union Exceeds false-flag ≤10% Threshold

`phase0-measurements.md:351`: union false-flag 11.4% — exceeds the ≤10% plan threshold.

---

## 4. Missing Measurements

This section is the most important. Each entry states what an engineer would need measured before letting this run against production strings, and whether the docs contain it.

### 4.1 Latency per Call — NOT MEASURED

**No document measures latency.** The phase 0 measurements track cost, token counts, and request counts, but not wall-clock time per API call. The token-cost table (`phase0-measurements.md:91-100`) shows prompt_tokens and completion_tokens, but no ms/call.

For a Celery task that will block a worker, latency matters: if a single `request_verdict` call takes 30s, two-seat collegium is 60s+ serial or 30s+ parallel. The code has `JUDGE_REQUEST_TIMEOUT = 120` (`judge-verdict-core.md:1422`) but no measurement of actual response time.

**Status: MISSING.** Not present in any of the five documents.

### 4.2 Latency of Two-Seat Collegium — NOT MEASURED

Even if single-call latency were measured, the two-seat collegium issues two parallel calls. The docs describe the orchestration as "оба вызова безусловны" (`judge-verdict-core.md:113`) but never measure the combined wall-clock time. Concurrency = 4 is mentioned for calibration (`phase0-measurements.md:117`), but that's batch concurrency, not per-unit latency.

**Status: MISSING.** Not present in any document.

### 4.3 Cost at Production Scale (Full Component) — PARTIALLY MEASURED

The phase 0 doc gives full COL4 fr cost: `deepseek` alone $2.4-3.0, plus `qwen` $0.17, total ~$3.17 (`phase0-measurements.md:418-419`). But COL4 fr is one component of many. No estimate of total production cost across all components, all languages, initial run + periodic re-runs.

**Status: PARTIAL.** Per-component cost known for fr; no aggregate projection.

### 4.4 false-positive Rate for `critical` — MEASURED BUT FAILS GATE

Measured at 25 observations on clean strings out of 415 clean observations (6.0%) for baseline collegium (`severity-recalibration-measurements.md:118`). Median false_crit 4-5/124 per run. The R2 gate requires ≤2/124 median, which is not met. No arm achieves it.

**Status: MEASURED, but the measurement shows the gate is unachievable.** The plan acknowledges this via R3 and returns the severity gate to design.

### 4.5 Per-Language Calibration — MEASURED FOR TWO LANGUAGES ONLY

fr and zh_Hans are measured. The plan (`severity-recalibration.md:167`) mentions "трек 4. Перенос промпта между парами языков" — budgeted at $0.40 but never executed. The `deepseek` weakness on zh_Hans (REAL@14 2/14 vs 11/14 for qwen, `severity-recalibration-measurements.md:128`) suggests per-language variance is material.

**Status: MISSING for all languages except fr and zh_Hans.** The track 4 cross-language comparison was never performed.

### 4.6 Agreement Rate Between the Two Seats (Inter-Judge Reliability) — PARTIALLY MEASURED

The Phase 0 sealed test shows error overlap: "помечено обеими 228, только `deepseek` 16, только `qwen` 34" (`phase0-measurements.md:354`). This gives a Jaccard of 228/(228+16+34) = 82.0%. But this is on the "flag boundary" — not on the 4-level severity scale. No Cohen's kappa or weighted kappa is reported.

**Status: PARTIAL.** Binary agreement measured; ordinal agreement on the 4-level severity scale is not.

### 4.7 Token Counts per Single String (Not Batch-Averaged) — NOT MEASURED

All token counts are batch-aggregated (batch 10, `phase0-measurements.md:91`). The per-string token count distribution matters for latency and cost budgeting at the per-unit level, but is not reported.

**Status: MISSING.**

### 4.8 Retry Rate Under Production Load — NOT MEASURED

The schema probe found 1 retry policy (`phase0-measurements.md:123-124`): "Неразобранный или неполный ответ считается сбоем транспорта, никогда не вердиктом: один ретрай, затем запись помечается `unparsed`." But the retry rate under sustained load (vs. calibration runs with sleep) is not measured. The 403 rate-limit incident (`severity-recalibration-measurements.md:134-137`) suggests production would need throttling, but no throttling parameters are measured.

**Status: NOT MEASURED.** The 403 incident is documented but not quantified as a rate (e.g., requests/minute before hitting the block).

### 4.9 Judge Behavior on Completely Novel Domains (Zero-Shot) — NOT MEASURED

All measurements are on game localization strings from known projects (COL4 fr, S&T2 zh_Hans). The judge's behavior on a completely novel game domain, terminology set, or language pair is not measured.

**Status: MISSING.**

### 4.10 false-positive Rate for Union Exceeding 10% — MEASURED, DESIGN DECISION PENDING

Union false-flag = 11.4% against the ≤10% threshold (`phase0-measurements.md:395`). The doc notes this is a product-owner decision (`phase0-measurements.md:403-404`): "Разница в цене ошибки несимметрична: ложный флаг стоит внимания продюсера, потому что по дизайну `flag` даёт state 20 и строка **всё равно отгружается**; пропущенный critical стоит сломанной строки в игре."

**Status: MEASURED, but the threshold conflict is unresolved.** The plan must decide whether 11.4% false-flag is acceptable or whether the threshold should be relaxed.

### 4.11 Repair Loop Effectiveness — NOT MEASURED

The plan describes a repair loop: "Коллегия и петля починки" (`judge-verdict-core.md:86`). No measurement exists of how often the repair loop succeeds in fixing flagged strings. The `description` field is intended to carry evidence into the repair prompt, but the measurement shows `description` alone does not improve metrics (`severity-recalibration-final.md:120-122`).

**Status: MISSING.** The repair loop is specified but not measured.

### 4.12 Judge Behavior on Strings with Existing Human Translations (Overwrite) — NOT MEASURED

The `judge` mode in `AutoForm` has an `overwrite_existing` switch (`judge-verdict-core.md:1890`). No measurement of how the judge behaves on strings that already have human-approved translations — e.g., does it flag more or fewer than on untranslated strings?

**Status: MISSING.**

### 4.13 Drift Over Time (Same String, Same Judge, Different Day) — NOT MEASURED

The noise floor is measured as within-session variance (n=5 identical runs). Day-to-day or week-to-week drift of the same model on the same strings is not measured. This matters for a feature that will be re-run periodically.

**Status: MISSING.**

### 4.14 Batch Size Effect on Verdict — NOT MEASURED

All calibration uses batch 10. The effect of batch size on verdict quality (e.g., batch 1 vs batch 20) is not measured. The plan mentions batch 10 was chosen from a prior evaluation (`phase0-schema-and-judge-calibration.md:115-117`), but that eval was on a different task (schema adherence, not verdict quality).

**Status: MISSING for the verdict task.**

### 4.15 Concurrency Effect on Verdict — NOT MEASURED

All calibration uses concurrency 4. The effect of concurrency on the model's attention distribution (and thus verdict quality) is not measured.

**Status: MISSING.**

### 4.16 Human Review Time per Flagged String — NOT MEASURED

The product goal is to reduce producer burden, but no measurement exists of how long a human takes to review a judge-flagged string vs. to translate from scratch. This is the denominator for any ROI calculation.

**Status: MISSING.**

### 4.17 Judge Verdict Stability Under Source Changes — NOT MEASURED

The `context_hash` mechanism (`judge-verdict-core.md:376-385`) is designed to invalidate verdicts when source/glossary/note change. But the sensitivity of verdicts to small source changes (e.g., a single word edit) is not measured — would a minor source edit flip a verdict?

**Status: MISSING.**

---

## 5. Exact Recalibrated Severity Definitions and Verdict Mapping

### 5.1 Severity Definitions (from the Rubric)

`docs/LLM-first/plans/2026-08-14-judge-severity-recalibration.md:153-156`:
> "Рубрика через **последствие для игрока**: `critical` = игрок не поймёт, что произошло, или получит неверную информацию; `major` = смысл искажён, но восстановим; `minor` = стиль и регистр."

### 5.2 Severity → Verdict Mapping (in Code)

`docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md:613-619`:

```python
_SEVERITY_VERDICT = {
    "none": JudgeVerdict.Verdict.PASS,
    "minor": JudgeVerdict.Verdict.PASS,
    "major": JudgeVerdict.Verdict.FLAG,
    "critical": JudgeVerdict.Verdict.REJECT,
}
```

### 5.3 Verdict → State Mapping

`docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md:636-649`:

- PASS + enable_review → STATE_APPROVED (state 30)
- PASS without review → STATE_TRANSLATED (state 20)
- FLAG → STATE_TRANSLATED (state 20) — "строка всё равно отгружается"
- REJECT → STATE_FUZZY (state 10) — "critical → state 10, не уходит в сборку без решения человека"
- UNPARSED → None (state unchanged)

### 5.4 What the Measurements Endorse

**The measurements endorse the severity-to-verdict mapping as specified, but do NOT endorse using severity as a reliable gate.**

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:196-199`:
> "1. **R1 не пройден** (обязательный `description`: recall 0.75→0.67).
> 2. **R2 не пройден** (ни одно плечо не ловит все critical во всех повторах; ложные critical 4-5/124 против ≤2).
> 3. **R3 сработал** → severity-гейт возвращается в дизайн."

The practical carry-forward is:

`docs/LLM-first/2026-08-19-severity-recalibration-final.md:200-201`:
> "4. Практический перенос: плечо C — лучший кандидат в дефолт (стабильность, REAL@14, FP deepseek), но severity-поле в нём использовать как гейт нельзя."

And the implementation config is **arm D** (arm C + render-preview):

`docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md:16-20`:
> "Конфигурация клиента (задача 5) — **плечо D** замера: промпт плеча C (обязательный `description` + рубрика severity «последствие для игрока», `RUBRIC_RULE`) **плюс** детерминированные рендер-превью плейсхолдеров во входе (`render_preview`, `RENDER_RULE`)."

### 5.5 Summary of the Verdict Pipeline

| Severity | Verdict | State | Ships? | Design Note |
|---|---|---|---|---|
| none | PASS | 30/20 | yes | approved if review enabled |
| minor | PASS | 30/20 | yes | errors recorded, string not held |
| major | FLAG | 20 | **yes** | questionable, but still ships |
| critical | REJECT | 10 | **no** | human queue; severity unreliable per R2/R3 |
| (unparsed) | UNPARSED | unchanged | depends | transport failure, never an opinion |
