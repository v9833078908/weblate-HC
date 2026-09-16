<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Машинный перевод без движка и судья на непереведённой строке

Дата: 2026-09-16.
Статус: черновик, ожидает согласования. Реализация не начата. Деплой и
применение миграции на живом инстансе этим документом не
авторизуются и требуют отдельного согласования (AGENTS.md, «Never deploy
without explicit approval»).

## Цель и основания

Закрыть два дефекта, из-за которых запуск автоматического перевода может
не перевести ни одной строки, не сообщить об этом и при этом записать в
историю строки заведомо ложное «Переведено автоматически» с пустым
переводом, а судью — заставить выносить `critical` на строку, которую
никто не переводил.

Основание — разбор живого кейса на `l10n.herocraft.com`, проект
`pirate-ships`, компонент `localization-json` (только read-only GET к
API и сессионные GET-страницы, разрешение получено в сессии
2026-09-16):

- Юнит `title_restriction_everyday_offers` существует в 16 языках
  (id 543623–543638). `ru` — «Ежедневные предложения», state 20. Все 15
  целевых языков: `target` пуст, `state` = 0, падающих проверок нет.
- 2026-09-16 11:56:04 — 16 changes «Добавлена строка» (автор
  `AlEfremov`). 11:56:50–11:57:18 — 15 changes `action=6`
  («Переведено автоматически»), по одному на язык, автор `AlEfremov`,
  `target` пуст, `old` пуст, `state` 0 → 0.
- `q=context:title_restriction_everyday_offers AND judge:reject` → 15;
  `judge:pass`, `judge:minor`, `judge:flag`, `judge:stale` → 0.
- Дословный вердикт со страницы перевода:
  `omission/critical: The target is empty, meaning the entire source text
  'Ежедневные предложения' (Daily offers) was dropped.` Оба судейских
  места — независимо, с тем же выводом.
- Та же подпись в тот же день у другого актора и проекта: 99 changes
  `action=6` с пустым `target` в `victory-banner/general/pt_BR`
  (13:22, автор `ag@herocraft.com`). Дефект не про эту строку и не про
  этот проект.
- За последние 200 changes инстанса ни один change не имеет автора
  `mt:openrouter`; 15 сентября в том же компоненте такие changes есть.
  Конфигурация MT при этом рабочая: проектный OpenRouter имеет
  `routing` с wildcard `*`, ключ наследуется от site-wide настройки.

Механизм, прочитанный по коду:

- Автор change'а разделяет два пути записи. Реальный результат MT
  пишется через `store_results()`, который берёт автора из
  `origin.user` (`weblate/trans/autotranslate.py:718-732`), а
  `batch_translate()` ставит `origin[plural] = self`
  (`weblate/machinery/base.py:1381-1390`) — автором был бы бот
  `mt:openrouter`. Наблюдаемый автор-человек оставляет единственный
  путь: projection судьи
  `self.update(locked, state, locked.get_target_plurals())`
  (`weblate/trans/autotranslate.py:905-920`) с `user=None`, то есть
  `self.user`.
- `Unit.translate` при полностью пустом target принудительно ставит
  `STATE_EMPTY` (`weblate/trans/models/unit.py:2495-2501`), поэтому
  projection на непереведённой строке всегда пишет пустой target и
  `state` 0, оставляя в истории change `AUTO`.
- Фаза 1 judge-режима ничего не записала: `fetch_mt` пересекает
  `machinery_settings` проекта с переданным списком движков
  (`weblate/trans/autotranslate.py:633-641`), и при пустом пересечении
  не отправляется ни один запрос.
- `AutoForm.engines` объявлено `required=False`
  (`weblate/trans/forms.py:1275-1277`), а `initial` на bound-данные не
  применяется. Поэтому POST с `auto_source=mt` и без `engines`
  валиден, а результат — ноль запросов к MT.

Последнее подтверждено прогоном в dev-контейнере (одноразовый тест,
удалён после проверки): `AutoForm` с `{"mode": "translate", "q":
"state:empty", "auto_source": "mt", "threshold": "80"}` валиден и даёт
`cleaned_data["engines"] == []`; `AutoTranslate.process_mt([], 80)`
завершается с `auto.updated == 0` (2 passed).

