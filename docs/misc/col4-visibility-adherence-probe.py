# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Compare glossary adherence by matcher visibility on the real COL4 output.

For every Russian source string that mentions a glossary term, the term is put
into two buckets:

* visible -- the word-boundary rule accepted the occurrence, so the term pair
  travelled into the prompt for that string;
* missed -- the only occurrences carry a Russian case ending, so the boundary
  rule dropped them and the term never reached the prompt.

Then it measures how often the canonical French rendering shows up in the
translation, comparing at stem level so a legitimate French inflection still
counts as adherence. The gap between the two buckets is the cost of the
source-side blind spot.
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

fr_stemmer = snowballstemmer.stemmer("french")


def stems(text: str) -> list[str]:
    return [fr_stemmer.stemWord(word.lower()) for word in WORD_RE.findall(text)]


def contains_inflected(term: str, text: str) -> bool:
    needle = stems(term)
    haystack = stems(text)
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def classify(term: str, text: str) -> str | None:
    """Return "visible", "missed", or None for this term in this source."""
    haystack = text if is_acronym(term) else text.lower()
    needle = term if is_acronym(term) else term.lower()
    seen_visible = seen_missed = False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before and before.isalpha():
            pass
        elif after and after.isalpha():
            seen_missed = True
        else:
            seen_visible = True
        start = haystack.find(needle, start + 1)
    if seen_visible:
        return "visible"
    if seen_missed:
        return "missed"
    return None


units = []
for slug in ("data", "localizecommon"):
    translation = Translation.objects.get(
        component__project=project, component__slug=slug, language__code="fr"
    )
    units.extend(translation.unit_set.filter(state__gte=20).exclude(target=""))

strings = Counter()
followed = Counter()
misses: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)

for unit in units:
    source = unit.get_source_plurals()[0]
    target = unit.get_target_plurals()[0]
    for term, rendering in pairs.items():
        bucket = classify(term, source)
        if bucket is None:
            continue
        strings[term, bucket] += 1
        if contains_inflected(rendering, target):
            followed[term, bucket] += 1
        elif len(misses[term, bucket]) < 2:
            misses[term, bucket].append((source, target))


def rate(term: str, bucket: str) -> str:
    total = strings[term, bucket]
    if not total:
        return "-"
    return (
        f"{followed[term, bucket]}/{total} = {followed[term, bucket] * 100 // total}%"
    )


print("TERM | VISIBLE_ADHERENCE | MISSED_ADHERENCE")
for term in sorted(pairs, key=lambda t: -(strings[t, "missed"])):
    if not strings[term, "visible"] and not strings[term, "missed"]:
        continue
    print(f"{term} -> {pairs[term]} | {rate(term, 'visible')} | {rate(term, 'missed')}")

for bucket in ("visible", "missed"):
    total = sum(v for (_, b), v in strings.items() if b == bucket)
    ok = sum(v for (_, b), v in followed.items() if b == bucket)
    print()
    print(f"TOTAL {bucket}: {ok}/{total} = {ok * 100 // total if total else 0}%")

print()
print("=== SAMPLES: term visible to the matcher, rendering still absent")
for (term, bucket), samples in misses.items():
    if bucket != "visible":
        continue
    for source, target in samples[:1]:
        print(f"{term!r} -> {pairs[term]!r}")
        print(f"    SRC {source[:130]}")
        print(f"    TGT {target[:130]}")

print()
print("=== SAMPLES: term dropped by the matcher, rendering absent")
for (term, bucket), samples in misses.items():
    if bucket != "missed":
        continue
    for source, target in samples[:1]:
        print(f"{term!r} -> {pairs[term]!r}")
        print(f"    SRC {source[:130]}")
        print(f"    TGT {target[:130]}")
