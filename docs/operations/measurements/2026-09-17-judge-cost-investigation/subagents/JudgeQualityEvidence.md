# JudgeQualityEvidence

Полный итоговый вывод субагента. Не все исходные гипотезы подтверждены: см. `docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md`.

## summary

The strongest evidence supports a full two-seat max-severity collegium only on the historical OpenRouter pair and corpus; it does not empirically justify the current LiteLLM aliases, repair policy, or severity-only thresholds. Cascades are directly contradicted by held-out and train/dev results. The only defensible current cost saving is the exact seat-2 `extra_body.enable_thinking=false` profile, supported by a small, single-language labeled slice and requiring continued offline non-inferiority validation.

## files

```json
[
  {
    "path": "docs/product/measurements/2026-08-13-phase0-measurements.md",
    "description": "Primary Phase 0 calibration: sealed test, TPR/TNR, critical misses, cascade/single/union comparisons, costs, selection non-replication, and corpus caveats."
  },
  {
    "path": "analysis/data/col4-judge-SEALED-test.json",
    "description": "Machine-readable 433-record sealed fr test with 167 clean, 146 major, and 120 critical records."
  },
  {
    "path": "analysis/data/col4-judge-single-traindev.json",
    "description": "Machine-readable single-model train/dev controls and usage/cost data."
  },
  {
    "path": "analysis/data/col4-judge-cascade-ds-qwen.json",
    "description": "DeepSeek→Qwen cascade results, including escalation, overturns, recall, specificity, and cost."
  },
  {
    "path": "analysis/data/col4-judge-mirror-ds-qwen.json",
    "description": "Qwen→DeepSeek mirror results and complementarity evidence."
  },
  {
    "path": "docs/product/designs/2026-08-26-balanced-judge-evaluation-corpus.md",
    "description": "Ground-truth limitations, human/LLM annotation bias, current language/domain coverage, and proposed six-pair expansion."
  },
  {
    "path": "docs/product/measurements/2026-08-19-severity-recalibration-final.md",
    "description": "Five-repeat severity/noise study and failed description/severity acceptance gates."
  },
  {
    "path": "docs/product/measurements/2026-08-14-st2-zh-judge-run.md",
    "description": "Human-confirmed zh defects, repeat drift, glossary effect, and severity blind spots."
  },
  {
    "path": "docs/product/measurements/2026-09-03-judge-thinking-passthrough.md",
    "description": "Current LiteLLM alias/reasoning behavior, seat-specific savings, labeled slice, and limitations."
  },
  {
    "path": "docs/product/measurements/2026-09-01-02-judge-seat-parallelism-production.md",
    "description": "Current production seat aliases, deadlines, and transport-only canary evidence."
  },
  {
    "path": "docs/product/measurements/2026-09-01-03-judge-litellm-nfg-es-canary.md",
    "description": "Current LiteLLM canary: parsed/latency/parallelism success but explicitly ungrounded correctness and blind cost."
  },
  {
    "path": "docs/product/measurements/2026-09-01-04-judge-unparsed-attribution.md",
    "description": "Provider/model/batch confounding and parsed/unparsed aggregate attribution."
  },
  {
    "path": "docs/product/measurements/2026-08-25-judge-repair-loop.md",
    "description": "Repair-loop failure: paid judging followed by uncaught MT repair error; no repair success measurement."
  },
  {
    "path": "docs/product/measurements/judge-measurements-index.md",
    "description": "Consolidated conclusions and six-area evaluation gaps; some latency entries are stale versus September evidence."
  }
]
```

## architecture

Evidence separates three layers: (1) historical quality calibration on OpenRouter with a constructed-heavy fr corpus and one human zh anchor; (2) current LiteLLM transport/latency/alias measurements without a current held-out quality benchmark; and (3) repair/severity policy experiments, which show instability or unmeasured outcomes. Historical model names, prompts, reasoning profiles, providers, and batch widths are not interchangeable under the documented R3 rule.

## report

Ranked findings and acceptance guidance:

1. Cascade is not empirically justified and should remain rejected. On train/dev, single DeepSeek achieved H-recall 94.0% versus 87.1% for the best two-stage mirror; DeepSeek→Sonnet fell to 70.7%. The mechanism is observed: DeepSeek→Sonnet overturned 48 cases, 46 wrongly; DeepSeek→Qwen overturned 21, 18 wrongly. This is a recall failure, not a cost fluke (`phase0-measurements.md:205-233`).

