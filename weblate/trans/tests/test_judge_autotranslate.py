# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError
from django.test import override_settings

from weblate.trans.actions import ActionEvents
from weblate.trans.autotranslate import (
    AutoTranslate,
    BatchAutoTranslate,
    PreparationScope,
)
from weblate.trans.judge import JudgeError, JudgeResult, judge_configuration_snapshot
from weblate.trans.judge_loop import (
    build_request,
    recheck_query,
)
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    ProducerRun,
    compute_context_hash,
    compute_target_hash,
    has_complete_current_evidence,
)
from weblate.trans.models.unit import Unit
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import (
    FUZZY_STATES,
    STATE_APPROVED,
    STATE_EMPTY,
    STATE_FUZZY,
    STATE_TRANSLATED,
)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_UNITS_PER_RUN=2000,
    JUDGE_MAY_APPROVE=False,
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
    ),
)
class JudgeAutoTranslateTest(ViewTestCase):
    def test_batch_scope_accepts_a_project(self) -> None:
        with mock.patch.object(BatchAutoTranslate, "_preload_workflow_settings"):
            batch = BatchAutoTranslate(
                self.project,
                user=self.user,
                q="",
                mode="judge",
            )

        translations = list(batch.translations)
        self.assertIn(self.get_translation(), translations)
        self.assertTrue(all(not translation.is_source for translation in translations))
        self.assertTrue(batch.translations.ordered)

    def perform(
        self,
        verdict_kind,
        *,
        severity="none",
        q="",
        overwrite=False,
        judgeable=False,
        attempt=0,
        unit_ids=None,
    ):
        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
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
                    attempt=attempt,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
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
            unit_ids=unit_ids,
        )
        if judgeable:
            for unit in auto.get_units():
                unit.translate(self.user, ["Judgeable target"], STATE_TRANSLATED)
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.process_judge(engines=[], threshold=80)
        return auto

    def test_redelivered_attempt_skips_already_completed_units_and_pays_only_for_the_rest(
        self,
    ) -> None:
        # acks_late/reject_on_worker_lost may redeliver the whole task: a
        # unit already durably recorded from an earlier delivery must not
        # be re-judged (and re-paid for), while a unit that never reached
        # that point must still be processed exactly once (Task 5a, B4).
        translation = self.get_translation()
        done_unit = self.get_unit()
        pending_unit = self.get_unit(source="Thank you for using Weblate.")
        pending_unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.TRANSLATION,
            scope_id=str(translation.pk),
            scope_label=str(translation),
            scope_path=translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
        )
        JudgeRunUnit.objects.create(
            run=run,
            unit=done_unit,
            unit_id_snapshot=done_unit.id,
            translation_id=done_unit.translation_id,
            component_id=done_unit.translation.component_id,
            project_id=done_unit.translation.component.project_id,
            input_target=[],
            input_target_hash=compute_target_hash([]),
            context_hash="already-done",
            outcome=JudgeRunUnit.Outcome.PASSED,
        )
        seen_unit_ids: list[set[int]] = []

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            seen_unit_ids.append({unit.id for unit in units})
            # A PENDING placeholder must already be durably reserved for
            # every unit about to be judged before any provider call.
            self.assertEqual(
                set(
                    JudgeRunUnit.objects.filter(
                        run_id=run.pk if run else None,
                        outcome=JudgeRunUnit.Outcome.PENDING,
                    ).values_list("unit_id_snapshot", flat=True)
                ),
                {unit.id for unit in units},
            )
            out = {}
            for unit in units:
                request = build_request(unit)
                out[unit.id] = JudgeVerdict.objects.create(
                    unit=unit,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            return out

        auto = AutoTranslate(
            translation=translation,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[done_unit.id, pending_unit.id],
            producer_run=run,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.process_judge(engines=[], threshold=80)
        self.assertEqual(seen_unit_ids, [{pending_unit.id}])
        done_row = JudgeRunUnit.objects.get(run=run, unit_id_snapshot=done_unit.id)
        self.assertEqual(done_row.outcome, JudgeRunUnit.Outcome.PASSED)
        self.assertEqual(done_row.context_hash, "already-done")
        pending_row = JudgeRunUnit.objects.get(
            run=run, unit_id_snapshot=pending_unit.id
        )
        self.assertEqual(pending_row.outcome, JudgeRunUnit.Outcome.PASSED)

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.REJECT, severity="critical")
        self.assertIn(self.get_unit().state, FUZZY_STATES)

    def test_flag_ships_as_translated(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.FLAG, severity="major")
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)
        self.assertNotIn(self.get_unit().state, FUZZY_STATES)

    def test_unparsed_leaves_the_state_untouched(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        before = self.get_unit().state
        self.perform(JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(self.get_unit().state, before)

    def test_proposal_only_reject_preserves_live_target_and_state(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            judge_proposal_only=True,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch",
            side_effect=lambda units, **_kwargs: {
                candidate.id: JudgeVerdict.objects.create(
                    unit=candidate,
                    max_severity="critical",
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(candidate.get_target_plurals()),
                    context_hash=compute_context_hash(
                        source=candidate.source,
                        note=candidate.source_unit.note,
                        explanation=candidate.source_unit.explanation,
                        glossary_terms=[],
                    ),
                )
                for candidate in units
            },
        ):
            auto.process_judge(engines=[], threshold=80)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.target.strip(), "Human translation")
        self.assertEqual(refreshed.state, STATE_TRANSLATED)

    @override_settings(JUDGE_MAY_APPROVE=True)
    def test_unparsed_configured_seat_cannot_approve_a_pass(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            out = {}
            for candidate in units:
                request = build_request(candidate)
                kwargs = {
                    "unit": candidate,
                    "max_severity": "none",
                    "judge_model": "vendor-a/model",
                    "target_hash": compute_target_hash(request.target_plurals),
                    "context_hash": compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                }
                out[candidate.id] = JudgeVerdict.objects.create(
                    seat=1,
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    **kwargs,
                )
                JudgeVerdict.objects.create(
                    seat=2,
                    unparsed=True,
                    model_verdict=JudgeVerdict.Verdict.UNPARSED,
                    **kwargs,
                )
            return out

        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.process_judge(engines=[], threshold=80)

        self.assertNotEqual(self.get_unit().state, STATE_APPROVED)

    def test_pass_with_active_max_length_check_stays_fuzzy(self) -> None:
        unit = self.get_unit()
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"], same_content=True)
        unit.translate(
            self.user, ["a much longer over budget target"], STATE_TRANSLATED
        )
        self.perform(JudgeVerdict.Verdict.PASS)
        self.assertEqual(self.get_unit().state, STATE_FUZZY)

    def test_pass_without_active_max_length_reaches_translated(self) -> None:
        unit = self.get_unit()
        unit.extra_flags = "max-length:100"
        unit.save(update_fields=["extra_flags"], same_content=True)
        unit.translate(self.user, ["short target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.PASS)
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

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

    def test_a_zero_cap_processes_no_strings(self) -> None:
        with override_settings(JUDGE_MAX_UNITS_PER_RUN=0):
            auto = self.perform(JudgeVerdict.Verdict.PASS)
        self.assertIsNone(auto.failure_message)
        self.assertEqual(auto.judge_units_processed, 0)
        self.assertIn("0 evaluated", auto.get_message())

    def test_direct_empty_scope_reports_a_judge_summary(self) -> None:
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="context:does-not-exist",
            mode="judge",
        )

        auto.process_judge(engines=[], threshold=80)

        self.assertIn("0 evaluated", auto.get_message())

    def test_judge_summary_reports_verdict_buckets(self) -> None:
        auto = self.perform(JudgeVerdict.Verdict.PASS, judgeable=True)

        self.assertIn("evaluated", auto.get_message())
        self.assertIn("no blocking concern", auto.get_message())

    def test_empty_batch_scope_does_not_report_cap_exhaustion(self) -> None:
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="context:does-not-exist",
            mode="judge",
            enforce_permissions=False,
        )

        auto.perform(
            auto_source="mt",
            engines=[],
            threshold=80,
            source_component_ids=None,
        )

        self.assertFalse(
            any("cap was reached" in warning for warning in auto.get_warnings())
        )
        self.assertIn("0 evaluated", auto.get_message())

    def test_judge_run_records_empty_engine_warning(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            overwrite_existing=True,
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        with mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}):
            auto.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        warning = (
            "No machine translation engine was selected, so no strings were "
            "machine translated."
        )
        self.assertEqual(auto.get_warnings(), [warning])
        run = ProducerRun.objects.get()
        self.assertEqual(run.warnings, [warning])

    def test_standalone_mt_reports_failure_on_quota_refusal(self) -> None:
        """A confirmed provider refusal fails the run, with a durable warning."""
        from weblate.machinery.base import (  # ruff: ignore[import-outside-top-level]
            MachineTranslationServiceError,
        )
        from weblate.trans.machinery import (  # ruff: ignore[import-outside-top-level]
            MachineryBatchOutcome,
        )

        refusal = MachineTranslationServiceError(
            "quota exhausted",
            reason_code=MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
            safe_message="The quota is exhausted.",
        )
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="translate",
            unit_ids=[self.get_unit().id],
            enforce_permissions=False,
        )

        def fake_fetch(
            units,
            *,
            services,
            on_failure=None,
            **kwargs,
        ):
            if on_failure is not None:
                on_failure(
                    MachineryBatchOutcome(
                        status="failed",
                        service="OpenRouter",
                        unit_ids=tuple(unit.id for unit in units),
                        reason_code=refusal.reason_code,
                        error=refusal.safe_message,
                    )
                )
            return {}

        with mock.patch(
            "weblate.trans.autotranslate.fetch_machinery_matches",
            side_effect=fake_fetch,
        ):
            message = auto.perform(
                auto_source="mt",
                engines=["openrouter"],
                threshold=80,
                source_component_ids=None,
            )

        self.assertIn("Automatic translation failed", message)
        self.assertIn("quota", message)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertTrue(any("quota" in warning for warning in run.warnings))

    def test_untranslated_unit_is_skipped_before_judging(self) -> None:
        unit = self.get_unit()
        initial_auto_changes = unit.change_set.filter(action=ActionEvents.AUTO).count()
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        def fake_batch(units, **_kwargs):
            candidate = units[0]
            request = build_request(candidate)
            return {
                candidate.id: JudgeVerdict.objects.create(
                    unit=candidate,
                    max_severity="critical",
                    model_verdict=JudgeVerdict.Verdict.REJECT,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            }

        with (
            mock.patch.object(AutoTranslate, "process_mt"),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
            ) as run_batch,
        ):
            auto.perform(
                auto_source="others",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        run_batch.assert_not_called()
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_EMPTY)
        self.assertEqual(
            unit.change_set.filter(action=ActionEvents.AUTO).count(),
            initial_auto_changes,
        )
        self.assertFalse(JudgeVerdict.objects.filter(unit=unit).exists())
        self.assertEqual(auto.judge_summary.untranslated, 1)
        self.assertIn("no translation to judge", auto.get_message())
        row = JudgeRunUnit.objects.get(unit_id_snapshot=unit.id)
        self.assertEqual(row.outcome, JudgeRunUnit.Outcome.SKIPPED)
        self.assertEqual(row.skip_reason, JudgeRunUnit.SkipReason.UNTRANSLATED)

    def test_unparsed_is_counted_in_the_warnings(self) -> None:
        auto = self.perform(JudgeVerdict.Verdict.UNPARSED, judgeable=True)
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
        unit1 = translations[0].unit_set.first()
        unit2 = translations[1].unit_set.first()
        assert unit1 is not None
        assert unit2 is not None
        unit1.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        unit2.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit1.id, unit2.id],
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

    def test_major_candidate_passes_through_the_operator_path(self) -> None:
        # A flagged operator round generates exactly one candidate; the
        # generated repair is itself re-judged before being stored as a
        # preview (Task 3, B2), and the live target/state stay untouched
        # for the producer to accept.
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
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
        candidate_passes = JudgeResult("none", "pass", [], "")
        results = iter([[major], [major], [candidate_passes], [candidate_passes]])

        def request(requests, *, on_batch, **kwargs):
            batch_results = next(results)
            on_batch(requests, batch_results)
            return batch_results

        client = mock.Mock(side_effect=request)
        with (
            mock.patch.object(auto, "process_mt"),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["repaired translation"]},
            ),
        ):
            auto.process_judge(engines=[], threshold=80)
        stored = self.get_unit()
        self.assertEqual(stored.target.strip(), "existing translation")
        self.assertEqual(stored.state, STATE_TRANSLATED)
        self.assertEqual(
            stored.judge_verdicts.filter(subject=JudgeVerdict.Subject.LIVE)
            .latest("pk")
            .verdict,
            JudgeVerdict.Verdict.FLAG,
        )
        # 2 seats for the operator round, then 2 more verifying the
        # generated candidate before it is offered to the producer.
        self.assertEqual(client.call_count, 4)
        self.assertEqual(
            stored.suggestion_set.get(userdetails__kind="judge-repair").target.strip(),
            "repaired translation",
        )

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

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
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
                    explanation=request.explanation,
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

    def test_judge_progress_never_goes_backwards_across_both_phases(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            overwrite_existing=True,
        )
        auto.progress_range = (20, 40)
        reported: list[int] = []

        def pretranslate(*_args, **_kwargs) -> None:
            auto.progress_steps = 1
            auto.set_progress(1)

        def fake_judge(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            if on_batch is not None:
                on_batch([object()], [object()])
            return {}

        with (
            mock.patch("weblate.trans.autotranslate.current_task") as task,
            mock.patch.object(auto, "process_mt", side_effect=pretranslate),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge,
            ),
        ):
            task.request.id = "test-task-id"
            task.update_state.side_effect = lambda **kwargs: reported.append(
                kwargs["meta"]["progress"]
            )
            auto.process_judge(engines=[], threshold=80)

        self.assertEqual(reported, sorted(reported))
        self.assertLessEqual(reported[0], 22)
        self.assertTrue(any(22 < progress < 40 for progress in reported))

    @override_settings(JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_judge_progress_keeps_moving_through_a_repair_round(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
        )
        auto.progress_range = (0, 100)
        reported: list[int] = []

        def fake_judge(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            # One string, two seats, one repair attempt: four batches, which a
            # denominator sized to a single round would clamp into a plateau.
            for _ in range(2 * (settings.JUDGE_MAX_REPAIR_ATTEMPTS + 1)):
                on_batch([object()], [object()])
            return {}

        with (
            mock.patch("weblate.trans.autotranslate.current_task") as task,
            mock.patch.object(auto, "process_mt"),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge,
            ),
        ):
            task.request.id = "test-task-id"
            task.update_state.side_effect = lambda **kwargs: reported.append(
                kwargs["meta"]["progress"]
            )
            auto.process_judge(engines=[], threshold=80)

        # Four distinct rising values, not one plateau at the phase maximum:
        # a denominator of one round reports 55, 100, 100, 100 instead.
        self.assertEqual(len(reported), 4)
        self.assertEqual(reported, sorted(set(reported)))
        self.assertEqual(reported[-1], 100)

    def test_judge_summary_counts_severity_buckets(self) -> None:
        unit = self.get_unit()
        auto = self.perform(
            JudgeVerdict.Verdict.FLAG,
            severity="major",
            unit_ids=[unit.id],
            judgeable=True,
        )
        self.assertEqual(auto.judge_summary.major_not_fixed, 1)
        self.assertEqual(auto.judge_summary.nothing_blocking, 0)
        self.assertIn("major not fixed", auto.get_message())

        auto = self.perform(
            JudgeVerdict.Verdict.REJECT,
            severity="critical",
            unit_ids=[unit.id],
            judgeable=True,
        )
        self.assertEqual(auto.judge_summary.critical_held, 1)
        self.assertIn("critical held", auto.get_message())

        auto = self.perform(
            JudgeVerdict.Verdict.PASS,
            severity="minor",
            unit_ids=[unit.id],
            judgeable=True,
        )
        self.assertEqual(auto.judge_summary.minor_noted, 1)
        self.assertIn("minor noted", auto.get_message())

    def test_judge_summary_counts_unparsed_strings(self) -> None:
        unit = self.get_unit()
        auto = self.perform(
            JudgeVerdict.Verdict.UNPARSED, unit_ids=[unit.id], judgeable=True
        )
        self.assertEqual(auto.judge_summary.unparsed, 1)
        self.assertIn("1 unparsed", auto.get_message())

    def test_judge_summary_counts_repaired_and_rejudged_strings(self) -> None:
        unit = self.get_unit()
        auto = self.perform(
            JudgeVerdict.Verdict.PASS,
            attempt=1,
            unit_ids=[unit.id],
            judgeable=True,
        )
        self.assertEqual(auto.judge_summary.repaired, 1)
        self.assertIn("repaired and re-judged", auto.get_message())

        auto = self.perform(
            JudgeVerdict.Verdict.PASS,
            attempt=0,
            unit_ids=[unit.id],
            judgeable=True,
        )
        self.assertEqual(auto.judge_summary.repaired, 0)
        self.assertNotIn("repaired and re-judged", auto.get_message())

    def test_cache_only_run_still_summarizes_verdicts(self) -> None:
        """fake_batch never calls on_batch, exactly like an all-cached run."""
        unit = self.get_unit()
        auto = self.perform(
            JudgeVerdict.Verdict.PASS, unit_ids=[unit.id], judgeable=True
        )
        self.assertEqual(auto.judge_summary.evaluated, 1)
        self.assertEqual(auto.judge_summary.nothing_blocking, 1)

    def test_judge_summary_reports_cap_remainder(self) -> None:
        baseline = self.perform(JudgeVerdict.Verdict.PASS, judgeable=True)
        total_matched = baseline.judge_units_matched
        if total_matched < 2:
            self.skipTest("fixture needs at least two units")
        with override_settings(JUDGE_MAX_UNITS_PER_RUN=1):
            auto = self.perform(JudgeVerdict.Verdict.PASS, judgeable=True)
        self.assertEqual(auto.judge_summary.cap_remainder, total_matched - 1)
        self.assertIn("remain because of the per-run cap", auto.get_message())

    def test_batch_summary_aggregates_across_translations(self) -> None:
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        unit1 = translations[0].unit_set.first()
        unit2 = translations[1].unit_set.first()
        assert unit1 is not None
        assert unit2 is not None
        unit1.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        unit2.translate(self.user, ["Judgeable target"], STATE_EMPTY)

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            out = {}
            for u in units:
                request = build_request(u)
                out[u.id] = JudgeVerdict.objects.create(
                    unit=u,
                    max_severity="major",
                    model_verdict=JudgeVerdict.Verdict.FLAG,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            return out

        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit1.id, unit2.id],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            auto.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        assert auto.judge_summary is not None
        self.assertEqual(auto.judge_summary.evaluated, 2)
        self.assertEqual(auto.judge_summary.major_not_fixed, 2)
        self.assertIn("major not fixed", auto.get_message())

    def test_project_cap_summary_counts_later_skipped_translations(self) -> None:
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        unit1 = translations[0].unit_set.first()
        unit2 = translations[1].unit_set.first()
        assert unit1 is not None
        assert unit2 is not None
        auto = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit1.id, unit2.id],
            enforce_permissions=False,
        )

        with (
            override_settings(JUDGE_MAX_UNITS_PER_RUN=1),
            mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}),
        ):
            auto.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        assert auto.judge_summary is not None
        self.assertEqual(auto.judge_summary.cap_remainder, 1)

    def run_batch_with_first_translation_failure(self, cap: int):
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        unit1 = translations[0].unit_set.first()
        unit2 = translations[1].unit_set.first()
        assert unit1 is not None
        assert unit2 is not None
        unit1.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        unit2.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        calls: list[list[int]] = []

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            calls.append([unit.id for unit in units])
            if units[0].id == unit1.id:
                msg = "boom"
                raise JudgeError(msg)
            out = {}
            for unit in units:
                request = build_request(unit)
                out[unit.id] = JudgeVerdict.objects.create(
                    unit=unit,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            return out

        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit1.id, unit2.id],
            enforce_permissions=False,
        )
        with (
            override_settings(JUDGE_MAX_UNITS_PER_RUN=cap),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
            ),
        ):
            auto.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        return auto, calls, unit1, unit2

    def test_batch_summary_survives_one_failing_translation(self) -> None:
        auto, calls, unit1, unit2 = self.run_batch_with_first_translation_failure(2)

        assert auto.judge_summary is not None
        self.assertEqual(auto.judge_summary.evaluated, 1)
        self.assertTrue(
            any("Automatic translation failed" in warning for warning in auto.warnings),
            auto.warnings,
        )
        self.assertEqual(calls, [[unit1.id], [unit2.id]])

    def test_failed_translation_consumes_selected_cap(self) -> None:
        auto, calls, unit1, _unit2 = self.run_batch_with_first_translation_failure(1)

        assert auto.judge_summary is not None
        self.assertEqual(auto.judge_summary.evaluated, 0)
        self.assertEqual(calls, [[unit1.id]])

    def test_refused_request_fails_the_run_without_a_fake_verdict(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            out = {}
            for candidate in units:
                request = build_request(candidate)
                # The peer seat answered before the other seat was refused.
                out[candidate.id] = JudgeVerdict.objects.create(
                    unit=candidate,
                    seat=2,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-b/model",
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            msg = "The LLM judge endpoint refused the request (HTTP 400)."
            raise JudgeError(msg)

        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            message = batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        self.assertIn("refused the request (HTTP 400)", message)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertIn("refused the request (HTTP 400)", run.failure)
        # The refusal itself contributes no unparsed verdict.
        self.assertEqual(
            JudgeVerdict.objects.filter(unit=unit, unparsed=True).count(), 0
        )
        # The peer row survives, but one seat is not a two-seat decision.
        self.assertEqual(JudgeVerdict.objects.filter(unit=unit).count(), 1)
        self.assertFalse(
            has_complete_current_evidence(unit, seats=(1, 2)),
            "a single surviving seat must not read as complete evidence",
        )
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

    @override_settings(JUDGE_MAX_REPAIR_ATTEMPTS=0)
    def test_judge_progress_reports_judging_phase(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
        )
        auto.progress_range = (0, 100)
        reported: list[tuple[str | None, int | None, int | None]] = []

        def fake_judge(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            for _ in range(2):
                on_batch([object()], [object()])
            return {}

        with (
            mock.patch("weblate.trans.autotranslate.current_task") as task,
            mock.patch.object(auto, "process_mt"),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge,
            ),
        ):
            task.request.id = "test-task-id"
            task.update_state.side_effect = lambda **kwargs: reported.append(
                (
                    kwargs["meta"].get("phase"),
                    kwargs["meta"].get("phase_current"),
                    kwargs["meta"].get("phase_total"),
                )
            )
            auto.process_judge(engines=[], threshold=80)

        self.assertEqual(reported, [("judging", 1, 2), ("judging", 2, 2)])

    @override_settings(JUDGE_MAX_REPAIR_ATTEMPTS=1)
    def test_judge_progress_reports_repairing_phase_after_the_first_round(
        self,
    ) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
        )
        auto.progress_range = (0, 100)
        reported: list[tuple[str | None, int | None, int | None]] = []

        def fake_judge(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            for _ in range(2 * (settings.JUDGE_MAX_REPAIR_ATTEMPTS + 1)):
                on_batch([object()], [object()])
            return {}

        with (
            mock.patch("weblate.trans.autotranslate.current_task") as task,
            mock.patch.object(auto, "process_mt"),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=fake_judge,
            ),
        ):
            task.request.id = "test-task-id"
            task.update_state.side_effect = lambda **kwargs: reported.append(
                (
                    kwargs["meta"].get("phase"),
                    kwargs["meta"].get("phase_current"),
                    kwargs["meta"].get("phase_total"),
                )
            )
            auto.process_judge(engines=[], threshold=80)

        self.assertEqual(
            reported,
            [
                ("judging", 1, 2),
                ("judging", 2, 2),
                ("repairing", 1, 2),
                ("repairing", 2, 2),
            ],
        )

    def test_non_judge_mode_completion_copy_is_unchanged(self) -> None:
        unit = self.get_unit()
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="translate",
            unit_ids=[unit.id],
        )
        auto.update(unit, STATE_TRANSLATED, ["some target"])
        self.assertIsNone(auto.judge_summary)
        message = auto.get_message()
        self.assertNotIn("evaluated", message)
        self.assertIn("string was updated", message)

    def test_project_launch_records_one_run_across_translations(self) -> None:
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        units = [translation.unit_set.first() for translation in translations]
        self.assertTrue(all(units))

        seen_runs: list[ProducerRun | None] = []

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            seen_runs.append(run)
            verdicts = {}
            for unit in units:
                request = build_request(unit)
                verdicts[unit.id] = JudgeVerdict.objects.create(
                    unit=unit,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            return verdicts

        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id for unit in units if unit is not None],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        run = ProducerRun.objects.get()
        self.assertTrue(all(seen_run == run for seen_run in seen_runs))
        self.assertEqual(
            set(JudgeRunUnit.objects.filter(run=run).values_list("unit_id", flat=True)),
            {unit.id for unit in units if unit is not None},
        )

    @override_settings(JUDGE_MAX_UNITS_PER_RUN=1)
    def test_capped_units_record_a_cap_skip(self) -> None:
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        units = [translation.unit_set.first() for translation in translations]
        self.assertTrue(all(units))

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            unit = units[0]
            request = build_request(unit)
            return {
                unit.id: JudgeVerdict.objects.create(
                    unit=unit,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            }

        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id for unit in units if unit is not None],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        self.assertEqual(
            JudgeRunUnit.objects.get(unit_id_snapshot=units[1].id).skip_reason,
            JudgeRunUnit.SkipReason.CAP,
        )

    def test_task_exception_marks_the_run_failed(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        with (
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch",
                side_effect=RuntimeError("boom"),
            ),
            self.assertRaisesRegex(RuntimeError, "boom"),
        ):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertIsNotNone(run.finished)
        self.assertEqual(run.failure, "boom")

    def test_target_race_records_stale_conflict(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["original"], STATE_TRANSLATED)

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
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
                    explanation=request.explanation,
                    glossary_terms=request.glossary_terms,
                ),
            )
            current.translate(self.user, ["changed"], STATE_TRANSLATED)
            return {current.id: verdict}

        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        self.assertEqual(
            JudgeRunUnit.objects.get(unit_id_snapshot=unit.id).outcome,
            JudgeRunUnit.Outcome.STALE_CONFLICT,
        )

    def test_run_records_the_real_actor_and_scope_not_request_data(self) -> None:
        """
        JudgeRun.actor/scope come only from constructor objects.

        BatchAutoTranslate takes a typed ``user: User | None`` and a typed
        ``obj: Translation | Component | ... | Workspace``, both resolved by
        the caller (the view resolves the scope from the URL path and the
        user from the authenticated session, never from POST body fields -
        see weblate/trans/views/edit.py, auto_translate.delay(user_id=
        request.user.id, ...)). There is no field on this boundary a
        request body could use to override them.
        """
        unit = self.get_unit()
        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run = ProducerRun.objects.get()
        self.assertEqual(run.actor_id, self.user.pk)
        self.assertEqual(run.scope_type, ProducerRun.ScopeType.PROJECT)
        self.assertEqual(run.scope_id, str(self.project.pk))
        self.assertEqual(run.scope_label, str(self.project))
        self.assertEqual(run.scope_path, self.project.get_absolute_url())

    def test_redelivered_task_reuses_its_ui_run(self) -> None:
        unit = self.get_unit()
        first = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        second = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        task = mock.MagicMock()
        task.request.id = "redelivered-task"

        with mock.patch("weblate.trans.autotranslate.current_task", task):
            first_run = first._create_producer_run()  # ruff: ignore[private-member-access]
            second_run = second._create_producer_run()  # ruff: ignore[private-member-access]

        self.assertEqual(first_run.pk, second_run.pk)
        self.assertEqual(ProducerRun.objects.count(), 1)

    def test_terminal_redelivery_adopts_run_read_only(self) -> None:
        unit = self.get_unit()
        task = mock.MagicMock()
        task.request.id = "terminal-redelivery"
        original = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch("weblate.trans.autotranslate.current_task", task):
            run = original._create_producer_run()  # ruff: ignore[private-member-access]
        run.status = ProducerRun.Status.COMPLETED
        run.finished = run.started
        run.summary = {"passed": 1}
        run.save(update_fields=["status", "finished", "summary"])
        terminal_finished = run.finished
        replay = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
            producer_run_id=str(run.pk),
        )

        with mock.patch("weblate.trans.autotranslate.current_task", task):
            adopted = replay._adopt_producer_run()  # ruff: ignore[private-member-access]

        self.assertEqual(adopted.pk, run.pk)
        self.assertEqual(adopted.summary, {"passed": 1})

        with (
            mock.patch("weblate.trans.autotranslate.current_task", task),
            mock.patch("weblate.trans.autotranslate.run_judge_batch") as judge,
        ):
            replay.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )

        judge.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.finished, terminal_finished)
        self.assertEqual(run.summary, {"passed": 1})

    def test_finish_producer_run_does_not_overwrite_a_terminal_run(self) -> None:
        unit = self.get_unit()
        # The mandatory barrier refuses to judge an empty string when no
        # engine can prepare it; this test is about double finalization, so
        # its scope starts fully translated.
        unit.translate(self.user, ["Judgeable target"], STATE_TRANSLATED)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        first_finished = run.finished

        # A second finalize call (a redundant exception handler, a stray
        # retry) must never overwrite an already-terminal run.
        batch._finish_producer_run(  # ruff: ignore[private-member-access]
            run, ProducerRun.Status.FAILED, "should not apply"
        )
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertEqual(run.finished, first_finished)
        self.assertEqual(run.failure, "")

    def test_finish_preserves_a_requested_cancellation(self) -> None:
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            enforce_permissions=False,
        )
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=1,
            status=ProducerRun.Status.CANCEL_REQUESTED,
        )

        batch._finish_producer_run(  # ruff: ignore[private-member-access]
            run, ProducerRun.Status.COMPLETED
        )

        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.CANCELLED)

    def test_finish_maps_a_requested_cancellation_to_partial_with_completed_rows(
        self,
    ) -> None:
        unit = self.get_unit()
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            enforce_permissions=False,
        )
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=1,
            status=ProducerRun.Status.CANCEL_REQUESTED,
        )
        JudgeRunUnit.objects.create(
            run=run,
            unit=unit,
            unit_id_snapshot=unit.id,
            translation_id=unit.translation_id,
            component_id=unit.translation.component_id,
            project_id=unit.translation.component.project_id,
            outcome=JudgeRunUnit.Outcome.PASSED,
        )

        batch._finish_producer_run(  # ruff: ignore[private-member-access]
            run, ProducerRun.Status.COMPLETED
        )

        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.PARTIAL)

    def test_record_skipped_judge_units_is_idempotent_on_retry(self) -> None:
        unit = self.get_unit()
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        run = batch._create_producer_run()  # ruff: ignore[private-member-access]
        # Simulate a retried step recording the same skip twice: this must
        # update the one (run, unit) row, never insert a second one.
        batch._record_skipped_judge_units(  # ruff: ignore[private-member-access]
            run, [unit], JudgeRunUnit.SkipReason.CAP
        )
        batch._record_skipped_judge_units(  # ruff: ignore[private-member-access]
            run, [unit], JudgeRunUnit.SkipReason.CAP
        )
        self.assertEqual(
            JudgeRunUnit.objects.filter(run=run, unit_id_snapshot=unit.id).count(), 1
        )

    def test_duplicate_run_unit_row_is_rejected_at_the_database(self) -> None:
        unit = self.get_unit()
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        run = batch._create_producer_run()  # ruff: ignore[private-member-access]
        JudgeRunUnit.objects.create(
            run=run,
            unit=unit,
            unit_id_snapshot=unit.id,
            translation_id=unit.translation_id,
            component_id=unit.translation.component_id,
            project_id=unit.translation.component.project_id,
            input_target=[],
            input_target_hash=compute_target_hash([]),
            context_hash="x",
            outcome=JudgeRunUnit.Outcome.SKIPPED,
        )
        with self.assertRaises(IntegrityError):
            JudgeRunUnit.objects.create(
                run=run,
                unit=unit,
                unit_id_snapshot=unit.id,
                translation_id=unit.translation_id,
                component_id=unit.translation.component_id,
                project_id=unit.translation.component.project_id,
                input_target=[],
                input_target_hash=compute_target_hash([]),
                context_hash="x",
                outcome=JudgeRunUnit.Outcome.SKIPPED,
            )

    def test_deleted_unit_and_verdict_leave_a_safe_dangling_row(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_TRANSLATED)

        def fake_batch(units, *, writable_ids, user, on_batch=None, run=None, **kwargs):
            current = units[0]
            request = build_request(current)
            return {
                current.id: JudgeVerdict.objects.create(
                    unit=current,
                    max_severity="none",
                    model_verdict=JudgeVerdict.Verdict.PASS,
                    judge_model="vendor-a/model",
                    seat=1,
                    target_hash=compute_target_hash(request.target_plurals),
                    context_hash=compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                    ),
                )
            }

        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", side_effect=fake_batch
        ):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        row = JudgeRunUnit.objects.get(unit_id_snapshot=unit.id)
        self.assertIsNotNone(row.unit)
        self.assertIsNotNone(row.verdict)

        JudgeVerdict.objects.filter(pk=row.verdict_id).delete()
        Unit.objects.filter(pk=unit.id).delete()

        row.refresh_from_db()
        self.assertIsNone(row.unit)
        self.assertIsNone(row.verdict)
        self.assertEqual(row.unit_id_snapshot, unit.id)

    # -- Task 3: producer one-unit re-check -------------------------------

    def _make_queued_recheck_run(self, unit, *, query=None, status=None):
        translation = unit.translation
        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.TRANSLATION,
            scope_id=str(translation.pk),
            scope_label=str(translation),
            scope_path=translation.get_absolute_url(),
            requested_query=query or recheck_query(unit.pk),
            requested_mode="recheck",
            cap=1,
            status=status or ProducerRun.Status.QUEUED,
            configuration_snapshot={},
        )

    def test_recheck_skips_pretranslation_and_mutating_path(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Existing translation"], STATE_TRANSLATED)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            overwrite_existing=True,
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch.object(auto, "process_mt") as mt,
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run,
        ):
            auto.process_judge(engines=[], threshold=80)
        mt.assert_not_called()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["writable_ids"], set())
        self.assertIs(kwargs["mutating_repairs"], False)
        self.assertEqual(kwargs["candidate_severities"], ("critical",))
        self.assertEqual(self.get_unit().target.strip(), "Existing translation")

    def test_recheck_pass_projects_without_any_repair(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Good translation"], STATE_TRANSLATED)
        passed = JudgeResult("none", "pass", [], "")
        client = mock.Mock(
            side_effect=lambda requests, *, on_batch, **_kwargs: (
                on_batch(requests, [passed]),
                [passed],
            )[-1]
        )
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets") as repair,
        ):
            auto.process_judge(engines=[], threshold=80)
        self.assertEqual(client.call_count, 2)
        repair.assert_not_called()
        refreshed = self.get_unit()
        self.assertEqual(refreshed.target.strip(), "Good translation")
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        self.assertEqual(refreshed.suggestion_set.count(), 0)

    def test_recheck_major_projects_directly_without_a_candidate(self) -> None:
        # The re-check's candidate set is critical-only: an admissible major
        # projects to its release state and pays no repair MT call.
        unit = self.get_unit()
        unit.translate(self.user, ["Tolerable translation"], STATE_TRANSLATED)
        major = JudgeResult("major", "flag", [], "")
        client = mock.Mock(
            side_effect=lambda requests, *, on_batch, **_kwargs: (
                on_batch(requests, [major]),
                [major],
            )[-1]
        )
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets") as repair,
        ):
            auto.process_judge(engines=[], threshold=80)
        self.assertEqual(client.call_count, 2)
        repair.assert_not_called()
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        self.assertEqual(refreshed.suggestion_set.count(), 0)

    def test_recheck_critical_holds_target_and_stores_one_candidate(self) -> None:
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        unit = self.get_unit()
        unit.translate(self.user, ["Failing translation"], STATE_TRANSLATED)
        critical = JudgeResult("critical", "reject", [], "")
        client = mock.Mock(
            side_effect=lambda requests, *, on_batch, **_kwargs: (
                on_batch(requests, [critical]),
                [critical],
            )[-1]
        )
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.pk: ["Better translation"]},
            ) as repair,
        ):
            auto.process_judge(engines=[], threshold=80)
        # 2 seats for the recheck round, then 2 more verifying the
        # generated candidate before it is offered to the producer.
        self.assertEqual(client.call_count, 4)
        self.assertEqual(repair.call_count, 1)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.target.strip(), "Failing translation")
        self.assertEqual(refreshed.state, STATE_FUZZY)
        self.assertEqual(
            refreshed.suggestion_set.get(
                userdetails__kind="judge-repair"
            ).target.strip(),
            "Better translation",
        )

    def test_recheck_disputed_critical_projects_as_major_without_candidate(
        self,
    ) -> None:
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        unit = self.get_unit()
        unit.translate(self.user, ["Tolerable translation"], STATE_TRANSLATED)
        critical = JudgeResult("critical", "reject", [], "")
        minor = JudgeResult("minor", "pass", [], "")
        client = mock.Mock(
            side_effect=lambda requests, *, on_batch, seat, **_kwargs: (
                on_batch(requests, [critical if seat == 1 else minor]),
                [critical if seat == 1 else minor],
            )[-1]
        )
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.pk: ["Better translation"]},
            ) as repair,
        ):
            auto.process_judge(engines=[], threshold=80)
        self.assertEqual(client.call_count, 2)
        repair.assert_not_called()
        self.assertEqual(auto.judge_summary.major_not_fixed, 1)
        self.assertEqual(auto.judge_summary.critical_held, 0)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        self.assertEqual(refreshed.suggestion_set.count(), 0)

    @override_settings(JUDGE_CONSENSUS_REJECT=False)
    def test_recheck_disputed_critical_holds_target_in_rollback_mode(self) -> None:
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        unit = self.get_unit()
        unit.translate(self.user, ["Tolerable translation"], STATE_TRANSLATED)
        critical = JudgeResult("critical", "reject", [], "")
        minor = JudgeResult("minor", "pass", [], "")
        client = mock.Mock(
            side_effect=lambda requests, *, on_batch, seat, **_kwargs: (
                on_batch(requests, [critical if seat == 1 else minor]),
                [critical if seat == 1 else minor],
            )[-1]
        )
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.pk: ["Better translation"]},
            ) as repair,
        ):
            auto.process_judge(engines=[], threshold=80)
        # 2 seats for the recheck round, then 2 more verifying the
        # generated candidate before it is offered to the producer.
        self.assertEqual(client.call_count, 4)
        repair.assert_called_once()
        self.assertEqual(auto.judge_summary.major_not_fixed, 0)
        self.assertEqual(auto.judge_summary.critical_held, 1)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        self.assertEqual(refreshed.suggestion_set.count(), 1)

    def test_worker_adopts_the_queued_recheck_run(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Judgeable target"], STATE_TRANSLATED)
        run = self._make_queued_recheck_run(unit)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            enforce_permissions=False,
            producer_run_id=str(run.pk),
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_candidate_severities=(JudgeVerdict.Severity.CRITICAL,),
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", return_value={}
        ) as run_batch:
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertIsNotNone(run.finished)
        # The batch received the adopted run, not a newly created one.
        self.assertEqual(run_batch.call_args.kwargs["run"].pk, run.pk)
        self.assertEqual(
            ProducerRun.objects.filter(requested_mode="recheck").count(), 1
        )

    def test_worker_adopts_the_queued_project_judge_run(self) -> None:
        project = self.component.project
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.PROJECT,
            scope_id=str(project.pk),
            scope_label=str(project),
            scope_path=project.get_absolute_url(),
            requested_query="",
            requested_mode="judge",
            cap=10,
            status=ProducerRun.Status.QUEUED,
            configuration_snapshot=judge_configuration_snapshot(),
        )
        # A dispatched bulk judge run prepares its closed scope before the
        # judges start, and an empty string with no configured engine is a
        # blocker. This test is about adopting the pre-created row, so every
        # string in scope already has text.
        scope_units = Unit.objects.filter(
            translation__component__project=project
        ).select_related("translation")
        for scope_unit in scope_units:
            if scope_unit.translation.is_source:
                continue
            if not any(scope_unit.get_target_plurals()):
                forms = (
                    scope_unit.translation.plural.number if scope_unit.is_plural else 1
                )
                scope_unit.translate(
                    self.user, ["Judgeable target"] * forms, STATE_TRANSLATED
                )
        batch = BatchAutoTranslate(
            project,
            user=self.user,
            q="",
            mode="judge",
            enforce_permissions=False,
            producer_run_id=str(run.pk),
            judge_proposal_only=True,
            judge_pretranslate=False,
            judge_mutating_repairs=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", return_value={}
        ) as run_batch:
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED, run.failure)
        # The batch received the pre-created estimate row, not a new one.
        self.assertEqual(ProducerRun.objects.count(), 1)
        if run_batch.called:
            self.assertEqual(run_batch.call_args.kwargs["run"].pk, run.pk)

    def test_worker_refuses_a_run_that_is_not_queued(self) -> None:
        unit = self.get_unit()
        run = self._make_queued_recheck_run(unit, status=ProducerRun.Status.RUNNING)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q=recheck_query(unit.pk),
            mode="judge",
            unit_ids=[unit.pk],
            enforce_permissions=False,
            producer_run_id=str(run.pk),
            judge_pretranslate=False,
        )
        with self.assertRaises(ValueError):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run.refresh_from_db()
        # Someone else's RUNNING run must not be failed by this worker.
        self.assertEqual(run.status, ProducerRun.Status.RUNNING)

    def _enable_openrouter(self) -> None:
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])

    def test_preparation_fills_missing_then_judges(self) -> None:
        """Missing strings are machine-filled before the first judge call."""
        from weblate.machinery.models import (  # ruff: ignore[import-outside-top-level]
            MACHINERY,
        )

        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        engine = MACHINERY["openrouter"]({"key": "test"})
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            origin = services[0] if services else engine
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["Pre-filled"],
                    "quality": [90],
                    "origin": [origin],
                }
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        unit.refresh_from_db()
        self.assertEqual(unit.target.strip(), "Pre-filled")
        self.assertTrue(run_batch.called)
        run = ProducerRun.objects.get()
        self.assertEqual(run.preparation_phase, "ready")

    def test_dispatched_bulk_judge_prepares_before_judging(self) -> None:
        """
        A producer-dispatched run fills missing strings before any judge call.

        The dispatcher sets ``judge_proposal_only=True``, which only says the
        judge's own verdicts must not mutate state; it must not turn the
        mandatory machine-translation barrier off (C3).
        """
        from weblate.machinery.models import (  # ruff: ignore[import-outside-top-level]
            MACHINERY,
        )

        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.PROJECT,
            scope_id=str(self.project.pk),
            scope_label=str(self.project),
            scope_path=self.project.get_absolute_url(),
            requested_query="",
            requested_mode="judge",
            cap=1,
            status=ProducerRun.Status.QUEUED,
            dispatch_phase="judge-project",
            dispatch_task_id=uuid4(),
            execution_version=1,
            scope_cursor=0,
            scope_snapshot=[unit.pk],
            preparation_snapshot=PreparationScope(
                unit_ids=(unit.pk,),
                missing_ids=(unit.pk,),
                per_language_missing={unit.translation.language.code: 1},
                mt_engine="openrouter",
            ).to_json(),
            preparation_phase="pending",
        )
        engine = MACHINERY["openrouter"]({"key": "test"})

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            origin = services[0] if services else engine
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["Pre-filled"],
                    "quality": [90],
                    "origin": [origin],
                }
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.pk],
            producer_run_id=str(run.pk),
            judge_pretranslate=False,
            judge_mutating_repairs=False,
            judge_proposal_only=True,
            enforce_permissions=False,
        )
        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ) as fetch,
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        self.assertTrue(fetch.called, "the mandatory preparation never ran")
        unit.refresh_from_db()
        self.assertEqual(unit.target.strip(), "Pre-filled")
        self.assertTrue(run_batch.called)
        run.refresh_from_db()
        self.assertEqual(run.preparation_phase, "ready")
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)

    def test_missing_strings_without_an_engine_block_the_barrier(self) -> None:
        """Missing strings and no engine must fail, not report a ready scope."""
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch", return_value={}
        ) as run_batch:
            message = batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        run_batch.assert_not_called()
        self.assertIn("Judges were not started", message)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.preparation_phase, "blocked")
        self.assertEqual(run.summary["mt_preparation"]["phase"], "blocked")
        self.assertFalse(JudgeVerdict.objects.exists())

    @override_settings(JUDGE_BATCH_SIZE=1)
    def test_scope_changed_after_the_barrier_fails_the_run(self) -> None:
        """
        C3 scenario 7: a string emptied before its batch stops the run.

        The per-batch polling itself is covered by
        ``weblate/trans/tests/test_judge_client.py``; this asserts the batch
        side: the guard trips, the reason reaches the run and no judge call is
        sent for the emptied string.
        """
        first, second = self.get_translation().unit_set.order_by("position", "pk")[:2]
        for unit in (first, second):
            forms = unit.translation.plural.number if unit.is_plural else 1
            unit.translate(self.user, ["Judgeable target"] * forms, STATE_TRANSLATED)
        judged: list[int | None] = []

        def fake_run_judge_batch(units, *, scope_guard=None, **kwargs):
            # The barrier passed; the next string loses its text before its
            # own HTTP batch is formed.
            empty_forms = second.translation.plural.number if second.is_plural else 1
            second.translate(self.user, [""] * empty_forms, STATE_EMPTY)
            request = build_request(second)
            self.assertTrue(
                scope_guard([request]), "the guard missed the emptied string"
            )
            judged.append(request.unit_id)
            return {}

        batch = BatchAutoTranslate(
            self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[first.pk, second.pk],
            enforce_permissions=False,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_batch",
            side_effect=fake_run_judge_batch,
        ):
            message = batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        self.assertIn("scope changed", message)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED, run.failure)
        self.assertIn("scope changed", run.failure)
        self.assertTrue(any("scope changed" in warning for warning in run.warnings))
        self.assertEqual(judged, [second.pk])

    def test_preparation_covers_a_string_beyond_the_judge_cap(self) -> None:
        """C1: the judge cap narrows the queue, never the prepared volume."""
        from weblate.machinery.models import (  # ruff: ignore[import-outside-top-level]
            MACHINERY,
        )

        self._enable_openrouter()
        translations = list(
            self.component.translation_set.exclude_source().order_by("pk")[:2]
        )
        if len(translations) < 2:
            self.skipTest("fixture has only one target translation")
        inside, beyond = (translation.unit_set.first() for translation in translations)
        assert inside is not None
        assert beyond is not None
        inside.translate(self.user, ["Judgeable target"], STATE_TRANSLATED)
        beyond.translate(self.user, [""], STATE_EMPTY)
        engine = MACHINERY["openrouter"]({"key": "test"})
        prepared: list[int] = []

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            origin = services[0] if services else engine
            for mt_unit in units:
                prepared.append(mt_unit.id)
                mt_unit.machinery = {
                    "translation": ["Pre-filled"],
                    "quality": [90],
                    "origin": [origin],
                }
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[inside.id, beyond.id],
            enforce_permissions=False,
        )
        with (
            override_settings(JUDGE_MAX_UNITS_PER_RUN=1),
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        # The string past the judge cap is prepared even though it is never
        # sent to the judge.
        self.assertIn(beyond.id, prepared)
        beyond.refresh_from_db()
        self.assertEqual(beyond.target.strip(), "Pre-filled")
        judged_ids = [
            unit.id for call in run_batch.call_args_list for unit in call.args[0]
        ]
        self.assertNotIn(beyond.id, judged_ids)

    def test_quota_refusal_blocks_judge_globally(self) -> None:
        """A quota refusal on one language stops the judge phase everywhere."""
        from weblate.machinery.base import (  # ruff: ignore[import-outside-top-level]
            MachineTranslationServiceError,
        )
        from weblate.trans.machinery import (  # ruff: ignore[import-outside-top-level]
            MachineryBatchOutcome,
        )

        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            if on_failure is not None:
                on_failure(
                    MachineryBatchOutcome(
                        status="failed",
                        service="OpenRouter",
                        unit_ids=tuple(mt_unit.id for mt_unit in units),
                        reason_code=(
                            MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED
                        ),
                        error="The quota is exhausted.",
                    )
                )
            return {}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            message = batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        run_batch.assert_not_called()
        self.assertIn("Judges were not started", message)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "")
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.preparation_phase, "blocked")
        self.assertFalse(JudgeVerdict.objects.exists())
        row = JudgeRunUnit.objects.get(unit_id_snapshot=unit.id)
        self.assertEqual(row.outcome, JudgeRunUnit.Outcome.SKIPPED)
        self.assertEqual(row.skip_reason, JudgeRunUnit.SkipReason.MT_PREREQUISITE)

    def test_human_write_during_preparation_is_not_overwritten(self) -> None:
        """A human translation written after the fetch is never replaced."""
        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            # The MT answer arrives, but the human already wrote the target.
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["MT answer"],
                    "quality": [90],
                    "origin": [None],
                }
            live = type(unit).objects.get(pk=unit.pk)
            live.translate(self.user, ["Human text"], STATE_TRANSLATED)
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
            mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}),
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        unit.refresh_from_db()
        self.assertEqual(unit.target.strip(), "Human text")

    def test_ready_scope_skips_paid_probe(self) -> None:
        """A fully translated scope never pays for a preparation call."""
        unit = self.get_unit()
        unit.translate(self.user, ["Already translated"], STATE_TRANSLATED)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with (
            mock.patch("weblate.trans.autotranslate.fetch_machinery_matches") as fetch,
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        fetch.assert_not_called()
        self.assertTrue(run_batch.called)
        run = ProducerRun.objects.get()
        self.assertEqual(run.preparation_phase, "ready")

    def test_changed_source_is_not_stored_from_a_stale_preparation_answer(
        self,
    ) -> None:
        """A preparation answer for an older source never lands (C5)."""
        self._enable_openrouter()
        unit = self.get_unit()
        old_source = unit.source
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["Stale answer"],
                    "quality": [90],
                    "origin": [None],
                }
            live = type(unit).objects.get(pk=unit.pk)
            live.source = f"{old_source} (changed)"
            live.save(update_fields=["source"])
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
            mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}),
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        unit.refresh_from_db()
        self.assertEqual(unit.change_set.filter(action=ActionEvents.AUTO).count(), 0)

    def test_cancellation_between_preparation_batches_keeps_written_mt(self) -> None:
        """A cancel request between language batches stops new paid work."""
        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        run = batch._create_producer_run()  # ruff: ignore[private-member-access]
        run.status = ProducerRun.Status.RUNNING
        run.save(update_fields=["status"])
        scope = batch.build_preparation_scope()

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            ProducerRun.objects.filter(pk=run.pk).update(
                status=ProducerRun.Status.CANCEL_REQUESTED
            )
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["Stored before cancel"],
                    "quality": [90],
                    "origin": [None],
                }
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
        ):
            # Returns None: the stop was a cancellation, not a failure.
            outcome = batch._run_preparation(  # ruff: ignore[private-member-access]
                run, scope, 80, []
            )

        self.assertIsNone(outcome)
        unit.refresh_from_db()
        self.assertEqual(unit.target.strip(), "Stored before cancel")
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.CANCEL_REQUESTED)
        self.assertEqual(run.preparation_phase, "preparing")

    def test_resume_of_a_blocked_run_retries_only_remaining_missing(self) -> None:
        """A resumed preparation never re-pays for an already-filled string."""
        self._enable_openrouter()
        filled = self.get_unit()
        filled.translate(self.user, [""], STATE_EMPTY)
        still_empty = self.get_unit("Thank you for using Weblate.", language="de")
        still_empty.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[filled.id, still_empty.id],
            enforce_permissions=False,
        )
        run = batch._create_producer_run()  # ruff: ignore[private-member-access]
        run.status = ProducerRun.Status.RUNNING
        run.save(update_fields=["status"])
        scope = batch.build_preparation_scope()
        # The human filled one missing string after the failure.
        filled.translate(self.user, ["Human fill"], STATE_TRANSLATED)

        prepared_ids: list[int] = []

        def fake_fetch(units, *, services, on_failure=None, **kwargs):
            prepared_ids.extend(mt_unit.id for mt_unit in units)
            for mt_unit in units:
                mt_unit.machinery = {
                    "translation": ["Prepared"],
                    "quality": [90],
                    "origin": [None],
                }
            return {mt_unit.id: mt_unit.machinery for mt_unit in units}

        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=fake_fetch,
            ),
        ):
            outcome = batch._run_preparation(  # ruff: ignore[private-member-access]
                run, scope, 80, []
            )

        self.assertIsNone(outcome)
        self.assertEqual(prepared_ids, [still_empty.id])
        run.refresh_from_db()
        self.assertEqual(run.preparation_phase, "ready")

    def test_recheck_of_an_empty_unit_never_pays_a_preparation_probe(self) -> None:
        """A single-unit recheck judges emptiness without project MT."""
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        run = self._make_queued_recheck_run(unit, query=f"id:{unit.pk}")
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q=f"id:{unit.pk}",
            mode="judge",
            unit_ids=[unit.pk],
            enforce_permissions=False,
            producer_run_id=str(run.pk),
            judge_pretranslate=False,
        )
        with (
            mock.patch("weblate.trans.autotranslate.fetch_machinery_matches") as fetch,
            mock.patch(
                "weblate.trans.autotranslate.run_judge_batch", return_value={}
            ) as run_batch,
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        fetch.assert_not_called()
        # The stored text is empty: the judge phase honestly skips the unit
        # as untranslated (no fake pass) and no verdict is ever bought.
        self.assertFalse(run_batch.called)
        self.assertFalse(JudgeVerdict.objects.exists())
        self.assertEqual(
            JudgeRunUnit.objects.get(unit_id_snapshot=unit.id).outcome,
            JudgeRunUnit.Outcome.SKIPPED,
        )

    def test_summary_carries_mt_preparation_block(self) -> None:
        """The durable summary reports the preparation as its own block."""
        self._enable_openrouter()
        unit = self.get_unit()
        unit.translate(self.user, [""], STATE_EMPTY)
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            unit_ids=[unit.id],
            enforce_permissions=False,
        )
        with (
            mock.patch(
                "weblate.trans.autotranslate.fetch_machinery_matches",
                side_effect=lambda *_args, **_kwargs: {},
            ),
            mock.patch("weblate.trans.autotranslate.run_judge_batch", return_value={}),
        ):
            batch.perform(
                auto_source="mt", engines=[], threshold=80, source_component_ids=None
            )

        run = ProducerRun.objects.get()
        prep = run.summary.get("mt_preparation")
        self.assertIsInstance(prep, dict)
        # A preparation that wrote nothing and left the string missing is
        # a blocked barrier, not a silent success.
        self.assertEqual(run.preparation_phase, "blocked")
        self.assertEqual(prep["phase"], "blocked")
        self.assertEqual(prep["reason_code"], "mt-prerequisite")
        self.assertEqual(prep["missing_initial"], 1)
        self.assertEqual(prep["remaining"], 1)

    def test_worker_fails_a_run_whose_query_mismatches(self) -> None:
        unit = self.get_unit()
        run = self._make_queued_recheck_run(unit, query="state:empty")
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="state:empty",
            mode="judge",
            unit_ids=[unit.pk],
            enforce_permissions=False,
            producer_run_id=str(run.pk),
            judge_pretranslate=False,
        )
        with self.assertRaises(ValueError):
            batch.perform(
                auto_source="mt",
                engines=[],
                threshold=80,
                source_component_ids=None,
            )
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertTrue(run.failure)

    def test_preview_judge_scope_uncapped_for_execution_version_1(self) -> None:
        translation = self.get_translation()
        Unit.objects.filter(translation__component=self.component).delete()
        for i in range(5):
            Unit.objects.create(
                translation=translation,
                id_hash=i + 1000,
                source=f"source string {i}",
                target=f"target string {i}",
                state=STATE_TRANSLATED,
                position=i,
            )
        with override_settings(JUDGE_MAX_UNITS_PER_RUN=2):
            batch = BatchAutoTranslate(
                self.component,
                user=self.user,
                q="",
                mode="judge",
                enforce_permissions=False,
            )
            preview_v1 = batch.preview_judge_scope(execution_version=1)
            self.assertEqual(preview_v1.matched, 5)
            self.assertEqual(preview_v1.processed, 5)
            self.assertEqual(preview_v1.remaining, 0)

            preview_v0 = batch.preview_judge_scope(execution_version=0)
            self.assertEqual(preview_v0.matched, 5)
            self.assertEqual(preview_v0.processed, 2)
            self.assertEqual(preview_v0.remaining, 3)

            _preview, units_v1 = batch.preview_judge_scope_snapshot(execution_version=1)
            self.assertEqual(len(units_v1), 5)

            _preview, units_v0 = batch.preview_judge_scope_snapshot(execution_version=0)
            self.assertEqual(len(units_v0), 2)

    def test_scope_hash_includes_execution_version(self) -> None:
        from weblate.api.producer.views import (  # ruff: ignore[import-outside-top-level]
            _scope_hash_for,
        )

        unit = self.get_unit()
        hash_v1 = _scope_hash_for(
            self.project, {"query": ""}, [unit], {}, execution_version=1
        )
        hash_v0 = _scope_hash_for(
            self.project, {"query": ""}, [unit], {}, execution_version=0
        )
        self.assertNotEqual(hash_v1, hash_v0)
