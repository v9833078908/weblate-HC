# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Count invented renderings for glossary terms the matcher failed to supply."""

from __future__ import annotations

import re
from collections import Counter

from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)

PROBES = {
    "Гигахрущ": re.compile(r"\bGiga\w*", re.IGNORECASE),
    "ликвидатор": re.compile(r"\b\w*liquidat\w*|\bélimina\w*|\bnettoyeur\w*", re.IGNORECASE),
    "Самосбор": re.compile(r"\bSamosbor\w*|\bAuto-?assembl\w*|\bRassembl\w*", re.IGNORECASE),
}

units = [
    unit
    for unit in translation.unit_set.all()
    if unit.state >= 20 and unit.target.strip()
]

for term, regex in PROBES.items():
    source_re = re.compile(rf"(?<![^\W\d_]){re.escape(term)}", re.IGNORECASE)
    found: Counter[str] = Counter()
    for unit in units:
        if source_re.search(unit.source):
            for match in regex.findall(unit.target):
                found[match] += 1
    print(f"TERM {term!r} distinct_renderings={len(found)}")
    for rendering, count in found.most_common(12):
        print(f"    {count:4d}  {rendering}")
