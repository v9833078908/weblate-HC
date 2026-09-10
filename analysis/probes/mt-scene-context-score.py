#!/usr/bin/env python3
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""
M3, M4 and M5 for the scene-context MT slice, plus the blind LQA packet.

Reads the driver output of ``mt-scene-context-probe.py`` and prints:

* ``M3`` stability - share of lines whose translation is byte-identical in
  all three repeats of the same arm (the noise floor for every other
  comparison);
* ``M4`` batch contract - transport failures, replies the production parser
  refuses, missing strings, and final-punctuation breaks (prompt rule 27);
* ``M5`` cost - prompt/completion tokens and seconds per translated line.

``--packet`` writes a blind LQA packet: one entry per line where the arms'
modal renderings differ, the three variants shuffled under labels the
annotator cannot map back to an arm, plus a separate key file.

Usage::

    python3 analysis/probes/mt-scene-context-score.py analysis/data/mt-scene-context-2026-09-10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
DUMPS = ROOT / "analysis/data/hub1-remediation-2026-08-25"
ARMS = ("P", "S0", "S")
REPEATS = (1, 2, 3)
FINAL_PUNCT = re.compile(r"[.!?…:;]+$")


def scene_of(context: str) -> str:
    return context.rsplit("_", 1)[0]


def load_dump(lang: str) -> dict[str, dict]:
    path = DUMPS / f"heart-abyss__hub-1__{lang}.json"
    return {
        unit["context"]: unit
        for unit in json.loads(path.read_text(encoding="utf-8"))
    }


def load_run(run_dir: pathlib.Path, lang: str, arm: str, repeat: int) -> dict | None:
    path = run_dir / f"{lang}-{arm}-r{repeat}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def final_class(text: str) -> str:
    match = FINAL_PUNCT.search(text.rstrip())
    return match.group(0) if match else ""


def contract_stats(payload: dict, dump: dict[str, dict]) -> dict:
    requests = len(payload["records"])
    transport = sum(1 for record in payload["records"] if "error" in record)
    attempt_failures = 0
    attempts_total = 0
    for record in payload["records"]:
        tries = record.get("attempts") or []
        attempts_total += len(tries) or 1
        attempt_failures += sum(1 for try_ in tries if "error" in try_)
    refused = sum(
        1
        for record in payload["records"]
        if record.get("contract") not in {"ok", "transport-failure"}
    )
    asked = sum(len(record["contexts"]) for record in payload["records"])
    answered = 0
    punctuation = 0
    for record in payload["records"]:
        for context, text in (record.get("translations") or {}).items():
            if not text:
                continue
            answered += 1
            source = dump[context]["source"][0]
            if final_class(source) != final_class(text):
                punctuation += 1
    return {
        "requests": requests,
        "http_attempts": attempts_total,
        "attempt_timeouts": attempt_failures,
        "transport_failures": transport,
        "refused_replies": refused,
        "asked": asked,
        "answered": answered,
        "missing": asked - answered,
        "punctuation_breaks": punctuation,
    }


def cost_stats(payload: dict) -> dict:
    prompt = completion = reasoning = 0
    seconds = 0.0
    lines = 0
    for record in payload["records"]:
        usage = record.get("usage") or {}
        prompt += usage.get("prompt_tokens") or 0
        completion += usage.get("completion_tokens") or 0
        reasoning += usage.get("reasoning_tokens") or 0
        seconds += record.get("seconds") or 0.0
        lines += sum(1 for text in (record.get("translations") or {}).values() if text)
    return {
        "lines": lines,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "seconds": round(seconds, 1),
        "prompt_per_line": round(prompt / lines, 1) if lines else 0,
        "completion_per_line": round(completion / lines, 1) if lines else 0,
        "seconds_per_line": round(seconds / lines, 2) if lines else 0,
    }


