<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Glossary term-cache determinism implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use `test-driven-development` for Tasks 1-3
> and `verification-before-completion` for Task 4.

**Date:** 2026-08-24. **Status:** implemented and verified.

**Verification (2026-08-24):**

- Task 1 recorded the defect before any production change: three of the four
  new tests failed for the stated reason (`2 != 1` narrow after wide, `1 != 2`
  wide after narrow, `unexpectedly identical` for the `full` upgrade), the
  wildcard case passed.
- After Task 2 all four pass, together with the three
  `JudgeGlossaryContextTest` cases.
- Task 3 Step 1 deviates from completion criterion 4 as first written: with the
  keyed cache, two assertions in `test_judge_round.py` described the retired
  mechanism (the slot is unchanged after the judge asked) rather than the
  invariant, so they now assert that a narrow caller still gets its own single
  term. Both properties the tests defended are still covered, and the coverage
  is no longer coupled to how the accessor achieves it.
- Retiring the save/restore costs nothing: a throwaway probe counted
  `fetch_glossary_terms` calls over one round's sequence on one instance
  (check, verdict, request, repair check). Keyed cache alone: 3 fetches
  `[narrow, wide, narrow]`. Keyed cache plus restore: 3 fetches
  `[narrow, wide, wide]`. The restore only moves which caller pays.
- Owning suites: 1,854 passed, 84 skipped. The single failure,
  `LocKitGlossaryUploadUITest.test_confirmed_glossary_becomes_a_live_tbx_component`,
  is the pre-existing host VCS artifact (`Invalid revision range ..<sha>`);
  the whole class passes in isolation with these changes (33 passed).
- Focused `prek` hooks passed. Mypy stayed at 120 normalized findings; the only
  difference is a line-number shift of a pre-existing `no-redef` finding in
  `weblate/trans/models/unit.py`.
- Changelog: no entry. The check-side direction needs a wide fetch and
  `run_checks` on one instance. Production `run_checks` call sites are
  `weblate/checks/tasks.py:42`,
  `weblate/checks/management/commands/updatechecks.py:14`,
  `weblate/trans/admin.py:65`, `weblate/trans/tasks.py:1196`,
  `weblate/trans/models/unit.py:866,1071,2294,2309` and
  `weblate/trans/judge_loop.py:259,286`. Wide fetches happen in
  `weblate/trans/views/edit.py:1396,1681` and `weblate/glossary/views.py:74`,
  none of which run checks on the fetched instance. Only the judge loop pairs
  them, and the judge is unreleased.

**Goal:** Make `unit.glossary_terms` answer the selection its caller asked for,
so no consumer can inherit another consumer's glossary selection or impose its
own on the next one.

## Why this change exists

Commit `276b034` fixed one direction at one call site: the judge now saves and
restores the cache around its own fetch. The cache itself is still a single
unkeyed slot. `get_glossary_terms` fetches only when the slot is empty and
otherwise returns whatever is in it, ignoring its own arguments
(`weblate/glossary/models.py:360-366`):

```python
if unit.glossary_terms is None:
    fetch_glossary_terms([unit], full=full, include_variants=include_variants)
return cast("list[Unit]", unit.glossary_terms)
```

Consumers disagree about the selection on purpose:

| Caller | `full` | `include_variants` |
| --- | --- | --- |
| `weblate/checks/glossary.py:52` (`check_glossary`) | False | **False** |
| `weblate/checks/same.py:150` (`same`) | False | **False** |
| `weblate/machinery/microsoft.py:199` | False | **False** |
| `weblate/machinery/llm.py:430,654,657` | False | **False** |
| `weblate/glossary/views.py:74` (add term) | True | True |
| `weblate/trans/views/edit.py:1198` (sidebar, zen) | True/False | True |
| `weblate/glossary/models.py:441` (judge) | True | True |

The two arguments are not equivalent:

- `include_variants` decides **membership**: variants of a matched term are
  added to the result (`weblate/glossary/models.py:540-577`).
- `full` decides only **prefetch depth**
  (`prepare_glossary_units`, `weblate/glossary/models.py:333-335`). A
  `full=False` list is correct for a `full=True` caller but costs per-term
  queries while rendering.

So today the first caller on a unit instance decides for every later one, in
both directions:

- A check that runs first pins the narrow selection. The judge, the add-term
  view and the sidebar then see a list without variants. For the judge that was
  F1: `context_hash` was computed over the wide list, so the stored verdict was
  unreachable and every round was discarded.
- A wide consumer that runs first pins variants onto `check_glossary` and
  `same`, which asked to exclude them. `evaluate_glossary_terms` then judges a
  term its caller deliberately left out.

## Architecture

The slot records the selection it was filled for. `get_glossary_terms` refetches
when that stamp does not satisfy the request. An externally assigned list
carries no stamp and stays a deliberate wildcard.

Satisfaction rule: a stamp `(cached_full, cached_variants)` satisfies a request
`(full, include_variants)` when

```text
cached_variants == include_variants and (cached_full or not full)
```

- membership must match exactly, so `include_variants` is compared for equality;
- prefetch depth may exceed the request, so a `full=True` list serves a
  `full=False` caller without a second query.

