# LLM-as-Judge for Machine-Translation Quality Validation: State of the Art (2024-2026)

Research synthesis for a no-human-reviewer game-localization pipeline (HCGameLoc/Weblate fork).

Languages in scope: fr, de, es, pt_BR, tr, ru, ja, ko, zh, th, vi, id, hi, fa. Source: ru or en.
Glossary scale: 22-300 terms.

---

## 1. Judge Architectures

There are five families of judge architecture in the current literature. They are not mutually exclusive - production systems increasingly combine them in cascades (see Section 5).

### 1.1 Direct Assessment (DA)

The judge produces a single scalar score (typically 0-100) for a source-translation pair, mimicking human Direct Assessment. GEMBA-DA (Kocmi & Federmann, 2023) is the canonical LLM instantiation: a zero-shot prompt asks GPT-4 to score translation quality on a 100-point scale `[verified: https://aclanthology.org/2023.wmt-1.64/]`.

**Reliability problem.** The EMNLP 2024 study "What do Large Language Models Need for MT Evaluation?" found that "LLMs often fail to output a numerical score consistently, raising questions about reliability for direct numeric MT quality assessment" across 8 language pairs and 6 LLMs `[verified: https://aclanthology.org/2024.emnlp-main.214/]`. WMT25 shared task findings confirm: "reference-based metrics still outperform LLMs at the more granular segment level" even though "LLMs perform strongly at the system level" `[verified: https://aclanthology.org/2025.wmt-1.24/]`.

### 1.2 Back-Translation (Round-Trip Translation)

Translate the candidate MT output back into the source language, then compare the back-translation to the original source. The distance (BLEU, chrF, BERTScore, or semantic similarity) is taken as a quality proxy. This is reference-free and does not require the judge to "understand" the target language.

**The literature is near-unanimous that back-translation is unreliable as a standalone evaluator.** Key evidence:

- The WMT 2022 QE study "Quality Estimation via Backtranslation" found that "backtranslation-based scores alone perform substantially worse than supervised QE systems" and only add value as a complementary feature `[verified: https://aclanthology.org/2022.wmt-1.54/]`.
- "Rethinking Round-Trip Translation for MT Evaluation" (Findings ACL 2023) rehabilitated RTT somewhat for the NMT era - the old SMT-era failures were caused by SMT's copy mechanism, absent in NMT - but still recommends RTT "as a complementary signal rather than a sole evaluator" `[verified: https://aclanthology.org/2023.findings-acl.22/]`.
- The Translation Journal survey concludes RTT "is widely regarded as a bad technique for MT evaluation" because "a good forward translation may be masked by a poor back translation and vice versa" `[verified: https://translationjournal.net/journal/51reverse.htm]`.

### 1.3 MQM Error-Span Annotation (Current SOTA for LLM Judges)

The dominant 2024-2025 architecture asks the LLM to annotate MQM-style error spans with category and severity, then derive a score from the error inventory. This is more reliable than direct scoring because it forces the model to enumerate defects before emitting a verdict.

| Method | Year | Core idea | Reference |
|---|---|---|---|
| **GEMBA-MQM** | 2023 | GPT-4, 3-shot, language-agnostic prompts, marks error spans without references | `[verified: https://aclanthology.org/2023.wmt-1.64/]` |
| **AutoMQM** | 2023 | PaLM-2 prompted to identify/categorize/locate errors; combines scoring + interpretable spans | `[verified: https://aclanthology.org/2023.wmt-1.100/]` |
| **EAPrompt** | 2024 | Two-step: (1) identify major/minor errors, (2) count + weight. Surpassed GEMBA in 8/9 scenarios | `[verified: https://aclanthology.org/2024.findings-acl.520/]` |
| **GEMBA-MQM V2** | 2025 | GPT-4.1-mini, 10 runs/segment, MQM severity weights 25/5/1, reciprocal-rank weighted average, outlier removal at 2 sigma. Ranked #1 at WMT25 by average correlation | `[verified: https://aclanthology.org/2025.wmt-1.67/]` |
| **MQM-APE** | 2025 | Adds automatic post-editing: LLM edits each detected error, filters non-impactful ones. Improves span quality across 8 LLMs | `[verified: https://aclanthology.org/2025.coling-main.374/]` |
| **M-MAD** | 2025 | Multi-agent debate: 4 dimensions (Accuracy/Fluency/Style/Terminology), 2-agent pro-con debate per dimension. Matches SOTA even with GPT-4o-mini | `[verified: https://aclanthology.org/2025.acl-long.351/]` |
| **TASER** | 2025 | Large Reasoning Models (o3) with structured evaluation reasoning for MQM + DA | `[verified: https://aclanthology.org/2025.wmt-1.76/]` |

### 1.4 Reference-Free Quality Estimation (Encoder-Based QE)

Trained regression models that take (source, translation) and output a score without a reference. These are NOT LLM judges in the generative sense - they are fine-tuned encoders.

- **CometKiwi** (Unbabel, WMT 2022/2023): XLM-R-based, outputs a score in [0,1]. The reference-free variant `Unbabel/wmt22-cometkiwi-da` is the production workhorse. Scaled-up versions in 2023 use larger encoders `[verified: https://aclanthology.org/2022.wmt-1.60/]` `[verified: https://aclanthology.org/2023.wmt-1.73/]` `[verified: https://huggingface.co/Unbabel/wmt22-cometkiwi-da]`.
- **MetricX-QE-24** (Google): mT5-based, reference-free quality predictor `[verified: https://aclanthology.org/2024.wmt-1.35.pdf]`.
- **CompactQE** (2025): shows small open-weight LLMs (<30B) can do single-pass QE without proprietary models `[verified: https://arxiv.org/html/2605.15763v1]`.