Чего в основаниях нет: самого payload'а того POST'а. Скрипт
`push_and_write.py` находится вне этого репозитория, а `LLMUsageLog`
(`outcome`, `refusal_reason`, `reply_excerpt`, миграция
`0124_llm_usage_refusal_evidence`) читается только management-командой
на живом инстансе, что отдельно не согласовано. Поэтому план не
опирается на конкретную форму запроса: задача 1 закрывает вариант
«`auto_source=mt` без `engines`», задачи 2 и 3 делают исход честным при
любом варианте, включая `mode=judge` с `auto_source=others` и уже
сохранённые конфигурации add-on'а.

## Объём и не-цели

В объёме:

- Отказ формы автоматического перевода принимать `auto_source=mt` без
  выбранного движка — во всех потребителях `AutoForm` (UI POST, API
  `autotranslate`, GET preview).
- Явное предупреждение прогона, когда набор движков схлопнулся в пустой
  уже во время выполнения (сохранённая конфигурация add-on'а, прямой
  вызов задачи, `mode=judge` с `auto_source=others`).
- Судья никогда не получает строку с пустым переводом; такие строки
  учитываются в сводке и в отчёте прогона, а projection на них не
  выполняется, поэтому в истории строки не появляется change `AUTO` с
  пустым переводом.
- Документация и changelog для трёх пунктов выше.

Не-цели:

- Не менять атрибуцию change'ей MT (`mt:<service>`), фильтр
  `any(unit.machinery["quality"])` в `store_batch`, пороги и
  расписание судьи.
- Не чистить уже созданные на живом инстансе 15 вердиктов `reject` и
  changes `AUTO` с пустым переводом в `pirate-ships`, а также 99 таких
  же строк в `victory-banner`. Это мутация продакшн-данных, отдельная
  задача с отдельным согласованием.
- Не читать `LLMUsageLog` на живом инстансе и не восстанавливать
  payload прошедшего запуска.
- Не править внешний скилл `weblate-push-translate`: он вне этого
  репозитория.
- Не вводить новую сущность «прогон без MT»: пустой набор движков
  остаётся допустимым состоянием для `auto_source=others`.

## Выбранный подход

### Отказ на входе там, где заявлен машинный перевод

`auto_source=mt` — это утверждение «переводи движками». Пустой список
движков делает это утверждение невыполнимым, и единственный честный
ответ — ошибка валидации на поле `engines`. Это покрывает API (400 с
ошибкой поля), UI POST (ошибка формы) и GET preview: preview-эндпоинт
уже возвращает 400 на невалидной форме
(`weblate/trans/views/edit.py:1633-1635`), а фронтенд на 400 показывает
«Automatic translation preview input is invalid.» и блокирует кнопку
:guilabel:`Apply` (`weblate/static/loader-bootstrap.js:1490-1499`), и
пересчитывает preview при изменении `engines`
(`weblate/static/loader-bootstrap.js:1507-1509`). Правок JS не нужно.

Требование намеренно привязано к `auto_source`, а не к `mode`. В
judge-режиме `auto_source` может быть `others`, а проект — вообще не
иметь настроенного MT-сервиса; тогда выбор движка невозможен, и
требование на уровне `mode` заблокировало бы легитимный прогон «судить
уже переведённое». Такой прогон вместо отказа получает предупреждение
из задачи 2.

### Предупреждение вместо тишины на уровне выполнения

Форма не покрывает уже сохранённые конфигурации add-on'а и прямые
вызовы задачи, а `mode=judge` с `auto_source=others` остаётся
разрешённым. Поэтому место, где пустой набор движков становится
наблюдаемым фактом, — `fetch_mt`: там уже известен и запрошенный
список, и результат пересечения с настройками проекта. Различаются два
случая: ничего не выбрано и выбранное не настроено в этом проекте —
второй указывает на опечатку в идентификаторе движка, и слить их в одно
сообщение значило бы потерять диагностику. Предупреждения попадают в
сообщение задачи и в `ProducerRun.warnings` существующим механизмом
(`add_warning`, `get_warnings`).

### Судья не судит пустое

