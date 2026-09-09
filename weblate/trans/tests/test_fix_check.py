# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Task 2 of the mass-fix-failing-checks plan.

Covers `weblate.trans.fix_check`: `apply_fixup_python`, the decision-7
before/after contract, `collect_fix_candidates`, `perform_fix`, and the
`ActionEvents.FIX_FAILING_CHECK` action-set membership.
"""

from __future__ import annotations

from unittest.mock import patch

from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, User
from weblate.checks.models import CHECKS, Check
from weblate.lang.models import Language
from weblate.trans.actions import (
    ACTIONS_CONTENT,
    ACTIONS_REVERTABLE,
    ACTIONS_SHOW_CONTENT,
    ActionEvents,
)
from weblate.trans.fix_check import (
    _compute_final_target,
    _decision_7_holds,
    apply_fixup_python,
    collect_fix_candidates,
    perform_fix,
)
from weblate.trans.models import Change, Component, Unit
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.models.pending import PendingUnitChange
from weblate.trans.models.translation import Translation
from weblate.trans.models.unit import calculate_hash
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import get_optional_path
from weblate.utils.state import FUZZY_STATES, STATE_TRANSLATED


class ApplyFixupPythonTest(ViewTestCase):
    """`apply_fixup_python` translates JS flags and rejects `plurals`."""

    def test_global_flag_replaces_every_occurrence(self) -> None:
        fixups = [("regex", " {2,}", " ", "gu")]
        self.assertEqual(
            apply_fixup_python(fixups, ["a  b  c", "d  e"]), ["a b c", "d e"]
        )

    def test_missing_global_flag_replaces_only_first(self) -> None:
        fixups = [("regex", r"\d", "X", "u")]
        self.assertEqual(apply_fixup_python(fixups, ["1 2 3"]), ["X 2 3"])

    def test_case_insensitive_flag(self) -> None:
        fixups = [("regex", "hello", "hi", "giu")]
        self.assertEqual(apply_fixup_python(fixups, ["Hello there"]), ["hi there"])

    def test_no_fixups_returns_copy(self) -> None:
        texts = ["a", "b"]
        result = apply_fixup_python(None, texts)
        self.assertEqual(result, texts)
        self.assertIsNot(result, texts)

    def test_plurals_variant_raises(self) -> None:
        with self.assertRaises(ValueError):
            apply_fixup_python([("plurals", ["a", "b"])], ["x", "y"])

    def test_untranslatable_flag_raises(self) -> None:
        """A flag this engine cannot translate must not be dropped silently."""
        for flags in ("gm", "gs", "y"):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                apply_fixup_python([("regex", "a", "b", flags)], ["aaa"])

    def test_every_plural_form_is_fixed(self) -> None:
        self.assertEqual(
            apply_fixup_python(
                [("regex", r"\.{3,}", "…", "gu")], ["one...", "many...."]
            ),
            ["one…", "many…"],
        )


class FixCheckEngineTest(ViewTestCase):
    """Engine-level behaviour: buckets, permissions, batching, history."""

    def setUp(self) -> None:
        super().setUp()
        self.end_stop_unit = self.get_unit(source="Thank you for using Weblate.")
        self.end_stop_check = CHECKS["end_stop"]
        self.double_space_check = CHECKS["double_space"]

    def _fail_end_stop(self, unit: Unit | None = None, target: str = "Dekuji") -> Unit:
        unit = unit or self.end_stop_unit
        unit.translate(self.user, target, STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(unit=unit, name="end_stop", dismissed=False).exists()
        )
        return unit

    def _fail_double_space(self, source: str, target: str) -> Unit:
        unit = self.get_unit(source=source)
        unit.translate(self.user, target, STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="double_space", dismissed=False
            ).exists()
        )
        return unit

    # -- collect_fix_candidates: buckets -----------------------------------

    def test_eligible_candidate_is_bucketed_and_previewed(self) -> None:
        unit = self._fail_end_stop()
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.manual, 0)
        self.assertEqual(result.denied, 0)
        self.assertEqual(len(result.shown), 1)
        self.assertEqual(result.shown[0].unit.pk, unit.pk)
        self.assertEqual(result.shown[0].final_target, ["Dekuji."])
        self.assertEqual(result.remaining, 0)

    def test_dismissed_check_is_excluded(self) -> None:
        self._fail_end_stop()
        Check.objects.filter(unit=self.end_stop_unit, name="end_stop").update(
            dismissed=True
        )
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.manual, 0)
        self.assertEqual(result.denied, 0)

    def test_glossary_component_is_excluded(self) -> None:
        self.component.create_glossary()
        glossary = Component.objects.get(project=self.project, is_glossary=True)
        glossary_unit = glossary.source_translation.unit_set.create(
            source="Hello, world!\n",
            target="Hello, world!\n",
            context="",
            id_hash=calculate_hash("Hello, world!\n", ""),
            position=1,
            state=STATE_TRANSLATED,
        )
        # Source-translation checks are normally omitted for a glossary.
        # Seed the otherwise impossible projected row to prove the query
        # excludes it before classification.
        Check.objects.create(unit=glossary_unit, name="double_space")
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=glossary),
            self.project,
            self.double_space_check,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.manual, 0)

    def test_denied_without_unit_edit_permission(self) -> None:
        self._fail_end_stop()
        limited = User.objects.create_user(
            "limited-fix", "limited-fix@example.com", "x"
        )
        limited.groups.clear()
        limited.clear_permissions_cache()
        self.assertFalse(limited.has_perm("unit.edit", self.end_stop_unit))
        result = collect_fix_candidates(
            limited,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.denied, 1)
        self.assertEqual(result.manual, 0)

    def test_none_user_bypasses_permission_check(self) -> None:
        self._fail_end_stop()
        result = collect_fix_candidates(
            None,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.denied, 0)

    def test_unresolved_fix_is_manual(self) -> None:
        unit = self.end_stop_unit
        unit.source = "Category;"
        unit.save(update_fields=["source"])
        unit.translate(self.user, "Kategorie", STATE_TRANSLATED)
        unit.refresh_from_db()
        check_obj = CHECKS["end_semicolon"]
        self.assertIsNone(check_obj.get_fixup(unit))

        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            check_obj,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.manual, 1)

    def test_conflicting_terminal_mark_is_manual(self) -> None:
        # Source ends "." (stop); target ends "?" (question): the overlap
        # case from Task 1's terminal-mark conflict guard. The fixup
        # refuses to touch it, so the unit is manual, not turned into "?.".
        unit = self._fail_end_stop(target="Diky?")
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.manual, 1)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Diky?")

    def test_introduced_failure_is_rejected(self) -> None:
        # French: appending a bare "?" without NNBSP would newly fail
        # punctuation_spacing. No fixture unit has a French question-mark
        # source, so this exercises the contract function directly with a
        # hand-built "bad" replacement, proving the engine itself (not
        # just the check's own fixup) rejects an introduced failure.
        end_question = CHECKS["end_question"]
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.translate(self.user, "Merci", STATE_TRANSLATED)
        unit.refresh_from_db()
        unit.translation.language = Language.objects.get(code="fr")

        sources = ["Save it?"]
        old_targets = ["Merci"]
        bad_new_targets = ["Merci?"]  # no NNBSP: introduces punctuation_spacing
        self.assertFalse(
            _decision_7_holds(end_question, unit, sources, old_targets, bad_new_targets)
        )

    def test_plural_targets(self) -> None:
        unit = self._fail_double_space(
            source="Orangutan has %d banana.\n", target=["a  b", "c  d", "e  f"]
        )
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.double_space_check,
        )
        row = next(r for r in result.shown if r.unit.pk == unit.pk)
        self.assertEqual(row.final_target, ["a b\n", "c d\n", "e f\n"])

    def test_idempotent_reapply(self) -> None:
        unit = self._fail_end_stop()
        fix_result_1 = perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(fix_result_1.fixed, 1)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji.")

        fix_result_2 = perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(fix_result_2.fixed, 0)
        self.assertEqual(fix_result_2.manual, 0)
        self.assertEqual(fix_result_2.denied, 0)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Dekuji.")

    def test_perform_fix_all_scope_none_ids(self) -> None:
        self._fail_end_stop()
        result = perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
            unit_ids=None,
        )
        self.assertEqual(result.fixed, 1)

    def test_perform_fix_explicit_ids_only(self) -> None:
        unit = self._fail_end_stop()
        other = self._fail_double_space(source="Hello, world!\n", target="a  b")
        result = perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
            unit_ids=[unit.pk],
        )
        self.assertEqual(result.fixed, 1)
        other.refresh_from_db()
        self.assertEqual(other.target, "a  b\n")  # untouched: wrong check_id anyway

    def test_perform_fix_stale_id_is_counted_not_applied(self) -> None:
        unit = self._fail_end_stop()
        # Fix it once out of band, then request the SAME id again: it no
        # longer matches the live `check:=end_stop` query.
        perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        result = perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
            unit_ids=[unit.pk],
        )
        self.assertEqual(result.fixed, 0)
        self.assertEqual(result.stale_or_no_change, 1)

    def test_stable_continuation_after_partial_apply(self) -> None:
        first = self._fail_end_stop()
        second = self._fail_double_space(source="Hello, world!\n", target="a  b")
        preview_1 = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
            preview_limit=250,
        )
        self.assertEqual([row.unit.pk for row in preview_1.shown], [first.pk])
        perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
            unit_ids=[first.pk],
        )
        preview_2 = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(preview_2.total_eligible, 0)
        second.refresh_from_db()
        self.assertEqual(second.target, "a  b\n")

    def test_judge_counter_current_verdict_goes_stale(self) -> None:
        unit = self._fail_end_stop()
        JudgeVerdict.objects.create(
            unit=unit,
            judge_model="test-model",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="ctx",
        )
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.verdicts_no_longer_current, 1)

    def test_judge_counter_already_stale_verdict_not_counted(self) -> None:
        unit = self._fail_end_stop()
        JudgeVerdict.objects.create(
            unit=unit,
            judge_model="test-model",
            seat=1,
            target_hash="not-the-current-hash",
            context_hash="ctx",
        )
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.verdicts_no_longer_current, 0)

    def test_no_judge_verdict_not_counted(self) -> None:
        self._fail_end_stop()
        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.verdicts_no_longer_current, 0)

    def test_action_set_membership(self) -> None:
        self.assertIn(ActionEvents.FIX_FAILING_CHECK, ACTIONS_CONTENT)
        self.assertIn(ActionEvents.FIX_FAILING_CHECK, ACTIONS_SHOW_CONTENT)
        self.assertNotIn(ActionEvents.FIX_FAILING_CHECK, ACTIONS_REVERTABLE)

    def test_history_records_fix_failing_check_action(self) -> None:
        unit = self._fail_end_stop()
        perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        change = Change.objects.filter(
            unit=unit, action=ActionEvents.FIX_FAILING_CHECK
        ).first()
        self.assertIsNotNone(change)
        self.assertEqual(change.target, "Dekuji.")

    def test_single_run_batched_checks_per_component(self) -> None:
        self._fail_end_stop()
        self._fail_double_space(source="Hello, world!\n", target="a  b")
        calls: list[int] = []
        original = Component.run_batched_checks

        def counting_run_batched_checks(self) -> None:
            calls.append(self.pk)
            original(self)

        Component.run_batched_checks = counting_run_batched_checks
        try:
            perform_fix(
                self.user,
                Unit.objects.filter(translation__component=self.component),
                self.project,
                self.end_stop_check,
            )
        finally:
            Component.run_batched_checks = original
        self.assertEqual(calls, [self.component.pk])

    def test_storage_autofix_parity(self) -> None:
        """The preview's computed target matches what apply actually stores."""
        unit = self._fail_end_stop()
        preview = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        previewed_target = preview.shown[0].final_target
        perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.get_target_plurals(), previewed_target)

    def test_dos_line_ending_parity(self) -> None:
        """Preview and apply agree on `Unit.translate()`'s DOS-EOL conversion."""
        unit = self.get_unit(source="Hello, world!\n")
        unit.translate(self.user, "a  b\nc d", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "a  b\nc d\n")
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="double_space", dismissed=False
            ).exists()
        )

        self.component.file_format_params = {"dos_eol": True}
        self.component.save(update_fields=["file_format_params"])

        preview = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.double_space_check,
        )
        expected_target = ["a b\r\nc d\r\n"]
        self.assertEqual(
            _compute_final_target(self.double_space_check, unit), expected_target
        )
        self.assertTrue(
            _decision_7_holds(
                self.double_space_check,
                unit,
                unit.get_source_plurals(),
                unit.get_target_plurals(),
                expected_target,
            )
        )
        self.assertEqual(preview.total_eligible, 1)
        previewed_target = preview.shown[0].final_target
        self.assertEqual(previewed_target, ["a b\r\nc d\r\n"])

        perform_fix(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.double_space_check,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.get_target_plurals(), previewed_target)
        self.assertEqual(unit.target, "a b\r\nc d\r\n")


