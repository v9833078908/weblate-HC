#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Severity recalibration on the sealed zh_Hans slice (S&T2 summer-update, 124 units).

Executes plan docs/LLM-first/plans/2026-08-14-judge-severity-recalibration.md,
track 2. Three arms, all with the project glossary in the prompt and the
"conformance necessary but never sufficient" rule (settled by the 2026-08-14
run):

    A  baseline      current prompt + glossary          (reproduces armC-fixedrule)
    B  + description required English description per error (back-translation)
    C  + rubric       + player-consequence severity rubric
    D  + render       C + rendered previews: sample values substituted into
                      placeholders of both source and target (2026-08-19 addendum:
                      tests whether judge-invisible render defects, 24207-class,
                      become visible when the judged artifact is the rendered string)

Each (arm, model) is run --repeats times; the median and spread are read by the
scoring step, never a single run. Glossary and offline check_glossary results
are read from committed files, so the corpus and inputs are frozen and offline;
only the judge calls hit OpenRouter.

Inputs (committed, frozen):
    docs/misc/st2-zh-units.jsonl            124 units {id,context,source,target,note,state}
    docs/misc/st2-summer-glossary-zh.json   30 approved ru->zh terms
    docs/misc/st2-zh-glossary-checks.json   offline check_glossary firings

Usage:
    OPENROUTER_API_KEY=... python3 docs/misc/st2-zh-recalibration.py \
        --arm C --model qwen/qwen3-235b-a22b-2507 --repeats 5 --out-dir docs/misc/st2-zh-recal
    python3 docs/misc/st2-zh-recalibration.py --arm A --dry-run   # print prompt+payload, no calls
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_URL = "https://openrouter.ai/api/v1/chat/completions"
REPO_ROOT = Path(__file__).resolve().parents[2]
MISC = REPO_ROOT / "docs" / "misc"

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}
VERDICT_BY_SEVERITY = {0: "pass", 1: "pass", 2: "flag", 3: "reject"}
CATEGORIES = (
    "terminology",
    "mistranslation",
    "omission",
    "addition",
    "fluency",
    "punctuation",
    "markup",
    "register",
)

# Arm A prompt is byte-identical to st2-judge-experiment.py system_prompt(glossary=True)
# so that arm A reproduces the committed armC-fixedrule baseline.
SYSTEM_PROMPT = """\
You are an MQM annotator for a Russian to Chinese video game localization. The \
game is a turn-based strategy game set in World War II; the register is formal \
military/political, and that is intended, not an error.

For each segment you receive the source, the Chinese target under review, and \
the deterministic checks the pipeline has already run against that exact target. \
Report only translation errors, as a list. Do not rewrite the target, do not \
explain, do not praise.

Rules.

1. Report a span exactly as it appears in the target. A span you cannot copy \
from the target verbatim is not a valid error.
2. Do not re-report anything already listed under `checks`: code has proven \
those, and repeating them adds nothing.
3. Punctuation and capitalization are owned by deterministic fixes. Report them \
only where they change meaning.
4. Length differences are not errors. Chinese runs shorter than Russian.
5. A different but faithful wording is not an error. Report only what a player \
would experience as wrong.

Severity.

* `critical` - meaning inverted, lost, or replaced: a dropped or added \
negation, a wrong name, number or referent, an untranslated fragment, a line \
that would mislead the player about what happens.
* `major` - meaning distorted but recoverable, or a glossary term rendered \
against the glossary.
* `minor` - style, register or awkward phrasing that does not change meaning.

Categories: terminology, mistranslation, omission, addition, fluency, \
punctuation, markup, register.

Give a verdict per segment: `reject` if any error is critical, `flag` if the \
worst is major, otherwise `pass`. A segment with no errors is `pass`.

Answer for every segment you were given, exactly once each, keyed by its `id`."""

GLOSSARY_RULE = """\

Each segment may carry `glossary`: terms of this project with their approved \
Chinese rendering. They are reference material, not text to translate. A term \
rendered against its approved form is a `major` terminology error.

Glossary conformance is necessary but never sufficient. A segment whose terms \
all match the glossary can still be mistranslated, ungrammatical, or a pile of \
correct words in an order no player can parse. Judge the sentence first, the \
terms second."""

