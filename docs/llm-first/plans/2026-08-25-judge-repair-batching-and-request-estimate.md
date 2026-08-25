# Judge Repair Batching and Request Estimate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Send judge repairs through the existing machinery batch scheduler instead of one request per string, and show a request count in the automatic-translation form that is computed from batches rather than strings.

**Architecture:** The generic machinery-batch scheduler moves out of `weblate/trans/autotranslate.py` into `weblate/trans/machinery.py`, because `autotranslate.py` already imports `run_judge_batch` from `judge_loop` (`weblate/trans/autotranslate.py:25`) and the reverse import would be a cycle. `judge_loop` then collects every repair candidate of one round through that scheduler in the engine's own batches, converts each batch result into the candidate shape the current selection code already validates, and keeps the per-unit lock, snapshot comparison, deterministic-check rollback, verdict persistence and re-judge flow untouched. The form estimates judge calls as `ceil(strings / JUDGE_BATCH_SIZE) × seats × rounds` through one helper that lives next to `JUDGE_BATCH_SIZE`, so the UI and the transport cannot drift.

**Tech Stack:** Python 3.13+, Django, Celery, Weblate `BatchMachineTranslation`, pytest, Django i18n templates, Ruff/prek.

---

Status: **awaiting approval.** Revision 2 (2026-08-25), rewritten after the
review of revision 1. Every correction is listed under "What revision 2
changed" with the evidence that forced it.

## Scope and decisions

Production evidence from `victory-banner/common/es`, 2026-08-25:

- 60 empty Spanish units were translated in six `openrouter` requests (the
  configured batch size is ten, `weblate/machinery/llm.py:345`), with zero
  `Mismatching assistant reply ids`.
- Attempt 1 repaired 14 units. `repair_target()` calls
  `engine.translate(unit, user)` once per unit
  (`weblate/trans/judge_loop.py:85-113`), so it used 14 separate requests and
  repaid the fixed prompt prefix each time.
- The form's formula is `strings × 2 seats × (1 + attempts)`
  (`weblate/trans/views/basic.py:802-812`), while `request_verdicts()`
  transports `JUDGE_BATCH_SIZE` segments per call
  (`weblate/trans/judge.py:483-490`). With 60 strings, batch size five and one
  allowed repair round the form says 240 where the batch-based number is 48.

### Preconditions

1. `docs/llm-first/plans/2026-08-25-judge-repair-error-isolation.md` must be
   landed first. Its change and its two tests
   (`weblate/trans/tests/test_judge_loop.py:146,160`) already exist in the
   working tree. Task 3 of this plan deletes the guard it adds, because after
   batching the failure is handled inside `repair_targets()` for both paths;
   the two tests are rewritten there, not deleted.
2. All line numbers below assume that change is committed
   (`_process_round_unit` at `weblate/trans/judge_loop.py:276-320`, the repair
   call and its `try/except` at `:306-309`).

### Invariants this work must preserve

1. A repair is attempted only after both seats have judged the round, only for
   `writable_ids`, and only before `JUDGE_MAX_REPAIR_ATTEMPTS` is exhausted.
2. `run_checks()` runs on the refreshed unit *before* its repair request is
   built, so the judge's own finding reaches the repair prompt
   (`weblate/machinery/llm.py:1035-1041` reads `failing_checks` per string).
   Batching must not make candidate preparation lazy.
3. A batch result that lacks a usable candidate for one unit leaves that unit
   unchanged; a successful sibling must still be repaired and re-judged.
4. `_apply_repair()` remains the only writer of a repaired target. Its lock,
   request/context snapshot comparison, `STATE_FUZZY` interim state and
   deterministic-check rollback stay unchanged.
5. `run_judge_batch()` keeps all three per-unit outcomes: a stale or unusable
   round drops the unit's verdict (`verdicts.pop`), an unchanged unit records
   its final snapshot (`record_final_snapshot`), a changed unit is re-judged.
   The snapshot is load-bearing: `weblate/trans/autotranslate.py:818-842`
   refuses to project a final state when the recorded snapshot does not match
   the locked unit, and the 2026-08-25 measurement failed with exactly that
   symptom (`финальных состояний 0`).
6. A repaired unit is re-judged by both seats. Audit rows remain one row per
   `(unit, run_id, attempt, seat)`; no verdict is overwritten.
7. An engine that answers one string per request keeps today's one-request-per-
   unit behaviour. `LTEngineTranslation` is such a service
   (`weblate/machinery/libretranslate.py:178-182`, `batch_size = 1`).
8. The UI number is a planned judge request count. It is not a dollar
   estimate, not a quality claim and not an absolute ceiling: a refused batch
   is retried once (`weblate/trans/judge.py:477`).

### What revision 2 changed

