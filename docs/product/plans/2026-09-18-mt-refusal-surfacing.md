# План: поверхность отказов MT через ProducerRun и REST

Дата: 2026-09-18
Статус: реализовано (2026-09-18, ветка feat/mt-refusal-surfacing). Одобрение плана не разрешает деплой.

## Проблема

Автоматический перевод может завершиться с отказом LLM, а ран при этом выглядит успешным.

Подтверждённый production-кейс (2026-09-18, `pirate-ships`, `localization-json/ko`,
ключ `update_1_44_info_5`):

- `ProducerRun` `147b6c33`: `status=completed`, `failure=''`, `warnings=[]`, `written=0`.
- `LLMUsageLog` для того же рана: `outcome=refused`,
  `refusal_reason='Mismatching assistant reply items.'`, `reply_excerpt` заполнен.
- 14 соседних языковых ранов того же ключа завершились `applied`.

Корневая причина: `_fetch_machinery_batch`
(`weblate/trans/machinery.py:48-56`) ловит `MachineTranslationError`, логирует
`failed automatic translation` и возвращает `True`, чтобы оставить batch локальным.
Исключение не доходит до failure-пути `AutoTranslate` (`weblate/trans/autotranslate.py:1207-1211`),
поэтому `_finish_producer_run` финализирует ран как `COMPLETED` без failure и warnings.
Это deliberate-поведение batch-слоя (отказ полного batch восстанавливается делением
пополам, `weblate/machinery/llm.py:3067-3088`), но для batch из одной строки
`_split_sources` возвращает `None` и отказ терминален — строка остаётся пустой,
а ран выглядит чистым.

Фактический источник истины об отказах уже существует: `LLMUsageLog`
(`weblate/trans/models/llm_usage.py`) хранит `outcome`
(`applied`/`partial`/`refused`), `refusal_reason`, `reply_excerpt` и FK `run`
(`related_name="usage_logs"`). Запись создаётся в
`weblate/machinery/openai.py:_write_llm_usage` с `run_id=self.usage_run_id`,
а `usage_run_id` выставляется из автоперевода
(`weblate/trans/autotranslate.py:702-703`).

## Решение

Не трогать локальное поглощение ошибок в `_fetch_machinery_batch`. Вместо этого
сделать статус и warnings рана производными от usage-лога на финализации, и
отдать warnings наружу через REST. Прецедент паттерна уже есть: judge-режимы
агрегируют `JudgeRunUnit` в `_finish_producer_run`
(`weblate/trans/autotranslate.py:2399-2420`).

Почему не альтернатива «пропагировать исключение из batch-слоя»: это меняет
поведение recovery через деление пополам, рискует ложными FAILED на частичных
отказах и требует менять контракт batch-слоя. Агрегация usage-лога непротиворечива
(`SET_NULL`-защита FK, агрегация идёт по `run` внутри финализации), идемпотентна
(финализация терминальна) и покрывает оба пути — синхронный REST и Celery.

## Область

- `weblate/trans/autotranslate.py` — `_finish_producer_run`.
- `weblate/api/views.py` — `TranslationViewSet.autotranslate`.
- `weblate/trans/tests/test_autotranslate.py`, `weblate/api/tests.py` — тесты.
- `docs/specs/openapi.yaml` (через `make -C docs update-openapi`), `docs/changes.rst`.

## Non-goals

- Не менять `_fetch_machinery_batch` и механизм деления batch пополам.
- Не создавать ProducerRun для `auto_source="others"`/TM и `component_copy.py:148`.
- Не чинить исходную разметку `update_1_44_info_5` (висячий `</size>`) — отдельная
  producer-задача, не кодовая.
- Ретрай валидационных отказов — отложен, см. ниже.
- Без миграций: поля `warnings`, `failure`, `summary`, статус `PARTIAL`
  (`weblate/trans/models/judge.py:337-345`) и usage-связь уже существуют.

## Задача 1. Статус и warnings рана из usage-лога

**Файл:** `weblate/trans/autotranslate.py` (`_finish_producer_run`, 2356-2451).

Добавить в `requested_mode == "translate"`-ветвь (рядом с judge-агрегацией,
2399-2420) агрегацию по translation-записям usage-лога рана:

1. Выбрать `LLMUsageLog.objects.filter(run=run,
   operation=LLMUsageLog.Operation.TRANSLATION)`, исключая `outcome=''` и
   `outcome=LLMUsageLog.Outcome.APPLIED`.
2. Построить дедуплицированные warnings: по каждой distinct-паре
   `(outcome, refusal_reason)` — строка с причиной и числом запросов.
   Текст через gettext, не локализуемые идентификаторы не выводить.
3. Статус:
   - уже `FAILED`/`CANCELLED`/`PARTIAL` — не трогать;
   - есть refused-записи и нет ни одной `applied` translation-записи —
     `COMPLETED` → `PARTIAL`;
   - есть refused-записи и `applied` тоже есть — оставить `COMPLETED`,
     только warnings (отказ мог быть восстановлен делением пополам);
   - `partial`-записи без `refused` — только warning, статус не менять.