The WMT 2024 QE shared task found these encoder-based QE models remain competitive with or ahead of LLM-based approaches, particularly at segment level, though "LLMs are closing the gap" `[verified: https://aclanthology.org/2024.wmt-1.3/]`.

### 1.5 Single-Judge vs Panel / Consensus

Three aggregation strategies are documented:

1. **Multi-run aggregation (GEMBA V2):** Same model, same prompt, 10 independent runs. Aggregate via reciprocal-rank weighted average after removing outliers beyond 2 sigma. "Ten judgments are better than one" - this was the #1 system at WMT25 `[verified: https://aclanthology.org/2025.wmt-1.67/]`.
2. **Multi-agent debate (M-MAD):** Different agents argue pro/con per MQM dimension. Consensus strategy (conclude when agents agree) outperformed deliberation and interactive-review strategies. Critically, M-MAD found that "multidimensional coupling (debating across all dimensions together) significantly degrades per-dimension performance" - dimensions must be debated separately `[verified: https://aclanthology.org/2025.acl-long.351/]`.
3. **Hierarchical multi-agent (HIMATE):** Agents organized by MQM error taxonomy in a hierarchy, exchanging information across nodes `[verified: https://aclanthology.org/2025.findings-emnlp.593.pdf]`.

### 1.6 Critical Question: Which Works for Low/Medium-Resource Languages?

This is the decisive question for this project (th, vi, id, hi, fa are in scope). **The evidence is clear and unfavorable to both back-translation and LLM-based direct assessment for low-resource languages.**

**Back-translation is NOT more reliable than direct assessment for low-resource languages.** The reason is structural: back-translation requires a reverse-translation model, and for low-resource languages the reverse model is itself weak, compounding errors. The WMT 2022 BT-QE study found BT "substantially worse than supervised QE" regardless of language `[verified: https://aclanthology.org/2022.wmt-1.54/]`. No study was found showing BT outperforming direct LLM assessment specifically for low-resource languages.

**LLM-based reference-less evaluation collapses for low-resource languages.** The most directly relevant evidence is the LoResLM 2025 paper "When LLMs Struggle: Reference-less Translation Evaluation for Low-resource Languages" `[verified: https://aclanthology.org/2025.loreslm-1.33.pdf]`. Measured Spearman correlations between LLM-predicted and human DA scores, zero-shot:

| Language pair | GEMBA (best LLM) | TransQuest (fine-tuned) | CometKiwi (fine-tuned) |
|---|---|---|---|
| En-Gu (Gujarati) | 0.249 | 0.630 | 0.637 |
| En-Hi (Hindi) | 0.254 | 0.478 | 0.615 |
| En-Mr (Marathi) | 0.276 | 0.606 | 0.546 |
| En-Ta (Tamil) | 0.358 | 0.603 | 0.635 |
| En-Te (Telugu) | 0.145 | 0.358 | 0.338 |
| Et-En (Estonian) | 0.571 | 0.760 | 0.860 |

The LLM correlations for Indic-target languages (0.14-0.36) are barely above chance, while fine-tuned encoder models reach 0.34-0.64. The paper identifies tokenization as the root cause: LLM tokenizers produce token counts that "significantly deviate from the original word counts" for agglutinative/compounding languages, degrading cross-lingual semantic matching.

The paper's conclusion: "prompt-based approaches are outperformed by the encoder-based fine-tuned QE models" and "LLM-based adapters may not perform as well as encoder-based models."

`[inference]` Note: these results used sub-13B open models (Llama-2, Gemma, OpenChat). Frontier closed models (GPT-4.1, Claude) were not tested here and would likely score higher, but the tokenization problem affects all autoregressive LLMs. The GEMBA-MQM V2 results at WMT25 used GPT-4.1-mini across diverse languages including some lower-resource pairs and ranked first - suggesting frontier models partially compensate, but the study did not isolate very-low-resource pairs like Thai/Vietnamese.

**Practical implication for this project:** For th, vi, hi, fa, and to a lesser extent id, the judge should:

- Use a frontier model (GPT-4.1-class), not a small open model.
- Prefer MQM-span-annotation prompting (forces structured reasoning) over raw DA scoring.
- Run CometKiwi as a complementary signal (it is a fine-tuned multilingual encoder, less affected by LLM tokenization artifacts).
- Accept that judge reliability will be materially lower for these languages and design the verdict system to escalate rather than auto-reject.

---

## 2. Verdict Design

### 2.1 Continuous Score vs Categorical vs MQM Error Lists

| Verdict type | Example | Pros | Cons |
|---|---|---|---|
| **Continuous (0-100)** | GEMBA-DA | Simple, allows threshold tuning | LLMs emit scores inconsistently; score clustering; hard to calibrate across languages |
| **Categorical (pass/flag/reject)** | Draft plan verdicts | Maps to Weblate review states; actionable for stakeholders | Loses severity granularity; threshold sensitivity; no audit detail |
| **MQM error list + severity** | GEMBA-MQM, EAPrompt | Interpretable; maps to specific defects; severity weighting; stakeholders see what is wrong | Higher token cost; requires structured output enforcement |

**The strong 2024-2025 trend is hybrid: MQM error list first, then derive a categorical verdict from the error inventory.** EAPrompt formalizes this as a two-step process: (1) identify and categorize major/minor errors, (2) count + weight to produce a score `[verified: https://aclanthology.org/2024.findings-acl.520/]`. GEMBA V2 uses "JSON-first prompting" with severity-weighted scoring (critical=25, major=5, minor=1, minor-punctuation=0.1) `[verified: https://aclanthology.org/2025.wmt-1.67/]`.

