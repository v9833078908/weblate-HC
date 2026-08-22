# Conditional game DSL syntax protection implementation plan

**Goal:** Prevent French punctuation spacing and machine-translation cleanup from
altering Hero Craft conditional-DSL syntax, without treating the translated
display text inside a conditional as an immutable placeholder.

**Architecture:** Add a linear, Hero-Craft-specific conditional-DSL syntax scanner
in ``weblate_customization.checks``. It emits only immutable syntax spans:
conditional headers and operators, nested brace placeholders, and structural
delimiters. Text rendered in a conditional branch remains outside every span and
may be translated. ``GameMarkupCheck.check_highlight()`` exposes these spans as
``syntax`` highlights, which makes the existing shared highlight consumers,
including ``AddFrenchPunctuationSpacing`` and LLM cleanup, leave DSL syntax
unchanged.

The generic ``weblate.trans.protected_tokens`` helpers keep their current flat
placeholder contract. In particular, this work must not redefine
``placeholder_sequence()``, alter multilingual-spreadsheet validation, or make
an outer conditional expression byte-identical as a whole. That would reject
the legitimate German branch translations in ``humanTimer``.

**Tech Stack:** Python 3.10, Django/Weblate check and autofix registries, pytest
in the existing dev-docker container.

---

## Preconditions and boundaries

- Work in the main checkout. Do not create or restart a second ``dev-docker``
  stack: its ports and Compose project are shared.
- The running container loads ``weblate_customization`` from
  ``dev-docker/data/python/``. Copy the package after changing it; never add
  that ignored directory to Git.
- The production check and autofix registrations already exist in
  ``dev-docker/docker-compose.yml``. Do not alter configuration or deployment
  files.
- Before implementation, record the engine owner's answer in the issue or
  implementation notes: the conditional header and its delimiters are syntax;
  branch text is rendered and translatable. Do not add a new outer-DSL
  byte-level validation rule until that grammar is formally specified.
- The LQA report's two confirmed French defects (``amountFormatted`` and
  ``timer``) already fail the existing ``GameMarkupCheck`` through their nested
  placeholders. This change prevents a future save from creating the same
  corruption; it does not repair stored translations.
- ``humanTimer`` remains a runtime follow-up, not a newly enforced check. Its
  German branch text (``h.`` to ``Std.``, ``m.`` to ``Min.``, and ``s.`` to
  ``Sek.``) is a required regression fixture.

## Task 1: Add a linear conditional-DSL syntax scanner

**Files:**

- Modify: ``weblate_customization/src/weblate_customization/checks.py``
- Modify: ``weblate_customization/tests/test_checks.py``

### Step 1: Write the failing scanner tests

Add focused tests for a public module-level helper, named
``conditional_dsl_syntax_spans(text)``. It returns ordered, non-overlapping
``(start, end)`` spans and never includes translated branch text.

Use these source fixtures from the audited workbook:

```python
AMOUNT_FORMATTED = (
    "{value:cond:>99999?{value:amount()}|}{value:cond:<=99999?{value:N0}|}"
)
TIMER = "{hours:cond:>0?{hours:00}:|}{minutes:00}:{seconds:00}"
HUMAN_TIMER_EN = (
    "{hours:cond:>0?{hours}h. |}"
    "{minutes:cond:>0?{minutes}m. |}"
    "{seconds:cond:>=0?{seconds}s.|}"
)
HUMAN_TIMER_DE = (
    "{hours:cond:>0?{hours}Std. |}"
    "{minutes:cond:>0?{minutes}Min. |}"
    "{seconds:cond:>=0?{seconds}Sek.|}"
)
```

Assert all of the following:

1. The scanner protects the conditional header, comparison, ``?``, ``|``,
   opening and closing structural braces, and every nested ``{...}``
   placeholder in ``AMOUNT_FORMATTED`` and ``TIMER``.
2. It does **not** protect ``h.`` / ``m.`` / ``s.`` in ``HUMAN_TIMER_EN`` or
   ``Std.`` / ``Min.`` / ``Sek.`` in ``HUMAN_TIMER_DE``.
