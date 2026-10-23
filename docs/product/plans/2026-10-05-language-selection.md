# Language selection implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task in the current session. Steps use checkbox syntax for tracking. No subagent dispatch is required.

**Goal:** Понятный выбор языков лок-кита и новых целевых языков с распознаванием названий и проверкой точных кодов Weblate.

**Architecture:** FastAPI получает справочник языков внешнего Weblate только через GET. Интерфейс использует его для карточек и распознавания названий; backend проверяет коды до сохранения и запуска. Существующий путь манифест → план изменений → worker сохраняется.

**Tech Stack:** FastAPI, Python, React, TypeScript, Intl.DisplayNames, существующий WeblateClient.

**Spec:** `docs/product/designs/2026-10-05-language-selection.md`.

**Status:** Выполнено. После согласия пользователя «Да пожалуйста устанавливай» установлен `release-4bc6191a894a`. Внешняя запись выключена; полный бэкап и восстановление проверены.

## Global constraints

- Исходный язык подписывается «Язык, с которого переводить».
- Тринадцать рекомендаций: `ru`, `en`, `fr`, `it`, `de`, `zh_Hans`, `ko`, `ja`, `pt_BR`, `es_MX`, `pl`, `cs`, `nl`; китайский по умолчанию упрощённый.
- Галочки доступны с клавиатуры; названия полные, коды показаны рядом. Нет дублей одного кода в разных колонках.
- Язык лок-кита показывается только слева, независимо от состояния галочки. Рекомендации, языки Weblate и ручной ввод объединяются по каноническому коду; повторный ввод выбирает существующую карточку. Региональные варианты не объединяются с базовым языком.
- `pt` и `es` не подменяются региональными вариантами. Скрытые листы не обрабатываются.
- Внешняя запись выключена. Существующие проекты Weblate не изменяются; платный перевод не запускается.
- Только функциональные проверки значимых сценариев и сборка. Нагрузочные проверки не добавляются.
- Установка на сервер требует отдельного разрешения для этого выпуска. Разрешение без бэкапа для предыдущего удаления кнопок к этой доработке не относится.

## Review focus

1. Несколько выбранных листов с разными языками: объединение только видимых выбранных листов; не выдавать языки Weblate за языки лок-кита.
2. Региональные варианты, регистр и разделители: точные коды до манифеста, профиля и worker, без смены смысла старых языков.
3. Неизвестный/неоднозначный ввод и недоступный справочник: понятная ошибка, отсутствие непроверенной операции.
4. Смена исходного языка, повторное открытие, снятие галочек: исходный исключается из целей; сохранённые выбранные языки не теряются и не дублируются.
5. Добавление языка, которого нет в XLSX: пустая целевая колонка, сохранение прежних переводов и ограниченный запрос MT.

### Task 1: Справочник и серверная проверка

**Files:**
- Create: `backend/app/services/languages.py`, `backend/app/routes/languages.py`, `tests/test_languages.py`.
- Modify: `backend/app/main.py`, `backend/app/routes/producer.py`, `backend/app/routes/runs.py`, `backend/app/routes/project_links.py`, `console/src/api/client.ts`, `docs/specs/openapi.yaml`, `console/src/api/generated.ts`.

**Interfaces:**
- `load_language_catalog(remote: WeblateClient) -> list[dict[str, str]]`: записи `{code, name}` из всех страниц GET `languages/`, проверка схемы и короткий кеш в процессе; неполный результат не кешировать. Кеш ограничен фиксированным сервером и конфигурацией клиента.
- `canonical_language(code: str, catalog: list[dict[str, str]]) -> str`: точный разрешённый код; нормализация регистра и `-`/`_` без потери региона/скрипта; иначе DomainError `language_unknown`.
- `validate_language_selection(source: str, targets: list[str], catalog: list[dict[str, str]]) -> tuple[str, list[str]]`: проверка всех кодов, отсутствие исходного среди целей, стабильное удаление дублей.
- Авторизованный GET `/api/producer/languages/` возвращает `{results: [{code, name}]}`; `ProducerApi.getLanguages(signal?: AbortSignal)` получает тот же список. Endpoint не принимает адрес API или токен от пользователя.

- [x] Написать `test_catalog_readonly_pagination_and_auth`, `test_canonical_regional_codes` (`pt-br` → `pt_BR`, `ES-mx` → `es_MX`, `zh-hans` → `zh_Hans`, `pt` → `pt`) и `test_unknown_and_source_target_blocked`. Получить ожидаемые FAIL.
- [x] Реализовать справочник, GET endpoint и проверку перед `patch_draft`, обычным запуском и подтверждением плана изменений; ошибки не изменяют черновик/журнал и не отправляют внешние записи.
- [x] Проверить подмену запроса напрямую, неполную пагинацию и недоступный каталог. Обновить OpenAPI и клиент; запустить `pytest tests/test_languages.py tests/test_access.py`, `pnpm --dir console api:generate`, `pnpm --dir console typecheck`. Commit `feat(languages): validate Weblate language selection`.

