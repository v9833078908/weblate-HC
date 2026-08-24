# Glossary note column in profile inference - implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A flat glossary table with `ru,en,tr,fr,note` imports through Weblate's **Use as glossary** flow without a handwritten profile; its note becomes the source term Explanation and reaches OpenRouter as `source_explanation`.

**Architecture:** Extend only `loc_kit_ingest.infer.infer_glossary_profile`. An exactly named, populated source-note column is emitted as `grammar.notes` with `scope: source` and `row_offset: 0`. Existing profile validation, TBX rendering, component creation, glossary storage, preview, and LLM payload construction remain the only downstream path.

**Tech Stack:** Python, standalone `loc_kit_ingest`, Django `ViewTestCase`, TBX, pytest.

**Design:** `docs/product/designs/2026-08-10-loc-kit-glossary-note-column-design.md`.

**Status (2026-08-15): implemented and verified.** Flat and pairs note-column
inference, source explanations, preview behavior, and the LLM payload contract
are covered by the standalone and Weblate tests.

## Scope locked by review

- **UI only.** This changes the existing Weblate `Use as glossary` workflow. Do not add a CLI `--glossary` mode or change generic PO inference.
- **Exact header matching only.** Use `header.strip().casefold()` against the closed `_NOTE_HEADERS` set. Do not infer a note column from prose length.
- **One populated source-note column.** Warn for every recognized empty column; refuse when two or more recognized columns contain data. Its populated cells must occur only on term rows; do not merge columns or silently skip an unattached note.
- **`context` and `usage` remain accepted.** The operator explicitly requested them. Preview exposes the imported text before component creation; do not silently remove them from the approved set.
- **No schema, parser, writer, UI, migration, API, or outbound-provider changes.** A need for any of these is a design regression.
- **No live OpenRouter assertion.** Verify the deterministic `BaseLLMTranslation._get_glossary_entry` contract instead.

## What already exists

| Existing layer | Reuse |
|---|---|
| `infer_glossary_profile` | Detects language columns, flat/pairs layout, regions, and unmapped data. Add the note-column rule here. |
| Profile v2 record-map | Already accepts `notes[]` with `scope`, `column`, `header`, and `row_offset`. |
| `parse_component` | Reads source notes, joins them in declaration order, validates unclaimed cells, and produces `GlossaryTerm.source_explanation`. |
| `writer._render_tbx` | Writes source explanations to TBX `<descrip>`. |
| `validate_glossary_profile` | Executes closed-schema validation, full parse, render, and parse-back before a preview or component exists. |
| Glossary preview | Already renders `Source note` and `note_count`. |
| `BaseLLMTranslation._get_glossary_entry` | Already maps source-unit Explanation to the `source_explanation` payload field. |

## Failure modes and guards

| Failure | Guard | Evidence |
|---|---|---|
| Extra metadata is mistaken for a note | Closed exact header set; preview shows resulting text | Unit test for unknown column and wizard preview |
| Old empty `note` column blocks a real `comment` column | Empty recognized columns warn and do not compete | Unit test with empty + populated columns |
| Two populated note columns are ambiguous | Deterministic refusal and existing manual-profile recovery | Unit test |
| Note cell is outside a term row | Reject it before profile publication, including captions, descriptions, and source-less skipped rows | Flat and pairs inference tests |
| Explanation does not reach LLM context | Inspect actual created French unit via `_get_glossary_entry` | Wizard contract test |
| Wide sheet amplifies work | Reuse existing one-pass `populated` column set | Code review and inference tests |

## NOT in scope

- Target-language note columns such as `fr note` to `<note from="translator">`: the schema supports them, but this request is a single source explanation column.
- Prefix/fuzzy headers such as `Комментарий переводчику`: they can collide with metadata and stay a manual-profile case.
- Header-shape/prose-length detection: it risks routing `Character limit` into every LLM request.
- Glossary flags (`terminology`, `read-only`, `forbidden`) from a table: the writer does not emit them.
- Updating an existing glossary: tracked separately in `docs/product/plans/2026-08-10-loc-kit-glossary-update-existing.md`.
- Automatic glossary inference in the standalone CLI: the accepted scope is the Weblate wizard only.

