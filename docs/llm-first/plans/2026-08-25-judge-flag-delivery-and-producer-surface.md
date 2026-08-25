# Let an advisory verdict ship, and make the producer's run legible

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task. Use `test-driven-development` and
> `verification-before-completion` where specified below.

**Date:** 2026-08-25. **Status:** awaiting approval.

**Decided** by the product owner on 2026-08-25, and implemented by this
plan: an unresolved `major` ships (Task 1), and a `minor` finding becomes
visible as a third judge check rather than staying inside the pass counter
(Task 8).

**Also decided on 2026-08-25**, in an office-hours session that measured the
rendered page instead of arguing about it: Task 4 replaces the readiness card
with one queue strip above the language table, adds no judge column to the
language table, and closes the `Delivery` open question by dropping the
column. The measurements that decided it are stated in Task 4.

**Goal:** A false judge flag costs the producer attention, not a blocked
delivery. Every producer-facing surface says the same thing about the same
verdict. A producer who starts one run can read, afterwards, what was
translated, what was judged, what the judge changed and in which text, what
it merely noted, and what is left for a human.

**Architecture:** `state_for_verdict` is the single place where a verdict
becomes a shipping decision, so the change is one mapping plus the four
surfaces that describe it. The release gate stays exactly where it is
certified: only `critical` holds a string, through `STATE_FUZZY` and the
existing `WITHOUT_NEEDS_EDITING` commit policy. Producer legibility is built
from evidence already persisted - `JudgeVerdict` rows per
`(unit, run_id, attempt, seat)` and the judge statistics added by Plan 02 -
not from a new live channel.

**Tech Stack:** Django, PostgreSQL, Celery, Django templates, Bootstrap,
pytest, Ruff.

---

## Why the mapping changes back

`docs/llm-first/plans/2026-08-22-03-judge-review-gate.md` Task 3 mapped
`FLAG` to `STATE_NEEDS_CHECKING`. The consequence was not re-derived at the
time:

1. `models/judge.py:240` returns `STATE_NEEDS_CHECKING` (12) for `FLAG`.
2. `utils/state.py:41` puts 12 in `FUZZY_STATES`.
3. `models/unit.py:2883-2890` blocks **any** fuzzy unit under
   `WITHOUT_NEEDS_EDITING`.
4. That policy is set on eight production projects
   (`docs/llm-first/vision/llm-first-product-architecture.md:596-601`).

So an unresolved `major` stopped shipping. The union collegium was chosen
against the opposite premise, stated in the sealed-test go/no-go:
"ложный флаг стоит внимания продюсера, потому что по дизайну `flag` даёт
state 20 и строка всё равно отгружается"
(`docs/llm-first/measurements/2026-08-13-phase0-measurements.md:438-441`,
index `judge-measurements-index.md:565`).

With the premise gone, the measured false-flag rate became a delivery
defect rather than a queue cost:

| Reading | Number | Source |
| --- | --- | --- |
| union false flags on clean strings | 11.4% of 167 | `phase0-measurements.md:365` |
| union threshold status | **not passed** (≤10%) | `phase0-measurements.md:429` |
| flag volume vs reject volume | 146 major vs 120 critical | `phase0-measurements.md:358` |
| strings leaving auto-pass | 27.1% of the prod pool | `phase0-measurements.md:407` |

Restoring `FLAG -> STATE_TRANSLATED` also restores agreement with
`docs/llm-first/designs/2026-08-13-judge-native-ui-design.md:659-661`, which
had already decided this and gave the same reason.

**What the decision costs, measured and accepted:** 7 of 146 `major` defects
reach a build (`phase0-measurements.md:365`) and the miss rate among
auto-passed strings is 0.65% (`:407`). The release gate is untouched:
reject-recall on `critical` 98.3% [94.1, 99.7], 0 of 120 criticals in
auto-pass, 0 of 167 false rejects.

### What is deliberately NOT in scope

- **Changing the aggregation rule.** Requiring both seats to agree on
  `major` would cut false flags, but the intersection figures are a
  reconstruction with a batch-composition confound
  (`phase0-measurements.md:326-331`); the run that would settle it is not
  bought. Keep the union; revisit only with a measurement.
- **A live phase indicator during a run.** `get_task_progress`
  (`weblate/utils/celery.py:237-248`) carries a number and nothing else, and
  Plan 02 contract item 11 forbids a second live status channel. Phase
  wording therefore lands in the pre-run estimate and the completion summary,
  where it is free.
