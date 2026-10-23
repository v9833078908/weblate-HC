# Приёмка автоматического восстановления и проверки без изменений

Статус: реализация и приёмка завершены в изолированных локальных ветках. Установка на работающий сервер не выполнялась.

## Сохранность исходного решения

До изменения кода сохранены SQLite, PostgreSQL, файлы Weblate, конфигурация,
журналы и Git bundles обоих приложений. Приватная копия:
`D:/Weblate/weblate-HC/.worktrees/wizard-full-release/.superpowers/manual-full-cycle-20261022/private/backups/autonomous-plan-before-20261023-004155`.
Секреты не входят в репозитории и выпуск.

PostgreSQL восстановлен в отдельную БД: 490 миграций. SQLite прошла integrity
check и повторное восстановление. SHA256 долговечных файлов и Git bundles
проверены. Первоначальный manifest сохранён как `manifest-initial.json`;
его временные WAL/SHM записи исключены из исправленного manifest после
подтверждения неизменности долговечных файлов SQLite.

Защищённый baseline: 1662 готовых перевода, digest
`43829ee3838f546e45722db13082d82e`, 158 исторических MT receipts.
Исходные данные не использовались для тестового исправления переводов.
Дополнительно исходный dump восстановлен в отдельную БД
`autonomous_upgrade_proof`; новые миграции 0150/0151/0152 применены штатно.
После обновления SQL digest и ORM подтвердили 1662 непустых перевода со
статусом translated, прежние 158 receipts и ноль новых provider admissions.

## Поведение выпуска

- Полный ответ провайдера сохраняется приватно до разбора. Семантический
  запрет повторной оплаты переживает смену ключа, модели, пакета и удаление raw.
- При частичном ответе сверяются строки прежнего пакета; готовые сохраняются,
  неопределённые изолируются, доказанно неотправленный хвост продолжается.
- Если провайдер не гарантирует получение прежнего ответа, неизвестный исход
  остаётся исключением. Автоматического повторного платного запроса нет.
- Проверка готового задания использует отдельный immutable `verify_only` run.
  MT, кандидаты, применение исправлений и унификация повторов выключены.
  Внешняя ручная правка сохраняется и делает прежнюю проверку устаревшей.
- POST допускаются через общий долговечный интервал 75 секунд; Retry-After
  сохраняется. GET не занимает платный слот. Новое окно MT — 6 часов,
  максимум 24 часа; сохранённый абсолютный дедлайн не продлевается.
- Заполненность, два разобранных ответа и PASS показываются отдельно.
  Изменённый результат получает новую версию; прежние файлы не перезаписываются.

## Проверки

На синтетических данных проверены уровни 210/810/1070/1620/1642/1662,
потеря ответа, частичный пакет, два worker, смена ревизии/ABA, отказ доступа,
429, перезапуск, неизменность verify-only и устаревание экспорта.
Платные сервисы в этих тестах заменены локальными fixtures; тестовый Docker
подключён только к отдельной внутренней сети.

Проверки интерфейса: 122 Vitest, typecheck, сборка и 1 Chromium-сценарий
прошли. Генератор OpenAPI повторно даёт ту же схему. Восемь проверок отката
прошли, включая запуск старого кода с совместимым описанием миграции 0004
на новой БД; счётчик 1662 и ссылка на оплаченный receipt сохранились.

Итоговые результаты после исправлений ревью:

| Проверка | Результат |
| --- | --- |
| Визард, полный backend | 464 passed, 1 warning in 312.90s (0:05:12) |
| Weblate, судьи/policy/Producer API | 273 passed, 41 subtests passed in 970.96s (0:16:10) |
| Weblate, MT/journal/reconciliation | 65 passed, 4 subtests passed in 197.51s (0:03:17) |
| Weblate, окончательный policy/journal после уточнения типов | 21 passed, 10 subtests passed in 65.83s |
| Интерфейс Vitest | Tests  122 passed (122) |
| Chromium | 1 passed (1.4s) |

Проверка типов, сборка, стабильная генерация OpenAPI, Ruff и проверка миграций прошли.
Code review: один независимый проход gpt-6-astra; критический и три важных пункта
исправлены с RED→GREEN и итоговым регрессом. Повторное ревью не выполнялось.
Отложенных minor замечаний нет: подпись оплаты повышена до Important и исправлена.
Один upstream Starlette deprecation warning в тестах визарда сохранён.

## Ограничения среды проверки

Полный `uv sync --all-extras --dev` Weblate блокируется отсутствующим ICU
toolchain; использован существующий runtime с тестовыми инструментами.
Полный prek содержит прежние ошибки Windows symlink/REUSE и bash-пути;
затронутые Python-файлы проверены настроенными Ruff hooks отдельно.
После подключения закреплённых mypy 2.3.0, Django/DRF stubs выполнена
проверка трёх новых сервисных модулей с `--follow-imports=silent`: ошибок нет.
Без этого ограничения полный граф импортов сообщает 333 ошибки в 58 других
файлах, включая отсутствующие stubs сторонних библиотек. Полный mypy и вся CI
не объявляются зелёными. Pylint новых сервисных модулей прошёл отдельно.

## Установка и откат

Установка требует отдельного разрешения на конкретный manifest двух приложений.
Перед ней: новый baseline и свежий бэкап, восстановление с отключёнными внешними
вызовами, затем Weblate/capabilities и визард. Платные admissions остаются
выключенными до контрактного smoke на отдельной копии проекта.

При откате сохраняется текущая БД с новыми переводами и журналами. Совместимый
старый код запускается только для чтения; оба worker выключены и замаскированы.
Восстановление старой БД после появления новых долговечных записей запрещено.
Откат кода не отменяет уже принятый провайдером запрос.

