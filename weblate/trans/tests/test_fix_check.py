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

import importlib.util
import sys
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Role, User
from weblate.checks.base import Highlight
from weblate.checks.models import CHECKS, Check
from weblate.lang.models import Language
from weblate.trans.actions import (
    ACTIONS_CONTENT,
    ACTIONS_REVERTABLE,
    ACTIONS_SHOW_CONTENT,
    ActionEvents,
)
from weblate.trans.fix_check import (
    MECHANICAL_GROUP_IDS,
    _compute_final_target,
    _decision_7_holds,
    _line_separator_spacing_new_targets,
    _protected_spans_preserved,
    _punctuation_spacing_new_targets,
    apply_fixup_python,
    collect_fix_candidates,
    collect_mechanical_group_candidates,
    collect_terminal_policy_candidates,
    mechanical_group_available,
    perform_fix,
    perform_mechanical_group_fix,
    perform_terminal_policy_fix,
)
from weblate.trans.models import Change, Component, Unit
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.models.pending import PendingUnitChange
from weblate.trans.models.translation import Translation
from weblate.trans.models.unit import calculate_hash
from weblate.trans.tests.factories import make_unit
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import get_optional_path
from weblate.utils.state import FUZZY_STATES, STATE_TRANSLATED

# See weblate/checks/tests/test_mass_fixup.py for why this is needed: the
# dev container copies `weblate_customization` onto `sys.path` at
# `/app/data/python`, but it is not an installed dependency of the root
# project, so tests exercising it need to add its own source root.
_CUSTOMIZATION_SRC = str(
    Path(__file__).resolve().parents[3] / "weblate_customization" / "src"
)
if _CUSTOMIZATION_SRC not in sys.path:
    sys.path.insert(0, _CUSTOMIZATION_SRC)


def _require_weblate_customization() -> None:
    try:
        spec = importlib.util.find_spec("weblate_customization.autofixes")
    except ImportError:
        spec = None
    if spec is None:  # pragma: no cover - environment guard
        msg = f"weblate_customization is not importable (tried {_CUSTOMIZATION_SRC})"
        raise SkipTest(msg)


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

    def test_dollar_group_reference_is_translated_to_python_syntax(self) -> None:
        # `PunctuationSpacingCheck.get_fixup` (and any future check) writes
        # replacements in JavaScript `String.replace()` syntax ("$1", "$2")
        # for `new RegExp(...).replace(...)` on the editor side; Python's
        # `re.sub` needs "\\1"/"\\2" instead; JS's "$1" bare in Python
        # would otherwise be copied through as the literal two characters
        # "$1", not a captured group.
        fixups = [("regex", r"([ \t])([:!])", "_$2", "gu")]
        self.assertEqual(apply_fixup_python(fixups, ["a :b"]), ["a_:b"])

    def test_dollar_ampersand_is_translated_to_whole_match(self) -> None:
        fixups = [("regex", r"\d+", "[$&]", "gu")]
        self.assertEqual(apply_fixup_python(fixups, ["x12y"]), ["x[12]y"])

    def test_double_dollar_is_a_literal_dollar_sign(self) -> None:
        fixups = [("regex", r"x", "$$1", "gu")]
        self.assertEqual(apply_fixup_python(fixups, ["x"]), ["$1"])

    def test_literal_backslash_in_replacement_is_preserved(self) -> None:
        fixups = [("regex", r"x", "a\\b", "gu")]
        self.assertEqual(apply_fixup_python(fixups, ["x"]), ["a\\b"])


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

    def test_added_terminal_mark_has_no_fixup_and_is_counted_apart(self) -> None:
        # The two directions of one terminal check are owned by different
        # mechanisms: this feature restores a mark the source has and the
        # translation lost, while a mark the translation added on its own
        # belongs to the autofix layer. A whole scope can consist of the
        # second kind - CoL4/data French is 2093 such rows - so that
        # direction is counted apart instead of being an unexplained
        # "needs manual review".
        self._fail_end_stop()
        reverse = self.get_unit(source="Hello, world!\n")
        reverse.source = "Hello world"
        reverse.save(update_fields=["source"])
        reverse.translate(self.user, "Ahoj svete.", STATE_TRANSLATED)
        reverse.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=reverse, name="end_stop", dismissed=False
            ).exists()
        )
        self.assertIsNone(self.end_stop_check.get_fixup(reverse))

        result = collect_fix_candidates(
            self.user,
            Unit.objects.filter(translation__component=self.component),
            self.project,
            self.end_stop_check,
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.manual, 1)
        self.assertEqual(result.manual_no_fixup, 1)
        self.assertEqual(len(result.shown), 1)
        self.assertEqual(result.shown[0].final_target, ["Dekuji."])

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


