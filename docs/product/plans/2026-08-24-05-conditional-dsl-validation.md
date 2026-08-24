# Conditional DSL validation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Awaiting implementation approval.

**Goal:** Make `game-markup` reject parser-breaking changes to Hero Craft conditional DSL while continuing to allow localized branch text.

**Architecture:** Refactor the conditional recognition already used by `conditional_dsl_syntax_spans()` into one linear structural parser that returns both highlight spans and ordered immutable signatures. `GameMarkupCheck.check_single()` will compare those signatures only when the source contains a recognized conditional, alongside its existing markup and placeholder comparisons; text inside branches remains outside the signature and may be translated.

**Tech Stack:** Python, `regex`, Django/Weblate `TargetCheck`, `unittest` assertions executed by pytest in the existing dev Docker stack.

---

## Context and fixed contract

The source defect is recorded in
`docs/operations/audits/2026-08-24-need-for-greed-multilingual-lqa.md`:

- French `ui/amountFormatted` inserted NBSP and narrow NBSP inside
  `{value:cond:...}` and was caught only because nested placeholders also
  changed.
- French `ui/humanTimer` inserted the same spaces inside each conditional
  header but kept the nested sequence `{hours}`, `{minutes}`, `{seconds}`, so
  the current check did not fire.

`weblate_customization/src/weblate_customization/checks.py:134-180` already has
`conditional_dsl_syntax_spans()`. It recognizes balanced outer conditionals,
protects their immutable spans, and leaves branch text unprotected.
`GameMarkupCheck.check_single()` at lines 195-200 still compares only markup
multisets and the ordered generic placeholder sequence.

`docs/product/plans/2026-08-22-nested-game-placeholder-protection.md` deliberately
left conditional validation out of scope. This plan is the narrow follow-up; it
must reuse that scanner contract rather than introduce a second grammar.

The implementation contract is fixed as follows:

1. A recognized source conditional defines the validation contract.
2. The complete ordered source and target conditional signature sequences must
   match. An additional target conditional therefore fails.
3. Immutable elements are the outer braces, identifier, exact `:cond:` marker,
   operator, operand, `?`, each top-level `|`, and each nested placeholder in
   source order.
4. Branch text is excluded. `h.` may become `Std.`, `godz.`, `sa.`, `dk.`, or
   other locale text.
5. No whitespace normalization is allowed inside immutable syntax. ASCII space,
   NBSP (`U+00A0`), and narrow NBSP (`U+202F`) all remain significant.
6. The observed operator grammar is limited to `<`, `<=`, `>`, and `>=`.
   Undocumented operators require an engine-backed fixture and are out of
   scope.
7. A source with no recognized question-mark conditional gets no new
   conditional validation. Existing forms such as `{value:cond:1}` retain
   their current behavior.
8. Empty targets retain the current non-failing behavior.
9. The existing markup and placeholder comparisons remain unchanged.

### Implementation boundaries

- Work against the existing checkout and shared `dev-docker` stack. Do not
  create or start a second stack; the ports and Compose project are shared.
- The running container imports `weblate_customization` from
  `dev-docker/data/python/`. Copy the package there after changing it; never add
  that ignored copy to Git.
- Do not modify `weblate.trans.protected_tokens`, autofixes, machinery,
  spreadsheet validation, check registration, deployment configuration, stored
  translations, or the LQA audit.
- Do not modify `docs/changes.rst`: its current unreleased Bug fixes entry,
  "French punctuation spacing and automatic translation no longer modify
  syntax in Hero Craft conditional game placeholders," already covers this
  follow-up and must not be duplicated.
- Use @superpowers:test-driven-development for Tasks 1-3 and
  @superpowers:verification-before-completion for Task 4.

---

