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

Форма не покрывает исторические JSON-конфигурации add-on'а и прямые
вызовы задачи, а `mode=judge` с `auto_source=others` остаётся
разрешённым. Новые конфигурации через UI эта форма после задачи 1 уже
не сохранит: `AutoAddonForm` наследует `AutoForm`
(`weblate/addons/forms.py:1296-1320`). Поэтому предупреждение нужно для
устаревшей или программно созданной записи и прямого запуска task.

Место, где пустой набор движков становится наблюдаемым фактом, —
`fetch_mt`: там известен и запрошенный список, и результат пересечения
с настройками проекта. Предупреждение выводится только если в scope
есть хотя бы один юнит: при пустом scope отсутствие движка ничего не
объясняет. Для непустого scope различаются два случая: ничего не
выбрано и выбранное не настроено в этом проекте — второй указывает на
ошибочный идентификатор. Предупреждения попадают в сообщение задачи и в
`ProducerRun.warnings` существующим механизмом (`add_warning`,
`get_warnings`).

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

Ограничение `JUDGE_MAX_UNITS_PER_RUN` сохраняет нынешнюю семантику:
оно резервирует упорядоченный scope **до** pretranslation. Непереведённая
после фазы 1 строка остаётся частью выбранного scope, расходует эту
резервацию и не заменяется следующей строкой. Это сохраняет верхнюю
границу MT-работы и не позволяет обходить cap последовательным
pretranslation. `JudgeScopePreview.processed`, лимит и batch
`judge_remaining` поэтому остаются числом выбранных строк, а не числом
фактических judge-вызовов.

UI больше не называет `processed` строками, которые «will be
judge-evaluated»: это строки, выбранные для pretranslation и возможного
judge. Стоимость, вычисленная до phase 1, явно маркируется как верхняя
оценка выбранного scope. После запуска `JudgeSummary.evaluated` сообщает
фактически судившиеся строки, а `JudgeSummary.untranslated` — выбранные,
но не дошедшие до judge. Так preview, cap и финальный отчёт не
противоречат друг другу.

Отдельного защитного условия в projection не добавляется: рассинхрон
«перевод исчез между вердиктом и projection» уже ловит существующая
проверка снапшота `(locked.target, locked.state) != final_snapshots[...]`
→ `stale_conflicts` (`weblate/trans/autotranslate.py:896-902`).

Строки-пропуски получают собственную причину `SkipReason.UNTRANSLATED`,
а не переиспользуют `CAP` или `PERMISSION`. `skip_reason` хранится, но
сейчас не рендерится; поэтому `weblate/trans/views/judge.py` добавит
локализованное пояснение именно для `UNTRANSLATED` в уже существующую
колонку :guilabel:`Problem`: «This string had no translation to judge.».
Фильтр :guilabel:`Skipped` и шаблон `producer-run.html` уже выводят
строку и `row.problem`, поэтому шаблон не меняется.

## Карта файлов

|Файл|Ответственность в этой работе|
|---|---|
|`weblate/trans/forms.py`|`AutoForm.clean` — отказ при `auto_source=mt` без движков|
|`weblate/trans/autotranslate.py`|`fetch_mt` — предупреждения о пустом наборе движков; `process_judge` — фильтр непереведённых строк и раннее завершение без judge-вызова; `JudgeSummary`, `format_judge_summary`, `_summarize_verdicts`, перенос `_record_skipped_judge_units` в `BaseAutoTranslate`|
|`weblate/trans/models/judge.py`|`JudgeRunUnit.SkipReason.UNTRANSLATED`|
|`weblate/trans/views/judge.py`|локализованная причина `UNTRANSLATED` в `row.problem`|
|`weblate/static/loader-bootstrap.js`|честная формулировка selected scope и верхней оценки judge cost|
|`weblate/trans/migrations/0128_judge_run_unit_untranslated_skip.py`|новая миграция выбора значений `skip_reason`|
|`weblate/trans/tests/test_autotranslate.py`|новые тесты формы и предупреждений|
|`weblate/trans/tests/test_judge_autotranslate.py`|новые тесты фильтра, cap reservation и обновление тестов, чьи фальшивые verdicts требуют перевода|
|`weblate/trans/tests/test_judge_form.py`|обновление двух конструкций формы|
|`weblate/trans/tests/test_judge_views.py`|обновление POST и GET preview без `engines`; проверка причины в report и copy preview|
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
- [ ] Обновить существующие тесты, которые действительно проходят
      валидацию формы с `auto_source=mt` и пустым `engines`. В
      `weblate/trans/tests/test_judge_form.py` обе конструкции формы
      получают `["weblate"]`; в GET preview-тестах
      `weblate/trans/tests/test_judge_views.py` также передавать
      `["weblate"]`, потому что preview не запускает MT. В POST-тесте
      без eager execution допустим тот же список. В
      `test_project_scope_task_runs_in_eager_mode` **не** включать
      реальный `weblate`: оставить `engines=[]`, переключить
      `auto_source` на `others` или замокать `AutoTranslate.process_mt`,
      чтобы тестировал запуск judge-task, а не реальный machinery.
      `test_judge_mode_requires_review_permission_at_the_view` не
      менять: permission проверяется раньше `AutoForm.is_valid()`.
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

