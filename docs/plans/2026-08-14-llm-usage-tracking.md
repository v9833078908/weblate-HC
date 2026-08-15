# LLM Usage Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Записывать в Postgres точные токены и стоимость каждого LLM-запроса OpenRouter, с разбивкой по модели и проекту, и отдавать их командой `llm_usage_report`.

**Architecture:** Точная стоимость уже приходит в теле каждого ответа OpenRouter (`usage.cost`, кредиты = USD). Сейчас `BaseOpenAITranslation.fetch_llm_translations` читает из ответа только `choices` и выбрасывает `usage`. План: писать `usage` в новую модель `LLMUsageLog` в единственном шве, где парсится тело ответа; `RoutedLLMTranslation` наследует этот шов без изменений. Проект берётся из `settings["_project"]` (project-scoped конфигурация, `Project.get_machinery_settings` кладёт его сам), а для site-wide конфигурации - из первого юнита батча через `ContextVar`, выставляемый в `_fetch_llm_batch`/`_afetch_llm_batch`.

**Tech Stack:** Django ORM, asgiref `sync_to_async` (async-путь), `contextvars.ContextVar` (прецедент: `weblate_customization/machinery.py`), http_mock в тестах.

Статус: **реализован и проверен 2026-08-15**. Прошёл ревью; правки
ревью влиты (project_slug, убран `service`, явная цепочка извлечения
`usage`, `response_id` 255, счётчик unpriced в отчёте, тест наследования
RoutedLLM, тест `usage` без поля `cost`).

Обоснование «не Langfuse/LiteLLM/Helicone» - отчёт `agent://LLMCostResearch`: внешняя система пересчитала бы стоимость по своей прайс-таблице и *снизила* точность; источник истины - кредиты, которые списывает сам OpenRouter.

---

### Task 1: Модель `LLMUsageLog`

**Files:**
- Create: `weblate/trans/models/llm_usage.py`
- Modify: `weblate/trans/models/__init__.py:34` (импорт после `loc_kit`, перед `pending`)
- Create: `weblate/trans/migrations/0100_llmusagelog.py` (генерируется)
- Test: `weblate/trans/tests/test_llm_usage.py`

**Step 1: Write the failing test**

`weblate/trans/tests/test_llm_usage.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from weblate.trans.models.llm_usage import LLMUsageLog


class LLMUsageLogModelTest(TestCase):
    def test_create_with_cost(self) -> None:
        log = LLMUsageLog.objects.create(
            model="google/gemini-2.5-flash",
            project_slug="col4",
            prompt_tokens=9,
            completion_tokens=12,
            total_tokens=21,
            cost_usd=Decimal("0.00001234"),
            response_id="chatcmpl-123",
            cached_tokens=4,
        )
        self.assertEqual(log.total_tokens, 21)
        self.assertEqual(log.cost_usd, Decimal("0.00001234"))
        self.assertEqual(log.reasoning_tokens, 0)

    def test_create_unpriced(self) -> None:
        log = LLMUsageLog.objects.create(model="gpt-5.4-nano", prompt_tokens=5)
        self.assertIsNone(log.cost_usd)
        self.assertEqual(log.project_slug, "")
        self.assertEqual(log.completion_tokens, 0)
```

**Step 2: Run test to verify it fails**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL `ModuleNotFoundError: No module named 'weblate.trans.models.llm_usage'`

**Step 3: Write the model**

`weblate/trans/models/llm_usage.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import models


class LLMUsageLog(models.Model):
    """
    One record per LLM chat-completions request.

    Written at the single seam where the response body is parsed
    (``BaseOpenAITranslation.fetch_llm_translations``), so the token counts and
    the cost are exactly what OpenRouter billed, not a local price-table
    estimate. ``cost_usd`` is null when the provider reports no cost (observed
    for the gpt-5.4 tiers); tokens are always stored so the cost can be
    reconstructed from the OpenRouter price list later.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    model = models.CharField(max_length=200, db_index=True)
    project_slug = models.CharField(max_length=200, blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(
        max_digits=12, decimal_places=8, null=True, blank=True
    )
    response_id = models.CharField(max_length=255, blank=True)
    cached_tokens = models.IntegerField(default=0)
    reasoning_tokens = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.model} {self.total_tokens} tokens ${self.cost_usd}"
```

