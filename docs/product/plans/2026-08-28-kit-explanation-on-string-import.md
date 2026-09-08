# Explanation из loc-kit для новых и существующих строковых компонентов

Дата: 2026-08-28. Переработан: 2026-09-08.
Статус: редакция плана согласована; реализация не согласована и не начата.
Ревью: `docs/product/reviews/2026-09-08-kit-explanation-plan-review.md`.
Связанный план существующего компонента:
`docs/product/plans/2026-08-18-loc-kit-table-add-strings.md`.

## Цель

Колонка `explanation` из CSV/TSV/XLSX должна попадать в
`Unit.explanation` исходной строки и оставаться только в базе Weblate:

- при создании компонента через вкладку «Отправить файлы перевода» — из той
  же загрузки, без повторной загрузки файла;
- при загрузке таблицы в существующий строковый компонент — через общий
  preview/apply-поток из связанного плана.

Объяснение не должно попадать в `.po` или игровой репозиторий. Исходные и
целевые тексты, состояния, флаги и метки существующего ключа не меняются.

## Что происходит сейчас

Контекст не теряется. Неязыковая текстовая колонка становится `comments`
(`loc_kit_ingest/infer.py:374-378`), рендерится как developer comment `#.`
только в PO языка-источника (`loc_kit_ingest/writer.py:52-57`) и загружается
как `Unit.note`. `note` уже получают:

- машинный перевод (`weblate/machinery/llm.py:574-581`, `:1071-1075`);
- judge (`weblate/trans/judge.py:915-918`,
  `weblate/trans/judge_loop.py:122-123`);
- редактор как «Source string description»
  (`weblate/templates/translate.html:155-163`, `:918-923`).

Проблема другая: `note` принадлежит файлу, а не локализации.
`Unit.explanation` хранится в базе, редактируется под `source.edit`, имеет
отдельную семантику в LLM-промпте и для строгого JSON игрового компонента
является единственным носителем контекста: комментариев в таком файле нет
(`docs/product/guides/game-repo-integration-contract.md:163-168`,
`:239-261`).

Создание из таблицы сейчас тоже не может довести попометные данные до
юнитов: файл разбирается внутри `TemporaryDirectory`
(`weblate/utils/views.py:753-799`), наружу выходит только `kit_info`
(`:809-825`), которое используется для полей формы и сообщений
(`weblate/trans/views/create.py:624-682`).

## Выбранная архитектура

Это один контракт с двумя точками входа, а не два импортёра:

1. Общая функция разбирает и применяет `{ключ -> explanation}`.
2. Мастер создания сохраняет исходную загрузку как
   `LocKitImportDraft` и после `create_translations` автоматически
   резервирует request-free задачу общей функции (A').
3. Существующий компонент использует ту же функцию внутри единственного
   `loc-kit-strings-update` preview/apply-потока. Отдельного
   explanation-only view не создаётся.

### Контракт данных

В `loc_kit_ingest` вводится схема **v3 для PO metadata**:

- v1 сохраняет прежнюю точную семантику keyed PO и
  term-description-pairs TBX;
- v2 сохраняет прежнюю точную семантику TBX `record-map`;
- v3 разрешена только для `kind: "po"` и добавляет два необязательных
  скалярных поля component: `explanation` и `flags`, каждое с
  `column`, `header`, `name`;
- профиль PO без обеих служебных колонок продолжает выводиться как v1;
- TBX с `schema_version: 3` отклоняется.

Один bump обслуживает этот план и уже принятое поле `flags` из связанного
add-strings-плана; v1 не переинтерпретируется задним числом.

`StringUnit` получает `explanation: str = ""` и `flags: str = ""`.
`ParsedUnit` остаётся протоколом. PO-рендер не использует оба поля:
служебные данные доступны потребителю `ParseResult`, но не появляются в
артефакте.

Для Explanation используется узкий закрытый набор заголовков:
`explanation`, `explanations`, `пояснение`, `пояснения`.
`comment`, `comments`, `note`, `context`, `description` и их текущие
локализованные варианты остаются developer comments. Две заполненные
explanation-колонки — ошибка инференции, а не склейка или выбор первой.

### Общая функция применения

Предлагаемый интерфейс в `weblate/trans/loc_kit.py`:

```python
def apply_kit_explanations(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite: bool,
) -> KitExplanationApplyResult: ...
```

`KitExplanationApplyResult` считает `set`, `unchanged`, `blank`,
`missing_key`, `would_overwrite` и `already_in_note`; preview использует ту
же классификацию до мутации.

Инварианты:

- сопоставление по `StringUnit.key == Unit.context` среди исходных юнитов;
  `writer.py:49` кладёт ключ в `msgid`, а `PoMonoUnit.context`
  (`weblate/formats/ttkit.py:847-860`) возвращает его при пустом `msgctxt`;
- компонент не glossary, не `locked`;
- `component.file_format_cls.supports_explanation` обязан быть `False`;
  тем самым DB-only обеспечивается самой функцией;
- требуется `source.edit` на компоненте; `upload.perform` не заменяет его;
- пустое входное значение — `blank`, отсутствующий ключ — `missing_key`;
- равное значение — `unchanged`, повторный прогон не создаёт `Change`;
- непустое существующее значение без `overwrite=True` —
  `would_overwrite`, остальные строки продолжают применяться;
- источник, targets, state, flags и labels существующего юнита не меняются;
- запись только через `Unit.update_explanation`, чтобы сохранить историю и
  естественную инвалидацию judge-контекста.

Если `note` уже равен новому `explanation`, объяснение всё равно ставится,
чтобы стать редактируемым; исход классифицируется как `already_in_note`.
LLM MT и judge при равных нормализованных значениях передают только
`explanation`, чтобы не дублировать контекст.

### Права

- существующий компонент: загрузить/добавить строки можно под
  `upload.perform` и `unit.add`, применить Explanation — только под
  `source.edit`;
- мастер создания: если в ките есть Explanation, до сохранения черновика
  проверяется `source.edit` на проекте; после создания исходного перевода
  право повторно проверяется на компоненте;
- background task не сериализует HTTP request: creation-path переиспользует
  существующий `acting_user_id`, dedicated existing-component task получает
  `user_id`, оба заново загружают `User` и передают объект в сервис;
- потеря права между этими проверками не приводит к частичному применению:
  draft переходит в retryable `FAILED`, Explanation не меняются.

## Задачи

### 1. Схема v3 и разбор служебных колонок

**Результат:** `flags` и Explanation однозначно живут в `ParseResult`, но не
в PO.

**Файлы:** `loc_kit_ingest/profile.py`, `infer.py`, `model.py`, `parser.py`,
`writer.py`, тесты и schema-раздел
`docs/product/guides/loc-kit-ingest.md`.

**Действия:**

- добавить `SCHEMA_VERSION_PO_METADATA = 3` и v3 PO-only closed schema;
- добавить два scalar metadata-поля component и проверки коллизий колонок;
- распознавать `_EXPLANATION_HEADERS` и уже согласованный `_FLAGS_HEADERS`
  до fallback в `comments`;
- заполнить `StringUnit.explanation`/`flags`;
- сохранить прежний PO-рендер: ни Explanation, ни flags в файл не идут;
- v1/v2 и кит без служебных колонок не менять.

**Проверка:** `cd loc_kit_ingest && uv run pytest`; отдельные сценарии:
Explanation + Comment сохраняются в разных полях; две explanation-колонки
отклоняются; неизвестное поле отклоняется; v1/v2 parse-back не меняется;
в отрендеренном PO нет текста Explanation и flags.

### 2. Общий preview/apply-контракт

**Результат:** один сервис классифицирует и применяет пояснения для обеих
точек входа.

**Файлы:** `weblate/trans/loc_kit.py`,
`weblate/trans/tasks.py`, `weblate/trans/models/loc_kit.py`, миграция,
`weblate/trans/tests/test_loc_kit_ingest_contract.py`.

**Действия:**

- реализовать `KitExplanationApplyResult`, preview-классификацию и
  `apply_kit_explanations`;
- закрепить eligibility, `source.edit`, overwrite и DB-only-инварианты;
- блокировать исходные юниты в стабильном порядке;
- `LocKitImportDraft` получает состояния `APPLYING`/`FAILED`, nullable
  UUIDField `apply_task_id` и атомарный протокол резерва;
- confirm существующего компонента внутри `transaction.atomic` берёт draft
  через `select_for_update`, допускает только `PREVIEW_READY`/retryable
  `FAILED`, генерирует `task_id = uuid.uuid4()`, одним update сохраняет
  `state=APPLYING` + `apply_task_id=task_id`, затем регистрирует
  `transaction.on_commit` с
  `task.apply_async(..., task_id=str(task_id))`;
- callback публикации ловит ошибку брокера, отдельной транзакцией делает
  compare-and-set только для того же
  `(pk, state=APPLYING, apply_task_id=task_id)` в `FAILED`, оставляет файл
  и повторно поднимает ошибку в UI; draft не зависает в `APPLYING`;
- bound task работает с `acks_late=True` и
  `reject_on_worker_lost=True`, заново читает `User`, component, draft,
  profile и preview; HTTP request через очередь не передаётся;
- task внутри одной транзакции берёт draft и component lock, требует
  `state=APPLYING`, `apply_task_id == self.request.id` и повторно проверяет
  permission/eligibility; duplicate delivery после `CONSUMED` — no-op,
  несовпадающий task id никогда не применяет данные;
- успешная транзакция переводит draft в `CONSUMED`, а удаление файла
  регистрируется через `transaction.on_commit`; retryable exception
  оставляет тот же `APPLYING`/task id для Celery retry, окончательная ошибка
  отдельной транзакцией compare-and-set переводит только свой reservation
  в retryable `FAILED`;
- preview показывает число юнитов с актуальным judge-вердиктом, которые
  станут stale; ручной записи `JudgeVerdict`/`Unit.state` нет.

**Проверка:** совпавшие ключи получают Explanation; повторный прогон
идемпотентен; blank/missing/would-overwrite частично успешны; targets,
state, flags, labels и файлы неизменны; нет `source.edit` — ни одной
мутации; `supports_explanation=True`, glossary и locked отклоняются.

### 3. Создание компонента: durable handoff A'

**Результат:** одна загрузка через мастер создаёт компонент с Explanation;
повторная загрузка не нужна.

**Файлы:** `weblate/utils/views.py`,
`weblate/trans/models/loc_kit.py`, `weblate/trans/models/component.py`,
`weblate/trans/tasks.py`, `weblate/trans/views/create.py`, поля формы
создания, тесты создания.

**Действия:**

- строковый table-upload сохранять в `LocKitImportDraft`; preview и
  локальная валидация завершаются до создания компонента;
- до связывания draft проверить
  `request.user.has_perm("source.edit", project)`;
  `Unit.update_explanation` сам permission не проверяет;
- между шагами визарда нести UUID `draft.token`, а не доверенный клиенту
  database id; перед `Component.save` разрешить token через
  owner/session-bound `LocKitImportDraft.get_active` и только затем положить
  внутренний `loc_kit_draft_id` на instance;
- читать `loc_kit_draft_id` в `Component.save` и явно передать его через
  eager-вызов `after_save`, `queue_background_task`,
  `component_after_save` и `Component.after_save`; transient attribute без
  этих kwargs через Celery не проходит;
- после успешного inline `create_translations` `after_save` вызывает общий
  scheduler из задачи 2: тот повторно проверяет `source.edit` на созданной
  source translation, резервирует UUID task id и публикует request-free
  apply-task строго по протоколу `APPLYING`/`on_commit`/publish-failure;
- если `create_translations` делегирует загрузку в `perform_load`, draft id
  и `acting_user_id` явно передаются в kwargs `perform_load`; draft остаётся
  `PREVIEW_READY`. Только после успешного `create_translations_immediate`
  `perform_load` вызывает тот же scheduler. Так владелец продолжения —
  ровно одна завершившая load-задача, а draft id не теряется;
- apply-task ещё раз загружает actor и проверяет `source.edit` на source
  translation непосредственно перед `Unit.update_explanation`; потеря права
  переводит его reservation в `FAILED` без мутаций;
- только успешная apply-task помечает draft `CONSUMED` и удаляет файл;
  duplicate delivery видит `CONSUMED` и ничего не применяет;
- только в этом же cutover перестать направлять explanation-колонку в
  `comments`/`#.`.

**Проверка:** настоящий Celery-путь и eager-путь дают одинаковый результат;
fast worker не стартует до сохранения `apply_task_id`; broker publish failure
оставляет `FAILED`, не `APPLYING`; duplicate delivery и двойной confirm дают
одну мутацию; worker получает id после повторной загрузки `Component` из БД;
deferred `perform_load` сохраняет draft id и ставит apply ровно один раз;
expired/wrong-owner/wrong-project draft не применяется; потеря `source.edit`
не меняет Explanation; ошибка не удаляет файл; кит без Explanation проходит
прежним путём.

### 4. Существующий компонент: расширить add-strings

**Результат:** B реализуется внутри `loc-kit-strings-update`, не отдельным
view.

**Источник задач:** разделы 1-4 и verification связанного
`docs/product/plans/2026-08-18-loc-kit-table-add-strings.md`, обновлённые
этим решением.

Preview объединяет новые строки и Explanation: для существующего ключа
source/targets/flags остаются append-only, но Explanation классифицируется
отдельно. Confirm применяет новые строки, затем Explanation общей функцией;
checkbox overwrite видим только при `would_overwrite > 0`. Без
`source.edit` строки по-прежнему можно добавить, но Explanation получает
`unavailable` и не меняется; это явно показано до confirm и в итоге.

### 5. Убрать дубль контекста в LLM

**Результат:** старый file-owned `note`, совпадающий с новым DB-owned
Explanation, не дублируется в промпте.

**Файлы:** `weblate/machinery/llm.py`, `weblate/trans/judge_loop.py` и
существующие prompt/context-hash тесты.

Нормализовать оба значения текущей общей нормализацией; при равенстве
передавать только `explanation`. Разные значения сохраняются оба. Изменение
Explanation по-прежнему меняет judge context hash и делает старый вердикт
stale.

### 6. Документация и changelog

В том же изменении, что код:

- `docs/product/guides/loc-kit-ingest.md`: schema v3, узкие заголовки,
  создание за одну загрузку, общий existing-component preview;
- `docs/product/guides/game-repo-integration-contract.md`: UI назвать
  основным массовым способом после его появления; API PATCH оставить
  программным/аварийным способом; `explanations.json` оставить долговечным
  источником восстановления после пересоздания компонента или смены ключа;
  добавить отдельную строку про loc-kit Explanation, не менять строку
  developer comment -> `note`;
- `docs/changes.rst`: одна запись в текущей unreleased-секции.

## Общая проверка

1. `cd loc_kit_ingest && uv run pytest`.
2. Скопировать пакет в dev-контейнер:
   `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`.
3. `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`
   плюс затронутые тесты component creation / machinery / judge.
4. Живой smoke-test на dev-инстансе:
   - создать компонент из небольшого кита с Explanation + Comment;
   - проверить Explanation, отдельный source description и отсутствие
     обоих служебных полей в PO;
   - тем же китом открыть `loc-kit-strings-update` для существующего JSON
     компонента, проверить preview и overwrite;
   - подтвердить, что targets/state/flags/labels не изменились.
5. `uv run prek run --all-files`.

Деплой не входит. `l10n.herocraft.com` и платные LLM-вызовы требуют
отдельного явного разрешения.
