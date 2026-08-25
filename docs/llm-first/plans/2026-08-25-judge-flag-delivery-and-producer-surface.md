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

**Decided on 2026-08-25 in a flow-validation interview** (answers 5-16 of the
producer's walkthrough), adding Tasks 9-13:

| # | Decision | Task |
| --- | --- | --- |
| 5, 7 | The run's completion alert carries a button to the run result; the three edits it needs (task result carries the URL, `message.html` gains a slot, the JS renders an anchor) are accepted | 10 |
| 6 | A judge run that changed nothing says so, instead of reporting "0 strings updated" | 10 |
| 8 | The report is addressed by `run_id` (variant D of the interview), not a live queue and not a generated document | 9 |
| 9 | The project page links to the native check browser; it grows no judge table of its own | 4 |
| 14 | The `resolution` fields stop being dead schema: a producer's decision on a verdict is recorded | 11 |
| 15 | A held `critical` can be shipped deliberately, by a producer or an admin, and only on the record | 12 |
| 16 | The judge repairs automatically; what reaches a human is `critical` plus what a human escalated, and everything else is a line in the report | 13 |

**Superseded by answer 16, and stated here because it reverses a recorded
decision.** The office-hours session earlier the same day made the strip's
primary number the union `check:judge-flag OR check:judge-reject` and called
it the queue of strings that need a human. Answer 16 defines that queue
differently: what reaches a human is `critical` plus what a human escalated,
while an unrepaired `major` and every `minor` are report lines. Both
statements come from the same producer, hours apart, and the later one is more
specific, so it governs:

- The strip's primary number becomes **needs a human** = `critical` +
  escalated. Task 4 carries the change; Task 11 adds the escalation key it
  needs.
- `judge-flag` and `judge-note` keep their checks, their named filters and
  their place in the run report. They are evidence a producer can search, and
  the material of "noted, not fixed" - they are not a queue asking for work.
- Nothing about the release gate moves: `critical` was already the only
  severity that holds a string.

Veto this by saying so before Task 4 Step 1; the tests assert the number, so
it cannot be changed quietly afterwards.

**Decided on 2026-08-25 (interview answers 1 and 4):**

*Answer 1:* a run starts at the level its launcher lives on - from the
project page it judges the project, from a component the component, from a
language the language. The scope the page already scopes is the scope the
judge judges. Task 4 adds the project-page launcher: a "Run the judge" button
on `/projects/<slug>/` (and on a workspace page the workspace), rendered by
the same strip include, POSTing to the existing `auto_translation` endpoint
with `project_id` / `workspace_id` set (`views/edit.py:1568-1618` already
accepts them; `BatchAutoTranslate` at `autotranslate.py:1130` already walks
the whole project). The strip on the component page and the per-language run
stay as they are.

*Answer 4:* the task alert says what it is doing, in the same alert, as text
above the bar - "Judging 120 of 448", then "Repairing 12 of 18", then
"Done: evaluated N, repaired K, held M". Not a second channel: the
percent-only bar remains underneath, and the phase line is the `progress`
field the existing `get_task_progress`/`TaskSerializer`/poller chain already
carries, extended with the phase and the counts the caller knows
(`judge_loop.py` knows the phase; `BatchAutoTranslate.get_task_meta` at
`autotranslate.py:1130` is where it is written). What it must never do is
restate the phase from a guess: if the caller does not know the phase yet,
the line says "Queued" or nothing, never a placeholder.

**Still open, and not implemented by this plan:** the default filter of a
run, whether the report states actual cost (see Task 9 - `LLMUsageLog`
carries no run id today), and whether advisory and note counts appear beside
the primary number at all or live only in the report and the filter list.
These are interview questions 2, 3 and the remainder of 12; they are recorded
here so the next session does not mistake silence for a decision.

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

**Two UX constraints, stated by the producer on 2026-08-25 and binding on
every task below.**

*There are no translators.* This installation is LLM-first: a machine
translates, two machines judge, a machine repairs, and the only human in the
loop is the producer. So no surface may offer a hand-off that has no
recipient, and the four outcomes of a held string are named exactly:

1. **ship it** - `accepted_as_is`, Task 12, and it leaves the queue;
2. **fix it here** - an ordinary edit in the editor, which produces new text
   and makes the old verdict stale by itself;
3. **run again** - re-judge or re-translate the string;
4. **escalate** - `escalated`, the one hand-off that does have a recipient:
   a person outside this pipeline, a game team or a native speaker, whom the
   producer cannot replace. It is the only outcome that keeps a string in
   "needs a human", because it is a human saying a human is required. It also
   applies to a `major` the producer refuses to let ship.

`JudgeVerdict.Resolution.SENT_BACK` (`models/judge.py:115-118`) is the one
that dies: it names a translator queue, and there is no translator. Task 11
does not offer it, and every "review" wording that implies such a queue is
rewritten to address the producer directly.

*The card must not get crowded.* The verdict card is the one screen where the
producer decides, so it is the one screen that must stay readable. Tasks 3,
8, 11 and 12 all land on it, and their combined budget is: one severity line,
the error list, the existing back-translation, one repair block that appears
only when a repair happened, and **one** action row. No task may add a second
action row, a second panel, or a permanently visible explanation. The
instruction the judge sends the fixer is machinery and stays off this card
entirely (Task 13).

One consequence for copy everywhere: `judge:pass` selects `none` **and**
`minor` (`utils/stats.py:940-941`, and Task 8 keeps it that way), so no label
on it may say "no findings". It says "nothing blocking", which is what it
counts.

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
with each other. The vocabulary is the judge's own severity - `critical`,
`major`, `minor` - decided with the producer on 2026-08-25 and detailed
below; this task makes every surface use it.

| Surface | Now | After |
| --- | --- | --- |
| `weblate/trans/filter.py:79-88` | "Advisory - ships" = `judge:pass`; "Held for decision" = `judge:flag OR judge:reject` | "Judge - critical (held)" = `judge:reject`; "Judge - major (ships)" = `judge:flag`; "Judge - minor (nothing to do)" = `judge:minor`; "Judge - nothing blocking" = `judge:pass` |
| `weblate/templates/snippets/judge-readiness.html:36-44,66-67` | Advisory column = flag, Held = reject, legend claims delivery is not held | content replaced by Task 4's strip, whose primary number is "needs a human"; the per-severity split stays in the native check browser |
| `weblate/trans/autotranslate.py:415-431` | `advisory` = FLAG, `held` = REJECT | the run summary names severities and adds `repaired` (Task 10) |
| `weblate/templates/snippets/judge-verdict.html:62-80` | flag card "Questionable", no delivery badge | the card states the severity and, in one line, whether the string ships |

### The check names themselves (producer's decision, 2026-08-25)

Asked what `Judge: questionable` and `Judge: rejected` mean, the producer had
to ask - which is the answer. They are the two check names met most often,
they are translated from the verdict enum rather than from anything a reader
knows, and one of them is now factually wrong: `JudgeFlagCheck.description`
still reads "The string is held for review" (`checks/judge.py:95-97`) while
Task 1 makes that string ship.

**Decided: name them by severity, exactly as the judge reports it.** The
severity is the one word that never drifts - it is what the model returns,
what `max_severity` stores, what the strip counts and what the report groups
by. Consequences belong in the description, where they can change without
renaming a check.

| `check_id` | Now | After | Description after |
| --- | --- | --- | --- |
| `judge-reject` | Judge: rejected | **Judge - critical** | The judge found a critical problem and the automatic repair did not fix it. The string does not ship until you decide. |
| `judge-flag` | Judge: questionable | **Judge - major** | The judge found a major problem and the automatic repair did not fix it. The string ships; read it when you can. |
| `judge-note` (Task 8) | - | **Judge - minor** | The judge noted something minor. No repair was attempted and nothing is expected of you. |

`check_id` values do not change: they are in URLs, in `enforced_checks`
configuration, in `CHECK_LIST` and in every existing `?q=check:judge-flag`
link. Only `name` and `description` move.

The named filters take the same three words plus the consequence, so the
dropdown and the check list cannot disagree: "Judge - critical (held)",
"Judge - major (ships)", "Judge - minor (nothing to do)". All nine strings are
registered in `weblate/locale/ru/LC_MESSAGES/django.po` in this commit - today
none of them are, so the producer reads English check names in a Russian
interface.

**Files:**

- Modify: `weblate/trans/filter.py`
- Modify: `weblate/checks/judge.py` (the two `name`/`description` pairs),
  `weblate/locale/ru/LC_MESSAGES/django.po`
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
| Needs a human | `(judge:reject AND NOT judge:resolved) OR judge:escalated` | `judge:reject` 1; both resolution keys arrive with Task 11 |
| Not reviewed | `NOT has:judge AND NOT language:<source code> AND NOT state:read-only` | 10 |
| Last attempt returned nothing | `judge:unparsed` | filter parses; 0 in this component |

**A resolved `critical` must leave this number, or the count becomes a
monument.** Task 12 ships a held string by recording `accepted_as_is` and
moving the state; it deliberately does not touch `max_severity`, because the
verdict is immutable evidence. So a bare `critical` predicate would keep every
deliberate override in the producer's queue forever. The predicate therefore
reads the resolution, not only the severity: an unresolved current `critical`
counts, any resolution removes it, and `escalated` puts it back - that is
exactly the "escalated by a human" half of answer 16. One verification step
below watches an override disappear from the count.

and two controls that always render:

- **Breakdown by check** -> `/checks/-/<project>/<component>/`. This is where
  every judge finding is listed per language, with links to the string and
  with dismissal: the advisory strings that ship, the notes, and the held
  ones. It is the strip's answer to "show me everything the judge said",
  while the strip itself only counts what still needs a person.
- **Run the judge** -> `?mode=judge&q=NOT+has%3Ajudge#auto`, the component's
  own automatic-translation tab, which already offers `mode=judge`
  (`forms.py:1347-1350`) and already carries the estimate block Task 5
  extends. The per-language run stays where it is, on the translation page.

Why the primary number is no longer the flag/reject union: answer 16 put an
unrepaired `major` in the report rather than in a person's queue, and the
header records that supersession. The union query itself survives as Task 2's
named filters and in the check browser, so nothing becomes unsearchable - only
the headline count changes meaning, from "findings that exist" to "strings a
person must still decide".

Vocabulary: Task 2's named filters keep their own names ("Advisory - ships",
"Held for decision"). The strip's own label is the only place the words "needs
a human" appear, and its `title` states what it counts and what leaves it, so
the filter dropdown, the check browser and the strip cannot drift apart.

