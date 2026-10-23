# План: обновить строковый компонент из loc-kit таблицы

Дата: 2026-08-18. Переработан: 2026-09-08.
Статус: **реализован и проверен 2026-09-08** как задача 4 связанного плана
`2026-08-28-kit-explanation-on-string-import.md` (`loc-kit-strings-update`:
start/preview/confirm, `append_translation_strings` + общий
`apply_kit_explanations`). Осознанное отступление: confirm синхронный,
в одном HTTP-запросе, без Celery-протокола `APPLYING`/`FAILED`/
`apply_task_id`, `reservation` и retry-семантики, описанных ниже в этом
плане (раздел «Проверка») - вместо этого он повторяет уже принятый паттерн
`append_glossary_terms`/`LocKitGlossaryPreviewView._apply_update`.
Соответственно, критерии проверки этого плана про fast-worker visibility,
retryable/terminal failure, duplicate task delivery и coordinator как
Celery-задачу неприменимы и не проверялись. Синхронный вариант утверждён с
пределом 5 000 непустых translation/flag/Explanation cells: он проверяется
до draft и перед service apply, поэтому большой кит не создаёт мутаций, а
пользователь получает требование разделить таблицу. Компромисс
задокументирован в `docs/product/guides/loc-kit-ingest.md` (раздел «Right
size, not right protocol»); остальные критерии («Проверка», строки 262-296
этого файла, за вычетом Celery-специфичных) покрыты
`weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitStringsUpdateServiceTest`
и `::LocKitStringsUpdateViewTest`.
Связанный план общего Explanation-контракта и мастера создания:
`docs/product/plans/2026-08-28-kit-explanation-on-string-import.md`.
Ревью исходного Explanation-плана:
`docs/product/reviews/2026-09-08-kit-explanation-plan-review.md`.
Пост-мерж ревью нашло и закрыло реальный дефект: `append_translation_strings`
не брала `component.locked_for_update()`, в отличие от соседних
`apply_kit_explanations`/`append_glossary_terms`, из-за чего двойной или
конкурентный confirm одного драфта не был идемпотентен - см. второй
Follow-up в том же файле ревью.
Follow-up code review закрыл ещё два Important: проверены new-key
`read-only`/пустой source-language contracts, а preview считает target-юниты
с актуальным judge-вердиктом, которые смена Explanation сделает stale.
`loc_kit_explanations` также доезжает через deferred `perform_load`, а не
теряется при lock timeout; см. третий Follow-up в файле ревью.
Follow-up synchronous safety: измерение крупнейшего отслеживаемого кита
(`analysis/data/heart-abyss-hub-1-units-9lang.tsv`, 396 строк и 3 960
непустых cells) привело к пределу 5 000 cells. Guard существует в HTTP
входе и в coordinator, поэтому прямой/retry вызов тоже не сможет обойти
предел.

## Цель

Одна форма `loc-kit-strings-update` применяет CSV/TSV/XLSX к существующему
обычному компоненту:

- добавляет отсутствующие ключи и их языковые значения;
- применяет `read-only` и другие разрешённые флаги только к новым ключам;
- добавляет или явно перезаписывает `Unit.explanation` существующих и новых
  исходных юнитов;
- никогда не меняет source, targets или flags уже существующего ключа.

Отдельного explanation-only view не создаётся. Это точка входа для
существующего компонента в общий контракт из связанного плана; мастер
создания вызывает тот же Explanation apply после `create_translations`.

Основной реальный сценарий — игровой JSON-компонент: строгий JSON не несёт
developer comments, поэтому DB-only `Unit.explanation` является его
единственным источником попометного контекста для редактора, MT и judge.

## Существующие основания

- `loc_kit_ingest.reader.read_sheets` читает CSV/TSV/XLSX.
- `infer_profile` и `parse_component` дают `StringUnit` и диагностики.
- `LocKitImportDraft.target_component` хранит временную загрузку для
  существующего компонента.
