<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Judge glossary-entry symmetry implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task. Use `test-driven-development` for Tasks 1-3 and
> `verification-before-completion` for Task 5.

**Date:** 2026-08-24. **Status:** approved, implementation pending.

**Goal:** Give the LLM judge the same prompt-entry representation the LLM
translator uses for every glossary term it receives: cleaned source and target,
source and target explanations, and effective flags. Route every judge context
producer through one matched-entry accessor so a request, a stored verdict, the
repair lock and the unit page cannot disagree about what the judge saw.

**Architecture:** Separate two contracts that the old wording conflated.
Glossary **selection** remains consumer-specific: the judge receives terms
matched against one string, while LLM machine translation may send a whole small
glossary or a deduplicated union for a batch. Glossary **entry serialization**
becomes neutral and shared in `weblate/glossary/models.py` through
`GlossaryPromptEntry`, `build_glossary_prompt_entry`,
`build_glossary_prompt_entries` and
`get_matched_glossary_prompt_entries`. Machinery supplies its already-selected
terms to the shared collection builder; every judge path uses the matched
accessor. `JudgeRequest.glossary_terms` carries mappings rather than tuples, and
`compute_context_hash` hashes the complete serialized entries.

**Tech Stack:** Django, `ahocorasick_rs` glossary matcher, pytest via
`./rundev.sh test`, Ruff/other hooks through `prek`, mypy.

---

## Why this change exists

Measured on production, 2026-08-24, unit 345773 `LevelModes_AttackDesc`
(`victory-banner/common/fr`):

```text
source: Захватите ключевые позиции врага
matched terms: 1
   'Захват' -> 'Assaut' modes=[]
judge sees pairs: 1
```

The translator was given the same matched term plus
`source_explanation: "Режим боя: захватить ключевые позиции врага. Название
игрового режима."` and the instruction to use explanations and flags to
disambiguate terms. It correctly wrote
`Capturez les positions clés de l'ennemi`. The judge was given only
`("Захват", "Assaut")`, treated the noun naming a game mode as a hard rule for
the derived imperative verb, and returned a `major` terminology error against a
correct translation.

Eight of the 18 `judge-flag` rows in that run are this one class. All 1913
glossary terms across the seven production projects had a non-empty explanation
at the time of the probe. The data needed for the decision already existed and
was discarded by `weblate/trans/judge_loop.py:_glossary_pairs`.

The codebase already enforces the neighbouring invariant that judge project
context comes from the same machinery configuration as translator persona and
style. This plan applies the same principle to the meaning of one glossary
entry, without pretending that the two consumers select the same list.

## Contract and deliberate asymmetry

The implementation must preserve this table:

| Concern | LLM machine translation | LLM judge |
| --- | --- | --- |
| Entry shape, cleanup, explanations and flags | Shared neutral builder | Shared neutral builder |
| Duplicate identical entries | Removed by shared collection builder | Removed by shared collection builder |
| Small glossary selection | May send the whole glossary below `LLM_FULL_GLOSSARY_LIMIT` | Matched terms only |
| Large glossary selection | Deduplicated union matched across a batch, `include_variants=False` | Matched per unit, preserving current `include_variants=True` behaviour |
| Context identity | Existing MT cache key remains owned by machinery | Full entry content is hashed by judge |

“Symmetry” in this plan means the first two rows. Changing selection policy
would alter prompt size, cache behaviour, judge cost and measured behaviour; it
is not required to fix this incident.

## Behavioural changes the judge receives

Routing matched judge terms through the shared entry builder intentionally
changes four things:

1. Entries gain `source_explanation`, `target_explanation` and `flags` when
   present.
2. A pair marked `not-applicable` for the target language is omitted.
3. A `read-only` entry carries `target == source` rather than a stored target.
4. Source and target pass through `cleanup_glossary_term`, matching LLM
   machinery handling of control characters, whitespace and prohibited leading
   characters.

The shared collection builder also preserves existing LLM machinery
deduplication of byte-equivalent prompt entries. Applying that same
deduplication to the judge is intentional: repeated identical JSON objects carry
no extra evidence and must not receive accidental extra weight.

## Out of scope

- Making judge and MT glossary **selection** identical.
- The `Захват` terminology record itself. Splitting the game-mode noun from the
  action is a producer decision; two stemmable entries with contradictory
  targets would still collide on the same Russian stem.
- Changing how glossary variants are selected. The judge keeps its current
  `include_variants=True` behaviour; MT keeps `include_variants=False` in its
  matched fallback.
- Hashing persona, style, prompt version, checks or other pre-existing judge
  context fields. This increment updates the glossary portion only.
