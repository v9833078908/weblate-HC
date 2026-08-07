# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import unicodedata
from typing import TYPE_CHECKING

from loc_kit_ingest.model import (
    Diagnostic,
    GlossaryTerm,
    ParseResult,
    Severity,
    SkippedRow,
    StringUnit,
)
from loc_kit_ingest.profile import KeyedGrammar, PairsGrammar, RecordMapGrammar

if TYPE_CHECKING:
    from loc_kit_ingest.profile import ComponentProfile


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_blank(value: str) -> bool:
    """Structural blank check only - never used to store trimmed text."""
    return value.strip() == ""


def _cell(rows: list[list[str]], row_idx: int, col_idx: int) -> str:
    """Get a cell value, returning '' for out-of-bounds."""
    if row_idx < 0 or row_idx >= len(rows):
        return ""
    row = rows[row_idx]
    if col_idx < 0 or col_idx >= len(row):
        return ""
    return row[col_idx]


def _script_ratio(text: str, target_block: str) -> float:
    """
    Fraction of alphabetic chars that belong to target Unicode block.

    Returns 0.0 for empty text.
    """
    total = 0
    matched = 0
    for ch in text:
        if not ch.isalpha():
            continue
        total += 1
        if any(
            unicodedata.category(ch) == "Lu" or unicodedata.category(ch) == "Ll"
            for _ in [0]
        ):
            pass  # always true for isalpha
        if _is_in_block(ch, target_block):
            matched += 1
    if total == 0:
        return 0.0
    return matched / total


_CYRILLIC_START = 0x0400
_CYRILLIC_END = 0x04FF


def _is_in_block(ch: str, block: str) -> bool:
    cp = ord(ch)
    if block == "cyrillic":
        return _CYRILLIC_START <= cp <= _CYRILLIC_END
    if block == "latin":
        return cp <= 0x024F  # Basic Latin + Latin Extended
    return False


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def parse_component(component: ComponentProfile, rows: list[list[str]]) -> ParseResult:
    """Dispatch to the appropriate parser based on kind/grammar."""
    if component.kind == "po":
        return _parse_keyed(component, rows)
    if component.kind == "tbx":
        if isinstance(component.grammar, RecordMapGrammar):
            return _parse_record_map(component, rows)
        if isinstance(component.grammar, PairsGrammar):
            return _parse_pairs(component, rows)
    return ParseResult(
        component=component.component,
        kind=component.kind,
        units=(),
        diagnostics=(
            Diagnostic(
                Severity.ERROR,
                "parser.unknown_kind",
                component.component,
                component.sheet,
                0,
                f"unknown kind {component.kind!r}",
            ),
        ),
        skipped_rows=(),
    )


# --------------------------------------------------------------------------- #
# Keyed grammar (PO)
# --------------------------------------------------------------------------- #


