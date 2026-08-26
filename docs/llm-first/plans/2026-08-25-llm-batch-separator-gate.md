<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# LLM batch separator gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.
>
> Use `test-driven-development` for Task 2 and
> `verification-before-completion` for Tasks 3 and 5.

**Goal:** Refuse an LLM batch reply that loses or adds a Hero Craft engine `$`
line separator, so a structural defect is re-asked instead of entering the
translation cache or being stored and found on a later manual LQA pass.

**Architecture:** No new subsystem, model, LLM call or dependency. Two
classmethods on `BaseLLMTranslation`, beside the placeholder check that already
refuses a batch today. The count gate honours the existing
`ignore-game-line-break` and `ignore-all-checks` overrides. A refusal raises
`MachineTranslationError`, which the existing recovery path re-asks a batch in
halves down to single strings. A false positive therefore adds requests and can
leave a singleton untranslated; it never stores an unvalidated reply.

**Tech Stack:** Python 3.13, Django, `weblate/machinery/llm.py`
(`BaseLLMTranslation`, shared by OpenAI, Azure OpenAI, Mistral and the fork's
`RoutedLLMTranslation`), `weblate/machinery/tests.py`, pytest inside the
`dev-docker` container.

---

**Date:** 2026-08-25.
**Status:** Task 1 complete. Task 1.5 and Tasks 2-5 await approval;
implementation not started. Scope reduced to the separator rule by decision of
2026-08-25; the duplicate-target rule is deferred, see "Deferred" below.

## Why this exists

`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` §5.1 and
`docs/llm-first/measurements/2026-08-24-batch-misalignment-radius-scan.md` §4
measured three real content-misbinding defects in production. Commit `5ee14df`
closed the *label-set* half of the class: a batch reply is paired with its
sources through an echoed `id` (`weblate/machinery/llm.py:2445-2468`), and a
reply whose id set does not match is refused. What stays open is a reply that
echoes every id correctly while carrying a neighbour's content.

Task 1 measured five candidate deterministic rules before any code was written
and eliminated four. Full numbers in
`docs/llm-first/measurements/2026-08-25-deterministic-alignment-limits.md`.

| Candidate rule | Verdict |
| --- | --- |
| **tight `$` separator must survive** | **Kept - this plan.** 0 false positives on 1099 correctly aligned pairs; 1 true positive, on the only `critical` human-annotated defect in the `col4` set (`EVENT_408`, `checks: ["game-line-break"]`). |
| source numbers must survive | Dropped. 0 of 1099 aligned pairs judged - it abstains on all of them. Its single verdict anywhere was a false positive: `_x000b_`, an XLSX control-character escape, reads as the number `000`. |
| one reply for two different sources | Deferred, see below. |
| terminal punctuation | Rejected. 67 of 396 correctly aligned ru -> fr pairs change it; that is the `end_stop` defect class, not misalignment. |
| target length | Rejected. A safe band needs a 3.8-6.6x length ratio. |

**Read this before reporting the result as a fix.** The rule is a *conditional*
signal, not a guarantee. It helps only where the source uses `$` tightly. It
would not refuse the German rotation that started this work, nor the `zh_Hant`
cross-segment exchange: neither carries a separator. This change converts one
small, measured, structurally-defined defect into "refused and re-asked" instead
of "stored and found by hand". It is not a release gate and does not make
misbinding impossible.

Why this particular rule is worth shipping on its own: its trigger is the
count-preservation portion of an engine contract that this repository already
enforces as `GameLineBreakCheck`. The gate moves count loss or addition earlier
in the pipeline - from a check on a stored target to a refusal before cache or
target storage.

Whitespace hugging a surviving separator is deliberately outside this gate.
`LineSeparatorSpacing` removes it deterministically during `Unit.translate`;
asking an LLM again for an output a local autofix can repair would spend money
without increasing protection. This is therefore not a full copy of
`GameLineBreakCheck`.

## Cost

The user constraint is LLM spend, so state the effect precisely.

- **Baseline spend does not change.** `batch_size` is untouched, so a run with no
  refusals costs exactly what it costs today. The expensive lever - shrinking the
  batch, estimated x2.1 to x3.4 and formally unmeasured - is explicitly out of
  scope.
