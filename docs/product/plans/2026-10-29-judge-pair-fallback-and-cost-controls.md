<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Judge: один OR-резерв только после отказа обоих корпоративных мест

## Статус и согласованные решения

Статус: пользователь разрешил завершить парный fallback, исправления CI,
слияние, публикацию в корпоративный Git и GitHub и production-выкладку.
Новые Gemini-эксперименты и включение Flex исключены. Проверка suites,
миграции и локального fake-HTTP smoke обязательна до подтверждения завершения.

Пользователь также разрешил интеграцию полной существующей production-базы
Weblate, включая опциональную Weblate-side SSO, без изменений корневого проекта
или настроек самостоятельного Wizard. Отдельно разрешена минимальная аддитивная
миграция `0156`: связь exchange с фактическим attempt и признак полноты ответа,
с исключением связанных attempts из обычной очистки. Новая таблица и угадывание
связей для исторических записей не разрешены. Последнее согласование также
разрешает rollout проверенного non-Gemini результата.

Пользователь утвердил в чате:

- Основная пара: корпоративный `openrouter/deepseek-v4.1-flash`, стандартный
  `reasoning_effort=none`, температура 0; `Kimi K3.1 Pro`, стандартный
  `reasoning_effort=low`, температура 1. Kimi — явно согласованное исключение
  из требования reasoning off, не разрешение включать thinking другим моделям.
- Оба основных судьи отвечают — используются оба мнения. Одно место отказало —
  используется другое без OpenRouter. Только отказ обоих открывает OR-резерв.
- Резерв ровно один: `deepseek/deepseek-v4-pro` через OpenRouter, reasoning
  `none`. Второго OR-судьи нет; несовместимый Qwen не заменяется на `minimal`.
- Переводчик Gemini отдельно получает явный `medium`; это не настройка судей.
- Производственные переводы и массовые повторные прогоны не выполняются.

Историческая ветка срочного хотфикса основной пары:
`fix/llm-spend-production-20261029`, worktree
`.worktrees/judge-hotfix-20261029`, база `c22b2e0fc1be6247887cfb1a9fc569e0877c1763`.
Этот хотфикс основной пары уже доставлен и проверен ранее в текущей сессии.
Текущая реализация парного fallback: `feat/judge-pair-fallback-20261029`,
worktree `.worktrees/judge-pair-fallback-20261029`, поверх проверенной полной
production-интеграции и её CI-исправлений `d9d22048`. Пользовательский основной
checkout и старый `.worktrees/llm-spend-20261029` не используются как база выпуска.

Предыдущий контракт: `docs/product/plans/2026-09-01-02-judge-openrouter-availability-fallback.md`.
Замеры и ограничения качества: `docs/product/measurements/2026-10-29-judge-minimum-reasoning-latency.md`.

## Проблема и границы

`weblate/trans/judge.py:_run_batch` делает независимый fallback каждого места
в старом пути. Durable-путь, напротив, отключает его: нельзя просто включить
старую ветку и обойти защиту от повторной оплаты. `_run_seats` в
`weblate/trans/judge_loop.py` сохраняет ответы на вызывающем потоке и удерживает
worker на ACK-барьере; сейчас любой необработанный отказ останавливает пару.

Меняется выбор endpoint и полнота доказательства, не рубрика severity,
переводческий промпт, права, число repair-попыток или размер MT-батча 10.
Не вводятся новые провайдеры, фоновые оплачиваемые повторы, распределённый
circuit breaker, бюджетная подсистема или оценочные цены вместо provider cost.
Нет массового перезапуска Heart Abyss, удаления старых вердиктов или пересчёта
истории расходов выдуманными суммами. Неполная старая история остаётся историей.

## Контракт исполнения

### Идентичность и пригодное мнение

Не сравнивать `request_identity` двух мест между собой: в него входят разные
profile fingerprints. Для каждого места строить ожидаемую identity отдельно
через существующий `compute_judge_request_identity`/`_request_identity`.
Пара относится к одному unit, subject (`live` либо `candidate`), target/plurals,
source/note/explanation/glossary/clarification, project context и языкам.
Каждый сохранённый результат дополнительно должен соответствовать ожидаемым
profile fingerprint и prompt schema именно своего места.

