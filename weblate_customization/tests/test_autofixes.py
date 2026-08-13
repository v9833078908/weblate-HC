# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the deterministic autofixes."""

from __future__ import annotations

from django.test import SimpleTestCase
from weblate_customization.autofixes import (
    AddFrenchPunctuationSpacing,
    LineSeparatorSpacing,
    RemoveAddedFinalStop,
)

from weblate.trans.tests.factories import make_unit


class LineSeparatorSpacingTest(SimpleTestCase):
    fix = LineSeparatorSpacing()

    def test_strips_every_space_kind(self) -> None:
        unit = make_unit(source="Line$Next", code="fr")
        for space in (" ", "\t", "\u00a0", "\u2009", "\u202f"):
            self.assertEqual(
                self.fix.fix_target([f"Ligne{space}${space}Suivante"], unit),
                (["Ligne$Suivante"], True),
            )

    def test_clean_target_is_untouched(self) -> None:
        unit = make_unit(source="Line$Next", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Ligne$Suivante"], unit),
            (["Ligne$Suivante"], False),
        )

    def test_currency_source_is_left_alone(self) -> None:
        unit = make_unit(source="Price 5 $", code="fr")
        self.assertEqual(self.fix.fix_target(["Prix 5 $"], unit), (["Prix 5 $"], False))

    def test_separator_free_source_is_left_alone(self) -> None:
        unit = make_unit(source="Plain", code="fr")
        self.assertEqual(self.fix.fix_target(["5 $"], unit), (["5 $"], False))

    def test_ignore_flag_disables_the_fix(self) -> None:
        unit = make_unit(source="Line$Next", code="fr", flags="ignore-game-line-break")
        self.assertEqual(
            self.fix.fix_target(["Ligne \u00a0$ Suivante"], unit),
            (["Ligne \u00a0$ Suivante"], False),
        )

    def test_real_prod_regression(self) -> None:
        unit = make_unit(source="Идет скачивание$Пожалуйста$В зависимости", code="fr")
        target = (
            "Téléchargement en cours.$\u00a0Veuillez patienter.\u00a0$"  # codespell:ignore
            "Cela peut prendre plusieurs minutes."
        )
        expected = (
            "Téléchargement en cours.$Veuillez patienter.$"  # codespell:ignore
            "Cela peut prendre plusieurs minutes."
        )
        self.assertEqual(self.fix.fix_target([target], unit), ([expected], True))


