# Keyless loc-kits and universal TBX glossary intake - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Preserve the existing deterministic PO-kit import while allowing an explicitly selected CSV, TSV, or XLSX glossary table to become a validated bilingual TBX glossary component, regardless of whether its records occupy one row, alternating rows, or another fixed repeating row layout.

**Architecture:** `loc_kit_ingest` remains a standalone, network-free conversion engine. It gains a strict profile v2 and one declarative `record-map` TBX grammar. Weblate owns the optional OpenRouter proposal client, temporary upload draft, rate limit, preview workflow, and final component creation. The LLM never parses or imports data: it proposes a JSON profile from a bounded structural sample, and the existing local profile/parser/writer pipeline validates the proposal against every cell before a component can be created.

**Tech stack:** Python 3.13, Django, Celery, pytest, translate-toolkit (`tbxfile`), `httpx2` through Weblate's outbound HTTP helpers, OpenRouter structured outputs, Bootstrap/jQuery templates, Docker development stack.

---

## Approved product decisions

| Decision | Contract |
| --- | --- |
| Input formats | CSV, TSV, XLSX only. ZIP retains its existing behavior; DOCX, PDF, images, OCR, and Google Sheets are out of scope. |
| Entry point | The existing component-upload form is used. Analysis starts only when the existing **Use as glossary** field is checked. Ordinary loc-kit tables remain local and never reach an LLM. |
| Sheet scope | One selected worksheet creates one glossary component. Multi-sheet workbooks are never merged or batch-created. |
| LLM role | Proposal only. OpenRouter returns strict JSON, then local code runs profile validation, parsing, rendering, and parse-back validation. No model output can execute code or create a component directly. |
| Provider | Separate, site-wide OpenRouter configuration and key. Do not reuse `RoutedLLMTranslation` or its MT configuration. Users cannot select a model, key, endpoint, or project-level override. |
| Data sent externally | A deterministic structural sample only: sheet metadata, row/column occupancy, headers, and capped cell samples. The complete file remains local. |
| Resource bounds | At most 128 KiB of serialized sample; `RATELIMIT_LOC_KIT_ANALYSIS_*` defaults to three analysis requests per session per hour. Excess data requires a manually supplied profile. |
| Profile correction | Preview offers the proposed JSON as a download and accepts a corrected `.loc-ingest.json` upload. There is no separate mapping-editor language. |
| Draft lifetime | A session-bound, owner-bound temporary draft lasts at most one hour. It is deleted on create, cancel, or periodic cleanup. |
| Mapping grammar | Profile v2 uses one generic `record-map` grammar. Existing v1 `term-description-pairs` profiles remain readable unchanged. |
| Glossary semantics | Import source/target terms and source/target notes. Do not infer or import `forbidden`, `read-only`, or `terminology` flags. A nonempty unmapped cell is an error, never discarded. |
| Required values | A v2 import requires one source language and at least one initial target language. Source-only glossary imports are explicitly unsupported in this workflow. |
| Context identity | Context is a stable collision-free encoding of `(section, source term)` and may contain Unicode. It is not an ASCII slug and does not require English or another Latin-script language. |
| Publication gate | Every new glossary import must complete `render -> parse-back -> equality validation` before the Weblate component wizard may proceed. The existing PO UI path remains unchanged. |

## Scope boundaries

Included:

- Keyless PO-kit support already planned in Part A.
- Profile v2 `record-map` parsing for glossary tables.
- Optional OpenRouter profile suggestion and manual profile fallback.
- A temporary, authenticated UI workflow for sheet selection, preview, correction, cancellation, and creation.
- TBX rendering, parse-back validation, Weblate contract tests, configuration/security documentation, and a real workbook smoke test.

Excluded:

- LLM inference for ordinary PO-kit uploads.
- Automatic format sniffing, automatic external transmission, arbitrary user-supplied endpoints, or user-supplied API keys.
- Source-only glossary imports, glossary flags, variants, TM import, DOCX/PDF/OCR, sheet merging, and batch component creation.
- Saving uploaded glossary files or LLM prompts as a durable product record.

## Current implementation facts

Read these before implementation. They constrain the change.