Пригодно разобранное мнение, включая reject/critical; это не отказ сервиса.
Для допуска нового OR-запроса не использовать target-only `active_round`,
старый `current_round` без проверки профиля или completion всей worker-задачи.
Отсутствующая строка не означает отказ: она может быть ещё не отправлена,
остановлена scope guard, отменена или закрыта финансовой блокировкой.

На каждую строку учитывать раздельно: пригодное основное мнение, подтверждённый
неуспех основного обращения, неотправленное/остановленное обращение,
неопределённый оплаченный результат. Только два подтверждённых неуспеха по
совместимому снимку дают допуск к резерву. Более раннее пригодное основное
мнение по точной текущей identity подавляет OR и не теряется из-за более
позднего transport failure. Неподходящий профиль или другой subject не годятся.

### Клиент и отказ провайдера

`request_verdicts` получает явно выбранный замороженный профиль; клиент не
выбирает endpoint сам. Удалить старый per-seat failover и обе его ветки
(auth и general recovery), а не оставить переключатель старой политики.

Ожидаемый HTTP-отказ провайдера отделить от `JudgeError` локальной конфигурации
типизированным исходом/исключением с status, failure kind, actual attempt и
точным отправленным batch. Не ловить весь `JudgeError` как недоступность.
Сохранять уже полученную стоимость до распространения отказа. Для 401/403/402
не повторять запрос на каждой следующей строке одного прохода; неотправленный
хвост получает отдельную причину остановки, но не фиктивные HTTP attempts.
400/404/405/406/415/422 остаются fail-fast ошибкой запроса, а не способом
тратить деньги на альтернативный endpoint. Ошибки БД, прав, локальных настроек
и отмена не открывают OR. 429/5xx/transport/protocol используют только уже
разрешённые ограниченные primary-попытки; durable-путь не получает скрытых retries.

Изменить также вызываемые границы `_post_batch`, `_read_body` и
`_persist_attempt`: ошибки `capture_transport_response`/journal/БД и хранения
обязательного attempt не должны превращаться широким `except Exception` в
`transport` либо исчезать в логе. Только ошибки фактического HTTP-транспорта
классифицируются как его неуспех. Для парного допуска неполная запись
доказательства — фатальная локальная ошибка. Проверка — отказ реального
capture/attempt persistence после ответа; после него ни одного нового POST.

### Оркестратор и барьер

1. Выполнить основные места с текущим ACK-барьером по покрытию записей, не
   по равенству индексов батча. Размеры/adaptive batching могут различаться.
2. Каждое событие сохранить на вызывающем потоке до ACK. Ожидаемый отказ
   провайдера завершает соответствующее место, но не здорового коллегу.
   Барьер может освобождать коллегу при завершении другого worker; это
   синхронизация, не доказательство, что все строки были проверены.
3. При любой фатальной ошибке разбудить все pending/future ACK с этой ошибкой,
   дождаться terminal events и закрыть worker-local DB connections; новых
   primary, isolation и OR-запросов после обнаруженной ошибки не отправлять.
4. После primary-фазы отобрать только строки с доказанными двумя отказами.
   Успешные строки и строки с неизвестным исходом туда не попадают.
5. Непосредственно перед новым OR POST перепроверить отмену, права/делегирование,
   scope, subject, snapshot, текущие identities и применимые lease/deadline.
   Изменённый снимок прекращает отправку, сохраняя ответы уже выполненных calls.
6. Выполнить один последовательный OR-проход выбранной модели. Нет второго OR
   места, рекурсии в fallback, автоматического OR retry или OR split isolation.
   Ширина ограничена профилем места 1; transport bounds наследуются от него.
7. OR 400 останавливает проход как ошибка настройки, без следующего recovery.
   Недоступный OR сохраняет непроверенный результат, не pass. Уже полученные
   полезные primary/OR результаты не удаляются.

### Durable delivery, возобновление и deferrals

У текущего `_durable_judge_response` work identity содержит seat, но не
endpoint. Нельзя переиспользовать primary seat-1 identity с другим digest:
это конфликт сохранённого запроса. Для OR нужен явный различитель фазы в
существующей delivery identity, не новая система journals. Старую primary
identity не менять, чтобы не переслать уже сохранённые запросы после cutover.
Допуск OR при этом обязан проверять обе primary-финансовые записи; новая
identity не должна обходить `DeliveryUnresolved`/sending/unknown.

