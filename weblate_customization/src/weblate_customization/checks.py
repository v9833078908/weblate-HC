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
from decimal import Decimal, InvalidOperation
from functools import cache
from itertools import pairwise
from operator import itemgetter
from typing import NamedTuple

import regex
from django.utils.translation import gettext_lazy

from weblate.checks.base import Highlight, TargetCheck
from weblate.trans.protected_tokens import (
    MARKUP,
    PLACEHOLDER_PATTERN,
    markup_tokens,
    placeholder_sequence,
)

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
# A scale word carries the zeros a digit would spell out, so "10 тысяч" and
# "10 thousand" are one quantity with two readings. Only the languages this fork
# uses as a source are listed: a target whose spelling is not here keeps its
# literal digits and therefore behaves exactly as before.
WORD_SCALES: dict[str, dict[str, int]] = {
    "ru": dict.fromkeys(
        ("тыс", "тысяча", "тысячи", "тысяч", "тысячу", "тысячах"), 1_000
    )
    | dict.fromkeys(("млн", "миллион", "миллиона", "миллионов"), 1_000_000)
    | dict.fromkeys(("млрд", "миллиард", "миллиарда", "миллиардов"), 1_000_000_000),
    "en": dict.fromkeys(("thousand", "thousands"), 1_000)
    | dict.fromkeys(("million", "millions"), 1_000_000)
    | dict.fromkeys(("billion", "billions"), 1_000_000_000)
    | dict.fromkeys(("trillion", "trillions"), 1_000_000_000_000),
}
# CJK scale notation is a closed character set, so a parsed run is the whole
# quantity and its digits are not separately readable: that is what makes "10万"
# ten times "1万" instead of a restatement of "10". Each group holds the same
# scale written in Japanese, Chinese and Korean.
SECTION_SCALES = {"十": 10, "百": 100, "千": 1_000, "십": 10, "백": 100, "천": 1_000}
BIG_SCALES = (
    dict.fromkeys("万萬만", 10_000)
    | dict.fromkeys("億亿억", 100_000_000)
    | dict.fromkeys("兆조", 1_000_000_000_000)
)
CJK_DIGITS = {char: value for value, char in enumerate("〇一二三四五六七八九")} | {
    "零": 0,
    "两": 2,
    "兩": 2,
}
# Language.is_cjk() holds the same set, but the verdict stays a pure string
# function so the offline probes can share it.
CJK_LANGUAGES = frozenset({"ja", "zh", "ko"})

_MANTISSA = r"\d+(?:[.,]\d+)?"
# One compiled pattern per language: a single alternation over every table would
# read a Russian word in a German string. Case-insensitive, because "Tausend",
# "Million" and "Тысяч" are all normal spellings.
WORD_SCALE_PATTERNS = {
    code: regex.compile(
        rf"({_MANTISSA})\s*(" + "|".join(sorted(table, key=len, reverse=True)) + r")\b",
        regex.IGNORECASE,
    )
    for code, table in WORD_SCALES.items()
}
_CJK_NUMERALS = "".join((*CJK_DIGITS, *SECTION_SCALES, *BIG_SCALES))
# A run is a maximal stretch of numerals, digits and their separators holding at
# least one CJK numeral. "3回に1回" holds none and stays two plain numbers.
CJK_RUN = regex.compile(
    rf"[\d.,{_CJK_NUMERALS}]*[{_CJK_NUMERALS}][\d.,{_CJK_NUMERALS}]*"
)
_WHOLE_MANTISSA = regex.compile(rf"\A{_MANTISSA}\Z")


class Quantity(NamedTuple):
    """
    A stated quantity: the value read, and the literal reading it may fall back to.

    An empty fallback means the reading admits no alternative.
    """

    value: Decimal
    fallback: tuple[Decimal, ...]


class TargetQuantity(NamedTuple):
    """A target quantity and the literal tokens an open scale expression contains."""

    value: Decimal
    literals: tuple[Decimal, ...]


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


# Mission DSL substitution identifier before a bracketed, translated body:
# item_type[|{0}], skirmish_league_id[gen|в {0}|в любой лиге]. A bracket
# without a `|` is ordinary prose, not a substitution.
TOKEN_PATTERN = regex.compile(r"([a-z][a-z0-9_]*)\[[^\]]*\|")


