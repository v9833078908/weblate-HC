# Persist judge runs, report them, and prepare production migration

> **For Claude:** Use `executing-plans` task by task. Use
> `test-driven-development` and `verification-before-completion`.

**Date:** 2026-08-25. **Status:** deferred until
`docs/llm-first/plans/2026-08-25-01-judge-producer-ux-and-delivery.md` is
implemented and verified.

**Goal:** Give every component/project/workspace judge launch one durable
identity, record a complete per-unit outcome including cached evidence, link the
finished task to an exact report, add machine-only repair instructions, and
release legacy advisory holds through a guarded operator command.

## Prerequisite contract from Plan 1

Plan 1 owns delivery mapping, severity vocabulary, minor evidence, resolution
and override actions, queue strip, safe prefilled launchers, repair-card evidence,
typed live summary, phase progress, localization, and threat-model wording.
This plan does not reimplement any of those seams.

## Why `JudgeVerdict.run_id` is not a run model

Current code generates `run_id` inside `run_judge_batch()`. A project or
workspace run calls that function once per Translation, so one user launch
produces several IDs. Cached units return an older verdict and write no row for
the new ID. A report grouped only by the current field therefore loses cached
units, splits broad scopes, and cannot persist launcher scope, actor, task status,
filter, duration, or failure.

```text
Producer launch
  -> one JudgeRun
     -> Translation A batch -> new/cached JudgeRunUnit rows -> JudgeVerdict refs
     -> Translation B batch -> new/cached JudgeRunUnit rows -> JudgeVerdict refs
     -> ...
  -> one completion summary
  -> one report URL
```

## What already exists

- `BatchAutoTranslate` already owns the complete permission-filtered scope and
  cap across Translations.
- `JudgeVerdict` remains immutable model evidence by seat and attempt.
- The Plan 1 `JudgeSummary` is the live summary source.
- Celery task messages already carry object URL/label and JSON result metadata.
- Native check and editor pages remain the current-evidence surfaces.
- `LLMUsageLog` has operation/project/model/token/cost evidence but no run ID.
- Existing guarded judge management commands define command style and tests.

## NOT in scope

- Actual per-run cost. `LLMUsageLog` has no run ID; timestamp attribution is not
  accepted. Adding that relation needs a separate product decision.
- A live global queue of all runs. This plan adds exact reports and a last-run
  link, not an operations dashboard.
- A generated report artifact or download. The report is a database-backed page.
- Changing the default run filter.
- A paid live A/B eval for the new instruction field. The product owner chose
  schema/parser pytest only. This leaves model-output quality unmeasured and is
  recorded as an accepted limitation.
- Production deployment or command execution. Rollout needs explicit approval.

## Task 1: Add durable run and per-unit outcome models

**Files:** `weblate/trans/models/judge.py`, model exports/admin as required, a
migration, `weblate/trans/judge_loop.py`, `weblate/trans/autotranslate.py`, and
existing judge loop/model/autotranslate tests.

### `JudgeRun`

Use one row per producer launch with:

- UUID primary public ID;
- actor, Celery task ID, created/started/finished timestamps;
- closed `scope_type` plus `scope_id`, `scope_label`, and `scope_path` snapshot;
- requested query/mode and cap;
- status: queued/running/completed/failed;
- typed summary snapshot and failure/warnings;
- database indexes for actor/time, scope/time, and status/time.

The scope resolver accepts only Translation, Component, Project, and Workspace.
It rechecks current access when rendering. Do not use an unrestricted content
model or accept a user-supplied model name.

### `JudgeRunUnit`

Use one row per matched Unit in a run with a unique `(run, unit)` constraint:

- Translation/component/project IDs for filtering and permission joins;
- input target/context hashes;
- final representative JudgeVerdict reference, including cached old evidence;
- outcome: passed, minor, major, critical, unparsed, skipped, stale-conflict;
- repair status: not-attempted, no-candidate, applied, rolled-back;
- initial/final severity, attempt count, before/after target plural snapshots;
- whether evidence was cached and whether final state projection succeeded.

A cached verdict is referenced, not copied. `JudgeVerdict.run_id` keeps its
original model-call identity. `JudgeRunUnit.run` records participation in the
new producer launch.

### Execution boundary

Create one `JudgeRun` at the `BatchAutoTranslate` boundary and pass it through
every child `AutoTranslate` and `run_judge_batch()` call. Record every matched
unit, including cached, unparsed, skipped by permission, cap remainder, and
stale conflicts. Finalize status and summary in `finally`; a worker exception
must leave a failed run, not an eternal running row.

### Tests first

- One component/project/workspace launch creates exactly one run.
- Two Translations share that run.
- Cached and newly judged units both create one `JudgeRunUnit` and remain
  distinguishable.
- All-cached run has a reportable non-empty scope and zero paid calls.
- Partial seat failure, all-unparsed, repair rollback, no repair candidate,
  target race, cap, permission skip, and task exception produce explicit outcomes.
