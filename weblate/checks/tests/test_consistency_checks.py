# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for consistency checks."""

from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from weblate.checks.consistency import (
    ConsistencyCheck,
    PluralsCheck,
    RepeatDriftCheck,
    ReusedCheck,
    SamePluralsCheck,
    TranslatedCheck,
)
from weblate.checks.models import CHECKS, Check
from weblate.lang.models import Language
from weblate.trans.actions import ActionEvents
from weblate.trans.models import Component, Translation, Unit
from weblate.trans.tests.factories import make_unit
from weblate.trans.tests.test_views import (
    ComponentTestCase,
    FixtureTestCase,
)
from weblate.utils.state import STATE_EMPTY, STATE_FUZZY, STATE_TRANSLATED


class PluralsCheckTest(TestCase):
    def setUp(self) -> None:
        self.check: PluralsCheck | SamePluralsCheck = PluralsCheck()

    def test_none(self) -> None:
        self.assertFalse(
            self.check.check_target(["string"], ["string"], make_unit("plural_none"))
        )

    def test_empty(self) -> None:
        self.assertFalse(
            self.check.check_target(
                ["string", "plural"], ["", ""], make_unit("plural_empty")
            )
        )

    def test_hit(self) -> None:
        self.assertTrue(
            self.check.check_target(
                ["string", "plural"], ["string", ""], make_unit("plural_partial_empty")
            )
        )

    def test_good(self) -> None:
        self.assertFalse(
            self.check.check_target(
                ["string", "plural"],
                ["translation", "trplural"],
                make_unit("plural_good"),
            )
        )


class SamePluralsCheckTest(PluralsCheckTest):
    def setUp(self) -> None:
        self.check = SamePluralsCheck()

    def test_hit(self) -> None:
        self.assertTrue(
            self.check.check_target(
                ["string", "plural"],
                ["string", "string"],
                make_unit("plural_partial_empty"),
            )
        )


