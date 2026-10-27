# Вход в Wizard через Weblate

Пользователь нажимает «Войти через Weblate». Пароль и дополнительные факторы
проверяет Weblate; после подтверждения пользователь возвращается в Wizard.
Действующая сессия Weblate обычно избавляет от повторного ввода пароля. Wizard
создаёт отдельную сессию и не копирует пароль или его хеш.

Контракт доступа: `docs/product/designs/weblate-wizard-identity.md`.
План: `docs/product/plans/2026-10-26-weblate-wizard-single-sign-on.md`.
Функция выключена по умолчанию и не обновляет Weblate до upstream 2026.11.

Локальная проверка и исправления security review:
`docs/operations/reports/2026-10-26-wizard-sso-local-validation.md`.

## Подготовка Weblate

Установить extra `wizard-sso` (производственный extra `all` его включает).
Подготовить отдельный RSA signing key вне Git и резервировать его приватно.
Для Docker задать:

```ini
WEBLATE_WIZARD_SSO_ENABLED=1
WEBLATE_WIZARD_PUBLIC_URL=https://wizard.example.com/console/projects
WEBLATE_WIZARD_SSO_ISSUER=https://weblate.example.com/oidc
WEBLATE_WIZARD_SSO_CLIENT_ID=wizard-production
WEBLATE_WIZARD_SSO_PRIVATE_KEY_FILE=/run/secrets/wizard-oidc-signing.pem
```

В недокерной конфигурации эквиваленты называются `WIZARD_SSO_ENABLED`,
`WIZARD_SSO_ISSUER`, `WIZARD_SSO_CLIENT_ID`, `WIZARD_SSO_PRIVATE_KEY_FILE`.
Для ссылки «Визард локализации» в верхнем меню также задайте
`WIZARD_PUBLIC_URL` с HTTPS-адресом страницы проектов Wizard. Ссылка видна
вошедшим пользователям при включённом SSO; пустой или некорректный адрес её скрывает.
Адрес должен указывать на тот же экземпляр Wizard, что и зарегистрированный
OAuth callback. Учётные данные, параметры запроса и фрагменты в нём не допускаются.
После их определения вызвать `weblate.wizard_auth.config.configure(globals())`.
Этот вызов добавляет optional OAuth app только при включённой функции.
Миграции выполнять с включённой конфигурацией до запуска процессов с SSO;
обычный запуск без флага не устанавливает таблицы optional OAuth app.

Зарегистрировать в Django admin OAuth Application с указанным client ID:
Confidential, Authorization code, RS256 и ровно одним HTTPS redirect URI
`https://wizard.example.com/auth/weblate/callback`. Client secret скопировать
сразу в приватный файл Wizard: Weblate хранит его хеш. Не включать dynamic
registration, password/implicit grants или client credentials. Экран согласия
оставить включённым. Не менять глобальную CSP ради callback: интеграция добавляет
зарегистрированный origin только в ответ страницы авторизации.

Discovery: `/oidc/.well-known/openid-configuration`, JWKS:
`/oidc/.well-known/jwks.json`; остальные адреса клиент получает из discovery.
При смене signing key предыдущий приватный ключ можно временно добавить в
`WIZARD_SSO_INACTIVE_KEYS` в защищённом settings override для перекрытия JWKS.
Секретные значения не выводить в диагностику или командные строки общего журнала.

## Подготовка Wizard

Настроить HTTPS reverse proxy по существующему `deploy/proxy.conf.example`;
два приложения могут работать на разных серверах. Задать:

```ini
WIZARD_SSO_ENABLED=1
WIZARD_PUBLIC_ORIGIN=https://wizard.example.com
WIZARD_SECURE_COOKIE=1
WEBLATE_API_BASE_URL=https://weblate.example.com/api/
WIZARD_SSO_ISSUER=https://weblate.example.com/oidc
WIZARD_SSO_CLIENT_ID=wizard-production
WIZARD_SSO_CLIENT_SECRET_FILE=/run/secrets/wizard-oidc-client
WIZARD_SSO_TOKEN_KEY_FILE=/run/secrets/wizard-token-encryption
WIZARD_EXTERNAL_WRITES=0
```

