# Продюсерские задачи переживают перезапуск сервиса

Статус: предложен, ожидает утверждения.
Повод: `docs/operations/reports/2026-09-11-anvil-saga-fr-autotranslate-stall.md`
(автоперевод anvil-saga/fr убит деплоем в 10:21 UTC, висит «73 %» до ~14:23 UTC).

Три независимых дефекта, каждый чинится отдельно и проверяется отдельно.
Порядок задач = порядок ценности: 1 убирает четырёхчасовую дыру, 2 убирает
ложный экран, 3 убирает сам обрыв.

## Задача 1. Прерванная задача возвращается в очередь сразу, а не через 4 часа

Сейчас: SIGTERM → celery начинает **тёплое** завершение и ждёт задачу (часы) →
через 10 с supervisor шлёт SIGKILL → интерпретатор умирает без финализаторов →
сообщение остаётся в Redis `unacked`, и его вернёт только сканер по
`visibility_timeout` = 4 ч (`weblate/settings_docker.py:1434`).

Механизм решения (проверен в установленных celery 5.6.3 / kombu 5.6.2, не по памяти):

- `REMAP_SIGTERM=SIGQUIT` (`billiard/common.py:34-41`) переводит SIGTERM на
  обработчик **холодного** завершения (`celery/apps/worker.py:411`).
- Холодное завершение зовёт `consumer.cancel_active_requests()`
  (`celery/apps/worker.py:359`), а для `acks_late`-задачи это `Request.cancel`
  → `mark_as_retry` **без** `acknowledge()` (`celery/worker/request.py:439-455`).
- Чистый выход процесса срабатывает финализатор
  `QoS._on_collect = Finalize(self, self.restore_unacked_once)`
  (`kombu/transport/virtual/base.py:193`), который возвращает неподтверждённое
  сообщение в очередь немедленно.
- `CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT` (celery ≥ 5.5,
  `celery/worker/worker.py:412-435`) даёт короткой задаче шанс доработать до
  отмены.

Изменения:

1. `deploy/environment.example` и `deploy/docker-compose.yml`,
   `dev-docker/docker-compose.yml`: `REMAP_SIGTERM: SIGQUIT` в окружении
   сервиса `weblate`.
2. `weblate/settings_docker.py`: `CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT =
   get_env_int("WEBLATE_CELERY_SOFT_SHUTDOWN_TIMEOUT", 20)` рядом с
   существующим блоком celery (строка 1426+).
3. `deploy/Dockerfile`: дописать `stopwaitsecs` и `stopasgroup`/`killasgroup`
   в `/etc/supervisor/conf.d/celery-*.conf` (файлы приходят из upstream-образа,
   правим `sed`-ом в слое сборки, значение > soft timeout, например 60).
4. `deploy/docker-compose.yml` и `dev-docker/docker-compose.yml`:
   `stop_grace_period: 90s` у сервиса `weblate`, иначе docker убьёт контейнер
   раньше, чем supervisor дождётся воркеров.

Почему это безопасно: сообщение возвращает умирающий воркер, `visibility_timeout`
остаётся 4 ч, второго экземпляра не появляется. Повторный прогон идемпотентен
по построению: `q=state:empty`, `overwrite_existing=False` — уже переведённые
строки в выборку не попадают (в инциденте: 4530 записано, 4952 осталось).

Проверка: в dev-стеке запустить автоперевод компонента, дождаться прогресса,
`docker compose restart weblate`; ожидание — в логах воркера `Initiating Soft
Shutdown`, затем `Restoring N unacknowledged message(s)`, задача **с тем же
task id** снова уходит в работу в пределах минуты, `zrange unacked_index` пуст.
Контрольный замер до фикса — те же шаги на текущем коде: задача не возвращается.

## Задача 2. Экран прогресса отличает «идёт» от «оборвано» и считает строки

Сейчас `TasksViewSet.retrieve` (`weblate/api/views.py:4955-4967`) отдаёт только
`completed`/`progress`/`result`/`log`, а `get_task_progress`
(`weblate/utils/celery.py:252`) возвращает последний сохранённый процент. У
задачи, чей воркер мёртв, состояние навсегда `PROGRESS`, и
`loader-bootstrap.js:1798` честно рисует замороженный бар без срока и без ошибки.

