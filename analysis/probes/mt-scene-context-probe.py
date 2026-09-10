#!/usr/bin/env python3
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Driver for the scene-context MT slice (plan 2026-09-04-scene-context-for-mt).

Three arms over the same corpus, the same production prompt and the same
production request path:

* ``P``  - scene-blind order (fixed-seed shuffle, one draw per language),
           batches of 10 that cut scenes wherever they fall.
* ``S0`` - units ordered by ``position``, no batch crosses a scene boundary.
           No new context in the prompt.
* ``S``  - ``S0`` plus a ``scene`` block in the message and one new prompt
           rule. Lines already answered by an earlier batch of the same
           scene carry that arm's own translation, the rest carry ``null``.

Everything else is production on HEAD: ``PROMPT``, ``_get_string_parts``,
``_build_message``, ``RoutedLiteLLMTranslation.get_chat_payload`` (strict
JSON schema, no ``provider`` field), ``fetch_llm_translations`` with its
retry loop, and ``_parse_llm_translations`` for the reply contract.

Usage::

    PROBE_MODEL=deepseek-v4-pro python3 analysis/probes/mt-scene-context-probe.py --dry-run
    PROBE_REPEATS=3 python3 analysis/probes/mt-scene-context-probe.py

Environment:
    PROBE_ARMS      default ``P,S0,S``
    PROBE_LANGS     default ``de,fr``
    PROBE_REPEATS   default ``3``
    PROBE_MODEL     default ``deepseek-v4-pro`` (LiteLLM proxy identifier)
    PROBE_WORKERS   default ``3`` concurrent requests
    PROBE_BATCH     default ``10`` strings per request
    PROBE_SCENE_CAP default ``60`` scene lines per block
    PROBE_OUT       output directory, default
                    ``analysis/data/mt-scene-context-<today>``
    PROBE_CORPUS    ``hub1`` (default) or ``temple``
