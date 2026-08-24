# Plan 03: Judge Decisions and Whitebox

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task. Use `test-driven-development`, `frontend-design`,
> `lightpanda-browser`, `requesting-code-review`, and
> `verification-before-completion` where specified below.

**Date:** 2026-08-24. **Status:** proposed, awaiting approval.

**Series:** third slice of the judge UI series defined in
`docs/llm-first/designs/2026-08-13-judge-native-ui-design.md` (row «3. Решения
и белый ящик»), after
`docs/llm-first/plans/2026-08-13-01-judge-verdict-core.md` (implemented) and
`docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md` (approved,
implementation pending).

**Dependencies:** Tasks 1-5 depend only on the implemented verdict core.
Task 6 extends the `judge_field` search parser that Plan 02 Task 3 creates;
Task 7 amends the readiness table and stats that Plan 02 Tasks 4 and 8 create.
Tasks 6-7 are blocked until those Plan 02 tasks land and must extend, not
duplicate, their code.

**Goal:** Let a producer record an auditable decision - accept as is with a
mandatory reason, send back to work, or escalate - on every string the LLM
judge held, so the held queue drains by explicit human action and the full
judging history is inspectable on the unit page.

**Architecture:** `JudgeVerdict` already carries dormant resolution fields
(`resolution`, `resolution_reason`, `resolved_by`, `resolved_at`; migration
`0101_judge_verdict.py`) - this plan adds the single write path, never a new
model. A decision is written to every verdict row of the active parsed round
for the exact current target, so target-hash binding makes decisions
stale-safe for free: a target edit changes the hash, the decided round stops
being active, and the unit re-enters the normal judge flow. Checks, search,
stats, and UI all read the same round-plus-resolution predicate.

**Tech stack:** Django models/views/templates, Bootstrap 3 + jQuery frontend
patterns already used by `judge-verdict.html`, pytest via `./rundev.sh test`.

---

## Contract

### Decision semantics

Decisions exist only on a **held** unit: the active parsed round for the
current target projects `flag` or `reject`
(`active_verdict()`, `weblate/trans/models/judge.py`) and no resolution is
recorded on that round. Exactly three decisions, from
`JudgeVerdict.Resolution`:

1. **Accept as is** (`accepted_as_is`) - mandatory non-empty reason. The
   target text is not modified. Unit state becomes `STATE_APPROVED` (30) when
   the translation's review workflow is enabled, otherwise
   `STATE_TRANSLATED` (20). The `judge-flag`/`judge-reject` check projection
   clears. This is the producer overriding the judge; it is never rendered as
   the judge approving.
2. **Send back** (`sent_back`) - optional reason. Unit state becomes
   `STATE_FUZZY` (10) so the string returns to the translator/MT queue. The
   verdict and its checks remain visible evidence. No repair-attempt counter
   is reset in storage: the next stored target has a new `target_hash`, which
   makes the next judge run a fresh round by construction.
3. **Escalate** (`escalated`) - optional reason. No state change. The unit
   leaves the held queue and appears under `judge:escalated` for an external
   linguist to find.

Invariants:

- A resolution row set is written atomically to **all** verdict rows of the
  deciding round (same `unit`, `run_id`, `attempt`, both seats, including an
  unparsed seat row inside a parsed round), in one transaction with the state
  change.
- Writing a decision requires the active round to still be negative and
  unresolved for the **current** target under `select_for_update`; a
  concurrent target edit or duplicate decision aborts with a translated,
  non-destructive error.
- A decision never edits `Unit.target`. The write path must prove the stored
  target is byte-identical before and after accepting (autofixes must not
  re-run; see the state-change mechanism in Task 1).
- Verdict evidence rows stay immutable except the four resolution fields;
  a recorded resolution is never overwritten or deleted by this plan's code.
- Every decision writes one `Change` row with a dedicated action and
  non-localized `details` (`resolution`, `reason`, `run_id`, `target_hash`),
  per the audit-log convention in `AGENTS.md`.

### Dismiss is disabled for judge checks

Per the design doc (lines 372-374): a mandatory-reason decision and the
check `Dismiss` mechanism are two competing overrides; two overrides are an
audit hole. `ignore_check` (`weblate/trans/views/js.py:89`) must refuse
`judge-flag`/`judge-reject`, and no template may render a dismiss control for
them.

### Permissions