- The judge hang and per-batch durability work tracked in
  `docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`.
- Any production read or paid model call. Task 6 requires separate approval.

---

## Task 0: Record a clean baseline

This task writes nothing. It prevents unrelated working-tree changes or
pre-existing failures from being attributed to this implementation.

### Step 1: Record the working tree

```bash
git status --short --branch
```

Keep the output with the execution notes. Do not edit, stage, restore or commit
paths that were already dirty and are not listed in this plan.

### Step 2: Run the focused baseline

```bash
./rundev.sh test weblate/glossary/tests.py \
  weblate/machinery/tests.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py -v
```

Expected: green, or a recorded pre-existing failure reproducible before any
edit. Do not start implementation with an unexplained red baseline.

---

## Task 1: Introduce one neutral glossary prompt-entry contract

The glossary layer already owns term cleanup, effective modes, matching and
selection. It will also own prompt serialization. Machinery will consume the
neutral functions instead of exporting its private assembler to lower layers.

**Files:**

- Modify: `weblate/glossary/models.py`
- Modify: `weblate/glossary/tests.py`
- Modify: `weblate/machinery/llm.py`
- Modify: `weblate/machinery/tests.py`
- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

### Step 1: Write the failing matched-entry test

Add this method to `GlossaryStemMatcherTest` in
`weblate/glossary/tests.py`. The term stays stemmable: `terminology` is
bookkeeping, not an exact-only mode.

```python
def test_matched_prompt_entries_carry_context(self) -> None:
    """The judge accessor returns the neutral prompt-entry contract."""
    from weblate.glossary.models import get_matched_glossary_prompt_entries

    id_hash = calculate_hash("Захват", "")
    source_unit = self.ru_glossary_component.source_translation.unit_set.create(
        source="Захват",
        target="Захват",
        context="",
        id_hash=id_hash,
        position=1,
        state=STATE_TRANSLATED,
        explanation="Название игрового режима.",
        extra_flags="terminology",
    )
    self.ru_glossary.unit_set.create(
        source="Захват",
        target="Assaut",
        context="",
        source_unit=source_unit,
        id_hash=id_hash,
        position=1,
        state=STATE_TRANSLATED,
        explanation="Nom français du mode.",
    )
    self.ru_glossary.invalidate_cache()

    unit = Unit(
        translation=self.ru_translation,
        id_hash=1,
        source="Захватите ключевые позиции врага",
        target="",
        context="",
        position=1,
        state=STATE_EMPTY,
    )

    self.assertEqual(
        get_matched_glossary_prompt_entries(unit),
        [
            {
                "source": "Захват",
                "target": "Assaut",
                "source_explanation": "Название игрового режима.",
                "target_explanation": "Nom français du mode.",
                "flags": ["terminology"],
            }
        ],
    )
```

The bare noun matching the imperative pins the production stem path rather than
an exact-only shortcut.

### Step 2: Run the new test and verify the missing contract

```bash
./rundev.sh test weblate/glossary/tests.py \
  -k matched_prompt_entries_carry_context -v
```

Expected: FAIL with an import error for
`get_matched_glossary_prompt_entries`.

### Step 3: Add the neutral type and builders

In `weblate/glossary/models.py`:

- import `json`;
- add `TypedDict` to the runtime typing import;
- keep `Iterable` in the existing `TYPE_CHECKING` block;
- add the following definitions next to `get_glossary_terms`.

```python
class GlossaryPromptEntry(TypedDict, total=False):
    source: str
    target: str
    source_explanation: str
    target_explanation: str
    flags: list[str]


GLOSSARY_PROMPT_FLAGS = ("read-only", "terminology", "exact", "forbidden")


def _normalize_prompt_text(text: str | None) -> str:
    return text.strip() if text is not None else ""


def build_glossary_prompt_entry(unit: Unit) -> GlossaryPromptEntry | None:
    """Serialize one glossary unit for an LLM prompt, or exclude it."""
    modes = get_glossary_term_modes(unit)
    if "not-applicable" in modes:
        return None

    forbidden = "forbidden" in modes
    if not forbidden and not unit.translated and "read-only" not in modes:
        return None

    source = cleanup_glossary_term(unit.source)
    target = source if "read-only" in modes else cleanup_glossary_term(unit.target)
    if not source or not target:
        return None

    entry: GlossaryPromptEntry = {"source": source, "target": target}
    source_unit = getattr(unit, "source_unit", None)
    if source_explanation := _normalize_prompt_text(
        getattr(source_unit, "explanation", "")
    ):
        entry["source_explanation"] = source_explanation
    if target_explanation := _normalize_prompt_text(
        getattr(unit, "explanation", "")
    ):
        entry["target_explanation"] = target_explanation

    effective_flags = set(modes)
    if "terminology" in unit.all_flags:
        effective_flags.add("terminology")
    flags = [flag for flag in GLOSSARY_PROMPT_FLAGS if flag in effective_flags]
    if flags:
        entry["flags"] = flags
    return entry


def build_glossary_prompt_entries(
    terms: Iterable[Unit],
) -> list[GlossaryPromptEntry]:
    """Serialize and deduplicate already-selected glossary units."""
    result: list[GlossaryPromptEntry] = []
    included: set[str] = set()
    for term in terms:
        entry = build_glossary_prompt_entry(term)
        if entry is None:
            continue
        cache_key = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if cache_key in included:
            continue
        included.add(cache_key)
        result.append(entry)
    return result


def get_matched_glossary_prompt_entries(unit: Unit) -> list[GlossaryPromptEntry]:
    """Return prompt entries matched against one unit for the judge."""
    return build_glossary_prompt_entries(
        get_glossary_terms(unit, full=True, include_variants=True)
    )
```