### The project page (interview answers 9 and 1)

A producer with several loc-kit components works from
`/projects/need-for-greed/`, so that page must reach the judge without
visiting each component. It gets **one strip, no table**, and the strip is
the launcher only: **Run the judge** and **Breakdown by check**, with the
three count cells absent. This is not a template-side omission, it is a
correctness constraint: `ProjectStats` does not expose judge keys
(`AggregatingStats` at `utils/stats.py:1011+` carries `basic_keys =
SOURCE_KEYS` and no judge branch), and a project-level "Not reviewed" number
has no query that reproduces it, because `NOT language:<source code>` must
exclude each component's own source language and a project may mix them.
The include therefore gains an explicit flag, `show_judge_counts`, and the
project render passes `False`. From the project page the judge judges the
project - `auto_translation` with `project_id` set, which is exactly what
`BatchAutoTranslate` already walks (`autotranslate.py:1130`). No scope
picker: the page the producer started from is the scope.

That link is already a complete per-project judge surface, and it is
localized: `CheckList` accepts a `Project` path and lists every check name in
the project with total, dismissed and active counts
(`checks/views.py:122-125,148-159`), and `/checks/judge-flag/<project>/`
breaks one check down to a row per component (`:171-180`), while
`/checks/judge-flag/<project>/-/<code>/` does it per component for a single
language (`:227-238`). Because `judge-flag`, `judge-reject` and Task 8's
`judge-note` are ordinary checks, all of that exists the moment they fire.