1. `loc_kit_ingest/` is standalone: `reader.py -> infer.py -> profile.py -> parser.py -> writer.py`. It has no Django imports and must remain deterministic and network-free.
2. `loc_kit_ingest/profile.py` currently accepts only `schema_version = 1`; a TBX component accepts only `grammar.type == "term-description-pairs"` and requires `key_language` plus nonempty `initial_target_languages`.
3. `loc_kit_ingest/parser.py` hardcodes alternating term/description rows, derives contexts through ASCII-only `_slug`, and rejects any uncovered nonblank row. The last invariant is correct and must remain.
4. `loc_kit_ingest/writer.py` writes one bilingual TBX per target language and already parse-backs rendered TBX in `validate_rendered_component`.
5. `weblate/utils/views.py:create_component_from_kit` currently converts a table synchronously into PO files in one `TemporaryDirectory`, creates a local repository immediately, and returns PO-only `kit_info`.
6. `weblate/trans/views/create.py:CreateFromZip` moves a converted upload straight to the final component form. Its initial values are not a security boundary; a glossary draft must bind format, source language, and glossary intent again at confirmation.
7. `ComponentZipCreateForm` already inherits `is_glossary` from `ComponentNameForm`, but its field order and help text still describe only PO inference.
8. `TBXFormat` is bilingual, supports context and explanations, and requires an empty template. `Component.is_glossary` forces `manage_units` and `new_lang="none"`.
9. `weblate/glossary/models.py:get_glossary_units` matches glossary components by source language. A wrong source language produces a valid but useless glossary.
10. `weblate/utils/requests.py:fetch_validated_url` is the required outbound HTTP path. The fixed OpenRouter target must not bypass Weblate's routing and validation helpers.
11. `docs/security/threat-model.rst:833-845` requires an update when adding an outbound integration class or public surface.

### Verified correction to the old terminology design

The old plan required a Latin-script `key_language` because `_slug` drops Cyrillic and CJK. That is an implementation limitation, not a TBX or Weblate requirement. A direct Translate Toolkit round trip preserved the context `Раздел.Термин`; Weblate stores `Unit.context` in a `TextField` (`weblate/trans/models/unit.py:645-647`). Profile v2 must remove the ASCII-context assumption rather than encode it into inference.

---

## Data contracts

### Profile compatibility

- `schema_version: 1` remains valid with its existing closed-schema interpretation. Its runtime context identity is deliberately upgraded to the Unicode-safe helper in Task B2.
- `schema_version: 2` adds only the TBX `record-map` grammar. Do not reinterpret a v1 profile as v2.
- Both versions retain the closed-schema rule: unknown fields fail with a stable `profile.*` diagnostic.
- The UI accepts exactly one TBX component for the selected sheet. Its `sheet` must equal the selected draft sheet, and its component name is generated by the server, not trusted from the LLM or corrected profile.

### Profile v2 `record-map`

A record is a repeated row group. `record_stride` is the number of rows in that group; language terms are read at `term_row_offset`; note fields name their own offset and column. This represents a one-row-per-term spreadsheet with `stride: 1`, and an alternating term/description spreadsheet with `stride: 2`.

A record's section (its domain) comes from exactly one of two sources, never both:

- `grammar.section_field` - a column read once per record. This is the preferred shape: a flat table where every row carries its own domain.
- `region.section_row` plus `region.section_column` - a caption cell above a block of records. This supports spreadsheets that group terms under headings instead of repeating the domain.

A component with neither has records without a section.

```json
{
  "schema_version": 2,
  "components": [
    {
      "sheet": "Glossary",
      "component": "CoL4-Glossary",
      "kind": "tbx",
      "source_lang": "ru",
      "header_row": 1,
      "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 3, "header": "en"}
      ],
      "grammar": {
        "type": "record-map",
        "skip_rows": [],
        "regions": [
          {"first_record_row": 2, "last_record_row": 7, "record_stride": 1}
        ],
        "term_row_offset": 0,
        "section_field": {"column": 1, "header": "domain", "row_offset": 0},
        "notes": [
          {"scope": "source", "column": 4, "header": "note_ru", "row_offset": 0},
          {
            "scope": "target",
            "language": "en",
            "column": 5,
            "header": "note_en",
            "row_offset": 0
          }
        ]
      },
      "initial_target_languages": ["en"]
    }
  ]
}
```

`parse_profile` validates the structural requirements below. `validate_sheet_headers` validates the declared language and note headers against the selected sheet before any data row is parsed:

- `record_stride` is a positive integer; each `row_offset` is in `[0, record_stride)`.
- A region has an inclusive range divisible by its stride; regions, section rows, and skip rows do not overlap ambiguously.
- A component declares `grammar.section_field` or region section cells, never both. `section_row` and `section_column` occur together, are in range, and a missing block caption is represented by omitting both fields. `section_field.row_offset` obeys the same `[0, record_stride)` rule as any other field, and its column may not collide with a language or note column.
- Language and note header declarations are nonempty strings; `validate_sheet_headers` requires exact equality with the actual header row.
- A target note names an initial target language; source notes have no `language` field.
- No term or note field aliases the same `(row_offset, column)` unexpectedly.
- The source is present in `languages`; targets are nonempty, unique, and do not contain the source.
- v2 has no `key_language`; v1 retains it only for compatibility.

