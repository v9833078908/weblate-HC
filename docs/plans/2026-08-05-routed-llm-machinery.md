# Routed LLM Machinery — план реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Один movinery-движок «Routed LLM», который по целевому языку строки сам выбирает модель на OpenRouter (CJK → DeepSeek V3.1, остальные → Gemini 2.5 Flash), с таблицей роутинга в конфиге UI, а не в коде.

**Architecture:** Наследуем `OpenAITranslation` (OpenRouter отдаёт OpenAI-совместимый API). Целевой язык перехватываем в четырёх публичных download-методах `BaseLLMTranslation`, модель резолвим из JSON-поля `routing` формы настроек (`язык → model id`, фолбэк `"*"`). Один ключ и один base_url (OpenRouter). Регистрация через `WEBLATE_ADD_MACHINERY`, деплой копированием в `dev-docker/data/python/` — тот же механизм, что у `GameMarkupCheck`.

**Tech Stack:** Python/Django (Weblate fork), `weblate_customization` package, pytest внутри dev-контейнера (`./rundev.sh test`), `weblate.utils.tests.http_mock` для мока HTTP.

---

## Проверенные факты (основа плана; файл:строка из этого репо)

1. **Язык недоступен в точках выбора модели.** `get_runtime_base_url()`, `get_headers()`, `get_model()` не получают язык. Язык проходит через публичные входы `BaseLLMTranslation`: `download_multiple_translations` / `download_pending_translations` (`weblate/machinery/llm.py:2277-2303`) и их async-пары `adownload_*` (`llm.py:2308-2333`). Все четыре делегируют во внутренние `_download_multiple_translations` / `_adownload_*`.
2. **Модель запрашивается внутри fetch.** `fetch_llm_translations` → `self.get_traced_model()` → `self.get_model()`; async — `aget_traced_model()` → `aget_model()` (`weblate/machinery/openai.py:37-61`, `llm.py` `get_traced_model`). Инстанс движка создаётся на каждый запрос (`weblate/machinery/views.py:494,566`), поэтому instance-атрибут для языка безопасен.
3. **Сток `get_model()` ходит в `GET {base_url}/models` и валидирует выбор** (`openai.py:119-131`). Нам это не нужно: роутер возвращает model id напрямую → `/models` не дёргается вообще.
4. **`is_supported` у LLM-движков всегда `True`** (`llm.py:274-275`). Переопределяем: нет маршрута и нет `"*"` → `False` (движок не предлагается для этого языка).
5. **Форма.** `BaseOpenAIMachineryForm = KeyMachineryForm + LLMBasicMachineryForm` (`weblate/machinery/forms.py:570`): уже есть `key`, `base_url`, `model` (CharField, optional), `persona`, `style`, `language_instructions`. Готовый образец валидации JSON-словаря «код языка → строка» — `clean_language_instructions` (`forms.py:518-567`): `validate_language_code` + `Language.objects.fuzzy_get_strict`. Поле-тип: `EmptyMappingJSONField` (`forms.py:503`).
6. **Инстанцирование формы:** `service.settings_form(service, data=..., allow_private_targets=...)` (`weblate/machinery/models.py:71-75`). Наследование от `BaseOpenAIMachineryForm` это покрывает.
7. **Регистрация:** `WEBLATE_MACHINERY = list(DEFAULT_WEBLATE_MACHINERY); modify_env_list(WEBLATE_MACHINERY, "MACHINERY")` (`weblate/settings_docker.py:1312-1313`) → env `WEBLATE_ADD_MACHINERY`.
8. **Scope конфига:** глобально `/manage/machinery/` → `Setting` (scope MT); на проект `/machinery/<project>/` → `project.machinery_settings`; merge с override/disable — `Project.get_machinery_settings` (`weblate/trans/models/project.py:1332-1347`).
9. **Тестовый паттерн:** `OpenAITranslationTest` (`weblate/machinery/tests.py:3789+`): инстанцирует класс с dict-конфигом, `http_mock.register("POST", url, json=...)`, зовёт `download_multiple_translations("en", "fr", [("Hello", None)])`; тела запросов доступны через `http_mock.calls` (`tests.py:372`). `./rundev.sh test <path>` запускает pytest в контейнере с `weblate.settings_test` и БД (`rundev.sh:49-59`).
10. **Деплой кастомизации:** контейнер НЕ устанавливает пакет; модуль копируется в `dev-docker/data/python/` (= `/app/data/python` в `sys.path`). Правка кода без `cp` не видна ни рантайму, ни тестам.
11. **Матчинг кодов языков:** разделители в кодах — `[-_@]` (паттерн `LANGUAGE_CODE_PART_RE`, `llm.py:175`); в тестах встречается `zh-TW`, в данных — `zh_Hans`, `pt_BR`.

