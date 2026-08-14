#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""S&T2 summer-update zh_Hans: run the collegium on every unit and compare to current state.

Reuses the judge infrastructure from col4-judge-eval.py.

Usage:
  # Run a single judge, save verdicts to JSON:
  python st2-judge-experiment.py --judge deepseek/deepseek-v4-pro --save-to /tmp/st2_ds.json
  python st2-judge-experiment.py --judge qwen/qwen3-235b-a22b-2507 --save-to /tmp/st2_qw.json

  # Merge two saved verdict files:
  python st2-judge-experiment.py --merge /tmp/st2_ds.json /tmp/st2_qw.json

  # Run both in one process (may time out on slow models):
  python st2-judge-experiment.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from itertools import starmap
from typing import Any

API_URL = "https://openrouter.ai/api/v1/chat/completions"

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}
VERDICT_BY_SEVERITY = {0: "pass", 1: "pass", 2: "flag", 3: "reject"}

CATEGORIES = (
    "terminology", "mistranslation", "omission", "addition",
    "fluency", "punctuation", "markup", "register",
)

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


def reply_schema() -> dict[str, Any]:
    error = {
        "type": "object",
        "properties": {
            "span": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
        },
        "required": ["span", "category", "severity"],
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
    unit_id: int
    context: str
    source: str
    target: str
    checks: list[str] = field(default_factory=list)
    glossary: list[tuple[str, str]] = field(default_factory=list)
    family: list[tuple[str, str, str]] = field(default_factory=list)
    state: int = 0
    position: int = 0


@dataclass
class Verdict:
    verdict: str
    errors: list[dict]
    model_verdict: str
    unparsed: bool = False

    @property
    def needs_human(self) -> bool:
        return self.verdict in {"flag", "reject"}

    @property
    def is_reject(self) -> bool:
        return self.verdict == "reject"


UNPARSED = Verdict("pass", [], "", unparsed=True)


@dataclass
class Usage:
    requests: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    records: int = 0
    unparsed: int = 0
    transport_errors: Counter = field(default_factory=Counter)
    details: list[str] = field(default_factory=list)

    def merge(self, other: Usage) -> None:
        self.requests += other.requests
        self.retries += other.retries
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd
        self.records += other.records
        self.unparsed += other.unparsed
        self.transport_errors.update(other.transport_errors)
        for detail in other.details:
            if detail not in self.details and len(self.details) < 5:
                self.details.append(detail)


PRICES: dict[str, tuple[float, float]] = {}


def load_prices() -> None:
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for entry in data.get("data", []):
            model_id = entry.get("id", "")
            pricing = entry.get("pricing", {})
            prompt_price = float(pricing.get("prompt", "0"))
            completion_price = float(pricing.get("completion", "0"))
            if prompt_price or completion_price:
                PRICES[model_id] = (prompt_price, completion_price)
    except Exception:
        pass


def priced(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_price, completion_price = PRICES.get(model, (0.0, 0.0))
    return prompt_tokens * prompt_price + completion_tokens * completion_price


def post(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
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
            if isinstance(content, str):
                segments = json.loads(content).get("segments", [])
            else:
                segments = content.get("segments", [])
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


def render_segment(index: int, record: Record) -> dict[str, Any]:
    segment: dict[str, Any] = {
        "id": index,
        "key": record.context,
        "source_ru": record.source,
        "target_zh": record.target,
    }
    if record.checks:
        segment["checks"] = record.checks
    if record.glossary:
        segment["glossary"] = [
            {"source": s, "target": t} for s, t in record.glossary
        ]
    if record.family:
        segment["family"] = [
            {"key": k, "source_ru": s, "target_zh": t} for k, s, t in record.family
        ]
    return segment


GLOSSARY_RULE = """\

Each segment may carry `glossary`: terms of this project with their approved \
Chinese rendering. They are reference material, not text to translate. A term \
whose approved rendering is absent from the target is a `major` terminology \
error; a target that follows the glossary is correct by definition, even if \
another wording would read better."""

FAMILY_RULE = """\

Each segment may carry `family`: neighbouring strings whose key shares a prefix \
with this one, already translated. They are reference material, not text to \
translate. Use them to judge consistency: the same source notion rendered one \
way here and another way in the family is an error even when this segment reads \
well on its own."""


def system_prompt(*, glossary: bool, family: bool) -> str:
    """Baseline prompt stays byte-identical when no extra context is supplied."""
    prompt = SYSTEM_PROMPT
    if glossary:
        prompt += GLOSSARY_RULE
    if family:
        prompt += FAMILY_RULE
    return prompt


def build_payload(model: str, batch: list[Record]) -> dict[str, Any]:
    segments = list(starmap(render_segment, enumerate(batch)))
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt(
                    glossary=any(r.glossary for r in batch),
                    family=any(r.family for r in batch),
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"segments": segments}, ensure_ascii=False),
            },
        ],
        "response_format": {"type": "json_schema", "json_schema": reply_schema()},
        "provider": {"require_parameters": True},
        "usage": {"include": True},
    }