No judge numbers are aggregated to the project, and this is a correctness
decision rather than a cost one. Judge statistics exist only on
`TranslationStats` (`utils/stats.py:98-107,916-954`); `AggregatingStats`
carries `basic_keys = SOURCE_KEYS` and no judge branch (`:1011+`), so
`project.stats.judge_total` raises `Unsupported stats` today. Adding the
aggregation would produce a number no query can reproduce: the strip's
"Not reviewed" link must exclude the component's own source language
(`NOT language:<source code>`), and a project's components may each have a
different source language, so one project-level `q` cannot express the
exclusion that the number was counted with. A count whose queue disagrees
with it is the exact defect this plan is removing from the readiness card.
The two absence-based numbers therefore stay per component, where they are
exact.

### Where the numbers come from

All of them from the `prefetch_stats()` pass the language table already makes
(`views/basic.py:687`), summed over the same non-source, non-ghost
translations the card iterated (`:708-710`). No extra pass over the database,
but the judge aggregate does gain one branch and one annotation, described
below.

- Needs a human: a new `judge_needs_human` key in the same
  `calculate_judge()` aggregate (`utils/stats.py:940-941`), counting units
  whose current verdict is `critical` with no resolution, plus units whose
  current verdict carries `resolution="escalated"`. This needs the current
  verdict's resolution as an annotation beside the existing ones in
  `judge_status_annotations()` (`models/judge.py:359-402`), which is one more
  correlated subquery in a query that already runs three. Task 11 owns both,
  because the field it reads is the field Task 11 starts writing.

  **Deliberately not `check:judge-reject`.** A dismissed check disappears from
  `calculate_checks` (`utils/stats.py:961`) while the severity stays, so a
  check-based count would let a dismissal hide a held string that still blocks
  delivery. Dismissal is the right tool for an advisory and the wrong tool for
  a hold, and the release gate reads state, not check rows.
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
- Modify: `weblate/templates/project.html` - the same strip include, rendered
  only when the judge is configured for the project and the user may see its
  components, with `show_judge_counts=False`; the run button POSTs to
  `auto_translation` with `project_id`, and the breakdown link goes to
  `/checks/-/<project>/`.
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
- On `/projects/<slug>/` the strip renders with `show_judge_counts=False`:
  the run button and the breakdown link are present, and the three count
  cells are absent. Assert the absence, not just the flag, so a template
  refactor that forgets the flag fails the test.

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
producer already looks, beside `Judge - critical` and `Judge - major`. Its
name is `Judge - minor`, from the same severity vocabulary Task 2 sets.

