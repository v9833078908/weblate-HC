# Game number value comparison implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** awaiting approval

**Goal:** Make `game-number` compare numeric values for the scale notations used by the measured languages, catch the Japanese and Chinese tenfold errors from `heart-abyss/hub-1`, and add no new firing to the replay corpora.

**Architecture:** Keep the existing URL, markup, date, ordinal, digit-folding and grouping preprocessing. Replace token strings with `Counter[Decimal]`, parse scale words only through a language-specific vocabulary, and parse CJK numeral runs only for CJK languages. Preserve the old silent behavior for a target language whose scale notation is unsupported by subtracting only source occurrences derived from a scale; never infer a scale from an arbitrary word following a number.

**Tech Stack:** Python 3.13, `decimal.Decimal`, `regex`, Weblate `TargetCheck`, pytest in the existing `dev-docker` container.

---

## Why this fix exists

`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` section 3.1 measured two production strings:

| Context | Russian value | Defective target | Actual value |
|---|---:|---|---:|
| `hub1_guard_1_3` | 10,000 | `ja` `10万文` | 100,000 |
| `hub1_guard_1_4` | 100,000 | `ja` `100万文` | 1,000,000 |
| `hub1_guard_1_4` | 100,000 | `zh_Hans` `一百万文` | 1,000,000 |

The current `_numbers` extracts digit tokens. It sees `10` in both `10 тысяч` and `10万`, so the Japanese tenfold error is invisible. It also sees `10` in `10 тысяч` and `10000` in a correct `10,000`, so correct translations fire.

Measured result on the two contexts:

- 11 current firings: 10 false positives and 1 accidental true positive.
- 2 additional Japanese false negatives.
- 3 genuine magnitude defects in total.

The three production strings were already repaired on 2026-08-24. Verification therefore uses the frozen pre-repair values from the measurement and the repaired values as the no-noise side.

## Review corrections incorporated

This revision replaces the rejected design from the first draft:

1. There is no `UNKNOWN_SCALE_NOTATION` and no generic `\p{L}` guard. `10 mon`, `200 durability` and other ordinary number-plus-noun phrases cannot masquerade as scales.
2. Both `values` and `scaled` are `Counter[Decimal]`; repeated quantities stay count-aware.
3. CJK parsing is strict and returns `None` for malformed runs. `Decimal` conversion cannot escape as `InvalidOperation`.
4. Explicit zero is distinct from an omitted numeral, so `0万 == 0`.
5. Plain values are canonical `Decimal` values, so `1000 == 1000.0`.
6. Target abbreviations are parsed only when the source contains a scale-derived quantity. This accepts `10K` for `10 thousand` without reading identifiers such as `245B` in unrelated strings as 245 billion.
7. The implementation is one coherent TDD task; no Task 1 code references symbols defined in Task 2.
8. Rollout runs check recomputation synchronously with `update_state=False` and queries only the selected component.
9. The corpus replay script is committed, the obsolete Part B is removed, and the final task commits and pushes every plan-owned file.

## Proven prototype before implementation

The revised algorithm was executed against the current preprocessing regexes before this plan was written.

| Corpus | Result |
|---|---:|
| Frozen section 3.1 matrix | 18/18 expected verdicts |
| Existing `GameNumberCheckTest` contracts | 25/25 preserved |
| New edge matrix | 11/11 expected verdicts |
| `heart-abyss/hub-1`, `ru -> en,fr` | 0 firings / 792 pairs |
| `st2`, `ru -> zh_Hans` | 0 firings / 124 pairs |
| `col4-b0-annotations`, `ru -> fr` | 1 firing / 260 pairs |

The one retained Col4 firing is `EVENT_516_RESULT_977`: `_x000b_` occurs twice in the source and its `000` fragments are still read as numbers. That is a separate XLSX/control-character ingestion defect and remains out of scope.

## Behavior contract

### Supported comparisons