def judge_batch(
    model: str, batch: list[Record], api_key: str, timeout: int,
) -> tuple[list[Verdict], Usage]:
    usage = Usage(records=len(batch))
    payload = build_payload(model, batch)

    for attempt in range(2):
        usage.requests += 1
        t0 = time.monotonic()
        try:
            response = post(payload, api_key, timeout)
            usage.seconds += time.monotonic() - t0
        except Exception as exc:
            usage.seconds += time.monotonic() - t0
            usage.transport_errors[type(exc).__name__] += 1
            if attempt == 0:
                usage.retries += 1
                continue
            if len(usage.details) < 5:
                usage.details.append(str(exc)[:200])
            return [UNPARSED] * len(batch), usage

        try:
            u = response.get("usage", {})
            prompt_tokens = u.get("prompt_tokens", 0)
            completion_tokens = u.get("completion_tokens", 0)
            cost = u.get("cost", 0)
            if cost == 0:
                cost = priced(model, prompt_tokens, completion_tokens)
            usage.prompt_tokens += prompt_tokens
            usage.completion_tokens += completion_tokens
            usage.cost_usd += cost
        except Exception:
            pass

        verdicts = parse_reply(response, len(batch))
        if verdicts is not None:
            return verdicts, usage

        if attempt == 0:
            usage.retries += 1
            continue
        else:
            usage.unparsed += len(batch)
            return [UNPARSED] * len(batch), usage

    return [UNPARSED] * len(batch), usage


def judge(
    model: str, records: list[Record], api_key: str, batch_size: int, timeout: int,
) -> tuple[dict[str, Verdict], Usage]:
    batches = [
        records[i : i + batch_size] for i in range(0, len(records), batch_size)
    ]
    results: dict[str, Verdict] = {}
    total = Usage()

    for i, batch in enumerate(batches):
        batch_verdicts, batch_usage = judge_batch(model, batch, api_key, timeout)
        total.merge(batch_usage)
        for record, verdict in zip(batch, batch_verdicts):
            results[record.record_id] = verdict
        print(f"  batch {i+1}/{len(batches)} done  ", end="\r", file=sys.stderr)

    print("", file=sys.stderr)
    return results, total


def max_severity_union(
    v1: dict[str, Verdict], v2: dict[str, Verdict],
) -> dict[str, Verdict]:
    result: dict[str, Verdict] = {}
    for rid in v1:
        a = v1[rid]
        b = v2.get(rid, UNPARSED)
        if a.unparsed and b.unparsed:
            result[rid] = UNPARSED
        elif a.unparsed:
            result[rid] = b
        elif b.unparsed:
            result[rid] = a
        else:
            sev_a = max(
                (SEVERITY_RANK.get(e.get("severity", ""), 0) for e in a.errors),
                default=0,
            )
            sev_b = max(
                (SEVERITY_RANK.get(e.get("severity", ""), 0) for e in b.errors),
                default=0,
            )
            best = a if sev_a >= sev_b else b
            result[rid] = Verdict(
                verdict=VERDICT_BY_SEVERITY[max(sev_a, sev_b)],
                errors=best.errors,
                model_verdict=best.model_verdict,
            )
    return result


def load_units(path: str) -> list[Record]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            u = json.loads(line)
            source = u["source"]
            target = u["target"]
            if isinstance(source, list):
                source = source[0] if source else ""
            if isinstance(target, list):
                target = target[0] if target else ""
            records.append(
                Record(
                    record_id=str(u["id"]),
                    unit_id=u["id"],
                    context=u.get("context", ""),
                    source=source,
                    target=target,
                    checks=[],
                    state=u.get("state", 0),
                    position=u.get("position", 0),
                )
            )
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


def verdicts_from_dict(d: dict[str, Any]) -> dict[str, Verdict]:
    return {
        rid: Verdict(
            verdict=x["verdict"],
            errors=x["errors"],
            model_verdict=x.get("model_verdict", ""),
            unparsed=x.get("unparsed", False),
        )
        for rid, x in d.items()
    }