---

### Task 1: emit a flat source-note column with linear detection

**Files:**

- Modify: `loc_kit_ingest/infer.py:41-53, 462-570, 677-715`
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: write failing public-inference tests** - append these fixtures and tests:

```python
FLAT_WITH_NOTE = [
    ["ru", "en", "tr", "fr", "note"],
    ["Russian", "English", "Turkish", "French", "Note"],
    ["Партия", "Party", "Parti", "Parti", "Правящая политическая партия."],
    ["Самосбор", "Samosbor", "Samosbor", "Samosbor", "Термин вселенной."],
]


def test_note_column_becomes_a_source_note_field() -> None:
    document, notes = infer_glossary_profile("S", FLAT_WITH_NOTE, component="s")
    (comp,) = document["components"]
    assert [lang["code"] for lang in comp["languages"]] == ["ru", "en", "tr", "fr"]
    assert comp["grammar"]["notes"] == [
        {"scope": "source", "column": 5, "header": "note", "row_offset": 0}
    ]
    assert any("explanation of the source term" in note for note in notes)
    parse_profile(document)


@pytest.mark.parametrize("header", ["Notes", "ОПИСАНИЕ", "Comment", " context "])
def test_note_header_matching_ignores_case_and_padding(header: str) -> None:
    rows = [row[:] for row in FLAT_WITH_NOTE]
    rows[0][4] = header
    document, _notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["notes"][0]["column"] == 5


def test_empty_recognised_note_column_does_not_compete() -> None:
    rows = [
        ["ru", "en", "note", "comment"],
        ["Партия", "Party", "", "Правящая политическая партия."],
        ["Самосбор", "Samosbor", "", "Термин вселенной."],
    ]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["notes"][0]["column"] == 4
    assert any("column 3" in note and "empty" in note for note in notes)


def test_two_populated_note_columns_are_refused() -> None:
    rows = [
        ["ru", "en", "note", "comment"],
        ["Партия", "Party", "Проза", "Ещё проза"],
    ]
    with pytest.raises(InferenceError, match="columns 3, 4 all look like term notes"):
        infer_glossary_profile("S", rows, component="s")


def test_empty_note_column_is_excluded_with_a_warning() -> None:
    rows = [["ru", "en", "note"], ["Партия", "Party", ""]]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert "notes" not in comp["grammar"]
    assert any("column 3" in note and "empty" in note for note in notes)


def test_flat_note_on_a_source_less_row_is_refused() -> None:
    rows = [
        ["ru", "en", "note"],
        ["Партия", "Party", "Правящая политическая партия."],
        ["", "", "не привязанная к термину заметка"],
    ]
    with pytest.raises(InferenceError, match="non-term row"):
        infer_glossary_profile("S", rows, component="s")


def test_unrecognised_extra_column_has_actionable_error() -> None:
    rows = [["ru", "en", "Character limit"], ["Партия", "Party", "40"]]
    with pytest.raises(InferenceError, match="recognised term-note header, for example"):
        infer_glossary_profile("S", rows, component="s")
```

