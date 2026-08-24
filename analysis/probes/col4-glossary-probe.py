# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build a COL4 French batch made only of units that carry glossary terms."""

from __future__ import annotations

import base64
import json

from weblate.glossary.models import fetch_glossary_terms
from weblate.machinery.models import MACHINERY
from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)
settings = project.get_machinery_settings()["openrouter"]
service = MACHINERY["openrouter"](settings)

glossary_component = project.component_set.get(slug="glossariy")
fr_glossary = glossary_component.translation_set.get(language__code="fr")
pairs = {
    unit.source: unit.target
    for unit in fr_glossary.unit_set.all()
    if unit.target.strip()
}
print("GLOSSARY_PAIRS", json.dumps(pairs, ensure_ascii=False))

candidates: list = []
for chunk_start in range(0, 1200, 100):
    chunk = list(translation.unit_set.all()[chunk_start : chunk_start + 100])
    if not chunk:
        break
    fetch_glossary_terms(chunk, include_variants=False)
    candidates.extend(unit for unit in chunk if unit.glossary_terms)
    if len(candidates) >= 20:
        break

batch = candidates[:20]
print("BATCH_UNITS_WITH_GLOSSARY", len(batch))
fetch_glossary_terms(batch, include_variants=False)
sources = [(unit.source, unit) for unit in batch]
prompt, content, previous_content, previous_response = service._prepare_llm_translation(
    "ru", "fr", sources, None
)
print("SOURCES", json.dumps([unit.source for unit in batch], ensure_ascii=False))
messages = [
    {"role": "system", "content": prompt},
    {"role": "user", "content": previous_content},
    {"role": "assistant", "content": previous_response},
    {"role": "user", "content": content},
]
print(
    "PAYLOAD_B64",
    base64.b64encode(json.dumps(messages, ensure_ascii=False).encode()).decode(),
)
