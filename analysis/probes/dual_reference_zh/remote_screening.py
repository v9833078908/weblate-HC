# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the registered screening pilot in a production container without writes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import django
import requests

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

GEMINI_MODEL = "google/gemini-3.7-flash"
GEMINI_MAX_TOKENS = 1024
BATCH_SIZE = 5
WORKERS = 4
TARGET_LANGUAGE = "Simplified Chinese (zh-Hans)"


def canonical_hash(forms: list[str]) -> str:
    """Match the dataset's JSON-list hash without retaining the source text."""
    return hashlib.sha256(
        json.dumps(forms, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def batches(records: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    """Yield fixed-size batches in frozen input order."""
    for start in range(0, len(records), BATCH_SIZE):
        yield records[start : start + BATCH_SIZE]


def source_payload(record: Mapping[str, Any], arm: str) -> dict[str, object]:
    """Return only the source roles permitted for a generation or QA arm."""
    roles = {
        "A": (("ru", "primary"),),
        "B": (("en", "primary"),),
        "C": (("en", "primary"), ("ru", "reference")),
        "D": (("ru", "primary"), ("en", "reference")),
        "E": (("en", "primary"),),
        "F": (("en", "primary"), ("ru", "reference")),
    }
    return {
        "record_id": record["record_id"],
        "context": record["context"],
        "sources": [
            {"language": language, "role": role, "text": record[language]}
            for language, role in roles[arm]
        ],
    }


def translation_messages(
    records: list[dict[str, Any]], arm: str
) -> list[dict[str, str]]:
    """Build one arm-specific generation request."""
    return [
        {
            "role": "system",
            "content": (
                f"Translate each primary source into {TARGET_LANGUAGE} for a game. "
                "A reference source is context only and must never be emitted. "
                "Preserve placeholders, markup, `$`, and engine identifiers exactly. "
                'Return only JSON: {"translations": [{"record_id": "...", '
                '"translation": "..."}]}. Include every record_id exactly once.'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"items": [source_payload(record, arm) for record in records]},
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def review_messages(
    records: list[dict[str, Any]], arm: str, b_outputs: Mapping[str, str]
) -> list[dict[str, str]]:
    """Build blindable semantic review of the immutable B drafts."""
    items = [
        {
            **source_payload(record, arm),
            "candidate": b_outputs[record["record_id"]],
        }
        for record in records
    ]
    return [
        {
            "role": "system",
            "content": (
                "You are a Chinese game-localization reviewer. Identify only real "
                "errors in the candidate relative to the supplied source roles. "
                "Classify every issue as minor, major, or critical and use categories "
                "accuracy, terminology, fluency, style, locale, or functional. "
                'Return only JSON: {"reviews": [{"record_id": "...", "issues": '
                '[{"severity": "minor|major|critical", "category": "...", '
                '"message": "..."}]}]}. Include every record_id exactly once.'
            ),
        },
        {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
    ]


def edit_messages(
    records: list[dict[str, Any]],
    arm: str,
    b_outputs: Mapping[str, str],
    reviews: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, str]]:
    """Build one editor request, including both raw reviewer responses."""
    items = [
        {
            **source_payload(record, arm),
            "candidate": b_outputs[record["record_id"]],
            "reviews": reviews[record["record_id"]],
        }
        for record in records
    ]
    return [
        {
            "role": "system",
            "content": (
                f"You are a {TARGET_LANGUAGE} game-localization editor. Improve the "
                "candidate only when a reviewer issue is valid against the supplied "
                "source roles. Reviewers may be wrong. Preserve placeholders, markup, "
                "`$`, and engine identifiers exactly. Edit even when reviews are empty. "
                'Return only JSON: {"translations": [{"record_id": "...", '
                '"translation": "..."}]}. Include every record_id exactly once.'
            ),
        },
        {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
    ]


def evaluation_messages(
    records: list[dict[str, Any]], candidates: Mapping[str, str]
) -> list[dict[str, str]]:
    """Build an arm-blind screening proxy evaluation request."""
    items = [
        {
            "record_id": record["record_id"],
            "context": record["context"],
            "ru": record["ru"],
            "en": record["en"],
            "candidate": candidates[record["record_id"]],
        }
        for record in records
    ]
    return [
        {
            "role": "system",
            "content": (
                "You are a blinded screening evaluator of Chinese game localization. "
                "Assess the candidate using the same RU and EN meaning evidence for "
                "every item. Mark unusable true only for major or critical defects; "
                "minor defects are usable. Do not infer or mention any experimental arm. "
                'Return only JSON: {"ratings": [{"record_id": "...", "unusable": '
                'true|false, "severity": "pass|minor|major|critical", "categories": '
                '["..."]}]}. Include every record_id exactly once.'
            ),
        },
        {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
    ]


def post_json(
    base_url: str,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None,
    timeout: float,
) -> tuple[dict[str, object], dict[str, object]]:
    """Call an OpenAI-compatible endpoint and keep only response metadata."""
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        msg = "Model response content is not a string."
        raise TypeError(msg)
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        msg = "Model response is not a JSON object."
        raise TypeError(msg)
    receipt = {
        "served_model": body.get("model", model),
        "usage": body.get("usage"),
        "status": response.status_code,
    }
    return parsed, receipt


def parse_items(
    response: Mapping[str, object], key: str, expected: tuple[str, ...]
) -> dict[str, dict[str, object]]:
    """Validate an object-array response against its exact requested IDs."""
    items = response.get(key)
    if not isinstance(items, list):
        msg = f"Response has no {key} list."
        raise TypeError(msg)
    mapped: dict[str, dict[str, object]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("record_id"), str):
            msg = f"Response has malformed {key} item."
            raise TypeError(msg)
        record_id = item["record_id"]
        if record_id in mapped:
            msg = f"Response repeats {record_id}."
            raise ValueError(msg)
        mapped[record_id] = item
    if set(mapped) != set(expected):
        msg = f"Response {key} IDs do not match request."
        raise ValueError(msg)
    return mapped


def load_selector(selector: Path | None, encoded: str | None) -> list[dict[str, Any]]:
    """Read a non-raw selector from a file or an in-memory base64 argument."""
    if selector is not None:
        value = selector.read_text(encoding="utf-8")
    elif encoded is not None:
        value = base64.b64decode(encoded).decode("utf-8")
    else:
        msg = "A selector file or selector JSON is required."
        raise ValueError(msg)
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        msg = "Selector must be a JSON array of objects."
        raise TypeError(msg)
    return parsed


def load_records(selected_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load current Unit text only after it matches the frozen snapshot hashes."""
    # Django is initialized by main() before the Unit model is imported.
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit

    loaded: list[dict[str, Any]] = []
    for selected in selected_records:
        ru_unit = Unit.objects.get(pk=selected["ru_unit_id"])
        en_unit = Unit.objects.get(pk=selected["en_unit_id"])
        ru = list(ru_unit.get_target_plurals())
        en = list(en_unit.get_target_plurals())
        if (
            canonical_hash(ru) != selected["ru_text_sha256"]
            or canonical_hash(en) != selected["en_text_sha256"]
            or str(ru_unit.id_hash) != selected["id_hash"]
            or str(en_unit.id_hash) != selected["id_hash"]
            or ru_unit.context != selected["key"]
            or en_unit.context != selected["key"]
            or en_unit.source_unit_id != ru_unit.id
        ):
            msg = f"Frozen snapshot mismatch for {selected['record_id']}."
            raise ValueError(msg)
        if len(ru) != 1 or len(en) != 1:
            msg = f"Plural forms are unsupported for {selected['record_id']}."
            raise ValueError(msg)
        loaded.append(
            {**selected, "context": selected["key"], "ru": ru[0], "en": en[0]}
        )
    return loaded


def run_parallel(
    work: list[tuple[str, list[dict[str, Any]]]],
    callback,
) -> dict[str, object]:
    """Execute independent batches and retain every success or failure."""
    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(callback, batch): key for key, batch in work}
        for future, key in ((future, futures[future]) for future in futures):
            try:
                results[key] = future.result()
            except Exception as error:
                results[key] = {"error": f"{type(error).__name__}: {error}"}
    return results


def main() -> None:
    """Execute all registered stages and write one local-only raw result file."""
    parser = argparse.ArgumentParser()
    selector_group = parser.add_mutually_exclusive_group(required=True)
    selector_group.add_argument("--selector", type=Path)
    selector_group.add_argument("--selector-json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    django.setup()
    # Imports depend on Django app initialization.
    # ruff: ignore[import-outside-top-level]
    from django.conf import settings

    # ruff: ignore[import-outside-top-level]
    from weblate.configuration.models import Setting, SettingCategory

    records = load_records(load_selector(args.selector, args.selector_json))
    if args.validate_only:
        args.output.write_text(
            json.dumps({"validated_records": len(records), "inference": False}),
            encoding="utf-8",
        )
        return
    openrouter = Setting.objects.get_settings_dict(SettingCategory.MT)["openrouter"]
    record_map = {record["record_id"]: record for record in records}
    output: dict[str, object] = {
        "generator_model": GEMINI_MODEL,
        "records": records,
        "translations": {},
        "reviews": {},
        "ratings": {},
        "attempts": [],
    }

    def gemini(batch: list[dict[str, Any]], arm: str, messages: list[dict[str, str]]):
        body, receipt = post_json(
            openrouter["base_url"],
            openrouter["key"],
            GEMINI_MODEL,
            messages,
            max_tokens=GEMINI_MAX_TOKENS,
            timeout=120,
        )
        mapped = parse_items(
            body, "translations", tuple(record["record_id"] for record in batch)
        )
        return {"items": mapped, "receipt": receipt, "arm": arm}

    translations: dict[str, dict[str, str]] = {arm: {} for arm in "ABCD"}
    for arm in "ABCD":
        work = [
            (f"{arm}:{index}", batch) for index, batch in enumerate(batches(records))
        ]
        results = run_parallel(
            work,
            lambda batch, current_arm=arm: gemini(
                batch, current_arm, translation_messages(batch, current_arm)
            ),
        )
        output["translations"][arm] = results
        for result in results.values():
            if isinstance(result, dict) and "items" in result:
                translations[arm].update(
                    {
                        record_id: item["translation"]
                        for record_id, item in result["items"].items()
                        if isinstance(item.get("translation"), str)
                    }
                )

    judge_base = settings.JUDGE_BASE_URL
    judge_key = settings.JUDGE_API_KEY
    judge_profiles = (
        ("seat-1", settings.JUDGE_MODEL_SEAT_1, 120.0),
        ("seat-2", settings.JUDGE_MODEL_SEAT_2, 150.0),
    )

    def judge(
        batch: list[dict[str, Any]],
        model: str,
        timeout: float,
        messages: list[dict[str, str]],
        key: str,
    ):
        body, receipt = post_json(
            judge_base, judge_key, model, messages, max_tokens=None, timeout=timeout
        )
        return {
            "items": parse_items(
                body, key, tuple(record["record_id"] for record in batch)
            ),
            "receipt": receipt,
        }

    reviews: dict[str, dict[str, dict[str, list[dict[str, object]]]]] = {
        "E": {},
        "F": {},
    }
    for arm in ("E", "F"):
        eligible = [
            record for record in records if record["record_id"] in translations["B"]
        ]
        output["reviews"][arm] = {}
        reviews[arm] = {}
        for seat, model, timeout in judge_profiles:
            work = [
                (f"{seat}:{index}", batch)
                for index, batch in enumerate(batches(eligible))
            ]
            results = run_parallel(
                work,
                lambda batch, current_arm=arm, current_model=model, current_timeout=timeout: (
                    judge(
                        batch,
                        current_model,
                        current_timeout,
                        review_messages(batch, current_arm, translations["B"]),
                        "reviews",
                    )
                ),
            )
            output["reviews"][arm][seat] = results
            reviews[arm][seat] = {}
            for result in results.values():
                if isinstance(result, dict) and "items" in result:
                    reviews[arm][seat].update(
                        {
                            record_id: item.get("issues", [])
                            for record_id, item in result["items"].items()
                            if isinstance(item.get("issues", []), list)
                        }
                    )

    for arm in ("E", "F"):
        eligible = [
            record
            for record in records
            if record["record_id"] in translations["B"]
            and all(
                record["record_id"] in reviews[arm][seat]
                for seat, _model, _timeout in judge_profiles
            )
        ]
        merged_reviews = {
            record["record_id"]: [
                {"seat": seat, "issues": reviews[arm][seat][record["record_id"]]}
                for seat, _model, _timeout in judge_profiles
            ]
            for record in eligible
        }
        work = [
            (f"{arm}:{index}", batch) for index, batch in enumerate(batches(eligible))
        ]
        results = run_parallel(
            work,
            lambda batch, current_arm=arm, current_reviews=merged_reviews: gemini(
                batch,
                current_arm,
                edit_messages(batch, current_arm, translations["B"], current_reviews),
            ),
        )
        output["translations"][arm] = results
        translations[arm] = {}
        for result in results.values():
            if isinstance(result, dict) and "items" in result:
                translations[arm].update(
                    {
                        record_id: item["translation"]
                        for record_id, item in result["items"].items()
                        if isinstance(item.get("translation"), str)
                    }
                )

    for arm in "ABCDEF":
        eligible = [record_map[record_id] for record_id in translations[arm]]
        output["ratings"][arm] = {}
        for seat, model, timeout in judge_profiles:
            work = [
                (f"{seat}:{index}", batch)
                for index, batch in enumerate(batches(eligible))
            ]
            results = run_parallel(
                work,
                lambda batch, current_model=model, current_timeout=timeout, current_arm=arm: (
                    judge(
                        batch,
                        current_model,
                        current_timeout,
                        evaluation_messages(batch, translations[current_arm]),
                        "ratings",
                    )
                ),
            )
            output["ratings"][arm][seat] = results

    args.output.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