**Outcome:** запуск с непустым scope, который во время выполнения не
получил ни одного движка, возвращает предупреждение с причиной, и оно
видно в сообщении задачи и в отчёте прогона. Различаются «движок не
выбран» и «выбранные движки не настроены в этом проекте». Пустой scope
такого предупреждения не получает. Поведение остальных прогонов и число
записанных строк не меняется.

**Files and interfaces:** `AutoTranslate.fetch_mt` в
`weblate/trans/autotranslate.py` (построение `engines`, строки
621-679). Предупреждения добавляются существующим `self.add_warning`,
попадают в `get_warnings()` и далее в `ProducerRun.warnings`
(`_finish_producer_run`, строка 1437). Текст — через `gettext`, в
стиле соседнего предупреждения о rate limit (строки 670-676).

**Actions:**

- [ ] После построения отсортированного списка движков в `fetch_mt`
      добавить предупреждение только при `num_units > 0` и пустом
      результате: при пустом запрошенном списке — «No machine
      translation engine was selected, so no strings were machine
      translated.»; при непустом запрошенном списке — сообщение,
      называющее запрошенные идентификаторы и говорящее, что ни один не
      настроен для этого проекта.
- [ ] Убедиться, что в judge-режиме предупреждение попадает в прогон:
      фаза 1 вызывает `process_mt` → `fetch_mt` внутри
      `process_judge`, а сообщение judge-режима формируется из сводки,
      поэтому предупреждение остаётся предупреждением, не подменяет
      сообщение и сохраняется в `ProducerRun.warnings`.
- [ ] Добавить тесты: в `weblate/trans/tests/test_autotranslate.py`
      непустой прогон `auto_source=mt`, `engines=[]` через
      `AutoTranslate.perform` даёт ровно одно предупреждение и
      `updated == 0`; неизвестный движок после пересечения настроек
      даёт отдельное предупреждение; пустой scope с `engines=[]` не
      предупреждает. В `weblate/trans/tests/test_judge_autotranslate.py`
      judge-прогон с `engines=[]` сохраняет предупреждение в
      `ProducerRun.warnings`.
- [ ] Добавить запись в верхнюю неизданную секцию `docs/changes.rst`,
      рубрика `.. rubric:: Improvements`: автоматический перевод больше
      не принимает машинный перевод без выбранного движка, а прогон, у
      которого движков не осталось, сообщает причину.

**Verification:** `./rundev.sh test weblate/trans/tests/test_autotranslate.py
weblate/trans/tests/test_judge_autotranslate.py` — зелено. Ручная
проверка исторической конфигурации: создать `AutoTranslateAddon` в
тестовом коде через прямую модельную конфигурацию с `auto_source=mt` и
`engines=[]` (не через `AutoAddonForm`), запустить `component_update` и
убедиться, что в `AddonActivityLog.details["result"]["warnings"]` есть
предупреждение.

### 3. Строка без перевода не судится и не пишется как «переведённая автоматически»

**Depends:** задача 2 (тот же модуль; правки не пересекаются по
методам, но выполняются после неё).

**Outcome:** строка, у которой после фазы 1 все формы перевода пусты,
не отправляется судье: у неё не появляется новый вердикт, в её истории
не появляется change `AUTO` с пустым переводом, и она не попадает в
новую очередь производителя как `judge:reject`. Она остаётся в
предварительно выбранном scope и расходует один reserved cap slot, но
не вызывает LLM. Прогон сообщает число таких строк в сводке и
предупреждении; в :guilabel:`Skipped` отчёта они видны с объяснением
«This string had no translation to judge.». Строка, которую фаза 1
успешно перевела, судится как сейчас; уже переведённые строки в объёме
судьи не затронуты.

**Files and interfaces:**

- `AutoTranslate.process_judge` в `weblate/trans/autotranslate.py`:
  после обновления списка юнитов по итогам фазы 1 (строки 816-822) и до
  `run_judge_batch` (строка 847) — разделение на судимые и
  непереведённые по `any(unit.get_target_plurals())`. Число выбранных
  `preview.processed` и `judge_units_processed` не пересчитывается:
  это reservation cap, а не число judge-вызовов.
- `JudgeSummary` (строки 97-127): новое поле `untranslated: int = 0`,
  учтённое в `__add__`; `format_judge_summary` (строки 130-154):
  отдельная фраза, добавляемая только при непустом значении, по образцу
  `repaired` и `cap_remainder`; `_summarize_verdicts` получает это
  число параметром. Поле автоматически попадает в
  `ProducerRun.summary` через `asdict` (строка 1434).
