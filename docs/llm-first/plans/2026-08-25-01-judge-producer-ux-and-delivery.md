# Make judge delivery and producer decisions legible

> **For Claude:** Use `executing-plans` task by task. Use
> `test-driven-development` for every behavior change and
> `verification-before-completion` before each commit.

**Date:** 2026-08-25. **Status:** awaiting implementation approval.

**Goal:** Let an unresolved `major` verdict ship, expose every judge severity in
native Weblate checks, and give the producer one coherent surface to launch the
judge, follow its phase, inspect repairs, and record a delivery decision.

This is the first of two plans. The deferred run-history and migration work is
in `docs/llm-first/plans/2026-08-25-02-judge-run-history-and-resolution-follow-up.md`.

## Decided behavior

- `critical` automatically holds a string.
- An unresolved `major` ships with `judge-flag` evidence.
- `minor` remains non-blocking but becomes visible as `judge-note`.
- Judge check IDs stay stable. Producer-facing names use the judge severity:
  `critical`, `major`, and `minor`.
- Judge check dismissal is disabled. Producer decisions use audited resolution.
- `accepted_as_is` ships a held string. `escalated` puts a `major` into
  `STATE_NEEDS_CHECKING`; a `critical` remains held.
- A resolution applies to the row returned by `collegium_verdict()`, the stable
  representative of the whole two-seat round.
- Every resolution transition writes an immutable Unit `Change` event. The
  representative `JudgeVerdict` stores the current resolution.
- Component, project, and workspace launchers open a prefilled AutoForm and its
  estimate. They never start a paid run directly.
- Live progress uses existing Celery PROGRESS result metadata. The numeric
  `TaskSerializer.progress` field stays an integer.

## State and request flow

```text
                         automatic verdict
              +-----------------------------------+
              |                                   |
              v                                   v
major -> STATE_TRANSLATED                  critical -> STATE_FUZZY
  | ships + judge-flag                       | held + judge-reject
  |                                           |
  | Needs a person                           | Ship it
  v                                           v
STATE_NEEDS_CHECKING + escalated       STATE_TRANSLATED + accepted_as_is
  | held + audited Change                     | audited Change
  | Ship it                                   |
  +--------------------> STATE_TRANSLATED <---+

request(expected_verdict_id, resolution, reason)
  -> permission + form validation
  -> transaction.atomic
  -> lock Unit, then representative JudgeVerdict
  -> recheck current target/context/round
  -> write resolution + immutable Change event
  -> apply state through Unit.translate(..., select_for_update=False)
  -> redirect back to the editor
```

## What already exists

- `state_for_verdict()` is the single severity-to-state seam. Reuse it.
- `JudgeVerdict` persists immutable seat opinions and current resolution fields.
- `collegium_verdict()`, `current_round()`, and `active_round()` define current
  and stale evidence. Do not add another verdict selector.
- Native failing-check pages, filters, and editor cards expose `judge-*` checks.
- AutoForm, scope preview, Celery progress, persistent task messages, and the JS
  poller already exist. Extend those paths.
- `TranslationStats.calculate_judge()` calculates target-fresh judge aggregates
  in one annotated query.
- `Change.old` and `Change.target` contain before/after translation text.

## NOT in scope

- Durable run reports, per-run cost, and report links. Plan 2 owns them.
- `JudgeRun` and `JudgeRunUnit`. Plan 2 introduces them before report UI.
- Releasing legacy production units already held at state 12. Plan 2 owns the
  guarded command and production inventory.
- A new judge `instruction` output field. Plan 2 owns it.
- Changing the two-seat union aggregation or buying a calibration run.
- Judge columns in the language table. The measured table already overflows.
- Project/workspace aggregate counts. Mixed source languages prevent one exact
  reproducible `Not reviewed` query. Those pages get controls only.
- Production deployment or paid live judge execution. Both need explicit approval.

## Task 1: Restore advisory shipping semantics

**Files:** `weblate/trans/models/judge.py`,
`weblate/trans/tests/test_judge.py`, and
`weblate/trans/tests/test_judge_autotranslate.py`.