Do not import `weblate.machinery.llm` from the glossary layer. The dependency
direction must remain machinery → glossary.

### Step 4: Move the existing builder tests to the neutral API

In `weblate/machinery/tests.py`, import
`build_glossary_prompt_entry` and `build_glossary_prompt_entries` from
`weblate.glossary.models`.

Rewrite the existing exact/forbidden and not-applicable tests to call
`build_glossary_prompt_entry` rather than the private machinery method. Add:

```python
def test_glossary_prompt_entry_cleans_read_only_source(self) -> None:
    term = make_unit(code="fr", source="=Ship", target="Vaisseau")
    term.extra_flags = "read-only"

    self.assertEqual(
        build_glossary_prompt_entry(cast("Unit", term)),
        {"source": "Ship", "target": "Ship", "flags": ["read-only"]},
    )


def test_glossary_prompt_entries_deduplicate_identical_entries(self) -> None:
    term = cast(
        "Unit", make_unit(code="fr", source="Ship", target="Vaisseau")
    )

    self.assertEqual(
        build_glossary_prompt_entries([term, term]),
        [{"source": "Ship", "target": "Vaisseau"}],
    )
```

The existing tests continue to pin `exact`, `forbidden` and
`not-applicable`. The new tests pin the two previously untested corrections:
`read-only` and cleanup, plus collection deduplication.

### Step 5: Route LLM machinery through the neutral collection builder

In `weblate/machinery/llm.py`:

1. Import `GlossaryPromptEntry` and `build_glossary_prompt_entries` from
   `weblate.glossary.models`.
2. Delete the local `LLMGlossaryEntry` type and `LLM_GLOSSARY_FLAGS` constant.
3. Replace every `LLMGlossaryEntry` annotation with `GlossaryPromptEntry`.
4. Delete `_get_glossary_entry` and `_get_glossary_entries`.
5. Keep `_get_full_glossary` and the current selection policy unchanged.
6. Make `_get_batch_glossary` serialize each already-selected collection:

```python
def _get_batch_glossary(self, units: list[Unit]) -> list[GlossaryPromptEntry]:
    """Glossary sent with a batch, and the one its cache key must match."""
    if not units:
        return []
    full = self._get_full_glossary(units[0])
    if full is not None:
        return build_glossary_prompt_entries(full)
    missing = [
        unit for unit in units if getattr(unit, "glossary_terms", None) is None
    ]
    if missing:
        fetch_glossary_terms(missing, include_variants=False)
    return build_glossary_prompt_entries(
        chain.from_iterable(
            get_glossary_terms(unit, include_variants=False) for unit in units
        )
    )
```

Do not replace `_get_batch_glossary` with the judge accessor: doing so would
silently remove the full-small-glossary and batch-union behaviours.

### Step 6: Update the loc-kit contract call sites

In `weblate/trans/tests/test_loc_kit_ingest_contract.py`:

- replace the now-unused `BaseLLMTranslation` import with
  `build_glossary_prompt_entry` from `weblate.glossary.models`;
- update both tests at the former private-method call sites to call the neutral
  builder;
- remove their `private-member-access` ignores.

### Step 7: Run the owning suites

```bash
./rundev.sh test weblate/glossary/tests.py \
  weblate/machinery/tests.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py -v
```

Expected: green. Existing tests for the full-small-glossary and matched fallback
must still prove that selection policy did not change.

### Step 8: Commit

