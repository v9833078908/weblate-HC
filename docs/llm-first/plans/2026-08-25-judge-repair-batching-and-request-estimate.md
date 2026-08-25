# Judge Repair Batching and Request Estimate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Batch LLM judge repairs through the existing machinery scheduler and show the real worst-case judge request count in the automatic-translation form.

**Architecture:** Move the existing generic machinery-batch scheduler out of `weblate/trans/autotranslate.py` into a dependency-neutral translation module. `judge_loop` will use that scheduler to obtain every repair candidate for one judge round in the configured engine's normal batches, then retain the existing per-unit lock, deterministic-check rollback, verdict persistence, and re-judge flow. The UI will estimate judge calls as `ceil(strings / JUDGE_BATCH_SIZE) × seats × rounds`; it will continue to list machine translation separately because only empty strings enter phase 1.

**Tech Stack:** Python 3.13+, Django, Celery, Weblate `BatchMachineTranslation`, pytest, Django i18n templates, Ruff/prek.

---

Status: **awaiting approval.**

## Scope and decisions

Production evidence from `victory-banner/common/es`, 2026-08-25:

- 60 empty Spanish units were translated in six `openrouter` requests (the
  configured batch size is ten), with zero `Mismatching assistant reply ids`.
- Attempt 1 repaired 14 units. Current `repair_target()` calls
  `engine.translate(unit, user)` once per unit (`weblate/trans/judge_loop.py:84-112`),
  so it used 14 separate requests and repaid the fixed LLM prompt prefix each
  time.
- The form's current formula is `strings × 2 seats × (1 + attempts)` in
  `weblate/trans/views/basic.py:802-812`, while the actual judge transports
  batches of `JUDGE_BATCH_SIZE` requests. With 60 strings, batch size five and
  one allowed repair round, the current display says 240 where the real
  judge-only upper bound is 48.

Invariants this work must preserve:

1. A repair is attempted only after both seats have judged the round, only for
   `writable_ids`, and only before `JUDGE_MAX_REPAIR_ATTEMPTS` is exhausted.
2. A batch response that lacks a usable candidate for one unit leaves that unit
   unchanged; a successful sibling must still be eligible for repair.
3. `_apply_repair()` remains the only writer of a repaired target. Its lock,
   request/context snapshot comparison, `STATE_FUZZY` interim state, and
   deterministic-check rollback must remain unchanged.
4. A repaired unit is re-judged by both seats. Audit rows remain one row per
   `(unit, run_id, attempt, seat)`; no verdict may be overwritten.
5. Existing non-batch default engines retain the present single-unit repair
   path. `RoutedLLMTranslation` is a batch-capable engine and is the production
   target of this optimisation.
6. The UI number is a request-count upper bound for the judge only. It is not
   a dollar estimate and must not claim a quality result or an approval.

Out of scope:

- Changing judge verdict thresholds, seat models, `JUDGE_MAY_APPROVE`, or
  repair eligibility.
- Running another paid production experiment or deploying this implementation.
- Adding a cost dashboard, batch/outcome telemetry fields, or a changelog entry.
  This is a minor correction to an unreleased judge feature; existing
  `LLMUsageLog` remains the cost source.
- Batch-retrying malformed producer responses. That belongs to the independent
  LLM batch-identity work.

## Task 1: Make the machinery scheduler reusable without changing its behaviour

**Files:**
- Create: `weblate/trans/machinery.py`
- Modify: `weblate/trans/autotranslate.py:7-11,20-22,50-56,59-265`
- Modify: `weblate/trans/tests/test_autotranslate.py:43-44,1311-1490`

**Step 1: Move the existing focused scheduler tests before moving code**

Change `weblate/trans/tests/test_autotranslate.py` so
`MachineryBatchFetchTest` imports `fetch_machinery_matches` from
`weblate.trans.machinery`, not `weblate.trans.autotranslate`. Do not change
its fixtures or assertions yet: they already cover engine batch sizing,
parallel dispatch, rate-limit retry and failed-batch isolation.

```python
from weblate.trans.machinery import fetch_machinery_matches
```

**Step 2: Run the focused scheduler tests to verify the import fails**

Run:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_autotranslate.py -q -k MachineryBatchFetchTest
```

Expected: collection fails with `ModuleNotFoundError: No module named
'weblate.trans.machinery'`.

**Step 3: Extract the scheduler verbatim into a dependency-neutral module**

Create `weblate/trans/machinery.py` with the repository copyright/SPDX header.
Move, without semantic edits, the following from `autotranslate.py`:

- `RATE_LIMIT_WAIT` and `RATE_LIMIT_POLL`;
- `_fetch_machinery_batch`;
- `_wait_for_rate_limit`;
- `_fetch_machinery_batches`;
- `_fetch_machinery_service`;
- `fetch_machinery_matches`.

Retain the existing `BatchMachineTranslation` input contract and return type:

```python
def fetch_machinery_matches(
    *,
    units: list[Unit],
    user: User | None,
    services: Sequence[BatchMachineTranslation],
    threshold: int,
    set_progress: Callable[[int], None] | None = None,
    log_translation: Translation | None = None,
    on_batch: Callable[[list[Unit]], None] | None = None,
) -> dict[int, UnitMemoryResultDict]:
    ...
