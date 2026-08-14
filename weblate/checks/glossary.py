# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.utils.html import escape, format_html, format_html_join
from django.utils.translation import gettext, gettext_lazy

from weblate.checks.base import TargetCheck
from weblate.utils.csv import (
    PROHIBITED_INITIAL_CHARS,
    PROHIBITED_INITIAL_CHARS_FOR_DISPLAY,
)
from weblate.utils.html import format_html_join_comma

if TYPE_CHECKING:
    from weblate.trans.models import Unit

GLOSSARY_CHECK_ID = "check_glossary"


def evaluate_glossary_terms(
    unit: Unit, source: str, target: str
) -> tuple[set[str], set[str]]:
    """
    Evaluate glossary terms for one source/target pair.

    Returns a ``(hard_failures, advisories)`` tuple of term sources. Hard
    failures demand a rewrite (forbidden term present, or an exact/read-only
    term missing its exact form). Advisories only ask the translator or the
    model to verify the term: a plain inflectable term without an exact or
    morphological match.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.checks.morphology import SOURCE_STEM_LANGUAGES, count_inflected

    # ruff: ignore[import-outside-top-level]
    from weblate.glossary.models import get_glossary_term_modes, get_glossary_terms

    language = unit.translation.language
    source_language = unit.translation.component.source_language
    boundary = r"\b" if language.uses_whitespace() else ""
    source_boundary = r"\b" if source_language.uses_whitespace() else ""
    hard: set[str] = set()
    advisory: set[str] = set()
    matched: set[str] = set()

    for term in get_glossary_terms(unit, include_variants=False):
        term_source = term.source
        modes = get_glossary_term_modes(term)
        # Not applicable pairs are excluded for this target language
        if "not-applicable" in modes or term_source in matched:
            continue
        expected = term_source if "read-only" in modes else term.target
        exact_only = bool(modes & {"read-only", "exact", "forbidden"})

        if "forbidden" in modes:
            if re.search(
                rf"{boundary}{re.escape(expected)}{boundary}", target, re.IGNORECASE
            ):
                hard.add(term_source)
            matched.add(term_source)
            continue

        if re.search(
            rf"{boundary}{re.escape(expected)}{boundary}", target, re.IGNORECASE
        ):
            matched.add(term_source)
            advisory.discard(term_source)
            continue
        if exact_only:
            # Exact, read-only and forbidden terms never go through Snowball
            hard.add(term_source)
            continue

        # Morphology can only lift a failure, never create one. Compare by
        # occurrence counts, not by presence anywhere in the string: a source
        # with the term twice and a single matching target form must not pass.
        source_count = len(
            re.findall(
                rf"{source_boundary}{re.escape(term_source)}{source_boundary}",
                source,
                re.IGNORECASE,
            )
        )
        if source_language.base_code in SOURCE_STEM_LANGUAGES:
            source_count = max(
                source_count,
                count_inflected(term_source, source, source_language.code),
            )
        if count_inflected(expected, target, language.code) >= max(source_count, 1):
            matched.add(term_source)
            advisory.discard(term_source)
        else:
            advisory.add(term_source)

    return hard, advisory


class GlossaryCheck(TargetCheck):
    default_disabled = True
    check_id = GLOSSARY_CHECK_ID
    name = gettext_lazy("Does not follow glossary")
    description = gettext_lazy(
        "The translation does not follow terms defined in a glossary."
    )
    version_added = "4.5"

    def check_single(self, source: str, target: str, unit: Unit):
        hard, advisory = evaluate_glossary_terms(unit, source, target)
        return hard | advisory

    def get_description(self, check_obj):
        unit = check_obj.unit
        sources = unit.get_source_plurals()
        targets = unit.get_target_plurals()
        source = sources[0]
        hard: set[str] = set()
        advisory: set[str] = set()
        # Check singular
        singular_hard, singular_advisory = evaluate_glossary_terms(
            unit, source, targets[0]
        )
        hard |= singular_hard
        advisory |= singular_advisory
        # Do we have more to check?
        if len(sources) > 1:
            source = sources[1]
        # Check plurals against plural from source
        for target in targets[1:]:
            plural_hard, plural_advisory = evaluate_glossary_terms(unit, source, target)
            hard |= plural_hard
            advisory |= plural_advisory

        # A term demanding a rewrite for one plural form is not also a maybe
        advisory -= hard
        if not hard and not advisory:
            return super().get_description(check_obj)

        # The check is a single UI projection of the evaluation, but a term the
        # morphological comparison could not confirm must not be phrased as a
        # certain failure: the translation may legitimately carry a form the
        # comparison does not cover.
        messages = []
        if hard:
            messages.append(
                format_html(
                    escape(
                        gettext(
                            "Following terms are not translated according to glossary: {}"
                        )
                    ),
                    format_html_join_comma("{}", ((term,) for term in sorted(hard))),
                )
            )
        if advisory:
            messages.append(
                format_html(
                    escape(
                        gettext(
                            "Check the following terms; keep the translation if it "
                            "already uses a grammatical form of the term: {}"
                        )
                    ),
                    format_html_join_comma(
                        "{}", ((term,) for term in sorted(advisory))
                    ),
                )
            )
        return format_html_join(" ", "{}", ((message,) for message in messages))


class ProhibitedInitialCharacterCheck(TargetCheck):
    check_id = "prohibited_initial_character"
    name = gettext_lazy("Prohibited initial character")
    description = gettext_lazy("The string starts with a prohibited character in CSV.")
    # Process readonly (source) strings
    ignore_readonly = False
    glossary = True
    version_added = "5.9"

    def should_skip(self, unit: Unit) -> bool:
        if not unit.translation.component.is_glossary:
            return True
        return super().should_skip(unit)

    def check_single(self, source: str, target: str, unit: Unit) -> bool:
        """Check if the source string starts with a prohibited character."""
        return (target and target[0] in PROHIBITED_INITIAL_CHARS) or (
            source and source[0] in PROHIBITED_INITIAL_CHARS
        )

    def get_description(self, check_obj) -> str:
        """Return description of the check."""
        return format_html(
            escape(
                gettext(
                    "The string starts with one or more of the following forbidden characters: {}"
                )
            ),
            format_html_join_comma(
                "<code>{}</code>", PROHIBITED_INITIAL_CHARS_FOR_DISPLAY
            ),
        )