В `weblate/trans/models/__init__.py` после строки 34 добавить:

```python
from weblate.trans.models.llm_usage import LLMUsageLog
```

**Step 4: Generate the migration**

Run: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations trans --name llmusagelog`
Expected: создаёт `weblate/trans/migrations/0100_llmusagelog.py` с `CreateModel LLMUsageLog`, dependency `0099_loc_kit_draft_target_component`. БД не нужна. Проверить, что содержимое соответствует полям модели (включая `options={"ordering": ["-created_at"]}`).

**Step 5: Run test to verify it passes**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: 2 passed

**Step 6: Commit**

```bash
git add weblate/trans/models/llm_usage.py weblate/trans/models/__init__.py weblate/trans/migrations/0100_llmusagelog.py weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): add LLMUsageLog model for token and cost accounting"
```

---

### Task 2: ContextVar проекта в llm.py

Смысл: для site-wide конфигурации `settings["_project"]` нет; проект батча известен в `_fetch_llm_batch`/`_afetch_llm_batch`, где лежат `sources` с юнитами. `ContextVar` корректно изолирует конкурентные батчи (`batch_concurrency=2`). Прецедент в коде: `ContextVar` в `weblate_customization/machinery.py:11`.

**Files:**
- Modify: `weblate/machinery/llm.py:2715` (`_fetch_llm_batch`) и `:2825` (`_afetch_llm_batch`)

**Step 1: Add the ContextVar and helper**

В `weblate/machinery/llm.py` к stdlib-импортам добавить `from contextvars import ContextVar`; после блока импортов:

```python
#: Project slug of the batch currently fetching, for usage accounting at the
#: HTTP seam, which does not receive the batch units.
llm_batch_project: ContextVar[str] = ContextVar("llm_batch_project", default="")


def _sources_project_slug(sources: list[tuple[str, Unit | None]]) -> str:
    """Project slug of a batch, from the first unit that carries one."""
    for _text, unit in sources:
        if unit is None:
            continue
        try:
            return unit.translation.component.project.slug
        except AttributeError:
            return ""
    return ""
```

**Step 2: Wrap both fetch calls**

В `_fetch_llm_batch` заменить тело после `_prepare_llm_translation` на:

```python
        project_token = llm_batch_project.set(_sources_project_slug(sources))
        try:
            translations_string = self.fetch_llm_translations(
                prompt, content, previous_content, previous_response
            )
        finally:
            llm_batch_project.reset(project_token)
```

В `_afetch_llm_batch` аналогично вокруг `await self.afetch_llm_translations(...)`:

```python
        project_token = llm_batch_project.set(_sources_project_slug(sources))
        try:
            translations_string = await self.afetch_llm_translations(
                prompt, content, previous_content, previous_response
            )
        finally:
            llm_batch_project.reset(project_token)