Write failing tests first:

- `FLAG` maps to `STATE_TRANSLATED` and not to `FUZZY_STATES`.
- `REJECT` remains `STATE_FUZZY`.
- Under `WITHOUT_NEEDS_EDITING`, unresolved major ships and critical is blocked.
- `PASS` and `JUDGE_MAY_APPROVE` remain unchanged.

Change only the `FLAG` branch in `state_for_verdict()`. Remove the unused import.
Explain the measured cost model in the function comment.

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/checks/tests/test_judge.py -p no:randomly --no-cov -q
```

Commit: `fix(judge): let an advisory verdict ship`

## Task 2: Make severity vocabulary and native evidence consistent

This merges the old vocabulary and minor-check tasks because they touch the same
registry, filters, card, localization, and tests.

**Files:** `weblate/checks/judge.py`, `weblate/checks/defaults.py`,
`weblate/auth/permissions.py`, `weblate/trans/filter.py`,
`weblate/utils/search.py`, `weblate/utils/stats.py`,
`weblate/templates/snippets/judge-verdict.html`, existing judge/search/view and
permission tests, and `weblate/locale/ru/LC_MESSAGES/django.po`.

| Stable preset ID | Label | Query |
| --- | --- | --- |
| `judge-advisory` | Judge - major (ships) | `judge:flag` |
| `judge-held` | Judge - critical (held) | `judge:reject` |
| `judge-minor` | Judge - minor (nothing blocking) | `judge:minor` |
| `judge-pass` | Judge - nothing blocking | `judge:pass` |

`judge:pass` deliberately selects both `none` and `minor`.

| Stable check ID | Name | Consequence |
| --- | --- | --- |
| `judge-reject` | Judge - critical | held automatically |
| `judge-flag` | Judge - major | ships with evidence |
| `judge-note` | Judge - minor | ships; no action expected |

Tests first:

- Every preset query parses and selects the exact severity set.
- `JudgeNoteCheck` fires only for a current parsed `minor`.
- `judge-note` belongs to `JUDGE_CHECKS`, is not fed back to the judge, is not
  shown as deterministic evidence, and cannot be enforced.
- The pass card renders minor errors but no error list for `none`.
- Major and critical cards render different delivery badges.
- `unit.check` is denied for `JUDGE_CHECKS`; Dismiss is absent and POST refused.
- Existing non-judge, non-enforced checks remain dismissible.
- Russian UI renders every new name without English fallback.

Add `judge_severity` to `BaseJudgeCheck`, register `JudgeNoteCheck`, and add
`judge_minor` to the existing aggregate. Reject dismissal through `JUDGE_CHECKS`,
not a string-prefix test.

Commit: `feat(judge): expose one severity vocabulary`

## Task 3: Record producer decisions atomically

This merges resolution, critical override, major escalation, and immutable
resolution history. They are one state machine.

**Files:** `weblate/trans/models/judge.py`, `weblate/trans/forms.py`,
`weblate/trans/views/edit.py`, `weblate/urls.py`, `weblate/trans/actions.py`,
the existing change-history renderer, `weblate/templates/snippets/judge-verdict.html`,
`weblate/utils/search.py`, `weblate/utils/stats.py`, existing judge/search/stats/
change tests, localization, and a migration if Django detects a changed action choice.

One domain helper accepts Unit, expected representative verdict ID, actor,
resolution, and reason. Inside one transaction it:

1. Requires a non-blank reason and `unit.review`.
2. Locks Unit first, then the representative JudgeVerdict.
3. Recomputes the current context round and checks the expected representative.
4. Rejects stale, superseded, missing, and invalid transitions.
5. Updates current resolution without changing severity/errors.
6. Writes an immutable Unit Change with verdict ID, old/new resolution,
   old/new state, actor, and reason.
7. Applies state through `Unit.translate()` using the existing Unit lock.

Allowed transitions:

- unresolved `major` -> `escalated` + `STATE_NEEDS_CHECKING`;
- unresolved `critical` -> `escalated`, state remains fuzzy;
- unresolved or escalated held verdict -> `accepted_as_is` + translated;
- `sent_back` is not exposed;
- edit/re-judge naturally makes the old resolution stale.

Tests first cover permission, blank reason, invalid transition, stale target and
context, critical acceptance, major escalation, `escalated -> accepted_as_is`
with both Change events preserved, duplicate POST, concurrent actions, immutable
verdict evidence, and exact `judge:resolved`/`judge:escalated`/stats parity.

Commit: `feat(judge): record producer verdict decisions`

## Task 4: Replace the readiness card with a queue strip and safe launchers

**Files:** judge-readiness/component/project/workspace templates,
`weblate/trans/views/basic.py`, `weblate/trans/views/edit.py`,
`weblate/trans/autotranslate.py`, and existing judge view/form tests.

Component strip:

| Label | Count | Linked queue |
| --- | --- | --- |
| Needs a human | unresolved current critical + current escalated | `(judge:reject AND NOT judge:resolved) OR judge:escalated` |
| Not reviewed | `judge_total - judge_evaluated` | `NOT has:judge AND NOT language:<source> AND NOT state:read-only` |
| Last attempt returned nothing | `judge_unparsed` | `judge:unparsed` |

Always render `Breakdown by check` and `Run the judge` when configuration and
permissions permit. The run control opens a prefilled AutoForm and estimate; it
does not POST. Project and workspace render controls only. Add a first-class
closed `Project` match case to preview, POST, and `BatchAutoTranslate`.

Tests first:

- `accepted_as_is` removes a critical from Needs a human.
- `escalated` adds a major and holds its state.
- Editing/re-judging drops stale resolutions from current keys.
- Every number equals its linked row count.
- Source/read-only units are absent from count and query.
- Glossary, disabled/incomplete configuration, and missing permissions hide controls.
- Project/workspace show controls but no counts.
- Component/project/workspace bind the correct prefilled scope.
- The old card, Delivery, Primary action, and legend are absent.

After pytest, click every real control in a browser at 1440 px and a narrow viewport.

Commit: `feat(judge): add the producer queue strip`

## Task 5: Show reliable repair evidence

**Files:** `weblate/trans/models/judge.py`, `weblate/trans/views/edit.py`,
`weblate/templates/snippets/judge-verdict.html`, and judge view tests.

For an active attempt greater than zero, show attempt-0 errors and the previous
text only from a unique Change in this window:

```text
last attempt-0 timestamp
  < Change.timestamp with Change.target == repaired target
  < first attempt-1 timestamp
