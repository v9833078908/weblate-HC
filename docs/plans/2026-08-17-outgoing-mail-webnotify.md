# План: исходящая почта прода через webnotify@herocraft.com

> **Статус:** выполнен 2026-08-17. Прод отправляет через
> `smtp.yandex.ru:465` (`From: Hero Craft Localization
> <webnotify@herocraft.com>`); `sendtestemail` и живые сбросы пароля
> для `i.efimov@` и `ivan.belov@` подтверждены `AuditLog(sent-email)`
> (10:37, 10:38 UTC). Все 7 пользователей с почтой и входом, `VerifiedEmail`
> у ivanbelov появился сам. Откат — `.env.bak-2026-08-17` на сервере.

> **Цель пользователя:** прод `l10n.herocraft.com` действительно отправляет
> письма (инвайты, сброс пароля, подтверждение адреса, письма админам,
> дайджесты) от `webnotify@herocraft.com`; существующие пользователи
> «добиты» до состояния, в котором самостоятельный сброс пароля работает.
>
> **Триггер:** пользователь `ivan.belov@herocraft.com` был создан 2026-08-14
> и не получил ни одного письма.

## Принятые решения

1. **Транспорт:** Yandex 360, ящик `webnotify@herocraft.com`,
   `smtp.yandex.ru:465` (implicit SSL). Пароль приложения получен от
   пользователя и **уже проверен** (только `AUTH`, без отправки) изнутри
   прод-контейнера: `AUTH OK` и на 465/SSL, и на 587/STARTTLS.
2. **Отправитель:** голый адрес `webnotify@herocraft.com` в
   `WEBLATE_SERVER_EMAIL` и `WEBLATE_DEFAULT_FROM_EMAIL` (меняются с
   `i.efimov@herocraft.com`; иначе Yandex отклонит письмо — `From` не
   принадлежит аутентифицированному ящику). Display-name в письмах
   появляется автоматически из `SITE_TITLE` (`notifications.py:396-397`:
   `formataddr((from_name, settings.DEFAULT_FROM_EMAIL))`, в проде
   `SITE_TITLE=Hero Craft Localization`). **Не вкладывать display-name в
   сам env**: тогда `formataddr` обернёт его второй раз и `From` станет
   `Hero Craft Localization <HCGameLoc <webnotify@...>>` — RFC-битый
   заголовок (воспроизведено на ревью плана), конверт при этом валиден,
   SMTP примет, а фильтры получателя зарежут. `WEBLATE_ADMIN_EMAIL=i.efimov@`
   не меняется — это получатель.
3. **Секрет** живёт только в `/srv/hcgameloc/deploy/.env` на сервере
   (не в git).
4. **Проверочный получатель:** `i.efimov@herocraft.com`.
5. **Простой:** пересоздание контейнера `weblate` разрешено в рабочее время.
6. **`ENABLE_HTTPS` отложен** — по замерам доступа (шаг 5) включение сломало
   бы реальных клиентов; остаётся отдельной задачей.

## Исходное состояние (замерено 2026-08-17)

Отправка не работает вообще. В `/srv/hcgameloc/deploy/.env`:

| ключ | значение | следствие |
| --- | --- | --- |
| `WEBLATE_EMAIL_HOST` | `localhost` | Django сдаёт письмо MTA внутри контейнера `hcgameloc-weblate-1`, где MTA нет |
| `WEBLATE_EMAIL_PORT` | `25` | — |
| `WEBLATE_EMAIL_USE_TLS` | `0` | явный `0` отключает автодетект TLS в `get_email_config()` (`weblate/utils/environment.py:297-318`), то есть даже с верным хостом ушло бы в открытом виде |
| `WEBLATE_EMAIL_HOST_USER` | пусто | без аутентификации |
| `WEBLATE_SERVER_EMAIL` / `WEBLATE_DEFAULT_FROM_EMAIL` | `i.efimov@herocraft.com` | не совпадёт с SMTP-аккаунтом `webnotify@` (см. риск 1) |

Проверено изнутри контейнера: `smtplib.SMTP("localhost", 25)` и
`django.core.mail.get_connection()` → `ConnectionRefusedError [Errno 111]`.

Что при этом **не** сломано и переделывать не нужно:

