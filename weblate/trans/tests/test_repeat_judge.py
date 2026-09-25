# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stored judge verdicts for repeat queue groups."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from weblate.trans.autotranslate import BatchAutoTranslate
from weblate.trans.judge import JudgeError
from weblate.trans.models import (
    JudgeVerdict,
    ProducerRun,
    RepeatPolicy,
    RepeatRecommendationRun,
    Unit,
)
from weblate.trans.models.judge import compute_target_hash
from weblate.trans.repeat_judge import (
    CHOOSE,
    READY,
    REPEAT_JUDGE_QUERY,
    REWRITE,
    UNCHECKED,
    compare_after_judge,
    judge_group,
    judge_groups,
)
from weblate.trans.repeat_recommendations import (
    build_group_context,
    context_fingerprint,
)
from weblate.trans.repeats import get_or_create_group, save_policy
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import join_plural
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED


class RepeatJudgeFixtures(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.translation = self.component.translation_set.get(language_code="cs")
        self.position = 1000

    def add_group(self, source: str, targets: list[str | tuple[str, ...]]):
        """Create a queue-shaped exact-repeat group with real unit contexts."""
        variants = []
        for target in targets:
            self.position += 1
            context = f"{source}-{self.position}"
            source_unit = self.component.source_translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=self.position,
                context=context,
                source=source,
                target=source,
                state=STATE_TRANSLATED,
            )
            target_forms = (target,) if isinstance(target, str) else target
            unit = self.translation.unit_set.create(
                id_hash=calculate_hash(source, context),
                position=self.position,
                source_unit=source_unit,
                context=context,
                source=source,
                target=join_plural(target_forms),
                state=STATE_TRANSLATED,
            )
            variants.append({"target": target_forms, "units": [unit]})
        return variants

    def make_verdict(
        self,
        unit: Unit,
        severity: str = JudgeVerdict.Severity.NONE,
        *,
        seat: int = 1,
        unparsed: bool = False,
        category: str = "mistranslation",
        description: str = "Changes the meaning",
        back_translation: str = "The original meaning",
        target_hash: str | None = None,
    ) -> JudgeVerdict:
        return JudgeVerdict.objects.create(
            unit=unit,
            target_hash=target_hash or compute_target_hash(unit.get_target_plurals()),
            context_hash="context",
            judge_model="vendor/model-a",
            seat=seat,
            unparsed=unparsed,
            max_severity=severity,
            errors=(
                [
                    {
                        "severity": severity,
                        "category": category,
                        "description": description,
                    }
                ]
                if severity
                in {JudgeVerdict.Severity.MAJOR, JudgeVerdict.Severity.CRITICAL}
                else []
            ),
            back_translation=back_translation,
        )

    def judge(self, variants):
        return judge_groups([(1, variants)])[1]


