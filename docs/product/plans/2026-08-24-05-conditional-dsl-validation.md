# Conditional DSL validation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Completed and verified on 2026-08-26.

**Goal:** Make `game-markup` reject parser-breaking changes to Hero Craft conditional DSL while continuing to allow localized branch text.

**Architecture:** Refactor the conditional recognition already used by `conditional_dsl_syntax_spans()` into one non-recursive parser that returns both highlight spans and ordered immutable signatures, without narrowing the grammar it recognizes. `GameMarkupCheck.check_single()` compares those signatures after its existing markup and placeholder comparisons, and only for a source that actually contains a recognized conditional; branch text stays outside the signature and remains translatable.

**Tech Stack:** Python, `regex`, Django/Weblate `TargetCheck`, `unittest` assertions executed by pytest in the shared dev Docker stack.

---

## Context

The source defect is recorded in
`docs/operations/audits/2026-08-24-need-for-greed-multilingual-lqa.md:163-172`:

- French `ui/amountFormatted` (Unit 359515) inserted `U+00A0` before each `:`
  and `U+202F` before each `?`, and was reported only because the nested
  placeholders changed too (`{value:amount()}` became `{value\u00a0:amount()}`).
- French `ui/humanTimer` (Unit 359527) took the same damage but kept
  `{hours}`, `{minutes}`, `{seconds}` intact, so nothing reported it.

`weblate_customization/src/weblate_customization/checks.py:134-180` already
recognizes conditionals and protects their immutable spans;
`GameMarkupCheck.check_single()` at lines 195-200 still compares only markup
multisets and the generic placeholder sequence.
`docs/product/plans/2026-08-22-nested-game-placeholder-protection.md`
deliberately left validation out of scope. This is the narrow follow-up, and it
must reuse that scanner rather than introduce a second grammar.

### Baseline evidence, measured 2026-08-24

Measured by calling the installed `GameMarkupCheck.check_single()` inside the
running dev container. `True` means reported today.

|Target change against its source|Today|Meaning for this plan|
|---|---|---|
|audited French `amountFormatted`|True|already reported, keep it reported|
|audited French `humanTimer`|**False**|the defect this plan exists for|
|ASCII space before `:cond:`|**False**|must flip|
|space inside the comparison, `cond: >0 ?`|**False**|must flip|
|`\|` lost, added, or moved|**False**|must flip|
|`hours` to `hour`|**False**|must flip|
|`>0` to `>=0`, `>0` to `>1`, `<=99999` to `<99999`|**False**|must flip|
|final `}` truncated|**False**|must flip|
|nested placeholder lost or swapped|True|already covered by `placeholder_sequence`|
|extra conditional appended to the target|True|already covered by `placeholder_sequence`|
|German or Turkish branch text|False|must stay `False`|
|`{value:cond:1}` unchanged|False|must stay `False`|

Two more measurements shape the design:

- `_CONDITIONAL_HEADER.match("hours:cond: >0 ?")` succeeds today, so whitespace
  inside the comparison is currently recognized and therefore protected.
- `conditional_dsl_syntax_spans("{a:cond:>0?{b:cond:>0?x|}y|}")` currently
  returns overlapping spans, because the nested conditional is recorded both as
  a child of its parent and as a conditional of its own. The sorted,
  non-overlapping span contract is already broken for that shape.
- The dev corpus has 36 sources containing `:cond:` and zero nested
  conditionals.

### Fixed contract

1. Recognition stays exactly what `_CONDITIONAL_HEADER` accepts today. The
   grammar is **not** narrowed, because the same recognition feeds the
   protected ranges used by `AddFrenchPunctuationSpacing`
   (`weblate_customization/src/weblate_customization/autofixes.py:173-183`) and
   by LLM placeholder protection (`weblate/machinery/llm.py:1780`). A narrower
   header would silently unprotect forms those two subsystems must not touch.
2. A recognized conditional in the source defines the contract for the target.
3. The complete ordered sequence of signatures must match. An added or removed
   conditional therefore fails.
4. One signature is the exact header text, then, in source order, every
   immediate nested placeholder verbatim and every top-level `|`.
5. Branch text is not in the signature. `h.` may become `Std.`, `godz.`, `sa.`,
   or `dk.`.
6. Nothing is normalized. ASCII space, `U+00A0`, and `U+202F` are significant:
   whitespace inside a header either changes the exact header text or makes the
   header unrecognized, and both fail.