Вердикт на пустой строке предопределён (`omission/critical`), стоит
денег и попадает в очередь производителя как ложный дефект, а
projection этого вердикта записывает в историю change `AUTO` с пустым
переводом. Значит фильтровать надо до запроса, а не гасить симптом в
projection: после обновления юнитов по итогам фазы 1
(`weblate/trans/autotranslate.py:816-822`) строки без перевода
исключаются из батча, считаются в сводке и записываются в отчёт
прогона как `SKIPPED` с новой причиной. Правило одно и не зависит от
`writable_ids`: строка без перевода не судится ни в обычном прогоне, ни
в одностроковой перепроверке с `judge_pretranslate=False`.

Отдельного защитного условия в projection не добавляется: рассинхрон
«перевод исчез между вердиктом и projection» уже ловит существующая
проверка снапшота `(locked.target, locked.state) != final_snapshots[...]`
→ `stale_conflicts` (`weblate/trans/autotranslate.py:896-902`).

Строки-пропуски получают собственную причину `SkipReason.UNTRANSLATED`,
а не переиспользуют `CAP` или `PERMISSION`. Отчёт прогона уже умеет их
показывать: фильтр `skipped` и построчное пояснение «This string was
skipped before judging.» существуют
(`weblate/trans/views/judge.py:111,169-174,249`), само поле
`skip_reason` нигде не рендерится, поэтому шаблоны не меняются.

## Карта файлов

|Файл|Ответственность в этой работе|
|---|---|
|`weblate/trans/forms.py`|`AutoForm.clean` — отказ при `auto_source=mt` без движков|
|`weblate/trans/autotranslate.py`|`fetch_mt` — предупреждения о пустом наборе движков; `process_judge` — фильтр непереведённых строк; `JudgeSummary`, `format_judge_summary`, `_summarize_verdicts`, перенос `_record_skipped_judge_units` в `BaseAutoTranslate`|
|`weblate/trans/models/judge.py`|`JudgeRunUnit.SkipReason.UNTRANSLATED`|
|`weblate/trans/migrations/0128_judge_run_unit_untranslated_skip.py`|новая миграция выбора значений `skip_reason`|
|`weblate/trans/tests/test_autotranslate.py`|новые тесты формы и предупреждений|
|`weblate/trans/tests/test_judge_autotranslate.py`|новые тесты фильтра; обновление тестов, чьи юниты не имеют перевода|
|`weblate/trans/tests/test_judge_form.py`|обновление двух конструкций формы|
|`weblate/trans/tests/test_judge_views.py`|обновление POST и GET preview без `engines`|
|`weblate/api/tests.py`|новый тест 400 на `auto_source=mt` без `engines`|
|`docs/api.rst`|описание обязательности `engines` для `auto_source=mt`|
|`docs/user/translating.rst`|раздел `auto-translation`: выбор движка обязателен|
|`docs/admin/checks.rst`|раздел `llm-judge`: строка без перевода не судится|
|`docs/changes.rst`|две записи в верхней неизданной секции|

## Задачи

### 1. Форма отказывает в машинном переводе без движка

**Outcome:** POST автоматического перевода с `auto_source=mt` и пустым
`engines` завершается ошибкой валидации на поле `engines`, а не
успешным прогоном, который ничего не перевёл. API отвечает 400 с этой
ошибкой, UI показывает ошибку поля, GET preview отвечает 400 и
блокирует :guilabel:`Apply`. `auto_source=others` с пустым `engines`
остаётся валидным, как сейчас.

**Files and interfaces:** `AutoForm.clean` в `weblate/trans/forms.py`
(рядом с существующей проверкой `overwrite_existing`, строки
1420-1430). Сообщение — переводимое, через `gettext`, по стилю
соседнего: «Select at least one machine translation engine.» Ошибка
добавляется через `self.add_error("engines", ...)`, чтобы API отдал её
в словаре ошибок полей (`weblate/api/views.py:3683-3691`).

**Actions:**

- [ ] Дополнить `AutoForm.clean` проверкой: `auto_source == "mt"` и
      пустой `cleaned_data.get("engines")` → ошибка поля `engines`.
      Учесть, что `clean` вызывается и когда `auto_source` не прошёл
      собственную валидацию: брать значения через `get`.
- [ ] Обновить существующие тесты, которые сейчас опираются на
      допустимость такого запроса: `weblate/trans/tests/test_judge_form.py`
      (две конструкции формы с `"auto_source": "mt", "engines": []`,
      проверяющие правило `overwrite_existing`) и
      `weblate/trans/tests/test_judge_views.py` (POST на
      `auto_translation` и GET на `auto_translation_preview` с пустым
      или отсутствующим `engines`). В обоих файлах передавать
      `["weblate"]`: этот движок присутствует в вариантах формы в
      тестовом окружении, что уже зафиксировано тестом
      `test_form_uses_list_initial_for_default_engine`.
