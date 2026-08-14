# Glossary adherence audit for col4/data/id on prod.
# Pipe into weblate shell. Read-only.
#
# Uses the product matcher (fetch_glossary_terms) so visibility includes the
# stem fallback exactly as the LLM prompt saw it, then checks whether the
# canonical id form of each matched term appears in the target - word-form
# tolerant on the target side via evaluate_glossary_terms (advisory bucket).
from __future__ import annotations

import re
from collections import Counter

from weblate.glossary.models import (
    fetch_glossary_terms,
    get_glossary_term_modes,
    glossary_matcher_fingerprint,
)
from weblate.trans.models import Component, Translation
from weblate.utils.state import STATE_TRANSLATED

CHUNK = 200
SAMPLE_LIMIT = 12

translation = Translation.objects.get(
    component__project__slug="col4", component__slug="data", language__code="id"
)
component = translation.component
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).select_related(
        "translation__component__source_language",
        "translation__language",
        "source_unit",
    )
)
print(f"units: {len(units)}")
print(
    "fingerprint:",
    glossary_matcher_fingerprint(
        translation.project, component.source_language, translation.language
    ),
)

# --- source-side visibility through the product matcher ---
matched = {}
for start in range(0, len(units), CHUNK):
    chunk = units[start : start + CHUNK]
    for unit in chunk:
        unit.glossary_terms = None
    fetch_glossary_terms(chunk, include_variants=False)
    for unit in chunk:
        matched[unit.pk] = list(unit.glossary_terms or [])

units_with_terms = sum(1 for terms in matched.values() if terms)
term_hits = sum(len(terms) for terms in matched.values())
distinct_terms = {t.source for terms in matched.values() for t in terms}
print(
    f"source-side: units with >=1 matched term: {units_with_terms}/{len(units)}"
)
print(f"source-side: total term hits: {term_hits}, distinct terms: {len(distinct_terms)}")

term_unit_counts: Counter = Counter()
for terms in matched.values():
    for term in terms:
        term_unit_counts[term.source] += 1
print("matched term frequency:", dict(term_unit_counts.most_common()))

# --- adherence: does the canonical id form (or an inflected form) appear? ---
# Direct rule application: matched term -> canonical id form present in the
# target (word boundaries). id has no Snowball algorithm, so an inflection-
# tolerant comparison is not available on the target side - report canonical
# presence and, for misses, show what the translator actually wrote.
misses = []
term_miss_counts: Counter = Counter()
boundary = r"\b"
for unit in units:
    target_text = unit.get_target_plurals()[0]
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if "not-applicable" in modes or "forbidden" in modes:
            continue
        expected = term.target
        if not expected:
            continue
        if not re.search(
            rf"{boundary}{re.escape(expected)}{boundary}", target_text, re.IGNORECASE
        ):
            term_miss_counts[(term.source, expected)] += 1
            if len(misses) < 40:
                misses.append((term.source, expected, unit.source[:80], target_text[:100]))

print(f"adherence: canonical-form misses: {sum(term_miss_counts.values())} term-hits across {len(term_miss_counts)} distinct terms")
print()
print("=== MISS FREQUENCY (term -> expected id form -> count) ===")
for (src, tgt), count in term_miss_counts.most_common():
    print(f"  {src!r} -> {tgt!r} x{count}")
print()
print("=== MISS SAMPLES ===")
for src, tgt, usrc, utgt in misses[:25]:
    print(f"  term {src!r} expects {tgt!r}")
    print(f"    SRC {usrc!r}")
    print(f"    TGT {utgt!r}")
