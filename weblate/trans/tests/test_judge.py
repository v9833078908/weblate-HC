# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import translation
from django.utils.translation import gettext

from weblate.auth.models import Group, Permission, Role, setup_project_groups
from weblate.trans.actions import ActionEvents
from weblate.trans.change_display import RenderJudgeResolution
from weblate.trans.forms import JudgeResolutionForm
from weblate.trans.judge import judge_request_upper_bound
from weblate.trans.judge_loop import accept_judge_candidate, build_request
from weblate.trans.models import Suggestion
from weblate.trans.models.change import Change
from weblate.trans.models.judge import (
    SEVERITY_RANK,
    JudgeCandidateError,
    JudgeCandidateMetadata,
    JudgeResolutionError,
    JudgeRun,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
    current_verdict,
    resolve_verdict,
    state_for_verdict,
    verdict_for_severity,
)
from weblate.trans.models.unit import Unit
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.utils.state import (
    FUZZY_STATES,
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_TRANSLATED,
)


def judge_context_hash(unit) -> str:
    request = build_request(unit)
    return compute_context_hash(
        source=request.source,
        note=request.note,
        explanation=request.explanation,
        glossary_terms=request.glossary_terms,
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


class JudgePrimaryErrorTest(SimpleTestCase):
    def test_primary_error_picks_max_severity(self) -> None:
        verdict = JudgeVerdict(
            max_severity="critical",
            errors=[
                {"category": "style", "severity": "minor", "description": "a"},
                {
                    "category": "mistranslation",
                    "severity": "critical",
                    "description": "b",
                },
            ],
        )
        self.assertEqual(verdict.primary_error["category"], "mistranslation")

    def test_primary_error_empty_list_returns_none(self) -> None:
        verdict = JudgeVerdict(max_severity="critical", errors=[])
        self.assertIsNone(verdict.primary_error)

    def test_primary_error_ties_return_the_first_listed(self) -> None:
        verdict = JudgeVerdict(
            max_severity="critical",
            errors=[
                {"category": "first", "severity": "critical", "description": "a"},
                {"category": "second", "severity": "critical", "description": "b"},
            ],
        )
        self.assertEqual(verdict.primary_error["category"], "first")


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
        base = compute_context_hash(
            source="Door", note="", explanation="", glossary_terms=[]
        )
        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door", note="hall", explanation="", glossary_terms=[]
            ),
        )
        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door",
                note="",
                explanation="",
                glossary_terms=[{"source": "Door", "target": "Porte"}],
            ),
        )

    def test_context_hash_reacts_to_source_explanation(self) -> None:
        base = compute_context_hash(
            source="Door", note="", explanation="", glossary_terms=[]
        )

        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door",
                note="",
                explanation="Shown on the locked-door screen.",
                glossary_terms=[],
            ),
        )

    def test_context_hash_reacts_to_glossary_target(self) -> None:
        first = {"source": "Door", "target": "Porte"}
        second = {"source": "Door", "target": "Portail"}
        self.assertNotEqual(
            compute_context_hash(
                source="Door", note="", explanation="", glossary_terms=[first]
            ),
            compute_context_hash(
                source="Door", note="", explanation="", glossary_terms=[second]
            ),
        )

    def test_context_hash_reacts_to_glossary_explanations_and_flags(self) -> None:
        plain = {"source": "Door", "target": "Porte"}
        variants = (
            {**plain, "source_explanation": "A game-mode name."},
            {**plain, "target_explanation": "Use on the battle screen."},
            {**plain, "flags": ["exact"]},
        )
        baseline = compute_context_hash(
            source="Door", note="", explanation="", glossary_terms=[plain]
        )
        for entry in variants:
            with self.subTest(entry=entry):
                self.assertNotEqual(
                    baseline,
                    compute_context_hash(
                        source="Door", note="", explanation="", glossary_terms=[entry]
                    ),
                )

    def test_context_hash_ignores_only_glossary_order(self) -> None:
        first = {"source": "a", "target": "b"}
        second = {"source": "c", "target": "d"}
        self.assertEqual(
            compute_context_hash(
                source="Door", note="", explanation="", glossary_terms=[first, second]
            ),
            compute_context_hash(
                source="Door", note="", explanation="", glossary_terms=[second, first]
            ),
        )


