# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import translation

from weblate.checks.judge import (
    JUDGE_CHECKS,
    JudgeFlagCheck,
    JudgeNoteCheck,
    JudgeRejectCheck,
)
from weblate.checks.models import CHECKS, Check
from weblate.trans.filter import FILTERS
from weblate.trans.models.judge import (
    JudgeVerdict,
    active_verdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_TRANSLATED


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
        self.assertIn("judge-note", CHECKS)
        self.assertEqual(
            JUDGE_CHECKS, frozenset({"judge-flag", "judge-reject", "judge-note"})
        )
        for check in (JudgeFlagCheck(), JudgeRejectCheck(), JudgeNoteCheck()):
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

    def test_minor_verdict_fires_judge_note_only(self) -> None:
        unit = self.get_unit()
        self.make(unit, "minor")
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertEqual(unit.all_checks_names & JUDGE_CHECKS, {"judge-note"})

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


class JudgeCheckDismissalTest(ViewTestCase):
    def make(self, unit, max_severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        JudgeVerdict.objects.create(unit=unit, max_severity=max_severity, **kwargs)
        unit.run_checks()
        unit.clear_checks_cache()

    def test_unit_check_denies_a_major_verdict(self) -> None:
        unit = self.get_unit()
        self.make(unit, "major")
        check = Check.objects.get(unit=unit, name="judge-flag")
        self.assertFalse(self.user.has_perm("unit.check", check))

    def test_unit_check_denies_a_critical_verdict(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical")
        check = Check.objects.get(unit=unit, name="judge-reject")
        self.assertFalse(self.user.has_perm("unit.check", check))

    def test_unit_check_denies_a_minor_verdict(self) -> None:
        unit = self.get_unit()
        self.make(unit, "minor")
        check = Check.objects.get(unit=unit, name="judge-note")
        self.assertFalse(self.user.has_perm("unit.check", check))

    def test_dismiss_post_is_refused_for_a_judge_check(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical")
        check = Check.objects.get(unit=unit, name="judge-reject")
        response = self.client.post(
            reverse("js-ignore-check", kwargs={"check_id": check.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_non_judge_check_remains_dismissible(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Hello, world!\n"], STATE_TRANSLATED)  # "same"
        unit.run_checks()
        unit.clear_checks_cache()
        check = Check.objects.get(unit=unit, name="same")
        self.assertTrue(self.user.has_perm("unit.check", check))
        response = self.client.post(
            reverse("js-ignore-check", kwargs={"check_id": check.pk})
        )
        self.assertEqual(response.status_code, 200)


class JudgeSeverityVocabularyLocalizationTest(SimpleTestCase):
    def test_check_names_and_descriptions_render_in_russian(self) -> None:
        with translation.override("ru"):
            self.assertEqual(str(JudgeFlagCheck.name), "Судья — серьёзно")
            self.assertEqual(str(JudgeRejectCheck.name), "Судья — критично")
            self.assertEqual(str(JudgeNoteCheck.name), "Судья — незначительно")
            self.assertIn("серьёзной проблеме", str(JudgeFlagCheck.description))
            self.assertIn("критической проблеме", str(JudgeRejectCheck.description))
            self.assertIn("незначительной проблеме", str(JudgeNoteCheck.description))

    def test_filter_preset_labels_render_in_russian(self) -> None:
        with translation.override("ru"):
            self.assertEqual(
                str(FILTERS.get_filter_name("judge-advisory")),
                "Судья — серьёзно (публикуется)",
            )
            self.assertEqual(
                str(FILTERS.get_filter_name("judge-held")),
                "Судья — критично (удерживается)",
            )
            self.assertEqual(
                str(FILTERS.get_filter_name("judge-minor")),
                "Судья — незначительно (ничего не блокирует)",
            )
            self.assertEqual(
                str(FILTERS.get_filter_name("judge-pass")),
                "Судья — ничего не блокирует",
            )
        self.assertEqual(FILTERS.get_filter_query("judge-advisory"), "judge:flag")
        self.assertEqual(FILTERS.get_filter_query("judge-held"), "judge:reject")
        self.assertEqual(FILTERS.get_filter_query("judge-minor"), "judge:minor")
        self.assertEqual(FILTERS.get_filter_query("judge-pass"), "judge:pass")