- Рассыльщик живой: в контейнере работают `celery worker --queues=notify`
  (concurrency 2), остальные очереди и `celery beat`. Дайджесты не уходили
  только из-за SMTP.
- Подписки на месте: 7 активных не-бот пользователей, 70 подписок
  (по 10 дефолтных на каждого).
- Egress не заблокирован: `smtp.yandex.ru` доступен из контейнера,
  на `:587` приходит баннер `220 mail-nwsmtp-...yandex.net`.
- **Креды уже проверены** изнутри прод-контейнера, отправка не
  производилась, только `AUTH`: `webnotify@herocraft.com` логинится и на
  `465/SSL`, и на `587/STARTTLS` → `AUTH OK` на обоих.
- Домен под Yandex 360: `MX 10 mx.yandex.ru`, SPF `v=spf1 redirect=_spf.yandex.net`,
  DMARC `p=none`. Отправка через `smtp.yandex.ru` проходит SPF и получает
  DKIM Яндекса без правок DNS.

Состояние пользователей: у всех 7 есть пароль и был вход. У
`ivan.belov@herocraft.com` (id 14, username `ivanbelov`) пароль установлен
вручную 2026-08-17, но `VerifiedEmail` пуст и social auth нет → **сброс
пароля по почте у него не сработает**, таблица `Invitation` пуста.

## Целевое состояние

1. Weblate отправляет через `smtp.yandex.ru:465` (SSL), аккаунт
   `webnotify@herocraft.com`; в письмах `From: Hero Craft Localization
   <webnotify@herocraft.com>` (имя из `SITE_TITLE`, адрес голый в env).
2. `weblate sendtestemail i.efimov@herocraft.com` доходит.
3. Сброс пароля и подтверждение адреса реально уходят; ссылки остаются
   `http://l10n.herocraft.com/...` до отдельной задачи про `ENABLE_HTTPS`
   (шаг 5), а edge редиректит их на `https`.
4. Иван Белов может сам сбросить пароль письмом, временный пароль от
   2026-08-17 больше не нужен.

## Как выполнять (сессия без контекста этой)

Доступ к проду — только через VPN-шлюз из `deploy/`:

```sh
cd deploy
./vps.sh status                 # поднимет hc-vpn-gw при необходимости
./vps.sh ssh "<команда на VPS>"
./vps.sh root /tmp/script.sh    # локальный скрипт под root на VPS
```

Прод-инстанс: хост `192.168.0.233` (egress NAT `162.55.180.78`), контейнер
`hcgameloc-weblate-1`, репозиторий `/srv/hcgameloc`, локальный порт
`127.0.0.1:8081`, edge с TLS — `192.168.0.210`.

Django-код с кириллицей запускать heredoc'ом в `weblate shell`:

```sh
B64=$(base64 < /tmp/script.py | tr -d '\n')
./vps.sh ssh "echo $B64 | base64 -d | docker exec -i hcgameloc-weblate-1 weblate shell"
```

Пароль приложения `webnotify@` пользователь передал в сессии 2026-08-17;
если он утерян — запросить снова, в репозиторий он не попадает.
`deploy/vps.sh deploy` для этой задачи не нужен: правится только `.env`
на сервере, а он не в git.

## Шаги

### 1. Правка `/srv/hcgameloc/deploy/.env` (только на сервере, в git не входит)

```
WEBLATE_EMAIL_HOST=smtp.yandex.ru
WEBLATE_EMAIL_PORT=465
WEBLATE_EMAIL_USE_SSL=1
WEBLATE_EMAIL_USE_TLS=0
WEBLATE_EMAIL_HOST_USER=webnotify@herocraft.com
WEBLATE_EMAIL_HOST_PASSWORD=<app password, уже получен>
WEBLATE_SERVER_EMAIL=webnotify@herocraft.com
WEBLATE_DEFAULT_FROM_EMAIL=webnotify@herocraft.com
```

Оба адресных поля — голые, без display-name (см. «Принятые решения», п. 2:
двойная обёртка `formataddr` в `notifications.py:397` делает RFC-битый
`From`; имя подставится из `SITE_TITLE` автоматически).