2. Full max-severity union is the only historical two-seat design with strong release-gate evidence. On the sealed fr test (433 records), DeepSeek alone had H-recall 90.6%, R-recall 95.0%, and 3/120 critical defects passed; historical union had H-recall 97.4%, R-recall 98.3%, and 0/120 critical defects passed, but false flags rose to 11.4% and auto-pass was only 72.9% (`phase0-measurements.md:345-395`). The union evidence is not a fresh current-alias run; reconstructed train/dev union and historical OpenRouter arms must not be advertised as current production quality.

3. Model selection has serious gold-set/sample-selection bias. DeepSeek's train/dev advantage was significant (McNemar p=0.0005) but disappeared on sealed test (p=0.20), with Qwen ahead on H-recall (92.1% vs 90.6%). The 919-record fr corpus has 500 constructed defects; 419 clean labels from two non-overlapping LLM annotator sets with defect prevalence 29.6% vs 15.7%, only two criticals, and no inter-annotator agreement. Human-real ground truth exists only for one zh pair (124 units, seven criticals); all other instruments are constructed-heavy. LLM annotators also labelled 9/14 human-confirmed defects below major, demonstrating blind-spot leakage (`balanced-judge-evaluation-corpus.md:18-62`).

4. Threshold/severity policy is not validated. Five-repeat zh calibration (two seats, three prompt arms) measured 20–32% seat flip noise and 24–31% collegium flip noise. Mandatory descriptions improved precision 0.51→0.54 but reduced recall 0.75→0.67, failing R1. The severity rubric reduced noise 39→30/124 and reached 12/14 REAL defects at median, but failed R2: no arm caught every critical in every repeat and false criticals remained 4–5/124 versus a ≤2 target (`2026-08-19-severity-recalibration-final.md:39-132,157-160`). Therefore severity-only release blocking and “second judge only escalates critical” are not justified.

5. Current deployed aliases are not the evaluated historical models. Production is now LiteLLM seat 1 `deepseek-v4-pro` (upstream DeepSeek, batch 2, 120s) and seat 2 `atlas/qwen3.8-max` (batch 1, 150s), while Phase 0 measured OpenRouter `deepseek/deepseek-v4-pro` and `qwen/qwen3-235b-a22b-2507` (`2026-09-01-02-judge-seat-parallelism-production.md:8-26`). The old Qwen alias and reasoning behavior no longer exist; the September document explicitly says old `qwen3.8-max` was replaced by `atlas/qwen3.8-max` (`2026-09-03-judge-thinking-passthrough.md:64-80`). Stage 3 is also superseded: many unparsed results were ~30s gateway resets, not judgment failures. Historical quality/cost numbers cannot certify current seats.

6. Current production canaries prove transport, not quality or economics. The production LiteLLM canary produced 38/38 HTTP 200, zero unparsed, and overlapping seats, but 25/25 selected units passed without ground truth; the document explicitly says correctness is unknown. Every usage cost is `None`, so current dollar acceptability is unmeasured (`2026-09-01-03-judge-litellm-nfg-es-canary.md:57-85,119-128`). Provider comparisons are confounded by model, batch width, and deadlines (`2026-09-01-04-judge-unparsed-attribution.md:51-75`).

7. The only promising saving is seat-2 no-thinking on the exact current alias/profile. It reduced output 945→72 completion tokens and 34.8→2.9s/string, with 16/16 parsed; on a tiny labeled ru→fr slice it caught 10/10 defects versus 9/10 for thinking and produced 0/5 clean false flags. This is only 10 defects, one language pair, one repeat, and constructed/legible classes; it cannot establish a production recall rate. Applying the same profile to DeepSeek seat 1 lost six detections in 20 and was correctly not adopted (`2026-09-03-judge-thinking-passthrough.md:13-18,33-47,117-146`).

8. Repair is not empirically justified. A 124-row two-seat run paid judge calls, then an uncaught `MachineTranslationError` in `repair_target` aborted the run; repair success, post-repair quality, model compatibility, and human blind comparison were not measured (`2026-08-25-judge-repair-loop.md:15-19,84-127`). Cheaper repair models or changed repair/reasoning policies require a fresh offline experiment.

