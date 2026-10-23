# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Routed LLM machinery."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import httpx2
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from weblate_customization.checks import GameMarkupCheck
from weblate_customization.machinery import (
    LITELLM_DEFAULT_BASE_URL,
    OPENROUTER_MACHINE_TRANSLATION_TITLE,
    RoutedLiteLLMTranslation,
    RoutedLLMMachineryForm,
    RoutedLLMTranslation,
)

from weblate.checks import models as check_models
from weblate.machinery.base import MachineTranslationError
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tests.factories import make_unit
from weblate.utils.tests import http_mock

if TYPE_CHECKING:
    from weblate.machinery.types import SettingsDict
    from weblate.trans.models import Unit

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


LITELLM_CONFIGURATION: dict[str, object] = {
    "key": "test-key",
    "base_url": "https://hcbifrost.herocraft.com/litellm/v1",
    "routing": {"ja": DEEPSEEK, "ko": DEEPSEEK, "zh": DEEPSEEK, "*": GEMINI},
    "persona": "",
    "style": "",
    "language_instructions": {},
}


CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def mock_chat(text: str = "テスト", *, invalid_reply: str | None = None) -> None:
    register_chat(CHAT_URL, text, invalid_reply=invalid_reply)


def register_chat(url: str, text: str, *, invalid_reply: str | None = None) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(json.loads(request.content)["messages"][-1]["content"])[
            "strings"
        ]
        content = (
            invalid_reply
            if invalid_reply is not None
            else json.dumps(
                {
                    "translations": {
                        item["id"]: {"text": [text], "placeholders": {}}
                        for item in inputs
                    }
                }
            )
        )
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 2,
                    "total_tokens": 11,
                },
            },
        )

    http_mock.register_callback("POST", url, callback=respond)


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


LITELLM_CHAT_URL = "https://hcbifrost.herocraft.com/litellm/v1/chat/completions"


def mock_litellm_chat(text: str = "テスト") -> None:
    register_chat(LITELLM_CHAT_URL, text)


def litellm_sent_models() -> list[str]:
    models: list[str] = []
    for call in http_mock.calls:
        if str(call.request.url) != LITELLM_CHAT_URL:
            continue
        payload = json.loads(call.request.content)
        if isinstance(payload, dict) and isinstance(model := payload.get("model"), str):
            models.append(model)
    return models


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

    def test_openrouter_request_has_service_title(self) -> None:
        self.assertEqual(
            self.machine().get_headers()["X-OpenRouter-Title"],
            OPENROUTER_MACHINE_TRANSLATION_TITLE,
        )

    def test_non_openrouter_request_omits_service_title(self) -> None:
        machine = RoutedLiteLLMTranslation(as_settings(dict(LITELLM_CONFIGURATION)))
        self.assertNotIn("X-OpenRouter-Title", machine.get_headers())


def batch_content(count: int) -> str:
    return json.dumps(
        {
            "strings": [
                {
                    "id": f"s{i}",
                    "source": f"source{i}",
                    "parts": [{"type": "text", "text": f"source{i}"}],
                }
                for i in range(count)
            ]
        }
    )