- **A complete batch-10 refusal has an illustrative cost, not a ceiling.** It
  produces the original ten-string request plus two five-string retries: twenty
  string completions total. Applying the measured $0.000220 per-string average
  gives about $0.0044 total or $0.0022 incremental. The fixed prompt prefix is
  paid on all three requests, so provider-reported usage remains authoritative.
  Prefix rescue can retry only the remaining tail and cost less; a singleton that
  keeps failing produces no translation.
- **The false-positive rate is unproven, not zero.** 0 of 1099 is an absence of
  observations across ru -> en, fr, zh only. Tight `$` occurs in exactly one
  source string in every corpus measured, so triggers should be rare, but that is
  an expectation, not a bound. A false positive risks translation coverage as
  well as request cost.
- Watch it without new instrumentation: the distinct log message plus the
  existing `LLMUsageLog.outcome` and `batch_size` columns make refused singleton
  attempts and falling strings-per-request visible.

## Non-goals

Each was considered and rejected with evidence. Do not widen the change.

- **A number-survival rule.** Killed by Task 1. It also lacks the markup, date
  and decimal normalization that `GameNumberCheck` documents, and is unguarded
  against localized digits and grouped thousands in languages the corpora do not
  contain. `GameNumberCheck` remains the right home for number comparison: a
  dismissible per-component check, not a hard gate in a path shared by every LLM
  service.
- **A Component Diagnostics alert for batch shift.** Across 27,025 units the
  offset-run detector produced 0 findings and the duplicate-target detector 57
  findings with 1 real defect. `Alert` is keyed
  `unique_together = ("component", "name")` (`weblate/trans/models/alert.py:85`),
  so the per-language dismissal that idea assumed does not exist.
- **The n x n semantic verifier** of
  `docs/llm-first/plans/2026-08-24-llm-batch-semantic-alignment-design.md`.
  Rejected on cost: about 548,800 classified relations on top of the producer.
- **Changing `batch_size`.**
- **Terminal punctuation or length in the gate.**
- **Any production command, any provider call, any LLM spend.** Every task here
  runs offline against files already in the repository.
- **`docs/security/threat-model.rst`.** None of its "conditions that change this
  model" apply.
- **Target whitespace hugging a surviving separator.** This is handled by the
  existing deterministic `LineSeparatorSpacing` autofix on target write; it must
  not cause an LLM retry here.

## Deferred: the duplicate-target rule

**Do not implement this. It is recorded so the reasoning is not lost.**

The rule would refuse one reply that renders two different sources identically -
the other half of a batch shift, one item repeated and one source's content lost,
as in `col4/data` 88213/88214.

It was cut from this plan because its only protection is a length threshold on
thin evidence. Task 1 enumerated every legitimate convergence in 1099 aligned
pairs: **5 pairs, the longest 27 characters** (two sources differing solely in a
trailing period, both rendering as `Savez-vous que ses troupes…`). A threshold of
40 sits above that and far below the real defect's 100-plus characters, but on
five pairs across three language pairs the false-positive rate is unproven.

A first reading also claimed a second margin - the closest convergent pair sits
29 units apart, so such strings could never share a request. **That claim is
withdrawn.** A request is not a window over the component in unit order:
`AutoTranslate.get_units` (`weblate/trans/autotranslate.py:389-395`) filters by
`STATE_READONLY`, `unit_ids`, suggest mode and `.search(q)`, and `fetch_mt`
batches that filtered list. If the units between two convergent strings are
already translated, the two become adjacent in the batch. Any convergent pair can
co-occur.

**Precondition for revisiting:** measure the length distribution of the 55
legitimate convergences in
`docs/llm-first/measurements/2026-08-24-batch-misalignment-radius-scan.md` §4.
That needs re-fetching those components through the API and is a separate,
approved piece of work. Until then a hard refusal on a guessed threshold is a
cost risk with no measured ceiling.

## Prerequisites

**Blocking.** `weblate/auth/permissions.py:442-443` currently holds an `if` with
no body and the file is modified in the working tree, so the dev container and
pytest do not start. That edit belongs to another change and must not be touched
here. Either wait for its owner, or execute this plan in a clean worktree off
`origin/main`.

