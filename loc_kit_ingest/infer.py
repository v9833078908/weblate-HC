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
from statistics import median
from typing import Any

from translate.lang import data as lang_data

from loc_kit_ingest.langcode import language_code
from loc_kit_ingest.profile import SCHEMA_VERSION, SCHEMA_VERSION_RECORD_MAP

# Language columns filled below this share of content rows are stray spillover,
# not a translation: real languages in a kit cluster near 100% while accidental
# paste-overs sit under 1%.
DEFAULT_MIN_FILL = 5.0

_UNSAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_-]+")

# Markers some kits put in the key cell of a caption row.
_IGNORE_MARKERS = frozenset({"id-ignore", "id_ignore", "ignore"})

_KEY_COLUMN = 0

# Header text that must never be treated as a language column, even if it
# resolves to a real code (``id`` is also the Indonesian language code).
_KEY_HEADER_DENYLIST = frozenset({"id"})

# A column of prose about the term, not a translation of it. Recognised by
# header text alone: a length rule would accept "Character limit" and route it
# into every LLM prompt, where a wrong guess is invisible.
_NOTE_HEADERS = frozenset(
    {
        "note",
        "notes",
        "comment",
        "comments",
        "description",
        "descriptions",
        "explanation",
        "explanations",
        "context",
        "usage",
        "definition",
        "meaning",
        "примечание",
        "примечания",
        "комментарий",
        "комментарии",
        "описание",
        "описания",
        "пояснение",
        "пояснения",
        "контекст",
        "определение",
        "значение",
    }
)


_IGNORABLE_HEADERS = frozenset({"id"})
# Term/description detection. A glossary term is a name, a description is
# prose: the gap is an order of magnitude in practice. Both bounds must hold,
# so a kit of long terms falls back to one term per row instead of guessing.
_DESCRIPTION_MIN_CHARS = 80
_DESCRIPTION_RATIO = 4.0

_LAYOUTS = frozenset({"auto", "flat", "pairs"})

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
            if (code := language_code(_cell(row, col))) is not None
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
    key_language_code = language_code(key_header)
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