- Глоссарный поток уже задаёт безопасный паттерн start -> preview -> confirm
  (`LocKitGlossaryUpdateStartView`, `append_glossary_terms`).
- `Translation.add_unit(..., is_batch_update=True)` добавляет новый
  монолингвальный ключ сразу во все существующие языки.
- `Unit.update_explanation` хранит DB-only Explanation и audit trail для
  форматов с `supports_explanation=False`.

Связанный Explanation-план владеет schema v3, узкими заголовками
Explanation, `StringUnit.explanation`/`flags` и общей функцией
`apply_kit_explanations`. Этот план не создаёт второй парсер или второй
Explanation-сервис.

## Общий preview/apply-контракт

Preview строится из одного `ParseResult` и классифицирует каждую строку
независимо по двум осям.

### Строка

- `new`: ключа нет, строка может быть добавлена;
- `existing`: ключ есть, source/targets/flags всегда остаются без изменений;
- `invalid`: ERROR-диагностика блокирует весь confirm.

### Explanation

- `set`: ключ существует или будет создан, текущее Explanation пусто;
- `unchanged`: значение уже равно входному;
- `already_in_note`: file-owned `note` равен входному значению; Explanation
  всё равно может быть установлен как редактируемый DB-owned контекст;
- `blank`: ячейка пуста;
- `missing_key`: ключ не существует и не будет создан из-за прав/ошибки;
- `would_overwrite`: непустое значение отличается; по умолчанию пропуск,
  применяется только с явным `overwrite=True`;
- `unavailable`: у пользователя нет `source.edit`.

Preview также показывает:

- новые и существующие ключи;
- по языкам: значения, создаваемые и недоступные языки;
- строки с пустым источником;
- строки с `flags`;
- Explanation по исходам выше;
- число юнитов с актуальным judge-вердиктом, которые станут stale;
- все ERROR-диагностики до любой мутации.

Confirm вызывает единый coordinator под `transaction.atomic` и
`component.locked_for_update()`:

```python
def apply_loc_kit_string_update(
    *,
    user: User,
    component: Component,
    preview: StringsUpdatePreview,
    overwrite_explanations: bool,
) -> StringsUpdateResult: ...
```

Coordinator сначала добавляет разрешённые новые строки, затем передаёт
исходные `StringUnit` в общий `apply_kit_explanations`. Результат содержит
`added`, `existing`, `flagged`, per-language counters,
created/unavailable languages и `KitExplanationApplyResult`.

Если preview содержит ERROR, task не меняет ничего. Частичные `unavailable`
по разрешениям или языкам не откатывают остальные разрешённые операции, но
всегда видны до confirm и в результате.

## Права и eligibility

Компонент обязан:

- быть не glossary и не `locked`;
- иметь монолингвальный template (`has_template()`);
- поддерживать добавление юнитов для string-операции;
- иметь `file_format_cls.supports_explanation == False` для
  Explanation-операции.

Доступ к start/preview разрешён, если пользователь может выполнить хотя бы
одну мутацию:

- новые строки: `upload.perform` и право управления/добавления юнитов;
- Explanation: `source.edit` на компоненте.

Preview отдельно показывает недоступную ось. Пользователь только с
`source.edit` может применить пояснения к существующим ключам, но новые
строки будут `unavailable`. Пользователь только с upload/add может добавить
строки, но Explanation будут `unavailable`. Наличие `upload.perform` никогда
не даёт право менять Explanation.

Недостающий язык создаётся только при `translation.add`; иначе этот язык
`unavailable`, остальные операции продолжаются.

## Задачи

### 1. Профиль и `StringUnit`

**Владелец:** задача 1 связанного Explanation-плана.

Schema v3 для PO добавляет scalar metadata `flags` и `explanation`.
`_FLAGS_HEADERS = {"flags", "weblate-flags", "флаги"}` проверяется до
fallback в comments. Explanation использует только узкий набор
`explanation`, `explanations`, `пояснение`, `пояснения`; `Comment`,
`Context`, `Description`, `Note` сохраняют прежнюю семантику developer
comment. `StringUnit` получает оба значения, PO-рендер их не пишет.

