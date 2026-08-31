# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.glossary.models import (
    get_glossary_terms,
    get_matched_glossary_prompt_entries,
)
from weblate.trans.judge_loop import build_request
from weblate.trans.models.judge import (
    JudgeVerdict,
    active_round,
    active_verdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
    current_round,
    current_verdict,
    describe_latest_verdict,
    has_complete_current_evidence,
    judge_status_annotations,
    latest_round,
)
from weblate.trans.models.variant import Variant
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_TRANSLATED


class JudgeRoundTest(ViewTestCase):
    def make(self, unit, max_severity, *, unparsed=False, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", "ctx")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, unparsed=unparsed, **kwargs
        )

    def judge_status(self, unit) -> dict[str, object]:
        return (
            type(unit)
            .objects.filter(pk=unit.pk)
            .annotate(**judge_status_annotations())
            .values(
                "judge_active_severity",
                "judge_has_parsed_history",
                "judge_latest_incomplete",
            )
            .get()
        )

    def test_status_annotations_without_evidence(self) -> None:
        self.assertEqual(
            self.judge_status(self.get_unit()),
            {
                "judge_active_severity": None,
                "judge_has_parsed_history": False,
                "judge_latest_incomplete": False,
            },
        )

    def test_status_annotations_reduce_the_fresh_round(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "minor", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "critical")

    def test_request_round_keeps_transport_recovery_out_of_repair_attempts(
        self,
    ) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(
            unit,
            "critical",
            seat=1,
            run_id=run,
            attempt=0,
            request_round=0,
        )
        recovered = self.make(
            unit,
            "none",
            seat=1,
            run_id=run,
            attempt=0,
            request_round=1,
        )

        self.assertEqual(recovered.attempt, 0)
        self.assertEqual(recovered.request_round, 1)

    def test_status_annotations_keep_parsed_fallback_after_incomplete_round(
        self,
    ) -> None:
        unit = self.get_unit()
        self.make(unit, "major", seat=1, run_id=uuid.uuid4())
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run, unparsed=True)
        self.make(unit, "none", seat=2, run_id=run, unparsed=True)
        self.assertEqual(
            self.judge_status(unit),
            {
                "judge_active_severity": "major",
                "judge_has_parsed_history": True,
                "judge_latest_incomplete": True,
            },
        )

    def test_status_annotations_identify_current_unparsed_only_evidence(self) -> None:
        unit = self.get_unit()
        self.make(unit, "none", unparsed=True)
        self.assertEqual(
            self.judge_status(unit),
            {
                "judge_active_severity": None,
                "judge_has_parsed_history": False,
                "judge_latest_incomplete": True,
            },
        )

    def test_status_annotations_identify_stale_parsed_history(self) -> None:
        unit = self.get_unit()
        self.make(unit, "major", target_storage_hash="old-target")
        self.assertEqual(
            self.judge_status(unit),
            {
                "judge_active_severity": None,
                "judge_has_parsed_history": True,
                "judge_latest_incomplete": False,
            },
        )

    def test_status_annotations_become_stale_after_target_edit(self) -> None:
        unit = self.get_unit()
        self.make(unit, "major")
        type(unit).objects.filter(pk=unit.pk).update(target="Edited target")
        self.assertEqual(
            self.judge_status(unit)["judge_active_severity"],
            None,
        )

    def test_status_annotations_ignore_context_only_changes(self) -> None:
        unit = self.get_unit()
        self.make(unit, "minor")
        type(unit).objects.filter(pk=unit.pk).update(source="Changed source")
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "minor")

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

    def test_current_evidence_requires_every_configured_seat_to_parse(self) -> None:
        unit = self.get_unit()
        context_hash = compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            glossary_terms=[],
        )
        self.make(unit, "none", seat=1, context_hash=context_hash)
        self.make(
            unit,
            "none",
            seat=2,
            unparsed=True,
            context_hash=context_hash,
        )

        self.assertFalse(has_complete_current_evidence(unit, seats=(1, 2)))

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

    def test_one_seat_retry_in_a_new_run_cannot_hide_the_other_seats_critical(
        self,
    ) -> None:
        # A durable deferral retry lands in a different (run_id, attempt);
        # the reader must still combine seat 1's critical with seat 2's
        # later pass instead of grouping by the newest round key only.
        unit = self.get_unit()
        context_hash = compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            glossary_terms=[],
        )
        old = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=old, context_hash=context_hash)
        self.make(
            unit, "none", seat=2, run_id=old, unparsed=True, context_hash=context_hash
        )
        # Seat 2's deferred retry succeeds in its own new run.
        retry = uuid.uuid4()
        self.make(unit, "none", seat=2, run_id=retry, context_hash=context_hash)

        verdict = current_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.max_severity, "critical")
        self.assertEqual(verdict.seat, 1)

        active = active_verdict(unit)
        assert active is not None
        self.assertEqual(active.max_severity, "critical")
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "critical")

    def test_mixed_run_assembly_reads_both_seats_fresh_opinions(self) -> None:
        # Both seats retried separately: each lands in its own round key
        # and the strictest fresh per-seat opinion still wins.
        unit = self.get_unit()
        context_hash = compute_context_hash(
            source=unit.source,
            note=unit.source_unit.note,
            glossary_terms=[],
        )
        first, second = uuid.uuid4(), uuid.uuid4()
        self.make(unit, "major", seat=1, run_id=first, context_hash=context_hash)
        self.make(unit, "critical", seat=2, run_id=second, context_hash=context_hash)

        verdict = current_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.max_severity, "critical")
        self.assertEqual(verdict.seat, 2)

    def test_active_round_ignores_context_drift(self) -> None:
        # Historical active_round semantics: target-hash-only matching, so
        # a glossary/note drift must not unproject a judge check.
        unit = self.get_unit()
        self.make(unit, "major", seat=1, context_hash="old-context")
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)


