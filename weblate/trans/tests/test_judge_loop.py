# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from unittest import mock

from django.test import override_settings

from weblate.trans.judge import JudgeResult
from weblate.trans.judge_loop import build_request, repair_target, run_judge_batch
from weblate.trans.models.judge import (
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
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

    def test_repair_target_selects_a_candidate_per_plural_form(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.translate.return_value = [
            [
                {"text": "lower quality", "quality": 50},
                {"text": "fixed text", "quality": 100},
            ]
        ]
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        with mock.patch("weblate.trans.judge_loop.MACHINERY", {"openrouter": engine}):
            self.assertEqual(repair_target(unit, self.user), ["fixed text"])

    def test_no_seat_may_lower_the_other(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_the_same_holds_when_the_strict_seat_votes_second(self) -> None:
        _, verdict, _ = self.run_batch([PASS, MAJOR])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_verdict_takes_the_higher_severity(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_flag_triggers_one_repair_judged_by_both_seats(self) -> None:
        _unit, verdict, client = self.run_batch(
            [MAJOR, MAJOR, PASS, PASS], repair=["fixed text"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(self.get_unit().target.strip(), "fixed text")
        self.assertEqual(client.call_count, 4)

    def test_exhausted_flag_repair_keeps_the_last_flag(self) -> None:
        _unit, verdict, client = self.run_batch(
            [MAJOR, MAJOR, MAJOR, MAJOR], repair=["still wrong"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(client.call_count, 4)

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

    def test_every_seat_gets_the_projects_own_context(self) -> None:
        # The judge must describe the game from the same configuration the
        # translator uses, or it argues from a setting nobody configured.
        project = self.component.project
        project.machinery_settings = {
            "openrouter": {
                "persona": "You judge a dark fantasy game.",
                "style": "Preserve profanity.",
            }
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(
            contexts, {"You judge a dark fantasy game.\n\nPreserve profanity."}
        )

    def test_an_unconfigured_project_sends_no_context(self) -> None:
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {""})

    def test_unparsed_neither_raises_nor_lowers_the_other_seat(self) -> None:
        _, verdict, _ = self.run_batch([CRITICAL, DEAD], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_clean_seat_next_to_an_unparsed_one_still_passes(self) -> None:
        _, verdict, _ = self.run_batch([PASS, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)

    def test_unparsed_from_both_seats_is_unparsed(self) -> None:
        _, verdict, _ = self.run_batch([DEAD, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)

    def test_current_all_unparsed_round_does_not_repair_from_history(self) -> None:
        unit = self.get_unit()
        old_request = build_request(unit)
        old_run = uuid.uuid4()
        for seat, model in enumerate(("vendor-a/model", "vendor-b/model"), start=1):
            JudgeVerdict.objects.create(
                unit=unit,
                max_severity="critical",
                model_verdict="reject",
                judge_model=model,
                seat=seat,
                run_id=old_run,
                target_hash=compute_target_hash(old_request.target_plurals),
                context_hash=compute_context_hash(
                    source=old_request.source,
                    note=old_request.note,
                    glossary_terms=old_request.glossary_terms,
                ),
            )
        client = mock.Mock(side_effect=[[DEAD], [DEAD]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop._cached_verdict", return_value=None),
            mock.patch(
                "weblate.trans.judge_loop.repair_target",
                return_value=["must not be used"],
            ) as repair,
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.UNPARSED)
        repair.assert_not_called()

    def test_matching_parsed_round_is_reused_without_network(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        run = uuid.uuid4()
        for seat, model in enumerate(("vendor-a/model", "vendor-b/model"), start=1):
            JudgeVerdict.objects.create(
                unit=unit,
                max_severity="none",
                model_verdict="pass",
                judge_model=model,
                seat=seat,
                run_id=run,
                target_hash=compute_target_hash(request.target_plurals),
                context_hash=compute_context_hash(
                    source=request.source,
                    note=request.note,
                    glossary_terms=request.glossary_terms,
                ),
            )
        client = mock.Mock()
        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            verdicts = run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.PASS)
        client.assert_not_called()

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

    def test_repair_is_rolled_back_when_it_adds_a_deterministic_check(self) -> None:
        unit = self.get_unit()
        original = unit.target
        client = mock.Mock(side_effect=[[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_target",
                return_value=["new but invalid"],
            ),
            mock.patch(
                "weblate.trans.judge_loop._deterministic_checks",
                side_effect=[set(), {"game-markup"}],
            ),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(self.get_unit().target, original)

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

    def test_the_judges_own_projection_is_not_sent_back_as_evidence(self) -> None:
        # Observed live on dev 2026-08-20: a seat justified a major with
        # "the judge-flag check indicating non-conformance" - its own
        # previous opinion, handed back to it as proven fact.
        unit = self.get_unit()
        request = build_request(unit)
        JudgeVerdict.objects.create(
            unit=unit,
            run_id=uuid.uuid4(),
            attempt=0,
            seat=1,
            judge_model="vendor-a/model",
            max_severity="major",
            model_verdict="flag",
            target_hash=compute_target_hash(request.target_plurals),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                glossary_terms=request.glossary_terms,
            ),
        )
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-flag", unit.all_checks_names)
        self.assertNotIn("judge-flag", build_request(unit).failing_checks)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeGlossaryRepairLockTest(ViewTestCase):
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        super().setUp()
        glossary_component = self.project.glossaries[0]
        glossary = glossary_component.translation_set.get(
            language=self.translation.language
        )
        id_hash = calculate_hash("Hello", "")
        self.source_term = glossary_component.source_translation.unit_set.create(
            source="Hello",
            target="Hello",
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            explanation="A greeting, not a character name.",
        )
        glossary.unit_set.create(
            source="Hello",
            target="Ahoj",
            context="",
            source_unit=self.source_term,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        glossary.invalidate_cache()

    def test_glossary_explanation_change_aborts_repair(self) -> None:
        unit = self.get_unit()
        original = unit.target
        client = mock.Mock(side_effect=[[MAJOR], [MAJOR]])

        def change_context(_unit, _user):
            self.source_term.explanation = "Changed while the judge was running."
            self.source_term.save(update_fields=["explanation"])
            return ["must not be applied"]

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_target", side_effect=change_context
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertNotIn(unit.id, verdicts)
        self.assertEqual(self.get_unit().target, original)
