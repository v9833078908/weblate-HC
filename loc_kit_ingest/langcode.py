# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Language-code recognition shared by the reader and inference layer."""

from __future__ import annotations

import re

from translate.lang import data as lang_data

_PARENS_CODE = re.compile(r"\(\s*([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*)\s*\)\s*$")
_BARE_CODE = re.compile(r"^\s*([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*)\s*$")

_LANGUAGE_ALIASES = {
    "zh_tc": "zh_Hant",
    "zh_hant": "zh_Hant",
    "zh_sc": "zh_Hans",
    "zh_hans": "zh_Hans",
}


def known_language(token: str) -> str | None:
    """Return a Weblate-style code for ``token``, or None if it is no language."""
    norm = token.strip().replace("-", "_")
    if not norm:
        return None
    alias = _LANGUAGE_ALIASES.get(norm.casefold())
    if alias is not None:
        return alias
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


def language_code(header_cell: str) -> str | None:
    """Extract a language code from a header cell, ``en`` or ``English(en)``."""
    match = _PARENS_CODE.search(header_cell)
    if match:
        return known_language(match.group(1))
    match = _BARE_CODE.match(header_cell)
    if match:
        return known_language(match.group(1))
    return None
