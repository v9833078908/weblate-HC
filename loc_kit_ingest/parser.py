# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

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
# Term-description-pairs grammar (TBX)
# --------------------------------------------------------------------------- #


def _slug(text: str) -> str:
    """NFKD ASCII transliteration, lowercase, [a-z0-9_-] only."""
    import re

    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9_-]+", "-", ascii_text)
    ascii_text = ascii_text.strip("-")
    return ascii_text


def _has_outer_whitespace(value: str) -> bool:
    """Check for leading or trailing whitespace."""
    return value != value.strip()


def _parse_pairs(component: ComponentProfile, rows: list[list[str]]) -> ParseResult:
    """Parse term-description-pairs grammar for TBX components."""
    grammar = component.grammar
    skip_set = set(grammar.skip_rows)  # 0-based
    assert component.key_language is not None

    key_lang = component.key_language
    source_lang = component.source_lang
    lang_columns = {l.code: l.column for l in component.languages}
    source_col = lang_columns[source_lang]
    key_col = lang_columns[key_lang]
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

        section_slug = _slug(section_text)

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

            # Key language term must exist for context.
            key_term = term_values.get(key_lang, "")
            if _is_blank(key_term):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.missing_target_term",
                        component.component,
                        component.sheet,
                        term_1based,
                        f"key_language term in language {key_lang!r} is empty",
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

            # Build context.
            key_slug = _slug(key_term)
            context = (
                f"{section_slug}.{key_slug}" if section_slug and key_slug else key_slug
            )
            if not context:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "tbx.empty_slug",
                        component.component,
                        component.sheet,
                        term_1based,
                        "context slug is empty after transliteration",
                    )
                )
            elif context in seen_contexts:
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
                    explanations=desc_values,
                    section=section_text,
                    term_row=term_1based,
                    description_row=desc_1based,
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
