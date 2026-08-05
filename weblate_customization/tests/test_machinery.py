# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Routed LLM machinery."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from weblate_customization.machinery import (
    RoutedLLMMachineryForm,
    RoutedLLMTranslation,
)

from weblate.machinery.base import MachineTranslationError
from weblate.utils.tests import http_mock

if TYPE_CHECKING:
    from weblate.machinery.types import SettingsDict

DEEPSEEK = "deepseek/deepseek-chat-v3.1"
GEMINI = "google/gemini-2.5-flash"

CONFIGURATION: dict[str, object] = {
    "key": "test-key",
    "base_url": "https://openrouter.ai/api/v1",
    "routing": {"ja": DEEPSEEK, "ko": DEEPSEEK, "zh": DEEPSEEK, "*": GEMINI},
    "persona": "",
    "style": "",
    "language_instructions": {},
}


CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def mock_chat(content: str = '["テスト"]') -> None:
    http_mock.register(
        "POST",
        CHAT_URL,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 2,
                "total_tokens": 11,
            },
        },
    )


def sent_payloads() -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for call in http_mock.calls:
        if str(call.request.url) != CHAT_URL:
            continue
        payload = json.loads(call.request.content)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def sent_models() -> list[str]:
    return [
        model
        for payload in sent_payloads()
        if isinstance(model := payload.get("model"), str)
    ]


def as_settings(value: dict[str, object]) -> SettingsDict:
    return cast("SettingsDict", value)


class FormOnlyMachinery:
    """Avoid network validation while testing only form field cleaning."""

    def __init__(self, configuration: SettingsDict) -> None:
        self.settings = configuration

    def validate_settings(self) -> None:
        pass


class RoutedLLMFormTest(TestCase):
    def validate(self, routing: object) -> RoutedLLMMachineryForm:
        return RoutedLLMMachineryForm(
            FormOnlyMachinery,
            data={**CONFIGURATION, "routing": routing},
        )

    def test_model_field_is_removed(self) -> None:
        form = RoutedLLMMachineryForm(FormOnlyMachinery)
        self.assertNotIn("model", form.fields)

    def test_valid_routing(self) -> None:
        form = self.validate(CONFIGURATION["routing"])
        self.assertTrue(form.is_valid(), form.errors)

    def test_valid_without_fallback(self) -> None:
        form = self.validate({"ja": DEEPSEEK})
        self.assertTrue(form.is_valid(), form.errors)

    def test_star_only(self) -> None:
        form = self.validate({"*": GEMINI})
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_empty(self) -> None:
        self.assertFalse(self.validate({}).is_valid())

    def test_rejects_non_dict(self) -> None:
        self.assertFalse(self.validate(["ja", DEEPSEEK]).is_valid())

    def test_rejects_bad_language(self) -> None:
        self.assertFalse(self.validate({"nosuchlang": DEEPSEEK}).is_valid())

    def test_rejects_empty_model(self) -> None:
        self.assertFalse(self.validate({"ja": ""}).is_valid())

    def test_rejects_normalized_collision(self) -> None:
        form = self.validate({"pt_BR": DEEPSEEK, "pt-BR": GEMINI})
        self.assertFalse(form.is_valid())
        self.assertIn("conflicting", str(form.errors["routing"]))

    def test_trims_model(self) -> None:
        form = self.validate({"ja": f"  {DEEPSEEK}  "})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["routing"], {"ja": DEEPSEEK})


