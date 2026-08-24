# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Size the autofix backfill and the proposed terminal rule on a whole instance.

Read-only: every autofix and check used here is pure, nothing is saved. Run it
through ``weblate shell`` so it sees the runtime autofix registry rather than a
checkout's idea of it:

    ./deploy/vps.sh ssh "echo <base64 of this file> | base64 -d \\
        | docker exec -i hcgameloc-weblate-1 weblate shell" > artifact.json

The report answers four questions, in the order the plan needs them.

1. What does the *shipped* AUTOFIX_LIST already repair on stored units? That is
   the backfill target - the reason ``reapply_autofixes`` exists at all, since
   autofixes only run on write (``unit.py:fix_target``).
2. What would the *proposed* terminal rule add on top? The rule is copied here
   verbatim from the plan so the measurement measures the rule, not a paraphrase.
3. How far does the source-blind predicate ("target ends with a mark the source
   lacks") overcount against the check-gated rule? Cathedral's HT001 counted
   source defects as translation defects exactly this way, so both numbers are
   reported side by side and never conflated.
4. Which units would lose a colon? They are listed in full: seven-ish
   observations decide a global rule and have to be read by a human.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict

from django.conf import settings
from django.db.models import F

from weblate.checks.chars import (
    EndColonCheck,
    EndExclamationCheck,
    EndQuestionCheck,
    EndStopCheck,
)
from weblate.checks.models import Check
from weblate.trans.autofixes import AUTOFIXES
from weblate.trans.autofixes.base import AutoFix
from weblate.trans.models import Component, Unit
from weblate.utils.state import STATE_READONLY

# ---------------------------------------------------------------------------
# The proposed rule, copied from the plan (task 2). Kept in one piece so the
# diff against weblate_customization/autofixes.py stays reviewable.
# ---------------------------------------------------------------------------

TRAILING_SPACING = re.compile(r"[ \u00a0\u202f\u2009]+$")
TERMINAL_MARKS = ".!?:"
CLOSING_QUOTES = '»"”'
TRAILING_QUOTES = re.compile(rf"[{re.escape(CLOSING_QUOTES)}]+$")
TERMINAL_CHECKS = (
    EndStopCheck(),
    EndColonCheck(),
    EndQuestionCheck(),
    EndExclamationCheck(),
)


class ProposedTerminalFix(AutoFix):
    """Task 2 of the plan: drop terminal punctuation the source does not have."""

    fix_id = "removed-final-stop"
    name = "Added final punctuation"

    @staticmethod
    def _failing(source: str, target: str, unit: Unit) -> frozenset[str]:
        return frozenset(
            check.check_id
            for check in TERMINAL_CHECKS
            if not check.should_skip(unit) and check.check_single(source, target, unit)
        )

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        # The target is NOT unwrapped. Reaching a mark behind a closing quote
        # measured as a net harm on prod: 13 of 14 such units were en_US strings
        # placing the full stop inside the quotes per US convention
        # (`the inscription "armory."`), which the rule would have degraded.
        # The source IS unwrapped, and only that direction is needed: it is what
        # keeps a source mark hidden behind a quote (`с криком "Еретик!"`) from
        # reading as punctuation the model invented.
        if not target or target[-1] not in TERMINAL_MARKS:
            return target, False
        if len(target) > 1 and target[-2] == target[-1]:
            return target, False
        stripped = TRAILING_SPACING.sub("", target[:-1])
        if not stripped:
            return target, False
        source_body = TRAILING_QUOTES.sub("", source)
        before = self._failing(source_body, target, unit)
        if not before:
            return target, False
        if not self._failing(source_body, stripped, unit) < before:
            return target, False
        return stripped, True


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


def candidate_units():
    """Stored units a backfill would consider, mirroring the planned command."""
    return (
        Unit.objects.exclude(state=STATE_READONLY)
        .exclude(translation__component__is_glossary=True)
        # A source translation is not a translation; repairing it would rewrite
        # the corpus the checks compare against.
        .exclude(
            translation__language_id=F("translation__component__source_language_id")
        )
        .select_related(
            "translation",
            "translation__language",
            "translation__component",
            "translation__component__source_language",
            "translation__component__project",
        )
        .order_by("pk")
    )


def run_fixes(fixes, target: list[str], unit: Unit) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    for fix in fixes:
        target, changed = fix.fix_target(target, unit)
        if changed:
            applied.append(fix.fix_id)
    return target, applied


def main() -> int:
    shipped = list(AUTOFIXES.values())
    has_shipped_terminal = any(
        fix.fix_id == ProposedTerminalFix.fix_id for fix in shipped
    )
    # Replacing by fix_id alone is not enough: on an instance where the shipped
    # terminal fix is not registered at all, the comprehension would return the
    # shipped list unchanged and report a false zero benefit. Appending is
    # outcome-equivalent to the deployed position here, because French spacing
    # only inserts a space before an existing mark and the terminal rule
    # removes the mark together with any spacing hugging it.
    proposed = [
        ProposedTerminalFix() if fix.fix_id == ProposedTerminalFix.fix_id else fix
        for fix in shipped
    ]
    if not has_shipped_terminal:
        proposed.append(ProposedTerminalFix())

    report: dict[str, object] = {
        "autofix_registry": [fix.fix_id for fix in shipped],
        "autofix_list_setting": list(settings.AUTOFIX_LIST),
        "shipped_terminal_fix_present": has_shipped_terminal,
        "components": Component.objects.count(),
        "units_total": Unit.objects.count(),
    }

    live_terminal = Counter(
        Check.objects.filter(
            name__in=[
                "end_stop",
                "end_colon",
                "end_question",
                "end_exclamation",
                "end_interrobang",
            ],
            dismissed=False,
        ).values_list("name", flat=True)
    )
    report["live_failing_terminal_checks"] = dict(live_terminal)

    scanned = 0
    empty = 0
    shipped_changed = 0
    proposed_changed = 0
    delta_changed = 0
    shipped_by_fix: Counter[str] = Counter()
    proposed_by_fix: Counter[str] = Counter()
    terminal_by_mark: Counter[str] = Counter()
    loose_by_mark: Counter[str] = Counter()
    per_component: defaultdict[str, Counter[str]] = defaultdict(Counter)
    colon_units: list[dict[str, object]] = []
    delta_examples: list[dict[str, object]] = []

    for unit in candidate_units().iterator(chunk_size=2000):
        target = unit.get_target_plurals()
        if not any(text.strip() for text in target):
            empty += 1
            continue
        scanned += 1
        source = unit.get_source_plurals()[0]
        component = str(unit.translation.component)
        language = unit.translation.language.code

        # Source-blind predicate, reported only to show how far it overcounts.
        stripped_source = source.rstrip()
        for text in target:
            text = text.rstrip()
            if not text or text[-1] not in "!?:":
                continue
            if stripped_source.endswith(text[-1]):
                continue
            loose_by_mark[text[-1]] += 1
            break

        after_shipped, applied_shipped = run_fixes(shipped, list(target), unit)
        after_proposed, applied_proposed = run_fixes(proposed, list(target), unit)

        if after_shipped != target:
            shipped_changed += 1
            shipped_by_fix.update(applied_shipped)
            per_component[component]["shipped"] += 1
        if after_proposed != target:
            proposed_changed += 1
            proposed_by_fix.update(applied_proposed)
            per_component[component]["proposed"] += 1
        if after_proposed == after_shipped:
            continue

        delta_changed += 1
        per_component[component]["delta"] += 1
        # Compare against the shipped result, not the stored target: another
        # autofix may have changed the string too, and its edit is not the
        # terminal rule's doing.
        marks = {
            old.rstrip()[-1]
            for old, new in zip(after_shipped, after_proposed, strict=False)
            if old != new and old.rstrip() and old.rstrip()[-1] in TERMINAL_MARKS
        }
        terminal_by_mark.update(marks)
        record = {
            "unit_id": unit.pk,
            "component": component,
            "language": language,
            "context": unit.context,
            "source": source,
            "target": target[0],
            "proposed": after_proposed[0],
            "marks": sorted(marks),
        }
        if ":" in marks:
            colon_units.append(record)
        elif len(delta_examples) < 40:
            delta_examples.append(record)

    report.update(
        {
            "units_scanned": scanned,
            "units_skipped_empty": empty,
            "backfill_shipped_units": shipped_changed,
            "backfill_shipped_by_fix": dict(shipped_by_fix),
            "backfill_proposed_units": proposed_changed,
            "backfill_proposed_by_fix": dict(proposed_by_fix),
            "terminal_rule_delta_units": delta_changed,
            "terminal_rule_delta_by_mark": dict(terminal_by_mark),
            "source_blind_predicate_by_mark": dict(loose_by_mark),
            "per_component": {
                name: dict(counter)
                for name, counter in sorted(per_component.items())
                if counter
            },
            "colon_units": colon_units,
            "delta_examples": delta_examples,
        }
    )

    sys.stdout.write("===AUTOFIX-SCAN-BEGIN===\n")
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n===AUTOFIX-SCAN-END===\n")
    return 0


main()