**Stated side effect, accepted:** strings carrying only a note will be
counted in :guilabel:`Strings with any failing checks` on the component and
translation pages. That number will grow and it will include non-blocking
notes. The alternative - a counter visible only in the language table - was
rejected because the screen a producer actually reads is the failing-check
list.

**And it stays out of the producer's queue.** Answer 16 puts a note in the
report, not in a person's work list, so `judge-note` is searchable and
visible as a failing check while the strip's "needs a human" number never
counts it. The two statements are consistent because they answer different
questions: the check list says what the judge observed, the strip says what
is still waiting on a decision.

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
- The note appears as a `Judge - minor` row in the component's failing-check
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
`judge_severity = "minor"`, name `Judge - minor`, and a description saying the
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

## Task 9: A run has a report, addressed by its `run_id`

**Decided 2026-08-25 (interview answer 8, variant D).** The producer's first
question after a run is not "which strings are flagged" - that is the strip -
but "what did this run do". No surface answers it today.

The identity already exists and is stable: `run_id` is generated once per run
(`judge_loop.py:387`) and carried into every row it writes (`:140-161`,
`:329-352`, `:443-444`); rows are unique per `(unit, run_id, attempt, seat)`
(`models/judge.py:176-178`) and `run_id` is indexed (`:169`). So the three
counts the producer asked for are derivable from persisted evidence, with no
new fields and no new writes:

| Report line | Derivation from `JudgeVerdict` rows of one `run_id` |
| --- | --- |
| checked | distinct units with an `attempt=0` row |
| repaired | units whose collegium severity at the final attempt is lower than at `attempt=0` |
| noted, not fixed | units whose final severity is `minor`, plus units whose `major` survived the repair budget |
| held for a human | units whose final severity is `critical` |
| no answer | units whose final rows are all `unparsed` |

`request_verdicts()` returns only `verdicts` (`judge_loop.py:491`), so the run
id never reaches its caller. Return it, or the completion alert of Task 10 has
nothing to link to.

**Cost is deliberately absent.** `LLMUsageLog` (`models/llm_usage.py:31-46`)
has no run id: rows are written at the machinery seam
(`BaseOpenAITranslation.fetch_llm_translations`), and the judge's requests
arrive there indistinguishable from each other beyond `operation="judge"` and
`project_slug`. Attributing rows to a run by timestamp and model would be a
guess printed as an invoice. Actual per-run cost therefore needs `run_id` on
`LLMUsageLog` threaded from the loop to that seam - a separate, still-open
decision (interview question 3). Until then the report states scope, counts
and duration only, and the pre-run estimate keeps owning money.

