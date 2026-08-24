# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Measure Russian stem recovery on the COL4 source side, and its cost.

The source-side matcher only accepts a term when a non-letter or a string edge
sits on both sides of the exact form, so every Russian case ending hides the
term. This runs the product stem index and matcher
(weblate.glossary.models.get_glossary_stem_automaton /
match_glossary_stems - the same functions fetch_glossary_terms falls back to)
to measure what it recovers on COL4, and prints the recovered word forms per
term so each one can be judged by eye: a recovered form is either the same
term inflected, or a different word the stemmer wrongly conflated.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from weblate.glossary.models import (
    get_glossary_stem_automaton,
    glossary_matcher_fingerprint,
    match_glossary_stems,
)
from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")

pairs: dict[str, str] = {}
for unit in fr_glossary.unit_set.all():
    if unit.source and unit.target:
        pairs[unit.source] = unit.target
pairs_by_lower = {source.lower(): source for source in pairs}


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def exact_hit(term: str, text: str) -> bool:
    """Use the same word-boundary rule as weblate/glossary/models.py:220-222."""
    haystack = text if is_acronym(term) else text.lower()
    needle = term if is_acronym(term) else term.lower()
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if (not before or not before.isalpha()) and (not after or not after.isalpha()):
            return True
        start = haystack.find(needle, start + 1)
    return False


units = []
for slug in ("data", "localizecommon"):
    translation = Translation.objects.get(
        component__project=project, component__slug=slug, language__code="fr"
    )
    units.extend(translation.unit_set.filter(state__gte=20).exclude(target=""))

source_language = translation.component.source_language
target_language = translation.language

print(
    "FINGERPRINT",
    glossary_matcher_fingerprint(project, source_language, target_language),
)

stem_automaton, term_sources = get_glossary_stem_automaton(
    project, source_language, target_language
)
if stem_automaton is None:
    print("No stem index built: source language not in SOURCE_STEM_LANGUAGES,")
    print("or every glossary term is exact/read-only/forbidden/not-applicable.")

recovered = Counter()
recovered_forms: dict[str, Counter] = defaultdict(Counter)
exact_strings = Counter()

for unit in units:
    source = unit.get_source_plurals()[0]
    for term in pairs:
        if exact_hit(term, source):
            exact_strings[term] += 1

    if stem_automaton is None:
        continue
    matches = match_glossary_stems(
        source, source_language, stem_automaton, term_sources
    )
    for term_lower, spans in matches.items():
        term = pairs_by_lower.get(term_lower, term_lower)
        if exact_hit(term, source):
            # Already visible to the exact matcher for this string; the stem
            # index does not get credit for a hit it did not need to make.
            continue
        recovered[term] += 1
        for start, end in spans:
            recovered_forms[term][source[start:end]] += 1

print("TERM | STRINGS_EXACT | STRINGS_RECOVERED_BY_STEM | RECOVERED_FORMS")
for term in sorted(pairs, key=lambda t: -recovered[t]):
    if not recovered[term] and not exact_strings[term]:
        continue
    forms = ", ".join(
        f"{form!r}:{count}" for form, count in recovered_forms[term].most_common(5)
    )
    print(f"{term} | {exact_strings[term]} | {recovered[term]} | {forms}")

print()
print("TOTAL_STRINGS_EXACT", sum(exact_strings.values()))
print("TOTAL_STRINGS_RECOVERED", sum(recovered.values()))