### Task 1: Add failing `GameMarkupCheck` regressions

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:24-140`
- Test: `weblate_customization/tests/test_checks.py`

#### Step 1: Add the audited malformed-target fixtures

Place these beside `AMOUNT_FORMATTED`, `HUMAN_TIMER_EN`, and
`HUMAN_TIMER_DE`. Use escapes for invisible spaces so review does not depend on
font rendering:

```python
AMOUNT_FORMATTED_FR_BROKEN = (
    "{value\u00a0:cond\u00a0:>99999\u202f?{value\u00a0:amount()}|}"
    "{value\u00a0:cond\u00a0:<=99999\u202f?{value\u00a0:N0}|}"
)
HUMAN_TIMER_FR_BROKEN = (
    "{hours\u00a0:cond\u00a0:>0\u202f?{hours}h. |}"
    "{minutes\u00a0:cond\u00a0:>0\u202f?{minutes}m. |}"
    "{seconds\u00a0:cond\u00a0:>=0\u202f?{seconds}s.|}"
)
HUMAN_TIMER_TR = (
    "{hours:cond:>0?{hours} sa. |}"
    "{minutes:cond:>0?{minutes} dk. |}"
    "{seconds:cond:>=0?{seconds} sn.|}"
)
```

#### Step 2: Add one regression for the exact French failures

Add this method to `GameMarkupCheckTest`:

```python
def test_rejects_whitespace_inside_conditional_syntax(self) -> None:
    for source, target in (
        (AMOUNT_FORMATTED, AMOUNT_FORMATTED_FR_BROKEN),
        (HUMAN_TIMER_EN, HUMAN_TIMER_FR_BROKEN),
        (
            HUMAN_TIMER_EN,
            HUMAN_TIMER_EN.replace("hours:cond", "hours :cond", 1),
        ),
    ):
        with self.subTest(target=target):
            self.assertTrue(self.check.check_single(source, target, None))
```

The `humanTimer` row is the essential regression: its nested generic
placeholder sequence is unchanged, so the current implementation returns
`False`.

#### Step 3: Add separator-order regressions

```python
def test_rejects_changed_conditional_separators(self) -> None:
    for target in (
        HUMAN_TIMER_EN.replace("h. |}", "h. }", 1),
        HUMAN_TIMER_EN.replace("h. |}", "h. ||}", 1),
        HUMAN_TIMER_EN.replace("{hours}h. |", "|{hours}h. ", 1),
    ):
        with self.subTest(target=target):
            self.assertTrue(
                self.check.check_single(HUMAN_TIMER_EN, target, None)
            )
```

These cases cover a lost, added, and moved top-level `|`. The moved case also
proves that the signature interleaves separators with nested placeholders
instead of comparing only independent counters.

#### Step 4: Add identifier, operator, and operand regressions

```python
def test_rejects_changed_conditional_headers(self) -> None:
    for target in (
        HUMAN_TIMER_EN.replace("hours:cond", "hour:cond", 1),
        HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>=0?", 1),
        HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>1?", 1),
        AMOUNT_FORMATTED.replace(":<=99999?", ":<99999?", 1),
    ):
        with self.subTest(target=target):
            self.assertTrue(
                self.check.check_single(HUMAN_TIMER_EN, target, None)
                if target != AMOUNT_FORMATTED.replace(":<=99999?", ":<99999?", 1)
                else self.check.check_single(AMOUNT_FORMATTED, target, None)
            )
```

Keep the implementation simple when writing the actual test: if the conditional
expression in the assertion is harder to read than two short loops, use two
short loops. Do not introduce a test helper used once.

#### Step 5: Add nested-placeholder and full-sequence regressions

```python
def test_rejects_changed_conditional_structure(self) -> None:
    targets = (
        HUMAN_TIMER_EN.replace("{hours}h.", "h.", 1),
        HUMAN_TIMER_EN.replace("{hours}h.", "{minutes}h.", 1),
        HUMAN_TIMER_EN[:-1],
        HUMAN_TIMER_EN + "{value:cond:>0?visible|}",
    )

    for target in targets:
        with self.subTest(target=target):
            self.assertTrue(
                self.check.check_single(HUMAN_TIMER_EN, target, None)
            )
```

This covers a lost placeholder, changed placeholder, malformed final block, and
an additional target conditional.

#### Step 6: Lock down the translatable branch and source-gating boundary

```python
def test_allows_localized_conditional_branch_text(self) -> None:
    for target in (HUMAN_TIMER_DE, HUMAN_TIMER_TR):
        with self.subTest(target=target):
            self.assertFalse(
                self.check.check_single(HUMAN_TIMER_EN, target, None)
            )


