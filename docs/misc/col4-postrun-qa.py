# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Post-run QA of the COL4 French automatic translation."""

from __future__ import annotations

import re
from collections import Counter

from weblate.trans.models import Project, Translation

ENDERS = ".!?…:;"
CYRILLIC = re.compile(r"[\u0400-\u04FF]")

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)

units = list(translation.unit_set.all())
translated = [unit for unit in units if unit.state >= 20 and unit.target.strip()]
print("TOTAL_UNITS", len(units))
print("TRANSLATED", len(translated))

checks: Counter[str] = Counter()
for unit in units:
    for check in unit.all_checks:
        checks[check.name] += 1
print("FAILING_CHECKS", dict(checks.most_common()))

glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")
pairs = {
    unit.source: unit.target
    for unit in fr_glossary.unit_set.all()
    if unit.target.strip() and unit.source.strip()
}

hits = misses = 0
miss_examples: list[tuple[str, str, str, str]] = []
for unit in translated:
    lowered = unit.source.lower()
    for term, rendering in pairs.items():
        if term.lower() not in lowered:
            continue
        if rendering.lower() in unit.target.lower():
            hits += 1
        else:
            misses += 1
            if len(miss_examples) < 12:
                miss_examples.append(
                    (term, rendering, unit.source[:70], unit.target[:70])
                )
total_terms = hits + misses
print("GLOSSARY_OCCURRENCES", total_terms)
print("GLOSSARY_APPLIED", hits)
print("GLOSSARY_MISSED", misses)
if total_terms:
    print("GLOSSARY_ADHERENCE_PCT", round(100 * hits / total_terms, 1))
for term, rendering, source, target in miss_examples:
    print(f"  MISS {term!r} -> {rendering!r}")
    print(f"    SRC {source}")
    print(f"    TGT {target}")

punct = [
    unit
    for unit in translated
    if (unit.source.rstrip()[-1:] in ENDERS) != (unit.target.rstrip()[-1:] in ENDERS)
]
print("FINAL_PUNCTUATION_MISMATCH", len(punct))

cyrillic = [unit for unit in translated if CYRILLIC.search(unit.target)]
print("CYRILLIC_LEAKED", len(cyrillic))
for unit in cyrillic[:5]:
    print("  CYR", repr(unit.source[:50]), "->", repr(unit.target[:50]))

targets = Counter(unit.target for unit in translated)
collisions = {
    target: count
    for target, count in targets.items()
    if count > 1
    and len({unit.source for unit in translated if unit.target == target}) > 1
}
print("TARGET_COLLISIONS", len(collisions))

separator_lost = [
    unit
    for unit in translated
    if unit.source.count("$") != unit.target.count("$")
]
print("SEPARATOR_COUNT_MISMATCH", len(separator_lost))

growth = [
    len(unit.target) / len(unit.source)
    for unit in translated
    if unit.source.strip()
]
if growth:
    print("LENGTH_RATIO_AVG", round(sum(growth) / len(growth), 3))
    print("LENGTH_RATIO_OVER_130PCT", len([r for r in growth if r > 1.3]))
