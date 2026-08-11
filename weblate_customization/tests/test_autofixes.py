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
