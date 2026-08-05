# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter LLM machinery with per-target-language model routing."""

from __future__ import annotations

import re
from typing import ClassVar, cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext, gettext_lazy, pgettext_lazy

from weblate.lang.forms import validate_language_code
from weblate.lang.models import Language
from weblate.machinery.forms import BaseOpenAIMachineryForm, EmptyMappingJSONField
from weblate.machinery.openai import OpenAITranslation

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
            raise ValidationError(
                gettext("Routing must be a non-empty JSON object.")
            )

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

    name = "Routed LLM"
    settings_form = RoutedLLMMachineryForm
    trusted_error_hosts: ClassVar[set[str]] = {"openrouter.ai"}

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