7. Only the outermost recognized conditional is a record. A nested one travels
   verbatim inside its parent's signature, which keeps spans non-overlapping
   and keeps every branch interior walked exactly once, instead of re-walking a
   parent interior for each conditional nested in it. The deliberate
   consequence is that branch text inside a nested conditional is immutable.
   The dev corpus contains no nested conditional, and over-protection is the
   safe direction for a string the engine parses.
8. A source without `:cond:`, or with no recognized conditional, gets no new
   failure. `{value:cond:1}` keeps its current behavior.
9. An empty target keeps its current behavior and never fails.
10. The existing markup and placeholder comparisons are unchanged and are
    evaluated first, so nothing new is computed for a string that already
    fails.

### Implementation boundaries

- Work in the existing checkout and the shared `dev-docker` stack. Do not
  create a second stack or a worktree: `dev-docker` publishes fixed host ports
  and its Compose project name comes from the directory, so a copy collides
  instead of isolating.
- The container imports `weblate_customization` from `dev-docker/data/python/`.
  Copy the package there after every edit. That copy is ignored by Git and is
  never staged.
- Prefer node IDs over `-k` in every verification command. A mistyped `-k`
  expression deselects everything and still reports success; a mistyped node ID
  fails loudly. Read the reported test count after each run.
- Do not touch `weblate.trans.protected_tokens`, the autofixes, machinery,
  spreadsheet validation, check registration, deployment configuration, stored
  translations, or the LQA audit.
- Do not run anything against production. Counting nested conditionals in the
  live corpus is a separate operational task that needs its own approval; the
  dev-corpus measurement above is the evidence this plan relies on.
- Use @superpowers:test-driven-development for Tasks 1 to 4 and
  @superpowers:verification-before-completion for Task 5.

---

### Task 1: Make the scanner contract tests run at all

`ConditionalDslSyntaxSpansTest` is never collected. `CheckTestCase`
(`weblate/checks/tests/test_checks.py:26`) is `SimpleTestCase, ABC` with an
abstract `check`, and the scanner class defines no `check`, so the class stays
abstract and pytest collects none of its methods. Both existing span tests are
dead, which is exactly the safety net Task 3 needs.

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:9-21`, `:40`

#### Step 1: Prove the class is dead

Run:

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  -n 0 -q
```

Expected: an error, not a pass. pytest reports
`ERROR: not found: .../test_checks.py::ConditionalDslSyntaxSpansTest`.

#### Step 2: Base the scanner tests on `SimpleTestCase`

The sibling module already does this
(`weblate_customization/tests/test_autofixes.py:9,26`), so follow it exactly.
Add the import in the same group and position:

```python
from django.test import SimpleTestCase
from weblate_customization.checks import (
```

Then change the class statement only:

```python
class ConditionalDslSyntaxSpansTest(SimpleTestCase):
```

Leave `CheckTestCase` imported: `GameMarkupCheckTest` and the other check
classes still use it.

#### Step 3: Run the now-live tests

Run:

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  -n 0 -q
```

Expected: `2 passed`. Both assertions were verified against the current
implementation before this plan was written, so a failure here means the import
or class change is wrong, not that the scanner regressed.

#### Step 4: Commit

```bash
git add weblate_customization/tests/test_checks.py
git commit -m "test(checks): collect the conditional scanner contract tests"
```

---

### Task 2: Add the failing regressions

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:24-37`, `:40-73`, `:76-140`

#### Step 1: Add the audited and nested fixtures

Place these after `HUMAN_TIMER_DE`. The invisible spaces are written as escapes
so a reviewer does not have to trust font rendering; they are byte-for-byte the
audited targets:

```python
AMOUNT_FORMATTED_FR = (
    "{value\u00a0:cond\u00a0:>99999\u202f?{value\u00a0:amount()}|}"
    "{value\u00a0:cond\u00a0:<=99999\u202f?{value\u00a0:N0}|}"
)
HUMAN_TIMER_FR = (
    "{hours\u00a0:cond\u00a0:>0\u202f?{hours}h. |}"
    "{minutes\u00a0:cond\u00a0:>0\u202f?{minutes}m. |}"
    "{seconds\u00a0:cond\u00a0:>=0\u202f?{seconds}s.|}"
)
HUMAN_TIMER_TR = (
    "{hours:cond:>0?{hours} sa. |}"
    "{minutes:cond:>0?{minutes} dk. |}"
    "{seconds:cond:>=0?{seconds} sn.|}"
)
NESTED_CONDITIONAL = "{a:cond:>0?{b:cond:>0?x|}y|}"
```