"""

from __future__ import annotations

import json
import os
import pathlib
import random
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_PORT", "5432")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django  # noqa: E402

django.setup()

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "weblate_customization" / "src"))

from weblate_customization.machinery import (  # noqa: E402
    RoutedLiteLLMTranslation,
)

PROD_API = "http://l10n.herocraft.com/api"
PROD_PROJECT = "heart-abyss"
PROD_GLOSSARY = "all-glossary"
CORPORA = {
    "hub1": {
        "component": "hub-1",
        "scenes": ("hub1_first_1", "hub1_ramen_1", "hub1_teahouse_1"),
    },
    "temple": {"component": "temple", "scenes": ()},
}

# The single new prompt line arm S adds, inserted after rule 28. Recorded
# byte-for-byte in every output file so production can copy it verbatim.
SCENE_RULE = (
    '29. The "scene" object, when present, is the dialogue scene the strings of '
    "this batch belong to, in engine order: each line carries its number, its "
    'speaker and its source, and "translation" when that line was already '
    "translated outside this batch. It is reference material for who is "
    "addressing whom, how many people are addressed, which register and "
    "politeness form the scene uses, and for staying consistent with the "
    "translations already made in it. Do not translate it, do not copy or quote "
    "it, and do not emit any of its content that is not in the strings you were "
    "asked to translate."
)
SCENE_RULE_ANCHOR = "\nValid placeholder and markup handling:"


def scene_of(context: str) -> str:
    return context.rsplit("_", 1)[0]


def prod_token() -> str:
    for candidate in (ROOT / ".env.local", ROOT / "deploy" / ".env.local"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            if line.startswith("PROD_WEBLATE_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    msg = "PROD_WEBLATE_API_TOKEN not found"
    raise SystemExit(msg)


def litellm_key() -> str:
    path = ROOT / "deploy" / ".env.local"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("LITELLM_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    value = os.environ.get("LITELLM_API_KEY", "").strip()
    if value:
        return value
    msg = "LITELLM_API_KEY not found in deploy/.env.local or environment"
    raise SystemExit(msg)


def api_get(url: str) -> dict:
    request = urllib.request.Request(  # noqa: S310 - fixed https/http prod host
        url, headers={"Authorization": f"Token {prod_token()}"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return json.loads(response.read())


def fetch_glossary(lang: str, cache: pathlib.Path) -> list[dict]:
    """
    Mirror the entries production sends with every batch of this project.

    The term base holds 221 pairs, below ``LLM_FULL_GLOSSARY_LIMIT``, so
    production sends all of them; ``build_glossary_prompt_entry`` shape is
    reproduced from the API (source, target, source explanation, flags).
    """
    if cache.exists():
        return json.loads(cache.read_text())

    def units(code: str) -> list[dict]:
        out: list[dict] = []
        url = (
            f"{PROD_API}/translations/{PROD_PROJECT}/{PROD_GLOSSARY}/"
            f"{code}/units/?page_size=1000"
        )
        while url:
            payload = api_get(url)
            out.extend(payload["results"])
            url = payload.get("next")
        return out

    source_units = {unit["url"]: unit for unit in units("ru")}
    entries: list[dict] = []
    seen: set[str] = set()
    for unit in units(lang):
        source = (unit["source"][0] or "").strip()
        target = (unit["target"][0] or "").strip()
        if not source or not target:
            continue
        entry: dict[str, object] = {"source": source, "target": target}
        source_unit = source_units.get(unit["source_unit"], {})
        explanation = (source_unit.get("explanation") or "").strip()
        if explanation:
            entry["source_explanation"] = explanation
        own_explanation = (unit.get("explanation") or "").strip()
        if own_explanation:
            entry["target_explanation"] = own_explanation
        flags = {
            flag.strip()
            for flag in (
                f"{source_unit.get('extra_flags', '')},{unit.get('extra_flags', '')}"
            ).split(",")
            if flag.strip()
        }
        advertised = [
            flag
            for flag in ("read-only", "terminology", "exact", "forbidden")
            if flag in flags
        ]
        if advertised:
            entry["flags"] = advertised
        key = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    return entries


def prod_machinery_settings() -> dict[str, object]:
    """Persona, style and per-language instructions as configured on prod."""
    path = ROOT / "analysis/data/prod_machinery_prompts_2026-09-04.md"
    text = path.read_text()
    marker = f"\n## PROJECT: {PROD_PROJECT}\n"
    start = text.index(marker) + len(marker)
    end = text.find("\n## PROJECT: ", start)
    block = text[start : end if end != -1 else len(text)]

    def section(name: str) -> str:
        head = f"\n### {name}\n"
        begin = block.index(head) + len(head)
        stop = block.find("\n### ", begin)
        return block[begin : stop if stop != -1 else len(block)].strip("\n")

    instructions: dict[str, str] = {}
    raw = section("language_instructions")
    current: str | None = None
    buffer: list[str] = []
    for line in raw.splitlines():
        if line.startswith("#### "):
            if current:
                instructions[current] = "\n".join(buffer).strip()
            current = line[5:].strip()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        instructions[current] = "\n".join(buffer).strip()

    return {
        "persona": section("persona").strip(),
        "style": section("style").strip(),
        "language_instructions": instructions,
    }


class ProbeLiteLLM(RoutedLiteLLMTranslation):
    """Production LiteLLM machinery with the DB usage write captured instead."""

    def __init__(self, configuration) -> None:
        super().__init__(configuration)
        self.last_usage: dict[str, object] | None = None

    def record_llm_usage(self, payload, model, batch_size: int = 0) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        self.last_usage = {
            "model": model,
            "batch_size": batch_size,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "completion_tokens": (usage or {}).get("completion_tokens"),
            "total_tokens": (usage or {}).get("total_tokens"),
            "reasoning_tokens": ((usage or {}).get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            ),
            "cost": str((usage or {}).get("cost")) if usage else None,
        }


def build_machine(model: str) -> ProbeLiteLLM:
    configuration = {
        "key": litellm_key(),
        "base_url": "https://hcbifrost.herocraft.com/litellm/v1",
        "routing": {"*": model},
        **prod_machinery_settings(),
    }
    return ProbeLiteLLM(configuration)


def load_corpus(lang: str, corpus: str) -> list[dict]:
    spec = CORPORA[corpus]
    path = (
        ROOT
        / "analysis/data/hub1-remediation-2026-08-25"
        / f"{PROD_PROJECT}__{spec['component']}__{lang}.json"
    )
    units = json.loads(path.read_text())
    scenes = spec["scenes"]
    if scenes:
        units = [unit for unit in units if scene_of(unit["context"]) in scenes]
    return sorted(units, key=lambda unit: (scene_of(unit["context"]), unit["position"]))


def batches_for_arm(
    arm: str, units: list[dict], lang: str, size: int
) -> list[list[dict]]:
    if arm == "P":
        # Production does not order the batch queryset, so the neighbour of a
        # string inside a request is whatever the table hands back. One fixed
        # draw per language stands in for that, identical across repeats so
        # M3 measures model noise and not order noise.
        shuffled = list(units)
        random.Random(f"scene-context-{lang}").shuffle(shuffled)
        return [shuffled[index : index + size] for index in range(0, len(shuffled), size)]

    grouped: dict[str, list[dict]] = {}
    for unit in units:
        grouped.setdefault(scene_of(unit["context"]), []).append(unit)
    result: list[list[dict]] = []
    for scene in sorted(grouped):
        lines = sorted(grouped[scene], key=lambda unit: unit["position"])
        result.extend(lines[index : index + size] for index in range(0, len(lines), size))
    return result


def scene_block(
    scene_key: str,
    units: list[dict],
    batch_contexts: set[str],
    answered: dict[str, str],
    cap: int,
) -> dict:
    lines = [
        unit for unit in units if scene_of(unit["context"]) == scene_key
    ]
    lines.sort(key=lambda unit: unit["position"])
    if len(lines) > cap:
        indexes = [
            index
            for index, unit in enumerate(lines)
            if unit["context"] in batch_contexts
        ]
        centre = (indexes[0] + indexes[-1]) // 2 if indexes else 0
        start = max(0, min(centre - cap // 2, len(lines) - cap))
        lines = lines[start : start + cap]
    payload = []
    for unit in lines:
        context = unit["context"]
        translation = None
        if context not in batch_contexts:
            translation = answered.get(context)
        payload.append(
            {
                "n": unit["position"],
                "speaker": unit["note"],
                "source": unit["source"][0],
                "translation": translation,
            }
        )
    return {"key": scene_key, "lines": payload}


def build_request(
    machine: ProbeLiteLLM,
    lang: str,
    arm: str,
    batch: list[dict],
    units: list[dict],
    glossary: list[dict],
    answered: dict[str, str],
    cap: int,
) -> tuple[str, str, list[str]]:
    string_ids = machine._build_string_ids(len(batch))  # noqa: SLF001
    payloads = []
    for unit, string_id in zip(batch, string_ids, strict=True):
        text = unit["source"][0]
        payload: dict[str, object] = {
            "id": string_id,
            "source": text,
            "parts": machine._get_string_parts(text, None),  # noqa: SLF001
            # po-mono component: production puts the context into "key"
            "key": unit["context"],
        }
        if unit["note"]:
            payload["note"] = unit["note"]
        payloads.append(payload)

    content = machine._build_message("ru", lang, payloads, glossary)  # noqa: SLF001
    prompt = machine._get_prompt(lang)  # noqa: SLF001

    if arm == "S":
        scene_keys = {scene_of(unit["context"]) for unit in batch}
        if len(scene_keys) != 1:
            msg = f"arm S batch spans {scene_keys}"
            raise AssertionError(msg)
        block = scene_block(
            next(iter(scene_keys)),
            units,
            {unit["context"] for unit in batch},
            answered,
            cap,
        )
        envelope = json.loads(content)
        envelope["scene"] = block
        content = json.dumps(envelope, ensure_ascii=False)
        if SCENE_RULE_ANCHOR not in prompt:
            msg = "prompt anchor for the scene rule is gone"
            raise AssertionError(msg)
        prompt = prompt.replace(
            SCENE_RULE_ANCHOR, f"\n{SCENE_RULE}\n{SCENE_RULE_ANCHOR}", 1
        )
    return prompt, content, string_ids


def reply_texts(raw: str | None, string_ids: list[str]) -> dict[str, str]:
    """Per-id text of a reply, read the way the product pairs it: by id."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        parsed = parsed.get("translations")
    if not isinstance(parsed, list):
        return {}
    out: dict[str, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        string_id = item.get("id")
        if string_id not in string_ids:
            continue
        parts = item.get("parts")
        if isinstance(parts, list):
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("type") != "placeholder"
            )
        else:
            text = item.get("text", "")
        out[string_id] = text
    return out