# Arm B: the description contract. The reader is a producer who does not read
# Chinese, so every error must carry a self-contained English explanation with a
# back-translation of the disputed span.
DESCRIPTION_RULE = """\

Every error must carry a `description`, written in English for a producer who \
does NOT read Chinese. State the back-translation of the disputed span and what \
is wrong with it: "the target says X, which means Y, whereas the source says Z". \
A bare span, a Chinese-only description, or a repeat of the category is not a \
valid description: the reader cannot act on it."""

# Arm C: severity by consequence to the player, replacing the terse bullets.
RUBRIC_RULE = """\

Severity is decided by the consequence to the player, not by how wrong the \
wording looks:

* `critical` - the player is misled about what happens or is given wrong \
information: an inverted, dropped or added negation; a wrong name, number or \
referent; inverted placeholder roles (a value shown in the wrong slot); an \
untranslated fragment that hides meaning; a line whose outcome the player cannot \
read. If the player would act on false information, it is critical.
* `major` - the meaning is distorted but a player can still recover it from \
context, or an approved glossary term is rendered against the glossary.
* `minor` - style, register, or awkward phrasing that does not change what the \
player understands.

When unsure between two levels, ask only: would the player be misled about what \
happened? Yes -> critical. Recoverable -> major. Cosmetic -> minor."""

# Arm D: rendered previews. The 24207 class (placeholder roles read wrong once
# the engine substitutes real values) is invisible in the raw string, so the
# judge additionally receives a deterministic render with distinct sample values.
RENDER_RULE = """\

Segments may carry `rendered_source_ru` and `rendered_target_zh`: the same texts \
with sample values substituted into the engine placeholders ({0}, {[PARAM0]}, \
%KEY%). This is what a player actually sees. The samples are arbitrary distinct \
numbers or tokens; a placeholder may in the real game hold a name or a category, \
not only a number. Judge the rendered pair too: if the rendered target is \
ungrammatical, puts a value in a slot where it reads as the wrong role, or \
orders the substituted values so the player reads a different fact than the \
rendered source states, that is an error of the segment even though the raw \
placeholder string looks plausible."""

# Distinct, order-revealing sample values: a swapped or misplaced slot changes
# the rendered meaning visibly. Deterministic by placeholder index.
SAMPLE_VALUES = ("3", "7", "15", "28", "42", "56", "64", "77")

_PLACEHOLDER_RE = re.compile(r"\{\[PARAM(\d+)\]\}|\{(\d+)\}|%([A-Za-z_]+)%")


def render_preview(text: str) -> str | None:
    """Substitute sample values; None when the text has no placeholders."""

    def sub(match: re.Match[str]) -> str:
        param, plain, named = match.groups()
        if named is not None:
            return SAMPLE_VALUES[sum(named.encode()) % len(SAMPLE_VALUES)]
        return SAMPLE_VALUES[int(param or plain) % len(SAMPLE_VALUES)]

    rendered, count = _PLACEHOLDER_RE.subn(sub, text)
    return rendered if count else None


def system_prompt(arm: str) -> str:
    prompt = SYSTEM_PROMPT + GLOSSARY_RULE
    if arm in ("B", "C", "D"):
        prompt += DESCRIPTION_RULE
    if arm in ("C", "D"):
        prompt += RUBRIC_RULE
    if arm == "D":
        prompt += RENDER_RULE
    return prompt


def reply_schema(with_description: bool) -> dict[str, Any]:
    props: dict[str, Any] = {
        "span": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
    }
    required = ["span", "category", "severity"]
    if with_description:
        props["description"] = {"type": "string"}
        required.append("description")
    error = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    segment = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "errors": {"type": "array", "items": error},
            "verdict": {"type": "string", "enum": ["pass", "flag", "reject"]},
        },
        "required": ["id", "errors", "verdict"],
        "additionalProperties": False,
    }
    return {
        "name": "mqm_verdicts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"segments": {"type": "array", "items": segment}},
            "required": ["segments"],
            "additionalProperties": False,
        },
    }


@dataclass
class Record:
    record_id: str
    context: str
    source: str
    target: str
    checks: list[str] = field(default_factory=list)
    glossary: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Verdict:
    verdict: str
    errors: list[dict]
    model_verdict: str
    unparsed: bool = False


UNPARSED = Verdict("pass", [], "", unparsed=True)


