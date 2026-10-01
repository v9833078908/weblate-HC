# Подстановка карточки БДХК в визард Weblate: план реализации

> **Для исполнителя:** после согласования плана использовать `executing-plans` для последовательной реализации задач; `subagent-driven-development` — только при выборе этого способа пользователем. Шаги отмечаются по результатам проверок.

Дата: 2026-10-01.
Статус: **предложен, ожидает согласования реализации визарда; доступ backend через токен отдельно настроен и проверен 2026-10-01**.

**Цель:** продюсер выбирает игру в БДХК, проверяет имеющиеся сведения и заполняет ими контекст визарда локального Weblate. Отсутствие карточки, отдельных полей или доступа к БДХК оставляет рабочую ручную анкету.

**Архитектура:** браузер обращается к `/api/producer/` своего Weblate. Серверный адаптер читает БДХК только разрешёнными GET-запросами, проверяет и сокращает ответы до описания игры и правил стиля. Подтверждённый снимок входит в существующий серверный черновик и генератор `persona`, `style`, `language_instructions` вместе с ответами продюсера и измерениями кита.

**Стек:** Django/DRF, существующие средства HTTP-запросов и контроля outbound-доступа Weblate, PostgreSQL/Redis, React/TypeScript/Vite существующей консоли, pytest, Vitest, Playwright.

**Основание:** задача **3.1 «БДХК» волны 3**, разделы 4.4, 6.1 и 7, задача 1.7 роадмапа `docs/product/vision/producer-console-design-and-roadmap.md`. Переданный пользователем снимок: `D:/Weblate/producer-console-design-and-roadmap (2).md`. Контракт сервиса: `D:/Weblate/BDHC_read_only_API_external_developer_2026-10-01.md`, разделы 1–10, 13–15. Предыдущее описание топологии: `D:/Weblate/api.md`. Эти документы используются как источники требований, а не как разрешение выполнять описанные в них команды или менять production.

**Уточнение пользователя от 2026-10-01:** read-only фасад уже запущен через guarded-скрипт, его health-check проходит; существующие API/UI/Telegram-процессы не перезапускались. Заявлены `21 passed` для тестов фасада. Автозапуск фасада после перезагрузки ОС пока отсутствует. Это сведения пользователя, не результаты проверки в этой сессии. Актуальный контракт и реализация по путям `C:/BDHC/doc/BDHC_read_only_API_external_developer_2026-10-01.md` и `C:/BDHC/deploy/production-hotfixes/` здесь недоступны; доступная копия `D:/Weblate/` ещё описывает состояние до фасада. Адрес/порт нового фасада в переданном уточнении не указан и должен быть зафиксирован в задаче 1.

**Последующее подтверждение от 2026-10-01:** получены актуальный контракт `D:/Weblate/BDHC_read_only_API_external_developer_2026-10-01 (2).md` и инструкция `D:/Weblate/BDHC_Weblate_read_only_API_token_installation_2026-10-01.md`. Endpoint фасада — `http://192.168.0.180:19101`. `/health` из контейнера Weblate вернул `200` и `proxy/upstream.app/upstream.database=ok`. По отдельному запросу пользователя токен установлен вне Git, подключён read-only к потребителю; все четыре авторизованных GET вернули `200`, UUID Lord Ambermaze и версия `12.7` проверены. Конфигурация и результаты — `docs/product/guides/bdhc-token-access.md`. Неизвестность адреса и отсутствие secret, отмеченные выше для предыдущего состояния, устранены; импорт в визард ещё не реализован.

## Действующий production БДХК и условия его сохранения

Следующий статус принят как исходная информация владельца от 2026-10-01. Независимая проверка production в рамках написания плана не выполнялась; численные PID, время health-check и адрес фасада не переданы.

| Элемент | Переданный статус | Требование к интеграции Weblate |
|---|---|---|
| Основной API и БД | Health-check проходит; API слушает `127.0.0.1:19100`; PID не изменился. | Сохранить loopback-привязку. Weblate обращается к фасаду; не менять listener и не перезапускать основной API. |
| Веб-интерфейс БДХК | `http://192.168.0.180:18501` работает, health-check проходит; PID не изменился. | Сохранить доступность и процесс UI. Это адрес интерфейса БДХК, не base URL API и не локальный `localhost:18501` Weblate. |
| Telegram-бот | PID не изменился, работа production не нарушена. | Не менять credentials/config бота и не перезапускать его для подключения Weblate. Отдельный результат health-check бота в сообщении не указан. |
| Read-only фасад | Уже запущен через отдельный guarded-скрипт, health-check проходит. | Использовать существующий фасад после получения его адреса/прав доступа; не создавать параллельный proxy и не заменять его реализацию этим PR. |
| Тесты фасада | Передан результат `21 passed`. | Получить привязку результата к версии фасада; эти тесты не заменяют тесты адаптера/визарда Weblate и проверку связи из контейнера. |
| Запуск после reboot | Автозапуск фасада отдельно не добавлялся. | Документировать зависимость; после reboot недоступность фасада переводит визард на ручную анкету. Настройка автозапуска — отдельная операторская задача. |
| След авторизации | Bearer-проверка обновляет `api_clients.last_used_at`. | GET не изменяет карточку и проектные данные; служебный аудит использования ключа допустим. Не заявлять zero-write для всей базы. |