The audited strings also carry one trailing space after the last `|}`, which
`HUMAN_TIMER_EN` does not. Do not add it: it is branch-external text that no
part of this contract inspects, and keeping the existing constant keeps the
existing tests honest.

#### Step 2: Add the nested span regression

Add to `ConditionalDslSyntaxSpansTest`:

```python
    def test_a_nested_conditional_is_one_outermost_record(self) -> None:
        spans = conditional_dsl_syntax_spans(NESTED_CONDITIONAL)
        inner = NESTED_CONDITIONAL.index("{b")
        inner_block = "{b:cond:>0?x|}"

        self.assertEqual(spans, sorted(spans))
        self.assertTrue(all(left[1] <= right[0] for left, right in pairwise(spans)))
        # The nested conditional travels as one child span, not as a second
        # record whose own header span overlaps its parent's child span.
        self.assertIn((inner, inner + len(inner_block)), spans)
        self.assertNotIn((inner + 1, inner + 1 + len("b:cond:>0?")), spans)
```

#### Step 3: Add the check regressions

Add to `GameMarkupCheckTest`. Every row returns `False` today, and therefore
fails before Task 4, except the first: the audited `amountFormatted` pair
already returns `True` because its nested placeholders changed as well. It is
kept so both audited defects are pinned by name, not because it is new
coverage.

```python
    def test_rejects_whitespace_inside_conditional_syntax(self) -> None:
        for source, target in (
            # Already reported today through the nested placeholders.
            (AMOUNT_FORMATTED, AMOUNT_FORMATTED_FR),
            (HUMAN_TIMER_EN, HUMAN_TIMER_FR),
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("hours:cond", "hours :cond", 1)),
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("cond:>0?", "cond: >0 ?", 1)),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(source, target, None))

    def test_rejects_changed_conditional_separators(self) -> None:
        for target in (
            HUMAN_TIMER_EN.replace("h. |}", "h. }", 1),
            HUMAN_TIMER_EN.replace("h. |}", "h. ||}", 1),
            HUMAN_TIMER_EN.replace("{hours}h. |", "|{hours}h. ", 1),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(HUMAN_TIMER_EN, target, None))

    def test_rejects_changed_conditional_headers(self) -> None:
        for source, target in (
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("hours:cond", "hour:cond", 1)),
            (
                HUMAN_TIMER_EN,
                HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>=0?", 1),
            ),
            (
                HUMAN_TIMER_EN,
                HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>1?", 1),
            ),
            (AMOUNT_FORMATTED, AMOUNT_FORMATTED.replace(":<=99999?", ":<99999?", 1)),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(source, target, None))

    def test_rejects_a_malformed_conditional_target(self) -> None:
        self.assertTrue(
            self.check.check_single(HUMAN_TIMER_EN, HUMAN_TIMER_EN[:-1], None)
        )

    def test_a_nested_conditional_branch_is_immutable(self) -> None:
        # Documented consequence of recording only the outermost conditional.
        self.assertTrue(
            self.check.check_single(
                NESTED_CONDITIONAL, NESTED_CONDITIONAL.replace("?x|", "?z|", 1), None
            )
        )
        self.assertFalse(
            self.check.check_single(
                NESTED_CONDITIONAL, NESTED_CONDITIONAL.replace("|}y|", "|}z|", 1), None
            )
        )

    def test_allows_localized_conditional_branch_text(self) -> None:
        self.assertFalse(self.check.check_single(HUMAN_TIMER_EN, HUMAN_TIMER_TR, None))

    def test_unrecognized_source_conditional_adds_no_failure(self) -> None:
        for text in ("{value:cond:1}", "Text {value:00}: text"):
            with self.subTest(text=text):
                self.assertFalse(self.check.check_single(text, text, None))
```

Do not add a German row to `test_allows_localized_conditional_branch_text`:
`test_conditional_dsl_highlights_leave_branch_text_translatable`
(`weblate_customization/tests/test_checks.py:140`) already asserts
`check_single(HUMAN_TIMER_EN, HUMAN_TIMER_DE, unit)` is `False`. Turkish adds a
second abbreviation shape, German would only duplicate.

Do not add rows for a lost, swapped, or added nested placeholder. They pass
today through `placeholder_sequence`, so they would prove nothing about this
change; `test_rejects_a_malformed_conditional_target` is the boundary guard that
does not overlap.

#### Step 4: Run the new tests and confirm they fail