Not a generated document. Weblate's report subsystem
(`templates/snippets/reports.html`, `credits`/`counts`/`costs`, persisted
downloads via `api:report-json|html|rst`) exists for periodic exports a human
asks for by date range. A run report is a view of rows that already exist and
is addressed by an id, so it is a page, not an artifact to generate and store.

**Files:**

- Modify: `weblate/trans/judge_loop.py` - return the run id alongside the
  verdicts.
- Modify: `weblate/trans/autotranslate.py` - keep the run id on the judge
  summary.
- Add: `weblate/trans/views/judge.py` - the report view, permission-gated by
  the same access check the component page uses.
- Add: `weblate/templates/judge-run.html`.
- Modify: `weblate/urls.py`.
- Modify: `weblate/templates/snippets/judge-readiness.html` - the strip gains
  a "Last run" link when the component has at least one run.
- Test: `weblate/trans/tests/test_judge_views.py`.

### Step 1: Failing tests

- A run whose rows exist renders every count above, and each count that has a
  queue links to it.
- A repaired unit is counted as repaired exactly once, not once per attempt.
- A unit judged in an earlier run and re-judged in this one is counted in this
  run only.
- A user without access to the component gets 404, not 403 with the counts in
  the body.
- The report renders with no cost figure anywhere.

### Step 2: Build it, then verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
git commit -m "feat(judge): report one run by its id"
```

---

## Task 10: The completion alert links to the report

**Decided 2026-08-25 (interview answers 5, 6, 7).**

The alert in the producer's screenshot is a Django message tagged
`task:<id>` (`views/edit.py:1708`) rendered by `message.html`, whose
`.task-message` text is replaced on completion by the JS poller
(`static/loader-bootstrap.js:1753-1763`) from the task's JSON
(`api/serializers.py:4150-4153`). The poller writes with `textContent`, so a
link inside the message string is escaped, not rendered. The three accepted
edits are therefore the only way:

1. the judge task result carries `report_url` beside `message`;
2. `message.html` gains an actions slot next to `.task-warnings`;
3. the poller renders an anchor into that slot when `result.report_url` is
   present, and nothing when it is not.

The same alert is re-rendered on every page while the run lives, from the
persistent list (`utils/celery.py:96-113`, `trans/context_processors.py:136-144`,
`base.html:478`). That registry already stores a per-task `url` and `label`
which the template never renders; render the label as a link to it, so a
producer who navigated away can get back to the object as well as forward to
the report.

**Answer 6, and a defect the producer caught while reading this plan:** the
judge summary never states how many strings the judge *fixed*. It counts
`evaluated`, `passed`, `advisory`, `held` and `incomplete`
(`autotranslate.py:418-433`) - a repair that succeeded is invisible, because
the repaired string ends up inside `passed`. A producer who is told "the judge
sends its findings to the translator and it fixes them" then reads a line that
never mentions a fix.

So the completion line gains the count Task 9 already derives: **repaired**.
It reads, in this order: evaluated, repaired, noted but not fixed, held for a
human, no answer. "Changed nothing" is then a statement the run earns only
when it truly found nothing - not the default the wording falls into. The
`updated` counter of plain automatic translation stays out of judge mode: it
counts machine-translation writes, and a repair is not one of them.

A judge run on a clean component therefore says it evaluated N strings and
found nothing to fix, and a run that repaired 12 of 40 findings says exactly
that. Neither ever says "0 strings updated".

**Files:**

- Modify: `weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`
- Modify: `weblate/templates/message.html`,
  `weblate/static/loader-bootstrap.js`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Test: `weblate/trans/tests/test_judge_autotranslate.py`,
  `weblate/trans/tests/test_autotranslate.py`

### Step 1: Failing tests

- A judge task result carries a `report_url` that resolves to Task 9's view.
- A non-judge run carries no `report_url`, and the alert renders no button.
- A judge run that repaired strings states the repaired count, and the number
  equals the report's `repaired` bucket for the same run.
- A judge run that found nothing says so, and never reports "0 strings
  updated".

### Step 2: Build it, then verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py -p no:randomly --no-cov -q
git commit -m "feat(judge): link the finished run to its report"
```