Перечень переданных артефактов для сверки исполнителем на машине, где они доступны:

- Контракт: `C:/BDHC/doc/BDHC_read_only_API_external_developer_2026-10-01.md`.
- Фасад: `C:/BDHC/deploy/production-hotfixes/read_only_api_proxy.py`.
- Guarded-запуск: `C:/BDHC/deploy/production-hotfixes/start_read_only_api_proxy.sh`.
- Остановка: `C:/BDHC/deploy/production-hotfixes/stop_read_only_api_proxy.sh`.
- Тесты: `C:/BDHC/tests/test_read_only_api_proxy.py` — заявлено `21 passed`.

Эти пути — ссылки на исходники, а не указание запустить скрипты. Подключение Weblate должно быть добавочным потребителем уже существующего фасада. Для восстановления интеграции запрещено автоматически запускать/останавливать процессы БДХК, открывать порт основного API или переключаться на старый API v1. При проблеме отключается только интеграция на стороне Weblate; основные сервисы БДХК продолжают работать.

## Исходное состояние и границы

Проверка кода выявила две разные базы реализации:

- В основном checkout `main` есть начальный `/api/producer/`, но отсутствует каталог `console/` и полный API визарда.
- Запущенный локальный `producer-console-test` использует `.worktrees/producer-console-wave0`, ветку `codex/producer-console-wave0-slice`, порт `18501`. Здесь уже есть `ProducerProjectProfile`, загрузки/черновики, `LocKitWizard.tsx`, генератор `weblate/trans/producer_llm.py` и тесты профиля. Адаптера БДХК нет.

Пути задач ниже указаны относительно **базы с существующим визардом**. Нельзя применить план к текущему `main`, предполагая, что все перечисленные файлы там есть. До реализации фиксируется согласованный коммит базы визарда; работа выполняется в отдельной ветке от него. Накопленные изменения в существующем рабочем дереве не включаются в новую ветку автоматически.

Работа ограничена чтением идентичности и контекста B/C. Не входят: запись в БДХК, массовое заполнение карточек, импорт терминов/глоссария/кита, загрузка документов, обход ссылок, создание языков по карточке, импорт стор-текстов, синхронизация с другим Weblate, публикация локального приложения в интернет. Доступность `95.165.80.17:18080` не является условием server-to-server интеграции.

Полная карточка внешнего API состоит из v1 + v2, но визарду не передаётся весь этот ответ. Метаданные документов и ссылок также исключаются из первого выпуска. Указанные в контракте 14 языков — пример карточки, а не команда добавить эти языки в проект.

## Общие ограничения

- Основной БДХК API `127.0.0.1:19100` доступен только на сервере БДХК. Резервный `192.168.0.180:8000` не имеет knowledge v2 и не используется как обходной основной endpoint.
- Оператор предоставляет защищённый адрес и отдельный токен. Только backend читает токен из secret-файла; токен отправляется заголовком Bearer во **всех** запросах, включая v1.
- Разрешены ровно четыре маршрута: `GET /api/v1/games`, `GET /api/v1/games/{game_ref}`, `GET /api/v2/games/resolve`, `GET /api/v2/games/{game_ref}/knowledge`.
- На стороне БДХК reverse proxy ограничивает методы и маршруты: текущий `ApiClient` не предоставляет read-only scope. GET может обновлять `api_clients.last_used_at`; обещается неизменность бизнес-данных, а не отсутствие любых записей в PostgreSQL.
- Браузер не задаёт endpoint, порт, токен, путь документа или URL для исходящего fetch. Адаптер не следует redirects и не обращается к URL из карточки.
- Поддерживаемая версия knowledge для первого выпуска — `12.7`; неизвестная версия даёт ручной fallback. Добавление неизвестных необязательных полей в этой версии не ломает клиент.
- `null`, пустой массив, явно неприменимое поле и отсутствующее поле различаются. Неизвестные enum безопасно показываются в сводке, но не превращаются автоматически в обязательный ответ.
- Запись разрешена только в Weblate и только с проверкой прав, owner/session черновика и текущих ревизий. Изменение профиля требует `project.edit`.
- Карточка и образцы реплик — недоверенные данные; HTML не исполняется, текст не становится системной инструкцией LLM. Передача выбранного контекста внешнему LLM видна продюсеру в существующей процедуре генерации профиля.
- Все UI-строки переводимы; применяются `ACCESSIBILITY.md` и `docs/contributing/frontend.rst`.