```bash
cp -r \
  weblate_customization/src/weblate_customization \
  dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  weblate_customization/tests/test_checks.py::GameMarkupCheckTest \
  -n 0 -q
```

Expected collection: `32 tests`, that is 3 in `ConditionalDslSyntaxSpansTest`
(2 pre-existing plus the nested one) and 29 in `GameMarkupCheckTest` (22
pre-existing plus the 7 added here). A smaller number means a method landed in
the wrong class or a class name is misspelled.

Expected: failures in
`test_a_nested_conditional_is_one_outermost_record`,
`test_rejects_whitespace_inside_conditional_syntax`,
`test_rejects_changed_conditional_separators`,
`test_rejects_changed_conditional_headers`,
`test_rejects_a_malformed_conditional_target`, and the first assertion of
`test_a_nested_conditional_branch_is_immutable`.

`test_allows_localized_conditional_branch_text` and
`test_unrecognized_source_conditional_adds_no_failure` must pass already. They
are the guards against a fix that reports everything.

Never delete or weaken a row to reach green output.

---

### Task 3: Return spans and signatures from one non-recursive parser

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:134-180`

Leave `_CONDITIONAL_HEADER` (line 111) exactly as it is. Narrowing it would
shrink the protected ranges that the French spacing autofix and the LLM cleanup
read out of `check_highlight()`, which is a regression in the opposite
direction from this plan's goal.

The code below was executed against the installed `_balanced_brace_blocks()`
and `_CONDITIONAL_HEADER` inside the dev container before this plan was
written. Over 80 texts, the fixtures of this plan plus every dev-corpus source
and target containing `:cond:`, it returns spans identical to the installed
scanner in all but one case: the synthetic nested fixture, where it removes the
overlap. Highlighting therefore does not change for any real string.

#### Step 1: Add the parser

Insert `_parse_conditional_dsl()` directly above
`conditional_dsl_syntax_spans()`:

```python
def _parse_conditional_dsl(
    text: str,
) -> tuple[list[tuple[int, int]], tuple[tuple[str, ...], ...]]:
    """
    Return immutable conditional spans and the ordered conditional signatures.

    A signature holds the exact header, every immediate nested placeholder and
    every top-level delimiter, in source order, and no branch text. Only the
    outermost recognized conditional is a record: a nested one travels verbatim
    inside its parent, which keeps spans non-overlapping and walks every branch
    interior once.
    """
    spans: list[tuple[int, int]] = []
    signatures: list[tuple[str, ...]] = []
    recognized_end = 0

    for start, end, children in sorted(
        _balanced_brace_blocks(text), key=lambda block: block[0]
    ):
        if start < recognized_end:
            continue
        header = _CONDITIONAL_HEADER.match(text, start + 1, end - 1)
        if header is None:
            continue

        # The header's comparison cannot cross a nested placeholder. The
        # matching outer brace proved every nested placeholder is complete.
        spans.extend(
            (
                (start, start + 1),
                (start + 1, header.end()),
                (end - 1, end),
            )
        )
        spans.extend(children)

        signature = [text[start + 1 : header.end()]]
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

        signatures.append(tuple(signature))
        recognized_end = end

    return spans, tuple(signatures)
```

Three details are load-bearing:

- `_balanced_brace_blocks()` returns a block when its closing brace is read, so
  a nested block precedes its parent. Sorting by start is what makes
  "outermost wins" and the signature order correct.
- `recognized_end` is updated only for a recognized conditional, so a
  conditional nested inside an ordinary brace group is still recorded.
- The signature holds no constant brace characters. Every recognized block has
  them, so they would carry no information.

Cost, stated precisely so nobody has to guess: `_balanced_brace_blocks()` is
one pass over the text, the `sorted()` call is `O(B log B)` in the number of
balanced brace blocks, and the branch interiors walked afterwards are disjoint,
so they cost one more pass over the text in total. `check_single()` reaches none
of this unless the source contains `:cond:`.

#### Step 2: Make the public scanner consume the parser

Replace the body of `conditional_dsl_syntax_spans()` down to the
`simple_placeholders` list with one call, and keep everything from
`simple_placeholders` onwards byte-for-byte:

```python
def conditional_dsl_syntax_spans(text: str) -> list[tuple[int, int]]:
    """
    Return immutable spans in the documented Hero Craft conditional DSL.

    Branch text remains unprotected so it can be translated. Nested brace
    placeholders, delimiters, and a directly adjacent placeholder separator
    are syntax rather than rendered text.
    """
    spans, _signatures = _parse_conditional_dsl(text)
