# Row source fallback and clean intake implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task in the current session. Steps use checkbox syntax for tracking. No subagent dispatch is required.

**Goal:** Автоматически использовать русский/английский текст строк без заявленного исходника, убрать сообщения о пустых строках и сделать настройку служебных колонок понятной.

**Architecture:** Выбор исходника детерминированный, выполняется в FastAPI без LLM. Один логический лист разбивается на технические компоненты по фактическому исходному языку; сравнение и экспорт сохраняют единую идентичность строк и исходную структуру листа. Интеграция использует существующий REST API Weblate, Django/Celery не дорабатываются.

**Tech Stack:** Python 3.12+, FastAPI, SQLite/SQLAlchemy, loc_kit_ingest, React/TypeScript, текущий WeblateClient и профиль машинного перевода.

**Spec:** `docs/product/designs/2026-10-11-row-source-fallback-and-clean-intake.md`.

**Status:** Задачи 1–7 реализованы и проверены; выпуск подготовлен к установке. Пять важных замечаний финального ревью исправлены, 231 backend / 105 frontend / 359 importer тестов прошли. Бэкап baseline и изолированное восстановление проверены 2026-10-11. Задача 8 ожидает отдельного разрешения установки и повторной проверки актуальности бэкапа.

## Global constraints

- Заявленный исходный язык приоритетен. Если текста нет: `ru`, затем `en`, исключая повтор заявленного языка. Приоритет `ru` перед `en` — предлагаемая политика для согласования.
- Другие языки, формулы и соседние строки не используются как запасной источник. Язык определяется по подтверждённой колонке, без LLM.
- Пустые строки не показывать ни как сообщения, ни как карантин, ни как метрику. Строки с ключом без подходящего текста остаются проблемными.
- Не перезаписывать готовые переводы, исходные ячейки, исторические снимки или существующие глоссарии. Внешние проекты, подключённые для чтения, остаются для чтения.
- Все новые внешние записи проходят существующую проверку владения, план изменений и подтверждение. Нельзя глобально включать запись в старые проекты ради новой функции.
- Не повторять неизвестный платный запрос. Журнал, lease и наблюдение должны различать реальные компоненты/языки, включая запасные группы.
- До установки полный бэкап фактического серверного выпуска и пробное восстановление. Разрешение без бэкапа для прежней правки кнопок сюда не относится.
- Доработка исторического интерфейса не изменяет сохранённый результат прошлого задания. Новый алгоритм применяется при подготовке новой версии.
- Без новых зависимостей и без изменений Django/Celery. Продуктовый код GPL-3.0-or-later; русский интерфейс через существующие строки локализации.
- Перед реализацией согласовать этот план. Установка нового выпуска требует отдельного явного разрешения; запрос плана не является разрешением установки.

## Review focus

1. Заявленная колонка отсутствует полностью или запасной язык не выбран целью: запасной исходник всё равно доступен, реальный запрос маркирован верно.
2. Формула в языковой колонке, повтор ключа между группами и служебные значения без ключа: нет молчаливой потери или скрытого импорта.
3. Цель совпала с запасным источником или уже заполнена: нет перевода на самого себя и перезаписи готового значения.
4. Источник строки изменился между версиями: конфликт виден, готовые переводы и remote units не удаляются и не дублируются.
5. Откат после принятого внешнего запроса: worker не повторяет платную операцию; старый код не запускается на manifest v2 без проверки совместимости.

## Task 1: Детерминированный выбор исходника

**Files:**
- Create: `backend/app/services/source_fallback.py`, `tests/test_source_fallback.py`.
- Modify: `backend/app/services/kits.py`.

**Interfaces:**
- `resolve_row_source(values: dict[str, str], declared: str, *, formula_languages: frozenset[str] = frozenset()) -> tuple[str, str, str] | None`: `(effective_source, original_text, origin)`; `origin` равен `declared` или `fallback`. Не мутирует `values`; исключает только языковые ячейки с подтверждённым признаком формулы, не обычный текст с начальным `=`.
- `is_content_empty(row: list[str], ignored_columns: set[int]) -> bool`: индексы 0-based; проверяет пустоту после удаления только подтверждённых служебных колонок.

