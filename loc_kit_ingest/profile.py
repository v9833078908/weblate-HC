# Copyright (C) HCGameLoc
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
    grammar: KeyedGrammar | PairsGrammar
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
# Grammar
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
# Component
# --------------------------------------------------------------------------- #


def _parse_component(
    obj: dict[str, Any],
    *,
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
        grammar: KeyedGrammar | PairsGrammar = _parse_keyed_grammar(grammar_raw)
    else:
        if grammar_type != "term-description-pairs":
            msg = "profile.grammar_mismatch"
            raise _err(
                msg,
                f"TBX component requires grammar type 'term-description-pairs', "
                f"got {grammar_type!r}",
            )
        grammar = _parse_pairs_grammar(grammar_raw, header_row_1based=header_row)

    # Kind-specific fields.
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
        key_language = _require(obj, "key_language", label="component")
        if not isinstance(key_language, str) or key_language not in lang_codes:
            msg = "profile.key_language_missing"
            raise _err(
                msg,
                f"key_language {key_language!r} is not among language codes {lang_codes}",
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
    held to the same closed schema as a hand-written one.
    """
    if not isinstance(data, dict):
        msg = "profile.not_object"
        raise _err(msg, "profile root must be a JSON object")

    _check_unknown(data, _ROOT_FIELDS, label="profile root")

    sv = data.get("schema_version")
    if sv != SCHEMA_VERSION:
        msg = "profile.schema_version"
        raise _err(
            msg,
            f"unsupported schema_version {sv!r}; expected {SCHEMA_VERSION}",
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
