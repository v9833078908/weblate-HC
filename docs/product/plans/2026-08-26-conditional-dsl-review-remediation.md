# Conditional DSL review remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Completed on 2026-08-26. Focused regression tests and scoped lint passed; the full custom-check module was not run because PostgreSQL at `127.0.0.1:5434` had no response.

**Goal:** Preserve the raw max-length representation for malformed Hero Craft conditional DSL and accurately document the separator-validation boundary without blocking localized branch text.

**Architecture:** The conditional parser will continue validating headers, nested placeholders, and top-level separator order around immutable tokens. A separator moved only within arbitrary translated branch text is not statically distinguishable from a valid localization, so this implementation makes that boundary explicit rather than treating translated content as engine syntax. `_conditional_length_text()` will reject either unmatched opening or closing brace before it collapses a conditional.

**Tech Stack:** Python, Django/Weblate custom checks, pytest, prek.

---

## Scope

Included:

- Return the replacement-transformed input unchanged whenever conditional-length input contains any unmatched brace.
- Pin that malformed closing braces take the same raw fallback as malformed opening braces.
- Pin the supported separator rule: an immutable placeholder crossing a top-level separator fails, while text-only branch localization remains permitted.
- Correct the current unreleased changelog claim that every moved separator is reported.

Out of scope:

- Changing the conditional DSL grammar or adding a runtime evaluator.
- Enforcing the exact character position of a separator in localized branch text.
- Changing automatic-translation, judge-loop, registry, deployment, or live Weblate configuration.
- Restarting shared containers, recomputing checks, altering stored translations, or deploying.

## Task 1: Pin and document the delimiter policy

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:228-235,281-296`
- Modify: `docs/changes.rst:49`

### Step 1: Add the failing localization-boundary regression

Add a source and target with the same header, immediate placeholder, and one
separator, but different localizable plain-text branch lengths:

```python
source = "{x:cond:>0?{value}a|bc}"
target = "{x:cond:>0?{value}ab|c}"
self.assertFalse(self.check.check_single(source, target, None))
```

Keep the existing regression that moves a separator across `{hours}`. It must
continue to fail because the immutable-token sequence changes.

### Step 2: Run the focused regression before changing documentation

Run:

```bash
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
DJANGO_SETTINGS_MODULE=weblate.settings_test PYTHONPATH=weblate_customization/src \
.venv/bin/python -m pytest \
  weblate_customization/tests/test_checks.py::GameMarkupCheckTest \
  -n 0 -q
```

Expected: pass. This is a contract test, not a code defect to reverse.

### Step 3: Correct the unreleased changelog wording

Replace the claim that a `|` can be "moved" with a precise statement: the
`game-markup` check reports a lost or added top-level `|`, and a separator whose
order relative to protected placeholders changes, while branch text remains
translatable.

### Step 4: Run the focused regression and docs checks

Run:

```bash
uv run prek run ruff-check ruff-format --files \
  weblate_customization/tests/test_checks.py
uv run prek run trailing-whitespace end-of-file-fixer rst-double-space \
  sphinx-lint codespell --files docs/changes.rst
```

Expected: pass. Do not run `rst-bullet-stop`; the known pre-existing multiline
entry in `docs/changes.rst` fails that hook.

### Step 5: Commit

```bash
git add weblate_customization/tests/test_checks.py docs/changes.rst
git commit -m "docs(checks): clarify conditional separator validation"
```

## Task 2: Preserve malformed conditional input for length checks

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:120-123`
- Modify: `weblate_customization/src/weblate_customization/checks.py:309-322`

### Step 1: Extend the failing fallback regression

Add a trailing unmatched-closing-brace input to the existing malformed-input
loop:

```python
HUMAN_TIMER_DE + "}"
```

The assertion remains identity-based:

```python
self.assertIs(_conditional_length_text(text), text)
```

This fails before the implementation because `_balanced_brace_blocks()` ignores
stray closing braces and the helper collapses the preceding valid conditional.

### Step 2: Run the focused regression and verify it fails

Run:

```bash
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
DJANGO_SETTINGS_MODULE=weblate.settings_test PYTHONPATH=weblate_customization/src \
.venv/bin/python -m pytest \
  weblate_customization/tests/test_checks.py::ConditionalLengthRepresentationTest \
  -n 0 -q
```

Expected: the unmatched-closing-brace subtest fails; the existing unmatched
opening-brace case still passes.

### Step 3: Add the raw-fallback guard

At the top of `_conditional_length_text()`, after its identity-preserving
no-conditional guard, add:

```python
if any(_unmatched_braces(text)):
    return text
```

Do not alter `_balanced_brace_blocks()`, conditional recognition, branch
selection, or stock replacement ordering.

### Step 4: Re-run focused regressions

Run both focused test classes:

```bash
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
DJANGO_SETTINGS_MODULE=weblate.settings_test PYTHONPATH=weblate_customization/src \
.venv/bin/python -m pytest \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  weblate_customization/tests/test_checks.py::ConditionalLengthRepresentationTest \
  weblate_customization/tests/test_checks.py::GameMarkupCheckTest \
  -n 0 -q
```

Expected: pass. These classes do not require a test database.

### Step 5: Run scoped lint and commit

```bash
uv run prek run ruff-check ruff-format --files \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "fix(checks): preserve malformed conditional length input"
```

## Task 3: Verify and publish without deployment

**Files:**

- Modify: `docs/product/plans/2026-08-26-conditional-dsl-review-remediation.md:5`

### Step 1: Run the full custom-check module when PostgreSQL is available

Do not start, restart, or rebuild the shared Docker stack. If `127.0.0.1:5434`
is accepting PostgreSQL connections, run:

```bash
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
DJANGO_SETTINGS_MODULE=weblate.settings_test PYTHONPATH=weblate_customization/src \
.venv/bin/python -m pytest weblate_customization/tests/test_checks.py -n 0 -q
```

If the database remains unavailable, record the connection refusal and retain
the focused no-database regression output as verification evidence.

### Step 2: Mark the plan complete

Change the status line to:

```text
**Status:** Completed and verified on 2026-08-26.
```

### Step 3: Commit and push

```bash
git add docs/product/plans/2026-08-26-conditional-dsl-review-remediation.md
git commit -m "docs(product): complete conditional DSL review remediation"
git push origin HEAD
```
