# Multilingual spreadsheet exchange Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let producers download one component as a single CSV or XLSX containing every component language, edit it in Google Sheets, preview the result, and atomically import it back into Weblate.

**Architecture:** This is a component-level exchange format, not a per-translation `BaseExporter`: `BaseExporter(translation=...)` and `download_multi()` deliberately serialize one file per language. A new service builds a pivoted view from the component's source units and their sibling translations, then uses one shared tabular model for CSV and XLSX. Import parses that model, validates every row before mutation, stores a short-lived owner/session-bound upload draft for preview confirmation, then applies the approved changes inside a transaction.

**Tech Stack:** Django forms and views, PostgreSQL/Django transactions, Translate Toolkit CSV/XLSX support, openpyxl through the existing `XlsxFormat`, Bootstrap/crispy templates, pytest.

---

## Approved format contract

- Scope: exactly one component per file, exposed from that component's :guilabel:`Files` menu. Project/category/workspace downloads remain ZIP exports and are not changed.
- Columns: `key`, then every component language in Weblate code order (source language included). Append `context` only when it is independent of `key` and needed to disambiguate a row. A monolingual JSON/XLSX component therefore has `key,ru,en,pt_BR,zh_Hans`, while a contextual bilingual component has `key,ru,en,context`.
- Row identity: for monolingual formats, `key` is `Unit.context`; for bilingual formats, it is the source text. `context` is included only for a distinct bilingual `msgctxt`; it is omitted when it would duplicate `key` or is empty for every row. The service must reject an export or import when the visible identity (`key`, plus `context` when present) is not unique. It must never guess from row position.
- One source unit produces exactly one row. A language cell is its sibling unit's target. Empty cells are exported unchanged.
- CSV uses UTF-8 with an Excel-compatible dialect; XLSX contains one worksheet. Cells are literal text, never formulas, and the same header and cell values must round-trip through Google Sheets.
- Import accepts only the same component's exported schema. It rejects unknown/missing language columns, duplicate headers or row identities, unknown keys, an unexpected or absent required independent `context` column, and malformed CSV/XLSX before any changes are written.
- A blank language cell means "not translated": after confirmation it clears that translation and gives it the normal untranslated state. It is not a no-op.
- Every non-empty changed target must preserve all protected tokens from its source in the same order. This reuses and tightens the game-markup token extraction, including `{0}`, `{playerName}`, `%s`, `%KEY%`, and Unity markup. An invalid row is reported in the preview and blocks confirmation; it is never silently fixed.
- Existing source files, Git workflows, and the existing per-language ZIP/CSV/XLSX/XLIFF downloads stay unchanged.

Production evidence, read-only on 2026-08-22: `pirate-ships/localization-json` has 3,735 source units and no blank `Unit.context`; `lang_name_en` maps to Russian source `Английский` and all language targets. `need-for-greed/buyers` (`xlsx`) likewise stores `Buyer1` in `Unit.context`. In both formats `context` is already the file key, not additional translator context.

## Task 1: Add the tabular exchange service and its contract tests

**Files:**
- Create: `weblate/trans/multilingual_spreadsheet.py`
- Create: `weblate/trans/tests/test_multilingual_spreadsheet.py`
- Modify: `weblate/formats/external.py` only if the existing `XlsxFormat` lacks a public, reusable serialization/parsing operation needed by the service.

**Step 1: Write failing export/import parser tests.**

Cover a monolingual test component with source and target translations. Assert that the parsed CSV and XLSX both have exactly the same headers and rows; `key` is first, language headers use `Language.code`, source is included, and `context` is appended only for a distinct contextual identity. Assert literal preservation of commas, quotes, newlines, Unicode, and formula-like cell values.

Add rejection tests for duplicate headers, a missing language column, an unknown language column, duplicated visible row identities, a changed key or independent context, and a workbook with more than one selected worksheet. Every rejection must be a typed validation error with a row/column diagnostic.

**Step 2: Run the new tests to verify they fail.**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py`

Expected: failure because the service does not exist.

**Step 3: Implement a small, format-neutral row model.**

In `weblate/trans/multilingual_spreadsheet.py`, define immutable parsed header/row/result types and these narrow operations:

```python
def export_component(component: Component, format_name: Literal["csv", "xlsx"]) -> bytes: ...
def parse_upload(component: Component, uploaded: UploadedFile) -> ParsedSpreadsheet: ...
def build_preview(component: Component, parsed: ParsedSpreadsheet) -> SpreadsheetPreview: ...
```

Query the component's source translation once, fetch sibling units by `source_unit_id` and language, and map them by source-unit primary key. Resolve incoming rows by `key` alone unless the exported schema includes an independent `context`, then use `(key, context)`; error rather than resolving an ambiguous identity. Do not instantiate `BaseExporter` or emit a file per `Translation`.

Use the existing CSV escaping conventions from `CSVExporter.string_filter()` and the existing `XlsxFormat` serialization/parsing path so that formula-looking strings are handled consistently with current Weblate CSV/XLSX behavior. Keep the table schema validation in this service, not in a view.

**Step 4: Run focused tests.**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py`

