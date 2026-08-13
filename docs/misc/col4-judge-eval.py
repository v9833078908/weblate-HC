# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Calibrate the two-stage LLM judge cascade on the COL4 fr golden set.

This is task B2' of phase 0.

The cascade, not two independent judges, is what gets measured here. Tier A
judges every record; tier B judges only what tier A escalated, and its verdict
wins where it ran. The roles are asymmetric on purpose, so the metrics are too:
tier A is scored on recall at the "needs a human" boundary, because anything it
lets through is never looked at again, and tier B is scored on precision over
the subset tier A handed it.

Standalone: no Django, no Weblate imports, stdlib only. The OpenRouter key comes
from the environment. Nothing is written to any Weblate instance.

Data. Labels and splits come from ``docs/misc/col4-judge-golden.json``. The
glossary, the developer note and the failing checks are joined by ``unit_id``
from ``dev-docker/data/col4-b0-units.jsonl`` and are passed to the judge as
they stand there. Note that the dump measured those checks on ``target_raw``,
before ``AUTOFIXES.fix_target()`` ran, so a record whose autofix removed a
trailing stop still carries ``end_stop``; recomputing them against the golden
targets leaves only ``reused``, on 37 of the 919 records. Passing the dump
values unchanged is a deliberate call, not an oversight. The ``test`` split is
sealed: it is only reachable by naming it alone.

Usage::

    export OPENROUTER_API_KEY=sk-or-...

    # Does the payload survive at all? One two-record batch per model.
    python docs/misc/col4-judge-eval.py --probe \
        --models anthropic/claude-haiku-4.5,openai/gpt-5.4-mini

    # Screening: each model alone over a split.
    python docs/misc/col4-judge-eval.py --arm single --split dev \
        --models anthropic/claude-haiku-4.5,openai/gpt-5.4-mini --out screen.json

    # The cascade and its mirror.
    python docs/misc/col4-judge-eval.py --arm cascade --split train,dev \
        --models anthropic/claude-haiku-4.5,openai/gpt-5.4-mini --out cascade.json
    python docs/misc/col4-judge-eval.py --arm mirror --split train,dev \
        --models anthropic/claude-haiku-4.5,openai/gpt-5.4-mini --out mirror.json

    # No network at all; prints the exact payload that would be sent.
    python docs/misc/col4-judge-eval.py --dry-run --split dev --limit 2

The last output line is ``JUDGE_JSON {...}``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import starmap
from math import comb, sqrt
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "docs" / "misc" / "col4-judge-golden.json"
DUMP_PATH = REPO_ROOT / "dev-docker" / "data" / "col4-b0-units.jsonl"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}
VERDICT_BY_SEVERITY = {0: "pass", 1: "pass", 2: "flag", 3: "reject"}
VERDICT_ORDER = {"pass": 0, "flag": 1, "reject": 2}
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

