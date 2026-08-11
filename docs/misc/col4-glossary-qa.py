# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Split COL4 glossary defects into model non-compliance and matcher misses.

A term counts as present in the source only on a word-boundary match, with
acronyms matched case-sensitively, so short terms such as "НИИ" no longer match
inside unrelated words like "предназначении".
"""

from __future__ import annotations

import re

from weblate.glossary.models import fetch_glossary_terms
from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)
glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")

pairs = {
    unit.source.strip(): unit.target.strip()
    for unit in fr_glossary.unit_set.all()
    if unit.target.strip() and unit.source.strip()
}


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def source_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if is_acronym(term):
        return re.compile(rf"(?<![^\W\d_]){escaped}(?![^\W\d_])")
    # Russian inflection: the term is a prefix of the inflected word form.
    return re.compile(rf"(?<![^\W\d_]){escaped}", re.IGNORECASE)


def target_pattern(rendering: str) -> re.Pattern[str]:
    head = rendering.split()[0]
    stem = head[:-1] if len(head) > 5 else head
    return re.compile(rf"(?<![^\W\d_]){re.escape(stem)}", re.IGNORECASE)


source_res = {term: source_pattern(term) for term in pairs}
target_res = {term: target_pattern(rendering) for term, rendering in pairs.items()}

units = [
    unit
    for unit in translation.unit_set.all()
    if unit.state >= 20 and unit.target.strip()
]

prompted_total = prompted_applied = 0
matcher_missed = 0
matcher_missed_terms: dict[str, int] = {}
noncompliance: list[tuple[str, str, str]] = []

for chunk_start in range(0, len(units), 200):
    chunk = units[chunk_start : chunk_start + 200]
    fetch_glossary_terms(chunk, include_variants=False)
    for unit in chunk:
        matched = {
            term.source.strip()
            for term in (unit.glossary_terms or [])
            if term.source.strip() in pairs
        }
        present = {
            term for term, regex in source_res.items() if regex.search(unit.source)
        }
        for term in present:
            applied = bool(target_res[term].search(unit.target))
            if term in matched:
                prompted_total += 1
                prompted_applied += applied
                if not applied and len(noncompliance) < 10:
                    noncompliance.append((term, unit.source[:80], unit.target[:80]))
            elif not applied:
                matcher_missed += 1
                matcher_missed_terms[term] = matcher_missed_terms.get(term, 0) + 1

print("UNITS", len(units))
print("TERMS_IN_PROMPT", prompted_total)
print("TERMS_IN_PROMPT_APPLIED", prompted_applied)
if prompted_total:
    print(
        "MODEL_COMPLIANCE_PCT",
        round(100 * prompted_applied / prompted_total, 1),
    )
print("MATCHER_MISSED_OCCURRENCES", matcher_missed)
print(
    "MATCHER_MISSED_BY_TERM",
    sorted(matcher_missed_terms.items(), key=lambda item: -item[1]),
)
for term, source, target in noncompliance:
    print(f"  NONCOMPLIANCE {term!r}")
    print(f"    SRC {source}")
    print(f"    TGT {target}")
