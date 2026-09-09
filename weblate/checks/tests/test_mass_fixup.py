# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Task 1 of the mass-fix-failing-checks plan.

Covers the ``mass_fixup`` safety tiers, the terminal-punctuation fixup
helper in ``weblate.checks.chars``, the ``ellipsis`` fixup in
``weblate.checks.source``, Python/JavaScript fixup parity, and the
ownership boundary against the autofix layer
(``docs/product/plans/2026-08-25-mass-fix-failing-checks.md``).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from weblate.checks.chars import (
    BeginSpaceCheck,
    DoubleSpaceCheck,
    EndColonCheck,
    EndEllipsisCheck,
    EndExclamationCheck,
    EndInterrobangCheck,
    EndQuestionCheck,
    EndSemicolonCheck,
    EndSpaceCheck,
    EndStopCheck,
    KabyleCharactersCheck,
    KashidaCheck,
    PunctuationSpacingCheck,
    TerminalEdit,
    ZeroWidthSpaceCheck,
    terminal_source_edit,
)
from weblate.checks.models import CHECKS
from weblate.checks.source import EllipsisCheck
from weblate.trans.autofixes import fix_target
from weblate.trans.tests.factories import make_unit

# `weblate_customization` is not an installed dependency of the root
# `hcgameloc` project - the dev container copies it onto `sys.path` at
# `/app/data/python` (see AGENTS.md, "Deploying custom checks and
# machinery"). Make its own source root importable here too, so the
# review-tier ownership test exercises the real production autofix through
# the configured `fix_target()` path instead of skipping.
_CUSTOMIZATION_SRC = str(
    Path(__file__).resolve().parents[3] / "weblate_customization" / "src"
)
if _CUSTOMIZATION_SRC not in sys.path:
    sys.path.insert(0, _CUSTOMIZATION_SRC)


def apply_fixup(fixups, text: str) -> str:
    """Apply a `get_fixup()` result the way `apply_fixup_python` will (Task 2)."""
    if not fixups:
        return text
    for kind, pattern, replacement, flags in fixups:
        if kind != "regex":
            msg = f"unexpected fixup kind {kind!r}"
            raise ValueError(msg)
        count = 0 if "g" in flags else 1
        re_flags = re.IGNORECASE if "i" in flags else 0
        text = re.sub(pattern, replacement, text, count=count, flags=re_flags)
    return text


class MassFixupTierTest(SimpleTestCase):
    """Tiers are set explicitly per class, never inferred from `get_fixup`."""

    def test_safe_tier(self) -> None:
        self.assertEqual(DoubleSpaceCheck().mass_fixup, "safe")
        self.assertEqual(KabyleCharactersCheck().mass_fixup, "safe")
        self.assertEqual(EllipsisCheck().mass_fixup, "safe")

    def test_review_tier(self) -> None:
        self.assertEqual(EndStopCheck().mass_fixup, "review")
        self.assertEqual(EndColonCheck().mass_fixup, "review")
        self.assertEqual(EndQuestionCheck().mass_fixup, "review")
        self.assertEqual(EndExclamationCheck().mass_fixup, "review")
        self.assertEqual(EndInterrobangCheck().mass_fixup, "review")

    def test_untiered_checks_stay_none(self) -> None:
        # Autofix-owned defects (Ownership boundary table).
        self.assertIsNone(EndEllipsisCheck().mass_fixup)
        self.assertIsNone(BeginSpaceCheck().mass_fixup)
        self.assertIsNone(EndSpaceCheck().mass_fixup)
        self.assertIsNone(ZeroWidthSpaceCheck().mass_fixup)
        self.assertIsNone(PunctuationSpacingCheck().mass_fixup)
        # No measured occurrences / no fixup.
        self.assertIsNone(EndSemicolonCheck().mass_fixup)
        # regex-module syntax, not stdlib-re compatible.
        self.assertIsNone(KashidaCheck().mass_fixup)

    def test_every_tiered_check_is_registered(self) -> None:
        tiered = {
            check_id: check.mass_fixup
            for check_id, check in CHECKS.items()
            if check.mass_fixup is not None
        }
        self.assertEqual(
            tiered,
            {
                "double_space": "safe",
                "kabyle-characters": "safe",
                "ellipsis": "safe",
                "end_stop": "review",
                "end_colon": "review",
                "end_question": "review",
                "end_exclamation": "review",
                "end_interrobang": "review",
            },
        )


