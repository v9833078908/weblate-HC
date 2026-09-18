# Полная проверка выбранного объёма небольшими порциями

Дата: 2026-09-17.
Статус: одобрен к реализации 2026-09-18; **реализован** на ветке
`feat/judge-full-scope-continuation`, ожидает PR-ревью и слияния в `main`.

## Цель и выбранное решение

Продюсер подтверждает проверку выбранных компонентов и языков и получает один
отчёт по всему подтверждённому объёму, а не только по первым 2000 переводам.
Техническое завершение порции не выдаётся за завершение пользовательского запуска.

Минимальное решение: **один существующий `ProducerRun`, один сохранённый список
строк, один курсор и одна текущая Celery-задача**. Следующая порция автоматически
публикуется через существующий durable dispatch. Результаты остаются в
`JudgeRunUnit`; новые таблицы кампаний, дочерних запусков и заданий не нужны.

Стартовый размер порции — 100 target-language строк, один исполнитель на run.
Это предложенная внутренняя константа, не новая настройка продюсера и не размер
HTTP-batch модели. Позже менять её по измерениям. Порция ограничивает число строк,
но не обещает жёсткое время: сохраняются существующие HTTP deadlines и retry budget.
Не поднимать cap до 10000 и не заменять его одним бесконечно длинным вызовом.

Не входят: новый scheduler/queue, увеличение параллелизма, бюджетная подсистема,
смена судей, prompt/QE/cascade, изменение MT/repair/approval-политики, дедупликация
отдельных намеренных запусков. Денежную границу из строки cap не изобретать.

## Контекст и зависимости

- `docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md` — интегрирован
  на main (merge `299a4011`): guard «одна доставка — один исполнитель»,
  durable dispatch и общий coverage уже доступны. Настоящий план меняет
  ограничение cap для новых bulk judge runs, не переписывая защиту исполнения.
- `docs/product/plans/2026-09-17-mt-prerequisite-before-judge.md` — контракт
  «MT всего preparation scope → барьер → судьи». Исполняется в ветке
  `feat/mt-prerequisite-before-judge` (worktree `.worktrees/mt-prerequisite-before-judge`):
  на момент ревью задачи 1–2 закоммичены, задача 3 не закоммичена. Интегрировать
  её согласованную реализацию до этого плана: порционирование не должно вернуть
  MT/judge по языкам вперемешку. Три изменения общих файлов не выполнять параллельно.
