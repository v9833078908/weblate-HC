# Multilingual spreadsheet string location column Implementation Plan

**Status:** awaiting approval

**Goal:** Carry the engine string identifier that a game kit ships next to its
key (Unity Localization's `Id`, stored by Weblate as `Unit.location`) into the
component multilingual CSV and XLSX exports, and validate it on import instead
of silently dropping it.

**Why:** `loc_kit_ingest` maps a numeric kit column to a PO location
(`loc_kit_ingest/infer.py:340-385`, `loc_kit_ingest/writer.py:47-57`), and
Weblate stores that as `Unit.location` on the source unit
(`weblate/trans/models/unit.py:1290-1315`). `po-mono` supports locations
(`weblate/formats/ttkit.py:1836-1847`), but
`weblate/trans/multilingual_spreadsheet.py:81-92` emits only `key`, the
component languages, and an optional `context`, so a producer editing the
multilingual sheet cannot see or return the engine identifier.

**Architecture:** One column added to the existing component-level exchange
schema in `weblate/trans/multilingual_spreadsheet.py`. It is read-only
metadata: exported from the source unit, required to match on import, never
written back to a unit. The schema stops deriving language columns by header
slicing and carries them explicitly, so metadata columns cannot shift the
language or identity indexes.

**Tech Stack:** Django, existing service module and views, pytest inside
dev-docker (`./rundev.sh test`).

---

## Format contract additions

- Column name is `location`, placed immediately after `key` and before the
  language columns. `context` stays the last column.
- `location` MUST NOT be named `id`: `id` is the Weblate language code for
  Indonesian and is already emitted as a language column by real components
  (`need-for-greed/orders` exports `key,bg,cs,de,en,es,fil,fr,hu,id,...`), so
  an `id` metadata column would collide with a language column and break the
  duplicate-header rule in `_validate_rows`.
- The column is present only when at least one source unit has a non-empty
  `Unit.location`. Components without locations export exactly the headers they
  export today, so existing sheets keep round-tripping unchanged.
- The value is `Unit.location` of the source unit, verbatim. Weblate already
  stores several PO locations comma-joined; the cell holds that string as is and
  is compared by exact equality. No parsing, splitting, or normalization.
- On import the column is read-only. A cell that does not equal the current
  source unit location is a row-level validation error, exactly like an unknown
  key. This keeps the game repository the source of truth for engine
  identifiers, and prevents a producer from believing an edited identifier was
  applied.
- Import never writes `location`, and never treats it as a translation target.

## Out of scope

- A Unity-native `Key,Id,English(en)` export. The user chose the metadata column
  in the existing Weblate schema; a Unity-shaped exporter stays a separate
  feature.
- Editing engine identifiers through Weblate.
- The per-language ZIP/CSV/XLSX/JSON downloads and the loc-kit importer.
- `docs/security/threat-model.rst`: no new endpoint, upload format, outbound
  call, or claimed security property. The change adds one read-only column to an
  already modelled import surface, so none of the model's "Conditions that
  change this model" apply.

## Task 1: Carry the string location through the exchange schema

**Files:**

- Modify: `weblate/trans/multilingual_spreadsheet.py`
- Modify: `weblate/trans/views/files.py` (`_apply_preview`)
- Modify: `weblate/trans/tests/test_multilingual_spreadsheet.py`
- Modify: `weblate/trans/tests/test_files.py`

**Step 1: Write the failing tests.**

In `test_multilingual_spreadsheet.py`:

- `test_export_includes_string_location_column`: a `create_po_mono()`
  component whose source units get `location` set directly in the database.
  Assert the CSV and XLSX headers are `("key", "location", *languages)`, that
  the row cell holds the unit's location, and that the exported bytes parse
  back through `parse_upload` unchanged.
- `test_export_omits_location_column_without_locations`: the existing
  `create_json()` component keeps today's headers, so no component without
  locations changes shape.
- `test_import_rejects_changed_location`: editing the `location` cell raises
  `ValidationError` naming row and column.
- `test_import_accepts_target_change_with_location_column`: changing only a
  language cell still yields a preview whose changes contain that row.

In `test_files.py`, extend the multilingual confirm coverage with a po-mono
component carrying locations: confirming a target edit applies the target and
leaves `Unit.location` untouched on both the source and the sibling unit. This
is the regression that matters, because `_apply_preview` currently derives the
editable columns from `headers[1:]` and would otherwise try to write the
location cell into a translation.

**Step 2: Run them and watch them fail.**

```bash
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py \
  weblate/trans/tests/test_files.py -k "location" -n 0
```

Expected: failures, because the column does not exist.

**Step 3: Implement.**

In `weblate/trans/multilingual_spreadsheet.py`:

- Extend `SpreadsheetSchema` to `headers`, `languages`, `has_location`,
  `has_context`. `languages` is the explicit tuple of component language codes,
  so no caller re-derives them by slicing `headers`.
- `_schema` computes `has_location = any(unit.location for unit in
  source_units)` and builds
  `("key", *(("location",) if has_location else ()), *languages, *(("context",)
  if has_context else ()))`.
- `export_component` writes values by schema order: key, then the source unit's
  `location`, then one cell per `schema.languages`, then `context`. Drop the
  `headers[1 : -1 if schema.has_context else None]` slice.
- `_validate_rows` resolves the row identity from explicit header indexes
  (`key` at 0, `context` by `headers.index("context")`) instead of `row[-1]`,
  and, when `has_location`, compares the location cell to the resolved source
  unit's `location`, erroring on mismatch.
- `build_preview` iterates `schema.languages` through a
  `header -> index` map rather than zipping `headers[1:]`, so metadata columns
  are structurally excluded from placeholder checks.

In `weblate/trans/views/files.py`, make `_apply_preview` iterate
`schema.languages` through the same index map, so only language cells reach
`Unit.translate`.

**Step 4: Run the focused tests.**

```bash
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py \
  weblate/trans/tests/test_files.py -k "multilingual or location" -n 0
```

Expected: pass.

**Step 5: Commit.**

```bash
git add weblate/trans/multilingual_spreadsheet.py weblate/trans/views/files.py \
  weblate/trans/tests/test_multilingual_spreadsheet.py \
  weblate/trans/tests/test_files.py
git commit -m "feat(trans): export string location in multilingual spreadsheets"
```

## Task 2: Document the column

**Files:**

- Modify: `docs/user/files.rst`
- Modify: `docs/changes.rst`

**Step 1: Extend the user documentation.**

In the existing `Multilingual component spreadsheets` section, state that a
`location` column follows `key` when the component stores string locations,
that it holds the identifier the translation file carries for the string, and
that it is read-only: an edited value is rejected on import.

**Step 2: Amend the unreleased changelog entry.**

`docs/changes.rst:11` already announces the multilingual spreadsheet exchange
in the unreleased section, and the feature is not released yet, so extend that
sentence with the string location column instead of adding a second bullet.

**Step 3: Commit.**

```bash
git add docs/user/files.rst docs/changes.rst
git commit -m "docs(trans): document the multilingual spreadsheet location column"
```

## Task 3: Verify and push

1. `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py weblate/trans/tests/test_files.py -n 0`
2. `uv run prek run ruff-check ruff-format --files <exact changed files>` (never
   a bare `--files` run: some hooks ignore the restriction and reformat
   unrelated files)
3. Re-export `need-for-greed/orders` locally after an import of `Orders.csv`
   through the component-creation wizard, and confirm the numeric identifiers
   appear in the `location` column and survive a round-trip upload.
4. `git push origin HEAD`