---

## Task 11: Record the producer's decision on a verdict

**Decided 2026-08-25 (interview answer 14): close the hole.**
`JudgeVerdict.resolution`, `resolution_reason`, `resolved_by` and
`resolved_at` exist (`models/judge.py:146-155`) and **nothing writes them** -
not the loop, not any view. So today a producer who disagrees with the judge
can only dismiss a check, which removes the row from `check:judge-*` while the
severity count keeps it (`utils/stats.py:961` against `:940-941`), and leaves
no record of *why*. That is also why the judge's live precision cannot be
measured from production work.

The verdict card (`templates/snippets/judge-verdict.html`, rendered in the
string editor at `templates/translate.html:601`, context built at
`views/edit.py:1295-1319`) gains **one action row and nothing else**, per the
card budget in the header. Two buttons, and only when the actor may act:

- **Ship it** - writes `accepted_as_is`, and for a held string also moves the
  state (that is Task 12, same row, same commit as far as the card is
  concerned: the producer sees one button, not two).
- **Needs a person** - writes `escalated`.

A reason is required for both, in one field that appears with the row rather
than above it. `sent_back` is not offered. No third button: fixing the text
and re-running are already the editor's own controls, and duplicating them
here would spend the card budget on things the page already has.

The verdict row itself stays immutable evidence - a resolution is a separate
column on it, never an edit of `max_severity` or `errors`.

**This task also owns the query surface Task 4's primary number needs.** A
resolution that only a card can show would leave the strip counting resolved
holds forever, so the same commit adds, beside the existing judge keys:

- `judge:resolved` - the current verdict carries any resolution;
- `judge:escalated` - the current verdict carries `escalated`;
- the current verdict's resolution as an annotation in
  `judge_status_annotations()` (`models/judge.py:359-402`), and a
  `judge_needs_human` branch in `calculate_judge()` reading it
  (`utils/stats.py:940-941`).

Both keys describe the newest current-context round, exactly like
`judge_active_severity`, so a resolution recorded against text that has since
changed stops counting on its own.

**Files:**

- Modify: `weblate/trans/models/judge.py` (a helper that writes the four
  fields under `select_for_update`), `weblate/trans/views/edit.py`,
  `weblate/trans/forms.py`
- Modify: `weblate/utils/search.py` (`judge:resolved`, `judge:escalated`),
  `weblate/utils/stats.py` (`judge_needs_human`)
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Test: `weblate/trans/tests/test_judge_views.py`,
  `weblate/utils/tests/test_search.py`, `weblate/utils/tests/test_stats.py`

### Step 1: Failing tests

- Recording a resolution writes all four fields, including the actor and the
  timestamp, and leaves `max_severity` and `errors` untouched.
- A user without review permission cannot record one.
- A resolution on a stale verdict is refused: the text it judged is gone.
- Task 9's report counts resolutions by kind.
- `judge:resolved` and `judge:escalated` parse, and their row counts equal
  `judge_needs_human` when combined as Task 4 combines them.
- An `accepted_as_is` on a `critical` removes that unit from
  `judge_needs_human`; an `escalated` on a `major` adds one.
- Editing the target after a resolution drops the unit out of both keys,
  because the round is no longer current.

### Step 2: Build it, then verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
git commit -m "feat(judge): record the producer's decision on a verdict"
```

---

## Task 12: Ship a held string deliberately, on the record

**Decided 2026-08-25 (interview answer 15): producer and admin.** A `critical`
verdict holds a string at `STATE_FUZZY`, and the project's
`WITHOUT_NEEDS_EDITING` commit policy keeps it out of the build. The only ways
out today are editing the text or re-running the judge; there is no way to say
"the judge is wrong, ship it".

Build it as Task 11's `accepted_as_is` resolution plus the state change, in
one action, never as a separate bypass: the string moves to translated *and*
carries a row saying who overrode which verdict and why. Gate it on the
permission that already lets a user move a unit's state on review
(`unit.review`), so no new permission vocabulary enters the project, and the
action is absent - not merely disabled - for anyone else.

The release gate's certification (`critical` holds a string) is unchanged: it
describes what the judge does, and this task adds a human who can answer it
with an audit trail. Task 9's report counts these overrides, which is the
number that tells whether the judge's `critical` threshold is calibrated.

**Files:**

- Modify: `weblate/trans/views/edit.py`, `weblate/trans/forms.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Failing tests