SYSTEM_PROMPT = """\
You are an MQM annotator for a Russian to French video game localization. The \
game is a dystopian narrative RPG set in a Soviet-style megastructure; the \
register is colloquial and often crude, and that is intended, not an error.

For each segment you receive the source, the French target under review, the \
glossary terms that apply to it, and the deterministic checks the pipeline has \
already run against that exact target. Report only translation errors, as a \
list. Do not rewrite the target, do not explain, do not praise.

Rules.

1. Report a span exactly as it appears in the target. A span you cannot copy \
from the target verbatim is not a valid error.
2. Do not re-report anything already listed under `checks`: code has proven \
those, and repeating them adds nothing.
3. Punctuation and capitalization are owned by deterministic fixes. Report them \
only where they change meaning.
4. Length differences are not errors. French runs longer than Russian.
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
    """
    Strict schema for the judge reply.

    ``minItems``/``maxItems`` are deliberately absent: they are outside the
    strict structured-output subset the OpenAI-family providers accept, and a
    rejected schema buys nothing. Completeness of the batch is enforced in code
    instead, where a short reply counts as a transport failure rather than as a
    set of verdicts.
    """
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
    stratum: str
    split: str
    context: str
    source: str
    target: str
    label: str
    severity: str | None
    defect_class: str | None
    annotator: str
    base_target: str = ""
    checks: list[str] = field(default_factory=list)
    glossary: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""

    @property
    def truth_needs_human(self) -> bool:
        return self.label == "defect"

    @property
    def truth_critical(self) -> bool:
        return self.severity == "critical"


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "seconds": round(self.seconds, 1),
            "records": self.records,
            "unparsed": self.unparsed,
            "cost_per_1000_records": (
                round(1000 * self.cost_usd / self.records, 4) if self.records else None
            ),
            "transport_errors": dict(self.transport_errors),
            "transport_error_samples": self.details,
        }


def load_records(splits: set[str]) -> list[Record]:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    with DUMP_PATH.open(encoding="utf-8") as handle:
        glossary = json.loads(handle.readline())["glossary"]
        rows = {}
        for line in handle:
            row = json.loads(line)
            rows[row["unit_id"]] = row

    records: list[Record] = []
    for entry in golden["records"]:
        if entry["split"] not in splits:
            continue
        row = rows.get(entry["unit_id"], {})
        terms = [
            (term, glossary[term])
            for term in row.get("gloss_terms", [])
            if term in glossary
        ]
        records.append(
            Record(
                record_id=entry["record_id"],
                unit_id=entry["unit_id"],
                stratum=entry["stratum"],
                split=entry["split"],
                context=entry["context"],
                source=entry["source"],
                target=entry["target"],
                label=entry["label"],
                severity=entry.get("severity"),
                defect_class=entry.get("defect_class"),
                annotator=entry.get("annotator", ""),
                base_target=entry.get("base_target", ""),
                checks=row.get("checks", []),
                glossary=terms,
                note=row.get("note", ""),
            )
        )
    records.sort(key=lambda record: record.record_id)
    return records


def render_segment(index: int, record: Record) -> dict[str, Any]:
    segment: dict[str, Any] = {
        "id": index,
        "key": record.context,
        "source_ru": record.source,
        "target_fr": record.target,
    }
    if record.glossary:
        segment["glossary"] = [
            {"ru": term, "fr": rendering} for term, rendering in record.glossary
        ]
    if record.checks:
        segment["checks"] = record.checks
    if record.note.strip():
        segment["note"] = record.note
    return segment


def changed_span(base: str, target: str) -> str:
    """
    Return the part of ``target`` that a mutation actually touched.

    A few-shot answer has to point at a span that is really there. Deriving it
    from the diff against the pre-mutation string keeps the example honest;
    inventing one teaches the judge to invent them too.
    """
    matcher = difflib.SequenceMatcher(None, base, target, autojunk=False)
    bounds = [
        (j1, j2) for tag, _i1, _i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    ]
    if not bounds:
        return ""
    start = min(left for left, _right in bounds)
    end = max(right for _left, right in bounds)
    while start > 0 and not target[start - 1].isspace():
        start -= 1
    while end < len(target) and not target[end].isspace():
        end += 1
    span = target[start:end].strip()
    if span:
        return span
    # A pure deletion landed on a word boundary; point at the word after it.
    tail = target[end:].split()
    return tail[0] if tail else ""


def few_shot_messages(train: list[Record]) -> tuple[list[dict], set[str]]:
    """
    One worked example per verdict, taken only from the train split.

    Few-shot drawn from dev or test would be leakage. The ids are returned so
    the examples can be held out of whatever is being measured. All three come
    from strata whose defect is a known edit of a known base string, because
    that is the only way to state a span that is provably correct.
    """
    chosen: list[tuple[Record, dict]] = []
    used_units: set[int] = set()

    for record in train:
        if record.stratum == "clean" and record.label == "pass":
            chosen.append((record, {"errors": [], "verdict": "pass"}))
            used_units.add(record.unit_id)
            break

    # The example's category has to be the one the rubric would assign, or the
    # few-shot teaches the wrong label for the class it demonstrates.
    category_by_class = {
        "obscenity-injected": "register",
        "glossary-substituted": "terminology",
        "realia-decapitalised": "terminology",
        "sentence-dropped": "omission",
        "clause-dropped": "omission",
        "quote-frame-dropped": "omission",
    }
    for severity, verdict in (("major", "flag"), ("critical", "reject")):
        for record in train:
            if record.stratum != "mutation" or record.severity != severity:
                continue
            # Three examples cut from one unit would teach the shape of this
            # batch rather than the shape of the task.
            if record.unit_id in used_units:
                continue
            span = changed_span(record.base_target, record.target)
            # A deletion leaves no span worth pointing at, and "!" as the
            # evidence for a dropped sentence is a worse lesson than none.
            if len(span) < 4 or not any(char.isalpha() for char in span):
                continue
            if span not in record.target:
                continue
            chosen.append(
                (
                    record,
                    {
                        "errors": [
                            {
                                "span": span,
                                "category": category_by_class.get(
                                    record.defect_class or "", "mistranslation"
                                ),
                                "severity": severity,
                            }
                        ],
                        "verdict": verdict,
                    },
                )
            )
            used_units.add(record.unit_id)
            break

    if not chosen:
        return [], set()

    answers = [
        {"id": index, **answer} for index, (_record, answer) in enumerate(chosen)
    ]
    segments = [
        render_segment(index, record) for index, (record, _answer) in enumerate(chosen)
    ]
    messages = [
        {
            "role": "user",
            "content": json.dumps({"segments": segments}, ensure_ascii=False),
        },
        {
            "role": "assistant",
            "content": json.dumps({"segments": answers}, ensure_ascii=False),
        },
    ]
    return messages, {record.record_id for record, _answer in chosen}


def build_payload(model: str, batch: list[Record], shots: list[dict]) -> dict[str, Any]:
    segments = list(starmap(render_segment, enumerate(batch)))
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *shots,
            {
                "role": "user",
                "content": json.dumps({"segments": segments}, ensure_ascii=False),
            },
        ],
        "response_format": {"type": "json_schema", "json_schema": reply_schema()},
        # Fail loudly rather than let a provider silently drop the schema.
        "provider": {"require_parameters": True},
        # No temperature. Measured 2026-08-13: with require_parameters on, the
        # reasoning tiers (claude-sonnet-5, gpt-5.4-mini, gpt-5.4-nano) return
        # 404 "no endpoints found that can handle the requested parameters",
        # because they do not declare temperature support. Sending it would
        # cost either the schema guarantee or three of the seven candidates,
        # and those models ignore temperature anyway. The price is that runs
        # are not bit-reproducible across providers.
        "usage": {"include": True},
    }


PRICES: dict[str, tuple[float, float]] = {}


def load_prices() -> None:
    """Per-token catalogue prices, for the models that report no cost."""
    try:
        with urllib.request.urlopen(
            "https://openrouter.ai/api/v1/models", timeout=60
        ) as response:
            catalogue = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return
    for model in catalogue.get("data", []):
        pricing = model.get("pricing") or {}
        try:
            PRICES[model["id"]] = (
                float(pricing.get("prompt", 0)),
                float(pricing.get("completion", 0)),
            )
        except (TypeError, ValueError):
            continue


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
            "X-Title": "HCGameLoc judge calibration",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def parse_reply(payload: dict[str, Any], size: int) -> list[Verdict] | None:
    """
    One verdict per record, or nothing at all.

    ``None`` means transport failure, never a verdict. A reply that is
    unparsable, short, long, duplicated or missing an id is a failure of the
    transport; Cathedral's 19.6% loss rate is what happens when a pipeline lets
    that distinction blur into flag/reject.
    """
    choices = payload.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    segments = parsed.get("segments")
    if not isinstance(segments, list) or len(segments) != size:
        return None

    verdicts: list[Verdict | None] = [None] * size
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        index = segment.get("id")
        if not isinstance(index, int) or not 0 <= index < size:
            return None
        if verdicts[index] is not None:
            return None
        errors = segment.get("errors")
        if not isinstance(errors, list):
            return None
        worst = max(
            (
                SEVERITY_RANK.get(error.get("severity"), 0)
                for error in errors
                if isinstance(error, dict)
            ),
            default=0,
        )
        verdicts[index] = Verdict(
            verdict=VERDICT_BY_SEVERITY[worst],
            errors=[error for error in errors if isinstance(error, dict)],
            model_verdict=str(segment.get("verdict", "")),
        )
    if any(verdict is None for verdict in verdicts):
        return None
    return verdicts  # type: ignore[return-value]


def judge_batch(
    model: str, batch: list[Record], shots: list[dict], api_key: str, timeout: int
) -> tuple[list[Verdict], Usage]:
    usage = Usage(records=len(batch))
    payload = build_payload(model, batch, shots)
    attempts = 0
    parse_attempts = 0
    # One retry on a parse failure, per the transport invariant. Rate limits and
    # provider hiccups get a few more: they are a different animal.
    while attempts < 6 and parse_attempts < 2:
        attempts += 1
        try:
            reply = post(payload, api_key, timeout)
        except urllib.error.HTTPError as error:
            usage.transport_errors[f"http_{error.code}"] += 1
            detail = error.read()[:300].decode(errors="replace")
            if len(usage.details) < 5:
                usage.details.append(f"http_{error.code}: {detail}")
            if error.code in {408, 409, 429, 500, 502, 503, 504}:
                usage.retries += 1
                time.sleep(min(30, 2**attempts))
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            usage.transport_errors[type(error).__name__] += 1
            usage.retries += 1
            time.sleep(min(30, 2**attempts))
            continue

        usage.requests += 1
        reply_usage = reply.get("usage") or {}
        prompt_tokens = reply_usage.get("prompt_tokens") or 0
        completion_tokens = reply_usage.get("completion_tokens") or 0
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        # OpenRouter does not report a cost for every model - the gpt-5.4 tiers
        # come back with zero - so fall back to the catalogue price. A cost
        # column that is silently zero for some arms is worse than no column.
        reported = reply_usage.get("cost") or 0.0
        usage.cost_usd += reported or priced(
            payload["model"], prompt_tokens, completion_tokens
        )
        if error_body := reply.get("error"):
            usage.transport_errors[f"api_{error_body.get('code', 'error')}"] += 1
            if len(usage.details) < 5:
                usage.details.append(str(error_body)[:300])
            break

        verdicts = parse_reply(reply, len(batch))
        if verdicts is not None:
            return verdicts, usage
        parse_attempts += 1
        usage.transport_errors["unparsed_reply"] += 1
        usage.retries += 1

    usage.unparsed = len(batch)
    return [UNPARSED] * len(batch), usage


def judge(
    model: str,
    records: list[Record],
    shots: list[dict],
    api_key: str,
    batch_size: int,
    concurrency: int,
    timeout: int,
) -> tuple[dict[str, Verdict], Usage]:
    batches = [
        records[start : start + batch_size]
        for start in range(0, len(records), batch_size)
    ]
    total = Usage()
    results: dict[str, Verdict] = {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(judge_batch, model, batch, shots, api_key, timeout)
            for batch in batches
        ]
        for batch, future in zip(batches, futures, strict=True):
            verdicts, usage = future.result()
            total.merge(usage)
            for record, verdict in zip(batch, verdicts, strict=True):
                results[record.record_id] = verdict
    total.seconds = time.monotonic() - started
    return results, total


def wilson(successes: int, total: int, z: float = 1.96) -> dict[str, Any]:
    if not total:
        return {"point": None, "low": None, "high": None, "n": 0, "k": 0}
    phat = successes / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    spread = z * sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return {
        "point": round(100 * phat, 1),
        "low": round(100 * (centre - spread) / denominator, 1),
        "high": round(100 * (centre + spread) / denominator, 1),
        "n": total,
        "k": successes,
    }


def scored_pairs(
    records: list[Record], verdicts: dict[str, Verdict]
) -> list[tuple[Record, Verdict]]:
    pairs = []
    for record in records:
        verdict = verdicts.get(record.record_id)
        if verdict is not None and not verdict.unparsed:
            pairs.append((record, verdict))
    return pairs


def rate(
    records: list[Record], verdicts: dict[str, Verdict], truth: str, predicate: str
) -> dict[str, Any]:
    """Share of records matching ``truth`` whose verdict matches ``predicate``."""
    truth_test = {
        "defect": lambda record: record.truth_needs_human,
        "critical": lambda record: record.truth_critical,
        "clean": lambda record: not record.truth_needs_human,
    }[truth]
    predicate_test = {
        "needs_human": lambda verdict: verdict.needs_human,
        "reject": lambda verdict: verdict.is_reject,
        "flag": lambda verdict: verdict.verdict == "flag",
        "pass": lambda verdict: verdict.verdict == "pass",
    }[predicate]
    subset = [
        (record, verdict)
        for record, verdict in scored_pairs(records, verdicts)
        if truth_test(record)
    ]
    return wilson(
        sum(1 for _, verdict in subset if predicate_test(verdict)), len(subset)
    )


def projected_miss_rate(tpr: float, tnr: float, prevalence: float) -> float | None:
    """
    P(defect | the judge said pass) in a pool with the given defect rate.

    The golden set is enriched with constructed defects, so its own auto-pass
    miss rate does not transfer. This projects the measured sensitivity and
    specificity onto the pool B0 actually measured: 18.2% defects in strings
    that look clean.
    """
    missed = prevalence * (1 - tpr)
    passed_clean = (1 - prevalence) * tnr
    total = missed + passed_clean
    return missed / total if total else None


def bootstrap_projection(
    records: list[Record],
    verdicts: dict[str, Verdict],
    prevalence: float,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    sample = [
        (record.truth_needs_human, verdict.needs_human)
        for record, verdict in scored_pairs(records, verdicts)
    ]
    if not sample:
        return {"point": None, "low": None, "high": None}

    def compute(rows: list[tuple[bool, bool]]) -> float | None:
        defects = [flagged for truth, flagged in rows if truth]
        cleans = [flagged for truth, flagged in rows if not truth]
        if not defects or not cleans:
            return None
        tpr = sum(defects) / len(defects)
        tnr = 1 - sum(cleans) / len(cleans)
        return projected_miss_rate(tpr, tnr, prevalence)

    point = compute(sample)
    if point is None:
        return {"point": None, "low": None, "high": None}
    # ruff: ignore[suspicious-non-cryptographic-random-usage]
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        drawn = [rng.choice(sample) for _ in sample]
        value = compute(drawn)
        if value is not None:
            values.append(value)
    values.sort()
    if not values:
        return {"point": round(100 * point, 2), "low": None, "high": None}
    return {
        "point": round(100 * point, 2),
        "low": round(100 * values[int(0.025 * len(values))], 2),
        "high": round(100 * values[min(len(values) - 1, int(0.975 * len(values)))], 2),
        "assumed_prevalence_pct": round(100 * prevalence, 1),
        "draws": len(values),
    }


def confusion(records: list[Record], verdicts: dict[str, Verdict]) -> dict[str, int]:
    matrix: Counter[str] = Counter()
    for record in records:
        verdict = verdicts.get(record.record_id)
        if verdict is None:
            continue
        truth = (
            "critical"
            if record.truth_critical
            else ("major" if record.truth_needs_human else "clean")
        )
        matrix[f"{truth}->{'unparsed' if verdict.unparsed else verdict.verdict}"] += 1
    return dict(sorted(matrix.items()))


def metrics(
    records: list[Record],
    final: dict[str, Verdict],
    tier_a: dict[str, Verdict] | None,
    tier_b: dict[str, Verdict] | None,
    escalated: list[Record],
    prevalence: float,
    bootstrap_draws: int,
    seed: int,
) -> dict[str, Any]:
    pairs = scored_pairs(records, final)
    clean_pairs = [
        (record, verdict) for record, verdict in pairs if not record.truth_needs_human
    ]
    auto_pass = [record for record, verdict in pairs if not verdict.needs_human]
    missed = [record for record in auto_pass if record.truth_needs_human]
    missed_critical = [record for record in missed if record.truth_critical]
    disagreements = sum(
        1
        for _record, verdict in pairs
        if verdict.model_verdict and verdict.model_verdict != verdict.verdict
    )

    result: dict[str, Any] = {
        "records": len(records),
        "scored": len(pairs),
        "unparsed": sum(
            1 for record in records if final.get(record.record_id, UNPARSED).unparsed
        ),
        "verdict_derived_from_errors_disagreed_with_model_verdict": disagreements,
        "confusion_matrix": confusion(records, final),
        "boundary_h_needs_human": {
            "_what": "flag or reject against pass. The confidence router, and the product boundary.",
            "tpr": rate(records, final, "defect", "needs_human"),
            "tnr": rate(records, final, "clean", "pass"),
        },
        "boundary_r_has_critical": {
            "_what": "reject against flag or pass. The release gate.",
            "tpr": rate(records, final, "critical", "reject"),
            "tnr_on_clean": rate(records, final, "clean", "pass"),
        },
        "false_critical_on_clean": {
            "_what": "A clean string rejected by the judge never reaches the game, so this is a delivery defect, not noise.",
            **wilson(
                sum(1 for _record, verdict in clean_pairs if verdict.is_reject),
                len(clean_pairs),
            ),
        },
        "false_flag_on_clean": wilson(
            sum(1 for _record, verdict in clean_pairs if verdict.verdict == "flag"),
            len(clean_pairs),
        ),
        "reject_recall_on_critical_mutations": rate(
            [record for record in records if record.stratum == "mutation"],
            final,
            "critical",
            "reject",
        ),
        "needs_human_recall_on_terminology": rate(
            [record for record in records if record.stratum == "terminology"],
            final,
            "defect",
            "needs_human",
        ),
        "auto_pass": {
            "rate": wilson(len(auto_pass), len(pairs)),
            "observed_miss_rate": wilson(len(missed), len(auto_pass)),
            "observed_critical_miss_rate": wilson(len(missed_critical), len(auto_pass)),
            "_observed_caveat": "The golden set is enriched with constructed defects, so the observed rates describe the set, not production. The projection below is the transferable number.",
            "projected_miss_rate_at_production_prevalence": bootstrap_projection(
                records, final, prevalence, bootstrap_draws, seed
            ),
        },
        "by_stratum": {},
        "by_annotator": {},
    }

    for stratum in sorted({record.stratum for record in records}):
        subset = [record for record in records if record.stratum == stratum]
        result["by_stratum"][stratum] = {
            "n": len(subset),
            "needs_human_recall": (
                rate(subset, final, "defect", "needs_human")
                if any(record.truth_needs_human for record in subset)
                else None
            ),
            "pass_rate_on_clean": (
                rate(subset, final, "clean", "pass")
                if any(not record.truth_needs_human for record in subset)
                else None
            ),
        }

    # Family-bias control: a judge that only wins on the strings its own family
    # annotated is not a better judge, it is a mirror.
    for annotator in sorted(
        {record.annotator for record in records if record.annotator}
    ):
        subset = [record for record in records if record.annotator == annotator]
        if len(subset) < 20:
            continue
        result["by_annotator"][annotator] = {
            "n": len(subset),
            "tpr_h": rate(subset, final, "defect", "needs_human"),
            "tnr_h": rate(subset, final, "clean", "pass"),
        }

    if tier_a is not None:
        result["tier_a"] = {
            "_what": "Tier A is the completeness filter. What it passes is never looked at again, so recall at boundary H decides whether the cascade holds together at all.",
            "recall_at_h": rate(records, tier_a, "defect", "needs_human"),
            "specificity_at_h": rate(records, tier_a, "clean", "pass"),
            "escalated": len(escalated),
            "escalation_rate_pct": (
                round(100 * len(escalated) / len(records), 1) if records else None
            ),
        }
    if tier_b is not None:
        upheld = [
            record
            for record in escalated
            if record.record_id in tier_b
            and not tier_b[record.record_id].unparsed
            and tier_b[record.record_id].needs_human
        ]
        overturned = [
            record
            for record in escalated
            if record.record_id in tier_b
            and not tier_b[record.record_id].unparsed
            and not tier_b[record.record_id].needs_human
        ]
        result["tier_b"] = {
            "_what": "Tier B is precision over the subset tier A escalated.",
            "precision_on_escalated": wilson(
                sum(1 for record in upheld if record.truth_needs_human), len(upheld)
            ),
            "upheld": len(upheld),
            "overturned": len(overturned),
            "overturned_correctly": sum(
                1 for record in overturned if not record.truth_needs_human
            ),
            "overturned_wrongly": sum(
                1 for record in overturned if record.truth_needs_human
            ),
        }
    return result


def dump_verdicts(
    records: list[Record], verdicts: dict[str, Verdict]
) -> list[dict[str, Any]]:
    """
    Emit one row per record so a later question needs no second run.

    The first version of this script stored aggregates only, which made the
    agreement between two judges unanswerable without paying twice.
    """
    rows = []
    for record in records:
        verdict = verdicts.get(record.record_id)
        if verdict is None:
            continue
        rows.append(
            {
                "record_id": record.record_id,
                "stratum": record.stratum,
                "label": record.label,
                "severity": record.severity,
                "predicted": "unparsed" if verdict.unparsed else verdict.verdict,
                "error_count": len(verdict.errors),
            }
        )
    return rows


def mcnemar(b: int, c: int) -> dict[str, Any]:
    """
    Exact two-sided McNemar over the discordant pairs.

    Only the records where the two judges disagree carry information about
    which one is better; the rest cancel.
    """
    total = b + c
    if not total:
        return {"b": b, "c": c, "discordant": 0, "p_value": None}
    tail = sum(comb(total, i) for i in range(min(b, c) + 1))
    p_value = min(1.0, 2 * tail / (2**total))
    return {"b": b, "c": c, "discordant": total, "p_value": round(p_value, 5)}


def pairwise_analysis(
    records: list[Record],
    by_model: dict[str, dict[str, Verdict]],
    prevalence: float,
    bootstrap_draws: int,
    seed: int,
) -> dict[str, Any]:
    """Union of two judges, plus a test of whether they really differ."""
    out: dict[str, Any] = {}
    names = list(by_model)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            left, right = by_model[first], by_model[second]
            union: dict[str, Verdict] = {}
            agree_flag = only_left = only_right = 0
            b = c = 0
            for record in records:
                one, two = left.get(record.record_id), right.get(record.record_id)
                if one is None or two is None or one.unparsed or two.unparsed:
                    continue
                # Union verdict: the stricter of the two.
                worst = max((one.verdict, two.verdict), key=VERDICT_ORDER.__getitem__)
                union[record.record_id] = Verdict(worst, one.errors + two.errors, "")
                if one.needs_human and two.needs_human:
                    agree_flag += 1
                elif one.needs_human:
                    only_left += 1
                elif two.needs_human:
                    only_right += 1
                left_right = one.needs_human == record.truth_needs_human
                right_right = two.needs_human == record.truth_needs_human
                if left_right and not right_right:
                    b += 1
                elif right_right and not left_right:
                    c += 1
            summary = metrics(
                records, union, None, None, [], prevalence, bootstrap_draws, seed
            )
            out[f"{first} + {second}"] = {
                "_what": "Both judges on every record, verdict is the stricter of the two. Recall cannot fall below either judge; specificity cannot rise above either.",
                "union": {
                    "tpr": summary["boundary_h_needs_human"]["tpr"],
                    "tnr": summary["boundary_h_needs_human"]["tnr"],
                    "reject_tpr": summary["boundary_r_has_critical"]["tpr"],
                    "false_critical_on_clean": summary["false_critical_on_clean"],
                    "auto_pass": summary["auto_pass"]["rate"],
                    "projected_miss": summary["auto_pass"][
                        "projected_miss_rate_at_production_prevalence"
                    ],
                },
                "flag_overlap": {
                    "both": agree_flag,
                    f"only {first}": only_left,
                    f"only {second}": only_right,
                },
                "mcnemar": {
                    "_what": f"b = records {first} got right and {second} got wrong at boundary H; c is the reverse.",
                    **mcnemar(b, c),
                },
            }
    return out


def run_probe(
    models: list[str], records: list[Record], api_key: str, timeout: int
) -> dict[str, Any]:
    probe = records[:2]
    out: dict[str, Any] = {}
    for model in models:
        started = time.monotonic()
        verdicts, usage = judge_batch(model, probe, [], api_key, timeout)
        accepted = not any(verdict.unparsed for verdict in verdicts)
        out[model] = {
            **usage.as_dict(),
            "accepted": accepted,
            "seconds": round(time.monotonic() - started, 1),
            "verdicts": [verdict.verdict for verdict in verdicts],
        }
        print(
            f"PROBE {'OK  ' if accepted else 'FAIL'} {model:34s} "
            f"cost=${usage.cost_usd:.5f} errors={dict(usage.transport_errors)}"
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="B2' judge cascade calibration")
    parser.add_argument(
        "--arm", choices=("cascade", "mirror", "single"), default="cascade"
    )
    parser.add_argument("--split", default="train,dev")
    parser.add_argument("--models", default="")
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--prevalence",
        type=float,
        default=0.182,
        help="true defect rate of the production pool, measured by B0",
    )
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260813)
    args = parser.parse_args()

    splits = {part.strip() for part in args.split.split(",") if part.strip()}
    if "test" in splits and splits != {"test"}:
        parser.error("the test split is sealed: run it alone or not at all")
    models = [model.strip() for model in args.models.split(",") if model.strip()]

    records = load_records(splits)
    shots, shot_ids = few_shot_messages(load_records({"train"}))
    records = [record for record in records if record.record_id not in shot_ids]
    if args.limit:
        records = records[: args.limit]
    if not records:
        parser.error("no records for the requested split")

    header = {
        "_arm": "probe" if args.probe else args.arm,
        "_splits": sorted(splits),
        "_records": len(records),
        "_models": models,
        "_batch": args.batch,
        "_concurrency": args.concurrency,
        "_few_shot_held_out": sorted(shot_ids),
        "_strata": dict(Counter(record.stratum for record in records)),
        "_labels": dict(Counter(record.label for record in records)),
        "_captured": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
    }

    if args.dry_run:
        payload = build_payload(
            models[0] if models else "MODEL", records[: args.batch], shots
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(
            "JUDGE_JSON",
            json.dumps({**header, "dry_run": True}, ensure_ascii=False, sort_keys=True),
        )
        return

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is not set")
    if not models:
        parser.error("--models is required for a live run")
    load_prices()

    if args.probe:
        result = {**header, "probe": run_probe(models, records, api_key, args.timeout)}
    elif args.arm == "single":
        arms = {}
        by_model: dict[str, dict[str, Verdict]] = {}
        for model in models:
            verdicts, usage = judge(
                model,
                records,
                shots,
                api_key,
                args.batch,
                args.concurrency,
                args.timeout,
            )
            summary = metrics(
                records,
                verdicts,
                None,
                None,
                [],
                args.prevalence,
                args.bootstrap,
                args.seed,
            )
            arms[model] = {
                "usage": usage.as_dict(),
                "metrics": summary,
                "verdicts": dump_verdicts(records, verdicts),
            }
            by_model[model] = verdicts
            print(
                f"SINGLE {model:34s} "
                f"H-tpr={summary['boundary_h_needs_human']['tpr']['point']} "
                f"H-tnr={summary['boundary_h_needs_human']['tnr']['point']} "
                f"R-tpr={summary['boundary_r_has_critical']['tpr']['point']} "
                f"false-crit={summary['false_critical_on_clean']['point']} "
                f"unparsed={summary['unparsed']} cost=${usage.cost_usd:.3f}"
            )
        result = {**header, "arms": arms}
        if len(models) > 1:
            result["pairwise"] = pairwise_analysis(
                records, by_model, args.prevalence, args.bootstrap, args.seed
            )
            for key, value in result["pairwise"].items():
                union = value["union"]
                print(
                    f"PAIR {key} union-H-tpr={union['tpr']['point']} "
                    f"union-H-tnr={union['tnr']['point']} "
                    f"mcnemar b={value['mcnemar']['b']} c={value['mcnemar']['c']} "
                    f"p={value['mcnemar']['p_value']}"
                )
    else:
        if len(models) != 2:
            parser.error("cascade and mirror need exactly two models")
        model_a, model_b = models if args.arm == "cascade" else models[::-1]
        verdicts_a, usage_a = judge(
            model_a, records, shots, api_key, args.batch, args.concurrency, args.timeout
        )
        escalated = [
            record
            for record in records
            if not verdicts_a[record.record_id].unparsed
            and verdicts_a[record.record_id].needs_human
        ]
        verdicts_b: dict[str, Verdict] = {}
        usage_b = Usage()
        if escalated:
            verdicts_b, usage_b = judge(
                model_b,
                escalated,
                shots,
                api_key,
                args.batch,
                args.concurrency,
                args.timeout,
            )
        # Tier B only overrides where it actually ran and actually answered.
        final = {}
        for record in records:
            fallback = verdicts_a[record.record_id]
            candidate = verdicts_b.get(record.record_id)
            final[record.record_id] = (
                candidate
                if candidate is not None and not candidate.unparsed
                else fallback
            )
        summary = metrics(
            records,
            final,
            verdicts_a,
            verdicts_b,
            escalated,
            args.prevalence,
            args.bootstrap,
            args.seed,
        )
        result = {
            **header,
            "tier_a_model": model_a,
            "tier_b_model": model_b,
            "usage": {
                "tier_a": usage_a.as_dict(),
                "tier_b": usage_b.as_dict(),
                "total_cost_usd": round(usage_a.cost_usd + usage_b.cost_usd, 6),
                "cost_per_1000_records": round(
                    1000 * (usage_a.cost_usd + usage_b.cost_usd) / len(records), 4
                ),
            },
            "metrics": summary,
        }
        print(
            f"{args.arm.upper()} A={model_a} B={model_b} "
            f"A-recall-H={summary['tier_a']['recall_at_h']['point']} "
            f"escalated={summary['tier_a']['escalation_rate_pct']}% "
            f"H-tpr={summary['boundary_h_needs_human']['tpr']['point']} "
            f"H-tnr={summary['boundary_h_needs_human']['tnr']['point']} "
            f"false-crit={summary['false_critical_on_clean']['point']} "
            f"cost=${result['usage']['total_cost_usd']:.3f}"
        )

    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print("JUDGE_JSON", json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
