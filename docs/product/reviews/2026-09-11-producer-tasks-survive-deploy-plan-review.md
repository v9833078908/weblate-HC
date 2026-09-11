# Ревью плана «Продюсерские задачи переживают перезапуск сервиса»

**Дата:** 2026-09-11.
**Рецензируется:** `docs/product/plans/2026-09-11-producer-tasks-survive-deploy.md`
(статус: требует согласования; реализация не начата).
**Итог:** **не одобрять до устранения двух P0.** Механизм инцидента и
предлагаемая граница остановки `celery-translate` установлены корректно.
План, однако, пока не задаёт реализуемый и правдивый контракт UI, а перенос
`fix_failing_checks` в очередь `translate` создаёт невыбранную политику
конкуренции двух продюсерских операций.

## Что проверено и подтверждается

| Утверждение плана | Результат |
| --- | --- |
| Автоперевод и `auto_translate_component` имеют `acks_late=True` и `reject_on_worker_lost=True` | Подтверждается: `weblate/trans/tasks.py:992-999,1130-1137`. |
| `fix_failing_checks` — третья такая задача | Подтверждается: `weblate/trans/tasks.py:1256-1263`; runtime census на dev вернул ровно эти три task name. |
| Сейчас `fix_failing_checks` идёт в общий `celery` | Подтверждается: `CELERY_TASK_ROUTES` содержит только `auto_translate*` для `translate` (`weblate/settings_docker.py:1444-1454`). |
| Upstream supervisor-конфиг выделяет отдельную программу `celery-translate` | Подтверждается на запущенном образе: `/etc/supervisor/conf.d/celery-translate.conf` слушает только `translate`, имеет `autorestart=true` и пока не задаёт `stopsignal`/`stopwaitsecs`. `Subprocess.stop()` передаёт именно `config.stopsignal`; значит точечная правка этого конфигурационного файла технически верна. |
| Размеры grace-window согласованы | При `WEBLATE_CELERY_SOFT_SHUTDOWN_TIMEOUT=20`, `stopwaitsecs=60` и `stop_grace_period=90s` worker имеет время отменить active request, kombu — выполнить finalizer и вернуть unacked-сообщение до Docker SIGKILL. |
| Имеющаяся проба различает SIGTERM/SIGKILL и SIGQUIT | Подтверждается результатом `analysis/probes/celery_shutdown_requeue.py`: `acks_late` + SIGQUIT очистил `unacked` и вернул одно сообщение в очередь, SIGTERM + SIGKILL оставил его в `unacked`. |
| Массовый фикс уже публикует счётчик единиц | Подтверждается: `progress_callback` отдаёт `progress/done/total` в `task.result` (`weblate/trans/tasks.py:1321-1331`), а существующий JS уже отображает `done / total processed` (`weblate/static/loader-bootstrap.js:1819-1835`). |

## Находки

### F1 (P0) — Task 2 не задаёт выполнимый контракт heartbeat/stale

План предлагает вычислять
`stale = not completed and (heartbeat is None or now - heartbeat > 180)`, но
в текущем хранилище нет ни стартового marker, ни признака, что данная задача
вообще обязана посылать heartbeat:

- `store_task_metadata()` хранит только `component_id`, `translation_id` и
  необязательный `user_id` (`weblate/utils/celery.py:51-80`);
- `TaskSerializer` и `TasksViewSet.retrieve()` отдают только
  `completed/progress/result/log` (`weblate/api/serializers.py:4150-4155`,
  `weblate/api/views.py:4955-4968`);
- `AutoTranslate.set_progress()` пишет Celery meta, но не cache heartbeat
  (`weblate/trans/autotranslate.py:272-289`), а его базовая meta — только
  `translation` (`:350-351`).

Следовательно, условие «только для задач, у которых heartbeat когда-либо был»
нельзя исполнить: отсутствие ключа одинаково для свежей задачи, задачи до
первого progress tick и для оборванной задачи. Через три минуты план либо
ложно объявит свежую работу оборванной, либо не объявит оборванной никогда.

