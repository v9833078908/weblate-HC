# Producer triage for judge verdicts, Solution 1 implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Make the embedded judge card a usable producer triage surface for
every verdict severity (critical/`judge-reject`, major/`judge-flag`,
minor/`judge-note`): pin the existing context-freshness gate with tests and
give drifted cards an action, compress the two-seat evidence, name the
actions by outcome, auto-advance the queue, and give the component page a
conservative release CTA. No new pages, no new models, no migrations.

**Architecture:** Templates, `_judge_view_context`, the resolution
transition table, and the readiness strip only. The verdict stays
immutable; the existing readers already guarantee a resolution is recorded
only against a verdict whose target *and* context still match the unit -
this plan adds no second hash computation anywhere. The release CTA reads
existing per-translation stats
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

## Task 1: Pin the freshness invariant; give drifted cards an action

No gate is missing - verified against the code: `current_round` filters
rows by the unit's current target *and* context hashes
(`_current_snapshot_hashes`, `weblate/trans/models/judge.py:759-769`),
`_judge_view_context` builds resolution choices only from
`judge_current_verdict = current_verdict(unit)`
(`weblate/trans/views/edit.py:1324-1331`), and `resolve_verdict` re-reads
`current_verdict` under the Unit lock and raises `stale` when drift made it
None (`weblate/trans/models/judge.py:1072-1093`). When
`judge_context_changed` is true, the displayed verdict is the target-only
fallback (`active_verdict`) and the current verdict is by construction
None, so the form and the drift note are already mutually exclusive.

What is missing is coverage pinning that invariant and any producer action
on a drifted/stale card: today the card shows only an explanatory footnote
with nothing to click.

**Files:**

- Modify: `weblate/trans/tests/test_judge_views.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`

### Step 1: Write tests pinning the existing invariant

- View: after a glossary/note change that flips `judge_context_changed`,
  the unit page renders the drift note and no resolution form
  (`judge_can_resolve` false), for critical and major verdicts alike.
- Model: `resolve_verdict` with the drifted verdict's pk raises
  `JudgeResolutionError("stale", ...)`; the resolution row stays
  untouched. These tests document behavior that already holds - they
  must pass without production changes and guard against regression.

### Step 2: Implement the drift-card action

Template only: on `judge_context_changed` (and on the `judge_stale`
branch, `judge-verdict.html:23-32`), next to the existing note render one
action - a "Re-judge this string" link to the automatic translation page
prefilled with `mode=judge&q=id:{unit.pk}` (the
`judge_queue_strip_context` run-URL pattern,
`weblate/trans/views/basic.py:750`). This is the Solution 1 stand-in for
the per-unit re-check endpoint. Add a rendering test for both branches.

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
even though it just left the `check:judge-*` filter. A plain template
switch to `next_unit_url` is wrong: every branch of
`resolve_judge_verdict` - invalid form, `JudgeResolutionError`, success -
redirects through the same `request.POST.get("next")`
(`weblate/trans/views/edit.py:1846-1864`), so failures would advance too
and the error message would land on the wrong unit.

**Files:**

- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing test

- A successful `resolve-judge-verdict` POST from the unit page redirects
  to `next_unit_url` (offset + 1 of the same search), not back to the
  resolved unit.
- A failed POST (blank reason) and a `JudgeResolutionError` (stale pk)
  both return to the same unit, message attached.

### Step 2: Implement

- Template: keep `next` as `{{ this_unit_url }}` (the error return) and
  add a second hidden field `success_next` set to `{{ next_unit_url }}`
  (already in the include context, `translate.html:272`); update the
  snippet's requires comment (line 1).
- View: only the success branch (`edit.py:1863-1864`) prefers
  `request.POST.get("success_next")`, falling back to `next`; both error
  branches keep using `next`. `redirect_next` already sanitizes the URL.

### Step 3: Verify GREEN

`uv run pytest weblate/trans/tests/test_judge_views.py`.

## Task 5: Readiness counters and a conservative ship CTA

The strip (`weblate/templates/snippets/judge-readiness.html`,
`judge_queue_strip_context` at `weblate/trans/views/basic.py:701-756`)
shows needs-human/not-reviewed/unparsed but names no release decision and
links no counter to a queue. Constraint, documented in the docstring
(`basic.py:716-723`): no existing view lists units across every language
of a component filtered by an arbitrary query, so each counter must link
to a destination that actually exists. Two do:

- `check:judge-reject` / `check:judge-flag` are projected Check rows, and
  the per-check `CheckList` accepts a Component path
  (`reverse("checks", kwargs={"name": "judge-reject", "path": ...})`,
  `weblate/urls.py:819-823`); its per-language rows redirect straight into
  the translate queue (`weblate/checks/views.py:290-294`).
- `judge:stale` / `judge:unparsed` are not checks and have no
  component-wide listing. Their destinations are per-language translate
  URLs `translation.get_translate_url()?q=judge:stale` (the `judge:`
  search field exists, `weblate/utils/search.py:816-828`), rendered as a
  per-language sub-list in the strip - the strip already iterates the
  prefetched `translations` on component pages, so nonzero per-language
  counts are free.

No new listing view is added.

**Files:**

- Modify: `weblate/trans/views/basic.py`
- Modify: `weblate/templates/snippets/judge-readiness.html`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing tests

- Counts include `blocked` (sum of `stats.judge_reject`), `stale`
  (`judge_stale`), `questionable` (`judge_flag`).
- Destinations: `blocked`/`questionable` link to the component-scoped
  per-check `CheckList` for `judge-reject`/`judge-flag`; `stale` and
  `unparsed` render one `translate?q=judge:stale` / `?q=judge:unparsed`
  link per non-source translation with a nonzero count, and none for a
  zero count.
- The "Ready to hand off" CTA renders only when
  `judge_reject == 0 and judge_stale == 0 and judge_unparsed == 0` and
  `judge_total > 0`; majors and minors never block it (product
  semantics: `docs/admin/checks.rst` - a major ships with evidence).
- Any nonzero blocker keeps the CTA absent and shows the counters
  instead.

### Step 2: Implement

Extend `judge_queue_strip_context` counts and the URLs above from the
existing per-translation stats (invalidation already happens on
resolution, `models/judge.py:1157`); render the CTA as links to the
existing download menu and repository tab of the component - no new
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