### Record-map parse rules

For each record:

1. Read the section from `section_field` at its offset, or inherit the enclosing region's caption cell. A blank `section_field` cell means the record has no section; it is not an error and it is not inherited from the previous record.
2. Read source and target terms from declared language columns at `term_row_offset`.
3. Read declared notes, preserving order. Group source notes and target notes separately.
4. Require a nonblank source term and a nonblank term for every initial target language. Notes are optional.
5. Reject leading or trailing whitespace in a TBX term or note. The existing `tbx.unsupported_outer_whitespace` error remains: Translate Toolkit trims such values on write, so accepting them would silently lose data.
6. Build context with a canonical collision-free serialization of `(section, source term)`, for example compact JSON with `ensure_ascii=False`. Duplicate contexts fail. Do not use `_slug`.
7. Mark all section, term, and declared note cells as consumed. A populated data cell not consumed by a declared field fails as `tbx.unmapped_cell`. A caption row may contain translated section labels in declared language columns; those labels are section metadata and are never made into terms.
8. Retain the existing `grammar.uncovered_row` protection for any nonblank row after the header not covered by a region, caption row, or explicit skip.

A model response that classifies a column as a glossary flag is rejected as unsupported. A user may only map descriptive content to a note field; the grammar deliberately has no flag role.

### LLM response contract

The response is a strict JSON object. A successful candidate has this shape:

```json
{
  "status": "profile",
  "profile": {
    "schema_version": 2,
    "components": []
  },
  "assumptions": ["short, user-visible facts"],
  "reason": null
}
```

For `status: "unsupported"`, `profile` is `null` and `reason` is a nonempty string. The OpenRouter JSON Schema must require these fields, forbid additional properties, and use `response_format.type = "json_schema"` with `strict: true`. The local `parse_profile` call is mandatory even after structured-output validation. An unsupported status, invalid JSON, invalid profile, network failure, timeout, or unsuitable model produces a recoverable preview error and never creates a component.

### Structural-sample contract

The sample builder receives normalized rows only after the local reader has successfully opened the selected CSV, TSV, or XLSX sheet. It first builds a local signature for every row: nonempty column indexes, cell count, and bounded value lengths. It serializes a run-length encoding of those signatures plus:

- sheet name, row count, column count, and header candidates;
- deterministic representatives: first and last row of each contiguous signature run, every unique signature, candidate header rows, section-like rows, and evenly spaced remaining rows while capacity allows; and
- UTF-8-safe cell excerpts with an explicit truncation marker and aggregate omitted-row/cell counts.

Only cell values may be truncated. If the complete signature encoding or the representative payload cannot fit in 128 KiB, return a local `loc_kit.sample_too_large` diagnostic and offer manual profile upload. Never retry by transmitting the entire sheet.

---

## Implementation conventions

- Work in the main checkout. Do not use a worktree: the shared Docker stack publishes fixed ports.
- New Python files use the repository copyright and GPL-3.0-or-later SPDX header, `from __future__ import annotations`, and project typing conventions.
- All visible strings use Django i18n helpers or `{% translate %}` / `{% blocktranslate %}`.
- Keep `loc_kit_ingest` free of Django, HTTP, credentials, environment reads, and LLM prompts.
- Use `weblate.utils.requests.fetch_validated_url` for the fixed provider request. Do not use raw `requests`, raw `httpx2`, or a user-configurable URL.
- Test external calls with Weblate's `http_mock`; no test uses a real OpenRouter key.
- Before each container test that imports the standalone package, copy it into the mounted deployment location:

  ```bash
  cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
  ```

- Run targeted tests after each task. Run formatting, type checks, and the wider verification once at the end.

---

# Part A - Keyless PO kits

Part A keeps ordinary PO intake independent of glossary analysis. It deliberately does not invoke the LLM or the record-map grammar.

## Task A1 - Establish current PO inference regression tests

**Files:**

- Create: `loc_kit_ingest/tests/test_infer.py`

Write deterministic tests for current keyed inference:

- a regular `key,ru,en` header uses column 0 as the key;
- `id` remains a string-id column even though it is a registered Indonesian language code;
- numeric, empty, and under-filled recognized-language columns are demoted with an observable note;
- a caption row becomes an explicit `skip_rows` entry;
- absent keys fail; and
- `infer_profile` produces one v1 component per sheet.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_infer.py -v
```

The tests must describe current behavior before changing `infer.py`.

## Task A2 - Specify and implement keyless PO detection

**Files:**

- Modify: `loc_kit_ingest/infer.py`
- Modify: `loc_kit_ingest/tests/test_infer.py`

Add red tests for a header beginning `ru,en,ja` whose first column contains nonnumeric terms. Implement one narrow rule:

- column 0 is promoted into language candidates only when its header resolves to a language code, is not in `_KEY_HEADER_DENYLIST = {"id"}`, and the column holds nonempty nonnumeric data;
- it remains the explicit PO key column, so source text and key are identical;
- a note records that column 0 is both key and language;
- `--profile` remains the escape hatch for genuinely ambiguous first columns.

Do not add glossary detection or any project-name heuristic here.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_infer.py -v
```

## Task A3 - Cover the unchanged Weblate PO path

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`
- Modify: `weblate/trans/forms.py`
- Modify: `weblate/templates/trans/component_create.html`

Add a contract test that uploads a generic keyless CSV through `create_component_from_kit`, asserts source language, source PO, target POs, source-term keys, and the explanatory note. Update form/template text to say that the first column is a key unless it is itself a recognized language column.

The checkbox used by the generic glossary flow must not affect this test.

**Run:**

```bash
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k keyless
```

---

# Part B - Profile v2 and deterministic glossary conversion

## Task B1 - Add profile v2 without weakening v1

**Files:**

- Modify: `loc_kit_ingest/profile.py`
- Create: `loc_kit_ingest/tests/test_profile_v2.py`
- Modify: `loc_kit_ingest/tests/test_parser_tbx.py`

Write failing tests before implementation for:

- a valid v2 one-row glossary with a per-record `section_field`, matching the example contract above;
- a valid v2 stride-two glossary with region caption cells, representing the old alternating-row shape;
- an existing v1 `terms.loc-ingest.json` loading with identical component fields;
- unknown v2 fields, both section styles declared at once, incompatible v1/v2 fields, invalid offsets/strides, overlapping regions, mismatched headers, duplicate field locations, invalid target-note language, omitted target languages, and source-in-targets all failing with stable diagnostics.

Implement immutable profile records for `RecordMapGrammar`, `RecordRegion`, `SectionField`, and `NoteField`. Extend `ComponentProfile` as a tagged union so v1 `PairsGrammar` and v2 `RecordMapGrammar` remain distinguishable. Do not loosen v1 validation to accommodate v2.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_profile_v2.py tests/test_parser_tbx.py -v
```

## Task B2 - Normalize TBX note data and Unicode context identity

**Files:**

- Modify: `loc_kit_ingest/model.py`
- Modify: `loc_kit_ingest/parser.py`
- Modify: `loc_kit_ingest/writer.py`
- Modify: `loc_kit_ingest/tests/test_parser_tbx.py`
- Modify: `loc_kit_ingest/tests/test_writer.py`

Refactor `GlossaryTerm` so it represents the actual TBX output contract rather than descriptions indexed by every language column:

- `source_explanation` is the deterministic concatenation of declared source notes;
- `target_explanations` maps initial target languages to deterministic concatenations of their declared notes;
- retain source/target term values, section, term row, and the source locations of note fields for diagnostics.

Add a small private context helper that serializes `(section, source_term)` collision-free and Unicode-safe. Use it in both v1 and v2 TBX parsing. Remove the v2 dependency on `_slug` and `key_language`; keep the v1 profile reader compatible, but migrate its runtime context calculation to the same Unicode-safe helper so old profiles no longer require a Latin-language column.

Keep all existing outer-whitespace errors. Do not trim TBX cells.

Write tests proving:

- Russian-only and CJK-only terms render and parse back with their Unicode context;
- same source in distinct sections is distinct, while same source in the same section fails duplicate context;
- single and multiple declared note fields are preserved in deterministic order;
- rendered source and target explanations equal the normalized in-memory values; and
- a missing target term, missing source term, or outer whitespace blocks output.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py tests/test_writer.py -v
```

## Task B3 - Parse `record-map` rows exhaustively

**Files:**

- Modify: `loc_kit_ingest/parser.py`
- Create: `loc_kit_ingest/tests/fixtures/glossary-record-map.csv`
- Create: `loc_kit_ingest/tests/fixtures/glossary-record-map.loc-ingest.json`
- Modify: `loc_kit_ingest/tests/test_parser_tbx.py`

Implement a separate `_parse_record_map` dispatch branch. It must:

- walk each region at exactly `record_stride` rows;
- read language terms at `term_row_offset` and note fields at their declared offsets;
- resolve each record's section from `section_field` or the region caption cell, and allow only declared language cells on caption rows;
- record consumed cells and reject populated record cells not declared by the profile as `tbx.unmapped_cell`;
- emit row-accurate diagnostics for blank source, blank required target, invalid outer whitespace, duplicate context, invalid section, and uncovered rows;
- keep a full `covered_rows` set, including section and explicit skip rows, and preserve `grammar.uncovered_row`.

The new fixture must be anonymous but structurally equivalent to a common spreadsheet glossary: a banner, a header row, a domain column, source/target term columns, a source-note column, a target-note column, and one row per term. Do not add the user-provided workbook to the repository.

Add a second in-memory stride-two test that uses region caption cells instead of a domain column. It proves that `record-map` is a grammar engine rather than a CoL4-specific reader.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -v -k "record_map or unicode or pairs"
```

