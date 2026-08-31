# Loc-kit glossary source flags implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Import source-scoped `read-only` and `forbidden` glossary flags from a deterministic `flags` column during glossary creation and append-only updates.

**Architecture:** Profile v2 gains one typed `source_flags` record field. The standalone parser normalizes its comma-separated closed vocabulary into `GlossaryTerm.source_flags`; the TBX writer persists it in `termEntry@weblate-flags`, and the Weblate preview and append service consume the same validated model.

**Tech Stack:** Python dataclasses, loc-kit record-map schema, translate-toolkit TBX, Django views/models/templates, pytest.

**Design:** `docs/product/designs/2026-08-31-02-loc-kit-glossary-source-flags.md`.

**Status (2026-08-31): implemented and verified at `721e058`.** The standalone
loc-kit suite passes. The two focused Weblate loc-kit files passed 135 tests;
three unrelated VCS fixture failures from that run passed when rerun alone.

---

## Task 1: Add the source flag field to profile v2

**Files:**

- Modify: `loc_kit_ingest/profile.py`
- Test: `loc_kit_ingest/tests/test_profile_v2.py`

### Step 1: Write failing profile tests

Cover a valid field:

```json
"source_flags": {"column": 4, "header": "flags", "row_offset": 0}
```

Also cover wrong types, missing keys, an offset outside `record_stride`, and a cell collision with a language, section, or note field.

### Step 2: Verify red

```bash
cd loc_kit_ingest && uv run pytest tests/test_profile_v2.py -k source_flags -q
```

Expected: FAIL because `source_flags` is an unknown record-map field.

### Step 3: Implement the closed schema

Add an immutable `SourceFlagsField(column, header, row_offset)`, parse it with exact header and one-based-to-zero-based column conversion, validate its offset against every region stride, and include it in `_check_record_map_field_locations`. Keep it optional and v2-only.

### Step 4: Verify green

Run the command from Step 2.

Expected: PASS.

### Step 5: Commit

```bash
git add loc_kit_ingest/profile.py loc_kit_ingest/tests/test_profile_v2.py
git commit -m "feat(loc-kit): define glossary source flag field"
```

---

## Task 2: Infer and parse the closed flag vocabulary

**Files:**

- Modify: `loc_kit_ingest/infer.py`
- Modify: `loc_kit_ingest/model.py`
- Modify: `loc_kit_ingest/parser.py`
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`
- Test: `loc_kit_ingest/tests/test_parser_tbx.py`
- Test: `loc_kit_ingest/tests/test_model.py`

### Step 1: Write failing inference and parser tests

Cover:

- exact case-insensitive `flags` header outside language columns;
- empty flag cells;
- `read-only`, `forbidden`, and comma-separated combinations;
- stable deduplication/order;
- rejection of unknown or parameterized values;
- rejection of populated flag cells on non-term rows;
- preservation on `GlossaryTerm.source_flags`.

### Step 2: Verify red

```bash
cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py tests/test_parser_tbx.py tests/test_model.py -k "source_flags or flags_column" -q
```

Expected: FAIL because inference rejects the non-language column and `GlossaryTerm` has no source flags.

### Step 3: Implement minimal inference and parsing

- Recognize only the normalized header `flags`.
- Emit `grammar.source_flags` with `row_offset: 0`.
- Treat the field as consumed by the record-map parser.
- Split non-empty cells on commas, trim tokens, reject empty/unknown/parameterized tokens, and store a sorted tuple from `{"read-only", "forbidden"}`.
- Keep `terminology`, `exact`, `not-applicable`, and arbitrary Weblate flags outside this contract.

### Step 4: Verify green

Run the command from Step 2.

Expected: PASS.

### Step 5: Commit

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/model.py loc_kit_ingest/parser.py loc_kit_ingest/tests/test_infer_glossary.py loc_kit_ingest/tests/test_parser_tbx.py loc_kit_ingest/tests/test_model.py
git commit -m "feat(loc-kit): parse glossary source flags"
```

---

## Task 3: Preserve flags through TBX publication

**Files:**