- [x] Написать `test_declared_wins`, `test_en_falls_back_to_ru`, `test_ru_falls_back_to_en`, `test_other_language_ru_before_en`, `test_whitespace_is_missing_but_text_is_preserved`, `test_no_es_fallback`, `test_formula_is_not_source`. Зафиксировать FAIL до кода.
- [x] Реализовать указанный порядок; найти `ru`/`en` в заголовках и подтверждённых ролях независимо от целей. Проверить отсутствие заявленной колонки целиком. Не принимать дублирующиеся языковые колонки без прежнего разрешения неоднозначности.
- [x] Добавить `test_absent_declared_column`, `test_unselected_fallback_language`, `test_unknown_service_column_not_silently_empty`, `test_duplicate_key_across_source_groups`, `test_literal_equals_text_is_not_formula`; применить существующий контроль формул и ключей до разделения.
- [x] Проверить `uv run pytest tests/test_source_fallback.py tests/test_kit_roundtrip.py`. Commit `feat(ingest): resolve missing row sources from Russian or English`.

## Task 2: Версионированный манифест и группы исходников

**Files:**
- Create: `backend/app/services/manifest.py`, `tests/test_multisource_manifest.py`.
- Modify: `backend/app/services/kits.py`, `backend/app/services/drafts.py`, `backend/app/services/kit_versions.py`, `backend/app/services/runs.py`, `backend/app/worker.py`.

**Interfaces:**
- `component_source(manifest: dict, component: dict) -> str`: v2 `component.source`, v1 `manifest.source`.
- `component_targets(manifest: dict, component: dict) -> list[str]`: цели пользователя без фактического исходного языка.
- `logical_sheet_identity(component: dict) -> str`: прежнее `file + '\u001f' + sheet`.
- `component_mapping_key(manifest: dict, component: dict) -> str`: прежний ключ для основной группы; для запасной дописать `'\u001fsource:' + component_source(...)`.
- Manifest v2 хранит `sheets`, `components.source`, `components.targets`, `logical_sheet_id`, построчные `effective_source`, `source_origin`, `original_row`; `source` сохраняет заявленный язык. `totals.input_rows` считает только строки с данными, без blank; `quarantine` содержит только реальные проблемы.

- [x] Написать `test_manifest_groups_have_actual_source`, `test_group_ids_stable_when_fallback_group_empty`, `test_rendered_keys_equal_group_members`, `test_old_manifest_readable`, `test_total_counts_exclude_blank`, `test_original_source_cells_unchanged` и получить FAIL.
- [x] Разделять группы согласно Task 1. Сохранить main `kit-N`, дополнительные `kit-N-src-ru/en`; закрепить номер листа между версиями. Для каждой группы выполнять inference с фактическим исходником, не требовать наличия пустой/отсутствующей заявленной колонки. Профиль содержит фактический исходник и skip_rows строк других групп; глобальные повторы ключей уже запрещены.
- [x] Сохранять координаты и полные строки логического листа. `archive_component` должен проверять равенство ключей/исходников повторного разбора и группы до создания архива. Не отправлять посторонние группы из общего массива rows.
- [x] Применить helpers во всех чтениях единого исходника для source_identity, восстановления черновика, истории и запуска. Исторические v1 читать без переписи; v2 сохранять только в новых версиях.
- [x] Проверить `uv run pytest tests/test_multisource_manifest.py tests/test_restore_kit.py tests/test_run_resume.py tests/test_kit_roundtrip.py`. Commit `feat(manifest): retain logical sheets and per-component source languages`.

## Task 3: Weblate, инструкции и ограниченный машинный перевод