class FixCheckRepeatDriftLinearityTest(ViewTestCase):
    """Task 2 step 8: run_checks passes stay linear in the edited group size."""

    def setUp(self) -> None:
        super().setUp()
        # `repeat-drift` is `default_disabled` and `propagates = "repeat"`
        # (`weblate/checks/consistency.py:299-311`): it is the only check
        # whose failure on one unit is recomputed for every other member of
        # its same-source group, which is what could turn a batch of N
        # edits into N x group_size recheck passes.
        Component.objects.filter(pk=self.component.pk).update(
            check_flags="repeat-drift"
        )
        self.component = Component.objects.get(pk=self.component.pk)
        self.translation = self.component.translation_set.get(language__code="cs")
        self._id_hash = 2000

    def add_repeat_unit(self, context: str, target: str) -> Unit:
        """Create one member of a same-source repeat group."""
        self._id_hash += 1
        source_unit = self.component.source_translation.unit_set.create(
            id_hash=self._id_hash,
            position=self._id_hash,
            context=context,
            source="Repeated source",
            target="Repeated source",
            state=STATE_TRANSLATED,
        )
        return self.translation.unit_set.create(
            id_hash=self._id_hash,
            position=self._id_hash,
            source_unit=source_unit,
            context=context,
            source="Repeated source",
            target=target,
            state=STATE_TRANSLATED,
        )

    def test_run_checks_calls_are_linear_in_the_repeat_group(self) -> None:
        double_space_check = CHECKS["double_space"]
        group_size = 4
        units = [
            self.add_repeat_unit(f"repeat_{index}", f"Opakovany  text {index}")
            for index in range(group_size)
        ]
        # The units must really form ONE repeat group, otherwise the
        # linearity bound below would hold trivially and prove nothing:
        # `repeat_units` is what `RepeatDriftCheck` propagates over.
        for unit in units:
            self.assertEqual(
                set(unit.repeat_units.values_list("pk", flat=True)),
                {other.pk for other in units if other.pk != unit.pk},
            )
            self.assertIn("repeat-drift", self.component.all_flags)

        calls: list[int] = []
        original = Unit.run_checks

        def counting_run_checks(self, **kwargs):
            calls.append(self.pk)
            return original(self, **kwargs)

        Unit.run_checks = counting_run_checks
        try:
            with self.captureOnCommitCallbacks(execute=True):
                result = perform_fix(
                    self.user,
                    Unit.objects.filter(translation=self.translation),
                    self.project,
                    double_space_check,
                )
        finally:
            Unit.run_checks = original

        self.assertEqual(result.fixed, group_size)
        # Linear, not quadratic: a small constant number of passes per
        # edited unit. Quadratic behaviour would be >= group_size per edit
        # (16 here) because every neighbour would be rechecked per edit.
        self.assertLessEqual(len(calls), 3 * group_size)


