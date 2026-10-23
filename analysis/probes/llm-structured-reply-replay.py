#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Replay currently empty strings through the project's configured LLM engine.

Samples strings in state Empty and observes whether today's model output is
accepted by the structured-reply parser.

It makes one direct ``download_multiple_translations`` request per sampled
unit - never ``batch_translate`` - so it never updates ``Unit.machinery`` or
quota accounting, and it never writes a unit target or changes a unit state.
It is not read-only overall: every request it sends is a real, billed model
call, and the machinery layer deliberately writes its own audit row for that
call to ``LLMUsageLog`` (outcome, refusal reason, and a bounded reply excerpt
for a refused or partial row) exactly as a production request would. Running
this probe costs real money and is production traffic on whatever project it
targets; get explicit approval before running it, especially against a live
instance, per this repository's ``AGENTS.md``.

This is a fresh sample, not a replay of any previously captured response
body: the model is stochastic, so this probe's per-run acceptance rate is an
observation of current output, not proof that a specific earlier reply is
now accepted. Static fixtures in ``weblate/machinery/tests.py`` are what
prove a captured shape is accepted; this probe is only for measuring the
residual refusal rate on fresh traffic.

Run it inside the container, so no secret is ever staged on disk:

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/llm-structured-reply-replay.py').read())"

Environment:

    PROBE_PROJECT   optional, default "pirate-ships"
    PROBE_LIMIT     optional, default 30, hard cap 200
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

from django.db.models import F, Max

from weblate.machinery.base import MachineTranslationError
from weblate.machinery.models import MACHINERY
from weblate.trans.forms import configured_routed_engine
from weblate.trans.models import LLMUsageLog, Project, Unit
from weblate.utils.state import STATE_EMPTY

HARD_CAP = 200
DEFAULT_PROJECT = "pirate-ships"
DEFAULT_LIMIT = 30


def sample_units(project: Project, limit: int) -> list[Unit]:
    return list(
        Unit.objects.filter(translation__component__project=project, state=STATE_EMPTY)
        .exclude(translation__language=F("translation__component__source_language"))
        .select_related(
            "translation__language",
            "translation__component",
            "translation__component__source_language",
        )
        .order_by("pk")[:limit]
    )


def replay_unit(machine, unit: Unit) -> str:
    """Ask the machinery for one unit; print what it asked and got back; return the outcome key."""
    translation = unit.translation
    print(f"\nunit {unit.pk}: source={unit.source!r}")
    try:
        # Same mapper the real pipeline calls in _prepare_translate; it does
        # not account usage or write anything, only resolves the pair the
        # configured service actually supports (see module docstring).
        source_language_code, target_language_code = machine.get_languages(
            translation.component.source_language, translation.language
        )
    except MachineTranslationError as error:
        print(f"  skipped: unsupported language pair ({error})")
        return "(unsupported)"
    cleaned_source, _replacements = machine.cleanup_text(unit.source, unit)
    if not cleaned_source:
        print("  skipped: empty after placeholder cleanup")
        return "(skipped)"

    # Measurement deliberately reuses the parser's own private helper to show
    # exactly what was asked; see the module docstring.
    asked_parts = machine._get_string_parts(cleaned_source, unit)
    print(f"  asked parts: {asked_parts}")

    raw_reply: list[str | None] = [None]
    original_fetch = machine.fetch_llm_translations

    def capture_reply(
        prompt: str, content: str, previous_content: str, previous_response: str
    ) -> str | None:
        reply = original_fetch(prompt, content, previous_content, previous_response)
        raw_reply[0] = reply
        return reply

    watermark = LLMUsageLog.objects.aggregate(top=Max("pk"))["top"] or 0
    try:
        with patch.object(machine, "fetch_llm_translations", side_effect=capture_reply):
            result = machine.download_multiple_translations(
                source_language_code,
                target_language_code,
                [(cleaned_source, unit)],
            )
    except MachineTranslationError as error:
        print(f"  raw reply: {raw_reply[0]!r}")
        print(f"  raised: {error}")
    else:
        print(f"  raw reply: {raw_reply[0]!r}")
        print(f"  accepted: {result[cleaned_source][0]['text']!r}")

    usage_row = (
        LLMUsageLog.objects.filter(
            pk__gt=watermark,
            operation="translation",
            component_id_snapshot=translation.component_id,
            target_language_code=target_language_code,
        )
        .order_by("pk")
        .first()
    )
    if usage_row is None:
        print("  no LLMUsageLog row observed for this request")
        return "(no row)"
    print(
        f"  usage row {usage_row.pk}: outcome={usage_row.outcome or '(blank)'!r} "
        f"refusal_reason={usage_row.refusal_reason!r}"
    )
    return usage_row.outcome or "(blank)"


def main() -> int:
    project_slug = os.environ.get("PROBE_PROJECT", "").strip() or DEFAULT_PROJECT
    requested = int(os.environ.get("PROBE_LIMIT", "").strip() or DEFAULT_LIMIT)
    limit = min(requested, HARD_CAP)

    try:
        project = Project.objects.get(slug=project_slug)
    except Project.DoesNotExist:
        print(f"no such project: {project_slug}")
        return 2

    engine_id = configured_routed_engine(project.get_machinery_settings())
    if engine_id is None:
        print(f"project {project_slug} has no configured OpenRouter/LiteLLM engine")
        return 2
    machine = MACHINERY[engine_id](project.get_machinery_settings()[engine_id])

    units = sample_units(project, limit)
    if not units:
        print(f"project {project_slug} has no currently empty strings")
        return 2

    print(f"project: {project_slug}, engine: {engine_id}, units: {len(units)}")

    outcomes: dict[str, int] = {}
    for unit in units:
        key = replay_unit(machine, unit)
        outcomes[key] = outcomes.get(key, 0) + 1

    print(f"\noutcomes: {outcomes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