- Plain numeric values in any Unicode decimal digit script.
- Locale decimal separators already covered by `NUMBER`.
- Locale grouping already covered by `THOUSANDS`.
- Language-specific word scales for `ru`, `en`, `de`, `es`, `fr`, and `it`.
- CJK section and myriad scales for `ja`, `zh`, and `ko`.
- Compound descending word scales such as `1 million 500 thousand`.
- Compound CJK values such as `1万5000`, `2万5千`, `1億5000万`, and `1兆2億`.
- Uppercase adjacent target abbreviations `K/M/B/T` and `К/М/Б/Т`, but only when the source contains a scale-derived quantity.

### Unsupported target languages

For a target language outside the word-scale table and outside CJK:

1. Plain source quantities keep the current check behavior.
2. A source occurrence derived from a scale is removed from `missing` if it remains unmatched.
3. Other missing source occurrences remain failures.

This is deliberate uncertainty, not guessed semantics. For example:

```text
source ru: 10 тысяч мон
tr target: 10 bin mon       -> silent; Turkish scale parsing is unsupported
tr target: 10 mon           -> also silent for this scale-derived source value
source en: Shield 2000
tr target: Kalkan 200       -> fires; the source value was plain, not scale-derived
```

For supported English, `10 тысяч` against `Reward 10 mon` fires because `mon` is not an English scale word. This is the regression that the former generic guard hid.

### Data flow

```text
source / target text
        |
        v
URL + markup + date removal
ordinal removal on source only
Unicode digit folding
thousands-group collapse
        |
        +--> language-specific word-scale spans ----+
        |                                            |
        +--> CJK spans, CJK languages only ----------+--> Counter[Decimal] values
        |                                            |    Counter[Decimal] scaled
        +--> target abbreviation spans, only when ---+
        |    source.scaled is non-empty
        |
        +--> remaining NUMBER tokens as Decimal

missing = source.values - target.values
if target scale notation is unsupported:
    missing = missing - source.scaled
failure = bool(missing)
```

## Ground rules

1. Work in the main checkout on a branch. Do not create a git worktree: `dev-docker` is a shared fixed-port stack.
2. Before every container test, copy `weblate_customization/src/weblate_customization` to `dev-docker/data/python/`.
3. Do not rebuild, restart, or deploy until the separately gated rollout task.
4. Keep the check ID and ignore flag unchanged: `game-number` and `ignore-game-number`.
5. Do not add a numeral-parsing dependency. The supported grammar is small, deterministic and already covered by the installed `regex` module.
6. Do not extend the language vocabulary beyond the reviewed words in this plan during implementation.

## Baseline command

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py
```

Measured baseline: 120 passed, 26 skipped.

---

## Task 1: Write the value-comparison regression tests

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:11-22,189-337`

### Step 1: Add a language-aware unit helper

Import `make_language` beside `make_unit` and add this helper above `GameNumberCheckTest`:

```python
def make_number_unit(
    source: str,
    target: str,
    *,
    source_code: str,
    target_code: str,
):
    unit = make_unit(source=source, target=target, code=target_code)
    unit.translation.component.source_language = make_language(source_code)
    return unit
```

The factory defaults the component source language to English. The helper changes only the unsaved component object used by `GameNumberCheck`; no database write occurs.

### Step 2: Add the frozen 18-row matrix

Add one test using `subTest` and the exact rows below:

