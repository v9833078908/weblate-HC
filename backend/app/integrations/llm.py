# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.db import DomainError
from app.schemas import TranslationProfile
from app.settings import read_secret_file


def profile_headers(settings, token):
    headers = {"Authorization": "Bearer " + token}
    if settings.llm_url == "https://openrouter.ai/api/v1":
        title = settings.llm_app_title
        referer = settings.llm_http_referer or settings.public_origin
        parsed = urlsplit(referer)
        if (
            not title.strip()
            or len(title) > 200
            or not title.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in title + referer)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError("llm_config", "Invalid profile service attribution")
        headers.update({"X-OpenRouter-Title": title, "HTTP-Referer": referer})
    return headers


def generate_profile_from_input(settings, input):
    from app.services.profile_agent import load_profile_skills

    if settings.llm_url not in {
        "https://openrouter.ai/api/v1",
        "https://hcbifrost.herocraft.com/litellm/v1",
    }:
        raise DomainError("llm_config", "Unsupported profile service endpoint")
    if (
        settings.llm_reasoning_effort not in {"none", "low", "medium", "high"}
        or not 2048 <= settings.llm_max_completion_tokens <= 32768
    ):
        raise DomainError("llm_config", "Invalid reasoning or completion budget")
    targets = input["target_languages"]
    schema = TranslationProfile.model_json_schema()
    schema["required"] = ["persona", "style", "language_instructions"]
    schema["properties"]["language_instructions"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            code: {"type": "string", "minLength": 1, "maxLength": 1000} for code in targets
        },
        "required": targets,
    }
    # Server configuration and provenance stay out of the model's data section.
    data = {
        key: input[key]
        for key in (
            "project_name",
            "game_title",
            "source_language",
            "target_languages",
            "answers",
            "facts",
            "locale_rules",
            "locale_guides",
            "evidence",
            "protected_tokens",
        )
    }
    payload = {
        "model": settings.llm_model,
        "stream": False,
        "max_completion_tokens": settings.llm_max_completion_tokens,
        "messages": [
            {"role": "system", "content": load_profile_skills()[0]},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "game_profile", "strict": True, "schema": schema},
        },
    }
    if "openrouter.ai" in settings.llm_url:
        payload.update(
            provider={"require_parameters": True},
            reasoning={"effort": settings.llm_reasoning_effort, "exclude": True},
        )
    else:
        payload["reasoning_effort"] = settings.llm_reasoning_effort
    try:
        token = read_secret_file(settings.llm_token_file)
    except (OSError, ValueError) as error:
        raise DomainError(
            "profile_agent_not_configured", "Profile model key unavailable"
        ) from error
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response = client.post(
                settings.llm_url + "/chat/completions",
                json=payload,
                headers=profile_headers(settings, token),
            )
        if response.status_code != 200:
            raise DomainError("llm_failed", f"Profile service returned HTTP {response.status_code}")
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise DomainError("profile_instructions", "Profile response incomplete or refused")
        return {
            "generated": json.loads(choice["message"]["content"]),
            "metadata": {
                "model": str(body.get("model") or settings.llm_model)[:200],
                "usage": {
                    k: v
                    for k, v in body.get("usage", {}).items()
                    if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and isinstance(v, int)
                },
            },
        }
    except httpx.HTTPError as error:
        raise DomainError(
            "llm_unknown", "Profile request outcome unknown; automatic retry disabled"
        ) from error
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise DomainError(
            "profile_instructions", "Profile service returned invalid JSON"
        ) from error


def generate_profile(settings, answers, evidence, targets, locale_rules):
    if settings.llm_url not in {
        "https://openrouter.ai/api/v1",
        "https://hcbifrost.herocraft.com/litellm/v1",
    }:
        raise DomainError("llm_config", "Unsupported profile service endpoint")
    schema = TranslationProfile.model_json_schema()
    schema["required"] = ["persona", "style", "language_instructions"]
    schema["properties"]["language_instructions"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            code: {"type": "string", "minLength": 1, "maxLength": 1000} for code in targets
        },
        "required": list(targets),
    }
    payload = {
        "model": settings.llm_model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": Path(__file__).with_name("profile.txt").read_text(encoding="utf-8"),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "answers": answers,
                        "text_evidence": evidence,
                        "target_languages": targets,
                        "locale_rules": locale_rules,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "producer_project_profile",
                "strict": True,
                "schema": schema,
            },
        },
    }
    if "openrouter.ai" in settings.llm_url:
        payload["provider"] = {"require_parameters": True}
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response = client.post(
                settings.llm_url + "/chat/completions",
                json=payload,
                headers=profile_headers(settings, read_secret_file(settings.llm_token_file)),
            )
        if response.status_code != 200:
            raise DomainError("llm_failed", f"Profile service returned HTTP {response.status_code}")
        return json.loads(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, httpx.HTTPError, OSError) as error:
        raise DomainError(
            "llm_failed", "Profile generation outcome is unknown; review before retrying"
        ) from error