def texts_of(payload: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for record in payload["records"]:
        for context, text in (record.get("translations") or {}).items():
            if text:
                out[context] = text
    return out


def modal(values: list[str]) -> str:
    counts = Counter(values)
    top = max(counts.values())
    # Ties resolve to the first repeat's rendering, so the choice is
    # deterministic and independent of the arm.
    for value in values:
        if counts[value] == top:
            return value
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--packet")
    parser.add_argument("--key")
    parser.add_argument("--json")
    parser.add_argument("--langs", default="de,fr")
    args = parser.parse_args()

    run_dir = pathlib.Path(args.run)
    langs = args.langs.split(",")
    report: dict[str, object] = {}

    print(f"{'scope':12} {'req':>4} {'t/o':>4} {'lost':>4} {'refus':>5} {'miss':>5} "
          f"{'punct':>5} {'ptok/l':>7} {'ctok/l':>7} {'s/l':>6} {'stable3':>8}")
    for lang in langs:
        dump = load_dump(lang)
        for arm in ARMS:
            per_repeat: dict[int, dict[str, str]] = {}
            contract = Counter()
            cost = Counter()
            seconds = 0.0
            for repeat in REPEATS:
                payload = load_run(run_dir, lang, arm, repeat)
                if payload is None:
                    continue
                stats = contract_stats(payload, dump)
                for key, value in stats.items():
                    contract[key] += value
                money = cost_stats(payload)
                for key in ("lines", "prompt_tokens", "completion_tokens", "reasoning_tokens"):
                    cost[key] += money[key]
                seconds += money["seconds"]
                per_repeat[repeat] = texts_of(payload)

            if not per_repeat:
                continue
            common = set.intersection(*(set(texts) for texts in per_repeat.values()))
            stable = sum(
                1
                for context in common
                if len({texts[context] for texts in per_repeat.values()}) == 1
            )
            lines = cost["lines"] or 1
            print(
                f"{lang}-{arm:9} {contract['requests']:4} "
                f"{contract['attempt_timeouts']:4} "
                f"{contract['transport_failures']:4} "
                f"{contract['refused_replies']:5} "
                f"{contract['missing']:5} {contract['punctuation_breaks']:5} "
                f"{cost['prompt_tokens'] / lines:7.0f} "
                f"{cost['completion_tokens'] / lines:7.0f} "
                f"{seconds / lines:6.2f} "
                f"{(stable / len(common) * 100 if common else 0):7.1f}%"
            )
            report[f"{lang}-{arm}"] = {
                "contract": dict(contract),
                "cost": {
                    **dict(cost),
                    "seconds": round(seconds, 1),
                    "prompt_per_line": round(cost["prompt_tokens"] / lines, 1),
                    "completion_per_line": round(cost["completion_tokens"] / lines, 1),
                    "seconds_per_line": round(seconds / lines, 2),
                },
                "stability": {
                    "compared_lines": len(common),
                    "identical_in_all_repeats": stable,
                    "share": round(stable / len(common), 4) if common else None,
                },
            }

    # Divergence and the blind packet
    packet: list[dict] = []
    key_rows: list[dict] = []
    rng = random.Random("mt-scene-context-blind")
    for lang in langs:
        dump = load_dump(lang)
        modals: dict[str, dict[str, str]] = defaultdict(dict)
        for arm in ARMS:
            collected: dict[str, list[str]] = defaultdict(list)
            for repeat in REPEATS:
                payload = load_run(run_dir, lang, arm, repeat)
                if payload is None:
                    continue
                for context, text in texts_of(payload).items():
                    collected[context].append(text)
            for context, values in collected.items():
                modals[context][arm] = modal(values)

        divergent = 0
        for context in sorted(modals):
            variants = modals[context]
            if len(variants) < len(ARMS):
                continue
            if len(set(variants.values())) == 1:
                continue
            divergent += 1
            unit = dump[context]
            labels = list("ABC")
            rng.shuffle(labels)
            mapping = dict(zip(labels, ARMS, strict=True))
            packet.append(
                {
                    "lang": lang,
                    "context": context,
                    "scene": scene_of(context),
                    "speaker": unit["note"],
                    "source": unit["source"][0],
                    "production_target": unit["target"][0],
                    "variants": {
                        label: variants[mapping[label]] for label in sorted(mapping)
                    },
                }
            )
            key_rows.append(
                {"lang": lang, "context": context, "labels": mapping}
            )
        report[f"{lang}-divergent-lines"] = divergent
        print(f"{lang}: {divergent} lines where arm modal renderings differ")

    if args.packet:
        pathlib.Path(args.packet).write_text(
            json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if args.key:
        pathlib.Path(args.key).write_text(
            json.dumps(key_rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