## Параметры (согласованы)

Модели согласованы с владельцем 2026-08-05 по итогам ресеча бенчмарков (субагент MTBenchResearch: WMT25, FLORES-200/COMET, Round-Trip, GAS 2026). Kimi K2 отклонён: 8/8 в Round-Trip, середняк на FLORES ZH⇔XX — переводческие бенчмарки его не поддерживают. **Id моделей — это конфиг, не код**: они попадают только в рекомендуемую конфигурацию и фикстуры тестов.

| Параметр | Значение (подтверждено) | Обоснование |
|---|---|---|
| `CJK_MODEL` (ja/ko/zh) | `deepseek/deepseek-chat-v3.1` ($0.25/$0.95 за 1M) | Лидер XX→ZH (COMET 85.27, arxiv 2507.13618); дешевле Kimi вдвое |
| `REST_MODEL` (`"*"`) | `google/gemini-2.5-flash` ($0.30/$2.50 за 1M) | Линейка выиграла WMT25; Flash валидирован на игровых диалогах (GAS 2026) |
| `base_url` | `https://openrouter.ai/api/v1` | Единый провайдер, один ключ |

Рекомендуемый конфиг движка (вводится в UI после деплоя):

```json
{
  "key": "<ключ OpenRouter — вводит владелец в UI>",
  "base_url": "https://openrouter.ai/api/v1",
  "routing": {
    "ja": "deepseek/deepseek-chat-v3.1",
    "ko": "deepseek/deepseek-chat-v3.1",
    "zh": "deepseek/deepseek-chat-v3.1",
    "*": "google/gemini-2.5-flash"
  }
}
```

Семантика `routing`: ключ — код языка Weblate (`ja`, `zh_Hans`, `pt_BR`, …) или `"*"` (фолбэк). Матчинг: точное совпадение (без учёта регистра, `-`≡`_`) → базовый код (`zh_Hans` → `zh`) → `"*"`. Нет совпадения и нет `"*"` → язык не поддерживается движком (не показывается в подсказках). Это даёт «гибко»: на проекте можно override'ом сузить/сменить карту (факт 8).

---

### Task 1: Каркас модуля и форма с валидацией routing

**Files:**
- Create: `weblate_customization/src/weblate_customization/machinery.py`
- Create: `weblate_customization/tests/__init__.py` (пустой)
- Test: `weblate_customization/tests/test_machinery.py`

**Step 1: Написать падающие тесты формы**

`weblate_customization/tests/test_machinery.py`:

```python
"""Tests for the Routed LLM machinery."""

from __future__ import annotations

import json

from django.test import SimpleTestCase, TestCase

from weblate_customization.machinery import (
    RoutedLLMMachineryForm,
    RoutedLLMTranslation,
)

DEEPSEEK = "deepseek/deepseek-chat-v3.1"  # sync with plan: CJK_MODEL
GEMINI = "google/gemini-2.5-flash"        # sync with plan: REST_MODEL

CONFIGURATION = {
    "key": "test-key",
    "base_url": "https://openrouter.ai/api/v1",
    "routing": {"ja": DEEPSEEK, "ko": DEEPSEEK, "zh": DEEPSEEK, "*": GEMINI},
    "persona": "",
    "style": "",
}


class RoutedLLMFormTest(TestCase):
    # TestCase (не Simple): валидация кодов языков ходит в БД (fuzzy_get_strict)

    def validate(self, routing):
        form = RoutedLLMMachineryForm(
            RoutedLLMTranslation,
            data={**CONFIGURATION, "routing": routing},
        )
        return form

    def test_valid_routing(self) -> None:
        self.assertTrue(self.validate(CONFIGURATION["routing"]).is_valid())

    def test_star_only(self) -> None:
        self.assertTrue(self.validate({"*": GEMINI}).is_valid())

    def test_rejects_empty(self) -> None:
        self.assertFalse(self.validate({}).is_valid())

    def test_rejects_non_dict(self) -> None:
        self.assertFalse(self.validate(["ja", DEEPSEEK]).is_valid())

    def test_rejects_bad_language(self) -> None:
        self.assertFalse(self.validate({"nosuchlang": DEEPSEEK}).is_valid())

    def test_rejects_empty_model(self) -> None:
        self.assertFalse(self.validate({"ja": ""}).is_valid())
```

**Step 2: Запустить — убедиться, что падают (модуля нет)**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py
```
Ожидание: `ImportError`/`ModuleNotFoundError: weblate_customization.machinery`.

**Step 3: Минимальная реализация — форма**

`weblate_customization/src/weblate_customization/machinery.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Routed LLM: один движок, модель выбирается по целевому языку.

Все модели ходят через OpenRouter (OpenAI-совместимый API). Таблица
роутинга живёт в конфиге движка (UI), а не в коде:
    {"ja": "<model>", "zh": "<model>", "*": "<fallback model>"}
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext, pgettext_lazy

from weblate.lang.models import Language
from weblate.machinery.forms import BaseOpenAIMachineryForm, EmptyMappingJSONField
from weblate.machinery.openai import OpenAITranslation
from weblate.utils.validators import validate_language_code

# Разделители в кодах языков, как в weblate/machinery/llm.py
LANGUAGE_CODE_PART_RE = re.compile(r"[-_@]")

FALLBACK_KEY = "*"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class RoutedLLMMachineryForm(BaseOpenAIMachineryForm):
    routing = EmptyMappingJSONField(
        label=pgettext_lazy(
            "Automatic suggestion service configuration",
            "Model routing by language",
        ),
        help_text=(
            'JSON object: target language code (or "*" fallback) to '
            "OpenRouter model ID."
        ),
        widget=None,  # заменяется Textarea ниже, как у language_instructions
    )

    def clean_routing(self) -> dict[str, str]:
        value = self.cleaned_data["routing"]
        if not isinstance(value, dict) or not value:
            raise ValidationError(
                gettext("Routing must be a non-empty JSON object.")
            )
        result: dict[str, str] = {}
        for key, model in value.items():
            if not isinstance(key, str) or not isinstance(model, str):
                raise ValidationError(
                    gettext("Routing must map language codes to model IDs.")
                )
            if not model.strip():
                raise ValidationError(
                    gettext('Routing model for "%s" must not be empty.') % key
                )
            if key != FALLBACK_KEY:
                validate_language_code(key)
                if Language.objects.fuzzy_get_strict(key) is None:
                    raise ValidationError(
                        gettext('Routing contains unknown language code "%s".')
                        % key
                    )
            result[key] = model.strip()
        return result
```

Примечание для исполнителя: `widget=None` — проверь, как `EmptyMappingJSONField` объявлен у `language_instructions` (`weblate/machinery/forms.py:503-516`), и повтори тот же способ задать `forms.Textarea`. Не изобретай свой виджет.

**Step 4: Прогнать тесты формы**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py
```
Ожидание: тесты формы PASS (тестов класса ещё нет). Если `RoutedLLMTranslation` не определён — временно закомментируй его импорт/тесты? Нет: добавь в Step 3 заглушку класса из Task 2 Step 3 (пустой subclass с `name`), чтобы импорт работал; это 3 строки, они станут настоящим классом в Task 2.

**Step 5: Commit**

```sh
git add weblate_customization/ && git commit -m "feat(customization): routed LLM machinery form with routing validation"
```

---

