# Review of the judge run navigation plan

**Date:** 2026-09-03.
**Reviews:** `docs/llm-first/plans/2026-09-03-02-judge-run-navigation.md`
(status: proposed, not approved).
**Outcome:** approve with five amendments. Every load-bearing claim in the
plan was re-checked against the tree and holds; the design is sound and the
constraints are the right ones. The amendments are edits to task text - none
touches the design - so no re-review is needed once they are folded in.

## What was verified and held

Every file and line reference in the plan was opened and read:

| Claim | Result |
| --- | --- |
| `weblate/templates/snippets/judge-readiness.html:30-33` renders the :guilabel:`Last run` button only on an exact-scope, actor-filtered match | Holds. `last_judge_run` (`weblate/trans/views/judge.py:313-350`) filters `scope_type`/`scope_id` exactly and by `actor`, so translation-scoped launches are invisible from component and project pages. |
| The split-button vocabulary exists in `weblate/templates/js/git-status.html:74-102` | Holds: `btn-group`, primary anchor, `dropdown-toggle` with a `visually-hidden` span, `dropdown-menu`. (The existing span text "Toggle Dropdown" is hardcoded English; the plan's Task 5 seeds the new caret label into ru, which is strictly better.) |
| `weblate/templates/snippets/autoform.html:57-61` carries the lone muted :guilabel:`Last run` link | Holds, exact lines. |
| `judge_queue_strip_context` at `weblate/trans/views/basic.py:775-848`, gated on `translation.auto` + `unit.review` | Holds (definition at line 774). |
| `show_translation`'s judge block at `weblate/trans/views/basic.py:1001-1008` | Holds. |
| The two actor-contract tests at `weblate/trans/tests/test_judge_views.py:1529-1583` | Hold, names and bodies as quoted. The neighbouring no-run, exact-scope, and sibling-component tests still pass under the new contract, so Task 6's scope of exactly two rewrites is correct. |
| `last_judge_run` has no callers besides the two named | Holds (`basic.py:843`, `basic.py:1007`); dropping the `actor` argument is safe. |
| ru `django.po` fuzzy count is 40 and `msgid "Last run"` is absent | Holds (counted 40; no match). |
| `docs/changes.rst:14` claims a page links to "its own last run" | Holds, and the 2026.8.1 section is *Not yet released*, so editing it is allowed. |
| `JudgeRun` denormalizes `scope_label`/`scope_path` and indexes `(scope_type, scope_id, -created)` | Holds (`weblate/trans/models/judge.py:278-345`); the index supports the subtree query's per-branch scope filter. |
| The report page already separates metadata with a literal `·` and `date:"DATETIME_FORMAT"` | Holds (`weblate/templates/judge-run.html:17`); the planned row format follows an existing surface, and the djlint-django hook (`.pre-commit-config.yaml:92`) is real. |
| The `/tmp` WIP artifacts named in the Note | Exist. |

The measurement table (seven dev-instance runs; five of six CoL4 runs hidden
under one link) is arithmetically consistent. It is a runtime observation and
was not re-run.

## Findings

### F1 - Task 8 names one of the two stale sentences

`docs/admin/checks.rst` describes the old contract twice. The plan cites
line 245 ("links to the checks breakdown and to the launch's own run
history"). Lines 266-267 say it again, almost in the changelog's words: "A
finished component, project, or translation page shows a link to its own last
run." A builder executing Task 8 literally fixes the first sentence and
leaves the second, and the documentation keeps promising the exact-scope
link. Amend Task 8 to name both.

### F2 - Task 9's "design detector" is undefined

No tool, script, or skill by that name exists in the repository, in
`scripts/`, in `analysis/probes/`, or in any earlier plan; the only analogous
instrument on record is a `/plan-design-review` row in the verification
tables of earlier plans (for example
`docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md:1141`). As
written, the step is not reproducible by a builder who was not in the room.
Name the instrument (and how to run it) or drop the step.

### F3 - Task 7 under-covers what Tasks 1-4 add

- **The workspace page is never tested.** The strip reaches it from
  `weblate/workspaces/views.py:309`, a file the plan never mentions. The
  change is centralized, so no implementation task needs that file - but the
  page-level surfacing should be pinned once, in the same test class;
  `attach_workspace()` scaffolding already exists in
  `test_judge_views.py`.
- **The row-content branches have no test.** The status word on a
  non-`completed` run and the scope-label-leads vs timestamp-leads switch
  (the `current_path` comparison) are the branches most likely to regress
  silently, because a bug there renders a slightly wrong row rather than an
  error. Author naming is asserted only through Task 6's two rewrites, both
  on the component page.
- **Ordering tests need the forced-timestamp trick.** `created` is
  `auto_now_add` and `order_by("-created")` has no tie-breaker; the two
  Task 6 tests already demonstrate the `queryset.update(created=...)`
  pattern and explain why sequential creation is non-deterministic. Say so
  in Task 7 so the new newest-first test is deterministic from the start.

### F4 - Pin the query budget the way the suite pins it, and materialize the list

"Project page's query count is unchanged from `main`" (Task 2 acceptance) is
not automatable as written: the suite compares within one run, not across
checkouts. The existing pins are relative `CaptureQueriesContext`
comparisons (`test_hand_off_query_count_stays_bounded`,
`test_report_page_query_count_does_not_grow_with_row_count`); the same style
here is "a project page with N runs issues the same number of queries as
with zero runs". And the one-query invariant holds only if `runs` is
materialized before `last_run` is derived: `qs.first()` or `qs[0]` on an
unevaluated queryset issues its own `LIMIT 1` query, ahead of the menu's
`LIMIT 10`. Tasks 1-2 should say "a materialized list"
(`runs = list(...)`; `last_run = runs[0] if runs else None`), and Task 7's
count assertion should cover a page that actually renders the menu, not only
the helper.

### F5 - State the autoform constraint

`snippets/autoform.html` is included by six templates (`translation.html`,
`component.html`, `project.html`, `workspace.html`, `category.html`,
`language-project.html`), and only `show_translation` supplies `judge_runs`.
That asymmetry is exactly why the menu appears on the translation page
alone - but nothing in Tasks 3-4 says the include must tolerate an absent or
empty `runs`, and nothing warns against later populating the variable on
pages that already carry the control in the :guilabel:`AI judge` card, which
would render it twice. One sentence in Task 4 closes both.

### F6 (minor) - The button is blind to a failed newest run

"The button is the newest run regardless of status" puts a failed newest run
behind a control labelled :guilabel:`Latest report` with no status cue; the
status word appears only on menu rows. The failed run's own page explains
itself, so this is acceptable as designed - but if it bothers anyone, the
one-run degradation is the place to add the status word, not the split
button.

## What the plan gets right, briefly

- The problem is measured, not asserted, and the measurement is the decisive
  one: exact-scope matching hides five of six runs on the worst page.
- The three confirmed decisions are precisely the ones a builder would
  otherwise relitigate (control shape, actor visibility, the third surface);
  freezing them against the existing `git-status.html` pattern is the right
  amount of design.
- The permission analysis is honest: the 404-on-open limitation is recorded
  as a limitation instead of being designed away with a worse gate, and the
  per-run re-check stays untouched.
- i18n by hand with an explicit `msgfmt --check` and fuzzy-count pin matches
  how this repository's ru locale is actually maintained.
- The ceiling (ten rows, no overflow affordance) refuses to pretend a menu is
  a history page, and the out-of-scope section says where that page would
  earn its own plan.
- The Note about the reverted WIP keeps implementation honest: work starts
  from the approved plan, not from the draft.

## Disposition

Fold F1-F5 into the task texts (each is a one-line amendment), consider F6,
then approve. The amendments do not interact and do not change the design,
so no second review round is needed.