```bash
git add weblate/glossary/models.py weblate/glossary/tests.py \
  weblate/machinery/llm.py weblate/machinery/tests.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "refactor(glossary): share the LLM prompt entry contract"
```

---

## Task 2: Migrate the judge representation and context atomically

This is one atomic task and one commit. Changing `JudgeRequest` without changing
the hash and all context producers would leave an intermediate commit that
cannot write or read verdicts correctly.

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_round.py`

### Step 1: Migrate the shared client fixture before changing `_segment`

In `weblate/trans/tests/test_judge_client.py`, add
`from dataclasses import replace` and change the non-empty `REQ` fixture to the
new representation:

```python
glossary_terms=[
    {
        "source": "ГЕРМОДВЕРЬ",
        "target": "porte blindée",
        "source_explanation": "Бронированная герметичная дверь.",
        "flags": ["terminology"],
    }
],
```

Add:

```python
class SegmentGlossaryTest(SimpleTestCase):
    def test_segment_carries_the_complete_glossary_entry(self) -> None:
        # ruff: ignore[private-member-access]
        from weblate.trans.judge import _segment

        request = replace(REQ, glossary_terms=list(REQ.glossary_terms))

        self.assertEqual(
            _segment(0, request)["glossary"], list(request.glossary_terms)
        )
```

### Step 2: Add complete context-hash tests

Replace the tuple-based glossary assertions in
`weblate/trans/tests/test_judge.py` with tests that pin every field which can
steer the judge:

```python
def test_context_hash_reacts_to_glossary_and_note(self) -> None:
    base = compute_context_hash(source="Door", note="", glossary_terms=[])
    self.assertNotEqual(
        base, compute_context_hash(source="Door", note="hall", glossary_terms=[])
    )
    self.assertNotEqual(
        base,
        compute_context_hash(
            source="Door",
            note="",
            glossary_terms=[{"source": "Door", "target": "Porte"}],
        ),
    )


def test_context_hash_reacts_to_glossary_target(self) -> None:
    first = {"source": "Door", "target": "Porte"}
    second = {"source": "Door", "target": "Portail"}
    self.assertNotEqual(
        compute_context_hash(source="Door", note="", glossary_terms=[first]),
        compute_context_hash(source="Door", note="", glossary_terms=[second]),
    )


def test_context_hash_reacts_to_glossary_explanations_and_flags(self) -> None:
    plain = {"source": "Door", "target": "Porte"}
    variants = (
        {**plain, "source_explanation": "A game-mode name."},
        {**plain, "target_explanation": "Use on the battle screen."},
        {**plain, "flags": ["exact"]},
    )
    baseline = compute_context_hash(
        source="Door", note="", glossary_terms=[plain]
    )
    for entry in variants:
        with self.subTest(entry=entry):
            self.assertNotEqual(
                baseline,
                compute_context_hash(
                    source="Door", note="", glossary_terms=[entry]
                ),
            )


def test_context_hash_ignores_only_glossary_order(self) -> None:
    first = {"source": "a", "target": "b"}
    second = {"source": "c", "target": "d"}
    self.assertEqual(
        compute_context_hash(
            source="Door", note="", glossary_terms=[first, second]
        ),
        compute_context_hash(
            source="Door", note="", glossary_terms=[second, first]
        ),
    )
```

### Step 3: Add a glossary-aware round and view integration test

Do **not** add glossary data to `JudgeRoundTest.setUp`: its existing unparsed
test intentionally hashes an empty glossary. Add a separate class to
`weblate/trans/tests/test_judge_round.py`, importing `build_request`,
`calculate_hash` and `STATE_TRANSLATED` as needed:

```python
class JudgeGlossaryContextTest(ViewTestCase):
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]
        self.glossary = self.glossary_component.translation_set.get(
            language=self.translation.language
        )

    def add_term(self) -> None:
        id_hash = calculate_hash("Hello", "")
        source_unit = (
            self.glossary_component.source_translation.unit_set.create(
                source="Hello",
                target="Hello",
                context="",
                id_hash=id_hash,
                position=1,
                state=STATE_TRANSLATED,
                explanation="A greeting, not a character name.",
            )
        )
        self.glossary.unit_set.create(
            source="Hello",
            target="Ahoj",
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        self.glossary.invalidate_cache()

    def test_fresh_verdict_matches_round_and_view_context(self) -> None:
        self.add_term()
        unit = self.get_unit()
        unit.glossary_terms = None
        request = build_request(unit)
        context_hash = compute_context_hash(
            source=request.source,
            note=request.note,
            glossary_terms=request.glossary_terms,
        )
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            model_verdict="flag",
            judge_model="vendor/model-a",
            seat=1,
            run_id=uuid.uuid4(),
            target_hash=compute_target_hash(
                request.target_plurals or [request.target]
            ),
            context_hash=context_hash,
        )

        unit.glossary_terms = None
        self.assertEqual(len(current_round(unit)), 1)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "context changed")
