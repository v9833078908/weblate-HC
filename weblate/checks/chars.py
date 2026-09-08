# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
import string
import unicodedata
from typing import TYPE_CHECKING, ClassVar

import regex
from django.utils.html import format_html
from django.utils.translation import gettext_lazy, ngettext

from weblate.checks.base import CountingCheck, TargetCheck, TargetCheckParametrized
from weblate.checks.markup import strip_entities
from weblate.checks.parser import single_value_flag
from weblate.checks.utils import highlight_string
from weblate.utils.html import MD_LINK, format_html_join_comma

if TYPE_CHECKING:
    from collections.abc import Iterable

    from weblate.trans.models import Unit

    from .base import FixupType

FRENCH_PUNCTUATION_NBSP = {":"}
FRENCH_PUNCTUATION_NNBSP = {";", "?", "!"}
FRENCH_PUNCTUATION = FRENCH_PUNCTUATION_NBSP.union(FRENCH_PUNCTUATION_NNBSP)
FRENCH_PUNCTUATION_SPACING = {"Zs", "Ps", "Pe"}
FRENCH_PUNCTUATION_FIXUP_RE_NBSP = (
    f"([ \u2009\u202f])([{''.join(FRENCH_PUNCTUATION_NBSP)}])"
)
FRENCH_PUNCTUATION_FIXUP_RE_NNBSP = (
    f"([ \xa0\u2009])([{''.join(FRENCH_PUNCTUATION_NNBSP)}])"
)
FRENCH_PUNCTUATION_MISSING_RE_NBSP = f"([^\xa0])([{''.join(FRENCH_PUNCTUATION_NBSP)}])"
FRENCH_PUNCTUATION_MISSING_RE_NNBSP = (
    f"([^\u202f])([{''.join(FRENCH_PUNCTUATION_NNBSP)}])"
)
MARKDOWN_IMAGE_MARKER = re.compile(r"!\[")
MY_QUESTION_MARK = "\u1038\u104b"
INTERROBANGS = ("?!", "!?", "؟!", "!؟", "？！", "！？", "⁈", "⁉")


def validate_accelerator_marker(marker: str) -> None:
    if len(marker) != 1 or marker not in string.punctuation:
        msg = f"Accelerator marker should be a single punctuation character: {marker}"
        raise ValueError(msg)


parse_accelerator_marker = single_value_flag(str, validate_accelerator_marker)


def markdown_link_syntax_ranges(target: str) -> Iterable[tuple[int, int]]:
    for match in MD_LINK.finditer(target):
        if target[match.start()] == "!":
            yield match.start(), match.start() + 1
        link_text_start = match.start(1)
        if link_text_start != -1:
            yield from (
                (link_text_start + marker.start(), link_text_start + marker.start() + 1)
                for marker in MARKDOWN_IMAGE_MARKER.finditer(match.group(1))
            )
        for group_index in (3, 4, 5, 6):
            start = match.start(group_index)
            if start != -1:
                yield start, match.end(group_index)


class AcceleratorKeyCheck(TargetCheckParametrized):
    """Check for inconsistent accelerator keys."""

    check_id = "accelerator"
    name = gettext_lazy("Accelerator key")
    description = gettext_lazy(
        "Source and translation contain inconsistent accelerator keys."
    )
    default_disabled = True
    version_added = "2026.7"

    @property
    def param_type(self):
        return parse_accelerator_marker

    def _count_accelerators(self, text: str, key: str) -> int:
        count = 0
        pos = 0
        while True:
            pos = text.find(key, pos)
            if pos == -1:
                return count
            # A doubled marker escapes a literal marker in GNU gettext msgfmt.
            if pos + 1 < len(text) and text[pos + 1] == key:
                pos += 2
            else:
                count += 1
                pos += 1

    def _check_accelerator(self, source: str, target: str, key: str) -> bool:
        src_count = self._count_accelerators(source, key)
        tgt_count = self._count_accelerators(target, key)
        return tgt_count > 1 or src_count != tgt_count

    def check_single(self, source: str, target: str, unit: Unit) -> bool:
        if not self.has_value(unit):
            return False
        try:
            key = self.get_value(unit)
        except ValueError:
            return True
        return self._check_accelerator(source, target, key)

    def check_target_params(
        self, sources: list[str], targets: list[str], unit: Unit, value: str
    ) -> bool:
        if len(sources) == 1 and len(targets) == 1:
            return self._check_accelerator(sources[0], targets[0], value)
        return any(self.check_target_generator(sources, targets, unit))


