# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the game markup and script quality checks."""

from __future__ import annotations

from weblate_customization.checks import (
    CyrillicLeakCheck,
    GameLineBreakCheck,
    GameMarkupCheck,
)

from weblate.checks.tests.test_checks import CheckTestCase
from weblate.trans.tests.factories import make_unit


class GameMarkupCheckTest(CheckTestCase):
    check = GameMarkupCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = (
            "<color=#abcdef>Value {0}</color>",
            "<color=#abcdef>Значение {0}</color>",
            "game-markup",
        )
        self.test_failure_1 = (
            "<color=#abcdef>Value</color>",
            "<color=#fedcba>Значение</color>",
            "game-markup",
        )
        self.test_failure_2 = ("Value {0}", "Значение {1}", "game-markup")


class GameLineBreakCheckTest(CheckTestCase):
    check = GameLineBreakCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = ("Line$Next", "Ligne$Suivante", "")
        # A dropped separator merges two lines on screen.
        self.test_failure_1 = ("Line$Next", "Ligne Suivante", "")
        # Two separators in, one out: a set comparison would miss this.
        self.test_failure_2 = ("Warning!$$Body", "Attention !$Corps", "")
        # Whitespace beside a separator renders as a stray indent.
        self.test_failure_3 = ("Line$Next", "Ligne$\u00a0Suivante", "")

    def test_currency_source_is_not_policed(self) -> None:
        self.assertFalse(self.check.check_single("Price 5 $", "Prix 5 $", None))

    def test_target_only_separator_is_not_policed(self) -> None:
        self.assertFalse(self.check.check_single("One line", "Une$ligne", None))

    def test_separator_free_strings_pass(self) -> None:
        self.assertFalse(self.check.check_single("Plain", "Simple", None))


class CyrillicLeakCheckTest(CheckTestCase):
    check = CyrillicLeakCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = ("Гигахрущ", "Gigastructure", "")
        # A clause left in the source script.
        self.test_failure_1 = ("Самосбор окончен", "ATTENTION: САМОСБОР ОКОНЧЕН", "")
        # A homoglyph is the same defect one character wide.
        self.test_failure_2 = ("Samosbor", "Sam\u043esbor", "")

    def test_a_cyrillic_target_language_is_not_policed(self) -> None:
        unit = make_unit(source="Gathering over", code="ru")
        self.assertTrue(self.check.should_skip(unit))

    def test_a_latin_target_language_is_policed(self) -> None:
        unit = make_unit(source="Самосбор окончен", code="fr")
        self.assertFalse(self.check.should_skip(unit))

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(self.check.check_single("Самосбор", "", None))
