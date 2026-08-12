# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
B0 dump: col4/data/fr units + glossary, layer-0 normalized (read-only).

Runs inside the dev container:
    docker exec -i dev-docker-weblate-1 weblate shell < docs/misc/col4-b0-dump.py
Writes /app/data/col4-b0-units.jsonl (host: dev-docker/data/).
"""

import json
import re

from weblate_customization.autofixes import (
    AddFrenchPunctuationSpacing,
    RemoveAddedFinalStop,
)

from weblate.trans.autofixes import fix_target
from weblate.trans.models import Project, Translation
from weblate.utils.state import STATE_TRANSLATED

# The running container predates the compose edit registering these two,
# so fix_target() only applies LineSeparatorSpacing; chain them manually
# in AUTOFIX_LIST order (terminal strip before French spacing).
REMOVE_FINAL_STOP = RemoveAddedFinalStop()
FRENCH_SPACING = AddFrenchPunctuationSpacing()
ENDERS = ".!?…:;"
ADDED_TERMINAL = re.compile(r"[\u00A0\u202F]?[!?:]$")

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)

glossary = project.component_set.get(slug="glossary")
fr_glossary = glossary.translation_set.get(language__code="fr")
pairs = {
    unit.source.strip(): unit.target.strip()
    for unit in fr_glossary.unit_set.all()
    if unit.target.strip() and unit.source.strip()
}

records: list[dict] = [{"glossary": pairs}]

plural_sep_hits = 0
for unit in translation.unit_set.prefetch_related("check_set").order_by("id"):
    if unit.state < STATE_TRANSLATED or not unit.target.strip():
        continue
    raw = unit.target
    fixed_list, fixups = fix_target([raw], unit)
    fixed = fixed_list[0]
    fixups = [str(f) for f in fixups]
    fixed, applied = REMOVE_FINAL_STOP.fix_single_target(fixed, unit.source, unit)
    if applied:
        fixups.append("removed-final-stop")
    fixups = [str(f) for f in fixups]
    # In-flight replica of the approved-but-unshipped `!?:` terminal autofix
    # (docs/LLM-first/plans/2026-08-11-layer0-autofix-quick-wins.md, task 1):
    # source has no terminal punctuation, target ends with optional
    # NBSP/NNBSP + one of !?: -> strip the whole pair.
    src = unit.source
    # Closing quotes hide the source's own terminal punctuation
    # ("...Еретик!\"") - unwrap them before testing, like EndStopCheck does.
    src_end = src.rstrip().rstrip("\"»”'")
    if src_end and not src_end.rstrip().endswith(tuple(ENDERS)):
        stripped = ADDED_TERMINAL.sub("", fixed)
        if stripped != fixed:
            fixed = stripped
            fixups.append("terminal-extension-!?:")
    fixed, applied = FRENCH_SPACING.fix_single_target(fixed, unit.source, unit)
    if applied:
        fixups.append("french-punctuation-spacing")
    checks = sorted(check.name for check in unit.all_checks)
    src_low = src.lower()
    gloss_terms = []
    gloss_missing = []
    for term, rendering in pairs.items():
        if term.lower() in src_low:
            gloss_terms.append(term)
            if rendering.lower() not in fixed.lower():
                gloss_missing.append(term)
    records.append(
        {
            "unit_id": unit.id,
            "context": unit.context,
            "source": src,
            "target_raw": raw,
            "target": fixed,
            "fixups": fixups,
            "note": unit.note,
            "checks": checks,
            "gloss_terms": gloss_terms,
            "gloss_missing": gloss_missing,
        }
    )

with open("/app/data/col4-b0-units.jsonl", "w", encoding="utf-8") as out:
    for record in records:
        json.dump(record, out, ensure_ascii=False)
        out.write("\n")
print("DONE plural_sep_hits", plural_sep_hits)
