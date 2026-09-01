# Producer triage for judge verdicts, Solution 1 implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Make the embedded judge card a usable producer triage surface for
every verdict severity (critical/`judge-reject`, major/`judge-flag`,
minor/`judge-note`): gate resolutions on context freshness, compress the
two-seat evidence, name the actions by outcome, auto-advance the queue, and
give the component page a conservative release CTA. No new pages, no new
models, no migrations.

**Architecture:** Templates, `_judge_view_context`, `resolve_verdict`, and
the readiness strip only. The verdict stays immutable; a resolution is
recorded only against a verdict whose target *and* context still match the
unit. The release CTA reads existing per-translation stats
(`judge_reject`/`judge_stale`/`judge_unparsed`) and never invents a new
counter.

**Tech stack:** Python 3.14, Django templates (Bootstrap/jQuery), pytest,
Docker Compose dev instance.

**Research:** `docs/llm-first/research/2026-09-01-judge-producer-triage-embed-research.md`
(sections 5-7). Deviation from that document's Task 3: the "Edit manually"
button and its state-switching JS are dropped per producer decision
2026-09-01 - the existing editor flow already covers manual fixes.

**Status:** proposed, awaiting approval. Out of scope: per-unit re-check
endpoint (Solution 2), stored repair candidate as `Suggestion` (Solution 3).

---

## Task 1: Context-drift gate on resolutions

A verdict whose glossary/note context drifted is still resolvable today:
`judge_can_resolve` (`weblate/trans/views/edit.py:1324-1342`) ignores
`judge_context_changed`, and `resolve_verdict`
(`weblate/trans/models/judge.py:1039-1158`) verifies only the target-fresh
representative pk, never the context hash. (Target staleness is already
transitively gated: a stale round yields no current verdict, hence no
choices.)

**Files:**

- Modify: `weblate/trans/tests/test_judge_views.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`

### Step 1: Write failing tests

- View: after a glossary/note change that flips `judge_context_changed`,
  the unit page renders no resolution form and `judge_can_resolve` is
  false, for critical and major verdicts alike.
- Model: `resolve_verdict` against a context-drifted verdict raises
  `JudgeResolutionError("stale", ...)` even when the representative pk
  matches; the resolution row stays untouched.

### Step 2: Implement

- `_judge_view_context`: `judge_can_resolve` additionally requires
  `not judge_context_changed`.
- `resolve_verdict`: under the unit lock, recompute the context hash
  (`compute_context_hash` over source/note/explanation/matched glossary
  entries, same inputs as `_judge_view_context`) and raise the existing
  `stale` error on mismatch.
- Template: on `judge_context_changed`, next to the existing drift note
  render one action - a "Re-judge this string" link to the automatic
  translation page prefilled with `mode=judge&q=id:{unit.pk}` (the
  `judge_queue_strip_context` run-URL pattern,
  `weblate/trans/views/basic.py:750`). This is the Solution 1 stand-in for
  the per-unit re-check endpoint.

### Step 3: Verify GREEN

`uv run pytest weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge.py`
(or `./rundev.sh test ...`).

## Task 2: Compress the two-seat card for every severity

The reject (`judge-verdict.html:33-64`), flag (`:65-89`), minor-pass
(`:90-115`), and repair-evidence (`:117-138`) branches each duplicate the
per-seat error loop, producing four to eight paragraphs of model prose per
card.

**Files:**

- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing tests

Rendered card contains exactly one visible summary line per verdict
(severity badge + first error description) and a `<details>` element
holding the full per-seat evidence (model names, timestamps, every
error, back-translations); repeated for reject, flag, and minor cards.

### Step 2: Implement

Extract one shared seat-evidence loop into the snippet used by all four
branches: visible part is the severity badge, ship/hold badge, and a
single merged one-line summary; everything else moves under native
`<details>/<summary>` (keyboard-accessible, per `ACCESSIBILITY.md` -
no JS). Keep the existing badges and i18n strings; keep Zen untouched
(the card does not render there).

### Step 3: Verify GREEN

Same suites as Task 1 plus `uv run prek run djlint-django --files weblate/templates/snippets/judge-verdict.html`.

## Task 3: Outcome-named actions per severity

Today a fresh major cannot be kept: `ALLOWED_RESOLUTION_TRANSITIONS`
(`weblate/trans/models/judge.py:1010-1036`) allows FLAG only
`"" -> escalated` and `escalated -> accepted_as_is`; the direct
`"" -> accepted_as_is` exists only for REJECT. A minor verdict is
represented by verdict `pass` with `max_severity == "minor"` and has no
transitions - correctly, since it never blocks anything.

**Files:**

- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_views.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`

### Step 1: Write failing tests

- Model: `resolve_verdict` accepts `(FLAG, "", accepted_as_is)`; the unit
  stays/returns `STATE_TRANSLATED` (already handled by the
  `new_state = STATE_TRANSLATED` branch, `models/judge.py:1118-1119`); a
  `JUDGE_RESOLUTION` Change is written; `(PASS, ...)` transitions remain
  absent.
- View: a fresh flag card offers both "Accepted as is" and "Escalated"
  choices; a minor card offers no resolution form but does render the
  compact evidence.

### Step 2: Implement

- Add `(JudgeVerdict.Verdict.FLAG, "", JudgeVerdict.Resolution.ACCEPTED_AS_IS)`
  to `ALLOWED_RESOLUTION_TRANSITIONS`.
- Card action row, driven by the already-filtered form choices (no new
  gating logic): "Keep as is" / "Escalate" submit the existing resolution
  form; add an "AI variants" link that activates the existing machinery
  tab (`translate.html:344-352`, plain `data-bs-toggle` targeting
  `#machinery` - no new JS); present on reject, flag, and minor cards.

### Step 3: Verify GREEN

Same suites as Task 1.

## Task 4: Auto-advance the queue after a resolution

The resolution form posts `next={{ this_unit_url }}`
(`judge-verdict.html:159`), so recording a decision reloads the same unit
even though it just left the `check:judge-*` filter.

**Files:**

- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing test

A successful `resolve-judge-verdict` POST from the unit page redirects to
`next_unit_url` (offset + 1 of the same search), not back to the resolved
unit. A failed POST (blank reason) still returns to the same unit.

### Step 2: Implement

Change the hidden `next` value to `{{ next_unit_url }}` (already in the
include context, `translate.html:272`); update the snippet's requires
comment (line 1). `resolve_judge_verdict` already redirects through
`redirect_next` - no view change.

### Step 3: Verify GREEN

`uv run pytest weblate/trans/tests/test_judge_views.py`.

## Task 5: Readiness counters and a conservative ship CTA

The strip (`weblate/templates/snippets/judge-readiness.html`,
`judge_queue_strip_context` at `weblate/trans/views/basic.py:701-750`)
shows needs-human/not-reviewed/unparsed but names no release decision and
links to no queue.

**Files:**

- Modify: `weblate/trans/views/basic.py`
- Modify: `weblate/templates/snippets/judge-readiness.html`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing tests

- Counts include `blocked` (sum of `stats.judge_reject`), `stale`
  (`judge_stale`), `questionable` (`judge_flag`); each nonzero counter
  links to the matching prefilled search (`q=check:judge-reject`,
  `q=has:judge AND NOT has:current-judge`-equivalent used by the stats
  filter, `q=check:judge-flag`).
- The "Ready to hand off" CTA renders only when
  `judge_reject == 0 and judge_stale == 0 and judge_unparsed == 0` and
  `judge_total > 0`; majors and minors never block it (product
  semantics: `docs/admin/checks.rst` - a major ships with evidence).
- Any nonzero blocker keeps the CTA absent and shows the counters
  instead.

### Step 2: Implement

Extend `judge_queue_strip_context` counts and URLs from the existing
per-translation stats (invalidation already happens on resolution,
`models/judge.py:1157`); render the CTA as links to the existing
download menu and repository tab of the component - no new
release pipeline. Non-color severity distinction per `ACCESSIBILITY.md`.

### Step 3: Verify GREEN

`uv run pytest weblate/trans/tests/test_judge_views.py`.

## Task 6: Documentation and changelog

**Files:**

- Modify: `docs/admin/checks.rst` (resolution semantics: a fresh major can
  now be accepted as-is directly; context drift blocks resolutions)
- Modify: `docs/changes.rst` (one entry in the unreleased section)
- Modify: `docs/guides/producer-guide-weblate.md` (triage walkthrough:
  queue -> card -> keep/escalate/AI variants -> auto-advance -> CTA;
  this file only, never `producer-guide.md`)

## Task 7: Verification

1. `uv run pytest weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge.py weblate/checks/tests/test_judge.py`
   plus the judge regression set
   (`weblate/trans/tests/test_judge_loop.py`, `test_judge_round.py`,
   `test_judge_autotranslate.py`, `test_judge_form.py`).
2. `uv run prek run --files <touched files>`.
3. Browser smoke on the dev instance (port 3001), one pass per queue
   (`q=check:judge-reject`, `check:judge-flag`, `check:judge-note`):
   - drifted verdict: no resolution form, single "Re-judge" link;
   - fresh critical: compact card, "Keep as is"/"Escalate"/"AI variants",
     resolution auto-advances to the next unit;
   - fresh major: direct "Keep as is" available;
   - minor: compact evidence, no resolution form;
   - component page: counters link to the queues; CTA appears only when
     blocked/stale/unparsed are all zero.

Deployment to production is not part of this plan and needs explicit
approval.
