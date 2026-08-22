# Multilingual spreadsheet exchange review fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix every Critical and Important defect found in the code review of `feat/multilingual-spreadsheet-exchange` (11 findings) plus the preview/cancel UI gap.

**Architecture:** Fixes stay inside the existing feature surface: shared identity resolution in `weblate/trans/multilingual_spreadsheet.py`, confirm/cancel hardening in `weblate/trans/views/files.py`, migration state repair, template preview table, and a one-line restore of `TOKEN_PATTERN` in `weblate_customization/src/weblate_customization/checks.py` (production regression from commit `8f26f2d`).

**Tech Stack:** Django views/models/migrations, openpyxl, pytest inside dev-docker (`./rundev.sh test`).

---

## Task 1: Restore TOKEN_PATTERN in checks.py

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py` (before `_tokens_dsl`, ~line 93)

**Step 1: Verify the regression exists**

Run: `./rundev.sh test weblate_customization/tests/test_checks.py -k GameTokenCheck`
Expected: FAIL with `NameError: name 'TOKEN_PATTERN' is not defined`

**Step 2: Restore the constant**

```python
# Mission DSL substitution identifier before a bracketed, translated body:
# item_type[|{0}], skirmish_league_id[gen|в {0}|в любой лиге]. A bracket
# without a `|` is ordinary prose, not a substitution.
TOKEN_PATTERN = regex.compile(r"([a-z][a-z0-9_]*)\[[^\]]*\|")
```

**Step 3: Copy into container and re-run**

```bash
cp weblate_customization/src/weblate_customization/checks.py dev-docker/data/python/weblate_customization/checks.py
./rundev.sh test weblate_customization/tests/test_checks.py -k GameTokenCheck
```

Expected: PASS

Note: `/dev-docker/data/` is gitignored (line 35 of `.gitignore`). The `cp` above is local-only container setup; do **not** `git add` it.

**Step 4: Commit**

```bash
git add weblate_customization/src/weblate_customization/checks.py
git commit -m "fix(checks): restore token pattern for GameTokenCheck"
```

### Task 2: Bound XLSX dimensions and convert parse errors to ValidationError

**Files:**

- Modify: `weblate/trans/multilingual_spreadsheet.py` (`_parse_xlsx`, lines ~168-200)
- Test: `weblate/trans/tests/test_multilingual_spreadsheet.py` (extend `MultilingualSpreadsheetValidationTest`)

**Step 1: Write failing tests**

```python
def test_rejects_xlsx_with_inflated_dimension(self) -> None:
    from weblate.trans.multilingual_spreadsheet import export_component, parse_upload

    workbook = load_workbook(BytesIO(export_component(self.component, "xlsx")))
    workbook.active.calculate_dimension = lambda: "A1:XFD1048576"
    # force inflated dimension by writing a far cell then deleting is not enough
    # in read_only; instead write dimension attribute directly:
    workbook.active._current_row = 1048575
    output = BytesIO()
    workbook.save(output)
    with self.assertRaises(ValidationError):
        parse_upload(
            self.component, SimpleUploadedFile("translations.xlsx", output.getvalue())
        )


def test_rejects_malformed_xlsx_xml(self) -> None:
    # valid ZIP, broken sheet XML
    import zipfile
    from weblate.trans.multilingual_spreadsheet import export_component, parse_upload

    source = BytesIO(export_component(self.component, "xlsx"))
    output = BytesIO()
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(output, "w") as zout:
        for item in zin.infolist():
            payload = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                payload = b"<worksheet><broken"
            zout.writestr(item, payload)
    with self.assertRaises(ValidationError):
        parse_upload(
            self.component, SimpleUploadedFile("translations.xlsx", output.getvalue())
        )
```

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -k "inflated or malformed"`
Expected: FAIL (dimension test: no ValidationError; malformed test: exception other than ValidationError)

**Step 2: Implement bounds + error conversion**

In `_parse_xlsx`:

```python
        workbook = load_workbook(BytesIO(content), data_only=False, read_only=True)
    except (BadZipFile, ZipSafetyError, ValueError, KeyError) as error:
        raise ValidationError("Invalid XLSX upload.") from error
    from openpyxl.utils.exceptions import InvalidFileException
```

Catch `InvalidFileException` too (restructure the except tuple to include it; import at top of function with `load_workbook`). Then after `worksheet = workbook.worksheets[0]`:

```python
    expected_columns = len(_schema(component, _component_units(component)[0]).headers)
    if (
        worksheet.max_column is not None and worksheet.max_column > expected_columns
    ) or (
        worksheet.max_row is not None
        and worksheet.max_row > len(component.source_translation.unit_set.all()) + 1
    ):
        _error("XLSX dimensions exceed the component schema.")
```

Note: `_parse_xlsx` currently receives only `content`; pass `component` (or expected bounds) in and adjust `parse_upload` accordingly. Iterate with explicit bounds: `worksheet.iter_rows(max_col=expected_columns)`.