Expected: all export and parse-contract tests pass.

**Step 5: Commit.**

```bash
git add weblate/trans/multilingual_spreadsheet.py weblate/trans/tests/test_multilingual_spreadsheet.py weblate/formats/external.py
git commit -m "feat(trans): add multilingual spreadsheet exchange service"
```

## Task 2: Make protected-token validation shared and ordered

**Files:**
- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `weblate_customization/tests/test_checks.py` or the existing custom-check test module containing `GameMarkupCheck` cases
- Modify: `weblate/trans/multilingual_spreadsheet.py`

**Step 1: Write failing token-order tests.**

Add cases proving `{0}` followed by `{playerName}` is valid only in that order; swapping them fails. Cover `%s`, `%KEY%`, nested Unity markup, a target without source tokens, and an empty target. The empty target is valid because it represents an untranslated cell.

**Step 2: Run the focused custom-check tests to verify the new ordering case fails.**

Run: `./rundev.sh test weblate_customization/tests/test_checks.py -k markup`

Expected: the reordered-token case currently passes because `GameMarkupCheck` sorts token lists.

**Step 3: Extract and reuse the existing token parser.**

Extend `PLACEHOLDER_PATTERN` for printf-style placeholders such as `%s`. Expose one helper from `checks.py` that returns the protected token sequence. Change `GameMarkupCheck.check_single()` from unordered multiset comparison to ordered sequence comparison, then call the same helper from the spreadsheet preview validator. Do not introduce a second placeholder regex in the importer.

**Step 4: Run custom-check and spreadsheet tests.**

Run:

```bash
./rundev.sh test weblate_customization/tests/test_checks.py -k markup
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py
```

Expected: exact order passes, changed/missing/added/reordered protected tokens fail, and blank targets remain importable as untranslated.

**Step 5: Commit.**

```bash
git add weblate_customization/src/weblate_customization/checks.py weblate_customization/tests weblate/trans/multilingual_spreadsheet.py
git commit -m "fix(checks): preserve protected token order"
```

## Task 3: Add a private, confirmable component-table import draft

**Files:**
- Create: `weblate/trans/models/multilingual_spreadsheet.py`
- Modify: `weblate/trans/models/__init__.py`
- Create: `weblate/trans/migrations/00xx_component_spreadsheet_import_draft.py`
- Modify: `weblate/trans/tasks.py`
- Modify: `weblate/trans/tests/test_multilingual_spreadsheet.py`
- Modify: `docs/security/threat-model.rst`

**Step 1: Write failing draft lifecycle tests.**

Test that a draft is readable only by its creating user and Django session; it expires after at most one hour, is inaccessible after consumption, deletes its private uploaded file on explicit deletion and cascade deletion, and the cleanup task removes expired drafts. Test that a source/component change after upload causes confirmation to fail rather than applying stale rows.

**Step 2: Run the lifecycle tests to verify they fail.**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -k draft`

Expected: failure because no draft model exists.

**Step 3: Implement a dedicated draft, following `LocKitImportDraft` security invariants.**

Create `ComponentSpreadsheetImportDraft` rather than widening the glossary-specific `LocKitImportDraft`. Store an unguessable UUID token, owner, session key, target component, original filename, private `FileSystemStorage` upload, serialized validated preview, state (`preview-ready` / `consumed`), timestamps, and a hard one-hour expiry. Its lookup must return no distinguishing information for missing, wrong-owner, wrong-session, expired, or consumed tokens. Add storage deletion through a post-delete receiver and a Celery cleanup task.

The draft stores untrusted input only. Confirmation must reparse the private file and rebuild the preview against the current component, so a forged preview JSON cannot choose targets or values.

Add the new table-import surface to the threat model: authenticated `upload.perform` caller, bounded application upload limits, private short-lived storage, owner/session token binding, fully local parsing, preview-before-apply, and no outbound request. This is required because it adds an import format.

**Step 4: Run draft tests.**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -k draft`

Expected: all lifecycle, authorization, expiry, and stale-component checks pass.

**Step 5: Commit.**

```bash
git add weblate/trans/models/multilingual_spreadsheet.py weblate/trans/models/__init__.py weblate/trans/migrations weblate/trans/tasks.py weblate/trans/tests/test_multilingual_spreadsheet.py docs/security/threat-model.rst
git commit -m "feat(trans): stage spreadsheet imports for confirmation"
```

## Task 4: Add component download, upload preview, and atomic apply