class RoutedProtectedSlotsTest(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(
            patch.dict(check_models.CHECKS.data, {"game-markup": GameMarkupCheck()})
        )

    def prepare(
        self,
        source: str = 'Tap <sprite name="fire">',
        *,
        flags: str = "",
        litellm: bool = False,
    ) -> tuple[RoutedLLMTranslation, Unit, str, dict[str, str], dict]:
        machine = (
            RoutedLiteLLMTranslation(as_settings(dict(LITELLM_CONFIGURATION)))
            if litellm
            else RoutedLLMTranslation(as_settings(dict(CONFIGURATION)))
        )
        unit = make_unit(source=source, flags=flags, code="ko")
        cleaned, replacements = machine.cleanup_text(source, unit)
        # ruff: ignore[private-member-access]
        parts = machine._get_string_parts(cleaned, unit)
        content = json.dumps(
            {"strings": [{"id": "s0", "source": cleaned, "parts": parts}]}
        )
        schema = machine.get_chat_payload(GEMINI, "prompt", content, "", "")[
            "response_format"
        ]["json_schema"]["schema"]
        return machine, unit, cleaned, replacements, schema

    def parse(
        self,
        reply: dict | str,
        prepared: tuple[RoutedLLMTranslation, Unit, str, dict[str, str], dict],
    ) -> str:
        machine, unit, source, replacements, _schema = prepared
        # ruff: ignore[private-member-access]
        result = machine._parse_llm_translations(
            reply if isinstance(reply, str) else json.dumps(reply),
            [(source, unit)],
            None,
            string_ids=["s0"],
        )
        return machine.uncleanup_text(replacements, result[source][0]["text"])

    def test_live_all_text_omission_is_rejected_by_provider_schema(self) -> None:
        reply = {"translations": {"s0": {"text": ["누르세요"], "placeholders": {}}}}
        for litellm in (False, True):
            with (
                self.subTest(litellm=litellm),
                self.assertRaises(JSONSchemaValidationError),
            ):
                validate(reply, self.prepare(litellm=litellm)[4])

    def test_sprite_is_restored_from_source_without_model_owned_metadata(self) -> None:
        reply = {
            "translations": {
                "s0": {"text": ["点击 ", ""], "placeholders": {"p0": {"position": 1}}}
            }
        }
        for litellm in (False, True):
            with self.subTest(litellm=litellm):
                prepared = self.prepare(litellm=litellm)
                validate(reply, prepared[4])
                self.assertEqual(
                    self.parse(reply, prepared), '点击 <sprite name="fire">'
                )

    def test_missing_or_unknown_occurrences_are_refused_without_tag_insertion(
        self,
    ) -> None:
        prepared = self.prepare()
        for placeholders in (
            {},
            {"p1": {"position": 1}},
            {"p0": {"position": 1}, "p1": {"position": 1}},
        ):
            reply = {
                "translations": {
                    "s0": {"text": ["点击 ", ""], "placeholders": placeholders}
                }
            }
            with self.subTest(placeholders=placeholders):
                with self.assertRaises(JSONSchemaValidationError):
                    validate(reply, prepared[4])
                with self.assertRaises(MachineTranslationError):
                    self.parse(reply, prepared)

    def test_positions_are_bounded_integers_without_coercion(self) -> None:
        prepared = self.prepare()
        for position in (-1, 2, True, False, "1", 0.5, float("inf"), float("nan")):
            reply = {
                "translations": {
                    "s0": {
                        "text": ["点击 ", ""],
                        "placeholders": {"p0": {"position": position}},
                    }
                }
            }
            with (
                self.subTest(position=position),
                self.assertRaises(MachineTranslationError),
            ):
                self.parse(reply, prepared)

    def test_wrong_identity_and_text_arity_are_refused(self) -> None:
        prepared = self.prepare()
        item = {"text": ["点击 ", ""], "placeholders": {"p0": {"position": 1}}}
        for reply in (
            {"translations": {"wrong": item}},
            {"translations": {"s0": item, "extra": item}},
            {
                "translations": {
                    "s0": {"text": ["点击 "], "placeholders": {"p0": {"position": 1}}}
                }
            },
            {
                "translations": {
                    "s0": {
                        "text": ["点击 ", "", ""],
                        "placeholders": {"p0": {"position": 1}},
                    }
                }
            },
            {
                "translations": {
                    "s0": {
                        "text": ["点击 ", ""],
                        "placeholders": {"p0": {"position": 1, "id": "invented"}},
                    }
                }
            },
        ):
            with self.subTest(reply=reply), self.assertRaises(MachineTranslationError):
                self.parse(reply, prepared)

    def test_duplicate_json_members_are_refused(self) -> None:
        prepared = self.prepare()
        body = '{"text":["点击 ",""],"placeholders":{"p0":{"position":1}}}'
        replies = (
            '{"translations":{"s0":' + body + '},"translations":{"s0":' + body + "}}",
            '{"translations":{"s0":' + body + ',"s0":' + body + "}}",
            (
                '{"translations":{"s0":{"text":["点击 ",""],"text":["","点击 "],'
                '"placeholders":{"p0":{"position":1}}}}}'
            ),
            (
                '{"translations":{"s0":{"text":["点击 ",""],'
                '"placeholders":{"p0":{"position":1}},'
                '"placeholders":{"p0":{"position":1}}}}}'
            ),
            (
                '{"translations":{"s0":{"text":["点击 ",""],'
                '"placeholders":{"p0":{"position":1},"p0":{"position":0}}}}}'
            ),
            (
                '{"translations":{"s0":{"text":["点击 ",""],'
                '"placeholders":{"p0":{"position":1,"position":0}}}}}'
            ),
        )
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises(MachineTranslationError):
                self.parse(reply, prepared)

    def test_raw_inline_sprite_cannot_duplicate_a_mandatory_occurrence(self) -> None:
        prepared = self.prepare()
        reply = {
            "translations": {
                "s0": {
                    "text": ['点击 <sprite name="fire">', ""],
                    "placeholders": {"p0": {"position": 1}},
                }
            }
        }
        with self.assertRaises(MachineTranslationError):
            self.parse(reply, prepared)

    def test_grammar_occurrences_can_reverse_without_changing_identifiers(self) -> None:
        prepared = self.prepare(
            "{start} / {end}", flags="icu-message-format, ignore-game-markup"
        )
        reply = {
            "translations": {
                "s0": {
                    "text": [" / ", "", ""],
                    "placeholders": {"p0": {"position": 1}, "p1": {"position": 0}},
                }
            }
        }
        self.assertEqual(self.parse(reply, prepared), "{end} / {start}")

    def test_repeated_occurrences_do_not_collapse(self) -> None:
        prepared = self.prepare(
            "{name} / {name}", flags="icu-message-format, ignore-game-markup"
        )
        reply = {
            "translations": {
                "s0": {
                    "text": [" / ", "", ""],
                    "placeholders": {"p0": {"position": 0}, "p1": {"position": 1}},
                }
            }
        }
        self.assertEqual(self.parse(reply, prepared), "{name} / {name}")

    def test_reversed_markup_is_still_refused(self) -> None:
        prepared = self.prepare('<sprite name="fire"> / <sprite name="water">')
        reply = {
            "translations": {
                "s0": {
                    "text": [" / ", "", ""],
                    "placeholders": {"p0": {"position": 1}, "p1": {"position": 0}},
                }
            }
        }
        with self.assertRaises(MachineTranslationError):
            self.parse(reply, prepared)

    def test_translatable_wrapper_keeps_its_exact_delimiters(self) -> None:
        prepared = self.prepare("<b>Tap</b> now", flags="xml-text")
        reply = {
            "translations": {
                "s0": {
                    "text": ["", " 现在"],
                    "placeholders": {"p0": {"position": 0, "text": "点击"}},
                }
            }
        }
        self.assertEqual(self.parse(reply, prepared), "<b>点击</b> 现在")

    def test_plain_text_batch_pairs_by_id_not_object_order(self) -> None:
        machine = RoutedLLMTranslation(as_settings(dict(CONFIGURATION)))
        reply = {
            "translations": {
                "b": {"text": ["Deux"], "placeholders": {}},
                "a": {"text": ["Un"], "placeholders": {}},
            }
        }
        # ruff: ignore[private-member-access]
        result = machine._parse_llm_translations(
            json.dumps(reply),
            [("One", None), ("Two", None)],
            None,
            string_ids=["a", "b"],
        )
        self.assertEqual(result["One"][0]["text"], "Un")
        self.assertEqual(result["Two"][0]["text"], "Deux")

    def test_missing_text_slots_do_not_fall_back_to_legacy_array(self) -> None:
        for reply in ('["点击"]', '{"translations":{"s0":{}}}', '{"translations":'):
            with self.subTest(reply=reply), self.assertRaises(MachineTranslationError):
                self.parse(reply, self.prepare())

    def test_closed_objects_and_string_slots_are_validated_locally(self) -> None:
        prepared = self.prepare()
        item = {"text": ["点击 ", ""], "placeholders": {"p0": {"position": 1}}}
        for reply in (
            None,
            [],
            {"translations": None},
            {"translations": []},
            {"translations": {"s0": item}, "extra": True},
            {"translations": {"s0": {**item, "extra": True}}},
            {"translations": {"s0": {**item, "text": "点击"}}},
            {"translations": {"s0": {**item, "text": [None, ""]}}},
            {"translations": {"s0": {**item, "text": [1, ""]}}},
            {"translations": {"s0": {**item, "placeholders": []}}},
            {"translations": {"s0": {**item, "placeholders": {"p0": None}}}},
        ):
            with self.subTest(reply=reply), self.assertRaises(MachineTranslationError):
                self.parse(json.dumps(reply), prepared)

    def test_adjacent_and_endpoint_markers_allow_empty_text_slots(self) -> None:
        source = '<sprite name="fire"><sprite name="water">'
        for positions in ((0, 0), (0, 2), (2, 2)):
            reply = {
                "translations": {
                    "s0": {
                        "text": ["", "", ""],
                        "placeholders": {
                            "p0": {"position": positions[0]},
                            "p1": {"position": positions[1]},
                        },
                    }
                }
            }
            with self.subTest(positions=positions):
                prepared = self.prepare(source)
                validate(reply, prepared[4])
                self.assertEqual(self.parse(reply, prepared), source)

    def test_equal_position_retains_source_order_not_reply_key_order(self) -> None:
        source = '<sprite name="fire"><sprite name="water"> ready'
        prepared = self.prepare(source)
        reply = {
            "translations": {
                "s0": {
                    "text": ["", "", " 就绪"],
                    "placeholders": {"p1": {"position": 2}, "p0": {"position": 2}},
                }
            }
        }
        self.assertEqual(
            self.parse(reply, prepared),
            '<sprite name="fire"><sprite name="water"> 就绪',
        )

    def test_markup_position_cannot_move_text_across_its_boundary(self) -> None:
        prepared = self.prepare('Tap <sprite name="fire">')
        reply = {
            "translations": {
                "s0": {"text": ["", "点击"], "placeholders": {"p0": {"position": 0}}}
            }
        }
        # Legal positions in the provider schema still obey the base validator.
        validate(reply, prepared[4])
        with self.assertRaises(MachineTranslationError):
            self.parse(reply, prepared)

    def test_syntax_position_allows_text_to_move_across_it(self) -> None:
        for source, text, position, expected in (
            ("Rank {0}", ["", " 位"], 0, "{0} 位"),
            ("{0} place", ["第 ", ""], 1, "第 {0}"),
        ):
            reply = {
                "translations": {
                    "s0": {
                        "text": text,
                        "placeholders": {"p0": {"position": position}},
                    }
                }
            }
            with self.subTest(source=source):
                self.assertEqual(self.parse(reply, self.prepare(source)), expected)

    def test_grammar_cannot_cross_a_markup_boundary(self) -> None:
        prepared = self.prepare(
            "{name}<br/>",
            flags="icu-message-format, ignore-game-markup, xml-text",
        )
        reply = {
            "translations": {
                "s0": {
                    "text": ["", "", ""],
                    "placeholders": {"p0": {"position": 2}, "p1": {"position": 1}},
                }
            }
        }
        in_source_order = {
            "translations": {
                "s0": {
                    "text": ["", "", ""],
                    "placeholders": {"p0": {"position": 0}, "p1": {"position": 1}},
                }
            }
        }
        validate(in_source_order, prepared[4])
        self.assertEqual(self.parse(in_source_order, prepared), "{name}<br/>")
        with self.assertRaises(MachineTranslationError):
            self.parse(reply, prepared)

    def test_wrapper_empty_forbidden_and_non_string_content_is_refused(self) -> None:
        prepared = self.prepare("<b>Tap</b> now", flags="xml-text")
        for content in ("", "点击 <", '点击 <sprite name="fire">', None, 3):
            reply = {
                "translations": {
                    "s0": {
                        "text": ["", " 现在"],
                        "placeholders": {"p0": {"position": 0, "text": content}},
                    }
                }
            }
            with (
                self.subTest(content=content),
                self.assertRaises(MachineTranslationError),
            ):
                self.parse(reply, prepared)

    def test_wrapper_text_is_required_and_metadata_remains_source_owned(self) -> None:
        prepared = self.prepare("<b>Tap</b> now", flags="xml-text")
        for placement in (
            {"position": 0},
            {"position": 0, "text": "点击", "close_id": "@@PH99@@"},
            {"position": 0, "text": "点击", "translatable": False},
        ):
            reply = {
                "translations": {
                    "s0": {"text": ["", " 现在"], "placeholders": {"p0": placement}}
                }
            }
            with self.subTest(placement=placement):
                with self.assertRaises(JSONSchemaValidationError):
                    validate(reply, prepared[4])
                with self.assertRaises(MachineTranslationError):
                    self.parse(reply, prepared)

    def test_examples_roundtrip_plain_text_and_repeated_token_occurrences(self) -> None:
        machine = RoutedLLMTranslation(as_settings(dict(CONFIGURATION)))
        examples = [
            {"source": "Hello", "target": "Bonjour"},
            {
                "source": "@@PH0@@ / @@PH0@@",
                "target": "@@PH0@@ et @@PH0@@",
            },
        ]
        # ruff: ignore[private-member-access]
        content, response = machine._build_previous_messages_from_examples(
            "en", "fr", examples
        )
        strings = json.loads(content)["strings"]
        schema = machine.get_chat_payload(GEMINI, "prompt", content, "", "")[
            "response_format"
        ]["json_schema"]["schema"]
        validate(json.loads(response), schema)
        # ruff: ignore[private-member-access]
        result = machine._parse_llm_translations(
            response,
            [(example["source"], None) for example in examples],
            None,
            string_ids=[item["id"] for item in strings],
        )
        self.assertEqual(result["Hello"][0]["text"], "Bonjour")
        self.assertEqual(result["@@PH0@@ / @@PH0@@"][0]["text"], "@@PH0@@ et @@PH0@@")

    def test_example_matches_reordered_tokens_by_identity_not_target_index(
        self,
    ) -> None:
        prepared = self.prepare(
            "{start} / {end} / {start}",
            flags="icu-message-format, ignore-game-markup",
        )
        machine, unit, source, replacements, _schema = prepared
        # ruff: ignore[private-member-access]
        content, response = machine._build_previous_messages_from_examples(
            "en",
            "ko",
            [{"source": source, "target": "@@PH10@@ / @@PH0@@ / @@PH18@@"}],
        )
        string_id = json.loads(content)["strings"][0]["id"]
        schema = machine.get_chat_payload(GEMINI, "prompt", content, "", "")[
            "response_format"
        ]["json_schema"]["schema"]
        validate(json.loads(response), schema)
        # ruff: ignore[private-member-access]
        result = machine._parse_llm_translations(
            response, [(source, unit)], None, string_ids=[string_id]
        )
        self.assertEqual(
            machine.uncleanup_text(replacements, result[source][0]["text"]),
            "{end} / {start} / {start}",
        )

    def test_explicit_plural_occurrence_preserves_selected_source_boundaries(
        self,
    ) -> None:
        machine = RoutedLLMTranslation(as_settings(dict(CONFIGURATION)))
        unit = make_unit(source=["{0} file", '<sprite name="fire"> file'], code="fr")
        first, _replacements = machine.cleanup_text(unit.get_source_plurals()[0], unit)
        second, _replacements = machine.cleanup_text(unit.get_source_plurals()[1], unit)
        self.assertEqual(first, "@@PH0@@ file")
        self.assertEqual(second, first)
        reply = json.dumps(
            {
                "translations": {
                    "s0": {
                        "text": ["Fichier ", ""],
                        "placeholders": {"p0": {"position": 1}},
                    }
                }
            }
        )
        # The syntax variant permits movement; the markup plural does not.
        # ruff: ignore[private-member-access]
        result = machine._parse_llm_translations(
            reply, [(first, unit)], [0], string_ids=["s0"]
        )
        self.assertEqual(result[first][0]["text"], "Fichier @@PH0@@")
        with self.assertRaises(MachineTranslationError):
            # ruff: ignore[private-member-access]
            machine._parse_llm_translations(
                reply, [(second, unit)], [1], string_ids=["s0"]
            )


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
        mock_chat("Bonjour")
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
    def test_usage_recorded_through_inherited_seam(self) -> None:
        mock_chat()  # usage 9/2/11, no cost
        self.machine().download_multiple_translations("en", "ja", [("Hello", None)])
        log = LLMUsageLog.objects.get()
        self.assertEqual(log.model, DEEPSEEK)
        self.assertEqual(log.prompt_tokens, 9)
        self.assertEqual(log.completion_tokens, 2)
        self.assertIsNone(log.cost_usd)
        self.assertEqual(log.project_slug, "")
        self.assertEqual(log.operation, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(log.unit_count, 1)

    @http_mock.activate
    def test_decoder_refusals_keep_billed_usage_and_original_reply_once(self) -> None:
        self.enterContext(
            patch.dict(check_models.CHECKS.data, {"game-markup": GameMarkupCheck()})
        )
        for asynchronous in (False, True):
            for malformed in (False, True):
                with self.subTest(asynchronous=asynchronous, malformed=malformed):
                    http_mock.reset()
                    machine = self.machine()
                    unit = make_unit(source='Tap <sprite name="fire">', code="ja")
                    source, _replacements = machine.cleanup_text(unit.source, unit)
                    replies: list[str] = []

                    def respond(
                        request: httpx2.Request,
                        *,
                        malformed: bool = malformed,
                        replies: list[str] = replies,
                    ) -> httpx2.Response:
                        inputs = json.loads(
                            json.loads(request.content)["messages"][-1]["content"]
                        )["strings"]
                        reply = (
                            '{"translations":'
                            if malformed
                            else json.dumps(
                                {
                                    "translations": {
                                        inputs[0]["id"]: {
                                            "text": ["点击 ", ""],
                                            "placeholders": {},
                                        }
                                    }
                                }
                            )
                        )
                        replies.append(reply)
                        return httpx2.Response(
                            200,
                            json={
                                "choices": [
                                    {
                                        "message": {"content": reply},
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

                    http_mock.register_callback("POST", CHAT_URL, callback=respond)
                    before = LLMUsageLog.objects.count()
                    with self.assertRaises(MachineTranslationError) as caught:
                        if asynchronous:
                            async_to_sync(machine.adownload_multiple_translations)(
                                "en", "ja", [(source, unit)]
                            )
                        else:
                            machine.download_multiple_translations(
                                "en", "ja", [(source, unit)]
                            )
                    self.assertEqual(LLMUsageLog.objects.count(), before + 1)
                    log = LLMUsageLog.objects.latest("pk")
                    self.assertEqual(log.outcome, LLMUsageLog.Outcome.REFUSED)
                    self.assertEqual(log.refusal_reason, str(caught.exception))
                    self.assertEqual(log.reply_excerpt, replies[0])
                    self.assertEqual(log.model, DEEPSEEK)
                    self.assertEqual(log.prompt_tokens, 9)
                    self.assertEqual(log.completion_tokens, 2)
                    self.assertEqual(log.total_tokens, 11)
                    self.assertEqual(log.batch_size, 1)
                    self.assertEqual(log.unit_count, 1)
                    self.assertEqual(log.operation, LLMUsageLog.Operation.TRANSLATION)
                    self.assertEqual(machine.get_model(), GEMINI)

    @http_mock.activate
    def test_route_context_is_reset_after_success(self) -> None:
        mock_chat()
        machine = self.machine()

        machine.download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertEqual(machine.get_model(), GEMINI)

    @http_mock.activate
    def test_route_context_is_reset_after_parser_error(self) -> None:
        mock_chat(invalid_reply="not JSON")
        machine = self.machine()

        with self.assertRaises(MachineTranslationError):
            machine.download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertEqual(machine.get_model(), GEMINI)

    @http_mock.activate
    def test_async_route_context_is_reset_after_parser_error(self) -> None:
        mock_chat(invalid_reply="not JSON")
        machine = self.machine()

        with self.assertRaises(MachineTranslationError):
            async_to_sync(machine.adownload_multiple_translations)(
                "en", "ja", [("Hello", None)]
            )

        self.assertEqual(machine.get_model(), GEMINI)


class RoutedLiteLLMTest(SimpleTestCase):
    """Cover what differs from ``RoutedLLMTranslation``: slug, URL, timeout, payload."""

    def machine(self, **overrides: object) -> RoutedLiteLLMTranslation:
        configuration = {**LITELLM_CONFIGURATION, **overrides}
        return RoutedLiteLLMTranslation(as_settings(configuration))

    def test_registration_slug(self) -> None:
        self.assertEqual(RoutedLiteLLMTranslation.name, "LiteLLM")
        self.assertEqual(RoutedLiteLLMTranslation.get_identifier(), "litellm")

    def test_default_base_url(self) -> None:
        configuration = {
            key: value
            for key, value in LITELLM_CONFIGURATION.items()
            if key != "base_url"
        }
        machine = RoutedLiteLLMTranslation(as_settings(configuration))
        self.assertEqual(machine.get_runtime_base_url(), LITELLM_DEFAULT_BASE_URL)

    def test_request_timeout(self) -> None:
        self.assertEqual(RoutedLiteLLMTranslation.request_timeout, 55)

    def test_trusted_error_host(self) -> None:
        self.assertIn(
            "hcbifrost.herocraft.com", RoutedLiteLLMTranslation.trusted_error_hosts
        )

    def test_chat_payload_has_schema_without_provider(self) -> None:
        payload = self.machine().get_chat_payload(
            GEMINI, "prompt", batch_content(2), "", ""
        )
        self.assertIn("response_format", payload)
        self.assertNotIn("provider", payload)

    def test_malformed_content_pop_is_safe(self) -> None:
        payload = self.machine().get_chat_payload(GEMINI, "prompt", "not json", "", "")
        self.assertNotIn("response_format", payload)
        self.assertNotIn("provider", payload)


class RoutedLiteLLMDownloadTest(TestCase):
    @http_mock.activate
    def test_sync_multiple_routes_cjk(self) -> None:
        mock_litellm_chat()
        machine = RoutedLiteLLMTranslation(as_settings(dict(LITELLM_CONFIGURATION)))
        machine.delete_cache()
        machine.cache_translations = False

        result = machine.download_multiple_translations("en", "ja", [("Hello", None)])

        self.assertTrue(result)
        self.assertEqual(litellm_sent_models(), [DEEPSEEK])