```

This one integration test fails if `build_request`, `current_round` or the unit
view constructs a different glossary context.

### Step 4: Add a repair-lock regression test

Add a separate `CREATE_GLOSSARIES = True` class in
`weblate/trans/tests/test_judge_loop.py` under the same judge settings used by
`JudgeLoopTest`. Create a matching `Hello` → `Ahoj` term as above and retain its
source unit on `self.source_term`. The test must mutate only its explanation
after both seats answer but before `_apply_repair` takes its snapshot:

```python
def test_glossary_explanation_change_aborts_repair(self) -> None:
    unit = self.get_unit()
    original = unit.target
    client = mock.Mock(side_effect=[[MAJOR], [MAJOR]])

    def change_context(_unit, _user):
        self.source_term.explanation = "Changed while the judge was running."
        self.source_term.save(update_fields=["explanation"])
        return ["must not be applied"]

    with (
        mock.patch("weblate.trans.judge_loop.request_verdicts", client),
        mock.patch(
            "weblate.trans.judge_loop.repair_target", side_effect=change_context
        ),
    ):
        verdicts = run_judge_batch(
            [unit], writable_ids={unit.id}, user=self.user
        )

    self.assertNotIn(unit.id, verdicts)
    self.assertEqual(self.get_unit().target, original)
```

The expected empty result is the existing stale-snapshot contract: no verdict
may finalize or repair a unit whose judged context changed in flight.

### Step 5: Verify the new tests fail for the intended reasons

```bash
./rundev.sh test weblate/trans/tests/test_judge.py -k context_hash -v
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  -k complete_glossary_entry -v
./rundev.sh test weblate/trans/tests/test_judge_round.py \
  -k fresh_verdict_matches_round_and_view_context -v
./rundev.sh test weblate/trans/tests/test_judge_loop.py \
  -k glossary_explanation_change_aborts_repair -v
```

Expected before implementation:

- mapping entries are unpacked as tuples or raise;
- explanations and flags are not hashed;
- the fresh verdict has a different context in at least one consumer;
- the repair does not notice an explanation-only context change.

### Step 6: Change `JudgeRequest` and `_segment`

In `weblate/trans/judge.py`, add the type-only import:

```python
if TYPE_CHECKING:
    from collections.abc import Sequence

    from weblate.glossary.models import GlossaryPromptEntry
```

Change the field and renderer:

```python
glossary_terms: Sequence[GlossaryPromptEntry]
```

```python
if req.glossary_terms:
    segment["glossary"] = [dict(entry) for entry in req.glossary_terms]