`WEBLATE_ADMIN_EMAIL=i.efimov@herocraft.com` не меняется: это получатель
писем об ошибках, а не отправитель. Перед правкой — `cp .env .env.bak-2026-08-17`
для мгновенного отката.

Порт 465 + `USE_SSL=1` выбран вместо 587: `AUTH OK` на обоих, но implicit TLS
не зависит от `STARTTLS`-переговоров, и `get_email_config()` при
`USE_SSL=1` сам ставит дефолтный порт 465, так что конфиг остаётся
непротиворечивым.

### 2. Применение

`env_file: .env` (`deploy/docker-compose.yml:51`) означает, что `docker restart`
новую переменную **не** увидит — нужно пересоздание:

```sh
cd /srv/hcgameloc/deploy && docker compose up -d weblate
```

Затрагивается только сервис `weblate` (`database`/`cache` живут), простой
~1-2 минуты. `deploy/vps.sh deploy` здесь не используется: он тянет коммит
из git, а `.env` в git нет.

### 3. Проверка транспорта

1. Переменные доехали в контейнер:

   ```sh
   docker exec hcgameloc-weblate-1 python -c "
   from django.conf import settings
   for k in ('EMAIL_HOST','EMAIL_PORT','EMAIL_HOST_USER','EMAIL_USE_TLS',
             'EMAIL_USE_SSL','DEFAULT_FROM_EMAIL','SERVER_EMAIL'):
       print(k, getattr(settings, k))
   print('password set:', bool(settings.EMAIL_HOST_PASSWORD))"
   ```

   Ожидаемое: `EMAIL_HOST=smtp.yandex.ru`, `EMAIL_PORT=465`,
   `EMAIL_HOST_USER=webnotify@herocraft.com`, `EMAIL_USE_TLS=False`,
   `EMAIL_USE_SSL=True`, оба адресных поля — голый
   `webnotify@herocraft.com`, `password set: True`.
2. **`From` валиден на обоих путях отправки.** Через `weblate shell`:

   ```python
   # путь Notification (notifications.py:396-397) — двойная обёртка:
   from django.conf import settings

   # путь Notification (notifications.py:396-397) —双重 обёртка:
   hdr = formataddr((settings.SITE_TITLE, settings.DEFAULT_FROM_EMAIL))
   print("notification From:", hdr, "| addr:", parseaddr(hdr)[1])
   # путь sendtestemail (Django) — сырая строка:
   print("sendtest addr:", parseaddr(settings.DEFAULT_FROM_EMAIL)[1])
   ```

   Ожидаемое: первый — `Hero Craft Localization <webnotify@herocraft.com>`
   c `addr = webnotify@herocraft.com`; второй — тот же addr. Если addr
   не содержит `@` — env содержит display-name, конфигурация неверна
   (возврат к «Принятым решениям», п. 2).
3. `docker exec hcgameloc-weblate-1 weblate sendtestemail i.efimov@herocraft.com`
   — нулевой код возврата, без исключения.
4. Пользователь подтверждает, что письмо пришло, и сообщает папку
   (Inbox / Спам) — единственная проверка доставки, недоступная изнутри.

### 4. Транзакционная почта на живом сценарии

Сброс пароля для `i.efimov@herocraft.com` (проверочный получатель; на чужие
адреса реальные письма не отправляем).

**Капча.** `REGISTRATION_CAPTCHA=True` в проде (проверено; env не
переопределяет, дефолт `weblate/accounts/defaults.py:16`). `ResetForm` →
`EmailForm` → `CaptchaForm` (`forms.py:657,808`): поле `captcha` обязательно,
вопрос лежит в сессии и рендерится в label как `What is X + Y?`
(`forms.py:547-553`; генерация — `captcha.py:62-73`, числа 1-10, ответ
неотрицательный). Altcha скрыта (`is_altcha_available()` = False при
`ENABLE_HTTPS=False`, `forms.py:483-490`), отправлять её не нужно.

