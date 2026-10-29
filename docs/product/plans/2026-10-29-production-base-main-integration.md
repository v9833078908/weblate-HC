<!--
Copyright © HCGameLoc
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Полная production-база Weblate в main

## Статус и разрешение

29 октября 2026 года пользователь поручил интегрировать полную production-базу
Weblate в `main`, одновременно передав парный Judge fallback Terra и исправление
красного CI отдельному субагенту. Реализация разрешена; в рамках этого переноса
новая production-выкладка, платные запросы и работа с живыми переводами не разрешены.

При промежуточном согласовании пользователь отдельно подтвердил сохранение
необязательного Weblate-side SSO в полной production-базе. Для следующей ветки
парного fallback разрешена минимальная additive-миграция 0156 с точной связью
exchange и HTTP-попытки и сохранением связанной provenance; она не входит в
настоящий перенос production-коммита 01746448.

Статус: локальная интеграция проверена; публикация PR ожидается. База `origin/main` —
`d815169a4f9ce65bf7fdfc403b136d461f586f7a`; исходный production-код —
`017464487bfaf46fa0fd013526670614f1ba535c`. Историческая проверка этой версии:
`docs/product/plans/2026-10-29-judge-pair-fallback-and-cost-controls.md`.
Контракты восстановления:
`docs/product/plans/2026-10-26-main-reconciliation-and-safe-recovery.md`,
`docs/product/guides/run-isolation-and-offline-release.md`.

## Выбранный подход и границы

Интеграция существующей production-истории в отдельной ветке от актуального
`origin/main`, а не замена main старым снимком и не повторный узкий backport.
Сохраняются все исправления main и проверенная production-семантика: native MT,
producer MT/quality, verify-only, журнал оплаченных запросов, исходный preparation
pipeline и восстановление frozen replies без повторного POST.

В production-истории присутствует необязательный Weblate-пакет
`weblate/wizard_auth/` с делегированным доступом и ссылкой навигации. Он входит в
существующую совместимость Weblate и сохраняется без включения новых настроек.
Самостоятельные корни приложений Wizard (`frontend-wizard/`, `console/`,
`wizard/`) не импортируются из другой истории: сравнение целевых деревьев
не содержит прикладных изменений этих корней. Отдельный CI-slice исправляет только
форматирование существующего `frontend-wizard/compose.yaml`. Никакие production
secrets, volumes, пользователи и настройки не переносятся.

Парный fallback — отдельная последующая ветка. CI-исправления — отдельная ветка;
их пересечения с `unit.py`, миграциями 0143/0144, dependency lock и workflow
разрешаются без изменения прикладной семантики. Не меняются лимиты расходов,
модели, severity, repair attempts или размер MT-батча.

## 1. Объединить исходники и историю

- [x] Интегрировать production-коммит в изолированную ветку от `origin/main`.
- [x] Разрешить только фактические конфликты; сохранить accounting и Flash/Kimi
  из main вместе с production durable replay и Gemini codec.
- [x] Сохранить deploy/offline/rollback инструменты и owning-документацию.
- [x] Подтвердить отсутствие прикладных изменений самостоятельных корней Wizard и
  отсутствие вмешательства в пользовательский основной checkout.

Основные границы: `weblate/trans/{autotranslate,delivery_policy,llm_delivery,
provider_recovery,mt_operations,quality_execution}.py`,
`weblate/api/producer/`, `weblate/trans/models/`, `weblate_customization/`,
`weblate/wizard_auth/`, `deploy/`, `pyproject.toml`, `uv.lock`.

## 2. Сохранить миграции и оплаченные результаты

- [x] Сохранить имена уже выпущенных миграций и общий граф: обе producer-ветки
  0147, история RESTRICT, merge 0149/0153, journal 0150–0152, codec 0154/0155.
- [x] Проверить upgrade с обеих producer-веток и с production repair history
  на отдельной синтетической PostgreSQL, без живой БД.
- [x] Проверить сохранность translation/operation receipts/digests и frozen
  Gemini v2 replay: один оплаченный POST, одна запись usage, без повторной оплаты.
- [x] Проверить unknown/неполный response и несовместимый digest: финансовый
  fence не допускает новый POST.

Проверки: `test_producer_release_migrations.py`, `test_llm_delivery.py`,
`test_mt_reconciliation.py`, `test_provider_recovery.py`,
`test_judge_delivery_recovery.py`, `weblate_customization/tests/test_machinery.py`.

## 3. Доказать совместимость обычного Weblate

- [x] Проверить native MT/streaming/retry, producer API и verify-only отдельно
  от server-admitted durable delivery.
- [x] Проверить необязательный Weblate OAuth/delegation пакет с выключенным
  default и существующими настроенными режимами; не менять настройки доступа.
- [x] Выполнить локальный реальный HTTP smoke с fake provider: structured MT,
  сохранённый ответ и повторное чтение без второго запроса; получить настоящий
  Django HTTP ответ для актуального producer API и проверить хранимый результат.
