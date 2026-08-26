# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the game markup and script quality checks."""

from __future__ import annotations

from itertools import pairwise

from django.test import SimpleTestCase
from weblate_customization.checks import (
    CyrillicLeakCheck,
    GameLengthCheck,
    GameLineBreakCheck,
    GameMarkupCheck,
    GameNumberCheck,
    GameTokenCheck,
    conditional_dsl_syntax_spans,
    game_number_fails,
)

from weblate.checks.tests.test_checks import CheckTestCase
from weblate.trans.tests.factories import make_language, make_unit

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
AMOUNT_FORMATTED_FR = (
    "{value\u00a0:cond\u00a0:>99999\u202f?{value\u00a0:amount()}|}"
    "{value\u00a0:cond\u00a0:<=99999\u202f?{value\u00a0:N0}|}"
)
HUMAN_TIMER_FR = (
    "{hours\u00a0:cond\u00a0:>0\u202f?{hours}h. |}"
    "{minutes\u00a0:cond\u00a0:>0\u202f?{minutes}m. |}"
    "{seconds\u00a0:cond\u00a0:>=0\u202f?{seconds}s.|}"
)
HUMAN_TIMER_TR = (
    "{hours:cond:>0?{hours} sa. |}"
    "{minutes:cond:>0?{minutes} dk. |}"
    "{seconds:cond:>=0?{seconds} sn.|}"
)
NESTED_CONDITIONAL = "{a:cond:>0?{b:cond:>0?x|}y|}"
# The nested branch also carries a placeholder, so a change to its text leaves
# `placeholder_sequence` equal and only the conditional signature can catch it.
NESTED_PLACEHOLDER_CONDITIONAL = "{a:cond:>0?{b:cond:>0?{c}x|}y|}"


class ConditionalDslSyntaxSpansTest(SimpleTestCase):
    def test_protects_conditional_syntax_but_not_branch_text(self) -> None:
        for text in (AMOUNT_FORMATTED, TIMER):
            spans = conditional_dsl_syntax_spans(text)
            protected = "".join(text[start:end] for start, end in spans)
            self.assertIn(":cond:", protected)
            self.assertIn("?", protected)
            self.assertIn(
                "{value:amount()}" if text == AMOUNT_FORMATTED else "{hours:00}",
                protected,
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

    def test_a_nested_conditional_is_one_outermost_record(self) -> None:
        spans = conditional_dsl_syntax_spans(NESTED_CONDITIONAL)
        inner = NESTED_CONDITIONAL.index("{b")
        inner_block = "{b:cond:>0?x|}"

        self.assertEqual(spans, sorted(spans))
        self.assertTrue(all(left[1] <= right[0] for left, right in pairwise(spans)))
        self.assertIn((inner, inner + len(inner_block)), spans)
        self.assertNotIn((inner + 1, inner + 1 + len("b:cond:>0?")), spans)


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

    def test_rejects_whitespace_inside_conditional_syntax(self) -> None:
        for source, target in (
            (AMOUNT_FORMATTED, AMOUNT_FORMATTED_FR),
            (HUMAN_TIMER_EN, HUMAN_TIMER_FR),
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("hours:cond", "hours :cond", 1)),
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("cond:>0?", "cond: >0 ?", 1)),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(source, target, None))

    def test_rejects_changed_conditional_separators(self) -> None:
        for target in (
            HUMAN_TIMER_EN.replace("h. |}", "h. }", 1),
            HUMAN_TIMER_EN.replace("h. |}", "h. ||}", 1),
            HUMAN_TIMER_EN.replace("{hours}h. |", "|{hours}h. ", 1),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(HUMAN_TIMER_EN, target, None))

    def test_rejects_changed_conditional_headers(self) -> None:
        for source, target in (
            (HUMAN_TIMER_EN, HUMAN_TIMER_EN.replace("hours:cond", "hour:cond", 1)),
            (
                HUMAN_TIMER_EN,
                HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>=0?", 1),
            ),
            (
                HUMAN_TIMER_EN,
                HUMAN_TIMER_EN.replace("hours:cond:>0?", "hours:cond:>1?", 1),
            ),
            (AMOUNT_FORMATTED, AMOUNT_FORMATTED.replace(":<=99999?", ":<99999?", 1)),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(source, target, None))

    def test_rejects_a_malformed_conditional_target(self) -> None:
        for target in (HUMAN_TIMER_EN[:-1], HUMAN_TIMER_EN + "}"):
            with self.subTest(target=target):
                self.assertTrue(self.check.check_single(HUMAN_TIMER_EN, target, None))

    def test_a_nested_conditional_branch_is_immutable(self) -> None:
        self.assertTrue(
            self.check.check_single(
                NESTED_CONDITIONAL, NESTED_CONDITIONAL.replace("?x|", "?z|", 1), None
            )
        )
        self.assertFalse(
            self.check.check_single(
                NESTED_CONDITIONAL, NESTED_CONDITIONAL.replace("|}y|", "|}z|", 1), None
            )
        )

    def test_a_nested_conditional_branch_is_immutable_without_token_help(self) -> None:
        # The generic placeholder sequence is identical here, so this fails only
        # while the conditional signature carries the nested block verbatim.
        self.assertTrue(
            self.check.check_single(
                NESTED_PLACEHOLDER_CONDITIONAL,
                NESTED_PLACEHOLDER_CONDITIONAL.replace("}x|", "}z|", 1),
                None,
            )
        )

    def test_allows_localized_conditional_branch_text(self) -> None:
        self.assertFalse(self.check.check_single(HUMAN_TIMER_EN, HUMAN_TIMER_TR, None))

    def test_unrecognized_source_conditional_adds_no_failure(self) -> None:
        for text in ("{value:cond:1}", "Text {value:00}: text"):
            with self.subTest(text=text):
                self.assertFalse(self.check.check_single(text, text, None))

    def test_a_target_only_conditional_adds_no_failure(self) -> None:
        # `:cond:` in the source without a recognized conditional gives the
        # target no contract, even when the target grows one.
        self.assertFalse(
            self.check.check_single(
                "{value} :cond:", "{x:cond:>0?{value}|} :cond:", None
            )
        )


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


