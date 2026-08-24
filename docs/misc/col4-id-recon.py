# Read-only recon for col4: new glossary + id translation state.
# Pipe into weblate shell on the target instance.
from __future__ import annotations

from collections import Counter

from weblate.trans.models import Component, Project, Translation, Unit

project = Project.objects.get(slug="col4")
print("PROJECT", project.slug)

for component in Component.objects.filter(project=project).select_related(
    "source_language"
):
    marker = "GLOSSARY" if component.is_glossary else "component"
    print(
        f"{marker}: {component.slug} src={component.source_language.code} "
        f"langs={component.translation_set.count()}"
    )

for glossary in project.glossaries:
    print(
        f"glossary {glossary.slug}: src={glossary.source_language.code} "
        f"translations={sorted(t.language.code for t in glossary.translation_set.all())}"
    )
    ru_terms = Unit.objects.filter(
        translation__component=glossary, translation__language__code="ru"
    ).count()
    print(f"  ru terms: {ru_terms}")
    id_terms = Unit.objects.filter(
        translation__component=glossary, translation__language__code="id"
    ).count()
    print(f"  id terms: {id_terms}")
    # Sample ru->id pairs via source/target units joined on id_hash
    ru_units = {
        u.id_hash: u
        for u in Unit.objects.filter(
            translation__component=glossary, translation__language__code="ru"
        )
    }
    id_units = {
        u.id_hash: u
        for u in Unit.objects.filter(
            translation__component=glossary, translation__language__code="id"
        )
    }
    shared = sorted(set(ru_units) & set(id_units))
    print(f"  shared keys: {len(shared)}")
    for key in shared[:60]:
        ru = ru_units[key]
        idu = id_units[key]
        print(f"  {ru.source!r} -> {idu.target!r} flags={sorted(ru.all_flags)}")

id_translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="id"
)
units = id_translation.unit_set
total = units.count()
translated = units.filter(state__gte=20).count()
print(f"col4/data/id: total={total} translated={translated}")

from weblate.checks.models import Check

check_counts: Counter = Counter(
    Check.objects.filter(unit__translation=id_translation)
    .exclude(dismissed=True)
    .values_list("name", flat=True)
)
print("failing checks:", dict(check_counts.most_common()))
for check in Check.objects.filter(unit__translation=id_translation).exclude(
    dismissed=True
)[:15]:
    print(
        f"  [{check.name}] unit={check.unit_id} SRC {check.unit.source[:60]!r} "
        f"TGT {check.unit.target[:60]!r}"
    )