**Files:**
- Modify: `backend/app/worker.py`, `backend/app/services/incremental.py`, `backend/app/services/profile_agent.py`, `backend/app/services/profiles.py`, `backend/app/services/kit_versions.py`, `backend/app/integrations/weblate.py` (только если нужен дополнительный валидатор источника).
- Tests: `tests/test_multisource_worker.py` (create), `tests/test_scoped_mt.py`, `tests/test_profile_agent.py`, `tests/test_glossary_launch.py`, `tests/test_run_resume.py`.

**Interfaces:** Компоненты и helpers Task 2. Использовать прежние сигнатуры `create_component(..., source, ...)`, `render_seed(component, source, target)` и журнал операций, но передавать фактический source и отфильтрованные targets.

- [x] Написать тест fake Weblate: лист с primary `de`, запасными `ru` и `en`, цели `ru/pl`; assert источники remote components равны `de/ru/en`, запрос `ru→ru` отсутствует, заполненные цели исключены из MT. Получить FAIL.
- [x] Обновить создание/наблюдение компонентов, создание языков, seed, MT, required_keys и проверки. Платный запрос ограничен пустыми целями соответствующей группы. Группа без целей не запускает MT.
- [x] Добавить источники примеров и список фактических языков во вход агента инструкций. Автогенерируемая persona ссылается на фактический исходник запроса; шаблон при недоступности LLM ведёт себя так же. Защищённые токены сканируются по выбранному тексту каждой группы.
- [x] Ручные инструкции сохраняются: при их привязке к одному несовпадающему исходнику блокировать запуск понятным сообщением. Не пытаться молча исправить произвольный пользовательский текст. Глоссарий не применяется к группе с несовпадающим source; сведения об этом доступны в деталях.
- [x] Проверить `test_fallback_source_metadata_reaches_mt`, `test_source_equals_target_skipped`, `test_filled_target_preserved`, `test_unknown_mt_is_observed_not_replayed`, `test_actual_source_profile_evidence`, `test_mismatched_glossary_not_reused`, `test_readonly_existing_project_untouched`. Все внешние вызовы замокированы.
- [x] `uv run pytest tests/test_multisource_worker.py tests/test_scoped_mt.py tests/test_profile_agent.py tests/test_glossary_launch.py tests/test_run_resume.py`. Commit `feat(worker): translate source fallback groups with correct language metadata`.

## Task 4: Сравнение версий без дублей и перезаписи

**Files:**
- Modify: `backend/app/services/kit_diff.py`, `backend/app/services/incremental.py`, `backend/app/services/project_links.py`, `backend/app/services/runs.py`, `console/src/components/ChangeReview.tsx`.
- Tests: `tests/test_kit_diff.py`, `tests/test_incremental.py`, `tests/test_persistent_flow.py`, `console/src/components/ChangeReview.test.tsx`.

**Interfaces:** Сохранённое component_map дополняется ключами Task 2. Проверка `(file, sheet, key)` охватывает все технические группы, а сравнение исходника учитывает его язык и текст.

- [x] Зафиксировать FAIL: повторная загрузка тех же запасных строк не создаёт компоненты/MT заново; новый ключ добавляется ровно в нужную группу; legacy основной mapping по-прежнему работает.
- [x] Разрешить плану изменений отдельно показывать создание отсутствующей запасной группы только внутри подтверждённого wizard-owned проекта. Не обращаться с ней как с обычным отсутствующим вручную выбранным компонентом.
- [x] Если ранее опубликованный ключ теперь имеет другой фактический язык/текст, показать конфликт источника; не вставлять вторую копию, не удалять первую и не перезаписывать готовые переводы. Проверить сценарии «появился заявленный текст», «исчез русский, появился английский», одинаковый текст при разных языках.
- [x] Подтверждение связано с manifest hash и полным remote snapshot. Изменение плана/снимка аннулирует старое подтверждение; несовместимые старые планы нельзя автоматически считать подтверждёнными.
- [x] `uv run pytest tests/test_kit_diff.py tests/test_incremental.py tests/test_persistent_flow.py tests/test_project_links.py`; frontend тест ChangeReview. Commit `fix(incremental): preserve row identity across source fallback groups`.

