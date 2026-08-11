# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Measure what ``GlossaryCheck`` would report on the real COL4 French output.

Runs the product check unchanged over every translated French unit, then splits
what it reports into two piles:

* rescued -- the canonical target term is present, only inflected, so a
  stem-level comparison clears it. Feeding this to the LLM as a failing check
  orders a rewrite of a correct string.
* residual -- no form of the term is present. This is the class the gate exists
  for.

Separately counts source-side occurrences the matcher drops because a Russian
inflection puts a letter where the word-boundary rule wants a non-letter. Those
terms never reach the prompt at all for that string.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import snowballstemmer

from weblate.checks.glossary import GlossaryCheck
from weblate.glossary.models import fetch_glossary_terms
from weblate.trans.models import Project, Translation

CHUNK = 200
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

project = Project.objects.get(slug="col4")
glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")

pairs: dict[str, str] = {}
for unit in fr_glossary.unit_set.all():
    if unit.source and unit.target:
        pairs[unit.source] = unit.target

ru_stemmer = snowballstemmer.stemmer("russian")
fr_stemmer = snowballstemmer.stemmer("french")


def stems(text: str, stemmer) -> list[str]:
    return [stemmer.stemWord(word.lower()) for word in WORD_RE.findall(text)]


def contains_inflected(term: str, text: str, stemmer) -> bool:
    """Match the term as a contiguous run of stems inside the text."""
    needle = stems(term, stemmer)
    haystack = stems(text, stemmer)
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def source_occurrences(term: str, text: str) -> tuple[int, int, int]:
    """
    Return (accepted, inflected, noise) occurrences of the term.

    ``inflected`` means the term starts at a word boundary but a letter follows
    it, which is what a Russian case ending looks like. ``noise`` means a letter
    precedes the term, so the hit sits inside an unrelated or compound word and
    the boundary rule is right to drop it.
    """
    accepted = inflected = noise = 0
    haystack = text if is_acronym(term) else text.lower()
    needle = term if is_acronym(term) else term.lower()
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before and before.isalpha():
            noise += 1
        elif after and after.isalpha():
            inflected += 1
        else:
            accepted += 1
        start = haystack.find(needle, start + 1)
    return accepted, inflected, noise


check = GlossaryCheck()
all_units = []
for slug in ("data", "localizecommon"):
    translation = Translation.objects.get(
        component__project=project, component__slug=slug, language__code="fr"
    )
    all_units.extend(translation.unit_set.filter(state__gte=20).exclude(target=""))

reported = Counter()
rescued = Counter()
residual = Counter()
rescued_samples: dict[str, list[tuple[str, str]]] = defaultdict(list)
residual_samples: dict[str, list[tuple[str, str]]] = defaultdict(list)
inflected_miss = Counter()
noise = Counter()
visible = Counter()
inflected_samples: dict[str, list[str]] = defaultdict(list)
units_total = 0
units_reported = 0

for start in range(0, len(all_units), CHUNK):
    chunk = all_units[start : start + CHUNK]
    fetch_glossary_terms(chunk, include_variants=False)
    for unit in chunk:
        units_total += 1
        source = unit.get_source_plurals()[0]
        target = unit.get_target_plurals()[0]

        for term in pairs:
            accepted, missed, dropped = source_occurrences(term, source)
            visible[term] += accepted
            inflected_miss[term] += missed
            noise[term] += dropped
            if missed and len(inflected_samples[term]) < 3:
                inflected_samples[term].append(source)

        result = check.check_single(source, target, unit)
        if not result:
            continue
        units_reported += 1
        for term in result:
            reported[term] += 1
            rendering = pairs.get(term, "")
            if rendering and contains_inflected(rendering, target, fr_stemmer):
                rescued[term] += 1
                if len(rescued_samples[term]) < 3:
                    rescued_samples[term].append((source, target))
            else:
                residual[term] += 1
                if len(residual_samples[term]) < 3:
                    residual_samples[term].append((source, target))

print("GLOSSARY_TERMS", len(pairs))
print("UNITS_TRANSLATED", units_total)
print("UNITS_CHECK_WOULD_REPORT", units_reported)
print("REPORTED_TERM_OCCURRENCES", sum(reported.values()))
print("RESCUED_BY_STEM", sum(rescued.values()))
print("RESIDUAL", sum(residual.values()))
print("SOURCE_VISIBLE", sum(visible.values()))
print("SOURCE_INFLECTED_MISS", sum(inflected_miss.values()))
print("SOURCE_SUBSTRING_NOISE_CORRECTLY_DROPPED", sum(noise.values()))

print()
print("=== RESCUED (check fires, canonical term present but inflected)")
for term, count in rescued.most_common():
    print(f"{term!r} -> {pairs[term]!r}: {count}")
    for source, target in rescued_samples[term]:
        print(f"    SRC {source[:110]}")
        print(f"    TGT {target[:110]}")

print()
print("=== RESIDUAL (no form of the term present)")
for term, count in residual.most_common():
    print(f"{term!r} -> {pairs[term]!r}: {count}")
    for source, target in residual_samples[term]:
        print(f"    SRC {source[:110]}")
        print(f"    TGT {target[:110]}")

print()
print("=== SOURCE-SIDE MISSES (matcher drops an inflected Russian form)")
for term, count in inflected_miss.most_common():
    if not count:
        continue
    print(f"{term!r}: missed {count} / matched {visible[term]}")
    for source in inflected_samples[term]:
        print(f"    SRC {source[:110]}")
