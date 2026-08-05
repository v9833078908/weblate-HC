# Routed LLM Machinery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use @executing-plans to implement this plan task-by-task.

**Goal:** Добавить локальный Weblate-движок `Routed LLM`, который через OpenRouter выбирает model ID по целевому языку из настраиваемой JSON-карты.

**Architecture:** Кастомный класс наследует `OpenAITranslation`, но резолвит модель из `routing` и не вызывает `/models`. Оба sync-входа перехватываются в общей `_download_multiple_translations`, оба async-входа — в `_adownload_multiple_translations`; target language хранится в `ContextVar` и всегда сбрасывается в `finally`. Форма удаляет неиспользуемое поле `model`, проверяет карту и использует собственный `validate_settings`, совместимый с конфигурациями без `"*"`.

**Tech Stack:** Python 3.12+, Django/Weblate machinery, `httpx2` через `weblate.utils.tests.http_mock`, pytest/Django TestCase, Docker Compose, Ruff/prek.

---

## Контекст и принятые решения

Подтверждённый дизайн: `docs/plans/2026-08-05-routed-llm-machinery-design.md`.

Маршрут выбирается в следующем порядке:

1. точный код без учёта регистра, где `-` и `_` эквивалентны;
2. базовая часть до `-`, `_` или `@`;
3. ключ `"*"`;
4. если совпадения нет, движок не поддерживает язык.

Примеры:

- `zh_Hans` → точный `zh_Hans`, затем `zh`, затем `"*"`;
- `pt-BR` совпадает с ключом `pt_BR`;
- карта `{"ja": "..."}` без `"*"` валидна и поддерживает только японский;
- ключи `pt_BR` и `pt-BR` в одной карте запрещены как неоднозначные.

Project-level machinery settings заменяют весь конфиг сервиса. Они не
объединяют только `routing` с глобальными `key`, `base_url`, `persona` и
остальными полями.

Рекомендуемая глобальная конфигурация:

```json
{
  "key": "<OpenRouter API key>",
  "base_url": "https://openrouter.ai/api/v1",
  "routing": {
    "ja": "deepseek/deepseek-chat-v3.1",
    "ko": "deepseek/deepseek-chat-v3.1",
    "zh": "deepseek/deepseek-chat-v3.1",
    "*": "google/gemini-2.5-flash"
  }
}
```