class JudgeResolutionTest(ViewTestCase):
    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(unit=unit, max_severity=severity, **kwargs)

    def test_permission_denied_without_review(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        with self.assertRaises(PermissionDenied):
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="needs a look",
            )

    def test_blank_reason_accepted(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="",
        )
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution_reason, "")
        change = (
            Change.objects.filter(unit=unit, action=ActionEvents.JUDGE_RESOLUTION)
            .order_by("-pk")
            .first()
        )
        self.assertIsNotNone(change)
        self.assertEqual(change.details["reason"], "")

    def test_unsupported_resolution_value_rejected(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        with self.assertRaises(JudgeResolutionError) as cm:
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.SENT_BACK,
                reason="not exposed",
            )
        self.assertEqual(cm.exception.code, "invalid_transition")

    def test_missing_verdict_rejected(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        with self.assertRaises(JudgeResolutionError) as cm:
            resolve_verdict(
                unit=unit,
                expected_verdict_id=1,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="nothing to resolve",
            )
        self.assertEqual(cm.exception.code, "missing")

    def test_stale_verdict_id_rejected(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        with self.assertRaises(JudgeResolutionError) as cm:
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk + 1000,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="stale client view",
            )
        self.assertEqual(cm.exception.code, "stale")

    def test_critical_escalation_keeps_state_fuzzy(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="needs a human look",
        )
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, JudgeVerdict.Resolution.ESCALATED)
        self.assertEqual(verdict.resolution_reason, "needs a human look")
        self.assertEqual(verdict.resolved_by_id, self.user.pk)
        self.assertIsNotNone(verdict.resolved_at)
        changes = Change.objects.filter(unit=unit, action=ActionEvents.JUDGE_RESOLUTION)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(changes[0].details["new_resolution"], "escalated")

    def test_critical_direct_acceptance_ships(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="acceptable in context",
        )
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, JudgeVerdict.Resolution.ACCEPTED_AS_IS)

    def test_major_escalation_holds_for_review(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="wants a second opinion",
        )
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_NEEDS_CHECKING)

    def test_fresh_flag_can_be_accepted_as_is_directly(self) -> None:
        # Task 7: a fresh FLAG no longer needs the escalate-then-accept
        # round trip; Keep as is is reachable in one step, same as Reject.
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="acceptable in context",
        )
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, JudgeVerdict.Resolution.ACCEPTED_AS_IS)
        changes = Change.objects.filter(unit=unit, action=ActionEvents.JUDGE_RESOLUTION)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(changes[0].details["old_resolution"], "")
        self.assertEqual(changes[0].details["new_resolution"], "accepted_as_is")

    def test_escalated_major_can_be_accepted_with_both_changes_preserved(
        self,
    ) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="first pass concern",
        )
        unit = self.get_unit()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="reviewed, fine to ship",
        )
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        changes = list(
            Change.objects.filter(
                unit=unit, action=ActionEvents.JUDGE_RESOLUTION
            ).order_by("pk")
        )
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0].details["new_resolution"], "escalated")
        self.assertEqual(changes[0].details["reason"], "first pass concern")
        self.assertEqual(changes[1].details["old_resolution"], "escalated")
        self.assertEqual(changes[1].details["new_resolution"], "accepted_as_is")
        self.assertEqual(changes[1].details["reason"], "reviewed, fine to ship")

    def test_duplicate_escalation_is_rejected(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="first request",
        )
        unit = self.get_unit()
        with self.assertRaises(JudgeResolutionError) as cm:
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="duplicate submit",
            )
        self.assertEqual(cm.exception.code, "invalid_transition")
        self.assertEqual(
            Change.objects.filter(
                unit=unit, action=ActionEvents.JUDGE_RESOLUTION
            ).count(),
            1,
        )

    def test_terminal_acceptance_cannot_be_redecided(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="first decision",
        )
        unit = self.get_unit()
        for resolution in (
            JudgeVerdict.Resolution.ESCALATED,
            JudgeVerdict.Resolution.ACCEPTED_AS_IS,
        ):
            with self.subTest(resolution=resolution):
                with self.assertRaises(JudgeResolutionError) as cm:
                    resolve_verdict(
                        unit=unit,
                        expected_verdict_id=verdict.pk,
                        actor=self.user,
                        resolution=resolution,
                        reason="too late",
                    )
                self.assertEqual(cm.exception.code, "invalid_transition")

    def test_concurrent_write_between_read_and_lock_is_not_missed(self) -> None:
        # This TestCase runs inside one outer transaction (savepoints, not
        # separate connections), so it cannot exercise Postgres's own
        # SELECT FOR UPDATE cross-connection blocking - that guarantee is
        # the database's, not this code's, to prove. What IS this code's
        # responsibility: re-read the representative fresh AFTER taking
        # the lock rather than trusting the earlier unlocked read. This
        # hooks exactly that window - current_verdict() is the last
        # unlocked read before the locked re-fetch - and lands a write
        # there to prove the locked re-read (not the stale earlier one)
        # is what the transition check actually uses.
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        unit.run_checks()

        real_current_verdict = current_verdict

        def land_a_write_in_the_race_window(candidate_unit):
            JudgeVerdict.objects.filter(pk=verdict.pk).update(
                resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
                resolution_reason="landed first, before the lock",
            )
            return real_current_verdict(candidate_unit)

        with (
            patch(
                "weblate.trans.models.judge.current_verdict",
                side_effect=land_a_write_in_the_race_window,
            ),
            self.assertRaises(JudgeResolutionError) as cm,
        ):
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="too slow",
            )
        self.assertEqual(cm.exception.code, "invalid_transition")
        # The write landed inside the same atomic() block as the rejected
        # request, so it rolls back with it (a real second transaction
        # would already have committed independently - see
        # JudgeResolutionRealConcurrencyTest for that property). What this
        # proves: the transition check used the value the mock wrote, not
        # the value from before it - the locked re-read, not the earlier
        # unlocked current_verdict() result, decided the outcome.

    def test_stale_when_target_text_changes(self) -> None:
        # A genuine staleness case (not just a wrong id): editing the
        # translation after judging changes target_hash, so the old round
        # is no longer "current" at all.
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        unit.run_checks()
        unit.translate(self.user, ["Ahoj upraveno"], STATE_TRANSLATED)
        unit = self.get_unit()
        with self.assertRaises(JudgeResolutionError) as cm:
            resolve_verdict(
                unit=unit,
                expected_verdict_id=verdict.pk,
                actor=self.user,
                resolution=JudgeVerdict.Resolution.ESCALATED,
                reason="text moved on",
            )
        self.assertEqual(cm.exception.code, "stale")

    def test_immutable_verdict_evidence_survives_resolution(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(
            unit,
            "critical",
            errors=[
                {
                    "span": "x",
                    "category": "terminology",
                    "severity": "critical",
                    "description": "original evidence",
                }
            ],
        )
        unit.run_checks()
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="override",
        )
        verdict.refresh_from_db()
        self.assertEqual(verdict.max_severity, "critical")
        self.assertEqual(verdict.errors[0]["description"], "original evidence")
        self.assertEqual(verdict.judge_model, "vendor/model-a")

    def test_resolution_applies_to_the_collegium_representative(self) -> None:
        # Two seats, one round: the representative is the strictest seat
        # (collegium_verdict), and that is the row the id must match.
        self.enable_review()
        unit = self.get_unit()
        run_id = self.make_verdict(unit, "minor", seat=1).run_id
        representative = self.make_verdict(unit, "critical", seat=2, run_id=run_id)
        unit.run_checks()
        self.assertEqual(current_verdict(unit).pk, representative.pk)
        resolve_verdict(
            unit=unit,
            expected_verdict_id=representative.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="strictest seat wins",
        )
        representative.refresh_from_db()
        self.assertEqual(representative.resolution, JudgeVerdict.Resolution.ESCALATED)