class BeginNewlineCheck(TargetCheck):
    """Check for newlines at beginning."""

    check_id = "begin_newline"
    name = gettext_lazy("Starting newline")
    description = gettext_lazy(
        "Source and translation do not both start with a newline."
    )

    def check_single(self, source: str, target: str, unit: Unit):
        return self.check_chars(source, target, 0, {"\n"})


class EndNewlineCheck(TargetCheck):
    """Check for newlines at end."""

    check_id = "end_newline"
    name = gettext_lazy("Trailing newline")
    description = gettext_lazy("Source and translation do not both end with a newline.")

    def check_single(self, source: str, target: str, unit: Unit):
        return self.check_chars(source, target, -1, {"\n"})


class BeginSpaceCheck(TargetCheck):
    """Whitespace check, starting whitespace usually is important for UI."""

    check_id = "begin_space"
    name = gettext_lazy("Starting spaces")
    description = gettext_lazy(
        "Source and translation do not both start with same number of spaces."
    )

    def check_single(self, source: str, target: str, unit: Unit):
        # One letter things are usually decimal/thousand separators
        if len(source) <= 1 and len(target) <= 1:
            return False

        stripped_target = target.lstrip(" ")
        stripped_source = source.lstrip(" ")

        # String translated to spaces only
        if not stripped_target:
            return False

        # Count space chars in source and target
        source_space = len(source) - len(stripped_source)
        target_space = len(target) - len(stripped_target)

        # Compare numbers
        return source_space != target_space

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        stripped_source = source.lstrip(" ")
        spaces = len(source) - len(stripped_source)
        replacement = source[:spaces] if spaces else ""
        return [("regex", "^ *", replacement, "u")]


class KabyleCharactersCheck(TargetCheck):
    """Flag and suggest standard Kabyle characters instead of visually similar but incorrect ones."""

    check_id = "kabyle-characters"
    name = gettext_lazy("Non‑standard characters in Kabyle")
    description = gettext_lazy(
        "Use standardized Latin Kabyle characters (e.g. ɣ instead of Greek γ; ɛ instead of ε)."
    )
    version_added = "5.12"
    mass_fixup = "safe"

    confusable_to_standard: ClassVar[dict[str, str]] = {
        "\u03b3": "\u0263",
        "\u0393": "\u0194",
        "\u03b5": "\u025b",
        "\u0395": "\u0190",
        "\u011f": "\u01e7",
        "\u011e": "\u01e6",
    }

    def should_skip(self, unit: Unit) -> bool:
        # Only run on Kabyle (covers 'kab' plus any variants)
        if not unit.translation.language.is_base({"kab"}):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit) -> bool:
        # by now we know it's Kabyle, so just look for confusables
        return any(char in target for char in self.confusable_to_standard)

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        return [
            ("regex", re.escape(confusable), standard, "gu")
            for confusable, standard in self.confusable_to_standard.items()
        ]


class EndSpaceCheck(TargetCheck):
    """Whitespace check."""

    check_id = "end_space"
    name = gettext_lazy("Trailing space")
    description = gettext_lazy("Source and translation do not both end with a space.")

    def check_single(self, source: str, target: str, unit: Unit):
        # One letter things are usually decimal/thousand separators
        if len(source) <= 1 and len(target) <= 1:
            return False
        if not source or not target:
            return False

        stripped_target = target.rstrip(" ")
        stripped_source = source.rstrip(" ")

        # String translated to spaces only
        if not stripped_target:
            return False

        # Count space chars in source and target
        source_space = len(source) - len(stripped_source)
        target_space = len(target) - len(stripped_target)

        # Compare numbers
        return source_space != target_space

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        stripped_source = source.rstrip(" ")
        spaces = len(source) - len(stripped_source)
        replacement = source[-spaces:] if spaces else ""
        return [("regex", " *$", replacement, "u")]


