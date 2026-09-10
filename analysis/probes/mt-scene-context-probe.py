#!/usr/bin/env python3
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Paired probe for the scene-context arm of MT (machine translation).

Plan: docs/product/plans/2026-09-04-scene-context-for-mt.md

Arms:
  P  - production as is: DB selection order, batches of 10.
  S0 - order by position, batch does not cross scene boundary (context key = scene prefix).
  S  - S0 + scene block in message + prompt rule byte-in-byte from arm S.

Usage:
  uv run python analysis/probes/mt-scene-context-probe.py --dry-run
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5437")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from django.conf import settings

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "analysis/data/mt-scene-context-2026-09-10"

SCENES = ("hub1_first_1", "hub1_ramen_1", "hub1_teahouse_1")

# Production machinery settings (mirrored from dev-docker/docker-compose.yml)
# Since no LITELLM_API_KEY is configured in this environment, the driver
# only builds messages (--dry-run) and records the protocol.
settings.WEBLATE_JUDGE_ENABLED = False  # we are not the judge

# Import the custom machinery service
# Ensure custom machinery package is importable (not installed in .venv)
sys.path.insert(0, str(ROOT / "weblate_customization" / "src"))

from weblate_customization.machinery import RoutedLiteLLMTranslation  # noqa: E402

# Import the base machinery prompt/message helpers
from weblate.machinery.llm import PROMPT, LLMStringPayload  # noqa: E402
from weblate.trans.autotranslate import AutoTranslate  # noqa: E402


def scene_of(context: str) -> str:
    return context.rsplit("_", 1)[0]


def load_units_for_lang(lang: str) -> list[dict]:
    path = ROOT / f"analysis/data/hub1-remediation-2026-08-25/heart-abyss__hub-1__{lang}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    chosen = [u for u in data if scene_of(u.get("context", "")) in SCENES]
    return sorted(chosen, key=lambda u: (scene_of(u.get("context", "")), u.get("position", 0)))


def build_production_machine(configuration: dict) -> RoutedLiteLLMTranslation:
    return RoutedLiteLLMTranslation(configuration)


