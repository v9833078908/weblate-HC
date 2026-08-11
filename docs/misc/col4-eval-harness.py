# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Eval harness for the COL4 ru->fr LLM path.

Runs the real machinery code path over a fixed, deterministically selected set of
COL4 units and reports transport failures, defect classes, glossary adherence and
cost. Nothing is written to the database: ``batch_translate`` only fills the
in-memory ``unit.machinery`` dict, and the source state each unit is put into for
the request is restored afterwards.

Run inside the Weblate container::

    B64=$(base64 < docs/misc/col4-eval-harness.py | tr -d '\\n')
    docker exec hcgameloc-weblate-1 weblate shell -c \\
        "import base64; exec(base64.b64decode('$B64').decode())"

The last output line is ``EVAL_JSON {...}`` for diffing two runs.

``EVAL_BATCH_SIZE`` and ``EVAL_CONCURRENCY`` override how many strings one
request carries and how many requests fly at once, for measuring either against
the same sample.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter

from weblate.checks.models import CHECKS
from weblate.glossary.models import get_glossary_terms
from weblate.machinery.models import MACHINERY
from weblate.trans.autotranslate import fetch_machinery_matches
from weblate.trans.models import Project, Translation
from weblate.utils.state import STATE_EMPTY, STATE_TRANSLATED

PROJECT_SLUG = os.environ.get("EVAL_PROJECT", "col4")
COMPONENT_SLUG = os.environ.get("EVAL_COMPONENT", "data")
LANGUAGE_CODE = os.environ.get("EVAL_LANGUAGE", "fr")
GLOSSARY_SLUG = os.environ.get("EVAL_GLOSSARY", "glossariy")
ENGINE = os.environ.get("EVAL_ENGINE", "openrouter")
SET_SIZE = int(os.environ.get("EVAL_SIZE", "150"))
THRESHOLD = int(os.environ.get("EVAL_THRESHOLD", "75"))
# Both default to what the service ships with.
BATCH_SIZE = int(os.environ.get("EVAL_BATCH_SIZE", "0"))
CONCURRENCY = int(os.environ.get("EVAL_CONCURRENCY", "0"))

TERMINAL_PUNCTUATION = ".!?…:;"
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
REJECT_PATTERNS = (
    ("json_error", "Could not parse assistant reply as JSON"),
    ("mismatch", "Mismatching assistant reply"),
    ("blank", "Blank assistant reply"),
    ("incomplete", "Incomplete assistant reply"),
    ("repaired", "response-repaired"),
)