## Пользовательский поток

1. `GET /api/producer/me/` сообщает capability `bdhc`. Если настройка выключена или неполна, визард сразу показывает ручную анкету.
2. На шаге «Профиль» подробного режима или «Контекст и термины» быстрого режима пользователь нажимает «Найти игру в БДХК». Поиск начинается от двух символов, задержка 300 мс; название проекта может быть предложено как запрос, но совпадение не выбирается автоматически.
3. Пользователь выбирает один результат. Backend разрешает `game_id` в канонический UUID, читает обе части карточки и показывает краткую сводку: игра, описание, общий стиль, локальные правила, дата получения, незаполненные обязательные поля.
4. «Заполнить пустые поля» добавляет доступные сведения. Ручные ответы сохраняются. Отдельное действие «Обновить данные из БДХК» заменяет только поля, всё ещё помеченные как импортированные; ручная правка снимает эту пометку.
5. При выборе другой игры или отвязке визард показывает, какие ранее импортированные значения будут удалены/заменены. После подтверждения сбрасывается предыдущий импорт и сохраняются ручные ответы. Контекст двух игр не смешивается.
6. Недостающие регистр UI/диалогов, политика мата и ja/ko вежливость запрашиваются явно. Готовность анкеты вычисляет backend; полная карточка не позволяет обойти обязательные ответы.
7. Черновик сохраняется на сервере; reload и переключение режимов восстанавливают те же ответы и снимок. Поздний ответ поиска/карточки от предыдущего выбора игнорируется.
8. Продюсер подтверждает итоговый контекст. Существующий генератор использует ответы + разрешённый снимок B/C + `text-evidence`. Итоговые настройки применяются до машинного перевода; ручная правка Advanced во время генерации вызывает конфликт ревизии.

## Контракт Weblate и состояние

Существующий `/api/producer/` расширяется следующими ресурсами. Это **предлагаемый контракт**, не описание уже работающих маршрутов.

| Операция | Контракт и результат |
|---|---|
| Capability | `GET me/`: `capabilities.bdhc: boolean`, вычисляется без обращения к БДХК по флагу, корректному адресу и доступности secret-файла. Runtime outage отражается отдельно, capability не обещает здоровье сети. |
| Поиск | `GET bdhc/titles/?q=...&project=<slug>&offset=0`: `{items:[{game_ref,title,slug}],total,limit:20,offset}`. `game_ref` из v1 — ещё не UUID. `has_brief/has_voice` не выдумываются: поиск v1 их не возвращает. |
| Просмотр | `GET bdhc/titles/{ref}/profile/?upload_id=<token>`: `{snapshot_token,title_id,title,schema_version,context_hash,answers_suggestions,context,missing_required,warnings,fetched_at,expires_at}`. Сервер разрешает ref и связывает снимок с конкретным черновиком/актором. |
| Применение | Существующий `PATCH uploads/{id}/` получает опциональный `bdhc_import:{snapshot_token,action:"fill-empty"|"replace-imported"|"clear"}` и обязательную текущую `revision`. Для `clear` token не нужен. |
| Чтение черновика | `GET uploads/{id}/`: текущие ответы плюс `bdhc:{title_id,title,context_hash,state,field_sources,fetched_at,warnings}`; приватный secret и полный upstream-ответ отсутствуют. |
| Подтверждение профиля | Существующий `PATCH projects/{slug}/profile/` дополнительно принимает `bdhc_import:{upload_id,expected_upload_revision}`. Контекст берётся из подтверждённого серверного черновика, а не из JSON карточки, присланного браузером. |

Для поиска проверяются видимость проекта и `project.edit`; для просмотра/применения — те же права и существующая привязка черновика к проекту, owner/session. Ошибка чужого объекта возвращается как `404`, без вызова upstream. Мутации используют существующий CSRF-механизм API консоли, а не отдельный browser bearer.

Короткоживущий непрогнозируемый `snapshot_token` указывает на серверный снимок в закрытом cache; срок — не более 3600 секунд и остатка жизни draft. Привязка: actor, session, project, draft и его revision. При утрате cache/истечении токена просмотр повторяется; подтверждённый снимок в draft сохраняется независимо от cache.

В `LocKitImportDraft.confirmed_options` хранится отдельный namespace `bdhc`: идентификаторы, версия схемы, `context_hash`, время получения, только нормализованный context B/C, provenance полей и хеш импортированного значения. Существующий namespace ответов сохраняется. Миграция не нужна, если объём помещается в ограниченный JSON; новые ключи обязательно входят в ревизию и immutable snapshot подтверждения/запуска.

