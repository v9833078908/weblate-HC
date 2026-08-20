# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.autotranslate import AutoTranslate
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import FUZZY_STATES, STATE_FUZZY, STATE_TRANSLATED


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_UNITS_PER_RUN=2000,
    JUDGE_MAY_APPROVE=False,
)
class JudgeAutoTranslateTest(ViewTestCase):
    def perform(self, verdict_kind, *, severity="none", q="", overwrite=False):
        def fake_batch(units, *, writable_ids, user):
            out = {}
            for u in units:
                out[u.id] = JudgeVerdict.objects.create(
                    unit=u,
                    max_severity=severity,
                    model_verdict=verdict_kind,
                    unparsed=(verdict_kind == JudgeVerdict.Verdict.UNPARSED),
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(u.get_target_plurals()),
                    context_hash="c",
                )
            return out

        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=q,
            mode="judge",
            overwrite_existing=overwrite,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.process_judge(engines=[], threshold=80)
        return auto

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.REJECT, severity="critical")
        self.assertIn(self.get_unit().state, FUZZY_STATES)

    def test_flag_ships_but_is_not_approved(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.FLAG, severity="major")
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

    def test_unparsed_leaves_the_state_untouched(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        before = self.get_unit().state
        self.perform(JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(self.get_unit().state, before)

    def test_existing_translation_is_judged_not_rewritten(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        with mock.patch.object(AutoTranslate, "process_mt") as mt:
            self.perform(JudgeVerdict.Verdict.PASS, q=f"context:{unit.context}")
        mt.assert_not_called()

    def test_fresh_translation_starts_at_needs_editing(self) -> None:
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="state:empty",
            mode="judge",
        )
        self.assertEqual(auto.fresh_translation_state, STATE_FUZZY)

    def test_a_run_over_the_cap_is_refused(self) -> None:
        with override_settings(JUDGE_MAX_UNITS_PER_RUN=0):
            auto = self.perform(JudgeVerdict.Verdict.PASS)
        self.assertIsNotNone(auto.failure_message)

    def test_unparsed_is_counted_in_the_warnings(self) -> None:
        auto = self.perform(JudgeVerdict.Verdict.UNPARSED)
        self.assertTrue(
            any("unjudged" in warning for warning in auto.warnings),
            auto.warnings,
        )