- **Recording a reverted repair.** `_RepairOutcome.changed`
  (`judge_loop.py:225-277`) exists only during the run; a rolled-back repair
  survives solely as two writes in unit history. Surfacing it needs new
  persisted state and belongs to its own plan.
- **Judge columns in the language table.** Measured on this branch's dev
  stack: at a 1440 px viewport the table already needs 1590 px inside a
  1410 px wrapper, and `Suggestions` and `Comments` are already off-screen.
  The `zero-width-NNN` classes are `@media (max-width: N)` rules
  (`static/styles/main.css:1851-1881`), so they free nothing at desktop
  width. `list-objects.html` is also shared by the project, category,
  dashboard and language pages. No judge column, on any of them.
- **A delivery or pending column.** Dropped, not deferred. It rendered
  "0 ready to deliver" on every row, it states a VCS commit fact rather than
  a judge fact, and Weblate already owns that surface in the pending-changes
  alert and in repository maintenance. The separate-axes sentence survives in
  `docs/admin/checks.rst` (Task 6).
- **A new search filter for coverage.** Not needed. `NOT has:judge` at
  component scope counts the source language too (20 against 10 on the
  fixture), and rendering `AND NOT language:<source code> AND NOT
  state:read-only` into the link is exact. Both forms were run against
  `/search/need-for-greed/buyers/`.
- **An audit trail for a dismissed judge check.** `Check.dismissed` carries
  no author, time or reason, so "the number went down" cannot be attributed
  to a person or to a reason, and the strip inherits that. This is the
  week-two risk of routing producer decisions through native dismissal; it
  needs its own plan beside Plan 03, and this plan does not pretend to solve
  it.
- **Anything touching production.** The rollout section needs its own
  approval.

---

## Task 1: An unresolved advisory verdict ships

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/tests/test_judge.py`,
  `weblate/trans/tests/test_judge_autotranslate.py`

### Step 1: Rewrite the failing assertions first

`test_judge.py:66-80` currently asserts `STATE_NEEDS_CHECKING` and
membership in `FUZZY_STATES` for `FLAG`. Replace with `STATE_TRANSLATED`,
and add one assertion that `FLAG` is **not** in `FUZZY_STATES`, so the
delivery property is tested rather than implied. Keep the `REJECT` case
asserting `STATE_FUZZY` unchanged.

`test_judge_autotranslate.py:71-77` asserts `FUZZY_STATES` for the
unresolved-major path. Replace the major case with `STATE_TRANSLATED` and
add an assertion that the unit is not blocked:
`self.assertFalse(unit.is_blocked_by_commit_policy)` with the project's
`commit_policy` set to `WITHOUT_NEEDS_EDITING`. That is the property the
producer cares about, and it is the one that silently flipped.

Prove RED:

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py -p no:randomly --no-cov -q
```

### Step 2: Change the mapping

In `weblate/trans/models/judge.py`, `state_for_verdict` returns
`STATE_TRANSLATED` for `FLAG`. Remove the now-unused
`STATE_NEEDS_CHECKING` import. Rewrite the docstring (`:227-234`) to state
the cost model explicitly: `critical` holds a string because a broken
string in a build is expensive; `major` ships with a `judge-flag` check
because the measured false-flag rate of the union collegium is 11.4% and a
false hold is more expensive than a false alarm. A future reader must not
have to re-derive this from two plans and a measurement.