Recommended acceptance metrics for any cheaper model/profile: freeze prompt, schema, alias/profile, batch width, and reasoning mode; evaluate against held-out human-labeled zh plus new language/script strata; require critical-recall lower 95% CI at least the incumbent release floor (historical target lower bound 88%, point estimate ≥95%), zero unexplained critical→pass regressions, H-recall non-inferiority margin ≤2–3 percentage points, and an explicitly approved clean false-critical upper bound (historical ≤2/124 target was not achieved). Require ≥5 repeated runs, report flip/noise rate and weighted ordinal agreement, require parsed/unparsed 100% on bounded canary with p95/max within seat deadlines, and measure actual provider usage/reasoning tokens and full-component dollar cost. For repairs additionally require successful completion, no source/target corruption, and human-adjudicated post-repair acceptance. No seat removal, model swap, cascade/demotion, or repair change is safe from the existing transport-only evidence.

## Дополнительные сообщения в ходе исследования

Сохранены только тексты исследовательских выводов, без служебных записей сессии. Порядок хронологический; ранние предположения могут быть опровергнуты следующими сообщениями.

### Сообщение 1

Audit evidence: Phase0 sealed test is strongest human-ish holdout but one-shot, old aliases/models. docs/product/measurements/2026-08-13-phase0-measurements.md:345-370,388-395: 433 fr records (167 clean/146 major/120 critical), DeepSeek single H-recall 90.6 [86.5,93.6], H-specificity 98.2, R-recall 95.0, 3/120 critical->pass; Qwen 92.1/89.8/97.5; union max severity 97.4 H-recall, 98.3 R-recall, 0/120 critical pass but false flags 11.4%, auto-pass 72.9%. Model selection did not replicate (train/dev DeepSeek H recall 94.8 vs sealed 90.6; Qwen 87.9->92.1; McNemar p .0005 vs .20), so do not justify current seat choice from train/dev. Cascade is empirically rejected: same doc :205-233: best cascade recall 87.1 vs DeepSeek single 94.0; tier-B overturns 46/48 real defects in DS→Sonnet, 18/21 DS→Qwen; only Qwen→DS 16 correct/8 wrong but still 87.1. Full parallel/max-severity union is the only measured two-seat design with recall gain, but direct union reconstructed on train/dev; sealed union result is from persisted arms and not fresh current config.

Corpus bias/gaps: docs/product/designs/2026-08-26-balanced-judge-evaluation-corpus.md:18-49: 419/919 clean labels from two LLM annotators; their defect rates 29.6% vs 15.7%, zero unit overlap, only 2 critical/560; all 500 defects constructed. :51-62: instruments only ru→zh human (124, 7 critical), en→fr constructed (236), ru→fr constructed (919); one human-real language pair. Proposed 6-pair coverage (en-de/tr/ja/fa, ru-ko/zh) remains proposal :98-125. Model annotators reproduce blind spots: 9/14 human-confirmed defects below major (same doc :39-43; also index :485-487). Six-audit gaps in index :445-603: false-reject ≤2% un-certifiable with n=167 clean; per-language calibration only fr+zh, no novel domain; ordinal seat agreement no kappa (only binary overlap); repair-loop success unmeasured; day-to-day drift, batch/concurrency effects, human review time and production-scale cost unmeasured. Index :523-537,539-561,575-603.

Severity/threshold evidence: docs/product/measurements/2026-08-19-severity-recalibration-final.md:39-81,82-132,157-160: n=5 repeats × 2 seats × 3 prompt arms, 30/30 parsed; noise ≥flag 20-32% per seat/arm and 24-31% collegium; collegium baseline missed critical 1/7 median, false critical 5/124, C rubric 2/7 missed + 5 false critical; B description improves precision 0.51→0.54 but recall .75→.67, fails R1; C reduces noise 39→30/124 and reaches REAL 12/14 median but R2 fails (no arm catches all critical every repeat; false critical 4-5 > ≤2). Severity-only blocking is not justified; description is useful evidence but no quality gain. docs/product/measurements/2026-08-14-st2-zh-judge-run.md:82-88,145-205: same prompt repeated DeepSeek 117/7/0 vs 109/12/3; single run not a measurement; 16% verdict flips at n=2 and 6/14 real defects missed by both baseline judges; glossary lifts recall 8→12/14 but is not judge quality.

Current deployed aliases differ materially from corpus: docs/product/measurements/2026-09-01-02-judge-seat-parallelism-production.md:8-26 identifies production seat1 deepseek-v4-pro (upstream openai/deepseek-ai, batch2, 120s), seat2 atlas/qwen3.8-max (qwen/qwen3.8-max, batch1,150s); historical phase0 used OpenRouter deepseek/deepseek-v4-pro + qwen/qwen3-235b-a22b-2507. Current alias/transport evidence docs/product/measurements/2026-09-03-judge-thinking-passthrough.md:64-80 says old qwen3.8-max alias no longer exists; atlas alias replaced it, so old reasoning-off numbers cannot transfer. Current production canary :50-65,90-102 proves only transport (38/38 HTTP200, 0 unparsed), not correctness; 2026-09-01-03-judge-litellm-nfg-es-canary.md:57-62,74-85,119-128 explicitly says 25/25 pass on selected units is ungrounded and LiteLLM cost_usd=None.