```

**Step 3: Verify no regressions**

Run: `./rundev.sh test weblate/machinery/tests.py -k "OpenAITranslationTest"`
Expected: все существующие тесты класса зелёные (поведение не изменилось).

**Step 4: Commit**

```bash
git add weblate/machinery/llm.py
git commit -m "feat(machinery): expose current batch project via context var"
```

---

### Task 3: Захват `usage` в шве openai.py

**Files:**
- Modify: `weblate/machinery/openai.py:86-110` (`fetch_llm_translations`, `afetch_llm_translations`)
- Test: `weblate/machinery/tests.py` (класс `OpenAITranslationTest`, после `test_async_translate`)

**Step 1: Write the failing tests**

В `OpenAITranslationTest` добавить мок с полным `usage` и пять тестов:

```python
    def mock_response_priced(self) -> None:
        self.mock_models()
        http_mock.register(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            json={
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": self.TRACE_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '["Ahoj světe"]'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 12,
                    "total_tokens": 21,
                    "cost": 0.00001234,
                    "prompt_tokens_details": {
                        "cached_tokens": 4,
                        "cache_write_tokens": 0,
                    },
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    @http_mock.activate
    def test_usage_recorded(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        self.mock_response_priced()
        self.assert_translate(self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN)
        log = LLMUsageLog.objects.get()
        self.assertEqual(log.model, self.TRACE_MODEL)
        self.assertEqual(log.prompt_tokens, 9)
        self.assertEqual(log.completion_tokens, 12)
        self.assertEqual(log.total_tokens, 21)
        self.assertEqual(log.cost_usd, Decimal("0.00001234"))
        self.assertEqual(log.response_id, "chatcmpl-123")
        self.assertEqual(log.cached_tokens, 4)
        self.assertEqual(log.project_slug, "")

    @http_mock.activate
    def test_usage_recorded_async(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        self.mock_response_priced()
        self.assert_async_translate(
            self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN
        )
        self.assertEqual(LLMUsageLog.objects.count(), 1)

    @http_mock.activate
    def test_usage_cost_zero_is_unpriced(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        self.mock_response()  # usage without cost, see existing mock
        self.assert_translate(self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN)
        log = LLMUsageLog.objects.get()
        self.assertEqual(log.prompt_tokens, 9)
        self.assertIsNone(log.cost_usd)

    @http_mock.activate
    def test_usage_missing_means_no_record(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        self.mock_models()
        http_mock.register(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            json={
                "id": "chatcmpl-err",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '["Ahoj"]'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
        self.assert_translate(self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN)
        self.assertEqual(LLMUsageLog.objects.count(), 0)

    @http_mock.activate
    def test_usage_record_failure_does_not_break_translation(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        self.mock_response_priced()
        with patch.object(
            LLMUsageLog._default_manager,  # ruff: ignore[private-member-access]
            "create",
            side_effect=DatabaseError("boom"),
        ):
            translation = self.assert_translate(
                self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN
            )
        self.assertTrue(translation)
        self.assertEqual(LLMUsageLog.objects.count(), 0)
```

Импорты в начало тестовой секции файла (проверить наличие): `from decimal import Decimal`, `from django.db import DatabaseError`, `from unittest.mock import patch`.

**Step 2: Run tests to verify they fail**

Run: `./rundev.sh test weblate/machinery/tests.py -k "usage"`
Expected: FAIL (`AttributeError: ... no attribute 'record_llm_usage'` нет, но записи `LLMUsageLog` не создаются → `DoesNotExist` / count mismatch)

**Step 3: Implement the seam**

В `weblate/machinery/openai.py`:
- импорты: `from decimal import Decimal`, `from typing import Any` (расширить `from typing import ClassVar`), `from asgiref.sync import sync_to_async`; в существующий `from .llm import BaseLLMTranslation` добавить `llm_batch_project`.
- заменить оба метода и добавить новый:

```python
    def fetch_llm_translations(
        self, prompt: str, content: str, previous_content: str, previous_response: str
    ) -> str | None:
        model = self.get_traced_model()
        response = self.request(
            "post",
            self.get_chat_completions_url(),
            json=self.get_chat_payload(
                model, prompt, content, previous_content, previous_response
            ),
        )
        payload = response.json()
        self.record_llm_usage(payload, model)
        return self.parse_chat_response(payload)

    async def afetch_llm_translations(
        self, prompt: str, content: str, previous_content: str, previous_response: str
    ) -> str | None:
        model = await self.aget_traced_model()
        response = await self.arequest(
            "post",
            self.get_chat_completions_url(),
            json=self.get_chat_payload(
                model, prompt, content, previous_content, previous_response
            ),
        )
        payload = response.json()
        await sync_to_async(self.record_llm_usage, thread_sensitive=False)(
            payload, model
        )
        return self.parse_chat_response(payload)

    def record_llm_usage(self, payload: dict[str, Any], model: str) -> None:
        """
        Persist the token usage and cost OpenRouter billed for this request.

        Never raises: a broken accounting write must not break a translation,
        and the exception is logged so a broken table is visible in the log.
        """
        try:
            if not isinstance(payload, dict):
                return
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                return
            prompt_tokens = usage.get("prompt_tokens") or 0
            completion_tokens = usage.get("completion_tokens") or 0
            total_tokens = usage.get("total_tokens") or (
                prompt_tokens + completion_tokens
            )
            if not prompt_tokens and not completion_tokens:
                return
            cost = usage.get("cost")
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            project = self.settings.get("_project")
            if project is not None:
                project_slug = project.slug
            else:
                project_slug = llm_batch_project.get()
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.llm_usage import LLMUsageLog

            LLMUsageLog.objects.create(
                model=model,
                project_slug=project_slug,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=Decimal(str(cost)) if cost else None,
                response_id=str(payload.get("id") or ""),
                cached_tokens=prompt_details.get("cached_tokens") or 0,
                reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
            )
        except Exception:
            LOGGER.exception("Failed to record LLM usage")
```

Импорт `LLMUsageLog` намеренно ленивый: `weblate.trans.models` тянет приложение trans, а machinery не должна получать цикл импортов на старте.

**Step 4: Run tests to verify they pass**

Run: `./rundev.sh test weblate/machinery/tests.py -k "OpenAITranslationTest"`
Expected: все тесты класса зелёные, включая пять новых.

**Step 5: Commit**

```bash
git add weblate/machinery/openai.py weblate/machinery/tests.py
git commit -m "feat(machinery): record OpenRouter token usage and cost per request"
```

---

### Task 4: Тест наследования RoutedLLMTranslation

Смысл: `RoutedLLMTranslation` сегодня наследует `fetch_llm_translations` чисто (проверено grep - не переопределяет). Будущий рефакторинг с переопределением сломал бы учёт молча; тест фиксирует контракт.

**Files:**
- Modify: `weblate_customization/tests/test_machinery.py` (класс `RoutedDownloadTest`)

**Step 1: Write the failing-then-passing test**

В `RoutedDownloadTest` добавить:

```python
    @http_mock.activate
    def test_usage_recorded_through_inherited_seam(self) -> None:
        from weblate.trans.models.llm_usage import LLMUsageLog

        mock_chat()  # usage 9/2/11, no cost
        self.machine().download_multiple_translations("en", "ja", [("Hello", None)])
        log = LLMUsageLog.objects.get()
        self.assertEqual(log.model, DEEPSEEK)
        self.assertEqual(log.prompt_tokens, 9)
        self.assertEqual(log.completion_tokens, 2)
        self.assertIsNone(log.cost_usd)
        self.assertEqual(log.project_slug, "")
```

Тест пройдёт сразу после Task 3 (шов унаследован); его ценность - детект будущей регрессии. Мутационная проверка: временно добавить в `RoutedLLMTranslation` пустое переопределение `fetch_llm_translations`, вернувшее `super()` без `record_llm_usage`, - тест обязан упасть; откатить мутацию.

**Step 2: Run**

Run: `./rundev.sh test weblate_customization/tests/test_machinery.py`
(если раннер не принимает путь вне `weblate/`: `docker compose -f dev-docker/docker-compose.yml exec weblate pytest /app/src/weblate_customization/tests/test_machinery.py`)
Expected: зелёные, включая новый.

**Step 3: Commit**

```bash
git add weblate_customization/tests/test_machinery.py
git commit -m "test(customization): pin usage recording through the inherited seam"
```

---

### Task 5: Команда `llm_usage_report`

**Files:**
- Create: `weblate/trans/management/commands/llm_usage_report.py`
- Test: `weblate/trans/tests/test_llm_usage.py` (добавить класс)

**Step 1: Write the failing test**

В `weblate/trans/tests/test_llm_usage.py` добавить:

```python
from io import StringIO

from django.core.management import call_command


class LLMUsageReportTest(TestCase):
    def setUp(self) -> None:
        LLMUsageLog.objects.create(
            model="m1",
            project_slug="col4",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=Decimal("0.001"),
        )
        LLMUsageLog.objects.create(
            model="m1", project_slug="col4", prompt_tokens=4, completion_tokens=1,
            total_tokens=5,
        )
        LLMUsageLog.objects.create(
            model="m2", project_slug="st2", prompt_tokens=7, completion_tokens=3,
            total_tokens=10, cost_usd=Decimal("0.0000005"),
        )

    def test_table_report(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", stdout=out)
        text = out.getvalue()
        self.assertIn("m1", text)
        self.assertIn("col4", text)
        self.assertIn("14", text)  # prompt sum for m1
        self.assertIn("0.001", text)
        self.assertIn("unpriced", text)

    def test_csv_report(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--format", "csv", stdout=out)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(
            lines[0],
            "model,project,requests,prompt_tokens,completion_tokens,cost_usd,unpriced",
        )
        self.assertEqual(len(lines), 3)

    def test_days_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--days", "1", stdout=out)
        self.assertIn("m1", out.getvalue())

    def test_model_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--model", "m2", stdout=out)
        text = out.getvalue()
        self.assertNotIn("m1", text)
        self.assertIn("st2", text)
```

**Step 2: Run test to verify it fails**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL `Unknown command: 'llm_usage_report'`

**Step 3: Implement the command**

`weblate/trans/management/commands/llm_usage_report.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Count, Q, Sum
from django.utils import timezone

from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

HEADER = [
    "model",
    "project",
    "requests",
    "prompt_tokens",
    "completion_tokens",
    "cost_usd",
    "unpriced",
]


class Command(BaseCommand):
    help = "reports LLM token usage and cost grouped by model and project"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days", type=int, default=None, help="only the last N days"
        )
        parser.add_argument("--model", default=None, help="only this model")
        parser.add_argument(
            "--project", default=None, help="only this project slug"
        )
        parser.add_argument("--format", choices=["table", "csv"], default="table")

    def handle(self, *args, **options) -> None:
        logs = LLMUsageLog.objects.all()
        if options["days"]:
            cutoff = timezone.now() - timedelta(days=options["days"])
            logs = logs.filter(created_at__gte=cutoff)
        if options["model"]:
            logs = logs.filter(model=options["model"])
        if options["project"]:
            logs = logs.filter(project_slug=options["project"])
        rows = list(
            logs.values("model", "project_slug")
            .annotate(
                requests=Count("id"),
                prompt=Sum("prompt_tokens"),
                completion=Sum("completion_tokens"),
                cost=Sum("cost_usd"),
                unpriced=Count("id", filter=Q(cost_usd__isnull=True)),
            )
            .order_by("model", "project_slug")
        )
        data = [
            [
                row["model"],
                row["project_slug"] or "-",
                row["requests"],
                row["prompt"] or 0,
                row["completion"] or 0,
                f"{row['cost']:.8f}" if row["cost"] is not None else "",
                row["unpriced"],
            ]
            for row in rows
        ]
        if options["format"] == "csv":
            writer = csv.writer(self.stdout)
            writer.writerow(HEADER)
            writer.writerows(data)
            return
        widths = [
            max(len(str(line[i])) for line in [HEADER, *data])
            for i in range(len(HEADER))
        ]
        for line in [HEADER, *data]:
            self.stdout.write(
                "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(line))
            )
```

Колонка `unpriced` - счётчик записей с `cost_usd IS NULL`: без неё `SUM(cost_usd)` молча недосчитывает долю неприценённых запросов (gpt-5.4).

**Step 4: Run tests to verify they pass**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: 6 passed (2 модельных + 4 команды)

**Step 5: Commit**

```bash
git add weblate/trans/management/commands/llm_usage_report.py weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): add llm_usage_report management command"
```

---

### Task 6: Changelog, lint, типы, полный прогон

**Files:**
- Modify: `docs/changes.rst` (текущая нерелизнутая секция, верх файла)

**Step 1: Changelog**

В верхнюю (нерелизнутую) секцию `docs/changes.rst` добавить:

```rst
* LLM machine translation now records per-request token usage and cost,
  reportable via the ``llm_usage_report`` management command.
```

**Step 2: Lint/format**

Run: `export PATH="/opt/homebrew/bin:$PATH" && uv run prek run --all-files`
Expected: без новых замечаний по изменённым файлам.

**Step 3: Type check**

Run: `uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh`
Expected: нет новых ошибок в `weblate/machinery/openai.py`, `weblate/machinery/llm.py`, `weblate/trans/models/llm_usage.py`, `weblate/trans/management/commands/llm_usage_report.py`.

**Step 4: Pylint**

Run: `uv run pylint weblate/machinery/openai.py weblate/machinery/llm.py weblate/trans/models/llm_usage.py weblate/trans/management/commands/llm_usage_report.py weblate/trans/tests/test_llm_usage.py`
Expected: 10.00/10 или только предсуществующие замечания.

**Step 5: Full suites**

Run: `./rundev.sh test weblate/machinery/tests.py` и `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: зелёные.

**Step 6: Commit**

```bash
git add docs/changes.rst
git commit -m "docs(changes): note LLM usage accounting"
```

---

## Риски и решения

- **Сбой записи роняет перевод** → `record_llm_usage` глотает все исключения, пишет `LOGGER.exception` (покрыто `test_usage_record_failure_does_not_break_translation`).
- **Обратный риск: перевод работает, учёт молча сломан** (например, миграция не применена) → окно в секунды, миграция и код едут одним деплоем; `LOGGER.exception` виден в логе. После деплоя проверить отсутствие `Failed to record LLM usage` в логе. Health-check не нужен.
- **ORM в async-пути** → прямой `objects.create` в корутине бросил бы `SynchronousOnlyOperation`; запись обёрнута в `sync_to_async(..., thread_sensitive=False)` (покрыто `test_usage_recorded_async`).
- **`usage.cost = 0` у gpt-5.4** → `cost_usd` nullable; токены и `response_id` сохраняются всегда, пересчёт по прайсу - вторая фаза.
- **`settings["_project"]` есть только у project-scoped конфигураций** → fallback на `llm_batch_project` (ContextVar из юнитов батча); у чисто текстовых запросов без юнитов slug пустой - осознанно.
- **Цикл импортов machinery → trans.models** → импорт `LLMUsageLog` ленивый, внутри метода.
- **Рост таблицы** → ~395 строк на полный col4-прогон; retention-политика не нужна.

## Вне объёма

- Langfuse / LiteLLM / Helicone (обоснование в шапке).
- Фолбэк-расчёт стоимости по `https://openrouter.ai/api/v1/models` для записей `cost_usd IS NULL` - отдельная фаза (токены и `response_id` для этого уже сохраняются).
- UI/дашборд, авто-сверка с `GET /api/v1/credits`, per-unit атрибуция.
- Деплой на прод: после мержа требуется `deploy/vps.sh` (миграция + код) - только по явному одобрению.

## Критерий приёмки

После реального (или замоканного с реальным payload) перевода `manage.py llm_usage_report` показывает ненулевые `prompt_tokens`/`cost_usd` с группировкой по модели и проекту; запись создаётся ровно на каждый LLM-запрос, включая async-путь и truncated-ответы; перевод не падает при любом содержимом `usage` и при падении самой записи.