All three decisions require `unit.review` on the target translation - the
same gate Plan 02 uses for running the judge. No new permission is added.
Per the design doc (lines 707-710) this is an access gate only: neither
`unit.review` nor any decision is evidence that a string passed LLM checking.

### Search filters (Task 6, after Plan 02)

- `judge:exhausted` - held awaiting decision: active parsed round for the
  current target is `flag` or `reject` and carries no resolution.
- `judge:override` - active round resolved `accepted_as_is`.
- `judge:escalated` - active round resolved `escalated`.

All are `Exists` subqueries of the same shape as `check_field`
(`weblate/utils/search.py:884-899`), registered inside the `judge_field`
parser Plan 02 creates. `sent_back` needs no filter: the unit is back in the
ordinary `state:<translated` workflow.

### Whitebox tab and card

- The verdict card (`weblate/templates/snippets/judge-verdict.html`) gains
  the three decision buttons, rendered only on a held unit for a user holding
  `unit.review`, plus a compact record of an existing resolution (who, when,
  reason).
- A new **AI evaluation** tab on the unit page shows the full judging
  history: every round and attempt, per-seat model, timestamp, parsed or
  unparsed, per-seat errors, back-translation, repair applications, and any
  resolution. Read-only; evidence, not controls.
- Copy never includes **Approved by AI**, **AI approved**, or **Ready for
  release**. The accept confirmation renders as a human decision:
  **Accepted by <user> against the judge verdict**.

### Spans are a degradable function

Per the design doc (lines 253-268): `description` is the mandatory evidence;
`span_start`/`span_end` in `JudgeVerdict.errors` entries are optional. The
whitebox tab highlights spans through a fourth `Formatter` parser
(`weblate/trans/templatetags/translations.py:288-297`) only when offsets are
valid for the rendered target; an invalid span renders the error entry
without highlight and never discards the verdict.

### Badge separation

Judge check badges get a dedicated outline class so opinion and fact are not
the same red dot: **контур = мнение, заливка = факт**. Deterministic check
badges keep their filled style (`weblate/trans/templatetags/translations.py:933-936`).

### Out of scope

- Any change to `JUDGE_MAY_APPROVE` or automatic approval.
- Paid judge or MT calls in any test or verification step.
- REST API exposure of resolutions.
- Zen editor decision buttons (translate page only).
- An external-linguist workflow beyond the `judge:escalated` filter.
- Span-accuracy investment or prompt changes.
- Project/category readiness dashboards; readiness polling.
- Production enablement or deploy; shared dev-stack rebuild.

---

## Task 1: Resolution write path and Change actions

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/actions.py`
- Create: `weblate/trans/tests/test_judge_resolution.py`

### Step 1: Write failing model tests

`record_judge_resolution(unit, user, resolution, reason="")` in
`weblate/trans/models/judge.py`, raising `JudgeResolutionError` (new,
subclass of `Exception`, translated message) on every invalid input. Pin:

- accept with empty/whitespace reason raises; send back and escalate accept
  an empty reason;
- resolving a unit whose active verdict is `pass`, stale, unparsed-only, or
  absent raises;
- resolving an already-resolved round raises (no overwrite);
- a successful decision writes `resolution`, `resolution_reason`,
  `resolved_by`, `resolved_at` on **every** row of the active round (both
  seats, same `run_id`/`attempt`) and on no other row;
- accept: state 30 when review workflow enabled, 20 otherwise; stored
  `Unit.target` byte-identical before/after; `target_hash` of the decided
  round still matches the current target after the write;
- send back: state 10; escalate: state unchanged;
- one `Change` row per decision with the matching action and
  `details == {"resolution": ..., "reason": ..., "run_id": str(...),
  "target_hash": ...}`;
- concurrent-edit guard: changing the target in another session before the
  write raises and leaves all rows untouched.

State changes must not go through the autofix pipeline: apply state via the
same direct mechanism the judge loop uses for verdict states
(`state_for_verdict` application in `weblate/trans/autotranslate.py`), not
via a re-translate of the target. The byte-identical assertion above is the
regression net for this decision (fork history: `translate()` re-runs
autofixes and has silently mutated stored targets before).

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_resolution.py
```

Expected: import error on `record_judge_resolution`.

### Step 3: Implement

