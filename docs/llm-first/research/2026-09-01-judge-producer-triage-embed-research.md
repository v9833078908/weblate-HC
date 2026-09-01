# Embedding producer triage for judge verdicts: UI, backend, and industry research

**Date:** 2026-09-01. **Status:** completed research. **Scope:** producer UX for
strings carrying `check:judge-reject` across the eight HCGameLoc projects;
embedding into existing Weblate surfaces with no new pages.

Assembled from three parallel investigations in one session: a map of the
existing UI (scout over `weblate/templates` + `weblate/trans/views/edit.py`), a
map of backend capabilities (scout over `judge_loop.py` / `autotranslate.py` /
`suggestion.py` / `models/judge.py`), and external research over 7 TMS products
(researcher over official documentation of Crowdin, Phrase, Smartling, Lokalise,
Transifex, memoQ, Unbabel). Every code claim was verified against repository
files as of the document date.

Related documents:

- `docs/llm-first/vision/llm-first-product-architecture.md` - sections 4.3-4.4
  (verdict write contract, V1-V3 UI evolution; the suggestion candidate is
  assigned to V3).
- `docs/llm-first/plans/2026-08-25-01-judge-producer-ux-and-delivery.md` - the
  shipped producer surface (resolutions, launchers, progress).
- `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md` - verdict card
  design, including the unimplemented "correction proposal as a suggestion" for
  reject.
- `docs/llm-first/research/2026-08-11-judge-ux-competitor-research.md` - early
  competitor research (pre-dating the card implementation).

## 1. Problem