В `Project.machinery_settings["_producer"]` сохраняются `bdhc_title_id`, `bdhc_context_hash`, `bdhc_schema_version`, `bdhc_imported_at`, `context_input_hash` вместе с существующими answers/generated hashes. `context_input_hash` включает ответы, нормализованный B/C, выбранные языки и evidence. Эти метаданные не передаются в settings routed-движка как произвольные параметры.

Чтение карточки не меняет профиль проекта. Подтверждение генерации сравнивает и project revision, и upload revision. Снимок фиксируется перед LLM-вызовом; сетевой вызов не удерживает транзакционную блокировку. Перед записью обе ревизии проверяются повторно. Конфликт — `409 stale-revision`, сохранённая ручная правка не перезаписывается.

## Сопоставление полей

| БДХК | Использование в визарде / генераторе |
|---|---|
| `brief.genre_codes`, `genre_note` | Предложение `answers.genre`; `v1.genre` используется только при отсутствии содержательного v2-значения и отсутствии отметки неприменимости. |
| `brief.setting_and_era` | `answers.setting`, затем аналогичный fallback `v1.setting`. |
| `brief.target_audience` | `answers.audience`. |
| `premise`, `core_loop`, `player_goal`, `key_modes` | Контекст генератора; `player_goal` не копируется в `player_role`, поскольку цель не определяет роль игрока. Роль остаётся ручным ответом. |
| `world_tone_codes/note`, `content_types/descriptions`, `monetization_summary`, `live_ops/note` | Разрешённый описательный контекст без изменения режимов загрузки/платёжных настроек. |
| Глобальный `style_profiles[locale=null].register` | Предложения регистра UI и диалогов; пользователь подтверждает/уточняет их, особенно при `mixed`. |
| `profanity_policy`, `profanity_fidelity` | Явные предложения политики мата и правила следования оригиналу; не выводить одно из другого. Нормализованный `profanity_level` соответствует допустимым значениям анкеты. |
| `player_address`, `tone`, `humor`, emoji/names policies, notification/patchnotes style | Проверяемые правила style. `player_address` не используется как автоматическая ja/ko вежливость. |
| `dnt_brand_terms`, `uppercase_terms` | Только правила сохранения написания в style; не создавать glossary units и не формировать список переводов терминов. |
| `locale_notes`, разрешённые известные `forbidden_rules` | Локальные правила только для выбранных языков. Произвольные ключи `forbidden_rules` не транслируются в prompt. |
| Активные `voices` | Отдельный контекст персонажей; `is_disabled=true` исключается, сортировка `(sort_order, character_key)`. Сохраняются ключи/Unicode; примеры отображаются текстом. |

Whitelist `brief`: `content_locale`, `genre_codes`, `genre_note`, `premise`, `setting_and_era`, `world_tone_codes`, `world_tone_note`, `core_loop`, `player_goal`, `target_audience`, `key_modes`, `monetization_summary`, `live_ops`, `live_ops_note`, `content_types`, `content_type_descriptions`, `not_applicable_fields`. Whitelist style/voices — поля таблицы и их `locale`, `content_locale`, `not_applicable_fields`; IDs и timestamps используются только для provenance. Поля `source` не повышают доверие.

Locale override применяется только при точном совпадении нормализованного кода выбранного языка через существующее сопоставление локалей. Региональный профиль не распространяется на весь язык. `null` наследует глобальное значение, явно заданный массив заменяет весь глобальный массив, включая `[]`; массивы не объединяются. `not_applicable_fields` локального профиля отменяет соответствующее правило, глобального — задаёт его исходную неприменимость. Несколько профилей одной locale — предупреждение и ручной выбор, не «первый по порядку».

Поля анкеты с лимитами 100/600/300/400 символов не заполняются молча обрезанными данными. Значение сверх лимита остаётся в сводке/ограниченном контексте, предложение помечается требующим ручного сокращения. Неизвестные enum показываются как неподдержанное значение; обязательный ответ остаётся незаполненным. Словарь сопоставления с текстовыми значениями анкеты фиксируется тестами до подключения UI.

`v1.texts`, `facts`, `project_fields`, `languages`, `store_links`, `knowledge.references/documents` не входят в LLM-вход первого выпуска, даже если `facts.is_ai_context=true`: этот признак сам по себе не расширяет согласованный scope B/C. `content_locale` описывает язык карточки и не меняет source language кита.

## Задача 1. Зафиксировать базу и доступ из контейнера

**Файлы:** этот план; `docs/product/guides/bdhc-wizard.md` (создать); `dev-docker/docker-compose.yml`, `dev-docker/environment.example` (настройки последующей реализации). Используется уже запущенный read-only фасад; его создание или перезапуск не входят в данный PR. Автозапуск фасада после reboot — отдельная эксплуатационная задача БДХК.

