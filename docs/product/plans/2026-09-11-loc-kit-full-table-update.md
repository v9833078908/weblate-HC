<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Полная loc-kit таблица: обновление существующего компонента

Дата: 2026-09-11.
Статус: план на согласование; написание разрешено, реализация и деплой не разрешены.

## Цель и основания

Продюсер добавляет строки в исходный XLSX/CSV/TSV, загружает **весь файл** через
существующий `Files → Update from a loc-kit table`, проверяет изменения и
подтверждает добавление. Не нужны delta-файлы, разбиение по 5 000 ячеек,
конвертация в PO или отдельные загрузки каждого языка. Существующие переводы
сохраняются. Контекст новых строк не теряется. Повторная отправка безопасна.

Согласованные требования из разговора 2026-09-11: полный файл, preview,
фоновые длительные операции, доступный после ухода со страницы результат,
новые ключи с языковыми значениями и Explanation, сохранение существующих
source/targets/flags, отсутствие удаления по отсутствию строки в файле,
явные расхождения существующих source, сохранение заполненного Character.

Основания и владельцы контрактов:

- `AGENTS.md` — правила репозитория, отдельные разрешения на реализацию и деплой.
- `docs/product/guides/loc-kit-ingest.md` — действующий контракт импорта.
- `docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md` —
  обязательная зависимость по interactive delivery и склейке статистики.
  По сообщению пользователя 2026-09-11 этот план **уже реализуется в ворктри**;
  его строка «реализация не начата» в main устарела. Не менять чужую ветку
  или её статус в рамках написания этого плана.
- `docs/product/plans/2026-08-18-loc-kit-table-add-strings.md` — существующая
  точка входа; раздел статуса описывает сознательный синхронный вариант.
- `docs/product/plans/2026-08-28-kit-explanation-on-string-import.md` —
  Explanation принадлежит БД, не должен попадать в PO/игровой репозиторий.
- `docs/product/reviews/2026-09-08-kit-explanation-plan-review.md` — прежние
  проверки блокировок, сохранения Explanation и judge context.
- Штатный Add new strings: `docs/user/files.rst:166-175`,
  `weblate/trans/models/translation.py:1999-2066`. Пропускает существующие
  ключи, но использует только source/target/context. Это не универсальный
  многоязычный импорт с Explanation.
- Официальное описание:
  <https://docs.weblate.org/en/weblate-2026.8.1/user/files.md#upload-method>.

### Полученные доказательства, не будущие результаты

Локальный запуск `read_sheets → infer_profile → parse_profile → parse_component`
на `/Users/eli/Downloads/localization.xlsx` завершился без ошибок разбора:
один лист `Sheet 1 - localization.import`, 965 физических строк, шапка во
второй строке, 963 ключа; ru/en/de/fr/it/es/pt/ja/ko; 7 непустых Explanation,
пустой Character, 8 674 непустых language/flag/Explanation cells.
Файл не загружался и не применялся к работающему Weblate.

На соответствующем `localization.import.csv` проверен render/parse-back:
9 PO-файлов, диагностик round-trip нет; Explanation `cloud_message` (66
символов) отсутствует в исходном PO. Это намеренная семантика writer, а не
ошибка: Weblate должен отдельно применить карту Explanation в БД.

Измерения времени применения 963 новых строк на дев-стенде ещё **не было**.
Число 5 000 не является доказанным пределом производительности Weblate.

## Объём и выбранный подход

Доработать один существующий строковый поток, сохранив общий loc-kit parser,
`Translation.add_unit`, `Unit.update_explanation`, права и файловые операции
Weblate. Вынести разбор/подготовку preview и применение в обычную очередь
Celery; HTTP сохраняет файл или подтверждение и возвращает страницу состояния.
Для маленьких и больших файлов протокол один, без второго sync-fast-path.

Рабочая единица применения — небольшая порция **целых строк**, а не
отдельных target-ячеек. Все языки нового ключа, его metadata, Explanation и
продвижение курсора фиксируются вместе. Подсчёт прогресса — обработанные
строки; число записей в языках показывается отдельно. Блокировка компонента
освобождается между порциями. Входной файл проверяется полностью до первого
подтверждения; наличие ошибки в последней строке не допускает частичного импорта.