class CaptureHandler(logging.Handler):
    """Collect machinery warnings and outgoing HTTP requests."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.requests = 0
        self.rejects: Counter[str] = Counter()
        self.finish_reasons: Counter[str] = Counter()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        if "HTTP Request: POST" in message and "chat/completions" in message:
            self.requests += 1
            return
        if record.levelno < logging.WARNING:
            return
        for label, needle in REJECT_PATTERNS:
            if needle in message:
                self.rejects[label] += 1
                self.messages.append(message[:400])
                break
        match = re.search(r"finish_reason=(\w+)", message)
        if match:
            self.finish_reasons[match.group(1)] += 1


def select_units(translation: Translation, size: int) -> list:
    """Pick a stable, corpus-wide sample without relying on stored state."""
    ids = list(translation.unit_set.order_by("id").values_list("id", flat=True))
    if not ids:
        return []
    step = max(1, len(ids) // size)
    picked = ids[::step][:size]
    return list(translation.unit_set.filter(id__in=picked).order_by("id"))


def fingerprint(units: list) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    for unit in units:
        digest.update(f"{unit.id}\x00{unit.source}\x00".encode())
    return digest.hexdigest()[:12]


def glossary_pairs(project: Project) -> dict[str, str]:
    component = project.component_set.get(slug=GLOSSARY_SLUG)
    target = component.translation_set.get(language__code=LANGUAGE_CODE)
    return {
        unit.source.strip(): unit.target.strip()
        for unit in target.unit_set.all()
        if unit.source.strip() and unit.target.strip()
    }


def term_stem(rendering: str) -> str:
    head = rendering.split(maxsplit=1)[0]
    return head[:-1] if len(head) > 5 else head


def main() -> None:
    project = Project.objects.get(slug=PROJECT_SLUG)
    translation = Translation.objects.get(
        component__project=project,
        component__slug=COMPONENT_SLUG,
        language__code=LANGUAGE_CODE,
    )
    units = select_units(translation, SET_SIZE)
    if not units:
        print("EVAL_JSON", json.dumps({"error": "empty unit set"}))
        return

    machinery_settings = project.get_machinery_settings()
    if ENGINE not in machinery_settings:
        print("EVAL_JSON", json.dumps({"error": f"{ENGINE} not configured"}))
        return
    service = MACHINERY[ENGINE](machinery_settings[ENGINE])
    # A cached reply would measure the cache, not the pipeline.
    service.cache_translations = False
    if BATCH_SIZE:
        service.batch_size = BATCH_SIZE
    if CONCURRENCY:
        service.batch_concurrency = CONCURRENCY
    # A service stopped by an earlier run answers nothing at all, which would
    # otherwise be reported as a run with zero defects.
    was_rate_limited = service.is_rate_limited()
    if was_rate_limited:
        service.delete_cache()

    # The provider reports tokens, cost and finish_reason per reply; the
    # machinery drops all three, so record them at the parse seam.
    replies: list[dict] = []
    service_class = type(service)
    original_parse = service_class.parse_chat_response

    def recording_parse(payload):
        choices = payload.get("choices") or [{}]
        replies.append(
            {
                "usage": payload.get("usage") or {},
                "finish_reason": choices[0].get("finish_reason"),
            }
        )
        return original_parse(payload)

    service_class.parse_chat_response = staticmethod(recording_parse)

    # Ask for a fresh translation rather than a refinement: an existing target
    # would put "translation" into the payload and exercise the edit path.
    saved = [(unit.target, unit.state, unit.machinery) for unit in units]
    for unit in units:
        unit.target = ""
        unit.state = STATE_EMPTY
        unit.machinery = {}

    capture = CaptureHandler()
    root = logging.getLogger()
    root.addHandler(capture)
    previous_level = root.level
    root.setLevel(logging.INFO)
    started = time.monotonic()
    try:
        fetch_machinery_matches(
            units=units,
            user=None,
            services=[service],
            threshold=THRESHOLD,
        )
    finally:
        root.setLevel(previous_level)
        root.removeHandler(capture)
        service_class.parse_chat_response = original_parse
    elapsed = time.monotonic() - started

    candidates: list[tuple[object, str]] = []
    for unit in units:
        texts = unit.machinery.get("translation") or []
        text = texts[0] if texts else ""
        if text:
            candidates.append((unit, text))

    checks: Counter[str] = Counter()
    final_punctuation = cyrillic = 0
    growth: list[float] = []
    glossary_hits = glossary_misses = 0
    missed_terms: Counter[str] = Counter()
    pairs = glossary_pairs(project)
    stems = {term: term_stem(rendering) for term, rendering in pairs.items()}

    for unit, text in candidates:
        source = unit.source
        # Restore a translated state so the checks do not skip the unit.
        unit.target = text
        unit.state = STATE_TRANSLATED
        for check in CHECKS.values():
            try:
                if check.check_target([source], [text], unit):
                    checks[check.check_id] += 1
            except Exception:
                checks[f"{check.check_id}:error"] += 1
        stripped_source = source.strip()
        if (
            stripped_source
            and stripped_source[-1] not in TERMINAL_PUNCTUATION
            and text.strip().endswith(".")
        ):
            final_punctuation += 1
        if CYRILLIC_RE.search(text):
            cyrillic += 1
        if stripped_source:
            growth.append(len(text) / len(stripped_source))
        for term in get_glossary_terms(unit, include_variants=False):
            expected = stems.get(term.source.strip())
            if expected is None:
                continue
            if re.search(re.escape(expected), text, re.IGNORECASE):
                glossary_hits += 1
            else:
                glossary_misses += 1
                missed_terms[term.source.strip()] += 1

    for unit, (target, state, machinery) in zip(units, saved, strict=True):
        unit.target = target
        unit.state = state
        unit.machinery = machinery

    cost = round(sum(r["usage"].get("cost") or 0 for r in replies), 6)
    prompt_tokens = sum(r["usage"].get("prompt_tokens") or 0 for r in replies)
    completion_tokens = sum(r["usage"].get("completion_tokens") or 0 for r in replies)
    cached_tokens = sum(
        (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        for r in replies
    )
    finish_reasons = Counter(r["finish_reason"] for r in replies)
    occurrences = glossary_hits + glossary_misses
    result = {
        "set_size": len(units),
        "fingerprint": fingerprint(units),
        "batch_size": service.batch_size,
        "concurrency": service.batch_concurrency,
        "seconds": round(elapsed, 1),
        "requests": len(replies),
        "requests_logged": capture.requests,
        "rate_limited_before": was_rate_limited,
        "rate_limited_after": service.is_rate_limited(),
        "rejects": dict(capture.rejects),
        "rejects_total": sum(capture.rejects.values()),
        "finish_reasons": {str(k): v for k, v in finish_reasons.items()},
        "translated": len(candidates),
        "coverage_pct": round(100 * len(candidates) / len(units), 1),
        "cost_usd": cost,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "checks": dict(checks.most_common()),
        "final_punctuation_added": final_punctuation,
        "cyrillic_leaked": cyrillic,
        "length_ratio_avg": round(sum(growth) / len(growth), 3) if growth else None,
        "length_ratio_over_130pct": len([r for r in growth if r > 1.3]),
        "glossary_occurrences": occurrences,
        "glossary_applied": glossary_hits,
        "glossary_adherence_pct": (
            round(100 * glossary_hits / occurrences, 1) if occurrences else None
        ),
        "glossary_missed_terms": dict(missed_terms.most_common(10)),
    }
    for key_name, value in result.items():
        print(f"{key_name.upper()} {value}")
    for message in capture.messages[:5]:
        print("REJECT_SAMPLE", message)
    print("EVAL_JSON", json.dumps(result, ensure_ascii=False, sort_keys=True))


main()