def _parse_keyed(component: ComponentProfile, rows: list[list[str]]) -> ParseResult:
    grammar = component.grammar
    assert isinstance(grammar, KeyedGrammar)
    assert component.first_data_row is not None
    assert component.key is not None

    skip_set = set(grammar.skip_rows)  # 0-based
    source_lang = component.source_lang
    source_col = next(l.column for l in component.languages if l.code == source_lang)

    units: list[StringUnit] = []
    diagnostics: list[Diagnostic] = []
    skipped: list[SkippedRow] = []
    seen_keys: dict[str, int] = {}

    # Build column maps
    lang_columns = {l.code: l.column for l in component.languages}
    comment_cols = component.comments
    reference_cols = component.references

    for row_idx in range(component.header_row + 1, len(rows)):
        row_1based = row_idx + 1

        # Explicit skip
        if row_idx in skip_set:
            skipped.append(
                SkippedRow(
                    component.component, component.sheet, row_1based, "profile_skip"
                )
            )
            continue

        # Rows before first_data_row that aren't explicitly skipped are ignored
        if row_idx < component.first_data_row:
            continue

        row = rows[row_idx]
        # Check blank row
        if _is_blank_row(row):
            if grammar.allow_blank_rows:
                skipped.append(
                    SkippedRow(
                        component.component, component.sheet, row_1based, "blank"
                    )
                )
                continue
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "grammar.unexpected_row",
                    component.component,
                    component.sheet,
                    row_1based,
                    "unexpected blank row",
                )
            )
            continue

        # Extract key
        key = _cell(rows, row_idx, component.key.column)

        # Extract source
        source_val = _cell(rows, row_idx, source_col)

        # Check short row
        max_col = max(
            [component.key.column, source_col]
            + [c.column for c in comment_cols]
            + [r.column for r in reference_cols]
            + list(lang_columns.values())
        )
        if len(row) <= max_col and len(row) < max_col + 1:
            # Row doesn't have enough columns for the key and source
            if len(row) <= component.key.column or len(row) <= source_col:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "po.short_row",
                        component.component,
                        component.sheet,
                        row_1based,
                        f"row has {len(row)} columns, needs at least {max_col + 1}",
                    )
                )
                continue

        # Missing source
        if _is_blank(source_val):
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "po.missing_source",
                    component.component,
                    component.sheet,
                    row_1based,
                    "source cell is empty",
                )
            )
            continue

        # Duplicate key
        if key in seen_keys:
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "po.duplicate_key",
                    component.component,
                    component.sheet,
                    row_1based,
                    f"duplicate key {key!r}, first seen at row {seen_keys[key]}",
                )
            )
            continue
        seen_keys[key] = row_1based

        # Build language values
        values: dict[str, str] = {}
        for code, col in lang_columns.items():
            values[code] = _cell(rows, row_idx, col)

        # Build comments
        comments = tuple(
            _cell(rows, row_idx, c.column)
            for c in comment_cols
            if _cell(rows, row_idx, c.column)
        )

        # Build references
        references = tuple(
            _cell(rows, row_idx, r.column)
            for r in reference_cols
            if _cell(rows, row_idx, r.column)
        )

        units.append(
            StringUnit(
                key=key,
                values=values,
                comments=comments,
                references=references,
                row=row_1based,
            )
        )

        # Warnings
        _add_keyed_warnings(diagnostics, component, row_1based, values, source_lang)

    return ParseResult(
        component=component.component,
        kind="po",
        units=tuple(units),
        diagnostics=tuple(diagnostics),
        skipped_rows=tuple(skipped),
    )


def _is_blank_row(row: list[str]) -> bool:
    return all(_is_blank(cell) for cell in row)


def _add_keyed_warnings(
    diagnostics: list[Diagnostic],
    component: ComponentProfile,
    row_1based: int,
    values: dict[str, str],
    source_lang: str,
) -> None:
    """
    Emit warnings without rewriting values.

    Script checks compare en for Cyrillic and ru for Latin, independently.
    These are the only script checks and do not overlap each other.
    """
    for code, val in values.items():
        if code == source_lang:
            # Source-language wrong-script: ru should not be mostly Latin.
            if code == "ru" and _script_ratio(val, "latin") > 0.5:
                diagnostics.append(
                    Diagnostic(
                        Severity.WARNING,
                        "po.wrong_script",
                        component.component,
                        component.sheet,
                        row_1based,
                        f"language {code!r} has mostly Latin characters",
                    )
                )
            continue

        target_val = val
        source_val = values.get(source_lang, "")

        # Empty target warning
        if _is_blank(target_val) and not _is_blank(source_val):
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "po.empty_target",
                    component.component,
                    component.sheet,
                    row_1based,
                    f"empty target for language {code!r}",
                )
            )

        # Target equals source warning
        if target_val == source_val and not _is_blank(source_val):
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "po.target_equals_source",
                    component.component,
                    component.sheet,
                    row_1based,
                    f"target equals source for language {code!r}",
                )
            )

        # Wrong script: en with Cyrillic majority.
        if code == "en" and _script_ratio(target_val, "cyrillic") > 0.5:
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "po.wrong_script",
                    component.component,
                    component.sheet,
                    row_1based,
                    f"language {code!r} has mostly Cyrillic characters",
                )
            )


# --------------------------------------------------------------------------- #
# TBX shared helpers
# --------------------------------------------------------------------------- #


def _context_key(section: str, source_term: str) -> str:
    """
    Build a stable, collision-free context from (section, source term).

    Compact JSON with ``ensure_ascii=False`` keeps Cyrillic and CJK intact -
    Weblate stores ``Unit.context`` in a TextField, so a glossary never needs
    a Latin-script column. JSON quoting is what makes it collision-free: a
    section or term containing the separator cannot forge another pair's key.
    """
    return json.dumps([section, source_term], ensure_ascii=False, separators=(",", ":"))


