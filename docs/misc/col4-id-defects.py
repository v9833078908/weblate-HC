# Dump real glossary-adherence defects + cyrillic-leak units for col4/data/id.
# Read-only, pipe into weblate shell. Emits TSV rows.
from __future__ import annotations

import re

from weblate.checks.models import Check
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


def surfaces(term_source: str, text: str) -> list[str]:
    needle = get_text_stems(term_source, "ru")
    if not needle:
        return []
    spans = list(iter_word_spans(text))
    haystack = get_text_stems(text, "ru")
    return [
        text[spans[i][1] : spans[i + len(needle) - 1][2]]
        for i in range(len(haystack) - len(needle) + 1)
        if haystack[i : i + len(needle)] == needle
    ]


# Homograph surfaces verified by eye in the previous audit: the matched word
# is NOT the glossary term. (term, surface.lower()) pairs.
HOMOGRAPH = {
    ("Вера", "верят"), ("Вера", "верю"), ("Вера", "верит"), ("Вера", "вере"),
    ("Вера", "верил"), ("Вера", "верите"),
    ("Чистые", "чистой"), ("Чистые", "чистить"), ("Чистые", "чисто"),
    ("Чистые", "чист"), ("Чистые", "чистая"), ("Чистые", "чистыми"),
    ("Партия", "парту"), ("Партия", "парты"), ("Партия", "партой"),
    ("Партия", "парте"),
    ("Устав", "устали"),
    ("Смена", "сменилась"),
    ("Обращённый", "обращения"),
}
# Acceptable short/variant renderings, not defects.
VARIANT = {
    ("Максим", "макс"),
    ("Сидоров", "сидор"),
    ("Истина", "истинный"),
}

print("=== REAL ADHERENCE DEFECTS ===")
print("unit_id\tterm_ru\texpected_id\tsource\ttarget")
defects = 0
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
        surf = [s for s in surfaces(term.source, unit.source)]
        # skip if EVERY matched surface is a known homograph or variant
        if surf and all(
            (term.source, s.lower()) in HOMOGRAPH or (term.source, s.lower()) in VARIANT
            for s in surf
        ):
            continue
        defects += 1
        print(
            f"{unit.pk}\t{term.source}\t{term.target}\t"
            f"{unit.source}\t{target_text}"
        )
print(f"total real defects: {defects}")
print()
print("=== CYRILLIC LEAK ===")
print("unit_id\tsource\ttarget\tcyrillic_fragments")
for check in Check.objects.filter(
    unit__translation=translation, name="cyrillic-leak"
).exclude(dismissed=True):
    u = check.unit
    frags = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё-]*", u.get_target_plurals()[0])
    print(f"{u.pk}\t{u.source}\t{u.get_target_plurals()[0]}\t{frags}")
