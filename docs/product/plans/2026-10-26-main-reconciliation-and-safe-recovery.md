# Согласование актуального main с локальными Weblate и Wizard

Статус: **согласован; локальная реализация и проверки выполнены, сборка/приёмка завершаются**. Дата: 26 октября 2026 года.
В этом этапе выполнены чтение исходников, сравнение деревьев и подготовка плана.
Код приложения, production, службы, старое задание и платные запросы не изменялись.

Цель: сохранить последние исправления Weblate и подготовленные локальные функции
восстановления/проверки качества, не теряя переводы и свидетельства оплаченных
запросов. Результат исполнения плана — проверенный локальный кандидат и комплект
переноса; установка на production остаётся отдельным согласуемым действием.

Архитектура: новая рабочая ветка от настоящего корпоративного `main`; перенос
собственного изменения дерева с трёхсторонним сравнением относительно e752.
Новый контракт routed-ответов используется для новых запросов. Восстановление
старого ответа отделено от отправки запроса и допускается только при доказанном
соответствии сохранённой операции. Wizard продолжает оркестрацию через producer API.

Основания: согласованный план
`docs/product/plans/2026-10-19-weblate-wizard-full-cycle-responsibilities.md`,
`docs/operations/plans/2026-10-23-06-production-transfer-and-rollback.md` и новые
требования пользователя: анализ последних обновлений без изменения production.
Проверенные ранее этапы не объявляются заново проверенными после интеграции.

## Исходные состояния и границы анализа

| Объект | Проверенная ревизия / состояние |
| --- | --- |
| Корпоративный Git | `http://10.39.40.3:8080/scm/git/ai_weblate` |
| Свежий корпоративный main | `a554cebea17c390da027ec7e2b8c67ac65397df3`, получен 26.10 в 18:00 МСК |
| База уже выполненной интеграции | `e7520b439f69294923f86895a3a63b5bc8d386d3` |
| Локальный Weblate до этого документа | `y.bazenkov/weblate-autonomous-recovery`, `19cc555a60e63047d55ba7253b1074d04fff4700` |
| Локальный Wizard | `y.bazenkov/wizard-autonomous-recovery`, `803f6a4f927248c1fef3308992572a83834a7257` |
| Последняя сегодняшняя сверка production Weblate | source `03ef9d855455f65c168ff791748d80c506d55cb4`; image `sha256:bc016f7abb11f44ad1b438e2124e04fbe5f3c719149201fb0e8889d69e579653` |
| Последняя сегодняшняя сверка места Weblate | 82,80 ГиБ свободно; 15 Docker images; это наблюдение, не резерв места на установку |
| Wizard production | отдельный сервер; исходники прежнего выпуска не изменились, Docker отсутствует; свободно 16,85 ГиБ |

Указанное место и состояние серверов взяты из сегодняшних read-only проверок,
а не подтверждены установкой этого кандидата. Предыдущий список 85 образов для
очистки устарел: фактический состав Docker уже изменился не нашими действиями.
Очистка для исполнения этого локального плана не нужна и не разрешена.

Локальные копии для будущей реализации:

- Weblate: `D:/Weblate/weblate-HC/.superpowers/production-rollout-20261023/publication/weblate`.
- Wizard: `D:/Weblate/weblate-HC/.superpowers/production-rollout-20261023/publication/wizard`.
- Материалы анализа: `D:/Weblate/weblate-HC/.superpowers/production-rollout-20261023/artifacts/main-adjustment-plan-20261026/`:
  `main-source.json`, `full-tree-impact.json`, отдельное bare-хранилище `git-readonly`
  и извлечённые исходники `incoming/`. Эти служебные материалы не публикуются.
- Статическая трёхсторонняя проба runtime-файлов:
  `D:/Weblate/weblate-HC/.superpowers/production-rollout-20261023/artifacts/production-impact-20261026/static-impact.json`.

## Что изменилось и на что влияет

Время ниже — время коммита в Москве, не доказанное время установки или push.
e752 уже включён в локальный кандидат; следующие семь коммитов ещё не включены.