@dataclass
class Usage:
    requests: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    unparsed: int = 0
    transport_errors: Counter = field(default_factory=Counter)

    def merge(self, other: Usage) -> None:
        self.requests += other.requests
        self.retries += other.retries
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd
        self.seconds += other.seconds
        self.unparsed += other.unparsed
        self.transport_errors.update(other.transport_errors)


PRICES: dict[str, tuple[float, float]] = {}


def load_prices() -> None:
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for entry in data.get("data", []):
            pricing = entry.get("pricing", {})
            prompt_price = float(pricing.get("prompt", "0"))
            completion_price = float(pricing.get("completion", "0"))
            if prompt_price or completion_price:
                PRICES[entry.get("id", "")] = (prompt_price, completion_price)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass


def priced(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_price, completion_price = PRICES.get(model, (0.0, 0.0))
    return prompt_tokens * prompt_price + completion_tokens * completion_price


def render_segment(index: int, record: Record, arm: str = "A") -> dict[str, Any]:
    segment: dict[str, Any] = {
        "id": index,
        "key": record.context,
        "source_ru": record.source,
        "target_zh": record.target,
    }
    if arm == "D":
        rendered_source = render_preview(record.source)
        rendered_target = render_preview(record.target)
        if rendered_source is not None or rendered_target is not None:
            segment["rendered_source_ru"] = rendered_source or record.source
            segment["rendered_target_zh"] = rendered_target or record.target
    if record.checks:
        segment["checks"] = record.checks
    if record.glossary:
        segment["glossary"] = [{"source": s, "target": t} for s, t in record.glossary]
    return segment


def build_payload(model: str, batch: list[Record], arm: str) -> dict[str, Any]:
    segments = [render_segment(i, record, arm) for i, record in enumerate(batch)]
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt(arm)},
            {
                "role": "user",
                "content": json.dumps({"segments": segments}, ensure_ascii=False),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": reply_schema(arm in ("B", "C", "D")),
        },
        "provider": {"require_parameters": True},
        "usage": {"include": True},
    }


