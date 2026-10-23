# JudgeBestPractices

Полный итоговый вывод субагента. Не все исходные гипотезы подтверждены: см. `docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md`.

## Problem

HCGameLoc’s judge runs are dominated by expensive, repeated model evaluation, but the evidence does not yet distinguish model inference, repair, retries, cache misses, reasoning tokens, provider throttling, and redelivery. The decision is which cost/quality architecture to approve for reference-free translation evaluation while preserving deterministic game-string checks as the safety floor.

Cost accounting that must be used for every option: `C = Σ[(I_uncached × P_in) + (I_cached × P_cache) + (O_total × P_out)] / 1,000,000`, plus any fixed/request charges and provider-specific batch multipliers. If usage separates answer and reasoning tokens, `O_total = O_answer + O_reasoning`; if `completion_tokens` already includes reasoning, count that total once, not twice. HTTP attempts, successful responses, parsed verdicts, billable requests, and rows/seats are different units. This is directly consistent with DeepSeek’s token-billing rule and usage fields [verified: https://api-docs.deepseek.com/quick_start/pricing/] and Qwen’s usage/token guidance [verified: https://docs.qwencloud.com/developer-guides/run-and-scale/token-counting].

Local evidence: the two 2026-09-16 Anvil runs processed 2,000 rows each and recorded 3,004/3,031 HTTP attempts, yet the investigation explicitly says attempts are not billable calls and cost was not measured [verified: local docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md:14-25,104-193]. The current settings include batch size 5, 120-second request deadline, retries, and 2000-row cap [verified: local docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md:247-282].

## Options

### 1. Deterministic-first gate plus local/reference-free QE screening

**What it is.** Run existing placeholder, markup, line-break, number, token, length, glossary, and other deterministic checks first. Send only rows with a possible semantic defect, high-risk metadata, or low-confidence QE result to a generative judge; reserve the strongest judge for the final uncertainty tail.

**Fit with the detected stack / existing code.** This fits the Django/Celery Weblate fork and its existing custom checks in `weblate_customization/checks.py` and `weblate/trans/protected_tokens.py` [verified: local AGENTS.md:177-205]. A self-hosted or separately served CometKiwi/xCOMET-style source-only evaluator can sit in the existing judge workflow before the LiteLLM/OpenRouter call; no provider alias mapping is needed for the QE stage.

**Pros.**

- Removes tokens from defects already decidable exactly; this is the safest cost reduction because it does not trade away semantic recall [inference].
- CometKiwi is explicitly reference-free and combines sentence/word-level quality prediction; its WMT22 submission reports gains from reference-informed pretraining and joint sentence/word supervision [verified: https://aclanthology.org/2022.wmt-1.60/].
- xCOMET adds error spans and critical-error/hallucination robustness rather than only a scalar score [verified: https://aclanthology.org/2024.tacl-1.54/].
- A local encoder avoids per-row API charges and is deterministic/repeatable at inference [inference].

**Cons / risks.**

- Reference-free QE is not a proof of correctness. WMT/EMNLP evidence shows learned metrics can miss subtle high-quality differences, overestimate bad translations, underestimate clean ones, and exhibit domain sensitivity [verified: https://aclanthology.org/2024.emnlp-main.802.pdf; https://aclanthology.org/2023.eamt-1.2.pdf].
- xCOMET-XL/XXL are large (3.5B/10.7B parameters), so self-hosting has real GPU/operations cost [verified: https://aclanthology.org/2024.tacl-1.54/].
- A conservative threshold and ongoing human/golden-set audit are required; otherwise a cheap screen becomes an unsafe auto-pass mechanism [inference].

**Rough effort / reversibility.** M for deterministic routing and a bounded QE service; L if new GPU serving is required. High reversibility: keep current judge path as a fallback and disable screening by configuration.

**Sources.** <https://aclanthology.org/2022.wmt-1.60/>; <https://aclanthology.org/2024.tacl-1.54/>; <https://aclanthology.org/2024.emnlp-main.802.pdf>; <https://aclanthology.org/2023.eamt-1.2.pdf>.

### 2. Calibrated cheap-to-strong cascade with selective escalation

**What it is.** Use a low-cost model or QE classifier for every eligible row; escalate only rows whose calibrated error probability, uncertainty, deterministic-risk features, or model disagreement crosses a threshold. The strong model is the rescue/arbiter, not a second mandatory seat on every row.

**Fit with the detected stack / existing code.** This maps directly to the existing two-seat judge configuration and batch workflow, but the public IDs must remain unresolved until LiteLLM routing is inspected. The local cascade experiment already measured a DeepSeek→Qwen arrangement at `$0.3064/1,000 records`, with 483 records, 49 tier-A requests, 23 tier-B requests, and `$0.147968` total in that harness [verified: local analysis/data/col4-judge-cascade-ds-qwen.json:294-325]. Those are local experiment prices, not current provider list prices.

**Pros.**

- Google Research finds naïve sequence confidence length-biased; learned token-level uncertainty features/quantiles give better cost-quality tradeoffs, and no single quantile transfers reliably across datasets [verified: https://arxiv.org/pdf/2404.10136v1.pdf].
- FrugalGPT supports the general strategy of prompt adaptation, cheaper approximations, and learned cascades; its benchmark result was up to 98% cost reduction, but it is not translation-specific and should not be treated as a production guarantee [verified: https://arxiv.org/abs/2305.05176].
- Calibration-first routing is preferable to a hand-tuned raw confidence threshold: UCCI reports a 31% measured cost reduction at matched NER F1 after mapping uncertainty to calibrated error probabilities and optimizing a constrained threshold [verified: https://arxiv.org/abs/2605.18796].
- It preserves strong-model review for ambiguous rows while eliminating routine duplicate judging [inference].

**Cons / risks.**

- The router must be calibrated on HCGameLoc’s languages, genres, defect taxonomy, and current translation engine; NER/QA cascade results do not transfer automatically [inference].
- LiteLLM may not expose token log-probabilities or calibrated uncertainty; disagreement, structured-verdict entropy, deterministic-risk features, or a small learned router may be needed instead [inference].
- Escalating based only on the first model’s confidence can miss cases where the strong model would correct a confident but wrong cheap verdict; measure rescue rate and false auto-pass rate [inference].
- Cascades add state-machine complexity. Same-row idempotency, stable request keys, and a same-batch acknowledgement barrier are required so a faster seat cannot run ahead after a peer failure [inference, grounded in local judge workflow constraints].

**Rough effort / reversibility.** M. High reversibility: retain the existing strong judge as the default and initially set escalation to 100%, then lower it only after calibration evidence.

**Sources.** <https://arxiv.org/pdf/2404.10136v1.pdf>; <https://arxiv.org/abs/2305.05176>; <https://arxiv.org/abs/2605.18796>.

### 3. Provider-native economics and reliability hardening without changing judge semantics

**What it is.** Keep the current judge prompt and verdict contract, but optimize execution: offline batch API where SLA permits, stable prompt prefixes for cache hits, concise strict JSON schema, explicit maximum output/reasoning budgets, preflight schema compatibility, bounded retries, deadline/idle timeouts, stable idempotency keys, and durable usage attribution.

**Fit with the detected stack / existing code.** The repository already has `response_format=json_schema`, batch size 5, a no-reasoning default, request deadlines, transport/protocol/transient retry settings, and durable request-attempt/run history [verified: local weblate/trans/defaults.py:53-121; local docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md:247-282]. This option changes transport and accounting behavior, not verdict semantics.

**Pros.**

- OpenAI’s official Batch API gives 50% lower cost, a separate rate-limit pool, unique `custom_id`s, and a stated 24-hour completion window; output order is not guaranteed, so IDs must be used for reconciliation [verified: https://developers.openai.com/api/docs/guides/batch].
- Qwen’s official FAQ documents 50% batch pricing, reasoning tokens billed at output rates, and `thinking_budget` as a cost/quality control [verified: https://docs.qwencloud.com/resources/faq-text-generation].
- Prompt caching can reduce repeated-prefix input cost and latency: OpenAI says up to 90% input discount when prefixes match exactly [verified: https://developers.openai.com/api/docs/guides/prompt-caching]; Qwen documents implicit cache at 20% of normal input and explicit/session hits at 10%, with provider-specific creation charges and short validity [verified: https://docs.qwencloud.com/developer-guides/text-generation/context-cache].
- A schema preflight is especially valuable here: the local model-evaluation harness observed a schema-incompatible model causing 285 logged requests instead of 15 expected [verified: local analysis/data/col4-model-eval.json:1-10,63-80].
- Short structured output and capped reasoning directly reduce output-token variance; retries should be limited to retryable transport/protocol failures, not malformed prompts [inference].

**Cons / risks.**

- Batch is unsuitable for interactive completion and may take up to 24 hours; provider-specific cache/batch stacking is not interchangeable [verified: https://developers.openai.com/api/docs/guides/batch; https://docs.qwencloud.com/resources/faq-text-generation].
- Lower reasoning or output caps can reduce recall on difficult idioms, terminology, and long-context cases [inference].
- Cache hits require stable prefixes; changing model, tools, schema, reasoning settings, or prompt order can invalidate reuse [verified: https://developers.openai.com/api/docs/guides/prompt-caching].
- Reliability hardening reduces waste but cannot by itself explain or eliminate current run overlap/redelivery; usage must be measured per logical row, model, token bucket, retry, and repair [inference].

**Rough effort / reversibility.** S/M. Very high reversibility: all changes can be feature-gated and rolled back without changing labels.

**Pricing identity warning.** Official DeepSeek currently publishes `deepseek-flash` = DeepSeek-V4.1-Flash and `deepseek-v4-pro` = DeepSeek-V4-Pro-0813. Its displayed rates are per 1M tokens: off-peak flash `$0.003` cache-hit / `$0.15` miss / `$0.60` output and pro `$0.022` / `$0.66` / `$1.98`; peak weekday windows double those rates, with concurrency limits 2500 and 500 [verified: https://api-docs.deepseek.com/quick_start/pricing/, accessed 2026-09-17]. Official QwenCloud publishes actual `qwen3.8-max` at `$2` input / `$6` output / `$0.25` implicit-cache input / `$2.50` explicit-create / `$0.17` explicit-read per 1M, and `qwen3.7-plus-2026-05-26` at `$0.40` / `$1.60` / `$0.08` / `$0.50` / `$0.04` [verified: https://www.qwencloud.com/models/qwen3.8-max; https://www.qwencloud.com/models/qwen3.7-plus-2026-05-26, accessed 2026-09-17]. These public identities and rates do **not** prove that internal LiteLLM aliases `atlas/qwen3.8-max` or `deepseek-v4-pro` map to those endpoints, versions, or prices; only LiteLLM configuration and usage/invoice records can establish that [inference].

**Sources.** <https://developers.openai.com/api/docs/guides/batch>; <https://developers.openai.com/api/docs/guides/prompt-caching>; <https://docs.qwencloud.com/resources/faq-text-generation>; <https://docs.qwencloud.com/developer-guides/text-generation/context-cache>; <https://api-docs.deepseek.com/quick_start/pricing/>; <https://www.qwencloud.com/models/qwen3.8-max>.

### 4. Selective multi-judge consensus only on the risk tail

**What it is.** Do not run two strong judges on every row. Run one primary judge; invoke a second independent judge only for high-risk rows, primary/cheap-model disagreement, low-confidence structured verdicts, or cases likely to alter a release-blocking decision. Use a conservative rule only for the escalated subset and preserve human review for unresolved disagreement.

**Fit with the detected stack / existing code.** It uses the current two-seat judge contract and existing consensus/reject behavior, but changes seat-2 from mandatory to conditional. The existing golden-set analysis has 433 records (266 defects, 167 clean) and explicitly warns that it is enriched with constructed defects, not production [verified: local analysis/data/col4-judge-SEALED-test.json:1-38]. On that set, strict union of two judges had estimated TPR 97.4% and TNR 88.6%; both flagged 228, only DeepSeek 16, only Qwen 34 [verified: local analysis/data/col4-judge-SEALED-test.json:7423-7481].

**Pros.**

- Preserves the recall benefit of a second opinion where it matters while avoiding its full token cost on routine pass rows [inference].
- Local evidence suggests complementarity exists but is limited; disagreement is concentrated in a minority, supporting conditional rather than universal consensus [inference from local study].
- Multi-metric/ensemble approaches can improve correlation, but WMT evidence also shows metric divergence can reflect either useful complementary signal or unexamined bias [verified: https://aclanthology.org/2024.wmt-1.32.pdf].

**Cons / risks.**

- Strict union improves defect recall at the cost of specificity; the local TNR 88.6% means many clean rows can be escalated or held, and the constructed-defect distribution is not production [verified: local analysis/data/col4-judge-SEALED-test.json:1-38,7438-7481].
- Two generative judges can share training/prompt bias; disagreement is not automatically truth [inference].
- Conditional fan-out needs durable per-row state, same-batch barriers, and exact deduplication; otherwise a retry or worker lease can create duplicate paid calls [inference].

**Rough effort / reversibility.** M. High reversibility if the current mandatory-consensus path remains available as a fallback.

**Sources.** <https://aclanthology.org/2024.wmt-1.32.pdf>; local analysis/data/col4-judge-SEALED-test.json:1-38,7423-7481.

### 5. Distilled and calibrated small translation judge

**What it is.** Train or fine-tune a smaller local model on human MQM/golden-set labels plus high-confidence teacher examples, then calibrate its error probabilities and use it as the default judge or router. Keep the frontier judge for drift sampling and uncertain/high-severity cases.

**Fit with the detected stack / existing code.** The result can expose the same structured verdict contract to the existing Django/Celery workflow, but it requires a new model-serving/training lifecycle absent from the current LiteLLM-only configuration. It is therefore a longer-term architecture, not a no-change production optimization.

**Pros.**

- Eliminates per-row API spend for the common path after training and can provide stable latency [inference].
- MiniLLM reports better generation quality and calibration than standard KD across 120M–13B students, while warning that standard forward-KL distillation can overfit low-probability teacher regions [verified: https://arxiv.org/html/2306.08543v5; ICLR version https://proceedings.iclr.cc/paper_files/paper/2024/file/8ac015d409635f196f9e3e9dcfb9a94e-Paper-Conference.pdf].
- FIRST reports improved accuracy and reduced mis-calibration through efficient trustworthy distillation [verified: https://aclanthology.org/2024.emnlp-main.703.pdf].
- A calibrated student can produce the uncertainty signal needed by Option 2 [inference].

**Cons / risks.**

- Teacher errors, domain shift, and label scarcity can be distilled into a confidently wrong student; QE literature documents domain mismatch and scarce in-domain labels [verified: https://aclanthology.org/2023.eamt-1.2.pdf].
- Calibration can degrade even when accuracy improves; MiniLLM and selective-KD work both show calibration must be measured separately, not assumed [verified: https://arxiv.org/html/2306.08543v5; https://arxiv.org/html/2602.01395].
- Up-front training, GPU serving, model/version governance, drift monitoring, and periodic human labels may exceed the savings at the current 2,000-row cap [inference].

**Rough effort / reversibility.** L. Medium reversibility: the old API judge can remain as a fallback, but training/serving investment is sunk.

**Sources.** <https://arxiv.org/html/2306.08543v5>; <https://aclanthology.org/2024.emnlp-main.703.pdf>; <https://aclanthology.org/2023.eamt-1.2.pdf>; <https://arxiv.org/html/2602.01395>.

## Comparison table

| Option | Effort | Risk | Stack-fit | Maintenance | Key tradeoff |
|---|---|---|---|---|---|
| 1. Deterministic-first + QE | M/L | Medium quality risk if QE auto-passes; low risk for exact checks | High: existing custom checks/Celery | Medium: model version + calibration | Lowest recurring token cost, but reference-free QE is not proof |
| 2. Calibrated cascade | M | Medium: router calibration and false auto-pass | High: maps to two-seat workflow | Medium | Strong cost/quality frontier, but uncertainty signal must be validated locally |
| 3. Provider-native economics | S/M | Low semantic risk; medium SLA/provider risk | Very high: existing settings and structured contract | Low/medium | Fastest savings, but mostly reduces waste rather than judge volume |
| 4. Selective multi-judge | M | Medium specificity/duplicate-call risk | High: existing seats/consensus | Medium/high | Better recall on risk tail, not worth paying two judges for every row |
| 5. Distilled small judge | L | High drift/calibration/teacher-bias risk | Medium: new serving/training subsystem | High | Best long-run unit economics, highest up-front and governance cost |

## Recommendation

**Recommendation for human approval: choose Option 2, implemented with Option 1’s deterministic-first gate and Option 3’s accounting/reliability controls.** This is the best Pareto choice for HCGameLoc: exact checks remove no-risk work first; a calibrated low-cost screen limits expensive model calls; the existing strong judge remains the rescue path; and stable IDs, usage buckets, bounded retries, structured output, cache accounting, and reasoning caps prevent operational waste. Do not approve a universal two-judge run: local evidence shows only limited complementarity and materially lower specificity under strict union. Do not approve the distilled model as the immediate answer: its economics are attractive only after proving a stable language/domain calibration set and measuring training/serving cost against actual provider invoices. Before assigning any price to `atlas/qwen3.8-max` or `deepseek-v4-pro`, obtain the LiteLLM alias map plus per-request usage (uncached input, cached input, output, reasoning, retries, repairs, provider/model ID, and billable status); official Qwen/DeepSeek list prices are reference points only, not evidence of the internal route.

## Дополнительные сообщения в ходе исследования

Сохранены только тексты исследовательских выводов, без служебных записей сессии. Порядок хронологический; ранние предположения могут быть опровергнуты следующими сообщениями.

### Сообщение 1

Research evidence assembled: local investigation doc confirms 2 Anvil runs (2000 rows each, 3004/3031 HTTP attempts, 5h20/9h11), but attempts != billable calls; settings batch=5, deadline=120s, retries, cap=2000, seats deepseek-v4-pro + atlas/qwen3.8-max (docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md:14-25,104-193,247-282). Local probe cost evidence: deepseek/deepseek-v4-pro $0.4669/1k records, qwen/qwen3-235b $0.0931/1k (analysis/data/col4-judge-probe.json:88-159); cascade DS→Qwen $0.3064/1k, DS tier 49 req/483, Qwen 23 req/483, total $0.147968 for 483 (col4-judge-cascade-ds-qwen.json:294-325); model eval found failed-schema model caused 285 logged requests vs 15 expected (col4-model-eval.json:1-10,63-80). Primary refs: CometKiwi ACL 2022 <https://aclanthology.org/2022.wmt-1.60/> (ref-free QE, references in pretraining + sentence/word multitask improve; top WMT22); xCOMET ACL/TACL 2024 <https://aclanthology.org/2024.tacl-1.54/> (source-only QE + span detection, robust localized critical errors/hallucinations; 3.5B/10.7B, still needs validation); high-quality metric limitations EMNLP 2024 <https://aclanthology.org/2024.emnlp-main.802.pdf> (metrics struggle subtle differences/HQ zero, over/underestimate; GEMBA bias); QE domain mismatch evidence <https://aclanthology.org/2023.eamt-1.2.pdf>. Cascades: Google Research Language Model Cascades <https://arxiv.org/pdf/2404.10136v1.pdf> (naive sequence confidence length-biased; learned token-uncertainty quantiles better, no fixed quantile transfers); FrugalGPT <https://arxiv.org/abs/2305.05176> (prompt adaptation/approximation/cascade, up to 98% cost reduction on benchmark; not translation-specific); UCCI <https://arxiv.org/abs/2605.18796> (calibrate uncertainty to error prob then constrained cost threshold; 31% measured cost reduction at same F1 in NER, caveat domain-specific). Pricing: official DeepSeek <https://api-docs.deepseek.com/quick_start/pricing/> lists deepseek-flash=DeepSeek-V4.1-Flash and deepseek-v4-pro=DeepSeek-V4-Pro-0813; cache-hit/miss/output per 1M: flash $0.003/$0.15/$0.60 off-peak (double peak), pro $0.022/$0.66/$1.98 off-peak (double peak), concurrency 2500/500; as of page (2026 current). Official QwenCloud <https://www.qwencloud.com/models/qwen3.8-max> lists actual qwen3.8-max $2 input/$6 output, implicit cache $0.25, explicit create $2.5/read $0.17 per 1M; <https://www.qwencloud.com/models/qwen3.7-plus-2026-05-26> lists $0.4/$1.6, implicit $0.08, explicit $0.5/$0.04. Internal atlas/qwen3.8-max and deepseek-v4-pro are LiteLLM aliases; do NOT infer they map to public Qwen/DeepSeek IDs or inherit those prices. Qwen cache docs <https://docs.qwencloud.com/developer-guides/text-generation/context-cache:> implicit automatic 20% input, explicit/session create 125%, hits 10%, min 256/1024, 5min; reasoning tokens billed as output; thinking_budget caps reasoning; batch 50% discount per FAQ <https://docs.qwencloud.com/resources/faq-text-generation>. OpenAI official Batch <https://developers.openai.com/api/docs/guides/batch> 50% lower, 24h, unique custom_id, order not guaranteed, charge completed requests; Prompt caching <https://developers.openai.com/api/docs/guides/prompt-caching> up to 90% input discount, exact prefix required, cache details in usage; reason effort/config can alter cache prefix. Formula: cost = (uncached_input*P_in + cached_input*P_cache + output_answer*P_out + reasoning_tokens*P_out)/1e6, but where provider usage counts reasoning inside completion_tokens do not add twice; use usage fields and provider-specific cache/batch stack rules.