180 секунд ещё и меньше допустимого безмолвного запроса: обычный LLM engine
имеет `request_timeout=120` (`weblate/machinery/llm.py:384-385`) и у базовой
machinery три retry (`weblate/machinery/base.py:131-146`); LiteLLM использует
55 секунд на попытку (`weblate_customization/.../machinery.py:405-408`).
Один медленный внешний вызов с retry может честно идти существенно дольше
трёх минут.

**Необходимая правка плана.** До UI сначала описать один закрытый status
record для *опт-in пользовательских задач*: `started_at`, `heartbeat_at`,
`heartbeat_enabled`, task class и разрешённый scope. Marker ставится при
публикации, heartbeat — в начале task и перед/после каждой потенциально долгой
внешней попытки. API само вычисляет status; браузер не интерпретирует сырые
timestamps. Порог должен быть больше worst-case одного запроса с retry либо
UI должен честно говорить «нет обновлений с …», а не «перезапуск прервал
задачу». Сильное сообщение о прерывании допустимо только при доказанном
событии деплоя/остановки.

Приёмка должна покрыть отдельно: до первого tick, живой запрос длиннее 180 с,
просроченный heartbeat, успешный completion и восстановленную delivery.

### F2 (P0) — перенос `fix_failing_checks` в `translate` не выбирает политику конкуренции

Из `acks_late=True` не следует, что задача должна жить в очереди
автоперевода. Сейчас `fix_failing_checks` обслуживается общим worker, а
`translate` — отдельным worker. После изменения длинная массовая правка сможет
занять capacity, на которой должен начаться автоперевод; у `translate` нет
отдельной stated priority policy. План 10.09 устанавливает приоритеты только
для очереди `celery` и прямо исключает `translate`
(`docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md:45-49,
102-107`).

Это не просто route-деталь: меняется наблюдаемое SLA двух действий продюсера,
но документ не выбирает, кому уступает другой. Фраза «заодно массовое
исправление перестаёт стоять за уборкой» также смешивает две цели: устойчивое
завершение при деплое и план 10.09 про backlog статистики.

**Рекомендуемое решение:** в этом плане не менять очередь mass fix. Добавить
тот же контролируемый cold-shutdown контракт и к `celery-celery`, где он уже
исполняется; для задач без `acks_late` это не создаёт новой гарантии доставки,
а даёт им как минимум нынешнее время на завершение. Если требование именно
«массовый фикс не ждёт фоновой работы» остаётся, вынести его в явное решение:
либо отдельная queue/worker, либо приоритет в `celery` с доказанным callsite
контрактом. Нельзя получить это как побочный эффект маршрутизации в
`translate`.

Тест должен прерывать массовую правку после реального изменения и доказывать
не только redelivery, но и отсутствие двойного изменения / захвата чужого
`lock_key` после восстановления.

### F3 (P1) — заявленная кнопка повторного запуска отсутствует как интерфейс

`/api/tasks/<id>/` имеет GET и DELETE; GET — read-only, DELETE отменяет
задачу (`weblate/api/views.py:4955-4978`). Репозитарий не имеет общего
endpoint для requeue/replay. У metadata нет полного тела исходной задачи и
её delivery options (`weblate/utils/celery.py:51-80`), а повторная публикация
того же `task id` во время delayed redelivery дала бы дубликат.

**Правка:** удалить кнопку из Task 2. Для подтверждённо возобновляемой задачи
показывать её task URL и объяснять автоматическое восстановление. Если нужна
кнопка, это отдельный мутирующий endpoint с task-specific permission,
пересчётом scope из текущего состояния, новым idempotency key, CSRF и
threat-model review; его нельзя считать мелкой правкой фронта.

### F4 (P1) — Task 2 дублирует существующий done/total и неверно размещает контракт

