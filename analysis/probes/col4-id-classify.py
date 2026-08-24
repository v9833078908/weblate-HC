# Classify the 56 loose misses on col4/data/id: real defect vs homograph
# (source word is not the glossary term in that string). Read-only.
from __future__ import annotations

import re

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

# Homograph source words observed in this corpus: the glossary term's stem
# matched a DIFFERENT Russian word. Map term -> surface word that is not the
# term. Built from the SRC samples already inspected; report every loose miss
# with its matched surface so the classification is auditable.
from weblate.checks.morphology import get_text_stems, iter_word_spans


def matched_surfaces(term_source: str, text: str, lang: str) -> list[str]:
    needle = get_text_stems(term_source, lang)
    if not needle:
        return []
    spans = list(iter_word_spans(text))
    haystack = get_text_stems(text, lang)
    out = []
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i : i + len(needle)] == needle:
            out.append(text[spans[i][1] : spans[i + len(needle) - 1][2]])
    return out


rows = []
for unit in units:
    target_text = unit.get_target_plurals()[0]
    tl = target_text.lower()
    for term in matched[unit.pk]:
        modes = get_glossary_term_modes(term)
        if modes & {"not-applicable", "forbidden"} or not term.target:
            continue
        if re.search(rf"\b{re.escape(term.target)}\b", target_text, re.IGNORECASE):
            continue
        if term.target.lower() in tl:
            continue
        surfaces = matched_surfaces(term.source, unit.source, "ru")
        rows.append((term.source, term.target, surfaces, unit.source, target_text))

print(f"loose misses: {len(rows)}")
print()
for src, tgt, surfaces, usrc, utgt in rows:
    print(f"TERM {src!r} expects {tgt!r} | matched surface: {surfaces}")
    print(f"  SRC {usrc[:100]!r}")
    print(f"  TGT {utgt[:110]!r}")
