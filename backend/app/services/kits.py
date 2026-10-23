# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import re
import stat
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from loc_kit_ingest.infer import (
    InferenceError,
    analyze_profile_candidates,
    infer_component,
    infer_glossary_profile,
)
from loc_kit_ingest.langcode import language_code
from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import parse_profile
from loc_kit_ingest.reader import ReaderError, inspect_sheets

from app.db import DomainError
from app.services.languages import normalized_code
from app.services.source_fallback import is_content_empty, resolve_row_source

MIB = 1024 * 1024


def selected_header_language(header: str, selected: dict[str, str]) -> str | None:
    """Match validated selections without losing script casing or rare locales."""
    token = re.search(r"\(([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*)\)\s*$", header.strip())
    raw = token[1] if token else header.strip()
    return selected.get(normalized_code(raw)) or selected.get(
        normalized_code(language_code(header) or "")
    )


def infer_selected_component(sheet, rows, component_id, source, targets):
    """Adapt catalog codes at the wizard boundary; keep source cells unchanged.

    The offline importer has a smaller vocabulary and different script casing.
    Temporary, collision-free recognized codes allow its structural rules to run
    for selected locales. Restore catalog codes and original headers immediately.
    """
    selected = {normalized_code(code): code for code in [source, *targets]}
    header_index = next(
        (
            i
            for i, row in enumerate(rows)
            if any(
                language_code(header) or selected_header_language(header, selected)
                for header in row[1:]
            )
        ),
        None,
    )
    if header_index is None:
        raise DomainError("source_column", "Source column is missing")
    header = rows[header_index]
    occupied = {normalized_code(language_code(cell) or "") for cell in header}
    occupied.update(selected)
    temporary = {}
    for code in selected.values():
        index = len(temporary)
        candidate = f"en_XW{index:X}"
        while normalized_code(candidate) in occupied:
            index += 1
            candidate = f"en_XW{index:X}"
        occupied.add(normalized_code(candidate))
        temporary[code] = candidate
    inference_rows = copy.deepcopy(rows)
    source_column = next(
        (i for i, cell in enumerate(header) if selected_header_language(cell, selected) == source),
        None,
    )
    if source_column is not None and not any(
        len(row) > source_column and row[source_column].strip() for row in rows[header_index + 1 :]
    ):
        # Inference drops wholly empty languages. This structural-only probe keeps
        # the confirmed column in its schema; real rows below are never changed
        # and will all be classified as missing_source, never imported.
        probe = [""] * len(header)
        probe[0] = "__wizard_structure_probe__"
        probe[source_column] = "__wizard_structure_probe__"
        inference_rows.append(probe)
    inference_rows[header_index] = [
        temporary[code] if (code := selected_header_language(cell, selected)) else cell
        for cell in header
    ]
    document, _ = infer_component(
        sheet,
        inference_rows,
        component=component_id,
        source_lang=temporary[source],
        min_fill=0,
        include_languages=frozenset(temporary[code] for code in targets),
    )
    restored = {normalized_code(value): code for code, value in temporary.items()}
    document["source_lang"] = source
    for language in document["languages"]:
        language["code"] = restored.get(normalized_code(language["code"]), language["code"])
        language["xml_lang"] = language["code"].replace("_", "-")
    for value in document.values():
        for field in value if isinstance(value, list) else [value]:
            if isinstance(field, dict) and "column" in field and "header" in field:
                field["header"] = header[field["column"] - 1]
    return document


def excluded_sheets(path: Path):
    if path.suffix.lower() != ".xlsx":
        return []
    with ZipFile(path) as z:
        root = ET.fromstring(z.read("xl/workbook.xml"))
    return [
        {"sheet": n.attrib["name"], "reason": "sheet_hidden"}
        for n in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")
        if n.attrib.get("state", "visible") != "visible"
    ]


