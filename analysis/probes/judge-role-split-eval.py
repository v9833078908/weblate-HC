# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Compare the current two-seat judge against a role-split hypothesis (task 9).

Two arms over the same ``ru->vi`` records, the same models and the same
request budget:

``current``
    Two seats, both running the product prompt
    (``weblate/trans/judge_prompts/verdict.txt``), both seeing the
    glossary. This is what ships today.

``split``
    Seat ``meaning`` judges source, target and game context with the
    glossary **removed**, and with the glossary's target term scrubbed
    from every field it could leak through. Seat ``terminology`` gets the
    glossary and is told explicitly that the segment's own context may
    contradict an entry and that it must say which one wins. A separate
    repair role then consumes the union of the issues, and its candidate
    is re-verified by both seats.

Both arms repair and re-verify, so a difference in the numbers is a
difference in the division of labour, not in how many chances an arm got.

What this probe refuses to output, by construction:

* Overall precision, recall, accuracy or "percent correct Vietnamese".
  There are no independent human labels in this set, so those numbers
  would be arithmetic over an invented truth.
* A verdict that a control string was damaged. An untouched control that
  comes back rewritten is counted as an *intervention*; whether the new
  text is better is not knowable here.
* A claim that ``pháo đài`` is correct. It is the corpus-majority
  rendering of ``Форт``, nothing more.

Standalone: no Django, no Weblate imports, stdlib only. The key comes from
the environment. Nothing is written to any Weblate instance.

Usage::

    # No network at all. Prints the exact payloads, checks every offline
    # invariant including the leakage guard, and prices the run.
    python analysis/probes/judge-role-split-eval.py --dry-run --split dev

    export OPENROUTER_API_KEY=sk-or-...

    # One arm over the tuning half, with a hard request ceiling.
    python analysis/probes/judge-role-split-eval.py --arm current \
        --split dev --models vendor-a/model,vendor-b/model \
        --max-requests 200 --out current.json

    python analysis/probes/judge-role-split-eval.py --arm split \
        --split dev --models vendor-a/model,vendor-b/model \
        --max-requests 200 --out split.json

The last output line is ``ROLE_SPLIT_JSON {...}``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = REPO_ROOT / "analysis" / "data" / "judge-role-split-ru-vi.json"
PRODUCT_PROMPT = REPO_ROOT / "weblate" / "trans" / "judge_prompts" / "verdict.txt"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

SOURCE_LANGUAGE = "Russian"
TARGET_LANGUAGE = "Vietnamese"
PROJECT_CONTEXT = (
    "The project is a mobile pirate strategy game. The player upgrades a "
    "home base tier by tier, sails a fleet, and raids enemy positions."
)

SEVERITY_RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}
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

PLACEHOLDER_RE = re.compile(r"\{\d+\}|\{\[PARAM\d+\]\}|%[A-Z_]+%|%[sd]|\{\}")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>")
NEGATIONS = ("không", "chưa", "đừng")

MEANING_PROMPT = """\
You are reviewing one segment of a {source_language} to {target_language} \
video game localization. {project_context}

You judge meaning only. You are given the source, the {target_language} \
target under review, and the game context. You are deliberately NOT given \
the project glossary: terminology conformance is another reviewer's \
responsibility, and you must not speculate about what an approved term \
might be.

Report only where the target tells the player something the source does \
not: an inverted, dropped or added negation; a wrong number or referent; a \
value that lands in a slot where it reads as the wrong role; a clause the \
source states and the target omits; an untranslated fragment. Judge the \
sentence a player reads.

Do not report a term merely because you would have chosen another word. \
If your only objection is word choice for a game entity, that belongs to \
the terminology reviewer, not to you: say nothing.

Rules 1, 3, 4, 5, 6 and 7 of the house style still apply: copy every span \
verbatim from the target, ignore length differences, accept a different \
but faithful wording, never judge the source itself, and never claim a \
conflict with a string you were not shown.

Severity: `critical` if the player is misled about what happens, `major` \
if the meaning is distorted but recoverable, `minor` for style only.

Categories: mistranslation, omission, addition, fluency, register.

Give a verdict per segment: `reject` if any error is critical, `flag` if \
the worst is major, otherwise `pass`.

Every error carries a `description` written in English for a producer who \
does not read {target_language}: state the back-translation of the span \
and what is wrong with it. Every segment carries a `back_translation` of \
the whole target into {source_language}.

Answer with exactly one JSON object whose only top-level key is \
`segments`, one object per segment keyed by its `id`, each with exactly \
`id`, `verdict`, `errors` and `back_translation`."""