class TerminalPolicyEngineTest(ViewTestCase):
    """
    Engine-level behaviour for the aggregate terminal-source policy (Task A).

    docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md:
    dedup across checks, permissions, batching, history. The pure
    per-unit append/replace/remove computation itself is covered by
    `weblate.checks.tests.test_mass_fixup.TerminalSourcePolicyTest`; this
    class only exercises the DB-backed engine wrapped around it.
    """

    def setUp(self) -> None:
        super().setUp()
        self.stop_unit = self.get_unit(source="Thank you for using Weblate.")

    def _scope(self):
        return Unit.objects.filter(translation__component=self.component)

    def test_replace_dedups_across_two_failing_checks(self) -> None:
        # source ends "."; target "Diky!" fails both `end_stop` AND
        # `end_exclamation` simultaneously - must be counted once, as one
        # "replace", never twice.
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        failing = set(
            Check.objects.filter(unit=self.stop_unit, dismissed=False).values_list(
                "name", flat=True
            )
        )
        self.assertTrue({"end_stop", "end_exclamation"} <= failing)

        result = collect_terminal_policy_candidates(
            self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.by_operation["replace"], 1)
        self.assertEqual(result.by_operation["append"], 0)
        self.assertEqual(result.by_operation["remove"], 0)
        self.assertEqual(len(result.shown), 1)
        self.assertEqual(result.shown[0].unit.pk, self.stop_unit.pk)
        self.assertEqual(result.shown[0].operation, "replace")
        self.assertEqual(result.shown[0].final_target, ["Diky."])

    def test_perform_fix_clears_both_checks_from_one_write(self) -> None:
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        result = perform_terminal_policy_fix(self.user, self._scope(), self.project)
        self.assertEqual(result.fixed, 1)
        self.stop_unit.refresh_from_db()
        self.assertEqual(self.stop_unit.target, "Diky.")
        self.assertFalse(
            Check.objects.filter(
                unit=self.stop_unit,
                name__in=["end_stop", "end_exclamation"],
                dismissed=False,
            ).exists()
        )

    def test_append_via_aggregate_collector(self) -> None:
        self.stop_unit.translate(self.user, "Dekuji", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        result = collect_terminal_policy_candidates(
            self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.by_operation["append"], 1)
        self.assertEqual(result.shown[0].final_target, ["Dekuji."])

    def test_remove_via_aggregate_collector(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.source = "Hello world"
        unit.save(update_fields=["source"])
        unit.translate(self.user, "Ahoj svete.", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(unit=unit, name="end_stop", dismissed=False).exists()
        )
        result = collect_terminal_policy_candidates(
            self.user, self._scope(), self.project
        )
        row = next(r for r in result.shown if r.unit.pk == unit.pk)
        self.assertEqual(row.operation, "remove")
        self.assertEqual(row.final_target, ["Ahoj svete"])

    def test_denied_without_unit_edit_permission(self) -> None:
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        limited = User.objects.create_user(
            "limited-terminal", "limited-terminal@example.com", "x"
        )
        limited.groups.clear()
        limited.clear_permissions_cache()
        result = collect_terminal_policy_candidates(
            limited, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.denied, 1)

    def test_end_interrobang_unit_is_not_captured_by_this_policy(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.source = "Really?!"
        unit.save(update_fields=["source"])
        unit.translate(self.user, "Opravdu", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="end_interrobang", dismissed=False
            ).exists()
        )
        result = collect_terminal_policy_candidates(
            self.user, self._scope(), self.project
        )
        self.assertNotIn(unit.pk, [row.unit.pk for row in result.shown])

    def test_unit_ids_explicit_selection_only_applies_selected_unit(self) -> None:
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        other = self.get_unit(source="Hello, world!\n")
        # Not the fixture's own "Hello, world!\n": its trailing newline
        # means the raw last character is "\n", not "!", so no terminal
        # check fires at all - mutate to a clean, unwrapped "!" ending.
        other.source = "Save it!"
        other.save(update_fields=["source"])
        other.translate(self.user, "Ulozit", STATE_TRANSLATED)
        other.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=other, name="end_exclamation", dismissed=False
            ).exists()
        )
        result = perform_terminal_policy_fix(
            self.user, self._scope(), self.project, unit_ids=[self.stop_unit.pk]
        )
        self.assertEqual(result.fixed, 1)
        self.stop_unit.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.stop_unit.target, "Diky.")
        self.assertEqual(other.target, "Ulozit")  # untouched: outside the selection

    def test_stale_id_after_independent_fix_is_counted_not_reapplied(self) -> None:
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        perform_terminal_policy_fix(self.user, self._scope(), self.project)
        result = perform_terminal_policy_fix(
            self.user, self._scope(), self.project, unit_ids=[self.stop_unit.pk]
        )
        self.assertEqual(result.fixed, 0)
        self.assertEqual(result.stale_or_no_change, 1)

    def test_history_records_fix_failing_check_action(self) -> None:
        self.stop_unit.translate(self.user, "Diky!", STATE_TRANSLATED)
        self.stop_unit.refresh_from_db()
        perform_terminal_policy_fix(self.user, self._scope(), self.project)
        change = Change.objects.filter(
            unit=self.stop_unit, action=ActionEvents.FIX_FAILING_CHECK
        ).first()
        self.assertIsNotNone(change)
        self.assertEqual(change.target, "Diky.")