def analyze_file(path: Path, name: str):
    try:
        inspection = inspect_sheets(path, max_bytes=100 * MIB)
    except (ReaderError, ValueError) as e:
        raise DomainError("invalid_kit", str(e)) from e
    sheets = []
    normalized_sheets: dict[str, list[list[str]]] = {}
    for sheet, original_rows in inspection.sheets.items():
        # Excel keeps its used range after cells are cleared. Ignore that
        # historical extent, but retain row positions and interior columns.
        populated_rows = [row for row in original_rows if any(value.strip() for value in row)]
        content_width = max(
            (
                index + 1
                for row in populated_rows
                for index, value in enumerate(row)
                if value.strip()
            ),
            default=0,
        )
        rows = [row[:content_width] for row in original_rows]
        normalized_sheets[sheet] = rows
        columns = rows[0] if rows else []
        candidates = []
        unresolved = []
        issues = []
        kind = "ambiguous"
        try:
            document, _ = analyze_profile_candidates({sheet: rows}, kit_stem=path.stem)
            component = document["components"][0]
            columns = rows[component["header_row"] - 1]
            candidates = component["source_candidates"]
            unresolved = component["unresolved_columns"]
            kind = "loc_kit_candidate"
        except InferenceError as error:
            no_headers = str(error).startswith("no row contains a recognised language code")
            issues.append(
                {
                    "code": "loc_kit.no_language_headers"
                    if no_headers
                    else "loc_kit.sheet_structure",
                    "scope": "sheet",
                    "detail": "No recognized language headers were found."
                    if no_headers
                    else "The worksheet structure could not be recognized.",
                }
            )
        formulas = [
            {
                "file": name,
                "sheet": sheet,
                "cell": f.coordinate,
                "column": f.column + 1,
                "formula": f.formula,
            }
            for f in inspection.formulas
            if f.sheet == sheet
        ]
        header_index = next((i for i, row in enumerate(rows) if row == columns), 0)
        for formula in formulas:
            column = formula["column"]
            header = columns[column - 1] if column <= len(columns) else ""
            coordinates = {f["cell"] for f in formulas if f["column"] == column}
            from openpyxl.utils import get_column_letter

            safe_header = not header.strip() or bool(
                re.fullmatch(
                    r"(?:length|len|длина|count|characters|chars)(?:[ _-].*)?", header.strip(), re.I
                )
            )
            pure = all(
                not (row[column - 1] if len(row) >= column else "").strip()
                or f"{get_column_letter(column)}{i + 1}" in coordinates
                for i, row in enumerate(rows)
                if i > header_index
            )
            formula["auto_ignore"] = (
                column != 1 and safe_header and pure and not language_code(header)
            )
        for column in sorted({formula["column"] for formula in formulas}):
            if not any(item["column"] == column for item in unresolved):
                unresolved.append(
                    {
                        "column": column,
                        "header": columns[column - 1] if column <= len(columns) else "",
                        "candidates": ["service"],
                    }
                )
        sheets.append(
            {
                "name": sheet,
                "classification": kind,
                "selected": False,
                "rows": len(populated_rows),
                "columns": columns,
                "languages": [],
                "source_language": None,
                "source_candidates": candidates,
                "unresolved_columns": unresolved,
                "ignored_columns": [],
                "formula_cells": formulas,
                "issues": issues,
            }
        )
    return (
        {"path": name, "format": path.suffix.lstrip("."), "selected": False, "sheets": sheets},
        normalized_sheets,
        excluded_sheets(path),
    )