TERMINOLOGY_PROMPT = """\
You are the terminology reviewer for a {source_language} to \
{target_language} video game localization. {project_context}

You are given the source, the {target_language} target under review, and \
`glossary`: source-matched project terms with their recorded \
{target_language} rendering. A glossary entry is recorded project data. It \
is not proof that a human approved it, and it can be wrong.

Your job is the term, not the sentence. Decide, for each applicable \
entry, whether the target renders the concept the segment actually names.

Two failures matter equally, and you must distinguish them:

1. The target ignores an entry that does apply to this segment.
2. The entry does not name the concept in this segment, and following it \
would change what the string means. The segment's own source, note and \
explanation outrank the entry. When this happens, report the conflict \
against the glossary, category `terminology`, and say in the description \
that the ENTRY is what does not fit, not the target.

An entry for a building, mode, screen or status constrains words that \
name that concept, not every grammatical form built from the same root, \
and not a different real-world object that happens to share a word.

Copy every span verbatim from the target. Report nothing about grammar, \
style, punctuation or length: another reviewer owns those.

Severity: `major` for a term rendered against an applicable entry, \
`critical` only where the wrong term makes the line tell the player \
something false, `minor` never.

Categories: terminology only.

Give a verdict per segment: `reject` if any error is critical, `flag` if \
the worst is major, otherwise `pass`.

Every error carries a `description` in English for a producer who does not \
read {target_language}. Every segment carries a `back_translation` of the \
whole target into {source_language}.

Answer with exactly one JSON object whose only top-level key is \
`segments`, one object per segment keyed by its `id`, each with exactly \
`id`, `verdict`, `errors` and `back_translation`."""

REPAIR_PROMPT = """\
You repair one {target_language} game string, given the {source_language} \
source and the issues two reviewers found in the current target. \
{project_context}

Fix exactly what the issues name. Change nothing else: keep the register, \
keep the wording that was not objected to, and keep every engine token \
(`{{0}}`, `{{[PARAM0]}}`, `%KEY%`) and rich-text tag present in the source, \
unchanged in spelling and count.

If the issues do not justify a change, or you cannot fix them without \
guessing at meaning you were not given, return the current target \
unchanged and say so in `reasoning`.

Your `reasoning` is read by a producer who does not read \
{target_language}. Do not put a {target_language} term in it as a \
recommendation for other strings.

Answer with exactly one JSON object with keys `target` and `reasoning`."""


def verdict_schema(size: int) -> dict[str, Any]:
    error = {
        "type": "object",
        "properties": {
            "span": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
            "description": {"type": "string"},
        },
        "required": ["span", "category", "severity", "description"],
        "additionalProperties": False,
    }
    segment = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["pass", "flag", "reject"]},
            "errors": {"type": "array", "items": error},
            "back_translation": {"type": "string"},
        },
        "required": ["id", "verdict", "errors", "back_translation"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "minItems": size,
                "maxItems": size,
                "items": segment,
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }


REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"target": {"type": "string"}, "reasoning": {"type": "string"}},
    "required": ["target", "reasoning"],
    "additionalProperties": False,
}


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    transport_failures: int = 0
    unparsed: int = 0

    def add(self, other: Usage) -> None:
        self.requests += other.requests
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost += other.cost
        self.transport_failures += other.transport_failures
        self.unparsed += other.unparsed

    def asdict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "observed_cost_usd": round(self.cost, 6),
            "transport_failures": self.transport_failures,
            "unparsed": self.unparsed,
        }


@dataclass
class Opinion:
    """One seat's answer about one record."""

    seat: str
    verdict: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    back_translation: str = ""
    unparsed: bool = False

    @property
    def max_severity(self) -> str:
        worst = max(
            (SEVERITY_RANK.get(e.get("severity", ""), 0) for e in self.errors),
            default=0,
        )
        return ("none", "minor", "major", "critical")[worst]

    @property
    def flagged(self) -> bool:
        return self.verdict in {"flag", "reject"} or bool(self.errors)

    def categories(self) -> set[str]:
        return {str(e.get("category", "")) for e in self.errors}


UNPARSED = Opinion("", "pass", [], "", unparsed=True)


