# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

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
# The fixups insert the space in front of a run, never inside it: a character
# of the run itself is excluded from the "what precedes" class, so "Quoi?!"
# becomes "Quoi\u202f?!" and not "Quoi\u202f?\u202f!".
FRENCH_PUNCTUATION_MISSING_RE_NBSP = (
    f"([^\xa0{''.join(sorted(FRENCH_PUNCTUATION))}])"
    f"([{''.join(FRENCH_PUNCTUATION_NBSP)}])"
)
FRENCH_PUNCTUATION_MISSING_RE_NNBSP = (
    f"([^\u202f{''.join(sorted(FRENCH_PUNCTUATION))}])"
    f"([{''.join(FRENCH_PUNCTUATION_NNBSP)}])"
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
    if language.is_base({"ja", "zh"}):
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
        "\u1038",  # bare MYANMAR SIGN VISARGA - see `_terminal_append_fixup`'s
        # `strip_prefix` docstring: `strip_prefix` only ever *consumes* a
        # trailing U+1038 that a real character precedes; a target that is
        # only U+1038 (or otherwise has nothing real before it) has no such
        # character, so `(?<=\S)` cannot anchor before it, and without this
        # entry the append would land *after* the existing U+1038 instead
        # of consuming it, doubling it. Listing it here makes that
        # degenerate case a conflict - refused, not mismutated.
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


# --- Explicit terminal-source policy (append/replace/remove) -----------
#
# `get_fixup()` above only ever appends a missing mark, and is shared with
# two paths outside the new bulk policy: the ordinary per-unit editor "Fix
# string" button (`weblate/static/editor/full.js`) and the pre-existing
# review-tier mass fix (`weblate/trans/fix_check.py`, the 2026-08-25 plan).
# Teaching it to also replace a conflicting mark or remove one the source
# does not have would put those two other paths in scope by accident -
# "the old editor append stays review; explicit-policy bulk must not appear
# during ordinary saving or a crafted review POST"
# (docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md, Task
# A). `terminal_source_edit()` below is therefore a *separate* function,
# called only by the new explicit-policy engine
# (`weblate/trans/fix_check.py`'s `collect_terminal_policy_candidates()`/
# `apply_terminal_policy()`); the append direction still delegates to
# `get_fixup()` itself, so every language branch is defined exactly once.

# Marks eligible for *replacement*: every conflict-guard member except a
# deliberate ellipsis and the two single-codepoint interrobang forms, which
# stay their own, separately reviewed checks - "not included in this
# policy; the existing interrobang review stays separate" (plan, Task A).
TERMINAL_REPLACEABLE_CHARS = TERMINAL_MARK_CHARS - {"…", "⁈", "⁉"}

# check_id -> the family (source's own terminal mark) that check owns.
# `end_interrobang` is deliberately absent - append-only via `get_fixup()`,
# untouched by this policy.
_TERMINAL_SOURCE_BASE: dict[str, str] = {
    "end_stop": ".",
    "end_colon": ":",
    "end_question": "?",
    "end_exclamation": "!",
}
# Only these two families are ever *removed* outright when the source has
# no mark at all. A colon or question mark the translation adds on its own
# is frequently a deliberate choice - direct speech, a rhetorical question
# the source drops by convention - measured and rejected for the narrower
# ASCII-only autofix this generalizes
# (`weblate_customization/src/weblate_customization/autofixes.py:39-43`,
# `RemoveAddedFinalStop`).
_TERMINAL_REMOVABLE: dict[str, frozenset[str]] = {
    ".": frozenset({".", "。"}),
    "!": frozenset({"!", "！"}),
}

_TERMINAL_SPACE_CLASS = " \u00a0\u202f\u2009"
# A closing quote, bracket or tag that might hide a source mark behind it.
_PROTECTED_TAIL_CHARS = frozenset("»\"'\u2019)]}>")
# The final word the abbreviation guard below measures. The letter classes
# must cover the whole script, not its ASCII core: with `[A-Za-z]` only,
# "pièce" measures as "ce" and "Propriétés" as "s", so every accented word
# looks like a 1-3 letter abbreviation and a French, Vietnamese or Polish
# translation is refused wholesale (measured on CoL4/data/fr: 189 of 386
# refusals). Latin-1 Supplement through Latin Extended-B and Latin
# Extended Additional cover the accented forms; the two Latin-1 maths
# symbols at U+00D7/U+00F7 are excluded on purpose.
_LATIN_LETTERS = "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff"
_CYRILLIC_LETTERS = "\u0400-\u04ff\u0500-\u052f"
_LATIN_WORD_RE = re.compile(rf"[{_LATIN_LETTERS}]+(?:'[{_LATIN_LETTERS}]+)*$")
_CYRILLIC_WORD_RE = re.compile(rf"[{_CYRILLIC_LETTERS}]+(?:'[{_CYRILLIC_LETTERS}]+)*$")
_CYRILLIC_RE = re.compile(rf"[{_CYRILLIC_LETTERS}]")
_NUMERIC_TAIL_RE = re.compile(r"[0-9]$")
_URL_LIKE_TAIL_RE = re.compile(
    r"(?:^|[\s(\[{])"
    r"(?:\w[\w+.-]*://\S+"  # scheme://...
    r"|www\.\S+"
    r"|[\w.+-]+@[\w-]+\.\w"  # email
    r"|/[^\s]*\w"  # /path/like/this
    r"|\d+\.\d+"  # 1.2, v1.2
    r"|[\w-]+\.[A-Za-z]{2,4})$",  # host.tld, file.ext
    re.IGNORECASE,
)


def _source_tail_is_protected(source: str) -> bool:
    """
    Whether source's raw ending hides a mark the new policy refuses to look through.

    Unlike `RemoveAddedFinalStop`, which unwraps a source closing quote or
    tag to still find the mark behind it (see its own docstring), the new
    explicit policy simply excludes such rows - "wrapped tails are shown as
    exclusions, not promised in the implementation" (plan, Task A). Treating
    a hidden mark as "no mark" would misclassify a replace as a remove.
    """
    if not source:
        return False
    return source[-1].isspace() or source[-1] in _PROTECTED_TAIL_CHARS


def _plain_source_mark(source: str, unit: Unit) -> str | None:
    """
    Return the terminal mark family the source unambiguously ends with.

    For `terminal_source_edit()` only - never for `get_fixup()`, whose
    broader per-check predicates (`_stop_source_has_mark` and friends) stay
    exactly as they are.

    Deliberately narrower than those: languages whose branches accept
    overlapping character sets across families - Armenian, the Devanagari
    group, Santali, Burmese - return `None` here. "On locale-predicate
    ambiguity, refuse, not fall back to ASCII" (plan, Task A). A source
    ending ``;`` also returns `None` unconditionally - out of scope for this
    policy even where the CJK branches of `_stop_source_has_mark`/
    `_colon_source_has_mark` treat it as colon-equivalent for `get_fixup()`.
    A CJK source ending ``:`` always resolves to the colon family here,
    mirroring `EndColonCheck`'s own CJK branch: "CJK source `:` does not
    turn into a stop just because `EndStopCheck` and `EndColonCheck` accept
    overlapping sets" (plan, Task A).
    """
    if not source:
        return None
    if source[-1] == ";":
        return None
    language = unit.translation.language
    if language.is_base({"hy", "hi", "bn", "or", "sat", "my"}):
        return None
    last = source[-1]
    if language.is_cjk() and last == ":":
        return ":"
    if last in {".", "。", "।", "۔", "։", "·", "෴", "។", "።"}:
        return "."
    if last in {":", "：", "៖"}:
        return ":"
    if last in {"?", "՞", "؟", "⸮", "？", "፧", "꘏", "⳺"}:
        return "?"
    if last in {"!", "！", "՜", "᥄", "႟", "߹"}:
        return "!"
    return None


def _short_word_len(pattern: re.Pattern[str], stem: str) -> int | None:
    """Length (apostrophes not counted) of the word `pattern` finds at the end of `stem`, or `None`."""
    match = pattern.search(stem)
    if match is None:
        return None
    return len(match.group(0).replace("'", ""))


def _terminal_edit_stem_is_protected(stem: str, source: str) -> bool:
    """
    Whether what a replace/remove would leave behind is unsafe to store.

    In order: blanking the string entirely; a decimal, version, URL,
    e-mail or path-like tail, where the touched mark is not sentence
    punctuation at all; and a possible abbreviation - a final word of 1-3
    Latin letters, accented forms included, so "pièce" counts five and not
    two (apostrophes, as in "can't", do not count against the limit and do
    not break the word), or, when source itself is Cyrillic, 1-3 Cyrillic
    letters. "A conservative filter, not a linguistic detector: common
    short words may also be excluded" (plan, Task A) - real coverage loss
    is an accepted cost of never corrupting "etc." or "v1.2".
    """
    if not stem:
        return True
    if _NUMERIC_TAIL_RE.search(stem) or _URL_LIKE_TAIL_RE.search(stem):
        return True
    latin_len = _short_word_len(_LATIN_WORD_RE, stem)
    if latin_len is not None and latin_len <= 3:
        return True
    if _CYRILLIC_RE.search(source):
        cyrillic_len = _short_word_len(_CYRILLIC_WORD_RE, stem)
        if cyrillic_len is not None and cyrillic_len <= 3:
            return True
    return False


@dataclass(frozen=True, slots=True)
class TerminalEdit:
    """One `terminal_source_edit()` proposal: its operation and fixup."""

    operation: Literal["append", "replace", "remove"]
    fixup: FixupType


def terminal_source_edit(check_obj: TargetCheck, unit: Unit) -> TerminalEdit | None:
    """
    Compute the explicit terminal-source policy edit that would clear `check_obj`.

    Returns `None` if this check offers none under the new policy - the
    caller then leaves the row for manual review, exactly like an
    unresolved `get_fixup()` result does today. See the module comment
    above `TERMINAL_REPLACEABLE_CHARS` for why this is not `get_fixup()`
    itself.

    Only `end_stop`, `end_colon`, `end_question` and `end_exclamation`
    participate (`_TERMINAL_SOURCE_BASE`); `end_interrobang` stays its own,
    separately reviewed check, append-only via `get_fixup()`, exactly as
    before.
    """
    base = _TERMINAL_SOURCE_BASE.get(check_obj.check_id)
    if base is None:
        return None
    source = unit.source_string
    target = unit.target
    if not target or _source_tail_is_protected(source) or source.endswith(";"):
        # A source ending ";" is out of scope entirely, not merely
        # unresolved to a family: without this, `_plain_source_mark`
        # returning `None` for it would make the *remove* branch below
        # misread "ends `;`, colon-equivalent for CJK `get_fixup()`" as
        # "has no mark at all" and offer to strip a target mark the source
        # legitimately earned.
        return None
    stripped_target = target.rstrip()
    if not stripped_target:
        return None
    last = stripped_target[-1]
    single_mark = last in TERMINAL_MARK_CHARS and (
        len(stripped_target) < 2 or stripped_target[-2] not in TERMINAL_MARK_CHARS
    )
    source_family = _plain_source_mark(source, unit)

    if source_family == base:
        if not single_mark:
            # No conflicting mark in the way: identical to what
            # `get_fixup()` already computes for this exact situation, so
            # reuse it rather than restating every language branch here.
            fixup = check_obj.get_fixup(unit)
            if not fixup:
                return None
            return TerminalEdit("append", next(iter(fixup)))
        if last not in TERMINAL_REPLACEABLE_CHARS:
            return None  # Ellipsis/interrobang tail: not this policy.
        stem = stripped_target[:-1].rstrip(_TERMINAL_SPACE_CLASS)
        if _terminal_edit_stem_is_protected(stem, source):
            return None
        mark = _terminal_mark(unit, base)
        pattern = rf"[{_TERMINAL_SPACE_CLASS}]*{re.escape(last)}\s*$"
        return TerminalEdit("replace", ("regex", pattern, mark, "u"))

    if (
        source_family is None
        and single_mark
        and last in _TERMINAL_REMOVABLE.get(base, frozenset())
    ):
        stem = stripped_target[:-1].rstrip(_TERMINAL_SPACE_CLASS)
        if _terminal_edit_stem_is_protected(stem, source):
            return None
        pattern = rf"[{_TERMINAL_SPACE_CLASS}]*{re.escape(last)}\s*$"
        return TerminalEdit("remove", ("regex", pattern, "", "u"))

    return None


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
                # A run of double punctuation ("?!", "!!") takes the space in
                # front of the whole run, so the run is evaluated as one unit:
                # spacing is required before its first character, and what
                # follows is looked up after its last one. Evaluating each
                # character on its own silently accepted "Quoi?!", because the
                # "?" is not followed by a space and the "!" is preceded by
                # punctuation.
                if i > 0 and target[i - 1] in FRENCH_PUNCTUATION:
                    continue
                if i == 0:
                    # Trigger if punctuation at beginning of the string
                    if char not in punctuation:
                        punctuation.append(char)
                    continue
                end = i
                while end + 1 < total and target[end + 1] in FRENCH_PUNCTUATION:
                    end += 1
                if (
                    end + 1 < total
                    and unicodedata.category(target[end + 1])
                    not in FRENCH_PUNCTUATION_SPACING
                ):
                    # Ignore when not followed by space or open/close bracket
                    continue
                prev_char = target[i - 1]
                if prev_char not in whitespace and char not in punctuation:
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