- Пользователь подтвердил, что
  `docs/product/plans/2026-09-16-03-mt-engine-selection-and-untranslated-judge-scope.md`
  реализован. Не повторять эту работу на основании устаревшего статуса документа.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/report.md` и
  `docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md`:
  Anvil-прогоны по 2000 строк занимали 5–9 часов; повторное исполнение пересеклось
  по 291 строке. Это основание делить исполнение, а не увеличивать длительность
  одной задачи. Оценки расходов в исследовании не являются invoice.
- `docs/product/plans/2026-09-17-judge-batching-and-latency-experiments.md` —
  независимые измерения скорости моделей, не предпосылка полного покрытия.

Наблюдаемые границы текущего кода:

- `BatchAutoTranslate.preview_judge_scope` и `preview_judge_scope_snapshot` в
  `weblate/trans/autotranslate.py` применяют глобальный cap; `_perform` пишет
  CAP-skips, а `perform` финализирует run после одного вызова.
- `ProducerRun` уже содержит `scope_snapshot`, `scope_hash`, `configuration_snapshot`,
  `summary`, `task_id`, `dispatch_*`, `resumed_from`; `JudgeRunUnit` — журнал строк.
- `publish_producer_run_dispatch` и `drain_producer_run_dispatches` в
  `weblate/trans/tasks.py` уже восстанавливают committed intent без broker mark.
  Сейчас они принимают QUEUED, а dispatch builder знает project/recheck.
- Producer API estimate/start используют capped snapshot. LSP references
  `preview_judge_scope_snapshot` подтвердили два потребителя в
  `weblate/api/producer/views.py`.
- UI `auto_translation` публикует задачу, а run создаётся позже; это нужно
  заменить предсозданным run для нового bulk-контракта.
- `ProducerRunResume` сейчас создаёт новый linked run только для FAILED.
  Не смешивать автоматическое продолжение порций с этим ручным действием.

Это исходные границы, не утверждение, что параллельно разрабатываемые зависимости
уже присутствуют в checkout. Перед исполнением читать их интегрированный код.

## Общие контракты

### 1. Полный scope и подтверждение расходов

Один scope builder для preview/estimate/start фиксирует упорядоченные IDs всего
разрешённого объёма без judge cap. Сохранять прежний порядок, исключение source/
read-only, explicit IDs, ограничения query и permissions. `[]` означает пустой
объём, не «все». После старта membership не пересчитывать через `NOT has:judge`.
Новые строки не добавляются; удалённые/перемещённые остаются учтёнными как пропуск
с причиной, а не исчезают из знаменателя. Число строк — target-language units.

Preview показывает весь judge scope, языки и отдельный preparation scope MT.
Старый estimate на 2000 нельзя принять как согласие проверить 58000: scope hash
должен включать `execution_version`. Сейчас `_scope_hash_for` хеширует
`{project, scope, unit_ids, preparation}` — ключа версии нет ни в MT-ветке,
ни на main. Добавить `execution_version` пятым ключом; старый estimate с
`execution_version=0` (или без него) автоматически не пройдёт drift-проверку.
Если стоимость неизвестна, так и писать; не умножать известную часть ledger и
не представлять результат как гарантированный счёт.

`scope_snapshot` хранит IDs, не копию текстов; объекты Unit загружать только для
порции. Не резервировать все строки как PENDING одним большим Python-списком.
Исходные source/target/context hashes и snapshots фиксируются существующим
журналом при фактической оценке. Следовательно, scope заморожен по membership,
но отчёт обязан отдельно учитывать изменения текста относительно оценки.

Единственная реальная оплата вне подтверждённого объёма — дублирующий внешний
вызов после системного отказа (§3, п. 4): публикация и батч остаются маленькими,
поэтому точная оценка по объёму и зафиксированная конфигурация остаются
достаточным согласием.

### 2. Минимальные данные и совместимость

В `ProducerRun` добавить только недостающие после интеграции зависимостей поля:

- `execution_version`: 0 для прежних запусков, 1 для нового полного scope;
- `scope_cursor`: позиция следующей порции в `scope_snapshot`, по умолчанию 0;
- `execution_options`: закрытая версия безопасных параметров запуска, необходимых
  для восстановления UI/API-вызова (режим кандидатов, выбранные MT engines,
  threshold и остальные реально используемые параметры). Только IDs/значения,
  никаких ключей или произвольных kwargs. Если зависимость уже хранит этот
  контракт, переиспользовать его, не добавлять второй JSON.

Одна additive migration от актуального leaf. На момент интеграции
`2026-09-17-mt-prerequisite-before-judge` leaf — `0136_producer_run_preparation`,
поэтому ожидаемый номер `0137`; перепроверить на момент работы. `dispatch_task_id`
уже является токеном поколения порции — отдельный generation counter не нужен.
Не переиспользовать `dispatch_phase` для MT readiness: у подготовки есть
собственное состояние.

Новые runs не используют `JUDGE_MAX_UNITS_PER_RUN` для усечения. В обязательном
историческом поле `cap` сохранять подтверждённую длину нового scope; описать эту
семантику через `execution_version`. Старые cap/summary/snapshot не переписывать
и не достраивать задним числом. Legacy незавершённый run не расширять при выкладке:
его завершает прежний контракт либо оператор явно создаёт новый полный запуск.

Это сознательно меняет артефакты плана
`2026-09-17-mt-prerequisite-before-judge`, который намеренно сохраняет cap:
его валидатор estimate/start с `JUDGE_MAX_UNITS_PER_RUN`, C6-текст
«судьи: до K строк», тесты с cap-фикстурами (например, последний язык за
`cap=1`) и resume-контракт «сохраняет judge cap/no-backfill» для новых bulk
runs заменяются на полный scope; исторические runs сохраняют прежнюю
интерпретацию. Исполнителю MT-плана не вкладываться в cap-формулировки,
которые продолжение сразу заменит.

### 3. Одна порция и надёжное продолжение

1. Сообщение несёт run ID и свой task UUID. До heartbeat и любой платной работы
   взять guard по run ID из плана redelivery и проверить под row lock, что UUID
   равен текущему `dispatch_task_id`. Просроченное поколение ничего не меняет.
2. Подготовка MT сохраняет существующую логику MT-плана без изменений: один
   `_perform` проходит translations, как сейчас, empty-only запись и snapshot/
   human guard остаются. Порционирования для MT нет — скорость и эффективность
   подготовки не меняются. При crash/redelivery прогресс продолжается по
   сохранённому `preparation_snapshot` и свежему missing, без повторной оплаты
   уже записанного. Judge `scope_cursor` до барьера не двигать.
3. После барьера взять `scope_snapshot[cursor:cursor + 100]`, сгруппировать по
   translation_id и для каждой translation вызвать `process_judge` только с
   подмножеством IDs текущей порции, не со всем queryset; translations без IDs
   в порции пропускаются. `_perform` прерывает цикл translations после обработки
   порции и возвращает `has_more`, а не проходит все translations до конца.
   Не применить исходный живой query второй раз к уже выбранным IDs.
   Существующие валидные завершённые результаты этого run после redelivery
   не отправлять повторно. Проверки актуальности входа/permissions сохраняются,
   а перед судейской отправкой подтверждать readiness всего закрытого объёма
   один раз за доставку, а не один раз на HTTP-батч.
4. Сохранять результаты по мере получения. Локальные exhausted unparsed/skips
   остаются исходами без заключения; после их фиксации можно перейти дальше.
   Системная ошибка доступа/ключа, недоступность провайдера после ограниченных
   retries или потеря нужного доступа останавливает run с причиной и остатком.
   Классификация этих системных ошибок идёт через существующий
   `MachineTranslationServiceError`/`MachineryBatchOutcome` контракт плана MT
   prerequisite, без второго классификатора.
5. Порция считается завершённой, когда каждый её ID имеет устойчивый исход
   (заключение или зафиксированный пропуск) и результаты сохранены. Далее в
   короткой транзакции внутри guard перечитать cancel/status/UUID, сдвинуть
   cursor, записать новый dispatch UUID, requested_at, обнулить
   published_at/attempts/error. Статус логического run остаётся RUNNING.
   `started` не сбрасывается.
6. После commit вызвать существующий publisher, затем освободить guard
   текущей доставки. При crash до публикации существующий drain подхватит
   intent. При crash после broker send, но до mark, возможен повтор того же
   UUID; его обезвреживает guard, а не вера в exactly-once.
7. Только на конце scope выполнить общую финализацию. Окончание порции — отдельный
   внутренний результат `has_more`, не вызов `_finish_producer_run(COMPLETED)`.

Связка поколений требует явных изменений существующих механизмов (все — новый
код, не описание текущего поведения):

- **(новое)** `_adopt_producer_run` (не `_producer_run_request_matches`)
  дополнительно проверяет `dispatch_task_id` при адопции RUNNING: совпадение
  `dispatch_task_id` текущей доставки — адопция продолжения, чужой UUID —
  отказ с FAILED. `_producer_run_request_matches` продолжает проверять scope
  identity (mode/query/unit_ids) и не берёт на себя guard поколения.
  COMPLETED/FAILED/CANCELLED/PARTIAL возвращаются как терминал.
- **(новое)** `producer_execution_guard` (file lock через `WeblateLock`,
  `file_only=True`) принимает delivery UUID и не берёт file lock, если UUID
  уже устарел (устаревшая доставка должна немедленно выйти без удержания lock,
  а не ждать 480 retries); если UUID ещё актуален, доставка может войти и
  продолжить работу. Guard сам — file lock; row lock нужно взять явно
  (`select_for_update`) внутри транзакции. Порядок смены поколения:
  advance/cancel/status под `select_for_update` внутри file-lock guard,
  publish нового UUID после commit, освобождение file lock после commit —
  новая доставка не может начать до того, как предыдущее поколение закончило
  транзакцию.
- **(новое)** `publish_producer_run_dispatch` (сейчас принимает только
  QUEUED) и `drain_producer_run_dispatches` (сейчас фильтрует только QUEUED)
  расширяются на RUNNING только для `execution_version=1` с валидным intent
  продолжения. Drain проверяет `execution_version` перед публикацией, чтобы
  при rollback старый код drain не подхватил continuation intent. При
  `CANCEL_REQUESTED`/терминале drain не публикует новую порцию, а сначала
  финализирует cancelled/partial. Publisher callbacks обновляют run только
  при совпадении UUID и допустимого статуса. Для version 0 поведение
  остаётся прежним.

Отмена между commit и publish запрещает новую отправку; уже попавшая в брокер
задача проверяет отмену до HTTP. Никаких DB-транзакций на время LLM.

Crash до advance cursor повторяет ту же порцию, но использует уже сохранённые
результаты. Crash после advance использует следующую. Окно «провайдер ответил,
но результат не сохранился» остаётся возможной повторной оплатой; exactly-once
billing не обещается. Разные намеренные run могут работать независимо.

### 4. Отчёт и актуальность результатов

Переиспользовать единый coverage из плана redelivery. Для execution_version=1
знаменатель известен даже у RUNNING/FAILED: `len(scope_snapshot)`.
Полнота версии 1 — каждый ID из `scope_snapshot` имеет устойчивый исход;
`cap_remainder` остаётся историческим полем прежних runs.
Показывать «с заключением / всего», ожидающие, исходы без заключения и причины,
плюс текущую фазу. Порции не отображать как самостоятельные отчёты.

Не считать PENDING, UNPARSED, DEFERRED, SKIPPED, REFUSED и STALE_CONFLICT
заключениями. Cached входит в число результатов один раз. Новые runs не получают
CAP-skips от технического порционирования. Отсутствие ошибок не равно полному
покрытию, полное покрытие не равно готовности к релизу.

При финализации и чтении отчёта проверять соответствие имеющихся заключений
текущему source/target/context/profile существующим контрактом актуальности.
Изменившееся/удалённое обозначать отдельно и исключать из актуального покрытия;
исторические заключения сохранять. Не запускать бесконечную автоматическую
переоценку при правках. Реализовать сравнение наборами/порциями, без запроса
на строку; не пересчитывать одинаковые hashes для view и serializer отдельно.

`completed` — обработан весь сохранённый scope; coverage может оставаться неполным
из-за локальных отказов/изменений. `failed` — системная остановка до завершения.
Отмена сохраняет существующую семантику cancelled/partial. Summary вычисляется
по всему ledger, не по последней порции; MT preparation summary не теряется.
Прогресс producer UI/API читается по run ID, не как success первой Celery-задачи.

Автоматическое продолжение всегда использует один run. Ручной `ProducerRunResume`
сохраняет существующий контракт: FAILED → новый linked run с тем же полным scope
и options, cursor=0, reuse только актуальных cached результатов. Это явный повтор,
не скрытая новая порция. Не менять endpoint на in-place resume в этой работе.
При смене model/MT-конфигурации требуется новый estimate, не старое согласие на
другие расходы. Не добавлять кнопку «перепроверить всё» или новую retry-политику.

## Задача 1. Зафиксировать полный объём и восстановимые параметры запуска

**Результат:** preview/start обозначают и сохраняют весь объём; поздние языки
не исчезают за cap, старое согласие не расширяет расходы.

**Файлы:** `weblate/trans/autotranslate.py:BatchAutoTranslate.preview_judge_scope`,
`preview_judge_scope_snapshot`; `weblate/trans/models/judge.py:ProducerRun`;
новая миграция в `weblate/trans/migrations/`;
`weblate/api/producer/views.py:ProducerProjectJudgeEstimate`, `ProducerRunStart`,
`_scope_hash_for`;
`weblate/trans/views/edit.py:auto_translation_preview`, `auto_translation`.

- [x] Повторить LSP references после интеграции зависимостей; один общий builder
      полного scope, один сохранённый execution-options контракт.
- [x] Добавить `execution_version` пятым ключом в `_scope_hash_for`; estimate
      без ключа или с `0` не проходит drift-проверку start'а версии 1.
- [x] Предсоздавать run и intent в UI так же, как в producer API; initial publish
      через on_commit и существующий dispatcher. Одинаковый start идемпотентен.
- [x] Удалить усечение из нового preview/start, валидировать scope/options drift.
      Нулевой scope не публикуется и ничего не оплачивает.
- [x] Подготовить migration и явный legacy/new branching только для уже
      сохранённых старых runs; новые bulk-входы не создавать по старому контракту.

**Проверка:** scope 2105 строк в нескольких языках даёт snapshot 2105, включая
последний язык; explicit IDs и `[]`; чужие IDs и права; старый estimate отвергнут;
повтор POST возвращает тот же run; падение до on_commit не оставляет платной задачи.
Существующие `weblate/api/tests.py` producer tests и
`weblate/trans/tests/test_judge_autotranslate.py`/`test_judge_form.py`.

## Задача 2. Исполнять порции и восстанавливать цепочку через текущий dispatch

**Зависимость:** задача 1 и интегрированный execution guard.
**Результат:** один run автоматически проходит весь scope, без второго
исполнителя и без потери продолжения при crash.

**Файлы:** `weblate/trans/tasks.py:auto_translate`, `auto_translate_component`,
`_producer_run_dispatch_kwargs`, `publish_producer_run_dispatch`,
`drain_producer_run_dispatches`, `producer_execution_guard`,
`guard_producer_execution`, `_fail_stalled_producer_run`;
`weblate/trans/autotranslate.py:BatchAutoTranslate.perform`, `_perform`,
`_adopt_producer_run`, `_producer_run_request_matches`, `_finish_producer_run`,
`AutoTranslate.process_judge`;
`weblate/utils/celery.py` — только если требуется согласовать liveness текущего UUID.

- [x] Вынести выполнение выбранных IDs из финализации целого run; передавать
      явный результат порции, не угадывать его по тексту сообщения.
- [x] В `_perform` реализовать цикл по порциям из scope_snapshot: для каждой
      порции сгруппировать IDs по translation, вызвать `process_judge` только с
      подмножеством IDs, пропускать translations без IDs в порции, и после
      обработки порции прервать цикл translations с `has_more=True`.
- [x] Поддержать все уже существующие ScopeType в bulk dispatch, не создавать
      project-only обход для component/language/workspace.
- [x] Реализовать транзакционную смену UUID/cursor и handoff guard по §3
      (UUID-aware guard, запрет ожидания устаревшей доставки, publish после
      commit и до освобождения lock); cancellation проверять перед каждой новой
      отправкой.
- [x] `perform` не должен финализировать execution_version=1 run при исключении
      из `_perform`: текущий код вызывает `_finish_producer_run(FAILED)` в
      `perform` после `_perform`, но для продолжения run должен остаться RUNNING
      при ошибке провайдера внутри порции — финализация идёт только при
      системной ошибке (потеря DB, OOM). Ошибка порции записывается как
      FAILED только если §3.4 определяет её как системную остановку.
- [x] `auto_translate_component`/add-on judge использовать тот же durable путь.
      Старые автоматические конфигурации, рассчитанные на capped scope, не
      расширять молча: потребовать явного повторного подтверждения полного объёма.
      Обычный non-judge MT и recheck/drain/candidate не превращать в bulk.

**Проверка:** два реальных процесса и fake HTTP provider, включая kill после
сохранённого batch; crash перед/после cursor advance и broker send; stale UUID
не ждёт и не блокирует следующую порцию; drain не публикует новую порцию для
RUNNING при CANCEL_REQUESTED, а финализирует cancelled/partial; publisher
расширяет RUNNING только для execution_version=1, а version 0 ведёт себя
прежним образом; cancel между commit/publish; ошибка публикации;
permission/quota отказ; исчезнувший Unit; ошибка провайдера внутри порции
оставляет run RUNNING с зафиксированным остатком, а не финализирует FAILED
через `perform`. Порция из 100 IDs, разбросанных по трём translations,
обрабатывает только эти IDs и не трогает остальные строки translations.
Ни одна сохранённая актуальная строка не оплачивается снова, общий summary
сохраняет результаты всех порций. MT языка за первой judge-порцией должен
завершиться до первого judge HTTP; подготовка сохраняет существующую скорость
и логику MT-плана.

Команды в изолированной test-среде, с кодом worktree:

    ./rundev.sh test -n 0 weblate/trans/tests/test_tasks.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_autotranslate.py

Дополнительно smoke с Redis/Celery и fake HTTP: scope больше 2000, остановка
worker и восстановление, один отчёт и все строки учтены. Ни прод, ни реальные LLM.

## Задача 3. Согласовать входы, единый отчёт и выпуск

**Зависимость:** задачи 1–2. Общие файлы менять последовательно одним владельцем.
**Результат:** человек видит прогресс всего задания, а не завершение первой порции;
нет альтернативного публичного bulk judge, который молча проверяет только 2000.

**Файлы:** `weblate/trans/views/judge.py:producer_run`;
`weblate/templates/producer-run.html`, `weblate/templates/snippets/autoform.html`;
`weblate/static/loader-bootstrap.js` — preview/polling;
`weblate/api/producer/serializers.py:ProducerRunSerializer`;
`weblate/api/producer/views.py:ProducerRunResume`, `ProducerRunCancel`;
`weblate/api/views.py:TranslationViewSet.autotranslate`;
`weblate/trans/models/judge.py:ProducerRun.get_coverage`;
`docs/admin/config.rst`, `docs/api.rst`, `docs/specs/openapi.yaml`,
`docs/product/guides/producer-guide-weblate.md`, `docs/security/threat-model.rst`,
`docs/changes.rst`.

- [x] Расширить существующий coverage для нового полного snapshot; добавить
      актуальность/ожидающие, сохранив честное unknown у исторических runs.
      Для execution_version=1 `scope_complete` определяется набором устойчивых
      исходов по `scope_snapshot` (set-сравнение, не per-row), а `cap_remainder`
      остаётся историческим полем версии 0.
- [x] UI/polling привязать к run; не выдавать success завершившейся Celery-порции.
      Отчёт остаётся по одному URL, вверху видны total, результат и причины остатка.
- [x] Для legacy REST `autotranslate(mode=judge)` предложен явный переход на
      тот же asynchronous start: после preview/подтверждения вернуть HTTP 202
      с run ID/report URL; non-judge ответы неизменны. Это изменение API-контракта
      входит в согласование плана, не скрытая совместимость. Адаптировать всех
      найденных через LSP callers и schema; без подтверждения полного объёма
      вернуть явный конфликт с требованием preview, не начать многосуточный HTTP.
- [x] Сохранить контракт linked resume и отмены, обновить docs и тесты клиентов.
      Не обещать жёсткий денежный потолок: здесь есть scope consent и остановка
      на отказе провайдера, не новая финансовая подсистема. В threat-model явно
      записать: одно подтверждение теперь авторизует многодневную оплачиваемую
      цепочку с автопродолжением; отмена между порциями остаётся единственным
      операционным circuit breaker; новые DB-поля не раскрываются наружу.
- [x] Для новых bulk runs убрать cap-warning и описание «до 2000»; оставить
      историческую семантику cap для старых отчётов. Депрекационные API-алиасы
      или второй параллельный механизм продолжения не создавать.

**Проверка:** 2105 строк, один run, несколько порций, итоговый denominator 2105;
unparsed/skipped не попали в заключения; изменение ранее проверенного target
видно как неактуальное; counts одинаковы в HTML/API независимо от фильтра/страницы.
Проверить реальный UI на обычной/узкой ширине, reload во время смены задач,
отмену и keyboard. Соблюдать `ACCESSIBILITY.md` и `docs/contributing/frontend.rst`.

    ./rundev.sh test -n 0 weblate/trans/tests/test_judge_views.py weblate/api/tests.py -k 'Judge or Producer or autotranslate'

Перед commit выполнить перечисленные регрессии и проверки изменённых файлов
через `uv run prek run --files <изменённые файлы>`; OpenAPI обновить принятым
генератором. Это будущие проверки, не заявление о пройденных тестах реализации.
Host-альтернатива — `uv run pytest` по инструкции `docs/contributing/tests.rst`.
Не перезапускать shared stack ради проверки worktree.

## Выпуск и критерий готовности

Реализация в worktree после отдельного одобрения; commit/push после проверки.
Rollout отдельно: additive migration, обновление всех workers и web без смеси
старых активных исполнителей с новыми. Старые runs не расширяются автоматически.
При откате не отдавать новые execution_version=1 сообщения старому worker:
drain проверяет `execution_version` перед публикацией continuation intent,
поэтому старый код drain не подхватит RUNNING run версии 1; сначала прекратить
dispatch новых порций/дождаться безопасной остановки по согласованной
операционной процедуре. Не сбрасывать cursor вручную.

Готово, когда один подтверждённый запуск больше 2000 строк сам доходит до конца
сохранённого scope, переживает повторную доставку и рестарт, не теряет продолжение
после DB commit, сохраняет все результаты и показывает правдивое покрытие.
Пропуски/ошибки не маскируются, но их наличие не вызывает бесконечный автоматический
retry. Размер порции остаётся внутренним свойством исполнения.

Открытая граница согласования: asynchronous 202 для legacy judge REST вместо
нынешнего синхронного ответа. Она описана явно, потому что сохранение старого
синхронного пути для полного объёма оставило бы многосуточный HTTP, а сохранение
его cap — второй неполный продуктовый путь. До одобрения этой границы план
не считать разрешённым к исполнению. Остальные изменения не требуют новой
кампании, нового брокера, отдельного UI порций или собственной системы бюджетов.