def _tokens_dsl(text: str) -> Counter[str]:
    """Count engine substitution identifiers, ignoring their translated bodies."""
    return Counter(match.group(1) for match in TOKEN_PATTERN.finditer(text))


_CONDITIONAL_HEADER = regex.compile(
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_]*):cond:(?P<comparison>[^{}?|]+)\?"
)


def _balanced_brace_blocks(
    text: str,
) -> list[tuple[int, int, list[tuple[int, int]]]]:
    """Return a non-recursive tree of balanced brace blocks in one pass."""
    blocks: list[tuple[int, int, list[tuple[int, int]]]] = []
    starts: list[tuple[int, list[tuple[int, int]]]] = []
    for position, char in enumerate(text):
        if char == "{":
            starts.append((position, []))
        elif char == "}" and starts:
            start, children = starts.pop()
            block = (start, position + 1, children)
            blocks.append(block)
            if starts:
                starts[-1][1].append((start, position + 1))
    return [] if starts else blocks


def _unmatched_braces(text: str) -> tuple[int, int]:
    """
    Count the braces the engine cannot pair: unmatched closes, then opens.

    `_balanced_brace_blocks()` ignores a stray closing brace, so a target that
    appends one keeps every span, token and signature of a well-formed one.
    The engine does not: the count is what tells the two apart.
    """
    opens = closes = 0
    for char in text:
        if char == "{":
            opens += 1
        elif char == "}":
            if opens:
                opens -= 1
            else:
                closes += 1
    return closes, opens


def _parse_conditional_dsl(
    text: str,
) -> tuple[list[tuple[int, int]], tuple[tuple[str, ...], ...]]:
    """
    Return immutable conditional spans and the ordered conditional signatures.

    A signature holds the exact header, every immediate nested placeholder and
    every top-level delimiter, in source order, and no branch text. Only the
    outermost recognized conditional is a record: a nested one travels verbatim
    inside its parent, which keeps spans non-overlapping and walks every branch
    interior once.
    """
    spans: list[tuple[int, int]] = []
    signatures: list[tuple[str, ...]] = []
    recognized_end = 0

    for start, end, children in sorted(_balanced_brace_blocks(text), key=itemgetter(0)):
        if start < recognized_end:
            continue
        header = _CONDITIONAL_HEADER.match(text, start + 1, end - 1)
        if header is None:
            continue

        # The header's comparison cannot cross a nested placeholder. The
        # matching outer brace proved every nested placeholder is complete.
        spans.extend(
            (
                (start, start + 1),
                (start + 1, header.end()),
                (end - 1, end),
            )
        )
        spans.extend(children)

        signature = [text[start + 1 : header.end()]]
        children_by_start = dict(children)
        position = header.end()
        while position < end - 1:
            child_end = children_by_start.get(position)
            if child_end is not None:
                signature.append(text[position:child_end])
                position = child_end
            elif text[position] == "|":
                spans.append((position, position + 1))
                signature.append("|")
                position += 1
            else:
                position += 1

        signatures.append(tuple(signature))
        recognized_end = end

    return spans, tuple(signatures)


def conditional_dsl_syntax_spans(text: str) -> list[tuple[int, int]]:
    """
    Return immutable spans in the documented Hero Craft conditional DSL.

    Branch text remains unprotected so it can be translated. Nested brace
    placeholders, delimiters, and a directly adjacent placeholder separator
    are syntax rather than rendered text.
    """
    spans, _signatures = _parse_conditional_dsl(text)
    simple_placeholders = [
        (match.start(), match.end())
        for match in PLACEHOLDER_PATTERN.finditer(text)
        if match.group().startswith("{")
    ]
    # A separate rule, never part of a signature: it protects the `:` in
    # `{minutes:00}:{seconds:00}`, which belongs to no conditional.
    for previous, following in pairwise(simple_placeholders):
        if previous[1] + 1 == following[0] and text[previous[1]] == ":":
            spans.append((previous[1], following[0]))

    return sorted({span for span in spans if span[0] < span[1]})


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
        if not target:
            return False
        # The existing comparisons run first: they are cheap, and a string that
        # already fails must not pay for the conditional parser. The substring
        # test is a C-level scan, while the parser is a per-character loop.
        if Counter(markup_tokens(source)) != Counter(markup_tokens(target)):
            return True
        if placeholder_sequence(source) != placeholder_sequence(target):
            return True
        if ":cond:" not in source:
            return False

        source_conditionals = _parse_conditional_dsl(source)[1]
        if not source_conditionals:
            return False
        return source_conditionals != _parse_conditional_dsl(target)[1] or (
            _unmatched_braces(source) != _unmatched_braces(target)
        )

    def check_highlight(self, source: str, unit):
        if self.should_skip(unit):
            return []

        spans: dict[tuple[int, int], str] = {}
        for match in MARKUP.finditer(source):
            spans[match.start(), match.end()] = (
                "markup" if match.group().startswith("<") else "syntax"
            )
        for span in conditional_dsl_syntax_spans(source):
            spans[span] = "syntax"

        return [
            Highlight(start, end, source[start:end], kind=kind)
            for (start, end), kind in sorted(spans.items())
        ]


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