- [x] Проверить состав source distribution и cold startup без Wizard runtime,
  credentials и импорта необязательного OAuth provider. Сборка production-образа
  и deployment остаются отдельным разрешаемым действием, не частью интеграции.

Проверки: `test_delivery_policy.py`, `test_quality_execution_policy.py`,
`test_mt_operations.py`, judge suites, producer API tests,
`weblate/wizard_auth/tests/`; `makemigrations --check --dry-run`.

## 4. Ревью, CI и публикация

- [x] Выполнить затронутые regression suites и repository hooks после завершения
  изменяемых slices; не запускать конкурирующие formatter/check процессы.
- [x] Получить независимое ревью migration/recovery и совместимости API/auth;
  устранить подтверждённые замечания и сохранить фактическую проверку.
- [x] Обновить документацию и сведения о выпуске после smoke, не выдавая старый
  release manifest за новый image или фактическую выкладку.
- [ ] Commit и push feature-ветки через `origin`; сверить корпоративный Git и
  GitHub независимо, открыть PR в main. Защиту main не отключать; новую
  production-выкладку не выполнять.

## Параллельные владельцы

| Ветка | Ответственность | Интеграция |
| --- | --- | --- |
| `integrate/production-base-main-20261029` | Родитель: существующая production-база | Первая база для main |
| `feat/judge-pair-fallback-20261029` | Terra: задачи 2–3 и связанная документация Judge-плана | После полной production-базы |
| `fix/ci-green-20261029` | Отдельный агент: реальные причины красного CI | Узкие исправления без изменения runtime-контрактов |

Проверка обеих независимых реализаций и общего результата остаётся у
оркестратора. Успех реализации не означает разрешения на deployment.

## Доказательства локальной проверки

- `uv run prek run --all-files --show-diff-on-failure`: все hooks прошли,
  включая REUSE, Ruff, YAML, ShellCheck, JSON и Markdown; исключений не было.
- Итоговый затронутый regression-набор: **912 passed, 2 skipped,
  210 subtests passed** на отдельном PostgreSQL 16 (`127.0.0.1:54283`).
  Он включает judge client/loop/round/deferrals/autotranslate/recovery,
  accounting, delivery policy/journal, producer MT/reconciliation/recovery,
  quality policy, migration compatibility, optional SSO, producer API,
  routed machinery, OpenAI, deploy retention и release capacity.
- Первый полный прогон обнаружил отсутствие class-local регистрации
  `RoutedLLMTranslation` в `MTReconciliationTest`. Применён тот же
  `override_settings(WEBLATE_MACHINERY=...)`, что в соседнем `ProducerMTTest`.
  До исправления два сценария падали с `KeyError: openrouter`; после —
  MT reconciliation и включённый SSO: **25 passed, 6 subtests passed**.
- Настоящий локальный Django HTTP: capabilities 200, admission 202,
  повтор admission 202, detail 200, состояние `completed`, одна записанная
  строка. Повтор обработки операции не добавил provider POST; новый экземпляр
  движка воспроизвёл сохранённый обмен без повторного POST и usage.
- Native login и dashboard проверены в Chromium: успешный вход, отсутствие
  Wizard navigation и ошибок браузера. SSO выключен; импорт `oauth2_provider`
  запрещён отдельным import guard. Тестовый транспорт разрешал только loopback.
- `uv build --sdist`: собран `hcgameloc-2026.8.1.dev0.tar.gz`. Из распакованного
  архива выполнен cold `django.setup()` и `get_wsgi_application()`, разрешён
  `/api/producer/me/`, проверено происхождение модулей из архива. OAuth provider
  недоступен и не импортирован; самостоятельные Wizard roots и `.env` отсутствуют.
  Это source distribution, **не production Docker image**.
- Отдельные синтетические upgrade-сценарии до `0155` сохранили все прежние
  значения: MT-ветка `0148` — Unit, ProducerRun, MT operation и attempt;
  quality-ветка `0147` — Unit и quality receipt; `0154` — exchange, work,
  codec, digest, response ID, financial state и usage cost. Первый bare-DB
  harness не выполнил post-migrate language setup; после штатного `migrate`
  оба старых producer-сценария прошли. Живая БД не использовалась.
- Независимое ревью runtime/migrations/auth не нашло оставшихся блокеров.
  Единственное замечание — старый leaf `0154` в `PREPARED-RELEASE.json` —
  исправлено на `0155`, одновременно обновлены source/base anchors.
- Повторный fetch корпоративного `origin` и GitHub подтвердил выбранный
  `main=d815169a`; перед публикацией refs сверяются повторно. Чужие feature
  branches не импортируются, не перезаписываются и не удаляются.

Все описанные verification-вызовы использовали искусственный provider;
платные inference-запросы не выполнялись. Отдельно разрешённый Gemini
Standard/Flex/response-cache эксперимент имеет собственный лимит и доказательства
и не меняет runtime этой интеграции. GitHub CI проверяется после публикации PR;
локальный зелёный набор не объявляется результатом GitHub runner.