| # | Revision 1 said | Why it was wrong | Now |
|---|---|---|---|
| 1 | Discriminate the fallback with `isinstance(engine, BatchMachineTranslation)` and test it with a non-`BatchMachineTranslation` fake engine | `MACHINERY = ClassRegistry(..., base_class=BatchMachineTranslation)` (`weblate/machinery/models.py:20-24`) and `class MachineTranslation(BatchMachineTranslation)` (`weblate/machinery/base.py:1319`), so the check is always true and the registry cannot host such a class | Discriminate on `engine.batch_size == 1`, test with a registry-shaped service (Task 2) |
| 2 | "Convert each `UnitMemoryResultDict` through the shared candidate-selection helper" | The helper input is `list[list[dict]]` with `text`/`quality` (`judge_loop.py:99-113`); the scheduler returns `{"translation": [...], "quality": [...], "origin": [...]}` (`base.py:1286-1304`) | An explicit adapter, `_machinery_candidates()` (Task 2) |
| 3 | "the normal machinery threshold" | Ambiguous: `translate()` defaults to `MACHINERY_DEFAULT_THRESHOLD = 80` (`base.py:52,851`) while phase 1 passes the form's threshold (`autotranslate.py:656`) | `MACHINERY_DEFAULT_THRESHOLD`, named (Task 2) |
| 4 | `assertContains(response, "8 LLM requests")` | The template renders the number and the sentence as separate nodes (`weblate/templates/snippets/autoform.html:40-44`), so the HTML contains `8\n          LLM requests`; the assertion could never pass and the "verify it fails" gate would have passed for the wrong reason | One `{% blocktranslate %}` with the number interpolated (Task 5) |
| 5 | "create enough non-readonly, un-translated units" | Units come from the VCS file, not the ORM; the `ViewTestCase` fixture is `weblate/trans/tests/data/cs.po` | Compute both numbers from `response.context["judge_row_count"]` and pick a batch size that crosses the boundary (Task 5) |
| 6 | `assertNotContains(response, "24 LLM requests")` | Vacuous: that string can never render | Assert the old string-based number is absent, computed from the rendered row count (Task 5) |
| 7 | `prepared → repairs → item.apply()` two-state pseudocode | Loses `verdicts.pop()` and `record_final_snapshot()` from `run_judge_batch:459-478` | Explicit three-state coordinator (Task 3) |
| 8 | "Modify: `test_judge_loop.py:12-18,71-83,98-139`" | Seven patch sites in two files: `test_judge_loop.py:83,187,281,339,375,470` and `test_judge_autotranslate.py:197`, plus the import at `test_judge_loop.py:14` and the unit test at `:103-115` | All sites enumerated (Task 3) |
| 9 | "the real worst-case judge request count" | `request_verdicts()` retries a refused batch once (`judge.py:477`), so the true ceiling is twice this number | "plans up to", with the retry stated (Task 5) |
| 10 | Acceptance: exact `ceil(units / batch_size)` requests; an unusable result "remains local to its unit" | The LLM layer halves a malformed batch and re-asks (`llm.py:2676-2721`), so the count holds on the happy path and isolation is achieved *after* that rescue | Both criteria restated |
| 11 | Task 4 verified everything with mocks and no provider call | Correct constraint, but revision 1 also had no test for what the measurement actually observed | Two deterministic judge-level isolation tests plus raw-reply capture in the probe (Task 4, Task 6) |
| 12 | Silent about the error-isolation plan touching the same lines | Both plans are dated 2026-08-25 and edit the same call | Preconditions section, and Task 3 removes the guard it makes dead |

### Out of scope

- Changing judge verdict thresholds, seat models, `JUDGE_MAY_APPROVE` or
  repair eligibility.
- A paid production experiment, a `rundev.sh` restart or a deployment.
- Any change to phase 2 progress accounting. `progress_steps` at
  `weblate/trans/autotranslate.py:805-807` counts strings judged, not requests
  sent; it stays string-based on purpose.
- A machine-translation request count in the form for empty strings.
- A cost dashboard, batch/outcome telemetry fields, or a changelog entry: this
  corrects an unreleased judge feature, and `LLMUsageLog` remains the cost
  source.
- Explaining the structured-reply refusal from the measurement
  (`docs/llm-first/measurements/2026-08-25-judge-repair-loop.md`, §2). The
  cause is not established there, and this plan does not claim to fix it. The
  id class of the same failure is already covered at the machinery layer by
  `test_translate_refuses_a_batch_reply_with_shifted_ids`
  (`weblate/machinery/tests.py:3961-4028`).

## Task 1: Make the machinery scheduler reusable without changing its behaviour

**Files:**

- Create: `weblate/trans/machinery.py`
- Modify: `weblate/trans/autotranslate.py:5-20,50-56,59-265`
- Modify: `weblate/trans/tests/test_autotranslate.py:41-47`

### Step 1: Point the existing focused tests at the new owner

`MachineryBatchFetchTest` (`weblate/trans/tests/test_autotranslate.py:1311`)
already covers engine batch sizing, parallel dispatch, rate-limit retry and
failed-batch isolation. Change only the import block at `:41-47` so
`fetch_machinery_matches` comes from the new module and the other two names
keep coming from `autotranslate`:

```python
from weblate.trans.autotranslate import AutoTranslate, BatchAutoTranslate
from weblate.trans.machinery import fetch_machinery_matches
```

### Step 2: Run the focused tests to verify the import fails

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_autotranslate.py -q -k MachineryBatchFetchTest
```

Expected: collection error, `ModuleNotFoundError: No module named
'weblate.trans.machinery'`.

### Step 3: Extract the scheduler verbatim

Create `weblate/trans/machinery.py` with the repository copyright/SPDX header
and move, without semantic edits, from `autotranslate.py`:

- `RATE_LIMIT_WAIT`, `RATE_LIMIT_POLL`;
- `_fetch_machinery_batch`, `_wait_for_rate_limit`, `_fetch_machinery_batches`,
  `_fetch_machinery_service`, `fetch_machinery_matches`.

Two details that a "verbatim" move loses if you are not careful:

- The moved code logs through the project logger, `from weblate.logger import
  LOGGER` (`autotranslate.py:20`). Import the same symbol in the new module;
  do **not** substitute `logging.getLogger(__name__)`, or the records change
  destination.
- Keep the public signature and return type exactly as they are, including the
  `TYPE_CHECKING` imports the annotations need:

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
) -> dict[int, UnitMemoryResultDict]: ...
```

In `autotranslate.py`, import `fetch_machinery_matches` from the new module,
delete the moved definitions, and delete only the imports the move actually
orphaned. `time`, `connections`, `MachineTranslationError` and `LOGGER` all
have other users in that file, so let Ruff decide rather than guessing. No
compatibility re-export.

### Step 4: Run the focused tests

Run the command from Step 2.

Expected: every `MachineryBatchFetchTest` case passes unchanged, proving the
concurrency, rate-limit and per-batch failure contracts survived the move.

### Step 5: Commit

```bash
git add weblate/trans/machinery.py weblate/trans/autotranslate.py \
  weblate/trans/tests/test_autotranslate.py
git commit -m "refactor(trans): share machinery batch scheduler"
```

## Task 2: Add a batch-capable repair-candidate API that writes nothing

**Files:**

- Modify: `weblate/trans/judge_loop.py:28-31,85-113`
- Modify: `weblate/trans/tests/test_judge_loop.py:14,103-115`

Every commit in this plan is green. Task 2 therefore keeps `repair_target()`
as a two-line delegate so the seven sites that patch it keep working; Task 3
deletes it in the same commit that stops calling it.

### Step 1: Write the failing candidate-API tests

Retarget the shipped selection test at `:103-115` at the extracted selector
(it is a pure function now, so the engine mock is no longer needed), and add
four cases next to it. `make_openrouter` stands in for the registry entry the
same way that test does today.

```python
class JudgeLoopTest(ViewTestCase):  # existing class
    def test_selection_takes_the_best_candidate_per_plural_form(self) -> None:
        # Replaces test_repair_target_selects_a_candidate_per_plural_form:
        # same contract, no engine mock, because selection is now pure.
        unit = self.get_unit()
        self.assertEqual(
            _select_repair_texts(
                unit,
                [
                    [
                        {"text": "lower quality", "quality": 50},
                        {"text": "fixed text", "quality": 100},
                    ]
                ],
            ),
            ["fixed text"],
        )

    def make_openrouter(self, engine):
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        return mock.patch("weblate.trans.judge_loop.MACHINERY", {"openrouter": engine})

    def test_repair_targets_asks_a_batch_engine_once(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["units"], [unit])
        self.assertEqual(
            fetch.call_args.kwargs["threshold"], MACHINERY_DEFAULT_THRESHOLD
        )
        engine.return_value.translate.assert_not_called()

    def test_repair_targets_skips_a_unit_without_a_usable_candidate(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        # A partially answered unit: the scheduler pre-fills "" for a plural
        # form the reply never carried (machinery/base.py:1295).
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": [""],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_skips_a_result_whose_lists_disagree(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        # A result that breaks the machinery contract must cost that unit its
        # repair, never the whole round (invariant 3).
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text", "extra form"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_keeps_one_request_per_unit_for_a_single_string_engine(
        self,
    ) -> None:
        # LTEngineTranslation ships batch_size = 1
        # (machinery/libretranslate.py:178-182).
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 1
        engine.return_value.translate.return_value = [
            [{"text": "fixed text", "quality": 100}]
        ]
        fetch = mock.Mock()
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_not_called()
        engine.return_value.translate.assert_called_once()
```

### Step 2: Run them to verify they fail

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q -k repair_targets
```

Expected: `ImportError: cannot import name 'repair_targets'`.

### Step 3: Split selection out of `repair_target()`

Rewrite `repair_target()` (`judge_loop.py:85-113`) as a selector, an adapter,
one entry point, and a delegate that keeps the old name working until Task 3
removes its last caller. The selector keeps the shipped rules verbatim: a
wrong number of plural forms, an empty candidate list, a non-string or empty
text, or a target equal to the current one all mean "no repair".

```python
def _select_repair_texts(
    unit: Unit, plural_candidates: list[list[dict]]
) -> list[str] | None:
    """Pick one usable text per plural form, or None."""
    if len(plural_candidates) != len(unit.get_target_plurals()):
        return None
    texts: list[str] = []
    for candidates in plural_candidates:
        if not candidates:
            return None
        best = max(candidates, key=lambda item: item.get("quality", 0))
        text = best.get("text", "")
        if not isinstance(text, str) or not text:
            return None
        texts.append(text)
    if texts == unit.get_target_plurals():
        return None
    return texts


