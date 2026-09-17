# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the registered screening pilot in a production container without writes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

import django
import requests

try:
    from .execution import build_block_schedule, safe_response_metadata
except ImportError:  # Supports the production container's direct-script entry point.
    from execution import (
        build_block_schedule,
        safe_response_metadata,
    )

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

GEMINI_MODEL = "google/gemini-3.7-flash"
GEMINI_MAX_TOKENS = 8192
BATCH_SIZE = 5
WORKERS = 4
TARGET_LANGUAGE = "Simplified Chinese (zh-Hans)"
MAX_ATTEMPTS = 2
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RANDOMIZATION_SEED = 20260916


class RequestError(Exception):
    """An HTTP or response-contract failure with safe request diagnostics."""

    def __init__(self, reason: str, metadata: dict[str, object]) -> None:
        super().__init__(reason)
        self.metadata = metadata
        self.attempts: list[dict[str, object]] = []

    def as_result(self) -> dict[str, object]:
        """Serialize the failure without leaking a request or full response."""
        return {
            "error": str(self),
            "response": self.metadata,
            "attempts": self.attempts,
        }


class Journal:
    """Persist completed attempts and batches before continuing execution."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        with path.open("x", encoding="utf-8"):
            pass
        path.chmod(0o600)

    def append(self, event: dict[str, object]) -> None:
        """Write a complete JSONL event and synchronize it to storage."""
        line = json.dumps({"timestamp": time.time(), **event}, ensure_ascii=True)
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())


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
    response_body = response.text
    metadata = safe_response_metadata(
        status=response.status_code,
        headers=response.headers,
        body=response_body,
    )
    if not response.ok:
        msg = "HTTP request failed."
        raise RequestError(msg, metadata)
    try:
        body = response.json()
    except requests.JSONDecodeError as error:
        msg = "Response body is not JSON."
        raise RequestError(msg, metadata) from error
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        msg = "Response has no message content."
        raise RequestError(msg, metadata) from error
    if not isinstance(content, str):
        msg = "Model response content is not a string."
        raise RequestError(msg, metadata)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        metadata["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
        metadata["content_length"] = len(content)
        metadata["finish_reason"] = choice.get("finish_reason")
        msg = "Model content is not JSON."
        raise RequestError(msg, metadata) from error
    if not isinstance(parsed, dict):
        msg = "Model response is not a JSON object."
        raise RequestError(msg, metadata)
    receipt = {
        "served_model": body.get("model", model),
        "usage": body.get("usage"),
        **metadata,
        "finish_reason": choice.get("finish_reason"),
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
        if key == "translations" and (
            not isinstance(item.get("translation"), str)
            or not item["translation"].strip()
        ):
            msg_0 = "Translation must be a nonempty string."
            raise ValueError(msg_0)
        if key == "reviews":
            issues = item.get("issues")
            if not isinstance(issues, list) or any(
                not isinstance(issue, dict)
                or issue.get("severity") not in {"minor", "major", "critical"}
                or not isinstance(issue.get("category"), str)
                or not isinstance(issue.get("message"), str)
                for issue in issues
            ):
                msg_0 = "Review issues have an invalid schema."
                raise ValueError(msg_0)
        if key == "ratings" and (
            not isinstance(item.get("unusable"), bool)
            or item.get("severity") not in {"pass", "minor", "major", "critical"}
            or item["unusable"] != (item["severity"] in {"major", "critical"})
            or not isinstance(item.get("categories"), list)
            or any(not isinstance(category, str) for category in item["categories"])
        ):
            msg_0 = "Rating has an invalid or inconsistent schema."
            raise ValueError(msg_0)
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
    work: list[dict[str, Any]],
    callback,
    *,
    journal: Journal | None = None,
    group: str = "",
) -> dict[str, object]:
    """Execute independent batches and retain every success or failure."""
    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(callback, task): task for task in work}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[str(key["key"])] = future.result()
            except RequestError as error:
                results[str(key["key"])] = error.as_result()
            except Exception as error:
                results[str(key["key"])] = {"error": type(error).__name__}
            if journal is not None:
                journal.append(
                    {
                        "kind": "batch",
                        "group": group,
                        "key": str(key["key"]),
                        "result": results[str(key["key"])],
                    }
                )
    return results


def run_with_retry(
    request, *, label: Mapping[str, object], journal: Journal | None = None
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    """Use one registered retry policy and retain each safe attempt result."""
    attempts: list[dict[str, object]] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            body, receipt = request()
        except (RequestError, requests.RequestException) as caught:
            error = (
                caught
                if isinstance(caught, RequestError)
                else RequestError(
                    "Transport failure.",
                    {"status": None, "exception_type": type(caught).__name__},
                )
            )
            status = error.metadata.get("status")
            retryable = status in RETRYABLE_HTTP_STATUSES or status in {None, 200}
            attempts.append(
                {
                    **label,
                    "attempt": attempt,
                    "outcome": "failure",
                    "retryable": retryable,
                    "response": dict(error.metadata),
                }
            )
            if journal is not None:
                journal.append({"kind": "attempt", **attempts[-1]})
            if attempt == MAX_ATTEMPTS or not retryable:
                error.attempts = attempts
                raise error from caught
            time.sleep(attempt)
        else:
            attempts.append(
                {
                    **label,
                    "attempt": attempt,
                    "outcome": "success",
                    "response": receipt,
                }
            )
            if journal is not None:
                journal.append({"kind": "attempt", **attempts[-1], "body": body})
            return body, receipt, attempts
    msg = "Retry loop ended without a response."
    raise RuntimeError(msg)


def scheduled_work(
    records: list[dict[str, Any]], *, arms: tuple[str, ...], seed: int
) -> list[dict[str, Any]]:
    """Give every arm the same paired blocks in registered randomized order."""
    schedule = build_block_schedule(
        records, arms=arms, batch_size=BATCH_SIZE, seed=seed
    )
    for task in schedule:
        task["key"] = f"{task['arm']}:{task['block']}"
    return schedule


def collect_attempts(value: object) -> list[dict[str, object]]:
    """Extract the journal from nested result maps exactly once per request."""
    if isinstance(value, dict):
        found = value.get("attempts")
        if isinstance(found, list) and all(isinstance(item, dict) for item in found):
            return found
        return [
            attempt for item in value.values() for attempt in collect_attempts(item)
        ]
    if isinstance(value, list):
        return [attempt for item in value for attempt in collect_attempts(item)]
    return []


def save_result(path: Path, output: dict[str, object]) -> None:
    """Atomically publish the complete artifact without replacing prior results."""
    if path.exists():
        msg = "Refusing to overwrite a previous run."
        raise FileExistsError(msg)
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        temporary.chmod(0o600)
        stream.write(json.dumps(output, ensure_ascii=True))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main() -> None:
    """Execute all registered stages and write one local-only raw result file."""
    parser = argparse.ArgumentParser()
    selector_group = parser.add_mutually_exclusive_group(required=True)
    selector_group.add_argument("--selector", type=Path)
    selector_group.add_argument("--selector-json")
    selector_group.add_argument("--records", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    django.setup()
    # Imports depend on Django app initialization.
    # ruff: ignore[import-outside-top-level]
    from django.conf import settings

    # ruff: ignore[import-outside-top-level]
    from weblate.configuration.models import Setting, SettingCategory

    if args.records is not None:
        records = json.loads(args.records.read_text(encoding="utf-8"))
        for record in records:
            if any(
                canonical_hash([record[lang]]) != record[f"{lang}_text_sha256"]
                for lang in ("ru", "en")
            ):
                msg = "Local record does not match frozen text hashes."
                raise ValueError(msg)
    else:
        records = load_records(load_selector(args.selector, args.selector_json))
    if not records or len({record["record_id"] for record in records}) != len(records):
        msg = "Records must be nonempty and unique."
        raise ValueError(msg)
    if args.validate_only:
        args.output.write_text(
            json.dumps({"validated_records": len(records), "inference": False}),
            encoding="utf-8",
        )
        return
    openrouter = Setting.objects.get_settings_dict(SettingCategory.MT)["openrouter"]
    if (settings.JUDGE_MODEL_SEAT_1, settings.JUDGE_MODEL_SEAT_2) != (
        "deepseek-v4-pro",
        "atlas/qwen3.8-max",
    ):
        msg = "Judge aliases changed from the registered profiles."
        raise ValueError(msg)
    if args.output.exists():
        msg = "Refusing to overwrite a previous run."
        raise FileExistsError(msg)
    journal = Journal(args.output.with_suffix(".events.jsonl"))
    output: dict[str, object] = {
        "generator_model": GEMINI_MODEL,
        "records": records,
        "translations": {},
        "reviews": {},
        "ratings": {},
        "attempts": [],
        "protocol": "v3",
        "max_tokens": GEMINI_MAX_TOKENS,
        "judge_models": [settings.JUDGE_MODEL_SEAT_1, settings.JUDGE_MODEL_SEAT_2],
    }
    journal.append({"kind": "start", "metadata": output})

    def gemini(task: Mapping[str, object], stage: str):
        batch = task["records"]
        arm = task["arm"]
        if not isinstance(batch, list) or not isinstance(arm, str):
            msg = "Malformed scheduled generation task."
            raise TypeError(msg)
        messages = (
            translation_messages(batch, arm)
            if stage == "generate"
            else edit_messages(batch, arm, translations["B"], task["reviews"])
        )
        expected = tuple(record["record_id"] for record in batch)

        def request() -> tuple[dict[str, object], dict[str, object]]:
            body, receipt = post_json(
                openrouter["base_url"],
                openrouter["key"],
                GEMINI_MODEL,
                messages,
                max_tokens=GEMINI_MAX_TOKENS,
                timeout=120,
            )
            try:
                return {"items": parse_items(body, "translations", expected)}, receipt
            except (TypeError, ValueError) as error:
                msg_0 = "Response contract is invalid."
                raise RequestError(msg_0, receipt) from error

        response, receipt, attempts = run_with_retry(
            request,
            label={"stage": stage, "arm": arm, "block": task["block"]},
            journal=journal,
        )
        return {**response, "receipt": receipt, "arm": arm, "attempts": attempts}

    translations: dict[str, dict[str, str]] = {arm: {} for arm in "ABCD"}
    results = run_parallel(
        scheduled_work(records, arms=("A", "B", "C", "D"), seed=RANDOMIZATION_SEED),
        lambda task: gemini(task, "generate"),
        journal=journal,
        group="generate",
    )
    for arm in "ABCD":
        arm_results = {
            key: result for key, result in results.items() if key.startswith(f"{arm}:")
        }
        output["translations"][arm] = arm_results
        for result in arm_results.values():
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
        task: Mapping[str, object],
        model: str,
        timeout: float,
        messages: list[dict[str, str]],
        key: str,
        stage: str,
    ):
        expected = tuple(record["record_id"] for record in task["records"])

        def request() -> tuple[dict[str, object], dict[str, object]]:
            body, receipt = post_json(
                judge_base, judge_key, model, messages, max_tokens=None, timeout=timeout
            )
            try:
                return {"items": parse_items(body, key, expected)}, receipt
            except (TypeError, ValueError) as error:
                msg = "Response contract is invalid."
                raise RequestError(msg, receipt) from error

        response, receipt, attempts = run_with_retry(
            request,
            label={
                "stage": stage,
                "arm": task["arm"],
                "block": task["block"],
                "seat": model,
            },
            journal=journal,
        )
        return {
            **response,
            "receipt": receipt,
            "attempts": attempts,
        }

    reviews: dict[str, dict[str, dict[str, list[dict[str, object]]]]] = {
        "E": {},
        "F": {},
    }
    review_eligible = [
        record for record in records if record["record_id"] in translations["B"]
    ]
    for arm in ("E", "F"):
        output["reviews"][arm] = {}
        reviews[arm] = {}
    for seat, model, timeout in judge_profiles:
        work = scheduled_work(
            review_eligible,
            arms=("E", "F"),
            seed=RANDOMIZATION_SEED + (1 if seat == "seat-1" else 2),
        )
        results = run_parallel(
            work,
            lambda task, current_model=model, current_timeout=timeout: judge(
                task,
                current_model,
                current_timeout,
                review_messages(task["records"], task["arm"], translations["B"]),
                "reviews",
                "review",
            ),
            journal=journal,
            group=f"review:{seat}",
        )
        for arm in ("E", "F"):
            arm_results = {
                key: result
                for key, result in results.items()
                if key.startswith(f"{arm}:")
            }
            output["reviews"][arm][seat] = arm_results
            reviews[arm][seat] = {}
            for result in arm_results.values():
                if isinstance(result, dict) and "items" in result:
                    reviews[arm][seat].update(
                        {
                            record_id: item.get("issues", [])
                            for record_id, item in result["items"].items()
                            if isinstance(item.get("issues", []), list)
                        }
                    )

    editor_eligible = [
        record
        for record in records
        if record["record_id"] in translations["B"]
        and all(
            record["record_id"] in reviews[arm][seat]
            for arm in ("E", "F")
            for seat, _model, _timeout in judge_profiles
        )
    ]
    editor_reviews = {
        arm: {
            record["record_id"]: [
                {"seat": seat, "issues": reviews[arm][seat][record["record_id"]]}
                for seat, _model, _timeout in judge_profiles
            ]
            for record in editor_eligible
        }
        for arm in ("E", "F")
    }
    work = scheduled_work(editor_eligible, arms=("E", "F"), seed=RANDOMIZATION_SEED + 3)
    for task in work:
        task["reviews"] = editor_reviews[task["arm"]]
    results = run_parallel(
        work, lambda task: gemini(task, "edit"), journal=journal, group="edit"
    )
    for arm in ("E", "F"):
        arm_results = {
            key: result for key, result in results.items() if key.startswith(f"{arm}:")
        }
        output["translations"][arm] = arm_results
        translations[arm] = {}
        for result in arm_results.values():
            if isinstance(result, dict) and "items" in result:
                translations[arm].update(
                    {
                        record_id: item["translation"]
                        for record_id, item in result["items"].items()
                        if isinstance(item.get("translation"), str)
                    }
                )

    for arm in "ABCDEF":
        eligible = [
            record for record in records if record["record_id"] in translations[arm]
        ]
        output["ratings"][arm] = {}
        for seat, model, timeout in judge_profiles:
            work = scheduled_work(
                eligible,
                arms=(arm,),
                seed=RANDOMIZATION_SEED + 10 + (1 if seat == "seat-1" else 2),
            )
            results = run_parallel(
                work,
                lambda task, current_model=model, current_timeout=timeout, current_arm=arm: (
                    judge(
                        task,
                        current_model,
                        current_timeout,
                        evaluation_messages(task["records"], translations[current_arm]),
                        "ratings",
                        "rating",
                    )
                ),
                journal=journal,
                group=f"rating:{arm}:{seat}",
            )
            output["ratings"][arm][seat] = results

    output["attempts"] = collect_attempts(
        {
            "translations": output["translations"],
            "reviews": output["reviews"],
            "ratings": output["ratings"],
        }
    )
    save_result(args.output, output)
    journal.append(
        {
            "kind": "complete",
            "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        }
    )


if __name__ == "__main__":
    main()