| Коммит | Дата, время | Содержание | Необходимая корректировка |
| --- | --- | --- | --- |
| `2abd6e6` | 23.10, 14:36 | Native judge API: overwrite, выбор engine; уточнение защищённых placeholder parts | Сохранить поведение обычного Weblate, но не позволить overwrite обойти verify_only |
| `5e27fcc` | 23.10, 18:05 | Ошибки repair-провайдера возвращают partial с сохранением verdicts | Объединить обработку ошибок с нашей политикой восстановления; partial не равен успешному завершению |
| `fad32b1` | 23.10, 21:40 | Routed OpenRouter/LiteLLM: закрытый объект translations, text slots и позиции защищённых элементов | Согласовать parser, prompt/schema и восстановление старых оплаченных ответов |
| `03ef9d8` | 23.10, 21:52 | Ограничения позиций внутренней разметки и непустых text slots | Сохранить ограничения в новых запросах и конечную проверку защищённых элементов |
| `69c9573` | 23.10, 21:57 | Запись результатов production-проверки sprite в документации | Сохранить как историческое свидетельство; это не проверка объединённого кандидата |
| `b15f2be` | 26.10, 13:48 | Тег previous перед rebuild; image prune после успешного deploy | Сохранить идею откатного образа, отключить глобальный prune в нашем пути переноса |
| `a554ceb` | 26.10, 13:54 | Builder cache prune с фильтром 336 часов | Удаление кэша тоже исключить из автоматического переноса; отдельное разрешение на очистку |

От e752 до свежего main изменено **16 файлов**. С локальным кандидатом
пересекаются **10 файлов**. Статическая проба runtime-обновлений до 03ef
обнаружила **два конфликтующих файла, три блока**:

- `weblate/trans/autotranslate.py`: объединить `VerificationChangedError`
  с новыми `JudgeError` и `PermissionDenied`, не ослабляя запрет записи.
- `weblate/trans/judge_loop.py`: сохранить наши quality helpers, добавить
  `MachineryBatchOutcome`; согласовать `run_id` и callback `on_failure`.

Остальные текстовые объединения не доказывают совместимость поведения.
Понедельничные deploy-файлы с нашим кандидатом текстово не пересекаются,
но их автоматическое удаление образов/кэша конфликтует с ограничениями переноса.

Полный diff дерева кандидата относительно оригинального e752 содержит **168 путей**,
включая ранее подготовленные тесты, документацию, миграции и инструменты выпуска.
Это не 168 новых изменений пятницы. Ограничиться переносом 16 свежих файлов
нельзя: так потеряются собственные функции кандидата. История опубликованного
кандидата основана на production-снимках; прямое слияние независимых историй
с main не является выбранным способом интеграции.

## Обязательные ограничения

- Ветки `main`, `production/*` и прежние опубликованные ветки не переписывать.
  Новые коммиты и публикации — `y.bazenkov <y.bazenkov@herocraft.com>`;
  исходное авторство импортированных изменений сохранить.
- Не подменять production-БД локальной. Исторические 1662 готовых перевода
  локального задания и сегодняшние production-данные — разные контрольные области.
  Для переноса нужны свежие количества и хеши содержимого обеих областей.
- Старое задание `8add8aa4-f75b-44dd-b46e-e6f071dee4a9` не возобновлять.
  Его неизвестный финансовый исход не превращать в разрешение повторного запроса.
- Сохранить semantic keys, request/response digests, raw responses, usage,
  idempotency receipts, историю и изоляцию заданий. Не удалять или пересоздавать
  их ради прохождения нового parser.
- Проверки выполняются с mock-провайдерами и без paid calls; локальный стенд
  изолирован от production, production Redis и внешних писателей.
- Не запускать production build/import/prune/migrate, не останавливать службы.
  Реальный перенос и изменения живых данных требуют отдельного разрешения.

## Задача 1. Собрать единую базу без потери собственного изменения

**Ветки:** новая Weblate `y.bazenkov/weblate-main-reconciliation-20261026`
от зафиксированного `a554cebe`; Wizard
`y.bazenkov/wizard-main-compatibility-20261026` от `803f6a4`.
Существующие publication clones/ветки сохранить; реализацию вести в отдельных checkout.