Add to `weblate/trans/actions.py` three `ActionEvents` entries using the next
free integers, following the `LABEL_ADD`/`LABEL_REMOVE` (86/87) tuple shape:
`JUDGE_ACCEPTED`, `JUDGE_SENT_BACK`, `JUDGE_ESCALATED`. Audit strings are not
localized. Implement `record_judge_resolution` with
`transaction.atomic()` + `select_for_update()` on the unit, re-reading the
active round inside the lock.

### Step 4: Prove GREEN

```bash
./rundev.sh test weblate/trans/tests/test_judge_resolution.py weblate/trans/tests/test_judge.py
```

The existing `test_judge.py` suite must stay green untouched.

### Step 5: Commit

```bash
git add weblate/trans/models/judge.py weblate/trans/actions.py weblate/trans/tests/test_judge_resolution.py
git commit -m "feat(judge): add resolution write path with audit trail"
```

---

## Task 2: Check projection honors decisions; dismiss disabled

**Files:**

- Modify: `weblate/checks/judge.py`
- Modify: `weblate/trans/views/js.py`
- Modify: `weblate/trans/tests/test_judge_resolution.py`

### Step 1: Write failing tests

- After `accepted_as_is`, `BaseJudgeCheck.check_target_unit` returns False
  for both judge checks and `run_checks()` removes the existing
  `judge-flag`/`judge-reject` `Check` rows.
- After `sent_back` and `escalated`, the check projection is unchanged (the
  evidence stays visible).
- POST to `ignore_check` for a `judge-flag` or `judge-reject` `Check` returns
  an error status and does not set `dismissed`, even for a user holding
  `unit.check`; a deterministic check on the same unit still dismisses
  normally.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_resolution.py -k "check or dismiss"
```

### Step 3: Implement

`BaseJudgeCheck.check_target_unit` consults the active round's resolution
(one query via the existing round readers; no new caching layer).
`ignore_check` refuses names in `JUDGE_CHECKS` (`weblate/checks/judge.py`)
before calling `set_dismiss`. Call `run_checks()` from
`record_judge_resolution` after an accept so the projection updates in the
same request.

### Step 4: Prove GREEN, then commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_resolution.py weblate/checks/tests/test_judge_checks.py
git add weblate/checks/judge.py weblate/trans/views/js.py weblate/trans/tests/test_judge_resolution.py
git commit -m "feat(judge): clear check projection on accept, forbid dismiss"
```

(If judge check tests live elsewhere, run the actual module that covers
`weblate/checks/judge.py`; do not create a duplicate suite.)

---

## Task 3: Decision UI on the verdict card

**Files:**

- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/forms.py`
- Modify: `weblate/urls.py`
- Create: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing view tests

New POST view `resolve_judge(request, unit_id)` (in
`weblate/trans/views/edit.py`, near `handle_suggestions` at line 1055, which
is the per-unit action precedent). Pin:

- GET is refused (`@require_POST`);
- anonymous and a user without `unit.review` on the translation get 403/
  redirect and no rows change;
- a reviewer accepting with a reason redirects back to the unit URL, writes
  the resolution, and flashes a success message;
- accept without a reason re-renders with a form error and writes nothing;
- decisions on a non-held unit return the translated error from Task 1 as a
  message, not a traceback.

Form `JudgeResolutionForm` (`weblate/trans/forms.py`): `resolution` choice +
`reason` textarea; `clean()` enforces the mandatory reason for
`accepted_as_is`. The template supplies its own `<form>` inside the modal, so
the form class must set `self.helper = FormHelper(self)` and
`self.helper.form_tag = False` (repo convention; nested-form trap).

### Step 2: Write the failing nesting regression test

In `test_judge_views.py`: render the translate page for a held unit as a
reviewer, walk `<form`/`</form` tag **depth** over the whole response body,
and assert depth never exceeds 1. A source-balance check is known to pass
against the nested-form bug; only depth counting catches it.

### Step 3: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py
```

### Step 4: Implement

- Card buttons: **Accept as is**, **Send back**, **Escalate**, rendered only
  when the unit is held and `request.user` has `unit.review`; accept opens a
  Bootstrap modal (existing modal patterns in `translate.html`) with the
  required reason textarea; send back and escalate submit directly with an
  optional reason field.
- An existing resolution renders as a single line on the card: decision,
  user, timestamp, reason; the accept line reads
  **Accepted by {user} against the judge verdict**.