Неопределённая оплата не является подтверждённым отказом: сначала существующий
reconciliation, без автоматического OR. Полностью сохранённый ответ с ошибкой
провайдера или непригодным вердиктом можно классифицировать; received response
переигрывается из journal, стоимость пишется ровно один раз. После падения между
primary и OR, после OR HTTP и до сохранения verdict, после отмены/возобновления
не должно появляться второго оплаченного OR-запроса для той же работы.

`received` само по себе не подтверждает завершённый ответ провайдера: сейчас
`send()` способен сохранить сериализованный `_BatchResponse` после timeout.
Основание — полностью захваченный HTTP status/body через
`capture_transport_response`, а не state журнала или объект клиентской ошибки.
Нет полного capture после отправки (timeout, interrupted body, exception) —
неопределённый оплаченный результат, включая старые такие journal-записи.
Оба primary timeout после отправки дают ноль OR calls и при первом проходе,
и после resume. Невалидный JSON в полностью полученном HTTP 200, напротив,
является завершённым, но непригодным ответом; его стоимость уже оплачена.

Перед формированием новых OR-батчей найти существующие exchanges через
`ProducerLLMWork.semantic_key`, восстановить весь исходный набор и порядок
из `ProducerLLMWork.identity`/`position`, а не только текущий pending subset.
Заново построить запрос для этого полного упорядоченного набора с совместимым
профилем сохранённого run snapshot и детерминированной границей; требовать
точного совпадения сохранённого request digest до чтения/применения ответа.
Актуальный transport/adaptive batch size не перепаковывает существующий exchange.
Replay полного ответа обслуживает ещё ожидающие строки; уже применённые
результаты не применяются повторно. Только семантические работы без exchange
формируют новые батчи. Изменённые snapshot/profile, неполное членство,
отсутствующая строка или response и несовместимый digest остаются fenced:
не угадывать исходный payload и не пересылать остаток новым POST.
Проверить crash после OR POST с изменившимися pending subset, порядком и шириной:
один оплаченный POST, восстановленные verdicts для совместимых строк.

Адресный `seats=(...)`, deferral и повторная проверка одного места не могут
доказать отказ всей пары только отсутствием второго места в текущем вызове.
Они используют точные сохранённые primary identities для второго места;
если доказательства нет, OR не вызывается. Здоровое сохранённое мнение коллеги
подавляет OR. Durable работа остаётся в существующем recovery, не отправляется
в старую неограниченную очередь deferrals.

### Происхождение и полнота

Резерв сохраняется как одно реальное мнение в логическом seat 1, с фактическим
OR model/provider/profile/attempt и отдельным request round. Его нельзя
копировать в seat 2, считать корпоративным успехом или использовать как две
независимые оценки. Старые primary failures и финансовые записи сохраняются.

Допуск к автоматическому approval требует двух актуальных основных профилей
одного снимка. Один корпоративный либо один OR verdict не удовлетворяет ему.
Исторический target-only показ checks не переписывается; решения о новых
расходах, repair/finalization и approval берут строгую текущую provenance.
Статус выполнения очереди не выдаётся за наличие двух мнений: итог run/warnings
сообщает деградацию и отсутствие полной проверки. Отказ всех endpoints —
`unparsed`/частичный результат, а не successful quality pass.

Общий строгий predicate строится рядом с существующими evidence helpers в
`weblate/trans/models/judge.py`: проверяет два primary profile/request identities,
subject, targets и контекст; не зависит только от пары номеров seat.
Использовать его в `has_complete_current_evidence` и
`weblate/trans/judge_quality.py:candidate_quality`. Тем самым один контракт
обязателен для candidate verification, ручного/автоматического candidate apply,
producer API quality result и `may_release_automatic_repeat_hold`.
Проверить всех потребителей в `weblate/api/producer/quality.py` и candidate apply
в `judge_loop.py`, а не только autotranslate finalizer. OR seat 1 плюс старый
либо несовместимый seat 2 не дают verified/acceptable, automatic apply,
release hold или approval; настоящая текущая primary-пара остаётся пригодной.

### Конфигурация и cutover

