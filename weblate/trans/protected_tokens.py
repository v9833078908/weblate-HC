# Copyright © 2026 Weblate contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Protected Unity markup and engine placeholders."""

from __future__ import annotations

from collections import Counter

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


# A placeholder the engine resolves by name or index is the same token wherever
# it stands, so a target may move it: Japanese renders "{0} of {1}" as
# "{1}の{0}", Hindi as "{1} में से {0}", and Turkish puts the second one first
# too. Only an anonymous conversion binds to its position - the engine fills
# "%s %s" left to right - so for those the order *is* the identity.
ANONYMOUS_PLACEHOLDER = regex.compile(r"\{\s*\}|%[diuoxXfFeEgGaAcsp]")


def _placeholders(text: str) -> tuple[Counter[str], tuple[str, ...]]:
    """Split placeholders into an unordered multiset and a positional sequence."""
    identified: Counter[str] = Counter()
    positional: list[str] = []
    for match in PLACEHOLDER_PATTERN.finditer(text):
        token = match.group()
        if ANONYMOUS_PLACEHOLDER.fullmatch(token):
            positional.append(token)
        else:
            identified[token] += 1
    return identified, tuple(positional)


def placeholders_match(source: str, target: str) -> bool:
    """Whether the target carries the source's placeholders, reordering allowed."""
    return _placeholders(source) == _placeholders(target)


def markup_tokens(text: str) -> list[str]:
    """Return Unity markup tags for unordered multiset comparison."""
    return [match.group() for match in TAG_PATTERN.finditer(text)]
