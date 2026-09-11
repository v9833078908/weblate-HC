<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Продюсерские задачи переживают перезапуск сервиса

**Дата:** 2026-09-11. **Статус:** переработан после ревью
`docs/product/reviews/2026-09-11-producer-tasks-survive-deploy-plan-review.md`;
готов к согласованию, реализация не начата, деплой не одобрен.

## Цель и выбранные решения

**Повод.** Деплой 11.09 пересоздал контейнер во время `auto_translate`
anvil-saga/fr. Из-за `acks_late=True` сообщение осталось в Redis `unacked`, но
SIGTERM воркера сменился на SIGKILL до kombu finalizer; оно стало видимым лишь
после `visibility_timeout` в четыре часа. Экран оставался на 73 % без
объяснения. Разовый возврат delivery tag снял инцидент; он описан в
`docs/operations/reports/2026-09-11-anvil-saga-fr-autotranslate-stall.md` и не
является продуктовым исправлением.

**Цель.** При обычном пересоздании сервиса активный пользовательский
автоперевод или массовое исправление возвращается из `unacked` в Redis до
окончания Docker grace period; страница продюсера показывает очередь, работу
или отсутствие обновлений правдиво, а deploy предупреждает об активной
автопереводной задаче *до* push и изменения checkout на VPS.

**Выбранные решения.**

1. Cold shutdown получает не только `celery-translate`, но и
   `celery-celery`. Там исполняются соответственно `auto_translate*` и
   остающийся в своей очереди `fix_failing_checks`; маршрутизация mass fix не
   меняется. Это сохраняет его SLA и priority-policy очереди `celery` из
   `docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md`.
2. Жизненность пользовательской задачи — закрытая cache-запись, а не вывод из
   `AsyncResult.state`: `PENDING` означает и «ещё стоит в очереди», и
   «запущенная доставка была восстановлена». UI показывает только `queued`,
   `running` или `no-update`, никогда не угадывает «деплой оборвал задачу».
3. `no-update` наступает не раньше 600 с с последнего heartbeat. Это больше
   четырёх попыток обычного LLM request по 120 с и трёх backoff до 30 с
   (`weblate/machinery/base.py:366-386`), поэтому нормальный retry не выглядит
   оборванной задачей. Текст этого статуса: «Нет обновлений уже 10 минут;
   задача могла продолжаться. Если состояние не изменится, обратитесь к
   администратору». Он не обещает автоматическое восстановление и не является
   ошибкой задачи.
4. Кнопки replay нет. Существующий `/api/tasks/<id>/` допускает только GET и
   DELETE; повторная публикация того же task id во время Redis redelivery дала
   бы дубликат. Автоматическое восстановление после штатного деплоя —
   единственный механизм повторного запуска в этом изменении.
5. Счётчик автоперевода — `updated / eligible` текущей delivery attempt:
   числитель увеличивается только после сохранения Unit, знаменатель — snapshot
   units, подходящих под запрос в начале попытки. На восстановленной delivery
   запускается новая попытка над оставшимися `state:empty`; UI сообщает, что
   запуск возобновлён, и не суммирует несопоставимые snapshots. Процент остаётся
   техническим progress по шагам движка, счётчик — пользовательским числом
   записанных строк.
6. `fix_failing_checks` уже публикует свой точный `done/total` в result meta и
   UI уже отображает его. Этот контракт не расширяется.

**Явные не-цели.** Отдельный Celery-контейнер; новые queue/worker для mass
fix; изменение priority очереди `translate`; перевод остальных задач на
`acks_late`; снижение `visibility_timeout`; ручной replay endpoint; постоянная
модель истории запусков.

## Наблюдаемые факты

Проба `analysis/probes/celery_shutdown_requeue.py` запускалась с установленными
celery 5.6.3/kombu 5.6.2 на изолированных Redis db/queue:

| Сценарий | После остановки | Результат |
| --- | --- | --- |
| `acks_late` + SIGTERM, затем SIGKILL | `unacked=1`, очередь пуста | задача ждёт visibility timeout |
| `acks_late` + SIGQUIT | очередь = 1, `unacked=0`, есть `Restoring` | сообщение переисполняется с тем же task id |
| Длинная задача без `acks_late` + SIGQUIT | очередь = 0 | delivery уже подтверждена и не восстанавливается |
| Короткая (3 с) задача без `acks_late` + SIGQUIT | задача `SUCCESS` | успевает завершиться |

Census текущих задач даёт три `acks_late=True`: `auto_translate`,
`auto_translate_component`, `fix_failing_checks`
(`weblate/trans/tasks.py:992-999,1130-1137,1256-1263`). Первые две уже идут в
`translate`; mass fix остаётся на `celery` (`weblate/settings_docker.py:1444-1454`).

Supervisor останавливает программу её `config.stopsignal`; проверенный
upstream-файл `/etc/supervisor/conf.d/celery-translate.conf` имеет отдельную
секцию `[program:celery-translate]`. Значит `stopsignal=QUIT` в этом файле —
точечный, поддерживаемый способ включить Celery cold shutdown. Границы
`20 < 60 < 90` означают: Celery ждёт 20 с до cancel, supervisor ждёт программу
до 60 с, Docker держит контейнер до 90 с.

## Общий контракт liveness

Новый cache record `task-liveness-<task_id>` создаётся **до публикации**
пользовательской задачи и хранится те же шесть часов, что
`task-meta`/`user-tasks`:

Запись содержит `enabled=True`, `started_at`, nullable `heartbeat_at` и
счётчик `attempt`.

- `auto_translation` (`weblate/trans/views/edit.py`) заранее выделяет UUID,
  сохраняет scope metadata и liveness record, затем публикует через
  `apply_async(task_id=...)` вместо `delay()`. При publish failure удаляет обе
  предварительные cache-записи. Это закрывает гонку, где быстрый worker начал
  task до регистрации на странице.
- Mass-fix view (`weblate/trans/views/search.py`) уже выделяет `task_id` до
  `apply_async`; он переносит `store_task_metadata`/liveness registration до
  публикации и в существующей exception-ветке освобождает lock **и** удаляет
  предварительные записи.
- `add_user_task` только добавляет текстовую запись в user list и подтверждает
  `enabled=True`; liveness record уже существует. Фоновый callsite не создаёт
  этот record.
- В начале `auto_translate` и `fix_failing_checks` helper атомарно увеличивает
  `attempt` и обновляет `heartbeat_at`. Их current progress paths вызывают
  helper вместе с `current_task.update_state`, поэтому активная работа имеет
  fresh heartbeat.
- `TasksViewSet.retrieve` читает record после обычной authorization
  `get_task()`, вычисляет `liveness` (`queued`, `running`, `no-update`) на
  сервере и возвращает его как optional поле. `completed=True` всегда
  перекрывает liveness. Сырые timestamps не выходят через API.
- `get_user_tasks` больше не выкидывает liveness-enabled task только потому,
  что Celery говорит `PENDING` старше `PENDING_TASK_MAX_AGE`; обычные старые
  записи без liveness сохраняют сегодняшний pruning-contract.
- `loader-bootstrap.js` отображает fixed translated copy для `queued` и
  `no-update`; при `running` сохраняет существующий экран. Он не добавляет
  кнопку, меняет `aria-live` текстом, а не только цветом.

## Задача 1. Восстановить acks-late задачи при штатной остановке

**Результат.** `auto_translate*` и `fix_failing_checks`, реально работавшие в
момент `docker compose up -d --build weblate`/`restart weblate`, возвращаются
в свою неизменённую Redis queue без четырёхчасового ожидания. Очереди и
приоритеты задач не меняются.

**Файлы и интерфейсы.**

- `weblate/settings_docker.py`: добавить
  `CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = get_env_int(
  "WEBLATE_CELERY_SOFT_SHUTDOWN_TIMEOUT", 20)` рядом с текущими настройками
  broker. Для worker, которые всё ещё получают SIGTERM, это no-op.