```python
def test_scale_values_match_the_measured_matrix(self) -> None:
    source_10k = "10 тысяч мон!"
    source_100k = "Научись считать - ваще та 100 тысяч мон!"
    rows = (
        (source_10k, "Belohnung - 10 Tausend Mon!", "de", False),
        (source_10k, "Reward - 10,000 mon!", "en", False),
        (source_10k, "¡Recompensa: 10 000 mon!", "es", False),
        (source_10k, "Récompense - 10\u202f000 mons\u202f!", "fr", False),
        (source_10k, "Ricompensa - 10 mila mon!", "it", False),
        (source_10k, "보상은 10천 몬!", "ko", False),
        (source_10k, "奖励一万文!", "zh_Hans", False),
        (source_10k, "獎勵一萬文!", "zh_Hant", False),
        (source_10k, "報酬は10万文!", "ja", True),
        (source_100k, "Lern zählen - es sind 100 Tausend Mon!", "de", False),  # codespell:ignore
        (source_100k, "Learn to count, ya dumbass - it's 100,000 mon", "en", False),
        (source_100k, "¡Aprende a contar, son 100 000 mon!", "es", False),
        (source_100k, "Apprends à compter, c'est 100 000 mons", "fr", False),
        (source_100k, "Impara a contare, sono 100 mila mon!", "it", False),
        (source_100k, "계산 좀 배워, 10만 몬이라고!", "ko", False),
        (source_100k, "學會數數吧--那可是十萬文!", "zh_Hant", False),
        (source_100k, "数え方を覚えろよ、こいつは100万文だぜ!", "ja", True),
        (source_100k, "学学数数吧--那可是一百万文!", "zh_Hans", True),
    )
    for source, target, target_code, expected in rows:
        with self.subTest(target_code=target_code, target=target):
            unit = make_number_unit(
                source,
                target,
                source_code="ru",
                target_code=target_code,
            )
            self.assertEqual(
                self.check.check_single(source, target, unit),
                expected,
            )
```

### Step 3: Add the blocking guard regression

```python
def test_an_ordinary_target_noun_is_not_a_scale(self) -> None:
    source = "10 тысяч мон!"
    target = "Reward 10 mon!"
    unit = make_number_unit(source, target, source_code="ru", target_code="en")
    self.assertTrue(self.check.check_single(source, target, unit))
```

This test is mandatory. A generic number-plus-word guard makes it fail.

### Step 4: Add unsupported-language compatibility tests

```python
def test_an_unsupported_target_scale_language_stays_silent(self) -> None:
    source = "10 тысяч мон!"
    for target, code in (
        ("Ödül 10 bin mon!", "tr"),
        ("Thưởng 10 nghìn mon!", "vi"),
        ("Nagroda - 10 tysięcy mon!", "pl"),
        ("Hadiah 10 ribu mon!", "id"),
        ("Beloning - 10 duizend mon!", "nl"),  # codespell:ignore
        ("รางวัล 1 หมื่นมอน!", "th"),
        ("پاداش ۱۰ هزار مون!", "fa"),
        ("इनाम 10 हज़ार मोन!", "hi"),
    ):
        with self.subTest(code=code):
            unit = make_number_unit(source, target, source_code="ru", target_code=code)
            self.assertFalse(self.check.check_single(source, target, unit))


def test_an_unsupported_language_does_not_hide_a_plain_source_error(self) -> None:
    source = "Shield 2000 durability"
    target = "Kalkan 200 dayanıklılık"
    unit = make_number_unit(source, target, source_code="en", target_code="tr")
    self.assertTrue(self.check.check_single(source, target, unit))
```

### Step 5: Add multiset, zero, strictness and canonicalization tests

Add separate tests for these observable contracts:

```python
cases = (
    # Repeated values remain a multiset.
    ("10 thousand + 10 thousand", "10K", "en", "en", True),
    # Unsupported compatibility removes every scale-derived occurrence.
    ("10 thousand + 10 thousand", "Reward", "en", "tr", False),
    # It does not remove a plain occurrence with the same value.
    ("10000 + 10 thousand", "Reward", "en", "tr", True),
    # Explicit zero is not an implicit one.
    ("Reward 0", "報酬は0万文", "en", "ja", False),
    # A malformed run is left to the existing NUMBER tokenizer and never raises.
    ("Reward 1.2.3", "報酬は1.2.3万文", "en", "ja", False),
    # Plain values compare numerically.
    ("Reward 1000", "Reward 1000.0", "en", "en", False),
    # Descending compound word scales form one value.
    ("Reward 1 million 500 thousand", "Reward 1,500,000", "en", "en", False),
    # Large CJK scales use their actual values.
    ("Reward 1,000,000,000", "Reward 10億", "en", "ja", False),
    ("Reward 1 trillion", "報酬は1兆文", "en", "ja", False),
    # Lowercase m stays a unit; uppercase adjacent K is a magnitude abbreviation.
    ("Reward 10 thousand", "Reward 10m", "en", "en", True),
    ("Reward 10 thousand", "Reward 10K", "en", "en", False),
)
```