def _machinery_candidates(
    unit: Unit, result: UnitMemoryResultDict
) -> list[list[dict]] | None:
    """Present one batch result the way translate() presents its own."""
    texts = result.get("translation", ())
    qualities = result.get("quality", ())
    if len(texts) != len(qualities):
        # A result that breaks the machinery contract costs this unit its
        # repair. Raising here would abort a whole round for one bad reply,
        # which is exactly what this plan removes.
        unit.translation.log_error(
            "judge repair: unusable machinery result for unit %d", unit.id
        )
        return None
    return [
        [{"text": text, "quality": quality}] for text, quality in zip(texts, qualities)
    ]
```

`batch_translate()` sizes both lists to the unit's plural count
(`weblate/machinery/base.py:1295-1297`), so unequal lengths mean a broken
contract. The adapter reports it and skips that unit; the selector's own
plural-count check then also rejects a result whose length disagrees with the
unit. Exceptions stay where the scheduler already isolates them, per batch.

```python
def repair_targets(units: list[Unit], user: User | None) -> dict[int, list[str]]:
    """
    Return usable repair targets keyed by unit id, writing nothing.

    Judge evidence reaches the repair prompt for free: the caller has
    already run run_checks(), so the round's judge-* Check row exists and
    weblate/machinery/llm.py feeds failing_checks into the prompt of every
    string in the batch. Callers MUST run_checks() first.
    """
    if not units:
        return {}
    translation = units[0].translation
    settings_map = translation.component.project.get_machinery_settings()
    engine_id = AutoForm.DEFAULT_ENGINE
    setting = settings_map.get(engine_id)
    if setting is None or engine_id not in MACHINERY:
        return {}
    engine = MACHINERY[engine_id](setting)
    if engine.batch_size == 1:
        # A service that answers one string per request gains nothing from
        # the scheduler, so it keeps the shipped per-unit path.
        return _repair_targets_per_unit(engine, units, user)
    try:
        matches = fetch_machinery_matches(
            units=units,
            user=user,
            services=[engine],
            threshold=MACHINERY_DEFAULT_THRESHOLD,
            log_translation=translation,
        )
    except MachineTranslationError as error:
        # The scheduler keeps a failing batch local to itself; this guards
        # the round against whatever still escapes, so one bad reply can
        # never cost the run its verdicts (invariant 3).
        translation.log_error("failed judge repair: %s", error)
        return {}
    repairs: dict[int, list[str]] = {}
    for unit in units:
        result = matches.get(unit.id)
        if result is None:
            continue
        candidates = _machinery_candidates(unit, result)
        if candidates is None:
            continue
        texts = _select_repair_texts(unit, candidates)
        if texts is not None:
            repairs[unit.id] = texts
    return repairs


def _repair_targets_per_unit(
    engine, units: list[Unit], user: User | None
) -> dict[int, list[str]]:
    repairs: dict[int, list[str]] = {}
    for unit in units:
        try:
            candidates = engine.translate(unit, user)
        except MachineTranslationError as error:
            # Symmetric with the scheduler, which keeps a failure local to
            # its batch (weblate/trans/machinery.py, _fetch_machinery_batch).
            unit.translation.log_error("failed judge repair: %s", error)
            continue
        texts = _select_repair_texts(unit, candidates)
        if texts is not None:
            repairs[unit.id] = texts
    return repairs


def repair_target(unit: Unit, user: User | None) -> list[str] | None:
    """
    Single-unit repair, kept until run_judge_batch() batches its round.

    Task 3 deletes this together with its last caller; it exists only so
    that this task's commit leaves every shipped test green.
    """
    return repair_targets([unit], user).get(unit.id)
```

Two contracts this pins down, both silent in revision 1:

- `MACHINERY_DEFAULT_THRESHOLD` (80) is the threshold `translate()` uses today
  (`weblate/machinery/base.py:52,851`), so repair eligibility does not move.
- `log_translation=translation` routes the scheduler's own log lines and its
  swallowed `MachineTranslationError` into the translation log
  (`weblate/trans/machinery.py`, `_fetch_machinery_batch`), so a repair that
  fails is visible instead of silent.

Add the imports this needs: `MACHINERY_DEFAULT_THRESHOLD` and
`UnitMemoryResultDict` from `weblate.machinery.base` (the latter under
`TYPE_CHECKING`) and `fetch_machinery_matches` from `weblate.trans.machinery`.

Do not write a target, change a state or run checks anywhere in this task.

### Step 4: Run the focused tests

Run the command from Step 2, then the file:

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q
```