**Файлы:** весь проверяемый список 168 путей из `full-tree-impact.json`,
16 incoming-файлов из того же manifest; новый отчёт
`docs/operations/reports/2026-10-26-main-reconciliation-files.tsv`.

- [ ] Перед началом проверить refs и чистоту checkout. Новые изменения main после
  a554 фиксировать отдельно и согласовать их включение, не расширять базу молча.
- [ ] Для каждого пути сравнить base=e752, ours=19cc555, theirs=a554cebe,
  учитывая отсутствие файла, binary blobs и режим executable. Зафиксировать решение
  keep incoming / keep local / combined для каждого отличия; не переносить секреты,
  виртуальные среды и production-конфигурацию из служебных каталогов.
- [ ] Разрешить названные конфликты, сохранить e752 native DeepSeek thinking,
  production `JudgeApplication.change=RESTRICT`, наши durable/quality/verify_only
  функции и граф миграций до merge `0153_merge_production_repair_history`.
- [ ] Перегенерировать `PREPARED-RELEASE.json` для итогового дерева позднее,
  не выдавать старый manifest или `PRODUCTION-SNAPSHOT.json` за снимок нового main.

**Завершение и проверка:** review полного файлового manifest и diff относительно
обеих сторон, без conflict markers; ни одна собственная функция не исчезла молча.
Это статическая проверка интеграции, не PASS runtime. Новые изменения поведения
проверяются следующими задачами; исторические результаты остаются историческими.

## Задача 2. Согласовать новый routed-ответ с журналом оплаченных операций

**Файлы Weblate:** `weblate_customization/src/weblate_customization/machinery.py`,
`weblate/machinery/llm.py`, `weblate/machinery/openai.py`,
`weblate/trans/llm_delivery.py`, `weblate/trans/models/llm_delivery.py`.
Создать ограниченный модуль codec
`weblate_customization/src/weblate_customization/routed_reply.py` и additive migration
`weblate/trans/migrations/0154_llm_exchange_request_codec.py` после 0153.
Если номер занят при исполнении, сначала согласовать новый граф; старые миграции
не перенумеровывать и не переписывать.

**Контракт:** поле `ProducerLLMExchange.request_codec` с закрытыми значениями
`routed-parts-v1`, `routed-slots-v2`, `opaque-v1`; пустое значение означает
неидентифицированную историческую запись. Добавить необязательный keyword
`request_codec=None` в `reserve_exchange` и `dispatch_exchange`; существующие
judge/plain OpenAI callers не должны менять payload и digest из-за метаданных.
Для новых routed-запросов сохранять `routed-slots-v2` до отправки. Не менять
уже сохранённый digest. Несовпадение известного codec закрывает операцию для отправки.

- [ ] Сохранить incoming строгую схему `translations -> id -> text/placeholders`,
  серверную реконструкцию source-owned markup и позиционные ограничения 03ef.
  Неверный v2-ответ не переключать на старый decoder и не отправлять заново.
- [ ] Снять точный legacy renderer/parser с 19cc555 только для восстановления.
  Для v1/неидентифицированного сохранённого ответа восстановить прежний request
  из той же операции, набора строк, ревизий и настроек. Читать legacy reply можно
  лишь при точном совпадении прежнего request digest, полном совпадении work set,
  проверенной принадлежности операции и корректном response digest.
- [ ] При невозможности воспроизвести прежний payload, отсутствии raw body,
  смешанном work set или неизвестном исходе оставить блокировку и понятную причину.
  Нельзя ослаблять существующий digest gate ради совместимости или подставлять
  новую identity. Legacy recovery никогда не вызывает transport, даже если
  исходная запись была reserved; новая отправка использует только v2.
- [ ] Не классифицировать все исторические записи одним backfill. Для доказанного
  legacy replay метаданные дополнять атомарно, исходное финансовое состояние
  и свидетельства не заменять. Сохранить raw reply до parsing и usage ровно один раз.