## Task 5: Единый лок-кит при выгрузке

**Files:**
- Modify: `backend/app/services/exports.py`, `backend/app/services/drafts.py`, `backend/app/services/kit_versions.py`.
- Tests: `tests/test_kit_roundtrip.py`, `tests/test_export_history.py`, `tests/test_connected_export.py`, `tests/test_restore_kit.py`.

**Interfaces:** `build_export`/`version_export` объединяют результаты компонентов в manifest v2 `sheets` по `(logical_sheet_id, original_row, target_code)`. Для v1 остаётся прежний путь. Две попытки записи в одну ячейку с разными значениями — ошибка, а не last-write-wins.

- [x] Написать roundtrip: один исходный XLSX-лист, три источника, готовая цель, пустой declared source, формулы и пустые хвостовые строки. Assert один выходной лист, прежний порядок и ключи, пустая исходная ячейка не заполнена альтернативой, готовая цель не заменена, новый перевод стоит на исходной строке.
- [x] Реализовать объединение обычного/подключённого экспорта и истории языков. Измерение количества ключей считает логические строки, не дублирует их по языкам и группам. Проверить XLSX/CSV/JSON и восстановление текущей версии в черновик.
- [x] `uv run pytest tests/test_kit_roundtrip.py tests/test_export_history.py tests/test_connected_export.py tests/test_restore_kit.py`. Commit `feat(exports): merge source groups back into original logical sheets`.

## Task 6: Спокойная диагностика и служебные колонки

**Files:**
- Modify: `backend/app/services/drafts.py`, `backend/app/routes/producer.py`, `backend/app/services/kits.py`, `docs/specs/openapi.yaml`, `console/src/api/client.ts`, `console/src/api/generated.ts`, `console/src/screens/wizard/types.ts`, `console/src/screens/wizard/LanguageSelection.tsx`, `console/src/screens/wizard/LocKitWizard.tsx`, `console/src/styles.css`, `console/src/locales/ru.json`.
- Tests: `tests/test_intake_diagnostics.py` (create), `tests/test_access.py`, `console/src/screens/wizard/LocKitWizard.test.tsx`, `console/src/api/client.test.ts`.

**Interfaces:** `public_intake_summary(manifest: dict) -> dict` в `drafts.py`: counters без blank, `fallback_counts` по языкам и `problem_counts` по причинам. API quarantine/rows фильтрует blank до pagination и возвращает согласованные count/next/previous; historical manifest не изменяется. Все фильтры выполняются сервером с прежней проверкой owner.

- [x] Зафиксировать FAIL: 4382 blank не дают сообщения, метрики или строк в таблице; непустые проблемы остаются; сводка fallback не является ошибкой и не блокирует дальнейшую форму.
- [x] Показать «Строки с данными», «Подготовлено к импорту», «Строки, требующие проверки» и раскрываемую сводку автоматической подстановки. Для v1 вычислять пользовательскую сводку из сохранённых причин, не переписывая totals в БД. Позволить выбрать заявленный язык из проверенного каталога при отсутствии его колонки, если есть подтверждённая ru/en альтернатива; показать пояснение, не подменять глобальный выбор. Добавить frontend тест `declared_source_without_column_uses_confirmed_fallback`.
- [x] Заменить список всех ячеек формул сводкой по выбранным листам/колонкам с буквами и назначением. Не показывать формулы снятых листов. Подтверждённые service роли убрать из обязательных вопросов; «Структура колонок» закрыта по умолчанию и доступна с клавиатуры.
- [x] Убрать опасное безусловное назначение ignored любой колонке с формулой: колонка с языковым заголовком/ключом не становится служебной автоматически. Для безымянных формульных колонок данного файла сохранить безопасное ignored; неизвестные смешанные колонки требуют решения.
- [x] Таблица проблем: файл, лист, номер Excel, причина, языки с текстом; кнопки страниц и детали. Формулы как source запрещены, а неизвестные роли и конфликты не скрываются сворачиванием раздела.
- [x] Проверить 390/1440 px, Enter/Space, focus, aria-expanded/aria-controls. `uv run pytest tests/test_intake_diagnostics.py tests/test_access.py`; `pnpm --dir console api:generate`, `pnpm --dir console i18n:generate`, `pnpm --dir console test`, `pnpm --dir console typecheck`, `pnpm --dir console build`. Commit `fix(wizard): hide blank rows and summarize intake decisions`.