## Task B4 - Render and validate profile v2 end to end

**Files:**

- Modify: `loc_kit_ingest/tests/test_pipeline.py`
- Modify: `loc_kit_ingest/tests/test_failure_matrix.py`
- Modify: `loc_kit_ingest/tests/test_writer.py`

Use `parse_profile -> parse_component -> render_component -> validate_rendered_component` for both one-row and stride-two v2 fixtures. Assert exact file names, language pair direction, context, source explanation, target explanation, and parse-back equality.

Add failures for:

- a row with a status/flag column left unmapped;
- a target note referencing a non-target language;
- a populated footer not listed in `skip_rows`;
- a record range not divisible by stride; and
- a renderer parse-back mismatch.

The assertion must be that no staging output is published after a failure.

**Run:**

```bash
cd loc_kit_ingest && uv run pytest tests/test_pipeline.py tests/test_failure_matrix.py tests/test_writer.py -v
```

## Task B5 - Keep the CLI deterministic

**Files:**

- Modify: `loc_kit_ingest/cli.py` only if help text needs clarification.
- Modify: `docs/specs/loc-kit-ingest.md` in Part E.

Do not add `--terms`, `--suggest-profile`, or an OpenRouter dependency to the CLI. Existing `--profile` is the universal CLI entry point for a v2 record-map. Update its help text only if needed to say that the profile can define a TBX record-map glossary.

**Run:**

```bash
cd loc_kit_ingest && uv run python -m loc_kit_ingest \
  tests/fixtures/glossary-record-map.csv \
  --profile tests/fixtures/glossary-record-map.loc-ingest.json \
  --out /tmp/loc-kit-record-map
```

Confirm parse-back success and only the expected target TBX files under `/tmp/loc-kit-record-map`.

---

# Part C - Site-wide OpenRouter profile proposal

## Task C1 - Add server-side configuration and defaults

**Files:**

- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/trans/models/_conf.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/utils/defaults.py`
- Modify: `weblate/utils/models.py`
- Modify: `docs/admin/config.rst` or its generated settings source, following the surrounding configuration convention.

Add site-wide settings, disabled by default:

- `LOC_KIT_PROFILE_ANALYSIS_ENABLED = False`
- `LOC_KIT_PROFILE_OPENROUTER_KEY = ""`
- `LOC_KIT_PROFILE_OPENROUTER_MODEL = ""`
- `LOC_KIT_PROFILE_SAMPLE_MAX_BYTES = 131072`
- `LOC_KIT_IMPORT_DRAFT_EXPIRY = 3600`
- `RATELIMIT_LOC_KIT_ANALYSIS_ATTEMPTS = 3`
- `RATELIMIT_LOC_KIT_ANALYSIS_WINDOW = 3600`

Expose Docker environment variables with the established `WEBLATE_` prefix. The API base URL is a module constant `https://openrouter.ai/api/v1`; it is not configurable. Clamp `LOC_KIT_PROFILE_SAMPLE_MAX_BYTES` to 131072 and `LOC_KIT_IMPORT_DRAFT_EXPIRY` to 3600 regardless of environment input. Document that enabling the feature sends structural samples to OpenRouter and that the configured model must support strict structured outputs.

**Tests:** configuration defaults, Docker environment parsing, disabled-by-default behavior, and clamping values above those hard limits.

## Task C2 - Build a bounded structural sampler

**Files:**

- Create: `weblate/trans/loc_kit.py`
- Create: `weblate/trans/tests/test_loc_kit_profile_suggester.py`

Keep this module in Weblate, not `loc_kit_ingest`. Add a pure `build_glossary_structure_sample(rows, sheet_name, max_bytes)` helper with the contract above. It must be stable for identical input and preserve every row signature whenever it emits a sample; a signature payload that cannot fit is `loc_kit.sample_too_large`, not a lossy fallback.

Write tests for:

- one-row and stride-two layouts retaining distinguishable signatures;
- Unicode values surviving UTF-8-safe truncation;
- deterministic output;
- headers and row/column coordinates preserved;
- a cap-too-small input returning the local size diagnostic instead of silently dropping structural data; and
- no raw full-sheet dump when the source has many rows.

## Task C3 - Implement the fixed OpenRouter proposal client

**Files:**

- Modify: `weblate/trans/loc_kit.py`
- Create: `weblate/trans/prompts/__init__.py`
- Create: `weblate/trans/prompts/loc_kit_profile.txt`
- Modify: `pyproject.toml`
- Modify: `weblate/trans/tests/test_loc_kit_profile_suggester.py`

Implement a small service with this boundary:

Keep the static instruction in `weblate/trans/prompts/loc_kit_profile.txt`, loaded with `importlib.resources`; include that asset in the wheel through `pyproject.toml`. The prompt receives the structural sample as a separate user message, tells the model to return only the response envelope, and says that insufficient structure must produce `status: "unsupported"`. Test the loaded prompt rather than duplicating it in Python.

1. Refuse when the site-wide service is disabled or its key/model is absent.
2. Send a POST only to the fixed OpenRouter chat-completions URL through `fetch_validated_url`.
3. Use `Authorization: Bearer <site key>`, the configured model, `stream: false`, `response_format` strict JSON Schema, OpenRouter's `provider.require_parameters: true` preference, and a fixed 120-second request timeout matching Weblate's LLM machinery.
4. Parse only `choices[0].message.content` as JSON and validate the envelope before returning it.
5. Never log the key, raw sample, raw model response, or user cell values. Turn provider errors into concise user-facing failure states and preserve the exception for server diagnostics only where project convention permits.

Use `http_mock` to assert exact endpoint, headers excluding the literal secret in failure text, payload fields, strict schema, request timeout, malformed response handling, 4xx/5xx handling, and no request when disabled. Do not test against the live network.

## Task C4 - Generate and validate candidate profiles locally

**Files:**

- Modify: `weblate/trans/loc_kit.py`
- Modify: `weblate/trans/tests/test_loc_kit_profile_suggester.py`

Add an orchestration function that accepts a selected sheet plus the LLM envelope or a manually uploaded profile. It must:

1. Require `status == "profile"` and exactly one v2 TBX component for the selected sheet.
2. Replace the candidate component name with the server-generated draft component name.
3. Call `parse_profile`, exact header validation, `parse_component`, `render_component`, and `validate_rendered_component`.
4. Return a preview DTO only on zero error diagnostics: source language, target languages, term count, note count, warnings, first bounded term sample, canonical profile JSON, and rendered files held only in the draft lifecycle.
5. Reject invalid model profiles, source-only profiles, flag declarations, another sheet, and every parse/render/parse-back error before the UI can offer confirmation.

Write tests that feed the same fixtures through LLM and manual-profile paths. They must produce identical local results. A deliberately malformed profile or simulated parse-back error must prove no component and no repository are created.

---

# Part D - Temporary draft and authenticated UI workflow

## Task D1 - Add temporary draft persistence and cleanup

**Files:**

- Create: `weblate/trans/models/loc_kit.py`
- Modify: `weblate/trans/models/__init__.py`
- Create: `weblate/trans/migrations/00xx_loc_kit_import_draft.py`
- Modify: `weblate/trans/tasks.py`
- Create: `weblate/trans/tests/test_loc_kit_drafts.py`

Add `LocKitImportDraft` with:

- unguessable UUID/token;
- owner foreign key and a one-way session binding;
- selected project/basic component fields needed to resume the normal wizard;
- uploaded source `FileField` in managed storage;
- selected sheet, validated profile JSON, preview metadata, and expiry timestamp;
- a state that distinguishes newly uploaded, sheet-selected, validated-preview, and consumed drafts.

Files are temporary data, not an audit log. Enforce `expires_at <= created_at + 1 hour`; all draft lookup paths require the same authenticated owner and session binding; expired or consumed drafts behave as absent. Explicit cancel and successful component creation delete the storage object. Add a Celery cleanup task every 15 minutes that deletes expired rows and files idempotently.

Every draft endpoint must also recheck the same project-level component-creation permission as the normal wizard. A revoked permission makes the draft unavailable and must not trigger analysis, expose staged files, or create a component.

Tests must cover owner isolation, session isolation, permission revocation, expiry, cancellation, successful consumption, storage deletion, repeated cleanup, and a stale token that cannot be reused.

## Task D2 - Add explicit glossary-analysis stages

**Files:**