Model ID и цены проверены 2026-08-05 по
[официальному каталогу OpenRouter](https://openrouter.ai/api/v1/models):

- [`deepseek/deepseek-chat-v3.1`](https://openrouter.ai/deepseek/deepseek-chat-v3.1)
  — $0.25/$0.95 за 1M input/output tokens;
- [`google/gemini-2.5-flash`](https://openrouter.ai/google/gemini-2.5-flash)
  — $0.30/$2.50 за 1M input/output tokens.

## Preflight

Перед Task 1 установить dev-зависимости и запустить текущий dev-контейнер:

```sh
uv sync --all-extras --dev
./rundev.sh start
./rundev.sh wait
./rundev.sh check
```

Expected: dependency sync завершается, контейнер healthy, `weblate check`
проходит. До исправления в Task 4 `rundev.sh` использует порт 8080 даже при
переданном `WEBLATE_PORT`; для container-based pytest номер опубликованного
порта не важен.

Если сборка `PyICU` на macOS сообщает об отсутствующих `pkg-config` или ICU,
сначала установить системные ICU development files и повторить `uv sync`.

---

### Task 1: Форма routing и чистый резолвер

**Files:**

- Create: `weblate_customization/src/weblate_customization/machinery.py`
- Create: `weblate_customization/tests/__init__.py`
- Create: `weblate_customization/tests/test_machinery.py`

**Step 1: Create the test package**

Создать `weblate_customization/tests/__init__.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
```

**Step 2: Write failing form and resolver tests**

Создать `weblate_customization/tests/test_machinery.py`:

```python
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
```

**Step 3: Deploy the missing module and verify the tests fail**

Run:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'weblate_customization.machinery'`.

**Step 4: Implement the form and pure resolver**

Создать `weblate_customization/src/weblate_customization/machinery.py`:

```python
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
```

**Step 5: Copy the module and run the focused tests**

Run:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py -q
```

Expected: all Task 1 tests pass.

**Step 6: Commit Task 1**

```sh
git add -- \
  weblate_customization/src/weblate_customization/machinery.py \
  weblate_customization/tests/__init__.py \
  weblate_customization/tests/test_machinery.py
git commit -m "feat(customization): add routed LLM form and resolver"
```

---

### Task 2: Route context and settings validation

**Files:**

- Modify: `weblate_customization/src/weblate_customization/machinery.py`
- Modify: `weblate_customization/tests/test_machinery.py`

**Step 1: Add HTTP helpers and failing validation tests**

В `test_machinery.py` добавить импорты:

```python
import json

from weblate.utils.tests import http_mock
```

После `CONFIGURATION` добавить:

```python
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
```

Добавить тестовый класс:

```python
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
```

**Step 2: Run the validation tests and verify failure**

Run:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_machinery.py::RoutedSettingsValidationTest -q
```

Expected: both tests fail through inherited OpenAI model discovery (`GET
/models`). The implementation must also avoid the inherited fixed `de`
validation target so that maps without fallback remain valid after direct model
routing replaces model discovery.

**Step 3: Replace RoutedLLMTranslation with the sync routed implementation**

В `machinery.py` добавить импорты:

```python
from contextvars import ContextVar
from typing import TYPE_CHECKING, ClassVar, cast

from weblate.machinery.base import (
    MACHINERY_DEFAULT_THRESHOLD,
    MachineTranslationError,
)

if TYPE_CHECKING:
    from weblate.auth.models import User
    from weblate.machinery.types import DownloadMultipleTranslations, SettingsDict
    from weblate.trans.models import Unit
```

Заменить класс `RoutedLLMTranslation` целиком:

```python
class RoutedLLMTranslation(OpenAITranslation):
    """OpenRouter LLM with per-target-language model routing."""

    name = "Routed LLM"
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

```

Важно: удалить старый `from typing import ClassVar, cast`, чтобы после добавления
`TYPE_CHECKING` не осталось дублирующего импорта.

**Step 4: Copy the module and rerun the validation tests**

Run:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_machinery.py::RoutedSettingsValidationTest -q
```

Expected: 2 passed, один POST на тест и ни одного GET `/models`.

**Step 5: Run all tests implemented so far**

```sh
./rundev.sh test weblate_customization/tests/test_machinery.py -q
```

Expected: all tests pass.

**Step 6: Commit Task 2**

```sh
git add -- \
  weblate_customization/src/weblate_customization/machinery.py \
  weblate_customization/tests/test_machinery.py
git commit -m "feat(customization): route OpenRouter model by target language"
```

---

### Task 3: Production-path HTTP test matrix

**Files:**

- Modify: `weblate_customization/tests/test_machinery.py`

Обычный редактор использует `atranslate()` и далее
`adownload_pending_translations()`. Поэтому одного sync
`download_multiple_translations()` недостаточно.

**Step 1: Write failing sync/async and multiple/pending tests**

Добавить импорт:

```python
from asgiref.sync import async_to_sync

from weblate.machinery.base import MachineTranslationError
```

Добавить класс:

```python
class RoutedDownloadTest(TestCase):
    def machine(
        self, routing: dict[str, str] | None = None
    ) -> RoutedLLMTranslation:
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
        self.machine().download_multiple_translations(
            "en", "ja", [("Hello", None)]
        )

        self.assertTrue(
            all("/models" not in str(call.request.url) for call in http_mock.calls)
        )

    @http_mock.activate
    def test_unsupported_language_fails_before_http(self) -> None:
        machine = self.machine({"ja": DEEPSEEK})

        with self.assertRaisesRegex(
            MachineTranslationError, "No routed model for target language: fr"
        ):
            machine.download_multiple_translations(
                "en", "fr", [("Hello", None)]
            )

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
            machine.download_multiple_translations(
                "en", "ja", [("Hello", None)]
            )

        self.assertEqual(machine.get_model(), GEMINI)
```

**Step 2: Run the matrix and verify the async tests fail**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test \
  weblate_customization/tests/test_machinery.py::RoutedDownloadTest -q
```

Expected: sync tests pass; both CJK async tests fail because the async shared
path does not yet set the route context and resolves the fallback model.

**Step 3: Implement the async shared route**

Добавить в `RoutedLLMTranslation` после sync
`_download_multiple_translations`:

```python
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
```

**Step 4: Run the complete focused suite**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py -q
```

Expected: all tests pass.

**Step 5: Run formatting and lint checks for the custom package**

```sh
uv run prek run --files \
  weblate_customization/src/weblate_customization/machinery.py \
  weblate_customization/tests/__init__.py \
  weblate_customization/tests/test_machinery.py
```

Expected: all hooks pass. Если formatter изменил файлы, снова выполнить `cp` и
focused pytest до коммита.

**Step 6: Commit Task 3**

```sh
git add -- \
  weblate_customization/src/weblate_customization/machinery.py \
  weblate_customization/tests/test_machinery.py
git commit -m "feat(customization): support async routed LLM requests"
```

---

### Task 4: Correct dev port handling and register the machinery

**Files:**

- Modify: `rundev.sh:12-16`
- Modify: `dev-docker/docker-compose.yml` (`services.weblate.environment`)

**Step 1: Verify the current port command is broken**

Run without starting containers:

```sh
WEBLATE_PORT=3001 ./rundev.sh config | rg 'published: "3001"'
```

Expected before the fix: no match; `rundev.sh` overwrites the value with 8080.

**Step 2: Make rundev.sh respect caller-provided values**

Replace:

```sh
WEBLATE_PORT=8080
export WEBLATE_PORT
WEBLATE_HOST=localhost:$WEBLATE_PORT
export WEBLATE_HOST
```

with:

```sh
WEBLATE_PORT=${WEBLATE_PORT:-8080}
export WEBLATE_PORT
WEBLATE_HOST=${WEBLATE_HOST:-localhost:$WEBLATE_PORT}
export WEBLATE_HOST
```

**Step 3: Verify shell syntax and rendered Compose port**

```sh
bash -n rundev.sh
WEBLATE_PORT=3001 ./rundev.sh config | rg 'published: "3001"'
```

Expected: shell syntax passes and the rendered port is 3001.

**Step 4: Commit the port fix**

```sh
git add -- rundev.sh
git commit -m "fix(dev): honor configured Weblate port"
```

**Step 5: Register Routed LLM in the dev container**

В `dev-docker/docker-compose.yml`, внутри
`services.weblate.environment`, добавить:

```yaml
      WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation
```

**Step 6: Copy the module and restart on port 3001**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh restart
WEBLATE_PORT=3001 ./rundev.sh wait
```

Expected: container becomes healthy and publishes `localhost:3001`.

**Step 7: Check registry loading**

```sh
WEBLATE_PORT=3001 ./rundev.sh check
WEBLATE_PORT=3001 ./rundev.sh exec -T weblate weblate shell -c \
  'from weblate.machinery.models import MACHINERY; print(MACHINERY["routed-llm"].__module__)'
```

Expected:

```text
weblate_customization.machinery
```

`weblate check` не должен выдавать `weblate.W039` для нового класса.

**Step 8: Commit machinery registration**

```sh
git add -- dev-docker/docker-compose.yml
git commit -m "chore(dev-docker): register routed LLM machinery"
```

---

### Task 5: Configure and smoke-test the dev instance

**Files:** none; this task changes Weblate database settings through the local UI.

**Step 1: Configure the global service**

Открыть <http://localhost:3001/manage/machinery/>, выбрать **Routed LLM** и
ввести:

- API key: существующий OpenRouter key владельца;
- API URL: `https://openrouter.ai/api/v1`;
- Model routing by language: JSON из раздела «Контекст и принятые решения».

Expected: форма сохраняется. Сохранение выполняет один тестовый translation
request; это штатное поведение Weblate machinery forms.

**Step 2: Verify CJK routing in the editor**

Открыть японскую или китайскую строку, затем вкладку автоматических
предложений.

Expected: появляется результат от **Routed LLM**. В OpenRouter activity должен
быть запрос к `deepseek/deepseek-chat-v3.1`.

**Step 3: Verify fallback routing**

Открыть французскую строку и вкладку автоматических предложений.

Expected: появляется результат от **Routed LLM**. В OpenRouter activity должен
быть запрос к `google/gemini-2.5-flash`.

Не использовать `grep openrouter` как доказательство выбранной модели: Weblate
не обязан логировать request payload.

**Step 4: Verify a configuration without fallback**

Временно сохранить карту:

```json
{
  "ja": "deepseek/deepseek-chat-v3.1"
}
```

Expected:

- форма сохраняется, используя `ja` для `validate_settings()`;
- на японском движок возвращает предложение;
- на французском editor всё ещё отправляет запрос к настроенному сервису, но
  Weblate получает пустой список предложений без HTTP-вызова к OpenRouter.

После проверки вернуть рекомендуемую карту с `"*"`.

**Step 5: Record the project override behavior**

На project-level странице не создавать override без необходимости. Если он
нужен, повторно заполнить полный конфиг, включая `key` и `base_url`: Weblate
заменяет конфигурацию сервиса целиком.

---

### Task 6: Document the fork customization

**Files:**

- Modify: `AGENTS.md` (`Project-specific setup`, `Deploying the custom check`)
- Modify: `README.rst` (`Что добавлено поверх upstream`, новая секция Routed LLM)

`docs/changes.rst` не менять: это локальная кастомизация форка, а не
user-visible upstream feature.

`docs/security/threat-model.rst` прочитать, но не менять: документ явно относит
local customization code к out-of-scope, а существующие machinery URL checks и
private-target restrictions не меняются.

**Step 1: Update AGENTS.md**

В описании `weblate_customization/` указать, что пакет содержит:

- `GameMarkupCheck` в `checks.py`;
- `RoutedLLMTranslation` в `machinery.py`;
- копирование обоих модулей в `dev-docker/data/python/`;
- регистрацию через `WEBLATE_ADD_CHECK` и `WEBLATE_ADD_MACHINERY`.

Отдельно зафиксировать:

```text
Routed LLM uses one OpenRouter key and a routing JSON mapping target language
codes to model IDs. Project-level settings replace the complete global service
configuration; they do not merge only the routing field.
```

**Step 2: Update README.rst overview**

Расширить описание `weblate_customization/` в разделе
«Что добавлено поверх upstream» двумя предложениями:

```rst
    Пакет также содержит ``RoutedLLMTranslation`` — OpenRouter-совместимый
    движок автоматических предложений, который выбирает model ID по целевому
    языку из JSON-карты ``routing``. Движок подключается через
    ``WEBLATE_ADD_MACHINERY``.
```

**Step 3: Add a README.rst Routed LLM section**

После секции «Проверка game-markup» добавить:

```rst
Routed LLM
----------

``RoutedLLMTranslation`` находится в
``weblate_customization/src/weblate_customization/machinery.py``. После каждой
правки скопируйте пакет в каталог Python-модулей dev-контейнера:

.. code-block:: sh

   cp -r weblate_customization/src/weblate_customization dev-docker/data/python/

Для регистрации движка сервису ``weblate`` нужна переменная:

.. code-block:: yaml

   WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation

Настройки задаются глобально в ``/manage/machinery/``. Поле ``routing`` — это
JSON-объект, где ключом служит код целевого языка или ``"*"`` для fallback, а
значением — OpenRouter model ID. Точное совпадение проверяется до базового кода
языка и fallback. Карта без ``"*"`` допустима.

Project-level настройка заменяет весь глобальный конфиг сервиса, поэтому для
неё нужно повторно задать API key, ``base_url`` и остальные нужные поля.
```

**Step 4: Validate README.rst**

Run against the running dev container:

```sh
WEBLATE_PORT=3001 ./rundev.sh exec -T weblate python -c \
  "from pathlib import Path; from docutils.core import publish_doctree; publish_doctree(Path('/app/src/README.rst').read_text(encoding='utf-8'), settings_overrides={'report_level': 2}); print('RST OK')"
```

Expected: `RST OK` and no level-2 diagnostics.

**Step 5: Review the threat-model condition explicitly**

Run:

```sh
rg -n "local customization|new outbound integration class|private-target" \
  docs/security/threat-model.rst
```

Expected conclusion for the commit message/review notes: no threat-model update
is required because this is local customization code using the existing
machinery trust boundary and URL validation.

**Step 6: Commit documentation**

```sh
git add -- AGENTS.md README.rst
git commit -m "docs: document routed LLM customization"
```

---

### Task 7: Final verification

**Files:** no new changes expected.

**Step 1: Refresh the deployed copy**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

**Step 2: Run the custom machinery suite**

```sh
./rundev.sh test weblate_customization/tests/test_machinery.py -q
```

Expected: all tests pass.

**Step 3: Run adjacent upstream LLM/OpenAI tests**

```sh
./rundev.sh test weblate/machinery/tests.py \
  -k 'OpenAITranslationTest or LLMBasicMachineryFormTest' -q
```

Expected: selected upstream tests pass.

**Step 4: Run repository checks for changed source files**

```sh
uv run prek run --files \
  rundev.sh \
  dev-docker/docker-compose.yml \
  weblate_customization/src/weblate_customization/machinery.py \
  weblate_customization/tests/__init__.py \
  weblate_customization/tests/test_machinery.py \
  AGENTS.md \
  README.rst
```

Expected: all hooks pass.

**Step 5: Run Weblate system checks and verify the registry**

```sh
WEBLATE_PORT=3001 ./rundev.sh check
WEBLATE_PORT=3001 ./rundev.sh exec -T weblate weblate shell -c \
  'from weblate.machinery.models import MACHINERY; cls = MACHINERY["routed-llm"]; print(cls.__module__, cls.__name__)'
```

Expected:

```text
weblate_customization.machinery RoutedLLMTranslation
```

**Step 6: Inspect the final diff and worktree**

```sh
git diff --check
git status --short
git log --oneline -8
```

Expected:

- `git diff --check` has no output;
- only intentional pre-existing untracked files remain;
- commits are separated by form/resolver, routing, tests, dev registration and
  documentation.

---

## Не входит в задачу

- разные API keys или base URLs для отдельных языков;
- автоматический выбор или проверка model ID через `/models`;
- изменение upstream `BaseLLMTranslation.get_model()` API;
- field-level merge глобальной и project-level routing maps;
- production deployment;
- изменение `docs/changes.rst`;
- изменение `docs/security/threat-model.rst`, пока кастомизация остаётся
  локальным out-of-scope кодом и не меняет заявленные security properties.
