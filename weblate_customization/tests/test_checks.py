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
        # Japanese counts what Russian words: "каждый третий" -> "3回に1回", on
        # top of a required "10" the source states and the target must keep -
        # a source with zero numbers would let the check's own early-return
        # guard hide a regression to symmetric comparison instead of proving
        # containment actually accepts the added counter digits.
        self.assertFalse(
            self.check.check_single(
                "Every third repair heals 10 HP",
                "3回に1回の修理は10回復します",
                None,
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

    def test_thousands_grouping_is_a_locale_choice(self) -> None:
        # English groups with commas, other locales with spaces or dots; the
        # grouped value is one number, not a different one.
        self.assertFalse(
            self.check.check_single(
                "Costs 1,900,000 credits", "Coûte 1 900 000 crédits", None
            )
        )

    def test_dotted_thousands_match_spaced(self) -> None:
        self.assertFalse(
            self.check.check_single("Reward 1.900.000 ISK", "Award 1 900 000 ISK", None)
        )

    def test_a_grouped_value_with_a_wrong_amount_still_fails(self) -> None:
        # Folding the grouping must not fold away a genuinely wrong amount.
        self.assertTrue(
            self.check.check_single("Costs 1,900,000 credits", "Coûte 1 000", None)
        )

    def test_a_slash_date_is_not_a_quantity(self) -> None:
        self.assertFalse(
            self.check.check_single("Limit from 01/12/2022", "Лимит с 01.12.2022", None)
        )

    def test_digits_in_a_url_are_not_quantities(self) -> None:
        self.assertFalse(
            self.check.check_single("Survey https://forms.gle/aB4cd5", "Опрос", None)
        )

    def test_an_english_ordinal_label_need_not_survive(self) -> None:
        # "1st" is spelled out per language ("первую покупку"); its digit is a
        # label, not a quantity the target must carry.
        self.assertFalse(
            self.check.check_single(
                "SALE BONUS FOR 1ST BUY", "БОНУС ЗА ПЕРВУЮ ПОКУПКУ", None
            )
        )

    def test_an_ordinal_does_not_hide_a_dropped_quantity(self) -> None:
        # Removing the ordinal label must not silence a real number beside it.
        self.assertTrue(self.check.check_single("1st prize is 500 gold", "Приз", None))

    def test_a_dropped_bare_number_still_fails(self) -> None:
        # A plain number the target omits is a real signal, grouping aside.
        self.assertTrue(
            self.check.check_single("Update 3.6 is out", "Встречайте!", None)
        )

    def test_european_single_dot_group_is_thousands(self) -> None:
        # "30.000" in German is 30000, not a decimal; it matches "30,000".
        self.assertFalse(
            self.check.check_single("30,000 for victory", "30.000 für den Sieg", None)
        )

    def test_native_digit_scripts_are_the_same_number(self) -> None:
        # Arabic-Indic and Devanagari digits are the source number, restated.
        self.assertFalse(
            self.check.check_single("30,000 for victory", "۳۰٬۰۰۰ برای پیروزی", None)
        )
        self.assertFalse(
            self.check.check_single("15,000 for victory", "१५,००० जीत के लिए", None)
        )

    def test_a_wrong_value_across_locales_still_fails(self) -> None:
        # Folding scripts and grouping must not fold away a different amount.
        self.assertTrue(
            self.check.check_single("30,000 for victory", "20.000 für Sieg", None)
        )
