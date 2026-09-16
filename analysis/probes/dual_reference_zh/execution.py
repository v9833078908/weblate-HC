# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, offline-built prompts for the registered screening pilot."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .payloads import build_generation_payload

GEMINI_MODEL = "google/gemini-3.7-flash"
GEMINI_TEMPERATURE = 0
GEMINI_MAX_TOKENS = 1024
SEGMENTS_PER_BATCH = 5

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
