# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Routed LLM machinery."""

from __future__ import annotations

from typing import cast

from django.test import SimpleTestCase, TestCase

from weblate.machinery.types import SettingsDict
from weblate_customization.machinery import (
    RoutedLLMMachineryForm,
    RoutedLLMTranslation,
)

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
    def machine(
        self, routing: dict[str, str] | None = None
    ) -> RoutedLLMTranslation:
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