def _join_notes(values: list[str]) -> str:
    """Concatenate declared note cells in declaration order, blanks dropped."""
    return "\n".join(value for value in values if not _is_blank(value))


def _has_outer_whitespace(value: str) -> bool:
    """Check for leading or trailing whitespace."""
    return value != value.strip()


def _parse_pairs(component: ComponentProfile, rows: list[list[str]]) -> ParseResult:
    """Parse term-description-pairs grammar for TBX components."""
    grammar = component.grammar
    assert isinstance(grammar, PairsGrammar)
    skip_set = set(grammar.skip_rows)  # 0-based

    source_lang = component.source_lang
    lang_columns = {l.code: l.column for l in component.languages}
    target_langs = component.initial_target_languages

    # Build the set of all covered data rows (0-based).
    covered_rows: set[int] = set()
    for region in grammar.regions:
        covered_rows.add(region.section_row)
        covered_rows.update(
            range(region.first_term_row, region.last_description_row + 1)
        )

    units: list[GlossaryTerm] = []
    diagnostics: list[Diagnostic] = []
    skipped: list[SkippedRow] = []
    seen_contexts: set[str] = set()

    # Process regions in order.
    for region in grammar.regions:
        section_row_idx = region.section_row
        section_1based = section_row_idx + 1

        # Section text
        section_text = _cell(rows, section_row_idx, 0)  # section is in col 0
        if _is_blank(section_text):
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "tbx.missing_section",
                    component.component,
                    component.sheet,
                    section_1based,
                    "section name is empty",
                )
            )
            continue

        # Walk term/description pairs.
        row_idx = region.first_term_row
        while row_idx <= region.last_description_row:
            term_row_idx = row_idx
            desc_row_idx = row_idx + 1
            term_1based = term_row_idx + 1
            desc_1based = desc_row_idx + 1

            # Check for orphan description (term row is blank but desc is not).
            term_row_data = rows[term_row_idx] if term_row_idx < len(rows) else []
            desc_row_data = rows[desc_row_idx] if desc_row_idx < len(rows) else []
            term_is_blank = _is_blank_row(term_row_data)
            desc_is_blank = _is_blank_row(desc_row_data)

            if term_is_blank and not desc_is_blank:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.orphan_description",
                        component.component,
                        component.sheet,
                        desc_1based,
                        "description without a term",
                    )
                )
                row_idx += 2
                continue

            if term_is_blank and desc_is_blank:
                row_idx += 2
                continue

            # Extract term values per language.
            term_values: dict[str, str] = {}
            for code, col in lang_columns.items():
                term_values[code] = _cell(rows, term_row_idx, col)

            # Check outer whitespace on terms.
            for code, col in lang_columns.items():
                val = term_values[code]
                if not _is_blank(val) and _has_outer_whitespace(val):
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "tbx.unsupported_outer_whitespace",
                            component.component,
                            component.sheet,
                            term_1based,
                            f"term in language {code!r} has leading or trailing whitespace",
                        )
                    )

            # Source term must exist.
            source_term = term_values.get(source_lang, "")
            if _is_blank(source_term):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.missing_term",
                        component.component,
                        component.sheet,
                        term_1based,
                        f"source term in language {source_lang!r} is empty",
                    )
                )

            # All initial_target_languages require a non-empty term.
            for tlang in target_langs:
                if _is_blank(term_values.get(tlang, "")):
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "tbx.missing_target_term",
                            component.component,
                            component.sheet,
                            term_1based,
                            f"target term in language {tlang!r} is empty",
                        )
                    )

            # Extract explanation values per language.
            desc_values: dict[str, str] = {}
            for code, col in lang_columns.items():
                desc_values[code] = _cell(rows, desc_row_idx, col)

            # Check outer whitespace on explanations.
            for code, col in lang_columns.items():
                val = desc_values[code]
                if not _is_blank(val) and _has_outer_whitespace(val):
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "tbx.unsupported_outer_whitespace",
                            component.component,
                            component.sheet,
                            desc_1based,
                            f"explanation in language {code!r} has leading or trailing whitespace",
                        )
                    )

            # Source explanation must exist.
            source_expl = desc_values.get(source_lang, "")
            if _is_blank(source_expl):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.missing_explanation",
                        component.component,
                        component.sheet,
                        desc_1based,
                        f"source explanation in language {source_lang!r} is empty",
                    )
                )

            # Build context from (section, source term): Unicode-safe and
            # collision-free, so a glossary needs no Latin-script column.
            context = _context_key(section_text, source_term)
            if context in seen_contexts:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.duplicate_context",
                        component.component,
                        component.sheet,
                        term_1based,
                        f"duplicate context {context!r}",
                    )
                )
            else:
                seen_contexts.add(context)

            units.append(
                GlossaryTerm(
                    context=context,
                    values=term_values,
                    source_explanation=source_expl,
                    target_explanations={
                        code: desc_values.get(code, "") for code in target_langs
                    },
                    section=section_text,
                    term_row=term_1based,
                    note_rows=(desc_1based,),
                )
            )

            row_idx += 2

    # Check for uncovered nonblank rows.
    for row_idx in range(component.header_row + 1, len(rows)):
        if row_idx in skip_set:
            skipped.append(
                SkippedRow(
                    component.component, component.sheet, row_idx + 1, "profile_skip"
                )
            )
            continue
        if row_idx not in covered_rows:
            row_data = rows[row_idx]
            if not _is_blank_row(row_data):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "grammar.uncovered_row",
                        component.component,
                        component.sheet,
                        row_idx + 1,
                        "nonblank row is not covered by any region or skip",
                    )
                )

    return ParseResult(
        component=component.component,
        kind="tbx",
        units=tuple(units),
        diagnostics=tuple(diagnostics),
        skipped_rows=tuple(skipped),
    )


