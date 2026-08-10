# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Derive a strict profile document from a kit's own header row.

The importer still never guesses at parse time. Inference runs once, up front,
and emits a profile document that is validated by the same closed schema as a
hand-written one and written into the output directory as evidence.

Recognising a language column relies on Translate Toolkit's language registry
rather than the shape of the header text, because plausible metadata headers
collide with real language codes: a column headed ``Id`` is Indonesian by code
and an engine string id by intent. Columns are therefore accepted as languages
only when the registry knows the code *and* the column holds non-numeric data.
"""

from __future__ import annotations

import re
from typing import Any

from translate.lang import data as lang_data

from loc_kit_ingest.profile import SCHEMA_VERSION, SCHEMA_VERSION_RECORD_MAP

# Language columns filled below this share of content rows are stray spillover,
# not a translation: real languages in a kit cluster near 100% while accidental
# paste-overs sit under 1%.
DEFAULT_MIN_FILL = 5.0

_PARENS_CODE = re.compile(r"\(\s*([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*)\s*\)\s*$")
_BARE_CODE = re.compile(r"^\s*([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*)\s*$")
_UNSAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_-]+")

# Markers some kits put in the key cell of a caption row.
_IGNORE_MARKERS = frozenset({"id-ignore", "id_ignore", "ignore"})

_KEY_COLUMN = 0

# Header text that must never be treated as a language column, even if it
# resolves to a real code (``id`` is also the Indonesian language code).
_KEY_HEADER_DENYLIST = frozenset({"id"})

# A deterministically mappable glossary is structurally simple: a few blocks of
# consecutive terms and a caption row or two. Past these bounds the emitted
# profile would carry hundreds of regions and skip rows, which the profile
# validator cross-multiplies, so a small upload becomes a quadratic parse.
# Refusing here costs nothing: the analyzer and manual-profile paths remain.
_MAX_REGIONS = 64
_MAX_SKIPPED_ROWS = 64

# How many row numbers a "missing term" note names before it summarises.
_MISSING_ROWS_SHOWN = 10


class InferenceError(Exception):
    """Raised when a sheet's shape cannot be determined with confidence."""


def _known_language(token: str) -> str | None:
    """Return a Weblate-style code for ``token``, or None if it is no language."""
    norm = token.strip().replace("-", "_")
    if not norm:
        return None
    if norm in lang_data.languages:
        return norm
    if "_" in norm:
        base, _, region = norm.partition("_")
        canonical = f"{base.lower()}_{region.upper()}"
        if canonical in lang_data.languages or base.lower() in lang_data.languages:
            return canonical
        return None
    lowered = norm.lower()
    return lowered if lowered in lang_data.languages else None


def _language_code(header_cell: str) -> str | None:
    """Extract a language code from a header cell, ``en`` or ``English(en)``."""
    match = _PARENS_CODE.search(header_cell)
    if match:
        return _known_language(match.group(1))
    match = _BARE_CODE.match(header_cell)
    if match:
        return _known_language(match.group(1))
    return None


def _cell(row: list[str], col: int) -> str:
    return row[col] if len(row) > col else ""


def _is_blank_row(row: list[str]) -> bool:
    return not any(cell.strip() for cell in row)


def _is_numeric(values: list[str]) -> bool:
    """True when every non-empty value is a plain number, like an engine id."""
    present = [v.strip() for v in values if v.strip()]
    if not present:
        return False
    return all(v.lstrip("-").replace(".", "", 1).isdigit() for v in present)


def _find_header_row(rows: list[list[str]]) -> tuple[int, dict[int, str]]:
    """
    Return the first row index holding language codes, and those codes.

    A banner row such as ``UI,,,`` carries no language code and is skipped
    naturally, which is why the header is located by content and not by index.
    """
    for index, row in enumerate(rows):
        found = {
            col: code
            for col in range(_KEY_COLUMN + 1, len(row))
            if (code := _language_code(_cell(row, col))) is not None
        }
        if found:
            return index, found
    msg = (
        "no row contains a recognised language code; "
        "pass an explicit --profile for this sheet"
    )
    raise InferenceError(msg)