```sh
# на VPS, в одной cookie-сессии
J=$(mktemp)
# 1) страница с формой: csrf-токен + вопрос капчи из label
curl -s -c "$J" -H 'Host: l10n.herocraft.com' http://127.0.0.1:8081/accounts/reset/ > /tmp/reset.html
CSRF=$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' /tmp/reset.html | head -1 | sed 's/.*value="//;s/"//')
Q=$(grep -oE 'What is [0-9]+ [-+*] [0-9]+' /tmp/reset.html | head -1)
ANS=$(python3 -c "print(eval('$Q'.replace('What is ','')))")
# 2) POST с ответом
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' \
  -b "$J" -c "$J" -H 'Host: l10n.herocraft.com' \
  -e http://127.0.0.1:8081/accounts/reset/ \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" \
  --data-urlencode "email=i.efimov@herocraft.com" \
  --data-urlencode "captcha=$ANS" \
  http://127.0.0.1:8081/accounts/reset/
```

Ожидаемое:

- `302` (или редирект на `email-sent`); `200` с текстом формы = капча/CSRF
  не сошлись, письмо не ушло.
- В логах — задача **`weblate.accounts.tasks.send_mails`** в очереди
  `notify` (`pipeline.py:210` → `notifications.py:1473` → `tasks.py:238-248`;
  маршрут `settings_docker.py:1442`), без исключения. Имени `notify_*` у
  этой задачи нет — не искать его.
- В БД — `AuditLog(activity="sent-email", email="i.efimov@herocraft.com")`
  (`pipeline.py:202-207`): пишется ровно в момент отправки, это самое
  надёжное доказательство. Проверка:

  ```python
  from weblate.accounts.models import AuditLog
  print(AuditLog.objects.filter(activity="sent-email").order_by("-timestamp")[:3].values("timestamp", "email"))
  ```

- Лимит: `reset-request` блокируется при 10 запросах/сутки
  (`AUTH_LOCK_ATTEMPTS`, `accounts/models.py:699-706`,
  `trans/defaults.py:142`). Двумя попытками не исчерпывается.
- Схема ссылки в письме — `http://l10n.herocraft.com/...`, это ожидаемо
  (шаг 5); публичный edge отвечает на неё `301` на `https`.

### 5. ENABLE_HTTPS — отложено, замер выполнен

`WEBLATE_ENABLE_HTTPS=0` → `1` дало бы `https://`-ссылки в письмах
(`weblate/settings_docker.py:59,67`: `SITE_URL` собирается из этого флага) и
Secure-cookie. Технических препятствий два ожидалось, одно снято, одно
подтвердилось.

Снято: цикла редиректов не будет —
`WEBLATE_SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https` уже стоит в
прод-`.env` (строка 21), `deploy/nginx-l10n.conf:24-27,45` прокидывает
заголовок с fallback на `$scheme`, публичный edge отдаёт TLS и сам
редиректит `http` → `https`.

Подтвердилось: plain-HTTP клиенты в обход edge существуют. Замер по
`/var/log/nginx/l10n.log` + ротации (7 суток, 11-17 августа, 9 120 запросов):

| путь | запросов | `X-Forwarded-Proto` |
| --- | --- | --- |
| `Host: l10n.herocraft.com` через edge `192.168.0.210` | 8 968 | `https` |
| `Host: l10n.herocraft.com` напрямую, без edge (клиент `10.0.0.103`) | 62 | отсутствует |
| `Host: 192.168.0.233` (прямой IP) | 90 | отсутствует |

Прямой IP используют три клиента: `192.168.0.75` (69 запросов — полноценная
браузерная сессия: страница + шрифты + `static/*`), `10.0.0.175` (18),
`10.0.0.103` (3). Клиент `10.0.0.103` ходит и по имени в обход edge, причём
это административный трафик: `POST /accounts/login/ 302`,
`POST /api/users/ 201`, `POST /api/users/ivanbelov/groups/ 200`,
`POST /api/projects/ 201` — то есть именно та сессия, в которой создавали
Ивана Белова, плюс API-запросы с токеном.

При `ENABLE_HTTPS=1` все эти 152 запроса получают `301` на `https://<host>`:
для `192.168.0.233` там нет TLS-слушателя (на сервере открыт только порт 80),
а API-скрипты без `-L` просто ломаются; Secure-cookie по plain HTTP тоже не
уйдут. Поэтому флаг **не трогаем** в этой работе.

Условие для возврата к задаче (любое одно): все клиенты переведены на
`https://l10n.herocraft.com`, либо на `192.168.0.233:443` появляется TLS,
либо в Weblate заводится исключение из редиректа для внутренних адресов.
Тогда правка — одна строка в `.env` + `docker compose up -d weblate`, откат
симметричный.