def test_unrecognized_source_conditional_adds_no_failure(self) -> None:
    source = "{value:cond:1}"

    self.assertFalse(self.check.check_single(source, source, None))
```

Keep the existing empty-target assertion in
`test_placeholder_order_and_printf_tokens_are_preserved()` unchanged.

#### Step 7: Run the focused test and prove it fails before implementation

Run:

```bash
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  -k "conditional_dsl or conditional_syntax or conditional_separators or conditional_headers or conditional_structure" \
  -n 0
```

Expected: FAIL. At minimum, the exact broken French `humanTimer`, changed outer
identifier, changed comparison, and changed `|` structure currently pass
`GameMarkupCheck` and make their new assertions fail.

Do not weaken or delete a failing row to obtain green output. Record any row
that unexpectedly passes under an existing generic placeholder comparison, but
retain it as a public-contract regression if it represents required behavior.

---

### Task 2: Refactor conditional parsing into spans and signatures

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:111-180`
- Test: `weblate_customization/tests/test_checks.py:40-73`

#### Step 1: Split the supported header into immutable fields

Replace `_CONDITIONAL_HEADER` with a narrow pattern for the observed grammar:

```python
_CONDITIONAL_HEADER = regex.compile(
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)"
    r":cond:"
    r"(?P<operator>[<>]=?)"
    r"(?P<operand>[^\s{}?|]+)"
    r"\?"
)
```

This deliberately rejects whitespace, `|`, braces, and `?` inside the operand.
It recognizes `<`, `<=`, `>`, and `>=` only. Do not add equality, boolean, or
text operators without an engine-backed source fixture.

#### Step 2: Add one private parser result helper

Keep allocation bounded to the syntax that must be returned. Do not add a class
hierarchy or a general DSL AST. Add a private helper with this contract:

```python
ConditionalSignature = tuple[str, ...]


def _parse_conditional_dsl(
    text: str,
) -> tuple[list[tuple[int, int]], tuple[ConditionalSignature, ...]]:
    """Return immutable syntax spans and ordered conditional signatures."""
```

Implement it by reusing `_balanced_brace_blocks(text)`:

1. Iterate balanced blocks.
2. Match `_CONDITIONAL_HEADER` immediately after each opening brace and before
   its matching closing brace.
3. Skip blocks without a matching header.
4. Start the signature with:

   ```python
   [
       "{",
       header.group("identifier"),
       ":cond:",
       header.group("operator"),
       header.group("operand"),
       "?",
   ]
   ```

5. Index the block's immediate `children` by start offset.
6. Walk from `header.end()` to the outer closing brace:
   - when the current position starts a child, append the exact
     `text[child_start:child_end]` and jump to `child_end`;
   - when the current character is a top-level `|`, append `"|"` and advance
     one character;
   - otherwise advance one character without appending branch text.
7. Append `"}"`.
8. Retain the existing span contract: outer braces, the complete header, every
   immediate nested placeholder, and every top-level `|` are syntax spans.
9. Sort recognized conditionals by outer start offset before returning their
   signatures. Sort and deduplicate spans before returning them.

The core should remain equivalent to this shape:

```python
def _parse_conditional_dsl(
    text: str,
) -> tuple[list[tuple[int, int]], tuple[ConditionalSignature, ...]]:
    parsed: list[tuple[int, list[tuple[int, int]], ConditionalSignature]] = []

    for start, end, children in _balanced_brace_blocks(text):
        header = _CONDITIONAL_HEADER.match(text, start + 1, end - 1)
        if header is None:
            continue

        spans = [
            (start, start + 1),
            (start + 1, header.end()),
            (end - 1, end),
            *children,
        ]
        signature = [
            "{",
            header.group("identifier"),
            ":cond:",
            header.group("operator"),
            header.group("operand"),
            "?",
        ]
        children_by_start = dict(children)
        position = header.end()

        while position < end - 1:
            child_end = children_by_start.get(position)
            if child_end is not None:
                signature.append(text[position:child_end])
                position = child_end
            elif text[position] == "|":
                spans.append((position, position + 1))
                signature.append("|")
                position += 1
            else:
                position += 1

        signature.append("}")
        parsed.append((start, spans, tuple(signature)))

    parsed.sort(key=lambda item: item[0])
    all_spans = sorted({span for _, spans, _ in parsed for span in spans})
    signatures = tuple(signature for _, _, signature in parsed)
    return all_spans, signatures
```