3. Concatenated conditionals remain separate and source ordered.
4. A malformed or unclosed outer brace produces no partial conditional span
   and completes in linear time. Test ``"{" * 100_000`` with a deliberately
   generous one-second ceiling.
5. Existing simple placeholders, Unity tags, percent keys, and printf tokens
   retain their present ``GameMarkupCheck`` behavior. This helper is additive;
   it does not replace ``placeholder_sequence()`` or ``markup_tokens()``.

Run:

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py -k conditional_dsl -n 0
```

Expected: FAIL because the scanner does not exist.

### Step 2: Implement only the documented conditional syntax

In ``weblate_customization/src/weblate_customization/checks.py``:

1. Add a private linear scanner for balanced braces. It must advance its input
   index once, never recurse, and never restart at each opening brace.
2. Recognise a conditional only when its balanced outer block has the
   observed shape:

   ```text
   {identifier:cond:comparison?branch[|alternate]}
   ```

   ``identifier``, ``:cond:``, the comparison, ``?``, the optional ``|``, and
   structural braces are syntax. A nested ``{...}`` is an opaque syntax
   placeholder. Everything else in a branch is visible, translatable text.
3. Return only non-empty source ranges. The ranges may be adjacent but must
   not overlap. Do not emit a whole outer block.
4. Return no conditional spans for malformed input. Leave existing generic
   placeholder parsing to ``weblate.trans.protected_tokens``.

Keep the grammar intentionally narrow. A new conditional form or operator
requires a fixture from an engine-supported string and an explicit extension
of this scanner, not a permissive catch-all brace rule.

### Step 3: Check the focused tests

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py -k "conditional_dsl or placeholder" -n 0
```

Expected: PASS. The scanner is linear, nested placeholders are syntax, and
branch text is not protected.

## Task 2: Publish syntax highlights without changing validation semantics

**Files:**

- Modify: ``weblate_customization/src/weblate_customization/checks.py``
- Modify: ``weblate_customization/tests/test_checks.py``
- Modify: ``weblate_customization/tests/test_autofixes.py``

### Step 1: Add failing highlight and autofix regressions

Add tests that exercise the registered highlight path, not only the scanner:

1. ``GameMarkupCheck.check_highlight()`` returns ``syntax`` highlights for the
   immutable pieces of ``AMOUNT_FORMATTED`` and ``TIMER``.
2. No highlight contains ``Std.``, ``Min.``, or ``Sek.`` for
   ``HUMAN_TIMER_DE``.
3. ``GameMarkupCheck.check_single(HUMAN_TIMER_EN, HUMAN_TIMER_DE, unit)`` is
   still false. The scanner must not create a new validation failure for
   translated branch text.
4. In ``AddFrenchPunctuationSpacingTest``, start with a valid conditional and
   a visible French colon:

   ```python
   source = f"Amount: {AMOUNT_FORMATTED}"
   target = f"Montant: {AMOUNT_FORMATTED}"
   ```

   The autofix may add ``U+00A0`` before the visible ``Montant:`` colon, but
   must leave every byte of ``AMOUNT_FORMATTED`` unchanged.
5. Add a ``TIMER``-only target test. The autofix must make no change inside
   its DSL punctuation.

Run:

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py weblate_customization/tests/test_autofixes.py -k "conditional_dsl or french" -n 0
```

Expected: FAIL because ``GameMarkupCheck`` currently supplies no highlights.

### Step 2: Implement ``GameMarkupCheck.check_highlight``

Import ``Highlight`` from ``weblate.checks.base`` and combine:

- existing Unity markup spans as ``kind="markup"``;
- existing simple brace, percent-key, and printf placeholders as
  ``kind="syntax"``;
- ``conditional_dsl_syntax_spans(source)`` as ``kind="syntax"``.

Deduplicate and sort spans before constructing ``Highlight`` instances so the
shared ``merge_highlight_spans()`` receives stable, non-overlapping intervals.
Return no highlights when ``should_skip(unit)`` is true.

Do not use ``kind="grammar"`` for an immutable engine token. The LLM structured
translation path permits grammar placeholders to be reordered, while markup
and syntax tokens are protected.

Do not modify ``AddFrenchPunctuationSpacing`` or
``PunctuationSpacingCheck``. Both already consume ``highlight_string()``, so
the correction must remain centralised in the syntax provider.

### Step 3: Run integration regressions

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py weblate_customization/tests/test_autofixes.py -n 0
```