`done`/`total` для mass fix уже находятся в `result`, и JS уже их показывает.
Ни `TaskSerializer`, ни `message.html` не требуют отдельных полей для этого
пути. Для auto-translation задача обратная: `progress_steps` измеряет не
стабильно «число строк» — для judge это worst-case calls
(`weblate/trans/autotranslate.py:803-805`), а meta auto-task содержит только
`translation` и `progress`.

**Правка:** определить для автоперевода, что именно означают числа:
`processed` — просмотренные/переданные движку строки, `total` — snapshot
всех eligible строк на старте; `updated` остаётся результатом, не
denominator. Публиковать эти два числа в существующем `result` meta, не
расширять serializer без необходимости. Для BatchAutoTranslate явно описать,
как агрегируются translation-level snapshots. Отдельный тест должен доказать,
что многошаговый judge не превращает число сетевых вызовов в «строки».

### F5 (P1) — pre-deploy guard и post-deploy assertion не реализуемы по описанию

`deploy_stack()` знает только необязательный первый аргумент `--build`
(`deploy/vps.sh:188-191`); заявленный `--force` пока будет проигнорирован как
обычный аргумент. Плюс скрипт сразу пушит и делает remote `git reset --hard`
до `docker compose up` (`:201-255`). Нужны явный parser, usage и точка guard
до публикации, иначе отказ «не деплоить» происходит после observable mutation
remote checkout.

После рестарта `LLEN translate` и `HLEN unacked` не доказывают, что вернулась
**та же** задача: очередь может быть уже потреблена новым worker, а `LLEN`
считает только количество. Скрипт должен сохранить pre-deploy набор task id,
а затем сравнить его с `inspect().active()/reserved()` и результатами
`AsyncResult`; либо отказаться от обещания per-task postcondition и выводить
только честное предупреждение до `--force`.

Наконец, `inspect().active()` покрывает только активные сообщения. План должен
явно решить, допускается ли деплой при уже queued producer-задаче и как
оператор её увидит; это не тот же случай, что task в `unacked`.

### F6 (P1) — плану не хватает интеграционных проверок и user-visible cleanup

Проба из `analysis/probes` полезна как доказательство транспорта, но не
проверяет реальный путь `Docker → PID 1 supervisord → process group → celery`
или задачу с частично сохранёнными строками. Нужен интеграционный smoke именно
через `docker compose ... restart weblate` с контролируемым real
`auto_translate`, прерыванием после первой записи, последующей redelivery и
сравнением конечных target/state с непрерванным запуском.

Задача 2 меняет пользовательский API/UI. По `AGENTS.md` ей нужен entry в
верхней unreleased-секции `docs/changes.rst`; его нет. Если после F3 появится
новый POST endpoint, в том же изменении требуется ревизия
`docs/security/threat-model.rst`.

### F7 (P2) — тест маршрута проверяет конфигурацию, а не доставку

`app.conf.task_routes` — mapping конфигурации, не наблюдаемый результат
маршрутизации. Проверка «в `app.conf.task_routes` есть `translate`» дублирует
текст настройки и не защитит wildcard/precedence regression. Тесту следует
спросить Celery router для `fix_failing_checks` или реально опубликовать
сообщение в изолированный Redis и доказать, что оно попало в нужный список.

## Условия одобрения

1. Устранить F1: status/heartbeat schema, честная copy и консервативный
   timeout; удалить или полностью спроектировать replay-action.
2. Устранить F2: принять явную очередь/SLA-policy для mass fix, не переносить
   её в `translate` по умолчанию.
3. Переписать Task 3 согласно F5; обеспечить escape path до любой remote
   мутации.
4. Добавить end-to-end restart/redelivery/partial-write smoke, changelog и
   тест фактического routing result.
5. После исправлений снова проверить единый интеграционный smoke с планом
   `docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md`:
   его priority policy и shutdown policy не должны неявно менять очереди друг
   друга.

После этих правок базовый подход можно одобрить: отдельный `stopsignal=QUIT`
для worker, где есть явно выбранная acks-late гарантия, и границы
`20 < 60 < 90` дают простой, проверяемый способ убрать четырёхчасовое
ожидание.