4. Не учитывать `operation=LLMUsageLog.Operation.JUDGE` (judge-агрегация уже
   есть и ветвится по `requested_mode`).

Warnings попадают в существующие поверхности автоматически: отчёт рана
(`weblate/templates/producer-run.html:46-48` рендерит `run.warnings`), Celery-результат
(`weblate/trans/tasks.py:1156, 1240, 1249` уже передаёт `warnings` в
task-результат), flash в UI (`weblate/trans/views/edit.py:1781, 1825, 2036-2037`).

**Контракт:** метод остаётся идемпотентным против терминальных статусов;
агрегация только читает `LLMUsageLog` внутри финализации. Потокобезопасность:
usage-строки пишутся в worker-потоках batch-слоя, но финализация вызывается
только после завершения всех futures (`_fetch_machinery_batches` ждёт batch-цикл),
поэтому отдельных межпоточных примитивов не нужно.
**Тесты** (`weblate/trans/tests/test_autotranslate.py`):

- unit: создать ран, повесить `LLMUsageLog(run=run, operation=translation,
  outcome=refused, refusal_reason=...)`, вызвать `_finish_producer_run` —
  статус `PARTIAL`, warnings содержат причину;
- regression через perform: engine пишет refused-запись и поднимает
  `MachineTranslationError` — ран завершается `PARTIAL`, не `COMPLETED`
  (сценарий ko `147b6c33`);
- refused + applied → `COMPLETED` с warning;
- `FAILED` до агрегации — статус не меняется.

**Проверка:** `./rundev.sh test weblate/trans/tests/test_autotranslate.py`

## Задача 2. Warnings и ссылка на ран в REST autotranslate

**Файл:** `weblate/api/views.py` (`TranslationViewSet.autotranslate`, синхронная
ветвь ~3812-3826; декоратор `@extend_schema` ~3673-3684).

Сейчас синхронный ответ — только `{"details": message}` (~3823-3826). Сделать аддитивно:

```json
{"details": message, "warnings": [...], "report_url": "..."}
```

- `warnings` — из `auto.get_warnings()`;
- `report_url` — `reverse("judge-run", kwargs={"pk": run.id})`, только когда
  `active_producer_run` есть (прецедент: `weblate/trans/tasks.py:192`,
  202-ветка REST-view на 3812-3826);
  без рана ключ не включать.

Обновить `docs/specs/openapi.yaml` через `make -C docs update-openapi`
(target определён в `docs/Makefile:42-43`, использует
`manage.py spectacular --validate --fail-on-warn`; линтится
`npx @redocly/cli lint` в CI). Заглушка ответа 200 `Translation`
уже не соответствует коду и при регенерации должна быть исправлена
отдельным аккуратным изменением схемы ответа в `@extend_schema`
декораторе (`api/views.py:3673-3684`), не правкой YAML руками.
Добавить entry в `docs/changes.rst`.

**Тесты** (`weblate/api/tests.py`, класс `test_autotranslate`, ~11456+):

- ответ содержит ключ `warnings` (список), код 200 не меняется;
- sync-путь (mt, где создаётся ран) содержит `report_url` и `warnings`;
- существующая проверка текста `"Automatic translation completed"` остаётся;
- 202-ветка (judge) не получает изменений — там уже есть `run_id`/`report_url`.

**Проверка:** `./rundev.sh test weblate/api/tests.py -k autotranslate`

## Отложено: ретрай валидационных отказов

Отдельное решение после задач 1-2. Возможный объём: один повторный запрос для
одноэлементного batch при validation refusal (`weblate/machinery/llm.py:3067-3088`
уже возвращает `None` из `_split_sources` для одной строки).

Риски, которые надо явно принять: повторная оплата стабильного отказа
(причина отказа — malformed source, повтор не поможет), усложнение семантики
usage-лога (дополнительные refused-записи от ретрая).

## Общая проверка

- `./rundev.sh test weblate/trans/tests/test_autotranslate.py`
- `./rundev.sh test weblate/api/tests.py -k autotranslate`
- `uv run prek run --all-files`
- `npx @redocly/cli lint docs/specs/openapi.yaml`

## Критерии приёмки

1. Отказ структурированного ответа на одну строку (сценарий ko) даёт ран
   `PARTIAL` с warning вместо чистого `COMPLETED`.
2. Warning содержит `Mismatching assistant reply items` / соответствующую
   `refusal_reason`.
3. REST-ответ autotranslate отдаёт `warnings` (и `report_url` при наличии рана).
4. `LLMUsageLog` и ProducerRun согласованы: refused-запись больше не может
   сосуществовать с чистым `COMPLETED` без warning.
5. Существующие тесты проходят; новых миграций нет.
