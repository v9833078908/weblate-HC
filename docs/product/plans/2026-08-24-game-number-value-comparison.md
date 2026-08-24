# Game number value comparison implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** awaiting approval

**Goal:** Read the scale notation the measured languages actually use, so that `10 тысяч` matches
`10,000`, `10 Tausend`, `10 mila`, `10천` and `一万` while `10万` does not, and so that no target
language outside `ja`, `zh` and `ko` gains a firing it does not already have.

**Architecture:** A stated quantity carries two readings: the value we believe, and the literal
digits today's check would have counted. A *closed* notation, the CJK scale characters, replaces the
digits inside it, because the character set is finite and parsed exhaustively. An *open* notation, a
scale word from a language table, only adds a reading and leaves the digits in place, because the
set of spellings is not enumerable. The verdict matches believed readings first and falls back to
literal ones. There is no per-language "supported notation" switch and no compatibility subtraction.

**Tech Stack:** Python 3.13, `decimal.Decimal`, `collections.Counter`, `regex`, Weblate
`TargetCheck`, pytest in the existing `dev-docker` container.

---

## Why this fix exists

`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` section 3.1 measured two
production strings across nine languages:

| Context | Russian value | Defective target | Actual value |
|---|---:|---|---:|
| `hub1_guard_1_3` | 10,000 | `ja` `10万文` | 100,000 |
| `hub1_guard_1_4` | 100,000 | `ja` `100万文` | 1,000,000 |
| `hub1_guard_1_4` | 100,000 | `zh_Hans` `一百万文` | 1,000,000 |

`_numbers` counts digit tokens, so `10 тысяч` and `10万` look identical and the tenfold error is
invisible, while a correct `10,000` looks different from `10 тысяч` and fires. Measured on the code
in production today: 6 of the 18 frozen matrix rows correct, 1 of the 6 bounty rows correct, and 5
firings over 1176 local corpus pairs of which 4 are this one false-positive class.

The three production strings were repaired on 2026-08-24, so verification uses the frozen
pre-repair values as the defect side and the repaired values as the no-noise side.

## What the previous draft of this plan got wrong

It consulted a per-language word table for the *target* and, for a language outside that table,
subtracted every scale-derived source occurrence from the result. A prototype of that design was
measured against the batteries below. Both mechanisms are dropped:

| Rejected mechanism | Measured consequence |
|---|---|
| A word table consulted for the target, so a spelling outside it reads as a bare mantissa | 8 correct translations start firing: `de` `10 Tsd.`, `de` `5 Mio.`, `de` `10 Tausende`, `it` `5 mln`, `fr` `5 M`, `es` `2 mil millones`, `en` `10k`, `en` `10 K` |
| Compatibility subtraction for a language outside the table | Dropped and wrong quantities go silent: `10 тысяч` against `Ödül mon`, and against `Ödül 20 bin mon` |
| Target `K/M/B/T` parsed whenever the source held a scale | `ja` `10k文` starts firing, and an identifier such as `245B` still misreads when the source holds a scale |

That design scored 1/12 on correct translations that must stay silent and 3/7 on real defects that
must fire, against 9/12 and 6/7 for the code in production today. The goal was right and the
direction of the failure was wrong: a check with `default_disabled = False` must not turn correct
work red in order to catch a defect that is invisible today anyway.

## The rule set

**Rule 1. A source quantity is a value plus a fallback.** A plain number is `(value, no fallback)`.
A scale word from the table for the source language is `(mantissa * factor, fallback [mantissa])`. A
descending compound folded into one quantity keeps every mantissa in its fallback, and all of them
must be present for the fallback to apply.

**Rule 2. The target yields every reading it admits.** Every plain number token, as today. Plus, for
`ja`, `zh` and `ko`, every parsed CJK scale run, which *replaces* the digits it contains. Plus, for a
target language that has a word table, the word-scale reading, which is *added* while the digits
stay.

**Rule 3. Match believed readings first, then fallbacks.** Pass one consumes each quantity's value
from the target multiset. Pass two lets a survivor consume its whole fallback instead. Anything
still unmatched fires.

The open and closed distinction is the load-bearing part. `10万` cannot mean ten, so the run owns
its digits and a source value of 10,000 finds nothing to match: the defect fires. `10 Tsd.` might be
any spelling of any scale, so the literal `10` stays available and the fallback of `10 тысяч` matches
it: the correct translation stays silent. The same rule keeps `zh` `千万不要忘记，奖励10k文` silent,
because the idiom parses as a run of its own and the `10` of `10k` is untouched. A fallback accepts
only the *matching* mantissa, so a wrong value is still caught without knowing the word: `100 тысяч`
against `de` `10 Tsd.` fires, because neither 100,000 nor 100 is present.

