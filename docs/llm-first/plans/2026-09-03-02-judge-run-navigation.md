# Judge run navigation: reaching a finished report

Status: implemented 2026-09-03 (tasks 1-9; the /plan-design-review step ran as a
manual pass over the changed templates because no such instrument exists as a
tool, and the browser pass ran against the live dev instance). Reviewed in
`docs/llm-first/reviews/2026-09-03-judge-run-navigation-review.md`; its
amendments F1-F6 are folded into the design and tasks below. Amended
2026-09-03 by F7 (row hierarchy) after the shipped control was seen on live
data, together with a layout fix for the control's own presentation: the
theme makes `.btn-group` a block-level flex container
(`weblate/static/styles/main.css`), which stretched the split button across
the whole card.

Follow-up to `docs/llm-first/plans/2026-09-03-judge-run-report-pareto.md`, which
rebuilt the report page itself. That plan assumed the producer arrives at the
report; this one makes arriving possible at all.

## Problem

A finished report is reachable only through the ephemeral task alert on the
progress bar. Miss that click and the report is gone from the UI: a settled
Celery task is forgotten immediately, so nothing on any page points back to it.

`weblate/templates/snippets/judge-readiness.html:30-33` does carry a
:guilabel:`Last run` button, but it renders only when
`last_judge_run()` (`weblate/trans/views/judge.py:313-350`) finds a run whose
scope matches the page **exactly**, and it filters by the requesting actor.
Every launch in practice is translation-scoped, so the button is structurally
absent from the component and project pages a producer returns to.

Measured on the development instance (all seven runs, actor `admin`):

|Scope|Runs|
|---|---|
|Need for Greed/Orders - Russian|1|
|CoL4/data - French|5|
|CoL4/common - French|1|

So `/projects/col4/` shows no report link at all today, and once one appears,
a single "last run" link would still hide five of the six runs underneath it.
Several runs per scope is the normal case, not an edge case.

A secondary symptom: `msgid "Last run"` has no entry in
`weblate/locale/ru/LC_MESSAGES/django.po`, so the control that does exist is
labelled in English on a Russian interface.

## Confirmed decisions

Settled with the requester before planning; a builder must not revisit them:

1. **Split button, not a plain dropdown.** The newest report is the button;
   earlier runs are one click deeper. This is the vocabulary Weblate already
   ships for "one dominant action plus variants" - see the `Update` control in
   `weblate/templates/js/git-status.html:74-102` (`btn-group`, a primary
   action, a `dropdown-toggle` carrying a `visually-hidden` label, a
   `dropdown-menu`). Nothing about this control is invented.
2. **Every actor's runs are listed,** with the author named on rows that are
   not the viewer's own. On a shared component, "someone judged this
   yesterday" is the answer the producer came for. This replaces the current
   actor-scoped contract and requires rewriting two existing tests.
3. **The same control on the translation page,** replacing the lone muted
   :guilabel:`Last run` link in `weblate/templates/snippets/autoform.html:57-61`.
   The translation page is where runs actually accumulate (five on one
   translation today), so it needs the list most.

## Design

Surface mode is Operate: the producer is inside a task, so the control earns
familiarity rather than expression, and brand lives in precision.

- **Control.** In the :guilabel:`AI judge` card: `[ Latest report ][v]`.
  The button opens the newest run reachable from the page's scope; the caret
  opens a `dropdown-menu` headed :guilabel:`Earlier runs` whose rows are the
  remaining runs, newest first.
- **Row content, and nothing else.** Rows are data, not sentences, so no row
  needs a translated string: the run's timestamp in `DATETIME_FORMAT`, the
  scope label when it differs from the page being viewed, the author when it
  is not the viewer, and the status word when the run is not `completed`.
  A middle dot separates fragments, matching the report page's own metadata
  line; no icons, badges, or counts. The status word is
  `run.get_status_display`, the same rendering the report page's metadata
  line uses, so even it introduces no new msgid.
- **Hierarchy.** The timestamp leads every row, and a row whose scope is not
  the page being viewed carries that scope as the muted second line. One
  uniform rule: on a project page most runs share a scope, so leading with
  the scope label repeats one string down the menu and buries the only
  differentiator (measured: five earlier runs, two distinct scopes, four
  identical leads). See F7 in the review, which replaced the original
  scope-led rule.
