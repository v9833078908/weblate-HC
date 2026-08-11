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
    from weblate.trans.models import Unit

HUGGING_SEPARATOR = re.compile(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*")
SPACING_CHARACTERS = re.compile(r"[ \u00a0\u202f\u2009]")


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
    Drop a final stop the source does not have.

    An LLM adds one to roughly a third of the strings of a game corpus, where
    captions and button labels are unpunctuated on purpose. The check itself
    decides, so every language branch it implements - the short-source
    shortcut, the ellipsis rule, CJK, Armenian, Devanagari, Santali, Burmese -
    is honoured without being restated here. The opposite direction, a stop
    lost in translation, is not repairable and stays a check.
    """

    fix_id = "removed-final-stop"
    name = gettext_lazy("Added final stop")

    @staticmethod
    def get_related_checks():
        return [EndStopCheck()]

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        if not target.endswith(".") or target.endswith(".."):
            return target, False
        check = EndStopCheck()
        if check.should_skip(unit) or not check.check_single(source, target, unit):
            return target, False
        stripped = target[:-1]
        if check.check_single(source, stripped, unit):
            # Removing the stop does not settle the disagreement, so the
            # mismatch is about something else.
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