The producer opens the list of blocked strings (`q=check:judge-reject`) and for
each string must: see the current verdict and take one of three decisions -
(a) keep the current text, (b) accept an AI-proposed fix, (c) edit manually -
and, once the queue is empty, download the translation files or push them to
the repository. Today the decision is smeared across three surfaces (the state
radio buttons, the verdict card with legally-named resolutions, the "Automatic
suggestions" tab), and the producer does not know where to click.

A live example of the overload (heart-abyss/temple/en): a card "Rejected - not
published" carries two nearly identical paragraphs from the two seats
(mistranslation/critical and terminology/critical about 'quotes' instead of
'ци'/Qi), the banner "The context changed (glossary or note updated) since this
verdict was recorded", and - despite that - the resolution form, although the
verdict is stale relative to context and no actions should be offered against
it.

### Hard design constraints

1. **No new pages** - embed into existing surfaces and remove clutter.
2. **Stale gate.** A verdict with `judge_stale` or `judge_context_changed` must
   not offer keep/accept/edit. The only action is "Re-check"; any repair
   candidate must be generated from the new context.
3. Clearing of `check:judge-reject` after a manual save is not proof of
   quality: the verdict is bound to the target hash, saving removes the
   projection, but nobody has judged the new text. Release readiness cannot
   rest solely on "the filter went empty".

## 2. Existing UI map (what is embeddable, what is removable)

### 2.1 Translate page `weblate/templates/translate.html`

- Lines 57-281: GET search form (65-68), the single translation POST form
  (71-281). Inside the editor card: secondary language (105-108), **the judge
  back-translation - only here** (110-116, "Approximate reconstruction"),
  source/context/explanation, `{% crispy form %}` (184), footer
  save / save-stay / suggest / skip (191-281).
- Lines 287-368: tabs nearby / similar / variants / suggestions / others /
  comments / machinery / other languages / history. Bodies: machinery 495-574,
  comments 576-626.
- Line 706: include of `snippets/judge-verdict.html` in the right column,
  **before** the "Things to check" card (708-763). Judge checks are not
  duplicated into "Things to check" by design (`docs/admin/checks.rst:177-179`).
- Zen mode (`zen.html`, `zen-units.html`, `get_zen_unitdata`
  `edit.py:1867-1952`): the judge card **does not render at all**. Decision:
  leave Zen untouched.

### 2.2 Verdict card `weblate/templates/snippets/judge-verdict.html`

Full branch map (5-189): the card exists when
`judge_verdict or judge_stale or judge_unparsed` (5-8); "not parsed" notice
12-21; "stale" notice 23-32; reject branch 33-64 (accepted_as_is vs
"not published" status 38-41, timestamp 44-46, **context-changed footnote
47-50**, per-seat error loop 52-59, two-seat note 61-63); flag 65-90; accepted
91-124 (minor errors 109-121); repair evidence 125-151; resolution badge/reason
152-166; **resolution form 167-189**.

Problems:

- Errors of the two seats render as two full loops - with agreeing seats this
  is duplicated prose.
- The resolution form posts `this_unit_url` as `next` (176-181) - after a
  decision the producer stays on the same string; there is no auto-advance
  through the queue.

### 2.3 Correction: the freshness gate already holds (earlier draft claimed a defect)

An earlier draft of this document reported a missing stale gate in
`_judge_view_context`. Verified against the code, that claim is wrong:
`current_round` filters verdict rows by the unit's current target *and*
context hashes (`_current_snapshot_hashes`,
`weblate/trans/models/judge.py:759-769`), resolution choices are built only
from `judge_current_verdict = current_verdict(unit)`
(`weblate/trans/views/edit.py:1324-1331`), and `resolve_verdict` re-reads
`current_verdict` under the Unit lock and raises `stale` when drift made it
None (`weblate/trans/models/judge.py:1072-1093`). When the card shows the
"context changed" note, the displayed verdict is the target-only fallback
(`active_verdict`) and the current verdict is by construction None - the
resolution form and the drift note are mutually exclusive already.

The remaining gap is UX, not safety: a drifted or stale card is an
explanatory footnote with nothing to click - no re-judge affordance and no
queue exit.

### 2.4 State radio buttons ("Needs editing / Waiting for review / Approved")

Not a separate widget but the `review` field of the translation form:
`TranslationForm` ChoiceField + RadioSelect (`forms.py:683-690`),
initialization `review=unit.state` (707-714), choice filtering by
readonly/fuzzy (717-753), crispy layout
`InlineRadios('review', css_class='review_radio')` (764-772), fuzzy/review
widgets hidden by permissions (773-777). JS consumers:
`editor/base.js:29-81` (markFuzzy/markTranslated/markApproved),
`editor/zen.js:209-211` (autosave on change). **Verdict:** cannot be removed or
disabled - only visually de-emphasized while preserving submit semantics.

A related trap: reject holds the string in FUZZY; a manual fix saved with the
radio still on "Needs editing" remains writable for the next judge run
(`autotranslate.py:725-727`) and will be overwritten by machine translation in
phase 1. The "Edit manually" scenario must switch the radio to "Waiting for
review".

### 2.5 Queue navigation - already exists

- `SearchNavigation` stores stable result IDs in the session keyed by the
  search URL (`edit.py:386-469,596-650,700-743`).
- `get_translate_unit` resolves offset/checksum and boundaries
  (`edit.py:1220-1295`); the context exposes `search_url/items/query`,
  `filter_pos/filter_count`, first/prev/next/last URLs
  (`edit.py:1385-1388,1446-1480`).
- The "N of M" indicator + jump - `snippets/position-field.html:1-113`.
- Saving a translation redirects to `next_unit_url` unless save-stay
  (`handle_translate`, `edit.py:956-980`).

**Verdict:** `q=check:judge-reject` + existing navigation = a ready-made queue
with zero-inbox behavior. No separate page is needed.

### 2.6 "Automatic suggestions" tab

Renders when `user_can_use_machinery` (translate.html:343-351), lazily
activated by JS (`full.js:263-302`), services from `#js-translate
data-services`, fetch and row rendering (`full.js:335-399,419+`). Results live
client-side only - there is no stored object. **Verdict:** until a stored
candidate exists, do not duplicate the tab into the judge card; a button in the
card that activates the tab is acceptable.

### 2.7 Dashboards and the ship CTA

- `snippets/judge-readiness.html:3-29` - the existing "AI judge" strip on
  component (243-244) / project / workspace: needs_human / not_reviewed /
  unparsed + breakdown_url / run_url / last_run. No blocked counter, no ship
  CTA.
- Counters already computed: `judge_reject` (critical for current text),
  `judge_stale`, `judge_unparsed`, `judge_resolved`, `judge_escalated`,
  `judge_needs_human` (`weblate/utils/stats.py:98-111,936-978`); the cache is
  persistent with async invalidation (580-640) - a release gate needs a fresh
  query, not only the cache.
- Download: Files menu `component.html:72-103`, `translation.html:57-61`
  (+ customizable tab 306-352); pending changes are committed before the zip is
  produced (`trans/views/files.py:90-110`), permission `translation.download`.
- Git: commit/push `trans/views/git.py:79-91,121-128,264-282`; pending/outgoing
  status - `trans/views/js.py:176-223`; repository maintenance tabs:
  `component.html:509-512`, `translation.html:428-431`.

**Verdict:** "Blocked for release: N" and a conditional "Download / Push" CTA
embed into the existing readiness strip and existing menus - no new pages.

## 3. Backend capabilities

### 3.1 Single-string re-judge - feasible through reuse

- `run_judge_batch` (`judge_loop.py:1080-1300`) accepts a one-unit list;
  always `validate_judge_configuration()` (both seats mandatory,
  `judge.py:45,352-467,513-526`); `run` is optional (uuid when None);
  evidence-only = `writable_ids=set()`.
- **Critical seam:** `run_judge_batch` itself only persists verdicts; the
  final state projection (`state_for_verdict`, stale-conflict detection,
  max-length override) and the `JudgeRunUnit` audit are done by the post-loop
  of `AutoTranslate.process_judge` (`autotranslate.py:805-903`). Therefore the
  correct one-unit call is a **wrapper over the existing celery
  `auto_translate`** (`tasks.py:975-1087`, already accepts `unit_ids`) with
  `mode="judge"`: permissions, the `JudgeRun`
  (`autotranslate.py:1172-1190`), projection, and audit come for free. A
  direct `run_judge_batch` call from a view is only evidence persistence with
  no producer-visible state transition.
- Cost: 1 unit x 2 seats = 2 baseline judge requests
  (`judge_initial_request_count`, `judge.py:545-558`); the upper bound excludes
  unparsed retries and repair MT calls - a UI estimate should say "baseline
  judge calls".
- Risks: duplicate POST = duplicate payment (needs a "task already running"
  gate + `use_cache`); a concurrent edit leads to repair rollback
  (`_apply_repair` verifies snapshot ownership under `select_for_update`,
  `judge_loop.py:429-470`).

### 3.2 Freshness semantics - mostly present

- `compute_target_hash` - all plural forms (`models/judge.py:98-101`);
  `compute_context_hash` - source + note + explanation + sorted JSON of
  prompt-visible glossary entries (107-130).
- `active_round` - target-only, parsed-only (694-737): a verdict "survives"
  context drift. `current_round` - target+context, including fresh unparsed
  rows (739-757). `collegium_verdict` - the strictest parsed seat (829-843).
  `current_verdict = collegium_verdict(current_round)` (994-996).
- Three states are cheaply distinguishable: verdict current
  (`current_verdict` non-empty); context drift (active non-empty, current
  empty, context hash differs); target changed (`judge_stale`). Caution: stats
  annotations are target-only and can disagree with context freshness - the
  action gate must require `current_verdict and not judge_context_changed and
  not judge_stale`.

### 3.3 Candidate as a native Suggestion - feasible, needs a contract

- `SuggestionManager.add(unit, target, request, vote=False, user=None)`
  (`suggestion.py:28-105`): max-length validation, `fix_target`, refusal when
  equal to the current target, deduplication by unit+target; user defaults to
  `request.user` or `get_anonymous()`; the `userdetails` JSON is a ready home
  for provenance (`judge-repair`, verdict id, target/context hashes).
- `Suggestion.accept(request, permission='suggestion.accept')`
  (`suggestion.py:188-221`): permission check, `unit.translate(...)` (Change
  ACCEPT), suggestion deletion; `save_backend` re-runs `run_checks`.
- What is missing: provenance in Change, freshness binding (accept would
  succeed even after drift - needs a hash guard under lock), candidate expiry
  when the verdict goes stale, and permissions: acceptance is
  `suggestion.accept` while resolutions are `unit.review`; roles can diverge.
- Ad hoc candidate generation: `repair_targets` (`judge_loop.py:152-219`)
  works for one unit but requires a prior `run_checks` (judge evidence reaches
  the prompt through `failing_checks`, `checks/judge.py:94-101` ->
  `machinery/llm.py`), a configured routed engine for the project, and the
  result is transient - no object is created. Today repair writes directly
  into the target (FUZZY) with rollback on a new deterministic check
  (`judge_loop.py:456-468`); for translated non-writable strings repair never
  runs at all - no candidate exists.

### 3.4 Resolutions

`resolve_verdict` (`models/judge.py:998-1147`): lock Unit -> lock the
representative verdict, refusal on expected-id mismatch / staleness,
`ALLOWED_RESOLUTION_TRANSITIONS` (1006-1036: accepted_as_is from
reject/flag/escalated; sent_back is never requested - "not exposed";
accepted_as_is is terminal), a mandatory reason, the `unit.review` permission,
an immutable Change. The endpoint already exists: `resolve_judge_verdict`
(`edit.py:1839-1865`, URL `urls.py:476-479`), form `JudgeResolutionForm`
(`forms.py:1438-1465`). A target change after a resolution makes the verdict
non-current - the resolution stays on the historical row.

## 4. External research: how 7 TMS embed AI/LQA verdicts into the editor

### 4.1 Per-product matrix

| Product | Inline actions and evidence | Staleness | Release gating | Queue |
|---|---|---|---|---|
| Crowdin | Inline QA banner in the editor; severity controls Save blocking ("Save Anyway" for warnings); per-issue Autofix and "Fix (N)" for executable fixes; custom checks return message + span replacements | Manual "Revalidate" after settings/AI-prompt/terminology changes; no auto-stale | Error blocks save/navigation; warning - explicit save anyway | QA filter + auto-advance to the next string after save |
| Phrase | QA pane on the right; clicking an issue highlights the segment; correct+confirm or Ignore; unignorable blocks confirmation; AI-checks tab with descriptions | Issues **stay attached** after a segment edit until an explicit "Run AI checks" (result replacement) - an anti-pattern for us | Mandatory QA warnings must be cleared before job completion | Editor filters + Prev/Next |
| Smartling | Right Quality Check panel; high blocks save/submit, medium - explicit "save with errors", low - warning; LQA Agent records MQM errors, **does not modify the translation**, routes only failed locales to humans | Documented "results can change" after edits/TM/glossary; re-run "Check now"; no explicit stale badge | High severity = hard block | "LQA Errors Present" filter |
| Lokalise | AI LQA report with suggested corrections; AI Suggestions panel while editing; per-string score with an issues drill-down | Score invalidated/pending on source/target change until recalculation | Score threshold routes to a review task; no hard export gate documented | Editor selection + workflow task |
| Transifex | TQI Insights tab in the editor: signals, issues, variants; **"Use this" replaces the target**, then save | **TQI removed on source/target change until recalculation; the editor shows "Pending TQI"** - a model for us | Editor error blocks save; warning saves with a notice | Rich filters (check type, issue state, TQI range) |
| memoQ | View pane by the grid: "Correct text as suggested", "View or dispute" with a comment, Hide false positive, Convert to LQA | Re-run/filter QA; no explicit stale contract documented | Errors block export; warnings do not | "Error" filter + auto-jump to the next after confirmation |
| Unbabel | Side panel; AI edit as strikethrough + blue replacement, "Accept" button, "Report bad suggestion"; recalculation after edits | Recalculate after a correction; no auto-stale | Suggestions are recommendations; ignoring does not affect delivery (weaker than our requirement) | Counter + arrows over flagged segments only |

Sources (all verified during this research):

- <https://support.crowdin.com/online-editor/>
- <https://support.crowdin.com/project-settings/qa-checks/>
- <https://support.crowdin.com/enterprise/custom-qa-checks/>
- <https://support.phrase.com/hc/en-us/articles/5709694857372-Quality-Assurance-Pane-QA-TMS>
- <https://support.phrase.com/hc/en-us/articles/25865045974300-Quality-Profiles>
- <https://support.phrase.com/hc/en-us/articles/14313477218588-Auto-LQA-TMS> (deprecated 2026-06-30, historical evidence only)
- <https://support.phrase.com/hc/en-us/articles/5709720416796-Filtering-TMS>
- <https://help.smartling.com/hc/en-us/articles/10447325366043-Quality-Checks-On-Your-Work>
- <https://help.smartling.com/hc/en-us/articles/49428460628507-LQA-Agent-AI-Powered-Quality-Assurance>
- <https://help.smartling.com/hc/en-us/articles/1260806372849-Translation-Quality-Features-Overview>
- <https://docs.lokalise.com/en/articles/7945761-ai-lqa>
- <https://docs.lokalise.com/en/articles/11631905-scoring-translation-quality>
- <https://help.transifex.com/en/articles/6318944-additional-tools-in-the-transifex-editor>
- <https://help.transifex.com/en/articles/12088096-tqi-tasks>
- <https://help.transifex.com/en/articles/6241755-translation-checks-behavior>
- <https://help.transifex.com/en/articles/14287841-transifex-editor-filters-reference>
- <https://docs.memoq.com/current/en/memoQWeb-help/mqw-view-pane.html>
- <https://docs.memoq.com/current/en/memoQWeb-help/mqw-translation-editor.html>
- <https://docs.memoq.com/current/en/Workspace/go-to-next-segment.html>
- <https://help.unbabel.com/hc/en-us/articles/29736962824343-Translator-Copilot>
- <https://help.unbabel.com/hc/en-us/articles/24499549061527-AI-Quality-Suggestions>

### 4.2 Four evaluated interaction patterns (researcher's per-option analysis)

The researcher evaluated four concrete interaction patterns against this
fork's Bootstrap/jQuery server-rendered stack before the transferable
patterns below were distilled.

#### Option A - Crowdin-style inline QA banner with executable fix

One compact verdict block next to the target editor: severity, one-line
evidence, explicit action row. Fresh reject offers "Use suggested fix",
"Keep as-is", "Edit manually"; stale offers only "Re-check". A candidate is
a preview until explicitly applied and saved.

- **Stack fit:** highest - extends the already embedded card
  (`translate.html:706`, `judge-verdict.html:33-189`) with ordinary Django
  POST forms; preserves save/skip navigation.
- **Pros:** inline banner with severity-driven save blocking ("Save Anyway"
  for warnings); per-issue Autofix / "Fix (N)" when a check returns
  executable fix data; custom checks return message + span replacements - a
  model for a stored candidate instead of an opaque instruction; QA filter +
  auto-advance matches the producer queue directly.
- **Cons / risks:** Crowdin's autofix suits deterministic replacements - an
  LLM candidate is semantically unsafe without preview + explicit save;
  Crowdin freshness is a manual project-wide "Revalidate", not a per-segment
  stale gate; a candidate lifecycle is medium complexity here because repair
  is currently immediate mutation/rollback with no stored object
  (`judge_loop.py:438-468`).
- **Effort / reversibility:** M; highly reversible (localized to the
  snippet, context assembly, and POST endpoints).

#### Option B - Phrase/Smartling-style side QA pane with explicit disposition

Treat the right-column judge card as a quality pane: compact severity badge
and summary by default, expandable evidence; "Keep as-is" with a required
reason, "Use fix" only when a current candidate exists, "Edit manually",
"Escalate"; "Re-check" as the sole action when stale.

- **Stack fit:** high - the existing card and list-group idiom already match
  a pane; existing transitions require a reason and preserve evidence
  (`models/judge.py:1006-1058`); the stale form is hidden in the already
  assembled context (`edit.py:1310-1337`) with no client-side routing.
- **Pros:** Phrase's pane highlights the affected segment per issue and
  distinguishes ignorable from unignorable warnings (the latter block
  confirmation); "Run AI checks" re-evaluates and replaces prior results;
  Smartling separates hard/soft severity (high blocks save/submit, medium is
  an explicit "save with errors", low warns); Smartling's LQA Agent records
  MQM errors without modifying the translation and routes failures to a
  human step - a strong precedent for the safe manual-edit fallback.
- **Cons / risks:** Phrase's current behavior leaves AI issues attached
  after a segment changes until a manual rerun - unsafe here unless
  freshness is a hard UI and server-side gate; Phrase's older Auto LQA docs
  are deprecated (historical evidence only); a pane with several
  dispositions becomes another dense dashboard unless summaries stay one
  line and evidence stays collapsed.
- **Effort / reversibility:** M; highly reversible (reshapes the existing
  snippet and guards existing form visibility).

#### Option C - Lokalise/Transifex score plus variants in the editor

A compact quality score/status next to the target, an expandable
issue/variant panel with "Use this" per candidate; freshness invalidates the
score/candidates and shows "Pending re-check" until recomputed.

- **Stack fit:** medium - rendering is easy, but the data model has no
  stored suggestion object and repair is direct (`judge_loop.py:438-468`),
  so this adds more persistence and state than a verdict card.
- **Pros:** Transifex embeds TQI Insights with signals/issues/variants and a
  "Use this" action that replaces the target; Lokalise gives a per-string
  score with an issues drill-down and threshold-based routing; both carry a
  strong freshness invariant (Transifex removes TQI on source/target change
  until recalculation; Lokalise shows pending/recalculation).
- **Cons / risks:** a scalar score obscures why a critical was rejected -
  this fork needs severity/category/evidence first, not score-first;
  storing multiple variants is more scope than the producer's three
  outcomes require; both products lean on workflow/task routing, which
  would recreate the separate queue surface this design explicitly avoids.
- **Effort / reversibility:** L; moderately reversible once candidate/score
  persistence and invalidation semantics exist.

#### Option D - memoQ/Unbabel correction and dispute panel

A small action column under the verdict summary: "Apply correction" for a
current candidate, "Keep as-is"/"Dispute" with a reason, focus-the-editor
for manual work; "Recalculate" replaces the result; a compact counter plus
previous/next controls over flagged segments only.

- **Stack fit:** high for actions and navigation - Bootstrap list-group
  items map to memoQ's pane; Unbabel's arrows map to the existing
  `next_unit_url`/`prev_unit_url` (`edit.py:1446-1480`); requires a stored
  candidate only for "Apply correction", no global score.
- **Pros:** memoQ exposes "Correct text as suggested", "View or dispute",
  comments, hide-false-positive, convert-warning-to-LQA; filtered "Error"
  navigation with auto-jump after confirmation; Unbabel highlights AI
  changes as strikethrough + replacement with an explicit "Accept", a
  bad-suggestion report, and recalculation after acceptance; its
  counter/arrows over flagged segments only are a direct precedent for
  queue completion without a new page.
- **Cons / risks:** Unbabel treats suggestions as non-blocking
  recommendations - weaker than the required critical release gate; memoQ's
  reviewer actions include delete/dispute, while this fork must keep the
  verdict immutable and record a producer resolution instead of deleting
  evidence; neither documents an explicit source/context stale contract, so
  the fork enforces it itself.
- **Effort / reversibility:** M; reversible while the candidate and
  recalculation endpoints stay narrow.

#### Researcher's comparison and recommendation

| Option | Effort | Risk | Stack fit | Maintenance | Key tradeoff |
|---|---|---|---|---|---|
| A. Crowdin inline banner + executable fix | M | Medium: candidate safety and stale invalidation must be server-enforced | Very high | Low-Medium | Fastest one-click fixes, but AI text must not be equated with deterministic autofix |
| B. Phrase/Smartling side QA pane | M | Low-Medium: clear severity gates, but the pane can become dense | High | Low | Strongest balance of compact evidence, explicit disposition, and release semantics |
| C. Lokalise/Transifex score + variants | L | Medium-High: score/candidate persistence and invalidation | Medium | Medium-High | Best prioritization and variant UX, but more state than the three outcomes need |
| D. memoQ/Unbabel correction/dispute panel | M | Medium: dispute/delete patterns need immutable-audit adaptation | High | Low-Medium | Best navigation and correction affordances, weakest built-in release gate precedent |

The researcher's recommendation: **Option B as the base, plus Option A's
explicit candidate-application affordance and Option D's flagged-segment
navigation** - keep the embedded card, severity + one-line evidence with
expandable detail, exactly three fresh-verdict outcomes, "Re-check" as the
only stale action, filtered query + existing next-unit navigation, and a
queue that reaches zero only when no unresolved critical remains. This
avoids Phrase's stale-result hazard, Unbabel's non-blocking release
behavior, and Lokalise/Transifex's extra score/task state. The three
solutions in section 5 are this recommendation cut into shippable
increments.

### 4.3 Transferable patterns (ranked by fit)

1. **One embedded quality card, not a triage page** - in all 7 products the
   panel lives next to the segment editor.
2. **Freshness is a hard state machine.** Target/context changed - only
   "Re-check", no actions against old evidence (models: Transifex TQI removal
   with "Pending TQI", Lokalise invalidation; anti-model: Phrase, where
   issues linger after an edit).
3. **Candidate: preview, then explicit apply** ("Use this" / "Accept" /
   Autofix), never a silent target mutation.
4. **Severity first, evidence compact:** a colored badge + a one-line summary;
   per-seat/category details behind a disclosure.
5. **Filter + auto-advance to the next** instead of a separate page (Crowdin,
   memoQ, Unbabel).
6. **Release gate on critical with a visible reason** (Crowdin/Phrase/
   Smartling/memoQ block; Unbabel does not - and that is a weakness).
7. **Every non-fix disposition records a reason** and never deletes evidence
   (our resolutions already work this way).
8. **Re-check is a result-replacement operation, not a dismiss button.**

## 5. Three solutions (value / effort)

### Solution 1 - repackage the existing surface (S)

Templates + `_judge_view_context` + minor JS only:

- Drift/stale card UX: pin the existing target+context freshness gate with
  regression tests (no production change - see 2.3); on a drifted or stale
  card render one status line + one action "Re-judge" (until Solution 2 - a
  link to the prefilled judge launch over the filter).
- Collapse seat duplication into a one-line summary + `<details>` with full
  texts, models, timestamps.
- Outcome-named actions on a fresh critical: "Keep as is" (the existing
  resolution), "Edit manually" (focus the editor + JS switches the radio to
  "Waiting for review" - protection against overwrite by the next run),
  "AI variants" (activates the existing suggestions tab).
- Auto-advance: the resolution form posts `next_unit_url` instead of
  `this_unit_url`.
- Readiness strip: "Blocked for release: N" (link to the filter) + a
  "Download / Push" CTA over the existing menus.

**Limitation (important): Solution 1 is cosmetic, not a standalone release
workflow.** After a manual save the `judge-reject` projection disappears (the
verdict is bound to the target hash) and auto-advance moves on, although
nobody has judged the new text - the "Blocked: 0" counter can become a false
zero. Until per-unit re-check exists, the ship CTA must remain either hidden
or explicitly labeled "N strings fixed manually and not re-checked" (the
`judge_stale` counter already reflects this: manually fixed strings flow from
`judge_reject` into `judge_stale`). CTA gate: `judge_reject == 0 AND
judge_stale == 0 AND judge_unparsed == 0` over a fresh query, not the cache.

### Solution 2 - + "Re-check this string" (M)

- POST endpoint -> a celery wrapper `auto_translate(unit_ids=[id],
  mode="judge")`: permissions, JudgeRun, state projection, audit - all
  existing.
- The explicit "Re-check" button exists only on pre-existing stale/context-
  drift cards. The manual-edit path cannot use a button: after save the unit
  immediately leaves `check:judge-reject` and the handler redirects to the
  next unit, so its card is never shown again. The triage save therefore
  enqueues the per-unit re-check automatically before redirecting; a
  "re-checking" badge shows if the producer navigates back while the task is
  pending. On completion the string either honestly leaves the filter or
  returns with a fresh verdict.
- Cost: 2 baseline judge calls per string. Double-payment gate: disable the
  button while a task is unfinished + `use_cache`.
- **Together with Solution 1 this forms the minimum viable release workflow**:
  the queue stops lying, the false zero is eliminated.

### Solution 3 - + "Accept the AI variant" (L)

- In the batch run and in the per-unit re-check, the repair candidate for a
  critical is persisted as a native `Suggestion` (provenance + target/context
  hashes in `userdetails`; never written into the target directly, including
  non-writable strings).
- The card shows the candidate diff; "Accept" verifies the hashes under a
  lock, applies, schedules an auto re-check; on context drift the candidate
  expires together with the verdict; generation always uses the new context.
- Closes the architecture's V3 item (suggestion candidate) with the native
  Weblate mechanism. Open questions: extra MT-call cost for every critical
  string; the `suggestion.accept` vs `unit.review` permission divergence; a
  diff UX for producers who cannot read the language (show the candidate BT
  after re-check).

### Summary

| | Effort | Value | Standalone |
|---|---|---|---|
| Solution 1 | S | Removes the overload, fixes the stale bug | No - cosmetic; the release gate stays conservative |
| Solutions 1+2 | S+M | Closed loop, an honest queue | Yes - the minimum viable workflow |
| Solution 3 | +L | The full three buttons | Optional, driven by 1+2 usage data |

## 6. Recommendation

The minimum release increment is **Solutions 1+2 together** (drift-card UX,
compact card, outcome-named actions, auto-advance, per-unit re-check, a
conservative ship CTA). Solution 3 is a separate phase after observing
producer behavior: if "edit manually" consistently dominates over accepting
variants from the tab, the stored candidate will pay off.

## 7. Proposed next steps

1. **Product decision (owner: producer/PM).** Approve Solutions 1+2 as one
   increment; defer Solution 3. Confirm two spend rules: who may press
   per-unit "Re-check" (2 baseline judge calls per press) and whether it is
   offered to every reviewer or only to holders of `unit.review`.
2. **Write the implementation plan** at
   `docs/llm-first/plans/2026-09-01-judge-producer-triage-embed.md` and get
   it approved before editing (working agreement). Proposed task cut:
   - Task 1, freshness invariant + drift-card action: regression tests
     pinning the existing gate (see 2.3 - no production change); the
     stale/drift card renders one status line and a single "Re-judge"
     affordance (`judge-verdict.html:23-32,167-189`). Tests: card branch
     rendering and a POST to `resolve_judge_verdict` rejected for a
     drifted verdict.
   - Task 2, card compression: merged one-line two-seat summary +
     `<details>` disclosure with full per-seat evidence, models, timestamps
     (`judge-verdict.html:33-64`).
   - Task 3, outcome actions on a fresh critical: "Keep as is" (existing
     resolution form), "Edit manually" (focus editor; JS flips the review
     radio to translated so a manual fix does not stay FUZZY-writable,
     `editor/base.js`), "AI variants" (activates the existing machinery
     tab).
   - Task 4, queue auto-advance: the resolution form posts `next_unit_url`
     instead of `this_unit_url` (`judge-verdict.html:176-181`).
   - Task 5, readiness strip + conservative ship CTA: "Blocked for release:
     N" link to the filter; CTA enabled only when fresh
     `judge_reject == 0 AND judge_stale == 0 AND judge_unparsed == 0`
     (`snippets/judge-readiness.html`, `trans/views/basic.py:701-747`),
     pointing at the existing download menu and repository push.
   - Task 6, per-unit re-check (Solution 2): POST endpoint + celery wrapper
     over `auto_translate(unit_ids=[unit.id], mode="judge")`
     (`tasks.py:975-1087`) so permissions, `JudgeRun`, state projection,
     and audit are reused; a per-unit "task already running" guard plus
     `use_cache` against double payment; a "re-checking" badge on the card
     while the task is pending. The explicit button appears only on
     pre-existing stale/context-drift cards; for the triage manual-edit
     path the save handler enqueues the re-check automatically before its
     redirect, because the saved unit leaves `check:judge-reject` at once
     and its card is never rendered again (`handle_translate`,
     `edit.py:956-980`).
   - Task 7, verification: judge view/loop test suites
     (`weblate/trans/tests/test_judge*.py`, `weblate/checks/tests/test_judge.py`),
     template lint (djLint via prek), and a browser smoke pass on the dev
     instance (port 3001) over a `q=check:judge-reject` queue: stale card
     shows only "Re-check", fresh card shows the three outcomes,
     resolution auto-advances, a manual save enqueues the automatic
     re-check (string reappears in `judge_stale`/pending until judged),
     CTA stays disabled until the three counters are zero.
3. **Rollout.** Dev instance first (`./rundev.sh`); production deployment
   only with explicit approval (working agreement). No migrations are
   required for Solutions 1+2.
4. **Measure before Solution 3.** From `JudgeRun`/`JudgeRunUnit` history and
   resolution Changes, count per week: accepted_as_is, manual edits followed
   by re-check, machinery-tab acceptances. If manual edits dominate and
   producers rarely accept tab variants, implement Solution 3 (stored
   candidate as a native `Suggestion` with provenance, freshness-guarded
   accept, expiry on drift) as its own plan.