**Step 2: verify red**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k 'note_column or note_header or recognised_note or populated_note or unrecognised_extra' -q`

Expected: FAIL because a populated `note` column is currently rejected as a non-language column.

**Step 3: add the approved header set** - after `_KEY_HEADER_DENYLIST`:

```python
# A column of prose about the term, not a translation of it. Recognised by
# header text alone: a length rule would accept "Character limit" and route it
# into every LLM prompt, where a wrong guess is invisible.
_NOTE_HEADERS = frozenset(
    {
        "note", "notes", "comment", "comments", "description", "descriptions",
        "explanation", "explanations", "context", "usage", "definition", "meaning",
        "примечание", "примечания", "комментарий", "комментарии",
        "описание", "описания", "пояснение", "пояснения", "контекст",
        "определение", "значение",
    }
)
```

**Step 4: add the classifier** - before `infer_glossary_profile`:

```python
def _find_note_column(
    header_row: list[str],
    populated: set[int],
    languages: dict[int, str],
    notes: list[str],
) -> int | None:
    """Return the sole populated source-note column, if one exists."""
    recognised = [
        col
        for col in range(len(header_row))
        if col not in languages
        and _cell(header_row, col).strip().casefold() in _NOTE_HEADERS
    ]
    populated_notes = [col for col in recognised if col in populated]
    for col in recognised:
        if col not in populated:
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r}) is empty; excluded"
            )
    if len(populated_notes) > 1:
        shown = ", ".join(str(col + 1) for col in populated_notes)
        msg = (
            f"columns {shown} all look like term notes; this layout needs an "
            "explicit profile"
        )
        raise InferenceError(msg)
    if not populated_notes:
        return None
    col = populated_notes[0]
    notes.append(
        f"column {col + 1} ({_cell(header_row, col)!r}) -> explanation of the source term"
    )
    return col


def _reject_note_outside_term_rows(
    rows: list[list[str]],
    note_col: int,
    content_indexes: list[int],
    term_rows: list[int],
) -> None:
    """Refuse note text that no generated record-map field can read."""
    term_row_set = set(term_rows)
    offenders = [
        index + 1
        for index in content_indexes
        if index not in term_row_set and _cell(rows[index], note_col).strip()
    ][:_MISSING_ROWS_SHOWN]
    if offenders:
        shown = ", ".join(str(row) for row in offenders)
        msg = (
            f"column {note_col + 1} holds text on non-term row(s) {shown}; "
            "this layout needs an explicit profile"
        )
        raise InferenceError(msg)
```

**Step 5: call it without a rescan.** Preserve the existing one-pass `populated` loop at `infer.py:555-559`. Immediately after it, add:

```python
    note_col = _find_note_column(header_row, populated, languages, notes)
    mapped = set(languages)
    if note_col is not None:
        mapped.add(note_col)
    unmapped = sorted(populated - mapped)
```

Replace the old `unmapped = sorted(populated - languages.keys())` line. The error should say:

```python
        msg = (
            f"column {col + 1} ({header_text!r}) holds data but is not a "
            "recognised language column; rename the header to a recognised "
            "term-note header, for example note, description, comment, or "
            "explanation, or supply an explicit profile"
        )
```

**Step 6: reject an unattached flat note, then emit the grammar.** The generated
profile treats source-less rows as `skip_rows`; the record-map parser deliberately
does not inspect their cells. Do not let a note on such a row be presented as
imported and then discarded. After `term_rows` is complete and before building
`grammar`, add:

```python
    if note_col is not None and not paired:
        _reject_note_outside_term_rows(rows, note_col, content_indexes, term_rows)
```

This is deliberately a temporary flat-only shape: leave current pairs behavior
untouched in Task 1, then replace both branches with the unified path in Task 2.
After the existing `if paired: grammar["notes"] = ...` block, add:

```python
    elif note_col is not None:
        grammar["notes"] = [
            {
                "scope": "source",
                "column": note_col + 1,
                "header": _cell(header_row, note_col),
                "row_offset": 0,
            }
        ]
```

**Step 7: verify green**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`

Expected: PASS. Every Task 1 test is green; do not leave an expected red assertion for a later task.

