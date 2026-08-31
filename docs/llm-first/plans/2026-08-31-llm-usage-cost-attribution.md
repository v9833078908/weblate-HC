# LLM usage cost attribution per component and target language

**Дата:** 2026-08-31. **Статус:** proposed, not started.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** каждая строка `LLMUsageLog` знает component и target language, за
которые провайдер выставил счёт, а `llm_usage_report` отдаёт точную сумму
в разрезе `(project, component, target language, model, operation)`.

**Architecture:** цена запроса неделима, поэтому единица атрибуции - запрос,
а не строка. Оба платных шва (machinery HTTP seam в
`weblate/machinery/openai.py:149` и judge client в `weblate/trans/judge.py:1073`)
уже знают батч; в них добавляются два денормализованных поля, значения которых
берутся только из юнитов батча и только когда батч однороден по этому измерению.
Неоднородный батч остаётся видимо неатрибутированным (пустая строка), а не
записывается на первый юнит.

**Tech stack:** Django 5, PostgreSQL, `contextvars`, pytest, Django management
commands.

---

## Ответ на вопрос заказчика

После реализации плана и деплоя миграции:

| Вопрос | Ответ | Точность |
| --- | --- | --- |
| Стоимость `(component, target language)` для `operation=translation` после деплоя | да | точная сумма `usage.cost`, которую вернул провайдер по записанным запросам; scope из юнитов батча (Task 2) |
| То же для `operation=judge` | да, но только вместе с Task 3 | judge пишет свою строку сам (`weblate/trans/judge.py:1347-1394`) и `ContextVar` machinery не видит; scope приходит через `JudgeRequest` |
| Разделение стоимости на перевод и judge | да | по полю `operation` |
| Стоимость Need for Greed за 2026-08-17 - 2026-08-28 | нет | у старых строк поля пустые, backfill невозможен |
| Стоимость одной строки | нет | цена запроса неделима; доступна только средняя `cost_usd / strings_asked` по группе |
| Полная сверка с инвойсом OpenRouter | частично | журнал `<=` инвойс, см. "Известные пробелы" |

`strings_asked` (`Sum(batch_size)`) - это оплаченные строко-запросы, включая
повторы после отказа валидатора. Уникальные переведённые строки берутся из
statistics Weblate, а не из журнала: сравнение двух чисел и есть измерение
накладных расходов на retries.

Гарантия по построению операционная, а не общая: у machinery и у judge два
независимых шва записи. Если Task 3 не выполнена, judge-строки остаются с
пустыми `component_slug` и `target_language_code`, и тогда единственный
честный режим отчёта - `--operation translation`; иначе разбивка по
компонентам будет молча неполной на величину judge-расхода. Доля judge в
общем расходе в этом плане не измерялась.

## Ревизия предыдущего предложения

Дефекты, найденные при проверке предыдущего варианта по коду:

1. **Scope из первого юнита.** `_sources_project_slug`
   (`weblate/machinery/llm.py:67-77`) возвращает project первого юнита с
   `unit is not None`. Копирование этого приёма на component и language даёт
   ложную атрибуцию неделимой цены. Исправлено: проверка однородности по
   каждому измерению отдельно.
2. **`raise MachineTranslationError` на смешанном батче - вредно.** Это
   исключение перехватывается в
   `weblate/machinery/llm.py:2704-2727`, которое разрезает батч пополам и
   **отправляет платные запросы снова**. То есть "защита" превратилась бы в
   тихий сплит с дополнительными расходами. Исправлено: пустой scope плюс
   `LOGGER.error`, без отказа перевода.
3. **Judge не покрыт.** `operation=judge` - отдельный платный шов
   (`weblate/trans/judge.py:1101`), до этого плана вообще не имевший
   component и language. `JudgeRequest` (`weblate/trans/judge.py:134-144`)
   не содержит component, а `_run_batch` получает только
   `Sequence[JudgeRequest]` и `project_slug`
   (`weblate/trans/judge.py:1347-1394`), поэтому `ContextVar` из machinery
   до него не доходит принципиально: нужно новое поле dataclass, иначе
   стоимость по компоненту будет посчитана только для перевода.
4. **Отказ до запроса невозможен для judge.** Judge пишет usage уже после
   платного ответа (`weblate/trans/judge.py:1391-1394`), поэтому предложенный
   тест "смешанный батч отклоняется без HTTP-запроса" для judge принципиально
   неверен, а отказ там означал бы потерю уже оплаченных вердиктов.
5. **Project мог обнуляться вместе с component.** Единый кортеж scope
   обнулял бы и project, хотя он однороден. Исправлено: три независимые
   проверки, каждое измерение сохраняется настолько точно, насколько может.