class FixCheckSourceTemplatePermissionTest(ViewTestCase):
    """A source-check candidate on a template unit needs `unit.template`."""

    def setUp(self) -> None:
        super().setUp()
        self.mono_component = self.create_json_mono(
            name="Source template", project=self.project
        )
        self.template_translation = self.mono_component.translation_set.get(
            language_code=self.mono_component.source_language.code
        )
        self.template_translation.filename = self.mono_component.template
        self.template_translation.save(update_fields=["filename"])
        self.assertTrue(self.template_translation.is_template)
        self.template_unit = self.template_translation.unit_set.create(
            source="Wait...",
            target="Wait...",
            context="",
            id_hash=calculate_hash("Wait...", ""),
            position=1,
            state=STATE_TRANSLATED,
        )
        self.template_unit.translate(
            self.user, "Wait...", STATE_TRANSLATED, propagate=False
        )
        self.template_unit.refresh_from_db()
        self.ellipsis_check = CHECKS["ellipsis"]
        self.assertTrue(
            Check.objects.filter(
                unit=self.template_unit, name="ellipsis", dismissed=False
            ).exists()
        )

    def test_denied_without_unit_template_permission(self) -> None:
        limited = User.objects.create_user(
            "limited-template", "limited-template@example.com", "x"
        )
        limited.groups.clear()
        group = Group.objects.create(
            name="Limited translate only", language_selection=SELECTION_ALL
        )
        group.roles.add(Role.objects.get(name="Translate"))
        group.components.add(self.mono_component)
        limited.groups.add(group)
        limited.clear_permissions_cache()
        self.assertFalse(limited.has_perm("unit.edit", self.template_unit))

        result = collect_fix_candidates(
            limited,
            Unit.objects.filter(translation__component=self.mono_component),
            self.mono_component.project,
            self.ellipsis_check,
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.denied, 1)

    def test_eligible_with_unit_template_permission(self) -> None:
        editor = User.objects.create_user(
            "source-editor", "source-editor@example.com", "x"
        )
        group = Group.objects.create(
            name="Edit source group", language_selection=SELECTION_ALL
        )
        group.roles.add(Role.objects.get(name="Edit source"))
        group.components.add(self.mono_component)
        editor.groups.add(group)
        editor.clear_permissions_cache()
        self.assertTrue(editor.has_perm("unit.edit", self.template_unit))
        self.assertTrue(editor.has_perm("unit.template", self.template_unit))

        result = collect_fix_candidates(
            editor,
            Unit.objects.filter(translation__component=self.mono_component),
            self.mono_component.project,
            self.ellipsis_check,
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.denied, 0)
        self.assertEqual(result.shown[0].final_target, ["Wait…"])