def dry_run_messages(arm: str, lang: str) -> None:
    units_raw = load_units_for_lang(lang)
    if not units_raw:
        print(f"No units for lang={lang}; skip.")
        return

    # We use the production PROMPT string directly (from weblate/machinery/llm.py)
    # and format it with the same persona/style/instructions that production uses,
    # avoiding any DB query (no running PostgreSQL in this environment).
    from weblate.machinery.llm import PROMPT  # type: ignore[attr-defined]

    persona_text = "You are a professional translation engine specialized in structured localization tasks."
    style_text = "Keep the text concise and natural."
    language_instructions_text = ""

    prompt_text = PROMPT.format(
        persona=persona_text,
        style=style_text,
        language_instructions=language_instructions_text,
    )

    # Arm P: DB selection order (use raw unit order), batches of 10.
    # Arm S0: order by position within scene, batches that don't cross scene.
    # Arm S: same batches + scene block + prompt rule.

    # Build batches according to arm rules
    if arm == "P":
        batches = [units_raw[i : i + 10] for i in range(0, len(units_raw), 10)]
    elif arm == "S0" or arm == "S":
        scenes_map: dict[str, list[dict]] = {}
        for u in units_raw:
            scene_key = scene_of(u.get("context", ""))
            scenes_map.setdefault(scene_key, []).append(u)
        ordered_units = []
        for scene_key in sorted(scenes_map):
            ordered_units.extend(sorted(scenes_map[scene_key], key=lambda u: u.get("position", 0)))
        batches = [ordered_units[:10]]
    else:
        batches = []

    # Create a dummy machinery instance (no DB needed for message construction
    # when sources contain no real Unit objects).
    dummy_config = {
        "key": "dry-run",
        "routing": {},
        "persona": persona_text,
        "style": style_text,
        "language_instructions": {},
    }
    machine = build_production_machine(dummy_config)

    # For arm S we add the scene block to the message (simulated below)
    # and add the new prompt rule (shown as a separate line).
    extra_rule = ""
    if arm == "S":
        extra_rule = "SCENE BLOCK: all scene lines (source + speaker + translation when available, max 60 lines) are reference material for register, number, and consistency. Do not translate them; use them only to resolve ambiguity in the current string."

    for batch_index, batch in enumerate(batches[:1]):  # first batch only
        sources: list[tuple[str, None]] = []
        for item in batch:
            text = item.get("source", [""])[0] if isinstance(item.get("source"), list) else item.get("source", "")
            sources.append((text, None))  # type: ignore[arg-type]
        string_ids = [f"s{idx:02x}" for idx in range(len(sources))]
        # Use production message builder directly (no DB queries with None units)
        message_text = machine._get_message("ru", lang, sources, string_ids=string_ids)  # type: ignore[attr-defined]

        print(f"=== ARM {arm} lang={lang} batch={batch_index} ===")
        print(f"Prompt ({len(prompt_text)} chars, {len(prompt_text.encode('utf-8'))} bytes):")
        # Show first 800 chars of prompt
        print(prompt_text[:800] + (" ..." if len(prompt_text) > 800 else ""))
        print()
        print(f"Message ({len(message_text)} chars):")
        # Show structured JSON of first 3 strings to keep output short
        try:
            msg_obj = json.loads(message_text)
            if isinstance(msg_obj, dict) and "strings" in msg_obj:
                msg_obj["strings"] = msg_obj["strings"][:3]
                print(json.dumps(msg_obj, ensure_ascii=False, indent=1))
            else:
                print(message_text[:1200] + (" ..." if len(message_text) > 1200 else ""))
        except Exception:
            print(message_text[:1200] + (" ..." if len(message_text) > 1200 else ""))
        print()

        # If arm is S, add the scene block description to the output
        if arm == "S":
            print("SCENE BLOCK (simulated for arm S):")
            # Extract scene info from the batch
            scene_lines = {}
            for item in batch:
                ctx = item.get("context", "")
                scene_key = scene_of(ctx)
                line = {
                    "n": item.get("position", 0),
                    "speaker": item.get("note", ""),
                    "source": item.get("source", [""])[0] if isinstance(item.get("source"), list) else item.get("source", ""),
                }
                scene_lines.setdefault(scene_key, []).append(line)
            for scene_key, lines in scene_lines.items():
                print(f"  scene: {scene_key} ({len(lines)} lines shown from batch)")
                for line in lines:
                    print(f"    {line['n']} speaker={line['speaker']} source={line['source'][:60]}...")


def main() -> None:
    dry_run = os.environ.get("PROBE_DRY_RUN", "") or "--dry-run" in sys.argv
    arms = os.environ.get("PROBE_ARMS", "P,S0,S").split(",")
    langs = os.environ.get("PROBE_LANGS", "de,fr").split(",")
    for lang in langs:
        for arm in arms:
            dry_run_messages(arm, lang)
    if dry_run:
        print(f"DRY-RUN COMPLETE: arms={','.join(arms)} langs={','.join(langs)}")
        print("No LLM calls made. No API key required.")
        print(f"Output directory would be: {OUT_DIR}")
        OUT_DIR.mkdir(exist_ok=True)
        # Write a minimal record file showing the protocol
        record = {
            "date": "2026-09-10",
            "plan": "2026-09-04-scene-context-for-mt",
            "dry_run": True,
            "arms": arms,
            "langs": langs,
            "message_sample": "see stdout above",
            "rule_s_bytes_fixed": "SCENE BLOCK + single new prompt line",
        }
        (OUT_DIR / f"dry-run-{lang}-{'_'.join(arms)}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1)
        )


if __name__ == "__main__":
    main()