Expected: PASS. Visible French punctuation is repaired, but the conditional
syntax is byte-identical.

## Task 3: Prove that imports and LLM cleanup retain their contracts

**Files:**

- Modify: ``weblate/trans/tests/test_multilingual_spreadsheet.py``
- Modify: ``weblate/machinery/tests.py``

### Step 1: Add a spreadsheet compatibility regression

Extend the multilingual spreadsheet tests with a row whose English source is
``HUMAN_TIMER_EN`` and whose German target is ``HUMAN_TIMER_DE``. Build its
preview and assert that it succeeds.

This locks down the essential boundary: spreadsheet validation continues to use
the existing ``placeholder_sequence()`` comparison and does not reject
translated conditional branch text.

### Step 2: Add an LLM cleanup regression

Using the existing deterministic LLM test machinery, create a unit containing
``AMOUNT_FORMATTED`` alongside visible text. Assert that cleanup replaces every
conditional syntax highlight with an opaque placeholder and restores the exact
source after uncleanup. Assert that every emitted structured placeholder part
is ``kind="syntax"`` and ``translatable=False``.

This test must not make a network request.

### Step 3: Run cross-subsystem tests

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py \
  weblate/trans/tests/test_multilingual_spreadsheet.py \
  weblate/machinery/tests.py -k "conditional_dsl or multilingual_spreadsheet or placeholder" -n 0
```

Expected: PASS. The scanner does not widen spreadsheet rejection, and LLM
cleanup treats DSL syntax as fixed rather than reorderable grammar.

## Task 4: Release notes, verification, and delivery

1. Add one concise bullet under the current unreleased ``Bug fixes`` rubric in
   ``docs/changes.rst``:

   ```rst
   * French punctuation spacing and automatic translation no longer modify
     syntax in Hero Craft conditional game placeholders.
   ```

2. Re-copy only the customization package into the dev container:

   ```bash
   cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
   ```

3. Run the complete affected test set serially:

   ```bash
   ./rundev.sh test \
     weblate_customization/tests/test_checks.py \
     weblate_customization/tests/test_autofixes.py \
     weblate/trans/tests/test_multilingual_spreadsheet.py \
     weblate/machinery/tests.py -n 0
   ```

4. Run formatting and lint hooks:

   ```bash
   uv run prek run --all-files
   ```

5. Commit the implementation and tests:

   ```bash
   git add \
     weblate_customization/src/weblate_customization/checks.py \
     weblate_customization/tests/test_checks.py \
     weblate_customization/tests/test_autofixes.py \
     weblate/trans/tests/test_multilingual_spreadsheet.py \
     weblate/machinery/tests.py \
     docs/changes.rst
   git commit -m "fix(checks): protect conditional game DSL syntax"
   git push origin HEAD
   ```

6. Do not deploy, restart a container, bulk-rewrite translations, or alter the
   audited XLSX. Those are separate, explicit approvals.

## Out of scope

- Changing ``weblate.trans.protected_tokens.placeholder_sequence()``,
  ``protected_tokens()``, or the generic ``MARKUP`` stripping behavior.
- A byte-level comparison or new validation error for an entire outer
  conditional DSL expression.
- A permissive parser for undocumented Hero Craft DSL forms.
- Bulk repair of existing translations, translation memory, suggestions, or LLM
  output.
- Changes to ``loc_kit_ingest``, deployment configuration, or the engine DSL
  grammar.
