# Loc-kit Ingest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
>
> **Status (2026-08-06): implemented and verified.** Deviations from this plan
> are documented in `docs/guides/loc-kit-ingest.md`: the profile is now inferred
> from the kit's header row (`infer.py`, `--profile` became optional evidence
> output), and the component-creation UI accepts kits directly through the
> universal "Upload translation files" tab (`create_component_from_kit`,
> discovery skipped). 127 standalone + 14 Weblate contract tests, live LLM
> smoke passed, manual UI smoke performed on `heart-abyss/temple`.

**Goal:** Build `python -m loc_kit_ingest`, an atomic, profile-driven one-shot
importer that converts XLSX/CSV/TSV kits into Weblate-compatible monolingual PO
components and bilingual per-target-language TBX glossary files.

**Architecture:** A required strict JSON profile selects every sheet, source
language, column and row grammar. Input is parsed deterministically into either
`StringUnit` or `GlossaryTerm`, fully validated, rendered into a sibling staging
directory, parsed back through the actual format libraries, zipped, and atomically
renamed to the requested new output directory. PO serves Temple/UI; Terms emits
one bilingual TBX file per configured target language, preserving source and
target explanations for Routed LLM.

**Tech Stack:** Python 3.12+, stdlib `csv`/`json`/`zipfile`/`tempfile`,
`openpyxl`, Translate Toolkit `pofile` and `tbxfile`, existing Weblate
`PoMonoFormat`, `TBXFormat`, glossary and LLM test helpers, pytest.

**Design spec:** `docs/guides/loc-kit-ingest.md`.

---

## Reviewed decisions and non-negotiable contracts

1. Weblate becomes source of truth after a one-shot seed. No reverse sync,
   merge policy or idempotent re-import.
2. The input sidecar profile is required and schema-versioned. There is no
   auto-detection, header aliasing, generic hint, or `--source-lang` override.
3. `source_lang` is mandatory in every profile component and is not inferred
   from filled cells.
4. Temple and UI emit monolingual PO. Terms emits bilingual TBX files, one
   target language per file; it never emits `source_lang.tbx`.
5. Profile grammar is explicit: keyed rows for PO, named term-description pair
   regions for Terms. No text-length heuristic or fallback parser exists.
6. All structural failures are atomic: no component, ZIP or report is published
   and a pre-existing output directory is never changed.
7. Test fixtures are anonymized. Real kits are used only for a manual final
   smoke; no game dialogue or lore is copied into the repository.
8. The plan includes fast standalone tests, Weblate format/component contract
   tests, a deterministic glossary-payload contract, and an opt-in live LLM
   smoke.

### Existing primitives to reuse

- `translate.storage.pypo.pofile` writes and parses PO while preserving leading
  and trailing whitespace, tabs, CRLF and embedded newlines in `msgstr`.
- `translate.storage.tbx.tbxfile` writes TBX `termEntry`/`langSet`; `tbxunit`
  accepts source/target language tags and `definition`/`translator` notes.
- `weblate.formats.ttkit.PoMonoFormat` maps a PO template `msgid` to source text
  and `msgstr` to string content. `TBXFormat` is bilingual, supports
  explanations, and is not a monolingual-base-file format.
- `docs/formats/tbx.rst` requires `tbx/*.tbx`, no base/template file, and maps
  the target language from the filename. It explicitly warns against a file
  named after the source language.
- `weblate.machinery.llm.BaseLLMTranslation._get_glossary_entry` publishes
  `source_explanation` and `target_explanation` only when the imported glossary
  units actually contain them.

Do not add a JSON-schema package, a generic configuration DSL, custom XML
serialization, a separate distributable package, or a second parser path. The
root package is deliberately executable as `uv run python -m loc_kit_ingest`.

### Data flow and failure boundary

```text
KIT + explicit PROFILE
        |
        v
[Strict profile validation] --error--> stderr, exit 2, no output mutation
        |
        v
[Read all selected sheets]
        |
        v
[Profile-directed parser per component]
        |
        +-- errors --> stderr, exit 2, no output mutation
        |
        v
[Diagnostics + in-memory artifacts]
        |
        v
[sibling staging directory]
        |
        +-- PO/TBX render -> parse-back -> ZIP -> report
        |         |
        |         +-- error --> remove staging, exit 2, output unchanged
        v
[os.replace(staging, --out)] -> stdout report, exit 0
```

### Output topology

```text
out/
├── Temple/
│   ├── ru.po                    # profile source template
│   └── en.po                    # one file per declared language
├── UI/
│   ├── en.po                    # profile source template
│   └── ru.po
├── Terms/
│   └── tbx/
│       ├── en.tbx               # ru source + en target
│       └── ja.tbx               # ru source + ja target
├── Temple.zip                   # ru.po, en.po, ... at ZIP root
├── UI.zip
├── Terms.zip                    # tbx/en.tbx, tbx/ja.tbx, ... at ZIP root
└── report.txt
```