What is compared by value: plain numbers in any Unicode decimal digit script with the locale
decimal separator and grouping, already handled by `NUMBER` and `THOUSANDS`; word scales for `ru`
and `en` on whichever side speaks that language; descending compounds such as
`1 million 500 thousand`; CJK section and myriad scales for `ja`, `zh` and `ko`, including `1万5000`,
`2万5千`, `1億5000万`, `1兆2億` and full-width digits. One case is knowingly given up: a target that
drops the scale word and keeps the mantissa, `10 тысяч` against `Reward 10 mon`, stays silent in a
non-CJK language. It is silent today, and buying it costs the eight false positives above.

The rules imply an invariant, and the implementation must prove it rather than assume it:

> For a pair whose target holds no parsed CJK scale run, the new check must not fire where the
> current check is silent.

Such a target yields exactly today's number multiset, and every source quantity keeps today's
literal reading as its fallback. That is **measured on a prototype, not proven**: the CJK tokenizer
and the two matching passes are what could break it on a mixed run. Task 3 therefore commits a gate
that re-measures it on every local corpus pair, and Task 1 pins the mixed-run cases as tests.

## Prototype measurements

The rules were executed against the current preprocessing regexes before this plan was written.

| Battery | Today | This plan |
|---|---:|---:|
| Frozen section 3.1 matrix, 18 rows | 6/18 | 18/18 |
| Repaired and pre-repair bounty rows, 6 rows | 1/6 | 6/6 |
| Correct translations that must stay silent, 12 rows | 9/12 | 11/12 |
| Real defects that must fire, 7 rows | 6/7 | 6/7 |
| Existing contracts, run as `ru -> ja` and as `en -> de` | 26/26 | 26/26 |
| Parser edges: zero, malformed, compound, myriad, idiom, prose, 12 rows | 6/12 | 11/12 |
| `heart-abyss/hub-1`, `ru -> en,fr`, 792 pairs | 2 keys | 0 keys |
| `st2`, `ru -> zh_Hans`, 124 pairs | 0 keys | 0 keys |
| `col4-b0-annotations`, `ru -> fr`, 260 pairs | 1 key | 1 key |
| Invariant violations over all 1176 corpus pairs | n/a | 0 |

Two rows are not reached and stay as they are today: `th` `1 หมื่น` for 10,000 and `de`
`1,5 Millionen` for 1,500,000 both fire now and keep firing. Each closes later by adding that
language to the word table, which under Rule 2 can only add a target reading and therefore cannot
create a firing. Vocabulary growth is safe to do lazily, one complaint at a time.

**Coverage caveat, read before trusting the corpus rows.** No target in any local corpus holds a
parsed CJK scale run: `analysis/data/heart-abyss-hub-1-units.tsv` carries only the `ru`, `en` and
`fr` columns, and the Chinese corpus writes its numbers with Arabic digits. The corpora therefore
support the invariant for open notation and say nothing about `ja`, `zh` and `ko`, whose only
evidence is the constructed matrix. Task 5 Step 3 closes that gap and blocks verification until it
does.

## Ground rules

1. Work in the main checkout on a branch. Do not create a git worktree: `dev-docker` is a shared
   fixed-port stack.
2. Before every container test, copy `weblate_customization/src/weblate_customization` to
   `dev-docker/data/python/`.
3. Do not rebuild, restart or deploy until the separately gated rollout task.
4. Keep the check ID and ignore flag unchanged: `game-number` and `ignore-game-number`.
5. Add no numeral-parsing dependency. The grammar is small, deterministic and covered by the
   installed `regex` module.
6. Add no word table for a language this plan does not list. Adding one is safe under Rule 2, but it
   is a separate change with its own measurement.

## Baseline command

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py
```

Measured baseline: 120 passed, 26 skipped.

---

## Task 1: Write the regression tests

**Files:**

- Modify: `weblate_customization/tests/test_checks.py:11-22,189-337`

### Step 1: Make every case state its languages

The check now reads both languages off the unit, so a test passing `None` would assert a shape
production never produces. Import `game_number_fails` beside `GameNumberCheck`, `make_language`
beside `make_unit`, and add two helpers above `GameNumberCheckTest`:

```python
def fails(
    source: str, target: str, *, source_code: str = "en", target_code: str = "cs"
) -> bool:
    """The verdict for one pair, with both languages stated."""
    return game_number_fails(
        source, target, source_language=source_code, target_language=target_code
    )


def make_number_unit(source: str, target: str, *, source_code: str, target_code: str):
    """A unit whose component source language is not the factory default."""
    unit = make_unit(source=source, target=target, code=target_code)
    unit.translation.component.source_language = make_language(source_code)
    return unit