Не входит:

- изменение штатного Upload translation, Add new strings и строгого
  `Upload multilingual spreadsheet`;
- обновление source/targets/flags существующих ключей, удаление строк;
- изменения glossary flow и мастера создания компонента;
- автоматическое определение неизвестных языков, генерация переводов/контекста,
  LLM или платные вызовы;
- импорт произвольной схемы Excel, формул, нескольких компонентов/листов
  за одно действие. Сохраняется существующее требование одного листа;
- обещание неограниченного размера файла, новый общий task framework;
- массовая правка локализаций или запуск на production.

## Наблюдаемые границы кода

| Файл / символ | Текущая ответственность и изменение |
|---|---|
| `loc_kit_ingest/reader.py:read_sheets`, `_read_xlsx` | Чтение таблиц; XLSX уже поддерживается. Добавить опциональные ограничители чтения, без Django-зависимости; текущие CLI callers сохраняют контракт. |
| `loc_kit_ingest/infer.py:infer_profile` | Одна схема для CSV/TSV/XLSX. Не создавать параллельный распознаватель. Передавать установленный source language компонента и явно сохранять языковые колонки, включая редкие/пустые. |
| `weblate/trans/forms.py:LocKitStringsUpdateForm`, `LocKitStringsConfirmForm` | Размер/расширение файла, overwrite. Сохранить `helper.form_tag=False`. |
| `weblate/trans/views/create.py:LocKitStringsUpdateStartView`, `LocKitStringsPreviewView` | Сейчас parse и apply в HTTP; заменить на stage/reserve/status. |
| `weblate/trans/views/create.py:_string_unit_to_json`, `_string_units_from_json` | Существующий формат `{key,values,comments,references,row,explanation,flags}`; перенести обе функции в request-free `weblate/trans/loc_kit.py`, обновить все callers, без aliases. |
| `weblate/trans/models/loc_kit.py:LocKitImportDraft` | Уже есть APPLYING, FAILED, CONSUMED и apply_task_id; расширить состояние только для существующего строкового update. |
| `weblate/trans/loc_kit.py` | Общая классификация preview, применение порции, неизменяемый вход; убрать публичный отказ по 5 000 cells после перехода callers. |
| `weblate/trans/tasks.py` | Новые задачи подготовки и применения; изменить cleanup только для активных строковых update. |
| `weblate/trans/models/translation.py`, `pending.py` | Долговечное сохранение source comments/references и штатный audit/pending lifecycle новых строк. |
| `weblate/formats/base.py`, `ttkit.py` | Минимальный интерфейс сохранения source notes только в поддерживающих descriptions форматах; не использовать exporter как writer runtime-файла. |
| `weblate/templates/trans/loc_kit_strings_update.html`, `loc_kit_strings_preview.html` | Загрузка, подготовка, preview, выполнение, частичный/полный итог, повтор. Существующие URL сохраняются. |
| `weblate/templates/component.html` | Ссылка владельцу на незавершённый/недавно завершённый импорт, чтобы результат можно было снова найти. |
| `weblate/trans/migrations/` | Django-generated migration после текущего head (обнаружен `0123_loc_kit_import_draft_application.py`); номер выбрать при реализации, не создавать вторую ветку миграций. |

Перед изменением экспортируемых символов выполнить LSP references. Не считать
таблицу выше полным списком потребителей: проверить serializers, tests, task
registry и вызовы cleanup. `LocKitImportDraft` используется и другими потоками.

## Общий контракт данных и фонового выполнения

Ниже **предлагаемые** поля/интерфейсы, а не утверждение об их наличии в коде.

### Долговечное состояние

Расширить `LocKitImportDraft` полями: `prepare_task_id`, `confirmed_options`
(JSON), `progress` (JSON: phase, processed_rows, total_rows, counters),
`next_row` (целочисленный курсор), `error_code`, `error_details` (без traceback
и секретов), `retry_phase`, `last_activity_at`, `finished_at`, `baseline_json`.
Использовать существующий `apply_task_id`, не дублировать его.
После подготовки `preview_json` содержит неизменяемый полный массив строк;
`profile_json` и `sheet` сохраняются. Progress/counters не переписывают input.
`baseline_json` хранит preview source/Explanation по ключу для проверки
конкурентных изменений; это отдельные данные, не изменение входных строк.