class DoubleSpaceCheck(TargetCheck):
    """Doublespace check."""

    check_id = "double_space"
    name = gettext_lazy("Double space")
    description = gettext_lazy("Translation contains double space.")
    mass_fixup = "safe"

    def check_single(self, source: str, target: str, unit: Unit):
        # One letter things are usually decimal/thousand separators
        if len(source) <= 1 and len(target) <= 1:
            return False
        if not source or not target:
            return False
        if "  " in source:
            return False
        # Check if target contains double space
        return "  " in target

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        return [("regex", " {2,}", " ", "u")]


def _is_french_terminal(unit: Unit) -> bool:
    """Whether the unit's target language uses French terminal spacing."""
    language = unit.translation.language
    return language.is_base({"fr"}) and language.code != "fr_CA"


def _terminal_mark(unit: Unit, base: str) -> str:
    """
    Render a terminal punctuation mark for the unit's target language.

    `base` is one of ``.``, ``:``, ``?`` or ``!`` - the *family* the source
    string's own terminal mark belongs to (the source may itself end with
    any member of that family accepted by the matching check's branches
    below, not only the plain ASCII character). The result satisfies the
    matching terminal check's own accepted-marks set (CJK fullwidth marks,
    the Greek and Arabic question marks, French non-breaking spacing before
    ``:``, ``?`` and ``!``); everywhere else it is the plain ASCII mark
    itself, which every remaining language branch in `EndStopCheck`/
    `EndColonCheck`/`EndQuestionCheck`/`EndExclamationCheck` already
    accepts. This table is only ever a proposal - the mass-fix engine
    (`weblate/trans/fix_check.py`) recomputes the check before and after
    applying it and discards anything that does not actually clear it.
    """
    language = unit.translation.language
    if language.is_cjk():
        return {".": "。", ":": "：", "?": "？", "!": "！"}[base]
    if base == "?":
        if language.is_base({"el"}):
            return "\u037e"  # Greek question mark
        if language.is_base({"ar"}):
            return "؟"
        if language.is_base({"my"}):
            return MY_QUESTION_MARK
    if _is_french_terminal(unit):
        if base == ":":
            return f"\u00a0{base}"
        if base in FRENCH_PUNCTUATION_NNBSP:
            return f"\u202f{base}"
    return base


# --- Source-side mark detection --------------------------------------------
#
# Whether the *source* string ends with a mark this check's target-language
# branch treats as belonging to that check's family - i.e. whether a
# missing counterpart in target is exactly what would make `check_single`
# fail for this unit. Each function below is a direct, deliberately
# unabbreviated transcription of the corresponding check's own branch
# dispatch (same language gates, same per-branch accepted sets, including
# the asymmetric ones where a branch's *source*-side gate is narrower than
# what it accepts on the *target* side, e.g. Greek/Armenian/Burmese
# `end_question`, which only ever gate on a literal ASCII ``?`` in source
# even though they accept a wider target-side mark). Getting this wrong in
# either direction is safe by construction - `_terminal_append_fixup`'s own
# conflict guard and the mass-fix engine's before/after contract (Task 2)
# both still gate the final write - but an under-broad predicate here would
# silently leave real, currently-failing rows without a proposed fixup.
def _stop_source_has_mark(source: str, target: str, unit: Unit) -> bool:
    if not source:
        return False
    language = unit.translation.language
    if language.is_cjk() and source[-1] in {":", ";"}:
        # `EndStopCheck._check_ja`'s own gate; only entered for a source
        # ending in `:`/`;`, never for a source ending in `.`/`。`/etc,
        # which falls through to the default branch below instead.
        return True
    if language.is_base({"hy"}):
        return source[-1] in {
            ".",
            "。",
            "।",
            "۔",
            "։",
            "·",
            "෴",
            "។",
            ":",
            "՝",
            "?",
            "!",
            "`",
        }
    if language.is_base({"hi", "bn", "or"}):
        return source[-1] in {".", "\u0964", "\u09f7", "|"}
    if language.is_base({"sat"}):
        return source[-1] in {".", "᱾"}
    if language.is_base({"my"}):
        if target.endswith(MY_QUESTION_MARK):
            return False
        return source[-1] in {".", "။"}
    return source[-1] in {".", "。", "।", "۔", "։", "·", "෴", "។", "።"}


def _colon_source_has_mark(source: str, unit: Unit) -> bool:
    if not source:
        return False
    language = unit.translation.language
    if language.is_base({"hy"}):
        return source[-1] == ":"
    if language.is_cjk():
        return source[-1] in {":", ";"}
    return source[-1] in {":", "：", "៖"}