### 2.2 MQM Severity-to-Score Mapping

The standard MQM scoring framework uses severity multipliers: Neutral=0, Minor=1, Major=5, Critical=25 `[verified: https://themqm.org/introduction-to-tqe/concrete-example-with-formulas/]` `[verified: https://www.themqm.org/mqm-pillars/the-mqm-scoring-models/]`. The penalty formula:

```text
ETPT = ((Minor_count x 1) + (Major_count x 5) + (Critical_count x 25)) x Error_Type_Weight
```

A single Critical error typically yields automatic fail. GEMBA V2 adopts this exact weighting (25/5/1) for LLM-produced annotations `[verified: https://aclanthology.org/2025.wmt-1.67/]`. Freitag et al. (2021) established that segment-level MQM scores correlate with human acceptability thresholds, and the WMT metrics tasks have validated these weights against human MQM annotations across many language pairs.

**For a pass/flag/reject verdict from MQM output, a defensible mapping is:**

- **Pass:** no Critical, no Major errors (or penalty total below a tunable threshold).
- **Flag:** at least one Major error, no Critical (needs-editing / waiting-for-review).
- **Reject:** at least one Critical error (e.g., wrong meaning, untranslated content, placeholder corruption).

### 2.3 Calibration Problems of LLM Judges

| Bias | Description | Evidence |
|---|---|---|
| **Score clustering** | LLMs avoid extremes, pile scores in 70-85 range | "LLMs often fail to output a numerical score consistently" `[verified: https://aclanthology.org/2024.emnlp-main.214/]`; mitigated by anchor examples (few-shot) and structured rubric scoring |
| **Positional bias** | In pairwise comparison, preference flips when output order swaps | "Large Language Models are not Fair Evaluators" showed order can flip rankings; MEC (Multiple Evidence Calibration) improves agreement by ~10-14% `[verified: https://aclanthology.org/2024.acl-long.511.pdf]` `[verified: https://arxiv.org/html/2406.07791]` |
| **Verbosity / length bias** | Longer outputs scored higher regardless of quality | "Explaining Length Bias in LLM-Based Preference Evaluations" decomposes win-rate into desirability vs length; verbosity is a persistent contaminant `[verified: https://arxiv.org/html/2407.01085]` `[verified: https://aclanthology.org/2024.findings-emnlp.57/]` |
| **Self-preference** | Judge favors outputs resembling its own style (low perplexity), not just its own outputs | "Self-Preference Bias in LLM-as-a-Judge" (8 LLMs studied): perplexity, not authorship, is the driver `[verified: https://arxiv.org/html/2410.21819v1]`. NeurIPS 2024: "LLM Evaluators Recognize and Favor Their Own Generations" `[verified: https://proceedings.neurips.cc/paper_files/paper/2024/file/7f1f0218e45f5414c79c0679633e47bc-Paper-Conference.pdf]`. In MT specifically: "LLMs as evaluators show a tendency to prefer more literal translations and exhibit self-biases" `[verified: https://arxiv.org/html/2410.18697]` |
| **Self-bias amplification (judge = translator)** | When the same model translates and judges, biases compound | "Deconstructing Self-Bias in LLM-generated Translation Benchmarks": bias "arises from two sources: (1) the LLM-as-a-testset and (2) the LLM-as-an-evaluator. When combined, the bias is amplified" `[verified: https://arxiv.org/html/2509.26600v1]` |

### 2.4 Known Mitigations

| Mitigation | Mechanism | Source |
|---|---|---|
| **Rubric-grounded prompts** | Annotation-guidelines prompt (AG-prompt) specifying score ranges and criteria explicitly; outperforms vanilla GEMBA zero-shot | `[verified: https://aclanthology.org/2025.loreslm-1.33.pdf]` |
| **Structured output (JSON-first)** | Force JSON with fields for errors/severity; prevents free-form score emission | GEMBA V2 `[verified: https://aclanthology.org/2025.wmt-1.67/]` |
| **Error-span-first then verdict** | Two-step: enumerate errors, then derive score/verdict from the inventory | EAPrompt `[verified: https://aclanthology.org/2024.findings-acl.520/]`; MQM-APE adds post-edit filtering `[verified: https://aclanthology.org/2025.coling-main.374/]` |
| **Multi-run aggregation** | N independent runs, aggregate (mean/RRWA) after outlier removal | GEMBA V2: 10 runs, RRWA, 2-sigma trim `[verified: https://aclanthology.org/2025.wmt-1.67/]` |
| **Position swap test** | For pairwise: run both orders, only accept if verdict is consistent | PORTIA "Split and Merge" `[verified: https://aclanthology.org/2024.emnlp-main.621.pdf]`; FairEval MEC `[verified: https://aclanthology.org/2024.acl-long.511.pdf]` |
| **Evidence-then-score** | Require the model to produce evaluation evidence before the score | FairEval (EC/MEC) `[verified: https://aclanthology.org/2024.acl-long.511.pdf]` |
| **Use a different model family for judging than for translating** | Breaks the perplexity-driven self-preference loop | `[inference]` from self-preference literature; no MT-specific study isolates this, but the mechanism (perplexity favoritism) implies it `[verified: https://arxiv.org/html/2410.21819v1]` |
| **De-bias fine-tuning** | OffsetBias preference dataset + EvalBiasBench test suite (6 bias types) | `[verified: https://aclanthology.org/2024.findings-emnlp.57/]` |

**Critical design implication for this project:** if the translation is produced by an OpenRouter-routed model (e.g., Gemini, GPT), the judge MUST be a different model family. This is not optional - the self-preference literature shows that judge = translator amplifies bias in both directions (over-scoring own style, under-scoring divergent style).

