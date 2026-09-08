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
    ZeroWidthSpaceCheck,
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
                samples = [
                    f"{prefix}{tail}" for tail in self.SAMPLE_TAILS
                ] + list(extra_samples)
                fixtures.append(
                    {
                        "pattern": pattern,
                        "flags": flags,
                        "replacement": replacement,
                        "samples": samples,
                    }
                )

        add(EllipsisCheck(), make_unit(code="ru", is_source=True, source="Wait...."))
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