class CosmeticSourceChangeCascadeTest(ViewTestCase):
    """A cosmetic template-source fix updates siblings without fuzzing them."""

    def create_component(self) -> Component:
        return self.create_po_mono()

    def test_cosmetic_source_change_preserves_sibling_state(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")
        sibling.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)
        sibling.refresh_from_db()
        expected_state = sibling.state
        expected_original_state = sibling.original_state
        expected_previous_source = sibling.previous_source
        PendingUnitChange.objects.all().delete()

        source.translate(
            self.user,
            "Hello, universe!\n",
            STATE_TRANSLATED,
            mark_source_change_fuzzy=False,
        )

        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, universe!\n")
        self.assertEqual(sibling.state, expected_state)
        self.assertEqual(sibling.original_state, expected_original_state)
        self.assertEqual(sibling.previous_source, expected_previous_source)
        pending = sibling.pending_changes.get()
        self.assertEqual(pending.author, self.user)
        self.assertEqual(pending.target, "Ahoj světe!\n")
        self.assertEqual(pending.state, expected_state)
        self.assertTrue(
            Change.objects.filter(
                unit=sibling, action=ActionEvents.SOURCE_CHANGE
            ).exists()
        )

        source.translate(
            self.user,
            "Hello, galaxy!\n",
            STATE_TRANSLATED,
            mark_source_change_fuzzy=False,
        )
        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, galaxy!\n")
        self.assertEqual(sibling.pending_changes.count(), 1)


