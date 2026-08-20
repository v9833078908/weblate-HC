# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Hero Craft game markup checks.

Validates Unity rich-text tags, engine placeholders and engine substitution
tokens that stock Weblate checks miss: <color=#RRGGBB>, <link>, <size=N>, <b>,
<sprite name="fire">, {0}/{c}, %KEY%, item_type[|{0}].
"""

from __future__ import annotations

import unicodedata
from collections import Counter

import regex
from django.utils.translation import gettext_lazy

from weblate.checks.base import TargetCheck

# Paired tags where attributes must match source exactly. `sprite` carries its
# attribute after a space (`<sprite name="fire">`) rather than after `=`, so an
# attribute starts at either character.
TAG_PATTERN = regex.compile(
    r"</?(color|link|size|b|i|u|s|sprite)(?:[=\s][^>]*)?/?>",
    regex.IGNORECASE,
)
# Engine placeholders: {0}, {c}, {1}, {SEASON}, %PLAYER, %SHIP%
PLACEHOLDER_PATTERN = regex.compile(r"\{[^{}]*\}|%[A-Z][A-Z0-9_]+%")

# The mission DSL: `item_type[|{0}]`, `skirmish_league_id[gen|в {0}|в любой лиге]`.
# The identifier in front of the bracket is an engine lookup key, so it stays
# in English while the bracketed alternatives are translated. A `|` inside the
# brackets is what separates a substitution from ordinary bracketed prose.
TOKEN_PATTERN = regex.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[[^\[\]]*\|[^\[\]]*\]")

# Tags and placeholders together: `_tokens` compares them, `_numbers` removes
# them so that `<size=14>` and `{0}` are never read as quantities.
MARKUP = regex.compile(rf"(?:{TAG_PATTERN.pattern})|(?:{PLACEHOLDER_PATTERN.pattern})")

# A number in the source is a fact the player acts on: damage, radius, seconds.
# Losing or altering it is a defect in every language. A number the target adds
# is not: Japanese counts "3回に1回" where the source says "каждый третий", and a
# full date is written per locale. So the rule is containment, not equality.
NUMBER = regex.compile(r"\d+(?:[.,\u066b]\d+)?")
# A full date is not a quantity, and its rendering belongs to the locale.
FULL_DATE = regex.compile(r"\b\d{1,2}[./,]\d{1,2}[./,]\d{4}\b")

# A URL is not a phrase the translator restates; digits in a share link or a
# patch-notes address are not game quantities, so drop the whole token.
URL = regex.compile(r"https?://\S+")
# An English ordinal ("1st", "21st") in the SOURCE is a label the target spells
# out ("1ST BUY" -> "ПЕРВУЮ ПОКУПКУ"), so its digit need not reach the
# translation. It is never dropped from the target: English renders the plain
# "24 декабря" as "December 24th", and dropping that digit would report the
# source's own number as missing.
ORDINAL = regex.compile(r"\b\d+(?:st|nd|rd|th)\b", regex.IGNORECASE)
# Digit grouping is a locale choice like the decimal separator: "1,900,000"
# (English), "1 900 000", "1.900.000" and "30.000" are the same number. An
# exactly three-digit group is what marks a separator as grouping rather than a
# decimal, so "1.5" and "3.6" are left alone. The Arabic thousands mark (U+066C)
# groups too, and native digits are folded to ASCII before this runs.
_GROUP = r"[ ,.\u00a0\u2009\u202f\u066c]"
_GROUP_RE = regex.compile(_GROUP)
THOUSANDS = regex.compile(rf"(?<![\d.,])\d{{1,3}}(?:{_GROUP}\d{{3}})+(?!\d)")

# `$` is the engine's line separator, not a character. Whitespace beside one
# renders as a stray indent; a lost one merges two lines.
SEPARATOR_SPACE = r"[ \t\u00a0\u2009\u202f]"
SEPARATOR_HUGGED = regex.compile(rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}")
# A separator sits tight between two lines. A source that spaces its dollar
# signs is using them for something else - currency, most likely - and its
# spacing is not ours to police.
SEPARATOR_LOOSE_IN_SOURCE = regex.compile(
    rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}|^\$|\$$"
)
# A Cyrillic codepoint in a target whose language is not written in Cyrillic is
# either untranslated text or a homoglyph. Weblate carries no script metadata
# for a language, so the ones that legitimately write in Cyrillic are listed.
CYRILLIC = regex.compile(r"\p{Script=Cyrillic}")
CYRILLIC_SCRIPT_LANGUAGES = frozenset(
    {
        "ab",
        "av",
        "ba",
        "be",
        "bg",
        "ce",
        "cv",
        "kk",
        "ky",
        "mk",
        "mn",
        "os",
        "ru",
        "sah",
        "sr",
        "tg",
        "tt",
        "udm",
        "uk",
    }
)


def separator_is_tight(source: str) -> bool:
    """Whether the source uses `$` as a line separator we can reason about."""
    return "$" in source and not SEPARATOR_LOOSE_IN_SOURCE.search(source)


def _tokens(text: str) -> list[str]:
    """Extract ordered markup tokens (tags with attrs + placeholders)."""
    return [match.group() for match in MARKUP.finditer(text)]


def _tokens_dsl(text: str) -> Counter[str]:
    """Count engine substitution identifiers, ignoring their translated bodies."""
    return Counter(match.group(1) for match in TOKEN_PATTERN.finditer(text))


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


class GameLineBreakCheck(TargetCheck):
    """`$` is a line break: the count must match and nothing may hug it."""

    check_id = "game-line-break"
    name = gettext_lazy("Game line break")
    description = gettext_lazy(
        "The number of $ line separators does not match the source, or "
        "whitespace sits next to a separator."
    )
    # Always on: a separator is engine syntax, not a per-component preference.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        if not separator_is_tight(source):
            return False
        return source.count("$") != target.count("$") or bool(
            SEPARATOR_HUGGED.search(target)
        )


class CyrillicLeakCheck(TargetCheck):
    """
    Cyrillic in a target whose language does not write in it.

    An LLM leaves a name, an interjection or a whole clause in the source
    script, and a homoglyph is the same defect one character wide, because a
    homoglyph is a Cyrillic codepoint sitting inside a Latin word. The source
    is irrelevant: this project translates out of Russian, so a source
    condition would silence the check on every string it exists for.
    """

    check_id = "cyrillic-leak"
    name = gettext_lazy("Cyrillic in the translation")
    description = gettext_lazy(
        "The translation contains Cyrillic characters, but its language does "
        "not use them."
    )
    default_disabled = False

    def should_skip(self, unit) -> bool:
        language = unit.translation.language
        if "cyrillic" in language.code or language.is_base(CYRILLIC_SCRIPT_LANGUAGES):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit) -> bool:
        return bool(target) and bool(CYRILLIC.search(target))


def _fold_digits(text: str) -> str:
    """Fold any Unicode decimal digit (Arabic-Indic, Devanagari, ...) to ASCII."""
    return "".join(
        str(value) if (value := unicodedata.decimal(char, None)) is not None else char
        for char in text
    )


def _collapse_grouping(text: str) -> str:
    """Fold locale digit grouping so 1,900,000 == 1 900 000 == 1.900.000 == 30.000."""
    return THOUSANDS.sub(lambda match: _GROUP_RE.sub("", match.group()), text)


def _numbers(text: str, *, drop_ordinals: bool = False) -> Counter[str]:
    """Quantities outside markup, URLs and dates; digits and grouping folded."""
    body = _fold_digits(URL.sub(" ", text))
    body = FULL_DATE.sub(" ", MARKUP.sub(" ", body))
    if drop_ordinals:
        body = ORDINAL.sub(" ", body)
    body = _collapse_grouping(body)
    return Counter(
        match.group().replace(",", ".").replace("\u066b", ".")
        for match in NUMBER.finditer(body)
    )


class GameNumberCheck(TargetCheck):
    """Every quantity the source states must survive into the translation."""

    check_id = "game-number"
    name = gettext_lazy("Game number")
    description = gettext_lazy(
        "A number from the source string is missing from the translation."
    )
    # Always on: a wrong damage or radius value is a defect, not a preference.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        source_numbers = _numbers(source, drop_ordinals=True)
        if not source_numbers or not target:
            return False
        return bool(source_numbers - _numbers(target))


class GameTokenCheck(TargetCheck):
    """Every engine token the source substitutes must survive into the translation."""

    check_id = "game-token"
    name = gettext_lazy("Game token")
    description = gettext_lazy(
        "An engine token from the source string is missing from the translation."
    )
    # Always on: a translated token name resolves to nothing at runtime.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        source_tokens = _tokens_dsl(source)
        if not source_tokens or not target:
            return False
        return bool(source_tokens - _tokens_dsl(target))