class JudgeResolutionRealConcurrencyTest(RepoTestMixin, TransactionTestCase):
    """
    Two independent DB connections racing the same representative row.

    A plain TestCase runs inside one outer transaction (savepoints, not
    separate commits), so a second thread's connection could never see
    the fixture at all. This needs real commits, hence TransactionTestCase.
    """

    def setUp(self) -> None:
        self.clone_test_repos()
        super().setUp()
        component = self.create_component()
        component.create_path()
        self.project = component.project
        setup_project_groups(self, self.project)
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        self.translation = component.translation_set.get(language_code="cs")
        self.user = create_test_user()
        self.user.groups.add(Group.objects.get(name="Users"))
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    def get_unit(self):
        return self.translation.unit_set.get(source="Hello, world!\n")

    def test_only_one_concurrent_acceptance_wins(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        target_hash = compute_target_hash(unit.get_target_plurals())
        target_storage_hash = compute_target_storage_hash(unit.target)
        context_hash = judge_context_hash(unit)
        verdict = JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            target_hash=target_hash,
            target_storage_hash=target_storage_hash,
            context_hash=context_hash,
            judge_model="vendor/model-a",
            seat=1,
        )
        unit.run_checks()
        connection.close()  # this connection must not straddle the threads

        barrier = threading.Barrier(2)
        outcomes: list[dict] = []
        lock = threading.Lock()

        def attempt(reason: str) -> None:
            try:
                barrier.wait(timeout=5)
                thread_unit = Unit.objects.get(pk=unit.pk)
                resolve_verdict(
                    unit=thread_unit,
                    expected_verdict_id=verdict.pk,
                    actor=self.user,
                    resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
                    reason=reason,
                )
                outcome = {"reason": reason, "ok": True}
            except JudgeResolutionError as error:
                outcome = {"reason": reason, "ok": False, "code": error.code}
            finally:
                connection.close()
            with lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=(f"attempt {i}",)) for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(outcomes), 2, "both threads must finish and report")
        successes = [o for o in outcomes if o["ok"]]
        failures = [o for o in outcomes if not o["ok"]]
        self.assertEqual(len(successes), 1, f"exactly one winner, got: {outcomes}")
        self.assertEqual(len(failures), 1, f"exactly one loser, got: {outcomes}")
        self.assertEqual(failures[0]["code"], "invalid_transition")
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, JudgeVerdict.Resolution.ACCEPTED_AS_IS)
        self.assertEqual(verdict.resolution_reason, successes[0]["reason"])
        self.assertEqual(
            Change.objects.filter(
                unit=unit, action=ActionEvents.JUDGE_RESOLUTION
            ).count(),
            1,
            "the loser must not have written a Change",
        )