def run_batch(
    machine: ProbeLiteLLM,
    lang: str,
    arm: str,
    repeat: int,
    index: int,
    batch: list[dict],
    units: list[dict],
    glossary: list[dict],
    answered: dict[str, str],
    cap: int,
) -> dict:
    prompt, content, string_ids = build_request(
        machine, lang, arm, batch, units, glossary, answered, cap
    )
    sources = [(unit["source"][0], None) for unit in batch]
    record: dict[str, object] = {
        "arm": arm,
        "lang": lang,
        "repeat": repeat,
        "batch": index,
        "contexts": [unit["context"] for unit in batch],
        "string_ids": string_ids,
        "prompt_chars": len(prompt),
        "content_chars": len(content),
        "scene_keys": sorted({scene_of(unit["context"]) for unit in batch}),
    }
    # The corporate proxy drops long requests at the production 55 s
    # transport ceiling under load, and the product's retry loop only
    # repeats an answered 429/503, not a read timeout. Measurement needs
    # the line, so the timed-out attempt is re-asked here and both the
    # first-attempt outcome and the attempt count are recorded.
    attempts: list[dict[str, object]] = []
    max_attempts = int(os.environ.get("PROBE_ATTEMPTS", "3"))
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            raw = machine.fetch_llm_translations(prompt, content, "", "")
        except Exception as error:  # noqa: BLE001 - transport metric
            elapsed = round(time.monotonic() - started, 2)
            attempts.append(
                {
                    "attempt": attempt,
                    "seconds": elapsed,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            record["attempts"] = attempts
            record["seconds"] = elapsed
            record["error"] = f"{type(error).__name__}: {error}"
            record["translations"] = {}
            record["contract"] = "transport-failure"
            if attempt < max_attempts:
                time.sleep(2.0 * attempt)
                continue
            return record
        elapsed = round(time.monotonic() - started, 2)
        attempts.append({"attempt": attempt, "seconds": elapsed})
        record.pop("error", None)
        record["attempts"] = attempts
        record["seconds"] = elapsed
        record["usage"] = machine.last_usage
        record["raw_chars"] = len(raw or "")
        texts = reply_texts(raw, string_ids)
        record["translations"] = {
            unit["context"]: texts.get(string_id, "")
            for unit, string_id in zip(batch, string_ids, strict=True)
        }
        try:
            parsed = machine._parse_llm_translations(  # noqa: SLF001
                raw, sources, None, string_ids=string_ids
            )
            record["contract"] = "ok"
            record["parsed_strings"] = len(parsed)
        except Exception as error:  # noqa: BLE001 - contract metric
            record["contract"] = f"{type(error).__name__}: {error}"
            record["parsed_strings"] = 0
        return record
    return record


def run_arm(
    model: str,
    lang: str,
    arm: str,
    repeat: int,
    units: list[dict],
    glossary: list[dict],
    size: int,
    cap: int,
    workers: int,
    log: threading.Lock,
) -> list[dict]:
    machine = build_machine(model)
    batches = batches_for_arm(arm, units, lang, size)
    records: list[dict] = []
    if arm == "S":
        # The scene block carries what earlier batches of the same scene
        # already answered, so those batches must finish first. Batches of
        # different scenes stay independent and run together.
        by_scene: dict[str, list[tuple[int, list[dict]]]] = {}
        for index, batch in enumerate(batches):
            by_scene.setdefault(scene_of(batch[0]["context"]), []).append((index, batch))

        def run_scene(items: list[tuple[int, list[dict]]]) -> list[dict]:
            answered: dict[str, str] = {}
            out = []
            for index, batch in items:
                record = run_batch(
                    build_machine(model),
                    lang,
                    arm,
                    repeat,
                    index,
                    batch,
                    units,
                    glossary,
                    answered,
                    cap,
                )
                answered.update(
                    {
                        context: text
                        for context, text in record["translations"].items()
                        if text
                    }
                )
                with log:
                    print(
                        f"  {lang} {arm} r{repeat} batch {index} "
                        f"{record.get('contract')} {record['seconds']}s",
                        flush=True,
                    )
                out.append(record)
            return out

        with ThreadPoolExecutor(max_workers=min(workers, len(by_scene))) as pool:
            for chunk in pool.map(run_scene, by_scene.values()):
                records.extend(chunk)
        return sorted(records, key=lambda item: item["batch"])

    def run_one(item: tuple[int, list[dict]]) -> dict:
        index, batch = item
        record = run_batch(
            build_machine(model),
            lang,
            arm,
            repeat,
            index,
            batch,
            units,
            glossary,
            {},
            cap,
        )
        with log:
            print(
                f"  {lang} {arm} r{repeat} batch {index} "
                f"{record.get('contract')} {record['seconds']}s",
                flush=True,
            )
        return record

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(run_one, enumerate(batches)))
    _ = machine
    return records


def main() -> None:
    arms = os.environ.get("PROBE_ARMS", "P,S0,S").split(",")
    langs = os.environ.get("PROBE_LANGS", "de,fr").split(",")
    repeats = int(os.environ.get("PROBE_REPEATS", "3"))
    model = os.environ.get("PROBE_MODEL", "deepseek-v4-pro")
    workers = int(os.environ.get("PROBE_WORKERS", "3"))
    size = int(os.environ.get("PROBE_BATCH", "10"))
    cap = int(os.environ.get("PROBE_SCENE_CAP", "60"))
    corpus = os.environ.get("PROBE_CORPUS", "hub1")
    out_dir = pathlib.Path(
        os.environ.get(
            "PROBE_OUT",
            str(ROOT / f"analysis/data/mt-scene-context-{time.strftime('%Y-%m-%d')}"),
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    dry_run = "--dry-run" in sys.argv

    log = threading.Lock()
    for lang in langs:
        units = load_corpus(lang, corpus)
        glossary = fetch_glossary(lang, out_dir / f"glossary-{lang}.json")
        machine = build_machine(model)
        if dry_run:
            for arm in arms:
                batches = batches_for_arm(arm, units, lang, size)
                prompt, content, _ids = build_request(
                    machine, lang, arm, batches[0], units, glossary, {}, cap
                )
                print(f"=== {lang} {arm}: {len(batches)} batches ===")
                print(f"prompt {len(prompt.encode())} bytes, "
                      f"content {len(content.encode())} bytes")
                envelope = json.loads(content)
                preview = {
                    key: value for key, value in envelope.items() if key != "glossary"
                }
                preview["glossary"] = f"<{len(envelope['glossary'])} entries>"
                if "strings" in preview:
                    preview["strings"] = preview["strings"][:2]
                if "scene" in preview:
                    preview["scene"] = {
                        "key": preview["scene"]["key"],
                        "lines": preview["scene"]["lines"][:3]
                        + [f"<{len(preview['scene']['lines'])} lines total>"],
                    }
                print(json.dumps(preview, ensure_ascii=False, indent=1))
                if arm == "S":
                    print("SCENE RULE BYTES:", json.dumps(SCENE_RULE))
            continue
        if "--repair" in sys.argv:
            # A batch lost to a proxy timeout is re-asked in place, so the
            # paired comparison keeps every line instead of dropping it in
            # all arms. Arm S is re-asked with the same scene block the run
            # built for it, minus translations the lost batch would have fed
            # to later batches - noted in the record.
            for arm in arms:
                for repeat in range(1, repeats + 1):
                    target = out_dir / f"{lang}-{arm}-r{repeat}.json"
                    if not target.exists():
                        continue
                    payload = json.loads(target.read_text())
                    batches = batches_for_arm(arm, units, lang, size)
                    changed = False
                    for position, record in enumerate(payload["records"]):
                        if record.get("contract") == "ok" and not record.get("error"):
                            continue
                        index = record["batch"]
                        answered = {}
                        if arm == "S":
                            scene = scene_of(batches[index][0]["context"])
                            for other in payload["records"]:
                                if other["batch"] >= index:
                                    continue
                                if scene_of(other["contexts"][0]) != scene:
                                    continue
                                answered.update(
                                    {
                                        context: text
                                        for context, text in (
                                            other.get("translations") or {}
                                        ).items()
                                        if text
                                    }
                                )
                        fresh = run_batch(
                            build_machine(model),
                            lang,
                            arm,
                            repeat,
                            index,
                            batches[index],
                            units,
                            glossary,
                            answered,
                            cap,
                        )
                        fresh["repaired"] = True
                        payload["records"][position] = fresh
                        changed = True
                        print(
                            f"repaired {target.name} batch {index}: "
                            f"{fresh.get('contract')} {fresh['seconds']}s",
                            flush=True,
                        )
                    if changed:
                        target.write_text(
                            json.dumps(payload, ensure_ascii=False, indent=1)
                        )
            continue

        for arm in arms:
            for repeat in range(1, repeats + 1):
                target = out_dir / f"{lang}-{arm}-r{repeat}.json"
                if target.exists():
                    print(f"skip existing {target.name}", flush=True)
                    continue
                print(f"== {lang} {arm} repeat {repeat} ==", flush=True)
                started = time.time()
                records = run_arm(
                    model,
                    lang,
                    arm,
                    repeat,
                    units,
                    glossary,
                    size,
                    cap,
                    workers,
                    log,
                )
                target.write_text(
                    json.dumps(
                        {
                            "plan": "2026-09-04-scene-context-for-mt",
                            "corpus": corpus,
                            "lang": lang,
                            "arm": arm,
                            "repeat": repeat,
                            "model": model,
                            "batch_size": size,
                            "scene_cap": cap,
                            "scene_rule": SCENE_RULE if arm == "S" else "",
                            "prompt_sha_source": "weblate/machinery/llm.py:PROMPT",
                            "wall_seconds": round(time.time() - started, 1),
                            "records": records,
                        },
                        ensure_ascii=False,
                        indent=1,
                    )
                )
                print(f"wrote {target.name}", flush=True)


if __name__ == "__main__":
    main()