### Task 2: Выбор языков и ручное распознавание

**Files:**
- Create: `console/src/screens/wizard/languages.ts`, `console/src/screens/wizard/LanguageSelection.tsx`.
- Modify: `console/src/screens/wizard/LocKitWizard.tsx`, `console/src/screens/wizard/ProfileIntegration.tsx`, `console/src/locales/ru.json`, `console/src/styles.css`, `console/src/gettext-catalog.js`.
- Tests: `console/src/screens/wizard/LocKitWizard.test.tsx`, `console/src/screens/wizard/ProfileIntegration.test.tsx`, `console/src/api/client.test.ts`.

**Interfaces:**
- `languageLabel(code: string, catalog: LanguageOption[]): string`: полное имя, русское где доступно; явные имена рекомендаций и полное имя Weblate при отсутствии поддержки Intl.
- `resolveLanguageInput(input: string, catalog: LanguageOption[]): LanguageOption[]`: кандидаты по точному нормализованному коду/имени/синониму; один кандидат распознан, несколько требуют выбора, ноль показывает ошибку. Без LLM.
- `LanguageSelection`: props `source`, `targets`, `kitCodes`, `remoteCodes`, `catalog`, `busy`, `onSourceChange`, `onTargetsChange`; не сохраняет проект самостоятельно.

- [x] Обновить проверки выбора: названия с кодами, раздельные группы, тринадцать рекомендаций, русский ручной ввод `португальский бразильский` → `pt_BR`, `испанский мексиканский` → `es_MX`, Enter и кнопка «Добавить», неизвестный ввод, смена источника и повторное открытие. Проверить лок-кит с `ru`, `en`, `fr`, `it`, `de`: у каждого языка одна карточка, справа этих языков нет; снятие галочки и повторный ручной ввод не создают дубль. Проверить совпадение рекомендации с языком Weblate, нормализацию `pt-br`/`pt_BR` и сохранение отдельных `pt`/`pt_BR`. Получить ожидаемые FAIL.
- [x] Реализовать две колонки, пояснения, карточки-checkbox, список распознанных вариантов и сохранение через прежние `included_target_languages`. Общий список берётся из видимых выбранных листов и ручных ролей колонок. Подключённые внешние языки маркируются отдельно.
- [x] Использовать полные названия и канонические коды также при подстановке языков в инструкции; ручные правки инструкции сохраняются. Проверить 390 px и клавиатуру. `pnpm --dir console test`, `pnpm --dir console typecheck`, `pnpm --dir console i18n:generate`, `pnpm --dir console build`. Commit `feat(wizard): show named language choices and recognize custom input`.

### Task 3: Проверка всей цепочки и подготовка выпуска

**Files:**
- Modify tests: `tests/test_kit_roundtrip.py`, `tests/test_scoped_mt.py`, `tests/test_persistent_flow.py`.
- Update: `docs/product/guides/persistent-projects.md`, this plan's status.
- Create report: `docs/product/reports/2026-10-05-language-selection.md`.

- [x] Проверить сценарий `ru` → `pt_BR`, `es_MX`, `zh_Hans`: коды совпадают в сохранённом выборе, новом манифесте, профиле, плане изменений, `create_translation` и точечном MT-запросе имитации Weblate. Существующие переводы `pt`, `es`, `de` остаются прежними; новые языки получают пустые колонки в экспортируемой книге.
- [x] Запустить связанные backend-проверки и полный существующий frontend-набор; проверить собранный экран браузером с имитацией API, на 1440/390 px. Все внешние запросы записи замокированы; реальный MT не запускается.
- [x] Зафиксировать код и отчёт, собрать ZIP и bundle в `D:/bdhc_wizard/releases/release-<commit>`, показать конкретный результат пользователю и запросить разрешение установки. До этого сервер не обновлять. Git remote отсутствует; чужой репозиторий не использовать.
- [x] После явного согласия: установить только визард, внешнюю запись оставить выключенной, учесть установленный UI patch `a17efff8572e` поверх backend `a63c5b42e802`; проверить реальную страницу без внешней записи. Перед установкой полный бэкап и проверка восстановления, если пользователь отдельно не разрешит обойтись без него для этого выпуска.