The profile owns `component`, not the spreadsheet sheet name. Require
`[A-Za-z0-9][A-Za-z0-9_-]*`, reject case-folded component and archive collisions,
and construct every output path only from validated component identifiers and
profile language codes.

---

## Task 0: Create a standalone package harness and anonymized fixtures

**Files:**

- Create: `loc_kit_ingest/__init__.py`
- Create: `loc_kit_ingest/__main__.py`
- Create: `loc_kit_ingest/cli.py`
- Create: `loc_kit_ingest/pytest.ini`
- Create: `loc_kit_ingest/tests/conftest.py`
- Create: `loc_kit_ingest/tests/test_harness.py`
- Create: `loc_kit_ingest/tests/fixtures/temple.csv`
- Create: `loc_kit_ingest/tests/fixtures/temple.loc-ingest.json`
- Create: `loc_kit_ingest/tests/fixtures/terms.csv`
- Create: `loc_kit_ingest/tests/fixtures/terms.loc-ingest.json`

Every new Python file begins with the repository header:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
```

**Step 1: Write the failing harness tests.**

```python
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_exists():
    result = subprocess.run(
        [sys.executable, "-m", "loc_kit_ingest", "--help"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--profile" in result.stdout


def test_fixture_content_is_anonymized():
    fixture_text = (Path(__file__).parent / "fixtures" / "temple.csv").read_text(
        encoding="utf-8"
    )
    assert "sample_key" in fixture_text
    assert "Heart Abyss" not in fixture_text
```

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_harness.py -q`

Expected: FAIL because the package and entry point do not exist.

**Step 3: Create the minimal package and test configuration.**

```ini
# loc_kit_ingest/pytest.ini
[pytest]
addopts = -q
pythonpath = ..
testpaths = tests
```

```python
# loc_kit_ingest/cli.py
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.parse_args(argv)
    return 0
```

```python
# loc_kit_ingest/__main__.py
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

`__init__.py` is empty except for the required copyright/SPDX header. Task 7
expands this smoke-test parser to the only supported CLI contract.

Create tiny, synthetic CSV/profile pairs:

- Temple: header, `id-ignore` label row, blank separator, semantic key,
  `Character`, `ru`/`en`, an Id-like reference and a string containing `[shake]`,
  `<color=#E3BA59>`, `{value:cond:1}` and `&#13;`.
- Terms: language header, language-label skip row, two named pair regions,
  section → term → description rows, and `ru`/`en`/`ja` values.
- Do not copy identifiers, prose, names, or row fragments from Downloads.

**Step 4: Run the harness.**

Run: `cd loc_kit_ingest && uv run pytest`

Expected: standalone pytest uses this `pytest.ini`; no Django settings/database
error occurs.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/
git commit -m "chore(loc-ingest): scaffold standalone test harness"
```

---

## Task 1: Add immutable domain records and typed diagnostics

**Files:**

- Create: `loc_kit_ingest/model.py`
- Test: `loc_kit_ingest/tests/test_model.py`

**Step 1: Write failing tests for the normalized boundary.**

```python
from loc_kit_ingest.model import (
    Diagnostic,
    GlossaryTerm,
    Severity,
    SkippedRow,
    StringUnit,
)


def test_string_unit_keeps_text_and_metadata_verbatim():
    unit = StringUnit(
        key="sample_key",
        values={"ru": " leading\tтекст\r\n", "en": " leading\ttext\r\n"},
        comments=("Character: Sample",),
        references=("42",),
        row=3,
    )
    assert unit.values["ru"] == " leading\tтекст\r\n"
    assert unit.comments == ("Character: Sample",)


def test_glossary_term_keeps_explanations_per_language():
    term = GlossaryTerm(
        context="characters.hero",
        values={"ru": "Герой", "en": "Hero"},
        explanations={"ru": "Описание", "en": "Description"},
        section="Characters",
        term_row=4,
        description_row=5,
    )
    assert term.explanations == {"ru": "Описание", "en": "Description"}


def test_diagnostic_and_skip_are_typed():
    diagnostic = Diagnostic(
        Severity.ERROR, "profile.unknown_field", "UI", "Sheet", 2, "bad"
    )
    skipped = SkippedRow("Temple", "Temple", 9, "blank")
    assert diagnostic.severity is Severity.ERROR
    assert skipped.reason == "blank"
```

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_model.py -q`

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Implement only the shared records.**

Use frozen `@dataclass` records and a `Severity` `StrEnum` with exactly
`ERROR = "error"` and `WARNING = "warning"`. Create:

```text
Diagnostic(severity, code, component, sheet, row, message)
SkippedRow(component, sheet, row, reason)
StringUnit(key, values, comments, references, row)
GlossaryTerm(context, values, explanations, section, term_row, description_row)
ParseResult(component, kind, units, diagnostics, skipped_rows)
```

Use tuples for `comments`, `references`, diagnostics and skipped rows after
construction. Do not add mutable defaults, a generic `dict` payload, or a
second error type for each parser.

**Step 4: Run the focused tests.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_model.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/model.py loc_kit_ingest/tests/test_model.py
git commit -m "feat(loc-ingest): add normalized units and diagnostics"
```

---

## Task 2: Parse and validate the strict versioned profile

**Files:**

- Create: `loc_kit_ingest/profile.py`
- Test: `loc_kit_ingest/tests/test_profile.py`

**Step 1: Write failing profile-contract tests.**

Cover all of these with isolated JSON objects written to `tmp_path`:

```python
import json
import pytest

from loc_kit_ingest.profile import ProfileError, load_profile


def test_profile_requires_the_only_supported_schema_version(tmp_path):
    path = tmp_path / "kit.loc-ingest.json"
    path.write_text(
        json.dumps({"schema_version": 2, "components": []}), encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="schema_version"):
        load_profile(path)


def test_profile_rejects_unknown_fields(tmp_path, valid_profile):
    valid_profile["surprise"] = True
    path = tmp_path / "kit.loc-ingest.json"
    path.write_text(json.dumps(valid_profile), encoding="utf-8")
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


def test_profile_rejects_unsafe_or_casefold_colliding_component_names(
    valid_profile, tmp_path
):
    valid_profile["components"][0]["component"] = "../escape"
    path = tmp_path / "kit.loc-ingest.json"
    path.write_text(json.dumps(valid_profile), encoding="utf-8")
    with pytest.raises(ProfileError, match="component"):
        load_profile(path)
```

Also test missing profile, malformed JSON, non-object root, missing/unknown
kind, missing `source_lang`, source language absent from `languages`, duplicate
language code/column, bad BCP-47 tag, invalid 1-based index, exact header
metadata, duplicate sheets/components, duplicate output/archive name,
`initial_target_languages` containing source, PO-only fields on TBX,
TBX-only fields on PO, defaulted optional arrays, invalid pair range (odd,
overlap, gap, section outside range), and `key_language` missing from languages.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_profile.py -q`

Expected: FAIL because `profile.py` is absent.

**Step 3: Implement schema-specific profile records and a strict loader.**

Use standard `json`, `dataclasses` and small `require_*` helpers, not Pydantic or
JSON Schema. Reject unknown fields at every nesting level. Convert public
1-based row/column numbers to private 0-based values exactly once in the loader;
diagnostics retain the original 1-based values.

The loader returns these immutable records:

```text
LanguageColumn(code, xml_lang, column, header)
KeyColumn(column, header)
MetadataColumn(column, name, header)
KeyedGrammar(skip_rows, allow_blank_rows)
PairRegion(section_row, first_term_row, last_description_row)
PairsGrammar(skip_rows, regions)
ComponentProfile(sheet, component, kind, source_lang, header_row,
                 first_data_row: int | None, languages, key: KeyColumn | None,
                 comments: tuple[MetadataColumn, ...],
                 references: tuple[MetadataColumn, ...],
                 grammar: KeyedGrammar | PairsGrammar,
                 key_language: str | None,
                 initial_target_languages: tuple[str, ...])
Profile(schema_version, components)
```

Rules to enforce:

- Root only has `schema_version` and non-empty `components`.
- Kind is exactly `po` or `tbx`; PO only accepts keyed grammar and TBX only
  accepts `term-description-pairs`.
- Match the closed schema in `docs/guides/loc-kit-ingest.md` exactly. Fields
  specific to the other kind are errors, not ignored configuration; omitted
  optional PO arrays and grammar defaults are materialized once by the loader.
- Every configured header is exact. `header: ""` is valid for an intentionally
  blank source heading such as Temple's metadata column.
- `component` matches the safe identifier expression. Its case-folded value and
  all generated archive names are unique.
- `code` matches `[A-Za-z][A-Za-z0-9_]*` and is unique after `casefold()` within
  its component, since it becomes a filename. `xml_lang` matches
  `[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*` and is never derived from `code`.
- A PO component has key + first data row. A TBX component has pair regions,
  `key_language`, and non-empty `initial_target_languages` excluding source.
- Pair regions are even-length ranges, term/description pairs do not overlap,
  and every declared section and pair row is after the header.

`ProfileError` contains a `Diagnostic` or diagnostic fields. It must not print,
create files, or return a partially valid profile.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_profile.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/profile.py loc_kit_ingest/tests/test_profile.py
git commit -m "feat(loc-ingest): require strict versioned input profiles"
```

---

## Task 3: Read source files and validate profile-to-sheet headers

**Files:**

- Create: `loc_kit_ingest/reader.py`
- Test: `loc_kit_ingest/tests/test_reader.py`

**Step 1: Write failing reader tests.**

```python
from loc_kit_ingest.reader import read_sheets, validate_sheet_headers


def test_csv_preserves_quoted_newlines_bom_and_trailing_spaces(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes(b'\xef\xbb\xbfid,ru,en\r\nkey," x\r\ny ",text \r\n')
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", " x\r\ny ", "text "]


def test_xlsx_preserves_multiline_text(ui_xlsx):
    rows = read_sheets(ui_xlsx)["UI"]
    assert rows[2][2] == "Line 1\nLine 2"


def test_header_mismatch_is_an_error(temple_profile, temple_rows):
    temple_rows[0][2] = "Russian"
    diagnostics = validate_sheet_headers(temple_profile, temple_rows)
    assert [(item.code, item.row) for item in diagnostics] == [("header.mismatch", 1)]
```

Add tests for unsupported suffix, decode error, corrupt XLSX, missing selected
sheet, unexpected nonempty XLSX sheet, short configured data row and an expected
blank header.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py -q`

Expected: FAIL because the reader is absent.

**Step 3: Implement `reader.py`.**

- Read CSV and TSV with `newline=""`, `encoding="utf-8-sig"`, and the exact
  delimiter determined by suffix.
- Read XLSX with `load_workbook(read_only=True, data_only=True)` and close it in
  `finally`; map `None` to `""` but otherwise use `str(cell)` unchanged.
- Return `dict[str, list[list[str]]]` in workbook order. Do not trim or infer.
- Validate after all files are read: the profile covers every nonempty sheet,
  each selected sheet exists, headers are exact, and all referenced columns are
  in bounds. Return `Diagnostic(ERROR, ...)` values rather than writing output.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/reader.py loc_kit_ingest/tests/test_reader.py
git commit -m "feat(loc-ingest): read kits and validate declared sheet headers"
```

---

## Task 4: Parse explicit keyed rows for PO components

**Files:**

- Create: `loc_kit_ingest/parser.py`
- Test: `loc_kit_ingest/tests/test_parser_po.py`

**Step 1: Write failing PO-parser tests.**

```python
from loc_kit_ingest.parser import parse_component


def test_keyed_parser_keeps_markup_whitespace_comments_and_references(
    temple_profile, temple_rows
):
    result = parse_component(temple_profile, temple_rows)
    unit = result.units[0]
    assert unit.key == "sample_key"
    assert unit.values["ru"] == " leading [shake]текст[/shake] "
    assert unit.comments == ("Character: Sample",)
    assert unit.references == ("42",)
    assert result.diagnostics == ()


def test_keyed_parser_records_explicit_and_blank_skips(temple_profile, temple_rows):
    result = parse_component(temple_profile, temple_rows)
    assert {(skip.row, skip.reason) for skip in result.skipped_rows} == {
        (2, "profile_skip"),
        (4, "blank"),
    }


def test_duplicate_key_blocks_the_component(temple_profile, temple_rows):
    temple_rows.append(list(temple_rows[2]))
    result = parse_component(temple_profile, temple_rows)
    assert any(item.code == "po.duplicate_key" for item in result.diagnostics)
```

Add tests for empty/missing source, short row, unexpected nonblank unkeyed row,
empty target (warning), target equal source (warning), Cyrillic-majority
English/Latin-majority Russian (one warning per cell, never double-report), and
a note-like target while source is empty.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_po.py -q`

Expected: FAIL because `parse_component` is absent.

**Step 3: Implement only the keyed grammar.**

Dispatch by `ComponentProfile.kind`; reject a profile/component mismatch. For a
PO component:

1. Iterate physical source rows after `first_data_row`.
2. Record exact `skip_rows` as `SkippedRow(..., "profile_skip")`.
3. Record blank rows only when `allow_blank_rows`; otherwise add
   `grammar.unexpected_row` error.
4. Every other row must contain the declared key and source cell. Construct a
   `StringUnit` from exact values, profile comments and references.
5. Maintain a key set; duplicate key is an error, never a suffix.
6. Emit warnings without rewriting values. Script checks only compare `en` with
   Cyrillic and `ru` with Latin and do not run a second overlapping check.

Do not call `.strip()` when storing keys or text. Use a private `is_blank()`
only for structural tests.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_po.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/parser.py loc_kit_ingest/tests/test_parser_po.py
git commit -m "feat(loc-ingest): parse deterministic keyed PO rows"
```

---

## Task 5: Parse explicit Terms pair regions with per-language explanations

**Files:**

- Modify: `loc_kit_ingest/parser.py`
- Test: `loc_kit_ingest/tests/test_parser_tbx.py`

**Step 1: Write failing glossary-parser tests.**

```python
from loc_kit_ingest.parser import parse_component


def test_glossary_pairs_preserve_source_and_target_explanations(
    terms_profile, terms_rows
):
    result = parse_component(terms_profile, terms_rows)
    term = result.units[0]
    assert term.context == "characters.hero"
    assert term.values == {"ru": "Герой", "en": "Hero", "ja": "ヒーロー"}
    assert term.explanations["ru"] == "Источник описание"
    assert term.explanations["en"] == "Target explanation"
    assert term.term_row == 4 and term.description_row == 5


def test_orphan_pair_and_uncovered_nonblank_row_are_errors(terms_profile, terms_rows):
    terms_rows[4] = []
    result = parse_component(terms_profile, terms_rows)
    assert {item.code for item in result.diagnostics} >= {
        "tbx.orphan_description",
        "grammar.uncovered_row",
    }
```

Add tests for missing section/source term/source explanation, missing configured
initial target term, duplicate context, an empty `key_language` cell, invalid
non-ASCII-only slug, an unknown extra nonblank row, gap/overlap detection from
the profile, preservation of markup/internal newlines, and a fatal
`tbx.unsupported_outer_whitespace` diagnostic for leading or trailing whitespace
in every term or explanation cell.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -q`

Expected: FAIL because the TBX grammar branch does not exist.

**Step 3: Add only the `term-description-pairs` branch.**

- Build a set of every covered section/term/description row from validated
  regions. A nonblank data row outside it is an error.
- A section's source text must be nonblank. It is used only for the context
  prefix, not emitted as a glossary term.
- Read alternating term/description rows exactly. Source term and source
  explanation are required. The configured `initial_target_languages` require
  a target term on every entry; target explanations can be empty.
- Before storing a term or explanation, reject leading/trailing whitespace with
  `tbx.unsupported_outer_whitespace`. Translate Toolkit's `tbxunit.addnote()`
  strips it; rejecting preserves the no-silent-data-loss contract while still
  preserving all internal whitespace, newlines and markup.
- Create `context = slug(source_section) + "." + slug(key_language_term)`.
  `slug()` uses NFKD ASCII transliteration, `[a-z0-9_-]` and lower case; an
  empty result or collision is an error. Do not append a numeric suffix.
- Store descriptions in `GlossaryTerm.explanations` keyed by language. This is
  the contract used by the TBX writer and LLM payload test.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/parser.py loc_kit_ingest/tests/test_parser_tbx.py
git commit -m "feat(loc-ingest): parse profiled glossary term-description pairs"
```

---

## Task 6: Render PO and bilingual TBX, then parse them back

**Files:**

- Create: `loc_kit_ingest/writer.py`
- Test: `loc_kit_ingest/tests/test_writer.py`

**Step 1: Write failing artifact tests.**

```python
from translate.storage.pypo import pofile

from loc_kit_ingest.writer import render_component, validate_rendered_component


def test_po_roundtrip_preserves_exact_text_and_metadata(tmp_path, po_component):
    paths = render_component(po_component, tmp_path)
    source = pofile.parsefile(str(paths["ru"]))
    unit = next(item for item in source.units if item.getid() == "sample_key")
    assert unit.target == " leading\t[shake]текст[/shake]\r\n"
    assert unit.getnotes("developer") == "Character: Sample"
    assert unit.getlocations() == ["42"]
    assert validate_rendered_component(po_component, tmp_path) == ()


def test_tbx_has_two_languages_and_both_explanations(tmp_path, tbx_component):
    paths = render_component(tbx_component, tmp_path)
    assert set(paths) == {"en", "ja"}
    xml = paths["en"].read_text(encoding="utf-8")
    assert 'xml:lang="ru"' in xml and 'xml:lang="en"' in xml
    assert "Источник описание" in xml
    assert "Target explanation" in xml
```

Add tests for `pt_PT`/`zh_Hans`/`zh_Hant` becoming their profile BCP-47 tags,
no TBX named after source, no file for an unconfigured target language, invalid
path/duplicate artifact rejection, and parse-back corruption diagnostics.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_writer.py -q`

Expected: FAIL because the writer is absent.

**Step 3: Implement `writer.py`.**

PO branch:

```python
store = pofile()
store.settargetlanguage(language.code)
unit = store.addsourceunit(string_unit.key)
unit.target = string_unit.values.get(language.code, "")
```

Write `developer` notes and locations to the source-language PO template. Use
one PO per declared language and preserve an empty target as an empty `msgstr`.
Parse every rendered PO back with `pofile` and compare key, target, comments,
references and exact values against the in-memory units.

TBX branch:

```python
store = tbxfile(
    sourcelanguage=source_column.xml_lang,
    targetlanguage=target_column.xml_lang,
)
unit = store.addsourceunit(term.values[source_code])
unit.setid(term.context)
unit.settarget(term.values[target_code])
unit.addnote(term.explanations[source_code], origin="definition")
unit.addnote(term.explanations.get(target_code, ""), origin="translator")
store.addheader()
```

Write exactly one `tbx/<target-code>.tbx` for each
`initial_target_languages` item. Use `xml_lang` from the profile, never a
converted Weblate code. Parse every TBX back with `tbxfile`, checking source,
target, context and definition/translator notes. Renderers return paths and
`Diagnostic` values; they do not create reports or publish paths.
  The profile parser has already rejected outer whitespace in TBX notes, because
  `tbxunit.addnote()` strips it. Do not use private XML mutation to work around
  that library behavior.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_writer.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/writer.py loc_kit_ingest/tests/test_writer.py
git commit -m "feat(loc-ingest): render validated PO and bilingual TBX files"
```

---

## Task 7: Orchestrate all components with staged atomic publication

**Files:**

- Create: `loc_kit_ingest/pipeline.py`
- Modify: `loc_kit_ingest/cli.py`
- Modify: `loc_kit_ingest/__main__.py`
- Test: `loc_kit_ingest/tests/test_pipeline.py`
- Test: `loc_kit_ingest/tests/test_cli.py`

**Step 1: Write failing success and atomic-failure tests.**

```python
from loc_kit_ingest.cli import main


def test_cli_publishes_all_components_and_archives(tmp_path, kit_with_profile):
    kit, profile = kit_with_profile
    output = tmp_path / "out"
    assert (
        main([str(kit), "--profile", str(profile), "--out", str(output), "--zip"]) == 0
    )
    assert (output / "Temple" / "ru.po").is_file()
    assert (output / "Terms" / "tbx" / "en.tbx").is_file()
    assert (output / "Terms.zip").is_file()
    assert (output / "report.txt").is_file()


def test_late_component_error_leaves_no_artifacts(tmp_path, kit_with_profile):
    kit, profile = kit_with_profile
    output = tmp_path / "out"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace('"en"', '"missing"', 1),
        encoding="utf-8",
    )
    assert main([str(kit), "--profile", str(profile), "--out", str(output)]) == 2
    assert not output.exists()


def test_existing_output_is_never_replaced(tmp_path, kit_with_profile):
    kit, profile = kit_with_profile
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert main([str(kit), "--profile", str(profile), "--out", str(output)]) == 2
    assert sentinel.read_text(encoding="utf-8") == "keep"
```

Cover: missing/invalid profile; unknown kind/source; unsupported/malformed input;
missing sheet/header/column; a bad sheet after valid sheets; duplicate key/context;
empty kit; unpaired glossary range; writer failure; parse-back validation failure;
ZIP failure; non-existing output parent; pre-existing output; no `report.txt` on
failure; successful no-ZIP and ZIP content at expected archive paths; diagnostics
on stderr and exit status `2`.

**Step 2: Run to verify failure.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_pipeline.py tests/test_cli.py -q`

Expected: FAIL because the orchestration code is absent.

**Step 3: Implement one transaction boundary.**

`pipeline.run(kit, profile_path, output, zip_components)` must:

1. Validate that output's parent exists and output does not exist. Neither check
   mutates the filesystem.
2. Load profile, read every sheet, validate headers, parse every component and
   aggregate all diagnostics before making staging. Any `ERROR` prints the
   formatted diagnostic report to stderr and returns `2`.
3. Create `tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)` only
   after the in-memory input is clean.
4. Render all components, run parse-back validation, create optional ZIP files
   and write `report.txt` inside staging. A render/ZIP failure deletes staging,
   writes diagnostics to stderr and returns `2`.
5. Publish staging with `os.replace(staging, output)`. This is the only path that
   changes output. On a publish exception, remove staging and return `2`.
6. Print the already-published report to stdout only on success and return `0`.

`main()` accepts exactly `KIT --profile PROFILE --out OUTPUT [--zip]`; do not add
`--source-lang`, implicit profile discovery, a merge/force option, or a hidden
fallback. `argparse` handles usage errors; the pipeline handles data errors.

**Step 4: Run the focused suite.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_pipeline.py tests/test_cli.py -q`

Expected: PASS.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/pipeline.py loc_kit_ingest/cli.py loc_kit_ingest/__main__.py \
  loc_kit_ingest/tests/test_pipeline.py loc_kit_ingest/tests/test_cli.py
git commit -m "feat(loc-ingest): publish profile-driven artifacts atomically"
```

---

## Task 8: Complete the standalone regression and failure matrix

**Files:**

- Modify: `loc_kit_ingest/tests/conftest.py`
- Modify: `loc_kit_ingest/tests/fixtures/*`
- Create: `loc_kit_ingest/tests/test_regressions.py`
- Create: `loc_kit_ingest/tests/test_failure_matrix.py`

**Step 1: Add boundary fixtures and parameterized regression tests.**

The fixtures must model, not copy, the three input layouts:

| Fixture | Required boundary |
|---|---|
| Temple-like CSV | explicit label row, blank row, developer comment, every markup family, same-as-source warning, wrong-script warning, source-empty note warning |
| Terms-like CSV | language label skip, two section/pair regions, target/source explanations, target language tags, orphan/extra-row variants |
| UI-like generated XLSX | title row, Key + numeric Id, multiline text, leading/trailing spaces, tabs, CRLF, entities and Unity tags |

```python
import pytest

from loc_kit_ingest.cli import main


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        ("unknown_profile_field", "profile.unknown_field"),
        ("duplicate_key", "po.duplicate_key"),
        ("orphan_description", "tbx.orphan_description"),
        ("writer_failure", "render.failed"),
        ("zip_failure", "zip.failed"),
    ],
)
def test_every_fatal_contract_leaves_output_untouched(
    tmp_path, mutation, expected_code, mutated_kit
):
    kit, profile = mutated_kit(mutation)
    output = tmp_path / "out"
    assert (
        main([str(kit), "--profile", str(profile), "--out", str(output), "--zip"]) == 2
    )
    assert not output.exists()
```

**Step 2: Run to verify red cases.**

Run: `cd loc_kit_ingest && uv run pytest tests/test_regressions.py tests/test_failure_matrix.py -q`

Expected: initially FAIL until every diagnostic code/rollback path is implemented.

**Step 3: Fill only missing assertions or implementation defects.**

Do not add a fallback parser to satisfy a fixture. Each fixture must name its
profile grammar and expected code. Assert exact `Diagnostic.severity`, `code`,
component/sheet/row, report ordering, stdout/stderr split, no report on failure,
and existing output preservation. Add ZIP member assertions with `zipfile.ZipFile`.

**Step 4: Run all fast tests.**

Run: `cd loc_kit_ingest && uv run pytest`

Expected: PASS; tests do not import Django or require PostgreSQL.

**Step 5: Commit.**

```bash
git add loc_kit_ingest/tests/
git commit -m "test(loc-ingest): cover boundary regressions and atomic failures"
```

---

## Task 9: Add automated Weblate PO/TBX and LLM-payload contract tests

**Files:**

- Create: `weblate/trans/tests/test_loc_kit_ingest_contract.py`
- Modify: `loc_kit_ingest/tests/conftest.py` only if a reusable synthetic writer
  fixture is needed

**Step 1: Write failing Weblate-level tests.**

Use anonymized in-memory units or output generated into `tmp_path`, never
Downloads. Match `ComponentTestCase`/`create_po_mono`/`create_tbx` patterns in
`weblate/trans/tests/utils.py`.

Required tests:

1. Generated source PO + a translation PO load through an actual
   `po-mono` component. Assert `Unit.context == key`, source/target values,
   developer comment, numerical reference and exact markup survive the Weblate
   parser.
2. Generated `tbx/en.tbx` with source `ru` and target `en` loads through an
   actual TBX glossary component configured with `tbx/*.tbx`, no base template,
   and source language `ru`. Assert context, source, target,
   `source_explanation` and target `explanation` on Weblate units.
3. Invoke the existing glossary payload construction path on the imported target
   unit. Assert the structured entry contains exactly source/target and both
   `source_explanation` and `target_explanation`, rather than checking rendered
   LLM prose.
4. Confirm no source-language TBX filename is accepted by the generated output
   contract and no target language is inferred from a multilingual bundle.

**Step 2: Run to verify failure.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`

Expected: FAIL until the generated formats satisfy the real Weblate contract.

**Step 3: Implement only contract-test setup and fix format defects.**

Reuse Weblate test helpers and real format classes; do not mock parser behavior
or reimplement Weblate interpretation in the test. Keep the test separate from
fast standalone tests because it deliberately loads the Django test settings and
test database.

**Step 4: Run the contract test again.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`

Expected: PASS.

**Step 5: Commit.**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py loc_kit_ingest/
git commit -m "test(loc-ingest): verify generated files through Weblate"
```

---

## Task 10: Add an opt-in live Routed LLM smoke and manual import runbook

**Files:**

- Create: `weblate_customization/tests/test_loc_kit_ingest_live.py`
- Modify: `docs/guides/loc-kit-ingest.md` only if the observed UI labels differ

**Step 1: Write the skipped-by-default live test.**

```python
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LOC_INGEST_LIVE_LLM") != "1",
    reason="set LOC_INGEST_LIVE_LLM=1 to spend one routed LLM request",
)


def test_imported_glossary_guides_one_routed_translation():
    """Use a temporary project/component and one synthetic source string."""
```

The setup must create its own temporary project, glossary and ordinary component;
it must not modify the existing Heart Abyss project. Use a single source/target
term and a sentence that contains it. It requires the active `routed-llm`
configuration and one valid OpenRouter key, has no retry loop, uses one request,
and is excluded from CI/default test invocation.

**Step 2: Verify default suite does not run it.**

Run: `./rundev.sh test weblate_customization/tests/test_loc_kit_ingest_live.py`

Expected: SKIPPED with the explicit opt-in reason.

**Step 3: Run the real, bounded smoke only after Task 9 passes.**

```bash
LOC_INGEST_LIVE_LLM=1 ./rundev.sh test \
  weblate_customization/tests/test_loc_kit_ingest_live.py
```

Expected: one routed response; payload contains the imported glossary source and
target/explanation fields; returned translation is nonempty. If credentials are
unavailable, record the test as skipped with the exact prerequisite, not passed.

**Step 4: Execute the manual local-Weblate smoke.**

1. Generate anonymized/real final output in a fresh `/tmp/loc-ingest-out`:

   ```bash
   uv run python -m loc_kit_ingest \
     "/path/Temple.csv" --profile "/path/Temple.loc-ingest.json" \
     --out /tmp/loc-ingest-out --zip
   ```

2. At `http://localhost:3001`, log in as `admin`/`admin`; create a disposable
   component from `Temple.zip`. Discover with `*.po`, base `ru.po`, file format
   `gettext PO file (monolingual)` and profile source language.
3. Verify unit count equals report count; key is context; Character is a
   developer comment; reference Id appears as location; markup passes
   `game-markup`.
4. Create a disposable Terms glossary from `Terms.zip`. Use `tbx/*.tbx`, empty
   base/template, `TermBase eXchange file`, profile source language and the
   glossary checkbox. Verify a source explanation and target explanation in UI.
5. Delete the disposable components after the smoke. Do not use the real project
   as a test fixture.

**Step 5: Commit.**

```bash
git add weblate_customization/tests/test_loc_kit_ingest_live.py docs/guides/loc-kit-ingest.md
git commit -m "test(loc-ingest): add opt-in glossary LLM smoke"
```

---

## Task 11: Final verification and documentation audit

**Files:**

- Modify: `docs/guides/loc-kit-ingest.md` only for observed runbook corrections
- Modify: `docs/product/plans/2026-08-06-loc-kit-ingest.md` only if implementation
  discovers a plan defect

**Step 1: Run all fast tests.**

```bash
cd loc_kit_ingest && uv run pytest
```

Expected: all standalone tests PASS.

**Step 2: Run the Weblate format contract.**

```bash
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py
```

Expected: PASS. The live test remains skipped unless explicitly opted in.

**Step 3: Run formatting/lint only on changed tracked Python files.**

```bash
uv run prek run --files \
  loc_kit_ingest/__init__.py \
  loc_kit_ingest/__main__.py \
  loc_kit_ingest/cli.py \
  loc_kit_ingest/model.py \
  loc_kit_ingest/parser.py \
  loc_kit_ingest/pipeline.py \
  loc_kit_ingest/profile.py \
  loc_kit_ingest/reader.py \
  loc_kit_ingest/writer.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  weblate_customization/tests/test_loc_kit_ingest_live.py
```

Expected: configured checks pass or only pre-existing unrelated failures remain.

**Step 4: Check documentation invariants.**

- `docs/guides/loc-kit-ingest.md` and this plan agree on profile-only parsing,
  PO/TBX division, bilingual TBX topology, `xml_lang`, atomic output and test
  layers.
- No command promises an uninstalled `loc-ingest` executable; all commands use
  `uv run python -m loc_kit_ingest`.
- No real game texts are in `loc_kit_ingest/tests/`.
- New Python files have the GPL-3.0-or-later SPDX header.

**Step 5: Commit the final integration.**

```bash
git add loc_kit_ingest/ weblate/trans/tests/test_loc_kit_ingest_contract.py \
  weblate_customization/tests/test_loc_kit_ingest_live.py \
  docs/guides/loc-kit-ingest.md docs/product/plans/2026-08-06-loc-kit-ingest.md
git commit -m "feat(loc-ingest): import profiled kits into Weblate formats"
```

---

## Test coverage map

```text
CODE PATHS                                          USER / IMPORT FLOWS
[Profile]                                           [C1 PO onboarding]
  ├─ schema/version/unknown fields [★★★]              ├─ CLI -> ZIP -> Discover [→E2E]
  ├─ components/languages/paths [★★★]                 ├─ context/comments/locations [→E2E]
  └─ grammar/range validation [★★★]                   └─ markup survives game-markup [→E2E]
[Reader]                                            [C3 Terms glossary]
  ├─ csv/tsv BOM + multiline [★★★]                    ├─ TBX filemask -> source/target [→E2E]
  ├─ xlsx sheets + multiline [★★★]                    ├─ explanations visible [→E2E]
  └─ header/sheet failure [★★★]                       └─ glossary payload fields [★★★]
[Parser]
  ├─ keyed units/skips/warnings [★★★]                [C4 Routed LLM]
  ├─ pair regions/explanations [★★★]                  ├─ deterministic payload contract [★★★]
  └─ orphan/gap/collision [★★★]                       └─ one real opt-in request [→EVAL]
[Writers]
  ├─ PO exact parse-back [★★★]                       [Failure state]
  ├─ TBX tags/explanations [★★★]                      ├─ no partial tree/report [★★★]
  └─ invalid render [★★★]                             └─ existing output untouched [★★★]
[Pipeline]
  ├─ stage/ZIP/publish [★★★]
  └─ late failure rollback [★★★]

Coverage target: every branch in standalone parsing/rendering/atomicity has a
behavior, boundary and failure test. Weblate format and one live LLM integration
are separately exercised; no unit test substitutes for either.
```

## Not in scope

- Export from Weblate back to the engine format.
- Spreadsheet/Google Sheets write-back or a two-way sync.
- Merge/retry/idempotent re-import after Weblate owns translations.
- Auto-detection, profile inference, guessing row semantics, or arbitrary
  third-party spreadsheet layouts without a reviewed profile.
- Publishing this local tool as an independent package or binary.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | Not run | Not needed for local ingestion tooling |
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | TIMEOUT | Both full-plan read-only attempts timed out; no Codex finding was accepted |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 18 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | Not run | No product UI change |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | Not run | Standalone harness and exact commands included |

**CODEX:** Two full-plan independent-review attempts timed out; this is not approval.

**VERDICT:** ENG CLEARED - ready to implement; Codex outside voice is non-gating and unavailable.
NO UNRESOLVED DECISIONS
