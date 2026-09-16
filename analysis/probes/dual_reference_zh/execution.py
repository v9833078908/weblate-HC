# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, offline-built prompts for the registered screening pilot."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from typing import Any

from .payloads import build_generation_payload

GEMINI_MODEL = "google/gemini-3.7-flash"
GEMINI_TEMPERATURE = 0
GEMINI_MAX_TOKENS = 1024
SEGMENTS_PER_BATCH = 5


def build_block_schedule(
    records: list[Mapping[str, Any]],
    *,
    arms: tuple[str, ...],
    batch_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Randomize treatment order while preserving paired record blocks."""
    if batch_size < 1:
        msg = "batch_size must be positive."
        raise ValueError(msg)
    if not arms:
        msg = "At least one arm is required."
        raise ValueError(msg)
    randomizer = random.Random(seed)  # ruff: ignore[suspicious-non-cryptographic-random-usage]
    ordered_records = list(records)
    randomizer.shuffle(ordered_records)
    tasks: list[dict[str, Any]] = []
    for block_index, start in enumerate(range(0, len(ordered_records), batch_size)):
        block_arms = list(arms)
        randomizer.shuffle(block_arms)
        block = ordered_records[start : start + batch_size]
        tasks.extend(
            {
                "arm": arm,
                "block": block_index,
                "records": block,
            }
            for arm in block_arms
        )
    return tasks


def safe_response_metadata(
    *, status: int, headers: Mapping[str, str], body: str
) -> dict[str, Any]:
    """Keep response diagnostics without retaining an arbitrary response body."""
    request_id = headers.get("x-request-id") or headers.get("request-id")
    content_type = headers.get("content-type")
    result: dict[str, Any] = {
        "status": status,
        "request_id": request_id,
        "content_type": content_type,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return result
    if not isinstance(parsed, dict) or not isinstance(parsed.get("error"), dict):
        return result
    error = parsed["error"]
    safe_error = {
        name: error[name]
        for name in ("code", "type")
        if isinstance(error.get(name), (str, int, float, bool))
    }
    if safe_error:
        result["error"] = safe_error
    return result


GENERATION_SYSTEM_PROMPT = """You are a game-localization translator.
Translate the source marked primary into Simplified Chinese. A source marked
reference is context only: use it solely to resolve meaning, never mention it
or translate it as a separate string. Preserve placeholders, markup, `$`, and
engine identifiers exactly. Return JSON only as {"translation": "..."}."""


def build_generation_messages(
    record: Mapping[str, Any], *, arm: str
) -> list[dict[str, str]]:
    """Build one generation prompt with the arm's source visibility only."""
    payload = build_generation_payload(record, arm=arm)
    return [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "record_id": payload["record_id"],
                    "context": payload["context"],
                    "sources": payload["sources"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def build_generation_batch_messages(
    records: list[Mapping[str, Any]], *, arm: str
) -> list[dict[str, str]]:
    """Build one fixed-size batch prompt without adding hidden source fields."""
    items = []
    for record in records:
        payload = build_generation_payload(record, arm=arm)
        items.append(
            {
                "record_id": payload["record_id"],
                "context": payload["context"],
                "sources": payload["sources"],
            }
        )
    return [
        {
            "role": "system",
            "content": (
                "You are a game-localization translator. Translate every primary "
                "source into Simplified Chinese. A reference source is context only. "
                "Preserve placeholders, markup, `$`, and engine identifiers exactly. "
                'Return JSON only as {"translations": [{"record_id": "...", '
                '"translation": "..."}]}. Include every record_id exactly once.'
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"items": items}, ensure_ascii=False, sort_keys=True),
        },
    ]


def parse_translation_response(
    response: Mapping[str, object], *, expected_record_ids: tuple[str, ...]
) -> dict[str, str]:
    """Validate a batch response and map translations by explicit record ID."""
    items = response.get("translations")
    if not isinstance(items, list):
        msg = "Translation response has no translations list."
        raise TypeError(msg)

    translations: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            msg = "Translation response item is not an object."
            raise TypeError(msg)
        record_id = item.get("record_id")
        translation = item.get("translation")
        if not isinstance(record_id, str) or not isinstance(translation, str):
            msg = "Translation response item is missing record_id or translation."
            raise TypeError(msg)
        if record_id in translations:
            msg = f"Duplicate translation for {record_id}."
            raise ValueError(msg)
        translations[record_id] = translation

    if set(translations) != set(expected_record_ids):
        msg = "Translation response record IDs do not match the request."
        raise ValueError(msg)
    return translations