### Task 2: Резолвер маршрута (чистая логика, без HTTP)

**Files:**
- Modify: `weblate_customization/src/weblate_customization/machinery.py`
- Test: `weblate_customization/tests/test_machinery.py` (дописать)

**Step 1: Падающие тесты резолвера**

Дописать в `test_machinery.py`:

```python
class RoutedResolveTest(SimpleTestCase):
    def machine(self, routing=None) -> RoutedLLMTranslation:
        conf = dict(CONFIGURATION)
        if routing is not None:
            conf["routing"] = routing
        return RoutedLLMTranslation(conf)

    def test_exact(self) -> None:
        self.assertEqual(self.machine().resolve_model("ja"), DEEPSEEK)

    def test_base_code(self) -> None:
        # zh_Hans / zh-TW сводятся к zh
        self.assertEqual(self.machine().resolve_model("zh_Hans"), DEEPSEEK)
        self.assertEqual(self.machine().resolve_model("zh-TW"), DEEPSEEK)

    def test_separator_and_case_insensitive(self) -> None:
        m = self.machine({"pt_BR": DEEPSEEK, "*": GEMINI})
        self.assertEqual(m.resolve_model("pt-br"), DEEPSEEK)

    def test_fallback(self) -> None:
        self.assertEqual(self.machine().resolve_model("fr"), GEMINI)

    def test_no_route_no_fallback(self) -> None:
        m = self.machine({"ja": DEEPSEEK})
        self.assertIsNone(m.resolve_model("fr"))

    def test_is_supported_follows_routing(self) -> None:
        m = self.machine({"ja": DEEPSEEK})
        self.assertTrue(m.is_supported("en", "ja"))
        self.assertFalse(m.is_supported("en", "fr"))
```

**Step 2: Запустить — падают** (`resolve_model` нет)

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py
```

**Step 3: Реализация класса с резолвером**

Дописать в `machinery.py`:

```python
class RoutedLLMTranslation(OpenAITranslation):
    """OpenRouter LLM with per-language model routing."""

    name = "Routed LLM"
    settings_form = RoutedLLMMachineryForm

    def get_runtime_base_url(self) -> str:
        return self.settings.get("base_url") or DEFAULT_BASE_URL

    # --- маршрутизация ---

    def resolve_model(self, target_language: str | None) -> str | None:
        routing: dict[str, str] = self.settings.get("routing") or {}
        if not target_language:
            return routing.get(FALLBACK_KEY)
        normalized = {
            key.lower().replace("-", "_"): model
            for key, model in routing.items()
        }
        code = target_language.lower().replace("-", "_")
        if code in normalized:
            return normalized[code]
        base = LANGUAGE_CODE_PART_RE.split(code)[0]
        if base in normalized:
            return normalized[base]
        return routing.get(FALLBACK_KEY)

    def is_supported(self, source_language, target_language) -> bool:
        return self.resolve_model(target_language) is not None