- **States.**
  - No runs: the card renders exactly as today, :guilabel:`Breakdown by check`
    plus :guilabel:`Run the judge`.
  - One run: a plain button with no caret. A menu holding a single item is
    noise. When that run is not `completed`, its status word follows the
    label, muted - the one place a failed newest run is visible without
    opening a menu; with two or more runs the button stays blind by design
    and the status lives on the rows.
  - Two or more: the split button, and :guilabel:`Breakdown by check` is
    dropped - once a report exists it answers "what needs my decision here"
    that the per-check breakdown only approximates.
  - A `queued`, `running`, or `failed` run appears in the menu with its status
    word; the button is the newest run regardless of status.
- **Ceiling.** Ten rows. Older runs stay unreachable, which is the honest
  limit of a menu and the point where a paginated per-scope history page
  earns its own plan. No overflow affordance is added now, and no placeholder
  pretends one exists.
- **Motion and behaviour** come from Bootstrap: keyboard navigation,
  `aria-expanded`, and dismissal are the framework's, and no custom
  JavaScript is added.

## Constraints the implementation must not break

- **Query budget.** Page query count must not grow. `runs` is materialized
  once (`list(...)`), and `last_run`/`judge_last_run` are its first element -
  never `.first()` or `[0]` on the queryset, which would issue a second
  `LIMIT 1` query ahead of the menu's `LIMIT 10`. Scope membership is matched
  with `Cast("pk", CharField())` subqueries rather than materialized id
  lists, so a project page does not pay one query per nesting level. The
  existing pins in `weblate/trans/tests/test_judge_views.py` are relative
  `CaptureQueriesContext` comparisons, so the new assertion reads "a project
  page with N runs issues the same number of queries as with zero runs",
  captured on a page that renders the menu.
- **Permission gate unchanged.** `judge_queue_strip_context`
  (`weblate/trans/views/basic.py:774-848`) still returns `None` without
  `translation.auto` and `unit.review`, and `judge_run` still re-checks
  permission per run, so a listed run the viewer may not open answers 404
  rather than leaking. A language-restricted viewer can therefore see a row
  that 404s; this is a known, safe limitation, recorded rather than papered
  over.
- **Everything else in the card stays:** the three counts, `hand_off_ready`
  and its download CTA, :guilabel:`Run the judge`, and the report page itself
  are untouched.
- **i18n by hand.** New strings are seeded directly into
  `weblate/locale/ru/LC_MESSAGES/django.po`; `makemessages` is not run (it
  fuzzy-matches unrelated msgids and silently regresses correct
  translations). `msgfmt --check` must pass and the fuzzy count must stay at
  40.
- **Accessibility per `ACCESSIBILITY.md`:** the caret carries a
  `visually-hidden` name for its action, focus stays visible, state is never
  colour-only, and the dropdown is a real menu of links.
- Entity references are banned in templates by the `djlint-django` hook; use
  the literal separator character.

## Tasks

### Task 1 - Subtree-aware run lookup

In `weblate/trans/views/judge.py`, add `_scope_run_query(scope)` returning a `Q`
that matches runs on the scope itself and on everything inside it (translation:
itself; component: itself and its translations; project: itself, its components,
its translations; workspace: itself, its projects and their components and
translations), built from `Cast` subqueries. Add
`recent_judge_runs(scope, *, limit=10)` returning a **materialized list**,
newest first, with `select_related("actor")` - callers take `runs[0]`, so no
`.first()` or `[0]` on a queryset ever issues a second query. Remove
`last_judge_run` and its `basic.py` import: both call sites now take the
first element of the list, and keeping a helper that re-queries would
silently double the budget.

**Acceptance:** one query per call, and none added by template rendering; a
translation-scoped run is found from its component, project, and workspace; a
run in a sibling project is not.

### Task 2 - Card and translation-page context

`judge_queue_strip_context` gains `runs` (the materialized list) and derives
`last_run` as `runs[0] if runs else None`; `show_translation`
(`weblate/trans/views/basic.py:1001-1008`) gains `judge_runs` and keeps
`judge_last_run` as its first element. Update the docstring to state why
`breakdown_url` is now conditional.

**Acceptance:** project, component, workspace, and translation pages all carry
the list; the query-count pin lives in Task 7.

### Task 3 - The control