```

The adjacent-placeholder colon scan at lines 171-178 stays a separate rule and
never enters a signature: it protects the `:` in `{minutes:00}:{seconds:00}`,
which is not part of any conditional. The final
`return sorted({span for span in spans if span[0] < span[1]})` is unchanged and
is what deduplicates the raw list the parser returns.

#### Step 3: Run the scanner tests

```bash
cp -r \
  weblate_customization/src/weblate_customization \
  dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  -n 0 -q
```

Expected: `3 passed`. The nested regression from Task 2 now passes, and the two
pre-existing contract tests still do.

The `GameMarkupCheckTest` regressions must still fail: nothing consumes the
signatures yet.

---

### Task 4: Compare signatures in `GameMarkupCheck`

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:195-200`

#### Step 1: Replace `check_single()`

```python
    def check_single(self, source: str, target: str, unit) -> bool:
        if not target:
            return False
        if Counter(markup_tokens(source)) != Counter(markup_tokens(target)):
            return True
        if placeholder_sequence(source) != placeholder_sequence(target):
            return True
        if ":cond:" not in source:
            return False

        source_conditionals = _parse_conditional_dsl(source)[1]
        return bool(source_conditionals) and (
            source_conditionals != _parse_conditional_dsl(target)[1]
        )
```

The order matters for cost, not only for style. `check_single()` runs for every
unit of every check pass, `_balanced_brace_blocks()` is a per-character Python
loop, and almost no string contains `:cond:`. The substring test is a C-level
scan that skips the parser entirely, and the two early returns keep a string
that already fails from paying for it.

Do not normalize a signature, compare branch text, compare counters instead of
sequences, or special-case the audited French strings.

#### Step 2: Leave `check_highlight()` alone

It must keep calling `conditional_dsl_syntax_spans()` on its `source` argument,
which is the text whose coordinates the returned highlights describe. Do not
switch it to `unit.source`, and do not have it call the parser directly.

#### Step 3: Run every regression

```bash
cp -r \
  weblate_customization/src/weblate_customization \
  dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalDslSyntaxSpansTest \
  weblate_customization/tests/test_checks.py::GameMarkupCheckTest \
  -n 0 -q
```

Expected: `32 passed`, the same 32 that were collected in Task 2.

#### Step 4: Run the whole custom check module

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py -n 0 -q
```

Expected: `156 passed`, that is the 146 collected before this work, plus the 2
scanner tests Task 1 brought back to life, plus the nested span test, plus the
7 check regressions. Unity markup, ordered placeholders, line separators,
numbers, game tokens, Cyrillic leakage, and length checks keep their behavior.

#### Step 5: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "fix(checks): validate conditional game DSL"
```

Stage nothing else. `dev-docker/data/python/` is ignored, and unrelated
untracked documents stay untracked.

---

### Task 5: Verify the neighbouring subsystems and record the change

**Files:**

- Verify: `weblate_customization/tests/test_autofixes.py`
- Verify: `weblate/trans/tests/test_multilingual_spreadsheet.py:111`
- Verify: `weblate/machinery/tests.py:7640`
- Modify: `docs/changes.rst:47`

#### Step 1: Run the custom autofixes

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_autofixes.py -n 0 -q
```

Expected: all pass. `AddFrenchPunctuationSpacing` reads protected ranges from
`highlight_string()`, so this is the test that would catch a shrunken
recognition surface.

#### Step 2: Run the spreadsheet and LLM conditional contracts by node ID

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  "weblate/trans/tests/test_multilingual_spreadsheet.py::MultilingualSpreadsheetExportTest::test_preview_accepts_translated_conditional_branches" \
  "weblate/machinery/tests.py::ConditionalDslLLMTranslationTest::test_translate_preserves_conditional_dsl_syntax" \
  -n 0 -q
```

Expected: `2 passed`. Node IDs are mandatory here. A `-k` expression such as
`conditional_dsl or placeholder` matches the machinery test but not
`test_preview_accepts_translated_conditional_branches`, so it would deselect the
spreadsheet contract and still print a green summary.

Add no new tests in these two files unless one of them actually fails. This
change touches the custom parser and one boolean result, nothing they own.

#### Step 3: Add the changelog entry

A check that starts reporting strings it used to pass is user-visible, and every
check change in this release carries its own bullet
(`docs/changes.rst:30,31,45,46,47`). The existing conditional-DSL entry on line
51 is about the autofix and machine translation not modifying syntax, not about
the check reporting it, so it is not a substitute.

