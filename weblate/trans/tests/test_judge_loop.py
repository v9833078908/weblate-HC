# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.judge import JudgeResult
from weblate.trans.judge_loop import run_judge_batch
from weblate.trans.models.judge import JudgeVerdict
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_TRANSLATED


def result(severity, verdict, **kw):
    errs = (
        []
        if severity == "none"
        else [
            {
                "span": "x",
                "category": "terminology",
                "severity": severity,
                "description": "d",
            }
        ]
    )
    return JudgeResult(
        max_severity=severity,
        model_verdict=verdict,
        errors=errs,
        back_translation=kw.get("bt", ""),
        unparsed=kw.get("unparsed", False),
    )


PASS = result("none", "pass")
MAJOR = result("major", "flag")
CRITICAL = result("critical", "reject")
DEAD = JudgeResult("none", "", [], "", unparsed=True)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeLoopTest(ViewTestCase):
    def run_batch(self, seat_results, repair=None, writable=True):
        # seat_results: list of results, consumed in order, one per call.
        client = mock.Mock(side_effect=[[r] for r in seat_results])
        unit = self.get_unit()
        writable_ids = {unit.id} if writable else set()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_target", return_value=repair),
        ):
            verdicts = run_judge_batch(
                [unit], writable_ids=writable_ids, user=self.user
            )
        return unit, verdicts[unit.id], client

    def test_both_seats_judge_every_string(self) -> None:
        unit, verdict, client = self.run_batch([PASS, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_no_seat_may_lower_the_other(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_the_same_holds_when_the_strict_seat_votes_second(self) -> None:
        _, verdict, _ = self.run_batch([PASS, MAJOR])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_verdict_takes_the_higher_severity(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_each_seat_uses_its_configured_model(self) -> None:
        _, _, client = self.run_batch([PASS, PASS])
        models = [c.kwargs["model"] for c in client.call_args_list]
        self.assertEqual(models, ["vendor-a/model", "vendor-b/model"])

    def test_every_seat_bills_the_units_project(self) -> None:
        # Without this the paid judge requests land in LLMUsageLog with a
        # blank project and llm_usage_report cannot attribute the spend.
        unit, _, client = self.run_batch([PASS, PASS])
        slugs = {c.kwargs["project_slug"] for c in client.call_args_list}
        self.assertEqual(slugs, {unit.translation.component.project.slug})

    def test_unparsed_neither_raises_nor_lowers_the_other_seat(self) -> None:
        _, verdict, _ = self.run_batch([CRITICAL, DEAD], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_clean_seat_next_to_an_unparsed_one_still_passes(self) -> None:
        _, verdict, _ = self.run_batch([PASS, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)

    def test_unparsed_from_both_seats_is_unparsed(self) -> None:
        _, verdict, _ = self.run_batch([DEAD, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)

    def test_confirmed_defect_triggers_one_repair_judged_by_both_seats(self) -> None:
        _unit, verdict, client = self.run_batch(
            [CRITICAL, CRITICAL, PASS, PASS], repair=["fixed text"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(client.call_count, 4)

    def test_exhausted_loop_returns_the_last_negative_verdict(self) -> None:
        _, verdict, _ = self.run_batch(
            [CRITICAL, CRITICAL, CRITICAL, CRITICAL], repair=["still wrong"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_repair_that_changes_nothing_stops_the_loop(self) -> None:
        _, _verdict, client = self.run_batch([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(client.call_count, 2)

    def test_a_human_string_is_not_repaired_when_not_writable(self) -> None:
        # D3/A3: overwrite off => the unit is not in writable_ids => a
        # false critical never rewrites the human translation.
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        _, _verdict, _client = self.run_batch(
            [CRITICAL, CRITICAL], repair=["MACHINE OVERWRITE"], writable=False
        )
        self.assertNotEqual(self.get_unit().target, "MACHINE OVERWRITE")

    def test_repair_sees_the_round_verdict_projected(self) -> None:
        # Ordering guard: run_checks() projects the round's Check row
        # before repair_target builds its prompt from failing_checks.
        seen = []

        def spy(unit, user):
            seen.append({c.name for c in unit.all_checks})
            return ["fixed text"]

        client = mock.Mock(side_effect=[[r] for r in (CRITICAL, CRITICAL, PASS, PASS)])
        unit = self.get_unit()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_target", side_effect=spy),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(seen, [{"judge-reject"}])

    def test_every_verdict_of_one_run_shares_the_run_id(self) -> None:
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL, PASS, PASS], repair=["fixed"])
        self.assertEqual(
            len(set(unit.judge_verdicts.values_list("run_id", flat=True))), 1
        )

    def test_each_seat_votes_once_per_round(self) -> None:
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL, PASS, PASS], repair=["fixed"])
        self.assertEqual(
            set(unit.judge_verdicts.values_list("attempt", "seat")),
            {(0, 1), (0, 2), (1, 1), (1, 2)},
        )
