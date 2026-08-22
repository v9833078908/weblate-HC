# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.autotranslate import AutoTranslate, BatchAutoTranslate
from weblate.trans.judge import JudgeResult
from weblate.trans.judge_loop import build_request
from weblate.trans.models.judge import (
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
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
                request = build_request(u)
                out[u.id] = JudgeVerdict.objects.create(
                    unit=u,
                    max_severity=severity,
                    model_verdict=verdict_kind,
                    unparsed=(verdict_kind == JudgeVerdict.Verdict.UNPARSED),
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        glossary_terms=request.glossary_terms,
                    ),
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

    def test_flag_lands_on_a_state_that_does_not_ship(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.FLAG, severity="major")
        self.assertIn(self.get_unit().state, FUZZY_STATES)

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

    def test_batch_cap_is_shared_across_translations(self) -> None:
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        unit = translations[0].unit_set.first()
        assert unit is not None
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with (
            override_settings(JUDGE_MAX_UNITS_PER_RUN=1),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run,
        ):
            auto.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        self.assertEqual(sum(len(call.args[0]) for call in run.call_args_list), 1)
        self.assertTrue(
            any("cap was reached" in warning for warning in auto.warnings),
            auto.warnings,
        )

    def test_judge_refreshes_units_after_pretranslation(self) -> None:
        unit = self.get_unit()
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
        )

        def pretranslate(*args, **kwargs):
            current = type(unit).objects.get(pk=unit.pk)
            current.translate(self.user, ["machine target"], STATE_FUZZY)

        with (
            mock.patch.object(auto, "process_mt", side_effect=pretranslate),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run,
        ):
            auto.process_judge(engines=[], threshold=80)
        judged_units = run.call_args.args[0]
        self.assertEqual(judged_units[0].target.strip(), "machine target")

    def test_major_repair_passes_through_the_operator_path(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["existing translation"], STATE_TRANSLATED)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            overwrite_existing=True,
            unit_ids=[unit.id],
        )
        major = JudgeResult("major", "flag", [], "")
        passed = JudgeResult("none", "pass", [], "")
        client = mock.Mock(side_effect=[[major], [major], [passed], [passed]])
        with (
            mock.patch.object(auto, "process_mt"),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_target",
                return_value=["repaired translation"],
            ),
        ):
            auto.process_judge(engines=[], threshold=80)
        stored = self.get_unit()
        self.assertEqual(stored.target.strip(), "repaired translation")
        self.assertEqual(stored.state, STATE_TRANSLATED)
        self.assertEqual(
            stored.judge_verdicts.latest("pk").verdict, JudgeVerdict.Verdict.PASS
        )
        self.assertEqual(client.call_count, 4)

    def test_final_state_write_skips_a_target_changed_after_judging(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["original target"], STATE_TRANSLATED)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
        )

        def fake_batch(units, *, writable_ids, user):
            current = units[0]
            request = build_request(current)
            verdict = JudgeVerdict.objects.create(
                unit=current,
                max_severity="none",
                model_verdict=JudgeVerdict.Verdict.PASS,
                judge_model="vendor-a/model",
                seat=1,
                target_hash=compute_target_hash(request.target_plurals),
                context_hash=compute_context_hash(
                    source=request.source,
                    note=request.note,
                    glossary_terms=request.glossary_terms,
                ),
            )
            current = type(current).objects.get(pk=current.pk)
            current.translate(self.user, ["human changed target"], STATE_TRANSLATED)
            return {current.pk: verdict}

        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.process_judge(engines=[], threshold=80)
        self.assertEqual(self.get_unit().target.strip(), "human changed target")