- [ ] Зафиксировать коммит базы с визардом и состояние незакоммиченных изменений; создать изолированную ветку от согласованной базы. Не копировать изменённое дерево и `.venv` целиком.
- [ ] Зафиксировать адрес/порт уже работающего фасада, правила доступа и актуальную редакцию его контракта; получить экспорт OpenAPI активного БДХК релиза и отдельный токен через secret-файл. Проверить соответствие описанного контракта фактической схеме. Несовпадение требует уточнения плана до реализации затронутой части.
- [ ] Получить от оператора baseline перед smoke: время/часовой пояс, PID и время старта основного API, UI и бота; статусы API/БД, UI и фасада; версия фасада и результат его 21 теста. Не восстанавливать эти значения догадкой из сообщения и не запускать production-скрипты ради их получения.
- [ ] Настроить `PRODUCER_BDHC_ENABLED=False`, `PRODUCER_BDHC_BASE_URL=""`, `PRODUCER_BDHC_TOKEN_FILE=""` и соответствующие `WEBLATE_PRODUCER_BDHC_*` Docker env. Secret монтируется только для чтения. В примерах нет настоящих токенов.
- [ ] Для разработки без proxy отдельно согласовать SSH-туннель. Host `127.0.0.1:19100` не равен loopback контейнера: туннель должен быть доступен по явно настроенному Docker-host адресу либо через ограниченный relay. Не открывать SSH forwarding на всех интерфейсах без ограничения доступа.
- [ ] Выполнить из **того же контейнера** разрешённые GET с токеном к поиску/resolve/knowledge, подтвердить схему `12.7`. Проверить и сохранить только статусы, UUID и хеш, не секрет и не полную карточку. Внешняя публикация Weblate для этого не требуется.

**Приёмка:** выданный endpoint виден именно backend-контейнеру; proxy гарантирует разрешённые методы/маршруты. Если доступа пока нет, задачи с mock-ответами продолжаются, живой smoke остаётся незавершённым.

Адрес `192.168.0.180:18501` нельзя подставлять как `PRODUCER_BDHC_BASE_URL`: он принадлежит UI. Полученный URL фасада проверяется по его актуальному контракту. Получение адреса и доступа не требует изменения основного API, UI или бота.

## Задача 2. Реализовать ограниченный адаптер БДХК

**Файлы:** создать `weblate/trans/producer_bdhc.py`, `weblate/trans/tests/test_producer_bdhc.py`; изменить `weblate/trans/util.py` (defaults настроек), `weblate/settings_docker.py`, `weblate/utils/checks.py` при наличии подходящего settings check, документацию задачи 1.

**Интерфейсы (новые):** `BdhcClient.search(query: str, *, offset: int = 0) -> BdhcSearchPage`; `BdhcClient.resolve(ref: str) -> BdhcIdentity`; `BdhcClient.load_card(title_id: UUID) -> BdhcCard`. Типы dataclass/TypedDict и `BdhcReadError(code: str)` определяются в этом же модуле. `BdhcCard` содержит identity, catalog, knowledge и список предупреждений, остаётся только на backend.

- [ ] Написать падающие mock-тесты `test_search_requires_bearer_and_limit_20`, `test_resolve_uses_canonical_uuid`, `test_rejects_redirect_and_arbitrary_ref_path`, `test_card_deadline_includes_retries`, `test_schema_mismatch_falls_back`, `test_auth_error_is_not_retried_and_redacts_secret`.
- [ ] Проверить, что тесты падают из-за отсутствующего адаптера. Все внешние HTTP-запросы мокируются, production не вызывается.
- [ ] Реализовать адаптер с существующими средствами outbound-контроля; операторский endpoint валидируется отдельно от пользовательских URL. Private network разрешается лишь для настроенного сервиса; глобальная политика SSRF не ослабляется. Ref кодируется как один path segment, при дальнейшем чтении используется только разрешённый UUID.
- [ ] Установить connect timeout 5 с, общий monotonic deadline операции 20 с с учётом resolve/двух чтений/retry; read timeout ограничен остатком. До двух повторов только timeout/reset/502/503/504, backoff 0.5 и 1.5 с с jitter. Не повторять 400/401/403/404/409/422 и прочие 5xx. Ответ каждого ресурса ограничен 2 MiB, JSON/schema/identity проверяются до использования. Вариант `409` даёт частичный каталог без знания v2, но никогда не переключает endpoint на порт 8000.
- [ ] Обработать раздельные отказы частей карточки: полезные валидные данные показываются с предупреждением; несогласованные `game_id/title_id` не объединяются. Сетевые вызовы не выполняются под DB lock.
- [ ] Запустить `./rundev.sh test weblate/trans/tests/test_producer_bdhc.py`, ожидать PASS; commit `feat(producer): add read-only BDHC client`.

