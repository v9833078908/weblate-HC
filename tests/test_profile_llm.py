# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json

import httpx
import pytest
from app.db import DomainError
from app.integrations.llm import generate_profile, generate_profile_from_input
from test_profile_agent import make_input
from test_profile_context import context_client as context_client
from test_profile_jobs import configured


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_controlled_model_payload_and_incomplete_response(
    context_client, monkeypatch, tmp_path, finish
):
    _, store, _, _, _, _ = context_client
    key = tmp_path / "key"
    key.write_text("private-test-key")
    settings = configured(store)
    settings.llm_token_file = str(key)
    captured = []
    real_client = httpx.Client

    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": settings.llm_model,
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {
                            "content": json.dumps(
                                {
                                    "persona": "a",
                                    "style": "a",
                                    "language_instructions": {"zh_Hant": "a"},
                                }
                            )
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 5,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs),
    )
    if finish == "length":
        with pytest.raises(DomainError, match="incomplete"):
            generate_profile_from_input(settings, make_input(context_client))
    else:
        result = generate_profile_from_input(settings, make_input(context_client))
        assert result["metadata"]["usage"] == {"prompt_tokens": 4, "completion_tokens": 5}
    payload = captured[0]
    assert payload["model"] == "openai/gpt-5.4-mini"
    assert payload["reasoning"] == {"effort": "low", "exclude": True}
    assert payload["max_completion_tokens"] == 16384
    assert payload["provider"]["require_parameters"]
    assert payload["response_format"]["json_schema"]["strict"]
    assert "PRIVATE TARGET" not in str(payload)
    assert "tools" not in payload and "private-test-key" not in str(payload)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("openrouter", [False, True])
def test_profile_service_attribution_and_luna_model(
    context_client, monkeypatch, tmp_path, legacy, openrouter
):
    _, store, _, _, _, _ = context_client
    settings = configured(store)
    key = tmp_path / "key"
    key.write_text("private-test-key")
    settings.llm_token_file = str(key)
    settings.llm_model = "openai/gpt-6-luna"
    settings.public_origin = "http://192.168.0.180:18511"
    if not openrouter:
        settings.llm_url = "https://hcbifrost.herocraft.com/litellm/v1"
    captured = []
    real_client = httpx.Client

    def transport(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "persona": "role",
                                    "style": "style",
                                    "language_instructions": {"zh_Hant": "rules"},
                                }
                            )
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs),
    )
    input = make_input(context_client)
    if legacy:
        generate_profile(
            settings,
            input["answers"],
            input["evidence"],
            input["target_languages"],
            input["locale_rules"],
        )
    else:
        generate_profile_from_input(settings, input)
    request = captured[0]
    payload = json.loads(request.content)
    assert payload["model"] == "openai/gpt-6-luna"
    assert request.headers["Authorization"] == "Bearer private-test-key"
    if openrouter:
        assert (
            request.headers["X-OpenRouter-Title"] == "HCGameLoc Wizard - Translation Profile Agent"
        )
        assert request.headers["HTTP-Referer"] == "http://192.168.0.180:18511"
    else:
        assert "X-OpenRouter-Title" not in request.headers
        assert "HTTP-Referer" not in request.headers
    assert "private-test-key" not in str(payload)


def test_attribution_is_configurable_and_rejects_header_injection(context_client, tmp_path):
    from app.integrations.llm import profile_headers

    _, store, _, _, _, _ = context_client
    settings = configured(store)
    settings.llm_app_title = "HCGameLoc Staging - Profile Agent"
    settings.llm_http_referer = "https://wizard.example.org/console"
    assert profile_headers(settings, "test")["X-OpenRouter-Title"] == settings.llm_app_title
    assert profile_headers(settings, "test")["HTTP-Referer"] == settings.llm_http_referer
    settings.llm_app_title = "Title\r\nInjected: value"
    with pytest.raises(DomainError, match="attribution"):
        profile_headers(settings, "test")