Drive every row through `make_number_unit` and `check_single`. The malformed case must fail the test if an exception escapes.

### Step 6: Run the red tests

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Expected before implementation:

- The frozen matrix fails on the current false positives and false negatives.
- `test_an_ordinary_target_noun_is_not_a_scale` fails because the current check sees `10` on both sides.
- The decimal canonicalization, compound word-scale, large CJK and lowercase-`m` cases fail.
- Tests that protect already-correct legacy behavior may pass; they are regression tests, not artificial red tests.

Do not assert an exact failing-method count. `subTest` aggregation and the pre-existing base-class checks make that count incidental.

---

## Task 2: Replace token comparison with strict language-aware values

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:15-20,30-55,269-310`
- Test: `weblate_customization/tests/test_checks.py:189-390`

### Step 1: Add the data model and reviewed vocabularies

Add `Decimal` and `InvalidOperation` from `decimal`, and `NamedTuple` from `typing`.

Use language-specific word tables. Do not combine them into one global alternation:

```python
WORD_SCALES: dict[str, dict[str, int]] = {
    "ru": {
        "тыс": 1_000,
        "тысяча": 1_000,
        "тысячи": 1_000,
        "тысяч": 1_000,
        "млн": 1_000_000,
        "миллион": 1_000_000,
        "миллиона": 1_000_000,
        "миллионов": 1_000_000,
    },
    "en": {
        "thousand": 1_000,
        "million": 1_000_000,
        "millions": 1_000_000,
        "billion": 1_000_000_000,
        "billions": 1_000_000_000,
        "trillion": 1_000_000_000_000,
        "trillions": 1_000_000_000_000,
    },
    "de": {
        "tausend": 1_000,
        "million": 1_000_000,
        "millionen": 1_000_000,
    },
    "es": {
        "mil": 1_000,
        "millon": 1_000_000,  # codespell:ignore
        "millón": 1_000_000,
        "millones": 1_000_000,
    },
    "fr": {
        "mille": 1_000,
        "million": 1_000_000,
        "millions": 1_000_000,
    },
    "it": {
        "mila": 1_000,
        "milione": 1_000_000,
        "milioni": 1_000_000,
    },
}

SECTION_SCALES = {
    "十": 10,
    "百": 100,
    "千": 1_000,
    "십": 10,
    "백": 100,
    "천": 1_000,
}
BIG_SCALES = {
    "万": 10_000,
    "萬": 10_000,
    "만": 10_000,
    "億": 100_000_000,
    "亿": 100_000_000,
    "억": 100_000_000,
    "兆": 1_000_000_000_000,
    "조": 1_000_000_000_000,
}
CJK_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "两": 2,
    "兩": 2,
}
CJK_LANGUAGES = frozenset({"ja", "zh", "ko"})
ABBREVIATION_SCALES = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "T": 1_000_000_000_000,
    "К": 1_000,
    "М": 1_000_000,
    "Б": 1_000_000_000,
    "Т": 1_000_000_000_000,
}

class Quantities(NamedTuple):
    values: Counter[Decimal]
    scaled: Counter[Decimal]