```bash
git status --short weblate/auth/permissions.py   # expect no output
python3 -c "import ast, pathlib; ast.parse(pathlib.Path('weblate/auth/permissions.py').read_text())"
```

Expected: no output from either command.

Test command used throughout, which avoids all local setup:

```bash
./rundev.sh test weblate/machinery/tests.py -k separator
```

Host form, if the container is unavailable:

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
    weblate/machinery/tests.py -k separator
```

---

## Task 1: Measure before building - COMPLETE

**Files:**

- Created: `analysis/probes/batch-anchor-threshold.py`
- Created: `docs/llm-first/measurements/2026-08-25-deterministic-alignment-limits.md`

Ran offline over `analysis/data/heart-abyss-hub-1-units.tsv` (396 ru -> en, 396
ru -> fr), `analysis/data/st2-zh-units.jsonl` (124 ru -> zh) and
`analysis/data/col4-b0-annotations.jsonl` (183 human-`pass`, 77 human-`defect`,
ru -> fr). 1099 pairs treated as correctly aligned.

Outcome, which reshaped this plan:

1. The tight `$` rule has 0 false positives and 1 true positive on a `critical`
   annotated defect. It is the whole of Task 2.
2. The number rule judges nothing and misfires once. Dropped.
3. The duplicate rule rests on 5 observed convergences; deferred above.
4. An earlier claim in this work that `st2-zh` was "48% anchored" was wrong and
   is corrected in the measurement §3.1: 59 of those 60 digits sit inside
   placeholders, which the existing gate already validates exactly.

Reproduce with:

```bash
uv run python analysis/probes/batch-anchor-threshold.py
```

**Commit (if not already committed):**

```bash
git add analysis/probes/batch-anchor-threshold.py \
        docs/llm-first/measurements/2026-08-25-deterministic-alignment-limits.md \
        docs/llm-first/plans/2026-08-25-llm-batch-separator-gate.md
git commit -m "docs(llm-first): measure the deterministic alignment limits"
```

---

## Task 1.5: Capture an unmodified test baseline

**Files:** none modified.

Run this only after the prerequisites pass and before changing
`weblate/machinery/llm.py` or `weblate/machinery/tests.py`. It must run from the
same checkout and test environment as the later head run. If the prerequisite
requires a clean worktree, capture both baseline and head in that same worktree.

### Step 1: Capture both owning baselines

```bash
./rundev.sh test weblate/machinery/tests.py > /tmp/machinery-base.txt
./rundev.sh test weblate/trans/tests/test_autotranslate.py > /tmp/autotranslate-base.txt
```

Expected: both outputs are retained for comparison. Do not use `git stash` or
`git checkout` to manufacture a baseline: neither removes a committed
implementation, and either can disturb another owner's work.

---

## Task 2: Refuse a reply that loses or adds a tight `$` separator

**Files:**

- Modify: `weblate/machinery/llm.py` (module constants; classmethods after
  `_extract_literal_at_suffixes`, `weblate/machinery/llm.py:1750-1758`; calls in
  `_validate_translations`, `weblate/machinery/llm.py:2493-2513`, and
  `_validate_translation_prefix`, `weblate/machinery/llm.py:2564-2569`)
- Test: `weblate/machinery/tests.py`, class `OpenAITranslationTest`, which begins
  at `weblate/machinery/tests.py:3807`

### Step 1: Write the acceptance tests first

Currency is how this rule breaks, so pin the abstentions before the refusals. A
gate that refuses correct translations is worse than no gate. The predicate
takes the target `Unit` so it can preserve the existing explicit check ignores.

```python
class OpenAITranslationTest:
    def test_separators_survive_ignores_currency_and_prose(self) -> None:
        # ruff: ignore[private-member-access]
        survives = self.MACHINE_CLS._separators_survive
        pairs = (
            ("Цена: $100", "Price: 100 USD"),
            ("Стоит 500$", "Costs 500 dollars"),
            ("Знак $ означает валюту", "Le signe dollar"),
            ("$", "dollar"),
            ("Скидка $ 20", "Remise de 20"),
            ("Цена $100 или $200", "Price 100 or 200"),
        )
        for source, target in pairs:
            with self.subTest(source=source):
                self.assertTrue(survives(source, target, None))

    def test_separators_survive_accepts_real_production_pairs(self) -> None:
        # ruff: ignore[private-member-access]
        survives = self.MACHINE_CLS._separators_survive
        pairs = (
            (
                "Леон, вставай. Мы уже приехали.",
                "Leon, lève-toi. Nous sommes arrivés.",
            ),
            ("Добро пожаловать в Столицу.", "Bienvenue dans la Capitale"),
            ("Знаете ли вы, что его войска….", "Savez-vous que ses troupes…"),
        )
        for source, target in pairs:
            with self.subTest(source=source):
                self.assertTrue(survives(source, target, None))

    def test_separators_survive_leaves_target_spacing_to_the_autofix(self) -> None:
        # ruff: ignore[private-member-access]
        self.assertTrue(
            self.MACHINE_CLS._separators_survive(
                "Line$Next", "Ligne$\u00a0Suivante", None
            )
        )

    def test_separators_survive_honors_explicit_check_ignores(self) -> None:
        # ruff: ignore[private-member-access]
        survives = self.MACHINE_CLS._separators_survive
        source = "Первая$Вторая"
        for flags in ("ignore-game-line-break", "ignore-all-checks"):
            with self.subTest(flags=flags):
                unit = make_unit(code="fr", source=source, flags=flags)
                self.assertTrue(survives(source, "Une ligne", unit))