### Step 3: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/checks/tests/test_judge.py -p no:randomly --no-cov -q
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py
git commit -m "fix(judge): let an advisory verdict ship instead of holding it"
```

---

## Task 2: One vocabulary on every producer surface

Four surfaces describe the same three outcomes today, and they do not agree
with each other. Plan 02's approved copy list is the vocabulary; this task
makes every surface use it.

| Surface | Now | After |
| --- | --- | --- |
| `weblate/trans/filter.py:79-88` | "Advisory - ships" = `judge:pass`; "Held for decision" = `judge:flag OR judge:reject` | "Evaluated - no blocking concern" = `judge:pass`; "Advisory - ships" = `judge:flag`; "Held for decision" = `judge:reject` |
| `weblate/templates/snippets/judge-readiness.html:36-44,66-67` | Advisory column = flag, Held = reject, legend claims delivery is not held | content replaced by Task 4's strip: one union number for flag plus reject, the split kept in the native check browser, the vocabulary in `docs/admin/checks.rst` |
| `weblate/trans/autotranslate.py:415-431` | `advisory` = FLAG, `held` = REJECT | unchanged, now true |
| `weblate/templates/snippets/judge-verdict.html:62-80` | flag card "Questionable", no delivery badge | flag card gains a badge stating it ships with a concern |

**Files:**

- Modify: `weblate/trans/filter.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/templates/snippets/judge-readiness.html` (only if Task 4
  landed first; otherwise this surface arrives with Task 4)
- Test: `weblate/trans/tests/test_judge_views.py`,
  `weblate/utils/tests/test_search.py` (whichever module already covers the
  filter list; do not add a new module)

### Step 1: Failing tests

- The named filter list maps `judge-advisory` to `judge:flag` and
  `judge-held` to `judge:reject`, and every filter query still parses.
- The verdict card for a `flag` verdict renders the ships badge; the card for
  a `reject` verdict keeps "Will not ship". Assert on both, so a future edit
  cannot silently make them identical again.

### Step 2: Apply the copy

Badge wording for the flag card, matching the reject card's shape:
`{% translate "Ships with a concern" %}` in a `text-bg-warning` badge.
Sentence for "Advisory - ships": name the check, so the producer can find the
queue - "A judge found a concern that needs attention; the string still ships
and carries the `judge-flag` check." Its home is the vocabulary list in
`docs/admin/checks.rst` (Task 6), because Task 4 deletes the legend that used
to carry it.

The flag branch also gains the line the reject branch already has -
"Both judges reviewed this string independently"
(`judge-verdict.html:56-58`) - under the same `judge_seats|length > 1`
condition. A producer reading a flag must be able to see it was two models,
not one.

Nothing in this task changes behaviour; it removes four disagreements.

### Step 3: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  weblate/utils/tests/test_search.py -p no:randomly --no-cov -q
git commit -m "fix(judge): say the same thing about a verdict everywhere"
```

---

## Task 3: Show the repair in the verdict card

A producer asked for "rounds and repairs". The data exists: verdicts
accumulate per `(unit, run_id, attempt, seat)`
(`models/judge.py:88-92`), so attempt 0 is what was found and attempt 1 is
what the second judgment said about the rewritten string. The card
(`snippets/judge-verdict.html`) shows only the final verdict, so a repaired
string is indistinguishable from one that passed on the first look.

**Files:**

- Modify: `weblate/trans/models/judge.py` (a query for the previous attempt
  of the active round; keep it next to `latest_round`/`active_round`)
- Modify: `weblate/trans/views/edit.py` (context, near `:1294-1317`)
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Failing test

Build a unit with a run that has attempt 0 `major` verdicts and attempt 1
`pass` verdicts, then assert the rendered card contains: the repair notice,
the first-attempt error list, and the final verdict. Assert that a unit
judged once renders no repair notice - the addition must not appear on the
common path.

### Step 2: Render it

One block above the verdict block, only when the active round has an earlier
attempt:

- heading: "Repaired once and judged again";
- one line naming the engine that rewrote it (the project's configured
  automatic-suggestion engine, already resolved by
  `judge_workflow.repair_route`);
- the attempt-0 error list, labelled as what the repair was asked to fix;
- **the text before the repair, next to the text now**, so the producer can
  read what the judge actually changed. This is the point of the block, not
  a detail: an error list without the rewrite tells a producer that
  something was wrong, not what was done about it. Render both plural forms
  when the unit has them, and mark the previous text as the pre-repair
  version;
- and, when the final verdict is still negative, one sentence saying the
  repair did not resolve it.

The pre-repair text comes from the unit's own history: the repair writes
through `locked.translate()` (`judge_loop.py:267`), and `generate_change`
stores the previous target in `Change.old`
(`weblate/trans/models/unit.py:2078`, field declared at
`weblate/trans/models/change.py:643`). Take the newest change that
introduced the current target and read its `old`. Read it in the view,
never in the template, and fall back to omitting the comparison - not to
failing the page - when no such change exists (an old verdict, a purged
history).

