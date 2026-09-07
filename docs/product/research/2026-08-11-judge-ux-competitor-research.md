# Competitor UX Research: AI/QE Verdict Surfacing in TMS Products

**Scope:** UI/UX patterns (not scoring math) for surfacing AI quality verdicts to reviewers who may not read the target language. Focus on how verdicts appear, how low-confidence items are queued, how explanations are shown, back-translation usage, and auto-approve behavior. Cross-domain calibrated-trust patterns included.

**Date:** 2026-08-11

---

## Per-Vendor UI Findings

### 1. Phrase (TMS + Strings) - QPS + Quality Profiles

**Score/verdict appearance:**

- **Segment level in CAT editor:** QPS (Quality Performance Score) appears as an integer 0-100 next to the translation, alongside TM/NT/TB match metadata in the CAT panel footer. Match origin (e.g., "MT + QPS 87") shown in a tooltip. Toggled on/off via project setting "Display Phrase Quality Performance Score matches" [verified: https://support.phrase.com/hc/en-us/articles/5709672289180-Phrase-QPS-Overview].
- **Score bands with labels:** 95-100 Excellent, 90-94 Very good, 75-89 Good, 0-74 Poor. These are textual labels accompanying the number, not just raw integers [verified: same].
- **Phrase Portal:** QPS shown in the translation interface to evaluate MT quality of file translations [verified: same].
- **Analytics dashboard:** A dedicated dashboard in Phrase Analytics helps find the optimal QPS threshold to balance cost savings vs. quality risk [verified: same].
- **API:** Document-level scores available via API for batch triage [verified: same].

**Auto-confirm / lock indication:**

- During pre-translation, PMs set a QPS threshold. Segments at/above threshold are auto-confirmed; high-QPS segments may be locked (visible but not editable by linguists). Default threshold is 100% [verified: https://support.phrase.com/hc/en-us/articles/5709717749788-Pre-translation-TMS].
- **Lock visual:** Locked segments show a lock icon. Tooltip explains WHY locked (e.g., "locked after passing quality evaluation review criteria") [verified: https://support.phrase.com/hc/en-us/articles/25865045974300-Quality-Profiles].
- Locked segments bypass Quality Profile re-evaluation; PMs/Admins can unlock manually [verified: same].

**Quality Profiles (AI checks, separate from QPS):**

- Up to three AI checks per profile, each with free-text "review criteria" (max 2000 chars) instructing the AI what to look for [verified: https://support.phrase.com/hc/en-us/articles/25865045974300-Quality-Profiles].
- Segments passing all checks are auto-locked; failures remain editable and appear in the **QA pane > AI checks tab** with a text description of each detected issue [verified: same].
- **Key UX detail:** Quality Profile warnings live in a SEPARATE tab ("AI checks") from standard QA warnings ("QA checks tab"). This separation prevents AI-verdict issues from being confused with deterministic format/placeholder checks [verified: same].
- Issues persist on the segment even after edits; removed only on split/join. Can be re-run via "Run AI checks" button in the QA pane [verified: same].
- **Insights tab** (org-level): locked-words percentage, total volume, AI Units consumed. Filterable by period/profile [verified: same].

**How low-confidence items are queued/filtered:**

- QPS threshold gates auto-confirmation. Below-threshold segments remain in the normal linguist workflow, not a dedicated queue. The assumption is linguists work top-to-bottom and the score badge draws their attention [verified: https://support.phrase.com/hc/en-us/articles/5709672289180-Phrase-QPS-Overview].
- Orchestrator workflows can route below-threshold segments to LQA or human review steps based on QPS score [verified: same].

**AI explanations:**

- Quality Profile failures show a free-text AI-generated description of the issue per check. No structured severity breakdown shown inline (unlike Lokalise/Transifex MQM). The explanation is whatever the AI wrote when it failed the segment against the review criteria [verified: https://support.phrase.com/hc/en-us/articles/25865045974300-Quality-Profiles].

**Back-translation:** Not shown. QPS is a quality-estimation score, not a verification method.

---

### 2. Lokalise - AI Scoring (MQM-based)

**Score/verdict appearance:**

- **Lens icon in editor:** Each target translation value has a small magnifying-lens icon next to it (source values do not). Click the lens to trigger scoring; a 0-100 score appears. Click the score to expand a detail panel [verified: https://docs.lokalise.com/en/articles/11631905-scoring-translation-quality].
- Scoring is on-demand (click-to-score) in the editor, or batch via tasks/workflows [verified: same].

**MQM penalty breakdown:**

- Score = 100 minus total penalties. Each detected issue lists its category (grammar, spelling, terminology, fluency, meaning) and severity weight: Critical (-75), Major (-25), Minor (-5) [verified: same].
- The detail panel shows the list of detected problems with their penalty, so the reviewer sees exactly which deductions produced the score [verified: same].
- **Threshold:** >=80 = safe to auto-approve/lightly review; <80 = human review strongly recommended. Default threshold 80, configurable in workflow settings [verified: same].

**Auto-approve behavior:**

- In workflows with a "Review task with AI scoring" step, translations scoring below threshold are automatically added to a review task. Translations at/above threshold proceed. Optionally "Exclude reviewed translations" to avoid re-queuing [verified: same].
- **What happens visually on auto-approve:** Not explicitly documented as a distinct visual state. The score badge itself is the signal; high scores simply don't generate review tasks [inference from workflow docs].

**How low-confidence items are queued/filtered:**

- Below-threshold items are routed into a named review task with due date and assignees. This creates an explicit work queue, not just an in-editor filter [verified: same].
- Unscorable translations are also added to the review task (fail-safe: if the judge can't score, treat as needs-review) [verified: same].

**AI explanations:**

- The MQM issue list IS the explanation: category + severity per deduction. No separate prose explanation beyond the MQM taxonomy labels [verified: same].

**Back-translation:** Not shown. Lokalise uses MQM-based direct evaluation, not back-translation.

---

### 3. Crowdin - AI Pipeline + Copilot + Backtranslation

**AI Pipeline (the verdict engine):**

- Multi-stage deterministic pipeline: Context Preparation -> Translation -> Prompt Adherence -> QA Checks -> File Consistency. Each stage is a separate node with its own model and reasoning-effort setting [verified: https://store.crowdin.com/ai-pipeline].
- Presets: Minimal (2 steps), Standard (4 steps), Thorough (5 steps). Each can add an **Ambiguity Filter** [verified: same].

**Ambiguity flags (the verdict that surfaces to UI):**

- The Ambiguity Filter identifies gender-sensitive or multi-meaning words. Instead of guessing, it flags these strings for human review to prevent breaking UX [verified: https://store.crowdin.com/ai-pipeline].
- **Crowdin Copilot** monitors pre-translation, analyzes failures after ambiguity flags, and resolves them by synthesizing "core questions" (e.g., choosing tone/register, term standardization) to re-run the pipeline with updated context [verified: https://crowdin.com/blog/meet-crowdin-copilot].
- Copilot can apply global fixes rather than handling hundreds of individual flags - glossary gap analysis, cross-TM inconsistency fixes, false-positive filtering [verified: same].

**Where results appear:**

- QA checks run in the Editor, highlighting issues in-context [verified: https://support.crowdin.com/project-settings/qa-checks/].
- AI Pipeline results: If AI-delivered translations have QA issues, Crowdin reprocesses affected strings and re-sends to AI with QA details (automatic re-translation loop) [verified: https://support.crowdin.com/pre-translation/].
- **White-box debugging:** Project -> Tools -> AI Pipeline Logs shows input/output of every pipeline step. Reviewers can inspect exactly where a hallucination occurred or a constraint was ignored [verified: https://store.crowdin.com/ai-pipeline].

**Copilot in-editor:**

- Copilot sees the active string, source text, translations across languages, votes/QA flags, current workflow step, and full strings in context. Can answer "is this translation consistent?" without manual copy-paste [verified: https://store.crowdin.com/crowdin-copilot].
- Two agent modes: Ask Agent (read-only queries), Task Agent (read/write, pauses for confirmation before irreversible actions) [verified: same].

**Back-translation (the standout feature for this use case):**

- Crowdin has a dedicated **Backtranslation** app that translates existing translations back into the source language to verify accuracy. Explicitly designed for "reviewers who don't speak the target language" [verified: https://store.crowdin.com/backtranslation].
- Settings: per-project enable/disable, markUntranslated, prompt ID for AI behavior [verified: same].
- This is the only major TMS with first-party back-translation as a verification method exposed in the reviewer UI [verified: same, contrasted with SPF.io below].

**How low-confidence items are queued/filtered:**

- AI Pipeline tiered reporting after auto-translation: target languages, files processed, translations added/skipped with QA/missing/issue breakdown [verified: https://support.crowdin.com/pre-translation/].
- Copilot identifies root causes to apply batch corrections across projects [verified: https://crowdin.com/blog/meet-crowdin-copilot].

---

### 4. Transifex - TQI + Auto-Review

**Score/verdict appearance:**

- **Inline score next to translation:** TQI score appears right next to each AI translation in the Editor. Real-time, calculated when the AI translation is generated [verified: https://help.transifex.com/en/articles/9465000-translation-quality-index-tqi].
- **Score range:** 0.85-1.00 Good, 0.70-0.85 Fair, 0.50-0.70 Poor, <0.50 Unusable. "Not Available" if not yet scored [verified: same].
- **Editing clears the score:** If you edit and save an AI-generated translation, the TQI score is no longer shown (the verdict applied to the original AI output, not the edited version) [verified: same].

**TQI Insights tab (in-editor breakdown):**

- A dedicated tab in the Editor alongside History, Glossary, Comments. Native part of the workflow, not a separate page [verified: same].
- Also accessible by clicking the TQI score directly, or **Ctrl+T** keyboard shortcut [verified: same].
- **Component scores:**
  - Translation Consistency: cross-LLM similarity (alignment, context clarity, complexity) [verified: same].
  - Structural Integrity: formatting, glossary use, HTML/structure correctness [verified: same].
  - Semantic Quality: MQM-based evaluation of human-like accuracy [verified: same].
- **Issues tab:** MQM-style issues color-coded by severity (critical = red, major = orange, minor = yellow). Each issue has thumbs-up/thumbs-down feedback to improve the algorithm [verified: same].
- **Variants tab:** Shows alternative translations from the multiple LLMs that generated them. Reviewer can apply a variant directly [verified: same].

**Auto-Review (the auto-approve mechanism):**

- Project Settings -> Workflow -> Quality Assurance section. Set a TQI threshold per target language [verified: same].
- Two automation options:
  - "Automatically review translations": AI translations at/above threshold are auto-marked as reviewed and added to TM [verified: same].
  - "Automatically add unreviewed AI translations to TM": At-threshold translations enter TM without manual review [verified: same].
- **What happens visually on auto-approve:** The translation transitions to "reviewed" state and enters TM. No special lock icon documented; the reviewed state is the visual signal [inference from workflow docs].

**How low-confidence items are queued/filtered:**

- Editor filters (under "More" menu): filter by TQI score range/threshold for targeted review [verified: same].
- Below-threshold translations simply don't get auto-reviewed; they remain in the normal editor queue for manual attention [verified: same].

**AI explanations:**

- The component breakdown + MQM issue list with severity colors IS the explanation. The cross-LLM consistency score is itself an explanation signal (low consistency = multiple LLMs disagreed = uncertain) [verified: same].
- Thumbs up/down on issues provides a feedback loop and signals to the reviewer that the AI's judgment is itself being judged [verified: same].

**Back-translation:** Not shown. Transifex uses multi-LLM consensus + MQM, not back-translation.

---

### 5. XTM - TQI + Intelligent Workflow

**Score/verdict appearance:**

- TQI is a percentage score per segment, produced by comparing translations from 2-3 LLMs and evaluating similarity, incorporating source segment, context (prev/next segment), top-3 TM matches, terminology, and project subject matter [verified: https://help.xtm.ai/en/xtm-cloud/26.2/en/global-ai-translate---tqi-settings.html].
- **Lower LLM agreement = lower score.** The consensus mechanism is the core signal [verified: same].
- **Thresholds:** Three configurable bands - Unacceptable, Acceptable, Good - settable at global/customer/project levels [verified: same].
- Scores surface in XTM Workbench (the editor) for linguist review, with suggestions about lower-quality segments [verified: https://help.xtm.ai/en/xtm-cloud/26.2/en/global-intelligent-workflow-settings.html].

**Intelligent Workflow (auto-routing):**

- Uses TQI confidence score to route content. High-confidence jobs skip manual checks; linguists only review low-quality segments [verified: https://help.xtm.ai/en/xtm-cloud/26.2/en/global-intelligent-workflow-settings.html].
- **Job average TQI:** Calculated from all scored segments. ICE matches, 100% leveraged matches, and manual translations are excluded [verified: same].
- **Auto-close conditions (all three required):**
  1. Job average TQI exceeds Acceptable threshold [verified: same].
  2. All target segments populated (TM or AI) [verified: same].
  3. All target segments in Good quality range (no segment below Acceptable) [verified: same].
- If even one segment is below Acceptable, workflow steps will NOT auto-close, forcing human review of that segment [verified: same].

**How low-confidence items are queued/filtered:**

- The Intelligent Workflow IS the queue mechanism. Below-threshold segments block auto-closure and force linguist attention in Workbench [verified: same].
- The "weakest link" rule (any single bad segment blocks auto-close) is a conservative design that prevents one bad segment from hiding in an otherwise-good job [verified: same].

**AI explanations:**

- TQI suggestions about lower-quality segments appear in Workbench for linguist correction [verified: same]. Details of the explanation format (structured MQM vs. prose) not fully documented in these pages.

**Back-translation:** Not shown. XTM uses multi-LLM consensus + similarity scoring.

---

### 6. Smartling - LQE + LQA Suite

**LQE (Language Quality Estimation) - the AI verdict:**

- Predicts quality of MT output per string, assigning one of three labels: **High** (likely no post-edit, may skip), **Medium** (understandable but needs validation/light edit), **Low** (requires extensive human validation) [verified: https://help.smartling.com/hc/en-us/articles/25058582212507-Language-Quality-Estimation-Agent-for-Machine-Translation].
- Two assessment methods: Standard (LLM evaluating grammar, fluency, semantic coherence, lexical accuracy + TM/QC profile/glossary/style guide checks) or Fine-tuned model (XLM-R, custom trained on your data) [verified: same].

**Where LQE levels appear:**

- **Strings View (list):** LQE level displayed in the Translation column below the saved translation (default view), or as a separate column via Custom View [verified: same].
- **LQE filter:** Filter the Strings View by predicted quality level (High/Medium/Low) [verified: same].
- **CAT Tool:** Linguists see the LQE level per string in the CAT Tool [verified: same].
- **Word Count Report:** Displays LQE levels for MT-step content [verified: same].
- **Cost Estimates:** LQE levels and discounts shown, auto-refreshed after LQE completes [verified: same].
- **Fuzzy Match Profile:** LQE-level discounts (payable rate % per level per vendor agreement) [verified: same].
- **Exception:** If a TM fuzzy-match discount applies, only the fuzzy % is shown; LQE level is suppressed to avoid conflicting signals [verified: same].

**Dynamic Workflow routing (auto-approve):**

- Post-translation Decision step routes by LQE level: High -> Published step (skip human), Medium -> Review step (light edit), Low -> Post-Edit step (full edit) [verified: same].
- This is a branching workflow, not a binary auto-approve. Three-tier routing matches the three-tier label [verified: same].

**LQA Dashboard (separate from LQE, for human-evaluated quality):**

- Reports > Linguistic Quality Assurance Dashboard. Updated daily for published strings evaluated under LQA [verified: https://help.smartling.com/hc/en-us/articles/19513205210651-Assess-Translation-Quality-with-the-LQA-Dashboard (via search summary; direct fetch returned 403)].
- **MQM Score per date** (trend), **MQM Score x Objective Error trend**, **MQM Score Calculations** (errors by severity: Critical/Major/Minor/Neutral with weights), **MQM Locales Breakdown**, **MQM Projects Breakdown**, **MQM Jobs Breakdown** (drill into Job Details) [verified: same, via search summary].
- Formula: `1 - ((Sum of (Error Count x Severity Weight)) / Total Word Count)` [verified: same].
- **LQA Agent** (AI): Automated MQM-based scoring across engines/content types, replacing slower human sampling [verified: https://www.smartling.com/software/lqaagent].
- **LQA Suite:** Customizable scorecards, sampling, dedicated evaluator workflows + LQA Agent for instant scoring [verified: https://www.smartling.com/software/lqa-suite/].

**How low-confidence items are queued/filtered:**

- Strings View LQE filter (High/Medium/Low) for triage [verified: LQE Agent doc].
- Dynamic Workflow Decision step creates the routing queue automatically [verified: same].

**AI explanations:**

- LQE provides a label only (High/Medium/Low), not a per-issue breakdown. The explanation is implicit in the routing (Low = "needs extensive editing"). Detailed issue breakdown comes from LQA (human or LQA Agent), which is a separate evaluation layer [verified: same].

**Back-translation:** Smartling publishes about back-translation as a concept [verified: https://www.smartling.com/blog/what-is-back-translation-and-why-is-it-important] but does not surface it as a first-party in-product reviewer feature. It's treated as a methodology, not a UI element.

---

## Cross-Domain Patterns: Calibrated Trust for Reviewers Who Cannot Verify Content

### Pattern A: Verdict > Evidence > Explanation Hierarchy (Stella Ops AI UX)

Three-panel structure with clear authority labeling [verified: https://stella-ops.org/docs/modules/web/ai-ux-patterns/]:

- **Verdict Panel** (authoritative outcome): the pass/flag/reject decision.
- **Evidence Panel** (factual backing): reachability, runtime data, VEX statements.
- **AI Assist Panel** (explanations): AI-generated rationale, clearly labeled as inference.

Visual rules: default 3-line AI summary (what changed, why it matters, recommended action); max 2 action chips per row; no paragraphs in list view; hover previews; click-to-detail. Green "evidence-backed" badge vs. amber "suggestion" badge to distinguish fact from inference [verified: same].

**Mapping to non-fluent reviewer:** The reviewer reads the Verdict (pass/flag), checks the Evidence (back-translation similarity, placeholder check result), and reads the AI Assist only if they need the "why." Authority labels prevent the reviewer from treating AI speculation as fact.

### Pattern B: Make AI Outputs Legible - Show What It Saw, Ignored, Confidence, Correction Path (ByteLabs)

Core principles [verified: https://www.bytelabs.space/blog/ux-patterns-for-ai-explainability-and-trust]:

- Show what the AI saw (source, context, glossary terms considered).
- Show what it ignored (terms it didn't apply, rules it skipped).
- Show confidence explicitly (calibrated, not raw probability).
- Always provide an override/edit path with version history.
- Tailor explanation depth to risk: minimal for routine, deep for high-risk.
- Distinguish facts from inferences; label non-sourced content as "generated estimate."

### Pattern C: Confidence-Based Three-Threshold Routing (Content Moderation Systems)

From production moderation pipelines [verified: https://letsbuildsolutions.com/blog/system-design/designing-a-content-moderation-system-automated-filtering-ml-classification-and-human-review-queues-at-scale/ and https://looprails.dev/article-hitl-content-moderation.html]:

- **Auto-approve** (high confidence, low impact): handle automatically, log only.
- **Human review** (uncertain): route to queue with full context.
- **Auto-reject/archive** (high confidence, high impact or clearly wrong): flag but allow appeal.

Reviewer UX: show content, surrounding context, ML scores, triggering rules, and similar past decisions. Provide structured decision reasons and keyboard shortcuts for throughput. Track overturn rates to calibrate [verified: same].

### Pattern D: Risk-Labeled Review Items with Evidence Fields (EskiLab HITL Queue)

Each review item carries explicit fields [verified: https://eskilab.com/human-review-queue-design-ai-operations/]:

| Field | Why it matters | Translation analog |
|---|---|---|
| risk_level | Controls priority | verdict severity (reject > flag > pass) |
| source_evidence | Lets reviewer verify | back-translation, glossary match, placeholder diff |
| proposed_action | Clarifies what approval does | "approve this translation" |
| AI_confidence | Adds signal, not proof | judge confidence score |
| reviewer_decision | Creates audit trail | approve / edit / reject / escalate |

**Key insight:** "If reviewers see only the final AI output, they cannot evaluate evidence. If they see too much raw context, they slow down or approve blindly. The design goal is enough context for a reliable decision" [verified: same].

### Pattern E: Override Requires Reason + Symmetric Friction (EU AI Act Art.14)

From regulatory-driven oversight design [verified: https://www.sota.io/blog/eu-ai-act-art14-human-oversight-ux-api-design-patterns-developer-guide-2026]:

- Override (disagreeing with AI) is a PRIMARY action, not hidden behind menus.
- Override requires a stated reason (audit trail).
- Symmetric friction: overriding should be as easy/hard as approving, preventing both rubber-stamping and reflexive rejection.
- Show override counts to the reviewer so they know their own agreement rate.
- Display calibrated confidence with visual indicators and thresholds, NOT raw probabilities. Translate to user-friendly labels (High/Medium/Low with percentages).

### Pattern F: Word-Level Explanation Highlights (TRuST-M / LIME)

From ACM study on LLM moderation explanations [verified: https://dl.acm.org/doi/10.1145/3779211.3793172]:

- LIME-style word-level highlights (showing which words drove the verdict) were preferred by 58% of reviewers for intuitive comprehension.
- Attention visualizations were least helpful (unclear token emphasis).
- Positive correlation between explanation clarity, user trust, and decision confidence.
- **Mapping to translation:** highlight the specific glossary term that was mis-inflected, the placeholder that moved, or the phrase that back-translated differently - not a paragraph of explanation.

### Pattern G: Back-Translation with Similarity Percentage (SPF.io)

From SPF.io Document Translation Portal [verified: https://www.spf.io/2024/04/05/accelerate-document-translation-with-alternative-translations/]:

- **Back Translation tab** shows the reverse translation alongside a **similarity percentage** comparing back-translation to original source.
- Explicitly designed for "reviewers who are not fluent in the target language to verify if a translation retains a similar meaning to the source sentence" [verified: same].
- Low similarity = visual signal to investigate; high similarity = confidence the meaning survived.
- Paired with "Alternatives" tab (multiple translation options to pick from) [verified: same].

### Pattern H: Appeals as a Real Escalation Tier (LoopRails)

From HITL content moderation design [verified: https://looprails.dev/article-hitl-content-moderation.html]:

- Appeals must provide genuine authority and time to reconsider, not just re-confirmation.
- Show context, not just a verdict: content, surrounding thread, category, model confidence, exact signals that triggered flags.
- Avoid "automation bias": present evidence and reasons to DISAGREE, not just a remove/keep binary.
- Track and surface overturns: monitor how often reviewers change the model's decision.

---

## Extracted UX Patterns Mapped to Weblate Surfaces

Weblate's existing UI surfaces: **unit list** (Zen/table rows with failing-checks badges, labels, state icons), **unit editor** (source/target text, per-unit checks panel, comments, suggestions, history), **project/component dashboard** (progress stats, checks overview), **search filter** (`q=` query syntax with check/state filters).

### Pattern 1: Score Badge in Unit List Row

**Source:** Phrase QPS (segment-level 0-100 next to translation), Transifex TQI (inline score next to AI translation), Smartling LQE (label in Translation column).

**Weblate mapping:** Add a judge-verdict badge to each unit row in the Zen/table view, positioned next to the target text (where failing-checks badges already live). Use a compact format: a colored pill with verdict label (PASS / FLAG / REJECT) or a numeric score band. Color-code: green (pass), amber (flag), red (reject). This parallels Weblate's existing check-failure red badges but represents the LLM judge's semantic verdict rather than deterministic format checks.

**Why it fits:** Weblate already renders per-unit status badges (failing checks, state). A judge badge is the same visual pattern with different semantics. Producers scanning the list get immediate triage signal without reading the target language.

### Pattern 2: Verdict Detail Panel in Unit Editor (Click-to-Expand)

**Source:** Transifex TQI Insights tab + Breakdown modal (Ctrl+T), Lokalise lens-icon click-to-expand, Phrase QA pane > AI checks tab.

**Weblate mapping:** Add a "Judge verdict" section to the unit editor's checks/information sidebar (where "Other checks," "Comments," "Suggestions" tabs already are). Click the verdict badge to expand. Contents:

- **Verdict** (pass/flag/reject) with confidence.
- **Evidence:** back-translation with similarity % (Pattern G), placeholder/markup diff, glossary term matches.
- **Explanation:** structured issue list (MQM-style: category + severity), or a 3-line AI summary (Pattern A).
- **Action buttons:** "Approve," "Mark needs editing," "Escalate" (comment).

**Why it fits:** Weblate's editor already has a tabbed sidebar for checks/comments/suggestions. The judge verdict is a natural additional tab or a promoted section within the existing checks area. The reviewer never needs to read the target text - the evidence (back-translation, diffs) is in the source language.

### Pattern 3: Separate AI-Verdict Checks from Deterministic Checks

**Source:** Phrase explicitly separates "AI checks" tab from "QA checks" tab in the QA pane.

**Weblate mapping:** Do NOT mix judge verdicts into the existing `Checks` framework (which produces deterministic pass/fail badges via `weblate.checks`). Keep the judge verdict as a distinct visual layer - different badge color/shape, different sidebar section. Rationale: deterministic checks (placeholder count, XML validity) are binary facts; judge verdicts are probabilistic assessments. Mixing them erodes trust in both.

**Why it fits:** Weblate's check system is already cleanly separated (each check is a registered class with a unique ID). Adding judge verdicts as "pseudo-checks" would be technically easy but semantically wrong. A separate presentation layer preserves the fact/inference distinction (Pattern B).

### Pattern 4: Filter by Verdict in Search (`q=` extension)

**Source:** Transifex filter by TQI range (More menu), Smartling LQE filter (High/Medium/Low), Lokalise below-threshold routing to review tasks.

**Weblate mapping:** Extend the `q=` search syntax with judge-verdict filters, e.g.:

- `q=judge:reject` - show only rejected units.
- `q=judge:flag` - show only flagged units.
- `q=judge_score:<80` - show units below a score threshold.
- `q=has:backtranslation` - show units with back-translation evidence.

These integrate with existing filters like `check:placeholders`, `state:needs-editing`, `label:...`.

**Why it fits:** Weblate's search is already filter-based with `check:`, `state:`, `label:` prefixes. Judge verdicts are a natural additional filter dimension. Producers can build a review queue by filtering `judge:reject OR judge:flag` and working through it.

### Pattern 5: Back-Translation Evidence Block in Editor

**Source:** SPF.io Back Translation tab with similarity %, Crowdin Backtranslation app.

**Weblate mapping:** In the unit editor, below the source/target text pair, add an optional "Back-translation" block:

- Shows the target text back-translated into the source language.
- Shows a similarity percentage between back-translation and original source.
- Color-code: high similarity (green), medium (amber), low (red).
- The reviewer reads only source-language text but can assess whether meaning survived.

**Why it fits:** This is THE pattern for non-fluent reviewers. Weblate's editor already shows source and target side-by-side; the back-translation is a third line in the source language. The similarity % gives a calibrated confidence signal without requiring the reviewer to judge the target text.

### Pattern 6: Auto-Approve with Visible State Transition + Audit Comment

**Source:** Transifex Auto-Review (threshold -> auto-marked reviewed + TM entry), Phrase QPS lock (lock icon with tooltip), XTM Intelligent Workflow (auto-close with conditions).

**Weblate mapping:** When the judge verdict is PASS with high confidence:

- Auto-transition the unit to state 30 (approved) or 20 (waiting-for-review) depending on config.
- Leave an auto-generated comment (using Weblate's existing comment system) recording: verdict, score, back-translation similarity, timestamp, judge model. This creates the audit trail.
- Visually, the unit shows the normal approved-state indicator PLUS the judge badge (so it's clear WHY it was auto-approved).

**Why it fits:** Weblate already has review states (10/20/30) and a comment system. Auto-approval is a state transition + a comment. The comment provides the "source_evidence" and "reviewer_decision" audit fields (Pattern D). Producers can later filter `state:approved AND has:judge_comment` to see what the judge approved.

### Pattern 7: Escalation Path via "Needs Editing" + Comment

**Source:** LoopRails appeals as escalation tier, EskiLab reviewer actions (approve/edit/reject/escalate/request info), EU AI Act override-with-reason.

**Weblate mapping:** When the judge verdict is FLAG or REJECT:

- Auto-set the unit to state 10 (needs-editing) so it surfaces in review queues.
- Auto-add a comment with the judge's issue breakdown (the "reason").
- The producer's action: read the back-translation evidence, then either approve (override), edit, or leave for a linguist. Approving an override requires no special friction (Weblate is a single-user studio), but the comment records the disagreement for audit.

**Why it fits:** Weblate's "needs-editing" state already exists for exactly this purpose. The judge becomes an automated commenter that sets needs-editing. Producers filter `state:needs-editing` to find the judge's flagged queue.

### Pattern 8: Project/Component Dashboard Verdict Summary

**Source:** Phrase Quality Profile Insights tab (locked-words %, total volume, AIU consumed), Smartling LQA Dashboard (MQM score trends, locale/project/job breakdowns), XTM job-average TQI.

**Weblate mapping:** Add a "Judge verdicts" widget to the project/component dashboard showing:

- Distribution: X% pass, Y% flag, Z% reject (donut chart).
- Flagged/rejected unit count with a link to the filtered unit list (`q=judge:flag OR judge:reject`).
- Average back-translation similarity score per language.
- Trend over time (are verdicts improving as prompts/glossary mature?).

**Why it fits:** Weblate dashboards already show progress stats and check-failure counts. A verdict-distribution widget is the same data shape (count by category). It gives producers a single-screen answer to "is this language's translation quality under control?"

### Pattern 9: Variant Suggestions (Alternative Translations)

**Source:** Transifex Variants tab (apply alternate LLM translation directly), SPF.io Alternatives tab, Smartling AI Post-Editing Agent.

**Weblate mapping:** When the judge verdict is FLAG/REJECT, show 1-2 alternative translations (re-generated by the LLM with the judge's feedback) in the editor sidebar, as "suggestions" using Weblate's existing suggestion mechanism. The producer can accept a suggestion with one click.

**Why it fits:** Weblate already has a suggestions system (users propose alternative translations, others vote/accept). Judge-generated variants are machine-produced suggestions using the same accept/reject UI.

### Pattern 10: White-Box Pipeline Log (Debug View)

**Source:** Crowdin AI Pipeline Logs (input/output per step), Stella Ops Evidence Panel.

**Weblate mapping:** For power users debugging why the judge flagged a unit, provide a "Judge reasoning" expandable section showing the full prompt/response chain: the judge's input (source, target, glossary, back-translation), its step-by-step reasoning, and the final verdict. This is the "AI Pipeline Logs" equivalent - it lets a producer or developer understand WHERE the judgment went wrong without re-running anything.

**Why it fits:** Weblate already has a unit history/changes view. The judge reasoning is a new history entry type. It supports the "calibrated trust" goal (Pattern A/B): reviewers can inspect evidence at any depth.

---

## Summary: Pattern-to-Surface Quick Reference

| Pattern | Weblate Surface | Primary Beneficiary |
|---|---|---|
| 1. Score badge in row | Unit list (Zen/table) | Producer scanning for triage |
| 2. Verdict detail panel | Unit editor sidebar | Producer reviewing flagged unit |
| 3. Separate AI vs deterministic checks | Unit list badges + editor tabs | All (trust clarity) |
| 4. Filter by verdict | Search (`q=`) | Producer building review queue |
| 5. Back-translation evidence | Unit editor (below source/target) | Non-fluent producer (core pattern) |
| 6. Auto-approve + audit comment | Unit state + comments | Workflow automation |
| 7. Escalation via needs-editing | Unit state + comments | Producer follow-up queue |
| 8. Verdict summary widget | Project/component dashboard | Producer at-a-glance status |
| 9. Variant suggestions | Editor suggestions | Producer one-click fix |
| 10. White-box reasoning log | Unit editor expandable | Developer/power user debugging |

---

## Sources

### Vendor Documentation (Primary)

- Phrase QPS Overview: <https://support.phrase.com/hc/en-us/articles/5709672289180-Phrase-QPS-Overview>
- Phrase Pre-translation (QPS thresholds, lock): <https://support.phrase.com/hc/en-us/articles/5709717749788-Pre-translation-TMS>
- Phrase CAT Pane (QPS display): <https://support.phrase.com/hc/en-us/articles/5709683926812-CAT-Pane-TMS>
- Phrase Quality Profiles (AI checks, lock, Insights): <https://support.phrase.com/hc/en-us/articles/25865045974300-Quality-Profiles>
- Lokalise Scoring Translation Quality: <https://docs.lokalise.com/en/articles/11631905-scoring-translation-quality>
- Lokalise AI LQA: <https://docs.lokalise.com/en/articles/7945761-ai-lqa>
- Crowdin AI Pipeline: <https://store.crowdin.com/ai-pipeline>
- Crowdin Copilot: <https://store.crowdin.com/crowdin-copilot>
- Crowdin Copilot blog: <https://crowdin.com/blog/meet-crowdin-copilot>
- Crowdin Pre-Translation: <https://support.crowdin.com/pre-translation/>
- Crowdin QA Check Settings: <https://support.crowdin.com/project-settings/qa-checks/>
- Crowdin Backtranslation: <https://store.crowdin.com/backtranslation>
- Transifex TQI: <https://help.transifex.com/en/articles/9465000-translation-quality-index-tqi>
- Transifex TQI Breakdown announcement: <https://community.transifex.com/t/new-feature-tqi-breakdown-for-deeper-insights/4278>
- Transifex TQI Insights in Editor: <https://community.transifex.com/t/new-tqi-insights-now-available-directly-in-the-editor/5245>
- Transifex Auto-Review: <https://community.transifex.com/t/now-live-supercharge-your-localization-with-auto-review-based-on-tqi/4762>
- Transifex Editor Filters: <https://help.transifex.com/en/articles/14287841-transifex-editor-filters-reference>
- XTM TQI settings: <https://help.xtm.ai/en/xtm-cloud/26.2/en/global-ai-translate---tqi-settings.html>
- XTM Intelligent Workflow: <https://help.xtm.ai/en/xtm-cloud/26.2/en/global-intelligent-workflow-settings.html>
- XTM TQI product page: <https://xtm.ai/ai-translation/tqi>
- Smartling LQE Agent: <https://help.smartling.com/hc/en-us/articles/25058582212507-Language-Quality-Estimation-Agent-for-Machine-Translation>
- Smartling LQA Dashboard: <https://help.smartling.com/hc/en-us/articles/19513205210651-Assess-Translation-Quality-with-the-LQA-Dashboard>
- Smartling LQA Agent: <https://www.smartling.com/software/lqaagent>
- Smartling LQA Suite: <https://www.smartling.com/software/lqa-suite/>
- Smartling Strings View Filters: <https://help.smartling.com/hc/en-us/articles/9352527251355-Strings-View-Search-Filters-Project>
- Smartling back-translation blog: <https://www.smartling.com/blog/what-is-back-translation-and-why-is-it-important>

### Cross-Domain (Calibrated Trust / HITL)

- Stella Ops AI UX Patterns (Verdict/Evidence/AI Assist panels): <https://stella-ops.org/docs/modules/web/ai-ux-patterns/>
- ByteLabs UX Patterns for AI Explainability and Trust: <https://www.bytelabs.space/blog/ux-patterns-for-ai-explainability-and-trust>
- TRuST-M (ACM WSDM 2025, LIME explanations preferred): <https://dl.acm.org/doi/10.1145/3779211.3793172>
- LoopRails HITL Content Moderation: <https://looprails.dev/article-hitl-content-moderation.html>
- Let's Build Content Moderation System Design: <https://letsbuildsolutions.com/blog/system-design/designing-a-content-moderation-system-automated-filtering-ml-classification-and-human-review-queues-at-scale/>
- EU AI Act Art.14 Human Oversight UX/API Patterns: <https://www.sota.io/blog/eu-ai-act-art14-human-oversight-ux-api-design-patterns-developer-guide-2026>
- EskiLab Human Review Queue Design: <https://eskilab.com/human-review-queue-design-ai-operations/>
- Mavik Labs HITL Review Queues 2026: <https://www.maviklabs.com/blog/human-in-the-loop-review-queue-2026/>
- ICO UK Guidance on Explaining AI Decisions: <https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/artificial-intelligence/explaining-decisions-made-with-artificial-intelligence/part-1-the-basics-of-explaining-ai/what-goes-into-an-explanation/>

### Back-Translation Specific

- SPF.io Alternative + Back Translations (similarity %): <https://www.spf.io/2024/04/05/accelerate-document-translation-with-alternative-translations/>
- Crowdin Backtranslation app: <https://store.crowdin.com/backtranslation>