def _is_caption_row(row: list[str], lang_cols: dict[int, str]) -> bool:
    """True for a row that labels the columns instead of holding content."""
    if _cell(row, _KEY_COLUMN).strip().casefold() in _IGNORE_MARKERS:
        return True
    for col, code in lang_cols.items():
        cell = _cell(row, col).strip().casefold()
        if not cell:
            continue
        name = lang_data.languages.get(code, ("",))[0].casefold()
        if name and cell.startswith(name):
            return True
    return False


def _sanitize_component(name: str) -> str:
    """
    Reduce a file stem to a valid component name.

    ``Heart Abyss_Localization - Temple`` becomes ``Temple``: kits are commonly
    exported one sheet per file with the sheet's purpose last.
    """
    candidate = name.rsplit(" - ", 1)[-1].strip()
    candidate = _UNSAFE_COMPONENT.sub("_", candidate).strip("_-")
    if not candidate or not candidate[0].isalnum():
        candidate = f"kit_{candidate}".strip("_-")
    if not candidate or not candidate[0].isalnum():
        msg = f"cannot derive a component name from {name!r}"
        raise InferenceError(msg)
    return candidate


def infer_component(
    sheet_name: str,
    rows: list[list[str]],
    *,
    component: str,
    source_lang: str | None = None,
    min_fill: float = DEFAULT_MIN_FILL,
    include_languages: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[str]]:
    """Infer one keyed PO component from a sheet. Returns (document, notes)."""
    if not rows:
        msg = f"sheet {sheet_name!r} is empty"
        raise InferenceError(msg)

    header_index, candidates = _find_header_row(rows)
    header_row = rows[header_index]
    notes: list[str] = []

    skip_rows: list[int] = []
    cursor = header_index + 1
    while cursor < len(rows) and _is_caption_row(rows[cursor], candidates):
        skip_rows.append(cursor + 1)
        cursor += 1
    first_data_index = cursor
    data_rows = rows[first_data_index:]
    if not data_rows:
        msg = f"sheet {sheet_name!r} has no data rows"
        raise InferenceError(msg)

    caption_row = rows[header_index + 1] if skip_rows else []
    content_rows = sum(1 for row in data_rows if not _is_blank_row(row))

    def column_values(col: int) -> list[str]:
        return [_cell(row, col) for row in data_rows]

    key_header = _cell(header_row, _KEY_COLUMN)
    key_language_code = _language_code(key_header)
    key_is_language = (
        key_language_code is not None
        and key_header.strip().casefold() not in _KEY_HEADER_DENYLIST
        and key_language_code not in candidates.values()
    )
    if key_is_language:
        key_column_values = column_values(_KEY_COLUMN)
        key_is_language = bool(
            any(v.strip() for v in key_column_values)
        ) and not _is_numeric(key_column_values)
    if key_is_language and key_language_code is not None:
        candidates = {_KEY_COLUMN: key_language_code, **candidates}

    languages: dict[int, str] = {}
    # Columns whose header is a language code but whose content proves otherwise.
    demoted: set[int] = set()
    for col in sorted(candidates):
        code = candidates[col]
        values = column_values(col)
        filled = [
            first_data_index + offset + 1
            for offset, value in enumerate(values)
            if value.strip()
        ]
        if not filled:
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is empty; excluded"
            )
            continue
        if _is_numeric(values):
            demoted.add(col)
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r}) holds only numbers; "
                f"treated as metadata, not language {code}"
            )
            continue
        share = 100.0 * len(filled) / content_rows if content_rows else 0.0
        if share < min_fill and code not in include_languages:
            shown = ", ".join(str(row) for row in filled[:10])
            if len(filled) > 10:
                shown += f", +{len(filled) - 10} more"
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is filled in "
                f"{len(filled)}/{content_rows} rows ({share:.1f}%), under the "
                f"{min_fill:g}% threshold; excluded as stray content at row(s) {shown}; "
                f"pass --include-lang {code} to import it anyway"
            )
            continue
        languages[col] = code
        notes.append(
            f"column {col + 1} -> language {code} "
            f"({len(filled)}/{content_rows} rows, {share:.1f}%)"
        )

    if key_is_language and _KEY_COLUMN in languages:
        notes.append(
            f"column {_KEY_COLUMN + 1} ({key_header!r} -> "
            f"{languages[_KEY_COLUMN]}) is both the PO key and a language "
            "column; key and source text are identical"
        )

    if not languages:
        msg = f"sheet {sheet_name!r} has no populated language column"
        raise InferenceError(msg)

    key_values = [
        _cell(row, _KEY_COLUMN) for row in data_rows if not _is_blank_row(row)
    ]
    if not any(v.strip() for v in key_values):
        msg = (
            f"sheet {sheet_name!r} has no keys in column 1; pass an explicit --profile"
        )
        raise InferenceError(msg)

    comments: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    # Visit only columns that can produce output. A column that is empty
    # across the data AND has no header falls through the body below without
    # any effect, so skipping it is behaviour-preserving - and necessary:
    # `max(len(row))` spans every row, so one blank-but-wide row would
    # otherwise drive a full column_values() pass per phantom column, which
    # is quadratic in an uploaded file.
    populated: set[int] = set()
    for row in data_rows:
        for col, value in enumerate(row):
            if value.strip():
                populated.add(col)
    headed = {col for col in range(len(header_row)) if _cell(header_row, col).strip()}
    for col in sorted(populated | headed):
        if col <= _KEY_COLUMN:
            continue
        # A column rejected as a language must not resurface as a comment: its
        # content is stray spillover and belongs in the report, not in the PO.
        if col in languages or (col in candidates and col not in demoted):
            continue
        values = column_values(col)
        if not any(v.strip() for v in values):
            empty_header = _cell(header_row, col)
            if empty_header.strip() and col not in candidates:
                notes.append(
                    f"column {col + 1} ({empty_header!r}) is empty and is not a "
                    "recognised language code; ignored"
                )
            continue
        header_text = _cell(header_row, col)
        name = (
            header_text.strip() or _cell(caption_row, col).strip() or f"column{col + 1}"
        )
        entry = {"column": col + 1, "name": name, "header": header_text}
        if _is_numeric(values):
            references.append(entry)
            notes.append(f"column {col + 1} ({name!r}) -> location reference")
        else:
            comments.append(entry)
            notes.append(f"column {col + 1} ({name!r}) -> developer comment")

    ordered = sorted(languages)
    resolved_source = source_lang or languages[ordered[0]]
    if resolved_source not in languages.values():
        msg = (
            f"source language {resolved_source!r} is not among the sheet's "
            f"languages {sorted(languages.values())}"
        )
        raise InferenceError(msg)
    if source_lang is None:
        notes.append(
            f"source language assumed to be {resolved_source!r} (leftmost populated "
            "language column); override with --source-lang"
        )

    document = {
        "sheet": sheet_name,
        "component": component,
        "kind": "po",
        "source_lang": resolved_source,
        "header_row": header_index + 1,
        "first_data_row": first_data_index + 1,
        "languages": [
            {
                "code": languages[col],
                "xml_lang": languages[col].replace("_", "-"),
                "column": col + 1,
                "header": _cell(header_row, col),
            }
            for col in ordered
        ],
        "key": {"column": _KEY_COLUMN + 1, "header": _cell(header_row, _KEY_COLUMN)},
        "comments": comments,
        "references": references,
        "grammar": {
            "type": "keyed",
            "skip_rows": skip_rows,
            "allow_blank_rows": any(_is_blank_row(row) for row in data_rows),
        },
    }
    return document, notes


