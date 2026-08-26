# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib

from django.test import SimpleTestCase, override_settings

from weblate.trans.models.judge import (
    SEVERITY_RANK,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
    state_for_verdict,
    verdict_for_severity,
)
from weblate.utils.state import (
    FUZZY_STATES,
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_TRANSLATED,
)


class JudgeSeverityGateTest(SimpleTestCase):
    def test_severity_maps_to_verdict(self) -> None:
        self.assertEqual(verdict_for_severity("none"), JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict_for_severity("minor"), JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict_for_severity("major"), JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict_for_severity("critical"), JudgeVerdict.Verdict.REJECT)

    def test_severity_rank_is_ordered_by_strictness(self) -> None:
        # SEVERITY_RANK derives from the enum declaration order; this test
        # pins that coupling so a silent reorder cannot invert strictness.
        self.assertLess(SEVERITY_RANK["none"], SEVERITY_RANK["minor"])
        self.assertLess(SEVERITY_RANK["minor"], SEVERITY_RANK["major"])
        self.assertLess(SEVERITY_RANK["major"], SEVERITY_RANK["critical"])

    def test_pass_stops_at_translated_without_the_approve_flag(self) -> None:
        # D2: a probabilistic pass never auto-approves in tier 1.
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=True, may_approve=False
            ),
            STATE_TRANSLATED,
        )

    @override_settings(JUDGE_MAY_APPROVE=True)
    def test_pass_may_approve_only_when_both_flags_hold(self) -> None:
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=True, may_approve=True
            ),
            STATE_APPROVED,
        )
        # No review configured: approval is impossible regardless of the flag.
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=False, may_approve=True
            ),
            STATE_TRANSLATED,
        )

    def test_flag_ships_as_translated(self) -> None:
        state = state_for_verdict(
            JudgeVerdict.Verdict.FLAG, enable_review=True, may_approve=True
        )
        self.assertEqual(state, STATE_TRANSLATED)
        self.assertNotIn(state, FUZZY_STATES)

    def test_flag_state_clears_the_without_needs_editing_commit_gate(self) -> None:
        # WITHOUT_NEEDS_EDITING excludes FUZZY_STATES from VCS commit/export
        # (weblate/trans/models/pending.py). An unresolved major must ship
        # with judge-flag evidence, while an unresolved critical is still
        # held back.
        major_state = state_for_verdict(
            JudgeVerdict.Verdict.FLAG, enable_review=True, may_approve=True
        )
        critical_state = state_for_verdict(
            JudgeVerdict.Verdict.REJECT, enable_review=True, may_approve=True
        )
        self.assertNotIn(major_state, FUZZY_STATES)
        self.assertIn(critical_state, FUZZY_STATES)

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        state = state_for_verdict(
            JudgeVerdict.Verdict.REJECT, enable_review=True, may_approve=True
        )
        self.assertEqual(state, STATE_FUZZY)
        self.assertIn(state, FUZZY_STATES)

    def test_unparsed_never_changes_state(self) -> None:
        self.assertIsNone(
            state_for_verdict(
                JudgeVerdict.Verdict.UNPARSED, enable_review=True, may_approve=True
            )
        )


class JudgeHashTest(SimpleTestCase):
    def test_target_hash_is_stable(self) -> None:
        self.assertEqual(
            compute_target_hash(["La porte est bloquée"]),
            compute_target_hash(["La porte est bloquée"]),
        )

    def test_target_hash_tracks_every_plural_form(self) -> None:
        self.assertNotEqual(
            compute_target_hash(["une porte", "deux portes"]),
            compute_target_hash(["une porte", "trois portes"]),
        )

    def test_target_hash_separator_cannot_be_forged(self) -> None:
        # Naive "\n".join() would collide these two different plural sets.
        self.assertNotEqual(
            compute_target_hash(["a\nb"]),
            compute_target_hash(["a", "b"]),
        )

    def test_target_storage_hash_uses_exact_raw_storage(self) -> None:
        target = "Одна дверь\x1e\x1eДве двери"
        self.assertEqual(
            compute_target_storage_hash(target),
            hashlib.md5(target.encode(), usedforsecurity=False).hexdigest(),
        )

    def test_target_storage_hash_tracks_raw_plural_changes(self) -> None:
        self.assertNotEqual(
            compute_target_storage_hash("Одна дверь\x1e\x1eДве двери"),
            compute_target_storage_hash("Одна дверь\x1e\x1eТри двери"),
        )

    def test_context_hash_reacts_to_glossary_and_note(self) -> None:
        base = compute_context_hash(source="Door", note="", glossary_terms=[])
        self.assertNotEqual(
            base, compute_context_hash(source="Door", note="hall", glossary_terms=[])
        )
        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door",
                note="",
                glossary_terms=[{"source": "Door", "target": "Porte"}],
            ),
        )

    def test_context_hash_reacts_to_glossary_target(self) -> None:
        first = {"source": "Door", "target": "Porte"}
        second = {"source": "Door", "target": "Portail"}
        self.assertNotEqual(
            compute_context_hash(source="Door", note="", glossary_terms=[first]),
            compute_context_hash(source="Door", note="", glossary_terms=[second]),
        )

    def test_context_hash_reacts_to_glossary_explanations_and_flags(self) -> None:
        plain = {"source": "Door", "target": "Porte"}
        variants = (
            {**plain, "source_explanation": "A game-mode name."},
            {**plain, "target_explanation": "Use on the battle screen."},
            {**plain, "flags": ["exact"]},
        )
        baseline = compute_context_hash(source="Door", note="", glossary_terms=[plain])
        for entry in variants:
            with self.subTest(entry=entry):
                self.assertNotEqual(
                    baseline,
                    compute_context_hash(
                        source="Door", note="", glossary_terms=[entry]
                    ),
                )

    def test_context_hash_ignores_only_glossary_order(self) -> None:
        first = {"source": "a", "target": "b"}
        second = {"source": "c", "target": "d"}
        self.assertEqual(
            compute_context_hash(
                source="Door", note="", glossary_terms=[first, second]
            ),
            compute_context_hash(
                source="Door", note="", glossary_terms=[second, first]
            ),
        )
