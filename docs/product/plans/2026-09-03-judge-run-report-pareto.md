# Judge run report Pareto reduction implementation plan

**Goal:** Turn the judge run report (`/judge-runs/<uuid>/`) into a page that
answers one producer question - "what do I do now?" - in its first screen:
how many strings are held back from release, which defect class covers most
of them, and one link per group into the editor where the triage actions
already live.

**Architecture:** No new models, no migrations, no new views or URLs. The
change is confined to `weblate/trans/views/judge.py` (context) and
`weblate/templates/judge-run.html` (layout), plus Russian wording in
`weblate/locale/ru/LC_MESSAGES/django.po`. Every action link targets an
existing view: `translate` for a translation-scoped run, `search` for a
component/project/workspace-scoped run. The producer decisions themselves
(accept the judge-guided fix, keep as is, send back to queue, fix by hand)
stay where `docs/llm-first/plans/2026-09-02-producer-editor-pareto.md` put
them - on the translate page verdict card. This page only routes to them.

**Tech stack:** Python 3.14, Django templates (Bootstrap 5), gettext
(`weblate/locale/ru`), pytest, Docker Compose dev instance on port 3001.

**Evidence:** live run `f9423ea4-f364-40d6-93cd-4f8b048fed56`
(Need for Greed/Orders - Russian, filter `NOT has:judge`, 102 rows, 58
minutes), read on 2026-09-03 through `weblate shell` in
`dev-docker-weblate-1`.

**Status:** proposed, not approved, nothing implemented.

---

## What the run actually contains

| Outcome | Repair status | Rows | Producer decision available |
| --- | --- | --- | --- |
| `critical` | `no-candidate` | 8 | fix by hand, keep as is, send back to queue |
| `major` | `no-candidate` | 11 | fix by hand, keep as is, send back to queue |
| `minor` | `not-attempted` | 10 | manual edit only |
| `passed` | `not-attempted` | 73 | none |

`repaired`, `rolled-back`, `unparsed`, `stale-conflict`, `cached`,
`accepted-as-is` and `escalated` are all zero on this run.

Primary-error categories over the 29 flagged rows:

| Category | Rows | Worst severity |
| --- | --- | --- |
| `mistranslation` | 19 | critical |
| `fluency` | 7 | major |
| `terminology` | 2 | major |
| `omission` | 1 | minor |

## Findings that change the earlier proposal

1. **`minor` has no resolution flow.** `minor` maps to
   `JudgeVerdict.Verdict.PASS` (`_SEVERITY_VERDICT`), and
   `ALLOWED_RESOLUTION_TRANSITIONS` only carries `REJECT` and `FLAG`. A
   "keep as is" grouping for minor rows would offer an action that does not
   exist. Minor is "read it if you care", nothing more.
2. **"Will not ship" is project-policy dependent.** `critical` lands on
   `STATE_FUZZY`; that is excluded from export only under
   `CommitPolicyChoices.WITHOUT_NEEDS_EDITING` (20) or `APPROVED_ONLY`.
   `need-for-greed` is 20, so the claim is true there, but the default is
   `ALL`, where a critical still ships. The wording must be driven by
   `Unit.is_blocked_by_commit_policy`'s condition, not hardcoded.
3. **Every flagged row on this run is `no-candidate`.** There is no stored
   fix to accept, so an "Accept fix" group would render empty here. The
   group must exist (a `candidate-stored` run is normal) but must not be
   the headline.
4. **Action links can be exact per scope type.** `translate`/`browse` accept
   only `Translation`/`ProjectLanguage`/`CategoryLanguage`
   (`weblate/trans/views/edit.py:1457`), but the site-wide `search` view
   accepts `Component`, `Project`, `Workspace` and `Translation`
   (`weblate/trans/views/search.py:169-183`). Both take `?q=`, so every
   scope type gets a working link.
5. **Link queries are live, not run snapshots.** `judge:reject` reads
   `judge_active_severity` for the text stored now. A producer who already
   fixed three strings sees five, not eight. `id:141811,141817,...` would be
   exact (comma lists are supported, `weblate/utils/search.py:296-299`) but
   grows unbounded with run size (`cap` is 2000 on this run). Decision: use
   the live `judge:*` vocabulary - the same one `_judge_hand_off_blocked`
   uses - and label the buttons in live terms ("still blocking"), never as
   "the 8 strings from this run".