def _classify_block(
    rows: list[list[str]], block: list[int], source_col: int, layout: str
) -> tuple[str, int | None, str | None]:
    """
    Decide how one contiguous block of term rows is laid out.

    Returns (kind, section row index or None, note). ``kind`` is ``"flat"``
    for one term per row or ``"pairs"`` for a term followed by its
    description. A block of odd length can only be paired when its first row
    is a section caption, which is what makes the count work out.

    This is a proposal, never the parse semantics: the emitted profile still
    goes through the full parse, render and parse-back gate, and a human sees
    the terms before anything is created. That is why measuring text length
    is acceptable here and forbidden in the parser.
    """
    size = len(block)
    if layout == "flat":
        return "flat", None, None

    section = block[0] if size % 2 else None
    if layout == "pairs":
        if size < 2 or (section is not None and size < 3):
            msg = (
                f"row {block[0] + 1} stands alone; a term/description block "
                "needs at least one pair"
            )
            raise InferenceError(msg)
        return "pairs", section, None

    lengths = [len(_cell(rows[index], source_col).strip()) for index in block]
    long_rows = [
        block[offset] + 1
        for offset, length in enumerate(lengths)
        if length >= _DESCRIPTION_MIN_CHARS
    ]
    if not long_rows:
        return "flat", None, None

    body = lengths[1:] if section is not None else lengths
    terms, descriptions = body[0::2], body[1::2]
    floor = max(
        _DESCRIPTION_MIN_CHARS,
        _DESCRIPTION_RATIO * median(terms) if terms else _DESCRIPTION_MIN_CHARS,
    )
    alternates = (
        len(body) >= 2
        and len(terms) == len(descriptions)
        and all(length < _DESCRIPTION_MIN_CHARS for length in terms)
        and all(length >= floor for length in descriptions)
        and (section is None or lengths[0] < _DESCRIPTION_MIN_CHARS)
    )
    if alternates:
        return "pairs", section, None

    shown = ", ".join(str(row) for row in long_rows[:_MISSING_ROWS_SHOWN])
    note = (
        f"row(s) {shown} hold long text but do not alternate with terms; "
        "imported as terms - switch the layout if they are descriptions"
    )
    return "flat", None, note


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
    notes.extend(
        f"column {col + 1} ({_cell(header_row, col)!r}) is empty; excluded"
        for col in recognised
        if col not in populated
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


def infer_glossary_profile(
    sheet_name: str,
    rows: list[list[str]],
    *,
    component: str,
    min_fill: float = DEFAULT_MIN_FILL,
    layout: str = "auto",
) -> tuple[dict[str, Any], list[str]]:
    """
    Infer a schema v2 record-map TBX profile for one worksheet.

    Targets have at least one term and meet ``min_fill`` across term rows.
    Target gaps declare ``allow_empty_targets``. A technical ``id`` column is
    declared in ``ignored_columns``; every other populated unmapped column is
    refused. ``layout`` is ``"auto"`` (classify each block), ``"flat"`` (one
    term per row) or ``"pairs"`` (a term followed by its description).
    Returns (document, notes).
    """
    if layout not in _LAYOUTS:
        msg = f"unknown layout {layout!r}"
        raise InferenceError(msg)
    if not rows:
        msg = f"sheet {sheet_name!r} is empty"
        raise InferenceError(msg)

    header_index, candidates = _find_header_row(rows)
    header_row = rows[header_index]
    notes: list[str] = []

    # Column 0 is an ordinary column here; promote it when its header is a
    # language code (a keyless kit like ``ru,en,ja`` keeps terms in column 1).
    first_header = _cell(header_row, _KEY_COLUMN)
    first_code = language_code(first_header)
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
    note_col = _find_note_column(header_row, populated, languages, notes)
    mapped = set(languages)
    if note_col is not None:
        mapped.add(note_col)
    ignored_cols: list[int] = []
    for col in sorted(populated - mapped):
        header_text = _cell(header_row, col)
        if header_text.strip().casefold() in _IGNORABLE_HEADERS:
            ignored_cols.append(col)
            notes.append(
                f"column {col + 1} ({header_text!r}) is a technical "
                "identifier; not imported"
            )
            continue
        msg = (
            f"column {col + 1} ({header_text or f'column{col + 1}'!r}) holds "
            "data but is not a recognised language column; rename the header "
            "to a recognised term-note header, for example note, description, "
            "comment, or explanation, or supply an explicit profile"
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

    blocks: list[list[int]] = []
    for index in record_rows:
        if blocks and index == blocks[-1][-1] + 1:
            blocks[-1].append(index)
        else:
            blocks.append([index])

    # The profile validator cross-multiplies skip_rows by regions, so an
    # alternating filled/blank source column would otherwise turn a small
    # sheet into a quadratic parse.
    if len(blocks) > _MAX_REGIONS:
        msg = (
            f"sheet {sheet_name!r} splits into {len(blocks)} blocks of "
            f"consecutive terms (limit {_MAX_REGIONS}); too fragmented to map "
            "deterministically"
        )
        raise InferenceError(msg)

    shapes: list[tuple[list[int], str, int | None]] = []
    for block in blocks:
        kind, section, note = _classify_block(rows, block, source_col, layout)
        shapes.append((block, kind, section))
        if note is not None:
            notes.append(note)
    kinds = {kind for _block, kind, _section in shapes}
    if len(kinds) > 1:
        # ``notes`` and ``term_row_offset`` are grammar-wide, so a stride-2
        # note offset would read the next term of a stride-1 region as its
        # description. One sheet, one layout.
        msg = (
            f"sheet {sheet_name!r} mixes term/description blocks with blocks "
            "of plain terms; this layout needs an explicit profile"
        )
        raise InferenceError(msg)
    paired = kinds == {"pairs"}

    regions: list[dict[str, int]] = []
    term_rows: list[int] = []
    description_rows: list[int] = []
    for block, kind, section in shapes:
        first = block[0] if section is None else block[1]
        region = {
            "first_record_row": first + 1,
            "last_record_row": block[-1] + 1,
            "record_stride": 2 if kind == "pairs" else 1,
        }
        if section is not None:
            region["section_row"] = section + 1
            region["section_column"] = source_col + 1
        regions.append(region)
        step = 2 if kind == "pairs" else 1
        rows_in_region = range(first, block[-1] + 1)
        term_rows.extend(rows_in_region[::step])
        description_rows.extend(rows_in_region[1::step] if step == 2 else [])
    target_langs: list[str] = []
    allow_empty_targets = False
    for col in sorted(languages):
        if col == source_col:
            continue
        code = languages[col]
        missing_rows = [
            index + 1 for index in term_rows if not _cell(rows[index], col).strip()
        ]
        term_filled = len(term_rows) - len(missing_rows)
        if not term_filled:
            continue
        share = 100.0 * term_filled / len(term_rows)
        if share < min_fill:
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is "
                f"filled in {term_filled}/{len(term_rows)} term rows "
                f"({share:.1f}%); too sparse to map deterministically"
            )
            raise InferenceError(msg)
        target_langs.append(code)
        if missing_rows:
            allow_empty_targets = True
            shown = ", ".join(str(row) for row in missing_rows[:_MISSING_ROWS_SHOWN])
            if len(missing_rows) > _MISSING_ROWS_SHOWN:
                shown += f", +{len(missing_rows) - _MISSING_ROWS_SHOWN} more"
            notes.append(
                f"language {code} has no term on {len(missing_rows)} row(s) "
                f"({shown}); imported untranslated"
            )
    if not target_langs:
        msg = f"sheet {sheet_name!r} has no target language with a term"
        raise InferenceError(msg)

    grammar: dict[str, Any] = {
        "type": "record-map",
        "skip_rows": sorted(index + 1 for index in skip_rows),
        "regions": regions,
        "term_row_offset": 0,
    }
    if ignored_cols:
        grammar["ignored_columns"] = [
            {"column": col + 1, "header": _cell(header_row, col)}
            for col in ignored_cols
        ]
    if allow_empty_targets:
        grammar["allow_empty_targets"] = True
    note_fields: list[dict[str, Any]] = []
    if paired:
        # A description cell is read by a note field, and a note field may
        # only name an initial target language. A language that carries
        # descriptions but misses a term has nowhere to put them, and every
        # unread populated cell is a parse error - refuse instead of
        # emitting a profile that cannot survive its own gate.
        for col in sorted(languages):
            code = languages[col]
            if col == source_col or code in target_langs:
                continue
            offenders = [
                index + 1
                for index in description_rows
                if _cell(rows[index], col).strip()
            ][:_MISSING_ROWS_SHOWN]
            if offenders:
                shown = ", ".join(str(row) for row in offenders)
                msg = (
                    f"language {code} has descriptions on row(s) {shown} but "
                    "is missing terms; this layout needs an explicit profile"
                )
                raise InferenceError(msg)
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
                "grammar": grammar,
            }
        ],
    }
    return document, notes