```

`make_unit` fixes the component source language to English
(`weblate/trans/tests/factories.py:145`), so the helper replaces it on the unsaved component; no
database write occurs.

Then rewrite the twenty existing `self.check.check_single(source, target, None)` calls as
`fails(source, target)`. Mechanical, and no expectation changes: `en -> cs` is a real configuration
with no word table on the target side, so each case keeps asserting the same numeric contract. The
base-class cases in `setUp` already run through a real `en -> cs` unit and stay untouched.

### Step 2: Prove the adapter reads the unit

```python
def test_the_check_reads_both_languages_from_the_unit(self) -> None:
    source = "10 тысяч мон!"
    for target, target_code, expected in (
        ("報酬は10万文!", "ja", True),
        ("Reward - 10,000 mon!", "en", False),
    ):
        with self.subTest(target_code=target_code):
            unit = make_number_unit(
                source, target, source_code="ru", target_code=target_code
            )
            self.assertEqual(self.check.check_single(source, target, unit), expected)
```

### Step 3: Add the frozen 18-row matrix

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
            self.assertEqual(
                fails(source, target, source_code="ru", target_code=target_code),
                expected,
            )
```

Every `False` row for `de`, `es`, `fr` and `it` passes without a word table for those languages, and
must keep passing if a later change adds one.

### Step 4: Add the behavior table

One test, one row per line, driven through `fails` with `subTest` exactly as in Step 3. Every row
was measured on the prototype.

| Source | Source | Target | Target | Fires | Why this row exists |
|---|---|---|---|---|---|
| `10 тысяч мон` | ru | `Belohnung 10 Tsd. Mon` | de | no | An abbreviation outside the table keeps its digits |
| `5 миллионов мон` | ru | `5 Mio. Mon` | de | no | Same, for millions |
| `10 тысяч мон` | ru | `10 Tausende Mon` | de | no | An inflected form the table does not carry |
| `5 миллионов мон` | ru | `5 mln di mon` | it | no | Italian abbreviation |
| `5 миллионов мон` | ru | `5 M de mons` | fr | no | Single-letter French abbreviation |
| `2 миллиарда мон` | ru | `2 mil millones de mon` | es | no | Two Spanish scale words in a row |
| `10 тысяч мон` | ru | `Reward 10k mon` | en | no | Lowercase magnitude letter |
| `10 тысяч мон` | ru | `Reward 10 K mon` | en | no | Detached magnitude letter |
| `2 млрд мон` | ru | `2,000,000,000 mon` | en | no | The Russian table now covers billions |
| `в 10 тысячах мон` | ru | `in 10,000 mon` | en | no | Prepositional Russian form |
| `10 тысяч мон` | ru | `報酬は10k文` | ja | no | A CJK target where no run parses |
| `10 тысяч мон` | ru | `千万不要忘记，奖励10k文` | zh_Hans | no | The parsed run is an idiom, not the quantity |
| `10 тысяч мон!` | ru | `Ödül mon!` | tr | yes | The quantity is gone |
| `10 тысяч мон!` | ru | `Ödül 20 bin mon!` | tr | yes | Wrong mantissa in an unlisted language |
| `5 миллионов мон` | ru | `Nagroda 7 milionów mon` | pl | yes | Same, for millions |
| `100 тысяч мон` | ru | `10 Tausend Mon` | de | yes | A fallback accepts only its own mantissa |
| `100 тысяч мон` | ru | `10 Tsd. Mon` | de | yes | Same, without knowing the word |
| `Reward 100000` | en | `Награда 10 тысяч мон` | ru | yes | Tenfold error into a listed language |
| `уровень 3, награда 10 тысяч` | ru | `レベル3、報酬10万文` | ja | yes | Mixed run: a correct 3 beside a wrong myriad |
| `Reward 1 million 500 thousand` | en | `Reward 1 million` | en | yes | A folded compound needs its whole fallback |
| `Reward 0` | en | `報酬は0万文` | ja | no | Explicit zero is not an omitted numeral |
| `Reward 1.2.3` | en | `報酬は1.2.3万文` | ja | no | A malformed run degrades, and never raises |
| `Reward 10000` | en | `報酬は1万.以上` | ja | no | A run may carry trailing punctuation |
| `Reward 1000` | en | `Reward 1000.0` | en | no | Decimal values compare numerically |
| `Reward 1 million 500 thousand` | en | `Reward 1,500,000` | en | no | Descending compounds fold |
| `Reward 1 thousand 2 million` | en | `Reward 2001000` | en | yes | Ascending ones do not |
| `Reward 1,000,000,000` | en | `Reward 10億` | ja | no | Myriad scales use their real values |
| `Reward 1 trillion` | en | `報酬は1兆文` | ja | no | Largest scale in the table |
| `Reward 15000000` | en | `報酬は1,500万文` | ja | no | Grouping inside a run |
| `Reward 100000` | en | `報酬は１０万文` | ja | no | Full-width digits |
| `Every third repair heals 10 HP` | en | `三回に一回の修理は10回復します` | ja | no | CJK numerals in prose are not quantities |
| `Never do that, 5 times` | en | `千万不要那样做，5次` | zh_Hans | no | Neither is an idiom |
| `10 thousand + 10 thousand` | en | `10000` | en | yes | Repeated quantities stay a multiset |