```

Zero or multiple candidates omit the comparison. Never guess. Tests cover exact,
missing, purged, ambiguous, plural, unresolved-repair, first-pass, and stale cases.

Commit: `feat(judge): show bounded repair evidence`

## Task 6: Use one summary and show live phases

**Files:** `weblate/trans/autotranslate.py`, `weblate/trans/tasks.py`,
`weblate/utils/celery.py`, `weblate/templates/message.html`,
`weblate/static/loader-bootstrap.js`, autoform template, and existing judge/
autotranslate/form tests.

Add a typed `JudgeSummary`. `AutoTranslate` produces it,
`BatchAutoTranslate` adds it, and one formatter builds completion copy. Buckets
include evaluated, nothing blocking, repaired and re-judged, major not fixed,
critical held, minor noted, unparsed, and cap remainder. `repaired and re-judged`
means retained final verdict `attempt > 0`; it does not claim severity improved.

PROGRESS result metadata carries:

```text
queued -> judging(current, total) -> repairing(current, total) -> done(summary)
```

Keep `progress` numeric. The poller reads phase from `data.result` before
completion and writes via `textContent`. Unknown phase is Queued or absent.

Tests cover single and batch scopes, cache-only work, cap, failure/warnings, no
repair, retained repair, unparsed output, and unchanged non-judge copy.
Browser smoke intercepts the task API through queued/judging/repairing/done and
checks visible text, monotonic bar, text-only rendering, transient 500 retry,
and no judge phase for non-judge tasks.

Commit: `feat(judge): show run phases and one summary`

## Task 7: Reconcile documentation, localization, and security claims

**Files:** `docs/admin/checks.rst`, `docs/changes.rst`, the judge vision,
superseded review-gate plan, measurement index,
`docs/security/threat-model.rst`, and Russian catalog.

Document that critical holds automatically until an authorized, atomic, audited
override; `unit.review` is the boundary; stale evidence is rejected; and
CSRF-protected POST is the only mutation. Record major as shipping evidence,
minor as non-blocking evidence, dismissal as disabled, and delivery and AI
evidence as separate axes.

Commit: `docs(judge): document producer verdict decisions`

## Test coverage map

```text
CODE PATHS                                      USER FLOWS
[+] state_for_verdict                          [+] Launch judge safely
  [TEST] pass/major/critical/unparsed             [TEST] component form
  [TEST] commit-policy gate                       [TEST] project/workspace form