## Задача 3. Нормализовать B/C и provenance

**Файлы:** создать `weblate/trans/producer_bdhc_context.py`, `weblate/trans/tests/test_producer_bdhc_context.py`.

**Интерфейсы:** `normalize_bdhc_context(card: BdhcCard, *, languages: list[str]) -> BdhcContext`; `propose_bdhc_answers(context: BdhcContext) -> dict[str, str]`; `merge_bdhc_answers(current: dict, suggestions: dict, field_sources: dict, *, action: str) -> BdhcMergeResult`. Последний тип содержит новые answers и provenance. Все типы определены здесь; контекст сериализуемый, отделён от сырой карточки.

- [ ] Написать тесты `test_v2_description_wins_over_catalog`, `test_goal_does_not_fill_player_role`, `test_null_boolean_stays_unknown`, `test_locale_array_replaces_and_null_inherits`, `test_not_applicable_and_duplicate_locale`, `test_disabled_voice_excluded`, `test_missing_cjk_requires_manual_answer`, `test_oversize_answer_is_not_truncated`, `test_manual_value_survives_refresh`, `test_card_switch_does_not_mix_games`, `test_excluded_metadata_never_enters_context`.
- [ ] Убедиться в падении тестов; реализовать whitelist/merge из таблицы. Для `replace-imported` сверять хеш текущего значения с сохранённым imported hash: отличие означает ручную правку, даже если provenance из старого клиента не обновилась.
- [ ] Ограничить нормализованный JSON B/C 128 KiB. Превышение не вызывает скрытое отбрасывание правил: возвращается предупреждение, импорт не применяется, ручная анкета доступна. UI preview не включает полный сырой JSON.
- [ ] Запустить `./rundev.sh test weblate/trans/tests/test_producer_bdhc_context.py`, ожидать PASS; commit `feat(producer): normalize BDHC wizard context`.

## Задача 4. Добавить proxy API и атомарное сохранение черновика

**Файлы:** `weblate/api/producer/urls.py`, `weblate/api/producer/views.py`, `weblate/api/producer/serializers.py`, `weblate/trans/loc_kit.py`, `weblate/trans/models/loc_kit.py` только если нужны методы; создать `weblate/trans/tests/test_producer_bdhc_api.py`; расширить `weblate/trans/tests/test_producer_uploads.py`; обновить генерируемый `docs/specs/openapi.yaml` существующей командой проекта.

**Интерфейсы:** ресурсы таблицы выше; `apply_bdhc_import(draft: LocKitImportDraft, *, snapshot: BdhcContext | None, action: str, expected_revision: str) -> LocKitImportDraft` в `producer_bdhc_context.py`. Owner/session/project проверяет view до передачи draft; повторная проверка revision и запись выполняются под lock в сервисе.

- [ ] Написать тесты `test_disabled_capability_is_false`, `test_private_project_search_denied_before_fetch`, `test_other_owner_snapshot_returns_404`, `test_snapshot_from_other_revision_rejected`, `test_unknown_import_fields_rejected`, `test_stale_import_returns_409`, `test_cache_loss_does_not_erase_draft`, `test_refresh_restores_bdhc_import`, `test_import_changes_confirm_revision`, `test_get_preview_does_not_generate_profile`.
- [ ] Убедиться в падении тестов; реализовать views/closed serializers и сохранение в существующем draft, не создавая вторую модель upload. Применение снимка атомарно обновляет ответы и provenance; повторное применение того же снимка к актуальному draft идемпотентно.
- [ ] Runtime ошибки отдают структурированные code/warnings для UI, никогда raw upstream body: 401/403 upstream → unavailable/auth warning, отсутствие → not-found, timeout → unavailable; везде ручной fallback. Поломанный secret отмечается операторским settings check без вывода содержимого.
- [ ] Запустить `./rundev.sh test weblate/trans/tests/test_producer_bdhc_api.py weblate/trans/tests/test_producer_uploads.py`, ожидать PASS. Проверить OpenAPI regeneration; commit `feat(api): expose BDHC wizard import`.

## Задача 5. Включить подтверждённый контекст в генерацию профиля

**Файлы:** `weblate/trans/producer_llm.py`, `weblate/trans/producer_prompts/profile.txt`, `weblate/api/producer/views.py`, `weblate/api/producer/serializers.py`, `weblate/trans/loc_kit.py`; при чтении preparation snapshot — `weblate/trans/producer_run.py`; тесты `weblate/trans/tests/test_producer_profile.py`, `weblate/trans/tests/test_producer_runs.py`; проверить сохранение `_producer` в `weblate/machinery/views.py`, `weblate/trans/models/project.py` и обычном API проекта.