- Modify: `loc_kit_ingest/writer.py`
- Test: `loc_kit_ingest/tests/test_writer.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

### Step 1: Write failing writer and Weblate import tests

Prove the rendered TBX contains `termEntry@weblate-flags`, parse-back returns the same flags, and a created Weblate glossary source unit exposes `read-only` or `forbidden` in `extra_flags` and in `build_glossary_prompt_entry`.

### Step 2: Verify red

```bash
cd loc_kit_ingest && uv run pytest tests/test_writer.py -k source_flags -q
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k "source_flags and creation" -n0
```

Expected: FAIL because the writer does not serialize flags.

### Step 3: Implement TBX serialization and parse-back validation

Set `weblate-flags` on each emitted `termEntry` when `source_flags` is non-empty. Extend `_validate_tbx` to compare the parsed flag set with `GlossaryTerm.source_flags`, preserving the existing render-then-parse publication gate.

### Step 4: Verify green

Run the commands from Step 2.

Expected: PASS.

### Step 5: Commit

```bash
git add loc_kit_ingest/writer.py loc_kit_ingest/tests/test_writer.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): persist glossary flags in TBX"
```

---

## Task 4: Show and apply flags in Weblate

**Files:**

- Modify: `weblate/trans/loc_kit.py`
- Modify: `weblate/trans/views/create.py`
- Modify: `weblate/templates/trans/loc_kit_glossary_preview.html`
- Test: `weblate/trans/tests/test_loc_kit_profile_suggester.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

### Step 1: Write failing preview and append tests

Cover:

- sampled preview terms and `preview_json` include normalized source flags;
- the preview table renders flags visibly;
- a new appended source unit merges imported flags with `terminology`;
- replaying or colliding with an existing term does not alter its flags.

### Step 2: Verify red

```bash
./rundev.sh test weblate/trans/tests/test_loc_kit_profile_suggester.py weblate/trans/tests/test_loc_kit_ingest_contract.py -k "source_flags or glossary_flags" -n0
```

Expected: FAIL because the preview and append service omit source flags.

### Step 3: Implement preview and append behavior

Add `source_flags` to `GlossaryTermPreview`, the bounded preview JSON, and the preview table. When `append_glossary_terms` creates a source term, merge every imported source flag and `terminology` into the source unit's existing flags before one `update_extra_flags` call. Leave the existing-term branch unchanged.

### Step 4: Verify green

Run the command from Step 2.

Expected: PASS.

### Step 5: Commit

```bash
git add weblate/trans/loc_kit.py weblate/trans/views/create.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/trans/tests/test_loc_kit_profile_suggester.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): apply glossary source flags"
```

---

## Task 5: Document the producer template and verify the full contract

**Files:**

- Modify: `docs/guides/loc-kit-ingest.md`
- Modify: `docs/changes.rst`

### Step 1: Update the guide

Add the canonical table:

```csv
en,ru,definition,flags
HeroCraft,HeroCraft,company name,read-only
Vessel,,never use this wording; use Ship,forbidden
```

Document the exact header, closed values, comma separation, preview, append-only behavior, automatic `terminology`, and the per-language flag non-goals. Remove statements that glossary flags are unsupported. Add a concise entry to the current unreleased changelog section.

### Step 2: Run standalone and Weblate regressions

```bash
cd loc_kit_ingest && uv run pytest
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py weblate/trans/tests/test_loc_kit_profile_suggester.py -n0
```

Expected: PASS.

### Step 3: Run scoped lint

```bash
uv run prek run --files loc_kit_ingest/profile.py loc_kit_ingest/infer.py loc_kit_ingest/model.py loc_kit_ingest/parser.py loc_kit_ingest/writer.py loc_kit_ingest/tests/test_profile_v2.py loc_kit_ingest/tests/test_infer_glossary.py loc_kit_ingest/tests/test_parser_tbx.py loc_kit_ingest/tests/test_model.py loc_kit_ingest/tests/test_writer.py weblate/trans/loc_kit.py weblate/trans/views/create.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/trans/tests/test_loc_kit_profile_suggester.py weblate/trans/tests/test_loc_kit_ingest_contract.py docs/guides/loc-kit-ingest.md docs/changes.rst
```

Expected: PASS.

### Step 4: Commit

```bash
git add docs/guides/loc-kit-ingest.md docs/changes.rst
git commit -m "docs(loc-kit): document glossary source flags"
```

NO UNRESOLVED DECISIONS