def _decimal(text: str) -> Decimal | None:
    """Parse a number token as a value, or return None when it will not parse."""
    try:
        return Decimal(text.replace(",", ".").replace("\u066b", "."))
    except InvalidOperation:
        return None


def _cjk_value(run: str) -> Decimal | None:
    """Value of a CJK numeral run, or None when it is not a well-formed quantity."""
    total = section = Decimal(0)
    current: Decimal | None = None
    group_seen = scale_seen = False
    last_section = last_big = Decimal("Infinity")
    index = 0
    while index < len(run):
        char = run[index]
        if char.isdigit():
            end = index
            while end < len(run) and (run[end].isdigit() or run[end] in ".,"):
                end += 1
            chunk = run[index:end].rstrip(".,")
            if current is not None or not _WHOLE_MANTISSA.match(chunk):
                return None
            if (current := _decimal(chunk)) is None:
                return None
            group_seen = True
            index += len(chunk)
            continue
        if char in CJK_DIGITS:
            # Two numerals in a row are not a quantity: "一二万" is prose.
            if current is not None:
                return None
            current = Decimal(CJK_DIGITS[char])
            group_seen = True
        elif char in SECTION_SCALES:
            scale = Decimal(SECTION_SCALES[char])
            if scale >= last_section:
                return None
            # A bare section scale means one of it: "十" is ten.
            section += (Decimal(1) if current is None else current) * scale
            current, group_seen, last_section, scale_seen = None, True, scale, True
        elif char in BIG_SCALES:
            scale = Decimal(BIG_SCALES[char])
            # A big scale needs an explicit group, so "万一" is not a quantity.
            # Never write `current or Decimal(1)`: that turns "0万" into 10000.
            if not group_seen or scale >= last_big:
                return None
            total += (
                section + (current if current is not None else Decimal(0))
            ) * scale
            section, current, group_seen = Decimal(0), None, False
            last_section, last_big, scale_seen = Decimal("Infinity"), scale, True
        else:
            return None
        index += 1
    if not scale_seen:
        return None
    return total + section + (current if current is not None else Decimal(0))


def _prepare(text: str, *, drop_ordinals: bool) -> str:
    """Body ready for number extraction: markup, URLs and dates folded away."""
    body = _fold_digits(URL.sub(" ", text))
    body = FULL_DATE.sub(" ", MARKUP.sub(" ", body))
    if drop_ordinals:
        body = ORDINAL.sub(" ", body)
    return _collapse_grouping(body)


def _covered(start: int, end: int, spans: list[tuple]) -> bool:
    """Whether [start, end) overlaps any span's [start, end)."""
    return any(start < span[1] and span[0] < end for span in spans)


def _number_tokens(body: str, skip: list[tuple]) -> list[Decimal]:
    """Plain number tokens outside every span in `skip`, in text order."""
    values = []
    for match in NUMBER.finditer(body):
        if _covered(match.start(), match.end(), skip):
            continue
        value = _decimal(match.group())
        if value is not None:
            values.append(value)
    return values