---

## 3. Terminology Validation by LLM Judge

### 3.1 The Problem

Glossary terms in this project include proper nouns, character names, item names, and domain terms from Heart Abyss (22-300 terms). The source is Russian (morphologically rich: 6 noun cases, 3 genders, conjugation) or English. Target languages include morphologically rich languages (ru, de, tr, fa, hi) and isolating/agglutinative languages (th, vi, ko, ja, zh). The challenge: verify that a glossary term appears correctly in the translation, accounting for inflection.

### 3.2 Deterministic Stem-Matching Is Necessary but Insufficient

Pure deterministic matching (exact string or stem match of the glossary target term in the translation) suffers from false negatives: a correctly inflected term (e.g., French "l'epee" when the glossary lists "epee"; Russian accusative "personazha" when nominative "personazh" is the term) will be missed. It also cannot detect semantic correctness (the term appears but in the wrong sense).

### 3.3 The Hybrid Design (Deterministic Detection + LLM Confirmation)

The most relevant published design is **IFMTBench** (2025), which explicitly separates terminology into a "gating constraint" with a two-stage pipeline `[verified: https://arxiv.org/html/2605.28218v1]`:

1. **Deterministic regex-based matching** of required glossary terms in the translation output.
2. **LLM fallback judge** for cases the regex misses - specifically to "handle legitimate morphological variations to avoid penalizing inflection, declension, or sense-aligned translations." The fallback judge provides a binary pass/fail.

This is exactly the hybrid the assignment asks about. The design rationale: deterministic checks are free (microseconds) and catch the common case; the LLM only adjudicates the residual ambiguous cases, controlling both cost and false-positive rate.

### 3.4 Supporting Evidence on Morphology-Aware Terminology

- **Morphology-Aware Source Term Masking** (EACL Findings 2024): "morph-masking" masks the source term and forces the model to generate the correctly inflected target form, rather than copying `[verified: https://aclanthology.org/2024.findings-eacl.117.pdf]`. This is a translation-side technique but validates that inflection-aware term handling is an active research area.
- **Target Lemma Annotations (TLA)** (EACL 2021): annotates source words with target lemmas so the model can inflect correctly without bilingual termbanks during training `[verified: https://aclanthology.org/2021.eacl-main.271.pdf]`.
- **CoTERM** (LREC 2026): uses the Herfindahl-Hirschman Index to measure term-translation consistency (how concentrated the translation of a term is across a corpus), emphasizing three criteria: correctness, consistency, and distinctness `[verified: https://lrec.elra.info/lrec2026-main-682]`.
- **Automated Terminology Consistency Metric** (WMT 2022): builds pseudo-references (first-occurrence or most-frequent translation) per document and scores with precision/recall over term-level quintuplets. Handles morphological variation via term-level matching `[verified: https://aclanthology.org/2022.wmt-1.41.pdf]`.

### 3.5 Game-Localization-Specific Terminology Tools

- **Godot-AI-Localizer**: enforces `brand_parity` (brand spelling) and `placeholder_integrity` as deterministic QA gates alongside translation `[verified: https://github.com/reprodev/Godot-AI-Localizer]`.
- **Loxily AI LQA Framework**: proposes 5 scoring dimensions including "Term consistency" as an explicit axis (weight ~20%), graded alongside Accuracy, Fluency, Style, and Formatting `[verified: https://loxily.com/en/blog/ai-lqa-scoring-framework]`.
- **L10n-Audit-Toolkit**: multi-stage deterministic + ICU-rule-based terminology consistency checks for game translations `[verified: https://wael-daaboul.github.io/L10n-Audit-Toolkit/]`.
- **VistatecVerifier**: commercial LLM-based pre-delivery QA for game localization with configurable terminology checks `[verified: https://www.vistatec.com/services/vistatecverifier/]`.
- **Lokalise glossary-as-constraint**: describes glossary as a "hard constraint layer" in LLM/MT workflows - deterministic enforcement, with the glossary being "machine-actionable" `[verified: https://lokalise.com/blog/ai-translation-glossary/]`.

### 3.6 Can an LLM Judge Reliably Verify Inflected Glossary Terms?

`[inference]` Based on the assembled evidence: an LLM judge CAN verify inflected glossary usage more accurately than deterministic stem-matching alone, because it can reason about morphology (e.g., "this is the accusative form of the term"). However, it is unreliable as the sole mechanism for the common case (exact match present) and expensive at scale. The IFMTBench hybrid (deterministic first, LLM fallback for misses) is the evidence-backed design. No study was found claiming LLM-only glossary validation outperforms hybrid; the literature consistently uses deterministic detection as the first gate.

---

## 4. Back-Translation as a Transparency Artifact for Non-Speakers

### 4.1 The Use Case

In this studio, producers and PMs do not read the target language. They need to trust that a translation is correct. Showing a back-translation (translation rendered back into ru or en) is an intuitive transparency artifact: "here is what the translation says, in your language."

### 4.2 Prior Art and Failure Modes

The literature on RTT as an evaluation method (Section 1.2) applies directly to its use as a trust artifact:

**Failure mode 1: Errors cancel out in the round-trip.** A wrong forward translation may still produce a back-translation close to the original source, because the back-translation model "corrects" the error or because the error is in a dimension (register, fluency, terminology) that does not survive into the back-translation. The WMT 2022 study found "BT can mislead users: MT errors in the forward translation may cause the back-translation to align semantically with the original source even when the forward output is flawed" `[verified: https://aclanthology.org/2022.wmt-1.54/]`.