- `deploy/Dockerfile`: пока образ выполняется как root, append
  `stopsignal=QUIT` и `stopwaitsecs=60` в **каждый отдельный** upstream config
  `/etc/supervisor/conf.d/celery-translate.conf` и
  `/etc/supervisor/conf.d/celery-celery.conf`. Не заменять `command`, не
  применять ко всем `celery-*.conf` и не использовать `REMAP_SIGTERM`.
- `deploy/docker-compose.yml` и `dev-docker/docker-compose.yml`: добавить
  `stop_grace_period: 90s` только service `weblate`.
- `deploy/environment.example`: объяснить
  `WEBLATE_CELERY_SOFT_SHUTDOWN_TIMEOUT=20` и инвариант
  `timeout < stopwaitsecs < stop_grace_period`.
- `weblate/settings_docker.py`, `CELERY_TASK_ROUTES`: **не менять**.
  `fix_failing_checks` продолжает обслуживаться `celery-celery`.

**Действия.**

- [ ] Добавить timeout и окружение.
- [ ] Настроить SIGQUIT/60 с на двух названных supervisor-программах.
- [ ] Выставить Docker grace в обоих compose.
- [ ] Дополнить пробу проверкой обоих имён очереди: `translate` и `celery`.
  Она должна подтвердить `acks_late` redelivery и сохранить отдельный случай
  delivery без `acks_late` как границу гарантии.

**Проверка.**

1. Запустить `analysis/probes/celery_shutdown_requeue.py`: SIGQUIT даёт
   `Restoring`, одну запись в исходной queue и пустые `unacked`/
   `unacked_index`; SIGTERM+SIGKILL остаётся control.
2. Собрать dev image, выполнить `WEBLATE_PORT=3001 ./rundev.sh`, затем
   проверить `supervisorctl status` и effective config обоих workers:
   `celery-translate`/`celery-celery` получают QUIT при остановке, остальные
   программы — прежний SIGTERM.
3. Интеграционный smoke через реальный
   `docker compose -f dev-docker/docker-compose.yml restart weblate`: запустить
   на локальном fixture `auto_translate` с `auto_source=others`, остановить
   после первой сохранённой строки, дождаться новой delivery того же task id и
   сравнить конечные target/state/history с непрерванным fixture. Уже
   сохранённая строка не перезаписывается (`q=state:empty`), оставшиеся
   обрабатываются. Повторить аналогично с одним actual mass-fix policy и его
   `lock_key`: не появляется второе изменение и не освобождается чужой lease.

## Задача 2. Сделать статус и счётчик пользовательской задачи правдивыми

**Результат.** Автопереводная плашка честно различает «ожидает worker»,
«обновляется», «нет обновлений 10 минут»; её счётчик показывает записи,
а не сетевые шаги. Mass fix сохраняет уже работающий `done/total`.

**Файлы и интерфейсы.**

- `weblate/utils/celery.py`: key builders, pre-publication registration,
  cleanup и heartbeat helpers рядом с `get_task_metadata_key`/`add_user_task`;
  `get_user_tasks` сохраняет liveness-enabled PENDING tasks до обычного
  metadata TTL.
- `weblate/trans/views/edit.py` и `weblate/trans/views/search.py`: сначала
  выделить/зарегистрировать task id, затем `apply_async`; publish failure
  удаляет registration вместе с уже существующим cleanup lock.
- `weblate/trans/tasks.py`: в начале `auto_translate` и
  `fix_failing_checks`, а также в их существующих progress paths, touch record.
- `weblate/trans/autotranslate.py`: один attempt-local accumulator,
  принадлежащий `BatchAutoTranslate` и разделяемый дочерними
  `AutoTranslate`. До первой мутации он строит complete permission-filtered
  `eligible` snapshot; после `AutoTranslate.update()` добавляет один `updated`.
  `get_task_meta()` всех вложенных progress updates использует один accumulator,
  поэтому outer progress не затирает `done/total` переводом одного языка.
  Redelivery создаёт новую попытку и snapshot только оставшихся единиц.
