# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def resolve_row_source(
    values: dict[str, str], declared: str, *, formula_languages: frozenset[str] = frozenset()
) -> tuple[str, str, str] | None:
    """Choose only confirmed same-row language cells, without rewriting them."""
    for code in dict.fromkeys((declared, "ru", "en")):
        text = values.get(code, "")
        if code not in formula_languages and text.strip():
            return code, text, "declared" if code == declared else "fallback"
    return None


def is_content_empty(row: list[str], ignored_columns: set[int]) -> bool:
    return not any(
        str(cell).strip() for index, cell in enumerate(row) if index not in ignored_columns
    )
