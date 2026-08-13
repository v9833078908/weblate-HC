# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic repair of defects a prompt rule cannot hold."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy

from weblate.checks.chars import (
    FRENCH_PUNCTUATION_MISSING_RE_NBSP,
    FRENCH_PUNCTUATION_MISSING_RE_NNBSP,
    EndColonCheck,
    EndExclamationCheck,
    EndQuestionCheck,
    EndStopCheck,
    PunctuationSpacingCheck,
)
from weblate.checks.utils import highlight_string
from weblate.trans.autofixes.base import AutoFix
from weblate_customization.checks import (
    SEPARATOR_SPACE,
    GameLineBreakCheck,
    separator_is_tight,
)

if TYPE_CHECKING:
    from weblate.checks.base import TargetCheck
    from weblate.trans.models import Unit

HUGGING_SEPARATOR = re.compile(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*")
SPACING_CHARACTERS = re.compile(r"[ \u00a0\u202f\u2009]")
TRAILING_SPACING = re.compile(r"[ \u00a0\u202f\u2009]+$")

# ASCII only. Colons are intentionally excluded: the production measurement
# found direct-speech markers that look indistinguishable from added punctuation.
TERMINAL_MARKS = ".!?"
# Closing quotes stripped from the SOURCE before comparing, so a source mark
# hiding behind one is still seen (prod unit 180448, `с криком "Еретик!"`).
# The target is never unwrapped - see the class docstring for the measurement
# that rejected it.
CLOSING_QUOTES = '»"”'
TRAILING_QUOTES = re.compile(rf"[{re.escape(CLOSING_QUOTES)}]+$")
# One instance each: checks are stateless, and the registry keeps singletons too.
TERMINAL_CHECKS: tuple[TargetCheck, ...] = (
    EndStopCheck(),
    EndColonCheck(),
    EndQuestionCheck(),
    EndExclamationCheck(),
)


class LineSeparatorSpacing(AutoFix):
    """`$` is a line break: whitespace beside it renders as a stray indent."""

    fix_id = "line-separator-spacing"
    name = gettext_lazy("Line separator spacing")

    @staticmethod
    def get_related_checks():
        return [GameLineBreakCheck()]

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        # One switch for the pair: the check's own ignore flag.
        if (
            not separator_is_tight(source)
            or GameLineBreakCheck().ignore_string in unit.all_flags
        ):
            return target, False
        new_target = HUGGING_SEPARATOR.sub("$", target)
        return new_target, new_target != target


class RemoveAddedFinalStop(AutoFix):
    """
    Drop terminal punctuation the source does not have.

    An LLM adds one to roughly a third of the strings of a game corpus, where
    captions and button labels are unpunctuated on purpose. The terminal check
    set decides, so every language branch it implements - the short-source
    shortcut, the ellipsis rule, interrobangs, CJK, Armenian, Devanagari,
    Santali, Burmese - is honoured without being restated here.

    Two rules keep this narrow. The removal has to shrink the set of failing
    terminal checks strictly: a removal that settles nothing, or that trades
    one mismatch for another, is not a repair. And only the SOURCE is
    unwrapped from closing quotes, never the target. Unwrapping the source is
    what keeps a source mark hidden behind a quote (``с криком "Еретик!"``)
    from reading as punctuation the model invented. Unwrapping the target was
    measured on prod and rejected: it reached 17 more units and degraded 13 of
    them, all ``en_US`` strings placing the full stop inside the quotes per US
    convention (``the inscription "armory."``). The opposite direction, a mark
    lost in translation, is not repairable and stays a check.
    """

    fix_id = "removed-final-stop"
    name = gettext_lazy("Added final punctuation")

    @staticmethod
    def get_related_checks():
        return list(TERMINAL_CHECKS)

    @staticmethod
    def _failing(source: str, target: str, unit: Unit) -> frozenset[str]:
        return frozenset(
            check.check_id
            for check in TERMINAL_CHECKS
            if not check.should_skip(unit) and check.check_single(source, target, unit)
        )

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        if not target or target[-1] not in TERMINAL_MARKS:
            return target, False
        if len(target) > 1 and target[-2] == target[-1]:
            # Dropping one dot of an unfinished ellipsis, or one of a doubled
            # mark, would only make it odder.
            return target, False
        stripped = TRAILING_SPACING.sub("", target[:-1])
        if not stripped:
            # The mark is the whole translation; blanking it is not a repair.
            return target, False
        source_body = TRAILING_QUOTES.sub("", source)
        before = self._failing(source_body, target, unit)
        if not before:
            return target, False
        if not self._failing(source_body, stripped, unit) < before:
            return target, False
        return stripped, True


class AddFrenchPunctuationSpacing(AutoFix):
    """
    Insert the narrow or non-breaking space French double punctuation needs.

    The built-in ``PunctuationSpacing`` only repairs a space of the wrong kind
    and deliberately never adds a missing one, to stay clear of URLs and
    Markdown. This one adds it, but only where the check already reports a
    defect, only outside highlighted ranges, and only if the result stops
    failing the check and differs from the original in whitespace alone.
    """

    fix_id = "french-punctuation-spacing"
    name = gettext_lazy("Missing French punctuation spacing")

    @staticmethod
    def get_related_checks():
        return [PunctuationSpacingCheck()]

    @staticmethod
    def _without_spacing(text: str) -> str:
        return SPACING_CHARACTERS.sub("", text)

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        check = PunctuationSpacingCheck()
        if check.should_skip(unit) or not check.check_single(source, target, unit):
            return target, False

        highlight_ranges = sorted(
            (highlight.start, highlight.end)
            for highlight in highlight_string(
                target, unit, highlight_syntax="rst-text" in unit.all_flags
            )
        )

        def replacer(space: str):
            def replace(match: re.Match) -> str:
                position = match.start(2)
                if any(start <= position < end for start, end in highlight_ranges):
                    return match.group(0)
                return f"{match.group(1)}{space}{match.group(2)}"

            return replace

        new_target = re.sub(
            FRENCH_PUNCTUATION_MISSING_RE_NBSP, replacer("\u00a0"), target
        )
        new_target = re.sub(
            FRENCH_PUNCTUATION_MISSING_RE_NNBSP, replacer("\u202f"), new_target
        )
        if (
            new_target == target
            or self._without_spacing(new_target) != self._without_spacing(target)
            or check.check_single(source, new_target, unit)
        ):
            return target, False
        return new_target, True
