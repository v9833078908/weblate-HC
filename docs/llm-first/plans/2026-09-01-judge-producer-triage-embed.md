# Producer triage with a stored judge repair candidate implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Make the embedded judge card a complete producer triage surface:
every fresh critical verdict arrives with one persisted judge-guided repair
candidate that the producer can preview and apply in one click, and the
accepted text stays blocked until a fresh one-unit judge run completes.

**Architecture:** Keep the existing translate page, verdict card, native
`Suggestion`, `JudgeRun`, and `auto_translate(mode="judge")` pipeline. Replace
the critical branch of the current immediate repair loop with a persisted
`Suggestion` bound to the representative verdict and target/context hashes.
Major and max-length repair behavior remains unchanged. Every candidate
acceptance path checks freshness under the Unit lock, writes the candidate as
`STATE_FUZZY`, and queues a current-text-only one-unit re-check.

**Tech stack:** Python 3.14, Django/PostgreSQL, Celery, Django templates
(Bootstrap/jQuery), pytest, Docker Compose dev instance.

**Research:**
`docs/llm-first/research/2026-09-01-judge-producer-triage-embed-research.md`.
Product decision 2026-09-01 supersedes that document's sequencing: Solution 3
is a first-increment requirement. The smallest safe increment therefore
contains Solution 1, the one-unit re-check substrate from Solution 2, and the
stored-candidate lifecycle from Solution 3.

**Status:** approved 2026-09-01, ready for implementation. No new page and no
new domain model.
One migration is expected for the `candidate-stored` audit choice on the
existing `JudgeRunUnit.repair_status` field.

**Out of scope:** multiple simultaneous candidates, score/ranking UI, judge
prompt/schema changes, automatic acceptance, and a new export pipeline. The
previously rejected dedicated "Edit manually" button remains out of scope; the
normal editor already covers manual fixes. A manual save remains stale until
the producer explicitly re-checks it.

---

## Invariants

1. Generation never mutates the Unit target.
2. A candidate is active only for the current unresolved critical verdict with
   matching target/context hashes.
3. Verdict card, suggestions UI, API, votes, bulk operations, and internal
   callers enforce one judge-specific acceptance guard.
4. Acceptance writes `STATE_FUZZY`; only a fresh re-check may make it shippable.
5. Generate, regenerate, accept, and re-check require `unit.review` and
   `translation.auto`; `suggestion.accept` alone is insufficient.
6. Stale/context-drift cards show only "Re-check this string".
7. Metadata contains IDs, hashes, route identity, and schema version only. It
   never contains prompts, responses, credentials, or endpoint URLs.
8. One verdict has at most one active candidate and one queued/running re-check.
9. Normal batch major/max-length automatic repair behavior is unchanged. A
   producer one-unit re-check is evidence-only: it projects pass/major directly
   and never mutates an accepted target through the major repair loop.
10. Cached stats may display counts but may not enable hand-off; the CTA needs a
    fresh target/context readiness check.

## Cost contract

| Path | Baseline paid calls |
| --- | ---: |
| Current immediate critical repair | 2 judge + 1 repair MT + 2 re-judge = 5 |
| New critical before producer action | 2 judge + 1 repair MT = 3 |
| Producer keeps current text | 3 total |
| Producer applies candidate; re-check is pass/major | 3 + 2 judge = 5 total |
| Producer applies candidate; re-check is critical | 3 + 2 judge + 1 new repair MT = 6 total |
| Producer asks for another candidate | +1 repair MT |

The saving comes from deferring the second judge round until acceptance. Keeping
the current text avoids two judge calls. An accepted candidate that passes or
returns admissible major costs the same five baseline calls as today's automatic
repair, but gives the producer control. A repeated critical costs one additional
repair MT call to persist its new candidate.
The candidate is generated once per fresh verdict and reused on every render.
Tests assert request counts; exact dollar attribution of judge-repair MT remains
out of scope because current usage rows classify those calls as translation.

## Target flow

```text
judge seats
  +-- pass/minor ----------------------> existing projection
  +-- major/max-length ----------------> existing repair + re-judge
  +-- critical
        +-- run_checks -> repair_targets once
        +-- snapshot lock
        +-- native Suggestion(verdict/run/target/context/engine)
            target remains unchanged and held

producer card
  +-- stale/context drift ------------> Re-check only
  +-- current critical, no candidate -> Generate / normal editor
  +-- current candidate
        +-- Keep as is ---------------> existing resolution
        +-- Use suggested fix
              +-- Unit -> Suggestion -> verdict locks
              +-- freshness + permission checks
              +-- STATE_FUZZY + audited Change
              +-- consume candidate
              +-- queued JudgeRun after commit
                    +-- judge current text, no phase-1 MT or mutating repair
                    +-- pass/major -> project state; critical -> held + new candidate
```