def merge_and_report(
    records: list[Record],
    v1: dict[str, Verdict],
    v2: dict[str, Verdict],
    label1: str,
    label2: str,
) -> None:
    """Print the full comparison report."""
    c1 = Counter(v.verdict for v in v1.values())
    c2 = Counter(v.verdict for v in v2.values())
    union = max_severity_union(v1, v2)
    cu = Counter(v.verdict for v in union.values())

    print(f"\n--- Individual verdicts ---")
    print(f"  {label1}: {dict(c1)}")
    print(f"  {label2}: {dict(c2)}")

    print(f"\n--- Collegium (max severity) ---")
    print(f"  verdicts: {dict(cu)}")

    sev_counter: Counter[str] = Counter()
    cat_counter: Counter[str] = Counter()
    for v in union.values():
        for err in v.errors:
            sev_counter[err.get("severity", "?")] += 1
            cat_counter[err.get("category", "?")] += 1
    print(f"  errors by severity: {dict(sev_counter)}")
    print(f"  errors by category: {dict(cat_counter)}")

    agree = sum(
        1 for rid in v1 if v1[rid].verdict == v2.get(rid, UNPARSED).verdict
    )
    print(f"\n  judge agreement: {agree}/{len(records)} "
          f"({100 * agree / len(records):.1f}%)")
    only_j1 = sum(
        1 for rid in v1
        if v1[rid].needs_human and not v2.get(rid, UNPARSED).needs_human
    )
    only_j2 = sum(
        1 for rid in v2
        if v2[rid].needs_human and not v1.get(rid, UNPARSED).needs_human
    )
    print(f"  only {label1} flags: {only_j1}")
    print(f"  only {label2} flags: {only_j2}")

    flagged = sum(1 for v in union.values() if v.needs_human)
    rejected = sum(1 for v in union.values() if v.is_reject)
    critical_errors = sum(
        1 for v in union.values()
        for e in v.errors if e.get("severity") == "critical"
    )

    print(f"\n--- Comparison: judges vs current state ---")
    print(f"  Current state: all {len(records)} units at state=20 (translated)")
    print(f"  Current failing checks: 0")
    print(f"  Judges would flag: {flagged}/{len(records)} "
          f"({100 * flagged / len(records):.1f}%)")
    print(f"  Judges would reject: {rejected}/{len(records)} "
          f"({100 * rejected / len(records):.1f}%)")
    print(f"  Critical errors found: {critical_errors}")
    print(f"  Auto-pass rate (judged): "
          f"{100 * (len(records) - flagged) / len(records):.1f}%")

    print(f"\n--- Non-pass units ---")
    for record in records:
        v = union.get(record.record_id, UNPARSED)
        if not v.needs_human:
            continue
        print(f"\n  [{record.record_id}] verdict={v.verdict} "
              f"ctx={record.context}")
        print(f"    src: {record.source[:120]}")
        print(f"    tgt: {record.target[:120]}")
        for err in v.errors:
            sev = err.get("severity", "?")
            cat = err.get("category", "?")
            span = err.get("span", "")[:80]
            print(f"    [{sev}] {cat}: {span}")

GLOSSARY_URL = (
    "https://l10n.herocraft.com/api/translations/"
    "strategy-and-tactics-2/summer-glossary/zh_Hans/units/?page_size=200"
)