- All labels through `{% translate %}`; buttons are real `<button>` elements
  inside the modal form; keyboard focus moves into the modal on open
  (follow `ACCESSIBILITY.md`).
- `_judge_view_context()` (`weblate/trans/views/edit.py:1285`) gains
  `judge_resolution` and `judge_can_resolve`.

### Step 5: Prove GREEN, then verify in the browser

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py
```

Use `lightpanda-browser` against the dev stack (`http://localhost:3001`,
admin/admin): open a held unit, **click** the real Accept button
(`tab.click`, never `form.submit()` - the click path is the DOM relationship
the nesting bug breaks), type a reason, submit, observe the success message,
the cleared check badge, and the resolution line. Repeat once for Send back.
No paid call occurs: the fixture verdicts are written by tests/dev data, and
deciding never calls a provider.

### Step 6: Revert-and-prove, then commit

Temporarily revert the `form_tag = False` line, run the depth test, and
confirm it FAILS; restore. (Two of three regression tests in this feature
family previously passed against the live bug.)

```bash
git add weblate/templates/snippets/judge-verdict.html weblate/trans/views/edit.py weblate/trans/forms.py weblate/urls.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): producer decision buttons on the verdict card"
```

---

## Task 4: AI evaluation whitebox tab

**Files:**

- Modify: `weblate/templates/translate.html`
- Create: `weblate/templates/snippets/judge-history.html`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing tests

- The tab appears only when the unit has at least one verdict row
  (`JudgeVerdict.objects.filter(unit=...).exists()`); otherwise the tab and
  its pane are absent.
- The pane lists every round grouped by `(run_id, attempt)` in reverse
  chronological order; each seat row shows model, timestamp,
  parsed/unparsed, severity, and errors; a resolution renders inside its
  round; a stale round is labelled as relating to a previous target.
- Rendering a unit with 3 rounds x 2 seats issues a bounded number of
  queries (one verdict queryset with `select_related("resolved_by")`;
  assert with `assertNumQueries` around the context helper).
- The forbidden copy strings (**Approved by AI**, **AI approved**,
  **Ready for release**) do not appear in the rendered page.

### Step 2: Prove RED, implement, prove GREEN