### Step 3: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
git commit -m "feat(judge): show the repair attempt on the verdict card"
```

---

## Task 4: Replace the readiness card with one queue strip

**Decided by the product owner on 2026-08-25**, in an office-hours session
that measured the rendered page on this branch's dev stack
(`127.0.0.1:3002`, `/projects/need-for-greed/buyers/`). The card, its
`Delivery` column, its `Primary action` column and its seven-item legend go.
No judge column enters the language table.

Four readings decided it:

| Reading | Number | How it was taken |
| --- | --- | --- |
| the card's `Advisory` and the table's `Checks` count the same strings | `allchecks` 2, `has:check` 2, `check:judge-flag` 2, `has:check AND NOT check:judge-*` **0** | `/api/translations/need-for-greed/buyers/ru/units/?q=` per term |
| the language table has no horizontal room left | 1590 px of table inside a 1410 px wrapper at a 1440 px viewport; `Suggestions` ends at 1471 px, `Comments` at 1605 px, `documentElement.scrollWidth` 1597 | `getBoundingClientRect()` per `th`; the wrapper is `overflow-x: visible` unless `user.profile.wide_tables` |
| `zero-width-NNN` cannot pay for a column group | they are `@media (max-width: N)` rules (`static/styles/main.css:1851-1881`), so at 1440 px every column renders and the row overflows | read |
| the judge already has a native, localized, per-language surface | `/checks/-/need-for-greed/buyers/` renders `Judge: questionable 3` and `Judge: rejected 1`; `/checks/judge-flag/need-for-greed/buyers/` renders a per-language table whose numbers link to `?q=check:judge-flag`, beside a dismissed column | opened in a browser |

`judge-flag` and `judge-reject` are ordinary checks (`checks/judge.py:91-106`,
registered at `checks/defaults.py:93-94`), so a finding is already carried by
the `Checks` column, by the failing-check browser, by the unit card and by the
enforced-check machinery. A column group would restate it 480 px past the
right edge of the screen. What no native surface can state is the *absence* of
a verdict (a `pass` leaves no check row), a verdict describing older text
(`run_checks` removes the row), an attempt that returned nothing, and starting
a capped run. Those four are the whole content of the strip.

### What the strip renders

One line above the language table. Three numbers, each rendered only when it
is non-zero, each a link to the queue it was counted from:

| Label | Query behind the number | Measured |
| --- | --- | --- |
| Needs attention | `check:judge-flag OR check:judge-reject` | 4 |
| Not reviewed | `NOT has:judge AND NOT language:<source code> AND NOT state:read-only` | 10 |
| Last attempt returned nothing | `judge:unparsed` | filter parses; 0 in this component |

and two controls that always render:

- **Breakdown by check** -> `/checks/-/<project>/<component>/`. This is where
  advisory and held split apart again, per language, with links to the string
  and with dismissal. The strip merges them into one number on purpose: asked
  what he does in the first sixty seconds after a run, the producer answered
  "work through the queue of strings that need a human", which is a size, not
  a taxonomy.
- **Run the judge** -> `?mode=judge&q=NOT+has%3Ajudge#auto`, the component's
  own automatic-translation tab, which already offers `mode=judge`
  (`forms.py:1347-1350`) and already carries the estimate block Task 5
  extends. The per-language run stays where it is, on the translation page.

Vocabulary: Task 2's named filters keep their own names ("Advisory - ships",
"Held for decision"). The strip adds exactly one union label, and its `title`
names both halves, so the filter dropdown, the check browser and the strip
cannot drift apart.

### Where the numbers come from

All of them from the `prefetch_stats()` pass the language table already makes
(`views/basic.py:687`), summed over the same non-source, non-ghost
translations the card iterated (`:708-710`). No new query.

- Needs attention: `getattr(stats, "check:judge-flag")` plus
  `getattr(stats, "check:judge-reject")`. **Not** `judge_flag + judge_reject`.
  `calculate_checks` counts with `check__dismissed=False`
  (`utils/stats.py:961`) while `calculate_judge` counts severities
  (`:940-941`), so after the first dismissal a severity sum would no longer
  equal the queue it links to. `BaseStats.__getattr__` routes the colon key
  through `calculate_by_name` (`:398-401`, `:918-919`), so this is as lazy and
  as cached as every other key.
- Not reviewed: `judge_total - judge_evaluated`. `judge_stale` is a subset of
  it (`utils/stats.py:942-948` requires a null active severity), so staleness
  becomes a clause in the `title` when non-zero rather than a fourth number.
  `judge_total` excludes read-only units (`:930`), which is why the link
  carries `NOT state:read-only`: without it the number and its queue disagree
  on any component that has read-only strings. That value is valid syntax and
  is not a guess: `STATE_NAMES` maps both `read-only` and `readonly` to
  `STATE_READONLY` (`utils/state.py:44-55`) and `convert_state` resolves it
  (`utils/search.py:257-267`), which is the same predicate as the bare
  `read-only` token (`:730-731`). Either spelling is correct; the explicit
  one is used so the link reads as a state filter next to the other clauses.
