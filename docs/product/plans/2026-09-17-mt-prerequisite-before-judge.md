<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Обязательный MT до запуска судей и явный отказ при исчерпании лимита

Дата: 2026-09-17.
Статус: одобрен к реализации 2026-09-17; исполняется **вторым**, после
`docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md`.
Создание новой миграции и её прогон в disposable test DB входят в объём
(задача 2, C5). Отдельного разрешения требуют применение миграций на живом
экземпляре, деплой, обновление статики, перезапуск worker'ов и любые
платные проверки у провайдеров.
Правила работы: `AGENTS.md`.

**Порядок.** Задачи 1 → 2 → 3 → 4 строго последовательны, и параллельно с
первым планом этот исполнять нельзя: общие `autotranslate.py`
(`perform`/`_perform`, `fetch_mt`, финализация summary и warnings), точки
входа `tasks.py`, `models/judge.py`, отчёт и `ProducerRunSerializer`
требуют одного владельца за раз. Из первого плана сюда приходят готовыми:
guard «одна доставка — один исполнитель» (перезапись `_perform` обязана
его сохранить), агрегат покрытия с разбивкой по `skip_reason` и неизменная
семантика статусов. Остальные правила исполнения — в разделе «Порядок,
готовность и приёмка».

## Цель и согласованные требования

Нажатие «Запустить судей» означает последовательность:
**заполнить отсутствующие переводы во всём выбранном объёме → проверить
готовность всех выбранных языков → запустить судей**.

Основание — требования пользователя в текущем обсуждении:

- Судьи не начинают проверку, пока выбранные языки не переведены.
- Перед судьями автоматически выполняется MT, но только для отсутствующих
  переводов. Существующий текст, включая approved, сохраняется.
- Если у OpenRouter закончились средства или месячный лимит ключа,
  судьи не запускаются; продюсер видит warning с причиной.
- Простого пропуска пустых строк и проверки остальных недостаточно.
- Лимит судьи не должен оставлять поздние языки без обязательного MT.

План заменяет
`docs/product/plans/2026-09-16-03-mt-engine-selection-and-untranslated-judge-scope.md`:
оттуда сохраняются диагностика отсутствующего движка, запрет вердиктов на
пустоте и честный отчёт, но не продолжение судей на оставшемся подмножестве
и не ограничение всего MT лимитом судьи. Старый документ остаётся историей,
а не второй инструкцией к реализации.

## Доказательства и границы уверенности

### Подтверждено на проде в этой сессии

