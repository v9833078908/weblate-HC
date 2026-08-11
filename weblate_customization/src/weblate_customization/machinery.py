# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter LLM machinery with per-target-language model routing."""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING, ClassVar, cast

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

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.machinery.types import DownloadMultipleTranslations, SettingsDict
    from weblate.trans.models import Unit

LANGUAGE_CODE_PART_RE = re.compile(r"[-_@]")
FALLBACK_KEY = "*"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


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


class RoutedLLMTranslation(OpenAITranslation):
    """OpenRouter LLM with per-target-language model routing."""

    name = "OpenRouter"
    settings_form = RoutedLLMMachineryForm
    trusted_error_hosts: ClassVar[set[str]] = {"openrouter.ai"}

    def __init__(self, configuration: SettingsDict) -> None:
        super().__init__(configuration)
        self._route_target: ContextVar[str | None] = ContextVar(
            "routed_llm_target", default=None
        )

    def get_runtime_base_url(self) -> str:
        return self.settings.get("base_url") or DEFAULT_BASE_URL

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
        count = self._expected_reply_length(content)
        if count:
            payload["response_format"] = self._reply_format(count)
            # A provider that silently ignores the schema would answer in a
            # shape the parser rejects, so route only to ones that honour it.
            provider = cast("dict[str, object]", payload.setdefault("provider", {}))
            provider["require_parameters"] = True
        return payload

    @staticmethod
    def _expected_reply_length(content: str) -> int:
        """Return how many strings the batch asks for, taken from the request."""
        try:
            strings = json.loads(content)["strings"]
        except (TypeError, KeyError, json.JSONDecodeError):
            return 0
        return len(strings) if isinstance(strings, list) else 0

    @staticmethod
    def _reply_format(count: int) -> dict:
        """
        Constrain the reply to one structured object per requested string.

        The element count is the part that matters: a reply that ends early is
        the dominant failure mode, and no prompt rule enforces the length.
        Part fields stay optional because only placeholders carry them, and a
        text part is rejected downstream unless it holds exactly type and text.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "translations",
                "schema": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "parts": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string",
                                            "enum": ["text", "placeholder"],
                                        },
                                        "text": {"type": "string"},
                                        "id": {"type": "string"},
                                        "kind": {"type": "string"},
                                        "role": {"type": "string"},
                                        "close_id": {"type": "string"},
                                        "translatable": {"type": "boolean"},
                                    },
                                    "required": ["type", "text"],
                                },
                            }
                        },
                        "required": ["parts"],
                    },
                },
            },
        }
