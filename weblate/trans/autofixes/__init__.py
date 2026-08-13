# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Import all the autofixes defined in settings.

Note, unlike checks, using a sortable data object so fixes are applied in desired order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weblate.utils.classloader import ClassLoader

from .base import AutoFix

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django_stubs_ext import StrOrPromise

    from weblate.trans.models.unit import Unit


class AutofixLoader(ClassLoader[AutoFix]):
    def __init__(self) -> None:
        super().__init__("AUTOFIX_LIST", base_class=AutoFix)

    def get_ignore_strings(self) -> Iterator[str]:
        for fix in self.values():
            for check in fix.get_related_checks():
                yield check.ignore_string


AUTOFIXES = AutofixLoader()


def _run_autofixes(target: list[str], unit: Unit) -> tuple[list[str], list[AutoFix]]:
    """Apply each autofix in order, collecting the ones that changed the target."""
    applied: list[AutoFix] = []
    for fix in AUTOFIXES.values():
        target, fixed = fix.fix_target(target, unit)
        if fixed:
            applied.append(fix)
    return target, applied


def fix_target(target: list[str], unit: Unit) -> tuple[list[str], list[StrOrPromise]]:
    """Apply each autofix to the target translation."""
    if target == []:
        return target, []
    target, applied = _run_autofixes(target, unit)
    return target, [fix.name for fix in applied]


def apply_autofixes(target: list[str], unit: Unit) -> tuple[list[str], list[str]]:
    """Apply each autofix, reporting stable identifiers instead of labels."""
    if target == []:
        return target, []
    target, applied = _run_autofixes(target, unit)
    return target, [fix.get_identifier() for fix in applied]


def autofix_fingerprint() -> tuple[str, ...]:
    """Return the ordered identifiers of the active autofixes."""
    return tuple(AUTOFIXES.keys())