New `weblate/templates/snippets/judge-runs-menu.html`, included with `runs` and
`current_path`, implementing the split button, the header, the row hierarchy,
and the one-run degradation described above.

**Acceptance:** markup matches the `git-status.html` split-button pattern; with
one run no `dropdown-toggle` is rendered; no row renders an empty muted line.

### Task 4 - Wire both surfaces

Include it in `judge-readiness.html` in place of :guilabel:`Breakdown by check`
when `runs` is non-empty, and in `autoform.html` in place of the muted
:guilabel:`Last run` link. `autoform.html` is included by six templates
(`translation.html`, `component.html`, `project.html`, `workspace.html`,
`category.html`, `language-project.html`) and only `show_translation`
supplies `judge_runs`: the include must tolerate an absent or empty `runs`,
and the variable must never be populated on pages that already carry the
control in the :guilabel:`AI judge` card, or the menu renders twice.

**Acceptance:** the breakdown button is present with zero runs and absent with
one or more; the translation page reaches its own earlier runs; a page whose
view does not supply the list renders no menu and no error.

### Task 5 - Russian strings

Hand-seed :guilabel:`Latest report`, :guilabel:`Earlier runs`, and the
`visually-hidden` caret label.

**Acceptance:** `msgfmt --check` passes, fuzzy count still 40, the diff is pure
addition, and the strings resolve at runtime.

### Task 6 - Rewrite the two actor-contract tests

`test_last_run_shows_the_newest_own_launch_over_someone_elses` and
`test_last_run_hides_a_run_launched_by_someone_else_only`
(`weblate/trans/tests/test_judge_views.py:1529-1583`) encode the superseded
"my runs only" rule. Rewrite them to assert the new rule - another actor's
newer run is the latest report, and its row names that actor - rather than
deleting the coverage.

**Acceptance:** both tests describe the new contract in their names and bodies;
no test silently loses its assertion.

### Task 7 - New coverage

In `JudgeQueueStripViewTest`: a translation-scoped run surfaces on the
project, component, and workspace pages (the workspace strip comes from
`weblate/workspaces/views.py:309`; `attach_workspace()` scaffolding already
exists in this file); ordering is newest-first across components, with
`created` forced via `JudgeRun.objects.filter(...).update(created=...)` -
`created` is `auto_now_add` and `order_by("-created")` has no tie-breaker, so
sequential creation would be non-deterministic (the Task 6 tests show the
pattern); a run in another project does not leak; one run renders no dropdown
and, when it is not `completed`, carries its status word; a non-`completed`
row in the menu carries its status word; every row leads with its timestamp
while a child-scope row adds the muted scope line and an own-scope row does
not (F7); the ten-row ceiling holds; the breakdown button appears only with
zero runs; a project page with N runs issues the same number of queries as
with zero runs, captured in the existing relative `CaptureQueriesContext`
style on a page that renders the menu.

### Task 8 - Documentation

Correct both stale sentences in `docs/admin/checks.rst`: line 245 ("links to
the checks breakdown and to the launch's own run history" - the breakdown
link is now conditional, and the history link is the menu) and lines 266-267
("A finished component, project, or translation page shows a link to its own
last run"). Also correct the run-report bullet at `docs/changes.rst:14`,
which currently claims a page links to "its own last run".

**Acceptance:** no released changelog section is touched; entries stay
concise; no sentence still describes an exact-scope last-run link.

### Task 9 - Verification

Full judge suites (`test_judge_views.py`, `test_judge.py`,
`test_judge_round.py`), `prek run --files` on every touched file, a
`/plan-design-review` pass over the changed templates (the design-review
instrument earlier plans record in their verification tables, for example
`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md`), then one
batched browser pass on the development instance at desktop and mobile widths
covering `/projects/col4/` (six runs, the worst case),
`/projects/need-for-greed/` (one run, the degraded case), a project with
none, and the translation page. Fix everything the pass shows in one batch,
confirm once, stop.

## Out of scope

- A paginated per-scope run history page and any filtering or search over runs.
- Any change to the report page delivered by the Pareto plan.
- Deployment: the development container serves the main checkout by
  live reload, and production deployment remains a separate, explicitly
  approved step.

## Note

A working draft of Tasks 1-4 exists as `/tmp/judge-runs-menu-wip.patch` plus
`/tmp/judge-runs-menu.html.wip`; the working tree was reverted so that
implementation starts from an approved plan rather than from that draft.
