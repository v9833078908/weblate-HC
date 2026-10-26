# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter and LiteLLM machinery with per-target-language model routing."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import TYPE_CHECKING, ClassVar, cast
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext, gettext_lazy, pgettext_lazy

from weblate.lang.forms import validate_language_code
from weblate.lang.models import Language
from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
)
from weblate.machinery.forms import BaseOpenAIMachineryForm, EmptyMappingJSONField
from weblate.machinery.openai import OpenAITranslation

from .routed_reply import LEGACY_PROMPT, legacy_reply_format, legacy_unwrap_reply

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.machinery.types import DownloadMultipleTranslations, SettingsDict
    from weblate.trans.models import Unit

LANGUAGE_CODE_PART_RE = re.compile(r"[-_@]")
FALLBACK_KEY = "*"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
LITELLM_DEFAULT_BASE_URL = "https://hcbifrost.herocraft.com/litellm/v1"
OPENROUTER_MACHINE_TRANSLATION_TITLE = "HCGameLoc Weblate - Machine Translation"


def normalize_language_code(code: str) -> str:
    """Normalize codes for route comparison without changing stored settings."""
    return code.casefold().replace("-", "_")


class RoutedLLMMachineryForm(BaseOpenAIMachineryForm):
    """Settings form for language-to-model routing."""

    model = None
    routing = EmptyMappingJSONField(
        label=pgettext_lazy(
            "Automatic suggestion service configuration",
            "Model routing by language",
        ),
        help_text=gettext_lazy(
            'JSON object mapping target language codes, or "*" fallback, '
            "to OpenRouter model IDs."
        ),
        widget=forms.Textarea,
    )

    def clean_routing(self) -> dict[str, str]:
        value = self.cleaned_data["routing"]
        if not isinstance(value, dict) or not value:
            raise ValidationError(gettext("Routing must be a non-empty JSON object."))

        result: dict[str, str] = {}
        normalized_keys: dict[str, str] = {}
        for key, model in value.items():
            if not isinstance(key, str) or not isinstance(model, str):
                raise ValidationError(
                    gettext("Routing must map language codes to model IDs.")
                )

            normalized_key = normalize_language_code(key)
            if normalized_key in normalized_keys:
                raise ValidationError(
                    gettext(
                        'Routing contains conflicting language codes "%(first)s" '
                        'and "%(second)s".'
                    )
                    % {"first": normalized_keys[normalized_key], "second": key}
                )
            normalized_keys[normalized_key] = key

            model = model.strip()
            if not model:
                raise ValidationError(
                    gettext('Routing model for "%s" must not be empty.') % key
                )

            if key != FALLBACK_KEY:
                try:
                    validate_language_code(key)
                except ValidationError as error:
                    raise ValidationError(
                        gettext('Routing contains invalid language code "%s".') % key
                    ) from error
                if Language.objects.fuzzy_get_strict(key) is None:
                    raise ValidationError(
                        gettext('Routing contains unknown language code "%s".') % key
                    )

            result[key] = model
        return result


class RoutedProjectOpenRouterMachineryForm(RoutedLLMMachineryForm):
    """Project settings form without an editable OpenRouter API key."""

    key = None

    def clean(self) -> None:
        if not self.errors:
            key = self.initial.get("key")
            if not isinstance(key, str) or not key:
                raise ValidationError(
                    gettext("OpenRouter API key must be configured site-wide.")
                )
            self.cleaned_data["key"] = key
        super().clean()


class RoutedLiteLLMMachineryForm(RoutedLLMMachineryForm):
    """Settings form for routing against the corporate LiteLLM proxy."""

    routing = EmptyMappingJSONField(
        label=pgettext_lazy(
            "Automatic suggestion service configuration",
            "Model routing by language",
        ),
        help_text=gettext_lazy(
            'JSON object mapping target language codes, or "*" fallback, '
            "to model IDs available on the LiteLLM proxy."
        ),
        widget=forms.Textarea,
    )