### Step 5: Run the red tests

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Before implementation the frozen matrix, the adapter test and the CJK edges fail. The
no-new-firing rows and most rewritten legacy rows already pass: they protect current behavior and
are regression tests, not artificial red tests. Do not assert an exact failing count; `subTest`
aggregation makes it incidental.

---

## Task 2: Replace token comparison with two-reading matching

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:15-20,282-310`

### Step 1: Tables

Add `from decimal import Decimal, InvalidOperation` and `NamedTuple` to the typing import. Place the
tables beside the existing number regexes, keeping the file's habit of explaining each constant.

```python
# A scale word carries the zeros a digit would spell out, so "10 тысяч" and
# "10 thousand" are one quantity with two readings. Only the languages this fork
# uses as a source are listed: a target whose spelling is not here keeps its
# literal digits and therefore behaves exactly as before.
WORD_SCALES: dict[str, dict[str, int]] = {
    "ru": dict.fromkeys(("тыс", "тысяча", "тысячи", "тысяч", "тысячу", "тысячах"), 1_000)
    | dict.fromkeys(("млн", "миллион", "миллиона", "миллионов"), 1_000_000)
    | dict.fromkeys(("млрд", "миллиард", "миллиарда", "миллиардов"), 1_000_000_000),
    "en": dict.fromkeys(("thousand", "thousands"), 1_000)
    | dict.fromkeys(("million", "millions"), 1_000_000)
    | dict.fromkeys(("billion", "billions"), 1_000_000_000)
    | dict.fromkeys(("trillion", "trillions"), 1_000_000_000_000),
}
# CJK scale notation is a closed character set, so a parsed run is the whole
# quantity and its digits are not separately readable: that is what makes "10万"
# ten times "1万" instead of a restatement of "10". Each group holds the same
# scale written in Japanese, Chinese and Korean.
SECTION_SCALES = {"十": 10, "百": 100, "千": 1_000, "십": 10, "백": 100, "천": 1_000}
BIG_SCALES = (
    dict.fromkeys("万萬만", 10_000)
    | dict.fromkeys("億亿억", 100_000_000)
    | dict.fromkeys("兆조", 1_000_000_000_000)
)
CJK_DIGITS = {char: value for value, char in enumerate("〇一二三四五六七八九")} | {
    "零": 0,
    "两": 2,
    "兩": 2,
}
# Language.is_cjk() holds the same set, but the verdict stays a pure string
# function so the offline probes can share it.
CJK_LANGUAGES = frozenset({"ja", "zh", "ko"})

_MANTISSA = r"\d+(?:[.,]\d+)?"
# One compiled pattern per language: a single alternation over every table would
# read a Russian word in a German string. Case-insensitive, because "Tausend",
# "Million" and "Тысяч" are all normal spellings.
WORD_SCALE_PATTERNS = {
    code: regex.compile(
        rf"({_MANTISSA})\s*(" + "|".join(sorted(table, key=len, reverse=True)) + r")\b",
        regex.IGNORECASE,
    )
    for code, table in WORD_SCALES.items()
}
_CJK_NUMERALS = "".join((*CJK_DIGITS, *SECTION_SCALES, *BIG_SCALES))
# A run is a maximal stretch of numerals, digits and their separators holding at
# least one CJK numeral. "3回に1回" holds none and stays two plain numbers.
CJK_RUN = regex.compile(
    rf"[\d.,{_CJK_NUMERALS}]*[{_CJK_NUMERALS}][\d.,{_CJK_NUMERALS}]*"
)
_WHOLE_MANTISSA = regex.compile(rf"\A{_MANTISSA}\Z")


class Quantity(NamedTuple):
    """A stated quantity: the value read, and the literal reading it may fall back to.

    An empty fallback means the reading admits no alternative.
    """

    value: Decimal
    fallback: tuple[Decimal, ...]