class RepeatJudgeTest(RepeatJudgeFixtures):
    def test_one_passed_and_one_flagged_is_ready_with_reason_and_evidence(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag"])
        passed = self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[0]["units"][0], seat=2)
        flagged = self.make_verdict(
            variants[1]["units"][0], JudgeVerdict.Severity.MAJOR
        )
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR, seat=2)

        result = self.judge(variants)

        self.assertEqual(result.bucket, READY)
        self.assertEqual(result.recommended, ("Pass",))
        self.assertEqual(result.variants["Pass",].mark, "passed")
        self.assertEqual(result.variants["Flag",].mark, "flagged")
        self.assertEqual(
            result.variants["Flag",].reason, "Mistranslation: Changes the meaning"
        )
        self.assertEqual(
            result.evidence,
            (
                (variants[0]["units"][0].pk, passed.pk, passed.target_hash),
                (variants[1]["units"][0].pk, flagged.pk, flagged.target_hash),
            ),
        )

    def test_unchecked_variant_prevents_ready(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Unknown"])
        self.make_verdict(variants[0]["units"][0])

        result = self.judge(variants)

        self.assertEqual(result.bucket, UNCHECKED)
        # The only passed variant is still preselected on the card (D17).
        self.assertEqual(result.recommended, ("Pass",))
        self.assertEqual(result.rule, 2)
        self.assertEqual(result.variants["Unknown",].mark, "unchecked")

    def test_unchecked_variant_prevents_ready_even_with_flagged_variant(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag", "Unknown"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).bucket, UNCHECKED)

    def test_one_parsed_seat_passes_when_other_seat_is_unparsed(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[0]["units"][0], seat=2, unparsed=True)
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).bucket, READY)

    def test_one_major_seat_flags_place(self) -> None:
        variants = self.add_group("Gate", ["Flag", "Unknown"])
        self.make_verdict(variants[0]["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).variants["Flag",].mark, "flagged")

    def test_strictest_seat_flags_place(self) -> None:
        variants = self.add_group("Gate", ["Flag", "Unknown"])
        unit = variants[0]["units"][0]
        self.make_verdict(unit)
        self.make_verdict(unit, JudgeVerdict.Severity.MAJOR, seat=2)

        self.assertEqual(self.judge(variants).variants["Flag",].mark, "flagged")

    def test_two_passed_variants_take_choose_priority_over_unchecked(self) -> None:
        variants = self.add_group("Gate", ["First", "Second", "Unknown"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0])

        result = self.judge(variants)

        self.assertEqual(result.bucket, CHOOSE)
        self.assertEqual(result.recommended, ("First",))
        self.assertEqual(result.rule, 4)

    def test_two_passed_variants_require_choice(self) -> None:
        variants = self.add_group("Gate", ["First", "Second"])
        for variant in variants:
            self.make_verdict(variant["units"][0])

        self.assertEqual(self.judge(variants).bucket, CHOOSE)

    def test_flagged_place_overrides_passed_place_in_same_variant(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Mixed", "Mixed"])
        variants[1]["units"].extend(variants.pop()["units"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0])
        self.make_verdict(variants[1]["units"][1], JudgeVerdict.Severity.MAJOR)

        result = self.judge(variants)

        self.assertEqual(result.variants["Mixed",].mark, "flagged")
        self.assertEqual(result.bucket, READY)

    def test_approved_place_requires_choice(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag"])
        variants[1]["units"][0].state = STATE_APPROVED
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).bucket, CHOOSE)

    def test_all_flagged_requires_rewrite(self) -> None:
        variants = self.add_group("Gate", ["First", "Second"])
        for variant in variants:
            self.make_verdict(variant["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).bucket, REWRITE)

    def test_nothing_judged_is_unchecked(self) -> None:
        variants = self.add_group("Gate", ["First", "Second"])

        result = self.judge(variants)

        self.assertEqual(result.bucket, UNCHECKED)
        self.assertEqual(result.evidence, ())

    def test_old_target_verdict_is_ignored(self) -> None:
        variants = self.add_group("Gate", ["Current", "Other"])
        self.make_verdict(
            variants[0]["units"][0],
            target_hash=compute_target_hash(["Old translation"]),
        )
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)

        self.assertEqual(self.judge(variants).bucket, UNCHECKED)

    def test_representative_back_translation_and_strictest_reason(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag"])
        passed_low = variants[0]["units"][0]
        flagged_low = variants[1]["units"][0]
        more = self.add_group("Gate", ["Pass", "Flag"])
        variants[0]["units"].append(more[0]["units"][0])
        variants[1]["units"].append(more[1]["units"][0])
        self.make_verdict(passed_low, back_translation="First back translation")
        self.make_verdict(
            more[0]["units"][0], back_translation="Second back translation"
        )
        self.make_verdict(
            flagged_low,
            JudgeVerdict.Severity.MAJOR,
            description="Major error",
            back_translation="Major back translation",
        )
        self.make_verdict(
            more[1]["units"][0],
            JudgeVerdict.Severity.CRITICAL,
            description="Critical error",
            back_translation="Critical back translation",
        )

        result = self.judge(variants)

        self.assertEqual(
            result.variants["Pass",].back_translation, "First back translation"
        )
        self.assertEqual(
            result.variants["Flag",].reason, "Mistranslation: Critical error"
        )
        # The flagged variant shows what the strictest seat read, so a
        # producer can see the rejected text is often an acceptable synonym.
        self.assertEqual(
            result.variants["Flag",].back_translation, "Critical back translation"
        )

    def test_equal_severity_reasons_use_lowest_unit_id(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Flag", "Flag"])
        variants[1]["units"].extend(variants.pop()["units"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(
            variants[1]["units"][0],
            JudgeVerdict.Severity.MAJOR,
            description="First error",
        )
        self.make_verdict(
            variants[1]["units"][1],
            JudgeVerdict.Severity.MAJOR,
            description="Second error",
        )

        result = self.judge(variants)

        self.assertEqual(result.variants["Flag",].reason, "Mistranslation: First error")

    def test_plural_variant_keeps_full_tuple(self) -> None:
        variants = self.add_group("Gate", [("One", "Many"), ("Other", "Others")])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)

        result = self.judge(variants)

        self.assertEqual(result.recommended, ("One", "Many"))
        self.assertIn(("Other", "Others"), result.variants)

    def test_judge_groups_reads_twenty_groups_with_one_verdict_query(self) -> None:
        groups = []
        for index in range(20):
            variants = self.add_group(f"Gate {index}", ["Pass", "Flag"])
            self.make_verdict(variants[0]["units"][0])
            self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)
            groups.append((index, variants))

        unit_ids = [
            unit.pk
            for _, variants in groups
            for variant in variants
            for unit in variant["units"]
        ]
        units = Unit.objects.filter(pk__in=unit_ids).select_related(
            "translation__component", "translation__plural"
        )
        by_id = {unit.pk: unit for unit in units}
        for _, variants in groups:
            for variant in variants:
                variant["units"] = [by_id[unit.pk] for unit in variant["units"]]

        with CaptureQueriesContext(connection) as queries:
            results = judge_groups(groups)

        self.assertEqual(len(queries), 1)
        self.assertEqual(len(results), 20)
        self.assertTrue(all(result.bucket == READY for result in results.values()))

    def test_judge_group_accepts_preloaded_verdicts(self) -> None:
        variants = self.add_group("Gate", ["Pass", "Unknown"])
        verdict = self.make_verdict(variants[0]["units"][0])

        result = judge_group(variants, {variants[0]["units"][0].pk: verdict})

        self.assertEqual(result.bucket, UNCHECKED)
        self.assertEqual(
            result.variants["Pass",].back_translation, "The original meaning"
        )


class RepeatModelComparisonTest(RepeatJudgeFixtures):
    """Every group preselects its best available choice, first D17 rule wins."""

    @staticmethod
    def recommendation(action: str, target=()) -> SimpleNamespace:
        return SimpleNamespace(action=action, target=list(target), rationale="Why")

    def judge_with(self, variants, recommendation):
        return judge_groups([(1, variants)], {1: recommendation})[1]

    def passed_group(self, targets):
        variants = self.add_group("Gate", targets)
        for variant in variants:
            self.make_verdict(variant["units"][0])
        return variants

    def flagged_group(self, targets):
        variants = self.add_group("Gate", targets)
        for variant in variants:
            self.make_verdict(variant["units"][0], JudgeVerdict.Severity.MAJOR)
        return variants

    def assert_preselected(self, result, bucket, rule, recommended=None) -> None:
        self.assertEqual(result.bucket, bucket)
        self.assertEqual(result.rule, rule)
        self.assertEqual(result.recommended, recommended)

    def test_rule_1_use_existing_on_a_passed_variant_is_ready(self) -> None:
        variants = self.passed_group(["One", "Two"])

        result = self.judge_with(variants, self.recommendation("use_existing", ["Two"]))

        self.assert_preselected(result, READY, 1, ("Two",))
        self.assertIsNone(result.judge_only)

    def test_rule_1_with_an_approved_place_stays_choose(self) -> None:
        variants = self.passed_group(["One", "Two"])
        unit = variants[0]["units"][0]
        unit.state = STATE_APPROVED

        result = self.judge_with(variants, self.recommendation("use_existing", ["Two"]))

        # The card still preselects the pick; bulk never takes it (D4).
        self.assert_preselected(result, CHOOSE, 1, ("Two",))

    def test_rule_2_only_passed_variant_beats_keep_independent(self) -> None:
        # Основание / fr: the judge passed only "Base", the model said
        # "different contexts suggest different referents".
        variants = self.add_group("Основание", ["Armature", "Base", "Monture"])
        self.make_verdict(variants[0]["units"][0], JudgeVerdict.Severity.MAJOR)
        self.make_verdict(variants[1]["units"][0])
        self.make_verdict(variants[2]["units"][0], JudgeVerdict.Severity.MAJOR)

        result = self.judge_with(variants, self.recommendation("keep_independent"))

        self.assert_preselected(result, READY, 2, ("Base",))
        self.assertEqual(result.judge_only, ("Base",))

    def ready_group(self):
        variants = self.add_group("Gate", ["Pass", "Flag"])
        self.make_verdict(variants[0]["units"][0])
        self.make_verdict(variants[1]["units"][0], JudgeVerdict.Severity.MAJOR)
        return variants

    def test_rule_1_ready_group_stays_ready_when_the_model_agrees(self) -> None:
        variants = self.ready_group()

        result = self.judge_with(
            variants, self.recommendation("use_existing", ["Pass"])
        )

        self.assert_preselected(result, READY, 1, ("Pass",))
        self.assertIsNone(result.judge_only)

    def test_rule_2_ready_group_keeps_the_judge_variant_when_the_model_disagrees(
        self,
    ) -> None:
        for recommendation in (
            self.recommendation("use_existing", ["Flag"]),
            self.recommendation("keep_independent"),
            self.recommendation("propose_new", ["Three"]),
            self.recommendation("needs_human"),
            None,
        ):
            with self.subTest(recommendation=recommendation):
                result = self.judge_with(self.ready_group(), recommendation)
                self.assert_preselected(result, READY, 2, ("Pass",))
                self.assertEqual(result.judge_only, ("Pass",))

    def test_rule_2_with_an_approved_place_stays_choose(self) -> None:
        variants = self.ready_group()
        variants[1]["units"][0].state = STATE_APPROVED

        result = self.judge_with(variants, self.recommendation("keep_independent"))

        self.assert_preselected(result, CHOOSE, 2, ("Pass",))

    def test_rule_3_keep_independent_without_a_single_passed_variant(self) -> None:
        for variants, bucket in (
            (self.passed_group(["One", "Two"]), CHOOSE),
            (self.flagged_group(["Bad", "Worse"]), REWRITE),
        ):
            with self.subTest(bucket=bucket):
                result = self.judge_with(
                    variants, self.recommendation("keep_independent")
                )
                self.assert_preselected(result, bucket, 3)

    def test_rule_4_several_passed_prefer_the_most_used_variant(self) -> None:
        variants = self.passed_group(["Rare", "Common", "Common"])
        variants[1]["units"].extend(variants.pop()["units"])
        variants += self.add_group("Gate", ["Bad"])
        self.make_verdict(variants[2]["units"][0], JudgeVerdict.Severity.MAJOR)

        for recommendation in (
            self.recommendation("use_existing", ["Bad"]),
            self.recommendation("propose_new", ["New"]),
            self.recommendation("needs_human"),
            None,
        ):
            with self.subTest(recommendation=recommendation):
                result = self.judge_with(variants, recommendation)
                self.assert_preselected(result, CHOOSE, 4, ("Common",))

    def test_rule_4_tie_follows_the_queue_order(self) -> None:
        result = self.judge_with(self.passed_group(["One", "Two"]), None)

        self.assert_preselected(result, CHOOSE, 4, ("One",))

    def test_rule_5_propose_new_when_every_variant_is_flagged(self) -> None:
        result = self.judge_with(
            self.flagged_group(["Bad", "Worse"]),
            self.recommendation("propose_new", ["New"]),
        )

        self.assert_preselected(result, REWRITE, 5)

    def test_rule_6_unchecked_variant_without_a_passed_one(self) -> None:
        variants = self.add_group("Gate", ["Flag", "Rare", "Common", "Common"])
        variants[2]["units"].extend(variants.pop()["units"])
        self.make_verdict(variants[0]["units"][0], JudgeVerdict.Severity.MAJOR)

        picked = self.judge_with(
            variants, self.recommendation("use_existing", ["Rare"])
        )
        self.assert_preselected(picked, UNCHECKED, 6, ("Rare",))
        for recommendation in (
            self.recommendation("use_existing", ["Flag"]),
            self.recommendation("needs_human"),
            None,
        ):
            with self.subTest(recommendation=recommendation):
                result = self.judge_with(variants, recommendation)
                self.assert_preselected(result, UNCHECKED, 6, ("Common",))

    def test_rule_7_never_preselects_a_flagged_variant(self) -> None:
        for recommendation in (
            self.recommendation("use_existing", ["Bad"]),
            # A proposal equal to a flagged variant is that variant.
            self.recommendation("propose_new", ["Bad"]),
            self.recommendation("needs_human"),
            None,
        ):
            with self.subTest(recommendation=recommendation):
                result = self.judge_with(
                    self.flagged_group(["Bad", "Worse"]), recommendation
                )
                self.assert_preselected(result, REWRITE, 7)

    def test_unchecked_bucket_stays_unchecked_when_the_model_picks(self) -> None:
        variants = self.add_group("Gate", ["One", "Two"])
        self.make_verdict(variants[0]["units"][0])

        result = self.judge_with(variants, self.recommendation("use_existing", ["One"]))

        # An unchecked variant still keeps the group out of ready (D9).
        self.assert_preselected(result, UNCHECKED, 1, ("One",))

    def test_plural_use_existing_keeps_the_full_target(self) -> None:
        variants = self.passed_group([("One", "Many"), ("Other", "Others")])

        result = self.judge_with(
            variants, self.recommendation("use_existing", ["Other", "Others"])
        )

        self.assertEqual(result.bucket, READY)
        self.assertEqual(result.recommended, ("Other", "Others"))


class RepeatComparisonTriggerTest(RepeatJudgeFixtures):
    """A completed queue judge run compares its ready, choose and rewrite groups."""

    def setUp(self) -> None:
        super().setUp()
        self.make_manager()
        self.policy = save_policy(
            policy=RepeatPolicy(
                project=self.project,
                source_language=self.component.source_language,
                target_language=self.translation.language,
            ),
            components=[self.component],
            labels=[],
            actor=self.user,
        )
        self.project_language = self.project.project_languages[
            self.translation.language
        ]

    def add_judged_group(self, source: str, severities: list[str]):
        variants = self.add_group(source, [f"{source} {i}" for i in range(2)])
        for variant, severity in zip(variants, severities, strict=True):
            if severity:
                self.make_verdict(variant["units"][0], severity)
        return get_or_create_group(self.policy, variants[0]["units"][0])

    def make_run(self, **fields) -> ProducerRun:
        values = {
            "actor": self.user,
            "scope_type": ProducerRun.ScopeType.PROJECT,
            "scope_id": str(self.project.pk),
            "scope_label": str(self.project_language),
            "scope_path": self.project_language.get_absolute_url(),
            "requested_query": REPEAT_JUDGE_QUERY,
            "requested_mode": "judge",
            "execution_options": {"judge_proposal_only": True},
            "cap": 4,
            "status": ProducerRun.Status.RUNNING,
        }
        values.update(fields)
        return ProducerRun.objects.create(**values)

    def finish(self, run: ProducerRun, status=ProducerRun.Status.COMPLETED):
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            enforce_permissions=False,
        )
        with (
            patch(
                "weblate.trans.repeat_recommendations.judge_primary_endpoint",
                return_value=SimpleNamespace(),
            ),
            patch(
                "weblate.trans.repeat_recommendations.resolve_judge_seat_profile",
                return_value=SimpleNamespace(
                    profile_fingerprint="p" * 64,
                    model="test-model",
                    temperature=0,
                    response_format="json_object",
                    provider="test",
                    reasoning="",
                ),
            ),
            patch("weblate.trans.repeat_recommendations.queue_attempt") as queue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            batch._finish_producer_run(  # ruff: ignore[private-member-access]
                run, status
            )
        run.refresh_from_db()
        return queue

    def sent_groups(self) -> set[int]:
        return {
            group["group"]
            for run in RepeatRecommendationRun.objects.all()
            for attempt in run.attempts.all()
            for group in attempt.request_snapshot["groups"]
        }

    def test_completed_queue_run_compares_checked_groups_without_results(
        self,
    ) -> None:
        none = JudgeVerdict.Severity.NONE
        major = JudgeVerdict.Severity.MAJOR
        choose = self.add_judged_group("Choose", [none, none])
        decided = self.add_judged_group("Decided", [none, none])
        ready = self.add_judged_group("Ready", [none, major])
        rewrite = self.add_judged_group("Rewrite", [major, major])
        self.add_judged_group("Unchecked", [none, ""])
        units = list(self.translation.unit_set.filter(source="Decided").order_by("pk"))
        earlier = RepeatRecommendationRun.objects.create(
            policy=self.policy,
            actor=self.user,
            snapshot={},
            snapshot_fingerprint="s",
            profile_fingerprint="p",
            prompt_fingerprint="t",
            request_cap=1,
            status=RepeatRecommendationRun.Status.COMPLETED,
        )
        context = build_group_context(policy=self.policy, group=decided, units=units)
        earlier.results.create(
            group=decided,
            group_revision=decided.revision,
            snapshot_fingerprint="s",
            context_fingerprint=context_fingerprint(context),
            action="use_existing",
            target=["Decided 0"],
        )
        run = self.make_run(requested_query=f"{REPEAT_JUDGE_QUERY} AND id:1")

        queue = self.finish(run)

        self.assertEqual(RepeatRecommendationRun.objects.count(), 2)
        # A rewrite group is compared so the model can propose new text (D17).
        self.assertEqual(self.sent_groups(), {choose.pk, ready.pk, rewrite.pk})
        self.assertEqual(queue.call_count, 1)
        comparison = RepeatRecommendationRun.objects.exclude(pk=earlier.pk).get()
        self.assertEqual(comparison.actor, self.user)
        self.assertEqual(
            run.summary["repeat_comparison"],
            {"status": "started", "run": comparison.pk, "groups": 3},
        )
        # A redelivered completion or a second finalization starts nothing.
        self.assertIsNone(compare_after_judge(run.pk))
        self.finish(run)
        self.assertEqual(RepeatRecommendationRun.objects.count(), 2)

    def test_other_runs_start_no_comparison(self) -> None:
        none = JudgeVerdict.Severity.NONE
        self.add_judged_group("Choose", [none, none])
        runs = [
            self.make_run(execution_options={"judge_proposal_only": False}),
            self.make_run(requested_query="check:other"),
            self.make_run(requested_mode="translate"),
            self.make_run(scope_path=self.project.get_absolute_url()),
        ]
        for run in runs:
            with self.subTest(run=run.pk):
                self.finish(run)
                self.assertNotIn("repeat_comparison", run.summary)
        for status in (ProducerRun.Status.FAILED, ProducerRun.Status.CANCELLED):
            with self.subTest(status=status):
                run = self.make_run()
                self.finish(run, status)
                self.assertNotIn("repeat_comparison", run.summary)
                self.assertIsNone(compare_after_judge(run.pk))
        partial = self.make_run(status=ProducerRun.Status.CANCEL_REQUESTED)
        self.finish(partial)
        self.assertNotEqual(partial.status, ProducerRun.Status.COMPLETED)
        self.assertFalse(RepeatRecommendationRun.objects.exists())

    def test_only_unchecked_groups_record_none(self) -> None:
        self.add_judged_group("Unchecked", [JudgeVerdict.Severity.NONE, ""])
        run = self.make_run()

        self.finish(run)

        self.assertEqual(
            run.summary["repeat_comparison"], {"status": "none", "groups": 0}
        )
        self.assertFalse(RepeatRecommendationRun.objects.exists())

    def test_unavailable_judge_and_missing_permission_are_recorded(self) -> None:
        none = JudgeVerdict.Severity.NONE
        self.add_judged_group("Choose", [none, none])
        run = self.make_run()
        with patch(
            "weblate.trans.repeat_judge.prepare_run",
            side_effect=JudgeError("The LLM judge is not configured."),
        ):
            self.finish(run)
        self.assertEqual(
            run.summary["repeat_comparison"],
            {"status": "skipped", "reason": "judge-unavailable"},
        )

        self.project.remove_user(self.user)
        self.user.groups.clear()
        run = self.make_run()
        self.finish(run)
        self.assertEqual(
            run.summary["repeat_comparison"],
            {"status": "skipped", "reason": "permission"},
        )
        self.assertFalse(RepeatRecommendationRun.objects.exists())

    def test_disabled_policy_starts_nothing(self) -> None:
        self.add_judged_group(
            "Choose", [JudgeVerdict.Severity.NONE, JudgeVerdict.Severity.NONE]
        )
        RepeatPolicy.objects.filter(pk=self.policy.pk).update(enabled=False)
        run = self.make_run()

        self.finish(run)

        self.assertNotIn("repeat_comparison", run.summary)
        self.assertFalse(RepeatRecommendationRun.objects.exists())

    @override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY="")
    def test_keyless_judge_raises_nothing(self) -> None:
        none = JudgeVerdict.Severity.NONE
        self.add_judged_group("Choose", [none, none])
        run = self.make_run()
        batch = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="judge",
            enforce_permissions=False,
        )
        with self.captureOnCommitCallbacks(execute=True):
            batch._finish_producer_run(  # ruff: ignore[private-member-access]
                run, ProducerRun.Status.COMPLETED
            )
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertEqual(
            run.summary["repeat_comparison"],
            {"status": "skipped", "reason": "judge-unavailable"},
        )
        self.assertFalse(RepeatRecommendationRun.objects.exists())
