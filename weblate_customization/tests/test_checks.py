# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the game markup and script quality checks."""

from __future__ import annotations

from weblate_customization.checks import (
    CyrillicLeakCheck,
    GameLineBreakCheck,
    GameMarkupCheck,
    GameNumberCheck,
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


class GameNumberCheckTest(CheckTestCase):
    check = GameNumberCheck()

    def setUp(self) -> None:
        super().setUp()
        # The decimal separator is a locale choice, not a different number.
        self.test_good_matching = (
            "Radius 0.5 m for 1.5 seconds",
            "Rayon 0,5 m pendant 1,5 seconde",  # codespell:ignore
            "",
        )
        # A rebalanced value that never reached the source, or a stale source:
        # either way the two strings promise the player different things.
        self.test_failure_1 = (
            "Shield with 200 durability",
            "Bouclier avec 1200 de durabilite",
            "",
        )
        # A dropped clause takes its number with it.
        self.test_failure_2 = ("Deals 200 damage over 3 s", "Infliger des degats", "")
        # Two values, both wrong, and neither is absent - only the set differs.
        self.test_failure_3 = ("Deals 20% and 30%", "Infligge 30% e 10%", "")

    def test_a_number_the_target_adds_is_accepted(self) -> None:
        # Japanese counts what Russian words: "каждый третий" -> "3回に1回".
        self.assertFalse(
            self.check.check_single(
                "Every third repair is faster", "3回に1回の修理は速くなります", None
            )
        )

    def test_a_full_date_is_not_a_quantity(self) -> None:
        self.assertFalse(
            self.check.check_single(
                "Starts on 14.04.2025", "Beginnt am 14. April 2025", None
            )
        )

    def test_a_placeholder_is_not_a_number(self) -> None:
        self.assertFalse(self.check.check_single("Value {0}", "Wert {0}", None))

    def test_a_lost_placeholder_is_not_this_checks_business(self) -> None:
        # game-markup owns placeholder integrity; two checks reporting one
        # defect would double every failing-check count.
        self.assertFalse(self.check.check_single("Value {0}", "Wert", None))

    def test_a_number_inside_markup_is_not_counted(self) -> None:
        self.assertFalse(
            self.check.check_single(
                "<size=14>Text</size>", "<size=14>Texte</size>", None
            )
        )

    def test_a_source_without_numbers_passes(self) -> None:
        self.assertFalse(self.check.check_single("Plain text", "Texte 42", None))

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(self.check.check_single("Damage 200", "", None))