- Last attempt returned nothing: `judge_unparsed`. It overlaps
  `judge_evaluated` by construction (`models/judge.py:380-394` against
  `:361-372`), which is exactly why it is its own number and is never folded
  into "Not reviewed": a string with a verdict and a newer failed attempt must
  stay visible.

`judge_pass`, `judge_flag` and `judge_reject` stay in the aggregate even though
the strip does not read them: `docs/admin/checks.rst` documents the overlap
from them, Task 8 adds `judge_minor` to the same `aggregate()` call, and
removing two branches of one query saves nothing measurable.

### The one code seam

`show_translation` already builds the auto-form's `initial` from `mode`, `q`
and `next` in the query string (`views/basic.py:923-930`); `show_component`
does not (`:778-785`). Without mirroring that block the run link lands on the
tab with an empty form and the producer silently runs the default filter.
Mirror it; do not invent a second mechanism. `AutoForm.__init__` already drops
a `mode` the user is not allowed to pick (`forms.py:1352-1354`), so no
permission story changes.

### Copy

Every string the strip adds is registered in
`weblate/locale/ru/LC_MESSAGES/django.po` in the same commit, together with
the three judge check names. Today `Release readiness`, `Delivery`,
`AI coverage`, `Advisory` and `Held for decision` have **zero** entries in
that file, so the producer reads an English card inside a Russian page while
the language table beside it is translated. A layout fix that leaves the copy
untranslated does not fix what the producer complained about.

### Files

- Modify: `weblate/templates/snippets/judge-readiness.html` - the file keeps
  its name and its include in `weblate/templates/component.html`; its content
  becomes the strip. Renaming buys nothing and churns a reviewed seam.
- Modify: `weblate/trans/views/basic.py` - replace the `judge_readiness` row
  list (`:704-735`) with one aggregate plus `judge_run_ready`, and mirror the
  auto-form `initial` block.
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`.
- Test: `weblate/trans/tests/test_judge_views.py`.
- Do **not** touch `weblate/templates/snippets/list-objects.html` or
  `weblate/trans/templatetags/translations.py`. The `title` kwarg on
  `list_objects_number` is no longer needed.

### Step 1: Failing tests

- Each number renders only when non-zero, and renders as a link whose `q` is
  exactly the query above, including `NOT language:<source code>` and
  `NOT state:read-only` on the coverage link. Without the language clause the
  same query returns 20 instead of 10 on the fixture, because it counts the
  source language the aggregate excludes.
- A read-only unit is absent from the coverage number **and** from the row
  count its link returns. Assert both halves: the number comes from
  `judge_total`, the queue from the parsed query, and they are two different
  code paths that must agree.
- Dismissing a `judge-flag` check lowers the needs-attention number **and**
  the row count its queue returns, by one. This assertion is what pins the
  choice of `check:` keys over severity keys; write it before the template.
- A unit with a parsed verdict for its current target plus a newer fully
  unparsed round is counted outside "Not reviewed" and inside "Last attempt
  returned nothing". Build it from two `JudgeVerdict` rows, not from a run.
- Nothing judge-related renders for a glossary component, with the judge
  disabled, or with an incomplete seat configuration; the run control is
  absent without `translation.auto` plus `unit.review`.
- The run link carries `mode=judge` and the component view puts it into the
  form's `initial`; assert the bound form, not only the href.
- Negative assertions on the deleted surface: no `judge-readiness-heading`, no
  `Delivery` cell, no `Primary action` cell, no `<dl>` legend.

### Step 2: Render and verify

Keep every count in the view or in stats, never a query in the template
(Plan 02 contract item "Do not query in the template").

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
git commit -m "feat(judge): replace the readiness card with one queue strip"
```

Then open the component page in a browser and click each control, because the
defect this task fixes was visible only on the rendered page.

---

## Task 5: Say what a run will do, and what it did

**Files:**

- Modify: `weblate/templates/snippets/autoform.html`
- Modify: `weblate/trans/autotranslate.py` (summary wording only)
- Test: `weblate/trans/tests/test_judge_form.py`

The estimate block already names the shape of the run: matched rows, the
worst-case request count as "strings × 2 judges × attempts", and the slot
contention sentence (`autoform.html:33-47`). One clause is missing and it is
the one a producer cannot guess - what an "attempt" is: a confirmed defect is
rewritten through the project's own automatic-translation engine and judged
again. Add that to the existing sentence rather than writing a second
paragraph.

The completion summary (`autotranslate.py:415-431`) reports
evaluated/advisory/held/incomplete and the cap remainder, and nothing about
the repairs. Two additions: "N repaired and re-judged", and a link back to
the component page's judge strip, which Plan 02 contract item 11 requires and
the current message omits.