def _question_source_has_mark(source: str, unit: Unit) -> bool:
    if not source:
        return False
    if unit.translation.language.is_base({"hy", "el", "my"}):
        # `_check_hy`/`_check_el`/`_check_my` all gate on a literal `?` in
        # source; the wider sets they compare against (`question_el`,
        # `MY_QUESTION_MARK`, and hy's own `{"?", "՞", "："}`)  # ruff: ignore[ambiguous-unicode-character-comment]
        # are target-side only.
        return source[-1] == "?"
    return source[-1] in {"?", "՞", "؟", "⸮", "？", "፧", "꘏", "⳺"}


def _exclamation_source_has_mark(source: str, unit: Unit) -> bool:
    if not source:
        return False
    if unit.translation.language.is_base({"my"}):
        return source[-1] in {"!", "႟"}
    return source[-1] in {"!", "！", "՜", "᥄", "႟", "߹"}


# --- Conflict guard ----------------------------------------------------
#
# Every character some language branch of the matching terminal check
# accepts on either side (source or target) as a terminal mark of that
# family - a deliberately *wide* union, unlike the precise per-branch
# detection above. A target already ending with one of these is never
# touched by `_terminal_append_fixup`: appending over it would trade one
# mismatched mark for another (`?` becoming `?.`) instead of clearing the
# check, so the unit is left for a human (`manual` bucket). Being wide here
# is safe - it can only make the guard refuse more often, never propose a
# wrong fix.
TERMINAL_MARK_CHARS = frozenset(
    {
        # end_stop
        ".",
        "。",
        "।",
        "۔",
        "։",
        "·",
        "෴",
        "។",
        "።",
        "\u09f7",
        "|",
        "᱾",
        "။",
        # end_colon
        ":",
        "：",
        "៖",
        "՝",
        "`",
        ";",
        # end_question
        "?",
        "՞",
        "؟",
        "⸮",
        "？",
        "፧",
        "꘏",
        "⳺",
        "\u037e",
        # end_exclamation
        "!",
        "！",
        "՜",
        "᥄",
        "႟",
        "߹",
        "¡",
        # end_interrobang tails not already listed above
        "⁈",
        "⁉",
        # never touch a deliberate ellipsis
        "…",
    }
)
_TERMINAL_CONFLICT_CLASS = "".join(
    re.escape(char) for char in sorted(TERMINAL_MARK_CHARS)
)


def _terminal_append_fixup(mark: str, *, strip_prefix: str = "") -> list[FixupType]:
    r"""
    Build a fixup that appends `mark`, refusing an existing terminal mark.

    The pattern matches only the (possibly empty) run of trailing
    whitespace, and only when a real, non-whitespace character precedes it
    and that character is not already some other terminal mark; replacing
    that span with `mark` therefore either appends cleanly onto real
    content, or - on an empty/whitespace-only target or a conflicting
    existing mark - changes nothing at all. `(?<=\S)` (a real character
    precedes), not `(?<!\s)` (merely "not whitespace", which is vacuously
    true at the very start of an empty string): the latter would let an
    untranslated or empty plural form be "fixed" into a bare mark on its
    own.

    `strip_prefix`, when given, is a single character consumed (at most
    once) immediately before the append point if present. Burmese
    `MY_QUESTION_MARK` is the two-codepoint sequence U+1038 VISARGA +
    U+104B SECTION, and U+1038 is also an ordinary Burmese word-final
    consonant marker; without this, a target already ending in U+1038
    would get a *second*, spurious one from the mark itself. Consuming one
    existing U+1038 first and then appending the full mark (whose own
    leading U+1038 replaces the one consumed) keeps that single visarga
    exactly once, immediately followed by the section mark, instead of
    silently doubling it.
    """
    prefix_pattern = f"{re.escape(strip_prefix)}?" if strip_prefix else ""
    pattern = rf"(?<![{_TERMINAL_CONFLICT_CLASS}])(?<=\S){prefix_pattern}\s*$"
    return [("regex", pattern, mark, "u")]