- Retry cannot duplicate `(run, unit)` rows or double-finalize the run.
- Actor and scope cannot be forged through request data.
- Deleting a Unit follows the chosen retention rule explicitly; no dangling
  verdict reference crashes reports.

Commit: `feat(judge): persist complete judge run outcomes`

## Task 2: Build the run report page

**Files:** new `weblate/trans/views/judge.py`, new
`weblate/templates/judge-run.html`, `weblate/urls.py`, the Plan 1 queue strip,
and judge view tests.

Address the page only by `JudgeRun` UUID. Resolve its closed scope and return
404 when the actor cannot currently access it. Never infer permission from the
stored actor alone.

Report header:

- scope, filter, actor, started/finished/duration, status;
- checked/matched/cached/skipped counts;
- repaired, rolled back, minor noted, major not fixed, critical held, unparsed,
  stale-conflict, accepted-as-is, and escalated counts;
- no cost figure.

Each count links to a paginated report-local outcome view such as
`?outcome=critical`, backed by `JudgeRunUnit`. Do not add a global search parser
key solely for this page. Rows link to the current editor and clearly mark when
current text no longer matches the run snapshot.

The component strip may show `Last run`. Project/workspace pages may show the
last run for their exact scope, but do not aggregate current judge counts.

Tests first:

- Every outcome count equals its report-local row count.
- Cached evidence appears once and is labeled cached.
- One unit in an older and newer run appears only in the requested report.
- Current/stale/deleted units render safely.
- Empty, queued, running, completed, and failed runs render explicit states.
- Unauthorized private scope returns 404 with no count leakage.
- Pagination and outcome filter remain bounded on large fixtures.
- No query path emits a cost figure.

Commit: `feat(judge): report one durable judge run`

## Task 3: Link task completion and persistent alerts to the report

**Files:** `weblate/trans/tasks.py`, `weblate/templates/message.html`,
`weblate/static/loader-bootstrap.js`, existing task serializer only if its schema
needs documentation, and autotranslate/task/browser tests.

The final task result carries `report_url` beside `message`; non-judge tasks do
not. Add a dedicated actions slot in the message template. The poller creates an
anchor only from the server-provided URL and uses text nodes for labels. Keep the
existing redirect `result.url` behavior separate.

The server-rendered persistent task message already has object URL/label; render
that label as the return link. On completion, add the exact report button. A page
loaded after task completion must derive the report URL from durable `JudgeRun`,
not only from expiring Celery result metadata.

Tests first:

- Judge task result and persisted message point to the same run UUID.
- Non-judge tasks have no report action.
- Navigation away and reload while running retains the object link, phase, and
  later report link.
- Reload after completion still shows a durable report link while the run is in
  report retention.
- Missing/deleted scope produces a safe unavailable state.
- URL and label rendering cannot inject HTML.
- Summary values equal `JudgeRun` and report values.

Browser smoke clicks the real report button and checks it opens the exact run.

Commit: `feat(judge): link completed tasks to durable reports`

## Task 4: Add the guarded legacy advisory-hold command

**Files:** new
`weblate/trans/management/commands/judge_release_advisory_holds.py`, existing
judge command tests, and operator documentation.

Dry-run by default. Require `--write` and a component or project scope. A unit
is writable only when all conditions hold:

1. current state is `STATE_NEEDS_CHECKING`;
2. current target/context has a fresh parsed representative `major` verdict;
3. the newest relevant Unit Change proves the automatic judge projection moved
   the state to 12 and no later actor changed it;
4. no deterministic enforced check currently fails;
5. no current escalation resolution exists.

List writable and needs-review buckets per component/language with stable IDs.
Also inventory old dismissed judge checks because Plan 1 disables new dismissal;
do not silently undismiss them. Anything ambiguous stays untouched. The fallback
is re-judging, not widening the predicate.

Tests first cover every condition independently, combined success, idempotent
write, dry-run immutability, scope requirement, disappeared units, and concurrent
state change between selection and write.

Commit: `feat(judge): guard legacy advisory hold release`

## Task 5: Add machine-only judge repair instructions

**Files:** `weblate/trans/judge.py`, `weblate/trans/models/judge.py`, a migration,
`weblate/checks/base.py` or the smallest existing check-description seam,
`weblate/checks/judge.py`, `weblate/machinery/llm.py`, judge parser/loop/check
tests, and judge documentation.

Add required response key `instruction`: short imperative text when errors are
non-empty, empty otherwise. Update strict schema properties/required keys,
parser exact-key validation, result dataclass, persistence, and admin/audit
visibility.

Do not append instruction to `describe_latest_verdict()` or any human-facing
`Check.get_description()`. Add one machine-description hook whose default
returns the existing human description. `BaseJudgeCheck` overrides it to append
the active instruction only for the LLM machinery failing-check context.

The fixer still runs only for major and critical. Minor, none, and unparsed make
no repair request. Bound instruction length in the response schema and normalize
it like other prompt text.

Tests first:

- Missing, extra, non-string, too-long, wrongly empty, and unexpectedly non-empty
  instruction replies become unparsed.