Флаг валидируется через `weblate.checks.flags.Flags`; невалидное значение —
ERROR preview. Кит без обеих колонок не меняет v1-профиль и поведение.

### 2. Добавление новых строк

**Файлы:** `weblate/trans/loc_kit.py` и contract tests.

Предлагаемый внутренний интерфейс:

```python
def append_translation_strings(
    request: AuthenticatedHttpRequest,
    component: Component,
    preview: StringsUpdatePreview,
) -> StringsAppendResult: ...
```

Внутри уже взятого component lock:

1. Собрать существующие `Unit.context`.
2. Существующие ключи полностью пропустить для source/targets/flags.
3. Для нового ключа вызвать
   `source_translation.add_unit(context=key, source=..., target=[],`
   `is_batch_update=True)`.
4. Записать непустые targets в языки из профиля.
5. Создать отсутствующий язык при `translation.add`, иначе отметить
   `unavailable`.
6. После targets применить валидированные flags к исходному юниту: порядок
   обязателен, потому что `read-only` блокирует последующую запись.

Пустой source разрешён только в batch-path. Для строки с пустым `ru` и
непустым `en`:

- с `flags=read-only` английское значение сохраняется, все языки получают
  read-only;
- без флага источник остаётся untranslated с текущим согласованным
  fallback-поведением;
- полностью пустая строка — `po.key_without_content`, ERROR до confirm.

### 3. Explanation в том же coordinator

**Владелец функции:** задача 2 связанного Explanation-плана.

После добавления новых строк coordinator вызывает `apply_kit_explanations`
для всех строк preview. Для существующих ключей изменяется только
Explanation; для новых — Explanation ставится после появления исходного
юнита. Checkbox «перезаписать непустые пояснения» показывается только при
`would_overwrite > 0` и `source.edit`.

Explanation-набор не применяется в HTTP-транзакции. Confirm выполняет
следующий протокол:

1. В `transaction.atomic` получить draft через `select_for_update`; допустить
   только owner/session-bound `PREVIEW_READY` или retryable `FAILED`.
2. Сгенерировать `task_id = uuid.uuid4()` **до публикации** и одним update
   сохранить `state=APPLYING`, nullable UUIDField
   `apply_task_id=task_id`.
3. Зарегистрировать
   `transaction.on_commit(lambda: task.apply_async(...,`
   `task_id=str(task_id)))`. `delay()` здесь запрещён: его id возникает после
   публикации и оставляет fast-worker race.
4. Если callback не опубликовал сообщение в broker, отдельной транзакцией
   compare-and-set только для того же
   `(pk, state=APPLYING, apply_task_id=task_id)` перевести draft в
   `FAILED`, сохранить файл и показать enqueue failure. Не оставлять
   reservation в `APPLYING`.

Bound task использует `acks_late=True` и `reject_on_worker_lost=True`, заново
загружает `User`, component, draft, profile и preview. В одной транзакции он
берёт draft и component lock, требует `state=APPLYING` и
`apply_task_id == self.request.id`, затем выполняет string и
Explanation-части. Успех переводит draft в `CONSUMED`; файл удаляется через
`transaction.on_commit`. Duplicate delivery после `CONSUMED` — no-op,
несовпадающий task id никогда не применяет данные. Retryable exception
сохраняет тот же reservation для Celery retry; окончательная ошибка отдельной
транзакцией compare-and-set переводит только свой reservation в `FAILED`,
доступный для явного retry до expiry.

### 4. Start, preview, confirm

**Файлы:** `weblate/trans/views/create.py`, формы, `weblate/urls.py`.

- `LocKitStringsUpdateStartView`: проверяет eligibility и наличие хотя бы
  одной доступной операции, читает файл локально, затем создаёт
  `LocKitImportDraft(target_component=component)`.