**Step 3: Run tests**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -n 0`
Expected: PASS (serial, per repo xdist flakiness note)

**Step 4: Commit**

`git commit -m "fix(trans): bound xlsx dimensions and surface parse errors"`

### Task 3: Fix migration 0102 storage drift

**Files:**

- Modify: `weblate/trans/migrations/0102_component_spreadsheet_import_draft.py` (uploaded field, ~line 36)

**Step 1: Match the loc-kit pattern**

Add `import weblate.trans.models.multilingual_spreadsheet` and change the field to:

```python
(
    "uploaded",
    models.FileField(
        blank=True,
        storage=weblate.trans.models.multilingual_spreadsheet.COMPONENT_SPREADSHEET_DRAFT_STORAGE,
        upload_to="",
    ),
),
```

Migration is unreleased (this branch) so editing it in place is safe.

**Step 2: Verify no drift**

Run: `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -n 0` (exercises migration) plus check `makemigrations --check --dry-run` inside the container if available.
Expected: no new migration proposed.

**Step 3: Commit**

`git commit -m "fix(trans): serialize draft storage backend in migration"`

### Task 4: Harden confirm/cancel in files.py

**Files:**

- Modify: `weblate/trans/views/files.py` (`multilingual_upload`, `multilingual_confirm`, `multilingual_cancel`, lines ~287-390)
- Modify: `weblate/trans/multilingual_spreadsheet.py` (export `_identity`/`_schema` helpers or add a public `resolve_source_units(component)` helper)
- Test: `weblate/trans/tests/test_files.py` (new `MultilingualSpreadsheetConfirmTest`)

Covers review findings: identity mapping by `(source, context)`, source-in-baseline staleness, lock recheck, stale ValidationError -> message, draft row locking.

**Step 1: Failing tests**

- `test_confirm_rejects_stale_component` — create draft via upload POST, then change a target unit, POST confirm, expect redirect + error message (not 500) and unchanged units.
- `test_confirm_rejects_locked_component` — lock component after preview, confirm, expect rejection.
- `test_confirm_maps_duplicate_sources_by_context` — non-template component with two units sharing source text but distinct context; confirm; assert each target went to the right unit.
- `test_cancel_then_confirm_rejected` — cancel a draft, then POST confirm on the same token; expect 404, no units changed.

**Step 2: Implement**

In `multilingual_spreadsheet.py` make `_identity`, `_schema` public (rename without underscore, keep aliases out — clean cutover) and export a helper:

```python
def resolve_source_units(component, parsed) -> list[Unit]:
    """Map each parsed row to its source unit by the validated visible identity."""
```

In `multilingual_upload` baseline: include source units as `str(unit.pk): [unit.source, unit.target, unit.state]` for `component.source_translation.unit_set` too.

In `multilingual_confirm`:

```python
with transaction.atomic():
    component = Component.objects.select_for_update().get(pk=component.pk)
    if component.locked:
        raise ValidationError(gettext("The component is locked."))
    draft = (
        ComponentSpreadsheetImportDraft.objects.select_for_update()
        .get(pk=draft.pk, state=ComponentSpreadsheetImportDraft.State.PREVIEW_READY)
    )
    ...baseline compare including source units...
    parsed = parse_upload(component, draft.uploaded)
    build_preview(component, parsed)
    source_rows = resolve_source_units(component, parsed)
```

Wrap the transaction in `try/except (ValidationError, ComponentSpreadsheetImportDraft.DoesNotExist)`, on failure `messages.error(...)` and `return redirect(component)`. Apply rows via `resolve_source_units` instead of the `source_by_key` dict. Mark draft CONSUMED under the lock.

In `multilingual_cancel`: wrap in `transaction.atomic()` with `select_for_update()` on the draft row before `delete()`.

**Step 3: Run tests**

Run: `./rundev.sh test weblate/trans/tests/test_files.py -k multilingual` and `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -n 0`
Expected: PASS

**Step 4: Commit**

`git commit -m "fix(trans): harden multilingual spreadsheet confirmation"`

### Task 5: Plural download handling

**Files:**

- Modify: `weblate/trans/views/files.py` (`multilingual_download`)
- Modify: `weblate/templates/component.html` (lines ~92-99, the two multilingual-download links)

**Step 1: Failing test** — plural component GET on `multilingual-download` returns redirect+message or 404, not 500. (Check how tests build a plural component: `MultilingualSpreadsheetPluralTest` in test_multilingual_spreadsheet.py already has one; mirror its `create_component`.)

**Step 2: Implement** — catch `ValidationError` in `multilingual_download` -> `messages.error` + `redirect(component)`. Gate template links behind `{% if not object.has_plural %}` if such a property exists; verify property name (`Component.has_plural` or `template_has_plural`) before using; if none exists cheaply, keep the view-level catch only and leave links visible (do not add a new model property — YAGNI for a menu).

**Step 3: Test + commit** — `fix(trans): reject plural multilingual downloads gracefully`

### Task 6: Preview table + cancel button in template

**Files:**

- Modify: `weblate/templates/multilingual_spreadsheet_import.html`
- Modify: `weblate/trans/views/files.py` (`multilingual_upload` render context: pass a compact diff — rows where any target differs, with language/key/old/new)

**Step 1: Failing test** — upload POST response contains a changed target cell value and a Cancel control posting to `multilingual-cancel`.

**Step 2: Implement**

View builds `changes = [{"key": ..., "language": ..., "old": ..., "new": ...}]` comparing parsed rows to current targets (source column skipped). Template renders a Bootstrap table (`table table-sm`) of changes and:

```html
<form method="post" action="{% url 'multilingual-cancel' token=draft.token %}">
  {% csrf_token %}
  <button type="submit" class="btn btn-default">{% translate "Cancel import" %}</button>
</form>
```

Follow ACCESSIBILITY.md: table needs `<th scope>` headers; buttons are real `<button type="submit">`.

**Step 3: Test + commit** — `feat(trans): render spreadsheet preview and cancel`

### Task 7: Full verification and push

1. `./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py weblate/trans/tests/test_files.py -k multilingual` (plus serial rerun if xdist flakes)
2. `./rundev.sh test weblate_customization/tests/test_checks.py`
3. `uv run prek run --all-files` (per AGENTS.md; runs from repo root config)
4. `git push origin HEAD`

## Out of scope

- Reworking `preview.changes` semantics beyond what Task 6 needs.
- Lock-timeout retry loop (Django default lock wait is acceptable; not in review findings as a blocker).
- Minor style nits (blank lines, `_csv_value` helper) — fold in only if touched lines require it.