class RoutedResolveTest(SimpleTestCase):
    def machine(self, routing: dict[str, str] | None = None) -> RoutedLLMTranslation:
        configuration = dict(CONFIGURATION)
        if routing is not None:
            configuration["routing"] = routing
        return RoutedLLMTranslation(as_settings(configuration))

    def test_exact(self) -> None:
        self.assertEqual(self.machine().resolve_model("ja"), DEEPSEEK)

    def test_base_code(self) -> None:
        self.assertEqual(self.machine().resolve_model("zh_Hans"), DEEPSEEK)
        self.assertEqual(self.machine().resolve_model("zh-TW"), DEEPSEEK)

    def test_separator_and_case_insensitive(self) -> None:
        machine = self.machine({"pt_BR": DEEPSEEK, "*": GEMINI})
        self.assertEqual(machine.resolve_model("PT-br"), DEEPSEEK)

    def test_fallback(self) -> None:
        self.assertEqual(self.machine().resolve_model("fr"), GEMINI)

    def test_no_route_no_fallback(self) -> None:
        machine = self.machine({"ja": DEEPSEEK})
        self.assertIsNone(machine.resolve_model("fr"))

    def test_is_supported_follows_routing(self) -> None:
        machine = self.machine({"ja": DEEPSEEK})
        self.assertTrue(machine.is_supported("en", "ja"))
        self.assertFalse(machine.is_supported("en", "fr"))


class RoutedSettingsValidationTest(TestCase):
    @http_mock.activate
    def test_actual_form_validates_route_without_fallback(self) -> None:
        mock_chat()
        form = RoutedLLMMachineryForm(
            RoutedLLMTranslation,
            data={**CONFIGURATION, "routing": {"ja": DEEPSEEK}},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(sent_models(), [DEEPSEEK])

    @http_mock.activate
    def test_actual_form_validates_star_only_route(self) -> None:
        mock_chat()
        form = RoutedLLMMachineryForm(
            RoutedLLMTranslation,
            data={**CONFIGURATION, "routing": {"*": GEMINI}},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(sent_models(), [GEMINI])


class RoutedDownloadTest(TestCase):
    def machine(self, routing: dict[str, str] | None = None) -> RoutedLLMTranslation:
        configuration = dict(CONFIGURATION)
        if routing is not None:
            configuration["routing"] = routing
        machine = RoutedLLMTranslation(as_settings(configuration))
        machine.delete_cache()
        machine.cache_translations = False
        return machine

    @http_mock.activate
    def test_sync_multiple_routes_cjk(self) -> None:
        mock_chat()
        result = self.machine().download_multiple_translations(
            "en", "ja", [("Hello", None)]
        )

        self.assertTrue(result)
        self.assertEqual(sent_models(), [DEEPSEEK])

    @http_mock.activate
    def test_sync_pending_routes_fallback(self) -> None:
        mock_chat('["Bonjour"]')
        result = self.machine().download_pending_translations(
            "en", "fr", [("Hello", None, 0)]
        )

        self.assertTrue(result)
        self.assertEqual(sent_models(), [GEMINI])

    @http_mock.activate
    def test_async_multiple_routes_cjk(self) -> None:
        mock_chat()
        result = async_to_sync(self.machine().adownload_multiple_translations)(
            "en", "zh_Hans", [("Hello", None)]
        )

        self.assertTrue(result)
        self.assertEqual(sent_models(), [DEEPSEEK])

    @http_mock.activate
    def test_async_pending_routes_cjk(self) -> None:
        mock_chat()
        result = async_to_sync(self.machine().adownload_pending_translations)(
            "en", "ja", [("Hello", None, 0)]
        )

        self.assertTrue(result)
        self.assertEqual(sent_models(), [DEEPSEEK])

    @http_mock.activate
    def test_no_models_endpoint_call(self) -> None:
        mock_chat()
        self.machine().download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertTrue(
            all("/models" not in str(call.request.url) for call in http_mock.calls)
        )

    @http_mock.activate
    def test_unsupported_language_fails_before_http(self) -> None:
        machine = self.machine({"ja": DEEPSEEK})

        with self.assertRaisesRegex(
            MachineTranslationError, "No routed model for target language: fr"
        ):
            machine.download_multiple_translations("en", "fr", [("Hello", None)])

        self.assertEqual(http_mock.calls, [])

    @http_mock.activate
    def test_route_context_is_reset_after_success(self) -> None:
        mock_chat()
        machine = self.machine()

        machine.download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertEqual(machine.get_model(), GEMINI)

    @http_mock.activate
    def test_route_context_is_reset_after_parser_error(self) -> None:
        mock_chat("not JSON")
        machine = self.machine()

        with self.assertRaises(MachineTranslationError):
            machine.download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertEqual(machine.get_model(), GEMINI)