The count is per string, not per verdict row, and the existing shape already
guarantees that: `run_judge_batch` returns `dict[int, JudgeVerdict]` - one
**final** verdict per unit id, not one row per seat
(`judge_loop.py:368-372,397`). So `sum(verdict.attempt > 0 for verdict in
verdicts.values())`, computed where the summary is already built
(`autotranslate.py:870-888`), counts distinct strings; two seats cannot
report two repairs for one string. Assert this in the test with a
two-seat run over one repaired string, so the invariant is pinned rather
than assumed.

```bash
./rundev.sh test weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_autotranslate.py -p no:randomly --no-cov -q
git commit -m "docs(trans): state the judge run sequence and its outcome"
```

---

## Task 6: Reconcile the documents with the decision

Five documents describe the old mapping; the code will not. Task 8 also adds
a third check, which the same page documents.

**Files:**

- `docs/admin/checks.rst:177-188` - the paragraph "A ``flag`` verdict sets
  the string to needs checking..." becomes: `flag` keeps the string
  translated and carries the `judge-flag` check; `reject` sets needs editing;
  the commit policy excludes only the latter. Keep the existing wording about
  repair, `JUDGE_MAY_APPROVE`, and the held-out-sample caveat. In the same
  paragraph, name `judge-note`: a `minor` finding does not change the
  verdict or the state, carries the note check so it is searchable, and is
  counted inside the evaluated total rather than beside it.
- `docs/admin/checks.rst` also becomes the home of the vocabulary the
  deleted legend carried, because Task 4's strip links here: one short
  list of what evaluated, advisory, held, notes, stale and incomplete mean,
  plus the sentence that delivery and AI review are separate axes, and the
  warning that the counts overlap rather than partition. Give the section an
  explicit label so the template can target it, and keep it in the existing
  judge section rather than starting a page.
- `docs/changes.rst:9` - the 2026.8.1 section is unreleased, so edit the
  entry in place rather than adding a second one: major verdicts are repaired
  once and otherwise ship with an advisory check, while rejected strings stay
  held. Add one sentence to the readiness entry at `:19` for the note check,
  because a new failing check is user-visible.
- `docs/llm-first/vision/llm-first-product-architecture.md` - `:592-595`
  (the phase-2 status paragraph), the 4.3 table row for `flag`, and `:607-610`
  (which still claims comments are written; they are not, superseded by
  `designs/2026-08-13-judge-native-ui-design.md:657-658`). Fix all three in
  one pass so the vision stops contradicting the executable contour.
- `docs/llm-first/plans/2026-08-22-03-judge-review-gate.md` - mark Task 3
  superseded by this plan, with the reason in one sentence: it moved the
  delivery decision without re-deriving the cost model the collegium was
  selected under.
- `docs/llm-first/measurements/judge-measurements-index.md:565` and 4.10 -
  record that the premise is restored, so 11.4% is again an attention cost
  and the threshold conflict stays a measurement question, not a delivery
  defect.

No threat-model change: no outbound path, permission, or claimed security
property moves.

```bash
uv run prek run rumdl rumdl-fmt --files <changed docs>
git commit -m "docs(judge): record that an advisory verdict ships"
```

---

## Task 7: Legacy units already held (dry-run only in this plan)

Production ran the review gate, so strings are sitting at state 12 with a
`judge-flag` check. The code change does not move them; they stay held
forever unless they are re-judged or migrated.

A blanket bulk edit over `check:judge-flag AND state:needs-checking` is
**not** acceptable: state 12 can also come from
`weblate/addons/flags.py:76`, from comment resolution
(`weblate/trans/models/comment.py:88`), or from a human, and such a unit must
keep its state.

**Files:** new management command
`weblate/trans/management/commands/judge_release_advisory_holds.py`, tests in
`weblate/trans/tests/test_commands.py` next to the existing guarded judge
commands.

Migration criterion, all conditions required:

1. current state is `STATE_NEEDS_CHECKING`;
2. the unit has a **fresh** parsed `flag` verdict for the current target
   (Plan 02's `judge:flag`, not a stale one);
3. the newest `Change` on the unit that carries a state transition
   (`"state" in change.details`, the same predicate as
   `Change.show_unit_state`, `weblate/trans/models/change.py:1055-1060`) has
   `action == ActionEvents.AUTO` **and** `details["state"] ==
   STATE_NEEDS_CHECKING`;
4. the unit fails no `enforced_checks`, so the hold cannot be a
   deterministic requirement rather than the judge's opinion.

**What condition 3 does and does not prove.** `JudgeVerdict` carries no
`Change` foreign key and no run marker on the change, so a change can never
be attributed to a specific run from persisted fields. What it does prove is
enough: the *last* thing that moved this unit's state was an automatic
translation writing state 12, and in this codebase only the judge's `FLAG`
projection does that. Plain automatic translation writes
`STATE_TRANSLATED`, `STATE_FUZZY` or `STATE_APPROVED`
(`autotranslate.py:389`, `:398-402`),
the repair write inside a run uses `STATE_FUZZY` without the `AUTO` action
(`judge_loop.py:267`), a human bulk edit records
`ActionEvents.BULK_EDIT` (`bulk.py:115`), comment-driven review records
`ActionEvents.MARKED_EDIT` and only ever sets 12 on a source unit
(`comment.py:84-90`), `SourceEditAddon` touches template source units before
creation (`addons/flags.py:70-76`), and the API refuses 11/12 outright
(`api/views.py:3836`). A later human or add-on write therefore fails
condition 3 by being newer, which is exactly the intent.

Combined with condition 2, the pair is what makes a write safe: fresh
advisory evidence for the current text, and no one has touched the state
since the judge set it.

Anything failing a condition is counted and listed as **needs review**, never
touched. The command is dry-run by default, prints both buckets per
component and language, and requires `--write` plus a component or project
scope. Same shape as `check_judge_repair_routes` and
`enable_review_workflow`.

If the dry run shows a large needs-review bucket, the fallback is not a
wider predicate: it is re-judging those strings, which produces fresh
evidence and writes the new mapping through the normal path.

```bash
./rundev.sh test weblate/trans/tests/test_commands.py -k judge -p no:randomly --no-cov -q
git commit -m "feat(judge): add a guarded release for advisory holds"
```

---

## Task 8: Make a `minor` finding visible

`minor` maps to `pass` (`models/judge.py:203-208`), and nothing shows it.
The finding itself is persisted - `JudgeVerdict.errors` holds every reported
error and `max_severity` holds `minor` (`models/judge.py:126-132`) - but the
`pass` branch of the card renders no error list, no check row exists, no
filter selects it, and `judge_pass` counts `none` and `minor` together
(`utils/stats.py:937-939`). So a producer cannot answer "what did the judge
notice on the strings it let through", which is exactly the transparency
being asked for.

Decision taken with the product owner on 2026-08-25: surface it as a third
judge check, so it appears in the component's failing-check list where a
producer already looks, next to `Judge: questionable` and `Judge: rejected`.

**Stated side effect, accepted:** strings carrying only a note will be
counted in :guilabel:`Strings with any failing checks` on the component and
translation pages. That number will grow and it will include non-blocking
notes. The alternative - a counter visible only in the language table - was
rejected because the screen a producer actually reads is the failing-check
list.

**Files:**

- Modify: `weblate/checks/judge.py`, `weblate/checks/defaults.py`
- Modify: `weblate/utils/search.py`, `weblate/utils/stats.py`
- Modify: `weblate/trans/filter.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Test: `weblate/checks/tests/test_judge.py`,
  `weblate/utils/tests/test_search.py`,
  `weblate/trans/tests/test_judge_views.py`

### Step 1: Failing tests

- `JudgeNoteCheck` fires for an active verdict whose `max_severity` is
  `minor`, and does **not** fire for `none`, `major`, `critical`, or an
  unparsed verdict.
- `judge-note` is a member of `JUDGE_CHECKS`. Assert this directly: the
  frozenset drives three separate behaviours (below), and a check that is
  absent from it silently breaks all three.
- `judge:minor` selects exactly the minor-severity units;
  `judge:pass` keeps selecting `none` and `minor` both.
- A `pass` verdict with `minor` severity renders its error list on the card;
  a `pass` verdict with `none` renders no error list.
- The note appears as a `Judge: note` row in the component's failing-check
  list; no language-table column is added.

### Step 2: The check

`BaseJudgeCheck` currently keys on the derived verdict
(`judge_verdict`, `checks/judge.py:43,58-60`). `minor` is a severity inside
`pass`, so add one class attribute next to it:

```python
    # Set instead of judge_verdict when the row projects a severity that
    # does not change the verdict.
    judge_severity: str = ""
```

and let `check_target_unit` prefer it:

```python
    def check_target_unit(self, sources, targets, unit) -> bool:
        verdict = self._active_verdict(unit)
        if verdict is None or verdict.unparsed:
            return False
        if self.judge_severity:
            return verdict.max_severity == self.judge_severity
        return verdict.verdict == self.judge_verdict
```

Then `JudgeNoteCheck` with `check_id = "judge-note"`,
`judge_severity = "minor"`, name `Judge: note`, and a description saying the
judge reported a minor problem that does not hold the string.

**Add it to `JUDGE_CHECKS`** (`checks/judge.py:109`). That one line buys all
three behaviours the other judge checks already have, and each is required
here:

| Consumer | Effect |
| --- | --- |
| `judge_loop.py:80` | the note is not fed back to the judge as evidence, so a seat cannot cite its own note |
| `models/unit.py:2100` | the note stays out of the :guilabel:`Things to check` card, whose content is deterministic checks |
| `models/unit.py:2458` | the note can never be added to `enforced_checks`, so a probabilistic opinion cannot block an edit |

Register the class in `weblate/checks/defaults.py` next to the existing two
(`:93-94`).

### Step 3: Filter, counter, card, table

- `weblate/utils/search.py:812-822`: add `"minor": Q(judge_active_severity="minor")`.
  Leave `judge:pass` as `none` plus `minor`: notes are a **subset** of
  "no blocking concern", not a fourth bucket, so the existing readiness
  copy stays true and no shipped filter changes meaning.
- `weblate/utils/stats.py:98-107` and `:937-939`: add `judge_minor` counting
  `judge_active_severity="minor"`. The annotation already distinguishes it,
  so this is one aggregate, no new query.
- `weblate/trans/filter.py:79-88`: add a named filter
  "Notes - nothing blocking" -> `judge:minor`, directly after the advisory
  entry.
- `weblate/templates/snippets/judge-verdict.html`: in the `pass` branch,
  when `judge_verdict.max_severity == "minor"`, render the per-seat error
  list under a heading that says the string ships and these are notes. Reuse
  the same markup as the flag branch; do not invent a second error layout.
- No language-table column. The note is reachable through the native
  failing-check list (`/checks/-/<project>/<component>/`), which Task 4's
  strip links to, and through `judge:minor`. It stays outside the strip's
  needs-attention number by construction: that query names `judge-flag` and
  `judge-reject` only, and a note does not hold a string.

### Step 4: Verify and commit

```bash
./rundev.sh test weblate/checks/tests/test_judge.py \
  weblate/utils/tests/test_search.py \
  weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
git commit -m "feat(judge): surface minor findings as a note check"
```

---

## Verification

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_commands.py \
  weblate/checks/tests/test_judge.py \
  weblate/utils/tests/test_search.py -p no:randomly --no-cov -q
uv run prek run ruff-check ruff-format djlint-django rumdl --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Then drive it once on the dev stack, clicking the real controls:

1. Run `mode=judge` on a component with a project-level
   `WITHOUT_NEEDS_EDITING` policy. A string whose final verdict is `flag`
   must show the ships badge, must appear under the Advisory queue, and must
   **not** appear under pending-blocked delivery.
2. A string whose final verdict is `reject` keeps "Will not ship" and stays
   blocked.
3. A repaired string shows the repair block with the first-attempt errors
   **and the text before the repair next to the text now**. This is the
   producer's acceptance test for "what did the judge actually change".
4. A string whose worst finding is `minor` stays translated, shows its notes
   on the card, and appears under the `Judge: note` failing check on the
   component page.
5. Every number in the strip opens a queue whose row count equals the number,
   including after a judge check is dismissed, and a component with no judge
   evidence renders no numbers at all - only the run control.
6. On a string with a parsed verdict plus a newer failed attempt, the strip
   counts it under "Last attempt returned nothing" while it stays outside
   "Not reviewed".
7. Run `judge_release_advisory_holds` without `--write` on the dev stack and
   confirm the needs-review bucket is non-empty when a unit's 12 came from an
   add-on rather than the judge.

If the suites return mass setup errors, check `docker stats --no-stream`
first (`AGENTS.md`).

## Production rollout

**Needs its own explicit approval; not part of the code change.**

1. Deploy the code.
2. Run `judge_release_advisory_holds` dry-run per project and read both
   buckets with the producer before writing anything.
3. Only then run it with `--write`, project by project.
4. Only then decide whether to re-run the French judge pass on
   `victory-banner/common`, which is still the open item from
   `docs/llm-first/plans/2026-08-24-auto-translate-queue-and-progress.md`.