**Проверки:** новые независимые regression cases в
`weblate/trans/tests/test_llm_delivery.py`,
`weblate/trans/tests/test_mt_reconciliation.py`, `weblate/machinery/tests.py`,
`weblate_customization/tests/test_machinery.py` и migration contract test.
Golden legacy payload/reply берутся из старого renderer, transport при replay
запрещён mock-assertion. Нужны PASS: успешный replay без HTTP/повторного usage;
изменённые digest/config/source/work set блокируются; unknown без тела блокируется;
невалидный v2 не допускает fallback; повторяющиеся placeholders, sprite, interior
markup и переводимые wrappers проходят корректный roundtrip. Обычный OpenAI,
LiteLLM и judge имеют отдельные контрольные случаи.

## Задача 3. Сохранить partial/error semantics и неизменность verify_only

**Weblate:** `weblate/api/views.py`, `weblate/machinery/base.py`,
`weblate/trans/autotranslate.py`, `weblate/trans/judge_loop.py`,
`weblate/trans/quality_execution.py`, `weblate/api/producer/quality.py`.
**Wizard:** `backend/app/integrations/weblate.py`,
`backend/app/services/full_quality_cycle.py`, `backend/app/services/run_recovery.py`,
`backend/app/services/mt_reconciliation.py`.

- [ ] Объединить incoming `MachineryBatchOutcome`/`on_failure` с сохранением
  результатов судей и durable recovery. `partial` показывает подтверждённую часть
  и причину остановки, но не автоматически успешный итог или новую платную попытку.
- [ ] Сохранить выбор engine и overwrite для обычного native API. Для verify_only
  принудительно оставить overwrite/pretranslate/mutating repairs выключенными,
  применять проверку неизменности перед записью; не обходить её новым error handler.
- [ ] Различать 401, 403, доказанный quota/rate refusal и неизвестный исход.
  Retry-After/backoff разрешает ожидание; само истечение времени не доказывает,
  что оплаченный запрос можно повторить. Unknown допускает только сверку/replay.
- [ ] Wizard уже принимает partial в `_require_run` и читает его decisions.
  Сначала доказать совместимость тестами; runtime-код клиента менять только там,
  где обнаружено конкретное расхождение. Не расширять whitelist ответа молча.

**Проверки Weblate:** targeted API/native-autotranslate, judge loop и
`weblate/trans/tests/test_quality_execution_policy.py`, `test_mt_operations.py`,
`test_mt_reconciliation.py`, `test_judge_delivery_recovery.py`.
**Проверки Wizard:** `tests/test_weblate_client.py`, `test_full_quality_cycle.py`,
`test_verify_only_cycle.py`, `test_mt_recovery.py`, `test_autonomous_recovery.py`.
Доказать: repair provider failure сохраняет уже полученные verdicts; partial
не повторяет завершённые операции; потерянный ответ опрашивается по тому же ключу;
verify_only сохраняет target/state/revisions и не вызывает mutating API даже с
overwrite=true на входе. Все transport calls контролируются mocks.

## Задача 4. Устранить автоматическую очистку из пути переноса

**Weblate:** `deploy/vps.sh`, `deploy/README.md`,
`deploy/build-offline-release.sh`, `deploy/check-import-capacity.py`,
`deploy/rollback-web-only.compose.yml`, `deploy/rollback-web-only.conf`,
`tests/test_release_capacity.py`.
**Wizard:** проверить контракты `deploy/install.sh`, `deploy/preflight.py`,
`deploy/rollback.sh`, `deploy/prepare_rollback_bridge.py`; обновить только при
фактической несовместимости. Weblate codec migration сама по себе не меняет
Alembic-граф Wizard `0001..0004`.

- [ ] Сохранить rollback pin, но брать точный ID работающего образа, проверять
  успешное сохранение и прекращать операцию при ошибке. Тег previous не является
  достаточной резервной копией и не должен молча перескакивать на неверный latest.
- [ ] В кандидате убрать автоматические `docker image prune` и `docker builder prune`
  из `deploy/vps.sh`, заменить диагностикой места. Отдельную систему сборки мусора
  не создавать; удаление — только будущий точный согласованный allowlist.
- [ ] Для переноса использовать offline image и согласованный путь с отключёнными
  писателями; обычный build/deploy со стартом workers не считать безопасным dry-run.
  Учесть временное место Docker save/load и restore, не только размер архива.