class EndStopCheck(TargetCheck):
    """Check for final stop."""

    check_id = "end_stop"
    name = gettext_lazy("Mismatched full stop")
    description = gettext_lazy(
        "Source and translation do not both end with a full stop."
    )
    mass_fixup = "review"

    def _check_my(self, source: str, target: str):
        if target.endswith(MY_QUESTION_MARK):
            # Laeave this on the question mark check
            return False
        return self.check_chars(source, target, -1, {".", "။"})

    def should_skip(self, unit: Unit) -> bool:
        # Thai and Lojban do not have a full stop
        if unit.translation.language.is_base({"th", "jbo"}):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit):
        if len(source) <= 4:
            # Might need to use shortcut in translation
            return False
        if not target:
            return False
        # Allow ... to be translated into ellipsis
        if source.endswith("...") and target[-1] == "…":
            return False
        if unit.translation.language.is_cjk() and source[-1] in {":", ";"}:
            # Japanese sentence might need to end with full stop
            # in case it's used before list.
            return self.check_chars(source, target, -1, {";", ":", "：", ".", "。"})
        if unit.translation.language.is_base({"hy"}):
            return self.check_chars(
                source,
                target,
                -1,
                {".", "。", "।", "۔", "։", "·", "෴", "។", ":", "՝", "?", "!", "`"},
            )
        if unit.translation.language.is_base({"hi", "bn", "or"}):
            # Using | instead of । is not typographically correct, but
            # seems to be quite usual. \u0964 is correct, but \u09F7
            # is also sometimes used instead in some popular editors.
            return self.check_chars(source, target, -1, {".", "\u0964", "\u09f7", "|"})
        if unit.translation.language.is_base({"sat"}):
            # Santali uses "᱾" as full stop
            return self.check_chars(source, target, -1, {".", "᱾"})
        if unit.translation.language.is_base({"my"}):
            return self._check_my(source, target)
        return self.check_chars(
            source, target, -1, {".", "。", "।", "۔", "։", "·", "෴", "។", "።"}
        )

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        if not _stop_source_has_mark(source, unit.target, unit):
            return None
        return _terminal_append_fixup(_terminal_mark(unit, "."))


class EndColonCheck(TargetCheck):
    """Check for final colon."""

    check_id = "end_colon"
    name = gettext_lazy("Mismatched colon")
    description = gettext_lazy("Source and translation do not both end with a colon.")
    mass_fixup = "review"

    def should_skip(self, unit: Unit) -> bool:
        # Thai and Lojban do not have a colon
        if unit.translation.language.is_base({"th", "jbo"}):
            return True
        return super().should_skip(unit)

    def _check_hy(self, source: str, target: str):
        if source[-1] == ":":
            return self.check_chars(source, target, -1, {":", "՝", "`"})
        return False

    def _check_ja(self, source: str, target: str):
        # Japanese sentence might need to end with full stop
        # in case it's used before list.
        if source[-1] in {":", ";"}:
            return self.check_chars(source, target, -1, {";", ":", "：", ".", "。"})
        return False

    def check_single(self, source: str, target: str, unit: Unit):
        if not source or not target:
            return False
        if unit.translation.language.is_base({"hy"}):
            return self._check_hy(source, target)
        if unit.translation.language.is_cjk():
            return self._check_ja(source, target)
        return self.check_chars(source, target, -1, {":", "：", "៖"})

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        if not _colon_source_has_mark(source, unit):
            return None
        return _terminal_append_fixup(_terminal_mark(unit, ":"))


class EndQuestionCheck(TargetCheck):
    """Check for final question mark."""

    check_id = "end_question"
    name = gettext_lazy("Mismatched question mark")
    description = gettext_lazy(
        "Source and translation do not both end with a question mark."
    )
    question_el = ("?", ";", ";")
    mass_fixup = "review"

    def should_skip(self, unit: Unit) -> bool:
        # Thai and Lojban do not have a question mark
        if unit.translation.language.is_base({"th", "jbo"}):
            return True
        return super().should_skip(unit)

    def _check_hy(self, source: str, target: str):
        if source[-1] == "?":
            return self.check_chars(source, target, -1, {"?", "՞", "։"})
        return False

    def _check_el(self, source: str, target: str):
        if source[-1] != "?":
            return False
        return target[-1] not in self.question_el

    def _check_my(self, source: str, target: str):
        return source.endswith("?") != target.endswith(MY_QUESTION_MARK)

    def check_single(self, source: str, target: str, unit: Unit):
        if not source or not target:
            return False
        if source.endswith(INTERROBANGS) or target.endswith(INTERROBANGS):
            return False
        if unit.translation.language.is_base({"hy"}):
            return self._check_hy(source, target)
        if unit.translation.language.is_base({"el"}):
            return self._check_el(source, target)
        if unit.translation.language.is_base({"my"}):
            return self._check_my(source, target)

        return self.check_chars(
            source, target, -1, {"?", "՞", "؟", "⸮", "？", "፧", "꘏", "⳺"}
        )

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        if not _question_source_has_mark(source, unit) or source.endswith(INTERROBANGS):
            return None
        mark = _terminal_mark(unit, "?")
        if mark == MY_QUESTION_MARK:
            return _terminal_append_fixup(mark, strip_prefix=MY_QUESTION_MARK[0])
        return _terminal_append_fixup(mark)


