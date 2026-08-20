# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.trans.models.judge import (
    JudgeVerdict,
    active_round,
    active_verdict,
    compute_context_hash,
    compute_target_hash,
    current_round,
    current_verdict,
    describe_latest_verdict,
    latest_round,
)
from weblate.trans.tests.test_views import ViewTestCase


class JudgeRoundTest(ViewTestCase):
    def make(self, unit, max_severity, *, unparsed=False, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "ctx")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, unparsed=unparsed, **kwargs
        )

    def test_collegium_takes_the_strictest_seat(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "major", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_no_seat_may_lower_the_other(self) -> None:
        # Seat 2 passing must not clear seat 1's flag: the cascade B2'
        # rejected, arriving through the collegium read instead.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "major", seat=1, run_id=run)
        self.make(unit, "none", seat=2, run_id=run)
        v = active_verdict(unit)
        assert v is not None
        self.assertEqual(v.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(v.seat, 1)

    def test_a_parsed_seat_outvotes_an_unparsed_one(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run, unparsed=True)
        self.make(unit, "critical", seat=2, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_an_all_unparsed_round_keeps_the_previous_verdict(self) -> None:
        # D5: a transport-dead re-judge must not erase the last real verdict
        # of unchanged text.
        unit = self.get_unit()
        old = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=old)
        self.make(unit, "critical", seat=2, run_id=old)
        new = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=new, unparsed=True)
        self.make(unit, "none", seat=2, run_id=new, unparsed=True)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_current_round_exposes_unparsed_without_historical_fallback(self) -> None:
        unit = self.get_unit()
        context_hash = compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            glossary_terms=[],
        )
        old = uuid.uuid4()
        self.make(
            unit,
            "critical",
            seat=1,
            run_id=old,
            context_hash=context_hash,
        )
        new = uuid.uuid4()
        self.make(
            unit,
            "none",
            seat=1,
            run_id=new,
            unparsed=True,
            context_hash=context_hash,
        )
        active = active_verdict(unit)
        assert active is not None
        self.assertEqual(active.verdict, JudgeVerdict.Verdict.REJECT)
        current = current_verdict(unit)
        assert current is not None
        self.assertEqual(current.verdict, JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(current_round(unit)[0].run_id, new)

    def test_a_verdict_for_other_text_is_not_active(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical", target_hash="stale-hash-matches-nothing")
        self.assertIsNone(active_verdict(unit))
        # ...but latest_round still sees it, for the "previous version" note.
        self.assertEqual(len(latest_round(unit)), 1)
        self.assertTrue(latest_round(unit)[0].is_stale(unit.get_target_plurals()))

    def test_all_unparsed_and_no_prior_verdict_is_none(self) -> None:
        unit = self.get_unit()
        self.make(unit, "none", unparsed=True)
        self.assertIsNone(active_verdict(unit))
        self.assertEqual(active_round(unit), [])

    def test_description_merges_both_seats(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(
            unit,
            "critical",
            seat=1,
            run_id=run,
            errors=[
                {
                    "span": "ВРАТА",
                    "category": "terminology",
                    "severity": "critical",
                    "description": "the Gates are called DOORS here",
                }
            ],
        )
        self.make(
            unit,
            "major",
            seat=2,
            run_id=run,
            errors=[
                {
                    "span": "clause",
                    "category": "fluency",
                    "severity": "major",
                    "description": "the second clause has no verb",
                }
            ],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("the Gates are called DOORS here", description)
        self.assertIn("the second clause has no verb", description)

    def test_description_escapes_markup_and_separates_errors(self) -> None:
        # Q1: llm.py strips tags; the evidence must survive as escaped text
        # and errors must stay distinguishable after normalization.
        unit = self.get_unit()
        self.make(
            unit,
            "major",
            errors=[
                {
                    "span": "a",
                    "category": "markup",
                    "severity": "major",
                    "description": "target dropped <color=#FF0000>",
                },
                {
                    "span": "b",
                    "category": "fluency",
                    "severity": "major",
                    "description": "register too formal",
                },
            ],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("color=#FF0000", description)  # not eaten by strip_tags
        self.assertIn(" | ", description)  # explicit separator