# --------------------------------------------------------------------------- #
# Record-map grammar (TBX, profile v2)
# --------------------------------------------------------------------------- #


def _parse_record_map(
    component: ComponentProfile, rows: list[list[str]]
) -> ParseResult:
    """
    Parse the generic record-map grammar for TBX components.

    A record is a fixed group of ``record_stride`` rows. Terms are read at
    ``term_row_offset``; every other declared field names its own offset. A
    populated cell inside a record that no declared field consumes is an
    error, never silently discarded.
    """
    grammar = component.grammar
    assert isinstance(grammar, RecordMapGrammar)
    skip_set = set(grammar.skip_rows)  # 0-based

    source_lang = component.source_lang
    lang_columns = {l.code: l.column for l in component.languages}
    target_langs = component.initial_target_languages
    source_notes = [n for n in grammar.notes if n.scope == "source"]
    target_notes: dict[str, list] = {code: [] for code in target_langs}
    for note in grammar.notes:
        if note.scope == "target" and note.language is not None:
            target_notes[note.language].append(note)

    units: list[GlossaryTerm] = []
    diagnostics: list[Diagnostic] = []
    skipped: list[SkippedRow] = []
    seen_contexts: set[str] = set()

    # Every row a region, caption, or skip accounts for. Anything else that
    # holds data trips grammar.uncovered_row.
    covered_rows: set[int] = set()
    for region in grammar.regions:
        if region.section_row is not None:
            covered_rows.add(region.section_row)
        covered_rows.update(range(region.first_record_row, region.last_record_row + 1))

    def err(code: str, row_1based: int, message: str) -> None:
        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                code,
                component.component,
                component.sheet,
                row_1based,
                message,
            )
        )

    def read_field(base_row: int, row_offset: int, column: int) -> str:
        return _cell(rows, base_row + row_offset, column)

    for region in grammar.regions:
        # A caption cell above the block, when the sheet groups terms under
        # headings instead of repeating a domain column.
        region_section = ""
        if region.section_row is not None:
            assert region.section_column is not None
            region_section = _cell(rows, region.section_row, region.section_column)
            if _is_blank(region_section):
                err(
                    "tbx.missing_section",
                    region.section_row + 1,
                    "section caption cell is empty",
                )
                continue
            # A caption row may repeat the section label in declared language
            # columns. Those labels are metadata, never terms - but any other
            # populated cell on that row is unaccounted for.
            allowed = {region.section_column, *lang_columns.values()}
            caption_row = (
                rows[region.section_row] if region.section_row < len(rows) else []
            )
            for col, value in enumerate(caption_row):
                if col not in allowed and not _is_blank(value):
                    err(
                        "tbx.unmapped_cell",
                        region.section_row + 1,
                        f"column {col + 1} on a caption row is not declared",
                    )

        for base_row in range(
            region.first_record_row, region.last_record_row + 1, region.record_stride
        ):
            term_row_idx = base_row + grammar.term_row_offset
            term_1based = term_row_idx + 1
            consumed: set[tuple[int, int]] = set()

            # Section: per-record column, or the region's caption.
            section_text = region_section
            if grammar.section_field is not None:
                field = grammar.section_field
                section_text = read_field(base_row, field.row_offset, field.column)
                consumed.add((base_row + field.row_offset, field.column))
                # A blank domain cell means "no section"; it is never
                # inherited from the previous record.
                if _is_blank(section_text):
                    section_text = ""

            # Terms.
            term_values: dict[str, str] = {}
            for code, col in lang_columns.items():
                value = read_field(base_row, grammar.term_row_offset, col)
                term_values[code] = value
                consumed.add((term_row_idx, col))
                if not _is_blank(value) and _has_outer_whitespace(value):
                    err(
                        "tbx.unsupported_outer_whitespace",
                        term_1based,
                        f"term in language {code!r} has leading or trailing whitespace",
                    )

            source_term = term_values.get(source_lang, "")
            if _is_blank(source_term):
                err(
                    "tbx.missing_term",
                    term_1based,
                    f"source term in language {source_lang!r} is empty",
                )
            for tlang in target_langs:
                if _is_blank(term_values.get(tlang, "")):
                    err(
                        "tbx.missing_target_term",
                        term_1based,
                        f"target term in language {tlang!r} is empty",
                    )

            # Notes, in declaration order.
            note_rows: list[int] = []

            def collect(notes: list) -> list[str]:
                values: list[str] = []
                for note in notes:
                    row_idx = base_row + note.row_offset
                    value = _cell(rows, row_idx, note.column)
                    consumed.add((row_idx, note.column))
                    if not _is_blank(value):
                        note_rows.append(row_idx + 1)
                        if _has_outer_whitespace(value):
                            err(
                                "tbx.unsupported_outer_whitespace",
                                row_idx + 1,
                                f"note in column {note.column + 1} has leading "
                                "or trailing whitespace",
                            )
                    values.append(value)
                return values

            source_explanation = _join_notes(collect(source_notes))
            target_explanations = {
                code: _join_notes(collect(target_notes[code])) for code in target_langs
            }

            # Any populated cell in this record that no field claimed.
            for offset in range(region.record_stride):
                row_idx = base_row + offset
                if row_idx >= len(rows):
                    continue
                for col, value in enumerate(rows[row_idx]):
                    if (row_idx, col) not in consumed and not _is_blank(value):
                        err(
                            "tbx.unmapped_cell",
                            row_idx + 1,
                            f"column {col + 1} holds data but no declared field "
                            "reads it",
                        )

            context = _context_key(section_text, source_term)
            if context in seen_contexts:
                err(
                    "tbx.duplicate_context",
                    term_1based,
                    f"duplicate context {context!r}",
                )
            else:
                seen_contexts.add(context)

            units.append(
                GlossaryTerm(
                    context=context,
                    values=term_values,
                    source_explanation=source_explanation,
                    target_explanations=target_explanations,
                    section=section_text,
                    term_row=term_1based,
                    note_rows=tuple(note_rows),
                )
            )

    # Uncovered nonblank rows after the header.
    for row_idx in range(component.header_row + 1, len(rows)):
        if row_idx in skip_set:
            skipped.append(
                SkippedRow(
                    component.component, component.sheet, row_idx + 1, "profile_skip"
                )
            )
            continue
        if row_idx not in covered_rows and not _is_blank_row(rows[row_idx]):
            err(
                "grammar.uncovered_row",
                row_idx + 1,
                "nonblank row is not covered by any region or skip",
            )

    return ParseResult(
        component=component.component,
        kind="tbx",
        units=tuple(units),
        diagnostics=tuple(diagnostics),
        skipped_rows=tuple(skipped),
    )
