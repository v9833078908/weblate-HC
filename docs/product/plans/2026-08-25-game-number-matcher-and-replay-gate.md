# Game number matcher and replay gate correction plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** complete

**Goal:** Make `game-number` use each target quantity occurrence at most once and make its offline replay gate reject incomplete or malformed corpora.

**Architecture:** Open scale notation has two incompatible readings: its computed value and its literal mantissa tokens. The matcher will retain those readings on one target occurrence and search for a complete source-to-target assignment. A target occurrence may be assigned in its computed-value mode or its literal-token mode, but never both. The replay script will use fixed corpus manifests, reject malformed rows before comparing, and expose its loaders through `main()` for regression tests.

**Tech stack:** Python 3.13, `decimal.Decimal`, Django `TestCase`, CSV/JSONL standard-library readers.

---

## Task 1: Lock matcher behaviour with regression tests

**Files:**

- Modify: `weblate_customization/tests/test_checks.py`

### Step 1: Add the false-negative regression

Add a `GameNumberCheckTest` assertion that `game_number_fails()` reports a failure for:

```python
fails(
    "10 thousand + 10 thousand",
    "10 thousand",
    source_code="en",
    target_code="en",
)
```

One target expression cannot cover both source quantities through its `10000` value and its literal `10` at the same time.

### Step 2: Add the greedy-assignment regression

Add an assertion that this valid assignment passes:

```python
fails(
    "10 thousand and 10000",
    "10000 and 10",
    source_code="en",
    target_code="en",
)
```

The open source quantity may use literal target `10`, while the strict source `10000` uses target `10000`.

### Step 3: Add the compound-occurrence regression

Add an assertion that `1 + 500` fails against `1 million 500 thousand`. The target compound is one occurrence, not two source-number resources.

### Step 4: Run the focused tests red

```sh
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Expected before implementation: the duplicate-target and compound-target tests pass incorrectly, while the valid-assignment test fails incorrectly.

## Task 2: Match quantity occurrences without double spending

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Test: `weblate_customization/tests/test_checks.py`

### Step 1: Represent target readings by occurrence

Replace the flattened `Counter[Decimal]` returned by `_readings()` with a representation that retains:

- one closed-CJK value occurrence;
- one bare-number occurrence;
- one open-scale occurrence that may serve its computed value or one literal fallback.

Every target occurrence has one mode and can satisfy one source requirement.

### Step 2: Search complete assignments

Build all valid assignments for each source quantity: its computed value requires one compatible target occurrence; its fallback requires a complete bundle of compatible literal-token occurrences. Search assignments without committing a greedy earlier match. Reject only when no complete assignment covers every source quantity.

The matching state must prevent a target open-scale occurrence from serving both its value and its literal fallback. Literal use consumes the whole occurrence, so a compound cannot be split between source quantities.

### Step 3: Run focused tests green

```sh
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Expected: all `GameNumberCheckTest` tests pass, including both new regressions.

## Task 3: Make the replay gate fail closed

**Files:**

- Modify: `analysis/probes/game-number-replay.py`
- Create: `weblate_customization/tests/test_game_number_replay.py`

### Step 1: Add loader regression tests

Test a valid temporary TSV manifest and assert that an incomplete nine-language TSV, a missing target cell, and an unexpected language header raise an error before replay. Test that empty JSONL corpora fail their expected cardinality check.

### Step 2: Add fixed manifests and loader validation

Define expected schemas and pair counts in the replay module:

- Heart Abyss `en,fr`: 792 pairs;
- ST2 `zh_Hans`: 124 pairs;
- Col4 `fr`: 260 pairs;
- Heart Abyss nine-language: `de,en,es,fr,it,ja,ko,zh_Hans,zh_Hant`, 3564 pairs.

Validate exact header order, row count, and non-empty `context`, source and target cells. Refactor top-level execution into `main()` so the loader can be imported by its tests; retain command-line output and exit behaviour.

### Step 3: Run loader tests red then green

```sh
./rundev.sh test "weblate_customization/tests/test_game_number_replay.py"
```

Expected before validation: malformed fixtures are accepted. Expected after validation: valid fixture passes and malformed fixtures are rejected.

### Step 4: Run the gate

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test \
PYTHONPATH=weblate_customization/src \
uv run python3 analysis/probes/game-number-replay.py
```

Expected output includes 4740 pairs, zero invariant violations and 12 parsed CJK targets.

## Task 4: Verify and finish

**Files:**

- Modify: `docs/product/plans/2026-08-25-game-number-matcher-and-replay-gate.md`

### Step 1: Run affected suites

```sh
./rundev.sh test "weblate_customization/tests/test_checks.py"
./rundev.sh test "weblate_customization/tests/test_game_number_replay.py"
```

### Step 2: Lint the changed files

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_game_number_replay.py \
  analysis/probes/game-number-replay.py \
  docs/product/plans/2026-08-25-game-number-matcher-and-replay-gate.md
```

### Step 3: Record completion and push

Set this plan's status to `complete`, commit only its files with a Conventional Commit message, and push `fix/game-number-value-comparison`.

NO UNRESOLVED DECISIONS
