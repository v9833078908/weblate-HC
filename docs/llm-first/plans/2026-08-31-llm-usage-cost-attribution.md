# LLM usage cost attribution per component and target language

**Дата:** 2026-08-31. **Статус:** reviewed, proposed, not started.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** каждая новая строка `LLMUsageLog` хранит stable service ID,
неизменяемые project/component ID snapshots, их читаемые slug snapshots и
target language. `llm_usage_report` отвечает одним итогом по текущему
`(project, component, target language, service, operation)`, не теряя расход
при rename project или component.

**Architecture:** цена запроса неделима, поэтому единица атрибуции - запрос, а
не строка. Machinery подтверждает scope только когда каждый source несёт одну и
ту же `Translation`; `None` или другой `translation_id` оставляет весь scope
пустым, не назначая стоимость первому юниту и не обходя relation каждого
юнита. Judge несёт те же ID snapshots через `JudgeRequest`. Оба платных шва
пишут стабильный service ID (`openrouter`, `litellm`, ...), чтобы одинаковая
model ID за разными шлюзами никогда не смешивалась.

**Tech stack:** Django 5, PostgreSQL, `contextvars`, pytest, Django management
commands.

---

## Ответ на вопрос заказчика

После реализации плана и деплоя миграции:

| Вопрос | Ответ | Точность |
| --- | --- | --- |
| Стоимость OpenRouter-перевода `(component, target language)` после деплоя | да, как точная сумма сохранённых `usage.cost` в поддерживаемой decimal scale | `--service openrouter --operation translation --summary`; ledger scope полон только при `priced_complete=yes` и `attribution_complete=yes` |
| То же для `operation=judge` | да, но только вместе с Task 3 и в пределах retention | service и immutable ID snapshots проходят через `JudgeRequest`; judge usage по умолчанию хранится 90 дней |
| Расход компонента после rename project/component | да, для строк после миграции | current slug резолвится в immutable ID, snapshot slug остаётся историческим label |
| Разделение расхода по OpenRouter, LiteLLM и другим шлюзам | да | по полю `service`, а не только по model ID |
| Стоимость Need for Greed за 2026-08-17 - 2026-08-28 | нет | старые строки не содержат новых ID/service/scope полей, backfill невозможен |
| Стоимость одной строки | нет | цена запроса неделима; доступна только средняя `cost_usd / strings_asked` по одной группе журнала |
| Полная сверка с инвойсом OpenRouter | частично | журнал `<=` инвойс, см. "Известные пробелы" |

`strings_asked` (`Sum(batch_size)`) - это оплаченные строко-запросы, включая
повторы после отказа валидатора. Это не число уникальных переведённых строк и
его нельзя превращать в retry rate делением на текущую statistics Weblate:
журнал ограничен периодом, statistics - накопленное изменяемое состояние.

`--summary` возвращает одну строку после всех фильтров. `priced_complete=yes`
значит, что у всех **включённых** rows есть provider-reported цена, которую
ledger сохранил в точной поддерживаемой schema scale; numeric вне
`numeric(24, 18)` становится `NULL`. `attribution_complete=yes` значит, что в
том же service/model/operation/time не осталось ни одной row с неразрешимым
scope. Вместе они делают `cost_usd` полной суммой **записанного ledger**, не
заменяя ограничение об оплаченной попытке без тела ответа.

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
   `unit is not None`. Копирование этого приёма на component и language
   выдумывает адресата неделимой цены.
2. **`raise MachineTranslationError` на смешанном батче - вредно.** Это
   исключение перехватывается в
   `weblate/machinery/llm.py:2704-2727`, которое разрезает батч пополам и
   **отправляет платные запросы снова**. Поэтому scope остаётся пустым, а
   перевод не отклоняется.
3. **Judge - отдельный платный шов.** `operation=judge` пишет
   `LLMUsageLog` в `weblate/trans/judge.py:1073-1105`; ContextVar machinery
   туда не доходит. Component должен пройти через `JudgeRequest`.
4. **Отказ до запроса невозможен для judge.** Usage пишется уже после
   платного ответа (`weblate/trans/judge.py:1391-1394`), поэтому смешанный
   batch сохраняется с пустым scope, а не отбрасывается вместе с вердиктами.
5. **`Unit | None` и relation на каждый unit.** Пропуск `None` при обходе
   источников приписывает общий счёт оставшемуся юниту, а
   `unit.translation` в цикле добавляет N+1 запросов. Machinery проверяет
   только `translation_id`; один ID даёт scope первого unit, любой другой
   случай - пустую тройку.
6. **`settings["_project"]` не может перезаписывать активный batch.**
   `weblate/machinery/openai.py:165-166` сейчас предпочитает project из
   настроек даже когда юниты говорят обратное. `None` как неактивное значение
   `llm_batch_project` отличает вызов вне batch от активного, но
   неатрибутированного batch.
7. **Model ID не определяет источник счёта.** `RoutedLLMTranslation.name` -
   `OpenRouter`, а `RoutedLiteLLMTranslation.name` - `LiteLLM`
   (`weblate_customization/.../machinery.py:137-143,356-368`); обе службы
   могут записать одинаковую model ID. Нужен service ID и фильтр отчёта.
8. **Нулевой provider cost - это цена, а не отсутствие цены.** Оба шва
   используют `Decimal(str(cost)) if cost else None`
   (`weblate/machinery/openai.py:162-176`,
   `weblate/trans/judge.py:1091-1104`), поэтому ответ `cost: 0` ошибочно
   становится unpriced.
9. **Детализация не отвечает на один денежный вопрос.** Группировка по model
   и operation (`Task 4` прежней версии) требовала ручного сложения и могла
   выдать частичную сумму как полную. Нужны `--summary`, `priced_complete` и
   `attribution_complete`.
10. **Текущий slug не является финансовой identity.** Snapshot slug нужен для
    истории, но фильтр только по нему теряет расход после rename. Нужны
    immutable `project_id_snapshot` и `component_id_snapshot`, а current slug
    должен резолвиться в них перед фильтрацией.
11. **Retention не симметричен.**
    `cleanup_judge_observability` удаляет только judge usage
    (`weblate/trans/tasks.py:1375-1381`), хотя translation usage не удаляет.
    Поэтому индекс для растущего translation ledger обязателен, а judge
    отчёт ограничен `LLM_USAGE_LOG_RETENTION_DAYS` (90 дней по умолчанию).
12. **Пределы точности должны быть названы:** `cost_usd IS NULL`,
    оплаченные запросы без тела ответа, неатрибутированные batch и loc-kit
    analysis без usage-строки. Без этого итоговая цифра выглядит полнее, чем
    есть.
13. **Scale `DecimalField` обрезал бы provider cost.** Текущие
    `max_digits=12, decimal_places=8`
    (`weblate/trans/models/llm_usage.py:49-51`) сохраняются PostgreSQL как
    `numeric(12, 8)`. Цена с девятой дробной цифрой была бы округлена до
    отчёта; миграция расширяет scale до 18 и тестирует значение с 18 цифрами.
14. **Migration должна предшествовать writer code.** Новые ORM kwargs на
    непромигрированной БД вызывают исключение, которое recorder ловит и
    логирует, но usage row теряется. Production шаг теперь требует additive
    migration до запуска новых workers.
15. **Default JSON parsing теряет decimal lexeme до `Decimal`.**
    `response.json()` в machinery и `json.loads(...)` в judge без
    `parse_float=Decimal` сначала создают binary `float`; последующий
    `Decimal(str(cost))` сохраняет уже округленное значение. Оба response
    seam должны parse-ить raw JSON сразу в `Decimal`, а тесты обязаны передать
    literal с 18 значимыми дробными цифрами через `content=`, не через
    `json=`.

## Что уже существует и инварианты