def _closed_spans(
    body: str, language: str | None
) -> list[tuple[int, int, Decimal, tuple[Decimal, ...]]]:
    """Parse CJK scale runs: the run owns its digits, so they read as one value."""
    if language not in CJK_LANGUAGES:
        return []
    spans = []
    for match in CJK_RUN.finditer(body):
        raw = match.group()
        run = raw.strip(".,")
        if not run:
            continue
        start = match.start() + (len(raw) - len(raw.lstrip(".,")))
        end = match.end() - (len(raw) - len(raw.rstrip(".,")))
        value = _cjk_value(run)
        if value is None:
            continue
        digits = tuple(
            digit
            for token in NUMBER.finditer(run)
            if (digit := _decimal(token.group())) is not None
        )
        spans.append((start, end, value, digits))
    return spans


def _open_spans(
    body: str,
    language: str | None,
    skip: list[tuple[int, int, Decimal, tuple[Decimal, ...]]],
) -> list[tuple[int, int, Decimal, tuple[Decimal, ...]]]:
    """Word-scale readings: a table hit *adds* a reading, the mantissa stays literal."""
    pattern = WORD_SCALE_PATTERNS.get(language)
    if pattern is None:
        return []
    table = WORD_SCALES[language]
    spans: list[tuple[int, int, Decimal, tuple[Decimal, ...]]] = []
    last_factor = Decimal("Infinity")
    for match in pattern.finditer(body):
        if _covered(match.start(), match.end(), skip):
            continue
        mantissa = _decimal(match.group(1))
        if mantissa is None:
            continue
        factor = Decimal(table[match.group(2).casefold()])
        value = mantissa * factor
        # Fold a descending compound ("1 million 500 thousand") into the span
        # it follows; an ascending one ("1 thousand 2 million") stays separate.
        if (
            spans
            and not body[spans[-1][1] : match.start()].strip()
            and factor < last_factor
        ):
            start, _end, prev_value, prev_mantissas = spans[-1]
            spans[-1] = (
                start,
                match.end(),
                prev_value + value,
                (*prev_mantissas, mantissa),
            )
        else:
            spans.append((match.start(), match.end(), value, (mantissa,)))
        last_factor = factor
    return spans


def _quantities(
    text: str, language: str | None, *, drop_ordinals: bool
) -> list[Quantity]:
    """Every quantity the text states, each with its literal fallback."""
    body = _prepare(text, drop_ordinals=drop_ordinals)
    closed = _closed_spans(body, language)
    spans = closed + _open_spans(body, language, closed)
    quantities = [Quantity(value, fallback) for _, _, value, fallback in spans]
    quantities.extend(Quantity(value, ()) for value in _number_tokens(body, spans))
    return quantities


def _readings(text: str, language: str | None) -> list[TargetQuantity]:
    """Target quantities whose value and literal readings share one occurrence."""
    body = _prepare(text, drop_ordinals=False)
    closed = _closed_spans(body, language)
    open_spans = _open_spans(body, language, closed)
    readings = [TargetQuantity(value, ()) for _, _, value, _ in closed]
    readings.extend(
        TargetQuantity(value, literals) for _, _, value, literals in open_spans
    )
    readings.extend(
        TargetQuantity(value, ())
        for value in _number_tokens(body, [*closed, *open_spans])
    )
    return readings


def _consume_options(
    readings: list[TargetQuantity],
    literal_bits: tuple[tuple[Decimal, int, int], ...],
    span_literal_masks: tuple[int, ...],
    needed: tuple[Decimal, ...],
    value_mask: int,
    literal_mask: int,
) -> set[tuple[int, int]]:
    """Return every way to consume `needed` without reusing a target occurrence."""
    options = {(value_mask, literal_mask)}
    for value in needed:
        next_options = set()
        for used_values, used_literals in options:
            for index, reading in enumerate(readings):
                value_bit = 1 << index
                if (
                    reading.value == value
                    and not used_values & value_bit
                    and not used_literals & span_literal_masks[index]
                ):
                    next_options.add((used_values | value_bit, used_literals))
            for literal, index, literal_bit in literal_bits:
                if (
                    literal == value
                    and not used_literals & literal_bit
                    and not used_values & (1 << index)
                ):
                    next_options.add(
                        (used_values, used_literals | span_literal_masks[index])
                    )
        options = next_options
        if not options:
            break
    return options


