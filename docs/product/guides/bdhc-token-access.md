# Доступ локального Weblate к API БДХК через токен

Backend использует read-only фасад `http://192.168.0.180:19101`.
Основной API БДХК остаётся на `127.0.0.1:19100`; порт `18501` на сервере
БДХК принадлежит интерфейсу и не используется для запросов API.

Это настройка доступа из контейнера. Подстановка карточки в визард
описывается отдельно в
`docs/product/plans/2026-10-01-bdhc-wizard-prefill.md`.

## Secret и конфигурация

Для текущей машины raw-токен установлен вне checkout, в WSL Ubuntu:
`/home/dr/.config/weblate/secrets/bdhc_readonly_token`. Каталог имеет права
`0700`, файл — `0600`, владелец UID/GID `1000:1000`, совпадающий с процессом
локального контейнера. Файл содержит только токен и завершающий перевод строки.

`dev-docker/docker-compose.bdhc.yml` добавляет контейнеру `weblate` переменные
`BDHC_API_BASE_URL`, `BDHC_API_TOKEN_FILE` и read-only bind mount на
`/run/secrets/bdhc_readonly_token`. Содержимое секрета не записывается в
Compose, environment или Git. Отсутствующий host-файл приводит к ошибке
запуска, а не созданию каталога вместо файла.

Игнорируемый Git файл `dev-docker/.env.local` содержит только несекретные
параметры этой машины:

```dotenv
BDHC_API_BASE_URL=http://192.168.0.180:19101
BDHC_API_TOKEN_HOST_FILE=/home/dr/.config/weblate/secrets/bdhc_readonly_token
```

## Запуск потребителя

Работающий локальный экземпляр использует Compose project
`producer-console-test`, checkout `.worktrees/producer-console-wave0` и
порт Weblate `18501`. Из WSL Ubuntu:

```bash
cd /mnt/d/Weblate/weblate-HC/.worktrees/producer-console-wave0/dev-docker
USER_ID=1000 GROUP_ID=1000 WEBLATE_PORT=18501 docker compose \
  -p producer-console-test \
  --env-file .env \
  --env-file /mnt/d/Weblate/weblate-HC/dev-docker/.env.local \
  -f docker-compose.yml \
  -f /mnt/d/Weblate/weblate-HC/dev-docker/docker-compose.bdhc.yml \
  up -d --no-deps weblate
```

Команда пересоздаёт только потребителя; PostgreSQL, Redis, maildev и
production БДХК не перезапускаются. При последующих Compose-запусках
используйте тот же override: обычный запуск без него может удалить mount
и переменные БДХК. Перезапуск работающего экземпляра требует разрешения;
команда здесь описывает конфигурацию, а не даёт постоянного разрешения.

## Проверка доступа

Из WSL выполните проверку файла без вывода токена:

```bash
docker exec producer-console-test-weblate-1 sh -c \
  'test -r "$BDHC_API_TOKEN_FILE" && test -s "$BDHC_API_TOKEN_FILE" && echo token_file=readable'
```

Для API используйте backend Python: читайте путь из `BDHC_API_TOKEN_FILE`,
отправляйте `Authorization: Bearer <token>` и `Accept: application/json`.
Допустимы только четыре GET-маршрута:

- `/api/v1/games?q=Lord%20Ambermaze&limit=5`;
- `/api/v2/games/resolve?ref=031d9a9f-326f-4d06-ab87-e095af11f446`;
- `/api/v1/games/031d9a9f-326f-4d06-ab87-e095af11f446`;
- `/api/v2/games/031d9a9f-326f-4d06-ab87-e095af11f446/knowledge`.

Успех: все четыре ответа `200`, выбранный UUID совпадает, название —
`Lord Ambermaze`, версия knowledge — `12.7`, `context_hash` непустой.
Без токена разрешённые маршруты возвращают `401`. Health endpoint
`/health` должен сообщать `proxy=ok`, `upstream.app=ok`,
`upstream.database=ok`. Полный ответ карточки и токен не выводятся в логи.

Чтение не изменяет бизнес-данные карточки; штатная авторизация обновляет
служебное `api_clients.last_used_at`. Токен не передаётся браузеру.
Отсутствие фасада не устраняется перезапуском основного API: его
guarded-запуск и пока отсутствующий автозапуск после reboot относятся к
эксплуатации БДХК.

## Замена токена

Оператор устанавливает новый raw-файл с теми же владельцем и правами,
затем пересоздаёт только потребителя с тем же Compose override и повторяет
четыре GET-проверки. Bind mount отдельного файла после атомарной замены
host-файла может продолжать указывать на старый inode до пересоздания
контейнера. Старый ключ отзывается владельцем БДХК только после успешной
проверки нового. Не используйте отзыв ключа как часть диагностики доступа.

## Проверка на текущей машине

2026-10-01 настройка применена к `producer-console-test-weblate-1`.
Из этого контейнера все четыре авторизованных GET вернули `200`:
поиск, каталог, resolve и knowledge. Проверены `Lord Ambermaze`,
ожидаемый `title_id`, `schema_version=12.7` и непустой `context_hash`.
Mount имеет `RW=false`, файл — `0600`, UID `1000`.

Время старта PostgreSQL, Redis и maildev до/после применения не изменилось.
Production БДХК не пересоздавался и не перезапускался этой настройкой.
Проверка подтверждает доступ к API из backend, но не завершение интеграции
визарда, описанной в отдельном плане.
