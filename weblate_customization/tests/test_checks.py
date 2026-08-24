# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the game markup and script quality checks."""

from __future__ import annotations

from itertools import pairwise

from weblate_customization.checks import (
    CyrillicLeakCheck,
    GameLengthCheck,
    GameLineBreakCheck,
    GameMarkupCheck,
    GameNumberCheck,
    GameTokenCheck,
    conditional_dsl_syntax_spans,
)

from weblate.checks.tests.test_checks import CheckTestCase
from weblate.trans.tests.factories import make_unit

AMOUNT_FORMATTED = (
    "{value:cond:>99999?{value:amount()}|}{value:cond:<=99999?{value:N0}|}"
)
TIMER = "{hours:cond:>0?{hours:00}:|}{minutes:00}:{seconds:00}"
HUMAN_TIMER_EN = (
    "{hours:cond:>0?{hours}h. |}"
    "{minutes:cond:>0?{minutes}m. |}"
    "{seconds:cond:>=0?{seconds}s.|}"
)
HUMAN_TIMER_DE = (
    "{hours:cond:>0?{hours}Std. |}"
    "{minutes:cond:>0?{minutes}Min. |}"
    "{seconds:cond:>=0?{seconds}Sek.|}"
)


class ConditionalDslSyntaxSpansTest(CheckTestCase):
    def test_protects_conditional_syntax_but_not_branch_text(self) -> None:
        for text in (AMOUNT_FORMATTED, TIMER):
            spans = conditional_dsl_syntax_spans(text)
            protected = "".join(text[start:end] for start, end in spans)
            self.assertIn(":cond:", protected)
            self.assertIn("?", protected)
            self.assertIn(
                "{value:amount()}", protected if text == AMOUNT_FORMATTED else text
            )
            self.assertEqual(spans, sorted(spans))
            self.assertTrue(all(left[1] <= right[0] for left, right in pairwise(spans)))
        self.assertIn(
            (TIMER.index("}:{") + 1, TIMER.index("}:{") + 2),
            conditional_dsl_syntax_spans(TIMER),
        )
        self.assertFalse(
            any(
                HUMAN_TIMER_DE[start:end] in {"Std.", "Min.", "Sek."}
                for start, end in conditional_dsl_syntax_spans(HUMAN_TIMER_DE)
            )
        )

    def test_malformed_or_nonconditional_text_has_no_conditional_spans(self) -> None:
        self.assertEqual(
            conditional_dsl_syntax_spans(AMOUNT_FORMATTED[:-1]),
            [],
        )
        self.assertEqual(conditional_dsl_syntax_spans(TIMER[:-1]), [])
        self.assertEqual(conditional_dsl_syntax_spans("{" * 100_000), [])
        self.assertEqual(
            conditional_dsl_syntax_spans("Text {value:00}: text"),
            [],
        )


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
        # A sprite carries its attribute after a space, not after "=", so the
        # tag pattern has to accept both spellings or the icon name is unchecked.
        self.test_failure_3 = (
            '+{0} health for <sprite name="human"> sailors',
            '+{0} Gesundheit für <sprite name="fire"> Matrosen',
            "game-markup",
        )

    def test_a_sprite_the_target_keeps_passes(self) -> None:
        self.assertFalse(
            self.check.check_single(
                'Damage to <sprite name="construction">',
                'Schaden an <sprite name="construction">',
                None,
            )
        )

    def test_angle_brackets_in_prose_are_not_tags(self) -> None:
        # Widening the attribute separator must not turn "<simple thing>" into a
        # tag, or every string with a stray bracket becomes a failure.
        self.assertFalse(self.check.check_single("a < b and c > d", "x < y", None))

    def test_placeholder_order_and_printf_tokens_are_preserved(self) -> None:
        source = "<b>{0} {playerName} %s %KEY%</b>"

        self.assertFalse(
            self.check.check_single(source, "<b>{0} {playerName} %s %KEY%</b>", None)
        )
        self.assertTrue(
            self.check.check_single(source, "<b>{playerName} {0} %s %KEY%</b>", None)
        )
        self.assertTrue(
            self.check.check_single(source, "<b>{0} {playerName} %KEY%</b>", None)
        )
        self.assertTrue(self.check.check_single(source, "<b>Value</b>", None))
        self.assertFalse(self.check.check_single(source, "", None))

    def test_conditional_dsl_highlights_leave_branch_text_translatable(self) -> None:
        unit = make_unit(source=AMOUNT_FORMATTED, code="de")
        highlights = self.check.check_highlight(AMOUNT_FORMATTED, unit)
        self.assertTrue(highlights)
        self.assertTrue(all(highlight.kind == "syntax" for highlight in highlights))
        self.assertFalse(
            any(
                highlight.text in {"Std.", "Min.", "Sek."}
                for highlight in self.check.check_highlight(HUMAN_TIMER_DE, unit)
            )
        )
        self.assertFalse(self.check.check_single(HUMAN_TIMER_EN, HUMAN_TIMER_DE, unit))


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
    def test_a_scale_word_is_part_of_the_value(self) -> None:
        source = "Награда - 10 тысяч мон!"
        for target, expected in (
            ("Belohnung - 10 Tausend Mon!", False),
            ("Reward - 10,000 mon!", False),
            ("¡Recompensa: 10 000 mon!", False),
            ("Récompense - 10 000 mons !", False),
            ("Ricompensa - 10 mila mon!", False),
            ("보상은 10천 몬!", False),
            ("奖励一万文!", False),
            ("獎勵一萬文!", False),
            ("報酬は10万文!", True),
        ):
            with self.subTest(target=target):
                self.assertEqual(self.check.check_single(source, target, None), expected)

    def test_a_myriad_scale_error_is_reported(self) -> None:
        source = "Научись считать - ваще та 100 тысяч мон!"
        for target, expected in (
            ("Lern zählen - es sind 100 Tausend Mon!", False),  # codespell:ignore
            ("Learn to count-it's 100,000 mon", False),
            ("¡Aprende a contar, son 100 000 mon!", False),
            ("Impara a contare, sono 100 mila mon!", False),
            ("계산 좀 배워, 10만 몬이라고!", False),
            ("學會數數吧--那可是十萬文!", False),
            ("数え方を覚えろよ、こいつは100万文だぜ!", True),
            ("学学数数吧--那可是一百万文!", True),
        ):
            with self.subTest(target=target):
                self.assertEqual(self.check.check_single(source, target, None), expected)

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

    def test_an_ordinal_in_the_target_keeps_the_source_number(self) -> None:
        # English renders the plain "24 декабря" as "December 24th". Dropping the
        # target's ordinal would report the source's own 24 as missing.
        self.assertFalse(
            self.check.check_single("Начнутся 24 декабря", "Begins December 24th", None)
        )

    def test_an_ordinal_in_the_target_does_not_hide_a_dropped_number(self) -> None:
        self.assertTrue(
            self.check.check_single(
                "Начнутся 24 декабря, уровень 11", "Begins December 24th", None
            )
        )