- [ ] Перепроверить rollback contracts и manifests с новым additive полем exchange;
  code-only rollback сохраняет актуальную БД, журнал и изоляцию, не включает workers.

**Проверки:** shell syntax плюс `tests/test_release_capacity.py` и stubbed Docker/SSH
regression для deploy-path: ни одна успешная/ошибочная ветка не вызывает prune,
ошибка pin блокирует перенос. Wizard `tests/test_deploy_safety.py`,
`test_production_rollback_compatibility.py`, `test_run_isolation.py` по затронутому
контракту. Никаких настоящих deploy/prune на production для проверки сценария.

## Задача 5. Проверить интегрированный кандидат и подготовить новый выпуск

**Файлы:** перечисленные выше test suites; `docs/security/threat-model.rst`,
`docs/api.rst`, `docs/changes.rst`, `docs/product/guides/run-isolation-and-offline-release.md`,
`PREPARED-RELEASE.json`; новый датированный итоговый отчёт в
`docs/operations/reports/`.

- [ ] В изолированном локальном окружении установить предусмотренные зависимости,
  выполнить targeted проверки задач 2–4 и связанный recovery/quality/API regression.
  Запустить configured prek/Ruff для изменённых файлов. Более широкий suite
  расширять по новым пересечениям/ошибкам; старые 696 PASS не выдавать за новый результат.
- [ ] На локальном восстановлении согласованного backup проверить migration graph,
  `makemigrations --check --dry-run`, план и forward применение миграций.
  Сравнить до/после хеши target/state/revision, receipts, raw bodies, request digests,
  usage и состояния старого job; исключить лишь явно добавленные codec metadata.
  Старые миграции не менять; неизвестные операции не запускать.
- [ ] Собрать образ локально из финального commit, с runtime-запретом внешних
  исходящих запросов на тестовом стенде. Подтвердить revision/image hash,
  поставку customization/codec, отсутствие секретов и работоспособность API.
- [ ] На синтетических данных пройти весь цикл MT → checks → judge → quality
  с mock-провайдером; отдельный verify_only запуск не меняет переводы. После
  остановки/возврата процесса уже записанный ответ используется без второго вызова.
- [ ] Проверить web-only rollback точного исходного образа на новой схеме,
  с отключёнными writers/workers; fallback к старому коду не оживляет unknown job.
  При отсутствии пригодного rollback image отметить gate незакрытым, не заменять
  его одной проверкой импортов моделей.
- [ ] Обновить threat model/контракты восстановления и документацию без секретов;
  сохранить отчёт, точные refs, результаты и ограничения. Новые commits/push —
  только в рабочие ветки от имени y.bazenkov, без merge main и переписывания истории.

**Готовность:** новый image и manifests относятся к одному commit; перечислены
PASS/FAIL/не выполнено по каждой группе, сохранность данных подтверждена хешами,
повторная оплата не использовалась как способ восстановления. Незакрытый gate
сборки, миграций, данных или отката означает «не готов к production».

## Отдельный этап после приёмки локального результата

Порядок будущего переноса сохраняется: fresh capacity/health/refs → пригодный
архив текущего image и проверенные backup → разрешённое окно без writers → B0
до изоляции → изоляция старого job без возобновления → B1 после изоляции →
Weblate → Wizard → приёмка с выключенными внешними записями. Старые manifests,
журналы и переводы сохраняются. Новые задания включаются только после приёмки.

Установка не входит в исполнение этого плана автоматически. При расхождении
актуального production с проверенной базой сначала анализируется новый delta.
Откат кода сохраняет актуальные БД и квитанции; восстановление B0 поверх новых
данных или downgrade схемы не является штатной процедурой отката.

## Самопроверка плана

Покрыты: все семь новых коммитов; собственное изменение дерева; новый/старый
codec и digest gate; partial/auth/rate semantics; неизменность verify_only;
два репозитория; миграции; автоматическая очистка; сборка, данные и откат.
Статический анализ не назван runtime-тестированием. Секреты, production-действия,
восстановление старого задания и paid calls исключены из текущего этапа.
