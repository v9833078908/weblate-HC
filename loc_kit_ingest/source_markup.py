# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Source rich-text markup defect detection for loc-kit source strings.

The first measured rule (plan
docs/product/plans/2026-09-11-loc-kit-source-validation.md, defect 525591):
a closing Unity rich-text tag that carries an attribute, e.g.
``</color=yellow>``. A closing tag must be bare (``</color>``); the
attribute belongs on the opening tag. A source written this way repeats
itself into every honest target copy, so ``GameMarkupCheck`` - which
compares source and target - cannot see it.

The rule is deliberately narrow: no pairing or nesting, no malformed-tag
grammar, no placeholders or DSL. Anything past the closing-tag-with-
attribute rule needs its own measured precision before it may live here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The only defect code in contract v1.
SOURCE_TAG_CLOSING_HAS_ATTRIBUTE = "source.tag_closing_has_attribute"

# A closing tag of a known Unity rich-text name that carries an `=` or a
# whitespace attribute before `>`. Case-insensitive like upstream TAG_PATTERN
# (weblate/trans/protected_tokens.py). The closed name list is what keeps
# ordinary punctuation (`HP > 50`, `<3`, `a -> b`, `2 < 3 > 1`, `<basic>`)
# out of scope.
_CLOSING_TAG_WITH_ATTRIBUTE = re.compile(
    r"</(?:color|link|size|b|i|u|s|sprite)[=\s][^>]*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceDefect:
    """
    One source-markup defect.

    ``span`` is a half-open ``(start, end)`` range into the analysed text.
    """

    code: str
    message: str
    span: tuple[int, int]


def source_markup_defects(text: str) -> tuple[SourceDefect, ...]:
    """
    Return one ``source.tag_closing_has_attribute`` defect per matching tag.

    One match yields exactly one defect; several independent bad closing
    tags yield defects with exact, non-overlapping spans. Nothing else in
    the string is analysed: a bare ``</sprite>``, ``<color=>`` or an
    unbalanced pair gets no verdict in v1.
    """
    defects: list[SourceDefect] = []
    for match in _CLOSING_TAG_WITH_ATTRIBUTE.finditer(text):
        matched = match.group(0)
        defects.append(
            SourceDefect(
                code=SOURCE_TAG_CLOSING_HAS_ATTRIBUTE,
                message=(
                    f"closing tag {matched!r} carries an attribute; a closing "
                    "tag must be plain, for example </color>"
                ),
                span=(match.start(), match.end()),
            )
        )
    return tuple(defects)