class EndExclamationCheck(TargetCheck):
    """Check for final exclamation mark."""

    check_id = "end_exclamation"
    name = gettext_lazy("Mismatched exclamation mark")
    description = gettext_lazy(
        "Source and translation do not both end with an exclamation mark."
    )
    mass_fixup = "review"

    def should_skip(self, unit: Unit) -> bool:
        # Thai, Lojban, and Armenian do not have an exclamation mark
        if unit.translation.language.is_base({"hy", "th", "jbo"}):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit):
        if not source or not target:
            return False
        if source.endswith(INTERROBANGS) or target.endswith(INTERROBANGS):
            return False
        if (
            unit.translation.language.is_base({"eu"})
            and source[-1] == "!"
            and "¡" in target
            and "!" in target
        ):
            return False
        if unit.translation.language.is_base({"my"}):
            return self.check_chars(source, target, -1, {"!", "႟"})
        if source.endswith("Texy!") or target.endswith("Texy!"):
            return False
        return self.check_chars(source, target, -1, {"!", "！", "՜", "᥄", "႟", "߹"})

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        if not _exclamation_source_has_mark(source, unit) or source.endswith(
            INTERROBANGS
        ):
            return None
        return _terminal_append_fixup(_terminal_mark(unit, "!"))


class EndInterrobangCheck(TargetCheck):
    """Check for final interrobang expression."""

    check_id = "end_interrobang"
    name = gettext_lazy("Mismatched interrobang")
    description = gettext_lazy(
        "Source and translation do not both end with an interrobang expression."
    )
    mass_fixup = "review"

    def check_single(self, source: str, target: str, unit: Unit):
        if not source or not target:
            return False

        return source.endswith(INTERROBANGS) != target.endswith(INTERROBANGS)

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        source = unit.source_string
        if not source.endswith(INTERROBANGS):
            return None
        mark = next(form for form in INTERROBANGS if source.endswith(form))
        if _is_french_terminal(unit):
            mark = f"\u202f{mark}"
        return _terminal_append_fixup(mark)


class EndEllipsisCheck(TargetCheck):
    """Check for ellipsis at the end of string."""

    check_id = "end_ellipsis"
    name = gettext_lazy("Mismatched ellipsis")
    description = gettext_lazy(
        "Source and translation do not both end with an ellipsis."
    )

    def should_skip(self, unit: Unit) -> bool:
        # Thai and Lojban do not have an ellipsis
        if unit.translation.language.is_base({"th", "jbo"}):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit):
        if not target:
            return False
        # Allow ... to be translated into ellipsis
        if source.endswith("...") and target[-1] == "…":
            return False
        return self.check_chars(source, target, -1, {"…"})


class EscapedNewlineCountingCheck(CountingCheck):
    r"""Check whether there is same amount of escaped \n strings."""

    string = "\\n"
    check_id = "escaped_newline"
    name = gettext_lazy("Mismatched \\n")
    description = gettext_lazy(
        "Number of \\n literals in translation does not match source."
    )

    ignore_re = re.compile(r"[A-Z]:\\\\[^\\ ]+(\\[^\\ ]+)+")

    def check_single(self, source: str, target: str, unit: Unit):
        if not target or not source:
            return False

        target = self.ignore_re.sub("", target)
        source = self.ignore_re.sub("", source)
        return super().check_single(source, target, unit)


