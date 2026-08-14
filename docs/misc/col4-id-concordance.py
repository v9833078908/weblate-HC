# Concordance audit for col4/data/id: for each glossary term, every distinct
# id word the translator used where the term matched in the source.
# Realia fragmentation = one ru term -> many id words. Read-only.
from __future__ import annotations

import re
from collections import Counter, defaultdict

from weblate.glossary.models import fetch_glossary_terms, get_glossary_term_modes
from weblate.checks.morphology import get_text_stems, iter_word_spans
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

CHUNK = 200

translation = Translation.objects.get(
    component__project__slug="col4", component__slug="data", language__code="id"
)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).select_related(
        "translation__component__source_language",
        "translation__language",
        "source_unit",
    )
)

matched = {}
for start in range(0, len(units), CHUNK):
    chunk = units[start : start + CHUNK]
    for unit in chunk:
        unit.glossary_terms = None
    fetch_glossary_terms(chunk, include_variants=False)
    for unit in chunk:
        matched[unit.pk] = list(unit.glossary_terms or [])


def stem_spans(term_source: str, text: str):
    needle = get_text_stems(term_source, "ru")
    if not needle:
        return []
    spans = list(iter_word_spans(text))
    haystack = get_text_stems(text, "ru")
    return [
        spans[i][1]
        for i in range(len(haystack) - len(needle) + 1)
        if haystack[i : i + len(needle)] == needle
    ]


# For each term: canonical id form -> count, plus every other id word form
# appearing at positions where the term was matched but the canonical form is
# absent. We approximate "what the translator wrote" by the id words that
# co-occur in misses, grouped per term.
used_variants: dict[str, Counter] = defaultdict(Counter)
canonical_used: Counter = Counter()

for unit in units:
    target_text = unit.get_target_plurals()[0]
    tl = target_text.lower()
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if modes & {"not-applicable", "forbidden"} or not term.target:
            continue
        expected = term.target
        if re.search(rf"\b{re.escape(expected)}\b", target_text, re.IGNORECASE):
            canonical_used[term.source] += 1
            continue
        if expected.lower() in tl:
            canonical_used[term.source] += 1
            continue
        # miss: record the surfaces of the source match for context only;
        # the variant the translator used must be eyeballed from samples.
        for surface in stem_spans(term.source, unit.source):
            pass

# Print only terms with >= 3 distinct surfaces across the whole corpus OR
# known fragmentation risk: named entities and faction words.
watch = [
    "ГИГАХРУЩ", "САМОСБОР", "Ликвидатор", "Партия", "Чистые", "Ячейка",
    "Община", "Плесень", "Настя", "Юля", "Комиссар", "Старейшина", "Курсант",
    "Пионер", "Блок", "Смена", "Знамя", "Мутант", "Послушник", "Хриплый",
]
for term_source in watch:
    # collect every target where the term matched, then count distinct word
    # forms within a window - too fuzzy; instead show canonical usage rate.
    total = sum(
        1
        for u in units
        if any(t.source == term_source for t in matched[u.pk])
    )
    used = canonical_used[term_source]
    print(f"{term_source!r}: matched in {total} units, canonical id form in {used}")

print()
print("=== GIGAKHRUSHCH / SWAKIT concordance: distinct renderings ===")
for term_source, canonical in (("ГИГАХРУЩ", None), ("САМОСБОР", None)):
    forms: Counter = Counter()
    for unit in units:
        if not any(t.source == term_source for t in matched[unit.pk]):
            continue
        target_text = unit.get_target_plurals()[0]
        # find likely renderings: uppercase words and words containing the stem
        for word in re.findall(r"[A-Za-z][A-Za-z-]+", target_text):
            wl = word.lower()
            if term_source == "ГИГАХРУЩ" and ("giga" in wl or "khrushch" in wl or "gigakhr" in wl):
                forms[word] += 1
            if term_source == "САМОСБОР" and ("swakit" in wl or "samosbor" in wl or "swa" in wl):
                forms[word] += 1
    print(f"{term_source!r}: {dict(forms.most_common())}")