```

### Step 2: Write the refusal and drift tests

```python
class OpenAITranslationTest:
    def test_separators_survive_refuses_the_measured_critical_defect(self) -> None:
        # ruff: ignore[private-member-access]
        survives = self.MACHINE_CLS._separators_survive
        self.assertFalse(
            survives(
                "Очень хорошо сказано!$Сдохни!",
                "Très bien dit\u202f! Meurs\u202f!",
                None,
            )
        )

    def test_separators_survive_refuses_a_lost_or_added_separator(self) -> None:
        # ruff: ignore[private-member-access]
        survives = self.MACHINE_CLS._separators_survive
        self.assertFalse(
            survives("Первая строка$Вторая строка", "Une seule ligne", None)
        )
        self.assertFalse(survives("Первая$Вторая", "Erste$Zweite$Dritte", None))
        self.assertTrue(survives("Первая$Вторая", "Erste$Zweite", None))
        self.assertTrue(survives("Одна$Две$Три", "Eins$Zwei$Drei", None))

    def test_separator_tightness_matches_the_customization_check(self) -> None:
        """The core copy of the tightness rule must not drift from the check."""
        try:
            # ruff: ignore[import-outside-top-level]
            from weblate_customization.checks import separator_is_tight
        except ImportError:
            self.skipTest("weblate_customization is not importable here")
        # ruff: ignore[private-member-access]
        mine = self.MACHINE_CLS._separator_is_tight
        for text in (
            "a$b",
            "a $b",
            "a$ b",
            "$a",
            "a$",
            "$",
            "no dollar at all",
            "a$b$c",
            "a\u00a0$b",
            "a\u2009$b",
            "a\u202f$b",
            "a\t$b",
        ):
            with self.subTest(text=text):
                self.assertEqual(mine(text), separator_is_tight(text))
