# Copyright © 2026 Weblate contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Protected Unity markup and engine placeholders."""

from __future__ import annotations

import regex

TAG_PATTERN = regex.compile(
    r"</?(color|link|size|b|i|u|s|sprite)(?:[=\s][^>]*)?/?>",
    regex.IGNORECASE,
)
PLACEHOLDER_PATTERN = regex.compile(
    r"\{[^{}]*\}|%[A-Z][A-Z0-9_]+%|%(?:\d+\$)?[diuoxXfFeEgGaAcsp]"
)
MARKUP = regex.compile(rf"(?:{TAG_PATTERN.pattern})|(?:{PLACEHOLDER_PATTERN.pattern})")


def protected_tokens(text: str) -> list[str]:
    """Return Unity tags and engine placeholders in source order."""
    return [match.group() for match in MARKUP.finditer(text)]


def placeholder_sequence(text: str) -> tuple[str, ...]:
    """Return engine placeholders in their required source order."""
    return tuple(match.group() for match in PLACEHOLDER_PATTERN.finditer(text))


def markup_tokens(text: str) -> list[str]:
    """Return Unity markup tags for unordered multiset comparison."""
    return [match.group() for match in TAG_PATTERN.finditer(text)]