Использовать существующие `JUDGE_FALLBACK_BASE_URL`, `JUDGE_FALLBACK_API_KEY`
и `JUDGE_FALLBACK_*_SEAT_1` для единственного резерва. Удалить runtime
`*_SEAT_2` настройки, resolver второго OR-профиля, валидацию обязательной пары
и активные примеры; архивные планы/сохранённые snapshots не переписывать.
В `judge_fallback_snapshot` отражать один профиль. Отсутствие резервного
endpoint допустимо; частичная конфигурация остаётся явной ошибкой до POST.
Профили primary Flash/Kimi из доставленного хотфикса сохранить.
Удалить ссылки на defaults также из `weblate/trans/models/_conf.py`: иначе
конфигурационный класс сломается при импорте. Проверить загрузку с одним
резервом и без резерва, настройку через configuration API и redacted snapshots.

## Задачи и проверка

### 1. Исправления учёта и Gemini — доставлены на production

Файлы: `weblate/trans/judge.py`, `weblate/machinery/openai.py`,
`weblate/trans/models/llm_usage.py`,
`weblate_customization/src/weblate_customization/machinery.py` и их существующие tests.

- [x] Сохранить полученные SSE usage/response ID при обрыве/invalid UTF-8/лимите;
  null/невалидное позднее событие не стирает валидные ранее полученные значения.
- [x] Хранить валидную стоимость без token counters, включая явный 0; malformed
  optional details не теряют расход. Непредставимую цену не округлять.
- [x] Сохранить приоритет HTTP failure над `finish_reason=length`.
- [x] Gemini medium только для routed OpenRouter `google/gemini-3.7-flash`.
- [x] Сохранить frozen codec/digest предыдущих оплаченных Gemini requests:
  новый wire profile применяется только к новым exchanges; полученный старый
  ответ восстанавливается без повторного POST. Не снимать финансовую блокировку.
- [x] Проверить durable replay: один POST и одна usage-запись.
- [x] Независимое ревью, scoped hooks и локальный HTTP smoke без платных calls.

### 2. Единичный резерв и явный клиент — после разрешения плана

Файлы: `weblate/trans/judge.py`, `weblate/trans/defaults.py`,
`weblate/settings_example.py`, `weblate/settings_docker.py`,
`weblate/trans/tests/test_judge_client.py`,
`weblate/trans/tests/test_judge_delivery_recovery.py`.

- [x] Явный профиль, никакого внутреннего failover; один resolver OR.
- [x] Ожидаемый отказ и локальная фатальная ошибка различаются без broad catch.
- [x] OR journal phase не обходит неопределённую primary оплату.
- [x] Заменить старые контрактные тесты per-seat fallback новой политикой;
  сохранить регрессии accounting и Flash/Kimi, не перепинить устаревшие defaults.

### 3. Пара, recovery и доказательства — зависит от задачи 2

Файлы: `weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`,
`weblate/trans/judge_quality.py`, `weblate/api/producer/quality.py`,
затронутые finalization consumers в `weblate/trans/autotranslate.py`,
`weblate/trans/llm_delivery.py` и `weblate/trans/models/llm_delivery.py`
(существующие journal membership/replay interfaces; новая таблица не нужна),
`weblate/trans/models/_conf.py`,
`weblate/trans/tests/test_judge_loop.py`, `test_judge_deferrals.py`,
`test_judge_autotranslate.py`, `test_judge_delivery_recovery.py` и существующие
тесты candidate quality, apply, repeat hold и configuration API.

- [x] Парный gate на уровне строк, строгая identity каждого профиля.
- [x] Сохранить ACK/fatal-error/DB-cleanup инварианты и healthy-seat continuation.
- [x] Recovery, subset, cancel/stale и неизвестная оплата не дают лишних OR calls.
- [x] Одно мнение сохраняется как одно; approval требует двух primary opinions.
- [x] Интеграционные сценарии: обе healthy; каждая из двух по отдельности 403;
  обе 403/402; смешанный batch с частичным parsed ответом; обе protocol-failed;
  несовпадающие ширины; OR 400/timeout; cancel, stale scope и ошибка persistence;
  старый другой profile/project context/subject; deferral одного места;
  crash/replay до/после OR POST. Утверждать точный набор оплаченных вызовов,
  реальные verdicts и итоговые состояния, не mock wiring.