```

`scaled` is a `Counter`, not a set. It records how many source occurrences were produced from scale notation.

### Step 2: Compile one word-scale pattern per language

Compile patterns once at import time. Each pattern matches only words from its language table. The number token must be strict: `\d+(?:[.,]\d+)?`.

The extractor may combine adjacent pairs only when:

1. The text between them is whitespace only.
2. Scale factors are strictly descending.

Therefore:

```text
1 million 500 thousand -> 1,500,000
1 thousand 2 million   -> two quantities; malformed ascending compound is not folded
```

Return spans and values rather than rewriting arbitrary text. Plain-number extraction skips consumed spans.

### Step 3: Implement a strict CJK state machine

The CJK parser must enforce these invariants:

- It runs only for base languages `ja`, `zh`, and `ko`.
- A run must contain a section or big scale.
- ASCII decimal fragments use a strict anchored match; `1.2.3万` returns `None`.
- Section scales and big scales descend within their groups.
- A big scale requires an explicit group. Bare `万` and idiomatic `万一` are not quantities.
- `group_seen` distinguishes `0万` from an omitted numeral.
- `InvalidOperation` is caught and returns `None`.

Core big-scale branch:

```python
elif char in BIG_SCALES:
    scale = BIG_SCALES[char]
    if not group_seen or scale >= last_big_scale:
        return None
    group = section + (Decimal(0) if current is None else current)
    total += group * scale
    section = Decimal(0)
    current = None
    group_seen = False
    last_section_scale = float("inf")
    last_big_scale = scale
```

Do not use `group or Decimal(1)`; that converts explicit zero to one.

### Step 4: Extract all quantities as `Decimal`

Replace `_numbers` with `_quantities`:

```python
def _quantities(
    text: str,
    language: str | None,
    *,
    drop_ordinals: bool = False,
    parse_abbreviations: bool = False,
) -> Quantities:
    ...
```

Order:

1. `_fold_digits(URL.sub(" ", text))`.
2. `MARKUP` and `FULL_DATE` removal.
3. Source-only ordinal removal.
4. `_collapse_grouping`.
5. Language-specific word-scale spans.
6. CJK spans for CJK languages.
7. Target abbreviations only when `parse_abbreviations=True`.
8. Remaining `NUMBER` spans converted through `Decimal`.

Every consumed scale span increments both counters. Every remaining plain number increments only `values`.

Normalize probe/API codes through their base prefix:

```python
def _base_language(code: str | None) -> str | None:
    if code is None:
        return None
    return code.split("_", 1)[0].split("-", 1)[0]
```

### Step 5: Implement one authoritative comparison function

Add a public pure function so the Weblate adapter and offline probes use the same verdict:

```python
def game_number_fails(
    source: str,
    target: str,
    *,
    source_language: str | None,
    target_language: str | None,
) -> bool:
    source_code = _base_language(source_language)
    target_code = _base_language(target_language)
    source_quantities = _quantities(
        source,
        source_code,
        drop_ordinals=True,
    )
    if not source_quantities.values or not target:
        return False

    target_quantities = _quantities(
        target,
        target_code,
        parse_abbreviations=bool(source_quantities.scaled),
    )
    missing = source_quantities.values - target_quantities.values

    target_understands_scales = (
        target_code in WORD_SCALES or target_code in CJK_LANGUAGES
    )
    if not target_understands_scales:
        missing -= source_quantities.scaled

    return bool(missing)
```

This is the complete compatibility rule. Do not add a generic unknown-word regex beside it.

### Step 6: Adapt `GameNumberCheck`

```python
def check_single(self, source: str, target: str, unit) -> bool:
    if unit is None:
        source_language = target_language = None
    else:
        source_language = unit.translation.component.source_language.base_code
        target_language = unit.translation.language.base_code
    return game_number_fails(
        source,
        target,
        source_language=source_language,
        target_language=target_language,
    )