def materialize_inputs(directory: Path, uploads: list[tuple[str, bytes]]):
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    used = 0
    for original, payload in uploads:
        suffix = Path(original).suffix.lower()
        limit = 50 * MIB if suffix == ".zip" else 20 * MIB
        if len(payload) > limit:
            raise DomainError("file_limit", "File size limit exceeded", 413)
        if suffix == ".zip":
            archive_path = directory / (uuid.uuid4().hex + ".zip")
            archive_path.write_bytes(payload)
            with ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > 100 or sum(m.file_size for m in members) > 100 * MIB:
                    raise DomainError("archive_limit", "Archive limit exceeded", 413)
                for member in members:
                    safe = PurePosixPath(member.filename.replace("\\", "/"))
                    if (
                        safe.is_absolute()
                        or ".." in safe.parts
                        or ":" in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)
                    ):
                        raise DomainError("unsafe_archive", "Unsafe archive member")
                    if member.is_dir():
                        continue
                    if safe.suffix.lower() not in {".xlsx", ".csv", ".tsv", ".txt"}:
                        raise DomainError(
                            "unsupported_archive_member", "Only table files are accepted"
                        )
                    content = archive.read(member)
                    used += len(content)
                    records.append(_save_input(directory, member.filename, content))
        else:
            if suffix not in {".xlsx", ".csv", ".tsv", ".txt"}:
                raise DomainError("unsupported_file", "Use XLSX, CSV, TSV, tabular TXT or ZIP")
            used += len(payload)
            records.append(_save_input(directory, original, payload))
        if used > 100 * MIB or len(records) > 100:
            raise DomainError("input_limit", "Total input limit exceeded", 413)
    if len({r["name"] for r in records}) != len(records):
        raise DomainError("duplicate_filename", "Upload paths must be unique")
    return records


def _save_input(directory, name, payload):
    suffix = Path(name).suffix.lower()
    path = directory / (uuid.uuid4().hex + (".csv" if suffix == ".txt" else suffix))
    path.write_bytes(payload)
    analysis, sheets, excluded = analyze_file(path, name)
    if suffix in {".csv", ".tsv", ".txt"}:
        old = next(iter(sheets))
        sheets = {Path(name).stem: sheets[old]}
        analysis["sheets"][0]["name"] = Path(name).stem
        for f in analysis["sheets"][0]["formula_cells"]:
            f["sheet"] = Path(name).stem
    return {
        "name": name,
        "path": str(path),
        "analysis": analysis,
        "sheets": sheets,
        "excluded": excluded,
    }