[+] judge check projection                        [TEST] no direct paid POST
  [TEST] all severities + stale/current         [+] Follow one run
  [TEST] dismissal denied                          [PYTEST] phase metadata
[+] resolution domain helper                       [BROWSER] queued->judge->repair->done
  [TEST] permission/reason/transition            [+] Decide on a verdict
  [TEST] stale/double/concurrent                    [E2E] escalate major -> held
  [TEST] representative + immutable rows            [E2E] accept held -> ships
  [TEST] immutable Change history                   [E2E] decision history visible
[+] judge stats/search                          [+] Inspect repair
  [TEST] count/query parity                         [TEST] exact attempt window
  [TEST] resolved/escalated/current-only             [TEST] ambiguous fallback
[+] typed summary aggregation                        [BROWSER] plural comparison
  [TEST] single/batch/cache/cap/failure          [+] Read Russian UI
                                                    [TEST] translated copy
```

The live LLM is not invoked without separate paid-run approval.

## Failure modes

| Path | Failure | Coverage | Visible result |
| --- | --- | --- | --- |
| Resolution POST | target/verdict changes in another tab | stale/concurrency pytest | explicit conflict, no partial write |
| Resolution POST | retry/double submit | idempotency pytest | one decision and Change |
| Queue strip | count and query diverge | parity pytest | blocked from completion |
| Project launcher | direct paid start | view/browser test | impossible; estimate first |
| Repair card | missing/ambiguous history | fallback pytest | comparison omitted |
| Poller | transient API failure | browser interception | retry, current text retained |
| Batch summary | one translation fails | batch failure pytest | warning plus completed counts |
| Localization | missing Russian entry | locale render pytest | test fails before release |

## Performance review

- Strip values come from prefetched TranslationStats; no template queries.
- Add a query-count test for the resolution annotation and capture PostgreSQL
  `EXPLAIN (ANALYZE, BUFFERS)` on a production-sized local fixture.
- Resolution locks one Unit and one representative verdict only.
- Repair history performs one bounded Change query on editor load.
- Project/workspace preview reuses the existing capped batch scan.

## Parallelization

| Lane | Work | Depends on |
| --- | --- | --- |
| A | Tasks 1-3, then Task 4 | sequential shared models/stats |
| B | Task 5 | Task 2 |
| C | Task 6 | Task 1 |
| D | Task 7 | Tasks 1-6 contracts frozen |

Lanes B and C may run in parallel after Task 2. Merge before Task 7 and browser QA.

## Final verification

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/checks/tests/test_judge.py \
  weblate/utils/tests/test_search.py \
  weblate/utils/tests/test_stats.py -p no:randomly --no-cov -q
uv run prek run ruff-check ruff-format djlint-django rumdl --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Run browser scenarios against the isolated worktree stack. This section does not
authorize a paid real judge run or production rollout.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | NOT RUN | - |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ERROR | Timed out after 5 minutes; no findings incorporated |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 19 issues resolved, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT RUN | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | - |

**VERDICT:** ENG CLEARED - Plan 1 is ready for implementation approval.

NO UNRESOLVED DECISIONS