Expected: green, the whole file. The five new cases pass, and the seven sites
that patch `weblate.trans.judge_loop.repair_target` keep passing through the
delegate, including the two error-isolation tests, which patch the name
itself and so never reach the new internals. A red run here means the delegate
is missing or `repair_targets()` swallowed something it should not.

### Step 5: Commit

```bash
git add weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): add a batch-capable repair-candidate API"
```

## Task 3: Stage, fetch, then apply repairs in one round

**Files:**

- Modify: `weblate/trans/judge_loop.py:276-320,455-485`, and delete the
  `repair_target()` delegate added by Task 2
- Modify: `weblate/trans/tests/test_judge_loop.py:14,72-88,146-190,187,281,339,375,470`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py:197`

### Step 1: Retarget the test harness and every patch site

`JudgeLoopTest.run_batch` (`test_judge_loop.py:72-88`) patches
`weblate.trans.judge_loop.repair_target` with a `Mock` returning a target.
Change it to patch `repair_targets` and return the dict shape, keyed by the
unit under test:

```python
class JudgeLoopTest(ViewTestCase):  # existing class
    def run_batch(self, seat_results, repair=None, writable=True, repair_error=None):
        ...  # client, unit and writable_ids unchanged
        if repair_error is not None:
            repair_mock = mock.Mock(side_effect=repair_error)
        else:
            repair_mock = mock.Mock(
                return_value={} if repair is None else {unit.id: repair}
            )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            ...  # the run_judge_batch() call and return are unchanged
```

Then update the direct patch sites, which pass their own side effects:
`test_judge_loop.py:187,281,339,375,470` and
`test_judge_autotranslate.py:197`. Each becomes a `repair_targets` patch whose
return value or side effect is a `dict[int, list[str]]`.

The two error-isolation tests need a new subject. After this task no
`MachineTranslationError` escapes `repair_targets()`, so
`test_repair_target_machinery_error_does_not_crash_the_batch` and
`test_repair_target_error_on_one_unit_lets_others_continue`
(`test_judge_loop.py:146-190`) must assert the same property one layer down:
a `fetch_machinery_matches` that raises, and a `repair_targets` that returns a
dict missing one unit, must both leave the run alive and the other unit
repaired. Rename them accordingly and keep the 2026-08-25 measurement
reference in their comments.

### Step 2: Add the batching contract test

```python
class JudgeLoopTest(ViewTestCase):  # existing class
    def test_a_negative_round_fetches_every_repair_in_one_call(self) -> None:
        first, second = self.get_two_repairable_units()
        repair_targets = mock.Mock(
            return_value={
                first.id: ["first repaired target"],
                second.id: ["second repaired target"],
            }
        )
        ...
        repair_targets.assert_called_once()
        self.assertEqual(
            [unit.id for unit in repair_targets.call_args.args[0]],
            [first.id, second.id],
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, "first repaired target")
        self.assertEqual(second.target, "second repaired target")
        self.assertEqual(client.call_count, 4)  # two seats × two rounds
```

Add a sibling test where the mock returns only `{first.id: [...]}` and assert
that `second` keeps its old target, keeps its verdict, and is not re-judged,
while `first` is repaired and re-judged. That is invariant 3.

### Step 3: Run both to verify they fail

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q
```

Expected: failures naming `repair_targets` as an unexpected patch target for
`weblate.trans.judge_loop`, because the loop still calls `repair_target` per
unit inside `_process_round_unit`.

### Step 4: Split `_process_round_unit()` into prepare and apply

Replace `_process_round_unit()` with a preparation function that returns the
inputs `_apply_repair()` needs, or `None` when the round is unusable. This is
where the `try/except MachineTranslationError` added by the error-isolation
plan (`judge_loop.py:306-309`) is deleted: no machinery call happens here any
more.

```python
@dataclass(frozen=True)
class _PreparedRound:
    unit: Unit
    request: JudgeRequest
    verdict: JudgeVerdict
    needs_repair: bool
    before_target: list[str]
    before_checks: set[str]
    before_state: int


def _prepare_round_unit(
    unit: Unit,
    request: JudgeRequest,
    round_state,
    *,
    writable_ids: set[int],
    attempt: int,
    attempts: int,
) -> _PreparedRound | None:
    """Project a round; describe its repair inputs without fetching them."""
    current = _refresh_unit(unit)
    if current.state != round_state:
        return None
    current.invalidate_checks_cache()
    current.clear_checks_cache()
    current.run_checks()
    verdict = _round_verdict(current)
    if verdict is None:
        # A changed target/context or an all-unparsed round must not fall
        # back to an older opinion for repair or finalization.
        return None
    needs_repair = (
        verdict.verdict not in _NON_REPAIRABLE_VERDICTS
        and attempt < attempts
        and unit.id in writable_ids
    )
    return _PreparedRound(
        unit=current,
        request=request,
        verdict=verdict,
        needs_repair=needs_repair,
        before_target=current.get_target_plurals(),
        before_checks=_deterministic_checks(current),
        before_state=current.state,
    )
```