Добавить состояния PREPARING и COMPLETED; не переиспользовать CONSUMED как
видимый результат: `get_active` сейчас скрывает CONSUMED. Состояния:

- upload → PREPARING → PREVIEW_READY либо FAILED;
- confirm → APPLYING → COMPLETED либо FAILED;
- повтор FAILED возобновляет только сохранённую фазу с прежними options/cursor;
- повтор confirm в APPLYING/COMPLETED возвращает ту же страницу, не новую задачу;
- отмена допускается до apply; после старта кнопки удаления draft нет.

Утверждённые options фиксируют overwrite и доступные оси прав; worker может
сократить их по текущим правам, но не расширить молча после выдачи новых прав.
Нужны исходные значения Explanation для явной замены: если после preview поле
изменилось другим пользователем, не перетирать его старым подтверждением;
показать конфликт и оставить текущее значение.

Подготовка и применение имеют task UUID как fencing token. Изменение draft —
через `select_for_update`; обычный status GET читает без блокировки.
Worker перед каждым результатом или порцией проверяет соответствие своего UUID.
Payload в брокере содержит только draft PK; UUID передаётся как task_id,
не как токен сессии или содержимое таблицы.

Предлагаемые задачи в `weblate/trans/tasks.py`:

```text
prepare_loc_kit_string_update(*, draft_id: int) -> None
apply_loc_kit_string_update_draft(*, draft_id: int) -> None
```

Task id берётся из Celery request. Вызов из shell без совпавшей reservation
не применяет данные. Общий синхронный service остаётся request-free внутренним
механизмом порции, не альтернативным публичным неограниченным импортом.

### Публикация и повтор

1. HTTP в короткой транзакции сохраняет draft/state/options/UUID.
2. `transaction.on_commit` публикует задачу с тем же UUID. Быстрый worker должен
   видеть уже зафиксированную запись.
3. Ошибка публикации переводит только своё поколение в FAILED и разрешает
   повтор; не стирает payload/cursor и не заявляет, что работа успешно запущена.
4. Crash между commit и публикацией, потерянное сообщение и worker loss не
   оставляют вечное «выполняется»: по `last_activity_at` после 30 минут без
   прогресса статус предлагает повтор. Повтор заменяет UUID под row lock;
   поздний старый worker теряет право записи. Нельзя брать новый token, пока
   прежняя порция держит row lock. Сначала finite retry при lock timeout,
   затем FAILED с уже зафиксированными счётчиками.
5. Задачи используют позднее подтверждение сообщений и redelivery при потере
   worker по существующему паттерну `fix_failing_checks`; никаких утверждений
   «ровно одна доставка». Повторная доставка безопасна по состоянию и курсору.
6. Один delivery не работает все 24 часа: бюджет до следующего продолжения —
   меньше половины настроенного broker visibility timeout. По завершении
   порции при исчерпании бюджета зарезервировать новый UUID под row lock и
   публиковать продолжение после commit с interactive priority. Суммарный
   срок считается по всем продолжениям. Prepare имеет ограниченный вход и
   deadline; при его превышении сообщает ошибку, не запускает вечный reparse.
7. Даже повторная доставка **того же UUID** безопасна: каждая порция читает
   state/token/next_row/права/translation_set заново под lock, не из памяти
   worker. Terminal переход также сверяет свежий cursor и state.
   Добавить regression двух worker с одним UUID на границе visibility timeout.
8. Backoff lock retries ограничить пятью минутами и конечным числом попыток;
   сохранить next retry в progress, не копировать часовой backoff mass fix.

### Блокировки и границы записи

Единый порядок, когда нужны обе блокировки: repository → Component row через
`Component.locked_for_update()` → draft row. Нельзя захватывать draft, а затем
ждать repository: confirm/retry/cleanup используют только короткий draft lock,
status GET не берёт write locks.

Каждая порция (начальный размер 25 строк, внутренняя настройка алгоритма, не
лимит загрузки) в одной DB-транзакции:

1. Повторно проверить token, state, текущие права, source language, формат,
   существование/блокировку компонента.