### 6. Добить существующих пользователей

Сброс пароля Ивану Белову **уже работает без `VerifiedEmail`**:
`UniqueEmailMixin.clean_email` (`weblate/accounts/forms.py:80-96`, база для
`ResetForm`) сначала ищет `User.objects.filter(email=mail)` по основному полю
`User.email` и только потом падает в `social_auth__verifiedemail__email`.
У Ивана `User.email = ivan.belov@herocraft.com`, значит ручная вставка
`VerifiedEmail` в БД не нужна — строка появится сама, когда он подтвердит
адрес в профиле или войдёт через email-бэкенд.

Поэтому вместо правки БД:

- Проверить фактом: `POST /accounts/reset/` с `email=ivan.belov@herocraft.com`
  → `302` и задача в очереди `notify` без исключения. Это же и есть
  «добивание»: пользователь получает рабочую ссылку и ставит свой пароль,
  временный пароль от 2026-08-17 перестаёт быть нужен.
- Распечатать по всем 7 pk: `User.email`, наличие `VerifiedEmail`,
  `last_login` — зафиксировать состояние после починки, а не предполагать его.
- Дайджесты: убедиться, что `beat` держит расписание `notify_daily` /
  `notify_weekly` (`celery -A weblate.utils.celery inspect scheduled` или
  логи `beat`), и что после починки SMTP ни одна `notify_*` задача не падает.
  Ждать суток не требуется.

### 7. Документация и коммит

- `deploy/environment.example`: заменить нерабочий пример
  (`smtp.office.lan:587`, пустой пароль) на форму, соответствующую проду
  (Yandex 360, `USE_SSL=1`, аккаунт-отправитель = `From`), без секретов.
- `deploy/nginx-l10n.conf:15-22`: комментарий «офисные IP нужны, потому что
  `l10n.herocraft.com` не резолвится в офисе» противоречит замеру — имя
  резолвится в edge `192.168.0.210` и оттуда обслуживается 98 % трафика,
  а прямой IP используют три клиента. Переписать по факту и добавить, что
  именно этот путь блокирует `ENABLE_HTTPS`.
- Changelog: изменение не пользовательское (конфигурация инстанса), запись в
  `docs/changes.rst` не нужна.
- Коммит + push по правилам `AGENTS.md` (`chore(deploy): ...`,
  `docs(deploy): ...`). Секрет остаётся только в `.env` на сервере.

## Риски

1. **`From` не совпадает с SMTP-аккаунтом.** Yandex отклоняет письмо, если
   отправитель не принадлежит аутентифицированному ящику. Оба адресных поля
   меняются на голый `webnotify@herocraft.com`; вариант с display-name в
   env запрещён — на ревью воспроизведён RFC-битый `From` от двойной
   обёртки `formataddr` (`notifications.py:397`). Проявится как отказ
   SMTP или спам-фильтр на шаге 3.
2. **Письма в спам.** DMARC `p=none`, SPF/DKIM Яндекса в порядке, но новый
   отправитель без истории. Смягчение: проверка на шаге 3.4 с указанием папки.
3. **Пароль приложения в env.** Лежит в `.env` на сервере (не в git),
   виден `docker inspect`. Это принятая в этом деплое модель для всех
   секретов, отдельного secret-store не вводим.
4. **Отсутствие `https` в ссылках писем.** Осознанно принято на шаге 5:
   edge отдаёт `301` с `http` на `https`, поэтому клик из письма всё равно
   заканчивается на TLS; сам токен при этом успевает проехать по plain HTTP
   внутри офисной сети. Закрывается отдельной задачей про `ENABLE_HTTPS`.

## Вне объёма

- Включение `WEBLATE_ENABLE_HTTPS` (замер и условия — шаг 5).
- Свой TLS-слушатель на `192.168.0.233` / внутренний DNS-алиас.
- Переезд на Brevo или другой bulk-сендер.
- Правка DNS (`SPF`/`DKIM`/`DMARC`) — не требуется.
- Пересмотр состава дефолтных подписок пользователей.
- Настройка почты в dev-стеке (`dev-docker/`, maildev уже работает).
