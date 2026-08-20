# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter client for the LLM judge (measured arm D).

Separate from RoutedLLMTranslation on purpose: the judge is not a machine
translation service. Mirrors the loc-kit profile client
(weblate/trans/loc_kit.py): fixed host, strict schema, one exception
type, no user-supplied endpoint/key/model. Requests are batched
(JUDGE_BATCH_SIZE segments per HTTP call) exactly as the measurement was
run — the measured noise/precision/recall/cost numbers assume batching.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext as _

from weblate.trans.models.judge import SEVERITY_RANK
from weblate.utils.requests import fetch_validated_url

if TYPE_CHECKING:
    from collections.abc import Sequence

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE_REQUEST_TIMEOUT = 120
# Measured category set (st2-zh-recalibration.py:59-68).
CATEGORIES = (
    "terminology", "mistranslation", "omission", "addition",
    "fluency", "punctuation", "markup", "register",
)
# Deterministic, order-revealing sample values (measured driver).
_SAMPLE_VALUES = ("3", "7", "15", "28", "42", "56", "64", "77")
_PLACEHOLDER_RE = re.compile(r"\{\[PARAM(\d+)\]\}|\{(\d+)\}|%([A-Za-z_]+)%")

LOGGER = __import__("logging").getLogger(__name__)


class JudgeError(Exception):
    """A judge gate failure: disabled or misconfigured. Transport and
    parse failures do NOT raise — they yield an unparsed result so one
    bad batch never aborts a run (D5)."""


@dataclass(frozen=True)
class JudgeRequest:
    unit_key: str
    source: str
    target: str
    source_language: str
    target_language: str
    note: str
    glossary_terms: Sequence[tuple[str, str]]
    failing_checks: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class JudgeResult:
    max_severity: str
    model_verdict: str
    errors: list[dict]
    back_translation: str
    unparsed: bool = False


UNPARSED = JudgeResult(
    max_severity="none", model_verdict="", errors=[], back_translation="", unparsed=True
)


def render_preview(text: str) -> str | None:
    """Substitute sample values into engine placeholders; None if none."""

    def sub(match: re.Match[str]) -> str:
        param, plain, named = match.groups()
        if named is not None:
            return _SAMPLE_VALUES[sum(named.encode()) % len(_SAMPLE_VALUES)]
        return _SAMPLE_VALUES[int(param or plain) % len(_SAMPLE_VALUES)]

    rendered, count = _PLACEHOLDER_RE.subn(sub, text)
    return rendered if count else None


def _load_prompt(source_language: str, target_language: str) -> str:
    """Load the arm-D verdict prompt with the language pair filled in.

    The measured prompt was hardcoded for ru->zh_Hans; generalizing the
    pair to fields is NOT covered by the measurement and must be
    validated on the first production run.
    """
    template = (
        resources.files("weblate.trans.judge_prompts")
        .joinpath("verdict.txt")
        .read_text(encoding="utf-8")
    )
    # Not str.format: the prompt deliberately shows literal {0} and
    # {[PARAM0]} placeholder syntax, which format would treat as fields.
    return (
        template.replace("{source_language}", source_language)
        .replace("{target_language}", target_language)
    )


def _response_schema() -> dict:
    """Strict schema of the measured arm-D reply shape.

    ``back_translation`` is the single deliberate deviation from the
    measured schema (an extra output field, unmeasured; minimal metric
    risk) — it feeds the producer card.
    """
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
        "properties": {"segments": {"type": "array", "items": segment}},
        "required": ["segments"],
        "additionalProperties": False,
    }


def _segment(index: int, req: JudgeRequest) -> dict:
    """Render one request as a measured arm-D segment."""
    segment: dict = {
        "id": index,
        "key": req.unit_key,
        "source": req.source,
        "target": req.target,
    }
    rendered_source = render_preview(req.source)
    rendered_target = render_preview(req.target)
    if rendered_source is not None or rendered_target is not None:
        segment["rendered_source"] = rendered_source or req.source
        segment["rendered_target"] = rendered_target or req.target
    if req.note:
        segment["note"] = req.note
    if req.glossary_terms:
        segment["glossary"] = [
            {"source": source, "target": target}
            for source, target in req.glossary_terms
        ]
    if req.failing_checks:
        segment["checks"] = list(req.failing_checks)
    return segment


def _max_severity(errors: list) -> str:
    """Worst error severity, or "none" when the list is empty."""
    worst = 0
    for error in errors:
        if isinstance(error, dict):
            worst = max(worst, SEVERITY_RANK.get(error.get("severity", ""), 0))
    return JudgeSeverityOrdered[worst] if worst else "none"


# Rank 0 is "none" only via an empty list; error severities start at rank 1.
JudgeSeverityOrdered = ("none", "minor", "major", "critical")