Wildcard: a list assigned straight to `unit.glossary_terms` has no stamp and is
served to any request. `weblate/trans/views/edit.py:1200-1201` depends on this to
say "glossary matching is off for this unit, everyone gets `[]`", and unit tests
depend on it to inject terms without a database.

Cost: one extra fetch at each genuine selection switch on one instance. In the
judge loop that is one switch per repair attempt (`locked` is asked for the wide
selection by the repair guard and the narrow one by `run_checks`). In the LLM
batch path and on the translate page every caller shares one selection, so the
count does not change there.

## Out of scope

- The per-unit `fetch_glossary_terms` inside `build_request` (N+1, pre-existing).
- `Project.invalidate_glossary_cache` and the automaton cache: this plan changes
  only the per-instance slot.
- What `full` prefetches, and the default value of either argument.
- Task 6 of `docs/llm-first/plans/2026-08-24-judge-glossary-symmetry.md`.

## Task 1: Record both failing directions

### Step 1: Add the selection tests

In `weblate/glossary/tests.py`, add `GlossarySelectionCacheTest` covering one
term that has a variant sibling:

- narrow after wide: fetch with `include_variants=True`, then
  `get_glossary_terms(unit, include_variants=False)` must not return the
  sibling.
- wide after narrow: fetch with `include_variants=False`, then
  `get_glossary_terms(unit, include_variants=True)` must return it.
- wildcard: `unit.glossary_terms = []` is returned for both selections.
- prefetch depth: narrow-then-`full=True` issues queries, `full=True`-then-narrow
  issues none (`assertNumQueries`).

### Step 2: Run them and record the failure

```bash
source scripts/test-database.sh && CI_DB_PORT=5434 \
  PYTHONPATH="$PWD/weblate_customization/src" \
  uv run pytest -n 0 weblate/glossary/tests.py -k selection_cache -v
```

The first two must fail because the stale list is returned; the wildcard and the
`full=True`-then-narrow case must already pass.

## Task 2: Stamp and honour the selection

### Step 1: Add the stamp

In `weblate/trans/models/unit.py:786`, next to the slot:

```python
self.glossary_terms: list[Unit] | None = None
self.glossary_terms_selection: tuple[bool, bool] | None = None
```

Document that `None` means the list was supplied from outside and is served to
any request.

### Step 2: Fill the stamp

In `fetch_glossary_terms` (`weblate/glossary/models.py:471-475`), stamp
`(full, include_variants)` in the same loop that initializes the slot, so units
that end with no matches are stamped too.

### Step 3: Honour the stamp

Add the rule as one public predicate and use it in `get_glossary_terms`:

```python
def glossary_selection_is_cached(
    unit: Unit, *, full: bool = False, include_variants: bool = True
) -> bool:
```

It returns `False` when the slot is empty, `True` when there is no stamp, and
otherwise applies the satisfaction rule. Verify Task 1 turns green.

## Task 3: Retire the two now-wrong notions of "cached"

### Step 1: Drop the save/restore in the judge accessor

`get_matched_glossary_prompt_entries` (`weblate/glossary/models.py:441-458`)
becomes a plain call through the keyed accessor, with a docstring stating the
guarantee now belongs to the cache. Its two regression tests in
`weblate/trans/tests/test_judge_round.py` keep defending both directions, with
every assertion about the mechanism replaced by one about the invariant: a
narrow caller still gets its own selection after the judge asked for a wider
one.

### Step 2: Make the batch probes selection-aware

`weblate/machinery/llm.py:427` and `:651` test `glossary_terms is None`, which
after Task 2 is a second, weaker notion of "cached": a unit holding a wide list
is skipped by the batch fetch and then refetched per unit. Replace both with
`glossary_selection_is_cached(unit, include_variants=False)`.

## Task 4: Verify and document

### Step 1: Run the owning suites

```bash
source scripts/test-database.sh && CI_DB_PORT=5434 \
  PYTHONPATH="$PWD/weblate_customization/src" uv run pytest -n auto \
  weblate/glossary/tests.py weblate/machinery/tests.py \
  weblate/checks/tests/test_glossary_checks.py \
  weblate/checks/tests/test_same_checks.py weblate/checks/tests/test_judge.py \
  weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py
```

### Step 2: Lint and type check

Focused `prek` hooks over the changed files, then the prescribed mypy command;
its normalized finding count must stay at the branch baseline of 120.

### Step 3: Decide the changelog entry on evidence

The check-side direction is user-visible only where a wide fetch and
`run_checks` meet on one instance. Establish whether that pairing exists outside
the unreleased judge flow. If it does, add one line to the unreleased section of
`docs/changes.rst`; if the judge loop is the only path, record that here and add
nothing, per the rule that unreleased features need no entry.

## Completion criteria

Tasks 1-4 are complete only when all of the following hold:

1. Both selection directions have a test that failed before Task 2 and passes
   after it.
2. `get_glossary_terms` is the only place that decides whether to refetch, and
   `glossary_selection_is_cached` is the only place that spells out the rule.
3. No caller reads `unit.glossary_terms` to decide whether a fetch is needed.
4. The two judge regression tests from `276b034` still defend both directions
   with the save/restore removed, asserting the invariant rather than the slot.
5. The suites in Task 4 pass, focused lint passes, and mypy stays at 120
   normalized findings.
6. The changelog decision in Task 4 is recorded with its evidence.