2. Взять строки от `next_row`, заново сравнить ключи с текущим компонентом.
3. Новый ключ: штатное создание source/языковых units, targets, контекст,
   flags после targets, permitted Explanation. Старый ключ: никогда не менять
   source/targets/flags/note/location; только разрешённый Explanation.
   Targets писать через `Unit.translate(..., propagate=False, author=user)`
   с `is_batch_update=True`: текущий `.save(..., same_content=True)` в
   append не создаёт target pending/audit, а translate с default propagation
   может изменить соседние компоненты. Сохранение через translate не должно
   менять ни одного существующего unit вне списка новых ключей этого импорта.
4. Сохранить Change/PendingUnitChange через `store_update_changes()` **тех же
   экземпляров Translation**, на которых накоплены pending_unit_changes и
   update_changes. Затем завершить batch checks и инвалидировать кеши один раз
   на порцию, не на ячейку; stats scheduler используется как есть. После
   rollback использовать новые экземпляры, не переиспользовать stale caches.
5. В той же транзакции записать counters, next_row и heartbeat.

После rollback повтор создаёт всю незафиксированную порцию заново. После commit
повтор начинает со следующей порции: изменение пользователем уже обработанного
ключа не перезаписывается. Не обещать атомарность **всего файла**: завершённые
порции остаются, UI честно показывает частичное выполнение.

Создание отсутствующего языка отделить от row cursor: файл/git/scan не
откатываются общей DB-транзакцией. До row batches, под repository lock:
`component.commit_pending("loc-kit add language", None)`, затем
`component.add_new_language(language, None, create_translations=False,
show_messages=False)` и `component.create_translations_immediate(
force_scan=True, langs=[translation.language_code], user=draft.owner)`.
При повторе согласовать уже созданные файл/Translation и завершить scan.
Проверять завершение pending flush до scan; не считать deferred commit
завершённой записью файлов. Heartbeat обновлять до/после каждого языка.
`WeblateLockTimeoutError` повторяет эту задачу с сохранением priority:
не вызывать wrapper `create_translations`, который может незаметно поставить
background perform_load. Не ждать дочернюю задачу через `.get()`.
Отсутствие права/недоступный язык — явный per-language skip; IO/VCS ошибка —
FAILED. Старые строки в новом языке не заполняются из старых строк таблицы.

Не выполнять commit/push внешнего репозитория внутри row transaction.
После применения строк нужна отдельная retryable фаза finalizing: через
штатный commit_pending довести pending до файлов и проверить завершение.
Она не повторяет row writes; при deferred commit остаётся незавершённой,
повторная постановка использует `user_waiting=True` зависимости очереди.
Только после подтверждённого flush выставлять COMPLETED. Сбой сохраняет
cursor и все pending; UI различает «строки сохранены, запись файлов ожидается»
и «полностью завершено». Проверить reload файла после finalizing.

### Контекст, файлы и расхождения

- Explanation остаётся `Unit.explanation`, пишется через
  `apply_kit_explanations`/`Unit.update_explanation`; пустая ячейка не очищает
  поле, непустое старое по умолчанию сохраняется. Сохранить judge-stale preview;
  не запускать judge или MT автоматически.
- Character и другие parser `comments` новых строк становятся source `note`,
  `references` — source `location`, в том же порядке, что при создании из kit.
  Для PO comments должны пережить pending flush, повторный parse и restart.
  Одного `unit.note = ...; save()` недостаточно: сейчас native pending creation
  не переносит notes в runtime file. Расширить pending metadata и format writer
  для **новых** units с описаниями; не путать `formats/base.py` exporter
  `store_unit_metadata` (он добавляет Explanation в экспорт) с runtime store.
  Точки изменения: добавить `note`/`location` keyword-only arguments в
  `Translation.add_unit/_add_unit_locked`, только для source Unit; для source
  `add_unit=True` сохранять их snapshot в `PendingUnitChange.metadata`.
  `find_or_add_pending_store_unit` принимает pending_change и передаёт
  snapshot в runtime format interface сохранения metadata нового unit.
  Для PO этот interface вызывает toolkit `addnote(..., origin="developer")`
  и `addlocation` в порядке parser references; он применяется также при
  восстановлении уже созданного в файле pending unit, без дублей notes.
  Capability явно объявляется поддерживающими форматами, default — unsupported.
  Не включать в snapshot Explanation и не менять exporter metadata.