class GameTokenCheckTest(CheckTestCase):
    check = GameTokenCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = (
            "Install item_type[|{0}] on the ship",
            "Zainstaluj item_type[|{0}] na statku",
            "game-token",
        )
        # A translated identifier resolves to nothing at runtime.
        self.test_failure_1 = (
            "Install item_type[|{0}] on the ship",
            "Zainstaluj element_type[|{0}] na statku",
            "game-token",
        )
        # A dropped token loses the substitution entirely.
        self.test_failure_2 = (
            "Buy item_template[|{0}]",
            "Mua {0}",
            "game-token",
        )
        # Two tokens in, one translated: a set comparison of one would miss it.
        self.test_failure_3 = (
            "Hold skirmish_league_place[|place {0}] skirmish_league_id[|in {0}|in any]",
            "Bleib skirmish_league_place[|auf Platz {0}] Liga[|in {0}|in jeder]",
            "game-token",
        )

    def test_a_case_tag_is_not_part_of_the_identity(self) -> None:
        # The bracket body is translated, including the grammatical case tag.
        self.assertFalse(
            self.check.check_single(
                "Defeat campaign_enemy[gen|{0}]",
                "Zwycięż campaign_enemy[acc|{0} w kampanii]",
                None,
            )
        )

    def test_ordinary_bracketed_prose_is_not_a_token(self) -> None:
        # A bracket with no "|" is an index, not a substitution, so the word in
        # front of it is prose the target rewrites. The word has to sit right
        # against the bracket, or the test never reaches the "|" requirement.
        self.assertFalse(
            self.check.check_single("Slot[3] unlocked", "Fach 3 freigeschaltet", None)
        )

    def test_a_source_without_tokens_passes(self) -> None:
        self.assertFalse(self.check.check_single("Plain text", "item_type[|{0}]", None))

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(self.check.check_single("Buy item_template[|{0}]", "", None))


class GameLengthCheckTest(CheckTestCase):
    check = GameLengthCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = ("Chest space", "Espacio de cofre", "game-length")
        # A short label blown well past the minimum and the ratio.
        self.test_failure_1 = (
            "Claim reward",
            "Reclamar recompensa garantizada ahora mismo",
            "game-length",
        )
        # A long sentence growing past the global 1.35x ceiling.
        self.test_failure_2 = (
            "Every hero starts with a few basic items and equipment to begin",
            (
                "Jeder Held beginnt mit ein paar grundlegenden Gegenständen und "
                "Ausrüstung sowie weiteren Dingen, die den Start erleichtern"
            ),
            "game-length",
        )
        # A mid-length source (11..30) doubling past its 2.0x tier.
        self.test_failure_3 = (
            "Storage capacity",
            "Permanente Lagerkapazität des gesamten Lagers",
            "game-length",
        )

    def test_moderate_expansion_passes(self) -> None:
        # 11 -> 20 visible characters: a normal Romance-language growth, under 2x.
        self.assertFalse(
            self.check.check_single("Select item", "Seleccionar elemento", None)
        )

    def test_a_legitimate_short_label_passes(self) -> None:
        # 7 -> 25 is 3.6x, but the shortest tier only fires past 28 target
        # characters: a one-word UI label may grow that far legitimately.
        self.assertFalse(
            self.check.check_single("Storage", "Almacenamiento permanente", None)
        )

    def test_markup_and_placeholders_are_not_measured(self) -> None:
        # Only the visible text counts; the tags and placeholders around it do
        # not, so a long tag cannot push a short string over the threshold.
        self.assertFalse(
            self.check.check_single(
                "<color=#E3BA59>Superspace</color> {0}",
                "<color=#E3BA59>Superespacio</color> {0}",
                None,
            )
        )

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(self.check.check_single("Claim reward", "", None))