- `JudgeRunUnit.SkipReason` в `weblate/trans/models/judge.py`
  (строки 589-591): значение `UNTRANSLATED = "untranslated"`.
- `_record_skipped_judge_units` (строки 1389-1418) переносится из
  `BatchAutoTranslate` в `BaseAutoTranslate` без изменения тела и
  сигнатуры, чтобы вызываться из `AutoTranslate.process_judge`; три
  существующих batch-вызова остаются рабочими через наследование.
- `weblate/trans/views/judge.py:_annotate_row`: для
  `SKIPPED` + `UNTRANSLATED` ставит локализованный `row.problem`;
  остальные skip reasons и отображение existing rows не меняются.
- `weblate/static/loader-bootstrap.js`: текст preview называет
  `processed` выбранными для pretranslation и возможного judge, а
  `Estimated judge cost` — верхней оценкой выбранного scope. Ответ
  preview API, его поля и арифметика цены не меняются.
- Миграция `weblate/trans/migrations/0128_judge_run_unit_untranslated_skip.py`:
  `AlterField` для `skip_reason` (изменение `choices`, на уровне БД
  no-op), зависимость — `0127_loc_kit_dispatch_ledger`.

**Actions:**

- [ ] Добавить значение `UNTRANSLATED` в `JudgeRunUnit.SkipReason` и
      сгенерировать миграцию:
      `docker exec -w /app/src dev-docker-weblate-1 python manage.py
      makemigrations trans -n judge_run_unit_untranslated_skip`.
- [ ] Перенести `_record_skipped_judge_units` в `BaseAutoTranslate`.
- [ ] В `process_judge` после refresh разделить выбранные строки на
      судимые и непереведённые, сузить `writable_ids` и `input_targets`
      до судимых, а непереведённые сразу записать как `SKIPPED /
      UNTRANSLATED`. Если судимых нет, не вызывать `run_judge_batch`,
      завершить judge progress, записать сводку и предупреждение, затем
      пройти обычное завершение `ProducerRun`; не делать ранний return,
      который пропустит сохранение run result.
- [ ] Сохранить reservation cap: не менять `preview.processed`,
      `judge_units_processed`, batch `judge_remaining` и порядок scope;
      не backfill'ить строки за пределом cap. Добавить
      `JudgeSummary.untranslated`, текст сводки и `ngettext`-warning
      «%d strings had no translation to judge.».
- [ ] В `_annotate_row` отрисовать только новую причину
      `UNTRANSLATED` как «This string had no translation to judge.»;
      существующие `CAP` и `PERMISSION` сохраняют generic fallback.
      Изменить preview copy в `loader-bootstrap.js`, не JSON contract.
- [ ] Обновить только тесты, чьи фальшивые verdicts должны пройти judge,
      дав их юнитам перевод до запуска; не подменять target в тестах
      пустого или нулевого cap scope. В частности, поправить
      verdict-summary, severity, cache, project-run, cap-row и worker
      recheck fixtures из текущего списка, но сохранить
      `test_a_zero_cap_processes_no_strings` как тест именно нулевого
      cap.
- [ ] Добавить тесты: (1) на непереведённой строке
      `run_judge_batch` не вызывается, нового `JudgeVerdict` и change
      `AUTO` нет, состояние остаётся `STATE_EMPTY`, summary/warning
      называют строку; (2) `ProducerRun` создаёт единственную строку
      `SKIPPED / UNTRANSLATED`, а report показывает точную причину;
      (3) непереведённая selected строка расходует cap и не backfill'ит
      следующую; (4) строка, переведённая phase 1 (стиль
      `test_judge_refreshes_units_after_pretranslation`), попадает в
      batch; (5) UI preview использует selected/upper-bound copy, а не
      «will be judge-evaluated».
- [ ] Дополнить раздел `llm-judge` в `docs/admin/checks.rst`: строка
      без перевода не отправляется судье, попадает в отчёт как
      пропущенная с этой причиной и расходует заранее выбранный cap; и
      добавить вторую запись в верхнюю неизданную секцию
      `docs/changes.rst` (рубрика `Improvements`).

**Verification:** воспроизведение дефекта до правки и его отсутствие
после. Сценарий нового теста (1) на текущем коде обязан падать именно
на утверждении об отсутствии change `AUTO`: сейчас projection пишет
пустой target, `Unit.translate` понижает состояние до `STATE_EMPTY`, и
change `AUTO` создаётся — это ровно то, что наблюдалось на живом
инстансе. Тест (3) фиксирует выбранную cap-семантику, а не
неопределённый backfill. Команды:
`./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py
weblate/trans/tests/test_judge_views.py` и `./rundev.sh check`.
Миграция проверяется без живого инстанса через
`python manage.py makemigrations --check`, а поведение выбора — тестом
`JudgeRunUnit.SkipReason.UNTRANSLATED`; отдельный `migrate --noinput`
на пустой тестовой БД допустим только как smoke check.

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