**Failure mode 2: Paraphrase noise creates false alarms.** A correct translation may back-translate to a paraphrase that looks different from the source, alarming a non-speaker stakeholder. The RTT literature consistently notes low correlation between RTT-BLEU and human judgment because legitimate paraphrase variation inflates the distance `[verified: https://aclanthology.org/2023.findings-acl.22/]` `[verified: https://aclanthology.org/2020.eamt-1.11.pdf]`.

**Failure mode 3: Hallucination amplification.** The back-translation model may hallucinate, producing a fluent but fabricated source text that either hides the forward error or invents a problem that does not exist `[verified: https://aclanthology.org/2022.wmt-1.54/]`.

### 4.3 Mitigations from Practitioners

| Mitigation | Rationale | Source |
|---|---|---|
| **Present BT as a secondary signal, never the sole verdict** | BT is complementary to QE and structured error reports; never standalone | `[verified: https://aclanthology.org/2022.wmt-1.54/]` |
| **Use semantic similarity, not BLEU, for the BT comparison** | Paraphrase-tolerant metrics reduce false alarms from legitimate variation | Revisiting RTT for QE (EAMT 2020) `[verified: https://aclanthology.org/2020.eamt-1.11.pdf]` |
| **Show the structured error report alongside BT** | The MQM error list (what is wrong, where, how severe) gives stakeholders actionable detail the BT cannot | EAPrompt, GEMBA-MQM lineage |
| **Label BT explicitly as "approximate reconstruction"** | Manages expectation that BT is lossy; prevents treating minor paraphrase as a defect | `[inference]` from RTT-failure-mode literature |
| **Use the same model for BT as for forward translation (symmetric round-trip)** | Reduces model-mismatch artifacts; the same model "understands" its own output space | `[inference]`; not empirically validated as superior in the surveyed literature |

### 4.4 Design Guidance

Back-translation is useful as a **trust/display artifact** (showing stakeholders approximately what was said) but must NOT be the **scoring mechanism**. The verdict should come from the MQM-span-annotation judge (Section 1.3), and the BT should be displayed alongside the verdict as supporting context with a clear "approximate" label. For low-resource languages where the judge is weak, BT is also weak (Section 1.6), so it provides no independent signal there - it is purely a display aid.

---

## 5. Cost / Latency Patterns

### 5.1 The Cascade Architecture (Industry Consensus Pattern)

The FutureAGI engineering guide (2026) documents a 4-tier cascade that multiple production systems converge on `[verified: https://futureagi.com/blog/evaluating-llm-translation-quality-2026/]`:

```text
Tier 0: Deterministic checks (length, placeholder integrity, glossary match, NER preservation)
         -> cost: microseconds; catches ~40-60% of defects in game localization
Tier 1: Reference-free QE (CometKiwi / MetricX-QE)
         -> cost: GPU inference, milliseconds; provides a fast quality score + drift alarm
Tier 2: LLM-as-judge (MQM-span-annotation, 5 quality rubrics)
         -> cost: API call; run on a SAMPLE (5-10% of production) + full CI
Tier 3: Frontier adjudication (stronger model or human)
         -> cost: highest; only when Tier 1 and Tier 2 disagree, or QE flags but judge passes
```

Key design choices:

- **Memoization/caching:** "keyed by source/target and glossary hashes to reuse verdicts" - identical source+target+glossary state never re-judges `[verified: https://futureagi.com/blog/evaluating-llm-translation-quality-2026/]`.
- **Sampling:** "Start with 100-200 examples per direction; stratify by source length, register, and domain. Beyond 300-500 per direction, sampling is preferred over expanding full data due to judge cost."

### 5.2 Batching and Prompt Compression

Slator's industry survey (2024) reports the primary cost levers: "combine batching (evaluate multiple translations in one prompt) with prompt compression" `[verified: https://slator.com/how-to-balance-cost-quality-in-ai-translation-evaluation/]`. Batching reduces per-unit API overhead; compression (removing boilerplate, using abbreviated rubrics) cuts token cost.

### 5.3 QE-Based Deferral (Cheap -> Expensive Translation)

"Translate Smart, not Hard" (2025) applies the cascade idea to the translation step itself: translate everything with a small/cheap model, use a QE score to decide which subset to defer to a large/expensive model. Achieves "quality comparable to using a large model on all inputs while using the large model for only about 30-50% of examples" `[verified: https://arxiv.org/html/2502.12701]`.

**RouteLMT** (2025) refines this: route based on predicted "marginal gain" (improvement from upgrading), predicted from the small model's own hidden state via a lightweight LoRA regression head - no external QE model needed `[verified: https://arxiv.org/html/2604.22520v1]`.

### 5.4 Distillation (Jury -> Small Model)

**TQLite** (2025): distills a high-performing multi-LLM jury's MQM verdicts into smaller open-source models for real-time evaluation. The expensive jury runs offline to generate training labels; the cheap distilled model runs in production `[verified: https://arxiv.org/html/2608.02975]`. This is the cost-reduction pattern with the highest long-term payoff: invest once in a jury, deploy a cheap model forever.

### 5.5 Judge Hyperparameter Tuning

"Tuning LLM Judge Design Decisions for 1/1000 of the Cost" (AutoML 2025) formalizes judge cost optimization as a multi-objective, multi-fidelity Bayesian optimization over judge hyperparameters (model, prompt, temperature, few-shot count, rubric), achieving equivalent agreement at ~1000x lower cost than naive frontier-model-everything `[verified: https://proceedings.mlr.press/v267/salinas25a.html]`.

### 5.6 Cost Summary by Strategy