```

The `unit is None` path preserves the existing direct tests for plain numbers. Production always supplies a unit through `TargetCheck.check_target_unit`.

Update the class docstring to state:

- Values are compared as a multiset of `Decimal`.
- Word scales are language-specific.
- Unsupported target scale languages skip only scale-derived source occurrences.
- Entirely spelled-out number words remain unsupported.

### Step 7: Run the focused tests

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Expected: all existing and new `GameNumberCheckTest` tests pass.

### Step 8: Prove the regression tests defend the critical branches

Temporarily make each mutation, run the named test, then restore the implementation before continuing:

| Mutation | Test that must fail |
|---|---|
| Treat any word after a number as an unknown scale | `test_an_ordinary_target_noun_is_not_a_scale` |
| Change `scaled` to a set or subtract one occurrence only | unsupported duplicate-scale case |
| Replace explicit zero handling with `group or Decimal(1)` | `0万` case |
| Let the ASCII scanner consume repeated decimal separators | malformed `1.2.3万` case |
| Parse abbreviations for every source string | corpus replay adds `EVENT_280` from `245B`/`81B` |
| Disable target-language compatibility subtraction | unsupported-language matrix fires |
| Apply compatibility subtraction to plain values | unsupported plain `2000 -> 200` case becomes silent |

### Step 9: Lint and commit

Do not run the repository-wide `typos` hook here; it ignores `--files` and currently reports unrelated findings under `analysis/data/`.

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "fix(checks): compare game numbers by value"
```

---

## Task 3: Update probes, changelog and the superseded plan

**Files:**

- Modify: `analysis/probes/game-number-probe.py`
- Create: `analysis/probes/game-number-scale-replay.py`
- Modify: `docs/changes.rst`
- Modify: `docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md:1309-1673`

### Step 1: Move the live probe to the authoritative verdict

Replace the `_numbers` import and difference with:

```python
from weblate_customization.checks import game_number_fails
```

```python
if game_number_fails(
    source_file.get(key, ""),
    target,
    source_language=SOURCE,
    target_language=language,
):
```

### Step 2: Add the committed local-corpus replay

Create `analysis/probes/game-number-scale-replay.py` with the repository copyright/SPDX header. It reads:

- `analysis/data/heart-abyss-hub-1-units.tsv` as `ru -> en,fr`.
- `analysis/data/st2-zh-units.jsonl` as `ru -> zh`.
- `analysis/data/col4-b0-annotations.jsonl` as `ru -> fr`.

It calls `game_number_fails` with explicit language codes and prints every firing key.

Expected output:

```text
heart-abyss/hub-1 ru->en,fr: 0 firings / 792 pairs
st2 ru->zh: 0 firings / 124 pairs
col4 b0 ru->fr: 1 firing / 260 pairs
  EVENT_516_RESULT_977
```

The probe must fail with a nonzero exit code if the counts or retained Col4 key differ. It is a verification gate, not a report-only script.

### Step 3: Extend the unreleased changelog entry

The check is unreleased. Extend its existing `docs/changes.rst` entry instead of adding a second bullet:

```rst
Quantities written with supported scale words or CJK scale characters are compared by numeric value, so ``10 тысяч``, ``10 Tausend``, ``10,000``, ``10천`` and ``一万`` are equal while ``10万`` is ten times larger. Languages without supported scale notation retain the previous silent behavior for scale-derived source quantities.
```

### Step 4: Remove obsolete Part B cleanly

In `docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md`:

1. Replace lines from `## Part B: make game-number compare values, not digits` through the separator before `### Out of scope` with:

```markdown
## Part B: superseded

The independent `game-number` work moved to
`docs/product/plans/2026-08-24-game-number-value-comparison.md` after engineering
review found that the original generic scale folding broke compound values,
unsupported languages, multiplicity and malformed-input handling. Do not execute
the removed B1-B3 tasks.

---
```

2. Promote `### Out of scope` to `## Out of scope`.
3. Remove the Part B sentence from the architecture paragraph at line 7 and the custom-check package from the tech-stack sentence at line 9.
4. Preserve Part A unchanged.

### Step 5: Lint and commit

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files analysis/probes/game-number-probe.py \
  analysis/probes/game-number-scale-replay.py
uv run prek run rumdl codespell trailing-whitespace end-of-file-fixer \
  --files docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md
uv run prek run rst-double-space rst-http rst-bullet-stop sphinx-lint codespell \
  --files docs/changes.rst
git add analysis/probes/game-number-probe.py \
  analysis/probes/game-number-scale-replay.py \
  docs/changes.rst \
  docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md