- `weblate/api/serializers.py`/`weblate/api/views.py`: `TaskSerializer`
  получает optional `liveness`; `retrieve()` вычисляет enum на сервере после
  `get_task()` и оставляет `result` совместимым. Для счётчика serializer не
  расширяется: существующий `result.done/result.total` — единый контракт UI.
- `weblate/static/loader-bootstrap.js`: обрабатывает новый enum; готовый
  `done/total` renderer остаётся. `weblate/templates/message.html` остаётся
  semantic live region; при необходимости copy меняется через её
  `.task-message`, не `innerHTML`.
- Тесты: `weblate/api/tests.py`,
  `weblate/trans/tests/test_autotranslate.py`,
  `weblate/trans/tests/test_fix_check_task.py` и точечный JS test по принятому
  frontend-паттерну.

**Действия.**

- [ ] Ввести pre-publication record, cleanup при publish failure, server-side
      enum и pruning-exception без изменения authorization: project-language
      task продолжает быть доступен только по `user_id` в `task-meta`.
- [ ] Перевести auto-translation с `delay()` на `apply_async(task_id=...)`,
      чтобы heartbeat при старте никогда не обгонял регистрацию.
- [ ] Вызвать heartbeat при старте и current progress, не добавляя global
      `task_prerun` signal для всех Celery-задач.
- [ ] Добавить attempt-local aggregate `updated/eligible` только в automatic
      translation; не переиспользовать `progress_steps`, потому что judge
      измеряет worst-case calls, а не строки.
- [ ] Отобразить enum текстом и сохранить existing error/result rendering.
- [ ] Не добавлять POST/replay action.

**Регрессионные проверки.**

- UUID, metadata и liveness record существуют до `apply_async`; simulated
  publish failure удаляет их и не оставляет task в user list. Первый heartbeat
  сразу после publish даёт `running`, а не теряется гонкой.
- Новая зарегистрированная задача без heartbeat и спустя 30+ минут остаётся в
  user task list с `liveness=queued`; старая обычная PENDING task по-прежнему
  удаляется после `PENDING_TASK_MAX_AGE`.
- Первый heartbeat даёт `running`; heartbeat старше 600 с даёт `no-update`;
  `completed=True` не даёт ни один из трёх running status.
- Один artificial LLM retry дольше 180 с, но короче 600 с, остаётся `running`.
- Auto-translation из нескольких translations показывает суммарные
  `updated/eligible`; judge с большим числом сетевых calls не меняет
  denominator; delivery после restart начинает новый count только для
  оставшихся `state:empty` units.
- API access к чужой project-language task остаётся 404; liveness record не
  расширяет область видимости. UI assertion проверяет текст и `aria-live`, а
  не класс цвета.

## Задача 3. Остановить deploy до того, как он прервёт активную задачу

**Результат.** По умолчанию `./deploy/vps.sh deploy` отказывается до `git push`
и remote `git reset`, если `celery-translate` исполняет `auto_translate*`.
`--force` — явный, документированный обход с выводом task id/name/возраста.
Queued сообщения `translate` только отображаются: Redis переживает контейнер,
и они не являются прерываемыми active tasks.

**Файлы и интерфейсы.**

- `deploy/vps.sh`: полноценный parser только для `--build` и `--force`,
  неизвестный флаг — usage/exit 2. `--force` не означает build.
- `deploy/vps.sh`: до `git push` выполнить через уже работающий
  `docker exec … weblate shell` read-only inspection `app.control.inspect()`.
  Отобрать `weblate.trans.tasks.auto_translate` и
  `auto_translate_component` из active task records и вывести id, name,
  `time_start`; broker `LLEN translate` вывести отдельной предупреждающей
  строкой. Не полагаться на новую management command: её ещё нет в running
  image до первого deploy.
- После deploy выводить только честные факты: health/login/revision и число
  queued messages. Скрипт не заявляет, что конкретный task id восстановился:
  он мог уже быть принят новым worker. Задача и UI из Task 2 — место для
  наблюдения redelivery.