- `LocKitStringsPreviewView`: вызывает schema v3 infer/parse, строит обе
  классификации и permission matrix, не меняет компонент.
- `LocKitStringsConfirmView`: заново загружает и проверяет draft,
  permissions и компонент, отклоняет stale/consumed/expired draft и
  выполняет UUID/reservation/on-commit протокол выше; двойной confirm
  проигрывает `select_for_update`/state check и не публикует вторую задачу.
- URL: `loc-kit-strings-update`; отдельного explanation URL нет.

Черновик остаётся owner-, session-, project- и component-bound. Глоссарный
confirm продолжает отказывать draft с string target; component-creation
confirm продолжает отказывать draft с `target_component`.

### 5. Шаблоны и меню

**Файлы:** `weblate/templates/trans/loc_kit_strings_update.html`,
`weblate/templates/trans/loc_kit_strings_preview.html`,
`weblate/templates/component.html`.

Форма использует `FormHelper(self)` и `form_tag = False`. Preview показывает
обе оси и объясняет частичный результат до confirm. Пункт меню видим, когда
доступна хотя бы string- или Explanation-операция, а не только по
`user_can_upload_translation`; запрещённый тип мутации не показывается как
доступный.

После confirm сообщение использует точные counters результата, включая
`would_overwrite`, `unavailable` и created/unavailable languages.

### 6. Документация

Документацией владеет задача 6 связанного Explanation-плана:

- loc-kit guide описывает schema v3, preview и существующий-component flow;
- game-repo contract называет UI основным способом после реализации, а
  API PATCH и `explanations.json` сохраняет как программный/аварийный путь и
  долговечный источник восстановления;
- одна changelog-запись покрывает создание и обновление.

## Проверка

Standalone (`cd loc_kit_ingest && uv run pytest`):

- `flags` и Explanation распознаются отдельно от languages/comments;
- Comment + Explanation сохраняются как разные поля;
- невалидный flag и две explanation-колонки дают ERROR;
- v1/v2 и кит без служебных колонок не меняются;
- ни flags, ни Explanation не появляются в PO.

Weblate contract
(`weblate/trans/tests/test_loc_kit_ingest_contract.py`):

- таблица с 2+ языками добавляет новый ключ во все доступные языки;
- пустой `ru` + `en` соблюдает read-only/fallback-контракт;
- существующий ключ не меняет source/targets/flags;
- существующий и новый ключ получают Explanation при `source.edit`;
- overwrite требует явной галочки; повторный прогон идемпотентен;
- без `source.edit` Explanation unavailable, но разрешённые строки
  добавляются; без string-permissions новые строки unavailable, но
  разрешённые Explanation применяются;
- отсутствующий язык создаётся только при `translation.add`;
- ERROR блокирует все мутации; partial unavailable — нет;
- быстрый worker не может увидеть draft до коммита
  `APPLYING`/`apply_task_id`;
- исключение из `apply_async` переводит только свой reservation в `FAILED`
  и сохраняет файл;
- двойной confirm публикует одну задачу; duplicate delivery с тем же id
  применяет один раз; чужой task id не применяет ничего;
- retryable failure сохраняет `APPLYING`, окончательная ошибка переводит
  свой reservation в `FAILED`, явный retry получает новый UUID;
- consumed draft не применяется повторно;
- coordinator получает `User`, а не HTTP request, и возвращает точные
  counters через task result.

Живой smoke-test в dev-контейнере:

1. Загрузить небольшую таблицу с новым и существующим ключом,
   Explanation + Comment, тремя языками и `read-only`.
2. Проверить preview обеих осей и permission matrix.
3. Confirm; проверить новые строки, Explanation, неизменность существующих
   source/targets/flags и отсутствие metadata в файлах.
4. Повторить с overwrite и без `source.edit`.

Перед Weblate-тестами синхронизировать standalone-пакет:
`cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`.
После целевых тестов — `uv run prek run --all-files`.

Деплой не входит. `l10n.herocraft.com` и платные LLM-вызовы требуют
отдельного явного разрешения.
