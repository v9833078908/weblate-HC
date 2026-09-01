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

### 2.3 Confirmed defect: the stale gate is missing

`_judge_view_context` (`weblate/trans/views/edit.py:1298-1362`) computes
`judge_can_resolve` **without regard to** `judge_context_changed`: context
drift (glossary/note updated) stays an explanatory footnote while the
resolution form renders. This is exactly the live example. `judge_stale`
suppresses the form only indirectly (no current verdict - no choices), while
context drift does not: `current_verdict` is non-empty because `current_round`
filters by target+context, whereas the displayed active verdict comes from the
target-only fallback (`models/judge.py:694-757,994-996`). The gate must be
enforced server-side.

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

Sources: support.crowdin.com/online-editor/, /project-settings/qa-checks/,
/enterprise/custom-qa-checks/; support.phrase.com articles 5709694857372
(QA Pane), 25865045974300 (Quality Profiles), 14313477218588 (Auto LQA,
deprecated 2026-06-30); help.smartling.com articles 10447325366043,
49428460628507; docs.lokalise.com articles 7945761, 11631905;
help.transifex.com articles 6318944, 12088096, 6241755, 14287841;
docs.memoq.com mqw-view-pane, go-to-next-segment;
help.unbabel.com articles 29736962824343, 24499549061527.

### 4.2 Transferable patterns (ranked by fit)

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

- Server-side stale gate: `judge_can_resolve=False` when `judge_stale` or
  `judge_context_changed`; a stale card = one status line + one action
  "Re-check" (until Solution 2 - a link to the prefilled judge launch over the
  filter).
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
- A button on the stale/drift card and after a manual save; a "re-checking"
  badge; on completion the string either honestly leaves the filter or returns
  with a fresh verdict.
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

The minimum release increment is **Solutions 1+2 together** (stale gate,
compact card, outcome-named actions, auto-advance, per-unit re-check, a
conservative ship CTA). Solution 3 is a separate phase after observing
producer behavior: if "edit manually" consistently dominates over accepting
variants from the tab, the stored candidate will pay off.

The implementation decision is to be recorded in a separate plan under
`docs/llm-first/plans/` (as of this document's date no plan has been created
or approved).
