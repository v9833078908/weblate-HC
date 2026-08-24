# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render the exact OpenRouter prompt for a COL4 French batch without calling the API."""

from __future__ import annotations

from weblate.glossary.models import fetch_glossary_terms
from weblate.machinery.models import MACHINERY
from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)

settings = project.get_machinery_settings()["openrouter"]
service = MACHINERY["openrouter"](settings)

units = list(translation.unit_set.all()[:5])
fetch_glossary_terms(units, include_variants=False)
sources = [(unit.source, unit) for unit in units]

prompt, content, previous_content, previous_response = service._prepare_llm_translation(
    "ru", "fr", sources, None
)

print("=" * 20, "MODEL", "=" * 20)
print(service.resolve_model("fr"))
print("=" * 20, "SYSTEM PROMPT", "=" * 20)
print(prompt)
print("=" * 20, "USER CONTENT", "=" * 20)
print(content)
print("=" * 20, "PREVIOUS EXAMPLE CONTENT", "=" * 20)
print(previous_content)
print("=" * 20, "PREVIOUS EXAMPLE RESPONSE", "=" * 20)
print(previous_response)