## Task 1: Define candidate persistence and audit

**Files:**

- `weblate/trans/models/suggestion.py`
- `weblate/trans/models/judge.py`
- `weblate/trans/models/__init__.py`
- `weblate/trans/tests/test_models.py`
- `weblate/trans/tests/test_judge.py`

Write failing tests for this closed metadata shape in `Suggestion.userdetails`
and matching immutable Change details:

```text
kind="judge-repair", schema=1, judge_verdict_id, judge_run_id,
target_hash, context_hash, engine
```

Cover valid parsing, missing/wrong-typed fields, unknown metadata, secret-free
validation, plural/max-length/autofix behavior, idempotence, and unchanged normal
suggestions. An identical human suggestion must not be reclassified or block the
separate judge candidate. A new verdict replaces the old active candidate.

Implement a versioned parser/constructor in `models/judge.py`. Extend
`SuggestionManager.add` only with trusted internal `userdetails` and
`change_details`; defaults remain unchanged. Scope deduplication to the judge
namespace. Use automation/anonymous authorship and render provenance from
`kind`, not from the launcher. Preserve candidate text in the existing
Suggestion Change and provenance in `Change.details`.

Verify:

```bash
./rundev.sh test weblate/trans/tests/test_models.py weblate/trans/tests/test_judge.py
```

Commit: `feat(judge): define stored repair candidate contract`.

## Task 2: Persist critical candidates instead of mutating

**Files:**

- `weblate/trans/judge_loop.py`
- `weblate/trans/autotranslate.py`
- `weblate/trans/models/judge.py`
- `weblate/trans/migrations/0113_alter_judgerununit_repair_status.py`
- `weblate/trans/tests/test_judge_loop.py`
- `weblate/trans/tests/test_judge_autotranslate.py`
- `weblate/trans/tests/test_judge_round.py`

Tests first:

- first-round critical makes two seat requests and one repair call, stores one
  candidate, never calls `_apply_repair`, and leaves target unchanged;
- translated/non-writable criticals also get candidates;
- provider failure, unusable plurals, and target/state/context drift store
  nothing and leave the target held;
- cache/retry reuses the candidate without a second repair MT call;
- major/max-length still use `_apply_repair` and retain request counts;
- a repaired major that ends critical stores a final candidate;
- audit distinguishes `candidate-stored`, `no-candidate`, `applied`, and
  `rolled-back`.

Split `_PreparedRound.needs_repair` into `needs_candidate` and
`needs_mutating_repair`. Critical candidate generation ignores writable state
and remaining repair attempts. Major/max-length mutation keeps current ownership
and attempt rules. Batch `repair_targets` over the union, persist critical
outputs under the `_apply_repair` snapshot check, and send only mutable outputs
to `_apply_repair`. Criticals never enter `next_pending`. Add
`RepairStatus.CANDIDATE_STORED` and its `AlterField` migration. Delete obsolete
critical auto-apply branches.

Verify:

```bash
./rundev.sh test weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_round.py
```

Commit: `feat(judge): persist critical repair candidates`.

## Task 3: Add generation retry and one-unit re-check

**Files:**

- `weblate/trans/tasks.py`
- `weblate/trans/autotranslate.py`
- `weblate/trans/models/judge.py`
- `weblate/trans/views/edit.py`
- `weblate/urls.py`
- `weblate/trans/tests/test_judge_autotranslate.py`
- `weblate/trans/tests/test_judge_views.py`
- `weblate/trans/tests/test_tasks.py`

Tests cover both permissions, two rapid POSTs dispatching once, queued/running
badge, completed/failed retry, dispatch failure, and a fuzzy Unit re-check that
judges current target without phase-1 MT or the major repair loop. A pass or
admissible major must use exactly two seat calls, make no repair MT call, and
project the accepted target to its release state. A repeated critical must use
two seat calls, keep the target held, and make one repair MT call only to store
the next candidate. Worker start must reuse the pre-created JudgeRun. Generation
tests cover first generation, existing-candidate no-op, explicit Generate
another, duplicate POST, failure preserving the old candidate, and drift
discarding the paid output.

Add task-internal `judge_run_id`, `judge_pretranslate=True`, and
`judge_mutating_repairs=True` to `auto_translate`, `BatchAutoTranslate`, and
`AutoTranslate`. One-unit re-check passes `unit_ids=[unit.id]`, `mode="judge"`,
the run ID, `judge_pretranslate=False`, and
`judge_mutating_repairs=False`.

The re-check flag passes `writable_ids=set()` into the existing judge loop so
major cannot trigger `_apply_repair` or another judge round. Task 2 candidate
generation is independent of `writable_ids`, so a repeated critical still
stores exactly one new hash-bound candidate. Both seats, permissions, cache
identity, projections, and audit remain enabled.