@dataclass
class RecordOutcome:
    record_id: str
    family: str
    opinions: list[Opinion] = field(default_factory=list)
    candidate: str | None = None
    repair_reasoning: str = ""
    recheck: list[Opinion] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return any(opinion.flagged for opinion in self.opinions)

    @property
    def conflict(self) -> bool:
        """The seats disagree about whether a human is needed."""
        answered = [o for o in self.opinions if not o.unparsed]
        if len(answered) < 2:
            return False
        return len({o.flagged for o in answered}) > 1

    @property
    def unresolved(self) -> bool:
        """No usable opinion at all: every seat failed to answer."""
        return bool(self.opinions) and all(o.unparsed for o in self.opinions)


def mechanics(text: str) -> dict[str, Any]:
    return {
        "numbers": sorted(NUMBER_RE.findall(text)),
        "placeholders": sorted(PLACEHOLDER_RE.findall(text)),
        "placeholder_order": PLACEHOLDER_RE.findall(text),
        "markup": sorted(MARKUP_RE.findall(text)),
    }


def load_product_prompt() -> str:
    template = PRODUCT_PROMPT.read_text(encoding="utf-8")
    return (
        template.replace("{source_language}", SOURCE_LANGUAGE)
        .replace("{target_language}", TARGET_LANGUAGE)
        .replace("{project_context}", PROJECT_CONTEXT)
    )


def fill(template: str) -> str:
    return (
        template.replace("{source_language}", SOURCE_LANGUAGE)
        .replace("{target_language}", TARGET_LANGUAGE)
        .replace("{project_context}", PROJECT_CONTEXT)
    )


def segment_for(record: dict[str, Any], *, glossary: bool) -> dict[str, Any]:
    """Shape one record into the product's own segment payload."""
    segment: dict[str, Any] = {
        "id": 0,
        "key": record["unit_key"],
        "source": record["source"],
        "target": record["target"],
    }
    if record.get("note"):
        segment["note"] = record["note"]
    if record.get("explanation"):
        segment["explanation"] = record["explanation"]
    if record.get("checks"):
        segment["checks"] = list(record["checks"])
    if glossary and record.get("glossary_terms"):
        segment["glossary"] = [dict(entry) for entry in record["glossary_terms"]]
    return segment


def leak_terms(record: dict[str, Any]) -> list[str]:
    """Target-side glossary strings the meaning seat must never receive."""
    terms = []
    for entry in record.get("glossary_terms") or []:
        target = str(entry.get("target", "")).strip()
        if target:
            terms.append(target)
        explanation = str(entry.get("target_explanation", "")).strip()
        if explanation:
            terms.append(explanation)
    return terms


def leakage_problems(payload: dict[str, Any], record: dict[str, Any]) -> list[str]:
    """
    Report any target-side glossary term reachable in a meaning payload.

    The segment's own ``target`` legitimately contains the disputed text;
    what must not leak is the glossary's recorded rendering arriving
    through any other field, because then the meaning seat is no longer
    an independent reading.
    """
    scrubbed = json.dumps(payload, ensure_ascii=False)
    target = record["target"]
    problems = []
    for term in leak_terms(record):
        occurrences = scrubbed.count(term)
        inside_target = target.count(term)
        if occurrences > inside_target:
            problems.append(
                f"{record['record_id']}: glossary target {term!r} reaches the "
                f"meaning seat {occurrences - inside_target} time(s) outside "
                "the segment's own target"
            )
    return problems


def build_verdict_payload(
    model: str, system: str, segment: dict[str, Any]
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps({"segments": [segment]}, ensure_ascii=False),
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "verdicts",
                "strict": True,
                "schema": verdict_schema(1),
            },
        },
        "provider": {"require_parameters": True},
    }


def build_repair_payload(
    model: str, record: dict[str, Any], issues: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": fill(REPAIR_PROMPT)},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "key": record["unit_key"],
                        "source": record["source"],
                        "target": record["target"],
                        "issues": issues,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "repair",
                "strict": True,
                "schema": REPAIR_SCHEMA,
            },
        },
        "provider": {"require_parameters": True},
    }