- [x] Подтвердить отсутствие OR после двух timeout с неизвестной оплатой,
  после ошибок настоящего capture/attempt persistence; подтвердить replay
  исходного OR batch после изменения pending subset/порядка/batch size.
- [x] Проверить общий строгий quality predicate на candidate, apply, repeat
  hold, producer API и approval, включая положительный контроль двух primary.

### 4. Документация, выпуск и smoke

Файлы: `docs/admin/config.rst`, `docs/product/guides/producer-guide-weblate.md`,
`docs/changes.rst`, `deploy/environment.example`, `dev-docker/docker-compose.yml`.
Проверить `docs/security/threat-model.rst`: сохраняются существующие boundaries;
добавить описание нового phase gate только если меняется заявленное свойство.

- [x] Описать один резерв, двух-primary gate, неполные результаты и financial fence.
- [x] Зафиксировать исследование батчей/кеша: батч 10 сохраняется; необходимость
  одиночных вызовов не опровергнута, увеличение ранее давало truncation. Новые
  оптимизации без доказанного эффекта и сохранения качества не вводятся.
- [x] Документировать ограничения сверки OR: ledger не полный provider statement;
  management key/export отсутствует, исторические пропуски не заполнять оценками.
- [x] Прогнать затронутые suites и реальный локальный HTTP сценарий пары с fake
  upstream, проверить хранимые attempts/usage/вердикты; не обращаться к моделям.
- [ ] Независимое ревью и аудит критериев; commit/push feature branch,
  разрешённое закрывающее слияние через PR и production-выкладка.
  Corporate credentials восстановлены в локальном игнорируемом хранилище;
  повторный Keychain не нужен. Каждый push через `origin` независимо сверяется
  с корпоративным Git и GitHub. Чужие ветки не перезаписываются и не удаляются.

### Результаты исследования расходов и батчинга

Read-only выборка production за 28 октября по московскому времени:
в журнале OpenRouter 1 636 запросов, 5 760 427 входных и 2 779 002 выходных
токена, сообщённая стоимость $14.67463843926. Gemini `google/gemini-3.7-flash`
занимает $13.704178725; его 2 319 180 reasoning-токенов входят в 2 744 874
выходных, а не прибавляются к ним. Главный расход связан с двумя запусками
перевода Anvil Saga. За 27 октября тот же журнал показывает $28.10440381155.
Это данные приложения, не полная выписка OR-ключа: management key/export
не получен, исторические пропуски не восстановлены оценочными суммами.

Батч 10 и concurrency 2 сохранены. В
`weblate/machinery/llm.py:BaseLLMTranslation` уже записан сравнительный опыт:
батч 20 давал truncation, а 5 ухудшал пунктуационный контракт. Реальный HTTP
батч зависит от кеша, plural expansion, оставшихся строк и повторяющегося
текста; `weblate/machinery/base.py:_get_pending_batch` разносит повторяющийся
текст по запросам. Поэтому одиночный запрос сам по себе не доказывает дефект.
Новые изменения кеша или упаковки без доказанного эффекта не внесены.
Явный Gemini `medium` фиксирует согласованную настройку, но не доказывает
экономию относительно прежнего provider default.

Неполные исторические результаты Heart Abyss не перезапускались и не
переписывались. Отказ провайдера не является отрицательной оценкой перевода;
восстановление основной пары не превращает прошлый `completed` в доказательство
полноценного второго мнения. Тогда парный fallback был отложен и не входил
в срочный выпуск; позднее пользователь отдельно разрешил его реализацию и rollout.

Команда текущей тестовой среды из worktree (изолированный PostgreSQL 16 на 54283):

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test \
CI_DB_HOST=127.0.0.1 CI_DB_PORT=54283 CI_DB_NAME=pair_review_fix_20261029 \
CI_DB_USER=weblate CI_DB_PASSWORD=weblate \
PYTHONPATH=weblate_customization/src uv run pytest \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_deferrals.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_delivery_recovery.py \
  weblate/trans/tests/test_llm_delivery.py \
  weblate/trans/tests/test_llm_usage.py \
  weblate_customization/tests/test_machinery.py \
  weblate/machinery/tests.py::OpenAITranslationTest --reuse-db -n 0 -q