class EllipsisFixupTest(SimpleTestCase):
    check = EllipsisCheck()

    def test_four_dots_becomes_ellipsis_not_dot_ellipsis(self) -> None:
        unit = make_unit(code="ru", is_source=True, source="Loading....")
        fixup = self.check.get_fixup(unit)
        self.assertEqual(fixup, [("regex", r"\.{3,}", "…", "gu")])
        self.assertEqual(apply_fixup(fixup, unit.source), "Loading…")

    def test_mid_string_dots(self) -> None:
        unit = make_unit(code="ru", is_source=True, source="1...5")
        fixup = self.check.get_fixup(unit)
        self.assertEqual(apply_fixup(fixup, unit.source), "1…5")

    def test_fixup_clears_the_check(self) -> None:
        unit = make_unit(code="ru", is_source=True, source="Loading....")
        fixup = self.check.get_fixup(unit)
        fixed = apply_fixup(fixup, unit.source)
        self.assertTrue(self.check.check_source_unit([unit.source], unit))
        self.assertFalse(self.check.check_source_unit([fixed], unit))


class TerminalFixupTest(SimpleTestCase):
    """Per-check fixup tests: default/CJK/fr/el/ar/my variants and plurals."""

    def _fix(self, check, unit):
        fixup = check.get_fixup(unit)
        self.assertIsNotNone(fixup, "expected a fixup, got None")
        return apply_fixup(fixup, unit.target)

    def test_end_stop_default(self) -> None:
        unit = make_unit(code="ru", source="Save it.", target="Сохрани")
        self.assertEqual(self._fix(EndStopCheck(), unit), "Сохрани.")

    def test_end_stop_cjk_fullwidth(self) -> None:
        unit = make_unit(code="ja", source="Save it.", target="保存")
        self.assertEqual(self._fix(EndStopCheck(), unit), "保存。")

    def test_end_stop_no_source_mark_returns_none(self) -> None:
        unit = make_unit(code="ru", source="Save it", target="Сохрани")
        self.assertIsNone(EndStopCheck().get_fixup(unit))

    def test_end_colon_default(self) -> None:
        unit = make_unit(code="de", source="Options:", target="Optionen")
        self.assertEqual(self._fix(EndColonCheck(), unit), "Optionen:")

    def test_end_colon_cjk_fullwidth(self) -> None:
        unit = make_unit(code="ja", source="Options:", target="オプション")
        self.assertEqual(self._fix(EndColonCheck(), unit), "オプション：")

    def test_end_colon_french_nbsp(self) -> None:
        unit = make_unit(code="fr", source="Options:", target="Options")
        self.assertEqual(self._fix(EndColonCheck(), unit), "Options\u00a0:")

    def test_end_question_greek(self) -> None:
        unit = make_unit(code="el", source="Save it?", target="Αποθήκευση")
        self.assertEqual(self._fix(EndQuestionCheck(), unit), "Αποθήκευση\u037e")

    def test_end_question_arabic(self) -> None:
        unit = make_unit(code="ar", source="Save it?", target="حفظ")
        self.assertEqual(self._fix(EndQuestionCheck(), unit), "حفظ؟")

    def test_end_question_burmese(self) -> None:
        unit = make_unit(code="my", source="Save it?", target="သိမ်းမလား")
        fixed = self._fix(EndQuestionCheck(), unit)
        self.assertTrue(fixed.endswith("\u1038\u104b"))
        self.assertFalse(EndQuestionCheck().check_single(unit.source, fixed, unit))

    def test_end_question_burmese_does_not_double_trailing_visarga(self) -> None:
        # "သိမ်းမလား" already ends with U+1038 VISARGA (an ordinary Burmese
        # word-final marker); MY_QUESTION_MARK itself starts with U+1038,
        # so a naive append would double it ("...ားး။"). One pre-existing
        # trailing U+1038 must be consumed before the mark is appended, so
        # the seam is exactly one U+1038 followed by U+104B, never two
        # U+1038 in a row.
        unit = make_unit(code="my", source="Save it?", target="သိမ်းမလား")
        self.assertEqual(unit.target[-1], "\u1038")
        fixed = self._fix(EndQuestionCheck(), unit)
        self.assertEqual(fixed, unit.target[:-1] + "\u1038\u104b")
        self.assertFalse(fixed.endswith("\u1038\u1038\u104b"))
        self.assertFalse(EndQuestionCheck().check_single(unit.source, fixed, unit))

    def test_end_question_burmese_without_trailing_visarga(self) -> None:
        # A target that does not already end in U+1038 gets a plain,
        # unmodified append of the full compound mark.
        unit = make_unit(code="my", source="Save it?", target="ကျန်းမာရေ")
        self.assertNotEqual(unit.target[-1], "\u1038")
        fixed = self._fix(EndQuestionCheck(), unit)
        self.assertEqual(fixed, unit.target + "\u1038\u104b")
        self.assertFalse(EndQuestionCheck().check_single(unit.source, fixed, unit))

    def test_end_question_burmese_bare_visarga_target_is_refused(self) -> None:
        # A target that is *only* U+1038 (nothing real precedes it) cannot
        # be safely fixed: there is no anchoring non-whitespace character
        # for `strip_prefix` to consume it from, so an append would land
        # after it and double it. `get_fixup` must leave it untouched
        # (manual bucket), not produce "U+1038 U+1038 U+104B".
        unit = make_unit(code="my", source="Save it?", target="\u1038")
        fixed = self._fix(EndQuestionCheck(), unit)
        self.assertEqual(fixed, unit.target)
        self.assertNotEqual(fixed, "\u1038\u1038\u104b")

    def test_end_question_french_nnbsp(self) -> None:
        unit = make_unit(code="fr", source="Save it?", target="Sauvegarder")
        self.assertEqual(self._fix(EndQuestionCheck(), unit), "Sauvegarder\u202f?")

    def test_end_question_french_ca_unaffected(self) -> None:
        # PunctuationSpacingCheck carves fr_CA out of French spacing rules;
        # the fixup table must not add NNBSP there either.
        unit = make_unit(code="fr_CA", source="Save it?", target="Sauvegarder")
        self.assertEqual(self._fix(EndQuestionCheck(), unit), "Sauvegarder?")

    def test_end_exclamation_cjk_fullwidth(self) -> None:
        unit = make_unit(code="ja", source="Save it!", target="保存")
        self.assertEqual(self._fix(EndExclamationCheck(), unit), "保存！")

    def test_end_exclamation_french_nnbsp(self) -> None:
        unit = make_unit(code="fr", source="Save it!", target="Sauvegarder")
        self.assertEqual(self._fix(EndExclamationCheck(), unit), "Sauvegarder\u202f!")

    def test_end_interrobang_mirrors_source_tail(self) -> None:
        unit = make_unit(code="ru", source="Really?!", target="Правда")
        self.assertEqual(self._fix(EndInterrobangCheck(), unit), "Правда?!")

    def test_end_interrobang_mirrors_source_cjk_tail(self) -> None:
        unit = make_unit(code="ja", source="本当に？！", target="本当")
        self.assertEqual(self._fix(EndInterrobangCheck(), unit), "本当？！")

    def test_end_interrobang_french_gets_nnbsp_too(self) -> None:
        unit = make_unit(code="fr", source="Vraiment?!", target="Vraiment")
        fixed = self._fix(EndInterrobangCheck(), unit)
        self.assertEqual(fixed, "Vraiment\u202f?!")
        self.assertEqual(
            PunctuationSpacingCheck().check_single(unit.source, fixed, unit), []
        )

    def test_end_interrobang_no_source_mark_returns_none(self) -> None:
        unit = make_unit(code="ru", source="Really?", target="Правда")
        self.assertIsNone(EndInterrobangCheck().get_fixup(unit))

    def test_fixup_clears_the_selected_check(self) -> None:
        cases = [
            (EndStopCheck(), "ru", "Save it.", "Сохрани"),
            (EndColonCheck(), "de", "Options:", "Optionen"),
            (EndQuestionCheck(), "el", "Save it?", "Αποθήκευση"),
            (EndExclamationCheck(), "ja", "Save it!", "保存"),
            (EndInterrobangCheck(), "ru", "Really?!", "Правда"),
        ]
        for check, code, source, target in cases:
            with self.subTest(check=check.check_id, code=code):
                unit = make_unit(code=code, source=source, target=target)
                self.assertTrue(check.check_single(source, target, unit))
                fixed = self._fix(check, unit)
                self.assertFalse(check.check_single(source, fixed, unit))

    def test_plurals_each_form_fixed_independently(self) -> None:
        unit = make_unit(
            code="ru",
            source=["1 item.", "%d items."],
            target=["1 предмет", "%d предметов"],
        )
        fixup = EndStopCheck().get_fixup(unit)
        forms = unit.get_target_plurals()
        fixed = [apply_fixup(fixup, form) for form in forms]
        self.assertEqual(fixed[0], "1 предмет.")
        self.assertEqual(fixed[1], "%d предметов.")

    def test_plurals_empty_form_stays_empty(self) -> None:
        # An untranslated/unused plural form must never be turned into a
        # bare mark just because a sibling form needed fixing.
        unit = make_unit(
            code="ru",
            source=["1 item.", "%d items."],
            target=["1 предмет", "%d предметов"],
        )
        fixup = EndStopCheck().get_fixup(unit)
        self.assertEqual(apply_fixup(fixup, ""), "")

    def test_whitespace_only_target_untouched(self) -> None:
        unit = make_unit(code="ru", source="Save it.", target="   ")
        fixup = EndStopCheck().get_fixup(unit)
        self.assertEqual(apply_fixup(fixup, "   "), "   ")

    def test_idempotent_reapply(self) -> None:
        unit = make_unit(code="ru", source="Save it.", target="Сохрани")
        fixup = EndStopCheck().get_fixup(unit)
        once = apply_fixup(fixup, unit.target)
        twice = apply_fixup(fixup, once)
        self.assertEqual(once, twice)
        self.assertEqual(once, "Сохрани.")