class Budget:
    """A hard ceiling. Exceeding it aborts instead of spending more."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0

    def take(self) -> None:
        if self.spent >= self.limit:
            msg = f"request ceiling of {self.limit} reached; run aborted"
            raise RuntimeError(msg)
        self.spent += 1


#: Set by ``--stub``: a deterministic local provider, so the parse,
#: budget, repair, re-check and metric code can be exercised end to end
#: without a network call or a cent of spend. It answers from the payload
#: alone and is not a model: it proves the harness runs, never that a
#: judge works.
STUB_MODE = False


def stub_reply(payload: dict[str, Any]) -> dict[str, Any]:
    """Answer a payload locally: flag anything the checks can see."""
    user = json.loads(payload["messages"][-1]["content"])
    if "issues" in user:
        # The repair role: hand back the target with the engine tokens of
        # the source, which is what a compliant repair would preserve.
        content = json.dumps(
            {"target": user["target"], "reasoning": "stub: unchanged"},
            ensure_ascii=False,
        )
    else:
        segment = user["segments"][0]
        target = str(segment["target"])
        source = str(segment["source"])
        visible = mechanics(target)["placeholders"] != mechanics(source)["placeholders"]
        errors = (
            [
                {
                    "span": target[:20],
                    "category": "omission",
                    "severity": "critical",
                    "description": "stub: engine token count differs",
                }
            ]
            if visible
            else []
        )
        content = json.dumps(
            {
                "segments": [
                    {
                        "id": segment["id"],
                        "verdict": "reject" if errors else "pass",
                        "errors": errors,
                        "back_translation": "stub",
                    }
                ]
            },
            ensure_ascii=False,
        )
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
    }


def post(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    if STUB_MODE:
        return stub_reply(payload)
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    # The URL is the module-level OpenRouter constant, not caller input.
    with urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
        request, timeout=timeout
    ) as response:
        return json.loads(response.read().decode())


def account(reply: dict[str, Any], usage: Usage) -> None:
    stats = reply.get("usage") or {}
    usage.prompt_tokens += int(stats.get("prompt_tokens") or 0)
    usage.completion_tokens += int(stats.get("completion_tokens") or 0)
    usage.cost += float(stats.get("cost") or 0.0)


def content_of(reply: dict[str, Any]) -> str:
    choices = reply.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")


def parse_verdict(text: str, seat: str) -> Opinion:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return Opinion(seat, "pass", [], "", unparsed=True)
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list) or len(segments) != 1:
        return Opinion(seat, "pass", [], "", unparsed=True)
    segment = segments[0]
    if not isinstance(segment, dict):
        return Opinion(seat, "pass", [], "", unparsed=True)
    errors = [error for error in segment.get("errors") or [] if isinstance(error, dict)]
    verdict = str(segment.get("verdict") or "")
    if verdict not in {"pass", "flag", "reject"}:
        return Opinion(seat, "pass", [], "", unparsed=True)
    return Opinion(
        seat,
        verdict,
        errors,
        str(segment.get("back_translation") or ""),
    )


def ask_seat(
    seat: str,
    model: str,
    system: str,
    segment: dict[str, Any],
    api_key: str,
    timeout: int,
    budget: Budget,
    usage: Usage,
) -> Opinion:
    payload = build_verdict_payload(model, system, segment)
    budget.take()
    usage.requests += 1
    try:
        reply = post(payload, api_key, timeout)
    except OSError:
        usage.transport_failures += 1
        return Opinion(seat, "pass", [], "", unparsed=True)
    account(reply, usage)
    opinion = parse_verdict(content_of(reply), seat)
    if opinion.unparsed:
        usage.unparsed += 1
    return opinion


def ask_repair(
    model: str,
    record: dict[str, Any],
    issues: list[dict[str, Any]],
    api_key: str,
    timeout: int,
    budget: Budget,
    usage: Usage,
) -> tuple[str | None, str]:
    payload = build_repair_payload(model, record, issues)
    budget.take()
    usage.requests += 1
    try:
        reply = post(payload, api_key, timeout)
    except OSError:
        usage.transport_failures += 1
        return None, ""
    account(reply, usage)
    try:
        answer = json.loads(content_of(reply))
    except (TypeError, ValueError):
        usage.unparsed += 1
        return None, ""
    if not isinstance(answer, dict) or not isinstance(answer.get("target"), str):
        usage.unparsed += 1
        return None, ""
    return answer["target"], str(answer.get("reasoning") or "")


def seat_plan(arm: str, models: list[str]) -> list[tuple[str, str, str]]:
    """``(seat name, model, system prompt)`` for one arm."""
    if arm == "current":
        product = load_product_prompt()
        return [
            (f"seat{index + 1}", model, product) for index, model in enumerate(models)
        ]
    return [
        ("meaning", models[0], fill(MEANING_PROMPT)),
        ("terminology", models[-1], fill(TERMINOLOGY_PROMPT)),
    ]


def issues_from(opinions: list[Opinion]) -> list[dict[str, Any]]:
    """Collect the union of what the seats found, seat-labelled."""
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for opinion in opinions:
        for error in opinion.errors:
            key = (
                str(error.get("span", "")),
                str(error.get("category", "")),
                str(error.get("severity", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            issues.append({**error, "found_by": opinion.seat})
    return issues


def judge_record(
    record: dict[str, Any],
    arm: str,
    models: list[str],
    api_key: str,
    timeout: int,
    budget: Budget,
    usage: Usage,
    *,
    repair: bool,
) -> RecordOutcome:
    outcome = RecordOutcome(record["record_id"], record["family"])
    for seat, model, system in seat_plan(arm, models):
        with_glossary = arm == "current" or seat == "terminology"
        segment = segment_for(record, glossary=with_glossary)
        if not with_glossary:
            problems = leakage_problems({"segments": [segment]}, record)
            if problems:
                msg = "; ".join(problems)
                raise RuntimeError(msg)
        outcome.opinions.append(
            ask_seat(seat, model, system, segment, api_key, timeout, budget, usage)
        )

    if not repair or not outcome.flagged:
        return outcome

    issues = issues_from(outcome.opinions)
    candidate, reasoning = ask_repair(
        models[0], record, issues, api_key, timeout, budget, usage
    )
    outcome.candidate, outcome.repair_reasoning = candidate, reasoning
    if candidate is None:
        return outcome
    rechecked = dict(record, target=candidate)
    for seat, model, system in seat_plan(arm, models):
        with_glossary = arm == "current" or seat == "terminology"
        segment = segment_for(rechecked, glossary=with_glossary)
        outcome.recheck.append(
            ask_seat(seat, model, system, segment, api_key, timeout, budget, usage)
        )
    return outcome


def wilson(successes: int, total: int, z: float = 1.96) -> dict[str, Any]:
    if not total:
        return {"rate": None, "low": None, "high": None, "n": 0}
    phat = successes / total
    denominator = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    spread = z * ((phat * (1 - phat) + z * z / (4 * total)) / total) ** 0.5
    return {
        "rate": round(phat, 4),
        "low": round((centre - spread) / denominator, 4),
        "high": round((centre + spread) / denominator, 4),
        "n": total,
    }


def repair_mechanics(record: dict[str, Any], outcome: RecordOutcome) -> str:
    """Deterministic verdict on a candidate: ``restored``, ``broken`` or ``unknown``."""
    if outcome.candidate is None:
        return "none"
    base, candidate = record["base_target"], outcome.candidate
    if mechanics(candidate) == mechanics(base):
        return "restored"
    source_tokens = mechanics(record["source"])
    candidate_tokens = mechanics(candidate)
    if (
        source_tokens["placeholders"] != candidate_tokens["placeholders"]
        or source_tokens["markup"] != candidate_tokens["markup"]
    ):
        return "broken"
    return "unknown"


def semantic_repair(record: dict[str, Any], outcome: RecordOutcome) -> str:
    """Report what a candidate did to the one checkable semantic feature."""
    if outcome.candidate is None:
        return "none"
    kind = (record.get("mutation") or {}).get("kind", "")
    candidate = outcome.candidate
    if kind == "negation_drop":
        particle = str((record.get("mutation") or {}).get("from", ""))
        return "restored" if particle and particle in candidate else "not_restored"
    if kind == "placeholder_swap":
        base_order = mechanics(record["base_target"])["placeholder_order"]
        return (
            "restored"
            if mechanics(candidate)["placeholder_order"] == base_order
            else "not_restored"
        )
    if kind == "number_swap":
        return (
            "restored"
            if mechanics(candidate)["numbers"]
            == mechanics(record["base_target"])["numbers"]
            else "not_restored"
        )
    return "unknown"


def measure(
    records: list[dict[str, Any]], outcomes: dict[str, RecordOutcome]
) -> dict[str, Any]:
    by_family: dict[str, dict[str, Any]] = {}
    families = sorted({record["family"] for record in records})
    for family in families:
        rows = [record for record in records if record["family"] == family]
        seen = [
            (row, outcomes[row["record_id"]])
            for row in rows
            if row["record_id"] in outcomes
        ]
        flagged = sum(1 for _row, outcome in seen if outcome.flagged)
        by_family[family] = {
            "flag_rate": wilson(flagged, len(seen)),
            "conflicts": sum(1 for _row, outcome in seen if outcome.conflict),
            "unresolved": sum(1 for _row, outcome in seen if outcome.unresolved),
        }

    injected = [
        (row, outcomes[row["record_id"]])
        for row in records
        if row["expected"]["defect_injected"] and row["record_id"] in outcomes
    ]
    semantic = [
        pair for pair in injected if not pair[0]["expected"]["mechanically_visible"]
    ]
    mechanical = [
        pair for pair in injected if pair[0]["expected"]["mechanically_visible"]
    ]
    controls = [
        (row, outcomes[row["record_id"]])
        for row in records
        if row["family"] == "control_unchanged" and row["record_id"] in outcomes
    ]
    incident = [
        (row, outcomes[row["record_id"]])
        for row in records
        if row["family"] == "glossary_incident" and row["record_id"] in outcomes
    ]
    counter = [
        (row, outcomes[row["record_id"]])
        for row in records
        if row["family"] == "glossary_not_applicable" and row["record_id"] in outcomes
    ]

    return {
        "by_family": by_family,
        "injected_defect_detection": {
            "semantic_only": wilson(
                sum(1 for _r, o in semantic if o.flagged), len(semantic)
            ),
            "mechanically_visible": wilson(
                sum(1 for _r, o in mechanical if o.flagged), len(mechanical)
            ),
        },
        "control_intervention": {
            "flagged": wilson(sum(1 for _r, o in controls if o.flagged), len(controls)),
            "rewritten": wilson(
                sum(
                    1
                    for row, outcome in controls
                    if outcome.candidate is not None
                    and outcome.candidate != row["target"]
                ),
                len(controls),
            ),
        },
        "incident_question_raised": wilson(
            sum(
                1
                for _row, outcome in incident
                if any("terminology" in o.categories() for o in outcome.opinions)
            ),
            len(incident),
        ),
        "counter_case_false_demand": wilson(
            sum(
                1
                for _row, outcome in counter
                if any("terminology" in o.categories() for o in outcome.opinions)
            ),
            len(counter),
        ),
        "repair_mechanics": dict(
            sorted(
                Counter(
                    repair_mechanics(row, outcomes[row["record_id"]])
                    for row in records
                    if row["record_id"] in outcomes
                ).items()
            )
        ),
        "repair_semantic": dict(
            sorted(
                Counter(
                    semantic_repair(row, outcomes[row["record_id"]])
                    for row in records
                    if row["record_id"] in outcomes
                    and row["expected"]["defect_injected"]
                ).items()
            )
        ),
        "refused_metrics": [
            (
                "overall precision/recall/accuracy: this set carries no "
                "independent human labels, so any such number would be "
                "arithmetic over an invented truth"
            ),
            "percent correct Vietnamese: not measurable here",
            (
                "damage to a control string: a rewrite is an intervention, "
                "not proven harm"
            ),
            "correctness of the corpus-majority rendering of Форт",
        ],
    }


def selftest(records: list[dict[str, Any]], models: list[str]) -> dict[str, Any]:
    """
    Prove the offline guards can actually fail.

    A leakage guard that has never fired is not evidence of anything. This
    feeds it the payload it is supposed to reject - the meaning seat's
    request with the glossary left in - and fails if it stays silent.
    """
    incident = [record for record in records if record["family"] == "glossary_incident"]
    if not incident:
        return {"ran": False, "reason": "no incident record in the selected split"}
    record = incident[0]
    _seat, model, system = seat_plan("split", models)[0]

    leaked = build_verdict_payload(model, system, segment_for(record, glossary=True))
    caught_glossary = bool(leakage_problems(leaked, record))

    through_explanation = dict(
        record,
        explanation=(
            f"{record.get('explanation', '')} The approved rendering is "
            f"{record['glossary_terms'][0]['target']}."
        ),
    )
    sneaked = build_verdict_payload(
        model, system, segment_for(through_explanation, glossary=False)
    )
    caught_explanation = bool(leakage_problems(sneaked, through_explanation))

    clean = build_verdict_payload(model, system, segment_for(record, glossary=False))
    silent_when_clean = not leakage_problems(clean, record)

    return {
        "ran": True,
        "record": record["record_id"],
        "catches_glossary_block": caught_glossary,
        "catches_term_via_explanation": caught_explanation,
        "silent_on_a_clean_payload": silent_when_clean,
        "passed": caught_glossary and caught_explanation and silent_when_clean,
    }


def offline_report(records: list[dict[str, Any]], arm: str, models: list[str]) -> dict:
    """Everything checkable without a provider, plus the exact payloads."""
    problems: list[str] = []
    payload_samples: list[dict[str, Any]] = []
    for record in records:
        for seat, model, system in seat_plan(arm, models):
            with_glossary = arm == "current" or seat == "terminology"
            segment = segment_for(record, glossary=with_glossary)
            payload = build_verdict_payload(model, system, segment)
            if not with_glossary:
                problems.extend(leakage_problems(payload, record))
            if len(payload_samples) < 4:
                payload_samples.append(payload)
        measured_visible = mechanics(record["target"]) != mechanics(
            record["base_target"]
        )
        claims_visible = record["expected"]["mechanically_visible"]
        if claims_visible and not measured_visible:
            problems.append(
                f"{record['record_id']}: labelled mechanically visible but "
                "the deterministic content is identical"
            )
        if record["family"] == "glossary_incident" and not record.get("glossary_terms"):
            problems.append(f"{record['record_id']}: incident row carries no glossary")
    # Worst case: every seat plus a repair and a full re-check per record.
    seats = len(seat_plan(arm, models))
    return {
        "leakage_and_label_problems": problems,
        "payload_samples": payload_samples,
        "request_budget": {
            "records": len(records),
            "seats": seats,
            "best_case_requests": len(records) * seats,
            "worst_case_requests": len(records) * (seats * 2 + 1),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge role-split comparison")
    parser.add_argument("--arm", choices=("current", "split"), default="current")
    parser.add_argument("--split", default="dev", help="dev, control, or dev,control")
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--models", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=0,
        help="hard ceiling; required for a real run",
    )
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="run the whole pipeline against a local deterministic "
        "answerer: no network, no key, no spend",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.data.read_text(encoding="utf-8"))
    wanted = {piece.strip() for piece in args.split.split(",") if piece.strip()}
    records = [row for row in payload["records"] if row["split"] in wanted]
    if args.limit:
        records = records[: args.limit]
    if not records:
        sys.exit(f"no records for split {args.split}")

    models = [piece.strip() for piece in args.models.split(",") if piece.strip()]
    if not models:
        models = ["vendor-a/model-placeholder", "vendor-b/model-placeholder"]

    if args.dry_run:
        report = offline_report(records, args.arm, models)
        guards = selftest(records, models)
        report |= {
            "arm": args.arm,
            "split": sorted(wanted),
            "models": models,
            "guard_selftest": guards,
        }
        print("ROLE_SPLIT_JSON", json.dumps(report, ensure_ascii=False, sort_keys=True))
        if report["leakage_and_label_problems"]:
            sys.exit("offline invariants violated")
        if guards["ran"] and not guards["passed"]:
            sys.exit("the leakage guard does not fail when it should")
        return

    global STUB_MODE  # ruff: ignore[global-statement]
    STUB_MODE = args.stub
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not args.stub:
        if not api_key:
            sys.exit("OPENROUTER_API_KEY is required for a real run")
        if args.max_requests <= 0:
            sys.exit("--max-requests is required for a real run; it spends money")
    if len(models) < 2:
        sys.exit("two models are required: one per seat")

    seats = len(seat_plan(args.arm, models))
    ceiling = args.max_requests or (
        len(records) * (seats * 2 + 1) * args.repeats if args.stub else 0
    )
    budget = Budget(ceiling)
    usage = Usage()
    rounds: list[dict[str, Any]] = []
    for attempt in range(args.repeats):
        outcomes: dict[str, RecordOutcome] = {}
        for record in records:
            outcomes[record["record_id"]] = judge_record(
                record,
                args.arm,
                models,
                api_key,
                args.timeout,
                budget,
                usage,
                repair=not args.no_repair,
            )
        rounds.append({"repeat": attempt, "metrics": measure(records, outcomes)})

    result = {
        "arm": args.arm,
        "split": sorted(wanted),
        "models": models,
        "repeats": args.repeats,
        "records": len(records),
        "usage": usage.asdict(),
        "rounds": rounds,
    }
    if args.out:
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("ROLE_SPLIT_JSON", json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