- Если формат компонента не умеет хранить source descriptions/references,
  непустые такие поля требуют явной ошибки preview с указанием поля/формата,
  а не потери или неоговорённого слияния с Explanation. Например, JSON без
  comments с пустым Character продолжает работать; JSON с Character не заявлять
  полностью поддержанным. Добавление нового DB-поля speaker не входит в этот план.
- Изменённый source старого ключа: таблица расхождений key/old/new, счётчик,
  явное «не изменяется». Explanation для таких строк автоматически не применять:
  контекст может относиться к другому исходнику. Это показывать отдельно.
- Не скрывать редкие языковые колонки из-за `DEFAULT_MIN_FILL`: в existing-update
  явные валидные коды языков считаются намеренными независимо от заполненности.
  При неизвестных непустых колонках показать интерпретацию comments/references.
  Source определяется компонентом; отсутствующая колонка source — ошибка.

### Защита входа и срок жизни

Сохранить `validate_component_zip_upload_size` и extension validator. До openpyxl
проверять XLSX как ZIP через `validate_zip_members`/`ZipSafetyLimits`: максимум
1000 членов и aggregate uncompressed size не более
`settings.TRANSLATION_UPLOAD_MAX_SIZE` по существующему spreadsheet-паттерну.
Ограничить фактически читаемые rows/cells/текст также для CSV/TSV и неверных XLSX
worksheet dimensions: бюджет суммарного UTF-8 текста и количества cells от того
же настроенного byte limit (каждая ячейка стоит минимум 1); это техническая
защита ресурсов, не число записей в БД. Завершать чтение при превышении, а не
после материализации всей таблицы. Никакого Django import в loc_kit_ingest.
Формулы не вычислять и не использовать cache как достоверный текст: сообщать
координату формулы; не ломать обычный строковый текст, начинающийся с `=`.

Не менять общий срок жизни glossary/creation draft (до часа). Для строкового
update до запуска — прежний срок, при активной задаче — продление на час при
heartbeat, после COMPLETED/FAILED — один час для просмотра/повтора. Heartbeat
явно обновляет expires_at: model.save не продлевает заполненное поле.
Cleanup использует построчный `select_for_update(skip_locked=True)` и
перепроверяет expiry/state/heartbeat, не bulk delete выбранных ранее строк.
Зависшая задача переводится в retryable FAILED с часом для восстановления.
Отдельный абсолютный срок для активного update — 24 часа; после него остановка
между порциями с частичным отчётом, затем час retention. Эти изменения отразить
в threat model: текущее «все drafts максимум час» иначе станет ложным.
После успешной подготовки и сохранения payload исходный upload удалить:
apply/retry читает preview_json. Failed prepare хранит файл до expiry для retry.
Скачивание исходного файла публично не добавляется; progress/result также
owner+session bound и требуют текущего доступа к компоненту. Потеря прав не
должна открывать metadata через task polling.

## Задачи реализации

### 1. Полный файл проходит подготовку и даёт корректный preview

**Outcome:** исходный XLSX пользователя (>5 000 cells) достигает preview без
ручной обработки, без записи units. Семантика parser общая с CSV/TSV.

**Files/interfaces:** reader/infer, формы, loc_kit.py, model/migration,
StartView и PreviewView, prepare task; контракт состояния выше.

**Actions:**

- [ ] Перенести сериализацию в request-free слой и обновить все references.
- [ ] Добавить поля и миграцию; изолировать новую lifecycle-семантику от glossary
  и component-creation consumers.
- [ ] Сохранять файл потоково в private storage, резервировать prepare после
  проверки upload permission и дешёвых лимитов, публиковать после commit.
- [ ] В prepare выполнить bounded parse, source/schema/language/duplicate
  validation, сохранить полный payload и preview; учитывать разрешённые оси,
  source drift, пустые/редкие языки и unsupported context.
- [ ] Убрать 5 000-cell rejection на upload; apply guard убрать в задаче 2.
- [ ] Вывести подготовку/ошибку/preview в существующем template с escaped
  значениями и номером строки; не выполнять тяжёлый diff на каждом polling GET.