```

In `autotranslate.py`, import this symbol from the new module and delete the
old definitions and imports made unused by the move. No compatibility
re-export: every repository caller should import the new owner directly.

**Step 4: Run the focused scheduler tests**

Run the command from Step 2.

Expected: all `MachineryBatchFetchTest` cases pass unchanged. This proves the
move retained the scheduler's concurrency, rate-limit and per-batch failure
contracts before the judge loop begins using it.

**Step 5: Commit the mechanical extraction**

```bash
git add weblate/trans/machinery.py weblate/trans/autotranslate.py \
  weblate/trans/tests/test_autotranslate.py
git commit -m "refactor(trans): share machinery batch scheduler"
```

## Task 2: Batch repair candidates for each negative judge round

**Files:**
- Modify: `weblate/trans/judge_loop.py:28-30,84-112,275-316,458-485`
- Modify: `weblate/trans/tests/test_judge_loop.py:12-18,71-83,98-139`

**Step 1: Write failing contracts for batched repair**

Add test helpers that create two repairable units and return distinct high-
quality machinery candidates. Patch
`weblate.trans.judge_loop.fetch_machinery_matches` and verify that a negative
round:

1. calls the scheduler once with both repairable units and the configured
   `openrouter` service;
2. applies each unit's own best candidate, not a candidate keyed by source text
   from another unit;
3. asks both seats to judge the repaired units again;
4. leaves a unit unchanged when the scheduler returns no usable candidate for
   that unit, while still repairing and re-judging its sibling.

The core assertion must defend the cost regression:

```python
scheduler.assert_called_once()
assert scheduler.call_args.kwargs["units"] == [first, second]
assert first.target == "first repaired target"
assert second.target == "second repaired target"
assert request_verdicts.call_count == 4  # two seats × initial and repair rounds
```

Add a separate fallback test using a non-`BatchMachineTranslation` fake engine.
It must prove the existing single-unit `repair_target()` behaviour still repairs
both units rather than silently dropping them.

**Step 2: Run the new judge-loop tests to verify they fail**

Run:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q \
  -k "batched_repair or repair_falls_back"
```

Expected: failure because `run_judge_batch()` invokes `repair_target()` inside
`_process_round_unit()` once per unit and has no batched scheduler call.

**Step 3: Add a batch-capable repair-candidate API**

Keep candidate selection in one place. Refactor the final validation currently
inside `repair_target()` into a private helper that accepts one unit and its
machinery candidates and returns `list[str] | None`. It must continue to reject
missing plural forms, blank text and an unchanged target.

Add a helper with this shape:

```python
def repair_targets(
    units: list[Unit], user: User | None
) -> dict[int, list[str]]:
    """Return usable repair targets keyed by unit id, without writing units."""
```

For a `BatchMachineTranslation` engine, call the extracted
`fetch_machinery_matches()` once with all repairable units, one service and the
normal machinery threshold. Convert each `UnitMemoryResultDict` through the
shared candidate-selection helper. Omit a unit with no valid candidates.

For a non-batch engine, call the existing single-unit path per unit and build
the same `dict[int, list[str]]`. This is a compatibility fallback, not a second
batch scheduler.

Do not write a target, change a state or run checks in `repair_targets()`.

**Step 4: Stage, fetch, then apply repairs**

Refactor `_process_round_unit()` into two explicit phases:

1. Prepare per-unit repair inputs only after `_round_verdict()` is current,
   negative, writable and below the attempt limit. Each input carries the unit,
   request snapshot, pre-repair target, deterministic checks and state required
   by `_apply_repair()`.
2. In `run_judge_batch()`, collect all prepared inputs after both seats finish;
   call `repair_targets()` once; call the unchanged `_apply_repair()` only for
   inputs with a returned target; append only changed outcomes to
   `next_pending`.

The coordinating shape must be equivalent to:

```python
prepared = [prepare_round_unit(...) for unit in pending]
repairs = repair_targets([item.unit for item in prepared if item.needs_repair], user)
for item in prepared:
    outcome = item.apply(repairs.get(item.unit.id))
    if outcome.changed:
        next_pending.append(outcome.unit)
```

Do not batch across different judge runs, components, target languages or
attempts. `run_judge_batch()` already scopes one call to one translation and
one round; preserve that boundary.

**Step 5: Run focused tests, including safety regressions**