```

Production работает на хотфиксе `017464487bfaf46fa0fd013526670614f1ba535c`.
Образ: `sha256:22f892f3c2e96bb068a61de9eecf213cd16e13a751d3efbbe12cec42b647b87e`.
Перед переключением preflight подтвердил отсутствие активных автопереводов
и пустую очередь. Сравнение compose-конфигураций разрешило только смену образа:
настройки основной пары и fallback не менялись.

### Проверка срочного выпуска

- Полный набор перед финальной защитой JSON-чисел: **686 passed, 2 skipped, 180 subtests passed**
  за 606 с на отдельном временном PostgreSQL 16 (`CI_DB_PORT=54283`).
  Предыдущий xdist-прогон достиг таймаута; следующий прогон на Docker-БД
  потерял соединения с PostgreSQL. Они не считаются успешными проверками.
- `makemigrations --check --dry-run`: `No changes detected`; на хосте есть
  независимое предупреждение об отсутствующем `git svn`.
- Локальный настоящий HTTP smoke подтвердил сохранение стоимости после
  malformed UTF-8, HTTP 400 без дальнейшего recovery, Gemini medium
  с декодированием ответа и точные границы Decimal.
- Независимое ревью: оставшихся блокирующих замечаний нет.
- Scoped hooks прошли с исключёнными `reuse` и `typos`: их ошибки находятся
  в прежних файлах вне этого изменения. Линтеры runtime-кода прошли.
- Финальная проверка не отправляла платных запросов.
- Дополнительное ревью backport выявило число с экспонентой вне возможностей
  `Decimal`: ошибка JSON-декодирования теряла ранее полученный usage. Регрессия
  воспроизведена до исправления; после исправления весь `test_judge_client.py`:
  **186 passed, 81 subtests passed**. Уже полученная стоимость и response ID
  сохраняются, исход `invalid-json` не превращается в transport retry.
- Выпуск `01746448`: OCI revision совпадает, SHA256 всех семи файлов проверены
  внутри образа; шесть импортируемых runtime-модулей повторно сверены в работающем
  контейнере. Миграция `0155_routed_gemini_reasoning_codec` применена.
- Контейнер `healthy`, login HTTP 200; Celery, Granian и nginx — `RUNNING`.
  Runtime подтвердил Flash `none` / температура 0, Kimi `low` / температура 1,
  Gemini `medium`, неизменный старый v2-запрос и точные границы стоимости.
- Verification evidence на сервере:
  `/srv/hcgameloc-rollout-20261029-gemini-01746448/verification.json`.
  Платных проверочных запросов: **0**; исторические переводы не перезапускались.

### Граница интеграции в main

Пользователь выбрал узкие PR без Wizard/SSO. Основные исправления пары,
учёта и Gemini medium перенесены в ветку `fix/llm-hotfixes-main-20261029`
от `a554cebea17c390da027ec7e2b8c67ac65397df3`. Код durable-delivery отсутствует
в этой базе main: связанное с ним восстановление оплаченных Gemini-ответов
остаётся в production-ветке и PR #25 до отдельной интеграции его базы.
Это не означает, что все хотфиксы уже попали в main.

Production PR: #24 — основная пара, #25 — учёт и восстановление Gemini.
Узкий backport открыт в [PR #26](https://github.com/v9833078908/weblate-HC/pull/26).
После независимого ревью владелец репозитория явно разрешил закрывающее
слияние без отдельного GitHub code-owner approval; правила защиты не отключаются.
Корпоративная публикация восстановлена без Keychain: по указанию пользователя
credentials перенесены из локального окружения Game Pulse в игнорируемый
корневой `.env.local` Weblate с правами `0600`. Только локальный Git helper
читает эту пару для `ai_weblate`; секретов в `.git/config` и коммитах нет.
Во время первой попытки выпуска Gemini preflight обнаружил активные
автопереводы и отказал до переключения контейнера; переводы не прерывались.

### Проверка узкого переноса

- Финальный прогон в `fix/llm-hotfixes-main-20261029`: **660 passed,
  2 skipped, 171 subtests passed**, 656 с, отдельная БД PostgreSQL 16.
  Проверены `test_judge_client.py`, `test_judge_loop.py`, `test_judge_deferrals.py`,
  `test_judge_autotranslate.py`, `test_llm_usage.py`, custom machinery и
  `OpenAITranslationTest`. Durable-delivery тесты принадлежат production-базе
  и не выдаются за тесты отсутствующей в main подсистемы.
- `makemigrations --check --dry-run`: `No changes detected`; предупреждение
  об отсутствующем `git svn` не связано с хотфиксами.
- Настоящий локальный HTTP smoke прошёл в обеих ветках: сохранение ранее
  полученной стоимости при invalid UTF-8 и непредставимом JSON-числе,
  отказ HTTP 400 без дальнейшего recovery и Gemini medium с декодированием.
- Независимое ревью двух участков закрыто после переноса пропущенных
  защит: приоритет исходной ошибки потока и проверка optional token counters.
  Scoped hooks runtime-кода прошли; `reuse` и `typos` исключались отдельно.
- Обычный путь требует approving code-owner review. Для этого закрывающего
  слияния владелец явно разрешил воспользоваться его административным правом;
  это не разрешение менять защиту ветки или пропускать проверку результата.

### Сверка корпоративной публикации

Через оба push URL `origin` опубликованы и независимо сверены по SHA:

- `release/production-20261028`;
- `hotfix/judge-flash-kimi-20261029`;
- `fix/llm-spend-production-20261029`;
- `fix/llm-hotfixes-main-20261029`.

На контрольной сверке перед закрытием PR у четырёх веток задачи SHA совпадали
на корпоративном Git и GitHub; `main` была
`a554cebea17c390da027ec7e2b8c67ac65397df3` на обоих серверах.
Другие существующие ветки разработчиков не перезаписывались и не удалялись.
Это двойная публикация из checkout, не серверная фоновая синхронизация.

### Независимое ревью перед слиянием

Ревью закреплено за базой `a554cebea17c390da027ec7e2b8c67ac65397df3`
и кодом PR `0abf8124a5d3b44b34f7fa5de8258eb44f667cc1`:
`MergeStandardsReview` не нашёл нарушений стандартов или значимых smells;
`MergeSpecReview` не нашёл расхождений с согласованным узким контрактом.
Парный fallback и перенос durable-delivery базы остаются вне этого слияния.

Красные CI-проверки не выдаются за успешные: pre-commit сообщает drift
в файлах вне PR; исходники перечисленных ошибок и файлы зависимостей
побайтно совпадают с main-базой. macOS job остановился при установке
`psycopg-binary==3.3.4` из-за отсутствующего wheel для runner.
Локальный HTTP smoke повторно подтвердил сохранение SSE usage/ID,
HTTP 400 без дальнейшего recovery и Gemini medium без платных обращений.

## Проверка парного fallback перед выпуском

Реализация сведена с полной production-базой и её CI-исправлениями.
PR #27 уже находится в `main=520cb0dbf98fba0763e8f54fcb1d8326a6621726`,
с одинаковым SHA на корпоративном Git и GitHub. Новые Gemini-эксперименты
и Flex в эту работу не включены.

- Целевая политика: **26 passed**. Независимое ревью выявило возможность
  отправить сохранённый, ещё не оплаченный батч после появления здорового
  primary-мнения у исключённого из pending subset участника. Регрессия сначала
  получила лишний OR POST, после исправления получила ноль POST с сохранённым
  reservation. Уже оплаченный полный ответ по-прежнему воспроизводится для
  совместимого остатка, даже если ранее завершённый участник стал здоровым.
- После сведения базы и выделения локальных helpers проверки пары,
  delivery recovery и journal: **46 passed, 2 subtests passed**.
  Mypy четырёх затронутых runtime-модулей: **0** ошибок. Frozen round parameters
  остались явными; новые локальные функции разделяют восстановление membership,
  preflight и проверку участника, не вводя второй слой orchestration.
- Миграции: **6 passed, 5 subtests passed**. Переход `0155 → 0156` сохранил
  старые run/attempt/exchange/work/usage значения, оставил association равным
  null и response-completeness равным false; историческая provenance не угадана.
- Настоящий loopback HTTP и настоящий Django quality snapshot API проверили
  восемь сценариев. Обе healthy дают полную проверку; каждая одиночная ошибка
  даёт ноль OR; два отказа дают один OR; unknown после отправки и local capture
  failure не открывают OR. Повтор не создаёт новых POST. Сохранённый OR batch
  из трёх строк восстановлен для двух строк в обратном порядке и при другой
  adaptive width без нового POST; неоплаченный reservation с уже здоровым
  участником остался fenced. Snapshot API вернул 200, target/state не менялись.
  Платных внешних запросов не было.
- Обновлён `docs/security/threat-model.rst`: per-record pair gate, неизвестная
  оплата, полнота фактического attempt, сохранность связанной provenance и
  строгая двух-primary проверка candidate/apply/repeat/approval.
  Удалён obsolete live probe старого per-seat failover; три самостоятельных
  диагностических probe используют явный публично разрешённый primary profile.
  Они проверены на синтаксис и обязательный аргумент, но не запускались платно.

Общий release-регрессионный набор завершён: **988 passed, 2 skipped,
212 subtests passed**. Он включает judge client/loop/round/deferrals,
autotranslate, durable delivery и accounting, producer MT/reconciliation,
quality policy, миграции, producer/API/OpenAPI, optional SSO, machinery
и release/retention checks. Проверки выполнялись на отдельном native PostgreSQL.

Аудит именованных критериев дополнен прямым отрицательным контролем:
`JudgePairFallbackTest.test_noncurrent_peer_failures_cannot_authorize_a_fallback`
проверил старый профиль, старый project context и другой subject:
**1 passed, 3 subtests passed**, по два corporate POST и ноль OR в каждом случае.
Положительный контроль `test_saved_exact_peer_failure_allows_single_seat_recheck`
по-прежнему допускает ровно один OR только для точной текущей пары.

Остальные итоги перед публикацией:

- Все repository hooks прошли. Sphinx HTML с `-W --keep-going` собрал 212
  документов без warnings после исправления длины одного RST underline.
- Проверка настоящего чистого `git archive` выявила устаревший fingerprint
  документационного примера в offline-release gate. Материал примера совпал
  с ранее разрешённой версией `03ef9d85`; обновлён только fingerprint.
  Неиспользуемое исключение для machinery tests удалено: credential markers
  там больше нет. Gate остаётся включённым; архив из 5154 members допущен.
  Затронутый release-capacity suite: **25 passed**.
- Резервная копия production подготовлена приватно под
  `/srv/hcgameloc-backups/2026-10-29-non-gemini-before-pair`: logical database
  dump проверен `pg_restore --list`, сохранены data, фактический environment
  и старый image. Неполный tar не используется. Исходные права не менялись;
  работающий сервис не перезапускался и платных запросов не выполнялось.
- Commit/push, закрывающее слияние и фактический rollout отмечаются отдельно
  после их выполнения; эти локальные доказательства не объявляют production
  обновлённым или GitHub CI полностью зелёным.

### Закрывающее слияние и последние проверки

- PR #28 слит разрешённым закрывающим слиянием владельца. Main на обоих
  remotes независимо подтверждён:
  `dd082b6651c0dd44b1f21c56d58028e68377b1f1`.
- Финальный набор пары и archive gate: **52 passed, 3 subtests passed**.
- Проверка старого writer после `0155 → 0156` сначала получила реальный
  PostgreSQL `NotNullViolation`. У `JudgeRequestAttempt.response_complete`
  сохранён серверный `db_default=False`: старый процесс может записать attempt
  после upgrade, не утверждая полноту ответа. Итог: **7 passed, 5 subtests**.
  Восемь настоящих loopback HTTP сценариев и отдельный old-writer smoke прошли
  повторно на свежей БД; внешних платных запросов не было.
- Подтверждённый дефект CI — удалённый ранее `saml.key` с оставшимися
  потребителями — исправлен настоящей process-local парой certificate/key.
  Collection registration/legal проходит. Первый consumer run: 72 passed и
  два несоответствия формата PEM; после сохранения исходного PKCS8-контракта
  оба случая, криптографическая проверка и SAML login прошли: **6 passed**.
  Фиксированные private credentials не возвращались в репозиторий.
- Сборка production-образа из точного main запущена отдельно; слияние не
  является свидетельством успешного запуска или полностью зелёного GitHub CI.