class BroadenedSourceMarkDetectionTest(SimpleTestCase):
    """
    Detection must mirror each check's actual per-branch dispatch.

    `check_single` dispatches on the *target*'s language to decide which
    accepted-marks set applies, and a real failing row's source can end
    with any member of that set - not only the plain ASCII character - so
    `get_fixup` has to recognise the same. Every case here first proves the
    real `check_single` fires (so the test itself is not vacuous), then
    that `get_fixup` proposes a fixup that clears it.
    """

    def _assert_fixes(self, check, unit) -> None:
        self.assertTrue(
            check.check_single(unit.source, unit.target, unit),
            "test case invalid: check_single is not failing for this unit",
        )
        fixup = check.get_fixup(unit)
        self.assertIsNotNone(fixup, "check fires but get_fixup offered nothing")
        fixed = apply_fixup(fixup, unit.target)
        self.assertFalse(check.check_single(unit.source, fixed, unit))

    def test_end_stop_cjk_target_gated_on_source_colon(self) -> None:
        # EndStopCheck._check_ja's own gate: only entered for a CJK target
        # whose source ends in `:`/`;`; a source ending `.` for the same
        # target falls through to the default set instead (covered by
        # test_end_stop_cjk_fullwidth).
        self._assert_fixes(
            EndStopCheck(), make_unit(code="ja", source="Label:", target="ラベル")
        )
        self._assert_fixes(
            EndStopCheck(), make_unit(code="ja", source="Label;", target="ラベル")
        )

    def test_end_colon_cjk_target_gated_on_source_semicolon(self) -> None:
        self._assert_fixes(
            EndColonCheck(), make_unit(code="ja", source="Label;", target="ラベル")
        )

    def test_end_stop_cjk_fullwidth_source_non_cjk_target(self) -> None:
        self._assert_fixes(
            EndStopCheck(),
            make_unit(code="ru", source="保存する。", target="Сохрани"),
        )

    def test_end_colon_cjk_fullwidth_source_non_cjk_target(self) -> None:
        self._assert_fixes(
            EndColonCheck(),
            make_unit(code="ru", source="オプション：", target="Опции"),
        )

    def test_end_question_cjk_fullwidth_source_non_cjk_target(self) -> None:
        self._assert_fixes(
            EndQuestionCheck(),
            make_unit(code="ru", source="保存する？", target="Сохрани"),
        )

    def test_end_exclamation_cjk_fullwidth_source_non_cjk_target(self) -> None:
        self._assert_fixes(
            EndExclamationCheck(),
            make_unit(code="ru", source="保存する！", target="Сохрани"),
        )

    def test_end_stop_armenian_colon_as_stop_armenian_target(self) -> None:
        # hy's own accepted set treats a source ending "'" (Armenian comma, "\u055d") as a stop, but
        # only when the *target* is Armenian too - the branch is gated on
        # target language, not merely present in source.
        self._assert_fixes(
            EndStopCheck(),
            make_unit(code="hy", source="Ավարտել՝", target="Ավարտի"),
        )

    def test_end_stop_devanagari_danda_non_devanagari_target(self) -> None:
        # The default set includes "।" directly, so this fires for *any*
        # target language, not only hi/bn/or.
        self._assert_fixes(
            EndStopCheck(),
            make_unit(code="ru", source="काम खत्म करो।", target="Закончить работу"),
        )

    def test_end_stop_pipe_stop_hindi_target(self) -> None:
        # hi/bn/or's own set includes "|" as an informal stop, but only
        # when the target is hi/bn/or.
        self._assert_fixes(
            EndStopCheck(),
            make_unit(code="hi", source="Save it|", target="इसे सहेजें"),
        )

    def test_end_question_greek_narrow_gate_ignores_other_question_marks(self) -> None:
        # `_check_el` gates on a literal ASCII "?" only; a source ending in
        # some *other* question-family character (Arabic "؟") does not
        # trigger it, and `check_single` genuinely does not fire either.
        unit = make_unit(code="el", source="Αποθήκευση؟", target="Save")
        self.assertFalse(
            EndQuestionCheck().check_single(unit.source, unit.target, unit)
        )
        self.assertIsNone(EndQuestionCheck().get_fixup(unit))

    def test_end_question_burmese_narrow_gate_ignores_other_question_marks(
        self,
    ) -> None:
        unit = make_unit(code="my", source="သိမ်းမလား؟", target="Save")
        self.assertFalse(
            EndQuestionCheck().check_single(unit.source, unit.target, unit)
        )
        self.assertIsNone(EndQuestionCheck().get_fixup(unit))