**Files:**
- Modify: `weblate/urls.py`
- Modify: `weblate/trans/views/files.py`
- Modify: `weblate/trans/forms.py`
- Modify: `weblate/templates/component.html`
- Create: `weblate/templates/multilingual_spreadsheet_import.html`
- Modify: `weblate/trans/tests/test_files.py`
- Modify: `weblate/trans/tests/test_multilingual_spreadsheet.py`

**Step 1: Write failing view tests.**

Test these exact behaviors:

- a user with `translation.download` receives one non-ZIP CSV or XLSX file from the component `Files` menu endpoint;
- a user without `upload.perform` cannot upload or confirm;
- upload renders a preview with additions, modifications, clears, and unchanged cells;
- confirm changes all validated rows atomically;
- a blank target clears a prior translation and marks it untranslated;
- one invalid changed placeholder blocks the whole confirmation with no writes;
- a locked component cannot upload or confirm;
- the original `upload` endpoint and existing ZIP downloads are unchanged.

**Step 2: Run the new view tests to verify they fail.**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_files.py -k multilingual
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -k "view or apply"
```

Expected: failures because the endpoint, form, and template do not exist.

**Step 3: Add component-only routes and menu controls.**

Add download routes that accept only `csv` or `xlsx` and call `export_component()`. Add upload, preview, confirm, and cancel routes that resolve exactly a `Component`; do not overload the current translation-only `upload()` view. Check `translation.download` for export and `upload.perform` for every upload/preview/confirm request, and recheck component lock status at confirmation.

In `component.html`, place two translated entries under the existing `Files` menu: "Download multilingual CSV" and "Download multilingual XLSX". Add one translated "Upload multilingual spreadsheet" entry that opens the new form. Set `FormHelper.form_tag = False` for forms rendered inside template-owned forms.

**Step 4: Implement preview and apply.**

The upload view creates the private draft only after parser and schema validation. The preview renders a bounded summary plus row diagnostics. The confirm view reparses the draft, validates protected-token order and all identities again, then uses `transaction.atomic()` and `select_for_update()` for source units and all sibling translations. Apply only language targets in the table; source-language values and keys are immutable. For an empty non-source target, call the existing unit mutation path with an empty target and the normal untranslated state so standard checks, audit entries, and cache invalidation remain correct. Mark the draft consumed only after the transaction succeeds.

**Step 5: Run focused tests.**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_files.py -k multilingual
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py
```

Expected: all download, authorization, preview, atomicity, clear-cell, and invalid-placeholder tests pass.

**Step 6: Commit.**

```bash
git add weblate/urls.py weblate/trans/views/files.py weblate/trans/forms.py weblate/templates/component.html weblate/templates/multilingual_spreadsheet_import.html weblate/trans/tests/test_files.py weblate/trans/tests/test_multilingual_spreadsheet.py
git commit -m "feat(trans): import multilingual component spreadsheets"
```

## Task 5: Document, lint, and smoke-test the delivered workflow

**Files:**
- Modify: `docs/changes.rst`
- Modify: the applicable `docs/user/files.rst` page, if it owns component download/upload guidance
- Modify: `docs/security/threat-model.rst` only if Task 3 did not already cover the final documented surface

**Step 1: Add user-facing documentation and changelog.**

Document the exact column contract, Weblate language-code headers, `context` position, one-row-per-key rule, blank-cell clearing semantics, Google Sheets save/re-import path, protected-token rejection, preview, and component-only scope. Add a concise entry to the current unreleased changelog section.

**Step 2: Run focused documentation checks.**

Run: `uv run prek run docs/changes.rst docs/user/files.rst docs/security/threat-model.rst`

Expected: formatter, reStructuredText, and spelling hooks pass for changed documentation.

**Step 3: Run product verification.**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_files.py -k multilingual
./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py
uv run prek run --all-files
```

Then drive the running development UI as `admin/admin`: open a component with at least `ru`, `en`, and one target language; use `Files` to download both formats; open and re-save them in Google Sheets-compatible tooling; upload each file; verify preview; clear one target cell; confirm; reload the translation and verify that only that target is now untranslated. Upload a file with reordered `{0}` and `{playerName}` and verify confirmation is blocked with no changes.

**Step 4: Commit and push.**

```bash
git add docs/changes.rst docs/user/files.rst docs/security/threat-model.rst
git commit -m "docs(trans): document multilingual spreadsheet exchange"
git push origin HEAD
```

## Out of scope

- One workbook spanning multiple components or projects.
- Git synchronization changes, original source-file generation, or modifications to existing ZIP downloads.
- Google Sheets API access, OAuth, polling, or direct cloud synchronization.
- Automatic repair, renumbering, or reordering of placeholders.
- A user-selectable subset or reordering of language columns.