class JudgeGlossaryContextTest(ViewTestCase):
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]
        self.glossary = self.glossary_component.translation_set.get(
            language=self.translation.language
        )

    def add_term(self) -> None:
        id_hash = calculate_hash("Hello", "")
        source_unit = self.glossary_component.source_translation.unit_set.create(
            source="Hello",
            target="Hello",
            context="",
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
            explanation="A greeting, not a character name.",
        )
        self.glossary.unit_set.create(
            source="Hello",
            target="Ahoj",
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=1,
            state=STATE_TRANSLATED,
        )
        self.glossary.invalidate_cache()

    def test_fresh_verdict_matches_round_and_view_context(self) -> None:
        self.add_term()
        unit = self.get_unit()
        unit.glossary_terms = None
        request = build_request(unit)
        self.assertEqual(
            request.glossary_terms,
            [
                {
                    "source": "Hello",
                    "target": "Ahoj",
                    "source_explanation": "A greeting, not a character name.",
                }
            ],
        )
        context_hash = compute_context_hash(
            source=request.source,
            note=request.note,
            glossary_terms=request.glossary_terms,
        )
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            model_verdict="flag",
            judge_model="vendor/model-a",
            seat=1,
            run_id=uuid.uuid4(),
            target_hash=compute_target_hash(request.target_plurals or [request.target]),
            context_hash=context_hash,
        )
        unit.glossary_terms = None
        self.assertEqual(len(current_round(unit)), 1)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "context changed")

    def add_variant_sibling(self) -> None:
        """Group a second term with the matched one, as a variant would."""
        variant = Variant.objects.create(
            component=self.glossary_component, variant_regex="", key="greeting"
        )
        id_hash = calculate_hash("Greeting", "")
        source_unit = self.glossary_component.source_translation.unit_set.create(
            source="Greeting",
            target="Greeting",
            context="",
            id_hash=id_hash,
            position=2,
            state=STATE_TRANSLATED,
        )
        self.glossary.unit_set.create(
            source="Greeting",
            target="Zdravím",
            context="",
            source_unit=source_unit,
            id_hash=id_hash,
            position=2,
            state=STATE_TRANSLATED,
            variant=variant,
        )
        self.glossary.unit_set.filter(source="Hello").update(variant=variant)
        self.glossary.invalidate_cache()

    def test_narrower_cached_selection_keeps_the_round_reachable(self) -> None:
        """A check selecting without variants must not decide judge context."""
        self.add_term()
        self.add_variant_sibling()
        unit = self.get_unit()
        unit.glossary_terms = None
        request = build_request(unit)
        self.assertEqual(len(request.glossary_terms), 2)
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            model_verdict="flag",
            judge_model="vendor/model-a",
            seat=1,
            run_id=uuid.uuid4(),
            target_hash=compute_target_hash(request.target_plurals or [request.target]),
            context_hash=compute_context_hash(
                source=request.source,
                note=request.note,
                glossary_terms=request.glossary_terms,
            ),
        )

        # `run_checks` asks for the narrower selection first and caches it.
        reread = self.get_unit()
        narrow = list(get_glossary_terms(reread, include_variants=False))
        self.assertEqual(len(narrow), 1)

        self.assertEqual(len(current_round(reread)), 1)
        # The check keeps its own answer afterwards, whatever the judge asked.
        self.assertEqual(len(get_glossary_terms(reread, include_variants=False)), 1)

    def test_matched_entries_do_not_decide_a_later_check(self) -> None:
        """The judge's wider selection must not become a later check's answer."""
        self.add_term()
        self.add_variant_sibling()
        unit = self.get_unit()
        unit.glossary_terms = None

        self.assertEqual(len(get_matched_glossary_prompt_entries(unit)), 2)

        # No restore is needed: the cache knows which selection filled it.
        self.assertEqual(len(get_glossary_terms(unit, include_variants=False)), 1)
