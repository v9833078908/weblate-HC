# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stored judge verdicts for repeat queue groups."""

from __future__ import annotations

from django.db import connection
from django.test.utils import CaptureQueriesContext

from weblate.trans.models import JudgeVerdict, Unit
from weblate.trans.models.judge import compute_target_hash
from weblate.trans.repeat_judge import (
    CHOOSE,
    READY,
    REWRITE,
    UNCHECKED,
    judge_group,
    judge_groups,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import join_plural
from weblate.utils.hash import calculate_hash
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED


class RepeatJudgeTest(ViewTestCase):
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
        self.assertIsNone(result.recommended)
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
        self.assertIsNone(result.recommended)

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
            flagged_low, JudgeVerdict.Severity.MAJOR, description="Major error"
        )
        self.make_verdict(
            more[1]["units"][0],
            JudgeVerdict.Severity.CRITICAL,
            description="Critical error",
        )

        result = self.judge(variants)

        self.assertEqual(
            result.variants["Pass",].back_translation, "First back translation"
        )
        self.assertEqual(
            result.variants["Flag",].reason, "Mistranslation: Critical error"
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
