# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.checks.judge import JUDGE_CHECKS, JudgeFlagCheck, JudgeRejectCheck
from weblate.checks.models import CHECKS, Check
from weblate.trans.models.judge import (
    JudgeVerdict,
    active_verdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.tests.test_views import ViewTestCase


class JudgeCheckTest(ViewTestCase):
    def make(self, unit, max_severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, **kwargs
        )

    def test_checks_are_registered_and_not_enforceable_by_default(self) -> None:
        self.assertIn("judge-flag", CHECKS)
        self.assertIn("judge-reject", CHECKS)
        self.assertEqual(JUDGE_CHECKS, frozenset({"judge-flag", "judge-reject"}))
        for check in (JudgeFlagCheck(), JudgeRejectCheck()):
            self.assertFalse(check.default_disabled)

    def test_reject_verdict_makes_run_checks_create_the_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical")  # verdict property -> reject
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)
        self.assertNotIn("judge-flag", unit.all_checks_names)

    def test_a_pass_verdict_leaves_no_judge_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "none")
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertEqual(unit.all_checks_names & JUDGE_CHECKS, set())

    def test_the_projected_row_survives_a_second_run_checks(self) -> None:
        # THE regression this whole design exists for: run_checks must
        # not delete the judge row it did not compute here (review A1/D1).
        unit = self.get_unit()
        self.make(unit, "critical")
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)
        # A translator edit, a component recheck, `updatechecks` — all of
        # these call run_checks again. The row must still be there.
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)

    def test_a_new_verdict_replaces_the_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical", run_id=uuid.uuid4())
        unit.run_checks()
        self.make(unit, "major", run_id=uuid.uuid4())
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertEqual(unit.all_checks_names & JUDGE_CHECKS, {"judge-flag"})

    def test_a_context_drift_keeps_the_projected_row(self) -> None:
        # The card shows a context-drifted verdict as "relates to a
        # previous version" (active_verdict). The row must follow that
        # reader: a filter and a card may never disagree about a unit.
        unit = self.get_unit()
        self.make(
            unit,
            "critical",
            context_hash=compute_context_hash(
                source=unit.source,
                note="the note as it was when the judge ran",
                glossary_terms=[],
            ),
        )
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)
        self.assertIsNotNone(active_verdict(unit))

    def test_description_carries_escaped_evidence(self) -> None:
        unit = self.get_unit()
        self.make(
            unit,
            "critical",
            errors=[
                {
                    "span": "x",
                    "category": "terminology",
                    "severity": "critical",
                    "description": "the Gates are called DOORS here",
                }
            ],
        )
        Check.objects.filter(unit=unit).delete()
        unit.run_checks()
        unit.clear_checks_cache()
        row = Check.objects.get(unit=unit, name="judge-reject")
        self.assertIn("DOORS", row.get_description())