class TerminalOverlapTest(SimpleTestCase):
    """A conflicting terminal mark is left for a human, never `?` -> `?.`."""

    def test_stop_source_question_target_is_untouched(self) -> None:
        unit = make_unit(code="ru", source="Save it.", target="Сохранить?")
        fixup = EndStopCheck().get_fixup(unit)
        fixed = apply_fixup(fixup, unit.target)
        self.assertEqual(fixed, "Сохранить?")
        self.assertNotEqual(fixed, "Сохранить?.")

    def test_question_source_stop_target_is_untouched(self) -> None:
        unit = make_unit(code="ru", source="Save it?", target="Сохранить.")
        fixup = EndQuestionCheck().get_fixup(unit)
        fixed = apply_fixup(fixup, unit.target)
        self.assertEqual(fixed, "Сохранить.")
        self.assertNotEqual(fixed, "Сохранить.?")

    def test_exclamation_source_question_target_is_untouched(self) -> None:
        unit = make_unit(code="ru", source="Save it!", target="Сохранить?")
        fixup = EndExclamationCheck().get_fixup(unit)
        self.assertEqual(apply_fixup(fixup, unit.target), "Сохранить?")


class FixupJavaScriptParityTest(SimpleTestCase):
    """
    Fixup regex parity between Python and JavaScript.

    Every regex fixup a tiered check emits must behave identically under
    Python `re` and be valid, identically-behaving JavaScript.
    """

    SAMPLE_TAILS = ("", "   ", "\t\n", "x", "x ", "x  ", "x.", "x?", "x…", "x`")

    def setUp(self) -> None:
        super().setUp()
        node = shutil.which("node")
        if node is None:
            msg = "node is not available on PATH"
            raise SkipTest(msg)
        self.node: str = node

    def _fixtures(self):
        fixtures = []

        def add(check, unit, prefix="target", extra_samples=()):
            fixups = check.get_fixup(unit)
            self.assertIsNotNone(fixups, f"{check.check_id} produced no fixup")
            for pattern, replacement, flags in ((p, r, f) for _, p, r, f in fixups):
                samples = [f"{prefix}{tail}" for tail in self.SAMPLE_TAILS] + list(
                    extra_samples
                )
                fixtures.append(
                    {
                        "pattern": pattern,
                        "flags": flags,
                        "replacement": replacement,
                        "samples": samples,
                    }
                )

        add(EllipsisCheck(), make_unit(code="ru", is_source=True, source="Wait...."))
        # The other two safe-tier checks. Task 1 step 5 wants parity for
        # every regex fixup a tiered check emits, and `KabyleCharactersCheck`
        # emits one tuple per confusable (`chars.py:214-218`), so the
        # samples must actually contain those characters.
        add(
            DoubleSpaceCheck(),
            make_unit(code="cs", source="a b", target="a  b"),
            extra_samples=("a  b", "a   b", "  lead", "trail  ", "a  b  c"),
        )
        kabyle_unit = make_unit(code="kab", source="Iγ", target="Iγ")
        add(
            KabyleCharactersCheck(),
            kabyle_unit,
            extra_samples=tuple(
                sample
                for confusable in KabyleCharactersCheck().confusable_to_standard
                for sample in (
                    confusable,
                    f"a{confusable}b",
                    f"{confusable}{confusable}",
                )
            ),
        )
        add(EndStopCheck(), make_unit(code="ru", source="Save it.", target="x"))
        add(EndStopCheck(), make_unit(code="ja", source="Save it.", target="x"))
        add(EndColonCheck(), make_unit(code="fr", source="Options:", target="x"))
        add(EndQuestionCheck(), make_unit(code="el", source="Save it?", target="x"))
        add(EndQuestionCheck(), make_unit(code="ar", source="Save it?", target="x"))
        add(EndQuestionCheck(), make_unit(code="fr", source="Save it?", target="x"))
        add(EndExclamationCheck(), make_unit(code="fr", source="Save it!", target="x"))
        add(EndInterrobangCheck(), make_unit(code="ru", source="Really?!", target="x"))
        add(
            EndInterrobangCheck(),
            make_unit(code="fr", source="Vraiment?!", target="x"),
        )
        # Burmese end_question: the only fixup using `strip_prefix` (its
        # pattern includes an optional literal U+1038 before the trailing
        # `\s*$`), covered separately because the generic Latin
        # `SAMPLE_TAILS` never end in U+1038 and would not exercise the
        # doubling-avoidance branch at all.
        add(
            EndQuestionCheck(),
            make_unit(code="my", source="Save it?", target="x"),
            extra_samples=[
                "သိမ်းမလား",  # ends in U+1038 - must not double it
                "ကျန်းမာရေ",  # does not end in U+1038 - plain append
                "\u1038",  # bare visarga
                "",
            ],
        )
        return fixtures

    def test_python_and_javascript_agree(self) -> None:
        fixtures = self._fixtures()

        # Every pattern must be valid JavaScript and every fixup must be
        # side-effect-free under Node - do both in one subprocess call.
        script = (
            "const fixtures = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "const out = fixtures.map(({pattern, flags, replacement, samples}) => {"
            "  const re = new RegExp(pattern, flags);"
            "  return samples.map((s) => s.replace(re, replacement));"
            "});"
            "process.stdout.write(JSON.stringify(out));"
        )
        result = subprocess.run(
            [self.node, "-e", script],
            input=json.dumps(fixtures),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"node rejected a fixup pattern: {result.stderr}",
        )
        js_results = json.loads(result.stdout)

        self.assertEqual(len(js_results), len(fixtures))
        for fixture, js_outputs in zip(fixtures, js_results, strict=True):
            py_outputs = [
                apply_fixup(
                    [
                        (
                            "regex",
                            fixture["pattern"],
                            fixture["replacement"],
                            fixture["flags"],
                        )
                    ],
                    sample,
                )
                for sample in fixture["samples"]
            ]
            self.assertEqual(
                py_outputs,
                js_outputs,
                f"Python/JS parity mismatch for pattern {fixture['pattern']!r}",
            )


class OwnershipBoundaryTest(SimpleTestCase):
    """
    Ownership tests from Task 1: mass-fix engine vs. the autofix layer.

    Task 1's two ownership tests: the mass-fix engine and the autofix layer
    must never fight over the same string.
    """

    def test_safe_tier_defects_survive_fix_target(self) -> None:
        # double_space: a mid-string double space is not owned by any
        # active autofix.
        unit = make_unit(code="cs", source="a b", target="a  b")
        fixed, applied = fix_target(["a  b"], unit)
        self.assertEqual(fixed, ["a  b"])
        self.assertEqual(applied, [])

        # kabyle-characters: a confusable Greek letter in a Kabyle target.
        unit = make_unit(code="kab", source="Iγ", target="Iγ")
        fixed, applied = fix_target(["Iγ"], unit)
        self.assertEqual(fixed, ["Iγ"])
        self.assertEqual(applied, [])

        # ellipsis: mid-string "..." in a source string. Not owned by
        # ReplaceTrailingDotsWithEllipsis, which only fires when the
        # *source* itself already ends with "…" (chars.py:40).
        unit = make_unit(code="ru", is_source=True, source="Loading... 50%")
        fixed, applied = fix_target(["Loading... 50%"], unit)
        self.assertEqual(fixed, ["Loading... 50%"])
        self.assertEqual(applied, [])

    def test_review_tier_repaired_target_survives_fix_target(self) -> None:
        try:
            spec = importlib.util.find_spec("weblate_customization.autofixes")
        except ImportError:
            spec = None
        if spec is None:  # pragma: no cover - environment guard
            msg = (
                f"weblate_customization is not importable (tried {_CUSTOMIZATION_SRC})"
            )
            raise SkipTest(msg)

        autofix_path = "weblate_customization.autofixes.RemoveAddedFinalStop"
        with override_settings(AUTOFIX_LIST=[*settings.AUTOFIX_LIST, autofix_path]):
            # end_stop: source has the mark, so RemoveAddedFinalStop's own
            # "the source lacks it" precondition is false and it must not
            # touch the engine's repair.
            unit = make_unit(code="ru", source="Save it.", target="Сохрани")
            fixup = EndStopCheck().get_fixup(unit)
            repaired = apply_fixup(fixup, unit.target)
            self.assertEqual(repaired, "Сохрани.")
            fixed, applied = fix_target([repaired], unit)
            self.assertEqual(fixed, [repaired])
            self.assertNotIn("Removed final stop", applied)

            # end_exclamation, same guarantee.
            unit2 = make_unit(code="ja", source="Save it!", target="保存")
            fixup2 = EndExclamationCheck().get_fixup(unit2)
            repaired2 = apply_fixup(fixup2, unit2.target)
            self.assertEqual(repaired2, "保存！")
            fixed2, applied2 = fix_target([repaired2], unit2)
            self.assertEqual(fixed2, [repaired2])
            self.assertEqual(applied2, [])

            # The remaining three review-tier checks carry the same
            # guarantee: a repaired target is never re-touched by the
            # autofix layer, so the two mass-mutation paths cannot fight.
            for check, unit_kwargs, expected in (
                (
                    EndColonCheck(),
                    {"code": "ru", "source": "Options:", "target": "Параметры"},
                    "Параметры:",
                ),
                (
                    EndQuestionCheck(),
                    {"code": "ru", "source": "Save it?", "target": "Сохранить"},
                    "Сохранить?",
                ),
                (
                    EndInterrobangCheck(),
                    {"code": "ru", "source": "Really?!", "target": "Правда"},
                    "Правда?!",
                ),
            ):
                with self.subTest(check=check.check_id):
                    unit3 = make_unit(**unit_kwargs)
                    repaired3 = apply_fixup(check.get_fixup(unit3), unit3.target)
                    self.assertEqual(repaired3, expected)
                    fixed3, applied3 = fix_target([repaired3], unit3)
                    self.assertEqual(fixed3, [repaired3])
                    self.assertNotIn("Removed final stop", applied3)


class TerminalSourcePolicyTest(SimpleTestCase):
    """
    `terminal_source_edit()`: the explicit append/replace/remove policy.

    Covers docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md
    Task A's worked examples plus the guard rails around them. Unlike
    `TerminalFixupTest` above, this exercises the new function directly,
    never `get_fixup()`.
    """

    def _edit(self, check, unit) -> TerminalEdit | None:
        return terminal_source_edit(check, unit)

    def _apply(self, edit: TerminalEdit | None, target: str) -> str:
        self.assertIsNotNone(edit)
        return apply_fixup([edit.fixup], target)

    # -- Worked examples from the plan ----------------------------------

    def test_replace_zh_hans_stop_to_fullwidth_exclamation(self) -> None:
        # source ends "!"; zh target's ideographic full stop becomes a
        # fullwidth exclamation mark
        unit = make_unit(
            code="zh_Hans",
            source="Buy some books to read!",
            target="这个书架空空如也，购买一些书籍供工人阅读。",
        )
        edit = self._edit(EndExclamationCheck(), unit)
        self.assertIsNotNone(edit)
        self.assertEqual(edit.operation, "replace")
        self.assertEqual(
            self._apply(edit, unit.target), "这个书架空空如也，购买一些书籍供工人阅读！"
        )

    def test_replace_en_question_source_stop_target(self) -> None:
        unit = make_unit(code="en", source="Save it?", target="Text.")
        edit = self._edit(EndQuestionCheck(), unit)
        self.assertEqual(edit.operation, "replace")
        self.assertEqual(self._apply(edit, unit.target), "Text?")

    def test_replace_ko_stop_source_exclamation_target_stays_ascii(self) -> None:
        # ko must get ASCII "." here, never fullwidth "。" - the renderer
        # bug this task also fixes (`_terminal_mark`).
        unit = make_unit(code="ko", source="Save it.", target="내용!")
        edit = self._edit(EndStopCheck(), unit)
        self.assertEqual(edit.operation, "replace")
        self.assertEqual(self._apply(edit, unit.target), "내용.")

    def test_replace_en_exclamation_source_stop_target_contraction(self) -> None:
        # "I can't!" -> "I can't.": the trailing apostrophe+letter of a
        # contraction must not look like a 1-3 letter abbreviation.
        unit = make_unit(code="en", source="I can't.", target="I can't!")
        edit = self._edit(EndStopCheck(), unit)
        self.assertEqual(edit.operation, "replace")
        self.assertEqual(self._apply(edit, unit.target), "I can't.")

    def test_append_ko_stop_stays_ascii_not_fullwidth(self) -> None:
        unit = make_unit(code="ko", source="General settings.", target="게임 설정")
        edit = self._edit(EndStopCheck(), unit)
        self.assertEqual(edit.operation, "append")
        fixed = self._apply(edit, unit.target)
        self.assertEqual(fixed, "게임 설정.")
        self.assertNotEqual(fixed, "게임 설정。")

    def test_remove_ascii_stop_when_source_has_no_mark(self) -> None:
        # CoL4/data/fr's dominant shape: pure removal.
        unit = make_unit(code="fr", source="Enregistrer", target="Enregistrer.")
        edit = self._edit(EndStopCheck(), unit)
        self.assertEqual(edit.operation, "remove")
        self.assertEqual(self._apply(edit, unit.target), "Enregistrer")

    def test_remove_fullwidth_stop_when_source_has_no_mark(self) -> None:
        unit = make_unit(code="zh_Hans", source="购买一些书籍", target="购买一些书籍。")
        edit = self._edit(EndStopCheck(), unit)
        self.assertEqual(edit.operation, "remove")
        self.assertEqual(self._apply(edit, unit.target), "购买一些书籍")

    def test_remove_ascii_exclamation_when_source_has_no_mark(self) -> None:
        # Not "Save now!": "now" is exactly 3 letters and the conservative
        # abbreviation filter deliberately also excludes ordinary short
        # words (see `_terminal_edit_stem_is_protected`'s docstring).
        unit = make_unit(
            code="en", source="Save immediately", target="Save immediately!"
        )
        edit = self._edit(EndExclamationCheck(), unit)
        self.assertEqual(edit.operation, "remove")
        self.assertEqual(self._apply(edit, unit.target), "Save immediately")

    # -- Explicit "do not remove" carve-outs -----------------------------

    def test_question_mark_is_never_removed(self) -> None:
        unit = make_unit(code="en", source="Can we proceed", target="Can we proceed?")
        self.assertIsNone(self._edit(EndQuestionCheck(), unit))

    def test_colon_is_never_removed(self) -> None:
        unit = make_unit(code="en", source="He said", target="He said:")
        self.assertIsNone(self._edit(EndColonCheck(), unit))

    # -- Exclusions -------------------------------------------------------

    def test_ellipsis_target_is_not_replaced(self) -> None:
        unit = make_unit(code="en", source="Save it?", target="Loading…")
        self.assertIsNone(self._edit(EndQuestionCheck(), unit))

    def test_doubled_mark_target_is_not_replaced(self) -> None:
        # A doubled mark is not a *single* conflicting mark, so this falls
        # through to the append delegation, which then correctly no-ops on
        # its own conflict guard - the same contract `get_fixup()` already
        # has (see `TerminalOverlapTest`): observably unchanged, whether or
        # not `terminal_source_edit` itself returns an edit.
        unit = make_unit(code="en", source="Save it?", target="Wait!!")
        edit = self._edit(EndQuestionCheck(), unit)
        fixed = apply_fixup([edit.fixup], unit.target) if edit else unit.target
        self.assertEqual(fixed, "Wait!!")

    def test_interrobang_target_is_not_touched_by_this_policy(self) -> None:
        unit = make_unit(code="ru", source="Save it.", target="Правда⁉")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_source_ending_semicolon_is_out_of_scope(self) -> None:
        # `get_fixup()` still treats CJK ";" as colon-equivalent for
        # append (unchanged, see `TerminalFixupTest`); the new policy
        # refuses the source outright, so it cannot misread it as "no
        # mark" either and offer to strip an earned target mark.
        unit = make_unit(code="ja", source="Label;", target="ラベル。")
        self.assertIsNone(self._edit(EndColonCheck(), unit))
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_quoted_source_tail_is_excluded_not_unwrapped(self) -> None:
        # Unlike `RemoveAddedFinalStop`, which unwraps a source closing
        # quote to still find the mark, this policy simply refuses.
        unit = make_unit(
            code="ru", source='с криком "Еретик!"', target="с криком «Еретик»"
        )
        self.assertIsNone(self._edit(EndExclamationCheck(), unit))

    def test_abbreviation_stem_is_protected(self) -> None:
        unit = make_unit(code="en", source="Save now", target="Dr.")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_cyrillic_abbreviation_stem_protected_when_source_cyrillic(self) -> None:
        unit = make_unit(code="ru", source="Заметка", target="др.")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_numeric_stem_is_protected(self) -> None:
        unit = make_unit(code="en", source="Chapter", target="Chapter 2.")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_version_like_stem_is_protected(self) -> None:
        unit = make_unit(code="en", source="Update", target="Update to v1.2.")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_url_like_stem_is_protected(self) -> None:
        unit = make_unit(code="en", source="Visit", target="Visit example.com.")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_mark_only_target_is_protected(self) -> None:
        unit = make_unit(code="en", source="Loading", target=".")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    # -- Exotic languages stay append-only, as before ----------------------

    def test_armenian_replace_is_not_offered(self) -> None:
        unit = make_unit(code="hy", source="Ավարտել։", target="Ավարտի?")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    def test_burmese_replace_is_not_offered(self) -> None:
        unit = make_unit(code="my", source="Save it.", target="သိမ်းမလား?")
        self.assertIsNone(self._edit(EndStopCheck(), unit))

    # -- Dedup: only the source's own family check proposes an edit -------

    def test_only_source_family_check_proposes_replace(self) -> None:
        unit = make_unit(code="en", source="Save it.", target="Text!")
        edit = self._edit(EndStopCheck(), unit)
        self.assertIsNotNone(edit)
        self.assertEqual(edit.operation, "replace")
        self.assertIsNone(self._edit(EndExclamationCheck(), unit))
        self.assertIsNone(self._edit(EndQuestionCheck(), unit))
        self.assertIsNone(self._edit(EndColonCheck(), unit))

    def test_end_interrobang_and_end_semicolon_never_offer_an_edit(self) -> None:
        unit = make_unit(code="en", source="Save it?!", target="Text.")
        self.assertIsNone(self._edit(EndInterrobangCheck(), unit))
        unit2 = make_unit(code="en", source="Label;", target="Value.")
        self.assertIsNone(self._edit(EndSemicolonCheck(), unit2))

    # -- Idempotence --------------------------------------------------------

    def test_replace_is_idempotent(self) -> None:
        # `terminal_source_edit` does not special-case "already correct" -
        # same precedent as `get_fixup()` (`TerminalOverlapTest`) - so a
        # second pass may still return an edit, but applying it must be a
        # true no-op: the observable contract, not the internal `None`.
        unit = make_unit(code="en", source="Save it?", target="Text.")
        edit = self._edit(EndQuestionCheck(), unit)
        once = self._apply(edit, unit.target)
        unit.target = once
        second = self._edit(EndQuestionCheck(), unit)
        twice = apply_fixup([second.fixup], once) if second else once
        self.assertEqual(once, twice)
        self.assertEqual(once, "Text?")