- [ ] Добавить тест формы в `weblate/trans/tests/test_autotranslate.py`:
      `auto_source=mt` без `engines` невалидна и ошибка стоит на поле
      `engines`; `auto_source=others` без `engines` валидна.
- [ ] Добавить тест API в `weblate/api/tests.py` рядом с существующим
      успешным `autotranslate`: тот же запрос без `engines` даёт 400 и
      ключ `engines` в теле ответа.
- [ ] Дополнить `docs/api.rst` (описание параметра `engines` в блоке
      `autotranslate`, строка 2480) и раздел `auto-translation` в
      `docs/user/translating.rst`: при переводе движками нужно выбрать
      хотя бы один движок.

**Verification:** в контейнере
`./rundev.sh test weblate/trans/tests/test_autotranslate.py
weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py`
— зелено, включая новые тесты. Отдельно
`./rundev.sh test weblate/api/tests.py -k autotranslate`. Ручная
проверка UI на dev-инстансе (`http://localhost:3001`): на странице
:guilabel:`Operations` → :guilabel:`Automatic translation` снять
отметку со всех движков при выбранном :guilabel:`Machine translation` —
строка preview показывает «Automatic translation preview input is
invalid.», кнопка :guilabel:`Apply` неактивна; вернуть движок — preview
считается снова.

### 2. Прогон с пустым набором движков говорит об этом

**Depends:** задача 1 (тот же файл `weblate/trans/autotranslate.py`
правится задачей 3 — выполнять последовательно, а не параллельно, чтобы
не разводить конфликты в одном модуле).

**Outcome:** запуск, который во время выполнения не получил ни одного
движка, возвращает предупреждение с причиной, и это предупреждение
видно в сообщении задачи и в отчёте прогона. Различаются «движок не
выбран» и «выбранные движки не настроены в этом проекте». Поведение
остальных прогонов и число записанных строк не меняется.

**Files and interfaces:** `AutoTranslate.fetch_mt` в
`weblate/trans/autotranslate.py` (построение `engines`, строки
631-641). Предупреждения добавляются существующим `self.add_warning`,
попадают в `get_warnings()` и далее в `ProducerRun.warnings`
(`_finish_producer_run`, строка 1438). Текст — через `gettext`, в
стиле соседнего предупреждения о rate limit (строки 670-676).

**Actions:**

- [ ] После построения отсортированного списка движков в `fetch_mt`
      добавить: при пустом результате и пустом запрошенном списке —
      предупреждение «No machine translation engine was selected, so no
      strings were machine translated.»; при пустом результате и
      непустом запрошенном списке — предупреждение, называющее
      запрошенные идентификаторы и сообщающее, что ни один из них не
      настроен для этого проекта.
- [ ] Убедиться, что в judge-режиме предупреждение попадает в прогон:
      фаза 1 вызывает `process_mt` → `fetch_mt` внутри
      `process_judge`, а сообщение judge-режима формируется из сводки,
      поэтому предупреждение должно остаться именно предупреждением, не
      подменяя сообщение.
- [ ] Добавить тесты: в `weblate/trans/tests/test_autotranslate.py` —
      прогон `auto_source=mt`, `engines=[]` через
      `AutoTranslate.perform` даёт ровно одно предупреждение о
      невыбранном движке и `updated == 0`; прогон с
      `engines=["does-not-exist-here"]`, отфильтрованным настройками
      проекта, даёт предупреждение о ненастроенном движке. В
      `weblate/trans/tests/test_judge_autotranslate.py` — judge-прогон
      с `engines=[]` содержит это предупреждение в `get_warnings()`, а
      после завершения — в `ProducerRun.warnings`.
- [ ] Добавить запись в верхнюю неизданную секцию `docs/changes.rst`,
      рубрика `.. rubric:: Improvements`: автоматический перевод больше
      не принимает машинный перевод без выбранного движка, а прогон, у
      которого движков не осталось, сообщает причину.