def fails(
    source: str, target: str, *, source_code: str = "en", target_code: str = "cs"
) -> bool:
    """Return the verdict for one pair, with both languages stated."""
    return game_number_fails(
        source, target, source_language=source_code, target_language=target_code
    )


def make_number_unit(source: str, target: str, *, source_code: str, target_code: str):
    """Build a unit whose component source language is not the factory default."""
    unit = make_unit(source=source, target=target, code=target_code)
    unit.translation.component.source_language = make_language(source_code)
    return unit


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

    def test_the_check_reads_both_languages_from_the_unit(self) -> None:
        source = "10 тысяч мон!"
        for target, target_code, expected in (
            ("報酬は10万文!", "ja", True),
            ("Reward - 10,000 mon!", "en", False),
        ):
            with self.subTest(target_code=target_code):
                unit = make_number_unit(
                    source, target, source_code="ru", target_code=target_code
                )
                self.assertEqual(
                    self.check.check_single(source, target, unit), expected
                )

    def test_scale_values_match_the_measured_matrix(self) -> None:
        source_10k = "10 тысяч мон!"
        source_100k = "Научись считать - ваще та 100 тысяч мон!"
        rows = (
            (source_10k, "Belohnung - 10 Tausend Mon!", "de", False),
            (source_10k, "Reward - 10,000 mon!", "en", False),
            (source_10k, "¡Recompensa: 10 000 mon!", "es", False),
            (source_10k, "Récompense - 10\u202f000 mons\u202f!", "fr", False),
            (source_10k, "Ricompensa - 10 mila mon!", "it", False),
            (source_10k, "보상은 10천 몬!", "ko", False),
            (source_10k, "奖励一万文!", "zh_Hans", False),
            (source_10k, "獎勵一萬文!", "zh_Hant", False),
            (source_10k, "報酬は10万文!", "ja", True),
            (
                source_100k,
                "Lern zählen - es sind 100 Tausend Mon!",  # codespell:ignore
                "de",
                False,
            ),
            (
                source_100k,
                "Learn to count, ya dumbass - it's 100,000 mon",
                "en",
                False,
            ),
            (source_100k, "¡Aprende a contar, son 100 000 mon!", "es", False),
            (source_100k, "Apprends à compter, c'est 100 000 mons", "fr", False),
            (source_100k, "Impara a contare, sono 100 mila mon!", "it", False),
            (source_100k, "계산 좀 배워, 10만 몬이라고!", "ko", False),
            (source_100k, "學會數數吧--那可是十萬文!", "zh_Hant", False),
            (source_100k, "数え方を覚えろよ、こいつは100万文だぜ!", "ja", True),
            (source_100k, "学学数数吧--那可是一百万文!", "zh_Hans", True),
        )
        for source, target, target_code, expected in rows:
            with self.subTest(target_code=target_code, target=target):
                self.assertEqual(
                    fails(source, target, source_code="ru", target_code=target_code),
                    expected,
                )

    def test_scale_notation_edge_cases(self) -> None:
        rows = (
            ("10 тысяч мон", "ru", "Belohnung 10 Tsd. Mon", "de", False),
            ("5 миллионов мон", "ru", "5 Mio. Mon", "de", False),
            ("10 тысяч мон", "ru", "10 Tausende Mon", "de", False),
            ("5 миллионов мон", "ru", "5 mln di mon", "it", False),
            ("5 миллионов мон", "ru", "5 M de mons", "fr", False),
            ("2 миллиарда мон", "ru", "2 mil millones de mon", "es", False),
            ("10 тысяч мон", "ru", "Reward 10k mon", "en", False),
            ("10 тысяч мон", "ru", "Reward 10 K mon", "en", False),
            ("2 млрд мон", "ru", "2,000,000,000 mon", "en", False),
            ("в 10 тысячах мон", "ru", "in 10,000 mon", "en", False),
            ("10 тысяч мон", "ru", "報酬は10k文", "ja", False),
            ("10 тысяч мон", "ru", "千万不要忘记，奖励10k文", "zh_Hans", False),
            ("10 тысяч мон!", "ru", "Ödül mon!", "tr", True),
            ("10 тысяч мон!", "ru", "Ödül 20 bin mon!", "tr", True),
            ("5 миллионов мон", "ru", "Nagroda 7 milionów mon", "pl", True),
            ("100 тысяч мон", "ru", "10 Tausend Mon", "de", True),
            ("100 тысяч мон", "ru", "10 Tsd. Mon", "de", True),
            ("Reward 100000", "en", "Награда 10 тысяч мон", "ru", True),
            ("уровень 3, награда 10 тысяч", "ru", "レベル3、報酬10万文", "ja", True),
            (
                "Reward 1 million 500 thousand",
                "en",
                "Reward 1 million",
                "en",
                True,
            ),
            ("Reward 0", "en", "報酬は0万文", "ja", False),
            ("Reward 1.2.3", "en", "報酬は1.2.3万文", "ja", False),
            ("Reward 10000", "en", "報酬は1万.以上", "ja", False),
            ("Reward 1000", "en", "Reward 1000.0", "en", False),
            (
                "Reward 1 million 500 thousand",
                "en",
                "Reward 1,500,000",
                "en",
                False,
            ),
            ("Reward 1 thousand 2 million", "en", "Reward 2001000", "en", True),
            ("Reward 1,000,000,000", "en", "Reward 10億", "ja", False),
            ("Reward 1 trillion", "en", "報酬は1兆文", "ja", False),
            ("Reward 15000000", "en", "報酬は1,500万文", "ja", False),
            ("Reward 100000", "en", "報酬は１０万文", "ja", False),
            (
                "Every third repair heals 10 HP",
                "en",
                "三回に一回の修理は10回復します",
                "ja",
                False,
            ),
            (
                "Never do that, 5 times",
                "en",
                "千万不要那样做，5次",
                "zh_Hans",
                False,
            ),
            ("10 thousand + 10 thousand", "en", "10000", "en", True),
            (
                "Reward 10000",
                "en",
                "Награда 10 тысяч мон",
                "ru",
                False,
            ),
            (
                "Награда 10 ТЫСЯЧ мон!",
                "ru",
                "Reward - 10,000 mon!",
                "en",
                False,
            ),
            (
                "Reward 1 million 1 thousand",
                "en",
                "Reward 1",
                "en",
                True,
            ),
        )
        for source, source_code, target, target_code, expected in rows:
            with self.subTest(source=source, target=target, target_code=target_code):
                self.assertEqual(
                    fails(
                        source,
                        target,
                        source_code=source_code,
                        target_code=target_code,
                    ),
                    expected,
                )

    def test_one_open_target_quantity_cannot_cover_two_source_quantities(
        self,
    ) -> None:
        self.assertTrue(
            fails(
                "10 thousand + 10 thousand",
                "10 thousand",
                source_code="en",
                target_code="en",
            )
        )

    def test_matching_uses_open_quantity_fallback_when_needed(self) -> None:
        self.assertFalse(
            fails(
                "10 thousand and 10000",
                "10000 and 10",
                source_code="en",
                target_code="en",
            )
        )

    def test_one_open_target_compound_cannot_cover_separate_source_numbers(
        self,
    ) -> None:
        self.assertTrue(
            fails(
                "1 + 500",
                "1 million 500 thousand",
                source_code="en",
                target_code="en",
            )
        )

    def test_a_number_the_target_adds_is_accepted(self) -> None:
        # Japanese counts what Russian words: "каждый третий" -> "3回に1回", on
        # top of a required "10" the source states and the target must keep -
        # a source with zero numbers would let the check's own early-return
        # guard hide a regression to symmetric comparison instead of proving
        # containment actually accepts the added counter digits.
        self.assertFalse(
            fails("Every third repair heals 10 HP", "3回に1回の修理は10回復します")
        )

    def test_a_full_date_is_not_a_quantity(self) -> None:
        self.assertFalse(fails("Starts on 14.04.2025", "Beginnt am 14. April 2025"))

    def test_a_placeholder_is_not_a_number(self) -> None:
        self.assertFalse(fails("Value {0}", "Wert {0}"))

    def test_a_lost_placeholder_is_not_this_checks_business(self) -> None:
        # game-markup owns placeholder integrity; two checks reporting one
        # defect would double every failing-check count.
        self.assertFalse(fails("Value {0}", "Wert"))

    def test_a_number_inside_markup_is_not_counted(self) -> None:
        self.assertFalse(fails("<size=14>Text</size>", "<size=14>Texte</size>"))

    def test_a_source_without_numbers_passes(self) -> None:
        self.assertFalse(fails("Plain text", "Texte 42"))

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(fails("Damage 200", ""))

    def test_thousands_grouping_is_a_locale_choice(self) -> None:
        # English groups with commas, other locales with spaces or dots; the
        # grouped value is one number, not a different one.
        self.assertFalse(fails("Costs 1,900,000 credits", "Coûte 1 900 000 crédits"))

    def test_dotted_thousands_match_spaced(self) -> None:
        self.assertFalse(fails("Reward 1.900.000 ISK", "Award 1 900 000 ISK"))

    def test_a_grouped_value_with_a_wrong_amount_still_fails(self) -> None:
        # Folding the grouping must not fold away a genuinely wrong amount.
        self.assertTrue(fails("Costs 1,900,000 credits", "Coûte 1 000"))

    def test_a_slash_date_is_not_a_quantity(self) -> None:
        self.assertFalse(fails("Limit from 01/12/2022", "Лимит с 01.12.2022"))

    def test_digits_in_a_url_are_not_quantities(self) -> None:
        self.assertFalse(fails("Survey https://forms.gle/aB4cd5", "Опрос"))

    def test_an_english_ordinal_label_need_not_survive(self) -> None:
        # "1st" is spelled out per language ("первую покупку"); its digit is a
        # label, not a quantity the target must carry.
        self.assertFalse(fails("SALE BONUS FOR 1ST BUY", "БОНУС ЗА ПЕРВУЮ ПОКУПКУ"))

    def test_an_ordinal_does_not_hide_a_dropped_quantity(self) -> None:
        # Removing the ordinal label must not silence a real number beside it.
        self.assertTrue(fails("1st prize is 500 gold", "Приз"))

    def test_a_dropped_bare_number_still_fails(self) -> None:
        # A plain number the target omits is a real signal, grouping aside.
        self.assertTrue(fails("Update 3.6 is out", "Встречайте!"))

    def test_european_single_dot_group_is_thousands(self) -> None:
        # "30.000" in German is 30000, not a decimal; it matches "30,000".
        self.assertFalse(fails("30,000 for victory", "30.000 für den Sieg"))

    def test_native_digit_scripts_are_the_same_number(self) -> None:
        # Arabic-Indic and Devanagari digits are the source number, restated.
        self.assertFalse(fails("30,000 for victory", "۳۰٬۰۰۰ برای پیروزی"))
        self.assertFalse(fails("15,000 for victory", "१५,००० जीत के लिए"))

    def test_a_wrong_value_across_locales_still_fails(self) -> None:
        # Folding scripts and grouping must not fold away a different amount.
        self.assertTrue(fails("30,000 for victory", "20.000 für Sieg"))

    def test_an_ordinal_in_the_target_keeps_the_source_number(self) -> None:
        # English renders the plain "24 декабря" as "December 24th". Dropping the
        # target's ordinal would report the source's own 24 as missing.
        self.assertFalse(fails("Начнутся 24 декабря", "Begins December 24th"))

    def test_an_ordinal_in_the_target_does_not_hide_a_dropped_number(self) -> None:
        self.assertTrue(
            fails("Начнутся 24 декабря, уровень 11", "Begins December 24th")
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