class NewLineCountCheck(CountingCheck):
    """Check whether there is same amount of new lines."""

    string = "\n"
    check_id = "newline-count"
    name = gettext_lazy("Mismatching line breaks")
    description = gettext_lazy(
        "Number of new lines in translation does not match source."
    )


class ZeroWidthSpaceCheck(TargetCheck):
    """Check for zero width space char (<U+200B>)."""

    check_id = "zero-width-space"
    name = gettext_lazy("Zero-width space")
    description = gettext_lazy("Translation contains extra zero-width space character.")

    def check_single(self, source: str, target: str, unit: Unit):
        if unit.translation.language.is_base({"km"}):
            return False
        if "\u200b" in source:
            return False
        return "\u200b" in target

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        return [("regex", "\u200b", "", "gu")]


class MaxLengthCheck(TargetCheckParametrized):
    """Check for maximum length of translation."""

    check_id = "max-length"
    name = gettext_lazy("Maximum length of translation")
    description = gettext_lazy("Translation should not exceed given length.")
    default_disabled = True

    param_type = single_value_flag(int)

    def check_target_params(
        self, sources: list[str], targets: list[str], unit: Unit, value
    ):
        replace = self.get_replacement_function(unit)
        return any(len(replace(target)) > value for target in targets)


class MaxLinesCheck(TargetCheckParametrized):
    """
    Check for maximum number of lines in translation.

    This check flags translations that exceed a configured maximum number of
    lines. It is useful for translations targeting fixed-height UI elements
    such as displays, terminals, or constrained containers where a specific
    number of visible lines is required.

    The flag ``max-lines:<value>`` sets the upper bound. A translation with
    more than ``<value>`` lines (counted by newlines + 1) triggers a warning.

    Example flag: ``max-lines:3``
    """

    check_id = "max-lines"
    name = gettext_lazy("Maximum number of lines")
    description = gettext_lazy("Translation should not exceed given number of lines.")
    default_disabled = True

    param_type = single_value_flag(int)

    def check_target_params(
        self, sources: list[str], targets: list[str], unit: Unit, value
    ):
        replace = self.get_replacement_function(unit)
        return any(replace(target).count("\n") + 1 > value for target in targets)


class EndSemicolonCheck(TargetCheck):
    """Check for semicolon at end."""

    check_id = "end_semicolon"
    name = gettext_lazy("Mismatched semicolon")
    description = gettext_lazy(
        "Source and translation do not both end with a semicolon."
    )

    def check_single(self, source: str, target: str, unit: Unit):
        if unit.translation.language.is_base({"el"}) and source and source[-1] == "?":
            # Complement to question mark check
            return False
        return self.check_chars(
            strip_entities(source), strip_entities(target), -1, {";"}
        )


class KashidaCheck(TargetCheck):
    check_id = "kashida"
    name = gettext_lazy("Kashida letter used")
    description = gettext_lazy("The decorative kashida letters should not be used.")

    kashida_regex = (
        # Allow kashida after certain letters, only if sandwiched between actual letters
        r"(?<=\p{sc=Arab})(?<![\u0640\ufcf2\ufcf3\ufcf4\ufe71\ufe77\ufe79\ufe7b\ufe7d\ufe7f])"
        # List of kashida letters to check
        r"[\u0640\ufcf2\ufcf3\ufcf4\ufe71\ufe77\ufe79\ufe7b\ufe7d\ufe7f]+"
        r"(?=\p{sc=Arab})(?![\u0640\ufcf2\ufcf3\ufcf4\ufe71\ufe77\ufe79\ufe7b\ufe7d\ufe7f])"
    )
    kashida_re = regex.compile(kashida_regex)

    def check_single(self, source: str, target: str, unit: Unit):
        return self.kashida_re.search(target)

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        return [("regex", self.kashida_regex, "", "gu")]