**Verification:** `./rundev.sh test weblate/trans/tests/test_autotranslate.py
weblate/trans/tests/test_judge_autotranslate.py` — зелено. Ручная
проверка на dev-инстансе: установить add-on «Automatic translation» с
`auto_source=mt` и без движков (конфигурация сохраняется напрямую,
минуя форму), запустить `component_update` и убедиться, что в
`AddonActivityLog.details["result"]["warnings"]` есть предупреждение, а
не пустой список.

### 3. Строка без перевода не судится и не пишется как «переведённая автоматически»

**Depends:** задача 2 (тот же модуль; правки не пересекаются по
методам, но выполняются после неё).

**Outcome:** строка, у которой после фазы 1 все формы перевода пусты,
не отправляется судье: у неё не появляется вердикт, в её истории не
появляется change `AUTO` с пустым переводом, и она не попадает в
очередь производителя как `judge:reject`. Прогон сообщает их число
отдельной фразой в сводке и предупреждением, а в отчёте прогона такие
строки видны в фильтре :guilabel:`Skipped` с причиной
`untranslated`. Строка, которую фаза 1 успешно перевела, судится как
сейчас; уже переведённые строки в объёме судьи не затронуты.

**Files and interfaces:**

- `AutoTranslate.process_judge` в `weblate/trans/autotranslate.py`:
  после обновления списка юнитов по итогам фазы 1 (строки 816-822) и до
  вызова `run_judge_batch` (строка 847) — разделение на судимые и
  пропущенные по признаку `any(unit.get_target_plurals())`.
- `JudgeSummary` (строки 97-127): новое поле `untranslated: int = 0`,
  учтённое в `__add__`; `format_judge_summary` (строки 130-154):
  отдельная фраза, добавляемая только при непустом значении, по образцу
  `repaired` и `cap_remainder`; `_summarize_verdicts` получает это
  число параметром (сводка по вердиктам его вычислить не может — у
  пропущенных строк вердикта нет). Поле автоматически попадает в
  `ProducerRun.summary` через `asdict` (строка 1434).
- `JudgeRunUnit.SkipReason` в `weblate/trans/models/judge.py`
  (строки 589-591): значение `UNTRANSLATED = "untranslated"`.
- `_record_skipped_judge_units` (строки 1389-1418) переносится из
  `BatchAutoTranslate` в `BaseAutoTranslate` без изменения тела и
  сигнатуры, чтобы вызываться и из `AutoTranslate.process_judge`;
  три существующих вызова (строки 1557, 1579, 1586) остаются рабочими
  через наследование. Запись выполняется только когда
  `self.producer_run` не `None`.
- Миграция `weblate/trans/migrations/0128_judge_run_unit_untranslated_skip.py`:
  `AlterField` для `skip_reason` (изменение `choices`, на уровне БД
  no-op), зависимость — `0127_loc_kit_dispatch_ledger`.

**Actions:**

- [ ] Добавить значение `UNTRANSLATED` в `JudgeRunUnit.SkipReason` и
      сгенерировать миграцию:
      `docker exec -w /app/src dev-docker-weblate-1 python manage.py
      makemigrations trans -n judge_run_unit_untranslated_skip`.
- [ ] Перенести `_record_skipped_judge_units` в `BaseAutoTranslate`.
- [ ] В `process_judge` разделить обновлённый список юнитов на
      судимые и непереведённые; в `run_judge_batch` передавать только
      судимые (и `writable_ids`, пересечённые с ними); при пустом
      списке судимых завершать прогон сводкой без запроса к судье.
- [ ] Записать пропущенные строки через `_record_skipped_judge_units`
      с причиной `UNTRANSLATED`, добавить их число в `JudgeSummary`,
      фразу в `format_judge_summary` и предупреждение вида «%d strings
      had no translation to judge.» (ngettext).