**Действия.**

- [ ] Разобрать аргументы до расчёта target; `--build` сохраняет сегодняшний
      выбор action, `--force` только снимает preflight refusal.
- [ ] Выполнить preflight после VPN/SSH readiness, но до push/reset.
- [ ] При active auto-translation завершить с non-zero и дать оператору два
      точных варианта: дождаться completion или повторить с `--force`.
- [ ] При `--force` записать в deploy log, какие task id могли быть
      восстановлены; не манипулировать Redis вручную.

**Проверка.**

- Fixture shell/command test подменяет inspection: active auto-translation
  блокирует без `--force`, queued-only не блокирует, `--force` продолжает,
  неизвестный флаг не запускает `git push`.
- `shellcheck deploy/vps.sh` зелёный.
- Dev smoke с активным `auto_translate`: normal deploy command прекращается
  до remote action; force path фиксирует предупреждение, перезапускает
  контейнер и Task 1/2 показывают восстановление без ручного Redis restore.

## Задача 4. Документация и совместная приёмка

**Результат.** Эксплуатационный и пользовательский контракт не остаётся только
в исходниках, а оба Celery-плана проверяются вместе.

**Действия и проверка.**

- [ ] `docs/changes.rst`, верхняя unreleased-секция: пользовательская
      автопереводная задача восстанавливается после штатного перезапуска;
      плашка показывает очередь/отсутствие обновлений и число записанных
      строк. Не обещать exactly-once delivery.
- [ ] `AGENTS.md`, Development environment: worker reload distinction,
      `acks_late` recovery через QUIT, timeout ordering и правило не делать
      Redis restore вручную, пока доступен штатный deploy recovery.
- [ ] Обновить этот план после реализации: commit SHA, целевые тесты,
      результаты транспортной и Docker-smoke проб, deploy status.
- [ ] Единый dev smoke после слияния с
      `docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md`:
      priority-0 interactive task на `celery` опережает background priority-3;
      активный auto-translation восстанавливается; `fix_failing_checks` всё
      ещё в `celery` и не занимает worker `translate`.
- [ ] `uv run prek run --files` на изменённых файлах, целевые pytest и
      `shellcheck deploy/vps.sh` зелёные.

`docs/security/threat-model.rst` в этой версии не меняется: новый публичный
endpoint, permission или mutation не добавляется. Любое возвращение replay
POST немедленно делает threat-model review частью его отдельного изменения.

## Риски и восстановление

- Cold shutdown меняет окно для задач без `acks_late` в `celery-celery`, но не
  даёт им новую гарантию доставки: подтверждённое сообщение не может вернуться
  в очередь. Их контрольный случай остаётся в пробе; короткая задача получает
  больше шансов завершиться, чем при сегодняшнем SIGKILL.
- `fix_failing_checks` после restart может быть redelivered с тем же id. Его
  existing `lock_key` должен остаться собственностью этой доставки; partial
  mutation smoke обязателен до деплоя.
- `no-update` — признак отсутствия update, не падения. Это сознательно
  консервативнее ложного «прервано» при медленном LLM.
- При `--force` активная задача прекращается сознательно; recovery гарантирует
  at-least-once delivery, не exactly-once. Автоперевод безопасен для уже
  записанных строк благодаря `q=state:empty` и
  `overwrite_existing=False`.
- `stop_grace_period: 90s` удлиняет аварийную остановку контейнера максимум на
  это время.

## Зависимости и порядок

Задача 1 первой: она меняет delivery contract. Задача 2 зависит от неё только
в redelivery-smoke, но её code contract независим; после фикса общего
liveness интерфейса может идти параллельно. Задача 3 зависит от решения
Задачи 1, потому что force-copy должна обещать только подтверждённое recovery
поведение. Задача 4 последняя.

Реализация требует отдельного согласования этого плана. Любой production
deploy требует отдельного `DEPLOY-OK`.