class CosmeticSourceChangeSiblingScopeTest(ViewTestCase):
    """Read-only and fileless siblings never receive a pending write."""

    def create_component(self) -> Component:
        return self.create_po_mono()

    def test_readonly_sibling_receives_no_pending_change(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")
        translation = sibling.translation
        translation.check_flags = "read-only"
        translation.save(update_fields=["check_flags"])

        source.translate(
            self.user,
            "Hello, universe!\n",
            STATE_TRANSLATED,
            mark_source_change_fuzzy=False,
        )

        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, universe!\n")
        self.assertEqual(sibling.pending_changes.count(), 0)

    def test_fileless_sibling_receives_no_pending_change(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")

        with patch.object(Translation, "get_filename", return_value=None):
            source.translate(
                self.user,
                "Hello, universe!\n",
                STATE_TRANSLATED,
                mark_source_change_fuzzy=False,
            )

        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, universe!\n")
        self.assertEqual(sibling.pending_changes.count(), 0)


class CosmeticSourceChangeCommitTest(ViewTestCase):
    """`commit_pending()` writes the new source while targets stay identical."""

    def create_component(self) -> Component:
        return self.create_po_mono()

    def test_commit_pending_writes_source_keeps_target_byte_identical(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")
        sibling.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)

        source.translate(
            self.user,
            "Hello, universe!\n",
            STATE_TRANSLATED,
            mark_source_change_fuzzy=False,
        )

        self.component.commit_pending("test", self.user)

        self.assertEqual(sibling.pending_changes.count(), 0)
        cs_content = get_optional_path(
            self.get_translation("cs").get_filename()
        ).read_text(encoding="utf-8")
        self.assertIn("Ahoj světe!", cs_content)
        en_content = get_optional_path(
            self.get_translation("en").get_filename()
        ).read_text(encoding="utf-8")
        self.assertIn("Hello, universe!", en_content)


class NonTemplateSourceLanguageCascadeTest(ViewTestCase):
    """A bilingual (non-template) source-language unit never cascades."""

    def create_component(self) -> Component:
        return self.create_po()

    def test_bilingual_source_edit_does_not_fuzz_siblings(self) -> None:
        translation = self.get_translation("cs")
        unit = translation.unit_set.get(source="Hello, world!\n")
        unit.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        expected_state = unit.state
        expected_previous_source = unit.previous_source
        pending_before = unit.pending_changes.count()

        source_translation = self.component.source_translation
        source_unit = source_translation.unit_set.get(source="Hello, world!\n")
        self.assertFalse(source_translation.is_template)
        source_unit.translate(
            self.user, "Hello, universe!\n", STATE_TRANSLATED, propagate=False
        )
        source_unit.refresh_from_db()
        self.assertEqual(source_unit.target, "Hello, universe!\n")
        unit.refresh_from_db()
        self.assertEqual(unit.state, expected_state)
        self.assertEqual(unit.previous_source, expected_previous_source)
        self.assertEqual(unit.pending_changes.count(), pending_before)


class OrdinarySourceChangeCascadeTest(ViewTestCase):
    """The cosmetic switch must not leak into normal source editing."""

    def create_component(self) -> Component:
        return self.create_po_mono()

    def test_default_source_edit_still_fuzzies_siblings(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")
        sibling.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, STATE_TRANSLATED)

        # No `mark_source_change_fuzzy` argument: the default path.
        source.translate(self.user, "Hello, universe!\n", STATE_TRANSLATED)

        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, universe!\n")
        self.assertIn(sibling.state, FUZZY_STATES)
        self.assertIn(sibling.original_state, FUZZY_STATES)
        self.assertEqual(sibling.previous_source, "Hello, world!\n")


class CosmeticSourceChangeAnonymousAuthorTest(ViewTestCase):
    """An unflushed pending row is updated, never duplicated (Task 3 step 3)."""

    def create_component(self) -> Component:
        return self.create_po_mono()

    def test_existing_pending_row_is_not_duplicated_without_an_author(self) -> None:
        source = self.get_unit("Hello, world!\n", "en")
        sibling = self.get_unit("Hello, world!\n", "cs")
        sibling.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)
        sibling.refresh_from_db()
        self.assertEqual(sibling.pending_changes.count(), 1)

        # `author` is derived from the acting user, so an anonymous
        # cosmetic cascade reaches the branch with `author=None`.
        source.translate(
            None,
            "Hello, universe!\n",
            STATE_TRANSLATED,
            mark_source_change_fuzzy=False,
        )

        sibling.refresh_from_db()
        self.assertEqual(sibling.source, "Hello, universe!\n")
        # Exactly one row, and it still carries the sibling's own target,
        # state and original author - not the source unit's.
        pending = sibling.pending_changes.get()
        self.assertEqual(pending.target, "Ahoj světe!\n")
        self.assertEqual(pending.state, sibling.state)
        self.assertEqual(pending.author, self.user)
