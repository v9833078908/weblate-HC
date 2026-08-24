# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Check the deterministic autofixes against real COL4 strings.

Runs the autofix chain the way ``Unit.translate`` does, without writing
anything: the fix is applied to a candidate target in memory and the result is
compared with what the checks say about it.
"""

from __future__ import annotations

from weblate.checks.chars import EndStopCheck, PunctuationSpacingCheck
from weblate.trans.autofixes import fix_target
from weblate.trans.models import Project, Translation

project = Project.objects.get(slug="col4")
translation = Translation.objects.get(
    component__project=project, component__slug="data", language__code="fr"
)

end_stop = EndStopCheck()
spacing = PunctuationSpacingCheck()

units = list(translation.unit_set.all()[:2000])
added_stop = repaired_stop = 0
missing_space = repaired_space = 0
samples: list[tuple[str, str, str]] = []

for unit in units:
    source = unit.source
    target = unit.target
    if not target.strip():
        continue
    fails_stop = bool(end_stop.check_single(source, target, unit))
    fails_space = bool(spacing.check_single(source, target, unit))
    if not fails_stop and not fails_space:
        continue

    fixed_targets, fixups = fix_target([target], unit)
    fixed = fixed_targets[0]

    if fails_stop and target.endswith(".") and not source.rstrip().endswith("."):
        added_stop += 1
        if not end_stop.check_single(source, fixed, unit):
            repaired_stop += 1
        elif len(samples) < 5:
            samples.append(("stop", target[-40:], fixed[-40:]))
    if fails_space:
        missing_space += 1
        if not spacing.check_single(source, fixed, unit):
            repaired_space += 1
        elif len(samples) < 5:
            samples.append(("space", target[:60], fixed[:60]))
    if fixups and len(samples) < 5 and fixed != target:
        samples.append(("fixups", ",".join(str(f) for f in fixups), fixed[-40:]))

print("UNITS_SCANNED", len(units))
print("ADDED_FINAL_STOP", added_stop, "REPAIRED", repaired_stop)
print("MISSING_FR_SPACING", missing_space, "REPAIRED", repaired_space)
for kind, before, after in samples:
    print(f"  {kind}: {before!r} -> {after!r}")