- Persisted instruction remains absent from editor/check descriptions.
- Machine description contains the instruction exactly once for major/critical.
- Minor/none/unparsed trigger zero repair calls.
- Major/critical trigger no more than configured repair budget.
- Repair rollback restores exact target/state and records `rolled-back` in the
  run outcome.
- Prompt assembly treats instruction as data and does not render markup.

Per product-owner decision, verification is schema/parser pytest only. No live
paired LLM eval is authorized by this plan. Record this limitation in the
measurement index; do not claim model-quality equivalence.

Commit: `feat(judge): send machine-only repair instructions`

## Task 6: Documentation and final verification

Update judge admin docs, changes, run/report behavior, operator command docs,
and security text for stored run reports and permission-checked access. Keep
production rollout explicitly separate.

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/trans/tests/test_commands.py \
  weblate/checks/tests/test_judge.py \
  weblate/utils/tests/test_search.py \
  weblate/utils/tests/test_stats.py -p no:randomly --no-cov -q
uv run prek run ruff-check ruff-format djlint-django rumdl --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Browser verification:

1. Launch component, project, and workspace runs and confirm one report per launch.
2. Confirm cached and newly judged units both appear and counts equal the alert.
3. Navigate away, reload during and after completion, and click the report link.
4. Verify report outcome filters, pagination, stale target labels, 404 access, and
   no cost claim.
5. Run the legacy command without `--write`; verify no database writes.
6. Verify instruction is present in machine context and absent from producer UI
   using mocks only.

## Test coverage map

```text
CODE PATHS                                     USER FLOWS
[+] JudgeRun lifecycle                        [+] One launch -> one report
  [TEST] queued/running/completed/failed         [E2E] component/project/workspace
  [TEST] retry/finally/idempotency                [E2E] cached + fresh units
[+] JudgeRunUnit outcome                       [+] Persistent task navigation
  [TEST] all outcomes + snapshots                [BROWSER] leave/reload/finish/click
  [TEST] cached verdict reference              [+] Read report
[+] report queryset                              [TEST] auth 404 and outcome parity
  [TEST] pagination/filter/stale/deleted          [TEST] large page query bound
[+] legacy command                            [+] Operator migration
  [TEST] each guard + dry-run/write               [TEST] dry-run inventory first
[+] instruction schema                         [+] Fixer context
  [TEST] strict parser boundaries                 [TEST] machine-only description
  [TEST] no minor repair                          [GAP ACCEPTED] no live LLM eval
```

## Failure modes

| Path | Failure | Coverage | Visible result |
| --- | --- | --- | --- |
| Run creation | worker dies mid-run | lifecycle/finally tests | failed or running-with-timeout state, never fake complete |
| Cached unit | no new JudgeVerdict row | cache outcome test | report labels reused evidence |
| Report auth | scope permission revoked | 404 test | no count leakage |
| Report page | unit deleted/stale | stale/deleted tests | explicit unavailable/stale row |
| Task result expires | Celery metadata gone | reload-after-completion test | durable JudgeRun link remains |
| Migration | provenance ambiguous | each-guard test | listed needs-review, never written |
| Instruction | model returns malformed key | parser tests | unparsed, no favorable verdict |
| Instruction quality | imperative text degrades verdicts | no live eval by decision | unmeasured accepted risk |

## Performance review

- Report queries aggregate and paginate `JudgeRunUnit`; never group all
  `JudgeVerdict` rows in Python.
- Add indexes for `(run, outcome)`, `(run, repair_status)`, and `(run, unit)`.
- Use `select_related` for unit/translation/component/project and assert a bounded
  query count for a full report page.
- `JudgeRunUnit` adds one row per matched unit per launch, less than existing
  two-seat verdict volume. Document retention before production rollout.
- Avoid copying cached JudgeVerdict rows. Store references and run outcome only.

## Parallelization

| Lane | Work | Depends on |
| --- | --- | --- |
| A | Task 1 model/lifecycle | Plan 1 |
| B | Task 4 command | Plan 1; independent of Task 1 |
| C | Task 5 instruction schema | Plan 1; independent of Task 1 |
| D | Tasks 2-3 report/alerts | Task 1 |
| E | Task 6 docs/QA | Tasks 1-5 |

Lanes A, B, and C can run in parallel worktrees only if each uses an isolated
database and no shared dev-docker ports. D waits for A. E waits for all lanes.

## Production rollout

**Not authorized by this plan.** After separate approval:

1. Deploy code.
2. Inspect JudgeRun retention/query performance on a small trial.
3. Run legacy command dry-run per project and review both buckets.
4. Run `--write` only for approved scopes.
5. Decide separately whether to buy any real judge rerun or instruction eval.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | NOT RUN | - |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ERROR | Timed out after 5 minutes; no findings incorporated |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 19 issues resolved, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT RUN | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | - |

**VERDICT:** ENG CLEARED - Plan 2 is coherent and intentionally deferred.

NO UNRESOLVED DECISIONS