Under the Unit lock, reuse an active QUEUED/RUNNING re-check or create one
`JudgeRun(scope_type=TRANSLATION, requested_mode="recheck",
requested_query="id:<unit-id>", cap=1)`. Dispatch on commit, store the Celery ID,
and mark broker failures FAILED. Worker validates scope/query/status before
RUNNING.

Add an asynchronous candidate-generation task. It re-reads the unresolved
critical, runs checks, calls `repair_targets([unit], actor)` once, and persists
only if verdict/target/context still match. Deduplicate with a bounded cache key
over Unit and verdict ID, cleared in `finally` with a recovery TTL. GET never
calls the provider. Generate returns an existing candidate; only Generate
another replaces it, after successful snapshot validation.

Commit: `feat(judge): add one-unit recheck orchestration`.

## Task 4: Guard acceptance across every path

**Files:**

- `weblate/trans/models/judge.py`
- `weblate/trans/models/suggestion.py`
- `weblate/trans/views/edit.py`
- `weblate/trans/tasks.py`
- `weblate/api/views.py`
- `weblate/templates/snippets/suggestions.html`
- `weblate/urls.py`
- `weblate/trans/tests/test_judge.py`
- `weblate/trans/tests/test_judge_views.py`
- `weblate/trans/tests/test_suggestions.py`
- `weblate/api/tests.py`
- `weblate/trans/tests/test_bulk_suggestions.py`

Tests cover success and target drift, context drift, another verdict, resolved
verdict, malformed metadata, stale ID, missing access, and either missing
permission. Failure changes neither target/state nor task count. Success writes
`STATE_FUZZY`, records `ActionEvents.ACCEPT` with provenance, consumes once,
queues once, applies deterministic autofixes, and redirects through
`success_next`.

Exercise the card, normal suggestion accept, API accept, vote autoaccept, and
bulk accept. Judge candidates never autoaccept from votes; bulk excludes them;
generic clone/vote/accept/approve/edit controls do not render.

Implement typed `JudgeCandidateError` and one `accept_judge_candidate` service:

1. Check `unit.review` and `translation.auto`.
2. Lock Unit, Suggestion, then representative JudgeVerdict.
3. Require unresolved REJECT and matching verdict/target/context metadata.
4. Write `STATE_FUZZY`, `ActionEvents.ACCEPT`, `propagate=False`, and provenance.
5. Delete the candidate.
6. Create the deduplicated queued re-check and dispatch on commit.

`Suggestion.accept` detects trusted judge metadata and delegates. Web/API
callers translate the typed error. Voting skips judge autoaccept. Bulk filters
judge candidates. The normal suggestions snippet renders a read-only diff and
link to the verdict card without generic actions.

Commit: `feat(judge): guard repair candidate acceptance`.

## Task 5: Render the embedded flow

**Files:**

- `weblate/trans/views/edit.py`
- `weblate/templates/snippets/judge-verdict.html`
- `weblate/trans/tests/test_judge_views.py`

Render-test mutually exclusive states:

- stale/context drift: only Re-check;
- queued/running: Re-checking, no duplicate submit;
- current candidate: diff, provenance, Use suggested fix, Generate another,
  Keep as is, and normal editor access;
- no candidate: Generate suggested fix, Keep as is, normal editor, and an
  explicitly generic Automatic suggestions fallback rendered only under
  `user_can_use_machinery`; without `machinery.view` both the tab and the
  panel are absent (`translate.html:343,502`), so the fallback must be
  omitted rather than link to nothing;
- no `machinery.view`: every other state renders unchanged and no
  Automatic suggestions control appears;
- generation pending: no second submit;
- resolved/consumed/stale/wrong-verdict candidate never active;
- major/minor never inherit a critical candidate.

`_judge_view_context` resolves at most one candidate for
`judge_current_verdict` and exposes queued/generation state. Preserve the
`current_verdict`/`active_verdict` freshness split. GET reads only; paid actions
are CSRF POSTs. Use native unit-target formatting and semantic controls. All
strings are translatable. Keep Zen and the decision to omit an Edit manually
button unchanged.

Verify view tests and djLint for `judge-verdict.html` and `suggestions.html`.

Commit: `feat(judge): embed repair candidate triage`.

## Task 6: Compress two-seat evidence

**Files:** `judge-verdict.html`, `test_judge_views.py`.

For reject, flag, minor-pass, and repair evidence, test one visible summary and
one native `<details>` containing all errors, models, timestamps, and
back-translations. Implement one shared path; keep severity and ship/hold badges
visible. No JavaScript. Preserve accessibility and i18n.