- Modify: `weblate/trans/forms.py`
- Modify: `weblate/trans/views/create.py`
- Modify: `weblate/urls.py`
- Modify: `weblate/templates/trans/component_create.html`
- Create: `weblate/templates/trans/loc_kit_sheet_select.html`
- Create: `weblate/templates/trans/loc_kit_glossary_preview.html`
- Create: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

Keep the normal ZIP and PO table behavior intact. For a CSV/TSV/XLSX upload with `is_glossary` checked:

1. Validate extension and existing upload size limits, read sheets locally, and create a temporary draft. Never call OpenRouter before a selected sheet exists.
2. Render a sheet-selection form when there is more than one sheet. Show sheet names and dimensions as accessible radio choices. One sheet may be preselected, but it remains the sole chosen sheet.
3. On selection, apply the session rate limit to the proposal POST. If the site-wide analyzer is enabled, build a structural sample and request one candidate. If disabled, unavailable, unsupported, or too large, present the same page with manual profile upload instead of falling back to PO inference.
4. Run the local validation orchestration and render preview only after it succeeds. The preview must show selected sheet, source/targets, term count, warnings, a bounded term sample, and the downloadable profile.
5. Let the user upload a replacement UTF-8 `.loc-ingest.json` profile no larger than `LOC_KIT_PROFILE_SAMPLE_MAX_BYTES`, then repeat local validation against the same draft file and selected sheet. Do not call the LLM for correction.
6. Offer a real POST cancel action with CSRF protection. It deletes the draft.

Do not add a `kit_terms` field. The existing `is_glossary` field is the only intent signal. Adjust its form order, labels, help text, and surrounding upload-tab prose so its external-analysis consequence is clear to the operator-facing feature documentation without presenting a per-user consent dialog.

Ensure visible focus, labelled radio inputs/file controls, a summary linked to any form errors, and no color-only warning/error state.

Use Django i18n helpers for every new visible string in forms, views, and templates.

## Task D3 - Bind preview output to standard component creation safely

**Files:**

- Modify: `weblate/trans/views/create.py`
- Modify: `weblate/utils/views.py`
- Modify: `weblate/trans/forms.py`
- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

After preview confirmation, re-enter the existing component-creation flow rather than creating a `Component` directly. The draft integration must:

- regenerate or retrieve only the validated staged TBX files;
- set `file_format="tbx"`, `filemask="tbx/*.tbx"`, empty `template`, profile source language, and `is_glossary=True`;
- make these conversion-derived fields immutable at the final form and revalidate them server-side, not only through `initial` values;
- preserve ordinary component settings that the existing final form legitimately exposes;
- create `LocalRepository` only at confirmed component creation, under the actual validated component path;
- run parse-back validation before repository creation and before `Component.post_create`;
- delete the draft only after successful creation; keep it until expiry after a recoverable final-form error.

Retain `create_component_from_kit` as the direct PO/ZIP helper. Factor a narrow shared rendering helper only if it avoids duplication without moving Django logic into `loc_kit_ingest`.

End-to-end tests must drive:

- a one-row generic glossary from upload to live TBX component;
- a stride-two profile uploaded manually with no outbound request;
- multiple worksheets requiring an explicit chosen sheet;
- a disabled analyzer exposing manual profile upload;
- a source-language mismatch being visible before final confirmation;
- a user trying to alter source language, file format, mask, template, or glossary flag in the final POST being rejected or overridden to the validated draft values;
- invalid profile, invalid parse-back, expired draft, cross-user token, and cancel paths creating no component.

After creation, assert `file_format == "tbx"`, `filemask == "tbx/*.tbx"`, `template == ""`, `is_glossary is True`, correct source/target translations, Unicode context, source explanation, target explanation, and glossary sidebar matching against a component with the same source language.

---

# Part E - Documentation, deployment, and verification

## Task E1 - Update the ingest specification

**Files:**

- Modify: `docs/specs/loc-kit-ingest.md`

Update the Russian specification without rewriting unrelated sections:

- retain v1 behavior and document v2 as an additive closed schema;
- replace the old hardcoded terminology-inference narrative with `record-map` semantics, Unicode context, note fields, one-source-plus-target restriction, and unmapped-cell failure;
- document that CLI stays manual-profile/deterministic;
- document the UI path: explicit glossary checkbox, single sheet selection, candidate profile, local validation, JSON correction, mandatory parse-back, and temporary one-hour draft;
- state the LLM data minimization and that flags/source-only glossary imports are unsupported in this flow;
- correct the UI assertion: TBX now does parse-back before creation.

## Task E2 - Document configuration and threat model

**Files:**

- Modify: `docs/admin/config.rst` or its generated source
- Modify: `docs/admin/projects.rst` where component upload behavior is documented
- Modify: `docs/security/threat-model.rst`
- Modify: `docs/changes.rst`
- Modify: `AGENTS.md`