6. **`settings["_project"]` игнорировался.** `project_slug` берётся из
   конфигурации сервиса, если она project-scoped
   (`weblate/machinery/openai.py:165-166`), и это может быть не проект юнитов.
   component и language берутся только из юнитов, никогда из настроек.
7. **Не названы пределы точности:** `cost_usd IS NULL`, оплаченные запросы без
   тела ответа, loc-kit анализ без записи usage. Вынесено в "Известные
   пробелы"; без этого раздела итоговая цифра выглядела бы полнее, чем есть.

## Инварианты, проверенные в коде

- LLM-батч уже структурно однороден: `batch_translate`
  (`weblate/machinery/base.py:1249-1266`) берёт `units[0].translation` для
  plural mapping и target language, а `run_judge_batch`
  (`weblate/trans/judge_loop.py:948`) - project первого юнита. Смешанный
  батч был бы дефектом корректности, а не только учёта.
- Батчи нарезаются срезами списка юнитов без группировки
  (`weblate/trans/machinery.py:201-204`), поэтому однородность обеспечивают
  вызывающие: `AutoTranslate.fetch_mt` работает внутри одной `Translation`
  (`weblate/trans/autotranslate.py:574`), judge drain группирует по
  `translation_id` (`weblate/trans/judge_loop.py:1454-1455`).
- Единственный вызывающий со смешанными юнитами -
  `weblate/trans/views/reports.py:324-337` (cost estimate), и он использует
  `WeblateMemory`, то есть LLM-шва не достигает.
- `build_request` (`weblate/trans/judge_loop.py:92-108`) - единственный
  конструктор `JudgeRequest` в продуктовом коде: через него идут и обычный
  прогон (`weblate/trans/judge_loop.py:973`), и drain
  (`weblate/trans/judge_loop.py:1295`), и аудит прогонов
  (`weblate/trans/autotranslate.py:865`, `1197`). Поэтому одного поля в
  `build_request` достаточно, чтобы каждая judge-строка несла scope, а тест
  `test_request_carries_the_units_component_and_language` (Task 3) охраняет
  этот инвариант.
- `contextvars` безопасны при `batch_concurrency > 1`: `set` происходит внутри
  рабочего потока в `_fetch_llm_batch`, а не в родительском.
- `RoutedLLMTranslation` и `RoutedLiteLLMTranslation`
  (`weblate_customization/src/weblate_customization/machinery.py:137,356`) не
  переопределяют запись usage, поэтому копирование в
  `dev-docker/data/python/` не требуется.

---

## Task 1: Поля component и target language в LLMUsageLog

**Files:**

- Modify: `weblate/trans/models/llm_usage.py:15-73`
- Create: `weblate/trans/migrations/0113_llm_usage_scope.py` (генерируется)
- Test: `weblate/trans/tests/test_llm_usage.py`

### Step 1: Write the failing tests

В `weblate/trans/tests/test_llm_usage.py`, в конец класса
`LLMUsageLogModelTest`:

```python
    def test_scope_defaults_blank(self) -> None:
        log = LLMUsageLog.objects.create(model="m", prompt_tokens=1)
        self.assertEqual(log.component_slug, "")
        self.assertEqual(log.target_language_code, "")

    def test_scope_is_stored(self) -> None:
        log = LLMUsageLog.objects.create(
            model="m",
            project_slug="need-for-greed",
            component_slug="ui",
            target_language_code="fr",
            prompt_tokens=1,
        )
        log.refresh_from_db()
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")
```

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL `TypeError: LLMUsageLog() got unexpected keyword arguments: 'component_slug'`

Хост-вариант, если контейнер не поднят:

```sh
source scripts/test-database.sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate/trans/tests/test_llm_usage.py -q
```

### Step 3: Add the fields

В `weblate/trans/models/llm_usage.py` добавить импорт после `from django.db import models`:

```python
from weblate.trans.defines import COMPONENT_NAME_LENGTH, LANGUAGE_CODE_LENGTH
```

`weblate/trans/defines.py` - модуль констант без моделей, цикла импорта нет
(`weblate/trans/models/component.py:62` уже импортирует его так же).

Сразу после `project_slug` (`weblate/trans/models/llm_usage.py:45`):

```python
    #: Component and target language the request was billed to, denormalized
    #: on purpose: a financial row must keep the value it was billed under
    #: even after a rename or a delete. Blank means the request is not
    #: attributable - it carried no unit, or it spanned several components or
    #: target languages, and a provider bills one request as an indivisible
    #: amount that cannot be split between them.
    component_slug = models.CharField(max_length=COMPONENT_NAME_LENGTH, blank=True)
    target_language_code = models.CharField(
        max_length=LANGUAGE_CODE_LENGTH, blank=True
    )
```

