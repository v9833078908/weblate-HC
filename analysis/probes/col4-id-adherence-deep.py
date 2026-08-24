# Deeper adherence audit for col4/data/id: substring variants, forbidden
# check, and a real defect sample. Read-only, pipe into weblate shell.
from __future__ import annotations

import re
from collections import Counter

from weblate.glossary.models import (
    fetch_glossary_terms,
    get_glossary_term_modes,
)
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

# --- substring-tolerant adherence (id affixes: Sel -> Selmu, Selnya, di Sel) ---
strict_miss = 0
loose_miss = 0
loose_miss_counts: Counter = Counter()
loose_samples = []
for unit in units:
    target_text = unit.get_target_plurals()[0]
    tl = target_text.lower()
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if "not-applicable" in modes or "forbidden" in modes:
            continue
        expected = term.target
        if not expected:
            continue
        if re.search(rf"\b{re.escape(expected)}\b", target_text, re.IGNORECASE):
            continue
        strict_miss += 1
        if expected.lower() in tl:
            continue  # inflected form of the canonical term, acceptable in id
        loose_miss += 1
        loose_miss_counts[term.source, expected] += 1
        if len(loose_samples) < 40:
            loose_samples.append(
                (term.source, expected, unit.source[:80], target_text[:110])
            )

print(f"strict canonical misses: {strict_miss}")
print(f"after id-affix tolerance (substring): real misses: {loose_miss}")
print()
print("=== REAL MISS FREQUENCY ===")
for (src, tgt), count in loose_miss_counts.most_common():
    print(f"  {src!r} -> {tgt!r} x{count}")
print()
print("=== REAL MISS SAMPLES (translator used a different word or none) ===")
for src, tgt, usrc, utgt in loose_samples:
    print(f"  term {src!r} expects {tgt!r}")
    print(f"    SRC {usrc!r}")
    print(f"    TGT {utgt!r}")
print()

# --- forbidden terms: any forbidden id word used? ---
forbidden_hits = []
for unit in units:
    target_text = unit.get_target_plurals()[0]
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if "forbidden" not in modes:
            continue
        if re.search(rf"\b{re.escape(term.target)}\b", target_text, re.IGNORECASE):
            forbidden_hits.append((term.source, term.target, target_text[:90]))
print(f"forbidden violations: {len(forbidden_hits)}")
for src, tgt, utgt in forbidden_hits[:10]:
    print(f"  {src!r} forbidden {tgt!r} in {utgt!r}")