- `batch_translate` (`weblate/machinery/base.py:1240-1282`) берёт
  `units[0].translation` для language и plural mapping. `fetch_machinery_matches`
  режет произвольный список срезами (`weblate/trans/machinery.py:199-205`),
  поэтому scope guard не заменяет корректную группировку вызывающего: он лишь
  отказывается финансово атрибутировать дефектный batch.
- `translation_id` - доступное без запроса поле `Unit`; после подтверждения
  одного ID helper читает `first_unit.translation` один раз. Это O(1), а не
  relation-обход каждого source.
- `BaseMachineTranslation.get_identifier()` возвращает стабильный service ID
  из class `name` (`weblate/machinery/base.py:202-203`). Для routed services
  это `openrouter` и `litellm`; judge уже классифицирует endpoint как
  `openrouter`, `litellm` или `unknown`
  (`weblate/trans/judge.py:180-186`).
- `build_request` (`weblate/trans/judge_loop.py:92-108`) - единственный
  конструктор `JudgeRequest` в продуктовом коде: через него идут обычный
  прогон, drain и аудит `JudgeRun`. ID snapshots и slug в этом конструкторе
  покрывают все judge usage-строки.
- `ContextVar` безопасен при `batch_concurrency > 1`, если set/reset остаются
  внутри sync/async `_fetch_llm_batch`; `None` у project означает "вне batch",
  а `""` - "внутри, но scope недоказуем".
- `RoutedLLMTranslation` и `RoutedLiteLLMTranslation`
  (`weblate_customization/src/weblate_customization/machinery.py:137,356`) не
  переопределяют запись usage, поэтому копирование в
  `dev-docker/data/python/` не требуется.

## Поток данных и границы точности

```text
machinery Unit batch                   judge Unit
        |                                    |
        v                                    v
same non-null translation_id?          build_request(ID snapshots)
   | yes               | no                  |
   v                   v                     v
one Translation    blank scope       JudgeRequest(scope + service)
   |                   |                     |
   +----------> accounting seam <------------+
                  | usage.cost + IDs + snapshot slugs
                  v
               LLMUsageLog
                  |
                  +--> detailed rows: service/model/snapshot scope/operation
                  \--> --summary via current IDs
                         |
                         +-- priced_complete=yes
                         \-- attribution_complete=yes
```

---

## Task 1: Поля service, stable identity и scope в LLMUsageLog

**Files:**

- Modify: `weblate/trans/models/llm_usage.py:15-86`
- Create: `weblate/trans/migrations/0113_llm_usage_scope.py` (генерируется)
- Test: `weblate/trans/tests/test_llm_usage.py`

### Step 1: Write the failing tests

В `weblate/trans/tests/test_llm_usage.py`, в конец класса
`LLMUsageLogModelTest`:

```text
    def test_attribution_defaults_blank(self) -> None:
        log = LLMUsageLog.objects.create(model="m", prompt_tokens=1)
        self.assertEqual(log.service, "")
        self.assertIsNone(log.project_id_snapshot)
        self.assertIsNone(log.component_id_snapshot)
        self.assertEqual(log.component_slug, "")
        self.assertEqual(log.target_language_code, "")

    def test_attribution_fields_are_stored(self) -> None:
        log = LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=7,
            project_slug="need-for-greed",
            component_id_snapshot=8,
            component_slug="ui",
            target_language_code="fr",
            prompt_tokens=1,
        )
        log.refresh_from_db()
        self.assertEqual(log.service, "openrouter")
        self.assertEqual(log.project_id_snapshot, 7)
        self.assertEqual(log.component_id_snapshot, 8)
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")

    def test_cost_preserves_provider_precision(self) -> None:
        cost = Decimal("0.123456789123456789")
        log = LLMUsageLog.objects.create(
            model="m",
            prompt_tokens=1,
            cost_usd=cost,
        )
        log.refresh_from_db()
        self.assertEqual(log.cost_usd, cost)
        field = LLMUsageLog._meta.get_field("cost_usd")
        self.assertEqual((field.max_digits, field.decimal_places), (24, 18))

    def test_cost_beyond_supported_scale_is_unpriced(self) -> None:
        self.assertIsNone(
            parse_provider_cost(Decimal("0.1234567891234567891"))
        )
```

Расширить существующий import `LLMUsageLog` также на `parse_provider_cost`.
Тест фиксирует безопасную границу: provider numeric, который БД не может
сохранить без округления, оставляет row unpriced вместо ложной точной суммы.

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL `TypeError: LLMUsageLog() got unexpected keyword argument 'service'`.

Хост-вариант, если контейнер не поднят:

```sh
source scripts/test-database.sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate/trans/tests/test_llm_usage.py -q
```

### Step 3: Add fields, exact-cost guard and report index

В docstring `LLMUsageLog` (`weblate/trans/models/llm_usage.py:19-24`) заменить
утверждение "exactly what OpenRouter billed" на "validated provider cost at the
raw response seam": generic OpenAI-compatible и LiteLLM requests тоже попадают
в эту модель. Перенести `Decimal` из `TYPE_CHECKING` в runtime import и добавить:

```python
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
```

После imports, до `LLMUsageLog`:

```python
COST_USD_MAX_DIGITS = 24
COST_USD_DECIMAL_PLACES = 18
_cost_usd_validator = DecimalValidator(
    max_digits=COST_USD_MAX_DIGITS,
    decimal_places=COST_USD_DECIMAL_PLACES,
)
```

Затем добавить импорт:

```python
from weblate.trans.defines import COMPONENT_NAME_LENGTH, LANGUAGE_CODE_LENGTH
```

`weblate/trans/defines.py` - модуль констант без моделей, цикла импорта нет
(`weblate/trans/models/component.py:62` уже импортирует его так же).

После `model` и `project_slug` (`weblate/trans/models/llm_usage.py:44-45`):

```text
    #: Stable machinery or judge endpoint ID, for example ``openrouter`` or
    #: ``litellm``. Blank is a row written before this migration.
    service = models.CharField(max_length=200, blank=True)
    #: Immutable identities used for current-slug report filters. They are
    #: deliberately scalar snapshots, not FKs: a deleted component must not
    #: rewrite the historical financial row.
    project_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    component_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    #: Human-readable labels at billing time, retained through rename/delete.
    component_slug = models.CharField(max_length=COMPONENT_NAME_LENGTH, blank=True)
    target_language_code = models.CharField(
        max_length=LANGUAGE_CODE_LENGTH, blank=True
    )
```

Заменить существующее поле `cost_usd`:

```text
    cost_usd = models.DecimalField(
        max_digits=COST_USD_MAX_DIGITS,
        decimal_places=COST_USD_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
```

OpenRouter документирует `usage.cost` как число, но не фиксирует масштаб
дробной части. PostgreSQL для текущего `DecimalField(max_digits=12,
decimal_places=8)` создаёт `numeric(12, 8)`, то есть округляет значение с
девятой дробной цифры. Восемнадцать дробных цифр - явный поддерживаемый
предел ledger, а не обещание неограниченной точности. Tasks 2-3 parse-ят
numeric literal JSON через `parse_float=Decimal`, поэтому в пределах этого
предела не возникает потери через binary `float`; шесть целых цифр остаются
на один batch.

После `LLMUsageLog`, до `recent_cost_range`, добавить:

```python
def parse_provider_cost(value: object) -> Decimal | None:
    """Return a cost that the ledger can store without rounding."""
    if value is None:
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not cost.is_finite():
        return None
    try:
        _cost_usd_validator(cost)
    except ValidationError:
        return None
    return cost
```

Writer сохраняет `None`, если provider numeric не влезает в `numeric(24, 18)`.
Так `unpriced` и `priced_complete=no` честно показывают отсутствие точной
суммы, вместо того чтобы PostgreSQL молча округлил стоимость.

В `Meta.indexes` добавить:

```text
            models.Index(
                fields=[
                    "project_id_snapshot",
                    "component_id_snapshot",
                    "target_language_code",
                    "service",
                    "operation",
                    "-created_at",
                ],
                name="llm_usage_scope_recent_idx",
            ),
```

