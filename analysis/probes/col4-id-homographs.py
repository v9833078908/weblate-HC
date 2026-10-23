# Count remaining loose-miss classes on col4/data/id: per term, which source
# surfaces matched, and whether the target shows the canonical id term, a
# substring variant, or something else. Read-only.
from __future__ import annotations

import re
from collections import Counter, defaultdict

from weblate.checks.morphology import get_text_stems, iter_word_spans
from weblate.glossary.models import fetch_glossary_terms, get_glossary_term_modes
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


def surfaces(term_source: str, text: str, lang: str) -> list[str]:
    needle = get_text_stems(term_source, lang)
    if not needle:
        return []
    spans = list(iter_word_spans(text))
    haystack = get_text_stems(text, lang)
    return [
        text[spans[i][1] : spans[i + len(needle) - 1][2]]
        for i in range(len(haystack) - len(needle) + 1)
        if haystack[i : i + len(needle)] == needle
    ]


# term -> (surface -> count) for loose misses only
miss_surfaces: dict[str, Counter] = defaultdict(Counter)
# also count how many matched units per term PASS (canonical or substring)
pass_count: Counter = Counter()
for unit in units:
    target_text = unit.get_target_plurals()[0]
    tl = target_text.lower()
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if modes & {"not-applicable", "forbidden"} or not term.target:
            continue
        if re.search(rf"\b{re.escape(term.target)}\b", target_text, re.IGNORECASE):
            pass_count[term.source] += 1
            continue
        if term.target.lower() in tl:
            pass_count[term.source] += 1
            continue
        for surface in surfaces(term.source, unit.source, "ru"):
            miss_surfaces[term.source][surface] += 1
        if not surfaces(term.source, unit.source, "ru"):
            miss_surfaces[term.source]["<exact form only>"] += 1

print("=== PER-TERM: pass vs loose-miss surfaces ===")
for term_source in sorted(miss_surfaces, key=lambda t: -sum(miss_surfaces[t].values())):
    total_miss = sum(miss_surfaces[term_source].values())
    passed = pass_count[term_source]
    print(f"{term_source!r}: pass={passed} miss={total_miss}")
    for surface, count in miss_surfaces[term_source].most_common():
        print(f"    miss on surface {surface!r} x{count}")
