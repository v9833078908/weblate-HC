# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json

from loc_kit_ingest.infer import InferenceError

CONTEXT_HEADER = "Ключ / контекст"
NOTE_HEADER = "Пояснение исходного термина"
FLAGS_HEADER = "Флаги исходного термина"


def exported_glossary_profile(sheet, rows, component_id, source, targets, resolve_language):
    """Read the earlier Weblate glossary export without treating its context as a note.

    Only this named flat export schema is adapted. The closed record-map parser
    still checks every populated cell, duplicate context, explanation and flag.
    Work on a row copy: the uploaded workbook stays available in its original form.
    """
    headers = [i for i, row in enumerate(rows) if row and row[0].strip() == CONTEXT_HEADER]
    if not headers:
        return None
    if len(headers) != 1:
        raise InferenceError("The glossary contains repeated export headers")
    index = headers[0]
    adapted = [list(row) for row in rows]
    header = adapted[index]
    if header.count(NOTE_HEADER) != 1 or header.count(FLAGS_HEADER) != 1:
        raise InferenceError("The exported glossary needs its explanation and flags headers")
    languages = []
    for col, value in enumerate(header):
        code = resolve_language(value)
        if code:
            if code in {language["code"] for language in languages}:
                raise InferenceError("The glossary contains duplicate language columns")
            languages.append(
                {
                    "code": code,
                    "xml_lang": code.replace("_", "-"),
                    "column": col + 1,
                    "header": value,
                }
            )
    if not languages or languages[0]["code"] != source:
        raise InferenceError("The glossary source must match the export source language")
    source_col = languages[0]["column"] - 1
    if index + 1 >= len(adapted):
        raise InferenceError("The exported glossary has no term rows")
    for row in adapted[index + 1 :]:
        if not any(value.strip() for value in row):
            continue
        try:
            context = json.loads(row[0])
        except (ValueError, IndexError) as error:
            raise InferenceError("Invalid serialized glossary context") from error
        if (
            not isinstance(context, list)
            or len(context) != 2
            or not all(isinstance(value, str) for value in context)
            or len(row) <= source_col
            or context[1] != row[source_col]
        ):
            raise InferenceError("The glossary context does not match its source term")
        row[0] = context[0]
    # Preserve all existing translations and publish source explanations even for
    # selected languages that do not have translated glossary terms yet.
    present = {language["code"] for language in languages}
    for code in targets:
        if code not in present:
            header.append(code)
            languages.append(
                {
                    "code": code,
                    "xml_lang": code.replace("_", "-"),
                    "column": len(header),
                    "header": code,
                }
            )
            present.add(code)
    for row in adapted[index + 1 :]:
        row.extend([""] * max(0, len(header) - len(row)))
    grammar = {
        "type": "record-map",
        "regions": [
            {"first_record_row": index + 2, "last_record_row": len(adapted), "record_stride": 1}
        ],
        "term_row_offset": 0,
        "allow_empty_targets": True,
        "skip_rows": [
            i + 1
            for i, row in enumerate(adapted)
            if i > index and not any(value.strip() for value in row)
        ],
        "section_field": {"column": 1, "header": CONTEXT_HEADER, "row_offset": 0},
        "notes": [
            {
                "scope": "source",
                "column": header.index(NOTE_HEADER) + 1,
                "header": NOTE_HEADER,
                "row_offset": 0,
            }
        ],
        "source_flags": {
            "column": header.index(FLAGS_HEADER) + 1,
            "header": FLAGS_HEADER,
            "row_offset": 0,
        },
    }
    return {
        "schema_version": 2,
        "components": [
            {
                "sheet": sheet,
                "component": component_id,
                "kind": "tbx",
                "source_lang": source,
                "header_row": index + 1,
                "languages": languages,
                "initial_target_languages": [
                    language["code"] for language in languages if language["code"] != source
                ],
                "grammar": grammar,
            }
        ],
    }, adapted