```

Copy each mapping so the frozen request never exposes its mutable dictionary
objects directly to later payload construction.

### Step 7: Hash complete serialized mappings

In `weblate/trans/models/judge.py`, add `Mapping` to the existing
`collections.abc` type-only import and replace `compute_context_hash`:

```python
def compute_context_hash(
    *, source: str, note: str, glossary_terms: Iterable[Mapping[str, object]]
) -> str:
    """Hash source, note and every prompt-visible glossary-entry field."""
    terms = sorted(
        json.dumps(
            dict(entry),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for entry in glossary_terms
    )
    return _digest([source, note, *terms])
```

Sorting JSON keys makes mapping key order irrelevant; sorting serialized
entries makes glossary order irrelevant. Entry content and multiplicity remain
context. The shared collection builder has already removed identical duplicate
entries before this function receives production input.

### Step 8: Route every judge context producer through the matched accessor

In `weblate/trans/judge_loop.py`:

- import `get_matched_glossary_prompt_entries`;
- delete `_glossary_pairs` and the old `get_glossary_terms` import;
- use the matched accessor in `build_request`;
- use it again for the freshly locked unit in `_apply_repair`.

The two required call sites are:

```python
glossary_terms=get_matched_glossary_prompt_entries(unit),
```

and:

```python
glossary_terms=get_matched_glossary_prompt_entries(locked),
```

In `weblate/trans/models/judge.py`, keep the import local to avoid a trans-model
import cycle. Add a type-only import of `GlossaryPromptEntry` and replace the old
unit-list helper with:

```python
def _glossary_prompt_entries(unit: Unit) -> list[GlossaryPromptEntry]:
    from weblate.glossary.models import (  # ruff: ignore[import-outside-top-level]
        get_matched_glossary_prompt_entries,
    )

    return get_matched_glossary_prompt_entries(unit)
```

Use `_glossary_prompt_entries(unit)` in `current_round`.

In `weblate/trans/views/edit.py`, add
`get_matched_glossary_prompt_entries` to the existing glossary import and use it
for `judge_context_changed`. Keep `get_glossary_terms`: the same module still
uses it to render the glossary sidebar and other views.

### Step 9: Run the whole atomic migration suite

```bash
./rundev.sh test weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/checks/tests/test_judge.py -v
```

Expected: green. Running all of `test_judge_client.py` here is mandatory: most
of that file uses the shared non-empty `REQ` fixture, so a missed tuple migration
cannot hide behind a `-k` filter.

### Step 10: Commit

```bash
git add weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/judge.py weblate/trans/views/edit.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py
git commit -m "feat(judge): preserve complete glossary context"
```

---

## Task 3: Teach the judge the exact flag semantics

Sending fields without defining how they constrain a verdict leaves the model
to invent semantics. The prompt must distinguish concept scope, inflection,
exact/read-only constraints, forbidden wording and lifecycle metadata.

**Files:**

- Modify: `weblate/trans/judge_prompts/verdict.txt`
- Modify: `weblate/trans/tests/test_judge_client.py`

### Step 1: Write semantic prompt assertions

Add to `SegmentGlossaryTest` or a neighbouring prompt test class:

```python
def test_prompt_defines_glossary_context_and_modes(self) -> None:
    # ruff: ignore[private-member-access]
    from weblate.trans.judge import _load_prompt

    prompt = _load_prompt("ru", "fr")

    self.assertIn("`source_explanation`", prompt)
    self.assertIn("`target_explanation`", prompt)
    self.assertIn("flagged `read-only`", prompt)
    self.assertIn("flagged `exact`", prompt)
    self.assertIn("flagged `forbidden`", prompt)
    self.assertIn("maintenance metadata", prompt)
    self.assertIn("derived verb", prompt)
```

Do not assert only the substrings `explanation` or `flags`: both can occur in
unrelated prompt paragraphs without defining glossary behaviour.

### Step 2: Verify the semantic test fails

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py \
  -k prompt_defines_glossary_context_and_modes -v
```

Expected: FAIL on the first missing glossary-specific phrase.

### Step 3: Replace the glossary paragraph

In `weblate/trans/judge_prompts/verdict.txt`, preserve the file's trailing
backslash continuation style and replace the flat glossary mandate with:

```text
Each segment may carry `glossary`: source-matched terms of this project with \
their approved {target_language} rendering. They are reference material, not \
text to translate. A term rendered against an applicable approved entry is a \
`major` terminology error.

A glossary entry may carry `source_explanation`, `target_explanation` and \
`flags`. Use the explanations to decide whether the entry names the concept in \
this segment. An entry for a game mode, screen, unit or status constrains words \
that name that concept, not every grammatical form built from the same root. A \
derived verb, command or adjective may legitimately use another word when the \
explanation shows that the named concept is not being referenced.

A regular entry may be inflected when {target_language} grammar requires it. \
An entry flagged `exact` must use the approved target form without inflection \
or paraphrase. An entry flagged `read-only` has target equal to source and must \
remain that lexical form rather than being translated, inflected or \
paraphrased. The target of an entry flagged `forbidden` is wording that must \
not appear. `terminology` is maintenance metadata which keeps an entry present \
across glossary languages; it does not make the entry stricter than an \
otherwise regular term. When an explanation shows that an entry does not apply \
to this segment, report no terminology error for it.
```

This mirrors the effective code contract. `not-applicable` needs no prompt rule
because such entries are removed before serialization.

### Step 4: Run the full client suite

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py -v
```

Expected: green.

### Step 5: Commit

```bash
git add weblate/trans/judge_prompts/verdict.txt \
  weblate/trans/tests/test_judge_client.py
git commit -m "feat(judge): define glossary context semantics"
```

---

## Task 4: Document the shared entry contract accurately

**Files:**

- Modify: `docs/admin/checks.rst`
- Modify: `docs/guides/loc-kit-ingest.md`
- Inspect only: `docs/changes.rst`

`AGENTS.md` is not part of this task: it contains no user-facing judge
description that needs the new contract.

### Step 1: Extend the judge documentation

In `docs/admin/checks.rst`, after the paragraph describing the two seats, add:

```rst
Both seats receive the glossary entries matched against each string, including
their source and target explanations and effective flags. Each entry uses the
same cleanup, filtering and serialization contract as LLM-based automatic
suggestion services. Selection remains consumer-specific: the judge uses
per-string matches, while an automatic suggestion service can use a full small
glossary or a batch-wide union.

Explanations scope an entry to the concept it names. For example, an entry that
names a game mode does not require the same target word for a verb derived from
that mode name. The ``exact``, ``read-only`` and ``forbidden`` flags retain their
glossary meanings. See :ref:`glossary` for maintaining terms and explanations.
```

Verify the reference rather than assuming it:

```bash
rg -n '^\.\. _glossary:' docs/
```

Expected: `docs/user/glossary.rst` defines the label.

### Step 2: Update the living loc-kit guide after the builder move

In `docs/guides/loc-kit-ingest.md`, replace the private machinery reference in
the TBX explanation paragraph with
`weblate.glossary.models.build_glossary_prompt_entry`. Do not rewrite dated
historical plans or archived designs that accurately name the API available at
the time they were written.

### Step 3: Confirm the no-changelog decision

```bash
rg -n "LLM judge|llm-judge" docs/changes.rst | head -5
```

Expected: the judge remains in the unreleased `2026.8.1` section. In that case,
no new changelog entry is needed because this fixes an unreleased feature. If
the entry has moved to a released section by implementation time, add a concise
bug-fix entry to the current unreleased section and include `docs/changes.rst`
in the commit.

### Step 4: Run documentation checks

```bash
uv run prek run rst-http rumdl rumdl-fmt end-of-file-fixer \
  trailing-whitespace --files docs/admin/checks.rst \
  docs/guides/loc-kit-ingest.md
```

Expected: Passed. Inspect `git status` immediately afterwards. Hooks must not
cause unrelated user files to be staged, restored or committed.

### Step 5: Commit

```bash
git add docs/admin/checks.rst docs/guides/loc-kit-ingest.md
git commit -m "docs(judge): describe glossary entry context"
```

Add `docs/changes.rst` only if Step 3 determined that it is required.

---

## Task 5: Full verification, status update and push

Use `verification-before-completion`; fresh command output is required before
claiming completion.

### Step 1: Run every owning suite once from the final tree

```bash
./rundev.sh test weblate/glossary/tests.py \
  weblate/machinery/tests.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/checks/tests/test_judge.py -v
```

Expected: green. If mass setup errors vary between identical runs, inspect
`docker stats --no-stream` before changing code; the Docker memory ceiling is a
known environmental failure mode.

### Step 2: Run focused lint and formatting

```bash
uv run prek run ruff-check ruff-format rst-http rumdl rumdl-fmt \
  end-of-file-fixer trailing-whitespace --files \
  weblate/glossary/models.py weblate/glossary/tests.py \
  weblate/machinery/llm.py weblate/machinery/tests.py \
  weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/judge.py weblate/trans/views/edit.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  docs/admin/checks.rst docs/guides/loc-kit-ingest.md
```

Expected: Passed. Compare `git status` with Task 0; do not discard or stage
unrelated modifications if a hook scans more than the listed files.

### Step 3: Type-check the touched production modules

```bash
uv run mypy --show-column-numbers \
  weblate/glossary/models.py weblate/machinery/llm.py \
  weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/judge.py weblate/trans/views/edit.py \
  | ./scripts/filter-mypy.sh
```

Expected: no new findings relative to the Task 0 baseline. In particular there
must be no undefined `GlossaryPromptEntry` annotation and no machinery/glossary
import cycle.

### Step 4: Verify the architectural boundaries statically

```bash
rg -n "_get_glossary_entry|LLMGlossaryEntry|LLM_GLOSSARY_FLAGS" \
  weblate/ docs/guides/
rg -n "term\.source, term\.target" \
  weblate/trans/judge_loop.py weblate/trans/models/judge.py \
  weblate/trans/views/edit.py
rg -n "get_matched_glossary_prompt_entries" \
  weblate/trans/judge_loop.py weblate/trans/models/judge.py \
  weblate/trans/views/edit.py
rg -n "build_glossary_prompt_entries" \
  weblate/glossary/models.py weblate/machinery/llm.py
```

Expected:

1. The first two commands print nothing.
2. The matched accessor appears in `build_request`, `_apply_repair`, the
   `current_round` helper and the unit-view context path.
3. The collection builder appears in the glossary definition and both MT
   selection branches.

Historical dated plans are deliberately outside the first search; they are not
living API documentation.

### Step 5: Review the final diff and update this plan's status

```bash
git status --short
git diff --check
git diff --stat
git log --oneline -5
```

Confirm that every implementation path is listed in Tasks 1-4 and that no
unrelated user change entered any commit. Change this document's status to
`implemented and verified`, add a short verification note with the exact test
commands/results, and commit only that status update:

```bash
git add docs/llm-first/plans/2026-08-24-judge-glossary-symmetry.md
git commit -m "docs(judge): record glossary symmetry verification"
```

### Step 6: Push

```bash
git push
```

Pushing is not deployment. Do not run `deploy/vps.sh`, production management
commands, `weblate shell` against production, or rebuild/restart a shared
running stack as part of this plan.

---

## Task 6: Paired production-derived measurement — separate approval required

Do **not** start this task with Tasks 0-5. It reads production data and makes
paid provider requests, so it needs a separate approval that includes the
estimated request count and cost.

### Why the existing golden harness cannot answer this question

`analysis/probes/col4-judge-eval.py` reads a flat source/target glossary mapping
from `dev-docker/data/col4-b0-units.jsonl`; the dump contains no explanations or
flags. Running the new prompt against that data would not exercise the changed
contract.

### Step 1: Freeze the evaluation set read-only

On separate approval, snapshot into a dev-only artifact:

- the 20 production units currently carrying `judge-flag` or `judge-reject`,
  including the eight `Захват` false positives;
- at least 20 human-confirmed clean controls from the same component and target
  language, split between strings with and without matched glossary entries;
- known true-defect controls, including `RadarDirections` and
  `Unit_DriverVermaht` after human adjudication;
- source, target, note, matched entries with explanations/flags, model IDs,
  seat order, reasoning settings and provider route.

Do not modify production verdicts or unit state.

### Step 2: Estimate and approve cost before calling the provider

For `N` frozen units and `R` repetitions per arm, the initial-call count is:

```text
2 arms × R × 2 seats × ceil(N / JUDGE_BATCH_SIZE)
```

Use at least three repetitions. Estimate cost from recent matching
`LLMUsageLog` rows, state the worst-case total, and obtain approval for that
number. Repairs are disabled for the measurement.

### Step 3: Run paired arms without verdict-cache reuse

Run pre-change and post-change code against identical frozen inputs. Call the
judge client directly or clear only the dev measurement verdicts between arms;
do not let `_cached_verdict` skip a paid arm. Alternate arm order between
repetitions.

Keep constant:

- source and target text;
- glossary snapshot;
- seat model IDs and seat order;
- batch size, reasoning effort and provider route;
- project persona and style.

Record `cached_tokens` per request. If one arm systematically receives prompt
cache hits and the other does not, the timing/cost comparison is invalid; rerun
with alternating order or report quality only. Never describe asymmetric cache
hits as a quality improvement.

### Step 4: Decide from repeated, adjudicated outcomes

Accept the behavioural change only if:

1. Every one of the eight adjudicated `Захват` false positives passes in the
   majority of post-change repetitions and improves relative to its paired
   pre-change result.
2. Known true terminology defects remain negative in the majority of runs, or
   any changed result has a documented human reason.
3. No clean control becomes newly negative in two or more of three repetitions.
4. Aggregate new-negative frequency on clean controls does not increase.

Reject or reopen the prompt if a clean control gains a repeatable error, a true
defect is consistently suppressed, or results follow cache/arm order rather
than glossary context.

### Step 5: Record the measurement

Write the frozen set description, exact configuration, per-repetition seat
results, collegium result, cache tokens, request count and cost to:

```text
docs/llm-first/measurements/2026-08-24-judge-glossary-symmetry-run.md
```

Commit and push the measurement separately. It is evidence about the
implementation, not part of Tasks 0-5 and not permission to deploy it.

---

## Completion criteria

Tasks 0-5 are complete only when all of the following hold:

1. `weblate/glossary/models.py` is the only owner of glossary prompt-entry
   shape, cleanup, filtering, flag order and entry deduplication.
2. LLM machinery preserves its existing full-small-glossary and batch-union
   selection while using the shared serializer.
3. `JudgeRequest`, `_segment`, `build_request`, `_apply_repair`,
   `current_round` and the unit page use the mapping representation consistently.
4. `compute_context_hash` changes for target, either explanation and flags, and
   remains independent of glossary and mapping-key order.
5. The prompt explains regular inflection, `exact`, `read-only`, `forbidden`,
   `terminology` and explanation-based concept scope.
6. Existing stored verdicts remain visible through `active_round` by target
   hash. Their old context hashes show the existing “context changed” notice;
   no migration rewrites historical evidence.
7. Every focused suite, lint command, type check and static boundary check in
   Task 5 has fresh passing evidence.
8. No production command or paid measurement has run without the separate Task
   6 approval.