Руководства: `docs/product/guides/autonomous-recovery.md`,
`docs/product/guides/backup-and-rollback.md`.


## Все принятые решения и их ограничения

- Ruling: use native managed worktree for T, manual worktree for independent W repository — native tool is bound to root Weblate repo — avoids editing sources mounted into existing stand; no behavior cost.
- Ruling: skill task-brief parser only supports English Task headings; extract equivalent Russian task briefs to this workspace without altering approved plan — exact task text preserved — bookkeeping only.
- Ruling: host Docker API unavailable; existing isolated stand uses WSL Ubuntu Docker. All backup and read-only baseline checks target <local-verification-containers> only — no production operations.
- Ruling: sandbox uv cache fails; use worktree .venv/Scripts/python.exe for tests after uv sync — same locked dependencies, no application behavior change.
- Ruling: released means terminal, lock-checked pending intent with no outbound send; exclude only that state from old unique unit/revision reservation — needed because a new batch key otherwise cannot admit a proven never-sent tail — cost: migration compatibility and late old-worker tests must remain part of release checks.
- Ruling: source audit version is checked in addition to target revision and machinery config digest — source A→B→A or changed profile must not apply an old reply — cost: legacy ambiguous raw outcomes remain conservative exceptions.
- Ruling: durable prompts use stable HMAC-derived boundaries/opaque string IDs — random nonce/IDs changed the request digest on each redelivery, blocking cached recovery. HMAC binds the content and secret, preserving untrusted-data separation without predictable externally supplied IDs — cost: durable mode differs from legacy random-ID generation and needs explicit regression coverage.
- Ruling: registered customization in private test settings and disabled bridge networking — existing tests had six KeyError openrouter failures because runtime registry was loaded without the custom package. Re-run of MT suite 39 passed — no application change for this correction.
- Ruling: add narrow raw-response hook in machinery/base.py and wrapper in openai.py — existing BaseLLM fetch runs after HTTP validation, so these hooks are required to meet approved persist-before-parser contract — cost: additional regression tests for non-durable behavior.
- Ruling: uv sync --all-extras --dev attempted in disposable container; missing ICU compiler blocks build. Keep installed runtime and add test tools only, document full dependency-sync limitation — cost: verification environment not a freshly rebuilt full uv environment.
- Ruling: admission scopes aggregate endpoint/account across all models/seats (producer:*:*) until separate quotas are verified — stricter and safe for shared account limits, GET remains free — cost: lower throughput than independent verified per-model quotas.
- Ruling: quality final scan keeps full input scope while judges receive only filled subset — artifact binding requires every original cell and prevents silent disappearance of empty exceptions — cost: an entirely empty job finishes with no paid judges and explicit exceptions.
- Ruling: protected_content_digest is separate from scope_revision because existing scope_revision includes judge evidence; evidence changes during verification are expected, protected content/audit/config changes are not — cost: capability-verified servers must expose the new digest before verification can start.
- Ruling: user explicitly requested local Git; keep implementation commits and bundles local, do not invent a wizard remote or publish code. Existing plan PR is unchanged — cost: external review/merge happens later.
- Ruling: allow verification of a completed owned project through its existing readonly binding only with immutable verify_only server capability; repair still requires the separate writable copy — needed for requested check of the 1662 original translations — cost: relies on enforced server policy and protected digest.
- Ruling: initial backup manifest listed ephemeral WAL/SHM sidecars after restore connections. Preserve manifest-initial.json, exclude only transient sidecars, verify durable SQLite hashes unchanged and immutable integrity + new restore, verify source bundles — cost: manifest amendment is required; no source data changed.
- Task 7: Ruling: bind absolute MT deadline once, including legacy fallback ceiling7200 when absent; configuration increase and Retry-After never extend persisted deadline — found by final regression, two new tests RED→GREEN, affected44passed — cost: older ambiguous checkpoints may finish earlier than a new6h job.
- Final: Ruling: regrade generic payment label to Important — showing all-payment0 while only MT measured can mislead billing/retry decisions, contradicts honest scope — clarify scope in API/UI, never infer billing from unparsed judge response — cost: API adds an explicit scope field, no change to fees or fences.
- Final: Ruling: publication checks the returned batch under run/unit/source locks; evidence readers check the originating run status, fresh actor, project configuration and that individual unit/source audit. Whole protected scope is checked at session entry/exit and final export — avoids quadratic rescans of1662 units for every evidence row while never reviving the changed unit — cost: unaffected unit evidence may remain reusable after another unit changes; entire old session export still becomes stale.
- Ruling: upgrade rehearsal matches source instance installed apps, excluding optional billing/legal added only by settings_test — otherwise conditional0084 migration dependency falsely appears inconsistent. No fake migrations or history edits — cost: rehearsal covers source app graph, not an unrelated optional-app upgrade.

## Исправления итогового ревью

1. Старый PASS после ABA, отмены или потери доступа: проверка перед записью
   под блокировками и единый допуск при чтении текущего evidence/кэша.
   Raw-ответы и запреты повторной оплаты сохраняются.
2. Waiting/429/конфигурационные/сетевые ветки ограничены сохранённым
   дедлайном. Если актуальное чтение невозможно, задание приостанавливается
   с неизвестной заполненностью; успех не выдумывается.
3. Готовая проверяемая область показывает 1662/1662 вместо 0/0;
   источник счётчика — проверенная входная область, не наличие MT-пакетов.
4. Финансовый счётчик явно ограничен MT. Неразобранный ответ судьи
   сам по себе не доказывает ни оплату, ни её отсутствие.