```

A lowercase-only match would fail the German rows of the frozen matrix, so `regex.IGNORECASE` is
load-bearing, not decoration.

### Step 2: The CJK state machine

Strict by construction. Every rejection returns `None`, which leaves the run to the plain
tokenizer, so a malformed run degrades to today's behavior instead of raising.

```python
def _cjk_value(run: str) -> Decimal | None:
    """Value of a CJK numeral run, or None when it is not a well-formed quantity."""
    total = section = Decimal(0)
    current: Decimal | None = None
    group_seen = scale_seen = False
    last_section = last_big = Decimal("Infinity")
    index = 0
    while index < len(run):
        char = run[index]
        if char.isdigit():
            end = index
            while end < len(run) and (run[end].isdigit() or run[end] in ".,"):
                end += 1
            chunk = run[index:end].rstrip(".,")
            if current is not None or not _WHOLE_MANTISSA.match(chunk):
                return None
            if (current := _decimal(chunk)) is None:
                return None
            group_seen = True
            index += len(chunk)
            continue
        if char in CJK_DIGITS:
            # Two numerals in a row are not a quantity: "一二万" is prose.
            if current is not None:
                return None
            current = Decimal(CJK_DIGITS[char])
            group_seen = True
        elif char in SECTION_SCALES:
            scale = Decimal(SECTION_SCALES[char])
            if scale >= last_section:
                return None
            # A bare section scale means one of it: "十" is ten.
            section += (Decimal(1) if current is None else current) * scale
            current, group_seen, last_section, scale_seen = None, True, scale, True
        elif char in BIG_SCALES:
            scale = Decimal(BIG_SCALES[char])
            # A big scale needs an explicit group, so "万一" is not a quantity.
            # Never write `current or Decimal(1)`: that turns "0万" into 10000.
            if not group_seen or scale >= last_big:
                return None
            total += (section + (current if current is not None else Decimal(0))) * scale
            section, current, group_seen = Decimal(0), None, False
            last_section, last_big, scale_seen = Decimal("Infinity"), scale, True
        else:
            return None
        index += 1
    if not scale_seen:
        return None
    return total + section + (current if current is not None else Decimal(0))
```

`_decimal` replaces the inline `.replace()` chain `_numbers` used, and is the only place
`InvalidOperation` is handled:

```python
def _decimal(text: str) -> Decimal | None:
    """A number token as a value, or None when it will not parse."""
    try:
        return Decimal(text.replace(",", ".").replace("\u066b", "."))
    except InvalidOperation:
        return None
```

### Step 3: Extraction

Five small functions, each doing exactly what its name says. Spans are half-open `(start, end)` over
the prepared body, and a span list is always in text order.

| Function | Contract |
|---|---|
| `_prepare(text, *, drop_ordinals) -> str` | `URL` to a space, then `MARKUP` and `FULL_DATE` to a space, then `ORDINAL` when asked, then `_collapse_grouping`. Exactly the body `_numbers` counted. |
| `_covered(start, end, spans) -> bool` | Whether the range overlaps any span. |
| `_number_tokens(body, skip) -> list[Decimal]` | Every `NUMBER` match not covered by `skip`, through `_decimal`. |
| `_closed_spans(body, language) -> list[(start, end, value, digits)]` | Empty unless the language is in `CJK_LANGUAGES`. For each `CJK_RUN` match, strip leading and trailing `.` and `,`, then keep the span when `_cjk_value` parses it. `digits` are the ASCII number tokens inside the run, and become the fallback. |
| `_open_spans(body, language, skip) -> list[(start, end, value, mantissas)]` | Empty unless the language has a table. Skip a match covered by `skip`. Fold a match into the previous span when only whitespace separates them and its factor is strictly smaller: the folded span carries the summed value and both mantissas. Otherwise start a new span. |

The two sides then differ in exactly one place:

```python
def _quantities(text: str, language: str | None, *, drop_ordinals: bool) -> list[Quantity]:
    """Every quantity the text states, each with its literal fallback."""
    # closed and open spans become Quantity(value, fallback);
    # every NUMBER token outside both becomes Quantity(value, ()).


def _readings(text: str, language: str | None) -> Counter[Decimal]:
    """Every value the text can be read as. A closed run replaces its digits."""
    # values of the closed and open spans, plus every NUMBER token outside the
    # closed spans ONLY, so a word-scale reading is added while its digits stay.
```

`_readings` prepares the body with `drop_ordinals=False`: an ordinal in the translation keeps its
digit, which is the existing contract.

### Step 4: The verdict

```python
def _base_language(code: str | None) -> str | None:
    """`zh_Hans` and `zh-Hant` both write 萬, so only the base code matters."""
    if code is None:
        return None
    return code.split("_", 1)[0].split("-", 1)[0]


def game_number_fails(
    source: str,
    target: str,
    *,
    source_language: str | None,
    target_language: str | None,
) -> bool:
    """Whether a quantity the source states is missing from the translation."""
    stated = _quantities(source, _base_language(source_language), drop_ordinals=True)
    if not stated or not target:
        return False
    available = _readings(target, _base_language(target_language))
    pending = []
    for quantity in stated:
        if available[quantity.value]:
            available[quantity.value] -= 1
        else:
            pending.append(quantity)
    for quantity in pending:
        if not quantity.fallback:
            return True
        if not all(available[value] for value in set(quantity.fallback)):
            return True
        for value in quantity.fallback:
            available[value] -= 1
    return False
```

Both passes are count-aware, and the value pass runs to completion before any fallback is spent, so
an exact match is never consumed by a tolerant one. `Counter.__getitem__` returns zero for an absent
key without inserting it, and every decrement follows a non-zero check.

### Step 5: The adapter

Delete `_numbers` and reduce the check to:

```python
    def check_single(self, source: str, target: str, unit) -> bool:
        return game_number_fails(
            source,
            target,
            source_language=unit.translation.component.source_language.base_code,
            target_language=unit.translation.language.base_code,
        )