Индексы не добавляются: отчёт запускается вручную по таблице с retention
(`weblate/trans/tasks.py:1375`), а `project_slug` тоже не индексирован.

### Step 4: Generate the migration

Run:

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations \
  trans --name llm_usage_scope
```

Expected: создан `weblate/trans/migrations/0113_llm_usage_scope.py` с
`dependencies = [("trans", "0112_judge_run_unit_deferred_outcome")]` и двумя
`AddField`. БД не нужна. Существующие строки получают `""` - это и означает
"неатрибутировано".

### Step 5: Run tests to verify they pass

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: PASS

### Step 6: Commit

```sh
git add weblate/trans/models/llm_usage.py \
  weblate/trans/migrations/0113_llm_usage_scope.py \
  weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): record component and target language on LLM usage"
```

---

## Task 2: Scope машинного перевода

**Files:**

- Modify: `weblate/machinery/llm.py:47-77`, `2774-2809`, `2899-2931`
- Modify: `weblate/machinery/openai.py:23-28`, `165-183`
- Test: `weblate/machinery/tests.py` (класс `OpenAITranslationTest`)

### Step 1: Write the failing tests

В `weblate/machinery/tests.py`, в класс `OpenAITranslationTest` рядом с
существующими usage-тестами (после `test_usage_recorded_async`, около строки
4405):

```python
    def mock_chat_reply_echo(self) -> None:
        """Answer any batch correctly, with a priced usage block."""
        http_mock.register(
            "GET",
            re.compile(r"/models$"),
            json={
                "object": "list",
                "data": [{"id": self.TRACE_MODEL, "object": "model"}],
            },
        )

        def chat_callback(request):
            payload = json.loads(request.content)
            strings = json.loads(payload["messages"][-1]["content"])["strings"]
            return httpx2.Response(
                200,
                headers={},
                json={
                    "id": "chatcmpl-scope",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    [
                                        {
                                            "id": item["id"],
                                            "parts": [
                                                {
                                                    "type": "text",
                                                    "text": f"{item['source']} (fr)",
                                                }
                                            ],
                                        }
                                        for item in strings
                                    ]
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 12,
                        "total_tokens": 21,
                        "cost": 0.001,
                    },
                },
            )

        http_mock.register_callback(
            "POST", re.compile(r"chat/completions"), chat_callback
        )

    @http_mock.activate
    def test_usage_records_the_batch_component_and_language(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        unit = make_unit(code="fr", source="Alpha")
        unit.translation.component.slug = "ui"
        machine = self.get_machine()

        machine.download_multiple_translations(
            "en", "fr", [("Alpha", cast("Unit", unit))]
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(log.project_slug, "mock")
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")

    @http_mock.activate
    def test_usage_records_the_batch_component_and_language_async(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        unit = make_unit(code="fr", source="Alpha")
        unit.translation.component.slug = "ui"
        machine = self.get_machine()

        async_to_sync(machine.adownload_multiple_translations)(
            "en", "fr", [("Alpha", cast("Unit", unit))]
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")

    @http_mock.activate
    def test_usage_leaves_a_mixed_component_batch_unattributed(self) -> None:
        """
        A provider bills one request as a whole.

        Charging a two-component request to whichever unit came first would
        invent a cost per component, so the component is left blank while the
        dimensions that are still unique are kept.
        """
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        first = make_unit(code="fr", source="Alpha")
        first.translation.component.slug = "ui"
        second = make_unit(code="fr", source="Beta")
        second.translation.component.slug = "loot"
        machine = self.get_machine()

        translations = machine.download_multiple_translations(
            "en",
            "fr",
            [("Alpha", cast("Unit", first)), ("Beta", cast("Unit", second))],
        )

        self.assertEqual(translations["Alpha"][0]["text"], "Alpha (fr)")
        log = LLMUsageLog.objects.get()
        self.assertEqual(log.project_slug, "mock")
        self.assertEqual(log.component_slug, "")
        self.assertEqual(log.target_language_code, "fr")

    @http_mock.activate
    def test_usage_leaves_a_mixed_language_batch_unattributed(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        first = make_unit(code="fr", source="Alpha")
        second = make_unit(code="de", source="Beta")
        machine = self.get_machine()

        machine.download_multiple_translations(
            "en",
            "fr",
            [("Alpha", cast("Unit", first)), ("Beta", cast("Unit", second))],
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(log.project_slug, "mock")
        self.assertEqual(log.component_slug, "mock")
        self.assertEqual(log.target_language_code, "")
```

`make_unit`, `cast`, `async_to_sync`, `re`, `json`, `httpx2`, `http_mock` и
`LLMUsageLog` в этом файле уже импортированы (`weblate/machinery/tests.py:60-113`).

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest" -k scope`
Expected: FAIL `AttributeError: 'LLMUsageLog' object has no attribute 'component_slug'`
(если Task 1 ещё не смёржен) либо `AssertionError: '' != 'ui'`.

### Step 3: Replace the scope helper

В `weblate/machinery/llm.py` заменить блок `47-53` (ContextVars project и
unit count оставить, добавить два новых):

```python
#: Project slug of the batch currently fetching, for usage accounting at the
#: HTTP seam, which does not receive the batch units.
llm_batch_project: ContextVar[str] = ContextVar("llm_batch_project", default="")
#: Component slug and target language code of the batch currently fetching,
#: set and reset at the same seam as llm_batch_project. Blank when the batch
#: is not attributable in that dimension.
llm_batch_component: ContextVar[str] = ContextVar("llm_batch_component", default="")
llm_batch_target_language: ContextVar[str] = ContextVar(
    "llm_batch_target_language", default=""
)
#: Source batch size of the request currently fetching, set/reset at the same
#: seam as llm_batch_project. Reflects the exact batch sent over HTTP,
#: including split-recovery sub-batches, not the original caller's batch.
llm_batch_unit_count: ContextVar[int] = ContextVar("llm_batch_unit_count", default=0)
```

Заменить `_sources_project_slug` (`weblate/machinery/llm.py:67-77`) целиком:

```python
def _unique(values: set[str]) -> str:
    """The single value of a set, or a blank string when it is not single."""
    return next(iter(values)) if len(values) == 1 else ""


def _sources_usage_scope(
    sources: list[tuple[str, Unit | None]],
) -> tuple[str, str, str]:
    """
    Project, component and target language a batch is billed to.

    A provider bills one request as an indivisible amount, so a dimension is
    only recorded when every unit of the batch agrees on it. A dimension the
    batch spans is left blank and stays visibly unattributed instead of being
    charged to whichever unit came first. Each dimension is judged on its own,
    so a two-component batch of one language still records that language.
    """
    projects: set[str] = set()
    components: set[str] = set()
    languages: set[str] = set()
    for _text, unit in sources:
        if unit is None:
            continue
        try:
            translation = unit.translation
            component = translation.component
            projects.add(component.project.slug)
            components.add(component.slug)
            languages.add(translation.language.code)
        except AttributeError:
            return "", "", ""
    if len(components) > 1 or len(languages) > 1:
        LOGGER.error(
            "LLM batch spans %d components and %d target languages, "
            "billing it to none of them",
            len(components),
            len(languages),
        )
    return _unique(projects), _unique(components), _unique(languages)
```

Это меняет и поведение project: батч, охватывающий два проекта, больше не
записывается на первый из них. Осознанно - неверная атрибуция хуже пустой.

### Step 4: Set and reset the new variables at both seams

В `_fetch_llm_batch` заменить `weblate/machinery/llm.py:2791-2792`:

```python
        project_slug, component_slug, target_language_code = _sources_usage_scope(
            sources
        )
        project_token = llm_batch_project.set(project_slug)
        component_token = llm_batch_component.set(component_slug)
        language_token = llm_batch_target_language.set(target_language_code)
        unit_count_token = llm_batch_unit_count.set(len(sources))
```

и внутренний `finally` (`weblate/machinery/llm.py:2802-2803`):

```python
            finally:
                llm_batch_project.reset(project_token)
                llm_batch_component.reset(component_token)
                llm_batch_target_language.reset(language_token)
```

Точно те же две правки в `_afetch_llm_batch`
(`weblate/machinery/llm.py:2916-2917` и `2924-2925`).

### Step 5: Write the fields at the accounting seam

В `weblate/machinery/openai.py` расширить импорт (`23-28`):

```python
from .llm import (
    BaseLLMTranslation,
    llm_batch_component,
    llm_batch_project,
    llm_batch_target_language,
    llm_batch_unit_count,
    llm_usage_record,
)
```

и добавить два поля в `LLMUsageLog.objects.create`
(`weblate/machinery/openai.py:170-183`), сразу после `project_slug`:

```python
            component_slug=llm_batch_component.get(),
            target_language_code=llm_batch_target_language.get(),
```

### Step 6: Run tests to verify they pass

Run: `./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest" -k "usage or scope"`
Expected: PASS, включая существующие `test_usage_recorded`,
`test_usage_unit_count_reflects_split_recovery`,
`test_usage_records_batch_size_and_outcome`.

### Step 7: Commit

```sh
git add weblate/machinery/llm.py weblate/machinery/openai.py weblate/machinery/tests.py
git commit -m "feat(machinery): bill LLM requests to their component and language"
```

---

## Task 3: Scope judge-запросов

**Files:**

- Modify: `weblate/trans/judge.py:134-144`, `1073-1105`, `1170-1182`, `1391-1394`
- Modify: `weblate/trans/judge_loop.py:92-108`
- Test: `weblate/trans/tests/test_judge_client.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the failing tests

В `weblate/trans/tests/test_judge_loop.py`, класс `JudgeLoopTest`, рядом с
`test_every_seat_bills_the_units_project` (строка 535):

```python
    def test_request_carries_the_units_component_and_language(self) -> None:
        # Judge spend is billed per request; without these two the usage row
        # cannot say which component and language paid for it.
        unit = self.get_unit()
        request = build_request(unit)
        self.assertEqual(request.component_slug, self.component.slug)
        self.assertEqual(request.target_language, unit.translation.language.code)
```

В `weblate/trans/tests/test_judge_client.py`, после
`test_usage_is_attributed_to_the_project` (строка 1246):

```python
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_attributed_to_the_component_and_language(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts(
            [replace(REQ, component_slug="ui")],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.component_slug, "ui")
        self.assertEqual(row.target_language_code, "fr")

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_leaves_a_mixed_batch_unattributed(self) -> None:
        """
        The judge pays before it can inspect the batch.

        Usage is written after the response arrives, so a mixed batch cannot be
        refused without discarding paid verdicts; it is recorded without a
        component instead of being charged to one.
        """
        payload = _reply(
            [
                {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
                {"id": 1, "verdict": "pass", "errors": [], "back_translation": ""},
            ]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts(
            [
                replace(REQ, component_slug="ui"),
                replace(REQ, unit_key="OTHER", component_slug="loot"),
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.component_slug, "")
        self.assertEqual(row.target_language_code, "fr")
```

`replace` в этом файле уже импортирован (`weblate/trans/tests/test_judge_client.py:9`),
как и `REQ`, `CHAT_URL` и `_reply` - новых импортов не требуется.

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test weblate/trans/tests/test_judge_client.py -k attributed`
Expected: FAIL `TypeError: JudgeRequest.__init__() got an unexpected keyword argument 'component_slug'`

### Step 3: Add the dataclass field

В `weblate/trans/judge.py`, в конец `JudgeRequest` (после `target_plurals`,
строка 144):

```python
    #: Component the unit belongs to, for per-component cost accounting. A
    #: default keeps historical constructions valid; a blank value simply
    #: leaves the request unattributed.
    component_slug: str = ""
```

В `weblate/trans/judge_loop.py:95-108`, в `build_request`, добавить после
`target_language=translation.language.code`:

```python
        component_slug=translation.component.slug,
```

### Step 4: Derive and write the judge scope

В `weblate/trans/judge.py` добавить перед `_write_llm_usage` (строка 1073):

```python
def _batch_usage_scope(batch: Sequence[JudgeRequest]) -> tuple[str, str]:
    """
    Component and target language a judge batch is billed to.

    Recorded only when the whole batch agrees, per dimension: one request is
    billed as an indivisible amount and must not be charged to one of several
    components.
    """
    components = {request.component_slug for request in batch}
    languages = {request.target_language for request in batch}
    if len(components) > 1 or len(languages) > 1:
        LOGGER.error(
            "judge batch spans %d components and %d target languages, "
            "billing it to none of them",
            len(components),
            len(languages),
        )
    return (
        next(iter(components)) if len(components) == 1 else "",
        next(iter(languages)) if len(languages) == 1 else "",
    )
```

Заменить сигнатуру и тело `_write_llm_usage` (`weblate/trans/judge.py:1073-1105`)
так, чтобы вместо `unit_count: int` принимался батч:

```python
def _write_llm_usage(
    payload: dict,
    model: str,
    project_slug: str,
    batch: Sequence[JudgeRequest],
    request_attempt: object | None,
) -> None:
    usage = _usage_values(payload)
    prompt_tokens, completion_tokens = (
        usage.get("prompt_tokens") or 0,
        usage.get("completion_tokens") or 0,
    )
    if not prompt_tokens and not completion_tokens:
        return
    details, completion_details = (
        usage.get("prompt_tokens_details") or {},
        usage.get("completion_tokens_details") or {},
    )
    component_slug, target_language_code = _batch_usage_scope(batch)
    LLMUsageLog.objects.create(
        model=model,
        project_slug=project_slug,
        component_slug=component_slug,
        target_language_code=target_language_code,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage.get("total_tokens") or (prompt_tokens + completion_tokens),
        cost_usd=Decimal(str(usage["cost"])) if usage.get("cost") else None,
        response_id=str(payload.get("id") or ""),
        cached_tokens=details.get("cached_tokens") or 0,
        reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
        operation=LLMUsageLog.Operation.JUDGE,
        unit_count=len(batch),
        batch_size=len(batch),
        request_attempt=request_attempt,
    )
```

`_record_usage` (`weblate/trans/judge.py:1170-1182`) принимает батч вместо
размера:

```python
def _record_usage(
    payload: dict | None,
    model: str,
    project_slug: str,
    batch: Sequence[JudgeRequest],
    attempt: object | None,
) -> None:
    if payload is None:
        return
    try:
        _write_llm_usage(payload, model, project_slug, batch, attempt)
    except Exception:
        LOGGER.exception("Failed to record LLM usage")
```

и единственный вызов (`weblate/trans/judge.py:1391-1394`):

```python
        if persistence:
            _record_usage(
                response.payload, profile.model, project_slug, batch, attempt
            )
```

`Sequence` в этом модуле импортирован в рантайме
(`weblate/trans/judge.py:18`), так что аннотация валидна и без
`TYPE_CHECKING`-блока.

### Step 5: Run tests to verify they pass

Run:

```sh
./rundev.sh test weblate/trans/tests/test_judge_client.py
./rundev.sh test weblate/trans/tests/test_judge_loop.py
```

Expected: PASS. Существующие проверки `unit_count`
(`test_judge_client.py:1289`, `1316`) должны остаться зелёными: `len(batch)`
равен прежнему `size`.

### Step 6: Commit

```sh
git add weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): bill judge requests to their component and language"
```

---

## Task 4: Отчёт по запросу

**Files:**

- Modify: `weblate/trans/management/commands/llm_usage_report.py`
- Test: `weblate/trans/tests/test_llm_usage.py` (класс `LLMUsageReportTest`)

### Step 1: Write the failing tests

Заменить `setUp` класса `LLMUsageReportTest`
(`weblate/trans/tests/test_llm_usage.py:55-78`) на строки с реальным scope и
добавить тесты. В начало файла добавить `import csv`.

```python
    def setUp(self) -> None:
        LLMUsageLog.objects.create(
            model="m1",
            project_slug="col4",
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            batch_size=10,
            cost_usd=Decimal("0.001"),
        )
        LLMUsageLog.objects.create(
            model="m1",
            project_slug="col4",
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=4,
            completion_tokens=1,
            total_tokens=5,
            batch_size=5,
        )
        LLMUsageLog.objects.create(
            model="m2",
            project_slug="st2",
            component_slug="hub-1",
            target_language_code="de",
            operation=LLMUsageLog.Operation.JUDGE,
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            batch_size=2,
            cost_usd=Decimal("0.0000005"),
        )
```

```python
    def test_csv_report_groups_by_component_and_language(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--format", "csv", stdout=out)
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(
            rows[0],
            [
                "model",
                "project",
                "component",
                "target_language",
                "operation",
                "requests",
                "strings_asked",
                "prompt_tokens",
                "completion_tokens",
                "cost_usd",
                "unpriced",
            ],
        )
        self.assertEqual(
            rows[1],
            [
                "m1",
                "col4",
                "ui",
                "fr",
                "translation",
                "2",
                "15",
                "14",
                "6",
                "0.00100000",
                "1",
            ],
        )

    def test_component_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--component", "hub-1", stdout=out)
        text = out.getvalue()
        self.assertIn("hub-1", text)
        self.assertNotIn("ui", text)

    def test_language_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--language", "fr", stdout=out)
        text = out.getvalue()
        self.assertIn("m1", text)
        self.assertNotIn("m2", text)

    def test_operation_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--operation", "judge", stdout=out)
        text = out.getvalue()
        self.assertIn("hub-1", text)
        self.assertNotIn("m1", text)

    def test_unattributed_rows_stay_visible(self) -> None:
        LLMUsageLog.objects.create(model="m3", project_slug="col4", prompt_tokens=3)
        out = StringIO()
        call_command("llm_usage_report", "--model", "m3", "--format", "csv", stdout=out)
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(rows[1][1:5], ["col4", "-", "-", "-"])
```

Существующий `test_csv_report` (`weblate/trans/tests/test_llm_usage.py:90-98`)
проверяет старый заголовок - удалить его: он полностью покрыт новым тестом.

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL `AssertionError: Lists differ` на заголовке и
`CommandError: unrecognized arguments: --component`.

### Step 3: Rewrite the command

`weblate/trans/management/commands/llm_usage_report.py` целиком:

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
    "component",
    "target_language",
    "operation",
    "requests",
    "strings_asked",
    "prompt_tokens",
    "completion_tokens",
    "cost_usd",
    "unpriced",
]

#: Finest grouping the journal supports. A request is the billed unit, so
#: anything finer than this would have to invent an allocation.
GROUP_FIELDS = [
    "model",
    "project_slug",
    "component_slug",
    "target_language_code",
    "operation",
]


class Command(BaseCommand):
    help = (
        "reports LLM token usage and cost grouped by model, project, "
        "component, target language and operation"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days", type=int, default=None, help="only the last N days"
        )
        parser.add_argument("--model", default=None, help="only this model")
        parser.add_argument("--project", default=None, help="only this project slug")
        parser.add_argument(
            "--component", default=None, help="only this component slug"
        )
        parser.add_argument(
            "--language", default=None, help="only this target language code"
        )
        parser.add_argument(
            "--operation",
            choices=LLMUsageLog.Operation.values,
            default=None,
            help="only this operation",
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
        if options["component"]:
            logs = logs.filter(component_slug=options["component"])
        if options["language"]:
            logs = logs.filter(target_language_code=options["language"])
        if options["operation"]:
            logs = logs.filter(operation=options["operation"])
        rows = list(
            logs.values(*GROUP_FIELDS)
            .annotate(
                requests=Count("id"),
                strings=Sum("batch_size"),
                prompt=Sum("prompt_tokens"),
                completion=Sum("completion_tokens"),
                cost=Sum("cost_usd"),
                unpriced=Count("id", filter=Q(cost_usd__isnull=True)),
            )
            .order_by(*GROUP_FIELDS)
        )
        data = [
            [
                row["model"],
                row["project_slug"] or "-",
                row["component_slug"] or "-",
                row["target_language_code"] or "-",
                row["operation"] or "-",
                row["requests"],
                row["strings"] or 0,
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

### Step 4: Run tests to verify they pass

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: PASS

### Step 5: Commit

```sh
git add weblate/trans/management/commands/llm_usage_report.py \
  weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): report LLM cost per component and target language"
```

---

## Task 5: Changelog

**Files:**

- Modify: `docs/changes.rst:22`

### Step 1: Edit the entry

Фича `llm_usage_report` ещё не выпущена (раздел 2026.8.1, "Not yet released"),
поэтому второй пункт не добавляется - расширяется существующий:

```rst
* LLM machine translation and LLM judge requests now record per-request token usage and cost together with the component and target language the request was billed to, reportable via the ``llm_usage_report`` management command, which can filter by project, component, target language, model, and operation.
```

### Step 2: Lint the docs

Run: `uv run prek run --files docs/changes.rst`
Expected: PASS

### Step 3: Commit

```sh
git add docs/changes.rst
git commit -m "docs(changes): note component and language cost attribution"
```

---

## Task 6: Проверка и выкладка

### Step 1: Lint and format

```sh
uv run prek run --files weblate/machinery/llm.py weblate/machinery/openai.py \
  weblate/machinery/tests.py weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/llm_usage.py \
  weblate/trans/management/commands/llm_usage_report.py \
  weblate/trans/migrations/0113_llm_usage_scope.py \
  weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py docs/changes.rst
```

Expected: PASS.

### Step 2: Pylint

```sh
uv run pylint weblate/machinery/llm.py weblate/machinery/openai.py \
  weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/llm_usage.py \
  weblate/trans/management/commands/llm_usage_report.py
```

Expected: 10.00/10 либо только предсуществующие замечания.

### Step 3: Mypy

```sh
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: нет новых ошибок в затронутых файлах.

### Step 4: Regression suites

```sh
./rundev.sh test weblate/trans/tests/test_llm_usage.py
./rundev.sh test weblate/trans/tests/test_judge_client.py
./rundev.sh test weblate/trans/tests/test_judge_loop.py
./rundev.sh test weblate/trans/tests/test_judge_views.py
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest"
./rundev.sh test weblate/trans/tests/test_autotranslate.py
```

Expected: PASS. `test_judge_views.py` включён потому, что предпросмотр
стоимости читает `recent_cost_range` (`weblate/trans/views/edit.py:1551`), а он
фильтрует по `project_slug`, `model`, `operation` и не должен меняться.

### Step 5: Migration check

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

### Step 6: Dev smoke test

Применить миграцию в дев-контейнере (без пересборки стека) и прогнать один
реальный батч автоперевода на тестовом компоненте, затем:

```sh
docker exec dev-docker-weblate-1 weblate migrate
docker exec dev-docker-weblate-1 weblate llm_usage_report --days 1 --format csv
```

Expected: строки с непустыми `component` и `target_language`, суммой
`cost_usd` и ненулевым `strings_asked`.

### Step 7: Commit and push

```sh
git push -u origin <branch>
```

### Step 8: Production

Миграция `0113_llm_usage_scope` на прод применяется только после явного
разрешения (AGENTS.md, "Never deploy without explicit approval"). До неё
прод-строки продолжают писаться без scope.

---

## Как считать стоимость языка и компонента после выкладки

```sh
# полная разбивка проекта за период
weblate llm_usage_report --project need-for-greed --days 30 --format csv > cost.csv

# только машинный перевод одного языка
weblate llm_usage_report --project need-for-greed --language fr --operation translation

# только judge по компоненту
weblate llm_usage_report --project need-for-greed --component ui --operation judge
```

Соединение с объёмом требует осторожности: знаменатели из журнала и из
statistics разной природы. `cost_usd` и `strings_asked` - величины за
выбранный период и включают повторы, а `translated` в
`GET /api/components/<project>/<component>/statistics/` - текущее
накопленное состояние, куда входят строки, переведённые вне периода или
человеком, и не входят строки, переведённые в периоде, а затем перезаписанные.

Поэтому:

- `cost_usd / strings_asked` - корректная средняя цена оплаченного
  строко-запроса; оба числа из одной строки отчёта, одного периода;
- `strings_asked / translated` и `cost_usd / translated` - только грубая
  оценка, и она осмысленна лишь когда период отчёта покрывает всю историю
  расхода этой группы и текущее содержимое компонента произведено именно этим
  расходом. Иначе это деление величины за период на накопленный остаток;
- точная цена одной строки недостижима в принципе: провайдер выставляет счёт
  за запрос, а не за строку.

Строка без `--operation` складывает два разных шва записи. Пока Task 3 не
выкачена, `operation=judge` даёт группы с `component = -`, поэтому сводку по
компонентам нужно снимать с `--operation translation`, а judge-расход считать
отдельно и на уровне проекта. После Task 3 обе операции атрибутированы
одинаково, и суммировать их можно.

## Известные пробелы

Их нужно называть в любом отчёте, иначе сумма выглядит полнее, чем есть:

1. **История не восстанавливается.** Строки до миграции несут `""`;
   связи между конкретным OpenRouter-запросом и изменениями строк в журнале
   нет, поэтому Need for Greed за 2026-08-17 - 2026-08-28 остаётся
   неатрибутированным. Приблизительный backfill сознательно не делается.
2. **`cost_usd IS NULL`.** Провайдер иногда не отдаёт цену; такая группа -
   нижняя граница, её видно по колонке `unpriced`.
3. **Оплаченные запросы без тела ответа.** При transport/deadline usage-строка
   не пишется вовсе (`weblate/trans/judge.py:1177-1178`), поэтому итог журнала
   не превышает инвойс OpenRouter, но может быть меньше него. Для judge такие
   вызовы видны в `JudgeRequestAttempt`; для machinery - только в логах.
4. **loc-kit profile analysis не пишет usage.** Он ходит в OpenRouter по
   отдельной site-wide конфигурации (`weblate/trans/loc_kit.py`) и в журнал не
   попадает. Компонента на момент анализа ещё не существует, поэтому в этот
   план он не входит.
5. **Кеш Weblate.** Повторно использованный перевод не стоит ничего, поэтому
   цена строки внутри группы неравномерна; итог группы при этом точен.
6. **`project_slug` может приходить из настроек сервиса**, а не из юнитов
   (`weblate/machinery/openai.py:165-166`). `component_slug` и
   `target_language_code` берутся только из юнитов.

## Out of scope

- Группировка батчей по `Translation` в `fetch_machinery_matches` - смешанный
  LLM-батч сегодня не создаёт ни один вызывающий, а если бы создавал, то ломал
  бы plural mapping и target language (`weblate/machinery/base.py:1249-1266`),
  то есть это отдельный дефект корректности, а не учёта.
- UI-страница расходов. Отчёт по запросу закрывает вопрос, страница - нет.
- Индексы под группировку отчёта и пересчёт цены по прайс-листу для
  `cost_usd IS NULL`.