```

### Step 3: Run them to verify they fail

```bash
./rundev.sh test weblate/machinery/tests.py -k separator
```

Expected: FAIL, `AttributeError: type object 'OpenAITranslation' has no
attribute '_separators_survive'`.

### Step 4: Implement

Add beside the other module-level constants in `weblate/machinery/llm.py`:

```python
SEPARATOR_SPACE = r"[ \t\u00a0\u2009\u202f]"
SEPARATOR_LOOSE_IN_SOURCE_RE = re.compile(
    rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}|^\$|\$$"
)
LINE_BREAK_IGNORE_FLAGS = ("ignore-game-line-break", "ignore-all-checks")
```

The source predicate deliberately duplicates
`weblate_customization.checks.SEPARATOR_LOOSE_IN_SOURCE`: core must not import
the optional customization package, and the drift test keeps both definitions
in step wherever both are importable.

Add after `_extract_literal_at_suffixes` (`weblate/machinery/llm.py:1758`):

```python
class BaseLLMTranslation:
    @classmethod
    def _separator_is_tight(cls, source_text: str) -> bool:
        """Whether `$` is the engine line separator here, not currency."""
        return "$" in source_text and not SEPARATOR_LOOSE_IN_SOURCE_RE.search(
            source_text
        )

    @classmethod
    def _separators_survive(
        cls, source_text: str, translation: str, unit: Unit | None
    ) -> bool:
        """
        Whether the reply kept the engine line-separator count its source carries.

        This is a count gate only. ``LineSeparatorSpacing`` owns whitespace
        beside a surviving separator at target-write time, so that deterministic
        correction must not cause an LLM retry. A rejected singleton can remain
        untranslated; it is never built into a translation result.
        """
        if unit is not None and any(
            flag in unit.all_flags for flag in LINE_BREAK_IGNORE_FLAGS
        ):
            return True
        if not cls._separator_is_tight(source_text):
            return True
        return translation.count("$") == source_text.count("$")
```

Tightness is deliberately tested on the original source, not on a
placeholder-stripped copy: stripping inserts whitespace and would turn a tight
`A$@@PH1@@` into a loose one.

### Step 5: Run the tests to verify they pass

```bash
./rundev.sh test weblate/machinery/tests.py -k separator
```

Expected: PASS for every added separator test. The drift test skips if
`weblate_customization` is not importable.

### Step 6: Wire it into both reply-validation paths

In `_validate_translations`, inside the per-item loop, after the existing
`_extract_literal_at_suffixes` refusal (`weblate/machinery/llm.py:2506-2510`):

```python
class BaseLLMTranslation:
    def _validate_translations(self) -> None:
        if not cls._separators_survive(
            source_text, normalized_translation, sources[index][1]
        ):
            msg = "Mismatching assistant reply separators."
            raise MachineTranslationError(msg)
```

In `_validate_translation_prefix`, extend the existing combined condition
(`weblate/machinery/llm.py:2564-2569`) with the same test and its already-bound
`unit`, keeping prefix semantics of stopping rather than raising:

```python
class BaseLLMTranslation:
    def _validate_translation_prefix(self) -> None:
        if (
            cls._extract_placeholders(normalized)
            != cls._extract_placeholders(source_text)
            or cls._extract_literal_at_suffixes(normalized)
            != cls._extract_literal_at_suffixes(source_text)
            or not cls._separators_survive(source_text, normalized, unit)
        ):
            break
