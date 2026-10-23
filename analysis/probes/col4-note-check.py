# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check whether glossary explanations and unit notes exist for COL4."""

from __future__ import annotations

from weblate.trans.models import Component, Project, Translation

project = Project.objects.get(slug="col4")

glossary = Component.objects.get(project=project, slug="glossariy")
print("GLOSSARY_COMPONENT", glossary.slug, glossary.file_format, "created", glossary.id)
print("GLOSSARY_IS_GLOSSARY", glossary.is_glossary)

source_translation = glossary.translation_set.get(language=glossary.source_language)
print("GLOSSARY_SOURCE_LANG", glossary.source_language.code)

src_units = list(source_translation.unit_set.all())
print("GLOSSARY_SOURCE_UNITS", len(src_units))
with_expl = [u for u in src_units if u.explanation.strip()]
print("GLOSSARY_UNITS_WITH_EXPLANATION", len(with_expl))
for unit in src_units[:8]:
    print(
        "  TERM",
        repr(unit.source[:40]),
        "| expl:",
        repr(unit.explanation[:90]),
        "| note:",
        repr(unit.note[:60]),
        "| flags:",
        unit.flags,
    )

fr_glossary = glossary.translation_set.get(language__code="fr")
fr_units = list(fr_glossary.unit_set.all())
print("GLOSSARY_FR_UNITS", len(fr_units))
print(
    "GLOSSARY_FR_WITH_EXPLANATION",
    len([u for u in fr_units if u.explanation.strip()]),
)

data = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)
data_units = list(data.unit_set.all())
print("DATA_UNITS", len(data_units))
print("DATA_WITH_NOTE", len([u for u in data_units if u.note.strip()]))
print("DATA_WITH_CONTEXT", len([u for u in data_units if u.context.strip()]))
src_data = data.component.translation_set.get(language=data.component.source_language)
print(
    "DATA_SOURCE_WITH_EXPLANATION",
    len([u for u in src_data.unit_set.all() if u.explanation.strip()]),
)
for unit in data_units[:3]:
    print(
        "  UNIT",
        repr(unit.source[:35]),
        "| ctx:",
        repr(unit.context[:40]),
        "| note:",
        repr(unit.note[:50]),
    )