Before copying this shape verbatim, preserve the existing invariant that spans
are non-overlapping. If a recognized conditional can also appear as a child of
another recognized conditional, skip nested recognized conditionals as
independent records or merge their spans deliberately; do not allow duplicate
or overlapping highlights. The audited grammar contains nested placeholders,
not nested conditionals, so no speculative nested-conditional semantics are
required.

#### Step 3: Make `conditional_dsl_syntax_spans()` consume the shared parser

Replace its embedded conditional walk with:

```python
def conditional_dsl_syntax_spans(text: str) -> list[tuple[int, int]]:
    spans, _signatures = _parse_conditional_dsl(text)
```

Then retain the current adjacent-placeholder colon scan at lines 171-178 and
return the sorted, deduplicated union. Do not move that colon into conditional
signatures: it is a separate highlighting rule for forms such as
`{minutes:00}:{seconds:00}`.

#### Step 4: Run the existing scanner contract tests

Run:

```bash
cp -r \
  weblate_customization/src/weblate_customization \
  dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  -k "ConditionalDslSyntaxSpansTest" \
  -n 0
```

Expected: PASS. Syntax spans remain ordered and non-overlapping, malformed input
returns no conditional spans, nested placeholders remain protected, and German
branch text remains unprotected.

The new `GameMarkupCheck` tests should still fail because `check_single()` has
not consumed signatures yet.

---

### Task 3: Compare conditional signatures in `GameMarkupCheck`

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:183-217`
- Test: `weblate_customization/tests/test_checks.py:76-170`

#### Step 1: Add the minimal signature comparison

Preserve the empty-target early return. Parse the source once, and parse the
target only when the source has at least one recognized signature:

```python
def check_single(self, source: str, target: str, unit) -> bool:
    if not target:
        return False

    _source_spans, source_conditionals = _parse_conditional_dsl(source)
    conditional_mismatch = bool(source_conditionals) and (
        source_conditionals != _parse_conditional_dsl(target)[1]
    )

    return (
        Counter(markup_tokens(source)) != Counter(markup_tokens(target))
        or placeholder_sequence(source) != placeholder_sequence(target)
        or conditional_mismatch
    )
```

Do not normalize signatures, compare branch text, compare only counters, or add
special cases for the audited French strings. The structural sequence is the
contract.

#### Step 2: Keep highlight behavior on the same parser

`check_highlight()` must continue calling `conditional_dsl_syntax_spans()`.
Do not parse `unit.source`; the method's `source` argument is the text whose
coordinates the returned highlights describe.

#### Step 3: Recopy the package and run all focused regressions

Run:

```bash
cp -r \
  weblate_customization/src/weblate_customization \
  dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  -k "conditional_dsl or conditional_syntax or conditional_separators or conditional_headers or conditional_structure" \
  -n 0
```

Expected: PASS.

Confirm specifically from the passing test output:

- both audited French targets fail the check;
- lost, added, and moved `|` fail;
- changed identifier, operator, and operand fail;
- malformed or extra target conditionals fail when the source is recognized;
- German and Turkish branch translations pass;
- `{value:cond:1}` remains outside the new contract.

#### Step 4: Run the complete custom-check test module

Run:

```bash
./rundev.sh test weblate_customization/tests/test_checks.py -n 0
```

Expected: PASS. Existing Unity markup, ordered placeholders, line separators,
numbers, game tokens, Cyrillic leakage, and length checks retain their behavior.

#### Step 5: Commit the implementation slice

Run:

```bash
git add \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "fix(checks): validate conditional game DSL"
```

Do not stage unrelated untracked plans or the ignored container copy.

---

### Task 4: Verify cross-subsystem contracts

**Files:**

- Verify: `weblate_customization/tests/test_autofixes.py`
- Verify: `weblate/trans/tests/test_multilingual_spreadsheet.py`
- Verify: `weblate/machinery/tests.py`
- Modify after all checks pass: `docs/product/plans/2026-08-24-05-conditional-dsl-validation.md`

#### Step 1: Run custom autofix integration tests

Run:

```bash
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py \
  -n 0