## Constraints the implementation must not break

| Constraint | Source |
| --- | --- |
| Every header count equals its own drill-down row count | `test_every_outcome_count_equals_its_report_local_row_count` |
| No cost figure anywhere on the page | `test_no_query_path_emits_a_cost_figure` |
| Query count does not grow with row count | `test_report_page_query_count_does_not_grow_with_row_count` |
| Unauthorized scope leaks no counts and no scope label | `test_unauthorized_private_scope_returns_404_with_no_count_leakage` |
| Unknown `?outcome=` is a 404 | `test_unknown_outcome_filter_is_rejected` |
| Deleted units and drifted targets still render | `test_current_and_stale_and_deleted_units_render_safely` |
| Straight quotes and plain hyphens only in new strings | user standing rule |
| Russian wording is hand-seeded, never a repo-wide `makemessages` | prior fuzzy-drift incident on this po file |

## Tasks

### Task 1 - Triage context in the view

`weblate/trans/views/judge.py`

Add a `triage` dict to the render context, computed from the counts already
gathered (no extra queries):

- `blocking` - `critical` rows whose verdict has no `accepted_as_is`
  resolution
- `major`, `minor`, `passed`
- `candidates` - rows with `repair_status = candidate-stored`
- `needs_recheck` - `unparsed` plus `stale-conflict`
- `total_actionable` - `blocking + major + minor + needs_recheck`
- `blocks_release` - whether the scope's project `commit_policy` excludes
  `FUZZY_STATES`, so the template can say "will not ship" only when that is
  true

**Acceptance:** the numbers for run `f9423ea4` are
`blocking=8, major=11, minor=10, passed=73, candidates=0, needs_recheck=0`,
and `blocks_release` is `True` for `need-for-greed` and `False` for a project
left at `CommitPolicyChoices.ALL`.

### Task 2 - Category Pareto aggregation

`weblate/trans/views/judge.py`

One query over the actionable rows only
(`outcome in {critical, major, minor}`), selecting
`verdict__errors` and `verdict__max_severity`; in Python, take each row's
primary error (first error at the verdict's own `max_severity`, mirroring
`JudgeVerdict.primary_error`), and group by `category` into
`[{category, count, worst, review_url}]` ordered by count descending.
`triage.top_category` is the first entry.

**Acceptance:** for run `f9423ea4` the list is
`mistranslation 19 (critical), fluency 7 (major), terminology 2 (major),
omission 1 (minor)`; the page's query count is unchanged between a 5-row and
a 45-row run (existing test still passes).

### Task 3 - Scope-aware review URLs

`weblate/trans/views/judge.py`

One helper mapping a `judge:*` query to a URL for the run's scope:
`translate` for `Translation`, `search` for `Component`/`Project`/
`Workspace`. Context gains `blocking_review_url`
(`judge:reject AND NOT judge:resolved`), `flagged_review_url`
(`judge:reject OR judge:flag`), `recheck_review_url`
(`judge:unparsed OR judge:stale`), and one `review_url` per category row
(the category's own severity query; the category itself is not a search
field, so the link narrows by severity only and the table states that).

**Acceptance:** for this translation-scoped run
`blocking_review_url` resolves to
`/translate/need-for-greed/orders/ru/?q=...` and returns 8 strings; a
component-scoped run produces a `/search/...` URL that returns 200.

### Task 4 - Default the string list to the actionable rows

`weblate/trans/views/judge.py`

- Introduce an `actionable` bucket (`critical`, `major`, `minor`,
  `unparsed`, `stale-conflict`) and make it the default when no `?outcome=`
  is given, ordered critical to minor.
- Keep every existing bucket key valid as an explicit `?outcome=` value, so
  no existing link or test URL breaks.
- Render only non-zero buckets as filter buttons; `matched`, `checked` and
  `cached` move into one plain-text technical line.

**Acceptance:** `/judge-runs/f9423ea4/` shows 29 rows by default;
`?outcome=passed` still shows 73; `?outcome=matched` still shows 102; the
count-equals-drill-down test passes for `actionable` too.

### Task 5 - Hero "What to do" card