def post(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def parse_reply(payload: dict[str, Any], size: int) -> list[Verdict] | None:
    try:
        body = payload["choices"][0]["message"]
        if "parsed" in body:
            segments = body["parsed"].get("segments", [])
        elif "content" in body:
            content = body["content"]
            if content is None:
                return None
            segments = (
                json.loads(content).get("segments", [])
                if isinstance(content, str)
                else content.get("segments", [])
            )
        else:
            segments = body.get("segments", [])
    except (KeyError, json.JSONDecodeError):
        return None
    if len(segments) != size:
        return None
    verdicts: list[Verdict] = []
    for seg in segments:
        if not isinstance(seg, dict):
            return None
        errors = seg.get("errors", [])
        if not isinstance(errors, list):
            return None
        max_sev = 0
        for err in errors:
            if isinstance(err, dict):
                max_sev = max(max_sev, SEVERITY_RANK.get(err.get("severity", ""), 0))
        verdicts.append(
            Verdict(
                verdict=VERDICT_BY_SEVERITY[max_sev],
                errors=errors,
                model_verdict=seg.get("verdict", ""),
            )
        )
    return verdicts


def judge_batch(
    model: str, batch: list[Record], arm: str, api_key: str, timeout: int
) -> tuple[list[Verdict], Usage]:
    usage = Usage()
    payload = build_payload(model, batch, arm)
    for attempt in range(2):
        usage.requests += 1
        t0 = time.monotonic()
        try:
            response = post(payload, api_key, timeout)
            usage.seconds += time.monotonic() - t0
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            usage.seconds += time.monotonic() - t0
            usage.transport_errors[type(exc).__name__] += 1
            if attempt == 0:
                usage.retries += 1
                continue
            return [UNPARSED] * len(batch), usage
        u = response.get("usage", {})
        prompt_tokens = u.get("prompt_tokens", 0)
        completion_tokens = u.get("completion_tokens", 0)
        cost = u.get("cost", 0) or priced(model, prompt_tokens, completion_tokens)
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.cost_usd += cost
        verdicts = parse_reply(response, len(batch))
        if verdicts is not None:
            return verdicts, usage
        if attempt == 0:
            usage.retries += 1
            continue
        usage.unparsed += len(batch)
        return [UNPARSED] * len(batch), usage
    return [UNPARSED] * len(batch), usage


def judge(
    model: str, records: list[Record], arm: str, api_key: str, batch_size: int, timeout: int, sleep: float,
) -> tuple[dict[str, Verdict], Usage]:
    batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
    results: dict[str, Verdict] = {}
    total = Usage()
    for i, batch in enumerate(batches):
        verdicts, usage = judge_batch(model, batch, arm, api_key, timeout)
        total.merge(usage)
        for record, verdict in zip(batch, verdicts):
            results[record.record_id] = verdict
        print(f"  batch {i + 1}/{len(batches)} done  ", end="\r", file=sys.stderr)
        if sleep and i + 1 < len(batches):
            time.sleep(sleep)
    print(file=sys.stderr)
    return results, total


def attach_glossary(records: list[Record], terms: list[tuple[str, str]]) -> None:
    import re

    for record in records:
        source = record.source.lower()
        record.glossary = [
            (ts, tt)
            for ts, tt in terms
            if re.search(
                rf"(?<![\w\u0400-\u04ff]){re.escape(ts.lower())}(?![\w\u0400-\u04ff])",
                source,
            )
        ]


def load_records(
    units_path: Path, glossary_path: Path, checks_path: Path
) -> list[Record]:
    records: list[Record] = []
    checks = json.loads(checks_path.read_text(encoding="utf-8")).get("checks", {})
    with units_path.open(encoding="utf-8") as f:
        for line in f:
            u = json.loads(line)
            rid = str(u["id"])
            records.append(
                Record(
                    record_id=rid,
                    context=u.get("context", ""),
                    source=u["source"],
                    target=u["target"],
                    checks=list(checks.get(rid, [])),
                )
            )
    terms = [tuple(pair) for pair in json.loads(glossary_path.read_text(encoding="utf-8"))]
    attach_glossary(records, terms)
    return records


def verdicts_to_dict(v: dict[str, Verdict]) -> dict[str, Any]:
    return {
        rid: {
            "verdict": x.verdict,
            "errors": x.errors,
            "model_verdict": x.model_verdict,
            "unparsed": x.unparsed,
        }
        for rid, x in v.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="zh severity recalibration, one arm")
    parser.add_argument("--arm", choices=("A", "B", "C", "D"), required=True)
    parser.add_argument("--model", default="qwen/qwen3-235b-a22b-2507")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--input", default=str(MISC / "st2-zh-units.jsonl"))
    parser.add_argument("--glossary-file", default=str(MISC / "st2-summer-glossary-zh.json"))
    parser.add_argument("--checks-file", default=str(MISC / "st2-zh-glossary-checks.json"))
    parser.add_argument("--out-dir", default=str(MISC / "st2-zh-recal"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start-run", type=int, default=1)
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds between batches")
    args = parser.parse_args()

    records = load_records(Path(args.input), Path(args.glossary_file), Path(args.checks_file))
    matched = sum(1 for r in records if r.glossary)
    checked = sum(1 for r in records if r.checks)
    print(f"Loaded {len(records)} units; glossary on {matched}; checks on {checked}")

    if args.dry_run:
        print("\n=== system prompt ===\n" + system_prompt(args.arm))
        print("\n=== sample payload (first batch) ===")
        print(
            json.dumps(
                build_payload(args.model, records[:5], args.arm)["messages"][1],
                ensure_ascii=False,
                indent=1,
            )
        )
        print("\nDry run: no LLM calls made.")
        return

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        sys.exit(1)
    load_prices()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_slug = args.model.split("/")[-1]
    grand = 0.0
    for k in range(args.start_run, args.repeats + 1):
        print(f"\n--- arm {args.arm}  {args.model}  run {k}/{args.repeats} ---")
        t0 = time.monotonic()
        v, u = judge(args.model, records, args.arm, api_key, args.batch_size, args.timeout, args.sleep)
        counts = Counter(x.verdict for x in v.values())
        grand += u.cost_usd
        print(
            f"  verdicts: {dict(counts)}  unparsed: {u.unparsed}  "
            f"cost: ${u.cost_usd:.4f}  time: {time.monotonic() - t0:.1f}s"
        )
        dest = out_dir / f"arm{args.arm}-{model_slug}-run{k}.json"
        dest.write_text(json.dumps(verdicts_to_dict(v), ensure_ascii=False), encoding="utf-8")
        print(f"  saved {dest}")
    print(f"\nTotal for {args.repeats} runs: ${grand:.4f}")


if __name__ == "__main__":
    main()