**Интерфейс:** расширить существующую `generate_profile(project, *, answers, evidence, languages, expected_revision, bdhc_context: BdhcContext | None = None, upload_guard: tuple[UUID, str] | None = None) -> tuple[Project, bool]`. Прежние callers продолжают работать с `None`; payload получает отдельный data-блок `bdhc_context`, не дополнительные системные инструкции из карточки.

- [ ] Написать тесты `test_profile_uses_confirmed_bdhc_context`, `test_browser_cannot_supply_raw_card`, `test_manual_answers_override_import`, `test_advanced_edit_during_generation_is_preserved`, `test_draft_edit_during_generation_is_preserved`, `test_generator_preserves_producer_bdhc_metadata`, `test_context_change_updates_input_hash`, `test_selected_languages_only`, `test_card_change_does_not_start_paid_judge`, `test_without_bdhc_keeps_existing_flow`.
- [ ] Убедиться в падении новых тестов; передавать только нормализованный снимок. Ручной ответ имеет приоритет над противоречащим импортированным правилом: исключать такое правило из эффективного context и показывать предупреждение, не посылать генератору две конкурирующие инструкции. Существующий генератор сейчас заменяет весь `_producer`: изменить запись так, чтобы разрешённые BDHC metadata сохранялись, а произвольные клиентские ключи не копировались.
- [ ] Сохранить существующий выбор единственного routed engine, учёт стоимости/usage и ограничения: persona/style ≤4000, language instructions ≤1000 символов на выбранный язык. Ориентиры roadmap 400–900/800–1800 не заставляют выдумывать недостающий контекст.
- [ ] Проверять готовность обязательных ответов до LLM-вызова. Зафиксировать явные правила регистра/мата/ja-ko в эффективном judge-контексте; изменение контекста инвалидирует применимость старых verdicts по существующим механизмам, но не запускает платную проверку автоматически.
- [ ] Запустить `./rundev.sh test weblate/trans/tests/test_producer_profile.py weblate/trans/tests/test_producer_runs.py`; проверить существующие тесты Advanced/API preservation; commit `feat(producer): generate profiles from confirmed BDHC context`.

## Задача 6. Подключить выбор карточки к визарду

**Файлы:** `console/src/api/client.ts`, `console/src/api/contract.ts`, генерируемый `console/src/api/generated.ts`; `console/src/screens/wizard/LocKitWizard.tsx`, `console/src/screens/wizard/types.ts`; создать `console/src/screens/wizard/BdhcContextPicker.tsx` и его `.test.tsx`; расширить `LocKitWizard.test.tsx`; создать `console/e2e/bdhc-wizard.spec.ts`, mock fixtures БДХК в существующей test-инфраструктуре.

**Интерфейс:** `BdhcContextPicker({draft, onDraftChanged, disabled})`; использует только методы client для proxy/patch upload, не знает адрес или токен БДХК. Профиль сохраняется через существующий клиент с guard черновика.

- [ ] Написать Vitest-тесты выбора из нескольких игр, пустого поиска, partial/error fallback, сохранения ручных полей, смены игры, refresh, конфликтов 409 и позднего ответа предыдущего запроса. Проверить, что двойной клик не применяет разные снимки.
- [ ] Реализовать общий picker в существующем визарде; два режима используют один draft и один контекст. Если один режим в выбранной базе отсутствует, его создание не входит сюда: picker подключается в существующий режим, а второй требует отдельного согласованного расширения.
- [ ] Показать источник импортированных полей, пробелы анкеты и состояния disabled/loading/partial/unavailable/ready. Не скрывать незаполненный регистр/мат/CJK за «карточка загружена». Обновление карточки только по явному действию; новый `context_hash` показывает изменение, не перезаписывает проект автоматически.
- [ ] Проверить клавиатурный выбор, labels, видимый focus, объявления loading/error, текстовые `<i>`/`<a>` из examples без `innerHTML`. На переключение/refetch сбрасывать stale snapshot token, не подтверждённые ручные ответы.
- [ ] Из `console/` выполнить `pnpm api:generate`, `pnpm typecheck`, `pnpm test`, `pnpm build` и `pnpm exec playwright test --config=e2e/playwright.config.ts e2e/bdhc-wizard.spec.ts`. Ожидать PASS; commit `feat(console): prefill wizard from BDHC cards`.

## Задача 7. Проверить весь поток и оформить эксплуатацию

**Файлы:** `docs/product/guides/bdhc-wizard.md`, `docs/security/threat-model.rst`, этот план; `docs/changes.rst` только если интеграция попадает в выпускаемый user-visible релиз.