Индекс обязателен: `cleanup_judge_observability` удаляет только judge rows, а
translation ledger растёт без этого retention. Порядок полей покрывает
обычный current-identity filter project -> component -> language -> service
-> operation -> period; `model` остаётся измерением детализации.

### Step 4: Generate the migration

Run:

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations \
  trans --name llm_usage_scope
```

Expected: создан `weblate/trans/migrations/0113_llm_usage_scope.py` с
`dependencies = [("trans", "0112_judge_run_unit_deferred_outcome")]`, пятью
`AddField`, одним `AlterField(cost_usd)` и одним `AddIndex`. БД не нужна.
Существующие строки получают `""` для service/slug scope и `NULL` для ID
snapshots, то есть остаются видимо legacy/unattributed; прежние денежные
значения не теряют точность при расширении decimal scale.

### Step 5: Run tests to verify they pass

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: PASS

### Step 6: Commit

```sh
git add weblate/trans/models/llm_usage.py \
  weblate/trans/migrations/0113_llm_usage_scope.py \
  weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): attribute LLM usage by service and scope"
```

---

## Task 2: Scope, stable identity и service машинного перевода

**Files:**

- Modify: `weblate/machinery/llm.py:47-77`, `2774-2809`, `2899-2931`
- Modify: `weblate/machinery/openai.py:23-28`, `162-184`
- Test: `weblate/machinery/tests.py` (класс `OpenAITranslationTest`)

### Step 1: Write the failing tests

В `weblate/machinery/tests.py`, в класс `OpenAITranslationTest` рядом с
существующими usage-тестами (после `test_usage_recorded_async`, около строки
4405):

```text
    def mock_chat_reply_echo(self, cost: str = "0.123456789123456789") -> None:
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
            request_payload = json.loads(request.content)
            strings = json.loads(
                request_payload["messages"][-1]["content"]
            )["strings"]
            response_payload = {
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
                    "cost": "__COST__",
                },
            }
            return httpx2.Response(
                200,
                headers={},
                content=json.dumps(response_payload).replace(
                    '"__COST__"', cost
                ),
            )

        http_mock.register_callback(
            "POST", re.compile(r"chat/completions"), chat_callback
        )

    @http_mock.activate
    def test_usage_records_the_batch_scope_and_service(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        unit = make_unit(code="fr", source="Alpha")
        unit.translation.component.slug = "ui"
        machine = self.get_machine()
        machine.settings["_project"] = Mock(slug="wrong-project", pk=999)

        machine.download_multiple_translations(
            "en", "fr", [("Alpha", cast("Unit", unit))]
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(log.project_slug, unit.translation.component.project.slug)
        self.assertEqual(
            log.project_id_snapshot, unit.translation.component.project_id
        )
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.component_id_snapshot, unit.translation.component_id)
        self.assertEqual(log.target_language_code, "fr")
        self.assertEqual(log.service, machine.get_identifier())
        self.assertEqual(log.cost_usd, Decimal("0.123456789123456789"))
        self.assertIsNone(llm.llm_batch_project.get())
        self.assertIsNone(llm.llm_batch_project_id.get())
        self.assertIsNone(llm.llm_batch_component_id.get())
        self.assertEqual(llm.llm_batch_component.get(), "")
        self.assertEqual(llm.llm_batch_target_language.get(), "")

    @http_mock.activate
    def test_usage_records_the_batch_scope_and_service_async(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        unit = make_unit(code="fr", source="Alpha")
        unit.translation.component.slug = "ui"
        machine = self.get_machine()

        async_to_sync(machine.adownload_multiple_translations)(
            "en", "fr", [("Alpha", cast("Unit", unit))]
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(log.project_id_snapshot, unit.translation.component.project_id)
        self.assertEqual(log.component_id_snapshot, unit.translation.component_id)
        self.assertEqual(log.component_slug, "ui")
        self.assertEqual(log.target_language_code, "fr")
        self.assertEqual(log.service, machine.get_identifier())
        self.assertEqual(log.cost_usd, Decimal("0.123456789123456789"))
        self.assertIsNone(llm.llm_batch_project.get())
        self.assertIsNone(llm.llm_batch_project_id.get())
        self.assertIsNone(llm.llm_batch_component_id.get())
        self.assertEqual(llm.llm_batch_component.get(), "")
        self.assertEqual(llm.llm_batch_target_language.get(), "")

    @http_mock.activate
    def test_usage_marks_out_of_scale_cost_unpriced(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo("0.1234567891234567891")
        unit = make_unit(code="fr", source="Alpha")

        self.get_machine().download_multiple_translations(
            "en", "fr", [("Alpha", cast("Unit", unit))]
        )

        self.assertIsNone(LLMUsageLog.objects.get().cost_usd)

    @http_mock.activate
    def test_usage_leaves_a_multi_translation_batch_unattributed(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        first = make_unit(code="fr", source="Alpha")
        second = make_unit(code="fr", source="Beta")
        # ``make_unit`` creates unsaved translations with the same synthetic
        # primary key. Make the broken caller observable without a DB fixture.
        second.translation_id = 2
        machine = self.get_machine()
        machine.settings["_project"] = Mock(slug="wrong-project", pk=999)

        translations = machine.download_multiple_translations(
            "en",
            "fr",
            [("Alpha", cast("Unit", first)), ("Beta", cast("Unit", second))],
        )

        self.assertEqual(translations["Alpha"][0]["text"], "Alpha (fr)")
        log = LLMUsageLog.objects.get()
        self.assertEqual(
            (
                log.project_id_snapshot,
                log.project_slug,
                log.component_id_snapshot,
                log.component_slug,
                log.target_language_code,
            ),
            (None, "", None, "", ""),
        )

    @http_mock.activate
    def test_usage_leaves_a_batch_with_an_unscoped_source_unattributed(self) -> None:
        LLMUsageLog.objects.all().delete()
        self.mock_chat_reply_echo()
        unit = make_unit(code="fr", source="Alpha")
        machine = self.get_machine()

        machine.download_multiple_translations(
            "en",
            "fr",
            [("Alpha", cast("Unit", unit)), ("standalone", None)],
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(
            (
                log.project_id_snapshot,
                log.project_slug,
                log.component_id_snapshot,
                log.component_slug,
                log.target_language_code,
            ),
            (None, "", None, "", ""),
        )

    def test_usage_outside_a_batch_uses_service_project_identity(self) -> None:
        LLMUsageLog.objects.all().delete()
        machine = self.get_machine()
        machine.settings["_project"] = Mock(slug="configured-project", pk=99)

        machine.record_llm_usage(
            {
                "id": "direct",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0},
            },
            self.TRACE_MODEL,
        )

        log = LLMUsageLog.objects.get()
        self.assertEqual(
            (log.project_id_snapshot, log.project_slug),
            (99, "configured-project"),
        )
        self.assertIsNone(log.component_id_snapshot)
```

`mock_chat_reply_echo` передаёт cost через raw `content=`, а не `json=`.
Literal `0.123456789123456789` обязан дойти до обоих sync/async ledger rows
без превращения в binary `float`; это регрессия response parser, не только
storage field.

Переименовать существующий `test_usage_cost_zero_is_unpriced`: он проверяет
отсутствующее поле `cost`, а не ноль. Оставить его assertion `is None` и
добавить `test_usage_zero_cost_is_stored`, где `mock_chat_usage` получает
`{"prompt_tokens": 9, "completion_tokens": 12, "cost": 0}` и итоговый
`cost_usd == Decimal("0")`. Это регрессия обоих смыслов поля: отсутствующая
цена остаётся unpriced, объявленная нулевая цена - нет.

`Mock`, `make_unit`, `cast`, `async_to_sync`, `re`, `json`, `httpx2`,
`http_mock`, `llm`, `Decimal` и `LLMUsageLog` уже импортированы
(`weblate/machinery/tests.py:17,21,55,60-113`).

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest" -k "scope or zero_cost"`
Expected: FAIL: отсутствуют новые fields/ContextVars, scope берётся из
`settings["_project"]`, а `cost: 0` превращается в `NULL`.

### Step 3: Replace the scope helper

В `weblate/machinery/llm.py` заменить блок `47-77`. `None` у project
означает, что accounting seam вызван вне `_fetch_llm_batch`; пустые slug и
`None` IDs означают активный batch без доказуемого владельца:

```python
#: Scope of the LLM batch currently fetching. ``None`` means no batch context;
#: empty strings/IDs mean an active batch that cannot be attributed safely.
llm_batch_project: ContextVar[str | None] = ContextVar(
    "llm_batch_project", default=None
)
llm_batch_project_id: ContextVar[int | None] = ContextVar(
    "llm_batch_project_id", default=None
)
llm_batch_component_id: ContextVar[int | None] = ContextVar(
    "llm_batch_component_id", default=None
)
llm_batch_component: ContextVar[str] = ContextVar("llm_batch_component", default="")
llm_batch_target_language: ContextVar[str] = ContextVar(
    "llm_batch_target_language", default=""
)
#: Exact strings in the HTTP request, including split-recovery sub-batches.
llm_batch_unit_count: ContextVar[int] = ContextVar("llm_batch_unit_count", default=0)
```

Заменить `_sources_project_slug` целиком:

```python
def _sources_usage_scope(
    sources: list[tuple[str, Unit | None]],
) -> tuple[str, int | None, int | None, str, str]:
    """
    Return a scope only when one Translation owns every source in a request.

    ``translation_id`` is an already-loaded foreign-key value. Reading it for
    every source avoids an N+1 relation walk; the first related Translation is
    then read once only after all IDs agree.
    """
    if not sources:
        return "", None, None, "", ""
    first_unit = sources[0][1]
    if first_unit is None or first_unit.translation_id is None:
        return "", None, None, "", ""
    translation_id = first_unit.translation_id
    if any(
        unit is None or unit.translation_id != translation_id
        for _text, unit in sources[1:]
    ):
        LOGGER.error("LLM batch spans unscoped or multiple translations")
        return "", None, None, "", ""
    try:
        translation = first_unit.translation
        component = translation.component
        return (
            component.project.slug,
            component.project_id,
            component.pk,
            component.slug,
            translation.language.code,
        )
    except AttributeError:
        return "", None, None, "", ""
```

Это намеренно не сохраняет language отдельного multi-component batch:
финансовый вопрос адресован одной `Translation`, а не частично известному
срезу неправильного запроса. Никакой scope не лучше выдуманной цены.

### Step 4: Set and reset the new variables at both seams

В `_fetch_llm_batch` заменить `weblate/machinery/llm.py:2791-2792`:

```text
        (
            project_slug,
            project_id_snapshot,
            component_id_snapshot,
            component_slug,
            target_language_code,
        ) = _sources_usage_scope(sources)
        project_token = llm_batch_project.set(project_slug)
        project_id_token = llm_batch_project_id.set(project_id_snapshot)
        component_id_token = llm_batch_component_id.set(component_id_snapshot)
        component_token = llm_batch_component.set(component_slug)
        language_token = llm_batch_target_language.set(target_language_code)
        unit_count_token = llm_batch_unit_count.set(len(sources))
```

В существующем внутреннем `finally` после `llm_batch_project.reset` добавить
reset `project_id`, `component_id`, component и language. Внешний `finally`
уже reset-ит `unit_count` и `llm_usage_record`; его не менять. Повторить те же
изменения в `_afetch_llm_batch`.

### Step 5: Parse raw responses, then write service, cost and active scope

В `weblate/machinery/openai.py` `Decimal` уже импортирован. В обоих
`fetch_llm_translations` и `afetch_llm_translations` заменить
`payload = response.json()`:

```python
payload = response.json(parse_float=Decimal)
```

`httpx2.Response.json(**kwargs)` передаёт keyword arguments в
`json.loads(self.content, **kwargs)`, сохраняя существующий parser response и
вместе с `parse_float=Decimal` - decimal значение `usage.cost` до
`record_llm_usage`. Расширить существующий local import:

```python
from weblate.trans.models.llm_usage import LLMUsageLog, parse_provider_cost
```

`parse_provider_cost` сохраняет `int`/`str` direct-call fixture и возвращает
`None` для numeric, который не влезает в `numeric(24, 18)`.

Затем расширить import новыми `llm_batch_project_id` и
`llm_batch_component_id`, помимо component/language ContextVars. Заменить
выбор project (`165-166`) и создание строки:

```text
        project_slug = llm_batch_project.get()
        project_id_snapshot = llm_batch_project_id.get()
        if project_slug is None:
            project = self.settings.get("_project")
            project_slug = project.slug if project is not None else ""
            project_id_snapshot = project.pk if project is not None else None
```

```text
        record = LLMUsageLog.objects.create(
            model=model,
            service=self.get_identifier(),
            project_id_snapshot=project_id_snapshot,
            project_slug=project_slug,
            component_id_snapshot=llm_batch_component_id.get(),
            component_slug=llm_batch_component.get(),
            target_language_code=llm_batch_target_language.get(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=parse_provider_cost(cost),
            response_id=str(payload.get("id") or ""),
            cached_tokens=prompt_details.get("cached_tokens") or 0,
            reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
            operation=LLMUsageLog.Operation.TRANSLATION,
            unit_count=llm_batch_unit_count.get() or None,
            batch_size=batch_size,
        )
```

`None` даёт fallback только для прямого вызова accounting вне batch. Активный
batch с пустым scope не может быть перезаписан project-scoped service setting.

### Step 6: Run tests to verify they pass

Run: `./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest" -k "usage or scope"`
Expected: PASS, включая существующие split-recovery/outcome tests и новые
проверки raw decimal numeric, service, нулевой цены, `None` source, project
override и sync/async ContextVar reset.

### Step 7: Commit

```sh
git add weblate/machinery/llm.py weblate/machinery/openai.py weblate/machinery/tests.py
git commit -m "feat(machinery): attribute LLM usage by service and scope"
```

---

## Task 3: Scope, stable identity, service и нулевая цена judge-запросов

**Files:**

- Modify: `weblate/trans/judge.py:134-144`, `786-800`, `959-972`, `1073-1105`, `1170-1182`, `1391-1394`
- Modify: `weblate/trans/judge_loop.py:92-108`
- Test: `weblate/trans/tests/test_judge_client.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write the failing tests

В `weblate/trans/tests/test_judge_loop.py`, класс `JudgeLoopTest`, рядом с
`test_every_seat_bills_the_units_project` (строка 535):

```text
    def test_request_carries_the_units_scope_identity(self) -> None:
        unit = self.get_unit()
        request = build_request(unit)
        self.assertEqual(request.project_id_snapshot, self.component.project_id)
        self.assertEqual(request.component_id_snapshot, self.component.pk)
        self.assertEqual(request.component_slug, self.component.slug)
        self.assertEqual(request.target_language, unit.translation.language.code)
```

В `weblate/trans/tests/test_judge_client.py` добавить
`from decimal import Decimal` и `_decode_non_stream` в import из
`weblate.trans.judge`. После `test_usage_is_attributed_to_the_project` добавить:

Обновить существующий `test_usage_is_attributed_to_the_project`: передать
`replace(REQ, project_id_snapshot=101, component_id_snapshot=102,
component_slug="ui")` вместо bare `REQ`, затем проверить оба ID snapshots.
Иначе новый корректный scope намеренно оставит старый fixture
неатрибутированным и регрессия станет ложной.

```text
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_attributed_to_component_language_and_service(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost": "__COST__",
        }
        http_mock.register(
            "POST",
            CHAT_URL,
            content=json.dumps(payload).replace(
                '"__COST__"', "0.123456789123456789"
            ),
        )

        request_verdicts(
            [
                replace(
                    REQ,
                    project_id_snapshot=101,
                    component_id_snapshot=102,
                    component_slug="ui",
                )
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )

        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.service, "openrouter")
        self.assertEqual(row.project_id_snapshot, 101)
        self.assertEqual(row.project_slug, "need-for-greed")
        self.assertEqual(row.component_id_snapshot, 102)
        self.assertEqual(row.component_slug, "ui")
        self.assertEqual(row.target_language_code, "fr")
        self.assertEqual(row.cost_usd, Decimal("0.123456789123456789"))

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_records_zero_provider_cost(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0}
        http_mock.register("POST", CHAT_URL, json=payload)

        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)

        self.assertEqual(
            LLMUsageLog.objects.get(model="vendor/model-a").cost_usd,
            Decimal("0"),
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_marks_out_of_scale_cost_unpriced(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost": "__COST__",
        }
        http_mock.register(
            "POST",
            CHAT_URL,
            content=json.dumps(payload).replace(
                '"__COST__"', "0.1234567891234567891"
            ),
        )

        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)

        self.assertIsNone(
            LLMUsageLog.objects.get(model="vendor/model-a").cost_usd
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_leaves_a_mixed_batch_unattributed(self) -> None:
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
                replace(
                    REQ,
                    project_id_snapshot=101,
                    component_id_snapshot=102,
                    component_slug="ui",
                ),
                replace(
                    REQ,
                    unit_key="OTHER",
                    project_id_snapshot=101,
                    component_id_snapshot=103,
                    component_slug="loot",
                ),
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(
            (
                row.project_id_snapshot,
                row.project_slug,
                row.component_id_snapshot,
                row.component_slug,
                row.target_language_code,
            ),
            (None, "", None, "", ""),
        )
```

В `JudgeSSETest` добавить coverage обоих parser путей. Каждый response
передаёт literal `0.123456789123456789` как raw bytes, а не Python float:

```text
    def test_non_stream_preserves_usage_decimal(self) -> None:
        response = httpx2.Response(
            200,
            content=(
                b'{"usage":{"cost":0.123456789123456789},'
                b'"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
            ),
        )
        payload, failure, *_ = _decode_non_stream(response, time.monotonic())
        self.assertEqual(failure, "")
        assert payload is not None
        self.assertEqual(
            payload["usage"]["cost"], Decimal("0.123456789123456789")
        )

    def test_stream_preserves_usage_decimal(self) -> None:
        response = httpx2.Response(
            200,
            content=(
                b'data: {"usage":{"cost":0.123456789123456789},'
                b'"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )
        payload, failure, *_ = _read_sse(response, started=time.monotonic())
        self.assertEqual(failure, "")
        assert payload is not None
        self.assertEqual(
            payload["usage"]["cost"], Decimal("0.123456789123456789")
        )
```

`replace`, `REQ`, `CHAT_URL`, `_reply`, `json`, `httpx2` и `_read_sse` уже
импортированы; новые imports нужны только для точных Decimal parser assertions.

### Step 2: Run tests to verify they fail

Expected: FAIL: у `JudgeRequest` нет ID snapshots/component slug, usage row не
содержит service, и `cost: 0` становится `NULL`.

### Step 3: Add the dataclass fields

В `weblate/trans/judge.py`, в конец `JudgeRequest` (после `target_plurals`,
строка 144):

```text
    #: Immutable scope plus labels at billing time. Defaults preserve direct
    #: historical constructions; any missing value leaves the whole request
    #: unattributed.
    project_id_snapshot: int | None = None
    component_id_snapshot: int | None = None
    component_slug: str = ""
```

В `weblate/trans/judge_loop.py:95-108`, в `build_request`, добавить после
`target_language=translation.language.code`:

```text
        project_id_snapshot=translation.component.project_id,
        component_id_snapshot=translation.component_id,
        component_slug=translation.component.slug,
```

### Step 4: Parse raw judge responses, then derive and write scope

В `weblate/trans/judge.py` `json` и `Decimal` уже импортированы. Чтобы
`usage.cost` не прошёл через binary `float`, заменить оба parser вызова:

```python
# _consume_sse_event
event = json.loads(data, parse_float=Decimal)

# _decode_non_stream
payload = json.loads(raw, parse_float=Decimal)
```

Так stream и non-stream response сохраняют тот же decimal numeric literal до
`_write_llm_usage`. Расширить import на:

```python
from weblate.trans.models.llm_usage import LLMUsageLog, parse_provider_cost
```

`parse_provider_cost` оставляет не помещающийся numeric `NULL`, поэтому
`priced_complete=no` вместо ложной суммы с rounding.

Затем добавить перед `_write_llm_usage`:

```python
def _batch_usage_scope(
    batch: Sequence[JudgeRequest],
    project_slug: str,
) -> tuple[int | None, str, int | None, str, str]:
    """Return one complete scope only when every request agrees."""
    project_ids = {request.project_id_snapshot for request in batch}
    component_ids = {request.component_id_snapshot for request in batch}
    component_slugs = {request.component_slug for request in batch}
    languages = {request.target_language for request in batch}
    if (
        not project_slug
        or None in project_ids
        or None in component_ids
        or "" in component_slugs
        or "" in languages
        or len(project_ids) != 1
        or len(component_ids) != 1
        or len(component_slugs) != 1
        or len(languages) != 1
    ):
        LOGGER.error("judge batch has an unscoped or mixed translation identity")
        return None, "", None, "", ""
    return (
        next(iter(project_ids)),
        project_slug,
        next(iter(component_ids)),
        next(iter(component_slugs)),
        next(iter(languages)),
    )
```

Заменить сигнатуру `_write_llm_usage` так, чтобы она принимала
`service: str`, `model: str`, `project_slug: str`, `batch` и
`request_attempt`. Внутри:

```text
    cost = usage.get("cost")
    (
        project_id_snapshot,
        project_slug,
        component_id_snapshot,
        component_slug,
        target_language_code,
    ) = _batch_usage_scope(batch, project_slug)
    LLMUsageLog.objects.create(
        model=model,
        service=service,
        project_id_snapshot=project_id_snapshot,
        project_slug=project_slug,
        component_id_snapshot=component_id_snapshot,
        component_slug=component_slug,
        target_language_code=target_language_code,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage.get("total_tokens") or (prompt_tokens + completion_tokens),
        cost_usd=parse_provider_cost(cost),
        response_id=str(payload.get("id") or ""),
        cached_tokens=details.get("cached_tokens") or 0,
        reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
        operation=LLMUsageLog.Operation.JUDGE,
        unit_count=len(batch),
        batch_size=len(batch),
        request_attempt=request_attempt,
    )
```

`_record_usage` сохраняет `project_slug` и передаёт его в
`_write_llm_usage(payload, service, model, project_slug, batch, attempt)`.
В единственном вызове из `_run_batch` передать:

```text
        if persistence:
            _record_usage(
                response.payload,
                profile.provider,
                profile.model,
                project_slug,
                batch,
                attempt,
            )
```

`project_slug` уже является scope текущего judge run; он читается один раз
на batch, а `JudgeRequest` не загружает `component.project` для каждого unit.
`profile.provider` классифицирует endpoint (`openrouter`, `litellm`,
`unknown`), поэтому service не выводится из model alias и не раскрывает URL
или ключ.

### Step 5: Run tests to verify they pass

Run:

```sh
./rundev.sh test weblate/trans/tests/test_judge_client.py
./rundev.sh test weblate/trans/tests/test_judge_loop.py
```

Expected: PASS. Новые raw Decimal tests покрывают stream, non-stream и
persisted usage; существующие проверки `unit_count`
(`test_judge_client.py:1289`, `1316`) остаются зелёными: `len(batch)` равен
прежнему `size`.

### Step 6: Commit

```sh
git add weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): attribute usage by service and scope"
```

---

## Task 4: Отчёт с stable-identity filter, completeness и одним итогом

**Files:**

- Modify: `weblate/trans/management/commands/llm_usage_report.py`
- Test: `weblate/trans/tests/test_llm_usage.py`

### Step 1: Write the failing tests

В `weblate/trans/tests/test_llm_usage.py` добавить `import csv`,
`from django.core.management.base import CommandError` и
`from weblate.trans.tests.test_views import ComponentTestCase`.

`LLMUsageReportTest.setUp` должен создавать synthetic rows с ID snapshots:

```text
    def setUp(self) -> None:
        LLMUsageLog.objects.create(
            model="m1",
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            batch_size=10,
            cost_usd=Decimal("0.001000000000000001"),
        )
        LLMUsageLog.objects.create(
            model="m1",
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=4,
            completion_tokens=1,
            total_tokens=5,
            batch_size=5,
        )
        LLMUsageLog.objects.create(
            model="m3",
            service="openrouter",
            project_id_snapshot=1,
            project_slug="col4",
            component_id_snapshot=2,
            component_slug="ui",
            target_language_code="fr",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
            batch_size=2,
            cost_usd=Decimal("0.000000000123456789"),
        )
        LLMUsageLog.objects.create(
            model="m2",
            service="litellm",
            project_id_snapshot=3,
            project_slug="st2",
            component_id_snapshot=4,
            component_slug="hub-1",
            target_language_code="de",
            operation=LLMUsageLog.Operation.JUDGE,
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            batch_size=2,
            cost_usd=Decimal("0.000000500000000000"),
        )
```

Добавить:

```text
    def test_csv_report_groups_by_service_component_and_language(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--format", "csv", stdout=out)
        rows = list(csv.reader(out.getvalue().strip().splitlines()))

        self.assertEqual(
            rows[0],
            [
                "service",
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
                "priced_complete",
                "unattributed_requests",
                "attribution_complete",
            ],
        )
        self.assertEqual(
            rows[1],
            [
                "litellm",
                "m2",
                "st2",
                "hub-1",
                "de",
                "judge",
                "1",
                "2",
                "7",
                "3",
                "0.000000500000000000",
                "0",
                "yes",
                "",
                "",
            ],
        )

    def test_summary_reports_pricing_and_scope_completeness(self) -> None:
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(
            rows[1],
            [
                "openrouter",
                "*",
                "*",
                "*",
                "*",
                "translation",
                "3",
                "17",
                "17",
                "8",
                "0.001000000123456790",
                "1",
                "no",
                "0",
                "yes",
            ],
        )

    def test_summary_marks_blind_scope_unknown(self) -> None:
        LLMUsageLog.objects.create(
            model="m4",
            service="openrouter",
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=3,
        )
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(row[-2:], ["1", "unknown"])

    def test_summary_marks_a_fully_priced_total_complete(self) -> None:
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--service",
            "litellm",
            "--operation",
            "judge",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )
        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(
            row[10:],
            ["0.000000500000000000", "0", "yes", "0", "yes"],
        )

    def test_service_filter(self) -> None:
        out = StringIO()
        call_command("llm_usage_report", "--service", "litellm", stdout=out)
        self.assertIn("hub-1", out.getvalue())
        self.assertNotIn("col4", out.getvalue())

    def test_component_requires_project(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "--component requires an existing --project.",
        ):
            call_command("llm_usage_report", "--component", "ui")

    def test_days_must_be_positive(self) -> None:
        with self.assertRaisesMessage(CommandError, "--days must be at least 1."):
            call_command("llm_usage_report", "--days", "0")

    def test_unattributed_rows_stay_visible(self) -> None:
        LLMUsageLog.objects.create(
            model="m4",
            service="openrouter",
            project_slug="col4",
            prompt_tokens=3,
        )
        out = StringIO()
        call_command(
            "llm_usage_report",
            "--model",
            "m4",
            "--format",
            "csv",
            stdout=out,
        )
        rows = list(csv.reader(out.getvalue().strip().splitlines()))
        self.assertEqual(rows[1][2:6], ["col4", "-", "-", "-"])
```

Добавить integration regression в новом классе:

```python
class LLMUsageReportIdentityTest(ComponentTestCase):
    def test_current_slugs_include_cost_recorded_before_rename(self) -> None:
        LLMUsageLog.objects.create(
            model="m",
            service="openrouter",
            project_id_snapshot=self.project.pk,
            project_slug=self.project.slug,
            component_id_snapshot=self.component.pk,
            component_slug=self.component.slug,
            target_language_code=self.translation.language.code,
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=1,
            batch_size=1,
            cost_usd=Decimal("0.1"),
        )
        self.project.slug = "renamed-project"
        self.project.save(update_fields=["slug"])
        self.component.slug = "renamed-component"
        self.component.save(update_fields=["slug"])

        out = StringIO()
        call_command(
            "llm_usage_report",
            "--project",
            "renamed-project",
            "--component",
            "renamed-component",
            "--language",
            self.translation.language.code,
            "--service",
            "openrouter",
            "--operation",
            "translation",
            "--summary",
            "--format",
            "csv",
            stdout=out,
        )

        row = list(csv.reader(out.getvalue().strip().splitlines()))[1]
        self.assertEqual(
            row[10:],
            ["0.100000000000000000", "0", "yes", "0", "yes"],
        )

    def test_unknown_current_identity_is_rejected(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            'Project "missing" does not exist.',
        ):
            call_command("llm_usage_report", "--project", "missing")
        with self.assertRaisesMessage(
            CommandError,
            'Component "missing" does not exist.',
        ):
            call_command(
                "llm_usage_report",
                "--project",
                self.project.slug,
                "--component",
                "missing",
            )
```

Удалить старый `test_csv_report`: новый контракт проверяет детализацию,
precision, service, identity, pricing completeness и unknown scope.

### Step 2: Run tests to verify they fail

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: FAIL на ID fields, `--summary`, stable current-slug resolution,
`priced_complete`/`attribution_complete` и `--days 0`.

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

from django.core.management.base import CommandError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from weblate.trans.models.component import Component
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.project import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

HEADER = [
    "service",
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
    "priced_complete",
    "unattributed_requests",
    "attribution_complete",
]

GROUP_FIELDS = [
    "service",
    "model",
    "project_id_snapshot",
    "project_slug",
    "component_id_snapshot",
    "component_slug",
    "target_language_code",
    "operation",
]

TOTALS = {
    "requests": Count("id"),
    "strings": Sum("batch_size"),
    "prompt": Sum("prompt_tokens"),
    "completion": Sum("completion_tokens"),
    "cost": Sum("cost_usd"),
    "unpriced": Count("id", filter=Q(cost_usd__isnull=True)),
}


def _display(value: str) -> str:
    return value or "-"


def _row(
    *,
    service: str,
    model: str,
    project: str,
    component: str,
    language: str,
    operation: str,
    totals: dict,
    unattributed_requests: int | None = None,
) -> list[str | int]:
    cost = totals["cost"]
    unpriced = totals["unpriced"] or 0
    attribution_complete = (
        ""
        if unattributed_requests is None
        else "yes"
        if unattributed_requests == 0
        else "unknown"
    )
    return [
        _display(service),
        _display(model),
        _display(project),
        _display(component),
        _display(language),
        _display(operation),
        totals["requests"] or 0,
        totals["strings"] or 0,
        totals["prompt"] or 0,
        totals["completion"] or 0,
        format(cost, "f") if cost is not None else "",
        unpriced,
        "yes" if not unpriced else "no",
        "" if unattributed_requests is None else unattributed_requests,
        attribution_complete,
    ]


class Command(BaseCommand):
    help = (
        "reports LLM token usage and cost by service, model, project, component, "
        "target language and operation"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days", type=int, default=None, help="only the last N days"
        )
        parser.add_argument("--service", default=None, help="only this service ID")
        parser.add_argument("--model", default=None, help="only this model")
        parser.add_argument("--project", default=None, help="current project slug")
        parser.add_argument(
            "--component",
            default=None,
            help="current component slug; requires --project",
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
        parser.add_argument(
            "--summary",
            action="store_true",
            help="emit one total after filters instead of model-level rows",
        )
        parser.add_argument("--format", choices=["table", "csv"], default="table")

    def handle(self, *args, **options) -> None:
        logs = LLMUsageLog.objects.all()
        days = options["days"]
        if days is not None:
            if days < 1:
                raise CommandError("--days must be at least 1.")
            logs = logs.filter(created_at__gte=timezone.now() - timedelta(days=days))
        for option, field in (
            ("service", "service"),
            ("model", "model"),
            ("operation", "operation"),
        ):
            if options[option]:
                logs = logs.filter(**{field: options[option]})

        scope_logs = logs
        project = None
        if options["project"]:
            try:
                project = Project.objects.only("id").get(slug=options["project"])
            except Project.DoesNotExist as error:
                raise CommandError(
                    f'Project "{options["project"]}" does not exist.'
                ) from error
            logs = logs.filter(project_id_snapshot=project.pk)
        if options["component"]:
            if project is None:
                raise CommandError("--component requires an existing --project.")
            try:
                component = Component.objects.only("id").get(
                    project_id=project.pk,
                    slug=options["component"],
                )
            except Component.DoesNotExist as error:
                raise CommandError(
                    f'Component "{options["component"]}" does not exist.'
                ) from error
            logs = logs.filter(component_id_snapshot=component.pk)
        if options["language"]:
            logs = logs.filter(target_language_code=options["language"])
            scope_logs = scope_logs.filter(
                Q(target_language_code=options["language"]) | Q(target_language_code="")
            )

        if options["summary"]:
            unattributed_requests = scope_logs.filter(
                Q(project_id_snapshot__isnull=True)
                | Q(component_id_snapshot__isnull=True)
                | Q(target_language_code="")
            ).count()
            totals = logs.aggregate(**TOTALS)
            data = [
                _row(
                    service=options["service"] or "*",
                    model=options["model"] or "*",
                    project=options["project"] or "*",
                    component=options["component"] or "*",
                    language=options["language"] or "*",
                    operation=options["operation"] or "*",
                    totals=totals,
                    unattributed_requests=unattributed_requests,
                )
            ]
        else:
            rows = logs.values(*GROUP_FIELDS).annotate(**TOTALS).order_by(*GROUP_FIELDS)
            data = [
                _row(
                    service=row["service"],
                    model=row["model"],
                    project=row["project_slug"],
                    component=row["component_slug"],
                    language=row["target_language_code"],
                    operation=row["operation"],
                    totals=row,
                )
                for row in rows
            ]
        if options["format"] == "csv":
            writer = csv.writer(self.stdout)
            writer.writerow(HEADER)
            writer.writerows(data)
            return
        widths = [
            max(len(str(line[index])) for line in [HEADER, *data])
            for index in range(len(HEADER))
        ]
        for line in [HEADER, *data]:
            self.stdout.write(
                "  ".join(
                    str(cell).ljust(widths[index]) for index, cell in enumerate(line)
                )
            )
```

`format(cost, "f")` намеренно не использует `:.8f`: CSV не должен снова
округлять уже проверенную storage цену. `priced_complete` описывает только
включённые rows. `attribution_complete=unknown` намеренно консервативен:
любой unscoped request с теми же service/model/operation/time мог включать
запрошенный component, даже если его project ID уже пуст.

### Step 4: Run tests to verify they pass

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: PASS.

### Step 5: Commit

```sh
git add weblate/trans/management/commands/llm_usage_report.py \
  weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): report complete scoped LLM cost totals"
```

## Task 5: Service- and identity-scoped observed-cost preview and changelog

**Files:**

- Modify: `weblate/trans/models/llm_usage.py:92-116`
- Modify: `weblate/trans/views/edit.py:1544-1587`
- Modify: `docs/changes.rst:22`
- Test: `weblate/trans/tests/test_llm_usage.py` (`RecentCostRangeTest`)
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write the failing tests

В `RecentCostRangeTest._create` добавить keywords
`project_id_snapshot=1`, `service="openrouter"` и передавать оба в
`LLMUsageLog.objects.create`. Обновить все вызовы `recent_cost_range`:
первым аргументом теперь `1`, вторым - service. Добавить:

```text
    def test_never_mixes_service_or_project_identity(self) -> None:
        for _ in range(5):
            self._create(
                cost=Decimal("0.001"),
                unit_count=1,
                project_id_snapshot=1,
                service="openrouter",
            )
            self._create(
                cost=Decimal("9.000"),
                unit_count=1,
                project_id_snapshot=2,
                service="openrouter",
            )
            self._create(
                cost=Decimal("9.000"),
                unit_count=1,
                project_id_snapshot=1,
                service="litellm",
            )

        low, high = recent_cost_range(
            1,
            "openrouter",
            "m1",
            LLMUsageLog.Operation.TRANSLATION,
        )

        self.assertEqual((low, high), (Decimal("0.001"), Decimal("0.001")))
```

В `JudgeAutoTranslateViewTest.test_preview_uses_observed_judge_costs` задать
`project_id_snapshot=self.component.project_id` и `service="openrouter"` у
пяти полезных rows. Добавить пять rows того же model/operation, но с другим
project ID или `service="litellm"` и ценой `9`. Ожидаемые `min == "0.12"` и
`max == "0.24"` не меняются. Это доказывает, что view не смешивает rename-safe
identity или service.

### Step 2: Run tests to verify they fail

Run:

```sh
./rundev.sh test weblate/trans/tests/test_llm_usage.py -k RecentCostRange
./rundev.sh test weblate/trans/tests/test_judge_views.py -k observed_judge_costs
```

Expected: FAIL: `recent_cost_range` не принимает service/ID и смешивает
одинаковую model ID от OpenRouter и LiteLLM либо от другого project ID.

### Step 3: Isolate ranges and update the changelog

В `weblate/trans/models/llm_usage.py` изменить сигнатуру:

```text
def recent_cost_range(
    project_id_snapshot: int, service: str, model: str, operation: str
) -> tuple[Decimal, Decimal] | None:
```

и заменить `project_slug=...` на
`project_id_snapshot=project_id_snapshot`, добавив `service=service` в
существующий `LLMUsageLog.objects.filter(...)`. Это намеренно исключает legacy
rows с пустыми IDs/service: preview станет `available` только после пяти
priced rows нового ledger, вместо неточной смеси до- и после-миграционных
данных.

Обновить docstring функции: range теперь требует exact
`project_id_snapshot/service/model/operation`, а не текущий slug. Это
сохраняет observed preview после rename проекта.

В `weblate/trans/views/edit.py` заменить `project_slugs` на `project_ids`,
полученные из `translation.component.project_id`. В обоих вызовах передать ID:

```python
recent_cost_range(
    project_id,
    profile.provider,
    profile.model,
    LLMUsageLog.Operation.JUDGE,
)
```

```python
recent_cost_range(
    translation.component.project_id,
    machine.get_identifier(),
    model,
    LLMUsageLog.Operation.TRANSLATION,
)
```

Расширить существующий пункт `docs/changes.rst:22`, не создавая второй:

```rst
* LLM machine translation and LLM judge requests now record per-request token usage and provider-reported cost with stable service, project, component, and target-language attribution. The ``llm_usage_report`` management command resolves current project and component slugs, reports attribution completeness, and can emit one scoped total with ``--summary``.
```

### Step 4: Run tests and lint

Run:

```sh
./rundev.sh test weblate/trans/tests/test_llm_usage.py
./rundev.sh test weblate/trans/tests/test_judge_views.py
uv run prek run --files docs/changes.rst
```

Expected: PASS.

### Step 5: Commit

```sh
git add weblate/trans/models/llm_usage.py weblate/trans/views/edit.py \
  weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_views.py \
  docs/changes.rst
git commit -m "fix(trans): isolate observed LLM costs by service"
```

---

## Task 6: Проверка и выкладка

### Step 1: Lint and format

```sh
uv run prek run --files weblate/machinery/llm.py weblate/machinery/openai.py \
  weblate/machinery/tests.py weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/llm_usage.py weblate/trans/views/edit.py \
  weblate/trans/management/commands/llm_usage_report.py \
  weblate/trans/migrations/0113_llm_usage_scope.py \
  weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_views.py \
  docs/changes.rst
```

Expected: PASS.

### Step 2: Pylint

```sh
uv run pylint weblate/machinery/llm.py weblate/machinery/openai.py \
  weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/models/llm_usage.py weblate/trans/views/edit.py \
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

Expected: PASS. `test_judge_views.py` теперь обязан проверить, что observed
range фильтруется и по service, а не смешивает одинаковую model ID от разных
шлюзов.

### Step 5: Migration check

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

### Step 6: Dev smoke test

После применения миграции создать или выбрать отдельный временный компонент с
уникальным `(project, component, language)`, прогнать один реальный batch
OpenRouter и запросить именно его scope:

```sh
docker exec dev-docker-weblate-1 weblate migrate
docker exec dev-docker-weblate-1 weblate llm_usage_report \
  --project llm-usage-smoke --component smoke-ui --language fr \
  --service openrouter --operation translation --summary --format csv
```

Expected: ровно одна summary row с `requests > 0`, непустыми current
project/component/service, ненулевым `strings_asked` и явными
`priced_complete`/`attribution_complete`. `priced_complete=no` или
`attribution_complete=unknown` - не smoke failure, а сигнал не называть сумму
полной без расследования соответствующих rows.

### Step 7: Commit and push

```sh
git push -u origin <branch>
```

### Step 8: Production

Только после явного разрешения на production:

1. Применить additive migration `0113_llm_usage_scope` **до** запуска любого
   worker/web image с новым `LLMUsageLog.objects.create(...)`. Старый код
   совместим с новыми пустыми columns.
2. Убедиться, что миграция завершилась успешно, затем выкатить application
   code и дождаться reload всех writers.
3. После первого нового LLM batch снять scoped `--summary` отчёт и сохранить
   вывод как evidence rollout.

Код нельзя выкатывать до migration: новые ORM kwargs обращаются к отсутствующим
columns, recorder ловит DB exception и логирует её, но usage row **теряется**;
он не записывается "без scope".

---

## Как считать стоимость языка и компонента после выкладки

```sh
# детализация проекта за период, включая legacy/unattributed rows
weblate llm_usage_report --project need-for-greed --days 30 --format csv > cost.csv

# один ответ: OpenRouter перевод на французский в UI
weblate llm_usage_report \
  --project need-for-greed --component ui --language fr \
  --service openrouter --operation translation --summary --format csv

# отдельный итог judge на том же scope; service берётся из judge detail rows
weblate llm_usage_report \
  --project need-for-greed --component ui --language fr \
  --service litellm --operation judge --summary --format csv
```

`--summary` отвечает на денежный вопрос одним рядом. `--project` и
`--component` сначала резолвятся в current immutable IDs, поэтому строка,
записанная до rename, включается в тот же component total. Сумма является
полной стоимостью **записанного ledger scope** только при **обоих**
`priced_complete=yes` и `attribution_complete=yes`. `unknown` не означает
ошибку суммы включённых rows: он означает, что рядом существует хотя бы один
unscoped request, который журнал не может исключить из этого component.
Оплаченная попытка без полученного тела остаётся отдельным пределом ниже.

Соединение с объёмом требует осторожности: `cost_usd` и `strings_asked` -
величины за выбранный период и включают повторы, а `translated` из
`GET /api/components/<project>/<component>/statistics/` - текущее накопленное
состояние. Поэтому:

- `cost_usd / strings_asked` - корректная средняя цена оплаченного
  строко-запроса из одной summary/detail row;
- `strings_asked / translated` и `cost_usd / translated` - только грубая
  оценка, допустимая, лишь когда период покрывает всю историю расхода и
  текущее содержимое компонента произведено именно этим расходом;
- точная цена одной строки недостижима: provider выставляет счёт за запрос, а
  не за строку.

Без `--operation` отчёт складывает translation и judge, но по-прежнему
разделяет service и model в detailed режиме. До выкладки Task 3 брать
component-level judge total нельзя: его scope пуст.

## Известные пробелы

Их нужно называть в любом отчёте, иначе сумма выглядит полнее, чем есть:

1. **История не восстанавливается.** Строки до миграции несут пустые service и
   scope; связи между конкретным OpenRouter-запросом и изменениями строк нет,
   поэтому Need for Greed за 2026-08-17 - 2026-08-28 остаётся
   неатрибутированным.
2. **`cost_usd IS NULL`.** Provider иногда не отдаёт цену. Такая row делает
   `priced_complete=no`; показанный `cost_usd` - нижняя граница включённых
   rows.
3. **Оплаченный запрос без тела ответа.** При transport/deadline usage row не
   пишется вовсе, поэтому журнал может быть меньше инвойса. Для judge попытка
   видна в `JudgeRequestAttempt`; для machinery - только в логах.
4. **Неатрибутированный batch.** `None` source или несколько
   `translation_id` оставляют IDs и scope пустыми. В `--summary` они дают
   `attribution_complete=unknown` консервативно: row может относиться к
   запрошенному component, но распределять её без выдуманной цены нельзя.
5. **Judge retention.** `cleanup_judge_observability` удаляет judge usage
   после `LLM_USAGE_LOG_RETENTION_DAYS` (90 дней по умолчанию). Translation
   usage текущий cleanup не удаляет.
6. **loc-kit profile analysis не пишет usage.** Он ходит в OpenRouter по
   отдельной site-wide конфигурации до создания компонента и в этот ledger не
   попадает.
7. **Кеш Weblate.** Повторно использованный перевод не стоит ничего; цена
   строки внутри группы неравномерна, хотя сумма записанных запросов точна.
8. **Граница decimal schema.** Provider numeric с более чем 24 digits или 18
   дробными places не округляется: `parse_provider_cost` сохраняет `NULL`, и
   `priced_complete=no` запрещает назвать summary точной. Это безопаснее
   неявного PostgreSQL rounding; расширение schema потребует отдельной
   миграции и новых доказательных tests.

## Out of scope

- Группировка входных батчей по `Translation` в `fetch_machinery_matches`.
  Guard этого плана лишь отказывается атрибутировать смешанный batch; исправить
  его первопричину - отдельный change поведения machinery.
- UI-страница расходов. Управляемый management command закрывает вопрос без
  новой permissioned поверхности.
- Изменение 90-дневного retention judge usage. Это отдельное решение о
  финансовом хранении и объёме БД; текущий лимит явно отражён выше.
- Запрос OpenRouter invoice/generation API для backfill или сверки пропавших
  response bodies. Нужны отдельные credential, data-retention и reconciliation
  правила.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| Eng review | User request | Architecture, correctness, tests, and performance | 1 | CLEARED | 15 findings folded into Tasks 1-6 |
| Independent review | `reviewer` | Adversarial code-to-plan check | 1 | CLEARED | Confirmed precision and migration-order defects; both fixed |
| Precision follow-up | System advisory | Raw JSON numeric precision at both response seams | 1 | CLEARED | Added Decimal parsers and bounded-cost guard |
| CEO review | Not run | No product-scope decision remains | 0 | N/A | Backend accounting plan |
| Design review | Not run | No UI scope | 0 | N/A | Not applicable |

**VERDICT:** ENG, independent and precision follow-up reviews cleared - the
revised plan preserves stable identity across rename, distinguishes priced from
attributed completeness, preserves raw JSON decimal values within declared
schema limits, returns one answer, and orders migration before writers.

NO UNRESOLVED DECISIONS