```

Reading the source language off the unit inside a check follows
`weblate/checks/duplicate.py:81` and `weblate/checks/same.py:177`. `base_code` is already a base
code; `_base_language` exists for the probes, which pass `zh_Hans`. Update the class docstring to
state that values are compared as a multiset of `Decimal`, that a scale word is understood for `ru`
and `en` while any other spelling keeps its literal digits, that CJK scale notation is parsed
exactly, and that spelled-out numbers are out of scope.

### Step 6: Run the focused tests

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

### Step 7: Prove the tests defend the branches

Make each mutation, run the named test, restore before continuing.

| Mutation | Test that must fail |
|---|---|
| Let `_readings` skip the open spans too | `de` `10 Tsd.` and `zh` idiom rows of Step 4 |
| Let `_readings` keep the digits of a closed span | `ja` rows of the frozen matrix |
| Drop `regex.IGNORECASE` | `de` rows of the frozen matrix |
| Replace the explicit-zero handling with `current or Decimal(1)` | `0万` edge |
| Accept a run without a scale | `三回に一回` edge |
| Let the ASCII scanner accept repeated separators | `1.2.3万` edge |
| Spend fallbacks in the same pass as values | repeated-quantity edge |
| Accept a partial fallback | `1 million 500 thousand` against `1 million` |
| Fold ascending compounds | `1 thousand 2 million` edge |

### Step 8: Lint and commit

The repository-wide `typos` hook ignores `--files` and currently reports unrelated findings under
`analysis/data/`, so name the hooks.

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "fix(checks): compare game numbers by value"
```

---

## Task 3: Move the probes onto the shipped verdict

**Files:**

- Modify: `analysis/probes/game-number-probe.py`
- Create: `analysis/probes/game-number-replay.py`

### Step 1: Fix the live probe

It imports the deleted `_numbers` and, at line 44, compares without `drop_ordinals`, so it never
matched the shipped rule. Replace the import with `game_number_fails` and the comparison with:

```python
if game_number_fails(
    source_file.get(key, ""),
    target,
    source_language=SOURCE,
    target_language=language,
):
```

Correct the usage comment in the header. The module needs Django settings, so the invocation is

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test \
PYTHONPATH=weblate_customization/src \
python3 analysis/probes/game-number-probe.py
```

A bare `PYTHONPATH=... python3 ...` fails with `No module named 'weblate.settings'`.

### Step 2: Commit the gate

Create `analysis/probes/game-number-replay.py` with the repository copyright and SPDX header. It
reads `analysis/data/heart-abyss-hub-1-units.tsv` as `ru -> en,fr`,
`analysis/data/st2-zh-units.jsonl` as `ru -> zh_Hans` and
`analysis/data/col4-b0-annotations.jsonl` as `ru -> fr`, and asserts three things:

1. The firing keys per corpus are exactly `{}`, `{}` and `{"EVENT_516_RESULT_977"}`.
2. The invariant: no pair fires under the new rule while the old digit-multiset rule is silent,
   unless the target holds a parsed CJK run. Keep the old rule inline in the script, five lines over
   `NUMBER`, rather than reviving `_numbers` in the product module.
3. How many targets held a parsed CJK run, so a reader sees how much closed notation the corpora
   exercise.

Expected output:

```text
heart-abyss/hub-1 ru->en,fr: 0 firings / 792 pairs
st2 ru->zh_Hans: 0 firings / 124 pairs
col4 b0 ru->fr: 1 firing / 260 pairs
  EVENT_516_RESULT_977
invariant violations: 0 / 1176 pairs
targets with a parsed CJK run: 0
```

The retained Col4 key holds `_x000b_` in the source, whose `000` fragments read as numbers. That is
an XLSX control-character ingestion defect and stays out of scope.

Statements at module level, no `if __name__ == "__main__":` guard, and `raise SystemExit(1)` on any
mismatch. The guard would matter: running the file through `weblate shell -c "exec(open(...).read())"`
executes with `__name__` set to the shell command module, because
`django/core/management/commands/shell.py:261` passes its own globals, so a guarded body would print
nothing and exit zero. Task 5 therefore invokes the file directly.

### Step 3: Lint and commit

```sh
uv run prek run ruff-check ruff-format codespell trailing-whitespace end-of-file-fixer \
  --files analysis/probes/game-number-probe.py analysis/probes/game-number-replay.py