Tab follows the existing pane pattern (`translate.html`: suggestions at
line 382, history at 473, comments at 555). Context comes from one new
helper next to `_judge_view_context()`, fetching all rows for the unit in a
single query. Read-only: no controls in the pane.

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -k history
```

### Step 3: Commit

```bash
git add weblate/templates/translate.html weblate/templates/snippets/judge-history.html weblate/trans/views/edit.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): whitebox AI evaluation tab with judging history"
```

---

## Task 5: Span highlighting and badge separation

**Files:**

- Modify: `weblate/trans/templatetags/translations.py`
- Modify: `weblate/static/style-bootstrap.css` (or the stylesheet the
  existing check badge classes live in - locate, do not create a new file)
- Modify: `weblate/templates/snippets/judge-history.html`
- Create: `weblate/trans/tests/test_judge_spans.py`

### Step 1: Write failing span tests

A helper that, given a target string and an `errors` list, returns highlight
ranges; the `Formatter` (`translations.py:288-297`, where placeables,
glossary, and search parsers live) consumes it as a fourth parser of the
same shape. Pin:

- a valid `span_start`/`span_end` pair inside the string produces one range;
- missing spans, `span_start >= span_end`, negative offsets, offsets past the
  string end, and non-integer values produce **no** range and no exception;
- overlapping valid spans render without breaking HTML (ranges merged or
  nested consistently with how search highlights behave);
- the error entry itself renders its `description` regardless of span
  validity.

### Step 2: Prove RED, implement, prove GREEN

```bash
./rundev.sh test weblate/trans/tests/test_judge_spans.py
```

Badge: judge check badges get a dedicated outline class (opinion) while
deterministic badges keep the filled style (fact), keyed off
`JUDGE_CHECKS` membership where `unit_state_class`/check badges are built
(`translations.py:933-936`). Add a template/CSS assertion to
`test_judge_views.py`: a held unit's judge badge carries the outline class,
a deterministic failing check on the same page does not.

### Step 3: Commit

```bash
git add weblate/trans/templatetags/translations.py weblate/templates/snippets/judge-history.html weblate/trans/tests/test_judge_spans.py weblate/static/style-bootstrap.css
git commit -m "feat(judge): degradable span highlighting and opinion badges"
```

---

## Task 6 (blocked by Plan 02 Task 3): decision search filters

**Files:**

- Modify: `weblate/utils/search.py` (inside the `judge_field` parser Plan 02
  creates)
- Modify: `weblate/utils/tests/test_search.py`

### Step 1: Write failing search tests

Fixtures: one held-unresolved unit, one accepted, one escalated, one
sent-back, one pass, one stale. Pin:

- `judge:exhausted` matches only the held-unresolved unit;
- `judge:override` matches only the accepted unit;
- `judge:escalated` matches only the escalated unit;
- a target edit after a decision removes the unit from `judge:override`
  (hash binding);
- each filter composes with language/translation scoping used by Zen links.

### Step 2: Prove RED, implement as `Exists` subqueries, prove GREEN

```bash
./rundev.sh test weblate/utils/tests/test_search.py -k judge
```

### Step 3: Commit

```bash
git add weblate/utils/search.py weblate/utils/tests/test_search.py
git commit -m "feat(judge): exhausted/override/escalated search filters"
```

---

## Task 7 (blocked by Plan 02 Tasks 4 and 8): readiness drains by decision

**Files:**

- Modify: `weblate/utils/stats.py`
- Modify: `weblate/utils/tests/test_stats.py`
- Modify: the readiness table template and its tests from Plan 02 Task 8

### Step 1: Write failing tests

- New lazy key `judge_exhausted` alongside Plan 02's `JUDGE_KEYS`, equal to
  the `judge:exhausted` filter count; recording any resolution invalidates
  the translation stats (hook inside `record_judge_resolution`, mirroring
  the verdict-write invalidation Plan 02 Task 1 adds).
- Readiness **Held for decision** count uses `judge_exhausted`; its Review N
  action links `q=judge:exhausted`; an accepted or escalated unit leaves the
  count without any judge re-run.
- The interim caption from Plan 02 («a decision is recorded only by editing
  the translation or re-evaluating») is removed; the assertions in Plan 02
  Task 8 Step 1 that pin it are updated in the same commit.

### Step 2: Prove RED, implement, prove GREEN

```bash
./rundev.sh test weblate/utils/tests/test_stats.py -k judge
```

Then re-run the Plan 02 readiness template test module touched above.

### Step 3: Commit

```bash
git add weblate/utils/stats.py weblate/utils/tests/test_stats.py <readiness template and test files>
git commit -m "feat(judge): held queue drains by producer decision"
```

Also amend `docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`
contract point 8 in this commit: the interim caption clause gets a one-line
«superseded by Plan 03 Task 7» note instead of silent divergence.

---

## Task 8: Docs, changelog, review, push

**Files:**

- Modify: `docs/user/translating.rst`
- Modify: `docs/changes.rst` (top unreleased section)
- Modify: `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md`
  (row 3 of the slicing table points to this file's actual path)

### Step 1: Write docs

Extend the judge workflow section Plan 02 Task 9 creates (or the existing
judge documentation if Plan 02's section is not yet merged) with the three
decisions, the mandatory reason, the audit trail, and the whitebox tab. Use
`:guilabel:` for button names; sentence-case headings; scoped, additive
edits. One concise changelog entry linking to the documentation.

### Step 2: Run affected hooks and focused suites

```bash
uv run prek run ruff-check ruff-format --files <changed .py files>
./rundev.sh test weblate/trans/tests/test_judge_resolution.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_spans.py weblate/trans/tests/test_judge.py
```

### Step 3: Request review

Use `requesting-code-review` scoped to this plan's diff. Review gates:

- no resolution overwrite path; verdict immutability outside the four fields;
- no `translate()`-pipeline target mutation on accept;
- no dismiss path for judge checks survives;
- no nested forms; keyboard path through the modal;
- no forbidden approval copy;
- no paid provider call anywhere in tests or fixtures;
- no Plan 02 duplication in Tasks 6-7.

Fix only in-scope findings and rerun affected evidence.

### Step 4: Commit and push

```bash
git add docs/user/translating.rst docs/changes.rst docs/llm-first/designs/2026-08-13-judge-native-ui-design.md
git commit -m "docs(judge): document producer decisions and whitebox tab"
git push
```

Final evidence must show: focused tests green, affected hooks green, browser
decision flow observed via real clicks, revert-and-prove executed for the
nesting test, branch pushed, no production change, no paid provider call.