git commit -m "docs(checks): document game-number value comparison"
```

---

## Task 4: Full verification

### Step 1: Run all affected suites

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py
./rundev.sh test weblate_customization/tests/test_autofixes.py
./rundev.sh test weblate/checks
```

Expected: all suites pass. If the dev container shows changing mass setup errors or extreme slowdown, check Docker memory pressure before changing code; this stack shares Docker resources with other projects.

### Step 2: Run the committed corpus gate

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate \
  weblate shell -c "exec(open('/app/src/analysis/probes/game-number-scale-replay.py').read())"
```

Expected: exact output from Task 3 Step 2 and exit code 0.

### Step 3: Check repaired and pre-repair bounty values

Run `game_number_fails` inside `weblate shell` with these six rows and explicit `ru`/target codes:

| Source | Target | Target code | Expected |
|---|---|---|---:|
| `10 тысяч мон!` | `1万文!` | `ja` | false |
| `100 тысяч мон!` | `10万文だぜ!` | `ja` | false |
| `100 тысяч мон!` | `十万文!` | `zh_Hans` | false |
| `10 тысяч мон!` | `10万文!` | `ja` | true |
| `100 тысяч мон!` | `100万文だぜ!` | `ja` | true |
| `100 тысяч мон!` | `一百万文!` | `zh_Hans` | true |

The command must raise `SystemExit(1)` on any disagreement, not merely print `BAD` and exit successfully.

### Step 4: Verify no generic guard or set-based scale state returned

```sh
python - <<'PY'
from pathlib import Path
text = Path("weblate_customization/src/weblate_customization/checks.py").read_text()
assert "UNKNOWN_SCALE_NOTATION" not in text
assert "unread_mantissas" not in text
assert "scaled: frozenset" not in text
PY
```

This is a narrow architecture assertion for three specifically rejected constructs, not a substitute for behavioral tests.

### Step 5: Report verification

Part B is verified only when:

- The frozen matrix gives exactly the three genuine pre-repair firings.
- All 25 old contracts and all new edge contracts pass.
- The corpus gate prints `0 / 792`, `0 / 124`, and only `EVENT_516_RESULT_977` for Col4.
- All three affected suites are green.

---

## Task 5: Dev rollout - REQUIRES EXPLICIT USER APPROVAL

Restarting the shared dev service is deployment under `AGENTS.md`. Nothing in this task runs without a separate instruction from the user.

### Step 1: Restart the code consumers

The copied package lives under `/app/data/python`; Granian watches `/app/src/weblate`, not that directory. The single `weblate` compose service supervises web and Celery processes, so restart that service after the copy:

```sh
docker compose -f dev-docker/docker-compose.yml restart weblate
```

### Step 2: Recompute synchronously without changing unit state

Do not enqueue `schedule_update_checks` and immediately query. Run the task synchronously with the component's current cache token and `update_state=False`:

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from uuid import uuid4
from django.core.cache import cache
from weblate.checks.models import Check
from weblate.trans.models import Component
from weblate.trans.tasks import update_checks

component = Component.objects.get(project__slug='heart-abyss', slug='hub-1')
token = uuid4().hex
cache.set(component.update_checks_key, token)
update_checks.run(component.pk, token, update_state=False)

checks = (
    Check.objects.filter(
        name='game-number',
        unit__translation__component=component,
    )
    .select_related('unit__translation__language')
    .order_by('unit__translation__language__code', 'unit__context')
)
print(f'component game-number count: {checks.count()}')
for check in checks:
    unit = check.unit
    print(
        unit.translation.language.code,
        unit.context,
        repr(unit.source),
        repr(unit.target),
    )
"
```

The synchronous call returns only after recomputation. The query is component-scoped and prints enough evidence to inspect every survivor. `update_state=False` avoids unrelated read-only state recalculation and the extra row locks it requires.

Expected for the repaired `heart-abyss/hub-1`: zero active `game-number` checks. If any remain, stop and inspect the printed rows; do not proceed to production.

### Step 3: Production remains separately gated

Production requires its own explicit approval and the repository's deployment path. After deployment:

1. Recompute only the intended component first.
2. Query the same component-scoped evidence.
3. Remove obsolete `ignore-game-number` flags only in a separately approved production write.

---

## Task 6: Finish the branch

**Files:**

- Modify: `docs/product/plans/2026-08-24-game-number-value-comparison.md`

### Step 1: Update plan status

Change:

```markdown
**Status:** awaiting approval
```

to:

```markdown
**Status:** complete
```

Do this only after Task 4 is green. Dev or production rollout is not required to mark the implementation complete; rollout remains a separate approval gate.

### Step 2: Run final scoped lint

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py \
  analysis/probes/game-number-probe.py \
  analysis/probes/game-number-scale-replay.py
uv run prek run rumdl codespell trailing-whitespace end-of-file-fixer \
  --files docs/product/plans/2026-08-24-game-number-value-comparison.md \
  docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md
```

Do not add `typos` to these scoped commands.

### Step 3: Commit and push all plan-owned changes

```sh
git add docs/product/plans/2026-08-24-game-number-value-comparison.md
git commit -m "docs(plan): complete game-number value comparison"
git push origin HEAD
```

If the push is rejected because another session advanced `origin/main`, integrate the remote commits without discarding unrelated working-tree changes, then push the same commits.

---

## Out of scope

| Item | Reason |
|---|---|
| Entirely spelled-out quantities such as `десять тысяч` | No numeric mantissa; requires language NLP rather than deterministic numeric parsing |
| Pure CJK digits without a scale, such as `二〇二五` | Ambiguous with years and prose; the measured defects all contain scale characters |
| Korean number words such as `일만` | Require a Korean lexical parser; measured Korean targets use ASCII mantissas with `천`/`만` |
| Full Turkish, Vietnamese, Polish, Indonesian, Dutch, Thai, Persian, Hindi and Portuguese scale vocabularies | Unsupported target languages use the explicit compatibility path and gain no new noise |
| Lowercase magnitude abbreviations | `m` is also metre; only uppercase adjacent target abbreviations are parsed, and only for a scale-derived source |
| `_x000b_` escaped control characters | An ingestion/XLSX cleanup defect, not numeral semantics |
| Removing production ignore flags | Production write requiring separate approval |
| Repairing any other production translations | Separate LQA remediation scope |

## Risks and defenses

| Risk | Defense |
|---|---|
| Ordinary nouns hide wrong values | No generic word guard; per-language scale vocabulary; `10 thousand -> 10 mon` regression test |
| Duplicate quantities collapse | `Counter[Decimal]` for both `values` and `scaled`; duplicate tests |
| Explicit zero becomes implicit one | `group_seen` state and `0万` test |
| Malformed text raises `InvalidOperation` | Strict anchored decimal token, `InvalidOperation` fallback, malformed-run test |
| IDs such as `245B` become magnitudes | Parse target abbreviations only when `source.scaled` is non-empty; Col4 replay gate |
| Unsupported locales become noisy | Remove only scale-derived missing occurrences for unsupported targets; constructed language matrix |
| Compatibility hides plain numeric defects | Plain values are absent from `source.scaled`; `2000 -> 200` unsupported-language test |
| Existing decimal/grouping behavior moves | All 25 existing contracts remain green and plain values use `Decimal` |
| Rollout reads stale/global results | Synchronous recomputation and component-scoped evidence query |
| Two plans prescribe different code | Remove old Part B and leave one superseding link |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---:|---|---|
| Eng Review | `/plan-eng-review` | Architecture, code quality, tests, performance | 2 | CLEAR | 10 findings resolved in this rewrite; 3 prior blockers removed |
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | Not run | Backend bug fix; not required |
| Design Review | `/plan-design-review` | UI/UX | 0 | Not run | No UI changes |
| Outside Voice | `/codex review` | Independent second opinion | 0 | Not run | Not required for this scoped fix |

**VERDICT:** ENG CLEARED - ready for user approval and implementation.

NO UNRESOLVED DECISIONS