def prepare_kit(draft: dict):
    answers = draft["answers"]
    source = answers.get("source_language")
    targets = answers.get("included_target_languages", [])
    if not source or not targets or source in targets:
        raise DomainError("language_selection", "Select a source and different target languages")
    selections = answers.get("selected_sheets", [])
    if not selections:
        raise DomainError("sheet_selection", "Select visible worksheets")
    components = []
    logical_sheets = []
    sheet_ids = dict(draft.get("sheet_ids", {}))
    for record in draft["_files"]:
        for name in record["sheets"]:
            identity = record["name"] + "\u001f" + name
            if identity not in sheet_ids:
                sheet_ids[identity] = (
                    f"kit-{max([int(v.split('-')[1]) for v in sheet_ids.values()], default=0) + 1}"
                )
    quarantine = []
    input_rows = 0
    for selection in selections:
        record = next((r for r in draft["_files"] if r["name"] == selection["file"]), None)
        if record is None:
            raise DomainError("sheet_not_found", "Unknown file")
        if selection["sheet"] in {e["sheet"] for e in record["excluded"]}:
            raise DomainError("sheet_not_visible", "Hidden worksheets cannot be processed")
        if selection["sheet"] not in record["sheets"]:
            raise DomainError("sheet_not_found", "Unknown worksheet")
        rows = [list(r) for r in record["sheets"][selection["sheet"]]]
        component_id = sheet_ids[selection["file"] + "\u001f" + selection["sheet"]]
        declared = answers["source_language"]
        source = declared
        if draft["kind"] == "glossary":
            from app.services.glossary_tables import exported_glossary_profile

            selected = {normalized_code(code): code for code in [source, *targets]}
            exported = exported_glossary_profile(
                selection["sheet"],
                rows,
                component_id,
                source,
                targets,
                lambda header: selected_header_language(header, selected) or language_code(header),
            )
            if exported:
                profile_doc, rows = exported
            else:
                profile_doc, _ = infer_glossary_profile(
                    selection["sheet"], rows, component=component_id, layout="auto"
                )
            component = parse_profile(profile_doc).components[0]
            if component.source_lang != source:
                raise DomainError("glossary_source", "Glossary source does not match")
            document = profile_doc["components"][0]
        else:
            # Inference needs a populated source; the declared column may be absent.
            selected_codes = {normalized_code(c): c for c in [declared, "ru", "en", *targets]}
            roles = [
                r
                for r in answers.get("column_roles", [])
                if r["file"] == selection["file"] and r["sheet"] == selection["sheet"]
            ]
            header_index = next(
                (
                    i
                    for i, row in enumerate(rows)
                    if any(selected_header_language(h, selected_codes) for h in row[1:])
                ),
                None,
            )
            if header_index is None:
                # With no recognized header, analysis displays the first row's
                # columns. Explicit assignments refer to that same row.
                if rows and any(r["role"] == "language" for r in roles):
                    header_index = 0
                else:
                    raise DomainError("source_column", "Нет подтверждённых языковых колонок")
            if any(not 1 <= r["column"] <= len(rows[header_index]) for r in roles):
                raise DomainError("column_role", "Unknown column")
            language_columns = {}
            for index, header in enumerate(rows[header_index]):
                code = selected_header_language(header, selected_codes)
                if code:
                    language_columns.setdefault(code, []).append(index + 1)
            for role in roles:
                for code in list(language_columns):
                    language_columns[code] = [
                        col for col in language_columns[code] if col != role["column"]
                    ]
                if role["role"] == "language":
                    language_columns.setdefault(role["language"], []).append(role["column"])
            if any(len(cols) > 1 for cols in language_columns.values()):
                raise DomainError("column_role", "Подтвердите повторяющиеся языковые колонки")
            source = next(
                (
                    code
                    for code in dict.fromkeys([declared, "ru", "en"])
                    if language_columns.get(code)
                    and any(
                        len(row) >= language_columns[code][0]
                        and row[language_columns[code][0] - 1].strip()
                        for row in rows[header_index + 1 :]
                    )
                ),
                None,
            )
            if source is None:
                # Keep key-only rows reviewable using an existing language column.
                source = next(
                    (code for code in [declared, "ru", "en"] if language_columns.get(code)), None
                )
            if source is None:
                raise DomainError(
                    "source_column", "Нет заявленного языка или русского/английского источника"
                )
            inference_rows = copy.deepcopy(rows)
            for role in roles:
                if role["role"] == "language" and 1 <= role["column"] <= len(
                    inference_rows[header_index]
                ):
                    inference_rows[header_index][role["column"] - 1] = role["language"]
            document = infer_selected_component(
                selection["sheet"],
                inference_rows,
                component_id,
                source,
                list(dict.fromkeys([*targets, *language_columns])),
            )
            header_row = document["header_row"] - 1
            for value in document.values():
                for field in value if isinstance(value, list) else [value]:
                    if isinstance(field, dict) and "column" in field and "header" in field:
                        field["header"] = rows[header_row][field["column"] - 1]
            for code, columns in language_columns.items():
                if columns and code not in {lang["code"] for lang in document["languages"]}:
                    col = columns[0]
                    document["languages"].append(
                        {
                            "code": code,
                            "xml_lang": code.replace("_", "-"),
                            "column": col,
                            "header": rows[header_row][col - 1],
                        }
                    )
            language_col_set = {lang["column"] for lang in document["languages"]}
            for field in ["comments", "references", "ignored_columns"]:
                document[field] = [
                    c for c in document.get(field, []) if c["column"] not in language_col_set
                ]
            for role in answers.get("column_roles", []):
                if role["file"] != selection["file"] or role["sheet"] != selection["sheet"]:
                    continue
                col = role["column"]
                if col < 1 or col > len(rows[header_row]):
                    raise DomainError("column_role", "Invalid column index")
                index = col - 1
                header = rows[header_row][index]
                for field in ["comments", "references", "languages", "ignored_columns"]:
                    document[field] = [c for c in document.get(field, []) if c["column"] != col]
                if role["role"] == "language":
                    code = role.get("language")
                    if not code:
                        raise DomainError("column_role", "Language is required")
                    document["languages"].append(
                        {
                            "code": code,
                            "xml_lang": code.replace("_", "-"),
                            "column": col,
                            "header": header,
                        }
                    )
                elif role["role"] == "key":
                    document["key"] = {"column": col, "header": header}
                elif role["role"] == "ignored":
                    document["ignored_columns"].append(
                        {"column": col, "header": header, "formula_policy": "ignore"}
                    )
                elif role["role"] in {"comment", "reference"}:
                    field = "comments" if role["role"] == "comment" else "references"
                    document[field].append({"column": col, "header": header, "name": header})
                elif role["role"] in {"explanation", "flags"}:
                    document[role["role"]] = {"column": col, "header": header}
            present = {language["code"] for language in document["languages"]}
            assigned_columns = {
                role["column"]
                for role in answers.get("column_roles", [])
                if role["file"] == selection["file"] and role["sheet"] == selection["sheet"]
            }
            for target in targets:
                if target not in present:
                    matching = [
                        index + 1
                        for index, header in enumerate(rows[header_row])
                        if selected_header_language(header, {normalized_code(target): target})
                        == target
                        and index + 1 not in assigned_columns
                        and index + 1 != document["key"]["column"]
                    ]
                    if len(matching) > 1:
                        raise DomainError(
                            "column_role", "Confirm the target column when language headers repeat"
                        )
                    if matching:
                        col = matching[0]
                        document["ignored_columns"] = [
                            column
                            for column in document.get("ignored_columns", [])
                            if column["column"] != col
                        ]
                    else:
                        col = len(rows[header_row]) + 1
                        rows[header_row].append(target)
                        for row in rows:
                            if row is not rows[header_row]:
                                row.extend([""] * max(0, col - len(row)))
                    document["languages"].append(
                        {
                            "code": target,
                            "xml_lang": target.replace("_", "-"),
                            "column": col,
                            "header": rows[header_row][col - 1],
                        }
                    )
            document["languages"] = [
                language
                for language in document["languages"]
                if language["code"] in {declared, "ru", "en", *targets}
            ]
            if source not in {language["code"] for language in document["languages"]}:
                raise DomainError("source_column", "Source column is missing")
            component = parse_profile({"schema_version": 4, "components": [document]}).components[0]
        selected_analysis = next(
            sh for sh in record["analysis"]["sheets"] if sh["name"] == selection["sheet"]
        )
        allowed_formula_columns = {
            column["column"]
            for column in document.get("ignored_columns", [])
            if column.get("formula_policy") == "ignore"
        }
        for formula in selected_analysis["formula_cells"]:
            import re

            coordinate = re.fullmatch(r"[A-Z]+([1-9][0-9]*)", formula["cell"])
            if (
                formula["column"] not in allowed_formula_columns
                or not coordinate
                or int(coordinate[1]) <= document["header_row"]
            ):
                raise DomainError(
                    "formula_policy",
                    "Formulas are allowed only in explicitly ignored service columns",
                )
        if component.kind == "po":
            from collections import defaultdict

            languages = {lang.code: lang.column for lang in component.languages}
            ignored = {c["column"] - 1 for c in document.get("ignored_columns", [])}
            reasons, chosen, occurrences = {}, {}, defaultdict(list)
            pre_skips = set(document["grammar"].get("skip_rows", []))
            for index in range(component.first_data_row, len(rows)):
                row = rows[index]
                if is_content_empty(row, ignored):
                    continue
                input_rows += 1
                coordinate = index + 1
                key = row[component.key.column] if len(row) > component.key.column else ""
                values = {
                    code: row[col] if len(row) > col else "" for code, col in languages.items()
                }
                resolved = resolve_row_source(values, declared)
                if not key.strip():
                    reasons[coordinate] = "missing_key"
                elif resolved is None:
                    reasons[coordinate] = "missing_source"
                elif coordinate not in pre_skips:
                    chosen[coordinate] = resolved
                else:
                    reasons[coordinate] = "profile_skip"
                if key.strip():
                    occurrences[key].append(coordinate)
            for coordinates in occurrences.values():
                if len(coordinates) > 1:
                    for coordinate in coordinates:
                        reasons[coordinate] = "duplicate_key"
                        chosen.pop(coordinate, None)
            for coordinate, reason in reasons.items():
                row = rows[coordinate - 1]
                quarantine.append(
                    {
                        **selection,
                        "row": coordinate,
                        "coordinate": str(coordinate),
                        "status": "quarantined",
                        "reason_code": reason,
                        "reason": reason,
                        "available_languages": [
                            code
                            for code, col in languages.items()
                            if len(row) > col and row[col].strip()
                        ],
                        "original_cells": row,
                        "normalized": None,
                    }
                )
            logical_sheets.append(
                {
                    "id": component_id,
                    **selection,
                    "rows": rows,
                    "header_row": document["header_row"] - 1,
                    "profile": copy.deepcopy(document),
                }
            )
            for actual in dict.fromkeys([declared, "ru", "en"]):
                members = {
                    coordinate
                    for coordinate, item in chosen.items()
                    if coordinate not in reasons and item[0] == actual
                }
                if not members:
                    continue
                group_doc = copy.deepcopy(document)
                group_id = component_id if actual == declared else component_id + "-src-" + actual
                group_doc["component"] = group_id
                group_doc["source_lang"] = actual
                group_doc["grammar"]["skip_rows"] = sorted(
                    set(range(component.first_data_row + 1, len(rows) + 1)) - members | pre_skips
                )
                group_profile = parse_profile(
                    {"schema_version": 4, "components": [group_doc]}
                ).components[0]
                result = parse_component(group_profile, rows)
                errors = [d for d in result.diagnostics if d.severity == "error"]
                if errors:
                    raise DomainError("kit_validation", errors[0].message)
                units = [
                    dict(
                        asdict(u),
                        declared_source=declared,
                        effective_source=actual,
                        effective_text=chosen[u.row][1],
                        source_origin=chosen[u.row][2],
                        original_row=u.row,
                    )
                    for u in result.units
                ]
                components.append(
                    {
                        "id": group_id,
                        **selection,
                        "logical_sheet_id": component_id,
                        "source": actual,
                        "targets": [t for t in targets if t != actual],
                        "kind": "po",
                        "profile": group_doc,
                        "schema_version": 4,
                        "units": units,
                        "rows": rows,
                        "header_row": document["header_row"] - 1,
                    }
                )
        else:
            result = parse_component(component, rows)
            errors = [d for d in result.diagnostics if d.severity == "error"]
            if errors:
                raise DomainError("kit_validation", errors[0].message)
            input_rows += len(result.units) + len(result.skipped_rows)
            quarantine.extend(
                {
                    **selection,
                    "row": r.row,
                    "coordinate": str(r.row),
                    "status": "quarantined",
                    "reason_code": r.reason,
                    "reason": r.reason,
                    "original_cells": rows[r.row - 1],
                    "normalized": None,
                }
                for r in result.skipped_rows
                if r.reason != "blank"
            )
            components.append(
                {
                    "id": component_id,
                    **selection,
                    "kind": component.kind,
                    "profile": document,
                    "schema_version": profile_doc["schema_version"],
                    "units": [asdict(u) for u in result.units],
                    "rows": rows,
                    "header_row": document["header_row"] - 1,
                }
            )
    return {
        "manifest_version": 2 if draft["kind"] == "string" else 1,
        "sheet_ids": sheet_ids,
        "sheets": logical_sheets,
        "source": answers["source_language"],
        "targets": targets,
        "components": components,
        "quarantine": quarantine,
        "totals": {
            "input_rows": input_rows,
            "imported_rows": sum(len(c["units"]) for c in components),
            "quarantined_rows": len(quarantine),
        },
    }
