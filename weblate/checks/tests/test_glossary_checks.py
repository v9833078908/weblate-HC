# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from random import choice
from typing import TYPE_CHECKING

from django.test import SimpleTestCase

from weblate.checks.glossary import (
    GlossaryCheck,
    ProhibitedInitialCharacterCheck,
    evaluate_glossary_terms,
)
from weblate.checks.models import Check
from weblate.trans.tests.factories import make_language, make_unit
from weblate.trans.tests.test_views import ComponentTestCase
from weblate.utils.csv import PROHIBITED_INITIAL_CHARS
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from weblate.trans.models import Unit


class GlossaryCheckTest(ComponentTestCase):
    check = GlossaryCheck()
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        super().setUp()
        self.unit = self.get_unit()
        self.unit.extra_flags = "check-glossary"
        with self.captureOnCommitCallbacks(execute=True):
            self.unit.translate(self.user, "Ahoj světe!\n", STATE_TRANSLATED)
        # Clear unit caches
        self.unit.check_cache = {}
        self.unit.glossary_terms = None
        self.glossary = self.project.glossaries[0].translation_set.get(
            language=self.unit.translation.language
        )

    def add_glossary(self, target: str, context="") -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self.glossary.add_unit(None, context, "hello", target, author=self.user)
        self.project.invalidate_glossary_cache()
        self.unit.glossary_terms = None

    def test_missing(self) -> None:
        self.assertFalse(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_good(self) -> None:
        self.add_glossary("ahoj")
        self.assertFalse(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_case_insensitive(self) -> None:
        self.add_glossary("Ahoj")
        self.assertFalse(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_forbidden(self) -> None:
        self.add_glossary("ahoj")
        self.glossary.unit_set.all().update(extra_flags="forbidden")
        self.assertTrue(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_bad(self) -> None:
        self.add_glossary("nazdar")
        self.assertTrue(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_multi(self) -> None:
        self.add_glossary("nazdar")
        self.add_glossary("ahoj", "2")
        self.assertFalse(
            self.check.check_target(
                self.unit.get_source_plurals(),
                self.unit.get_target_plurals(),
                self.unit,
            )
        )

    def test_description(self) -> None:
        """A plain term the comparison cannot confirm is phrased as a question."""
        self.test_bad()
        check = Check(unit=self.unit)
        self.assertEqual(
            self.check.get_description(check),
            "Check the following terms; keep the translation if it already uses "
            "a grammatical form of the term: hello",
        )

    def test_description_separates_hard_from_advisory(self) -> None:
        """Задача 4: a demanded rewrite is not phrased like an uncertain match."""
        # A forbidden term present in the target demands a rewrite.
        self.add_glossary("ahoj")
        self.glossary.unit_set.all().update(extra_flags="forbidden")
        # A plain term that is not found stays a question: cs has no Snowball
        # algorithm, so the comparison cannot confirm an inflected form.
        with self.captureOnCommitCallbacks(execute=True):
            self.glossary.add_unit(None, "w", "world", "svět", author=self.user)
        self.project.invalidate_glossary_cache()
        self.unit.glossary_terms = None

        check = Check(unit=self.unit)
        self.assertEqual(
            self.check.get_description(check),
            "Following terms are not translated according to glossary: hello"
            " Check the following terms; keep the translation if it already uses "
            "a grammatical form of the term: world",
        )

    def test_morphology_lifts_inflected_german_target(self) -> None:
        """Задача 3: a stem-matching German inflection lifts the check."""
        de_translation = self.component.translation_set.get(language_code="de")
        de_glossary = self.project.glossaries[0].translation_set.get(language_code="de")
        with self.captureOnCommitCallbacks(execute=True):
            de_glossary.add_unit(None, "", "Bauplan", "Bauplan", author=self.user)
        unit = de_translation.add_unit(
            None,
            context="",
            source="The Bauplan is ready.",
            target="Die Bauplänen sind fertig.",
            author=self.user,
            state=STATE_TRANSLATED,
        )
        unit.extra_flags = "check-glossary"
        # reset all_flags to reset cached_property
        unit.all_flags = unit.get_all_flags()
        unit.glossary_terms = None
        self.assertFalse(
            self.check.check_target(
                ["The Bauplan is ready."], ["Die Bauplänen sind fertig."], unit
            )
        )

    def test_morphology_exact_flag_blocks_lift(self) -> None:
        """Задача 3: exact/forbidden/read-only terms never go through Snowball."""
        de_translation = self.component.translation_set.get(language_code="de")
        de_glossary = self.project.glossaries[0].translation_set.get(language_code="de")
        with self.captureOnCommitCallbacks(execute=True):
            de_glossary.add_unit(
                None, "", "Bauplan", "Bauplan", author=self.user, extra_flags="exact"
            )
        unit = de_translation.add_unit(
            None,
            context="",
            source="The Bauplan is ready.",
            target="Die Bauplänen sind fertig.",
            author=self.user,
            state=STATE_TRANSLATED,
        )
        unit.extra_flags = "check-glossary"
        # reset all_flags to reset cached_property
        unit.all_flags = unit.get_all_flags()
        unit.glossary_terms = None
        self.assertTrue(
            self.check.check_target(
                ["The Bauplan is ready."], ["Die Bauplänen sind fertig."], unit
            )
        )


class ProhibitedInitialCharacterCheckTest(ComponentTestCase):
    check = ProhibitedInitialCharacterCheck()
    CREATE_GLOSSARIES = True

    def setUp(self) -> None:
        """Set up the test."""
        super().setUp()
        self.glossary = self.project.glossaries[0].translation_set.get(
            language_code="cs"
        )

    def add_glossary(self, source: str) -> Unit:
        """Add a glossary term."""
        return self.glossary.add_unit(
            None, context="", source=source, target=source, author=self.user
        )

    def get_term(self) -> str:
        # ruff: ignore[suspicious-non-cryptographic-random-usage]
        char = choice(list(PROHIBITED_INITIAL_CHARS))
        return f"{char} glossary term"

    def test_prohibited_initial_character(self) -> None:
        """Check that the check identifies prohibited characters."""
        valid_unit = self.add_glossary("glossary term")
        self.assertEqual(Check.objects.filter(name=self.check.check_id).count(), 0)
        self.assertFalse(
            self.check.check_target(["glossary term"], ["glossary term"], valid_unit)
        )

        for i, term in enumerate(PROHIBITED_INITIAL_CHARS, start=1):
            unit = self.add_glossary(term)
            self.assertEqual(
                Check.objects.filter(name=self.check.check_id).count(), i * 2
            )
            self.assertTrue(self.check.check_target([term], [term], unit))

    def test_ignore_prohibited_initial_character(self) -> None:
        """Check that the check can be ignored with flag."""
        term = self.get_term()
        unit = self.add_glossary(term)
        unit.extra_flags = "ignore-prohibited-initial-character"

        # reset all_flags to reset cached_property
        unit.all_flags = unit.get_all_flags()
        self.assertFalse(self.check.check_target([term], [term], unit))

    def test_non_glossary(self) -> None:
        self.assertEqual(Check.objects.filter(name=self.check.check_id).count(), 0)
        translation = self.get_translation()
        term = self.get_term()
        translation.add_unit(
            None, context="", source=term, target=term, author=self.user
        )
        self.assertEqual(Check.objects.filter(name=self.check.check_id).count(), 0)


class GlossaryMorphologyEvaluatorTest(SimpleTestCase):
    """
    Задача 3: target-side morphological lift, evaluate_glossary_terms.

    Pure unit tests independent of DB fixture languages, covering the
    allowlisted (ru/de/tr) and non-allowlisted (id) acceptance cases from
    docs/plans/2026-08-11-glossary-morphological-enforcement.md.
    """

    def build(
        self,
        *,
        target_code: str,
        term_source: str,
        term_target: str,
        source_text: str,
        target_text: str,
        term_flags: str = "",
        source_language_code: str = "en",
    ) -> Unit:
        unit = make_unit(code=target_code, source=source_text, target=target_text)
        unit.translation.component.source_language = make_language(source_language_code)
        term = make_unit(code=target_code, source=term_source, target=term_target)
        term.extra_flags = term_flags
        unit.glossary_terms = [term]
        return unit

    def test_positive_ru_korabl(self) -> None:
        unit = self.build(
            target_code="ru",
            term_source="Корабль",
            term_target="Корабль",
            source_text="Нужен Корабль здесь.",
            target_text="корабля больше нет",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Нужен Корабль здесь.", "корабля больше нет"
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())

    def test_positive_ru_dvigatel(self) -> None:
        unit = self.build(
            target_code="ru",
            term_source="Двигатель",
            term_target="Двигатель",
            source_text="Новый Двигатель установлен.",
            target_text="ремонт двигателя завершён",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Новый Двигатель установлен.", "ремонт двигателя завершён"
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())

    def test_positive_de_bauplan(self) -> None:
        unit = self.build(
            target_code="de",
            term_source="Bauplan",
            term_target="Bauplan",
            source_text="The Bauplan is ready.",
            target_text="Die Bauplänen sind fertig.",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "The Bauplan is ready.", "Die Bauplänen sind fertig."
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())

    def test_positive_de_galaktischer_traeger(self) -> None:
        unit = self.build(
            target_code="de",
            term_source="Galaktischer Träger",
            term_target="Galaktischer Träger",
            source_text="The Galaktischer Träger arrived.",
            target_text="Der Galaktischen Trägers wurde gesichtet.",
        )
        hard, advisory = evaluate_glossary_terms(
            unit,
            "The Galaktischer Träger arrived.",
            "Der Galaktischen Trägers wurde gesichtet.",
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())

    def test_positive_tr_case_ending(self) -> None:
        unit = self.build(
            target_code="tr",
            term_source="Modül",
            term_target="Modül",
            source_text="The Modül is ready.",
            target_text="Yeni modülüne bakın.",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "The Modül is ready.", "Yeni modülüne bakın."
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())

    def test_negative_id_dukungan_not_lifted(self) -> None:
        """Indonesian is not allowlisted: a stem collision must still fire."""
        unit = self.build(
            target_code="id",
            term_source="Dukungan",
            term_target="Dukungan",
            source_text="Dukungan diperlukan.",
            target_text="seorang pendukung datang",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Dukungan diperlukan.", "seorang pendukung datang"
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, {"Dukungan"})

    def test_acronym_target_is_not_lifted_by_morphology(self) -> None:
        """An abbreviation must not be accepted through a stem of itself."""
        unit = self.build(
            target_code="ru",
            term_source="институт",
            term_target="НИИ",
            source_text="Институт закрыт.",
            target_text="ни один не работает",
        )
        # "ни" shares the Russian stem of "НИИ" but is a particle, not the term.
        hard, advisory = evaluate_glossary_terms(
            unit, "Институт закрыт.", "ни один не работает"
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, {"институт"})

    def test_negative_id_batas_terakhir_not_lifted(self) -> None:
        unit = self.build(
            target_code="id",
            term_source="Batas terakhir",
            term_target="Batas terakhir",
            source_text="Batas terakhir sudah dekat.",
            target_text="Perbatasan Terakhir sudah dekat.",
        )
        hard, advisory = evaluate_glossary_terms(
            unit,
            "Batas terakhir sudah dekat.",
            "Perbatasan Terakhir sudah dekat.",
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, {"Batas terakhir"})

    def test_exact_flag_never_lifts(self) -> None:
        unit = self.build(
            target_code="ru",
            term_source="Корабль",
            term_target="Корабль",
            term_flags="exact",
            source_text="Нужен Корабль здесь.",
            target_text="корабля больше нет",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Нужен Корабль здесь.", "корабля больше нет"
        )
        self.assertEqual(hard, {"Корабль"})
        self.assertEqual(advisory, set())

    def test_read_only_never_lifts(self) -> None:
        unit = self.build(
            target_code="ru",
            term_source="Корабль",
            term_target="Крейсер",
            term_flags="read-only",
            source_text="Нужен Корабль здесь.",
            target_text="корабля больше нет",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Нужен Корабль здесь.", "корабля больше нет"
        )
        self.assertEqual(hard, {"Корабль"})
        self.assertEqual(advisory, set())

    def test_forbidden_fires_only_on_exact_form(self) -> None:
        unit = self.build(
            target_code="ru",
            term_source="Корабль",
            term_target="Корабль",
            term_flags="forbidden",
            source_text="Нужен Корабль здесь.",
            target_text="корабля больше нет",
        )
        # An inflected form of a forbidden term does not trigger it.
        hard, advisory = evaluate_glossary_terms(
            unit, "Нужен Корабль здесь.", "корабля больше нет"
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())
        # The exact forbidden form does.
        hard, advisory = evaluate_glossary_terms(
            unit, "Нужен Корабль здесь.", "старый Корабль тут"
        )
        self.assertEqual(hard, {"Корабль"})

    def test_occurrence_count_blocks_partial_lift(self) -> None:
        """A source with the term twice needs the target to match it twice."""
        unit = self.build(
            target_code="de",
            term_source="Bauplan",
            term_target="Bauplan",
            source_text="Bauplan und Bauplan.",
            target_text="Bauplänen sind da.",
        )
        hard, advisory = evaluate_glossary_terms(
            unit, "Bauplan und Bauplan.", "Bauplänen sind da."
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, {"Bauplan"})

        hard, advisory = evaluate_glossary_terms(
            unit, "Bauplan und Bauplan.", "Zweimal Bauplänen und Bauplänen."
        )
        self.assertEqual(hard, set())
        self.assertEqual(advisory, set())
