# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the engine line separator autofix."""

from __future__ import annotations

from django.test import SimpleTestCase
from weblate_customization.autofixes import LineSeparatorSpacing

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