```

Expected: PASS. French visible punctuation can still be localized while
conditional syntax highlights remain immutable.

#### Step 2: Run the existing spreadsheet and LLM conditional regressions

Run:

```bash
./rundev.sh test \
  weblate/trans/tests/test_multilingual_spreadsheet.py \
  weblate/machinery/tests.py \
  -k "conditional_dsl or placeholder" \
  -n 0
```

Expected: PASS. Spreadsheet validation still accepts localized `humanTimer`
branch text, and LLM cleanup still restores syntax spans without translating
them.

Do not add new spreadsheet or machinery tests unless an existing contract
actually fails. The implementation changes only the custom check parser and its
boolean validation result.

#### Step 3: Run the repository's configured checks

Run:

```bash
uv run prek run --all-files
```

Expected: PASS. Use the repository's configured `prek` hooks rather than a bare
`ruff` command.

If a hook changes a file outside this plan, stop and inspect before staging.
Never include unrelated fixes in this implementation.

#### Step 4: Perform the required smoke check through the real check entry point

Use the focused pytest regression as the smoke path: it constructs the real
`GameMarkupCheck`, passes the exact audited source and target strings through
`check_single()`, and observes the boolean failure result. Do not substitute a
source-text assertion or a regex-only unit test.

Run once more:

```bash
./rundev.sh test \
  weblate_customization/tests/test_checks.py::GameMarkupCheckTest::test_rejects_whitespace_inside_conditional_syntax \
  -n 0
```

Expected: PASS.

#### Step 5: Mark the plan completed

Change the status at the top of this file from:

```text
Awaiting implementation approval.
```

to:

```text
Completed and verified on 2026-08-24.
```

Do not change `docs/changes.rst`; the existing release note is sufficient.

#### Step 6: Commit the verified plan status

Run:

```bash
git add docs/product/plans/2026-08-24-05-conditional-dsl-validation.md
git commit -m "docs(product): complete conditional DSL validation plan"
```

---

### Task 5: Push without deployment

**Files:**

- Verify only: the two commits created in Tasks 3 and 4

#### Step 1: Confirm only planned tracked files were committed

Inspect the two commits before pushing. They must contain only:

- `weblate_customization/src/weblate_customization/checks.py`;
- `weblate_customization/tests/test_checks.py`;
- `docs/product/plans/2026-08-24-05-conditional-dsl-validation.md` status.

The unrelated untracked documents that may exist in the checkout must remain
untracked and uncommitted.

#### Step 2: Push the current branch

Run:

```bash
git push origin HEAD
```

Expected: the push succeeds.

#### Step 3: Stop before deployment

Do not run `deploy/vps.sh`, restart or rebuild shared containers, execute a
production management command, or rewrite stored translations. Deployment and
production repair require separate explicit approval.

---

## Acceptance criteria

1. The exact broken French `amountFormatted` and `humanTimer` targets from
   `docs/operations/audits/2026-08-24-need-for-greed-multilingual-lqa.md`
   return `True` from `GameMarkupCheck.check_single()`.
2. Lost, added, or moved top-level `|` separators return `True`.
3. Changed identifiers, operators, operands, or nested placeholders return
   `True`.
4. An additional target conditional returns `True` when the source contains a
   recognized conditional.
5. Localized branch text with unchanged syntax returns `False`.
6. A source without a recognized question-mark conditional receives no new
   conditional failure.
7. Existing empty-target, markup, placeholder, highlighting, autofix,
   spreadsheet, and LLM cleanup contracts pass unchanged.
8. `uv run prek run --all-files` passes.
9. Only the planned implementation and test files are committed and pushed.
10. No deployment or production data mutation occurs.