**Verification:** `test_loc_kit_ingest_contract.py`, `test_loc_kit_drafts.py`,
standalone `loc_kit_ingest/tests/`. Маленький синтетический XLSX с title row,
новым ключом, Explanation, Character, редким и полностью пустым target language;
отдельно regression >5 000 cells. До изменения valid full file отвергается;
после PREVIEW_READY, zero Unit/Change writes до confirm. Duplicate key в конце,
ошибочный source language, zip bomb, огромные dimensions и формула дают
диагностику до apply; wrong owner/session и glossary token не доступны.

### 2. Фоновое применение порциями без потери контекста

**Depends:** задача 1, формат payload и состояния закреплены.

**Outcome:** новые ключи добавлены во все доступные языки с переданными
значениями, Explanation и поддерживаемыми source notes; старые данные защищены.

**Files/interfaces:** loc_kit.py coordinator/append services, tasks.py apply,
translation.py/pending.py, minimal format write interface, соответствующие
format/pending tests; использовать protocol и lock ordering выше.

**Actions:**

- [ ] Перед row batches подготовить языки с повторяемым согласованием файла/DB.
- [ ] Сделать атомарную порцию source + targets + context + flags + Explanation
  вместе с pending/audit и cursor; не обновлять targets через обход штатного
  сохранения, теряющий pending, проверки или attribution.
- [ ] Подключить source notes/references к pending создания и runtime PO writer.
  Изменения interface проверять LSP references во всех форматах.
- [ ] Зафиксировать Explanation overwrite baseline и source-drift skips,
  повторные permission checks, per-language/axis unavailable outcomes.
- [ ] Удалить obsolete `validate_loc_kit_string_update_size` и constant,
  мигрировать прямых callers/tests, не оставлять обходной HTTP apply.
- [ ] После каждого commit сообщать фактический progress; завершить итогом,
  включая skipped/conflicts/unavailable, а не одним boolean success.

**Verification:** существующие `LocKitStringsUpdateServiceTest`,
`LocKitStringsUpdateViewTest` в `test_loc_kit_ingest_contract.py`; релевантные
`test_files.py` и format tests при изменении pending интерфейса. Сценарии:
existing target отличается от файла, existing flag read-only, blank target,
новые флаги read-only, отсутствие одного права, source drift, новая language,
совпадающий/непустой/пустой/конкурентно изменённый Explanation. Проверить
сохранность **после pending flush и reload** PO: translated targets, Character
в source note, Explanation только в БД, audit без дублей. JSON без Character
остаётся рабочим; неподдерживаемый контекст даёт ошибку до мутаций.

### 3. Повтор, сбой, уход со страницы и очистка

**Depends:** задача 2; общий mutation boundary реализует один integration owner.

**Outcome:** закрытие страницы не останавливает импорт; результат вновь доступен
из компонента; двойной confirm и worker redelivery не портят данные. Сбой не
маскируется сообщением «ничего не изменено», если часть файла уже применена.

**Files/interfaces:** model draft, tasks cleanup/prepare/apply,
PreviewView confirm/retry/status, component template, existing task progress
helpers при необходимости (они не заменяют durable DB result).

**Actions:**

- [ ] Реализовать token fencing, on_commit publication, finite retries,
  stale recovery, per-chunk cursor и terminal states по общему контракту.
- [ ] В статусе показывать фазу/строки/результат/ошибку/повтор; ограниченный
  polling только во время работы с backoff при ошибке сети, keyboard controls,
  aria-live без чтения всей таблицы при каждом обновлении.
- [ ] Reuse существующий preview URL для состояния, добавить owner-scoped
  ссылку на активный/последний retained update на странице компонента.
- [ ] Сделать cleanup совместимым с lease/row lock и terminal retention;
  сохранить прежние glossary/creation expiry tests.
- [ ] Завершение без изменений — отдельный понятный результат без ошибки.

**Verification:** transactional concurrency tests (не внешний transaction
обычного TestCase) и отдельные соединения: две доставки одного UUID, два POST
confirm, два draft одного компонента, cleanup vs active chunk; lock order без
deadlock. Crash до commit порции: никаких её units/cursor. Crash после commit:
retry не повторяет порцию и не перетирает последующее ручное изменение.
Сбой между language file creation и DB scan восстанавливается. Publish failure
и потерянная публикация после commit дают retryable state. Старый worker после
смены token не пишет. Отзыв прав/удаление компонента во время работы останавливает
дальнейшие мутации. Ошибка второй порции оставляет точный partial result.

