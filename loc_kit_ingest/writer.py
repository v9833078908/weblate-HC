# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loc_kit_ingest.model import Diagnostic, GlossaryTerm, ParseResult, Severity, StringUnit
from translate.storage.pypo import pofile
from translate.storage.tbx import tbxfile

if TYPE_CHECKING:
    from loc_kit_ingest.profile import ComponentProfile


# --------------------------------------------------------------------------- #
# PO rendering
# --------------------------------------------------------------------------- #


def _render_po(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> dict[str, Path]:
    """Write one PO file per declared language.

    Returns {language_code: path}.
    """
    source_lang = component.source_lang
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for lang in component.languages:
        store = pofile()
        store.settargetlanguage(lang.xml_lang)

        for unit_data in result.units:
            assert isinstance(unit_data, StringUnit)
            po_unit = store.addsourceunit(unit_data.key)
            po_unit.target = unit_data.values.get(lang.code, "")

            # Developer comments and references go only in the source-language PO.
            if lang.code == source_lang:
                for comment in unit_data.comments:
                    po_unit.addnote(comment, origin="developer")
                for ref in unit_data.references:
                    po_unit.addlocation(ref)

        file_path = out_dir / f"{lang.code}.po"
        file_path.write_bytes(bytes(store))
        paths[lang.code] = file_path

    return paths


def _validate_po(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> tuple[Diagnostic, ...]:
    """Parse back every rendered PO and compare against in-memory units."""
    diagnostics: list[Diagnostic] = []

    # Build lookup by key for comparison.
    expected_by_key = {u.key: u for u in result.units if isinstance(u, StringUnit)}

    for lang in component.languages:
        file_path = out_dir / f"{lang.code}.po"
        if not file_path.is_file():
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "render.missing_file",
                    component.component,
                    "",
                    0,
                    f"expected file {file_path.name} not found",
                )
            )
            continue

        try:
            parsed = pofile.parsestring(file_path.read_bytes())
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "render.parse_back_failed",
                    component.component,
                    "",
                    0,
                    f"failed to parse back {file_path.name}: {exc}",
                )
            )
            continue

        for po_unit in parsed.units:
            if po_unit.isheader():
                continue
            key = po_unit.getid()
            expected = expected_by_key.get(key)
            if expected is None:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "render.unexpected_key",
                        component.component,
                        "",
                        0,
                        f"unexpected key {key!r} in {file_path.name}",
                    )
                )
                continue

            actual_target = po_unit.target
            expected_target = expected.values.get(lang.code, "")
            if actual_target != expected_target:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "render.value_mismatch",
                        component.component,
                        "",
                        0,
                        f"value mismatch for key {key!r} language {lang.code!r}: "
                        f"expected {expected_target!r}, got {actual_target!r}",
                    )
                )

    return tuple(diagnostics)


# --------------------------------------------------------------------------- #
# TBX rendering
# --------------------------------------------------------------------------- #


def _render_tbx(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> dict[str, Path]:
    """Write one bilingual TBX per initial_target_language.

    Returns {target_code: path}. Source language file is never created.
    """
    source_lang = component.source_lang
    source_col = next(l for l in component.languages if l.code == source_lang)
    target_cols = {
        code: next(l for l in component.languages if l.code == code)
        for code in component.initial_target_languages
    }

    tbx_dir = out_dir / "tbx"
    tbx_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for target_code, target_col in target_cols.items():
        store = tbxfile(
            sourcelanguage=source_col.xml_lang,
            targetlanguage=target_col.xml_lang,
        )

        for unit_data in result.units:
            assert isinstance(unit_data, GlossaryTerm)
            tbx_unit = store.addsourceunit(unit_data.values[source_lang])
            tbx_unit.setid(unit_data.context)
            tbx_unit.target = unit_data.values.get(target_code, "")

            source_explanation = unit_data.explanations.get(source_lang, "")
            target_explanation = unit_data.explanations.get(target_code, "")

            if source_explanation:
                tbx_unit.addnote(source_explanation, origin="definition")
            if target_explanation:
                tbx_unit.addnote(target_explanation, origin="translator")

        file_path = tbx_dir / f"{target_code}.tbx"
        file_path.write_bytes(bytes(store))
        paths[target_code] = file_path

    return paths


def _validate_tbx(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> tuple[Diagnostic, ...]:
    """Parse back every rendered TBX and compare against in-memory terms."""
    diagnostics: list[Diagnostic] = []
    source_lang = component.source_lang

    expected_by_context = {
        u.context: u for u in result.units if isinstance(u, GlossaryTerm)
    }

    for target_code in component.initial_target_languages:
        file_path = out_dir / "tbx" / f"{target_code}.tbx"
        if not file_path.is_file():
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "render.missing_file",
                    component.component,
                    "",
                    0,
                    f"expected file {file_path.name} not found",
                )
            )
            continue

        try:
            parsed = tbxfile.parsestring(file_path.read_bytes())
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    "render.parse_back_failed",
                    component.component,
                    "",
                    0,
                    f"failed to parse back {file_path.name}: {exc}",
                )
            )
            continue

        for tbx_unit in parsed.units:
            ctx = tbx_unit.getid()
            expected = expected_by_context.get(ctx)
            if expected is None:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "render.unexpected_context",
                        component.component,
                        "",
                        0,
                        f"unexpected context {ctx!r} in {file_path.name}",
                    )
                )
                continue

            actual_source = tbx_unit.source
            expected_source = expected.values.get(source_lang, "")
            if actual_source != expected_source:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "render.source_mismatch",
                        component.component,
                        "",
                        0,
                        f"source mismatch for context {ctx!r}: "
                        f"expected {expected_source!r}, got {actual_source!r}",
                    )
                )

            actual_target = tbx_unit.target
            expected_target = expected.values.get(target_code, "")
            if actual_target != expected_target:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "render.target_mismatch",
                        component.component,
                        "",
                        0,
                        f"target mismatch for context {ctx!r}: "
                        f"expected {expected_target!r}, got {actual_target!r}",
                    )
                )

            # Check definition note (source explanation).
            expected_def = expected.explanations.get(source_lang, "")
            if expected_def:
                actual_def = tbx_unit.getnotes("definition")
                if expected_def not in actual_def:
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "render.explanation_mismatch",
                            component.component,
                            "",
                            0,
                            f"source explanation mismatch for context {ctx!r}",
                        )
                    )

    return tuple(diagnostics)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def render_component(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> dict[str, Path]:
    """Render a parsed component to files in out_dir.

    Returns {language_code: path} for PO, {target_code: path} for TBX.
    """
    if component.kind == "po":
        return _render_po(component, result, out_dir / component.component)
    if component.kind == "tbx":
        return _render_tbx(component, result, out_dir / component.component)
    raise ValueError(f"unknown kind {component.kind!r}")


def validate_rendered_component(
    component: ComponentProfile, result: ParseResult, out_dir: Path
) -> tuple[Diagnostic, ...]:
    """Parse back rendered files and compare against in-memory units."""
    if component.kind == "po":
        return _validate_po(component, result, out_dir / component.component)
    if component.kind == "tbx":
        return _validate_tbx(component, result, out_dir / component.component)
    raise ValueError(f"unknown kind {component.kind!r}")