class TranslatedCheckTest(FixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.check = TranslatedCheck()

    def run_check(self):
        unit = self.get_unit()
        return self.check.check_target(
            unit.get_source_plurals(), unit.get_target_plurals(), unit
        )

    def test_none(self) -> None:
        self.assertFalse(self.run_check())

    def test_translated(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.assertFalse(self.run_check())

    def test_untranslated(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.edit_unit("Hello, world!\n", "")
        self.assertTrue(self.run_check())

    def test_source_change(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.edit_unit("Hello, world!\n", "")
        unit = self.get_unit()
        unit.change_set.create(action=ActionEvents.SOURCE_CHANGE)
        self.assertFalse(self.run_check())

    def test_get_description(self) -> None:
        self.test_untranslated()
        check = Check(unit=self.get_unit())
        self.assertEqual(
            self.check.get_description(check),
            'Previous translation was "Nazdar svete!\n".',
        )

    def test_run_checks_untranslated(self) -> None:
        self.edit_unit("Hello, world!\n", "Nazdar svete!\n")
        self.edit_unit("Hello, world!\n", "")
        unit = self.get_unit()
        Check.objects.filter(unit=unit).delete()
        unit.clear_checks_cache()

        unit.run_checks()

        self.assertEqual(unit.state, STATE_EMPTY)
        self.assertIn("translated", unit.all_checks_names)

    def test_run_checks_untranslated_removes_stale_check(self) -> None:
        unit = self.get_unit()
        self.assertEqual(unit.state, STATE_EMPTY)
        Check.objects.create(unit=unit, name="same")

        unit.run_checks()

        self.assertNotIn("same", unit.all_checks_names)


class ReusedCheckGuardTest(SimpleTestCase):
    def test_reuse_ignores_non_propagating_component(self) -> None:
        check = ReusedCheck()
        unit = make_unit(target="Jeden")
        unit.translation.component.allow_translation_propagation = False
        unit.translation.component.batch_checks = True

        with patch.object(check, "handle_batch") as handle_batch:
            self.assertFalse(check.check_target_unit([], [], unit))

        handle_batch.assert_not_called()


class SameSourceUnitsMixin:
    """Create units with an explicit context, source and target."""

    def setUp(self) -> None:
        super().setUp()
        self.other = self.create_link_existing()
        self.translation_1 = self.component.translation_set.get(language__code="cs")
        self.translation_2 = self.other.translation_set.get(language__code="cs")
        self._id_hash = 1000

    def add_unit(
        self,
        translation,
        context: str,
        source: str,
        target: str,
        increment: bool = True,
    ):
        if increment:
            self._id_hash += 1
        source_unit = translation.component.source_translation.unit_set.create(
            id_hash=self._id_hash,
            position=self._id_hash,
            context=context,
            source=source,
            target=source,
            state=STATE_TRANSLATED,
        )
        return translation.unit_set.create(
            id_hash=self._id_hash,
            position=self._id_hash,
            source_unit=source_unit,
            context=context,
            source=source,
            target=target,
            state=STATE_TRANSLATED,
        )

class ConsistencyCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_reuse(self) -> None:
        check = ReusedCheck()
        self.assertEqual(list(check.check_component(self.component)), [])

        # Add non-triggering units
        unit = self.add_unit(self.translation_1, "one", "One", "Jeden")
        unit = self.add_unit(self.translation_2, "one", "One", "Jeden", increment=False)
        self.assertFalse(check.check_target_unit([], [], unit))
        self.assertEqual(list(check.check_component(self.component)), [])

        # Add triggering unit
        unit2 = self.add_unit(self.translation_2, "two", "Two", "Jeden")
        self.assertTrue(check.check_target_unit([], [], unit2))
        # Add another triggering unit
        unit3 = self.add_unit(self.translation_2, "three", "Three", "Jeden")
        self.assertTrue(check.check_target_unit([], [], unit3))

        self.assertNotEqual(list(check.check_component(self.component)), [])

        # Run all checks
        unit2.run_checks()
        # All four units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 4)

        # Change translation
        unit2.translate(self.user, "Dva", STATE_TRANSLATED)
        # Some units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 3)
        # Change translation
        unit3.translate(self.user, "Tři", STATE_TRANSLATED)
        # No units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 0)

    def test_reuse_existing(self) -> None:
        check = ReusedCheck()
        self.assertEqual(list(check.check_component(self.component)), [])

        # Add units
        unit = self.add_unit(self.translation_1, "one", "One", "Dva")
        unit2 = self.add_unit(self.translation_2, "two", "Two", "")
        # Run all checks
        unit2.run_checks()
        # No units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 0)

        # Change translation
        Unit.objects.get(pk=unit2.pk).translate(self.user, "Dva", STATE_TRANSLATED)
        # Both units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 2)
        # Change translation
        Unit.objects.get(pk=unit.pk).translate(self.user, "Jeden", STATE_TRANSLATED)
        # No units should be now failing
        self.assertEqual(Check.objects.filter(name="reused").count(), 0)

    def test_reuse_nocontext(self) -> None:
        check = ReusedCheck()
        self.assertEqual(list(check.check_component(self.component)), [])

        # Add non-triggering units
        unit = self.add_unit(self.translation_1, "", "One", "Jeden")
        unit = self.add_unit(self.translation_2, "", "One", "Jeden", increment=False)
        self.assertFalse(check.check_target_unit([], [], unit))
        self.assertEqual(list(check.check_component(self.component)), [])

        # Add triggering unit
        unit = self.add_unit(self.translation_2, "", "Two", "Jeden")
        self.assertTrue(check.check_target_unit([], [], unit))

        self.assertNotEqual(list(check.check_component(self.component)), [])

    def test_reuse_case(self) -> None:
        check = ReusedCheck()
        self.assertEqual(list(check.check_component(self.component)), [])
        self.translation_1.language = Language.objects.get(code="he")
        self.translation_1.save()
        self.translation_2.language = Language.objects.get(code="he")
        self.translation_2.save()

        # Add non-triggering units
        unit = self.add_unit(self.translation_1, "", "One", "Jeden")
        unit2 = self.add_unit(self.translation_2, "", "one", "Jeden")
        self.assertFalse(check.check_target_unit([], [], unit))
        # Verify there are no checks triggered
        self.assertEqual(list(check.check_component(self.component)), [])

        # Run all checks
        unit2.run_checks()
        self.assertEqual(Check.objects.filter(name="reused").count(), 0)

    def test_consistency(self) -> None:
        check = ConsistencyCheck()
        self.assertEqual(check.check_component(self.component), [])

        # Add triggering units
        unit = self.add_unit(self.translation_1, "one", "One", "Jeden")
        self.assertFalse(check.check_target_unit([], [], unit))
        unit = self.add_unit(self.translation_2, "one", "One", "Jedna", increment=False)
        self.assertTrue(check.check_target_unit([], [], unit))

        self.assertNotEqual(check.check_component(self.component), [])

    def test_consistency_empty_target(self) -> None:
        check = ConsistencyCheck()

        self.add_unit(self.translation_1, "one", "One", "Jeden")
        self.add_unit(self.translation_2, "one", "One", "", increment=False)

        self.assertNotEqual(check.check_component(self.component), [])

    def test_consistency_empty_target_run_checks(self) -> None:
        self.add_unit(self.translation_1, "one", "One", "Jeden")
        unit = self.add_unit(self.translation_2, "one", "One", "", increment=False)

        unit.run_checks()

        self.assertEqual(unit.all_checks_names, {"inconsistent"})

    def test_consistency_query_uses_min_max_targets(self) -> None:
        check = ConsistencyCheck()

        self.add_unit(self.translation_1, "one", "One", "Jeden")
        self.add_unit(self.translation_2, "one", "One", "Jedna", increment=False)

        with CaptureQueriesContext(connection) as queries:
            list(check.check_component(self.component))

        sql = "\n".join(query["sql"].upper() for query in queries)
        self.assertNotIn("COUNT(DISTINCT", sql)
        self.assertIn("MIN(", sql)
        self.assertIn("MAX(", sql)

        aggregate_sql = next(
            query["sql"].upper() for query in queries if "MIN(" in query["sql"].upper()
        )
        self.assertNotIn('"TRANS_COMPONENT"', aggregate_sql)
        self.assertIn('"TRANS_UNIT"."TRANSLATION_ID" IN', aggregate_sql)

        unit_sql = next(
            query["sql"].upper()
            for query in queries
            if '"TRANS_UNIT"."TRANSLATION_ID" IN' in query["sql"].upper()
            and '"TRANS_UNIT"."ID_HASH" IN' in query["sql"].upper()
            and "MIN(" not in query["sql"].upper()
        )
        self.assertNotIn('"TRANS_COMPONENT"', unit_sql)
        self.assertNotIn('"TRANS_TRANSLATION"', unit_sql)

    def test_consistency_skips_singleton_plurals(self) -> None:
        check = ConsistencyCheck()
        self.other.allow_translation_propagation = False
        self.other.save(update_fields=["allow_translation_propagation"])

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(check.check_component(self.component), [])

        sql = "\n".join(query["sql"].upper() for query in queries)
        self.assertNotIn("MIN(", sql)


class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def enable_repeat_drift(self):
        Component.objects.filter(project=self.project).update(
            check_flags="repeat-drift"
        )
        return Component.objects.get(pk=self.component.pk)

    def test_same_source_different_key_drifts(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")

        self.assertTrue(check.check_target_unit([], [], unit))

    def test_same_source_same_translation_is_clean(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_case_distinct_sources_are_not_one_group(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "hello", "Nazdar")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_glossary_component_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.translation.component.is_glossary = True

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_propagation_disabled_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.translation.component.allow_translation_propagation = False

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_needs_editing_unit_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.state = STATE_FUZZY

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_other_language_is_not_a_repeat(self) -> None:
        check = RepeatDriftCheck()
        german = self.component.translation_set.get(language__code="de")
        self.add_unit(german, "greet_intro", "Hello", "Hallo")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_source_language_translation_is_excluded(self) -> None:
        check = RepeatDriftCheck()
        czech = Language.objects.get(code="cs")
        self.other.source_language = czech
        self.other.save(update_fields=["source_language"])
        self.translation_2.unit_set.create(
            id_hash=9001,
            position=9001,
            context="greet_source",
            source="Hello",
            target="Nazdar",
            state=STATE_TRANSLATED,
        )
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_registered_and_reachable_through_run_checks(self) -> None:
        self.assertIn("repeat-drift", CHECKS)
        Component.objects.filter(project=self.project).update(
            check_flags="repeat-drift"
        )
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Nazdar")

        second.run_checks()

        self.assertIn("repeat-drift", second.all_checks_names)
        self.assertIn("repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names)

    def test_check_component_finds_cross_component_drift(self) -> None:
        check = RepeatDriftCheck()
        self.assertEqual(list(check.check_component(self.component)), [])

        first = self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        self.assertEqual(
            {unit.pk for unit in check.check_component(self.component)},
            {first.pk, second.pk},
        )

    def test_check_component_ignores_agreeing_repeats(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        self.add_unit(self.translation_2, "greet_outro", "Hello", "Ahoj")

        self.assertEqual(list(check.check_component(self.component)), [])

    def test_aggregate_groups_by_source_not_context(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        with CaptureQueriesContext(connection) as queries:
            list(check.check_component(self.component))

        aggregate_sql = next(
            query["sql"].upper() for query in queries if "MIN(" in query["sql"].upper()
        )
        self.assertIn("MD5(LOWER", aggregate_sql)
        self.assertNotIn('"TRANS_UNIT"."CONTEXT"', aggregate_sql)
        self.assertNotIn('"TRANS_UNIT"."ID_HASH"', aggregate_sql)
        self.assertNotIn('"TRANS_COMPONENT"', aggregate_sql)

    def test_batch_pass_creates_and_clears_rows(self) -> None:
        check = RepeatDriftCheck()
        first = self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        check.perform_batch(self.enable_repeat_drift())
        self.assertEqual(
            set(
                Check.objects.filter(name="repeat-drift").values_list(
                    "unit_id", flat=True
                )
            ),
            {first.pk, second.pk},
        )

        Unit.objects.filter(pk=second.pk).update(target="Ahoj")
        check.perform_batch(self.enable_repeat_drift())
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)

    def test_ignore_flag_keeps_the_unit_clean(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")
        Unit.objects.filter(pk=second.pk).update(extra_flags="ignore-repeat-drift")

        check.perform_batch(self.enable_repeat_drift())

        self.assertEqual(
            list(
                Check.objects.filter(name="repeat-drift").values_list(
                    "unit_id", flat=True
                )
            ),
            [self.translation_1.unit_set.get(context="greet_intro").pk],
        )

    def test_group_cap_is_reported(self) -> None:
        check = RepeatDriftCheck()
        check.batch_limit = 1
        self.add_unit(self.translation_1, "one_a", "One", "Jeden")
        self.add_unit(self.translation_1, "one_b", "One", "Jedna")
        self.add_unit(self.translation_1, "two_a", "Two", "Dva")
        self.add_unit(self.translation_1, "two_b", "Two", "Dvě")

        with self.assertLogs("weblate", level="WARNING") as logs:
            units = list(check.check_component(self.component))

        self.assertIn("hit the 1 group cap", "\n".join(logs.output))
        self.assertEqual(len(units), 2)

    def test_fixing_one_member_clears_the_sibling(self) -> None:
        self.enable_repeat_drift()
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Nazdar")
        second.run_checks()
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 2)

        second = Unit.objects.get(pk=second.pk)
        second.translate(self.user, "Ahoj", STATE_TRANSLATED)

        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)
        self.assertNotIn(
            "repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names
        )

    def test_breaking_one_member_flags_the_sibling(self) -> None:
        self.enable_repeat_drift()
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Ahoj")
        second.run_checks()
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)

        second = Unit.objects.get(pk=second.pk)
        second.translate(self.user, "Nazdar", STATE_TRANSLATED)

        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 2)
        self.assertIn(
            "repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names
        )

    def test_description_lists_the_other_renderings(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        description = check.get_description(Check(unit=unit, name="repeat-drift"))

        self.assertIn("Ahoj", description)
        self.assertNotIn("Nazdar", description)