```

The distinct message matters: `log_handled_error` records it. A refused
singleton is observable as `LLMUsageLog(outcome="refused", batch_size=1)`.

### Step 7: Test complete-reply, partial-prefix, and terminal-singleton recovery

Add to `OpenAITranslationTest`, modelled on
`test_translate_refuses_a_batch_reply_with_shifted_ids`
(`weblate/machinery/tests.py:3961-4028`):

```python
class OpenAITranslationTest:
    @http_mock.activate
    def test_translate_refuses_a_batch_reply_that_drops_a_separator(self) -> None:
        machine = self.get_machine()
        sources = ["Первая$Вторая", "Одна строка"]
        batch_sizes: list[int] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            batch_sizes.append(len(strings))
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [
                            {
                                "type": "text",
                                "text": (
                                    item["source"]
                                    if len(strings) == 1
                                    else item["source"].replace("$", " ")
                                ),
                            }
                        ],
                    }
                    for item in strings
                ]
            )

        with (
            patch.object(
                machine, "fetch_llm_translations", side_effect=request_callback
            ),
            patch.object(
                machine, "log_handled_error", wraps=machine.log_handled_error
            ) as handled,
        ):
            translations = machine.download_multiple_translations(
                "ru", "fr", [(text, None) for text in sources]
            )

        self.assertEqual(batch_sizes[0], 2)
        self.assertGreater(len(batch_sizes), 1)
        self.assertTrue(
            any(
                call.args[0].startswith("Mismatching assistant reply separators")
                for call in handled.call_args_list
            )
        )
        self.assertEqual(translations["Первая$Вторая"][0]["text"], "Первая$Вторая")

    @http_mock.activate
    def test_translate_does_not_accept_a_flattened_separator_in_partial_reply(
        self,
    ) -> None:
        machine = self.get_machine()
        sources = ["Первая строка", "Вторая$Третья", "Четвёртая строка"]
        requested_sources: list[list[str]] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            requested_sources.append([item["source"] for item in strings])
            if len(requested_sources) == 1:
                return json.dumps(
                    [
                        {
                            "id": strings[0]["id"],
                            "parts": [{"type": "text", "text": strings[0]["source"]}],
                        },
                        {
                            "id": strings[1]["id"],
                            "parts": [{"type": "text", "text": "Вторая Третья"}],
                        },
                    ]
                )
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [{"type": "text", "text": item["source"]}],
                    }
                    for item in strings
                ]
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translations = machine.download_multiple_translations(
                "ru", "fr", [(text, None) for text in sources]
            )

        self.assertEqual(requested_sources, [sources, sources[1:]])
        self.assertEqual(translations["Вторая$Третья"][0]["text"], "Вторая$Третья")

    @http_mock.activate
    def test_translate_never_builds_a_persistently_mismatched_singleton(
        self,
    ) -> None:
        machine = self.get_machine()
        source = "Первая$Вторая"
        request_sizes: list[int] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            request_sizes.append(len(strings))
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [
                            {
                                "type": "text",
                                "text": item["source"].replace("$", " "),
                            }
                        ],
                    }
                    for item in strings
                ]
            )

        with (
            patch.object(
                machine, "fetch_llm_translations", side_effect=request_callback
            ),
            patch.object(
                machine,
                "_build_translation_results",
                wraps=machine._build_translation_results,
            ) as build_results,
        ):
            with self.assertRaisesRegex(
                MachineTranslationError, "Mismatching assistant reply separators"
            ):
                machine.download_multiple_translations("ru", "fr", [(source, None)])

        self.assertEqual(request_sizes, [1])
        build_results.assert_not_called()
```

The partial-prefix test is essential: without the prefix guard, the parser
accepts the flattened second item as a valid prefix and only retries the third
item. The singleton test pins the terminal behavior: no `TranslationResult`
exists from a persistently mismatched reply.

### Step 8: Run them

```bash
./rundev.sh test weblate/machinery/tests.py -k separator
```

Expected: PASS.

### Step 9: Prove every guard can fail

Required by this repository's own experience: a regression test that passes
against the bug is worthless.

1. Temporarily make `_separators_survive` return `True` after its ignore and
   tightness guards. Run `-k separator`. The critical, lost/addition,
   complete-reply, partial-prefix, and terminal-singleton tests must fail; the
   currency, prose, target-spacing and explicit-ignore tests must still pass.
2. Restore the count comparison. Then remove only the
   `_separators_survive(..., unit)` clause from `_validate_translation_prefix`.
   `test_translate_does_not_accept_a_flattened_separator_in_partial_reply` must
   fail while the complete-reply and terminal-singleton tests still pass.
3. Restore the prefix guard and confirm the separator tests pass again.

If a refusal test passed with its relevant guard removed, fix the test, not the
code.

### Step 10: Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "fix(machinery): refuse LLM replies with mismatched tight separators"
```

---

## Task 3: Confirm no existing contract regressed

**Files:** none modified.

### Step 1: Reuse the pre-change baseline

Task 1.5 must already have produced `/tmp/machinery-base.txt` and
`/tmp/autotranslate-base.txt`. Do not recreate either after Task 2 has been
committed.

### Step 2: Run the machinery suite with the change

```bash
./rundev.sh test weblate/machinery/tests.py > /tmp/machinery-head.txt
diff <(grep -E '^(FAILED|ERROR)' /tmp/machinery-base.txt) \
     <(grep -E '^(FAILED|ERROR)' /tmp/machinery-head.txt)
```

Expected: empty diff.