Файл шифрования содержит отдельный ключ `cryptography.fernet.Fernet.generate_key()`.
Не использовать для него client secret или RSA signing key. Доступ к файлам —
только сервисной учётной записи и оператору; токены и БД резервировать приватно,
ключ шифрования — отдельно от БД. Потеря этого ключа требует нового входа, а не
переключения на общий API-токен. OIDC metadata и JWKS разрешены только на том же
HTTPS origin, что и заданный оператором issuer.

Браузер получает только случайную сессию HttpOnly/Secure/SameSite=Lax. Токены
не передаются интерфейсу, не кладутся в localStorage и не включаются в отчёт
задания. Уровень reverse proxy должен исключать query callback из access logs;
обработчик также удаляет query из ASGI scope перед выдачей ответа Uvicorn.

## Права и старые аккаунты

Профиль определяется по issuer + постоянному subject. Совпадения логина или
email недостаточно. Для каждого старого пользователя оператор проверяет
соответствие и выполняет сначала dry-run:

```console
python -m app link-identity --username LOCAL_USER --issuer https://weblate.example.com/oidc --subject VERIFIED_SUBJECT
```

После проверки повторить с `--apply`. Привязка сохраняет внутреннее имя владельца
и журналируется; конфликтующие связи отвергаются. Непривязанный пользователь
получает новую отдельную учётную запись без локального пароля. Старые проекты,
черновики, выгрузки, квитанции и владельцы не переименовываются.

OAuth scopes `wizard.read/import/translate/quality` ограничивают закрытый список
API-маршрутов. Они не выдают Django permissions. Weblate повторно проверяет
доступ к проектам/компонентам и право конкретного действия. ID token не разрешает
REST-доступ. Обычные клиенты Token/Bearer/Session продолжают прежнюю работу.
Профиль переводчика передаётся через producer endpoint только с полями persona,
style и language_instructions: ключи, URL и routing не возвращаются и не изменяются.

`WIZARD_SSO_EMERGENCY_LOGIN=1` — отдельный аварийный локальный вход оператора.
Он выключен по умолчанию, требует локального is_admin и не разрешает внешние
операции. Отказ SSO не включает этот режим или общий токен автоматически.

## Истечение, выход и задания

Транзакция входа живёт 10 минут, одноразовый code — 60 секунд, access token —
5 минут, сессия Wizard — 8 часов. Refresh tokens ротируются; семейство делегации
ограничено 24 часами от первого обмена. Обновление токена этот предел не продлевает.
Повторное использование refresh token отзывает семейство.

Закрытие вкладки не отменяет разрешённое задание. Выход из Wizard блокирует
локальную делегацию и отзывает её в Weblate. Если сеть недоступна, намерение
отзыва сохраняется и worker повторяет только отзыв. Немедленный распределённый
отзыв при сетевом разрыве не гарантируется; уже принятая внешняя обработка может
продолжаться до получения отзыва или истечения делегации. Общий выход из обоих
приложений не реализован: выход из Weblate не завершает автоматически сессию Wizard.

Отсутствие/истечение доступа останавливает новые шаги со статусом `auth_required`
или `permission_denied`. Пользователь входит заново и явно продолжает задание
той же личности. Ключи операций и контрольные точки сохраняются; сначала
сверяются прежние квитанции. Вход сам по себе не запускает обработку. Старому
заданию служебной учётной записи делегация не присваивается; оно остаётся для
чтения/изоляции. Неизвестный платный результат не становится новым запросом.

Проверка платной границы в Weblate распространяется и на новые MT batches,
и на judge/repair через durable delivery. Ответ уже принятого запроса сохраняется
даже после отзыва. Применение результата сохраняет проверки прав и ревизий.