class RemoveAddedFinalStopTest(SimpleTestCase):
    fix = RemoveAddedFinalStop()

    def test_removes_a_stop_the_source_does_not_have(self) -> None:
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient."], unit),
            (["Les pionniers rient"], True),
        )

    def test_keeps_a_stop_the_source_has(self) -> None:
        unit = make_unit(source="Les pionniers rient.", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient."], unit),
            (["Les pionniers rient."], False),
        )

    def test_removes_a_stop_the_source_does_not_end_with(self) -> None:
        # The source ends with an exclamation mark, so the full stop is still
        # one the source does not have and goes. Restoring the exclamation is
        # not something a fix can invent, so it stays a check.
        unit = make_unit(source="Les pionniers rient !", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient."], unit),
            (["Les pionniers rient"], True),
        )

    def test_repeated_dots_are_left_alone(self) -> None:
        # Dropping one dot of an unfinished ellipsis would only make it odder.
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient.."], unit),
            (["Les pionniers rient.."], False),
        )

    def test_short_source_is_left_alone(self) -> None:
        # The check skips short sources, where a shortcut translation is
        # expected, so the fix must not act on them either.
        unit = make_unit(source="Oui", code="fr")
        self.assertEqual(self.fix.fix_target(["Oui."], unit), (["Oui."], False))

    def test_ellipsis_is_left_alone(self) -> None:
        unit = make_unit(source="Un instant...", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Un instant."], unit), (["Un instant."], False)
        )

    def test_ignore_flag_disables_the_fix(self) -> None:
        unit = make_unit(
            source="Les pionniers rient", code="fr", flags="ignore-end-stop"
        )
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient."], unit),
            (["Les pionniers rient."], False),
        )

    def test_removes_added_exclamation_with_narrow_space(self) -> None:
        # Prod shape (unit 178826): the model added the mark and
        # AddFrenchPunctuationSpacing already put U+202F in front of it.
        unit = make_unit(source="А давайте", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Allons-y\u202f!"], unit), (["Allons-y"], True)
        )

    def test_keeps_added_colon_with_nbsp(self) -> None:
        # A colon can be a direct-speech marker. It was deliberately excluded
        # after the production measurement found seven unsafe Turkish cases.
        unit = make_unit(source="Старик сделал небольшую паузу и продолжил", code="fr")
        target = "Le vieil homme fait une pause et reprend\u00a0:"
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_removes_added_question_with_plain_space(self) -> None:
        unit = make_unit(source="Можно ли выпустить плесень", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Peut-on libérer la moisissure ?"], unit),
            (["Peut-on libérer la moisissure"], True),
        )

    def test_keeps_a_terminal_inside_a_closing_quote(self) -> None:
        # Measured on prod: reaching behind the quote repaired 17 units and
        # broke 13 of them. `…the inscription "armory."` is correct en_US
        # typography, and the rule must not touch it.
        unit = make_unit(
            source="Впереди двери с надписью \u00abоружейная\u00bb", code="en"
        )
        target = 'Ahead are large double doors with the inscription "armory."'
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_a_terminal_inside_a_closing_guillemet(self) -> None:
        unit = make_unit(source="Скорее, депеша", code="fr")
        target = "\u00abVite, la d\u00e9p\u00eache\u202f!\u00bb"
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_terminal_when_the_quoted_source_has_it(self) -> None:
        # Prod shape (unit 180448): the source mark hides behind a quote, so
        # the target mark is correct even though end_exclamation fails. This is
        # the one direction that IS unwrapped - the source side.
        unit = make_unit(source='Старейшина бежит на вас с криком "Еретик!"', code="fr")
        target = "L'Ancien se précipite sur toi en criant Hérétique\u202f!"  # codespell:ignore
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_a_mark_wrapped_in_markup(self) -> None:
        unit = make_unit(source="Скорее", code="fr")
        self.assertEqual(
            self.fix.fix_target(["<b>Vite\u202f!</b>"], unit),
            (["<b>Vite\u202f!</b>"], False),
        )

    def test_keeps_full_width_marks(self) -> None:
        unit = make_unit(source="Скорее", code="ja")
        self.assertEqual(self.fix.fix_target(["急いで！"], unit), (["急いで！"], False))

    def test_keeps_repeated_marks(self) -> None:
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient!!"], unit),
            (["Les pionniers rient!!"], False),
        )

    def test_keeps_interrobang(self) -> None:
        unit = make_unit(source="Ты серьёзно", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Tu es sérieux ?!"], unit),
            (["Tu es sérieux ?!"], False),
        )

    def test_refuses_to_empty_the_target(self) -> None:
        unit = make_unit(source="Осторожно, ликвидаторы", code="fr")
        self.assertEqual(self.fix.fix_target(["!"], unit), (["!"], False))

    def test_keeps_a_double_terminal_it_cannot_settle(self) -> None:
        # Dropping the dot would expose a question mark the source lacks:
        # the failing-check set changes instead of shrinking.
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Vraiment?."], unit), (["Vraiment?."], False)
        )

    def test_mark_specific_ignore_flag_disables_the_fix(self) -> None:
        unit = make_unit(source="А давайте", code="fr", flags="ignore-end-exclamation")
        self.assertEqual(
            self.fix.fix_target(["Allons-y\u202f!"], unit), (["Allons-y\u202f!"], False)
        )

    def test_fixes_every_plural_form(self) -> None:
        unit = make_unit(source=["Attends", "Attendez"], target=["", ""], code="fr")
        self.assertEqual(
            self.fix.fix_target(["Attends\u202f!", "Attendez\u202f!"], unit),
            (["Attends", "Attendez"], True),
        )


class AddFrenchPunctuationSpacingTest(SimpleTestCase):
    fix = AddFrenchPunctuationSpacing()

    def test_inserts_the_space_each_mark_needs(self) -> None:
        unit = make_unit(source="Attention: gathering over", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Attention: samosbor terminé"], unit),
            (["Attention\u00a0: samosbor terminé"], True),
        )

    def test_uses_a_narrow_space_before_the_other_marks(self) -> None:
        unit = make_unit(source="Really?", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Vraiment?"], unit), (["Vraiment\u202f?"], True)
        )

    def test_correct_spacing_is_untouched(self) -> None:
        unit = make_unit(source="Really?", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Vraiment\u202f?"], unit),
            (["Vraiment\u202f?"], False),
        )

    def test_other_languages_are_left_alone(self) -> None:
        unit = make_unit(source="Really?", code="cs")
        self.assertEqual(self.fix.fix_target(["Opravdu?"], unit), (["Opravdu?"], False))

    def test_canadian_french_is_left_alone(self) -> None:
        unit = make_unit(source="Really?", code="fr_CA")
        self.assertEqual(
            self.fix.fix_target(["Vraiment?"], unit), (["Vraiment?"], False)
        )


class TerminalAndFrenchSpacingOrderTest(SimpleTestCase):
    """Terminal removal runs before French spacing and survives a second pass."""

    fixes = (RemoveAddedFinalStop(), AddFrenchPunctuationSpacing())

    def run_fixes(self, target: list[str], unit) -> list[str]:
        for fix in self.fixes:
            target, _changed = fix.fix_target(target, unit)
        return target

    def test_no_dangling_space_survives_the_pair(self) -> None:
        unit = make_unit(source="А давайте", code="fr")
        first = self.run_fixes(["Allons-y!"], unit)
        self.assertEqual(first, ["Allons-y"])

    def test_second_pass_changes_nothing(self) -> None:
        unit = make_unit(source="А давайте", code="fr")
        first = self.run_fixes(["Allons-y\u202f!"], unit)
        self.assertEqual(self.run_fixes(first, unit), first)