git add analysis/probes/game-number-probe.py analysis/probes/game-number-replay.py
git commit -m "test(checks): gate game-number on the local corpora"
```

---

## Task 4: Documentation

**Files:**

- Modify: `docs/changes.rst:31`
- Modify: `docs/guides/producer-guide.md:397`
- Modify: `docs/guides/producer-guide-weblate.md:397`
- Modify: `docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md:7,9,1320-1682,1684`

### Step 1: Extend the unreleased changelog entry

The check is unreleased, so extend the existing bullet instead of adding a second one. Append to
`docs/changes.rst:31`:

```rst
Quantities written with a scale word or with CJK scale characters are compared by numeric value, so ``10 тысяч``, ``10 Tausend``, ``10,000``, ``10천`` and ``一万`` are equal while ``10万`` is ten times larger. A spelling the check cannot read keeps its literal digits, so it is reported exactly as before.
```

### Step 2: Extend the producer guides

Both guides describe the check for the game teams and carry the same row. Replace it in each with:

```markdown
| `game-number` | В переводе нет числа, которое есть в исходнике: другой урон, радиус или длительность. Значение сравнивается с учётом масштаба: `10 тысяч`, `10,000`, `10 Tausend`, `10천` и `一万` равны, а `10万` в десять раз больше |
```

### Step 3: Retire the superseded Part B

In `docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md`:

1. Replace lines 1320 to 1682, from `## Part B: make game-number compare values, not digits`
   through the `---` separator preceding `### Out of scope` at line 1684, with:

   ```markdown
   ## Part B: superseded

   The `game-number` work moved to
   `docs/product/plans/2026-08-24-game-number-value-comparison.md`, which rejected the global
   scale table proposed here: one alternation over every language reads a Russian word in a
   German string, and folding a target's digits into a scale it cannot verify turns correct
   translations red. Do not execute the removed B1-B3 tasks.

   ---
   ```

2. Promote `### Out of scope` at line 1684 to `## Out of scope`.
3. Remove the Part B clause from the architecture sentence at line 7 and the custom-check package
   from the tech-stack sentence at line 9.
4. Leave Part A untouched.

### Step 4: Lint and commit

```sh
uv run prek run rst-double-space rst-http rst-bullet-stop sphinx-lint codespell \
  --files docs/changes.rst
uv run prek run rumdl codespell trailing-whitespace end-of-file-fixer \
  --files docs/guides/producer-guide.md docs/guides/producer-guide-weblate.md \
  docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md
git add docs/changes.rst docs/guides/producer-guide.md \
  docs/guides/producer-guide-weblate.md \
  docs/llm-first/plans/2026-08-24-llm-batch-string-identity.md
git commit -m "docs(checks): document game-number value comparison"
```

---

## Task 5: Verification

### Step 1: The affected suites

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py
./rundev.sh test weblate_customization/tests/test_autofixes.py
./rundev.sh test weblate/checks
```

If the container returns mass setup errors whose count changes between identical runs, or a test
suddenly takes twenty times longer, check `docker stats --no-stream` before touching code: this
stack shares Docker memory with every other compose project.

### Step 2: The committed gate

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate \
  python /app/src/analysis/probes/game-number-replay.py
```

Expected: the output from Task 3 Step 2 and exit code 0.

### Step 3: Close the CJK coverage gap

The corpora hold no CJK scale notation, so replay the nine languages the component actually has.
This is a read-only API call against the live instance, not a deployment.

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test PYTHONPATH=weblate_customization/src \
PROBE_DOMAIN=l10n.herocraft.com PROBE_TOKEN=... \
PROBE_COMPONENT=heart-abyss/hub-1 PROBE_SOURCE=ru \
PROBE_LANGUAGES=ru,de,en,es,fr,it,ja,ko,zh_Hans,zh_Hant \
python3 analysis/probes/game-number-probe.py
```

Run it once on `HEAD~` and once on the branch. Verification requires that no key firing on the
branch is silent on `HEAD~` unless its target holds a CJK scale run, and that the two repaired
bounty keys fire on neither. Record both runs in
`docs/product/measurements/2026-08-24-game-number-nine-language-replay.md` and save the dump as
`analysis/data/heart-abyss-hub-1-units-9lang.tsv`, so the committed gate can consume it afterwards.
If the instance is unreachable this step blocks the plan: say so, and do not report Task 5 as green
on the three-corpus gate alone.

### Step 4: The bounty rows

Run these six rows through `game_number_fails` inside the container with explicit `ru` and target
codes. The command must `raise SystemExit(1)` on any disagreement rather than printing a failure and
exiting zero.

| Source | Target | Target code | Expected |
|---|---|---|---:|
| `10 тысяч мон!` | `1万文!` | `ja` | false |
| `100 тысяч мон!` | `10万文だぜ!` | `ja` | false |
| `100 тысяч мон!` | `十万文!` | `zh_Hans` | false |
| `10 тысяч мон!` | `10万文!` | `ja` | true |
| `100 тысяч мон!` | `100万文だぜ!` | `ja` | true |
| `100 тысяч мон!` | `一百万文!` | `zh_Hans` | true |

### Step 5: Report

Verified only when the three suites are green, the committed gate prints `0 / 792`, `0 / 124`, one
Col4 key and `invariant violations: 0`, the nine-language replay adds no firing outside a CJK run,
and the six bounty rows agree.

---

## Task 6: Finish the branch

**Files:**

- Modify: `docs/product/plans/2026-08-24-game-number-value-comparison.md`

Set **Status** to `complete` once Task 5 is green. Rollout is a separate gate and is not required to
call the implementation complete.

```sh
uv run prek run rumdl codespell trailing-whitespace end-of-file-fixer \
  --files docs/product/plans/2026-08-24-game-number-value-comparison.md