Run:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q
```

Expected: the new batching and fallback tests pass, plus existing tests prove
that a deterministic-check regression restores the old target, a stale unit is
not modified, two seats remain conjunctive, and one repair gets exactly one
re-judge round.

**Step 6: Commit repair batching**

```bash
git add weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py
git commit -m "fix(judge): batch repair translations"
```

## Task 3: Correct the automatic-translation request estimate

**Files:**
- Modify: `weblate/trans/views/basic.py:24-26,802-812`
- Modify: `weblate/templates/snippets/autoform.html:40-44`
- Modify: `weblate/trans/tests/test_judge_views.py:31,72-81`

**Step 1: Write a failing exact estimate test**

In `JudgeAutoTranslateViewTest`, create enough non-readonly, un-translated
units to cross a judge batch boundary. With
`JUDGE_BATCH_SIZE=5`, two seats and one repair attempt, six matching units must
render 8 judge requests, not the old 24:

```python
with override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1):
    response = self.client.get(self.translation_url)
self.assertContains(response, "8 LLM requests")
self.assertNotContains(response, "24 LLM requests")
```

Also retain a one-unit case: the ceiling of one item is one batch, therefore
four judge requests in the same configuration. This prevents an integer-floor
regression that would display zero.

**Step 2: Run the view test to verify it fails**

Run:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_views.py -q \
  -k "request_estimate"
```

Expected: the six-unit test reports `24 LLM requests`, exposing the
string-count formula.

**Step 3: Calculate requests from batches and make the explanation honest**

Import `JUDGE_SEATS` into `basic.py`. Replace the string formula with exact
integer ceiling division:

```python
judge_batches = (
    judge_row_count + settings.JUDGE_BATCH_SIZE - 1
) // settings.JUDGE_BATCH_SIZE
judge_request_estimate = (
    judge_batches * len(JUDGE_SEATS) * (1 + settings.JUDGE_MAX_REPAIR_ATTEMPTS)
)
```

Pass `judge_batches` and `judge_request_estimate` to the template. Replace the
parenthetical template explanation with translatable wording that says batches,
not strings, for example:

```django
{% translate "LLM requests (batches × 2 judges × rounds), plus machine translation for empty strings." %}
```

Keep the existing explicit caveat that empty strings add machine-translation
requests. Do not invent a dollar estimate in the UI: provider costs vary by
routed language/model and LLM usage records are the source of truth.

**Step 4: Run the focused view tests**

Run the command from Step 2.

Expected: the exact six-unit count is eight, the one-unit ceiling is four, and
the explanatory text says `batches`.

**Step 5: Commit the UI correction**

```bash
git add weblate/trans/views/basic.py weblate/templates/snippets/autoform.html \
  weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): estimate requests by batch"
```

## Task 4: Verify integration without spending provider money

**Files:**
- Modify: no production configuration or documentation files
- Test: `weblate/trans/tests/test_judge_loop.py`
- Test: `weblate/trans/tests/test_judge_views.py`
- Test: `weblate/trans/tests/test_autotranslate.py`

**Step 1: Run the combined judge and scheduler suites**

Run:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_autotranslate.py -q
```

Expected: all pass. These cover repair batching, per-unit rollback, verdict
state projection, rendered estimate, scheduler batching, rate-limit handling
and failed-batch isolation using mocked provider calls.

**Step 2: Run formatting and lint checks only for changed files**

Run:

```bash
uv run prek run ruff-format ruff-check --files \
  weblate/trans/machinery.py \
  weblate/trans/autotranslate.py \
  weblate/trans/judge_loop.py \
  weblate/trans/views/basic.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_views.py
```

Expected: both hooks pass without modifying unrelated files.

**Step 3: Inspect the migration graph and changed strings**

Run:

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check
```

Expected: `No changes detected`; this implementation adds no model field or
migration. Confirm the changed template string is wrapped in `{% translate %}`
and no hard-coded, untranslated UI text has been introduced.

**Step 4: Commit verification-only corrections if any**

If a focused check requires a correction, make the smallest correction, rerun
only its failing check and amend the commit that introduced it. Do not create a
placeholder follow-up or a production test run.

**Step 5: Publish only after approved implementation**

After the implementation commits are reviewed and the user explicitly approves
deployment separately, push the branch and follow the repository deployment
workflow. This plan itself does not authorise `./deploy/vps.sh deploy`, a
`rundev.sh` restart, or any paid provider request.

## Acceptance criteria

- [ ] One repair round issues `ceil(repairable_units / engine.batch_size)`
      producer requests for the batch-capable `openrouter` engine, not one per
      repairable unit.
- [ ] An unusable result remains local to its unit; it neither mutates that unit
      nor prevents a valid sibling repair.
- [ ] Existing locks, stale-snapshot rejection, deterministic-check rollback,
      interim state and two-seat re-judging still pass their regression tests.
- [ ] A non-batch configured engine preserves the former single-unit repair
      behaviour.
- [ ] Six matching units, batch size five, two seats and one repair attempt
      display an eight-request judge upper bound; one matching unit displays
      four.
- [ ] The UI explains that the bound is based on batches and still separates MT
      requests for empty strings.
- [ ] No migrations, cost claims, model-policy changes or paid production calls
      are introduced.
