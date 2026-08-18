#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Ground-truth annotator for the zh severity recalibration (track 1).

Labels every one of the 124 frozen units on none/minor/major/critical by the
player-consequence rubric, with a one-line English reason. Run with two
annotator models NOT from the collegium families (not deepseek, not qwen), e.g.
anthropic/claude-sonnet-5 and openai/gpt-5.4; disagreements are then resolved by
hand against the committed 14-defect anchor (st2-zh-judge-annotations.json) and
the defect inventory (st2-zh-corrected-export-2026-08-14.md).

Usage (inside a host that reaches OpenRouter):
    OPENROUTER_API_KEY=... python3 st2-zh-annotate.py \
        --model anthropic/claude-sonnet-5 \
        --input st2-zh-units.jsonl --glossary-file st2-summer-glossary-zh.json \
        --out st2-zh-annot-claude.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_URL = "https://openrouter.ai/api/v1/chat/completions"
SEVERITIES = ("none", "minor", "major", "critical")

SYSTEM_PROMPT = """\
You are an MQM annotator building ground truth for a Russian to Chinese video \
game localization. The game is a turn-based WWII strategy game; formal \
military/political register is intended, not an error.

For each segment you get the Russian source, the Chinese target, the string key, \
and any approved glossary terms. Assign exactly ONE severity to the whole \
segment, judged by the consequence to the player:

* `critical` - the player is misled about what happens or given wrong \
information: inverted/dropped/added negation; wrong name, number or referent; \
inverted placeholder roles (a value shown in the wrong slot); an untranslated \
fragment that hides meaning; an outcome the player cannot read. If a player \
would act on false information, it is critical.
* `major` - meaning distorted but a player can still recover it from context, or \
an approved glossary term rendered against the glossary.
* `minor` - style, register, or awkward phrasing that does not change what the \
player understands.
* `none` - the target is correct. A different but faithful wording is `none`. \
Length differences are not errors; Chinese runs shorter than Russian.

Glossary conformance is necessary but never sufficient: a segment whose terms \
all match can still be a critical mistranslation or a pile of correct words in \
an order no player can parse. Judge the sentence first, the terms second.

Give a one-line English `reason` for every segment: what is wrong (with a \
back-translation of the disputed Chinese), or why it is correct. Answer for \
every segment exactly once, keyed by its `id`."""


def schema() -> dict[str, Any]:
    seg = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "severity": {"type": "string", "enum": list(SEVERITIES)},
            "reason": {"type": "string"},
        },
        "required": ["id", "severity", "reason"],
        "additionalProperties": False,
    }
    return {
        "name": "severity_labels",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"segments": {"type": "array", "items": seg}},
            "required": ["segments"],
            "additionalProperties": False,
        },
    }


PRICES: dict[str, tuple[float, float]] = {}


def load_prices() -> None:
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for entry in data.get("data", []):
            pr = entry.get("pricing", {})
            p, c = float(pr.get("prompt", "0")), float(pr.get("completion", "0"))
            if p or c:
                PRICES[entry.get("id", "")] = (p, c)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass


def post(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def load_units(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out


def load_glossary(path: Path) -> list[tuple[str, str]]:
    return [tuple(p) for p in json.loads(path.read_text(encoding="utf-8"))]


def glossary_for(source: str, terms: list[tuple[str, str]]) -> list[dict[str, str]]:
    low = source.lower()
    return [
        {"source": s, "target": t}
        for s, t in terms
        if re.search(rf"(?<![\w\u0400-\u04ff]){re.escape(s.lower())}(?![\w\u0400-\u04ff])", low)
    ]


def segment(index: int, u: dict[str, Any], terms: list[tuple[str, str]]) -> dict[str, Any]:
    seg: dict[str, Any] = {
        "id": index,
        "key": u.get("context", ""),
        "source_ru": u["source"],
        "target_zh": u["target"],
    }
    g = glossary_for(u["source"], terms)
    if g:
        seg["glossary"] = g
    return seg


def annotate_batch(
    model: str, batch: list[dict[str, Any]], terms: list, api_key: str, timeout: int
) -> tuple[list[dict[str, Any]] | None, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"segments": [segment(i, u, terms) for i, u in enumerate(batch)]},
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_schema", "json_schema": schema()},
        "provider": {"require_parameters": True},
        "usage": {"include": True},
    }
    cost = 0.0
    for attempt in range(2):
        try:
            resp = post(payload, api_key, timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 0:
                continue
            return None, cost
        u = resp.get("usage", {})
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        c = u.get("cost", 0)
        if not c:
            pp, cp = PRICES.get(model, (0.0, 0.0))
            c = pt * pp + ct * cp
        cost += c
        try:
            body = resp["choices"][0]["message"]
            content = body.get("parsed") or body.get("content")
            segs = (json.loads(content) if isinstance(content, str) else content)["segments"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            if attempt == 0:
                continue
            return None, cost
        if len(segs) == len(batch):
            return segs, cost
        if attempt == 0:
            continue
        return None, cost
    return None, cost


def main() -> None:
    p = argparse.ArgumentParser(description="zh ground-truth severity annotator")
    p.add_argument("--model", required=True)
    p.add_argument("--input", default="st2-zh-units.jsonl")
    p.add_argument("--glossary-file", default="st2-summer-glossary-zh.json")
    p.add_argument("--out", required=True)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--timeout", type=int, default=120)
    args = p.parse_args()

    units = load_units(Path(args.input))
    terms = load_glossary(Path(args.glossary_file))
    print(f"Loaded {len(units)} units, {len(terms)} glossary terms")

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        sys.exit(1)
    load_prices()

    result: dict[str, dict[str, str]] = {}
    total_cost = 0.0
    unparsed = 0
    batches = [units[i : i + args.batch_size] for i in range(0, len(units), args.batch_size)]
    t0 = time.monotonic()
    for bi, batch in enumerate(batches):
        segs, cost = annotate_batch(args.model, batch, terms, api_key, args.timeout)
        total_cost += cost
        if segs is None:
            unparsed += len(batch)
            for u in batch:
                result[str(u["id"])] = {"severity": "unparsed", "reason": ""}
        else:
            for u, s in zip(batch, segs):
                result[str(u["id"])] = {
                    "severity": s.get("severity", "unparsed"),
                    "reason": s.get("reason", ""),
                }
        print(f"  batch {bi + 1}/{len(batches)} done  ", end="\r", file=sys.stderr)
    print(file=sys.stderr)

    out = {"model": args.model, "unparsed": unparsed, "labels": result}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter

    dist = Counter(v["severity"] for v in result.values())
    print(f"severity dist: {dict(dist)}")
    print(f"unparsed: {unparsed}  cost: ${total_cost:.4f}  time: {time.monotonic() - t0:.1f}s")
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