- [Запуск Victory Banner](https://l10n.herocraft.com/judge-runs/a1d09bd2-0030-450c-9aaf-dba9727e03d3/):
  `requested_mode=judge`, `requested_query=NOT has:judge`, cap 2000.
  В 157 строках отчёта `input_target` был пустым; 145 получили critical.
- [Строка «Стрельба»](https://l10n.herocraft.com/translate/victory-banner/general/zh_Hans/?checksum=2ebafddd934aab18#machinery),
  Unit 543398: пустой target, state 0, вердикт об отсутствии перевода.
- По отдельному разрешению пользователя удалены 145 результатов и 290
  соответствующих записей двух судей. Это **не** число HTTP-запросов.
  Резервная копия: `/app/data/recovery-victory-banner-20260917/` на проде.
- Отдельный MT по 157 пустым строкам не записал ни одного перевода:
  OpenRouter вернул `Key limit exceeded (monthly limit)`.
  [Запуск восстановления](https://l10n.herocraft.com/judge-runs/82f9b073-56c3-4eb4-8d79-8aa1ed90fbfc/)
  отмечен как failed вручную: штатный результат до этого говорил
  «Автоматический перевод завершён, строки не были обновлены», warnings=[]
  при наличии ошибок в логе.
- Существующие непустые переводы при восстановлении не изменились,
  новые судейские вердикты не создавались. Лимит ключа этим не исправлен.

Запись запуска не доказывает конкретный UI/POST, которым его инициировали.
Нельзя приписывать этому инциденту `judge_proposal_only=True` только потому,
что такой путь существует в текущем REST API. `written` также не доказывает
изменение текста: judge projection может менять только состояние.

### Прочитано в текущем checkout

- `weblate/trans/autotranslate.py`: `AutoTranslate.get_units` исключает
  read-only, но не пустоту; `preview_judge_scope` резервирует cap;
  `process_judge` делает локальный MT/refetch и передаёт список в
  `run_judge_batch`. `BatchAutoTranslate._perform` повторяет весь этот
  цикл **по одному языку**, что не даёт межъязыкового барьера.
- `weblate/trans/machinery.py`: `_fetch_machinery_batch` ловит
  `MachineTranslationError`, пишет лог и возвращает True; вызывающий
  код получает callback/progress и частичные результаты без признака ошибки.
- `weblate/machinery/llm.py`:
  `_download_multiple_translations_with_context_cache` делит обычные
  `MachineTranslationError` на половины. Отказ по бюджету нельзя лечить
  таким делением; сейчас специальное исключение есть для rate limit.
- `weblate/machinery/openai.py`: `BaseOpenAITranslation.check_failure`
  обрабатывает и upstream error внутри HTTP 200. Нельзя ограничиться
  проверкой HTTP 402 или объявить любой HTTP 403 исчерпанием денег.
- `weblate/trans/tasks.py`: `_producer_run_dispatch_kwargs` для
  `judge-project` задаёт `engines=[]`, pretranslate=False, proposal-only=True;
  `judge-recheck` намеренно остаётся одностроковым.
- `weblate/api/producer/views.py`: estimate/start сохраняют capped
  `scope_snapshot`; start повторно строит Batch без `scope.unit_ids`,
  хотя estimate их учитывает. Общий scope builder нужен обоим.
- `weblate/api/producer/serializers.py`: `ProducerRunSerializer` сейчас
  отдаёт failure/summary, но не warnings; одной записи предупреждения
  в БД недостаточно для REST-потребителя.
- `ProducerRun` уже хранит dispatch ledger, scope hash, scope snapshot,
  warnings, summary, resumed_from. Не создавать параллельную систему задач.

References на `BatchAutoTranslate` проверены через basedpyright: среди
потребителей tasks, legacy API, producer API, preview, component copy и тесты.
Перед изменением экспортируемых контрактов повторить LSP references на
актуальной ветке; generated/build-копии не редактировать.

## Объём и не-цели

В объёме: bulk judge через UI, REST и задачи; общий MT error path; безопасная
запись empty-only MT; preview и статус; redelivery/resume; документация.

Не-цели:

- Пополнять баланс, менять ключи/модели/провайдера, автоматически переключать
  OpenRouter на LiteLLM, менять judge provider failover.
- Повторять production cleanup или переписывать исторические отчёты.
- Переводить заново существующий текст, менять правила качества судей,
  добавлять новые языки или обходить права актёра.
- Превращать каждую одностроковую перепроверку, проверку кандидата или
  deferral drain в полный машинный перевод проекта.
- Создавать новый frontend или систему тарификации/телеметрии.
- Доводить до production весь `frontend-wizard`: его live adapter уже
  несовместим с существующим REST start (нет `estimate_id`, иная scope
  shape, ожидание отсутствующего list endpoint). Этот отдельный дефект
  не обходить ослаблением REST-валидации. В этой работе UI-доказательство —
  действующая Weblate-форма и отчёт; REST — реальный estimate/start/poll.

## Выбранный подход и общие контракты

### C1. Закрытый объём и два разных лимита работы

Один server-side scope builder используется estimate, start, task и resume.
Он фиксирует до платных запросов:

1. Выбранные component/target-language пары: взять пары из полного
   результата `get_units()` по исходному query внутри разрешённого объекта,
   **до** применения judge cap. Поэтому query с языковым ограничением не
   запускает MT соседних языков, а поздний выбранный язык за cap не исчезает.
   Source/read-only и недоступные актёру объекты исключаются как сейчас.
2. `preparation_unit_ids`: закрытый объём подготовки в этих парах, включая
   непустые строки для проверки готовности. Обычный query «не проверено»
   ограничивает очередь судей, а не скрывает от подготовки пустые строки
   с ранее записанными вердиктами. При явно переданных `unit_ids` это жёсткая
   граница: не расширять работу за их пределы до всего проекта/языка.
3. Упорядоченный judge scope по пользовательскому query внутри этого объёма,
   ограниченный `JUDGE_MAX_UNITS_PER_RUN`. Его не пересчитывать после MT
   через изменившиеся `state`/`has:judge` фильтры и не заполнять новыми строками
   вместо выбывших. Сохранять текущий порядок component/language/position/pk.

Preview показывает отдельно количество missing для MT **до judge cap**,
разбивку по языкам и число selected для судьи. Запуск через estimate принимает
тот же закрытый объём; изменение IDs, выбранных языков или MT-конфигурации
требует нового estimate (`estimate-drift`), а не молчаливого расширения расходов.
Не добавлять новый скрытый MT-cap: конечный snapshot и явно показанный объём
ограничивают работу. Если действующий лимит/политика MT мешает завершению,
операция останавливается до судей, а не обрезает поздние языки.

Новые строки/языки после фиксации объёма относятся к следующему запуску.
Удалённый/перемещённый из scope юнит либо потеря доступа до барьера — явный
scope-changed/permission blocker, не разрешение считать язык готовым.
При нулевом judge scope ничего не оплачивать, показать «Нет строк для проверки».

### C2. Что считается отсутствующим переводом

Для автоматической записи missing: `not any(unit.get_target_plurals())`;
не использовать `not unit.translated`, поскольку непустые state 10/11/12
подлежат проверке. Существующие непустые тексты/approved неизменны.

Готовность plural-строк проверять с учётом требуемых форм формата/языка,
не по одному `any()`. Частично заполненная plural-строка не готова, но
обычный MT не должен переписать её заполненные формы: остановка с причиной
«не заполнены формы перевода», ручное заполнение вне этой работы. Не вводить
эвристики «равно исходнику значит пусто» или нормализацию пробелов.

### C3. Глобальный барьер и отсутствие обхода

Bulk pipeline: `preparing → checking-readiness → judging → terminal`.
Сначала MT missing **во всех** выбранных языках, затем перечитывание БД.
Если ошибка обязательного MT либо остались missing/incomplete — `failed`,
судейские вызовы/новые verdicts/projection равны нулю. Успешно сохранённый
MT не откатывать. На подтверждённом отказе бюджета/ключа прекратить новые
MT-запросы, не проходить все языки с заведомо тем же отказом.

Один global barrier в `BatchAutoTranslate`, не локальная проверка после MT
каждого языка. Прямой `AutoTranslate.process_judge` обслуживает одноязыковый
случай через тот же контракт, а не альтернативный обход. Старые внутренние
флаги `judge_pretranslate=False`, `auto_source=others`, `engines=[]` не могут
отключить prerequisite у bulk judge. `overwrite_existing=True` для этого
pipeline отвергается до работы; отдельный обычный MT-режим не меняется.

После подготовки сама judge-фаза не перезаписывает исходно существующий
текст и не снимает approval. Существующие proposal/candidate операции
сохраняются; prerequisite MT не должен случайно включить mutating repairs
или разрешить overwrite для всего judge scope.

Перед каждым следующим judge HTTP-batch повторно проверять готовность
закрытого объёма и actual target для отправляемых строк. Если target исчез
после барьера — прекратить новые отправки, показать scope-changed warning;
уже отправленные запросы не отменить задним числом. Статус остановки —
`failed` с явной причиной scope-changed и сохранёнными результатами:
`partial` сохраняет действующую семантику отмены (`_finish_producer_run`
назначает его только из `CANCEL_REQUESTED`) и не переиспользуется для
остановки по ошибке, а неполнота выражается покрытием и причиной
остановки по контракту
`docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md`.
Не держать DB-lock на время HTTP.
Существующие snapshot/hash-проверки при сохранении результата остаются.

Одностроковая перепроверка/deferral/candidate проверяет свой собственный
actual target (для candidate — candidate target, не пустой live target).
Пустой вход запрещён без вызова судьи; соседние языки не переводятся.
Эти пути определяются durable purpose (`recheck`, `drain`, candidate subject),
а не клиентским флагом «обойти MT».

Если MT не требуется и выбранный объём уже готов, не делать платный probe
и не проверять баланс OpenRouter специально: отсутствие баланса независимо
не доказывает неготовность имеющихся переводов. Но уже полученный отказ
обязательного MT в текущем запуске нельзя игнорировать даже при частичном
успехе другой batch/службы. Повтор — отдельное осознанное действие.

### C4. Ошибка MT — данные результата, а не только запись лога

Предлагаемые новые контракты, не существующие API:

- `MachineTranslationServiceError(MachineTranslationError)` в
  `weblate/machinery/base.py`: закрытый `reason_code` для quota-exhausted,
  insufficient-credit, authentication, permission, provider-unavailable;
  безопасное сообщение без ключа, response body и URL управления ключом.
  HTTP-status сам по себе не всегда определяет reason.
- `MachineryBatchOutcome` в `weblate/trans/machinery.py`: success/failed/skipped,
  service, IDs batch, reason code. Ошибку передавать на caller thread через
  явный outcome/callback; возвращаемый словарь успешных переводов сохранять.
  Локальные ошибки парсинга/частичные результаты тоже не теряются из отчёта.
- Дополнительный failure callback у `fetch_machinery_matches` получает
  outcome; `AutoTranslate.fetch_mt` использует его для failure/warnings.
  Существующим другим потребителям не навязывать новый глобальный fail-fast.

Классифицировать HTTP 402 и подтверждённый monthly-key-limit в HTTP 403
или upstream error HTTP 200. Обычный 403 — отказ доступа, не «кончились деньги».
Generic error остаётся честным provider-error, если причины недостаточно.
429 после существующих ограниченных retries — retryable rate-limit, не баланс.
Quota/auth ошибки не дробить в LLM half-batch rescue и не retry'ить как
исправимый JSON. Уже запущенные parallel batches дождаться/сохранить безопасно,
не планировать новые после fatal signal; счётчики отражают фактически
сохранённые строки, не число callback-ов.

Если MT вернул ноль без исключения (порог, unsupported route, пустой ответ,
max-length оставил только suggestion), C2/C3 всё равно не дадут пройти барьер.
Отсутствующая служба/route определяется до HTTP. Для producer API выбирать
проектный движок через существующий `configured_routed_engine`, с текущим
field-by-field inheritance, без per-language fallback. Legacy UI сохраняет
явный выбор движков; отсутствие пригодного выбора при необходимых missing
является ошибкой, а не разрешением судить пустоту.

### C5. Безопасное сохранение и durable состояние

В empty-only режиме подготовки `store_results` под `select_for_update`
повторно проверяет missing и snapshot исходника/контекста; если человек
уже записал перевод или вход изменился, устаревший MT не применяется.
Пропуск не считается успешной записью. Это не глобальный запрет overwrite
для отдельного штатного MT-режима, где пользователь явно его запросил.
Для готового человеческого перевода readiness может пройти; изменённый
исходник требует нового разрешённого запроса либо нового запуска, не
применения старого ответа. HTTP выполняется вне транзакции записи.

Добавить в существующий `ProducerRun` отдельные данные preparation, не
перегружать `dispatch_phase` (он описывает публикацию задачи). Предлагаемые
поля: `preparation_snapshot` JSON (version, preparation IDs, selected translation
IDs, missing-at-start IDs, безопасный fingerprint MT-конфигурации) и
`preparation_phase` (pending/preparing/ready/blocked). Judge `scope_snapshot`
остаётся judge snapshot. Новый numbered migration генерировать от актуального
leaf: на 2026-09-17 это `0135_judge_run_unit_untranslated_skip`, то есть
следующий номер — `0136`; leaf перепроверить на момент исполнения, а номер
`0130` из старого плана давно занят.

Fingerprint не хранит ключи и не выдаёт их хэши наружу; смену конфигурации
учитывать через безопасную версию/серверный keyed fingerprint, не обычный
SHA от API key. Не записывать snapshot в `configuration_snapshot` в обход
существующего `safe_configuration_snapshot` allowlist.

Summary хранит отдельный `mt_preparation`: selected/missing_initial/written/
remaining, per-language remaining, phase, reason_code и число judge evaluations.
`_finish_producer_run` не должен стирать этот блок через новый `asdict`.
`warnings` и `failure` содержат безопасное объяснение. Для blocked judge rows:
SKIPPED с новой причиной `mt-prerequisite` (нет выдуманного critical);
CAP/PERMISSION не переиспользовать. Для single-unit empty — `untranslated`.
Все ранее reserved PENDING строки финализировать даже при нуле judge calls.

Crash/redelivery продолжает тот же закрытый scope, перечитывает missing и
не перезаписывает сохранённое. `ready` в БД не заменяет свежую проверку;
known provider refusal остаётся blocked, redelivery не повторяет расходы.
Явный resume создаёт новый linked run, переносит весь preparation scope,
повторяет MT только для оставшихся missing и сохраняет judge cap/no-backfill.
Отмена проверяется между batches, финализируется с сохранёнными MT
счётчиками; не означает разрешение начать judge. Терминальный status не
перезаписывается поздним callback-ом.

Legacy queued/running bulk judge без нового preparation snapshot нельзя
молча возобновить с расширенным MT-объёмом после выкладки: остановить с
понятным требованием нового estimate/запуска. Исторические terminal runs
читаются без migration/backfill вердиктов. Additive migration не должна
объявлять старые runs `ready`.

### C6. Warning и интерфейс операции

Без redesign: текущие Bootstrap banners, progress и `producer-run.html`.

- До запуска: «Сначала MT: N отсутствующих переводов на M языках.
  Затем судьи: до K строк. Имеющийся текст не изменится».
- Во время MT: «Подготовка переводов», реальные saved/required по фазе;
  не показывать judging, пока барьер не пройден.
- При отказе: «Судьи не запущены. MT остановлен: исчерпан месячный лимит
  ключа OpenRouter. Осталось N строк: … Обратитесь к администратору».
- Если известен именно insufficient-credit — говорить о балансе, а не
  о месячном лимите. Unknown — назвать отказ провайдера, не придумывать причину.
- Не показывать зелёное «завершено» при blocked prerequisite. Successful MT
  из ранних batches виден рядом с failed запуском, не скрывается.
- Durable warning виден после reload и через `ProducerRunSerializer.warnings`.
  Обычным пользователям не раскрывать служебные URL/ключи/модели. Текст
  переводимый, доступный с клавиатуры и не зависящий только от цвета.

## Задачи реализации

### 1. Довести ошибки MT до вызывающей операции

**Outcome:** отказ средств/ключа доходит до warning/failure; batch splitting
не умножает заведомо отказанные запросы. Partial-success сохраняется честно.
Обычные machinery suggestions не превращаются в новый global pipeline.

**Files and interfaces:** `weblate/machinery/base.py` — ошибки/download handler;
`weblate/machinery/openai.py:BaseOpenAITranslation.check_failure`;
`weblate/machinery/llm.py:_download_multiple_translations_with_context_cache`;
`weblate/trans/machinery.py` — batch/service/matches и C4;
`weblate/trans/autotranslate.py:AutoTranslate.fetch_mt`, `perform`;
`weblate/machinery/tests.py`,
`weblate/trans/tests/test_autotranslate.py:MachineryBatchFetchTest`.

**Actions:**

- [ ] Классифицировать HTTP и HTTP-200 upstream refusals до split-rescue.
- [ ] Передавать failure на caller thread; подключить warnings/failure и
      request stopping для mandatory preparation, без потери успешно
      полученных результатов и без DB-write из pool thread.
- [ ] Проверить LSP references `fetch_machinery_matches`, мигрировать всех
      затронутых callers и сохранить контракт возвращаемых переводов.
- [ ] Убрать ложный успешный итог standalone MT при provider refusal;
      не менять `auto_source=others`/component copy/обычный suggest semantics.

**Verification:** HTTP mocks: 402; 403 monthly limit; обычный 403; HTTP 200
с upstream error; partial good batch + fatal batch, sync и parallel.
Ожидается безопасная причина, сохранённый good batch, отсутствие новых
запросов после наблюдения fatal (in-flight допускаются), отсутствие
half-batch rescue для quota. Нынешний silent-error regression должен
сначала падать на отсутствии warning, затем проходить.
Команда: `./rundev.sh test weblate/machinery/tests.py
weblate/trans/tests/test_autotranslate.py`.

### 2. Закрыть scope, исполнить все MT и только затем открыть judge-фазу

**Depends:** задача 1; основной владелец `autotranslate.py` один.

**Outcome:** ни один язык не попадает к судьям, если любой выбранный язык
не готов. MT не ограничен judge cap, existing text/approved не меняется;
resume не повторяет выполненные записи и не расширяет расходы.

**Files and interfaces:**
`weblate/trans/autotranslate.py:JudgeScopePreview`, `JudgeSummary`,
`AutoTranslate.process_judge`, `store_results`, `BatchAutoTranslate`
(scope preview/adoption/execution/finalization);
`weblate/trans/models/judge.py:ProducerRun`, `JudgeRunUnit.SkipReason`;
`weblate/trans/tasks.py:auto_translate`, `auto_translate_component`,
`_producer_run_dispatch_kwargs`;
`weblate/trans/judge_loop.py:run_judge_batch`, `queue_judge_recheck`,
`_drain_seat`;
новая миграция в `weblate/trans/migrations/`;
`weblate/trans/tests/test_judge_autotranslate.py`, `test_autotranslate.py`,
`test_judge_loop.py`, `test_judge_deferrals.py`, `test_tasks.py`.

**Actions:**

- [ ] Реализовать C1/C2 один раз для preview и исполнения; перед HTTP
      проверить оба permission-набора: review/auto и direct-edit для MT.
      Если нужный MT запрещён хотя бы в выбранной паре — отказ до расходов,
      не тихое исключение языка из prerequisite.
- [ ] Разделить `_perform` на проход подготовки всех языков и проход судей;
      убрать локальное MT-before-judge из второго прохода, исключить двойной MT.
- [ ] Добавить C5 snapshot/state, атомарное резервирование и новую причину
      skipped; финализировать без зависших PENDING в `failed` (остановка по
      причине) или `cancelled`/`partial` — последние только как результат
      отмены, действующая семантика статусов не меняется.
- [ ] Ввести store-time missing/source guard и корректный actual-written
      счётчик; не держать locks на время HTTP.
- [ ] Устранить обход bulk флагами; preserve single-unit/candidate/drain
      purpose, добавить safety gate перед реальными judge batches.
- [ ] Сохранить исходные nonempty/approved targets и states на judge-phase
      этого pipeline; writable membership задаётся missing-at-start, а не
      `not translated`. Не включать старые state projections для approved.
- [ ] Обновить resume/cancellation контракты вместе с задачей 3 до интеграции.

**Verification:** в `JudgeAutoTranslateTest` и соседних поведенческих тестах:

1. Язык A MT успешно сохранён, язык B получает quota → judge HTTP calls=0,
   verdicts=0, failure+warning, A сохранён, B missing. До фикса проверка
   глобального порядка падает: A мог быть отправлен судьям раньше B.
2. Первый язык уже готов, последний missing находится за judge cap=1:
   перевод последнего выполняется до первого judge; при его отказе calls=0.
3. Engine missing/no route/пустой результат/ниже threshold/suggestion-only:
   остаток блокирует judge; нет ложного AUTO на пустом target.
4. Все языки готовы: отсутствует MT HTTP, judge работает; nonempty state
   10/11/12 допустимы, approved не демотируется, partial plurals блокируют.
5. Во время MT человек заполнил target: человеческий текст не затёрт,
   actual-written не увеличен. Изменился source — старый MT не записан.
6. После crash повтор использует тот же scope, MT для уже заполненного
   не оплачивается повторно; после explicit resume quota-фейла закрытый
   объём восстановлен, не только judge IDs из failed/skipped rows.
7. Перед следующей отправкой target стал empty: новые judge batches
   остановлены, уже завершённые сохранены; статус `failed` с причиной
   scope-changed, неполнота видна через покрытие и warnings, а не через
   статус `partial`.
8. recheck на пустоте не тратит деньги и не запускает project MT;
   candidate с непустым target не отбрасывается из-за пустого live target.

Команда: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py
weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_loop.py
weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_tasks.py`.
Проверка миграций в disposable test DB; номера/leaf брать из текущего дерева.

### 3. Согласовать запуск, estimate, warning и отчёт во всех входах

**Depends:** C1–C6 и задача 2; не параллельно с изменением shared scope builder.

**Outcome:** клиент видит весь предстоящий MT, принимает именно его объём,
после ошибки видит причину и языки; прямой REST/legacy task не обходят барьер.

**Files and interfaces:**
`weblate/api/producer/views.py:ProducerProjectJudgeEstimate`,
`ProducerProjectRunStart`, обработчики resume/detail;
`weblate/api/producer/serializers.py:ProducerJudgeEstimateSerializer`,
`ProducerRunSerializer`, request scope validators;
`weblate/api/views.py` autotranslate action;
`weblate/trans/forms.py:AutoForm`;
`weblate/trans/views/edit.py:auto_translation_preview`, `auto_translation`;
`weblate/trans/views/judge.py:producer_run`;
`weblate/templates/snippets/autoform.html`, `weblate/templates/producer-run.html`;
`weblate/static/loader-bootstrap.js`;
`weblate/api/tests.py`, `weblate/trans/tests/test_judge_views.py`,
`test_judge_form.py`, `test_selenium.py`;
`docs/specs/openapi.yaml` (генерируемый контракт, не новый fork-документ).

**Actions:**

- [ ] Общий scope validator для estimate/start; одинаково учесть explicit
      unit_ids (включая пустой список), query, права, подготовку и judge cap.
      Нельзя трактовать `[]` как «все» через `or None`.
- [ ] Включить preparation snapshot/config version в estimate/hash;
      старый estimate без нового контракта отвергать как estimate-drift,
      не считать его разрешением на дополнительные MT-расходы.
- [ ] Preview API расширить preparation counts/per-language/missing/blockers
      и basis; judge counts не выдавать за полный MT+judge cost. Если оценки
      MT нет — «стоимость неизвестна», не ноль. Никаких платных probes.
- [ ] Producer start/dispatch/resume переносит preparation отдельно от judge
      snapshot; existing idempotency и on_commit dispatch сохраняются.
- [ ] AutoForm валидирует движок для обязательного missing MT; полностью
      готовый judge scope не требует неиспользуемого движка. Standalone
      `auto_source=mt` без engines не проходит в тихий нулевой запуск.
- [ ] Прокинуть warnings через run serializer, task result и durable report;
      rendering C6, явная phase подготовки, локализация и доступность.
- [ ] Resume после смены MT-настроек требует нового estimate; увеличение
      лимита у провайдера без изменения конфигурации допускает обычный
      явный resume. Не переиспользовать старый consent для другого движка.
- [ ] Не показывать пересланные провайдером URL ключей в warning; permission
      на MT configuration management не подменять permission на review.
- [ ] Обновить endpoint schema существующим способом проекта и проверяемые
      контракты, не менять unrelated producer endpoints.

**Verification:** estimate/start с explicit IDs и с default scope одинаковы;
MT-язык за judge cap присутствует в preview; cross-project IDs не расширяют
доступ; `[]` ничего не запускает; config/scope drift → 409 до dispatch;
недостаточные direct-edit права → отказ до HTTP. Mocked quota через реальный
REST/legacy endpoint даёт failed run, durable warning после GET/reload и
нулевые judge requests. Повтор того же start идемпотентен; resume оставляет
completed MT нетронутым.

Для end-to-end REST использовать существующие `ProducerAPITest` и
`ProducerConsoleRealRunEndToEndTest` в `weblate/api/tests.py`: eager worker
и `http_mock` вместо подмены всего orchestration. Threaded provider
сценарии требуют `APITransactionTestCase`/`TransactionTestCase`, а не
внешней транзакции обычного TestCase. Snapshot empty-only записи проверить
отдельным конкурентным сценарием, не сравнением вызванных helper-ов.

Команды:
`./rundev.sh test weblate/api/tests.py -k producer` и
`./rundev.sh test weblate/api/tests.py -k autotranslate`, затем
`./rundev.sh test weblate/trans/tests/test_judge_views.py
weblate/trans/tests/test_judge_form.py`.

UI smoke в изолированной dev-среде, без платных/production calls: fixture
с двумя языками и перехваченными ответами MT (успех/403 monthly limit).
Открыть Operations → Automatic translation → judge, проверить preview,
фазы, warning на узком/обычном экране, keyboard, reload report. После 403
не должно быть judging-фазы и зелёного success. JS брать из актуального
collected bundle, а не stale static. Обновление static/worker общей среды
требует отдельного разрешения; не перезапускать shared stack ради smoke.

### 4. Обновить продуктовый контракт и выполнить интеграционную проверку

**Depends:** задачи 1–3.

**Outcome:** документация не обещает прежний per-language pipeline; нет
путей обхода через add-on/legacy API, прежние non-judge сценарии сохранены.

**Files and interfaces:** `docs/user/translating.rst` (`auto-translation`),
`docs/admin/checks.rst` (`llm-judge`), `docs/api.rst` (autotranslate/producer),
`docs/admin/config.rst` (семантика `JUDGE_MAX_UNITS_PER_RUN`),
`docs/product/guides/producer-guide-weblate.md`,
`docs/security/threat-model.rst`, `docs/changes.rst` (только upcoming).

**Actions:**

- [ ] Описать два объёма, all-language barrier, empty-only запись, warning
      лимита и повтор после его исправления. Не обещать автоматическую
      починку баланса или judge-проверку всего проекта при cap.
- [ ] Обновить threat model: bulk judge теперь включает явный MT-write
      prerequisite, direct-edit permission и больший, явно подтверждённый
      MT snapshot; per-unit recheck не расширяет outbound scope.
- [ ] Проверить existing add-on и non-judge tests; не закреплять тестами
      случайные тексты/проводку. Tests, предполагающие пустой fake judge
      input, заменить настоящим непустым fixture, а не отключать guard.
- [ ] Удалить одноразовые smoke scripts после доказательства; обновить
      статус этого плана фактическими результатами, не писать «зелено» заранее.

**Verification:** итоговый прогон без реальных провайдеров:

    ./rundev.sh test weblate/machinery/tests.py \
      weblate/trans/tests/test_autotranslate.py \
      weblate/trans/tests/test_judge_autotranslate.py \
      weblate/trans/tests/test_judge_views.py \
      weblate/trans/tests/test_judge_form.py \
      weblate/trans/tests/test_judge_loop.py \
      weblate/trans/tests/test_judge_deferrals.py \
      weblate/trans/tests/test_tasks.py
    ./rundev.sh test weblate/api/tests.py -k 'producer or autotranslate'
    ./rundev.sh test weblate/addons/tests.py -k AutoTranslateAddonTest
    ./rundev.sh check

Это команды репозитория для действующей dev-test среды, не разрешение
пересоздать shared stack. При работе в worktree использовать его собственный
test runtime/source; тест основного checkout не считается доказательством
worktree. Host-альтернатива: `uv sync --all-extras --dev`, затем инструкции
`docs/contributing/tests.rst`, `uv run pytest` на тех же файлах; для machinery
добавить `weblate_customization/src` в PYTHONPATH. Форматирование/линт один
раз после интеграции: `uv run prek run --files <изменённые файлы>`.

## Порядок, готовность и приёмка

Задачи 1 → 2 → 3 → 4 последовательны: shared orchestration/error contracts
небезопасно менять нескольким владельцам одновременно. Независимую работу
по UI-копии/документации можно выделить после фиксации C1–C6, но не назначать
отдельную команду для повторного проектирования этих контрактов.

Относительно `docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md`
этот план исполняется вторым. Оттуда приходят три предпосылки, которые здесь
не переопределяются: guard «одна доставка — один исполнитель» (перезапись
`_perform` обязана его сохранить), общий агрегат покрытия с разбивкой по
`skip_reason` (новые причины `mt-prerequisite` и существующая `untranslated`
попадают в «без заключения», не в проверенные) и неизменная семантика
статусов. Общие файлы — `autotranslate.py`, финализация summary/warnings,
`producer-run.html`, `ProducerRunSerializer` — меняются последовательно,
один владелец за раз; поле `warnings` сериализатора добавляет первый план,
этот принимает его как данное.

Соответствие требованиям:

| Требование | Где реализуется и проверяется |
|---|---|
| Сначала MT всех выбранных языков | C1/C3, задача 2, сценарии 1–2 |
| Не перезаписывать существующее | C2/C5, задача 2, сценарии 4–5 |
| Денег/лимита нет → warning, судей нет | C4/C6, задачи 1–3 |
| Не маскировать остаток успешным завершением | C3/C5, задачи 2–3 |
| Cap не обрезает подготовку поздних языков | C1, задачи 2–3 |
| Пустота после concurrent edit не отправляется | C3/C5, задача 2, сценарий 7 |
| Повтор безопасен после crash/quota | C5, задачи 2–3 |
| UI/REST/direct task не обходят guard | C3, задачи 2–3 |
| Single-unit/candidate не переводят соседние языки | C3, задача 2, сценарий 8 |

Архитектурно значимые решения сформулированы; блокирующих технических
неизвестных для согласования нет. Предложенные новые типы/поля отделены
от наблюдаемых интерфейсов. Согласование особенно включает расширение
preparation за judge cap и изменение bulk proposal-only запуска: MT может
записать только отсутствующий текст, сама judge-фаза остаётся без overwrite.

Реализация разрешена (см. статус выше), но только в порядке «первый план →
этот», и планирование по-прежнему не является разрешением на деплой.
Исполнять через `ultrasuperpowers-executing-plans` в изолированном worktree.
Коммит/пуш — по `AGENTS.md`, после общей проверки, не по каждому микрошагу.
Деплой, migration/static updates и worker restart — отдельное согласование;
production smoke с реальными провайдерами также требует разрешения.