class MechanicalGroupEngineTest(ViewTestCase):
    """
    Engine-level behaviour for Task B's mechanical groups.

    docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md:
    eligibility split, dedup, permissions, protected spans.
    """

    def _scope(self):
        return Unit.objects.filter(translation__component=self.component)

    def test_double_space_via_mechanical_engine(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.translate(self.user, "Ahoj  svete!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="double_space", dismissed=False
            ).exists()
        )
        result = collect_mechanical_group_candidates(
            "double-space", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, ["Ahoj svete!\n"])
        fix_result = perform_mechanical_group_fix(
            "double-space", self.user, self._scope(), self.project
        )
        self.assertEqual(fix_result.fixed, 1)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Ahoj svete!\n")

    @staticmethod
    def _without_autofix(*excluded_substrings: str):
        """
        `override_settings` with core autofixes matching `excluded_substrings` removed.

        `begin_space`/`end_space`/`zero-width-space`/`end_ellipsis` are all
        owned by a core autofix that runs on *every* `Unit.translate()`
        call (`SameBookendingWhitespace`/`RemoveZeroSpace`/
        `ReplaceTrailingDotsWithEllipsis` - see the 2026-08-25 plan's
        ownership table), so a normal `.translate()` call auto-corrects the
        defect before any Check row can ever go active. A fixture for one
        of these checks - mirroring how such a row actually arises in
        production, from data imported or translated before that autofix
        existed - has to bypass the owning autofix for the one call that
        creates it, then let it run normally again afterwards.
        """
        return override_settings(
            AUTOFIX_LIST=[
                path
                for path in settings.AUTOFIX_LIST
                if not any(needle in path for needle in excluded_substrings)
            ]
        )

    def test_edge_space_remove_both_edges_at_once(self) -> None:
        # Source has zero leading/trailing ASCII spaces; a target with
        # extra spaces on *both* edges must have both removed in one edit.
        unit = self.get_unit(source="Thank you for using Weblate.")
        with self._without_autofix("SameBookendingWhitespace"):
            unit.translate(self.user, " Diky ", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="begin_space", dismissed=False
            ).exists()
        )
        self.assertTrue(
            Check.objects.filter(unit=unit, name="end_space", dismissed=False).exists()
        )
        result = collect_mechanical_group_candidates(
            "edge-space-remove", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, ["Diky"])
        fix_result = perform_mechanical_group_fix(
            "edge-space-remove", self.user, self._scope(), self.project
        )
        self.assertEqual(fix_result.fixed, 1)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Diky")

    def test_edge_space_source_syncs_to_source_count(self) -> None:
        unit = self.get_unit(source="Thank you for using Weblate.")
        unit.source = " Thank you "
        unit.save(update_fields=["source"])
        with self._without_autofix("SameBookendingWhitespace"):
            unit.translate(self.user, "Diky", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="begin_space", dismissed=False
            ).exists()
        )
        result = collect_mechanical_group_candidates(
            "edge-space-source", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, [" Diky "])
        # The remove-only policy must not also claim this row: source has
        # a nonzero count here, so it is out of scope for "remove".
        remove_result = collect_mechanical_group_candidates(
            "edge-space-remove", self.user, self._scope(), self.project
        )
        self.assertEqual(remove_result.total_eligible, 0)

    def test_edge_space_remove_excludes_source_edge_candidate(self) -> None:
        # Mirror of the above: a nonzero-source-count row must not be
        # claimed by "remove", which requires a *zero*-count source edge.
        unit = self.get_unit(source="Hello, world!\n")
        with self._without_autofix("SameBookendingWhitespace"):
            unit.translate(self.user, "  Ahoj svete!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        result = collect_mechanical_group_candidates(
            "edge-space-remove", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, ["Ahoj svete!\n"])

    def test_zero_width_space_removed_when_source_lacks_it(self) -> None:
        unit = self.get_unit(source="Thank you for using Weblate.")
        with self._without_autofix("RemoveZeroSpace"):
            unit.translate(self.user, "Diky\u200b", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="zero-width-space", dismissed=False
            ).exists()
        )
        result = collect_mechanical_group_candidates(
            "zero-width-space", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, ["Diky"])
        fix_result = perform_mechanical_group_fix(
            "zero-width-space", self.user, self._scope(), self.project
        )
        self.assertEqual(fix_result.fixed, 1)

    def test_end_ellipsis_replaces_trailing_dots(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.source = "Loading…"
        unit.save(update_fields=["source"])
        with self._without_autofix("ReplaceTrailingDotsWithEllipsis"):
            unit.translate(self.user, "Nahravani...", STATE_TRANSLATED)
        unit.refresh_from_db()
        self.assertTrue(
            Check.objects.filter(
                unit=unit, name="end_ellipsis", dismissed=False
            ).exists()
        )
        result = collect_mechanical_group_candidates(
            "end-ellipsis", self.user, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 1)
        self.assertEqual(result.shown[0].final_target, ["Nahravani…"])
        fix_result = perform_mechanical_group_fix(
            "end-ellipsis", self.user, self._scope(), self.project
        )
        self.assertEqual(fix_result.fixed, 1)

    def test_denied_without_unit_edit_permission(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.translate(self.user, "Ahoj  svete!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        limited = User.objects.create_user(
            "limited-mechanical", "limited-mechanical@example.com", "x"
        )
        limited.groups.clear()
        limited.clear_permissions_cache()
        result = collect_mechanical_group_candidates(
            "double-space", limited, self._scope(), self.project
        )
        self.assertEqual(result.total_eligible, 0)
        self.assertEqual(result.denied, 1)

    def test_history_records_fix_failing_check_action(self) -> None:
        unit = self.get_unit(source="Hello, world!\n")
        unit.translate(self.user, "Ahoj  svete!\n", STATE_TRANSLATED)
        unit.refresh_from_db()
        perform_mechanical_group_fix(
            "double-space", self.user, self._scope(), self.project
        )
        change = Change.objects.filter(
            unit=unit, action=ActionEvents.FIX_FAILING_CHECK
        ).first()
        self.assertIsNotNone(change)
        self.assertEqual(change.target, "Ahoj svete!\n")

    def test_mechanical_group_available_reflects_configuration(self) -> None:
        self.assertTrue(mechanical_group_available("double-space"))
        self.assertTrue(mechanical_group_available("edge-space-remove"))
        self.assertTrue(mechanical_group_available("zero-width-space"))
        self.assertTrue(mechanical_group_available("end-ellipsis"))
        self.assertTrue(mechanical_group_available("punctuation-spacing"))
        # game-line-break is a weblate_customization check, not registered
        # by default in the test settings.
        self.assertFalse(mechanical_group_available("line-separator-spacing"))

    def test_every_group_id_is_registered(self) -> None:
        for group_id in MECHANICAL_GROUP_IDS:
            with self.subTest(group_id=group_id):
                # Must not raise, whether available or not.
                collect_mechanical_group_candidates(
                    group_id, self.user, self._scope(), self.project
                )


class ProtectedSpanGuardTest(SimpleTestCase):
    """
    `_protected_spans_preserved` (Task B): protected span text must never change.

    Even though `DoubleSpaceCheck` and friends do not skip protected
    spans themselves.
    """

    def test_edit_outside_highlighted_span_is_preserved(self) -> None:
        unit = make_unit(code="ru", source="a %d b", target="a  %d  b")

        def fake_highlight(text, _unit):
            index = text.index("%d")
            return [Highlight(index, index + 2, "%d", kind="grammar")]

        with patch(
            "weblate.trans.fix_check.highlight_string", side_effect=fake_highlight
        ):
            self.assertTrue(_protected_spans_preserved(unit, ["a  %d  b"], ["a %d b"]))

    def test_edit_inside_highlighted_span_is_rejected(self) -> None:
        unit = make_unit(code="ru", source="a b", target="<tag  attr>text")

        def fake_highlight(text, _unit):
            end = text.index(">") + 1
            return [Highlight(0, end, text[:end], kind="markup")]

        with patch(
            "weblate.trans.fix_check.highlight_string", side_effect=fake_highlight
        ):
            self.assertFalse(
                _protected_spans_preserved(
                    unit, ["<tag  attr>text"], ["<tag attr>text"]
                )
            )

    def test_identical_targets_are_trivially_preserved(self) -> None:
        unit = make_unit(code="ru", source="a b", target="a b")
        with patch("weblate.trans.fix_check.highlight_string", return_value=[]):
            self.assertTrue(_protected_spans_preserved(unit, ["a b"], ["a b"]))


class PunctuationSpacingMechanicalTest(SimpleTestCase):
    """
    `_punctuation_spacing_new_targets` (Task B): core-only vs. combined group.

    Core-only wrong-spacing fix versus the combined wrong+missing group,
    gated on whether the fork's `AddFrenchPunctuationSpacing` autofix is
    active.
    """

    def test_core_only_fixes_wrong_spacing_but_never_adds_missing(self) -> None:
        # Wrong (plain-space) NBSP before ":" is fixed; a *missing* space
        # before "!" is left alone without the custom autofix active.
        unit = make_unit(code="fr", source="Options: Save!", target="Options : Save!")
        self.assertEqual(
            _punctuation_spacing_new_targets(unit), ["Options\u00a0: Save!"]
        )

    def test_combined_also_adds_missing_spacing_when_autofix_active(self) -> None:
        _require_weblate_customization()
        autofix_path = "weblate_customization.autofixes.AddFrenchPunctuationSpacing"
        with override_settings(AUTOFIX_LIST=[*settings.AUTOFIX_LIST, autofix_path]):
            unit = make_unit(
                code="fr", source="Options: Save!", target="Options : Save!"
            )
            self.assertEqual(
                _punctuation_spacing_new_targets(unit),
                ["Options\u00a0: Save\u202f!"],
            )

    def test_already_correct_spacing_is_a_no_op(self) -> None:
        unit = make_unit(code="fr", source="Options:", target="Options\u00a0:")
        self.assertIsNone(_punctuation_spacing_new_targets(unit))


class LineSeparatorSpacingMechanicalTest(SimpleTestCase):
    """
    `_line_separator_spacing_new_targets`/`mechanical_group_available` (Task B).

    Unavailable without the fork's own check and autofix, and the
    count/order of `$` is never touched, only hugging whitespace.
    """

    def test_unavailable_without_customization_configured(self) -> None:
        self.assertFalse(mechanical_group_available("line-separator-spacing"))
        unit = make_unit(
            code="ru", source="Line one$Line two", target="Line one $Line two"
        )
        self.assertIsNone(_line_separator_spacing_new_targets(unit))

    def test_strips_only_hugging_whitespace_when_configured(self) -> None:
        _require_weblate_customization()
        check_path = "weblate_customization.checks.GameLineBreakCheck"
        autofix_path = "weblate_customization.autofixes.LineSeparatorSpacing"
        with (
            override_settings(CHECK_LIST=[*settings.CHECK_LIST, check_path]),
            override_settings(AUTOFIX_LIST=[*settings.AUTOFIX_LIST, autofix_path]),
        ):
            self.assertTrue(mechanical_group_available("line-separator-spacing"))
            unit = make_unit(
                code="ru", source="Line one$Line two", target="Line one $Line two"
            )
            self.assertEqual(
                _line_separator_spacing_new_targets(unit), ["Line one$Line two"]
            )