### Step 3: Compare the autotranslate suite with its baseline

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py > /tmp/autotranslate-head.txt
diff <(grep -E '^(FAILED|ERROR)' /tmp/autotranslate-base.txt) \
     <(grep -E '^(FAILED|ERROR)' /tmp/autotranslate-head.txt)
```

Expected: empty diff. This suite is known flaky under xdist, so only a
pre-change comparison is meaningful.

### Step 4: Run scoped lint and type checks

```bash
uv run prek run --files \
  weblate/machinery/llm.py \
  weblate/machinery/tests.py \
  --skip typos \
  --skip reuse \
  --skip kingfisher-auto
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: no new findings attributable to `weblate/machinery/llm.py`. Do not run
`prek --all-files`: unrelated pre-existing files can be rewritten by that command.

---

## Task 4: Changelog

**Files:**

- Modify: `docs/changes.rst`, top unreleased section only

### Step 1: Add the entry

The behaviour is user-visible: a batch that loses or adds an engine separator is
now refused and re-asked. A unit marked to ignore the existing line-break check
continues to bypass this gate.

```rst
* Automatic translation now refuses an LLM batch reply that loses or adds a
  ``$`` line separator and re-asks the batch in halves.
```

### Step 2: Run scoped documentation hooks

```bash
uv run prek run --files docs/changes.rst \
  --skip typos \
  --skip reuse \
  --skip kingfisher-auto \
  --skip rst-bullet-stop
```

`rst-bullet-stop` is skipped because it has a pre-existing violation in
`docs/changes.rst`; this change must not rewrite unrelated released entries.

### Step 3: Commit

```bash
git add docs/changes.rst
git commit -m "docs(changes): note the LLM batch separator gate"
```

---

## Task 5: Close out

**Files:** none modified.

### Step 1: Confirm the change stayed inside core

```bash
git diff --name-only origin/main...HEAD
```

Expected: only paths under `weblate/`, `docs/` and `analysis/probes/`.
`weblate_customization/` is untouched, so the
`cp -r weblate_customization/src/weblate_customization dev-docker/data/python/`
step does not apply.

### Step 2: Push

```bash
git push -u origin HEAD
```

### Step 3: Report honestly

State in the final summary:

- what this would have refused: `col4/data` `EVENT_408`, the lost separator,
  severity `critical`;
- what it would **not**: the `heart-abyss/hub-1` German rotation, the `zh_Hant`
  cross-segment exchange and the `col4/data` 88213/88214 duplicate - the first
  two invisible to every deterministic feature measured, the third belonging to
  the deferred rule;
- that target whitespace remains the deterministic `LineSeparatorSpacing`
  autofix's responsibility and explicit line-break check ignores still work;
- that the number rule was dropped and the duplicate rule deferred on measured
  grounds, not forgotten;
- that baseline spend is unchanged; a full batch-10 refusal is about $0.0044
  total and $0.0022 incremental before repeated fixed prompt tokens; and a
  repeated singleton refusal can leave a string untranslated;
- that no production instance was touched and no provider call was made.

Do not deploy. Deployment needs separate explicit approval.

---

## Acceptance

Complete only when all of these hold:

1. `_separators_survive` returns `True` for every currency and prose case, every
   real production pair, and target whitespace that the existing autofix owns.
2. `ignore-game-line-break` and `ignore-all-checks` bypass the count gate.
3. `_separator_is_tight` agrees with
   `weblate_customization.checks.separator_is_tight` on every case in the drift
   test, wherever both are importable.
4. `_separators_survive` returns `False` for the measured `critical` defect, for
   a dropped separator and for an added one.
5. A complete batch that flattens a separator is refused under `Mismatching
   assistant reply separators.` and the string is stored intact after recovery.
6. A persistently mismatched singleton raises `MachineTranslationError` and
   never reaches `_build_translation_results`.
7. A partial reply never returns a flattened separator as an accepted prefix.
8. Every direct, complete-reply, prefix-rescue, and terminal-singleton refusal
   test has been shown to fail when its relevant guard is removed.
9. Both owning suites show an empty failure diff against the Task 1.5 baseline.
10. No number-survival rule, duplicate-target rule, or target-whitespace gate was
    added.
11. `batch_size` is unchanged.
12. No production command was run and no provider call was made.