`run_checks()` stays inside preparation on purpose (invariant 2): the unit
instance handed to `repair_targets()` must already carry the round's judge-*
findings, because the batch prompt reads them per string.

Then replace the loop body at `judge_loop.py:459-478` with the three-state
coordinator. Every branch below maps to one branch of the code it replaces;
none may be dropped.

```python
def run_judge_batch(units, *, writable_ids, user, on_batch=None):  # abridged
    while True:  # the existing round loop; everything above it is unchanged
        ...
        prepared = [
            _prepare_round_unit(
                unit,
                round_requests[unit.id],
                round_states[unit.id],
                writable_ids=writable_ids,
                attempt=attempt,
                attempts=attempts,
            )
            for unit in pending
        ]
        repairs = repair_targets(
            [item.unit for item in prepared if item is not None and item.needs_repair],
            user,
        )

        next_pending = []
        for unit, item in zip(pending, prepared, strict=True):
            if item is None:
                # Stale unit or unusable round: its verdict must not stand.
                verdicts.pop(unit.id, None)
                continue
            verdicts[unit.id] = item.verdict
            new_target = repairs.get(unit.id) if item.needs_repair else None
            if new_target is None:
                record_final_snapshot(item.unit)
                continue
            outcome = _apply_repair(
                item.unit,
                item.request,
                item.before_target,
                item.before_checks,
                item.before_state,
                new_target,
                user,
            )
            if outcome.unit is None:
                # The snapshot no longer owns the unit.
                verdicts.pop(unit.id, None)
                continue
            if outcome.changed:
                next_pending.append(outcome.unit)
            else:
                # Rolled back by the deterministic-check gate.
                record_final_snapshot(outcome.unit)
        pending = next_pending
```

Do not batch across judge runs, components, target languages or attempts.
`run_judge_batch()` already scopes one call to one translation and one round;
`fetch_machinery_matches` relies on that too, since `batch_translate()` reads
`units[0].translation` (`weblate/machinery/base.py:1249`).

One behavioural difference to accept knowingly: all candidates for a round are
now fetched before any is applied, so a repair no longer sees the database
state left by its predecessor in the same round. Correctness is unaffected,
because `_apply_repair()` still refuses a unit whose target, state or context
moved, but the rejection window is a few seconds wider.

### Step 5: Run the judge suites

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py -q
```

Expected: green, including the shipped regressions that a deterministic-check
failure restores the old target, a stale unit is untouched, the two seats stay
conjunctive, one repair earns exactly one re-judge round, and
`test_judge_autotranslate.py` still projects the final `STATE_TRANSLATED`
(that last one is the guard for invariant 5).

### Step 6: Commit

```bash
git add weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py
git commit -m "fix(judge): batch repair translations"
```

## Task 4: Prove per-unit isolation against a partial batch

**Files:**

- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the two isolation tests

These are the deterministic replacements for a paid replay. They describe what
the judge loop owes the caller no matter what the producer does, and both are
reachable with mocks only.

1. `fetch_machinery_matches` raises `MachineTranslationError` for the whole
   round: no unit is repaired, both verdicts stand, both units keep their
   final snapshot, and the run does not raise. This is the batch-level
   descendant of the error-isolation contract.
2. `fetch_machinery_matches` answers only the first unit: the shape the LLM
   layer produces after it halves a malformed batch and the tail is still
   unusable (`weblate/machinery/llm.py:2676-2721`). The answered unit is
   repaired and re-judged; the unanswered one keeps its target, its verdict
   and its final snapshot.

Patch `weblate.trans.judge_loop.fetch_machinery_matches`, not
`repair_targets`, so the adapter and the selector are exercised too.

### Step 2: Run them to verify they fail, then implement nothing

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_loop.py -q -k isolation
```

Expected: both pass immediately if Task 2 and Task 3 are correct. If either
fails, the defect is in Task 2's adapter or Task 3's coordinator; fix there,
not here. Tests that pass on the first run are only acceptable because their
subject was built two tasks earlier; if you cannot make either test fail by
reverting one line of Task 3, the test is not defending anything and must be
rewritten.

### Step 3: Commit

```bash
git add weblate/trans/tests/test_judge_loop.py
git commit -m "test(judge): cover repair isolation across a partial batch"
```

## Task 5: Estimate judge requests from batches

**Files:**

