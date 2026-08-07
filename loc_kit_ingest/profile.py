# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loc_kit_ingest.model import Diagnostic, Severity

if TYPE_CHECKING:
    from pathlib import Path

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_XML_LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")

SCHEMA_VERSION = 1
SCHEMA_VERSION_RECORD_MAP = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, SCHEMA_VERSION_RECORD_MAP})

# Fields permitted per object, by nesting level.
_ROOT_FIELDS = frozenset({"schema_version", "components"})

_PO_FIELDS = frozenset(
    {
        "sheet",
        "component",
        "kind",
        "source_lang",
        "header_row",
        "first_data_row",
        "languages",
        "key",
        "comments",
        "references",
        "grammar",
    }
)
_TBX_FIELDS = frozenset(
    {
        "sheet",
        "component",
        "kind",
        "source_lang",
        "header_row",
        "languages",
        "grammar",
        "key_language",
        "initial_target_languages",
    }
)
_TBX_FIELDS_V2 = frozenset(
    {
        "sheet",
        "component",
        "kind",
        "source_lang",
        "header_row",
        "languages",
        "grammar",
        "initial_target_languages",
    }
)
_COMMON_FIELDS = frozenset(
    {
        "sheet",
        "component",
        "kind",
        "source_lang",
        "header_row",
        "languages",
        "grammar",
    }
)

_LANGUAGE_FIELDS = frozenset({"code", "xml_lang", "column", "header"})
_KEY_FIELDS = frozenset({"column", "header"})
_METADATA_FIELDS = frozenset({"column", "name", "header"})
_KEYED_GRAMMAR_FIELDS = frozenset({"type", "skip_rows", "allow_blank_rows"})
_PAIRS_GRAMMAR_FIELDS = frozenset({"type", "skip_rows", "regions"})
_REGION_FIELDS = frozenset(
    {
        "section_row",
        "first_term_row",
        "last_description_row",
    }
)

# Fields exclusive to each kind.
_PO_ONLY = frozenset({"first_data_row", "key", "comments", "references"})
_TBX_ONLY = frozenset({"key_language", "initial_target_languages"})
_TBX_ONLY_V2 = frozenset({"initial_target_languages"})

# Profile v2 record-map grammar.
_RECORD_MAP_GRAMMAR_FIELDS = frozenset(
    {
        "type",
        "skip_rows",
        "regions",
        "term_row_offset",
        "section_field",
        "notes",
    }
)
_RECORD_REGION_FIELDS = frozenset(
    {
        "first_record_row",
        "last_record_row",
        "record_stride",
        "section_row",
        "section_column",
    }
)
_SECTION_FIELD_FIELDS = frozenset({"column", "header", "row_offset"})
_NOTE_FIELD_FIELDS = frozenset({"scope", "column", "header", "row_offset", "language"})
_NOTE_SCOPES = frozenset({"source", "target"})


# --------------------------------------------------------------------------- #
# Profile records (immutable, 0-based internally)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LanguageColumn:
    code: str
    xml_lang: str
    column: int
    header: str


@dataclass(frozen=True)
class KeyColumn:
    column: int
    header: str


@dataclass(frozen=True)
class MetadataColumn:
    column: int
    name: str
    header: str


@dataclass(frozen=True)
class KeyedGrammar:
    skip_rows: tuple[int, ...]
    allow_blank_rows: bool


@dataclass(frozen=True)
class PairRegion:
    section_row: int
    first_term_row: int
    last_description_row: int


@dataclass(frozen=True)
class PairsGrammar:
    skip_rows: tuple[int, ...]
    regions: tuple[PairRegion, ...]


@dataclass(frozen=True)
class SectionField:
    column: int
    header: str
    row_offset: int


@dataclass(frozen=True)
class NoteField:
    scope: str  # "source" | "target"
    column: int
    header: str
    row_offset: int
    language: str | None  # set only when scope == "target"


@dataclass(frozen=True)
class RecordRegion:
    first_record_row: int
    last_record_row: int
    record_stride: int
    section_row: int | None
    section_column: int | None