**Step 8: commit**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): infer a flat glossary note column"
```

---

### Task 2: support the note column in explicit pairs layout

**Files:**

- Modify: `loc_kit_ingest/infer.py:630-715`
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: write failing pairs tests.** Use the existing explicit layout override, not auto-detection heuristics:

```python
PAIRS_WITH_NOTE = [
    ["ru", "en", "note"],
    ["Персонажи", "Characters", ""],
    ["Партия", "Party", "Мужской род во французском."],
    [
        "Правящая политическая партия страны, а не партия товара; "
        "не обозначает набор одинаковых предметов.",
        "The ruling party of the country, not a batch of goods.",
        "",
    ],
    ["Самосбор", "Samosbor", "Транслитерируется."],
    [
        "Аномальное явление, разрушающее материю вокруг себя и меняющее "
        "поведение персонажей поблизости.",
        "An anomaly that dissolves the matter around it.",
        "",
    ],
]


def test_explicit_pairs_layout_orders_description_before_note_column() -> None:
    document, _notes = infer_glossary_profile(
        "S", PAIRS_WITH_NOTE, component="s", layout="pairs"
    )
    (comp,) = document["components"]
    assert comp["grammar"]["regions"][0]["record_stride"] == 2
    scopes = [(n["scope"], n["column"], n["row_offset"]) for n in comp["grammar"]["notes"]]
    assert scopes[0] == ("source", 1, 1)
    assert scopes[-1] == ("source", 3, 0)
    parse_profile(document)