Add concise operator-facing documentation for enabled/model/key/sample limit/draft expiry/rate limit, including that structural samples are sent to OpenRouter when the operator enables analysis.

Update the threat model's system scope, trust-boundary table, input assumptions, protection claims, and change conditions to cover:

- authenticated table uploads becoming a fixed outbound OpenRouter request only after an explicit glossary intent;
- separate site-wide credentials and fixed provider host;
- bounded samples, rate limit, temporary draft storage, authorization/session binding, expiry cleanup, and no user-controlled endpoint;
- provider behavior remaining outside Weblate's security boundary.

Add one concise unreleased changelog item. Update `AGENTS.md` with the new v2/draft deployment and test-copy behavior; do not claim the custom MT machinery is reused.

## Task E3 - Deploy and smoke-test through the browser

**Precondition:** Configure the separate site-wide analyzer in `dev-docker/environment` only if a safe development OpenRouter key and structured-output-capable model are available. Do not use the production MT key.

```bash
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
WEBLATE_PORT=3001 ./rundev.sh
```

Exercise both paths at `http://localhost:3001/create/component/zip/`:

1. Upload a generic keyless CSV with **Use as glossary** clear. Confirm the existing PO route stays local and correctly selects its source language.
2. Upload the user-provided CoL4 workbook with **Use as glossary** selected. Choose its glossary sheet, inspect the profile proposal, and confirm that the preview maps terms, context/note, and recommended-translation note instead of treating metadata as terms. Confirm that it identifies `ru` as source and `en` and `ja` as initial targets.
3. Download the profile, make a harmless valid correction, upload it, and confirm preview is regenerated locally without another external request.
4. Confirm creation. Verify the resulting TBX component has `ru` as source, `en` and `ja` target files, expected explanations, Unicode-safe contexts, and a matching glossary panel entry in a regular component with the same source language.
5. Upload a workbook with multiple candidate sheets, cancel it, and confirm the draft is inaccessible afterward.

If a safe development provider configuration is unavailable, smoke the same workflow through the manual-profile branch. The OpenRouter request behavior remains covered by mocked tests.

## Task E4 - Final verification

Run after all targeted tests and browser smoke are green:

```bash
cd loc_kit_ingest && uv run pytest -q && cd ..
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
./rundev.sh test weblate/trans/tests/test_loc_kit_profile_suggester.py \
  weblate/trans/tests/test_loc_kit_drafts.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py
./rundev.sh check
uv run prek run --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Known repository noise must be compared against an unchanged baseline before attribution: `reuse lint` currently reports pre-existing `loc_kit_ingest` files, and `weblate/trans/tests/test_autotranslate.py` is flaky under container xdist.

---

## Acceptance criteria

1. Existing v1 PO and TBX profiles remain accepted with their existing fields and grammar; all current behavior stays covered apart from the intentional Unicode-safe context identity update.
2. A v2 one-row table and a v2 stride-two table both produce the same valid TBX component shape through local validation.
3. No terminology code relies on a project name, fixture name, source language, English column, ASCII-only slug, known section caption, or hardcoded column order.
4. The LLM is never called for ordinary PO uploads, missing configuration, manual profile corrections, or unselected sheets.
5. A malformed model answer, unsupported semantic flag, unmapped populated cell, source-only table, missing target term, whitespace-loss risk, bad header, or parse-back mismatch prevents component creation.
6. The profile preview is user-correctable through JSON and every correction is revalidated against the original selected sheet.
7. One user/session cannot read, confirm, or cancel another user's draft; expiry and cleanup remove files and metadata.
8. Generated glossary source language, target files, source explanation, target explanation, context, and `is_glossary` behavior are verified through Weblate's real TBX format and glossary matching code.
9. Documentation names the external-data boundary and threat model update.

## Risks and explicit follow-ups

- A structural sample can be insufficient for an unusual layout. That is a safe manual-profile outcome, not a reason to transmit the entire workbook.
- A profile can be structurally valid but semantically wrong. Preview and explicit confirmation remain required; the deterministic converter cannot solve semantic judgment.
- TBX cannot preserve leading/trailing note whitespace with the installed Translate Toolkit. Keep rejecting it until the underlying format contract changes.
- A glossary only helps components that share its source language. The preview must make the chosen source language obvious.
- Draft storage needs a working Django storage backend and Celery beat for punctual cleanup. Access checks and expiry still protect data if cleanup runs late.
- Flags and source-only glossaries are intentionally excluded from v1. Add them later only with a concrete TBX/Weblate contract and separate tests.