class PunctuationSpacingCheck(TargetCheck):
    check_id = "punctuation_spacing"
    name = gettext_lazy("Punctuation spacing")
    description = gettext_lazy(
        "Missing non breakable space before double punctuation sign."
    )
    versions_changed = (
        (
            "5.10",
            "This check used to apply to Breton language as well, but it was limited to French only.",
        ),
    )

    def should_skip(self, unit: Unit) -> bool:
        if (
            not unit.translation.language.is_base({"fr"})
            or unit.translation.language.code == "fr_CA"
        ):
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit):
        # Remove XML/HTML entities first (indices must match the string we iterate over)
        target = strip_entities(target)
        # Skip punctuation inside placeables (e.g XLIFF equiv-text, RST).
        # Enable syntax highlighting so RST inline literals/strong/emph spans
        # are also excluded (previously handled by RST_MATCH).
        highlighted_ranges = [
            (highlight.start, highlight.end)
            for highlight in highlight_string(
                target, unit, highlight_syntax="rst-text" in unit.all_flags
            )
        ]
        if "md-text" in unit.all_flags:
            highlighted_ranges.extend(markdown_link_syntax_ranges(target))
        highlighted_ranges.sort()
        whitespace = {" ", "\u00a0", "\u202f", "\u2009"}
        total = len(target)
        punctuation = []
        range_index = 0
        current_range = highlighted_ranges[0] if highlighted_ranges else None
        for i, char in enumerate(target):
            # Advance to the next highlighted range if we've passed the current one.
            while current_range is not None and i >= current_range[1]:
                range_index += 1
                if range_index < len(highlighted_ranges):
                    current_range = highlighted_ranges[range_index]
                else:
                    current_range = None
                    break
            # Skip characters that fall inside a highlighted range.
            if current_range is not None and current_range[0] <= i < current_range[1]:
                continue
            if char in FRENCH_PUNCTUATION:
                if i == 0:
                    # Trigger if punctuation at beginning of the string
                    if char not in punctuation:
                        punctuation.append(char)
                    continue
                if (
                    i + 1 < total
                    and unicodedata.category(target[i + 1])
                    not in FRENCH_PUNCTUATION_SPACING
                ):
                    # Ignore when not followed by space or open/close bracket
                    continue
                prev_char = target[i - 1]
                if (
                    prev_char not in whitespace
                    and prev_char not in FRENCH_PUNCTUATION
                    and char not in punctuation
                ):
                    punctuation.append(char)
        return punctuation

    def get_description(self, check_obj):
        punctuation = []
        unit = check_obj.unit
        for target in unit.get_target_plurals():
            for char in self.check_single("", target, unit):
                if char not in punctuation:
                    punctuation.append(char)
        if not punctuation:
            return super().get_description(check_obj)
        return format_html(
            ngettext(
                "Missing non breakable space before punctuation mark {}.",
                "Missing non breakable space before punctuation marks {}.",
                len(punctuation),
            ),
            format_html_join_comma(
                "<code>{}</code>", ((char,) for char in punctuation)
            ),
        )

    def get_fixup(self, unit: Unit) -> Iterable[FixupType] | None:
        # If there are placeables in target, skip Fix button and rely on save-time
        # autofix which has position-aware checks.
        if highlight_string(
            unit.target, unit, highlight_syntax="rst-text" in unit.all_flags
        ):
            return None
        return [
            # First fix possibly wrong whitespace
            (
                "regex",
                FRENCH_PUNCTUATION_FIXUP_RE_NBSP,
                "\u00a0$2",
                "gu",
            ),
            (
                "regex",
                FRENCH_PUNCTUATION_FIXUP_RE_NNBSP,
                "\u202f$2",
                "gu",
            ),
            # Then add missing ones
            (
                "regex",
                FRENCH_PUNCTUATION_MISSING_RE_NBSP,
                "$1\u00a0$2",
                "gu",
            ),
            (
                "regex",
                FRENCH_PUNCTUATION_MISSING_RE_NNBSP,
                "$1\u202f$2",
                "gu",
            ),
        ]


class MultipleCapitalCheck(TargetCheck):
    """Multiple capitals check."""

    check_id = "multiple_capital"
    name = gettext_lazy("Multiple capitals")
    description = gettext_lazy(
        "Translation contains words with multiple misplaced capital letters."
    )
    version_added = "5.16"

    # matches sequences of 2+ uppercase letters in *any language*
    UPPERCASE_SEQ = regex.compile(r"\p{Lu}{2,}")

    def check_single(self, source: str, target: str, unit: Unit) -> bool:
        # Flag if any uppercase sequence is present in target and not present in the source
        return (
            self.UPPERCASE_SEQ.search(target) is not None
            and not self.UPPERCASE_SEQ.search(source) is not None
        )