@dataclass(frozen=True)
class RecordMapGrammar:
    skip_rows: tuple[int, ...]
    regions: tuple[RecordRegion, ...]
    term_row_offset: int
    section_field: SectionField | None
    notes: tuple[NoteField, ...]


@dataclass(frozen=True)
class ComponentProfile:
    sheet: str
    component: str
    kind: str
    source_lang: str
    header_row: int
    first_data_row: int | None
    languages: tuple[LanguageColumn, ...]
    key: KeyColumn | None
    comments: tuple[MetadataColumn, ...]
    references: tuple[MetadataColumn, ...]
    grammar: KeyedGrammar | PairsGrammar | RecordMapGrammar
    key_language: str | None
    initial_target_languages: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    schema_version: int
    components: tuple[ComponentProfile, ...]


# --------------------------------------------------------------------------- #
# Error type
# --------------------------------------------------------------------------- #


class ProfileError(Exception):
    """Raised when a profile is structurally invalid."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(f"[{diagnostic.code}] {diagnostic.message}")


def _err(
    code: str, message: str, *, component: str = "", sheet: str = "", row: int = 0
) -> ProfileError:
    return ProfileError(
        Diagnostic(Severity.ERROR, code, component, sheet, row, message)
    )


# --------------------------------------------------------------------------- #
# Field-level checks
# --------------------------------------------------------------------------- #


def _check_unknown(obj: dict[str, Any], allowed: frozenset[str], *, label: str) -> None:
    extras = set(obj) - allowed
    if extras:
        msg = "profile.unknown_field"
        raise _err(msg, f"unknown field(s) {extras} in {label}")


def _require(obj: dict[str, Any], field: str, *, label: str) -> Any:
    if field not in obj:
        msg = "profile.missing"
        raise _err(msg, f"missing required field '{field}' in {label}")
    return obj[field]


def _require_int(
    obj: dict[str, Any], field: str, *, label: str, min_val: int = 1
) -> int:
    val = _require(obj, field, label=label)
    if not isinstance(val, int) or isinstance(val, bool) or val < min_val:
        msg = "profile.invalid_index"
        raise _err(
            msg,
            f"field '{field}' in {label} must be an integer >= {min_val}, got {val!r}",
        )
    return val


# --------------------------------------------------------------------------- #
# Language column
# --------------------------------------------------------------------------- #


def _parse_language(obj: dict[str, Any], *, component: str) -> LanguageColumn:
    _check_unknown(obj, _LANGUAGE_FIELDS, label="language column")
    code = _require(obj, "code", label="language column")
    xml_lang = _require(obj, "xml_lang", label="language column")
    column = _require_int(obj, "column", label="language column")
    header = _require(obj, "header", label="language column")

    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        msg = "profile.invalid_code"
        raise _err(msg, f"invalid language code {code!r}")
    if not isinstance(xml_lang, str) or not _XML_LANG_RE.fullmatch(xml_lang):
        msg = "profile.invalid_xml_lang"
        raise _err(msg, f"invalid xml_lang {xml_lang!r}")
    if not isinstance(header, str):
        msg = "profile.invalid_header"
        raise _err(msg, f"invalid header {header!r}")

    return LanguageColumn(
        code=code,
        xml_lang=xml_lang,
        column=column - 1,  # 0-based
        header=header,
    )


# --------------------------------------------------------------------------- #
# Key / metadata columns
# --------------------------------------------------------------------------- #


def _parse_key_column(obj: dict[str, Any], *, label: str) -> KeyColumn:
    _check_unknown(obj, _KEY_FIELDS, label=label)
    column = _require_int(obj, "column", label=label)
    header = _require(obj, "header", label=label)
    if not isinstance(header, str):
        msg = "profile.invalid_header"
        raise _err(msg, f"invalid header {header!r}")
    return KeyColumn(column=column - 1, header=header)


def _parse_metadata_column(obj: dict[str, Any], *, label: str) -> MetadataColumn:
    _check_unknown(obj, _METADATA_FIELDS, label=label)
    column = _require_int(obj, "column", label=label)
    name = _require(obj, "name", label=label)
    header = _require(obj, "header", label=label)
    for field_val, field_name in [(name, "name"), (header, "header")]:
        if not isinstance(field_val, str):
            msg = "profile.invalid_value"
            raise _err(msg, f"invalid {field_name} {field_val!r}")
    return MetadataColumn(column=column - 1, name=name, header=header)


def _parse_metadata_list(
    obj: dict[str, Any] | None, field: str, *, component: str
) -> tuple[MetadataColumn, ...]:
    if obj is None:
        return ()
    items = obj.get(field)
    if items is None:
        return ()
    if not isinstance(items, list):
        msg = "profile.invalid_value"
        raise _err(msg, f"'{field}' must be a list")
    return tuple(_parse_metadata_column(item, label=f"{field} entry") for item in items)


# --------------------------------------------------------------------------- #
# Grammar (v1)
# --------------------------------------------------------------------------- #


def _parse_skip_rows(obj: dict[str, Any], *, label: str) -> tuple[int, ...]:
    val = obj.get("skip_rows", [])
    if not isinstance(val, list):
        msg = "profile.invalid_value"
        raise _err(msg, f"'skip_rows' in {label} must be a list")
    result: list[int] = []
    for r in val:
        if not isinstance(r, int) or isinstance(r, bool) or r < 1:
            msg = "profile.invalid_index"
            raise _err(
                msg,
                f"skip_rows entry {r!r} in {label} must be a positive integer",
            )
        result.append(r - 1)  # 0-based
    return tuple(result)


def _parse_keyed_grammar(obj: dict[str, Any]) -> KeyedGrammar:
    _check_unknown(obj, _KEYED_GRAMMAR_FIELDS, label="keyed grammar")
    skip_rows = _parse_skip_rows(obj, label="keyed grammar")
    allow_blank = obj.get("allow_blank_rows", False)
    if not isinstance(allow_blank, bool):
        msg = "profile.invalid_value"
        raise _err(msg, "'allow_blank_rows' must be a boolean")
    return KeyedGrammar(skip_rows=skip_rows, allow_blank_rows=allow_blank)


def _parse_region(obj: dict[str, Any], *, header_row_1based: int) -> PairRegion:
    _check_unknown(obj, _REGION_FIELDS, label="pair region")
    section_row = _require_int(obj, "section_row", label="pair region")
    first_term = _require_int(obj, "first_term_row", label="pair region")
    last_desc = _require_int(obj, "last_description_row", label="pair region")

    if section_row <= header_row_1based:
        msg = "profile.section_before_header"
        raise _err(
            msg,
            f"section_row ({section_row}) must be after header_row ({header_row_1based})",
        )

    if first_term > last_desc:
        msg = "profile.invalid_range"
        raise _err(
            msg,
            f"first_term_row ({first_term}) must be <= last_description_row ({last_desc})",
        )

    # The range [first_term, last_desc] must contain an even number of rows
    # (alternating term, description pairs).
    pair_span = last_desc - first_term + 1
    if pair_span % 2 != 0:
        msg = "profile.pair_span_odd"
        raise _err(
            msg,
            f"pair region rows {first_term}-{last_desc} span {pair_span} rows, "
            "must be even (term/description pairs)",
        )

    if section_row >= first_term:
        msg = "profile.section_outside_range"
        raise _err(
            msg,
            f"section_row ({section_row}) must be before first_term_row ({first_term})",
        )

    return PairRegion(
        section_row=section_row - 1,  # 0-based
        first_term_row=first_term - 1,
        last_description_row=last_desc - 1,
    )


def _parse_pairs_grammar(
    obj: dict[str, Any], *, header_row_1based: int
) -> PairsGrammar:
    _check_unknown(obj, _PAIRS_GRAMMAR_FIELDS, label="pairs grammar")
    skip_rows = _parse_skip_rows(obj, label="pairs grammar")
    regions_raw = _require(obj, "regions", label="pairs grammar")
    if not isinstance(regions_raw, list) or not regions_raw:
        msg = "profile.missing_regions"
        raise _err(
            msg,
            "pairs grammar must have at least one region",
        )
    regions = tuple(
        _parse_region(r, header_row_1based=header_row_1based) for r in regions_raw
    )

    # Sort regions by first_term_row to check for gaps and overlaps.
    sorted_regions = sorted(regions, key=lambda r: r.first_term_row)
    for prev, curr in zip(sorted_regions, sorted_regions[1:]):
        if curr.first_term_row <= prev.last_description_row:
            msg = "profile.region_overlap"
            raise _err(
                msg,
                f"regions overlap: [{prev.first_term_row + 1}-{prev.last_description_row + 1}] "
                f"and [{curr.first_term_row + 1}-{curr.last_description_row + 1}]",
            )
        if curr.section_row <= prev.last_description_row:
            msg = "profile.region_overlap"
            raise _err(
                msg,
                f"section row {curr.section_row + 1} is inside "
                f"previous region ending at {prev.last_description_row + 1}",
            )

    return PairsGrammar(skip_rows=skip_rows, regions=regions)


# --------------------------------------------------------------------------- #
# Grammar (v2 record-map)
# --------------------------------------------------------------------------- #


def _parse_section_field(obj: dict[str, Any]) -> SectionField:
    _check_unknown(obj, _SECTION_FIELD_FIELDS, label="section_field")
    column = _require_int(obj, "column", label="section_field")
    header = _require(obj, "header", label="section_field")
    row_offset = _require_int(obj, "row_offset", label="section_field", min_val=0)
    if not isinstance(header, str):
        msg = "profile.invalid_header"
        raise _err(msg, f"invalid header {header!r}")
    return SectionField(column=column - 1, header=header, row_offset=row_offset)


def _parse_note_field(
    obj: dict[str, Any], *, target_languages: tuple[str, ...]
) -> NoteField:
    _check_unknown(obj, _NOTE_FIELD_FIELDS, label="note field")
    scope = _require(obj, "scope", label="note field")
    if scope not in _NOTE_SCOPES:
        msg = "profile.invalid_note_scope"
        raise _err(msg, f"note scope must be 'source' or 'target', got {scope!r}")
    column = _require_int(obj, "column", label="note field")
    header = _require(obj, "header", label="note field")
    row_offset = _require_int(obj, "row_offset", label="note field", min_val=0)
    if not isinstance(header, str):
        msg = "profile.invalid_header"
        raise _err(msg, f"invalid header {header!r}")

    language = obj.get("language")
    if scope == "source":
        if language is not None:
            msg = "profile.unexpected_note_language"
            raise _err(msg, "a source note must not declare 'language'")
    elif not isinstance(language, str) or language not in target_languages:
        msg = "profile.unknown_note_language"
        raise _err(
            msg,
            f"target note language {language!r} is not an initial target "
            f"language {target_languages}",
        )

    return NoteField(
        scope=scope,
        column=column - 1,
        header=header,
        row_offset=row_offset,
        language=language,
    )


def _parse_record_region(obj: dict[str, Any]) -> RecordRegion:
    _check_unknown(obj, _RECORD_REGION_FIELDS, label="record region")
    first_record = _require_int(obj, "first_record_row", label="record region")
    last_record = _require_int(obj, "last_record_row", label="record region")
    stride = _require_int(obj, "record_stride", label="record region")

    if first_record > last_record:
        msg = "profile.invalid_range"
        raise _err(
            msg,
            f"first_record_row ({first_record}) must be <= last_record_row ({last_record})",
        )
    span = last_record - first_record + 1
    if span % stride != 0:
        msg = "profile.record_span_not_divisible"
        raise _err(
            msg,
            f"region rows {first_record}-{last_record} span {span} rows, "
            f"not divisible by record_stride {stride}",
        )

    section_row_raw = obj.get("section_row")
    section_column_raw = obj.get("section_column")
    if (section_row_raw is None) != (section_column_raw is None):
        msg = "profile.incomplete_section_cell"
        raise _err(
            msg,
            "'section_row' and 'section_column' must be declared together",
        )

    section_row: int | None = None
    section_column: int | None = None
    if section_row_raw is not None:
        if (
            not isinstance(section_row_raw, int)
            or isinstance(section_row_raw, bool)
            or section_row_raw < 1
        ):
            msg = "profile.invalid_index"
            raise _err(
                msg, f"section_row must be a positive integer, got {section_row_raw!r}"
            )
        if (
            not isinstance(section_column_raw, int)
            or isinstance(section_column_raw, bool)
            or section_column_raw < 1
        ):
            msg = "profile.invalid_index"
            raise _err(
                msg,
                f"section_column must be a positive integer, got {section_column_raw!r}",
            )
        if section_row_raw >= first_record:
            msg = "profile.section_outside_range"
            raise _err(
                msg,
                f"section_row ({section_row_raw}) must be before "
                f"first_record_row ({first_record})",
            )
        section_row = section_row_raw - 1
        section_column = section_column_raw - 1

    return RecordRegion(
        first_record_row=first_record - 1,
        last_record_row=last_record - 1,
        record_stride=stride,
        section_row=section_row,
        section_column=section_column,
    )


def _parse_record_map_grammar(
    obj: dict[str, Any], *, target_languages: tuple[str, ...]
) -> RecordMapGrammar:
    _check_unknown(obj, _RECORD_MAP_GRAMMAR_FIELDS, label="record-map grammar")
    skip_rows = _parse_skip_rows(obj, label="record-map grammar")

    regions_raw = _require(obj, "regions", label="record-map grammar")
    if not isinstance(regions_raw, list) or not regions_raw:
        msg = "profile.missing_regions"
        raise _err(msg, "record-map grammar must have at least one region")
    regions = tuple(_parse_record_region(r) for r in regions_raw)

    term_row_offset = _require_int(
        obj, "term_row_offset", label="record-map grammar", min_val=0
    )

    section_field_raw = obj.get("section_field")
    has_region_section = any(r.section_row is not None for r in regions)
    if section_field_raw is not None and has_region_section:
        msg = "profile.section_conflict"
        raise _err(
            msg,
            "a component declares 'grammar.section_field' or per-region section "
            "cells, never both",
        )
    section_field: SectionField | None = None
    if section_field_raw is not None:
        if not isinstance(section_field_raw, dict):
            msg = "profile.invalid_value"
            raise _err(msg, "'section_field' must be an object")
        section_field = _parse_section_field(section_field_raw)

    notes_raw = obj.get("notes", [])
    if not isinstance(notes_raw, list):
        msg = "profile.invalid_value"
        raise _err(msg, "'notes' must be a list")
    notes = tuple(
        _parse_note_field(n, target_languages=target_languages) for n in notes_raw
    )

    # record_stride bounds: every declared row_offset must be in [0, stride).
    strides = {r.record_stride for r in regions}
    for stride in strides:
        if term_row_offset >= stride:
            msg = "profile.offset_out_of_range"
            raise _err(
                msg,
                f"term_row_offset ({term_row_offset}) is out of range for "
                f"record_stride {stride}",
            )
        if section_field is not None and section_field.row_offset >= stride:
            msg = "profile.offset_out_of_range"
            raise _err(
                msg,
                f"section_field.row_offset ({section_field.row_offset}) is out of "
                f"range for record_stride {stride}",
            )
        for note in notes:
            if note.row_offset >= stride:
                msg = "profile.offset_out_of_range"
                raise _err(
                    msg,
                    f"note field row_offset ({note.row_offset}) is out of range "
                    f"for record_stride {stride}",
                )

    # Sort regions to check for overlap between records and section captions.
    sorted_regions = sorted(regions, key=lambda r: r.first_record_row)
    for prev, curr in zip(sorted_regions, sorted_regions[1:]):
        if curr.first_record_row <= prev.last_record_row:
            msg = "profile.region_overlap"
            raise _err(
                msg,
                f"regions overlap: [{prev.first_record_row + 1}-{prev.last_record_row + 1}] "
                f"and [{curr.first_record_row + 1}-{curr.last_record_row + 1}]",
            )
        if curr.section_row is not None and curr.section_row <= prev.last_record_row:
            msg = "profile.region_overlap"
            raise _err(
                msg,
                f"section row {curr.section_row + 1} is inside previous region "
                f"ending at {prev.last_record_row + 1}",
            )
    # A skip row inside a region is ambiguous: the record walk would import it
    # while the skip list claims it was left out, so the same row would be both
    # a term and a reported skip. Reject the profile instead of guessing.
    for skip in skip_rows:
        for region in regions:
            if region.first_record_row <= skip <= region.last_record_row:
                msg = "profile.skip_inside_region"
                raise _err(
                    msg,
                    f"skip_rows entry {skip + 1} falls inside record region "
                    f"{region.first_record_row + 1}-{region.last_record_row + 1}",
                )
            if region.section_row is not None and skip == region.section_row:
                msg = "profile.skip_inside_region"
                raise _err(
                    msg,
                    f"skip_rows entry {skip + 1} is a region section caption row",
                )

    return RecordMapGrammar(
        skip_rows=skip_rows,
        regions=regions,
        term_row_offset=term_row_offset,
        section_field=section_field,
        notes=notes,
    )


def _check_record_map_field_locations(
    grammar: RecordMapGrammar, *, languages: tuple[LanguageColumn, ...]
) -> None:
    """
    No two of {language term, note} fields may read the same (row_offset,
    column) cell, and section_field's column may not collide with any
    language or note column at any offset.
    """
    locations: dict[tuple[int, int], str] = {}
    for lang in languages:
        loc = (grammar.term_row_offset, lang.column)
        if loc in locations:
            msg = "profile.duplicate_field_location"
            raise _err(
                msg,
                f"language {lang.code!r} aliases the same cell as {locations[loc]}",
            )
        locations[loc] = f"language {lang.code!r}"
    for note in grammar.notes:
        loc = (note.row_offset, note.column)
        if loc in locations:
            msg = "profile.duplicate_field_location"
            raise _err(
                msg,
                f"note in column {note.column + 1} aliases the same cell "
                f"as {locations[loc]}",
            )
        locations[loc] = f"note in column {note.column + 1}"

    if grammar.section_field is not None:
        lang_columns = {lang.column for lang in languages}
        note_columns = {note.column for note in grammar.notes}
        if grammar.section_field.column in lang_columns | note_columns:
            msg = "profile.section_field_column_collision"
            raise _err(
                msg,
                f"section_field column {grammar.section_field.column + 1} collides "
                "with a language or note column",
            )


# --------------------------------------------------------------------------- #
# Component
# --------------------------------------------------------------------------- #


def _parse_component(
    obj: dict[str, Any],
    *,
    schema_version: int,
    seen_components: dict[str, str],
    seen_sheets: dict[str, str],
    seen_archives: dict[str, str],
) -> ComponentProfile:
    if not isinstance(obj, dict):
        msg = "profile.invalid_value"
        raise _err(msg, "component must be an object")

    kind = obj.get("kind")
    if kind not in ("po", "tbx"):
        msg = "profile.invalid_kind"
        raise _err(msg, f"unknown kind {kind!r}; must be 'po' or 'tbx'")

    is_v2_tbx = schema_version == SCHEMA_VERSION_RECORD_MAP and kind == "tbx"
    if is_v2_tbx:
        allowed = _TBX_FIELDS_V2
    else:
        allowed = _PO_FIELDS if kind == "po" else _TBX_FIELDS
    _check_unknown(obj, allowed, label=f"component ({kind})")

    sheet = _require(obj, "sheet", label="component")
    component = _require(obj, "component", label="component")
    source_lang = _require(obj, "source_lang", label="component")
    header_row = _require_int(obj, "header_row", label="component")

    if not isinstance(sheet, str) or not sheet:
        msg = "profile.invalid_value"
        raise _err(msg, "sheet must be a non-empty string")
    if not isinstance(component, str) or not _COMPONENT_RE.fullmatch(component):
        msg = "profile.unsafe_component"
        raise _err(
            msg,
            f"component name {component!r} does not match [A-Za-z0-9][A-Za-z0-9_-]*",
        )
    if not isinstance(source_lang, str) or not source_lang:
        msg = "profile.invalid_value"
        raise _err(msg, "source_lang must be a non-empty string")

    cf_component = component.casefold()
    if cf_component in seen_components:
        msg = "profile.duplicate_component"
        raise _err(
            msg,
            f"duplicate component name (case-insensitive): {component!r}",
        )
    cf_sheet = sheet.casefold()
    if cf_sheet in seen_sheets:
        msg = "profile.duplicate_sheet"
        raise _err(
            msg,
            f"duplicate sheet name (case-insensitive): {sheet!r}",
        )
    archive = f"{component}.zip".casefold()
    if archive in seen_archives:
        msg = "profile.duplicate_archive"
        raise _err(
            msg,
            f"duplicate archive name: {component}.zip",
        )
    seen_components[cf_component] = component
    seen_sheets[cf_sheet] = sheet
    seen_archives[archive] = component

    # Languages
    langs_raw = _require(obj, "languages", label="component")
    if not isinstance(langs_raw, list) or not langs_raw:
        msg = "profile.invalid_languages"
        raise _err(msg, "languages must be a non-empty list")
    languages = tuple(_parse_language(l, component=component) for l in langs_raw)

    # Check unique language codes and columns.
    seen_codes: dict[str, str] = {}
    seen_cols: dict[int, str] = {}
    for lang in languages:
        cf = lang.code.casefold()
        if cf in seen_codes:
            msg = "profile.duplicate_language"
            raise _err(
                msg,
                f"duplicate language code (case-insensitive): {lang.code!r}",
            )
        if lang.column in seen_cols:
            msg = "profile.duplicate_column"
            raise _err(
                msg,
                f"duplicate column {lang.column + 1} (language {lang.code!r})",
            )
        seen_codes[cf] = lang.code
        seen_cols[lang.column] = lang.code

    # Source language must be in languages.
    lang_codes = {l.code for l in languages}
    if source_lang not in lang_codes:
        msg = "profile.source_lang_missing"
        raise _err(
            msg,
            f"source_lang {source_lang!r} is not among language codes {lang_codes}",
        )

    # Kind-specific, non-grammar fields. Parsed before grammar because the v2
    # record-map grammar's target notes must be validated against
    # initial_target_languages.
    first_data_row: int | None = None
    key: KeyColumn | None = None
    comments: tuple[MetadataColumn, ...] = ()
    references: tuple[MetadataColumn, ...] = ()
    key_language: str | None = None
    initial_target_languages: tuple[str, ...] = ()

    if kind == "po":
        first_data_row = _require_int(obj, "first_data_row", label="component")
        if first_data_row <= header_row:
            msg = "profile.invalid_index"
            raise _err(
                msg,
                f"first_data_row ({first_data_row}) must be > header_row ({header_row})",
            )
        key_raw = _require(obj, "key", label="component")
        if not isinstance(key_raw, dict):
            msg = "profile.invalid_value"
            raise _err(msg, "key must be an object")
        key = _parse_key_column(key_raw, label="key column")
        comments = _parse_metadata_list(obj, "comments", component=component)
        references = _parse_metadata_list(obj, "references", component=component)
        first_data_row -= 1  # 0-based
    else:
        if not is_v2_tbx:
            key_language = _require(obj, "key_language", label="component")
            if not isinstance(key_language, str) or key_language not in lang_codes:
                msg = "profile.key_language_missing"
                raise _err(
                    msg,
                    f"key_language {key_language!r} is not among language codes "
                    f"{lang_codes}",
                )
        itl_raw = _require(obj, "initial_target_languages", label="component")
        if not isinstance(itl_raw, list) or not itl_raw:
            msg = "profile.empty_target_languages"
            raise _err(
                msg,
                "initial_target_languages must be a non-empty list",
            )
        if source_lang in itl_raw:
            msg = "profile.source_in_targets"
            raise _err(
                msg,
                f"source language {source_lang!r} cannot be in initial_target_languages",
            )
        for t in itl_raw:
            if not isinstance(t, str) or t not in lang_codes:
                msg = "profile.unknown_target_language"
                raise _err(
                    msg,
                    f"target language {t!r} is not among language codes",
                )
        initial_target_languages = tuple(itl_raw)

    # Grammar.
    grammar_raw = _require(obj, "grammar", label="component")
    if not isinstance(grammar_raw, dict):
        msg = "profile.invalid_value"
        raise _err(msg, "grammar must be an object")
    grammar_type = grammar_raw.get("type")
    if kind == "po":
        if grammar_type != "keyed":
            msg = "profile.grammar_mismatch"
            raise _err(
                msg,
                f"PO component requires grammar type 'keyed', got {grammar_type!r}",
            )
        grammar: KeyedGrammar | PairsGrammar | RecordMapGrammar = _parse_keyed_grammar(
            grammar_raw
        )
    elif is_v2_tbx:
        if grammar_type != "record-map":
            msg = "profile.grammar_mismatch"
            raise _err(
                msg,
                f"v2 TBX component requires grammar type 'record-map', "
                f"got {grammar_type!r}",
            )
        grammar = _parse_record_map_grammar(
            grammar_raw, target_languages=initial_target_languages
        )
        _check_record_map_field_locations(grammar, languages=languages)
    else:
        if grammar_type != "term-description-pairs":
            msg = "profile.grammar_mismatch"
            raise _err(
                msg,
                f"TBX component requires grammar type 'term-description-pairs', "
                f"got {grammar_type!r}",
            )
        grammar = _parse_pairs_grammar(grammar_raw, header_row_1based=header_row)

    return ComponentProfile(
        sheet=sheet,
        component=component,
        kind=kind,
        source_lang=source_lang,
        header_row=header_row - 1,  # 0-based
        first_data_row=first_data_row,
        languages=languages,
        key=key,
        comments=comments,
        references=references,
        grammar=grammar,
        key_language=key_language,
        initial_target_languages=initial_target_languages,
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def parse_profile(data: dict[str, Any]) -> Profile:
    """
    Validate an already-decoded profile document.

    Inferred profiles go through exactly this path, so a generated document is
    held to the same closed schema as a hand-written one. schema_version 1 is
    the closed keyed-PO/term-description-pairs schema; schema_version 2 adds
    only the TBX record-map grammar and never reinterprets a v1 document.
    """
    if not isinstance(data, dict):
        msg = "profile.not_object"
        raise _err(msg, "profile root must be a JSON object")

    _check_unknown(data, _ROOT_FIELDS, label="profile root")

    sv = data.get("schema_version")
    if sv not in SUPPORTED_SCHEMA_VERSIONS:
        msg = "profile.schema_version"
        raise _err(
            msg,
            f"unsupported schema_version {sv!r}; expected one of "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )

    components_raw = data.get("components")
    if not isinstance(components_raw, list) or not components_raw:
        msg = "profile.empty_components"
        raise _err(
            msg,
            "components must be a non-empty list",
        )

    seen_components: dict[str, str] = {}
    seen_sheets: dict[str, str] = {}
    seen_archives: dict[str, str] = {}
    components = tuple(
        _parse_component(
            c,
            schema_version=sv,
            seen_components=seen_components,
            seen_sheets=seen_sheets,
            seen_archives=seen_archives,
        )
        for c in components_raw
    )

    return Profile(schema_version=sv, components=components)


def load_profile(path: Path) -> Profile:
    if not path.is_file():
        msg = "profile.not_found"
        raise _err(msg, f"profile file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = "profile.read_error"
        raise _err(msg, f"cannot read profile: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = "profile.malformed_json"
        raise _err(msg, f"malformed JSON in profile: {exc}") from exc

    return parse_profile(data)