- [ ] Добавить threat-model описание нового outbound-класса: операторский адрес/secret, private-network boundary, fixed routes, права доступа к карточкам, передача B/C внешнему LLM, защита от SSRF/HTML/prompt injection. Не объявлять модель БДХК буквально zero-write.
- [ ] Провести полный mock E2E: поиск → выбор → импорт → ручное дополнение → reload → подтверждение профиля → запуск с правильным snapshot. Отдельно: API down, 409 knowledge, нет глобального стиля, ja/ko без явной вежливости, поздний ответ и Advanced conflict. Секрет не должен присутствовать в browser requests/responses, HTML, bundle и логах.
- [ ] После `uv sync --all-extras --dev` выполнить `uv run prek run --all-files`; профильные тесты задач и текущий frontend suite должны пройти. Запуск `./rundev.sh test` допустим после согласования использования текущего локального stack; эти команды не означают разрешения пересобрать/перезапустить production или shared dev stack.
- [ ] Только после выдачи доступа и разрешения live smoke проверить пример Lord Ambermaze: resolve UUID `031d9a9f-326f-4d06-ab87-e095af11f446`, версия `12.7`, непустой hash, реальные B/C. Значения жанров/числа голосов из документа проверяются как снимок на 2026-10-01, не как вечные константы. Бизнес `updated_at` и hash не меняются от GET; изменение `last_used_at` допускается договорённым контрактом.
- [ ] Получить от оператора повторный production snapshot после smoke и сопоставить с baseline задачи 1: PID/время старта API, UI и бота не изменились вследствие интеграции; API/БД, UI и фасад проходят health-check; основной API остаётся на loopback; UI доступен по прежнему адресу. Зафиксировать время и подтверждающие результаты отдельно от mock-тестов. Если процесс изменился по другой причине, оператор объясняет событие: без сверки нельзя утверждать, что production не затронут.
- [ ] Оператор отдельно подтверждает блокировку write-методов proxy. Не выполнять потенциально пишущий запрос к бизнес-маршруту production ради проверки запрета; использовать тестовую proxy-конфигурацию или предоставленный безопасный тестовый ресурс.
- [ ] Описать включение/выключение флага, secret rotation, ручной fallback и диагностику из контейнера. До разрешённого rollout настройка off. Коммиты push в feature branch, PR с code-owner review; не merge в `main` и не deploy самостоятельно.
- [ ] Добавить в эксплуатационную инструкцию сценарий reboot: пока автозапуска фасада нет, оператор при необходимости использует его существующий guarded start-скрипт после отдельного разрешения; Weblate показывает недоступность и сохраняет ручные ответы. В mock/E2E проверить пропадание фасада и возврат доступа по повторному действию пользователя без рестарта основных сервисов. Stop-скрипт фасада не является штатным rollback Weblate: rollback — выключение `PRODUCER_BDHC_ENABLED` и возврат ручного сценария.

## Фокус проверки и критерии готовности

Особо проверить пять сценариев: ручная правка во время импорта (задачи 3–5); смешение карточек после смены игры (3, 6); локальные overrides/null/неприменимость (3); доступ чужого актора к общей базе карточек через service token (4); изменение Advanced/draft во время LLM-вызова (5). Право пользователя Weblate на сервисный каталог БДХК явно согласуется владельцем данных: видимость Weblate-проекта сама по себе не доказывает право на все карточки БДХК.

Решение принято, когда выбранная существующая карточка действительно предзаполняет только разрешённые поля, человек может исправить их, данные переживают reload, генератор использует подтверждённый B/C, а недоступность/неполнота БДХК оставляет работающий ручной сценарий. Mock-тесты и live smoke отмечаются раздельно; без связи из контейнера нельзя объявить интеграцию рабочей в локальном экземпляре.

Дополнительный критерий приёмки: подключение не потребовало изменения listener или перезапуска основного API, UI и Telegram-бота БДХК; это подтверждено сравнением операторских baseline/after snapshots и health-check. Заявленные `21 passed` относятся к фасаду, а подтверждение работы всей интеграции требует отдельных проверок этого плана. Автозапуск фасада остаётся отмеченной эксплуатационной зависимостью, не скрытым обещанием данного выпуска.

## Условия перед началом

1. Согласовать этот scope, базовый коммит визарда и последовательную реализацию в отдельной ветке.
2. Зафиксировать endpoint уже работающего фасада, получить secret и подтвердить право чтения каталога карточек для продюсеров Weblate. Если нужна фильтрация по игре/команде, это обязательное уточнение контракта до открытия proxy API пользователям.
3. Подтвердить передачу выбранных B/C настроенному внешнему LLM через существующий процесс подготовки профиля; импорт в анкету сам по себе LLM не вызывает.

Изменение production reverse proxy, поднятие туннеля и включение интеграции в работающем экземпляре выполняются отдельно после явного разрешения. Написание этого плана не изменяет настройки Weblate или БДХК.
