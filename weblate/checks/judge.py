# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Judge checks: a Check row derived from the active JudgeVerdict.

The check never computes anything itself — it reads the active verdict
(weblate.trans.models.judge.active_verdict), the same reader the unit
card uses, so a filter and a card can never disagree about a unit. That
keeps Unit.run_checks the single writer of judge-* rows, so they cannot
diverge from the verdict and staleness is free (a verdict describing
older text yields no active round, so run_checks removes the row). A
verdict whose glossary or note context drifted stays projected and is
marked on the card; hiding it here would strand the card's "relates to a
previous version" state behind an empty filter. The trans-model import is
local: this module is loaded at app start via CHECK_LIST, before models
are ready.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy

from weblate.checks.base import TargetCheck

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from weblate.checks.models import Check
    from weblate.trans.models.unit import Unit


class BaseJudgeCheck(TargetCheck):
    """A verdict projected from JudgeVerdict, never computed here."""

    default_disabled = False
    # Glossary components run only CHECKS.glossary (Unit.run_checks), so a
    # judged glossary would otherwise never get its verdict projected into
    # Check rows and the check:judge-* filters would stay empty there.
    glossary = True
    # A verdict exists for any judged text, an empty target included:
    # this is a verdict reader, not a linguistic check.
    ignore_untranslated = False
    # The severity this subclass renders as a failing check.
    judge_severity: str = ""

    def _active_verdict(self, unit: Unit):
        # Not cached in unit.check_cache: that dict survives a
        # re-judge (it is reset only on __init__/invalidate_checks_cache,
        # and writing verdicts does not touch the unit), so a cached
        # verdict would keep the projected row stale. Two reads per
        # run_checks pass instead of one is the price of correctness.
        from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
            active_verdict,
        )

        return active_verdict(unit)

    def check_target_unit(self, sources, targets, unit) -> bool:
        verdict = self._active_verdict(unit)
        return verdict is not None and verdict.max_severity == self.judge_severity

    def check_target_with_flags(self, sources, targets, unit, all_flags) -> bool:
        # Deliberately not cached per check id: a newer verdict must
        # replace the projected row on the very next run_checks of the
        # same instance. translate() invalidates the cache, but a
        # re-judge writes verdicts without touching the unit; _active_verdict
        # reads fresh every call for the same reason.
        if self.should_skip(unit) or self.is_ignored(all_flags):
            return False
        return self.check_target_unit(sources, targets, unit)

    def check_single(self, source: str, target: str, unit) -> bool:
        # Never used: check_target_unit is overridden. Present so the
        # abstract base contract is satisfied.
        return False

    def get_description(self, check_obj: Check) -> StrOrPromise:
        """
        Render the active verdict's errors for the repair prompt.

        weblate/machinery/llm.py calls get_description() when it builds
        failing_checks for the translator.
        """
        from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
            describe_latest_verdict,
        )

        return describe_latest_verdict(check_obj.unit) or self.description


class JudgeFlagCheck(BaseJudgeCheck):
    check_id = "judge-flag"
    judge_severity = "major"
    name = gettext_lazy("Judge - major")
    description = gettext_lazy(
        "An LLM judge reported a major problem. The string ships with this "
        "evidence attached."
    )


class JudgeRejectCheck(BaseJudgeCheck):
    check_id = "judge-reject"
    judge_severity = "critical"
    name = gettext_lazy("Judge - critical")
    description = gettext_lazy(
        "An LLM judge reported a critical problem. The string is held "
        "automatically until an authorized override."
    )


class JudgeNoteCheck(BaseJudgeCheck):
    check_id = "judge-note"
    judge_severity = "minor"
    name = gettext_lazy("Judge - minor")
    description = gettext_lazy(
        "An LLM judge reported a minor problem. The string ships; no "
        "action is expected."
    )


JUDGE_CHECKS = frozenset(
    {JudgeFlagCheck.check_id, JudgeRejectCheck.check_id, JudgeNoteCheck.check_id}
)
