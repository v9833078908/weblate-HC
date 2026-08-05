# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Hero Craft game markup checks.

Validates Unity rich-text tags and engine placeholders that stock Weblate
checks miss: <color=#RRGGBB>, <link>, <size=N>, <b>, {0}/{c}, %KEY%.
"""

from __future__ import annotations

import regex
from django.utils.translation import gettext_lazy

from weblate.checks.base import TargetCheck

# Paired tags where attributes must match source exactly
TAG_PATTERN = regex.compile(
    r"</?(color|link|size|b|i|u|s)(?:=[^>]*)?/?>",
    regex.IGNORECASE,
)
# Engine placeholders: {0}, {c}, {1}, {SEASON}, %PLAYER, %SHIP%
PLACEHOLDER_PATTERN = regex.compile(r"\{[^{}]*\}|%[A-Z][A-Z0-9_]+%")


def _tokens(text: str) -> list[str]:
    """Extract ordered markup tokens (tags with attrs + placeholders)."""
    return [
        match.group()
        for match in regex.finditer(
            rf"(?:{TAG_PATTERN.pattern})|(?:{PLACEHOLDER_PATTERN.pattern})", text
        )
    ]


class GameMarkupCheck(TargetCheck):
    """Translation markup must match source: tags (with attributes) and placeholders."""

    check_id = "game-markup"
    name = gettext_lazy("Game markup")
    description = gettext_lazy(
        "Tags (<color>, <link>, <size>, <b>) and placeholders ({0}, %KEY%) "
        "in translation do not match source."
    )
    # Always on for components with game markup; no flag needed
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        source_tokens = _tokens(source)
        if not source_tokens:
            return False
        return sorted(source_tokens) != sorted(_tokens(target))