- [ ] Обновить тесты, которые сейчас судят юниты без перевода и
      проверяют бухгалтерию вердиктов, дав их юнитам перевод перед
      прогоном (`unit.translate(self.user, ["some target"],
      STATE_TRANSLATED)`), в `weblate/trans/tests/test_judge_autotranslate.py`:
      `test_judge_summary_reports_verdict_buckets`,
      `test_unparsed_is_counted_in_the_warnings`,
      `test_judge_summary_counts_severity_buckets`,
      `test_judge_summary_counts_unparsed_strings`,
      `test_judge_summary_counts_repaired_and_rejudged_strings`,
      `test_cache_only_run_still_summarizes_verdicts`,
      `test_judge_summary_reports_cap_remainder`,
      `test_batch_summary_aggregates_across_translations`,
      `test_project_launch_records_one_run_across_translations`,
      `test_capped_units_record_a_cap_skip`,
      `test_deleted_unit_and_verdict_leave_a_safe_dangling_row`,
      `test_a_zero_cap_processes_no_strings`, а также хелпер
      `run_batch_with_first_translation_failure` (его используют
      `test_batch_summary_survives_one_failing_translation` и
      `test_failed_translation_consumes_selected_cap`) и хелпер
      `_make_queued_recheck_run` (его используют три теста воркера
      перепроверки). Изменение переводов фикстур — единственная
      правка: утверждения этих тестов про счётчики, отчёты и cap
      остаются как есть.
- [ ] Добавить новые тесты в `weblate/trans/tests/test_judge_autotranslate.py`:
      (1) при judge-прогоне на непереведённой строке `run_judge_batch`
      не получает её вовсе, у строки нет `JudgeVerdict`, нет change с
      `action=ActionEvents.AUTO`, `state` остался `STATE_EMPTY`, а
      сводка и предупреждение называют её число; (2) прогон с
      `ProducerRun` создаёт для неё строку `JudgeRunUnit` с
      `outcome=SKIPPED` и `skip_reason=UNTRANSLATED`; (3) строка,
      переведённая фазой 1 (существующий стиль подмены `process_mt`,
      как в `test_judge_refreshes_units_after_pretranslation`),
      по-прежнему попадает в батч — фильтр применяется после фазы 1, а
      не до неё.
- [ ] Дополнить раздел `llm-judge` в `docs/admin/checks.rst`: строка
      без перевода не отправляется судье, а попадает в отчёт прогона
      как пропущенная; и добавить вторую запись в верхнюю неизданную
      секцию `docs/changes.rst` (рубрика `Improvements`).

**Verification:** воспроизведение дефекта до правки и его отсутствие
после. Сценарий нового теста (1) на текущем коде обязан падать именно
на утверждении об отсутствии change `AUTO`: сейчас projection пишет
пустой target, `Unit.translate` понижает состояние до `STATE_EMPTY`, и
change `AUTO` создаётся — это ровно то, что наблюдалось на живом
инстансе. Команды:
`./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py
weblate/trans/tests/test_judge_views.py` и `./rundev.sh check`.
Миграция проверяется отдельно на чистой БД
(`manage.py migrate --noinput` вне тест-раннера), без применения к
живому инстансу.

## Integrated verification and handoff

Один прогон после всех трёх задач, в контейнере:

    ./rundev.sh test \
      weblate/trans/tests/test_autotranslate.py \
      weblate/trans/tests/test_judge_autotranslate.py \
      weblate/trans/tests/test_judge_form.py \
      weblate/trans/tests/test_judge_views.py \
      weblate/api/tests.py \
      weblate/addons/tests.py
    ./rundev.sh check

Линт по изменённым файлам на хосте:

    uv run prek run --files <изменённые файлы>

Ожидаемый результат отчитывается как есть, включая предсуществующие
находки линтера вне изменённых диапазонов — они сверяются построчно с
diff и не объявляются устранёнными.

Отдельно, после согласования на деплой (этим документом не
авторизовано): применение `0128_judge_run_unit_untranslated_skip` на
живом инстансе.

Открытые следствия, которые этот план сознательно не закрывает и
которые требуют отдельного согласования:

- Гигиена уже созданных данных: 15 вердиктов `judge:reject` и 15
  changes `AUTO` с пустым переводом в `pirate-ships/localization-json`
  и 99 таких же строк в `victory-banner/general/pt_BR`. После правки
  новые не появляются, старые остаются в очереди производителя.
- Подтверждение формы прошедшего запроса по `LLMUsageLog` на живом
  инстансе (`llm_usage_report --project pirate-ships` за 11:50-12:00) —
  чтение продакшн-данных management-командой.
- Внешний скилл `weblate-push-translate` и его `push_and_write.py`:
  после задачи 1 его запрос с `auto_source=mt` без `engines` будет
  получать 400 вместо тихого нуля, и скрипт нужно поправить в его
  собственном репозитории.