```

**Step 4: Прогнать — PASS. Step 5: Commit**

```sh
git add weblate_customization/ && git commit -m "feat(customization): per-language model resolver for routed LLM"
```

---

### Task 3: Перехват целевого языка и подмена модели в запросе

Это ядро. Язык фиксируем в четырёх публичных download-входах (факт 1), модель отдаём в `get_model`/`aget_model` (факт 2), `/models` не дёргаем (факт 3).

**Files:**
- Modify: `weblate_customization/src/weblate_customization/machinery.py`
- Test: `weblate_customization/tests/test_machinery.py` (дописать)

**Step 1: Падающий HTTP-тест роутинга**

Дописать в `test_machinery.py` (паттерн — `OpenAITranslationTest`, `weblate/machinery/tests.py:3789+`):

```python
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def mock_chat(content: str = '["Привет"]') -> None:
    http_mock.register(
        "POST",
        CHAT_URL,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "irrelevant",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def sent_models() -> list[str]:
    return [
        json.loads(call.request.body)["model"]
        for call in http_mock.calls
        if call.request.url == CHAT_URL
    ]


class RoutedDownloadTest(TestCase):
    def machine(self) -> RoutedLLMTranslation:
        machine = RoutedLLMTranslation(dict(CONFIGURATION))
        machine.delete_cache()
        machine.cache_translations = False
        return machine

    @http_mock.activate
    def test_cjk_goes_to_deepseek(self) -> None:
        mock_chat()
        result = self.machine().download_multiple_translations(
            "en", "ja", [("Hello", None)]
        )
        self.assertTrue(result)
        self.assertEqual(sent_models(), [DEEPSEEK])

    @http_mock.activate
    def test_other_goes_to_gemini(self) -> None:
        mock_chat()
        self.machine().download_multiple_translations(
            "en", "fr", [("Hello", None)]
        )
        self.assertEqual(sent_models(), [GEMINI])

    @http_mock.activate
    def test_no_models_endpoint_call(self) -> None:
        mock_chat()
        self.machine().download_multiple_translations(
            "en", "ja", [("Hello", None)]
        )
        self.assertTrue(
            all("/models" not in str(call.request.url) for call in http_mock.calls)
        )
```

Примечание: точную форму ответа-мока сверь со стоковым `mock_response` (`tests.py:3827-3854`) — если парсеру нужны поля `usage`/`created`, добавь их.

**Step 2: Запустить — падают** (модель берётся стоковым `get_model` → лезет в `/models`, не замокан → ошибка).

**Step 3: Реализация перехвата**

Дописать в `RoutedLLMTranslation`:

```python
    # Язык фиксируется на входе (инстанс живёт один запрос — факт 2)

    def _set_route(self, target_language) -> None:
        self._route_target = target_language

    def download_multiple_translations(
        self, source_language, target_language, sources, user=None, threshold=75
    ):
        self._set_route(target_language)
        return super().download_multiple_translations(
            source_language, target_language, sources, user, threshold
        )

    def download_pending_translations(
        self, source_language, target_language, sources, user=None, threshold=75
    ):
        self._set_route(target_language)
        return super().download_pending_translations(
            source_language, target_language, sources, user, threshold
        )

    async def adownload_multiple_translations(
        self, source_language, target_language, sources, user=None, threshold=75
    ):
        self._set_route(target_language)
        return await super().adownload_multiple_translations(
            source_language, target_language, sources, user, threshold
        )

    async def adownload_pending_translations(
        self, source_language, target_language, sources, user=None, threshold=75
    ):
        self._set_route(target_language)
        return await super().adownload_pending_translations(
            source_language, target_language, sources, user, threshold
        )

    # --- модель: без /models, напрямую из маршрута ---

    def get_model(self) -> str:
        model = self.resolve_model(getattr(self, "_route_target", None))
        if model is None:
            msg = "No routed model for language"
            raise MachineTranslationError(msg)
        return model

    async def aget_model(self) -> str:
        return self.get_model()
```

Импорт добавить: `from weblate.machinery.base import MachineTranslationError`.

Сигнатуры `download_*`/`threshold` сверь с `weblate/machinery/llm.py:2277-2333` (там default из `MACHINERY_DEFAULT_THRESHOLD` — используй его же, импорт из `weblate.machinery.base`... проверь фактический модуль константы grep'ом `MACHINERY_DEFAULT_THRESHOLD`).

**Step 4: Прогнать все тесты файла — PASS.**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_machinery.py
```

**Step 5: Линт + commit**

```sh
uv run prek run --files weblate_customization/src/weblate_customization/machinery.py weblate_customization/tests/test_machinery.py
git add weblate_customization/ && git commit -m "feat(customization): route model per target language in routed LLM"
```

---

### Task 4: Регистрация в dev-инстансе и smoke-тест в UI

**Files:**
- Modify: `dev-docker/docker-compose.yml` (сервис `weblate`, блок `environment`)

**Step 1: Задеплоить модуль и включить движок**

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

В `dev-docker/docker-compose.yml`, сервис `weblate`, в `environment:` добавить:

```yaml
      WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation
```

**Step 2: Перезапустить и проверить загрузку**

```sh
WEBLATE_PORT=3001 ./rundev.sh
./rundev.sh check
```
Ожидание: `weblate check` без ошибок про MACHINERY/import. Если ImportError — смотреть `./rundev.sh logs -f weblate`.

**Step 3: Настроить в UI (глобально)**

<http://localhost:3001/manage/machinery/> → карточка **Routed LLM** → Настроить:
- API-ключ: ключ OpenRouter (вводит владелец),
- API URL: `https://openrouter.ai/api/v1`,
- Model routing by language: JSON из раздела «Параметры».

Ожидание: форма сохраняется; движок в списке «Настроены».

**Step 4: Smoke в редакторе**

Открыть строку японского перевода (`/translate/<project>/<component>/ja/?offset=1`) → вкладка «Автоматические предложения»: появляется вариант от «Routed LLM». Затем французская строка — тоже. Проверка маршрута: `./rundev.sh logs weblate | grep -i openrouter` или счётчик использования моделей в дашборде OpenRouter (deepseek для ja, gemini для fr).

Негативный smoke: временно убрать `"*"` из routing в UI → на fr движок исчезает из подсказок (факт: `is_supported` → False), на ja остаётся. Вернуть `"*"`.

**Step 5: Commit**

```sh
git add dev-docker/docker-compose.yml && git commit -m "chore(dev-docker): register routed LLM machinery"
```

---

### Task 5: Документация форка

**Files:**
- Modify: `AGENTS.md` (раздел «Project-specific setup», абзац про `weblate_customization/`)
- Modify: `README.rst` (раздел «Что добавлено поверх upstream», описание `weblate_customization/`; и упомянуть в разделе про game-markup, что механизм `WEBLATE_ADD_*` используется и для movinery)

**Step 1:** В `AGENTS.md` дописать в абзац про `weblate_customization/`: пакет теперь ships `GameMarkupCheck` **и** `RoutedLLMTranslation` (`machinery.py`) — LLM-движок с роутингом модель-по-языку через OpenRouter; активация `WEBLATE_ADD_MACHINERY`; деплой тем же `cp -r` в `data/python`.

**Step 2:** В `README.rst` то же самое одним-двумя предложениями в стиле существующего текста (reST, русский).

**Step 3:** Проверить reST:

```sh
python3 -c "import docutils.core; docutils.core.publish_doctree(open('README.rst',encoding='utf-8').read(), settings_overrides={'report_level':2}); print('RST OK')"
```

**Step 4: Commit**

```sh
git add AGENTS.md README.rst && git commit -m "docs: document routed LLM machinery in fork docs"
```

---

## Что НЕ делаем (YAGNI)

- Никаких per-language ключей/базовых URL — один провайдер (OpenRouter), одна форма. Понадобится второй провайдер — расширим значение routing до объекта, отдельной задачей.
- Не трогаем `docs/changes.rst` — это upstream-changelog, а изменение локально для форка.
- Не регистрируем движок в production-конфиге — только dev-compose; продовый деплой отдельным решением.
- Не пишем тесты на стоковое поведение `OpenAITranslation` (промпт, парсинг ответа) — это upstream-контракт.

## Риски / что проверить по ходу

1. **`EmptyMappingJSONField` виджет** — повторить объявление как у `language_instructions`, не изобретать.
2. **Форма ответа мока** — сверить с `mock_response` стокового теста; при падении парсинга добавить `usage`/`created`.
3. **`MACHINERY_DEFAULT_THRESHOLD`** — найти фактический модуль константы перед импортом (grep).
4. **Ключ в БД открытым текстом** — стоковое поведение всех движков; учитывать при бэкапах (уже упомянуто в README-разделе про VPS? нет — не relevant, пропустить).
5. **Валидация настроек при сохранении** (`test_validate_settings`-путь) может дёрнуть download с тестовой строкой — маршрут возьмётся штатно через `download_multiple_translations`; если валидатор пойдёт без языка — `get_model` бросит `MachineTranslationError`, это видимая ошибка формы, не тихий фейл. Если всплывёт — зафиксировать язык валидации фолбэком `"*"`.