class RoutedLLMTranslation(OpenAITranslation):
    """OpenRouter LLM with per-target-language model routing."""

    name = "OpenRouter"
    settings_form = RoutedLLMMachineryForm
    project_settings_form = RoutedProjectOpenRouterMachineryForm
    trusted_error_hosts: ClassVar[set[str]] = {"openrouter.ai"}
    output_rules: ClassVar[dict[str, str]] = {
        "placeholder_rule": "Every source placeholder occurrence is mandatory. The server owns its exact token, markup, attributes and metadata. Choose only its position; never emit raw tokens or markup in translated text.",
        "shape_rule": 'Return one closed JSON object {"translations": {INPUT_ID: {"text": [...], "placeholders": {...}}}}. Each input id is a required object key, not an output field.',
        "identity_rule": "Return exactly the input string-id keys, including a singleton. No extra ids, fields, diagnostics or metadata.",
        "parts_rule": 'For N source placeholder parts, return exactly N+1 strings in "text" and exactly N occurrence keys p0 through p(N-1) in "placeholders". Number occurrences in source parts order, even for repeated ids. Each occurrence requires integer "position" in 0..N: insert immediately before that text slot. Equal positions keep source occurrence order. Grammar placeholders may reorder within their original markup boundaries; markup and syntax keep source order.',
        "text_rule": 'Translate the whole string, then split its text around the protected occurrences. Empty text slots are allowed. An occurrence with source translatable=true additionally requires "text" for its entire translated wrapped content; otherwise it contains only "position". Do not copy source id, type, kind, role, close_id or translatable into output.',
        "atom_rule": "Protected occurrences are opaque, source-owned atoms. Never put placeholder tokens, tags, attributes or their mapped immutable contents in text slots. The server reconstructs them exactly from the source.",
        "envelope_rule": 'Return exactly one JSON object with "translations", with nothing before or after it. Input ids pair outputs with sources, regardless of object key order.',
        "mapping_rule": "Placeholder mappings provide context for wording. Reference each source occurrence only through its mandatory pN key; do not copy mapped content into the output.",
        "wrapper_rule": 'For a translatable wrapper, put all translated inner content in that occurrence\'s "text". Never split or duplicate its delimiters; the server restores them.',
        "output_examples": """For source parts [{"type":"text","text":"Armor "},{"type":"placeholder","id":"@@PH0@@","kind":"markup","text":"","translatable":false},{"type":"text","text":" +10%"}] with id s0, a valid answer is:
{"translations":{"s0":{"text":["방어력 "," +10%"],"placeholders":{"p0":{"position":1}}}}}
The p0 occurrence is required even when its text is empty. A text-only answer without p0 is invalid.
For source "Он ушёл" without placeholders, do not add final punctuation:
{"translations":{"s1":{"text":["Il est parti"],"placeholders":{}}}}
Respond ONLY with the required JSON object. Follow the supplied response schema.""",
    }

    def __init__(self, configuration: SettingsDict) -> None:
        super().__init__(configuration)
        self._route_target: ContextVar[str | None] = ContextVar(
            "routed_llm_target", default=None
        )
        self._reply_codec: ContextVar[str] = ContextVar(
            "routed_reply_codec", default="routed-slots-v2"
        )

    @property
    def delivery_request_codec(self) -> str:
        return self._reply_codec.get()

    def _fetch_llm_batch(
        self, source_language, target_language, sources, source_occurrences
    ):
        codec = "routed-slots-v2"
        identity = self._delivery_identity(sources)
        if identity:
            from weblate.trans.llm_delivery import (  # ruff: ignore[import-outside-top-level]
                DeliveryUnresolved,
                saved_request_codec,
            )

            saved = saved_request_codec(identity)
            if saved in {"", "routed-parts-v1"}:
                codec = "routed-parts-v1"
            elif saved not in {None, codec}:
                msg = "Stored routed codec requires reconciliation"
                raise DeliveryUnresolved(msg)
        token = self._reply_codec.set(codec)
        try:
            return super()._fetch_llm_batch(
                source_language, target_language, sources, source_occurrences
            )
        finally:
            self._reply_codec.reset(token)

    def _get_prompt(self, target_language: str) -> str:
        if self.delivery_request_codec != "routed-parts-v1":
            return super()._get_prompt(target_language)
        return LEGACY_PROMPT.format(
            persona=self.format_prompt_part("persona"),
            style=self.format_prompt_part("style"),
            language_instructions=self.format_language_instructions(target_language),
        ) + (
            '\nWhen a response schema is supplied, return {"translations": [...]}:'
            " the array described above, wrapped in that one key.\n"
        )

    def _build_string_ids(self, count: int) -> list[str]:
        # Durable batch ids are separately derived from semantic identities.
        # Demonstration ids must also be stable for exact request-digest replay.
        if (
            self.durable_single_attempt
            and self.delivery_request_codec == "routed-slots-v2"
        ):
            return [f"example{index}" for index in range(count)]
        return super()._build_string_ids(count)

    def get_runtime_base_url(self) -> str:
        return self.settings.get("base_url") or DEFAULT_BASE_URL

    def get_headers(self) -> dict[str, str]:
        """Identify this service to OpenRouter without affecting other proxies."""
        headers = super().get_headers()
        if urlsplit(self.get_runtime_base_url()).hostname == "openrouter.ai":
            headers["X-OpenRouter-Title"] = OPENROUTER_MACHINE_TRANSLATION_TITLE
        return headers

    def get_routing(self) -> dict[str, str]:
        settings = cast("dict[str, object]", self.settings)
        value = settings.get("routing")
        if not isinstance(value, dict):
            return {}
        return {
            key: model
            for key, model in value.items()
            if isinstance(key, str) and isinstance(model, str)
        }

    def resolve_model(self, target_language: str | None) -> str | None:
        routing = self.get_routing()
        if not target_language:
            return routing.get(FALLBACK_KEY)

        normalized = {
            normalize_language_code(key): model for key, model in routing.items()
        }
        code = normalize_language_code(target_language)
        if code in normalized:
            return normalized[code]

        base = LANGUAGE_CODE_PART_RE.split(code, 1)[0]
        if base in normalized:
            return normalized[base]
        return routing.get(FALLBACK_KEY)

    def is_supported(self, _source_language, target_language) -> bool:
        return self.resolve_model(target_language) is not None

    def get_model(self) -> str:
        target_language = self._route_target.get()
        model = self.resolve_model(target_language)
        if model is None:
            msg = f"No routed model for target language: {target_language or '<unset>'}"
            raise MachineTranslationError(msg)
        return model

    async def aget_model(self) -> str:
        return self.get_model()

    def get_validation_target(self) -> str:
        for language in self.get_routing():
            if language != FALLBACK_KEY:
                return language
        return self.validate_target_language

    def validate_settings(self) -> None:
        try:
            self.download_languages()
        except Exception as error:
            raise ValidationError(
                gettext("Could not fetch supported languages: %s") % error
            ) from error

        try:
            self.download_multiple_translations(
                self.validate_source_language,
                self.get_validation_target(),
                [("test", None)],
                None,
                MACHINERY_DEFAULT_THRESHOLD,
            )
        except Exception as error:
            raise ValidationError(
                gettext("Could not fetch translation: %s") % error
            ) from error

    def _download_multiple_translations(
        self,
        source_language,
        target_language,
        sources: list[tuple[str, Unit | None]],
        user: User | None = None,
        threshold: int = MACHINERY_DEFAULT_THRESHOLD,
        *,
        source_occurrences: list[int] | None = None,
    ) -> DownloadMultipleTranslations:
        token = self._route_target.set(target_language)
        try:
            return super()._download_multiple_translations(
                source_language,
                target_language,
                sources,
                user,
                threshold,
                source_occurrences=source_occurrences,
            )
        finally:
            self._route_target.reset(token)

    async def _adownload_multiple_translations(
        self,
        source_language,
        target_language,
        sources: list[tuple[str, Unit | None]],
        user: User | None = None,
        threshold: int = MACHINERY_DEFAULT_THRESHOLD,
        *,
        source_occurrences: list[int] | None = None,
    ) -> DownloadMultipleTranslations:
        token = self._route_target.set(target_language)
        try:
            return await super()._adownload_multiple_translations(
                source_language,
                target_language,
                sources,
                user,
                threshold,
                source_occurrences=source_occurrences,
            )
        finally:
            self._route_target.reset(token)

    def get_chat_payload(
        self,
        model: str,
        prompt: str,
        content: str,
        previous_content: str,
        previous_response: str,
    ) -> dict:
        payload = super().get_chat_payload(
            model, prompt, content, previous_content, previous_response
        )
        strings = self._request_strings(content)
        if strings:
            payload["response_format"] = (
                legacy_reply_format(len(strings))
                if self.delivery_request_codec == "routed-parts-v1"
                else self._reply_format(strings)
            )
            provider = cast("dict[str, object]", payload.setdefault("provider", {}))
            provider["require_parameters"] = True
        return payload

    @staticmethod
    def _request_strings(content: str) -> list[dict]:
        try:
            strings = json.loads(content)["strings"]
        except (TypeError, KeyError, json.JSONDecodeError):
            return []
        return strings if isinstance(strings, list) else []

    @staticmethod
    def _closed_schema(properties: dict) -> dict:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    @classmethod
    def _reply_format(cls, strings: list[dict]) -> dict:
        translations = {}
        for item in strings:
            placeholders = [
                part for part in item["parts"] if part["type"] == "placeholder"
            ]
            # The validator already requires nonempty text on either side of
            # interior markup. Express that boundary in the provider schema too.
            fixed_boundaries = (
                bool(placeholders)
                and len(item["parts"]) == 2 * len(placeholders) + 1
                and all(
                    part["kind"] == "markup" and not part["translatable"]
                    for part in placeholders
                )
                and all(
                    part["type"] == "text" and part["text"].strip()
                    for part in item["parts"][::2]
                )
            )
            occurrences = {}
            for index, part in enumerate(placeholders):
                fields = {
                    "position": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": len(placeholders),
                    }
                }
                if fixed_boundaries:
                    fields["position"] = {"type": "integer", "enum": [index + 1]}
                if part["translatable"]:
                    fields["text"] = {"type": "string"}
                occurrences[f"p{index}"] = cls._closed_schema(fields)
            translations[item["id"]] = cls._closed_schema(
                {
                    "text": {
                        "type": "array",
                        "items": (
                            {"type": "string", "minLength": 1}
                            if fixed_boundaries
                            else {"type": "string"}
                        ),
                        "minItems": len(placeholders) + 1,
                        "maxItems": len(placeholders) + 1,
                    },
                    "placeholders": cls._closed_schema(occurrences),
                }
            )
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "translations",
                "strict": True,
                "schema": cls._closed_schema(
                    {"translations": cls._closed_schema(translations)}
                ),
            },
        }

    def _build_previous_messages_from_examples(
        self, source_language: str, target_language: str, examples: list
    ) -> tuple[str, str]:
        content, response = super()._build_previous_messages_from_examples(
            source_language, target_language, examples
        )
        if self.delivery_request_codec == "routed-parts-v1":
            return content, response
        translations = {}
        for source, target in zip(
            json.loads(content)["strings"], json.loads(response), strict=True
        ):
            occurrences: dict[tuple[str, str | None], deque[str]] = defaultdict(deque)
            for index, part in enumerate(
                part for part in source["parts"] if part["type"] == "placeholder"
            ):
                occurrences[part["id"], part.get("close_id")].append(f"p{index}")
            text = [""]
            placements = {}
            for part in target["parts"]:
                if part["type"] == "text":
                    text[-1] += part["text"]
                    continue
                key = occurrences[part["id"], part.get("close_id")].popleft()
                placement = {"position": len(text)}
                if part["translatable"]:
                    placement["text"] = part["text"]
                placements[key] = placement
                text.append("")
            translations[source["id"]] = {"text": text, "placeholders": placements}
        return content, json.dumps({"translations": translations}, ensure_ascii=False)

    @staticmethod
    def _unique_reply_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                msg = "Duplicate object member in protected-slot reply."
                raise ValueError(msg)
            result[key] = value
        return result

    @staticmethod
    def _require_reply_fields(value: object, keys: set[str]) -> dict:
        if not isinstance(value, dict) or set(value) != keys:
            msg = "Missing or unexpected fields in protected-slot reply."
            raise ValueError(msg)
        return value

    def _decode_reply(
        self,
        translations_string: str | None,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None,
        string_ids: list[str],
    ) -> list[dict]:
        reply = self._require_reply_fields(
            json.loads(
                translations_string or "", object_pairs_hook=self._unique_reply_object
            ),
            {"translations"},
        )
        translations = self._require_reply_fields(
            reply["translations"], set(string_ids)
        )
        occurrence_counts: dict[tuple[int | None, str], int] = defaultdict(int)
        decoded = []
        for index, ((source, unit), string_id) in enumerate(
            zip(sources, string_ids, strict=True)
        ):
            if source_occurrences is None:
                occurrence_key = (id(unit) if unit is not None else None, source)
                source_occurrence = occurrence_counts[occurrence_key]
                occurrence_counts[occurrence_key] += 1
            else:
                source_occurrence = source_occurrences[index]
            placeholders = [
                part
                for part in self._get_string_parts(source, unit, source_occurrence)
                if part["type"] == "placeholder"
            ]
            item = self._require_reply_fields(
                translations[string_id], {"text", "placeholders"}
            )
            text = item["text"]
            if (
                not isinstance(text, list)
                or len(text) != len(placeholders) + 1
                or any(not isinstance(slot, str) for slot in text)
            ):
                msg = "Wrong text slots in protected-slot reply."
                raise ValueError(msg)
            placements = self._require_reply_fields(
                item["placeholders"], {f"p{i}" for i in range(len(placeholders))}
            )
            before: list[list[dict]] = [[] for _ in text]
            for slot_index, part in enumerate(placeholders):
                fields = {"position", "text"} if part["translatable"] else {"position"}
                placement = self._require_reply_fields(
                    placements[f"p{slot_index}"], fields
                )
                position = placement["position"]
                if type(position) is not int or not 0 <= position < len(text):
                    msg = "Invalid protected occurrence position."
                    raise ValueError(msg)
                restored = dict(part)
                if part["translatable"]:
                    if not isinstance(placement["text"], str):
                        msg = "Invalid translated wrapper content."
                        raise ValueError(msg)
                    restored["text"] = placement["text"]
                before[position].append(restored)
            parts = []
            for bucket, slot in zip(before, text, strict=True):
                parts.extend(bucket)
                parts.append({"type": "text", "text": slot})
            decoded.append({"id": string_id, "parts": parts})
        return decoded

    def _parse_llm_translations(
        self,
        translations_string: str | None,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None,
        *,
        string_ids: list[str],
    ) -> DownloadMultipleTranslations:
        if self.delivery_request_codec == "routed-parts-v1":
            return super()._parse_llm_translations(
                legacy_unwrap_reply(translations_string),
                sources,
                source_occurrences,
                string_ids=string_ids,
            )
        try:
            decoded = self._decode_reply(
                translations_string, sources, source_occurrences, string_ids
            )
        except (ValueError, TypeError) as error:
            msg = str(error)
            self.log_handled_error(msg, extra_log=translations_string)
            self._record_llm_outcome("refused", reason=msg, reply=translations_string)
            raise MachineTranslationError(msg) from error
        return super()._parse_llm_translations(
            json.dumps(decoded, ensure_ascii=False),
            sources,
            source_occurrences,
            string_ids=string_ids,
        )


class RoutedLiteLLMTranslation(RoutedLLMTranslation):
    """Corporate LiteLLM proxy LLM with per-target-language model routing."""

    name = "LiteLLM"
    settings_form = RoutedLiteLLMMachineryForm
    project_settings_form = RoutedLiteLLMMachineryForm
    trusted_error_hosts: ClassVar[set[str]] = {"hcbifrost.herocraft.com"}
    # Below the proxy's hard ~60s gateway timeout (nginx 504 past that);
    # caps one transport attempt only, inherited retries are unchanged.
    request_timeout = 55

    def get_runtime_base_url(self) -> str:
        return self.settings.get("base_url") or LITELLM_DEFAULT_BASE_URL

    def get_chat_payload(
        self,
        model: str,
        prompt: str,
        content: str,
        previous_content: str,
        previous_response: str,
    ) -> dict:
        payload = super().get_chat_payload(
            model, prompt, content, previous_content, previous_response
        )
        # The corporate proxy passes response_format through without
        # enforcing it, and rejects OpenRouter-only request fields.
        payload.pop("provider", None)
        return payload