| Strategy | Cost reduction mechanism | Quality risk | Fit for this project |
|---|---|---|---|
| Deterministic-first cascade | Eliminates ~40-60% of units from LLM judging | Low (deterministic checks are high-precision) | High - placeholders/glossary are deterministic-checkable |
| Sampling (judge 5-10%) | Redces LLM calls 10-20x | Misses defects in un-judged units | Medium - acceptable if deterministic tier is strong |
| Batching | Amortizes API overhead per call | None if batch size is modest | High |
| Prompt compression | Reduces tokens per call | Slight quality drop if over-compressed | High |
| Caching/memoization | Eliminates re-judging identical units | None | High - game strings are stable |
| QE deferral | Defers only low-QE units to expensive judge | Depends on QE quality | Medium - adds a QE dependency |
| Distillation (TQLite) | Replace API calls with local model | Distillation fidelity ceiling | Low initially, high once volume justifies it |

---

## 6. Comparison Table: Judge Architectures

| Architecture | Accuracy (high-res) | Accuracy (low-res) | Cost/segment | Transparency | Glossary fit | Key tradeoff |
|---|---|---|---|---|---|---|
| **Direct Assessment (DA)** | Moderate (system-level good, segment-level weak) | Poor (near-chance for Indic) | Low (1 API call) | Low (just a number) | Poor (no term reasoning) | Cheap but unreliable and uninterpretable |
| **Back-Translation** | Poor (standalone) | Poor (worse, BT model also weak) | Medium (2 translations + comparison) | High for non-speakers (shows reconstruction) | Poor | Intuitive display artifact but not a real evaluator |
| **MQM-Span (GEMBA-MQM / EAPrompt)** | High (SOTA at WMT25) | Moderate-low (tokenization issues, but frontier models compensate partially) | Medium (1 API call, structured output) | High (error list with spans + severity) | Good (terminology is an MQM dimension) | Best accuracy/cost ratio; requires structured output enforcement |
| **MQM-Span + Multi-run (GEMBA V2)** | Highest (ranked #1 WMT25) | Unknown (not isolated) | High (10x API calls) | High | Good | Best accuracy but 10x cost |
| **Multi-Agent Debate (M-MAD)** | Highest (matches SOTA with GPT-4o-mini) | Unknown | Very High (8+ agent calls/segment) | Highest (per-dimension debate transcripts) | Excellent (Terminology is a dedicated debate dimension) | Best quality but cost-prohibitive at scale |
| **Encoder QE (CometKiwi)** | High (segment-level, trained) | Moderate-high (fine-tuned on pair) | Very Low (GPU inference, no API) | Low (just a score) | Poor (no term reasoning) | Cheapest reliable signal but no interpretability |
| **Hybrid Deterministic + LLM Fallback (IFMTBench)** | High | Moderate | Low (LLM only for misses) | High | Excellent (regex gate + LLM morphology confirmation) | Best glossary-specific design |

---

## 7. Recommendation: Judge Design for a No-Human-Reviewer Game-Localization Pipeline

### 7.1 The Setting

- No human translators or reviewers. The judge IS the reviewer.
- 13 target languages, 5 of them low/medium-resource (th, vi, id, hi, fa).
- 22-300 term glossary per project (Heart Abyss scale).
- Source: ru or en. Morphologically rich on both source (ru) and several targets.
- Stakeholders (producers/PMs) do not read target languages.
- Existing Weblate infrastructure: quality checks, comments, review states, REST API, suggestions.

### 7.2 Recommended Architecture: Tiered Cascade with MQM-Span Verdict

**Tier 0 - Deterministic (free, runs on every unit):**

- Placeholder/markup integrity check (existing Weblate check framework).
- Glossary exact-match detection (deterministic regex/stem match against the 22-300 term glossary).
- Length ratio, number preservation, untranslated-content detection.
- Verdict: if any Critical-deterministic check fails (placeholder corruption, missing content), auto-reject without LLM. This eliminates ~40-60% of units from LLM cost.

**Tier 1 - LLM Fallback for Glossary Morphology (cheap, only for Tier-0 glossary misses):**

- When a glossary term is NOT found by exact/stem match, call an LLM to confirm whether a morphologically inflected form of the term is present and correct.
- Binary pass/fail. This is the IFMTBench pattern `[verified: https://arxiv.org/html/2605.28218v1]`.
- Use a different model family than the translator to avoid self-preference `[verified: https://arxiv.org/html/2410.21819v1]`.

**Tier 2 - MQM-Span Judge (the primary quality verdict):**

- Prompt: MQM-span-annotation with JSON-first structured output, severity-weighted (Critical=25/Major=5/Minor=1), using the EAPrompt two-step pattern (enumerate errors first, derive verdict second) `[verified: https://aclanthology.org/2024.findings-acl.520/]`.
- Verdict mapping: Critical error -> Reject (auto-fail). Major error -> Flag (needs-editing). No errors or only Minor -> Pass.
- Include the glossary in the prompt context so the judge evaluates terminology as an explicit MQM dimension (as M-MAD does with its Terminology dimension) `[verified: https://aclanthology.org/2025.acl-long.351/]`.
- Single run per unit by default (cost). For flagged/rejected units, optionally re-run 3x and aggregate (GEMBA V2 pattern) to reduce false rejects before surfacing to stakeholders `[verified: https://aclanthology.org/2025.wmt-1.67/]`.

**Tier 3 - Back-Translation as Display Artifact (not a scoring signal):**

- Generate a back-translation into ru/en for every unit.
- Display it to stakeholders in the Weblate UI (comment or custom field) alongside the MQM verdict, labeled "approximate reconstruction."
- Do NOT derive any score from it. It exists purely so non-speakers can sanity-check meaning.

**Tier 4 - CometKiwi as Complementary Drift Signal:**

- Run CometKiwi (reference-free QE) on every unit as a cheap second opinion.
- Escalate to Tier 2 re-judgment (or a stronger frontier model) when CometKiwi and the MQM judge disagree (QE flags but judge passes, or vice versa). This is the cascade-disagreement escalation from the FutureAGI pattern `[verified: https://futureagi.com/blog/evaluating-llm-translation-quality-2026/]`.
- For low-resource languages (th, vi, hi, fa), CometKiwi is the more reliable signal than the LLM judge (per the LoResLM evidence), so weight the escalation toward QE there.

### 7.3 Weblate Integration Mapping

| Judge output | Weblate mechanism |
|---|---|
| Pass | Unit stays in approved state (30) |
| Flag (Major error) | Set unit to needs-editing (10); add a comment with the MQM error list |
| Reject (Critical error) | Set unit to needs-editing (10) + failing check badge; add comment with error detail |
| Glossary inflection confirmation | Failing check `glossary-term-missing` or comment |
| Back-translation | Comment or custom field labeled "BT (approximate)" |
| MQM error list | Comment (audit trail) - the existing plan's approach is correct |
| CometKiwi score | Comment or internal field for escalation logic |

### 7.4 Language-Specific Adjustments

| Language tier | Judge confidence | Action |
|---|---|---|
| High-resource (fr, de, es, pt_BR, ru, en) | High | Trust MQM judge verdict directly |
| Medium-resource (tr, ja, ko, zh) | Moderate | Trust MQM judge, but run CometKiwi as cross-check |
| Low-resource (th, vi, id, hi, fa) | Low | Require MQM judge + CometKiwi agreement for Pass; escalate disagreement to stronger model; never auto-reject on judge alone (higher false-reject risk) |

### 7.5 Why Not Back-Translation as Primary Method

The draft plan proposes back-translation as the primary method. The evidence argues against this:

1. Back-translation is unreliable as a standalone evaluator across all languages and worse for low-resource (Section 1.2, 1.6).
2. It cannot detect the most common game-localization defects (terminology, placeholder, register) which are invisible to round-trip comparison.
3. It produces false alarms from paraphrase variation, eroding stakeholder trust.
4. The MQM-span approach is strictly more informative (it tells you WHAT is wrong) at comparable cost.

Back-translation should be retained as a Tier 3 display artifact (it is genuinely useful for non-speakers to see meaning) but demoted from the scoring path.

### 7.6 Cost Estimate for 22-300 Term Glossary Scale

Assuming ~5000 translatable units per game project:

- Tier 0 (deterministic): ~free, catches ~40-60% -> ~2000-3000 units proceed to LLM.
- Tier 1 (glossary fallback): only for glossary-miss units, likely <10% -> ~500 LLM calls.
- Tier 2 (MQM judge): ~2000-3000 API calls (or sample 10% = ~500 calls if budget-constrained).
- Tier 3 (BT): ~5000 calls (cheap model, display only).
- Tier 4 (CometKiwi): GPU inference, no API cost.

At frontier-model pricing (~$0.005-0.015/segment for structured MQM output), full Tier-2 judging of 3000 units costs ~$15-45 per language per project. Sampling reduces this to ~$3-8. This is well within a gamedev localization budget.

---

## Sources Index

### Judge Architectures

- GEMBA-MQM (Kocmi & Federmann, WMT 2023): <https://aclanthology.org/2023.wmt-1.64/>
- AutoMQM / "The Devil Is in the Errors" (Fernandes et al., WMT 2023): <https://aclanthology.org/2023.wmt-1.100/>
- EAPrompt (Findings ACL 2024): <https://aclanthology.org/2024.findings-acl.520/>
- GEMBA-MQM V2 (WMT 2025): <https://aclanthology.org/2025.wmt-1.67/>
- MQM-APE (COLING 2025): <https://aclanthology.org/2025.coling-main.374/>
- M-MAD (ACL 2025): <https://aclanthology.org/2025.acl-long.351/>
- HIMATE (Findings EMNLP 2025): <https://aclanthology.org/2025.findings-emnlp.593.pdf>
- TASER (WMT 2025): <https://aclanthology.org/2025.wmt-1.76.pdf>
- Rubric-MQM (ACL Industry 2025): <https://aclanthology.org/2025.acl-industry.12/>
- "What do LLMs Need for MT Evaluation?" (EMNLP 2024): <https://aclanthology.org/2024.emnlp-main.214/>
- WMT25 Shared Task Findings: <https://aclanthology.org/2025.wmt-1.24/>
- WMT24 QE Shared Task Findings: <https://aclanthology.org/2024.wmt-1.3/>
- LoResLM 2025 (low-resource LLM QE): <https://aclanthology.org/2025.loreslm-1.33.pdf>
- Reference-less LLM QE for Indian languages: <https://arxiv.org/html/2404.02512v1>
- How Good is Zero-Shot MT Eval for Low-Resource Indian Languages (ACL Short 2024): <https://aclanthology.org/2024.acl-short.58/>
- CompactQE (2025): <https://arxiv.org/html/2605.15763v1>
- Large Reasoning Models as MT Evaluators (NeurIPS 2025): <https://arxiv.org/html/2510.20780v1>

### Back-Translation / Round-Trip

- QE via Backtranslation (WMT 2022): <https://aclanthology.org/2022.wmt-1.54/>
- Rethinking RTT for MT Evaluation (Findings ACL 2023): <https://aclanthology.org/2023.findings-acl.22/>
- Revisiting RTT for QE (EAMT 2020): <https://aclanthology.org/2020.eamt-1.11.pdf>
- RTT efficacy survey: <https://translationjournal.net/journal/51reverse.htm>
- RTT: What Is It Good For? (ACL 2005): <https://aclanthology.org/U05-1019.pdf>
- Backtranslation Score (ACL 2009): <https://aclanthology.org/P09-2034.pdf>
- On Evaluation of MT Trained with Back-Translation (ACL 2020): <https://aclanthology.org/2020.acl-main.253/>

### Reference-Free QE

- CometKiwi (WMT 2022): <https://aclanthology.org/2022.wmt-1.60/>
- Scaling CometKiwi (WMT 2023): <https://aclanthology.org/2023.wmt-1.73/>
- CometKiwi-DA model card: <https://huggingface.co/Unbabel/wmt22-cometkiwi-da>
- MetricX-24 (WMT 2024): <https://aclanthology.org/2024.wmt-1.35.pdf>
- Reference-less QE for resource-scarce scenarios (MDPI 2025): <https://www.mdpi.com/2078-2489/16/10/916>

### Verdict Design / Calibration / Bias

- MQM scoring models: <https://www.themqm.org/mqm-pillars/the-mqm-scoring-models/>
- MQM concrete example with formulas: <https://themqm.org/introduction-to-tqe/concrete-example-with-formulas/>
- MQM scoring models and SQC (arXiv 2024): <https://arxiv.org/html/2405.16969v5>
- "LLMs are not Fair Evaluators" / FairEval (ACL 2024): <https://aclanthology.org/2024.acl-long.511.pdf>
- OffsetBias / EvalBiasBench (Findings EMNLP 2024): <https://aclanthology.org/2024.findings-emnlp.57/>
- Length bias in LLM preference eval (arXiv 2024): <https://arxiv.org/html/2407.01085>
- Self-Preference Bias in LLM-as-a-Judge (arXiv 2024): <https://arxiv.org/html/2410.21819v1>
- LLM Evaluators Recognize Their Own Generations (NeurIPS 2024): <https://proceedings.neurips.cc/paper_files/paper/2024/file/7f1f0218e45f5414c79c0679633e47bc-Paper-Conference.pdf>
- Deconstructing Self-Bias in LLM Translation Benchmarks (arXiv 2025): <https://arxiv.org/html/2509.26600v1>
- LLMs for Literary Translation Eval (arXiv 2024): <https://arxiv.org/html/2410.18697>
- PORTIA / Split and Merge (EMNLP 2024): <https://aclanthology.org/2024.emnlp-main.621.pdf>
- Position Bias in LLM Judges (arXiv 2024): <https://arxiv.org/html/2406.07791>
- Systematic Evaluation of LLM-as-a-Judge (arXiv 2024): <https://arxiv.org/html/2408.13006v2>
- Mitigating Bias of LLM Evaluation (CCL 2024): <https://aclanthology.org/2024.ccl-1.101/>

### Terminology Validation

- IFMTBench (arXiv 2025): <https://arxiv.org/html/2605.28218v1>
- CoTERM (LREC 2026): <https://lrec.elra.info/lrec2026-main-682>
- Automated Terminology Consistency Metric (WMT 2022): <https://aclanthology.org/2022.wmt-1.41.pdf>
- Morphology-Aware Source Term Masking (Findings EACL 2024): <https://aclanthology.org/2024.findings-eacl.117.pdf>
- Target Lemma Annotations (EACL 2021): <https://aclanthology.org/2021.eacl-main.271.pdf>
- Coming to Terms with Glossary Enforcement (EAMT 2023): <https://aclanthology.org/2023.eamt-1.34.pdf>
- Glossary enforcement in MT (arXiv 2021): <https://ar5iv.labs.arxiv.org/html/2106.11891>
- Hybrid Fallback Term Injection in Low-Resource (LoResMT 2026): <https://aclanthology.org/2026.loresmt-1.6/>
- AI Translation with Glossary Support (Lokalise): <https://lokalise.com/blog/ai-translation-glossary/>

### Cost / Latency / Cascade

- Evaluating LLM Translation Quality (FutureAGI, 2026): <https://futureagi.com/blog/evaluating-llm-translation-quality-2026/>
- Translate Smart, not Hard (arXiv 2025): <https://arxiv.org/html/2502.12701>
- RouteLMT (arXiv 2025): <https://arxiv.org/html/2604.22520v1>
- TQLite (arXiv 2025): <https://arxiv.org/html/2608.02975>
- Tuning LLM Judge Design Decisions (AutoML 2025): <https://proceedings.mlr.press/v267/salinas25a.html>
- Cost/Quality Balance in AI Translation Eval (Slator): <https://slator.com/how-to-balance-cost-quality-in-ai-translation-evaluation/>
- Is Escalation Worth It? LLM Cascades (arXiv 2025): <https://arxiv.org/html/2605.06350>

### Game Localization

- AI-Driven Game Localization Case Study (Springer 2025): <https://link.springer.com/chapter/10.1007/978-981-95-4798-2_8>
- Loxily AI LQA Scoring Framework: <https://loxily.com/en/blog/ai-lqa-scoring-framework>
- Game Localization QA Checklist (Loxily): <https://loxily.com/en/blog/game-localization-qa-checklist>
- Godot-AI-Localizer: <https://github.com/reprodev/Godot-AI-Localizer>
- L10n-Audit-Toolkit: <https://wael-daaboul.github.io/L10n-Audit-Toolkit/>
- VistatecVerifier: <https://www.vistatec.com/services/vistatecverifier/>
- Gridly AI Translation Guide: <https://www.gridly.com/blog/ai-translation-game-localization/>
- Gridly AI-Assisted Game LQA: <https://www.gridly.com/blog/ai-assisted-game-lqa-transformation-localization-quality-assurance/>