### 4. End-to-end доказательство и обновление инструкций

**Depends:** задачи 1–3; без реализации этого исхода работа не завершена.

**Files:** `docs/product/guides/loc-kit-ingest.md`,
`docs/product/guides/producer-guide-weblate.md`, `docs/security/threat-model.rst`,
верхняя unreleased секция `docs/changes.rst`; статусы двух старых планов и этот
план. Не переписывать историю старых результатов — добавить ссылку на замену
синхронного ограничения новым контрактом.

**Actions и verification:**

- [ ] В отдельном тестовом компоненте po-mono на изолированном стенде загрузить
  локальный `localization.xlsx`, затем копию с 3 уникальными новыми ключами:
  один без переводов, один с несколькими targets/Explanation, один с Character.
  Старый target в файле намеренно отличается от Weblate. Preview показывает
  ровно 3 новых; после confirm добавлены ровно 3, старый target неизменен,
  контекст сохранён после commit/reload.
- [ ] Повторить полный файл: 0 новых, без дубликатов/audit дублей; объяснения
  unchanged. Отдельно overwrite existing Explanation и конфликт после preview.
- [ ] Создать полный синтетический файл с >5 000 **реальных новых** language
  cells: убедиться, что решение не просто перенесло лимит на effective diff.
  Закрыть вкладку после confirm, вернуться со страницы компонента, проверить
  durable result. Для большого XLSX проверить и фазу подготовки.
- [ ] Записать реальные upload/prepare/apply timings, peak memory worker и
  максимальное время удержания component lock. На основании измерений уменьшить
  внутреннюю порцию, если нужно; не возвращать просьбу разрезать файл.
- [ ] Проверить desktop/mobile, keyboard/focus, labels/errors, escaped text,
  permission denied, empty/no-change, partial/retry. Использовать текущий
  browser skill `lightpanda-browser`; если нет visual runtime, явно заблокировать
  UI-acceptance, не подменять проверку HTTP status кодом.
- [ ] Обновить руководство: один XLSX, source и key requirements, safe skips,
  Explanation overwrite, поддержка Character по формату, результат/повтор,
  разумные file limits и single-sheet scope. Обновить lifecycle/data retention
  и authenticated worker trust boundary в threat model.
- [ ] Удалить только созданные этой проверкой временные файлы/fixtures;
  исходный файл пользователя не менять, в git его не включать.

## Команды проверки и условия среды

Команды ниже — для **реализации**, не результаты этой плановой сессии.
Не запускать build/restart shared dev stack без разрешения. Web Python reload
не обновляет Celery; для smoke нужен отдельный актуальный worker/стенд либо
отдельно разрешённый restart. Production не использовать как fixture.

```sh
# standalone, cwd loc_kit_ingest
uv run pytest

# В уже работающем dev контейнере, тестовая БД (не живые данные):
./rundev.sh test -n 0 weblate/trans/tests/test_loc_kit_ingest_contract.py weblate/trans/tests/test_loc_kit_drafts.py
./rundev.sh test -n 0 weblate/trans/tests/test_files.py

# после всех изменений, один интеграционный проход:
uv run prek run --all-files
```

Для новых model fields с доступной настроенной dev/test БД:
`uv run ./manage.py makemigrations trans`, затем
`uv run ./manage.py makemigrations --check --dry-run` с
`DJANGO_SETTINGS_MODULE=weblate.settings_test` и test DB environment из
`docs/contributing/tests.rst`. Миграцию live не применять без разрешения.
Регрессионные тесты форматов подобрать по изменённым интерфейсам после LSP
references; не выдумывать имя существующей fixture/test class.

## Порядок, проверка готовности и handoff

Задачи 1 → 2 → 3 → 4 зависят от общего draft/service протокола; не давать
нескольким агентам одновременно редактировать loc_kit.py/tasks.py/create.py.
После фиксации общего контракта можно отдельно поручить format metadata и
UI/report работу с непересекающимися файлами; integration owner остаётся один.

