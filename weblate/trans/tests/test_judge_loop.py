# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from unittest import mock

from django.test import override_settings

from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
)
from weblate.trans.judge import JudgeResult, resolve_judge_seat_profile
from weblate.trans.judge_loop import (
    _select_repair_texts,
    _write_verdict,
    build_request,
    repair_targets,
    run_judge_batch,
)
from weblate.trans.models.judge import (
    JudgeRequestAttempt,
    JudgeRun,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_FUZZY, STATE_TRANSLATED


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


def mock_request_verdicts(batches):
    results = iter(batches)

    def request(requests, *, on_batch, **kwargs):
        batch_results = next(results)
        on_batch(requests, batch_results)
        return batch_results

    return mock.Mock(side_effect=request)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
)
class JudgeLoopTest(ViewTestCase):
    def run_batch(self, seat_results, repair=None, writable=True):
        # seat_results: list of results, consumed in order, one per call.
        client = mock_request_verdicts([[result] for result in seat_results])
        unit = self.get_unit()
        writable_ids = {unit.id} if writable else set()
        repair_mock = mock.Mock(
            return_value={} if repair is None else {unit.id: repair}
        )
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
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

    def test_shared_judge_run_allocates_request_rounds_monotonically(self) -> None:
        unit = self.get_unit()
        run = JudgeRun.objects.create(
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(unit.translation_id),
            scope_label=str(unit.translation),
            scope_path=unit.translation.get_absolute_url(),
            requested_mode="judge",
            cap=1,
        )
        client = mock_request_verdicts([[PASS], [PASS], [PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                run=run,
                use_cache=False,
            )
            run_judge_batch(
                [unit],
                writable_ids=set(),
                user=self.user,
                run=run,
                use_cache=False,
            )

        rows = list(unit.judge_verdicts.order_by("request_round", "seat"))
        self.assertEqual({row.run_id for row in rows}, {run.id})
        self.assertEqual([row.request_round for row in rows], [0, 0, 1, 1])
        self.assertEqual(
            unit.judge_verdicts.first().target_storage_hash,
            compute_target_storage_hash(unit.target),
        )

    def test_cache_invalidation_failure_does_not_erase_verdicts(self) -> None:
        unit = self.get_unit()
        with mock.patch(
            "weblate.trans.models.translation.Translation.invalidate_cache",
            side_effect=RuntimeError("cache unavailable"),
        ):
            self.run_batch([PASS, PASS])
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_verdict_stores_raw_target_hash(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS])
        expected = compute_target_storage_hash(unit.target)
        self.assertTrue(
            all(
                verdict.target_storage_hash == expected
                for verdict in unit.judge_verdicts.all()
            )
        )

    def test_verdict_hashes_request_target_snapshot(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        unit.target = "newer target"
        _write_verdict(
            unit,
            request,
            seat=1,
            attempt=0,
            run_id=uuid.uuid4(),
            result=PASS,
            profile=resolve_judge_seat_profile(1),
            project_context="",
        )
        self.assertEqual(
            unit.judge_verdicts.get().target_storage_hash,
            compute_target_storage_hash(request.target),
        )

    def test_run_and_seat_are_logged(self) -> None:
        with self.assertLogs("weblate.trans.judge_loop", level="INFO") as logs:
            self.run_batch([PASS, PASS])
        joined = "\n".join(logs.output)
        self.assertIn("judge run", joined)
        self.assertIn("seat", joined)

    def make_openrouter(self, engine):
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        return mock.patch("weblate.trans.judge_loop.MACHINERY", {"openrouter": engine})

    def test_selection_takes_the_best_candidate_per_plural_form(self) -> None:
        unit = self.get_unit()
        self.assertEqual(
            _select_repair_texts(
                unit,
                [
                    [
                        {"text": "lower quality", "quality": 50},
                        {"text": "fixed text", "quality": 100},
                    ]
                ],
            ),
            ["fixed text"],
        )

    def test_repair_targets_asks_a_batch_engine_once(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["units"], [unit])
        self.assertEqual(
            fetch.call_args.kwargs["threshold"], MACHINERY_DEFAULT_THRESHOLD
        )
        engine.return_value.translate.assert_not_called()

    def test_repair_targets_skips_a_unit_without_a_usable_candidate(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": [""],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_skips_a_result_whose_lists_disagree(self) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                unit.id: {
                    "translation": ["fixed text", "extra form"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(repair_targets([unit], self.user), {})

    def test_repair_targets_keeps_one_request_per_unit_for_a_single_string_engine(
        self,
    ) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 1
        engine.return_value.translate.return_value = [
            [{"text": "fixed text", "quality": 100}]
        ]
        fetch = mock.Mock()
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["fixed text"]}
            )
        fetch.assert_not_called()
        engine.return_value.translate.assert_called_once()

    def test_repair_targets_uses_litellm_when_only_litellm_is_configured(
        self,
    ) -> None:
        unit = self.get_unit()
        engine = mock.Mock()
        engine.return_value.batch_size = 1
        engine.return_value.translate.return_value = [
            [{"text": "litellm fix", "quality": 100}]
        ]
        self.component.project.machinery_settings = {"litellm": {"key": "ll-key"}}
        self.component.project.save(update_fields=["machinery_settings"])
        with mock.patch("weblate.trans.judge_loop.MACHINERY", {"litellm": engine}):
            self.assertEqual(
                repair_targets([unit], self.user), {unit.id: ["litellm fix"]}
            )

    def test_repair_targets_prefers_openrouter_when_both_are_configured(
        self,
    ) -> None:
        unit = self.get_unit()
        openrouter_engine = mock.Mock()
        openrouter_engine.return_value.batch_size = 1
        openrouter_engine.return_value.translate.return_value = [
            [{"text": "openrouter fix", "quality": 100}]
        ]
        litellm_engine = mock.Mock()
        self.component.project.machinery_settings = {
            "openrouter": {"key": "or-key"},
            "litellm": {"key": "ll-key"},
        }
        self.component.project.save(update_fields=["machinery_settings"])
        with mock.patch(
            "weblate.trans.judge_loop.MACHINERY",
            {"openrouter": openrouter_engine, "litellm": litellm_engine},
        ):
            self.assertEqual(
                repair_targets([unit], self.user),
                {unit.id: ["openrouter fix"]},
            )
        litellm_engine.assert_not_called()

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

    def test_repair_fetch_failure_does_not_crash_the_batch(self) -> None:
        # 2026-08-25 judge-repair-loop measurement: a malformed producer reply
        # must not lose the verdicts that both seats already wrote.
        unit = self.get_unit()
        original_target = unit.target
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        client = mock_request_verdicts([[MAJOR], [MAJOR]])
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.fetch_machinery_matches",
                side_effect=MachineTranslationError("boom"),
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(self.get_unit().target, original_target)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_a_negative_round_fetches_every_repair_in_one_call(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        repair_mock = mock.Mock(
            return_value={
                first.id: ["first repaired target"],
                second.id: ["second repaired target"],
            }
        )
        round_results = iter((MAJOR, MAJOR, PASS, PASS))

        def request(requests, *, on_batch, **kwargs):
            batch_results = [next(round_results)] * len(requests)
            on_batch(requests, batch_results)
            return batch_results

        client = mock.Mock(side_effect=request)
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        repair_mock.assert_called_once()
        self.assertEqual(
            [unit.id for unit in repair_mock.call_args.args[0]],
            [first.id, second.id],
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target.strip(), "first repaired target")
        self.assertEqual(second.target, "second repaired target")
        self.assertEqual(client.call_count, 4)

    def test_a_partial_repair_result_leaves_its_sibling_final(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        original_second_target = second.target
        repair_mock = mock.Mock(return_value={first.id: ["first repaired target"]})
        round_results = iter((MAJOR, MAJOR, PASS, PASS))

        def request(requests, *, on_batch, **kwargs):
            batch_results = [next(round_results)] * len(requests)
            on_batch(requests, batch_results)
            return batch_results

        client = mock.Mock(side_effect=request)
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", repair_mock),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        repair_mock.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target.strip(), "first repaired target")
        self.assertEqual(second.target, original_second_target)
        self.assertEqual(verdicts[second.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(second.judge_verdicts.count(), 2)

    def test_a_failed_repair_batch_leaves_every_unit_final(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        originals = {first.id: first.target, second.id: second.target}
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(side_effect=MachineTranslationError("not text"))
        client = mock_request_verdicts([[MAJOR, MAJOR], [MAJOR, MAJOR]])
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        fetch.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target, originals[first.id])
        self.assertEqual(second.target, originals[second.id])
        self.assertEqual(
            {verdicts[first.id].verdict, verdicts[second.id].verdict},
            {JudgeVerdict.Verdict.FLAG},
        )
        self.assertEqual(first.judge_verdicts.count(), 2)
        self.assertEqual(second.judge_verdicts.count(), 2)

    def test_a_partial_repair_batch_rejudges_only_answered_units(self) -> None:
        first = self.get_unit()
        second = self.get_unit(source="Thank you for using Weblate.")
        second.translate(self.user, ["second original target"], STATE_TRANSLATED)
        original_second_target = second.target
        engine = mock.Mock()
        engine.return_value.batch_size = 10
        fetch = mock.Mock(
            return_value={
                first.id: {
                    "translation": ["first repaired target"],
                    "quality": [90],
                    "origin": [None],
                }
            }
        )
        round_results = iter((MAJOR, MAJOR, PASS, PASS))

        def request(requests, *, on_batch, **kwargs):
            batch_results = [next(round_results)] * len(requests)
            on_batch(requests, batch_results)
            return batch_results

        client = mock.Mock(side_effect=request)
        with (
            self.make_openrouter(engine),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.fetch_machinery_matches", fetch),
        ):
            verdicts = run_judge_batch(
                [first, second],
                writable_ids={first.id, second.id},
                user=self.user,
            )
        fetch.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.target.strip(), "first repaired target")
        self.assertEqual(second.target, original_second_target)
        self.assertEqual(verdicts[first.id].verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdicts[second.id].verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(first.judge_verdicts.count(), 4)
        self.assertEqual(second.judge_verdicts.count(), 2)

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

    def test_litellm_only_project_still_gets_its_own_context(self) -> None:
        project = self.component.project
        project.machinery_settings = {
            "litellm": {
                "persona": "You judge a sci-fi game.",
                "style": "Keep it terse.",
            }
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {"You judge a sci-fi game.\n\nKeep it terse."})

    def test_openrouter_context_wins_when_both_engines_are_configured(self) -> None:
        project = self.component.project
        project.machinery_settings = {
            "openrouter": {"persona": "OpenRouter persona."},
            "litellm": {"persona": "LiteLLM persona."},
        }
        project.save(update_fields=["machinery_settings"])
        _, _, client = self.run_batch([PASS, PASS])
        contexts = {c.kwargs["project_context"] for c in client.call_args_list}
        self.assertEqual(contexts, {"OpenRouter persona."})

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
        client = mock_request_verdicts([[DEAD], [DEAD]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop._cached_verdict", return_value=None),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={},
            ) as repair,
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.UNPARSED)
        repair.assert_not_called()

    def test_historical_verdict_without_metadata_is_not_reused(self) -> None:
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
        client = mock_request_verdicts([[PASS], [PASS]])
        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            verdicts = run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)

    def test_newer_unparsed_evidence_disables_cache_reuse(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        parsed = unit.judge_verdicts.get(seat=1)
        JudgeVerdict.objects.create(
            unit=unit,
            model_verdict=JudgeVerdict.Verdict.UNPARSED,
            max_severity="none",
            unparsed=True,
            judge_model=parsed.judge_model,
            seat=parsed.seat,
            attempt=parsed.attempt,
            request_round=parsed.request_round,
            target_hash=parsed.target_hash,
            target_storage_hash=parsed.target_storage_hash,
            context_hash=parsed.context_hash,
            request_identity=parsed.request_identity,
            project_context_hash=parsed.project_context_hash,
            source_language=parsed.source_language,
            target_language=parsed.target_language,
            profile_fingerprint=parsed.profile_fingerprint,
            prompt_schema_version=parsed.prompt_schema_version,
        )
        client = mock_request_verdicts([[PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            run_judge_batch([unit], writable_ids=set(), user=self.user)

        self.assertEqual(client.call_count, 2)

    def test_profile_change_invalidates_cached_verdict(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        client = mock_request_verdicts([[PASS], [PASS]])
        with (
            override_settings(JUDGE_TEMPERATURE_SEAT_1=0.25),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
        ):
            run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(client.call_count, 2)

    def test_prompt_schema_change_invalidates_cached_verdict(self) -> None:
        unit, _, _ = self.run_batch([PASS, PASS], writable=False)
        client = mock_request_verdicts([[PASS], [PASS]])
        with (
            mock.patch(
                "weblate.trans.judge._prompt_schema_version",
                return_value="changed-prompt-schema",
            ),
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
        ):
            run_judge_batch([unit], writable_ids=set(), user=self.user)
        self.assertEqual(client.call_count, 2)

    def test_verdict_stores_request_attempt_and_empty_instruction(self) -> None:
        unit = self.get_unit()
        profile = resolve_judge_seat_profile(1)
        request = build_request(unit)
        attempt = JudgeRequestAttempt.objects.create(
            seat=1,
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            profile_fingerprint=profile.profile_fingerprint,
            prompt_schema_version=profile.prompt_schema_version,
            batch_digest="a" * 64,
            batch_size=1,
        )
        _write_verdict(
            unit,
            request,
            seat=1,
            attempt=0,
            run_id=uuid.uuid4(),
            result=JudgeResult(
                "none",
                "pass",
                [],
                "",
                instruction="must not persist",
                request_attempt_id=attempt.pk,
            ),
            profile=profile,
            project_context="",
        )
        verdict = unit.judge_verdicts.get()
        self.assertEqual(verdict.request_attempt_id, attempt.pk)
        self.assertEqual(verdict.instruction, "")

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
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["new but invalid"]},
            ),
            mock.patch(
                "weblate.trans.judge_loop._deterministic_checks",
                side_effect=[set(), {"game-markup"}],
            ),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(self.get_unit().target, original)

    def test_rollback_is_recorded_in_the_batch_repair_status(self) -> None:
        unit = self.get_unit()
        client = mock_request_verdicts([[CRITICAL], [CRITICAL]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["new but invalid"]},
            ),
            mock.patch(
                "weblate.trans.judge_loop._deterministic_checks",
                side_effect=[set(), {"game-markup"}],
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts.repair_status[unit.id], "rolled-back")

    def test_applied_repair_is_recorded_in_the_batch_repair_status(self) -> None:
        unit = self.get_unit()
        client = mock_request_verdicts([[CRITICAL], [CRITICAL], [PASS], [PASS]])
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["fixed"]},
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(verdicts.repair_status[unit.id], "applied")

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
        # Ordering guard: run_checks() projects the round's Check row before
        # repair_targets builds its prompt from failing_checks.
        seen = []

        def spy(units, _user):
            seen.extend({check.name for check in unit.all_checks} for unit in units)
            return {unit.id: ["fixed text"] for unit in units}

        client = mock_request_verdicts(
            [[result] for result in (CRITICAL, CRITICAL, PASS, PASS)]
        )
        unit = self.get_unit()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_targets", side_effect=spy),
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
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=1,
    JUDGE_RETRY_BUDGET_RATIO=1.0,
)
class JudgeUnparsedRetryRoundTest(ViewTestCase):
    def test_all_unparsed_round_is_retried_without_overwriting_history(self) -> None:
        unit = self.get_unit()
        client = mock_request_verdicts([[DEAD], [DEAD], [PASS], [PASS]])

        with mock.patch("weblate.trans.judge_loop.request_verdicts", client):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertEqual(client.call_count, 4)
        self.assertEqual(verdicts[unit.id].verdict, JudgeVerdict.Verdict.PASS)
        # Transport recovery uses a new request round without consuming a
        # repair-attempt coordinate.
        rows = list(unit.judge_verdicts.order_by("request_round", "seat"))
        self.assertEqual(len(rows), 4)
        self.assertEqual({row.attempt for row in rows}, {0})
        self.assertEqual(
            [row.request_round for row in rows],
            [0, 0, 1, 1],
        )
        self.assertTrue(all(row.unparsed for row in rows[:2]))
        self.assertTrue(all(not row.unparsed for row in rows[2:]))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
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

        def change_context(units, _user):
            self.source_term.explanation = "Changed while the judge was running."
            self.source_term.save(update_fields=["explanation"])
            return {unit.id: ["must not be applied"] for unit in units}

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets", side_effect=change_context
            ),
        ):
            verdicts = run_judge_batch([unit], writable_ids={unit.id}, user=self.user)

        self.assertNotIn(unit.id, verdicts)
        self.assertEqual(self.get_unit().target, original)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeIncrementalPersistenceTest(ViewTestCase):
    def test_completed_request_batches_survive_a_mid_seat_crash(self) -> None:
        units = list(self.get_translation().unit_set.order_by("pk")[:2])
        self.assertEqual(len(units), 2)

        def crash(requests, *, on_batch=None, **kwargs):
            if on_batch is not None:
                on_batch(requests[:1], [PASS])
                on_batch(requests[1:], [PASS])
            msg = "simulated worker loss"
            raise RuntimeError(msg)

        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", side_effect=crash),
            self.assertRaisesRegex(RuntimeError, "simulated worker loss"),
        ):
            run_judge_batch(
                units,
                writable_ids={unit.id for unit in units},
                user=self.user,
            )

        self.assertEqual(
            JudgeVerdict.objects.filter(unit__in=units, seat=1).count(),
            2,
        )


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
)
class JudgeMaxLengthRepairTest(ViewTestCase):
    """An active `max-length` check keeps a PASS verdict repairable."""

    def run_gated_batch(
        self,
        seat_results,
        *,
        max_length: int,
        target: str,
        repair: list[str] | None = None,
        writable: bool = True,
    ):
        client = mock_request_verdicts([[result] for result in seat_results])
        unit = self.change_unit(target)
        unit.extra_flags = f"max-length:{max_length}"
        unit.save(update_fields=["extra_flags"], same_content=True)
        writable_ids = {unit.id} if writable else set()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={} if repair is None else {unit.id: repair},
            ) as repair_mock,
        ):
            verdicts = run_judge_batch(
                [unit], writable_ids=writable_ids, user=self.user
            )
        return self.get_unit(), verdicts[unit.id], client, repair_mock

    def test_pass_with_active_max_length_still_triggers_one_repair(self) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["short"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(repair_mock.call_count, 1)
        self.assertEqual(client.call_count, 4)

    def test_unparsed_never_triggers_repair_even_with_active_max_length(
        self,
    ) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [DEAD, DEAD],
            max_length=5,
            target="a much longer raw target",
            repair=["short"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(repair_mock.call_count, 0)
        self.assertEqual(client.call_count, 2)

    def test_repair_clearing_max_length_drops_the_check(self) -> None:
        unit, verdict, _client, _repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["tiny"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertFalse(unit.has_check("max-length"))

    def test_repair_leaving_max_length_exhausts_attempts_and_stays_fuzzy(
        self,
    ) -> None:
        unit, verdict, _client, repair_mock = self.run_gated_batch(
            [PASS, PASS, PASS, PASS],
            max_length=5,
            target="a much longer raw target",
            repair=["still much too long to fit the budget"],
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(repair_mock.call_count, 1)
        self.assertTrue(unit.has_check("max-length"))
        self.assertEqual(unit.state, STATE_FUZZY)

    def test_unit_without_max_length_retains_pass_does_not_repair(self) -> None:
        _unit, verdict, client, repair_mock = self.run_gated_batch(
            [PASS, PASS], max_length=1000, target="short", repair=["unused"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(repair_mock.call_count, 0)
        self.assertEqual(client.call_count, 2)