## Task 7: Проверка полного сценария и выпуск

**Files:**
- Modify: `tests/test_persistent_flow.py`, `tests/test_deploy_contract.py`, `tests/test_deploy_safety.py`, `docs/product/guides/persistent-projects.md`, `docs/product/guides/backup-and-rollback.md`, this plan.
- Create: `docs/product/reports/2026-10-11-row-source-fallback-verification.md` при завершении, не сейчас.

- [x] Проверить полный mock сценарий: загрузка → группировка → инструкции → подтверждение → Weblate → MT → наблюдение → экспорт → повторная загрузка/новый язык. Добавить проверку недоступности API и неизвестного платного результата.
- [x] На приватной локальной копии WS.Localizations.xlsx проверить ожидаемые 1657 допущенных и 21 проблемную строку, отсутствие сообщений о 4382 blank. Добавить искусственные копии с русским текстом в пропусках: увеличивается допуск и фактический source равен ru; оригинал не менять.
- [x] Полные существующие backend/frontend проверки, Ruff, typecheck и build. Проверить чтение v1, manifest v2 с запасными группами, исходник без колонки, группировку после перестановки/снятия листов и экспорт без дублей. Отчёт содержит агрегаты, без секретов и игровых текстов.
- [x] Зафиксировать проверенный выпуск и source bundle. У wizard сейчас отсутствует Git remote: не пушить в чужой Weblate репозиторий и не создавать remote без указания пользователя. Сборку хранить в `D:/bdhc_wizard/releases/release-<commit>` через разрешённый доступ.
- [ ] Представить готовый выпуск и проверяемый экран пользователю, запросить отдельное разрешение установки. До разрешения сервер и внешние проекты не менять.

## Task 8: Бэкап, установка и возврат после разрешения

**Files:** существующие `scripts/` и инструкция `docs/product/guides/backup-and-rollback.md`; новый серверный отчёт `docs/product/reports/2026-10-11-row-source-fallback-installation.md` только по фактической установке.

- [ ] Считать фактический current release, настройки и активные задания. Не прерывать неизвестную внешнюю операцию и не считать таймаут признаком её отмены. Не останавливать службы БДХК или Weblate.
- [ ] Сделать согласованный приватный бэкап кода, конфигурации, SQLite и файлов версий на сервере; сохранить SHA256 и ссылку current. Содержимое с секретами не экспортировать на рабочую станцию без отдельного разрешения конкретного экспорта.
- [ ] Проверить SQLite integrity и восстановление в изолированной копии с заблокированными внешними запросами. Проверить вход, чтение старых задач и выгрузок. Записать конкретную точку возврата до переключения.
- [ ] Установить только визард после разрешения. Для smoke проверки запретить внешние записи; после проверки восстановить только прежнюю подтверждённую политику wizard-owned проектов, без расширения доступа.
- [ ] Проверить реальную форму, сводки, чтение старого задания, health и версии API/worker. Платный smoke перевод только после разрешения отдельного нового тестового проекта и ограниченного объёма, не на прежних проектах Weblate.
- [ ] Пробный откат на изолированной копии: старый код не должен выполнять jobs с manifest v2. При реальном возврате остановить workers, сохранить новое состояние, сверить журнал внешних операций; не восстанавливать старую БД поверх принятого MT так, чтобы запрос повторился. Возврат local DB не отменяет внешние записи; автоматическое удаление remote компонентов не выполнять.
- [ ] Обновить статус плана и отчёт фактическими результатами. Отдельно отметить, если нужна полная остановка работы v2-задач при возврате на старый выпуск.