def _record_usage(payload: dict, model: str) -> None:
    """Mirror machinery's record_llm_usage (never raises).

    The judge is a paid path outside machinery; the repair path
    (RoutedLLMTranslation <- OpenAITranslation) is logged by the same
    mechanism, so accounting must be symmetric.
    """
    try:
        from weblate.trans.models.llm_usage import LLMUsageLog

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        if not prompt_tokens and not completion_tokens:
            return
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        cost = usage.get("cost")
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        LLMUsageLog.objects.create(
            model=model,
            project_slug="",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=Decimal(str(cost)) if cost else None,
            response_id=str(payload.get("id") or ""),
            cached_tokens=prompt_details.get("cached_tokens") or 0,
            reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
        )
    except Exception:
        LOGGER.exception("Failed to record LLM usage")


def _parse_reply(payload: dict, size: int) -> list[JudgeResult] | None:
    """Parse a batch reply aligned by segment id; None when unusable."""
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
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(segments, list) or len(segments) != size:
        return None
    results: list[JudgeResult] = [None] * size  # type: ignore[list-item]
    for seg in segments:
        if not isinstance(seg, dict):
            return None
        try:
            index = seg["id"]
        except (KeyError, TypeError):
            return None
        if not isinstance(index, int) or not 0 <= index < size or results[index]:
            return None
        errors = seg.get("errors", [])
        if not isinstance(errors, list):
            return None
        for error in errors:
            if not isinstance(error, dict):
                return None
            if error.get("severity") not in ("minor", "major", "critical"):
                return None
            if error.get("category") not in CATEGORIES:
                return None
        back_translation = seg.get("back_translation", "")
        if not isinstance(back_translation, str):
            return None
        results[index] = JudgeResult(
            max_severity=_max_severity(errors),
            model_verdict=seg.get("verdict", ""),
            errors=errors,
            back_translation=back_translation,
        )
    if any(result is None for result in results):
        return None
    return results


def _post_batch(payload: dict, model: str) -> dict | None:
    """One POST; the response dict, or None on transport failure."""
    try:
        response = fetch_validated_url(
            "POST",
            OPENROUTER_CHAT_COMPLETIONS_URL,
            # Built inline so the bearer token is never bound to a frame
            # local that an error reporter could serialize.
            headers={
                "Authorization": f"Bearer {settings.JUDGE_OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=JUDGE_REQUEST_TIMEOUT,
            raise_for_status=False,
            follow_redirects=False,
        )
    except Exception:
        return None
    if response.status_code >= 400:
        return None
    try:
        return response.json()
    except Exception:
        return None


def request_verdicts(
    requests: Sequence[JudgeRequest], *, model: str
) -> list[JudgeResult]:
    """Judge every request; results in input order.

    Gate failures (disabled, no key, no model) raise JudgeError before
    any network call. Any batch failure (transport, HTTP >= 400,
    unreadable JSON, length mismatch, unknown severity/category) marks
    the whole batch unparsed — never a raise, never a default verdict.
    One retry per batch on 429/403 with a doubled sleep, no more.
    """
    if not settings.JUDGE_ENABLED:
        raise JudgeError(_("The LLM judge is disabled."))
    if not settings.JUDGE_OPENROUTER_KEY:
        raise JudgeError(_("The LLM judge is not configured."))
    if not model:
        raise JudgeError(_("The LLM judge is not configured."))

    batch_size = settings.JUDGE_BATCH_SIZE
    sleep = settings.JUDGE_REQUEST_SLEEP
    results: list[JudgeResult] = []
    batches = [
        list(requests[start : start + batch_size])
        for start in range(0, len(requests), batch_size)
    ]
    for position, batch in enumerate(batches):
        segment_payloads = [_segment(index, req) for index, req in enumerate(batch)]
        payload = {
            "model": model,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "judge_verdicts",
                    "strict": True,
                    "schema": _response_schema(),
                },
            },
            "provider": {"require_parameters": True},
            "usage": {"include": True},
            "messages": [
                {
                    "role": "system",
                    "content": _load_prompt(
                        batch[0].source_language, batch[0].target_language
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"segments": segment_payloads}, ensure_ascii=False
                    ),
                },
            ],
        }
        parsed = None
        for attempt in range(2):
            raw = _post_batch(payload, model)
            if raw is not None:
                parsed = _parse_reply(raw, len(batch))
                if parsed is not None:
                    _record_usage(raw, model)
                    break
            if attempt == 0:
                time.sleep(sleep * 2 + 1.0)
        results.extend(parsed if parsed is not None else [UNPARSED] * len(batch))
        if position < len(batches) - 1 and sleep:
            time.sleep(sleep)
    return results