План покрывает full XLSX, новые/старые ключи, все language cells, Explanation,
Character, отсутствие удаления, preview drift, progress/recovery, реальные
записи >5 000 cells, file safety, права, draft cleanup, pending/audit и docs.
Отдельное продуктовое ограничение явно сохранено: комментарии новых строк
требуют формата, умеющего хранить source descriptions; данный PO loc-kit
подходит. JSON с новым DB speaker metadata потребовал бы отдельного решения.

Перед реализацией: согласование этого плана пользователем. Перед завершением:
независимое code review и фактические результаты проверок; обновить статус и
приложить доказательства. Коммит/пуш — по `AGENTS.md`, без требования коммита
на каждый подпункт. Деплой — отдельное разрешение, включая миграции и workers.

## Интеграция с текущей работой над очередью

Этот план выполняется поверх результата
`docs/product/plans/2026-09-10-producer-tasks-ahead-of-housekeeping.md`,
а не параллельно реализует собственные приоритеты и scheduler статистики.
Перед началом кода взять интегрированную версию той работы и сверить фактические
интерфейсы; пока она в отдельном ворктри, это внешняя зависимость реализации,
не причина менять её файлы из main.

- Использовать `INTERACTIVE_TASK_PRIORITY` из `weblate/utils/celery.py`
  в `apply_async` **конкретных** upload/confirm/retry постановок prepare/apply.
  Не размножать число 0, не добавлять priority в декораторы задач.
- Если часть работы публикуется через `Component.queue_background_task` или
  `queue_commit_pending`, передавать согласованный `user_waiting=True`
  только для продолжения операции, которую запустил продюсер. Не выводить
  interactive из наличия `user_id`.
- Для новой task reservation нужен заранее выбранный UUID: если существующий
  component helper не принимает его, использовать обычный `apply_async`
  с `task_id` и общей константой; не расширять helper и не создавать новый
  диспетчер только ради этой интеграции.
- Подготовка языка выполняет `create_translations_immediate` в текущей
  интерактивной задаче; lock timeout повторяет её, не создаёт background
  `perform_load`. Протокол finalizing использует предусмотренный зависимостью
  interactive deferred commit. Не менять семантику автоматических callsites.
- Настройки Redis, `CELERY_TASK_DEFAULT_PRIORITY`, очереди и supervisord этим
  планом не меняются. Cleanup и порождённые пересчёты stats остаются background.
  Сохранения units используют уже внедрённый stats scheduler, не `.delay`
  в обход его склейки и не новую склейку «на импорт».
- Retry той же интерактивной задачи сохраняет delivery priority; новая
  постановка продолжения сохраняет явное происхождение операции.
  Приоритет не прерывает уже выполняющиеся задачи.

Дополнение к задаче 4: на изолированном Redis/worker fixture с готовой
зависимостью положить 300 background stats-задач, затем загрузить XLSX через UI.
Prepare и подтверждённый apply принимаются до ещё не зарезервированного
background backlog. Автоматические stats/cleanup остаются на default priority.
По завершении и дренировании follow-up stats суммы компонента/проекта совпадают
с языками. Этот smoke не должен останавливать shared worker без отдельного
разрешения. Общие потенциально конфликтующие файлы: tasks.py, create.py,
component.py, utils/celery.py, test_loc_kit_ingest_contract.py, docs/changes.rst;
после интеграции сначала сверить их, не перезаписывать патчи другой работы.

## Архитектурное ревью плана

2026-09-11 независимый architect дал `approve-with-changes`. В план внесены:
явный `propagate=False` и target pending/audit, inline scan новых языков без
падения в background priority, ограничение одной доставки относительно Redis
visibility timeout, snapshot source notes/references в pending и обязательная
finalizing-фаза. Это ревью плана, не подтверждение реализации.

Дополнения к приёмке задачи 2: новый target остаётся в PO после commit/reparse;
существующий unit с тем же source/context в соседнем компоненте не изменяется;
notes не дублируются при повторном flush; positions сохраняют входной порядок.
К задаче 4: обновить также раннее ограничение idempotent import в руководстве,
различая создание компонента и повторное append-update. Проверки плана:
все 24 первоначально извлечённые пути существуют; scoped prek проходит при
исключении двух общих блокеров — reuse на `.omp/lsp.json` и typos на ранее
существовавших файлах. Они не исправляются в рамках этого документа.