Current cost/savings evidence: 2026-09-03-thinking-passthrough.md:13-18,33-47,117-146: seat2 no-think extra_body reduced 945→72 completion, 34.8→2.9s/string, 16/16 parsed; 15-record ru→fr labeled slice thinking caught 9/10 vs no-think 10/10, both 0/5 clean false flags (:147 onward and limits), but n=10 defects/one pair/one repeat, not production recall. Seat1 no-think lost 6 detections in 20 and was not adopted. Savings can safely apply only seat2's exact current alias/profile, with ongoing offline gate; not seat1 or old qwen settings. Cost itself blind in current LiteLLM production (usage cost None, same canary lines 84-85,124-125); no total-dollar acceptability evidence. Prior OpenRouter phase0 costs: DS $0.284/1k vs Qwen $0.046/1k, but not current aliases.

Repair evidence: docs/product/measurements/2026-08-25-judge-repair-loop.md:15-19,84-127 shows one 124-row ru→zh run paid judge calls then a MachineTranslationError in repair_target crashed whole run; no repair success rate, and target model compatibility probe + blind human comparison were not done. Thus repair policy empirically unjustified; any cheaper repair/model needs offline validation.

Acceptance metrics for cheaper variants (recommend): frozen prompt/schema/profile + held-out human-labeled sets across at least existing zh plus new language/script strata; per-language critical recall lower CI bound >= incumbent release floor (historical target 88%, point >=95), no increase in critical→pass; clean false-critical upper CI <=2/124 (or explicitly redesign); H recall non-inferiority margin ≤2-3pp, false-flag upper bound agreed; parsed/unparsed 100% on bounded transport canary and p95/max within per-seat deadlines; repeat noise measured n≥5 with flip rate not worse than incumbent; disagreement/union must report weighted kappa and complementarity, not just overlap; cost measured via actual provider usage including reasoning tokens and full component scale. Current evidence supports seat2 no-think speed adoption only (if exact alias retained), rejects cascade/demotion and severity-only thresholds, and does not justify model swap/seat removal/repair change.

### Сообщение 2

Important audit hygiene: judge-measurements-index.md's 'latency NOT MEASURED' section (around :4.1-4.2) is stale/contradicted by Sep production docs; cite current per-seat measurements instead, and mark index status as historical. Likewise Stage3 docs explicitly supersede their own no-candidate decision: 2026-08-26-stage3 :1-14,104-162 attributes many unparsed results to ~30s gateway resets, not model skill; current aliases/provider/batch/stream differ. Never compare OpenRouter historical quality/cost to LiteLLM current pair without paired same-profile offline evaluation; 2026-09-01-04 :51-75 says model, batch width, deadline differ and R3 forbids provider attribution. Current LiteLLM parsed/unparsed aggregate is confounded too: atlas/qwen 96 parsed/51 unparsed during a rollout 400 window, DeepSeek 97/0, versus later stable runs.

### Сообщение 3

Suggested six-area audit framing grounded in repo: (1) Dataset/ground truth: constructed defects dominate; LLM-clean labels lack overlap/critical coverage; only zh has human inventory; add multi-language human adjudication and prevalence-stratified production sample. (2) Coverage/generalization: only ru→fr, en→fr, ru→zh; no novel domain, scripts, approved translations, or language transfer; six-pair proposal unexecuted. (3) Judge validity/reliability: sealed holdout shows non-replication; n=5 noise 20–32%; no weighted kappa/ordinal agreement; current aliases unvalidated. (4) Decision/rubric: severity thresholds fail R1/R2; union false flags 11.4%; critical false positives 4–5/124; repair outcomes unmeasured. (5) Systems/transport: current canaries prove parse/latency only; LiteLLM cost blind; Stage3 resets and alias changes confound old data; batch/concurrency/provider effects not isolated. (6) Economics/governance: historical OpenRouter per-1k costs are not current dollars; no full-scale/repair/review-time ROI; savings require fixed-profile offline non-inferiority then bounded canary, no automatic seat/model/repair changes from transport-only runs.