git add docs/product/plans/2026-08-24-game-number-value-comparison.md
git commit -m "docs(plan): complete game-number value comparison"
git push origin HEAD
```

Do not add `typos` to the scoped commands. If the push is rejected because another session advanced
`origin/main`, integrate the remote commits without discarding unrelated working-tree changes and
push the same commits.

---

## Task 7: Dev rollout - REQUIRES EXPLICIT USER APPROVAL

Restarting the shared dev service is deployment under `AGENTS.md`. Nothing here runs without a
separate instruction.

### Step 1: Restart the consumers

Granian watches `/app/src/weblate`, not `/app/data/python`, so the copied package needs a restart of
the service supervising both web and Celery:

```sh
docker compose -f dev-docker/docker-compose.yml restart weblate
```

### Step 2: Recompute one component, synchronously

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
    Check.objects.filter(name='game-number', unit__translation__component=component)
    .select_related('unit__translation__language')
    .order_by('unit__translation__language__code', 'unit__context')
)
print(f'component game-number count: {checks.count()}')
for check in checks:
    unit = check.unit
    print(unit.translation.language.code, unit.context, repr(unit.source), repr(unit.target))
"
```

The synchronous call returns only after recomputation, the query is component-scoped, and
`update_state=False` avoids unrelated state recalculation and its row locks. Expected for the
repaired component: zero active `game-number` checks. If any remain, inspect the printed rows and
stop.

### Step 3: Production stays gated

Production needs its own approval and the repository's deployment path. Recompute the intended
component first, query the same component-scoped evidence, and remove obsolete
`ignore-game-number` flags only in a separately approved production write.

---

## Out of scope

| Item | Reason |
|---|---|
| Word tables for `de`, `es`, `fr`, `it` and other targets | Not needed for any measured row; adding one only adds a target reading, so it is safe later, one reported complaint at a time |
| A target that drops the scale word and keeps the mantissa | Indistinguishable from a legitimate unknown spelling without that language's table; silent today |
| Entirely spelled-out quantities such as `десять тысяч` | No mantissa to anchor; needs language NLP |
| Pure CJK digit strings such as `二〇二五` | Ambiguous with years; every measured defect carries a scale character |
| Magnitude abbreviations `K/M/B/T` | The fallback already accepts them, and not parsing them keeps an identifier such as `245B` from becoming 245 billion |
| `_x000b_` control characters | XLSX ingestion defect, not numeral semantics |
| Removing production ignore flags, repairing other production strings | Separate approvals, separate LQA scope |

## Risks and defenses

| Risk | Defense |
|---|---|
| A correct translation turns red | An unreadable spelling keeps its literal digits; twelve no-new-firing rows plus the committed gate |
| A wrong value hides behind a fallback | A fallback accepts only its own mantissa, and all of a compound's mantissas; eight still-fires rows |
| A dropped quantity goes silent | No compatibility subtraction anywhere; the `tr`, `pl` and `de` rows |
| An idiom or unparsed run makes a CJK target strict | Strictness is local to a parsed run, not a property of the string; the `千万` and `10k文` rows |
| Counts drift on a mixed run | Both passes count-aware, values matched before any fallback is spent; the repeated-quantity and `レベル3、報酬10万文` rows |
| Explicit zero becomes an implicit one | `group_seen`, no `current or Decimal(1)`; the `0万` row |
| Malformed text raises `InvalidOperation` | `_decimal` returns `None` and the run falls back to the plain tokenizer; the `1.2.3万` row |
| CJK evidence rests on constructed rows only | Task 5 Step 3 blocks verification until the nine-language replay runs |
| The corpus gate passes silently | Module-level statements, `SystemExit(1)`, invoked as a file rather than through `shell -c` |
| Two plans prescribe different code | Part B replaced by a link, Part A untouched |

## Review record

| Round | Outcome |
|---|---|
| First engineering review | Cleared the previous draft |
| Second engineering review | Rejected it. Prototype measurements: 8 correct translations start firing in listed languages, dropped and wrong quantities go silent in unlisted ones, the corpus gate exercised the new path on 4 of 1176 pairs and on no target at all, the gate command could not fail, and the German rows failed the plan's own matrix for want of case-insensitive matching |
| This revision | Rewritten around the open and closed distinction. Prototype measured 18/18, 6/6, 11/12, 6/7, 26/26, 11/12 and zero invariant violations. Awaiting approval |

NO UNRESOLVED DECISIONS
