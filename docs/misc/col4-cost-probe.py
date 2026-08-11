# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Measure COL4 French corpus size and dump one real 20-unit OpenRouter payload."""

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

all_units = list(translation.unit_set.all())
total_chars = sum(len(unit.source) for unit in all_units)
print("CORPUS_UNITS", len(all_units))
print("CORPUS_SOURCE_CHARS", total_chars)
print("CORPUS_AVG_CHARS", round(total_chars / len(all_units), 1))
print("CORPUS_MAX_CHARS", max(len(unit.source) for unit in all_units))

batch = all_units[:20]
fetch_glossary_terms(batch, include_variants=False)
sources = [(unit.source, unit) for unit in batch]
prompt, content, previous_content, previous_response = service._prepare_llm_translation(
    "ru", "fr", sources, None
)

print("SYSTEM_PROMPT_CHARS", len(prompt))
print("BATCH_CONTENT_CHARS", len(content))
print("FEWSHOT_CONTENT_CHARS", len(previous_content or ""))
print("FEWSHOT_RESPONSE_CHARS", len(previous_response or ""))
print("BATCH_SOURCE_CHARS", sum(len(unit.source) for unit in batch))

messages = [
    {"role": "system", "content": prompt},
    {"role": "user", "content": previous_content},
    {"role": "assistant", "content": previous_response},
    {"role": "user", "content": content},
]
payload = json.dumps(messages, ensure_ascii=False)
print("PAYLOAD_B64", base64.b64encode(payload.encode()).decode())