def _matches(stated: list[Quantity], readings: list[TargetQuantity]) -> bool:
    """Whether every source quantity has a complete, non-conflicting target match."""
    literal_bits: list[tuple[Decimal, int, int]] = []
    span_literal_masks = []
    for index, reading in enumerate(readings):
        span_mask = 0
        for literal in reading.literals:
            literal_bit = 1 << len(literal_bits)
            literal_bits.append((literal, index, literal_bit))
            span_mask |= literal_bit
        span_literal_masks.append(span_mask)

    @cache
    def match(source_index: int, value_mask: int, literal_mask: int) -> bool:
        if source_index == len(stated):
            return True
        quantity = stated[source_index]
        needs = ((quantity.value,),) + (
            (quantity.fallback,) if quantity.fallback else ()
        )
        for needed in needs:
            for next_value_mask, next_literal_mask in _consume_options(
                readings,
                tuple(literal_bits),
                tuple(span_literal_masks),
                needed,
                value_mask,
                literal_mask,
            ):
                if match(source_index + 1, next_value_mask, next_literal_mask):
                    return True
        return False

    return match(0, 0, 0)


def _base_language(code: str | None) -> str | None:
    """`zh_Hans` and `zh-Hant` both write 萬, so only the base code matters."""
    if code is None:
        return None
    return code.split("_", 1)[0].split("-", 1)[0]


def game_number_fails(
    source: str,
    target: str,
    *,
    source_language: str | None,
    target_language: str | None,
) -> bool:
    """Whether a quantity the source states is missing from the translation."""
    stated = _quantities(source, _base_language(source_language), drop_ordinals=True)
    return bool(
        stated
        and target
        and not _matches(stated, _readings(target, _base_language(target_language)))
    )


class GameNumberCheck(TargetCheck):
    """
    Every quantity the source states must survive into the translation.

    Comparison is by numeric value, as a multiset of `Decimal`, not by digits: a
    scale word carries the zeros a digit would spell out, so "10 тысяч" equals
    "10 Tausend" and "10,000" but not "10万". A scale word is understood for
    `ru` and `en` source and target text; any other spelling keeps its literal
    digits and is compared exactly as before. CJK scale notation (`ja`, `zh`,
    `ko`) is parsed exactly, and the run's digits are not separately readable.
    A quantity spelled out entirely in words ("десять тысяч") carries no number
    on either side, so a worded source states nothing and a worded target
    satisfies nothing - ignore-game-number remains the escape hatch.
    """

    check_id = "game-number"
    name = gettext_lazy("Game number")
    description = gettext_lazy(
        "A number from the source string is missing from the translation."
    )
    # Always on: a wrong damage or radius value is a defect, not a preference.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        return game_number_fails(
            source,
            target,
            source_language=unit.translation.component.source_language.base_code,
            target_language=unit.translation.language.base_code,
        )


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


def _visible_length(text: str) -> int:
    """Length of what the player reads: markup tags and engine placeholders removed."""
    return len(MARKUP.sub("", text))


# Expansion is not linear: a one-word button legitimately doubles where a full
# sentence grows by a third. Tiers follow the loc-industry guidance; the floor
# keeps a tiny source ("OK", "+{0}") from firing on a reasonable short target.
# This is a character proxy for a rendered width: it catches gross overflow,
# not a pixel-perfect fit. For that, use max-size with the game font.
_LENGTH_TIERS = (
    # (max source length, ratio, minimum target length)
    (10, 3.0, 28),
    (30, 2.0, 40),
    (80, 1.5, 90),
)
_LENGTH_MAX_RATIO = 1.35


class GameLengthCheck(TargetCheck):
    """Translation is far longer than the source and likely overflows its slot."""

    check_id = "game-length"
    name = gettext_lazy("Game length")
    description = gettext_lazy(
        "The translation is much longer than the source and likely overflows "
        "its UI slot. Ignore with the ignore-game-length flag when the space "
        "is known to fit."
    )
    # Always on: an overflowing label clips in the running game.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        if not source or not target:
            return False
        source_len = _visible_length(source)
        target_len = _visible_length(target)
        if source_len == 0 or target_len == 0:
            return False
        for max_source, ratio, minimum in _LENGTH_TIERS:
            if source_len <= max_source:
                return target_len > minimum and target_len > source_len * ratio
        return target_len > source_len * _LENGTH_MAX_RATIO