- The override moves the unit out of `FUZZY_STATES` and writes an
  `accepted_as_is` resolution with a reason in one transaction.
- Without `unit.review` the control is absent and the POST is refused.
- A string with no `critical` verdict has no override control.

### Step 2: Build it, then verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge.py -p no:randomly --no-cov -q
git commit -m "feat(judge): let a reviewer ship a held string on the record"
```

---

## Task 13: The repair carries the judge's instruction, and the report states what it left

**Decided 2026-08-25 (interview answer 16):** if the judge has remarks it
writes instructions and sends them to the main model automatically, so that
what reaches a human is `critical` plus what a human escalated, and everything
else is a line in the report - so many checked, so many fixed, this was noted
and not fixed.

Most of this is already the code, and one gap is real.

What exists: `_process_round_unit` sends a verdict back through the project's
own MT engine (`judge_loop.py:303-306`), the fixer's prompt receives the
judge's own findings because `machinery/llm.py` calls `get_description()` on
every failing check and `BaseJudgeCheck.get_description()` renders the active
verdict's errors (`checks/judge.py:77-88`), the budget is
`JUDGE_MAX_REPAIR_ATTEMPTS` (`defaults.py:58`, production 1), and a repair
whose checks come back worse is rolled back to the exact previous
target/state.

### Decided: repair stays on `major` and `critical`

**Producer's answer, 2026-08-25:** repair `major` and `critical` only. That is
what the code already does - `_NON_REPAIRABLE_VERDICTS` holds `PASS` and
`UNPARSED` (`judge_loop.py:56-59`) and `minor` maps to `pass`
(`models/judge.py:203-208`) - so the predicate does not change, no severity
floor is introduced, and no setting is invented. A note is never sent to the
fixer and never costs a paid request.

Two consequences to carry, not to rediscover:

- The report's "noted, not fixed" line is now two populations with one
  meaning: every `minor` (never attempted) and every `major` that survived the
  budget. Task 9 derives both from the same rows, so no field is added.
- Task 8 is **not** a prerequisite of this task. Notes never reach the fixer,
  so the instruction only has to ride the rails that already exist:
  `JudgeFlagCheck` and `JudgeRejectCheck` project rows for exactly the two
  severities that are repaired (`checks/judge.py:91-109`), and
  `BaseJudgeCheck.get_description()` already feeds them to the translator
  prompt (`:77-88`). `judge-note` remains Task 8's own business.

### The gap: the judge does not author an instruction

Today the fixer receives the judge's MQM error list - `span`, `category`,
`severity`, `description` per error (`judge.py:218`). That is evidence about
what is wrong, not an instruction about what to do, and the requirement asks
for the instruction.

Add one output field, `instruction`, to the judge's response schema
(`judge.py:206-237`): a short, imperative repair instruction for the whole
segment, required whenever `errors` is non-empty and empty otherwise. Three
places move with it, and all three are strict by design, so none can be
skipped: the schema properties and its `required` list, the parser's exact
key-set equality (`judge.py:374`), and the `JudgeResult` dataclass
(`:144-146`). Persist it on `JudgeVerdict` next to `errors`, and append it to
`describe_latest_verdict` so it rides the check rail that `judge-flag` and
`judge-reject` already project.

**It is not rendered to the producer, by decision of 2026-08-25.** The
instruction is machinery talking to machinery; the producer judges the result,
not the prompt. So it is stored (the fixer needs it, and an unexplained repair
must be explainable in an audit) and it appears in no producer surface - not
the verdict card, not the run report. A developer reads it through the admin
or the database. Should it ever be needed for a defect hunt, the surface to
add it to is the run report, never the editor.

**The cost, stated:** the schema docstring already records that
`back_translation` is "the single deliberate deviation from the measured
schema (an extra output field, unmeasured; minimal metric risk)"
(`judge.py:206-208`). This makes it the second. Judge output tokens per string
grow, and the calibration measurements were taken without this field. The
honest mitigation is in the verification below: the first run after this task
is compared against the previous run on the same component, and if verdict
distribution moves, the field is the first suspect.

### What the report then says

Task 9's buckets become the producer's sentence with no new fields: `checked`
from `attempt=0` rows, `repaired` where the final severity is lower than the
first, `held` for `critical`, and `noted, not fixed` for every `minor` plus
every `major` that survived the budget.

**Files:**

- Modify: `weblate/trans/judge.py` (schema, parser, dataclass),
  `weblate/trans/models/judge.py` (persist `instruction`, extend
  `describe_latest_verdict`), plus a migration
- Modify: `weblate/checks/judge.py` (the instruction joins the rendered
  description)
- Do **not** modify `weblate/templates/snippets/judge-verdict.html`: the
  instruction stays out of the editor.
- Modify: `docs/admin/checks.rst` (the repair contract: what is repaired, what
  is not, the budget, and that the judge writes the instruction)
- Test: `weblate/trans/tests/test_judge_loop.py`,
  `weblate/trans/tests/test_judge.py`,
  `weblate/trans/tests/test_judge_views.py`

### Step 1: Failing tests

- A `minor` verdict triggers **no** repair request, and a `none` and an
  `unparsed` round trigger none either; a `major` and a `critical` each
  trigger exactly one within the budget.
- A reply whose `errors` is non-empty and whose `instruction` is missing is
  rejected by the parser as unparsed, not silently accepted.
- The persisted instruction appears in `describe_latest_verdict`, and
  therefore in the failing-check description the fixer receives.
- **The rail is pinned, not assumed:** repairing a `major` unit sends the
  fixer a prompt that contains the judge's instruction. Assert on what
  `repair_target()` hands the engine, so the test fails if the flag check
  stops being projected or if the prompt stops reading check descriptions.
- A `major` repaired into a `pass` is reported as repaired; a `major` that
  survives is reported as noted, not fixed; every `minor` is reported as
  noted, not fixed without a repair attempt; a `critical` that survives stays
  held and is never counted as repaired.
- The rollback path still restores the exact previous target and state when a
  repair regresses another check.

### Step 2: Build it, then verify

```bash
./rundev.sh test weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py -p no:randomly --no-cov -q
uv run prek run rumdl --files docs/admin/checks.rst
git commit -m "feat(judge): send the judge's repair instruction to the fixer"
```

Then, on the dev stack, run the same component twice - once before this task
and once after - and compare verdict distribution and repair count. Record
both in `docs/llm-first/measurements/` with the date. If the distribution
moved, the new output field is the first suspect and the measurement says so.

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
   on the card, appears under the `Judge - minor` failing check on the component
   page, and is **absent** from the strip's "needs a human" number.
5. Every number in the strip opens a queue whose row count equals the number,
   and a component with no judge evidence renders no numbers at all - only the
   run control. An advisory string appears in the check browser and in the
   named filter, and never in "needs a human".
6. On a string with a parsed verdict plus a newer failed attempt, the strip
   counts it under "Last attempt returned nothing" while it stays outside
   "Not reviewed".
7. Run `judge_release_advisory_holds` without `--write` on the dev stack and
   confirm the needs-review bucket is non-empty when a unit's 12 came from an
   add-on rather than the judge.
8. Start a judge run, wait for the alert to turn green, and **click the button
   in it**. It must land on the report for that run, and the report's
   `checked` count must equal the number the alert stated.
9. Navigate away mid-run, then load an unrelated page: the persistent alert is
   there, its label is a link back to the object, and when the run finishes it
   grows the same report button.
10. Run the judge on a component where nothing needs changing. The alert says
    it evaluated the strings and changed none; it never says "0 strings
    updated".
11. Open a `critical` string, record "the judge is wrong" with a reason, and
    confirm: the string leaves the fuzzy state, the verdict row is unchanged,
    the resolution row names the actor and the reason, and the report counts
    the override.
12. Repeat 11 as a user without `unit.review`: the control is absent and a
    hand-made POST is refused.
13. On a string whose worst finding is a `major`, confirm from the run report
    that a repair was attempted and that the fixer's prompt carried the
    judge's instruction (dev log or the stored verdict row - the editor must
    not show it). On a
    `minor`, confirm no repair request was made at all.
14. Record `accepted_as_is` on a held `critical` and watch the strip's "needs
    a human" number fall by one; record `escalated` on an advisory string and
    watch it rise by one.
15. From `/projects/<project>/`, reach the judge with one click and confirm the
    check browser lists every component that has judge findings.

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