- Modify: `weblate/trans/judge.py:44`
- Modify: `weblate/trans/views/basic.py:20-30,802-812`
- Modify: `weblate/templates/snippets/autoform.html:40-44`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_views.py:31,77-81`

### Step 1: Write the failing helper tests

`weblate/trans/tests/test_judge.py` is a `SimpleTestCase` file with no
database, which is the right home for pure batch arithmetic:

```python
class JudgeRequestEstimateTest(SimpleTestCase):
    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_one_string_still_costs_one_batch(self) -> None:
        # An integer-floor regression would display zero here.
        self.assertEqual(judge_request_upper_bound(1), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_a_full_batch_is_not_rounded_up(self) -> None:
        self.assertEqual(judge_request_upper_bound(5), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_one_string_over_a_batch_adds_one_batch(self) -> None:
        self.assertEqual(judge_request_upper_bound(6), 8)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=0)
    def test_no_repair_attempt_means_one_round(self) -> None:
        self.assertEqual(judge_request_upper_bound(6), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_no_strings_costs_nothing(self) -> None:
        self.assertEqual(judge_request_upper_bound(0), 0)

    @override_settings(JUDGE_BATCH_SIZE=0, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_a_broken_batch_size_yields_no_number(self) -> None:
        # validate_judge_configuration() refuses such a run
        # (weblate/trans/judge.py:105); the form must not 500 before that.
        self.assertIsNone(judge_request_upper_bound(6))
```

### Step 2: Run them to verify they fail

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge.py -q -k Estimate
```

Expected: `ImportError: cannot import name 'judge_request_upper_bound'`.

### Step 3: Add the helper next to the batch size it depends on

In `weblate/trans/judge.py`, right after `JUDGE_SEATS` (`:44`):

```python
def judge_request_upper_bound(strings: int) -> int | None:
    """
    Requests one judge run plans for a number of strings.

    Batches, not strings: request_verdicts() sends JUDGE_BATCH_SIZE
    segments per call. A repair round re-judges only the strings that were
    repaired, so this is a ceiling for the run; it is not absolute,
    because a refused batch is retried once. None means the configured
    batch size cannot be read.
    """
    batch_size = settings.JUDGE_BATCH_SIZE
    if not isinstance(batch_size, int) or batch_size < 1:
        return None
    if strings < 1:
        return 0
    batches = (strings + batch_size - 1) // batch_size
    return batches * len(JUDGE_SEATS) * (1 + settings.JUDGE_MAX_REPAIR_ATTEMPTS)
```

### Step 4: Write the failing view test

Replace the assertion-free `test_form_shows_the_honest_request_estimate`
(`test_judge_views.py:77-81`). Derive both numbers from the row count the view
itself rendered, so the test survives a fixture change and still names the old
formula it must not produce:

```python
class JudgeAutoTranslateViewTest(ViewTestCase):  # existing class
    def test_form_estimates_requests_from_batches_not_strings(self) -> None:
        with override_settings(JUDGE_BATCH_SIZE=2):
            response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        rows = response.context["judge_row_count"]
        # The fixture must cross a batch boundary or the two formulas agree.
        self.assertGreater(rows, 2)
        batched = ((rows + 1) // 2) * 2 * 2
        per_string = rows * 2 * 2
        self.assertContains(response, f"plans up to {batched} LLM requests")
        self.assertNotContains(response, f"plans up to {per_string} LLM requests")

    def test_form_estimate_never_floors_a_partial_batch_to_zero(self) -> None:
        with override_settings(JUDGE_BATCH_SIZE=1000):
            response = self.client.get(self.translation_url)
        self.assertContains(response, "plans up to 4 LLM requests")
```

The class already sets `JUDGE_ENABLED=True, JUDGE_MAX_REPAIR_ATTEMPTS=1`
(`test_judge_views.py:31`), so both cases run with two seats and two rounds.

### Step 5: Run it to verify it fails

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge_views.py -q -k estimate
```

Expected: both fail on the missing `plans up to` wording, because the shipped
template renders `A judge run costs in the worst case up to`, the number, and
the `LLM requests (strings × 2 judges × attempts)` sentence as three separate
nodes.

### Step 6: Use the helper in the view

In `weblate/trans/views/basic.py`, import the helper with the other
`weblate.trans` imports (`:20-30`) and replace the formula at `:810-812`:

```python
judge_request_estimate = judge_request_upper_bound(judge_row_count)
```

Nothing else in `show_translation()` changes; both names already reach the
template through the context at `:910-911`.

### Step 7: Make the template render one translatable sentence

Replace `weblate/templates/snippets/autoform.html:40-44`. The number must sit
*inside* the translated string: today the sentence is split into two catalog
entries around a bare `{{ }}`, which is both untranslatable in languages with
different word order and impossible to assert on.

```django
        {% if judge_request_estimate is not None %}
          <p class="text-muted" id="id_auto_request_estimate">
            {% blocktranslate with requests=judge_request_estimate trimmed %}
              A judge run plans up to {{ requests }} LLM requests (batches × judges × rounds); a refused batch is retried once. Empty strings additionally cost machine translation.
            {% endblocktranslate %}
          </p>
        {% endif %}
```

`trimmed` joins the wrapped source into one line, which is what makes
`assertContains(response, "plans up to 8 LLM requests")` a legitimate
assertion. Keep the `id` attribute: three shipped tests assert on it.

### Step 8: Run the focused tests

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py -q
```

Expected: green, including the shipped `id_auto_row_count`,
`id_auto_request_estimate` and `id_auto_duration_estimate` assertions.

### Step 9: Commit

```bash
git add weblate/trans/judge.py weblate/trans/views/basic.py \
  weblate/templates/snippets/autoform.html \
  weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): estimate requests by batch"
```

## Task 6: Record raw producer replies in the repair probe

**Files:**

- Modify: `analysis/probes/judge-repair-probe.py`

### Step 1: Persist what the measurement could not

`docs/llm-first/measurements/2026-08-25-judge-repair-loop.md` §2 describes the
reply that broke the shipped repair path, but
`analysis/data/judge-repair-2026-08-25/run-01.json` carries only `verdicts`
and `changes`, so the payload itself was never saved, the shape had to be
transcribed by hand and its cause stayed unproven. Add the raw request/reply
text of every failed machinery call to the run artifact, keyed by unit id, so
the next occurrence is diagnosable without a rerun.

Keep it inside `analysis/`: this is measurement tooling, not product code, and
it is excluded from packaging and from the `typos`/`codespell` hooks.

### Step 2: Verify the artifact shape offline

Run the probe's own dry path (no provider key configured) and confirm the new
key appears and is empty:

```bash
uv run python analysis/probes/judge-repair-probe.py --help
```

Expected: the option/flag documentation mentions the capture, and no network
call is made.

### Step 3: Commit

```bash
git add analysis/probes/judge-repair-probe.py
git commit -m "chore(analysis): capture raw replies in the judge repair probe"
```

## Task 7: Verify the whole change without spending provider money

**Files:** none modified; verification only.

### Step 1: Run every suite this change touches

```bash
source scripts/test-database.sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_autotranslate.py -q
```

Expected: all pass, with mocked provider calls only. `test_autotranslate.py`
is flaky under xdist in this repository; if a failure appears there, re-run
that file alone before blaming this change.

### Step 2: Format and lint only the changed files

```bash
uv run prek run ruff-format ruff-check --files \
  weblate/trans/machinery.py \
  weblate/trans/autotranslate.py \
  weblate/trans/judge_loop.py \
  weblate/trans/judge.py \
  weblate/trans/views/basic.py \
  weblate/templates/snippets/autoform.html \
  weblate/trans/tests/test_autotranslate.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_views.py
```

Expected: both hooks pass and touch no unrelated file.

### Step 3: Confirm there is no model change and no untranslated string

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check
```

Expected: `No changes detected`. Then confirm by eye that the new template
sentence is inside `{% blocktranslate %}`, that no other user-facing string
was added, and that `id_auto_request_estimate` survives.

### Step 4: Check the type surface

```bash
uv run mypy --show-column-numbers weblate/trans/machinery.py \
  weblate/trans/judge_loop.py weblate/trans/judge.py | ./scripts/filter-mypy.sh
```

Expected: no new finding. The moved scheduler keeps its annotations, and
`repair_targets()` returns `dict[int, list[str]]`.

### Step 5: Correct, do not defer

If a check fails, make the smallest correction, re-run only that check, and
amend the commit that introduced the problem. Do not leave a placeholder or a
follow-up task.

### Step 6: Stop before anything paid or deployed

This plan authorises no `./deploy/vps.sh`, no `./rundev.sh` restart and no
provider request. Pushing the branch and any live replay of the
`judge-repair-probe` corpus are separate decisions that need their own
approval.

## Acceptance criteria

- [ ] One repair round calls the machinery scheduler once, and a batch-capable
      engine issues `ceil(repairable_units / engine.batch_size)` producer
      requests on the happy path, not one per repairable unit. A malformed
      reply may add requests, because the LLM layer halves the batch and
      re-asks (`weblate/machinery/llm.py:2676-2721`).
- [ ] An engine with `batch_size == 1` still issues one request per unit and
      never reaches the scheduler.
- [ ] A round whose batch answers only some units repairs exactly those, and
      leaves every other unit's target, verdict and final snapshot untouched.
- [ ] A `MachineTranslationError` anywhere in candidate fetching leaves the run
      alive, every verdict written, and every unit finalizable; no
      `try/except` for it remains in `judge_loop._prepare_round_unit`.
- [ ] Existing locks, stale-snapshot rejection, deterministic-check rollback,
      interim `STATE_FUZZY` and two-seat re-judging pass their shipped tests.
- [ ] `record_final_snapshot()` still runs for every unchanged unit, and
      `test_judge_autotranslate.py` still observes the projected final state.
- [ ] `judge_request_upper_bound()` is the only place batch arithmetic for the
      estimate exists; 1 string is one batch, a full batch is not rounded up,
      and a broken `JUDGE_BATCH_SIZE` renders no number instead of a 500.
- [ ] The form shows the batch-based number inside one `{% blocktranslate %}`
      sentence, says "plans up to" rather than "worst case", mentions the
      single retry, and still separates machine translation for empty strings.
- [ ] Phase 2 progress accounting (`weblate/trans/autotranslate.py:805-807`)
      is unchanged.
- [ ] No migration, no cost claim, no model-policy change, no paid call.