def infer_profile(
    sheets: dict[str, list[list[str]]],
    *,
    kit_stem: str,
    source_lang: str | None = None,
    component: str | None = None,
    min_fill: float = DEFAULT_MIN_FILL,
    include_languages: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[str]]:
    """Infer a whole profile document for one kit file. Returns (document, notes)."""
    if not sheets:
        msg = "kit contains no sheets"
        raise InferenceError(msg)

    single = len(sheets) == 1
    components: list[dict[str, Any]] = []
    notes: list[str] = []
    for sheet_name, rows in sheets.items():
        if single:
            name = component or _sanitize_component(kit_stem)
        else:
            name = _sanitize_component(sheet_name)
        document, sheet_notes = infer_component(
            sheet_name,
            rows,
            component=name,
            source_lang=source_lang,
            min_fill=min_fill,
            include_languages=include_languages,
        )
        components.append(document)
        notes.extend(f"{name}: {note}" for note in sheet_notes)

    return {"schema_version": SCHEMA_VERSION, "components": components}, notes


def infer_glossary_profile(
    sheet_name: str,
    rows: list[list[str]],
    *,
    component: str,
    min_fill: float = DEFAULT_MIN_FILL,
) -> tuple[dict[str, Any], list[str]]:
    """
    Infer a schema v2 record-map TBX profile for one worksheet.

    Deterministic v0: every populated column must be a language column; the
    leftmost language is the source, targets are languages filled on every
    record row. The record-map parser treats any unmapped populated cell as
    an error, so an extra column is a refusal, never a guess.
    Returns (document, notes).
    """
    if not rows:
        msg = f"sheet {sheet_name!r} is empty"
        raise InferenceError(msg)

    header_index, candidates = _find_header_row(rows)
    header_row = rows[header_index]
    notes: list[str] = []

    # Column 0 is an ordinary column here; promote it when its header is a
    # language code (a keyless kit like ``ru,en,ja`` keeps terms in column 1).
    first_header = _cell(header_row, _KEY_COLUMN)
    first_code = _language_code(first_header)
    if (
        first_code is not None
        and first_header.strip().casefold() not in _KEY_HEADER_DENYLIST
        and first_code not in candidates.values()
    ):
        candidates = {_KEY_COLUMN: first_code, **candidates}

    skip_rows: list[int] = []  # 0-based
    cursor = header_index + 1
    while cursor < len(rows) and _is_caption_row(rows[cursor], candidates):
        skip_rows.append(cursor)
        cursor += 1

    content_indexes = [
        index for index in range(cursor, len(rows)) if not _is_blank_row(rows[index])
    ]
    if not content_indexes:
        msg = f"sheet {sheet_name!r} has no data rows"
        raise InferenceError(msg)

    languages: dict[int, str] = {}
    for col in sorted(candidates):
        code = candidates[col]
        values = [_cell(rows[index], col) for index in content_indexes]
        filled = sum(1 for value in values if value.strip())
        if not filled:
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) "
                "is empty; excluded"
            )
            continue
        if _is_numeric(values):
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r}) holds only "
                "numbers; this layout needs an explicit profile"
            )
            raise InferenceError(msg)
        share = 100.0 * filled / len(content_indexes)
        if share < min_fill:
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is "
                f"filled in {filled}/{len(content_indexes)} rows "
                f"({share:.1f}%); too sparse to map deterministically"
            )
            raise InferenceError(msg)
        languages[col] = code

    if not languages:
        msg = f"sheet {sheet_name!r} has no populated language column"
        raise InferenceError(msg)

    # Every populated column must be a declared language: the record-map
    # parser errors on any populated cell no field reads (tbx.unmapped_cell).
    # Collect the populated columns in ONE pass over the rows. Looping
    # range(max_width) and rescanning every row per column costs
    # rows x width, and a single blank-but-wide row raises width for free,
    # so that shape is a cheap quadratic from an uploaded file.
    populated: set[int] = set()
    for index in content_indexes:
        for col, value in enumerate(rows[index]):
            if value.strip():
                populated.add(col)
    unmapped = sorted(populated - languages.keys())
    if unmapped:
        col = unmapped[0]
        header_text = _cell(header_row, col) or f"column{col + 1}"
        msg = (
            f"column {col + 1} ({header_text!r}) holds data but is not a "
            "recognised language column; this layout needs an explicit "
            "profile"
        )
        raise InferenceError(msg)

    source_col = min(languages)
    source_lang = languages[source_col]

    record_rows: list[int] = []
    for index in content_indexes:
        if _cell(rows[index], source_col).strip():
            record_rows.append(index)
            continue
        skip_rows.append(index)
        # Bounds skip_rows AND the notes list, both of which are serialized
        # into the draft and rendered on one page.
        if len(skip_rows) > _MAX_SKIPPED_ROWS:
            msg = (
                f"sheet {sheet_name!r} has more than {_MAX_SKIPPED_ROWS} rows "
                f"without a {source_lang} term; too fragmented to map "
                "deterministically"
            )
            raise InferenceError(msg)
        notes.append(f"row {index + 1} has no {source_lang} term; skipped")
    if not record_rows:
        msg = f"sheet {sheet_name!r} has no rows with a {source_lang} term"
        raise InferenceError(msg)

    regions: list[dict[str, int]] = []
    start = prev = record_rows[0]
    for index in record_rows[1:]:
        if index == prev + 1:
            prev = index
            continue
        regions.append(
            {
                "first_record_row": start + 1,
                "last_record_row": prev + 1,
                "record_stride": 1,
            }
        )
        start = prev = index
    regions.append(
        {
            "first_record_row": start + 1,
            "last_record_row": prev + 1,
            "record_stride": 1,
        }
    )
    # The profile validator cross-multiplies skip_rows by regions, so an
    # alternating filled/blank source column would otherwise turn a small
    # sheet into a quadratic parse.
    if len(regions) > _MAX_REGIONS:
        msg = (
            f"sheet {sheet_name!r} splits into {len(regions)} blocks of "
            f"consecutive terms (limit {_MAX_REGIONS}); too fragmented to map "
            "deterministically"
        )
        raise InferenceError(msg)

    target_langs: list[str] = []
    for col in sorted(languages):
        if col == source_col:
            continue
        code = languages[col]
        # Count every gap but keep only the head: the message shows ten.
        missing_head: list[int] = []
        missing_count = 0
        for index in record_rows:
            if _cell(rows[index], col).strip():
                continue
            missing_count += 1
            if len(missing_head) < _MISSING_ROWS_SHOWN:
                missing_head.append(index + 1)
        if missing_count:
            shown = ", ".join(str(row) for row in missing_head)
            if missing_count > len(missing_head):
                shown += f", +{missing_count - len(missing_head)} more"
            notes.append(
                f"language {code} is missing a term on row(s) {shown}; "
                "recognised but not imported"
            )
            continue
        target_langs.append(code)
    if not target_langs:
        msg = f"sheet {sheet_name!r} has no fully filled target language column"
        raise InferenceError(msg)

    document = {
        "schema_version": SCHEMA_VERSION_RECORD_MAP,
        "components": [
            {
                "sheet": sheet_name,
                "component": component,
                "kind": "tbx",
                "source_lang": source_lang,
                "header_row": header_index + 1,
                "languages": [
                    {
                        "code": languages[col],
                        "xml_lang": languages[col].replace("_", "-"),
                        "column": col + 1,
                        "header": _cell(header_row, col),
                    }
                    for col in sorted(languages)
                ],
                "initial_target_languages": target_langs,
                "grammar": {
                    "type": "record-map",
                    "skip_rows": sorted(index + 1 for index in skip_rows),
                    "regions": regions,
                    "term_row_offset": 0,
                },
            }
        ],
    }
    return document, notes