Изменения:

1. Пульс: `AutoTranslate.set_progress` (`weblate/trans/autotranslate.py:272`) и
   `progress_callback` массового исправления (`weblate/trans/tasks.py:1321`)
   пишут `cache.set(f"task-heartbeat-{task_id}", {"time": …, "hostname": …})`
   с TTL уровня `TASK_METADATA_TTL`.
2. API: `TaskSerializer` (`weblate/api/serializers.py:4150`) получает поля
   `state`, `stale`, `done`, `total`; `retrieve` считает
   `stale = not completed and heartbeat is not None and now - heartbeat > 180`,
   а при `state == "RETRY"` отдаёт `stale=False` с пометкой перезапуска.
3. Фронт: `weblate/static/loader-bootstrap.js` (блок с `data-task`) при `stale`
   красит плашку в `alert-warning`, пишет «Выполнение прервано перезапуском
   сервиса; задача возобновится автоматически» и показывает кнопку повторного
   запуска; при `RETRY` — «Перезапускается». Требования `ACCESSIBILITY.md`
   соблюдаются: текстовая формулировка, не только цвет, `aria-live` уже есть.
4. Строки вместо голого процента: `set_progress` кладёт в meta `done`/`total`
   единиц, шаблон `weblate/templates/message.html` показывает «4530 из 9482».
   В инциденте бар показывал 73 % при фактических 47,8 %.

Проверка: юнит-тест на `retrieve` с подделанным heartbeat (свежий → `stale`
false, просроченный → true) и на пересчёт `done/total`; вручную — dev-стек,
убить воркер `supervisorctl stop celery-translate`, убедиться, что плашка в
течение трёх минут переходит в предупреждение, а не висит.

## Задача 3. Деплой не запускается молча поверх работающей задачи

Изменения:

1. Новая management-команда `weblate/utils/management/commands/running_tasks.py`
   (рядом с существующей `celery_queues.py`): печатает JSON активных задач —
   имя, id, возраст, инициатор, scope — по `app.control.inspect().active()`.
2. `deploy/vps.sh`, `deploy_stack()` перед `docker compose up`: вызывает её
   через `docker exec`, и если в очереди `translate` есть активная задача,
   печатает список и требует подтверждения либо `--force`; по умолчанию деплой
   не стартует.
3. В конце деплоя выводит те же задачи с пометкой «возобновлены» — после
   задачи 1 это проверяемое утверждение, а не обещание.

Проверка: на dev-стеке `deploy`-путь не воспроизводится, поэтому проверяем
саму команду (`weblate running_tasks --json` при запущенном автопереводе
возвращает непустой список) и shellcheck-прогон `deploy/vps.sh`.

## Вне объёма

- Вынос Celery в отдельный контейнер. Правильно, но это отдельная работа по
  инфраструктуре; задачи 1-3 дают нужный результат в текущей однокотейнерной
  схеме.
- Постоянная история запусков автоперевода (страница вида judge-run).
  Восстановление плашки на любой странице уже работает через `add_user_task`
  (`weblate/utils/celery.py:96`), отдельная история — следующий шаг.
- Снижение `visibility_timeout`: опасно, именно оно защищает от дублей.

## Риски

- `REMAP_SIGTERM=SIGQUIT` меняет поведение **всех** воркеров: длинные задачи
  теперь отменяются через soft timeout вместо ожидания. Для задач без
  `acks_late` отмена означает потерю; проверить список долгих задач
  (`weblate/trans/tasks.py`: автоперевод, массовое исправление, judge — все
  `acks_late=True, reject_on_worker_lost=True`).
- Увеличенный `stop_grace_period` удлиняет деплой на время soft timeout.
- Поднятый `stopwaitsecs` требует, чтобы в образе не осталось программ,
  которые не умирают по SIGTERM, иначе каждый рестарт станет на минуту дольше.