`weblate/templates/judge-run.html`

First card on the page. When `triage.blocking` is non-zero and
`triage.blocks_release` is true: "N strings will not ship until you fix
them", the top category sentence, and one primary button to
`blocking_review_url`. When `blocks_release` is false: "N strings are
rejected and need a fix before release". When there is no blocking row but
flagged rows exist: "Nothing is blocked from shipping" plus the flagged
button. When `triage.candidates` is non-zero: a second button "Review N
suggested fixes". When nothing is actionable: one sentence plus the passed
count. Run metadata (scope, filter, status, start, duration) collapses to a
single line above it.

**Acceptance:** the live page's first screen states the 8, names
`mistranslation`, and its primary button lands on 8 strings in the editor.

### Task 6 - Pareto table

`weblate/templates/judge-run.html`

Problem / Strings / Worst / link, one row per category, count descending.

**Acceptance:** four rows in the documented order on the live run.

### Task 7 - Rewrite the string table

`weblate/templates/judge-run.html`

Columns: Source, Translation, Problem, Action.

- Source and translation excerpts come from the row's unit; a target that no
  longer matches `input_target` is marked "changed since this run".
- Problem shows `primary_error.category` and `description`; rows without a
  verdict fall back to an explicit sentence per outcome (passed, skipped,
  unparsed, stale-conflict) instead of a bare enum label.
- Action is a single link into the editor, labelled by what the producer
  will do there: "Fix and re-check" (critical), "Review" (major, minor),
  "Re-check" (unparsed, stale-conflict), "Review the suggested fix"
  (`candidate-stored`), "See the applied fix" (`applied`), "nothing to do"
  (deleted unit).
- Excerpts are truncated in the template, and the row loop adds no query
  (`select_related` already covers `unit`, `unit__translation`, `verdict`).

**Acceptance:** a producer can read the run's 8 critical rows and know what
is wrong with each without opening a single string; the deleted-unit and
drifted-target tests still pass.

### Task 8 - Russian wording

`weblate/locale/ru/LC_MESSAGES/django.po`, then
`./rundev.sh compilemessages`

Hand-add a `msgid`/`msgstr` pair for every new string. No repo-wide
`makemessages`: this file already carries unrelated drift, and a broad run
fuzzy-matches unrelated entries and silently regresses them.

**Acceptance:** `grep -c '^#, fuzzy'` on the po file is unchanged; the live
page renders Russian for the new card.

### Task 9 - Tests

`weblate/trans/tests/test_judge_views.py`, in `JudgeRunReportViewTest`

- `actionable` is the default bucket and its count equals its rows
- every legacy `?outcome=` key still resolves
- zero-count buckets render no button; `matched`/`checked`/`cached` render
  as text
- triage counts and `blocks_release` for both commit policies
- category list order, counts and worst severity
- each review URL resolves and is scope-correct for translation and
  component scopes
- a `candidate-stored` row renders the "Review the suggested fix" action
- a row with no verdict renders an explicit sentence, not an empty cell
- the existing cost-figure and query-count assertions are extended to the
  new default view

**Acceptance:** the whole class passes plus the existing judge suites
(`test_judge_views.py`, `test_judge.py`, `test_judge_round.py`).

### Task 10 - Documentation

- `docs/changes.rst`: one entry in the unreleased section for the reworked
  judge run report.
- No new admin page: the report is described where the judge run is
  documented today.

**Acceptance:** `uv run prek run --all-files` is clean on the touched files.

### Task 11 - Verification

- `uv run pytest weblate/trans/tests/test_judge_views.py` with an isolated
  `CI_DB_NAME`.
- Browser pass on `http://localhost:3001/judge-runs/f9423ea4-f364-40d6-93cd-4f8b048fed56/`:
  first screen states the 8 blocking strings, the primary button lands on 8
  strings in the editor, the Pareto table matches the documented numbers,
  and the default list shows 29 rows.

**Acceptance:** both green, with the browser observation recorded.

## Out of scope

Batch triage from the report, per-row accept/resolve controls on the report
(they stay on the verdict card), any change to judge prompts, severities,
verdict storage or the search vocabulary, exporting the report, cost
figures, and any deployment. Deployment to dev or production is a separate,
separately approved step.