Append one bullet as the last entry of the `.. rubric:: Improvements` section,
directly after the `game-length` bullet on line 47:

```rst
* The ``game-markup`` check now also compares the syntax of Hero Craft conditional placeholders such as ``{hours:cond:>0?{hours}h. |}``: a space inserted inside a conditional header, a lost, added or moved ``|``, and a changed identifier, comparison or nested placeholder are reported, while the text of a conditional branch stays translatable.
```

Keep it on one line, keep the closing stop, and do not edit any released
section.

#### Step 4: Run the configured hooks

Name the hooks and the files. `prek` is the entry point; a bare `ruff`
invocation is not guaranteed to exist in this environment.

```bash
uv run prek run ruff-check ruff-format --files \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
uv run prek run trailing-whitespace end-of-file-fixer rst-double-space \
  sphinx-lint codespell --files docs/changes.rst
```

Expected: pass, with the formatter free to reflow the code it owns.

Do **not** treat `uv run prek run --all-files` as the gate here. Two hooks
ignore a `--files` restriction and scan or fix the whole tree: `typos`
(`pass_filenames: false`) currently reports about 130 pre-existing findings in
`analysis/data/`, the LQA audits, and unrelated plans, and `ruff-check --fix`
rewrites files outside the given set. The open cleanup for those findings is
`docs/product/plans/2026-08-23-prek-full-pass-remaining-hooks.md`; do not fold
it into this change. If a hook rewrites a file this plan does not name, restore
it before staging.

`rst-bullet-stop` is deliberately absent from the second command: it already
fails on the pre-existing multi-line bullet at `docs/changes.rst:33-36`, whose
first line cannot end with a stop. Keep the new entry on one line ending with a
stop, which is what that hook wants, and do not reformat line 33 to make the
hook green.

#### Step 5: Smoke test through the real entry point

The proof is the audited pair travelling through the constructed check, not a
regex assertion:

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  "weblate_customization/tests/test_checks.py::GameMarkupCheckTest::test_rejects_whitespace_inside_conditional_syntax" \
  -n 0 -q
```

Expected: `1 passed`.

#### Step 6: Commit

```bash
git add docs/changes.rst
git commit -m "docs(changes): note conditional DSL syntax validation"
```

---

### Task 6: Close the plan and push

**Files:**

- Modify: `docs/product/plans/2026-08-24-05-conditional-dsl-validation.md:5`

#### Step 1: Mark the plan completed

Replace the status line with:

```text
**Status:** Completed and verified on 2026-08-24.
```

#### Step 2: Confirm the commit contents

The branch must carry exactly four commits from this plan, touching only:

- `weblate_customization/tests/test_checks.py`;
- `weblate_customization/src/weblate_customization/checks.py`;
- `docs/changes.rst`;
- this plan file.

#### Step 3: Commit and push

```bash
git add docs/product/plans/2026-08-24-05-conditional-dsl-validation.md
git commit -m "docs(product): complete conditional DSL validation plan"
git push origin HEAD
```

#### Step 4: Stop before deployment

Do not run `deploy/vps.sh`, restart or rebuild the shared containers, run a
management command against production, or rewrite stored translations. The
French units 359515 and 359527 will now report `game-markup` until the separate
correction plan repairs them, and the audit explicitly forbids silencing them
with `ignore-game-markup`.

---

## Acceptance criteria

1. Both audited French targets return `True` from
   `GameMarkupCheck.check_single()`.
2. A space inside a conditional header, in any of ASCII, `U+00A0`, or `U+202F`,
   returns `True`.
3. A lost, added, or moved top-level `|` returns `True`.
4. A changed identifier, comparison, or nested placeholder returns `True`.
5. A malformed target conditional returns `True` while the source is
   recognized.
6. Localized branch text with unchanged syntax returns `False`, including the
   German row already asserted at `weblate_customization/tests/test_checks.py:140`.
7. A source without a recognized conditional gains no failure, and no string
   without `:cond:` reaches the parser.
8. `conditional_dsl_syntax_spans()` returns sorted, non-overlapping spans for a
   nested conditional, which it does not do today.
9. `_CONDITIONAL_HEADER` still accepts everything it accepts today, so the
   autofix and LLM protection surface does not shrink.
10. `weblate_customization/tests/test_checks.py` reports 156 passing tests, and
    both named cross-subsystem node IDs pass.
11. The named hooks pass for the three changed files, and no unrelated file is
    left modified.
12. Only the four files named in Task 6 are committed, and nothing is deployed.