def test_automatic_pairs_layout_maps_the_note_column() -> None:
    document, _notes = infer_glossary_profile("S", PAIRS_WITH_NOTE, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["regions"][0]["record_stride"] == 2
    assert comp["grammar"]["notes"][-1] == {
        "scope": "source",
        "column": 3,
        "header": "note",
        "row_offset": 0,
    }


def test_pairs_rejects_note_on_a_source_less_row() -> None:
    rows = [*PAIRS_WITH_NOTE, ["", "", "не привязанная к термину заметка"]]
    with pytest.raises(InferenceError, match="non-term row"):
        infer_glossary_profile("S", rows, component="s", layout="pairs")


@pytest.mark.parametrize("row", [1, 3])
def test_explicit_pairs_rejects_note_outside_term_rows(row: int) -> None:
    rows = [item[:] for item in PAIRS_WITH_NOTE]
    rows[row][2] = "необъявленная заметка"
    with pytest.raises(InferenceError, match="non-term row"):
        infer_glossary_profile("S", rows, component="s", layout="pairs")
```

Row 1 is the section caption; row 3 is the first description. Both would otherwise survive profile generation and then fail the parser as an unmapped cell. The separate pairs test covers a source-less row that would otherwise enter `skip_rows` and silently lose its note.

**Step 2: verify red**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k 'pairs_layout or pairs_rejects' -q`

Expected: FAIL because Task 1 emits a note column only for flat grammar.

**Step 3: replace the Task 1 flat-only guard and tail with one ordered list.** Keep the existing pairs term-description validation and collect its fields into `note_fields`. Reuse `_reject_note_outside_term_rows` after `term_rows` is complete: it considers every nonblank post-header row that is not a term row, so captions, description rows, and source-less rows destined for `skip_rows` are covered by one rule.

Delete both Task 1's `if note_col is not None and not paired` guard and its flat-only `elif`. Start an empty `note_fields` list, populate it with the existing pairs fields, then append the source note column after the shared guard:

```python
    note_fields: list[dict[str, Any]] = []
    if paired:
        # Keep the existing validation that every described language is an
        # initial target, then preserve its current source/target fields.
        note_fields.extend(
            {
                "scope": "source" if col == source_col else "target",
                "column": col + 1,
                "header": _cell(header_row, col),
                "row_offset": 1,
            }
            | ({} if col == source_col else {"language": languages[col]})
            for col in sorted(languages)
            if col == source_col or languages[col] in target_langs
        )
    if note_col is not None:
        _reject_note_outside_term_rows(rows, note_col, content_indexes, term_rows)
        note_fields.append(
            {
                "scope": "source",
                "column": note_col + 1,
                "header": _cell(header_row, note_col),
                "row_offset": 0,
            }
        )
    if note_fields:
        grammar["notes"] = note_fields
```

The current pairs fields must precede this append: `_join_notes` preserves declaration order.

The new automatic-layout test is required: the wizard first calls
`infer_glossary_profile(..., layout="auto")`, so an explicit-layout test alone
cannot protect its normal path.

**Step 4: verify green**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`

Expected: PASS.

**Step 5: commit**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): support note columns in pairs glossaries"
```

---

### Task 3: pin the complete Weblate wizard and LLM-context contract

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: add the CSV fixture** near `GLOSSARY_LANG_ONLY_CSV`:

```python
GLOSSARY_NOTE_CSV = (
    "ru,en,fr,note\n"
    "Russian,English,French,Note\n"
    "Партия,Party,Parti,"
    '"Правящая политическая партия. Во французском le Parti, мужской род."\n'
    "Самосбор,Samosbor,Samosbor,Термин вселенной.\n"
)
```

**Step 2: add the contract test** to `LocKitGlossaryUploadUITest`, beside `test_term_description_sheet_maps_descriptions_as_explanations`:

```python
    @override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
    def test_note_column_reaches_the_created_glossary_llm_entry(self) -> None:
        self._start(upload=self._csv("Terms.csv", GLOSSARY_NOTE_CSV), slug=self.slug)
        draft = self._draft()
        draft.refresh_from_db()

        self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
        preview = json.loads(draft.preview_json)
        self.assertEqual(preview["term_count"], 2)
        self.assertEqual(preview["note_count"], 2)
        self.assertIn("мужской род", preview["terms"][0]["source_explanation"])

        page = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertContains(page, "мужской род")

        self._confirm()
        component = Component.objects.get(slug=self.slug)
        translation = component.translation_set.get(language__code="fr")
        unit = translation.unit_set.get(source="Партия")
        # ruff: ignore[private-member-access]
        entry = BaseLLMTranslation._get_glossary_entry(unit)
        self.assertIsNotNone(entry)
        self.assertEqual(
            entry["source_explanation"],
            "Правящая политическая партия. Во французском le Parti, мужской род.",
        )
```

This intentionally verifies the deterministic contract. Do not call OpenRouter, assert a model output, or attempt to observe a server-to-provider request from the browser.

**Step 3: copy the standalone package for the container test only**

```bash
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
```

The destination is ignored and must never appear in `git add`.

**Step 4: verify the focused test**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUploadUITest::test_note_column_reaches_the_created_glossary_llm_entry -n0
```

Expected: PASS.

**Step 5: verify the contract file**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0`

Expected: PASS. `GLOSSARY_CSV` (`domain,ru,en,note_ru,note_en`) must still remain at `SHEET_SELECTED`: `domain` and `note_ru`/`note_en` are not accepted source-note headers.

**Step 6: commit**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "test(loc-kit): cover note columns through the glossary wizard"
```

---

### Task 4: document the supported table shape

**Files:**

- Modify: `docs/admin/projects.rst:117-140`
- Modify: `docs/guides/loc-kit-ingest.md:100-125, 271-278`
- Modify: `docs/changes.rst` under Weblate 2026.8.1 / Improvements

**Step 1: public documentation.** Add one bullet after the layout bullet in `docs/admin/projects.rst`:

```rst
* A single column of source-term notes can be named ``note``. Exact aliases
  such as ``description``, ``comment``, ``explanation``, and ``context`` are
  also recognised. Its text becomes the glossary term explanation; preview it
  before confirming because automatic suggestion services receive it as term
  context.
```

**Step 2: specification.** Replace the claim that every non-language column is refused. Document the complete closed `_NOTE_HEADERS` list, exact casefolded matching, one populated column, warning-and-ignore behavior for empty recognized columns, `scope: source`, `row_offset: 0`, and deterministic refusal for any other populated metadata column. Also correct the later statement that v2 `record-map` is never inferred from a header: it is now inferred locally for the documented simple layouts, while the manual-profile and OpenRouter paths remain fallbacks for every other shape.

**Step 3: changelog.** Add:

```rst
* Glossary table import now recognises a column of term notes by its header and imports it as the source term explanation, which automatic suggestion services receive as context, see :ref:`uploading-glossary-tables`.
```

**Step 4: commit**

```bash
git add docs/admin/projects.rst docs/guides/loc-kit-ingest.md docs/changes.rst
git commit -m "docs(loc-kit): document glossary note columns"
```

---

### Task 5: full verification and browser smoke

**Step 1: standalone and Weblate contract suites**

Run from the repository root:

```bash
(cd loc_kit_ingest && uv run pytest) && \
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/ && \
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
```

**Step 2: lint changed paths**

```bash
uv run prek run --files \
  loc_kit_ingest/infer.py \
  loc_kit_ingest/tests/test_infer_glossary.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  docs/admin/projects.rst \
  docs/guides/loc-kit-ingest.md \
  docs/changes.rst
```

**Step 4: live smoke through actual controls.** Do not submit the form programmatically.

1. Open the component-create flow in the local instance and upload `GLOSSARY_NOTE_CSV`.
2. Check **Use as glossary** and click the real continue control.
3. Confirm preview reports two terms, two notes, displays `мужской род` in **Source note**, and lists the note-column inference note.
4. Click the real confirmation control. Open the new glossary term `Партия` and confirm its Explanation is populated.

Do not invoke automatic translation in this smoke: Task 3 already proves the exact payload seam deterministically.

**Step 5: commit only if verification fixes require one**

```bash
git add -- loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py weblate/trans/tests/test_loc_kit_ingest_contract.py docs/admin/projects.rst docs/guides/loc-kit-ingest.md docs/changes.rst
git commit -m "fix(loc-kit): close note-column verification defects"
```

## Implementation tasks from engineering review

- [x] **T1 (P1)** - `loc_kit_ingest/infer.py` - classify flat note columns, reject note text outside its term row, and emit a valid grammar field in the same red-green task.
  - Verify: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`
- [x] **T2 (P1)** - `loc_kit_ingest/infer.py` - apply the shared non-term-row guard to pairs and preserve the declaration order of descriptions and the note column.
  - Verify: automatic and explicit-pairs tests.
- [x] **T3 (P1)** - `weblate/trans/tests/test_loc_kit_ingest_contract.py` - assert the actual created French glossary unit exposes CSV text through the LLM glossary-entry contract.
  - Verify: exact `LocKitGlossaryUploadUITest` node.
- [x] **T4 (P2)** - `docs/admin/projects.rst`, `docs/guides/loc-kit-ingest.md`, and `docs/changes.rst` - document the user-facing note column and remove the stale claim that v2 `record-map` is never inferred locally.
  - Verify: docs source review and configured lint.
- [x] **T5 (P2)** - `loc_kit_ingest/infer.py` - classify empty/populated note headers from the one-pass populated set.
  - Verify: empty-plus-populated and duplicate-populated tests.

## Worktree parallelization

Sequential implementation, no parallelization opportunity. Tasks 1 and 2 both alter the same inference function; Task 3 depends on its final behavior. Documentation can be edited after Task 2, but keeping it with the verification pass avoids a merge conflict for negligible benefit.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | - |
| Codex Review | `/codex review` | Independent second opinion | 2 | issues folded | Source-less-row guard, automatic-pairs coverage, and record-map documentation consistency |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | CLEAR | 8 accepted decisions: UI scope, TDD, pairs, LLM proof, commands, linear scan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | - |

**CROSS-MODEL:** Both reviews required public documentation and wording alignment. The outside voice flagged `context`/`usage` false-positive risk; the operator explicitly retained them, with preview as the accepted guardrail.

**VERDICT:** READY TO IMPLEMENT. The current review's source-less-row guard and automatic-pairs coverage are folded into Tasks 1 and 2; documentation consistency is folded into Task 4.

NO UNRESOLVED DECISIONS