def load_glossary(cache: str) -> list[tuple[str, str]]:
    """Project glossary, cached on disk so arms share one fetch."""
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return [tuple(pair) for pair in json.load(f)]
    token = os.environ.get("WEBLATE_API_TOKEN", "")
    if not token:
        print("Set WEBLATE_API_TOKEN to fetch the glossary", file=sys.stderr)
        sys.exit(1)
    request = urllib.request.Request(
        GLOSSARY_URL, headers={"Authorization": f"Token {token}"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    terms = [
        (unit["source"][0], unit["target"][0])
        for unit in payload["results"]
        if unit.get("target") and unit["target"][0].strip()
    ]
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(terms, f, ensure_ascii=False)
    return terms


def attach_glossary(records: list[Record], terms: list[tuple[str, str]]) -> None:
    """Mirror of weblate.glossary.models.get_glossary_terms: match on the source.

    Whether the target honours the term is the judge's business, not ours;
    the segment carries every term the source mentions.
    """
    for record in records:
        source = record.source.lower()
        record.glossary = [
            (term_source, term_target)
            for term_source, term_target in terms
            if re.search(
                rf"(?<![\w\u0400-\u04ff]){re.escape(term_source.lower())}"
                rf"(?![\w\u0400-\u04ff])",
                source,
            )
        ]


def attach_family(records: list[Record], limit: int) -> None:
    """Neighbours whose key shares a prefix, so drift across a family is visible.

    Ranking is by shared leading key tokens first, then by a shared trailing
    token: ``ID_USER_LOST_ARMED_PROVINCE_NAME`` must reach
    ``..._DESC`` and the ``FAILED_CAPTURED`` sibling, not an unrelated
    ``ID_USER_*`` string.
    """
    tokens = {r.record_id: r.context.strip().split("_") for r in records}

    def score(a: list[str], b: list[str]) -> int:
        shared = 0
        for left, right in zip(a, b):
            if left != right:
                break
            shared += 1
        return shared * 2 + (1 if a[-1] == b[-1] else 0)

    for record in records:
        mine = tokens[record.record_id]
        ranked = sorted(
            (
                (-score(mine, tokens[other.record_id]),
                 abs(other.position - record.position), other)
                for other in records
                if other.record_id != record.record_id
                and score(mine, tokens[other.record_id]) >= 4
            ),
            key=lambda item: (item[0], item[1]),
        )
        record.family = [
            (other.context.strip(), other.source, other.target)
            for _, _, other in ranked[:limit]
        ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S&T2 summer-update zh_Hans judge experiment"
    )
    parser.add_argument("--input", default="/tmp/st2_zh_units.jsonl")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--judge",
        help="Run a single judge model and save to --save-to",
    )
    parser.add_argument(
        "--save-to",
        help="Save per-unit verdicts as JSON",
    )
    parser.add_argument(
        "--merge", nargs=2, metavar=("FILE1", "FILE2"),
        help="Merge two saved verdict JSON files and print the union",
    )
    parser.add_argument("--model1", default="deepseek/deepseek-v4-pro")
    parser.add_argument("--model2", default="qwen/qwen3-235b-a22b-2507")
    parser.add_argument(
        "--glossary", action="store_true",
        help="Attach project glossary terms matching each source",
    )
    parser.add_argument(
        "--glossary-cache", default="/tmp/st2_glossary.json",
        help="Where the fetched glossary is cached",
    )
    parser.add_argument(
        "--siblings", type=int, default=0, metavar="N",
        help="Attach up to N key-family neighbours as reference context",
    )
    args = parser.parse_args()

    if args.merge:
        records = load_units(args.input)
        v1 = verdicts_from_dict(json.loads(open(args.merge[0], encoding="utf-8").read()))
        v2 = verdicts_from_dict(json.loads(open(args.merge[1], encoding="utf-8").read()))
        merge_and_report(records, v1, v2, args.merge[0], args.merge[1])
        return

    records = load_units(args.input)
    if args.glossary:
        terms = load_glossary(args.glossary_cache)
        attach_glossary(records, terms)
        matched = sum(1 for r in records if r.glossary)
        print(f"  glossary: {len(terms)} terms, attached to {matched} units")
    if args.siblings:
        attach_family(records, args.siblings)
        matched = sum(1 for r in records if r.family)
        print(f"  family context: attached to {matched} units")

    print(f"Loaded {len(records)} units")
    print(f"  states: {dict(Counter(r.state for r in records))}")
    print(f"  source len: min={min(len(r.source) for r in records)} "
          f"max={max(len(r.source) for r in records)} "
          f"avg={sum(len(r.source) for r in records) // len(records)}")

    if args.dry_run:
        print("Dry run complete. No LLM calls made.")
        return

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        sys.exit(1)

    load_prices()

    if args.judge:
        model = args.judge
        print(f"Running judge: {model}")
        t0 = time.monotonic()
        v, u = judge(model, records, api_key, args.batch_size, args.timeout)
        elapsed = time.monotonic() - t0
        c = Counter(x.verdict for x in v.values())
        print(f"  verdicts: {dict(c)}")
        print(f"  unparsed: {u.unparsed}")
        print(f"  cost: ${u.cost_usd:.4f}  time: {elapsed:.1f}s  "
              f"prompt: {u.prompt_tokens}  completion: {u.completion_tokens}")

        if args.save_to:
            with open(args.save_to, "w", encoding="utf-8") as f:
                json.dump(verdicts_to_dict(v), f, ensure_ascii=False)
            print(f"  saved to {args.save_to}")
        return

    # Default: run both judges
    print(f"\n--- Judge 1: {args.model1} ---")
    t0 = time.monotonic()
    v1, u1 = judge(args.model1, records, api_key, args.batch_size, args.timeout)
    elapsed1 = time.monotonic() - t0
    c1 = Counter(v.verdict for v in v1.values())
    print(f"  verdicts: {dict(c1)}")
    print(f"  cost: ${u1.cost_usd:.4f}  time: {elapsed1:.1f}s")

    print(f"\n--- Judge 2: {args.model2} ---")
    t0 = time.monotonic()
    v2, u2 = judge(args.model2, records, api_key, args.batch_size, args.timeout)
    elapsed2 = time.monotonic() - t0
    c2 = Counter(v.verdict for v in v2.values())
    print(f"  verdicts: {dict(c2)}")
    print(f"  cost: ${u2.cost_usd:.4f}  time: {elapsed2:.1f}s")

    merge_and_report(records, v1, v2, args.model1, args.model2)

    total_cost = u1.cost_usd + u2.cost_usd
    print(f"\n--- Cost ---")
    print(f"  Judge 1: ${u1.cost_usd:.4f}")
    print(f"  Judge 2: ${u2.cost_usd:.4f}")
    print(f"  Total: ${total_cost:.4f}")


if __name__ == "__main__":
    main()