Commit: `refactor(judge): compress verdict evidence`.

## Task 7: Name remaining outcomes

**Files:** `models/judge.py`, `judge-verdict.html`, `test_judge.py`,
`test_judge_views.py`.

Test and add `(FLAG, "", accepted_as_is)` with immutable `JUDGE_RESOLUTION`
history. PASS/minor transitions remain absent. Fresh flag shows Keep as is and
Escalate; minor has evidence without resolution. AI variants remains a generic
machinery-tab link for major/minor and the critical fallback, rendered only
under `user_can_use_machinery` (`translate.html:343,502`) and tested for
absence without `machinery.view`; never label it as the stored judge
candidate.

Commit: `feat(judge): name producer triage outcomes`.

## Task 8: Auto-advance only after success

**Files:** `judge-verdict.html`, `views/edit.py`, `test_judge_views.py`.

Test successful resolution and candidate acceptance using
`success_next=next_unit_url`. Blank reason, stale verdict/candidate, invalid
form, and permission failure return to `next=this_unit_url`. Preserve URL
sanitization and last-item fallback. Generate and Re-check do not advance merely
because work was queued.

Commit: `feat(judge): advance successful triage decisions`.

## Task 9: Add conservative readiness

**Files:** `views/basic.py`, `judge-readiness.html`, `test_judge_views.py`.

Test critical, major, stale/context-changed, and unparsed counters and only real
destinations. Candidate presence never clears critical. CTA is absent for any
critical, target stale, context drift, unparsed current round, or zero history.
Use cached stats for display only. When cached blockers are zero, run the
authoritative fresh target/context blocker check. Add query-count coverage. CTA
links existing download/repository controls; it is not a release action.

Commit: `feat(judge): add conservative hand-off readiness`.

## Task 10: Documentation and security

**Files:**

- `docs/admin/checks.rst`
- `docs/changes.rst`
- `docs/guides/producer-guide-weblate.md`
- review and modify if required: `docs/security/threat-model.rst`

Document that this is judge-guided repair MT, not literal text from a judge
seat; freshness; one-click apply; fuzzy hold; re-check; recovery; permissions;
cost; direct major accepted-as-is; and stale manual edits blocking readiness.
Edit only `producer-guide-weblate.md`. Review the threat model because the
change adds paid authenticated endpoints and an AI write path; update it if its
authorization/freshness claims change.

Commit: `docs(judge): document stored repair triage`.

## Task 11: Verification

Run the judge, model, suggestion, API, bulk, task, and Selenium modules touched
above, including:

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_models.py \
  weblate/trans/tests/test_suggestions.py \
  weblate/trans/tests/test_bulk_suggestions.py \
  weblate/api/tests.py \
  weblate/checks/tests/test_judge.py
uv run prek run --files <all-touched-files>
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check
```

Browser/Selenium flows:

1. Fresh candidate: preview, apply, auto-advance.
2. Pending re-check: Re-checking and duplicate suppression.
3. Re-check pass: candidate consumed, blocker gone.
4. Re-check admissible major: exactly two seat calls, no repair MT, accepted
   target becomes release-ready with advisory evidence.
5. Re-check critical: exactly two seat calls plus one candidate MT, new
   candidate appears, blocker remains.
6. Context drift: candidate blocked, only Re-check.
7. Missing candidate: Generate and failed retry preserving the old candidate.
8. Normal-batch major repair and component readiness.

Use deterministic fixtures/mocked provider responses to avoid unapproved spend.
Use the dev surface at port 3001 only if already running; rebuilding the shared
stack needs approval.

## Acceptance criteria

1. Every current critical target stays untouched and has at most one current
   native judge candidate.
2. Producer previews and applies it from the card in one click.
3. Acceptance cannot ship before a fresh one-unit re-check.
4. Drift blocks every UI/API/internal acceptance route.
5. Generation, regeneration, and re-check dedupe retries/double-clicks.
6. Normal-batch major repair is unchanged; producer one-unit re-check never
   repairs or re-judges an admissible major.
7. Tests prove the 3-call pre-decision, 5-call pass/major, and 6-call
   repeated-critical paths.
8. Readiness cannot report a false zero from cache or target-only freshness.
9. UI is translatable and keyboard accessible.
10. No secret, prompt, or response body is persisted.

Deployment to production is not part of this plan and requires explicit
approval.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | NOT RUN | - |
| Codex Review | `/codex review` | Independent second opinion | 0 | NOT RUN | - |
| Eng Review | `/plan-eng-review` | Architecture and tests | 1 | CLEAR | 1 blocking scope gap resolved; full candidate lifecycle added |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT RUN | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | - |

**VERDICT:** APPROVED + ENG CLEARED - ready for implementation.

NO UNRESOLVED DECISIONS