class JudgeResolutionLocalizationTest(SimpleTestCase):
    def test_action_and_form_copy_renders_in_russian(self) -> None:
        with translation.override("ru"):
            self.assertEqual(
                str(ActionEvents.JUDGE_RESOLUTION.label),
                "Решение по вердикту судьи",
            )
            form = JudgeResolutionForm()
            self.assertEqual(str(form.fields["resolution"].label), "Решение")
            self.assertEqual(str(form.fields["reason"].label), "Причина")
            choice_labels = {
                value: str(label) for value, label in form.fields["resolution"].choices
            }
            self.assertEqual(choice_labels["escalated"], "Эскалировать на проверку")
            self.assertEqual(choice_labels["accepted_as_is"], "Принять как есть")
            self.assertEqual(
                str(RenderJudgeResolution.RESOLUTION_LABELS["escalated"]),
                "эскалировано",
            )
            self.assertEqual(
                str(RenderJudgeResolution.RESOLUTION_LABELS["accepted_as_is"]),
                "принято как есть",
            )

    def test_domain_helper_errors_render_in_russian(self) -> None:
        with translation.override("ru"):
            self.assertEqual(
                str(
                    JudgeResolutionError(
                        "invalid_transition",
                        gettext("That decision is not available."),
                    )
                ),
                "Это решение недоступно.",
            )
            self.assertEqual(
                str(
                    JudgeResolutionError(
                        "missing",
                        gettext("No current judge verdict was found for this string."),
                    )
                ),
                "Для этой строки не найден актуальный вердикт судьи.",
            )
            self.assertEqual(
                str(
                    JudgeResolutionError(
                        "invalid_transition",
                        gettext("That decision does not apply to this verdict."),
                    )
                ),
                "Это решение неприменимо к данному вердикту.",
            )


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeRequestEstimateTest(SimpleTestCase):
    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_one_string_still_costs_one_batch(self) -> None:
        self.assertEqual(judge_request_upper_bound(1), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_a_full_batch_is_not_rounded_up(self) -> None:
        self.assertEqual(judge_request_upper_bound(5), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_one_string_over_a_batch_adds_one_batch(self) -> None:
        self.assertEqual(judge_request_upper_bound(6), 8)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=0)
    def test_no_repair_attempt_means_one_round(self) -> None:
        self.assertEqual(judge_request_upper_bound(6), 4)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_no_strings_costs_nothing(self) -> None:
        self.assertEqual(judge_request_upper_bound(0), 0)

    @override_settings(
        JUDGE_BATCH_SIZE_SEAT_1=2,
        JUDGE_BATCH_SIZE_SEAT_2=5,
        JUDGE_MAX_REPAIR_ATTEMPTS=1,
    )
    def test_per_seat_batch_ceilings_drive_the_estimate(self) -> None:
        # ceil(6 / 2) + ceil(6 / 5), multiplied by two judge rounds.
        self.assertEqual(judge_request_upper_bound(6), 10)

    @override_settings(JUDGE_BATCH_SIZE=0, JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_a_broken_batch_size_yields_no_number(self) -> None:
        self.assertIsNone(judge_request_upper_bound(6))


CANDIDATE_METADATA = {
    "kind": "judge-repair",
    "schema": 1,
    "judge_verdict_id": 1,
    "judge_run_id": "11111111-1111-1111-1111-111111111111",
    "target_hash": "a" * 64,
    "context_hash": "b" * 64,
    "engine": "openrouter",
}


class JudgeCandidateMetadataTest(SimpleTestCase):
    def test_a_non_integer_schema_is_rejected(self) -> None:
        # 1.0 and True both compare equal to 1 in Python; neither is a valid
        # schema version under the closed contract.
        for value in (1.0, True):
            with self.subTest(schema=value):
                details = dict(CANDIDATE_METADATA, schema=value)
                with self.assertRaises(JudgeCandidateError):
                    JudgeCandidateMetadata(details)

    def test_valid_metadata_round_trips(self) -> None:
        candidate = JudgeCandidateMetadata(CANDIDATE_METADATA)
        self.assertEqual(candidate.verdict_id, 1)
        self.assertEqual(
            candidate.run_id, uuid.UUID(CANDIDATE_METADATA["judge_run_id"])
        )
        self.assertEqual(candidate.context_hash, "b" * 64)
        self.assertEqual(candidate.engine, "openrouter")
        self.assertEqual(candidate.as_dict(), CANDIDATE_METADATA)

    def test_unknown_kind_is_not_a_candidate(self) -> None:
        self.assertIsNone(JudgeCandidateMetadata.parse({"kind": "other"}))
        self.assertIsNone(JudgeCandidateMetadata.parse({}))

    def test_missing_or_wrong_typed_fields_are_rejected(self) -> None:
        for field in ("schema", "judge_verdict_id", "judge_run_id", "engine"):
            with self.subTest(field=field):
                broken = dict(CANDIDATE_METADATA)
                del broken[field]
                with self.assertRaises(JudgeCandidateError):
                    JudgeCandidateMetadata(broken)
        for field in ("judge_verdict_id", "engine"):
            with self.subTest(field=field):
                broken = dict(CANDIDATE_METADATA)
                broken[field] = 5 if field == "engine" else "5"
                with self.assertRaises(JudgeCandidateError):
                    JudgeCandidateMetadata(broken)
        broken = dict(CANDIDATE_METADATA)
        broken["schema"] = 2
        with self.assertRaises(JudgeCandidateError):
            JudgeCandidateMetadata(broken)

    def test_unknown_keys_are_rejected(self) -> None:
        broken = dict(CANDIDATE_METADATA)
        broken["prompt"] = "leak"
        with self.assertRaises(JudgeCandidateError):
            JudgeCandidateMetadata(broken)

    def test_hashes_must_be_hex64(self) -> None:
        for field in ("target_hash", "context_hash"):
            with self.subTest(field=field):
                broken = dict(CANDIDATE_METADATA)
                broken[field] = "short"
                with self.assertRaises(JudgeCandidateError):
                    JudgeCandidateMetadata(broken)

    def test_run_id_must_be_a_uuid(self) -> None:
        broken = dict(CANDIDATE_METADATA)
        broken["judge_run_id"] = "not-a-uuid"
        with self.assertRaises(JudgeCandidateError):
            JudgeCandidateMetadata(broken)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeCandidateAcceptanceTest(ViewTestCase):
    """Direct tests for accept_judge_candidate (Task 4 acceptance guard)."""

    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def grant(self, codenames) -> None:
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])
        role = Role.objects.create(name="Candidate acceptance")
        for codename in codenames:
            role.permissions.add(Permission.objects.get(codename=codename))
        group = Group.objects.create(name="Candidate accepters")
        group.roles.add(role)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        verdict = JudgeVerdict.objects.create(
            unit=unit, max_severity=severity, **kwargs
        )
        unit.run_checks()
        return verdict

    def make_candidate(self, unit, verdict, target="Better translation"):
        metadata = {
            "kind": "judge-repair",
            "schema": 1,
            "judge_verdict_id": verdict.pk,
            "judge_run_id": str(verdict.run_id),
            "target_hash": compute_target_hash(unit.get_target_plurals()),
            "context_hash": judge_context_hash(unit),
            "engine": "openrouter",
        }
        suggestion, _result = Suggestion.objects.add(
            unit,
            [target],
            request=None,
            vote=False,
            raise_exception=False,
            userdetails=metadata,
        )
        return suggestion

    def accept_as(self, candidate, user=None) -> None:
        request = self.get_request()
        request.user = user or self.user
        with (
            patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            accept_judge_candidate(candidate, request)

    def test_accept_holds_fuzzy_consumes_candidate_and_queues_recheck(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)

        self.accept_as(candidate)

        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        self.assertEqual(refreshed.target, "Better translation\n")
        self.assertFalse(Suggestion.objects.filter(pk=candidate.pk).exists())
        runs = JudgeRun.objects.filter(
            requested_mode="recheck", scope_id=str(unit.translation_id)
        )
        self.assertEqual(runs.count(), 1)
        change = (
            Change.objects.filter(unit=unit, action=ActionEvents.ACCEPT)
            .order_by("-pk")
            .first()
        )
        self.assertIsNotNone(change)
        self.assertEqual(change.details.get("judge_verdict_id"), verdict.pk)
        self.assertEqual(change.details.get("judge_run_id"), str(verdict.run_id))

    def test_accept_denied_without_unit_review(self) -> None:
        self.grant(["translation.auto"])
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        before_target = unit.target

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertEqual(self.get_unit().target, before_target)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertFalse(JudgeRun.objects.filter(requested_mode="recheck").exists())

    def test_accept_denied_without_translation_auto(self) -> None:
        self.grant(["unit.review"])
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        before_target = unit.target

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertEqual(self.get_unit().target, before_target)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_fails_on_target_drift(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        unit.translate(self.user, ["Manually edited"], STATE_TRANSLATED)

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        refreshed = self.get_unit()
        self.assertEqual(refreshed.target, "Manually edited\n")
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertFalse(JudgeRun.objects.filter(requested_mode="recheck").exists())

    def test_accept_fails_on_context_drift(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        source_unit = unit.source_unit
        source_unit.explanation = "a fresh glossary note"
        source_unit.save(update_fields=["explanation"])

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_fails_when_another_verdict_is_now_current(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        # A second row for the SAME seat and the SAME target/context (e.g. a
        # deferral retry) supersedes the verdict the candidate was minted
        # for: _seat_round_rows always takes each seat's newest row.
        newer = self.make_verdict(unit, "critical", seat=verdict.seat)
        self.assertNotEqual(newer.pk, verdict.pk)
        self.assertEqual(current_verdict(unit).pk, newer.pk)

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_fails_on_resolved_verdict(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="needs a human look",
        )

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_fails_on_malformed_metadata(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        candidate = Suggestion.objects.create(
            unit=unit,
            target="Broken candidate",
            userdetails={"kind": "judge-repair", "schema": 1},
        )

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)

        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_fails_on_stale_suggestion_id(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        stale_pk = candidate.pk
        candidate.delete()
        # A dangling reference to a Python object whose row is already gone
        # under lock: the concurrent-handling case.
        candidate.pk = stale_pk

        with self.assertRaises(JudgeCandidateError):
            self.accept_as(candidate)
