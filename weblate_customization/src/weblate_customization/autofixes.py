# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic repair of Hero Craft engine line separators."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy

from weblate.trans.autofixes.base import AutoFix
from weblate_customization.checks import (
    SEPARATOR_SPACE,
    GameLineBreakCheck,
    separator_is_tight,
)

if TYPE_CHECKING:
    from weblate.trans.models import Unit

HUGGING_SEPARATOR = re.compile(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*")


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
