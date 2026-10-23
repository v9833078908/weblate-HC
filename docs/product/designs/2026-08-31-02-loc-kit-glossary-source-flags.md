# Loc-kit glossary source flags

## Goal

A producer can mark imported glossary terms as `read-only` or `forbidden` in a CSV, TSV, or XLSX table. The flags survive the publication gate, appear in the preview, and apply to newly created terms in both glossary creation and append-only update flows.

## Input contract

A record-map glossary may declare one source flag field:

```json
"source_flags": {"column": 4, "header": "flags", "row_offset": 0}
```

Deterministic inference recognizes one exact, case-insensitive `flags` header outside the language and note columns. Each non-empty cell is a comma-separated set containing only `read-only` and `forbidden`. Empty cells are valid. Unknown or parameterized flags are errors. The importer normalizes accepted values into a stable tuple.

`terminology` remains automatic maintenance metadata. Target-scoped `exact` and `not-applicable` remain out of scope because one source column cannot express per-language behavior safely.

## Data flow

`RecordMapGrammar.source_flags` identifies the cell. The parser stores normalized flags on `GlossaryTerm.source_flags`. The TBX writer serializes them on `<termEntry weblate-flags="...">`; parse-back validation compares the flags as well as terms and explanations. Weblate's existing TBX flag parser then preserves them during component creation.

The preview shows the flags for each sampled term. The append-only service merges imported flags with `terminology` on each new source unit. Existing terms remain untouched, including their flags.

## Alternatives rejected

- Encode flags in explanation or note text: models cannot enforce typed semantics reliably and producers cannot audit the result.
- Parse a magic column only in the Weblate view: bypasses the standalone parser and publication gate.
- Accept arbitrary Weblate flags: exposes unrelated checks and parameterized behavior without a product contract.

## Verification

- Profile and inference tests cover accepted headers, empty cells, and unknown flags.
- Parser and writer tests cover normalization and TBX parse-back.
- Weblate preview, creation, append-only, and LLM glossary-entry tests prove end-to-end behavior.
- Standalone loc-kit tests, focused Weblate tests, and scoped lint pass.

## Non-goals

- Updating flags on an existing glossary term.
- Per-language `exact` or `not-applicable` import.
- Source-only glossary import or arbitrary Weblate check flags.
