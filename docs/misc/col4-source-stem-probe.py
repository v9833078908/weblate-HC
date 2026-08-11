# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Measure Russian stem recovery on the COL4 source side, and its cost.

The source-side matcher only accepts a term when a non-letter or a string edge
sits on both sides of the exact form, so every Russian case ending hides the
term. This measures what a stem-level comparison would recover on COL4 and
prints the recovered word forms per term so each one can be judged by eye: a
recovered form is either the same term inflected, or a different word the
stemmer wrongly conflated.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import snowballstemmer

from weblate.trans.models import Project, Translation

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

project = Project.objects.get(slug="col4")
glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")

pairs: dict[str, str] = {}
for unit in fr_glossary.unit_set.all():
    if unit.source and unit.target:
        pairs[unit.source] = unit.target

ru_stemmer = snowballstemmer.stemmer("russian")


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def exact_hit(term: str, text: str) -> bool:
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


term_stems = {
    term: [ru_stemmer.stemWord(word.lower()) for word in WORD_RE.findall(term)]
    for term in pairs
}

units = []
for slug in ("data", "localizecommon"):
    translation = Translation.objects.get(
        component__project=project, component__slug=slug, language__code="fr"
    )
    units.extend(translation.unit_set.filter(state__gte=20).exclude(target=""))

recovered = Counter()
recovered_forms: dict[str, Counter] = defaultdict(Counter)
exact_strings = Counter()

for unit in units:
    source = unit.get_source_plurals()[0]
    words = WORD_RE.findall(source)
    stemmed = [ru_stemmer.stemWord(word.lower()) for word in words]
    for term, needle in term_stems.items():
        if exact_hit(term, source):
            exact_strings[term] += 1
            continue
        if not needle or len(needle) > len(stemmed):
            continue
        for i in range(len(stemmed) - len(needle) + 1):
            if stemmed[i : i + len(needle)] == needle:
                recovered[term] += 1
                recovered_forms[term][" ".join(words[i : i + len(needle)])] += 1
                break

print("TERM | STRINGS_EXACT | STRINGS_RECOVERED_BY_STEM | RECOVERED_FORMS")
for term in sorted(pairs, key=lambda t: -recovered[t]):
    if not recovered[term] and not exact_strings[term]:
        continue
    forms = ", ".join(
        f"{form}×{count}" for form, count in recovered_forms[term].most_common(8)
    )
    print(f"{term} | {exact_strings[term]} | {recovered[term]} | {forms}")

print()
print("TOTAL_STRINGS_EXACT", sum(exact_strings.values()))
print("TOTAL_STRINGS_RECOVERED", sum(recovered.values()))
