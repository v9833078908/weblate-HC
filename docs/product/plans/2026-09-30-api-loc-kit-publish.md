<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# План: публикация проекта из loc-kit через REST API

Дата: 2026-09-30
Статус: предложен, ждёт одобрения. Одобрение плана не разрешает деплой.

## Проблема

Создатель проекта без checkout репозитория и без локального `loc_kit_ingest`,
с одним личным API-токеном и site-ролью "Add new projects", не может через REST
получить тот же результат, что даёт UI из того же файла кита. Проверено на dev
2026-09-30 (сценарий и `publish.py` лежат в scratchpad сессии, в репозиторий не
входят):

1. `POST /api/projects/<p>/components/` с `zipfile` идёт через
   `create_component_from_zip` (`weblate/api/serializers.py:2472-2478`) и
   CSV/TSV/XLSX не принимает. UI-вкладка "Upload translation files" принимает
   их через `create_component_from_kit` (`weblate/utils/views.py:710`,
   `weblate/trans/views/create.py:649`).
2. Колонка Explanation через API не применяется. UI передаёт её в
   `Component.loc_kit_explanations` (`create.py:743-765`), и `after_save`
   применяет её после загрузки (`weblate/trans/models/component.py:4932-4935`,
   `5819`). Через API остаётся PATCH по одной единице, около 0,35 с на unit.
3. Глоссарий из таблицы через API невозможен. UI-путь (`create.py:767`,
   `1254-1363`, `1698-1750`) выводит профиль детерминированно, проверяет его
   через `validate_glossary_profile`, рендерит TBX и ставит в очередь
   `flag_glossary_terminology` (`weblate/glossary/tasks.py:143`). Без этого
   флага языки, которых нет в таблице, получают 0 терминов (проверено: `pt_BR`,
   `zh_Hans`, `zh_Hant`).
4. `access_control` в `POST /api/projects/` молча отбрасывается: его нет в
   `ProjectSerializer.Meta.fields` (`serializers.py:1654-1722`). На production
   по умолчанию `100` (`deploy/environment.example:48`).
5. Мастер проекта ставит `check_flags = repeat-drift` (`create.py:207`), API
   не ставит.

## Решение

Один запрос API должен давать то же, что вся UI-последовательность для того же
файла. Для этого переиспользуются те же функции, без черновиков и без новых
подсистем.

- Кит передаётся в существующем поле `zipfile`, как и в UI (поле формы тоже
  называется `zipfile`, `forms.py:3225`). Путь выбирается по расширению имени
  файла: `.csv`/`.tsv`/`.xlsx` (`KIT_TABLE_SUFFIXES`, `utils/views.py:707`)
  ведут на путь кита, любое другое имя - на прежний путь ZIP. Так сохраняется
  совместимость с клиентами, которые шлют ZIP без расширения `.zip`. `docfile`
  не трогаем: он означает один документ, который становится шаблоном.
- `is_glossary` вместе с таблицей даёт путь глоссария, без него - путь строк.
  Это то же ветвление, что в `CreateFromZip.form_valid` (`create.py:644-649`).
- Поля, которые решает кит, API выводит сам: `file_format`, `filemask`,
  `template`, `new_base`. Если клиент их прислал, запрос отклоняется с 400 до
  любой записи на диск. Так не нужно сверять значения после конвертации, и не
  остаётся осиротевших репозиториев. Для глоссария это соответствует
  `LOC_KIT_LOCKED_FIELDS` (`create.py:1581`).
- Черновик `LocKitImportDraft` API не нужен: UI хранит черновик между шагами
  мастера, а в API шаг один, и весь конвейер проходит внутри одного запроса.

## Разрешения

| Операция | UI сейчас | API после изменения |
| --- | --- | --- |
| Создать проект | `project.add` или `workspace.add_project` (`create.py:197-204`, `227`) | без изменений, `ProjectViewSet.create` (`api/views.py:2240-2289`) уже совпадает |
| Задать `access_control` | вкладка Access: `billing:project.permissions` на проекте (`forms.py:4004`); создатель получает его через команду Administration в `post_create` (`models/project.py:1126-1133`). В `ProjectCreateForm` (`forms.py:4217`) поля нет | при создании: та же проверка `billing:project.permissions` после `post_create`, иначе 403 и откат. PATCH: 400, см. non-goals |
| Создать компонент (строки или глоссарий) | `get_creatable_projects` = `project.edit` (+ действующий billing, если он установлен) (`create.py:124-142`) | `project.edit` (`api/views.py:2004-2005`) без изменений. Расхождение по billing уже существует и в этом плане не трогается |
| Применить Explanation | `source.edit` на проекте на шаге init, иначе предупреждение и пропуск (`create.py:684-716`); повторная проверка в `apply_kit_explanations` | то же: без `source.edit` компонент создаётся, в отчёте `loc_kit.warnings` появляется запись, объяснения пропускаются |
| Флаг `terminology` у глоссария | бот `glossary:sync`, отдельного права нет | то же |

## Область

- `weblate/utils/views.py`: `create_component_from_kit` (710) и новая
  `create_glossary_from_table` рядом с ней.
- `weblate/api/serializers.py`: `ProjectSerializer` (1597, `create` 1759,
  `validate` 1769), `ComponentSerializer` (поле `sheet`, `to_internal_value`
  2139, `validate` 2390, `create` 2498).
- `weblate/api/views.py`: `ProjectViewSet.components` (1982-2025),
  `ProjectViewSet.perform_create` (2293).
- Тесты: `weblate/api/tests.py`,
  `weblate/trans/tests/test_loc_kit_ingest_contract.py`.
- Документация: `docs/specs/openapi.yaml` (генерируется), `docs/api.rst`,
  `docs/admin/projects.rst` (`uploading-glossary-tables`),
  `docs/product/guides/loc-kit-ingest.md`, `docs/changes.rst`,
  `docs/security/threat-model.rst`.
- Миграций нет. `loc_kit_ingest/` не меняется.

## Non-goals

- Фолбэк на OpenRouter для глоссария через API. Если детерминированный вывод
  профиля не сработал, API возвращает 400 с причиной и советует UI, где есть
  анализ и ручная загрузка профиля. `request_profile_proposal` из API не
  вызывается ни при каком значении `LOC_KIT_PROFILE_ANALYSIS_ENABLED`, поэтому
  исходящего запроса у нового пути нет.
- Ручной профиль (`upload-profile`) и переключение раскладки (`relayout`) через
  API.
- Смена `access_control` через PATCH. Для неё нужна проверка лицензий
  компонентов из `ProjectSettingsForm.get_unlicensed_components`
  (`forms.py:3876`); это отдельный шаг, см. открытые вопросы.
- Дополнение существующего глоссария и полное обновление строкового компонента
  через API (`append_glossary_terms`, `loc-kit-strings-update`).
- Выбор листа для строк. UI отклоняет многолистовой XLSX для строк
  (`utils/views.py:760-768`), API делает так же.
- Machinery settings, команды, порядок компонентов. Уже работает через API.
- Idempotency-Key и транзакционное создание проекта вместе с компонентами.
- Деплой.

## Задача 1. Создание проекта: repeat-drift и access_control

**Файлы:** `weblate/api/serializers.py`, `weblate/api/views.py`.

1. `ProjectSerializer.create` (1759): если в `initial_data` нет `check_flags`,
   записать `validated_data["check_flags"] = REPEAT_DRIFT_CHECK_ID` (импорт из
   `weblate.checks.consistency`, как в `create.py:33`). Явно переданный
   `check_flags`, включая пустую строку, остаётся без изменений.
2. Добавить `"access_control"` в `Meta.fields`: поле читается в GET и
   принимается при создании.
3. `ProjectSerializer.validate` (1769): если `self.instance` есть и
   `attrs["access_control"]` отличается от текущего значения, вернуть
   `ValidationError({"access_control": "Changing access control through the API
   is not supported; use the project access settings."})`. Сейчас поле молча
   теряется, после изменения будет явный 400.
4. `ProjectSerializer.create`: убрать `access_control` из `validated_data` и
   сохранить его на сериализаторе (`self.requested_access_control`), чтобы
   `post_create` с billing не затёр его без проверки (`project.py:1126-1131`).
5. `ProjectViewSet.perform_create` (2293), внутри уже открытого
   `transaction.atomic`, после `post_create`: если запрошенное значение
   отличается от `instance.access_control`, проверить
   `user.has_perm("billing:project.permissions", instance)`; при отказе
   `PermissionDenied`, транзакция откатывается и проект не создаётся. Иначе
   установить `acting_user` и `save(update_fields=["access_control"])`, чтобы
   запись `ACCESS_EDIT` в Change появилась так же, как из UI
   (`project.py:851-859`). Проверка лицензий не нужна: у нового проекта нет
   компонентов.

**Тесты** (`weblate/api/tests.py`, `ProjectAPITest`, рядом с `test_create` 3999
и `test_create_with_project_add_permission` 4087):

- без `check_flags` проект получает `repeat-drift`, `effective_check_flags`
  содержит его;
- явные `check_flags` (`""` и другой флаг) сохраняются как есть;
- пользователь без superuser с `project.add` и `access_control=100`: 201, в БД
  `100`, есть Change `ACCESS_EDIT`;
- `billing_allows_access_control` замокан на `False`: 403,
  `Project.objects.count()` не изменился;
- PATCH `access_control`: 400, значение не изменилось;
- GET отдаёт `access_control`.

**Проверка:** `./rundev.sh test weblate/api/tests.py -k "ProjectAPITest and create"`

## Задача 2. Строковый компонент из CSV/TSV/XLSX

**Файлы:** `weblate/utils/views.py`, `weblate/api/serializers.py`,
`weblate/api/views.py`.

1. `create_component_from_kit(data, uploaded, *, source_lang=None)`: передать
   `source_lang` в `infer_profile(..., source_lang=source_lang)`
   (`loc_kit_ingest/infer.py:503` уже принимает его, как CLI `--source-lang`).
   UI вызывает функцию без аргумента, и его поведение не меняется. Это не то же
   самое, что передавать `data["source_language"]`: в форме UI это поле всегда
   заполнено значением по умолчанию.
2. `ComponentSerializer.to_internal_value` (2139): до подстановки
   `manage_units` определить, что загружен кит: `zipfile` с расширением из
   `KIT_TABLE_SUFFIXES`. Для кита:
   - если в `data` есть любое из `file_format`, `filemask`, `template`,
     `new_base`, вернуть `ValidationError({field: "Derived from the loc-kit
     table; omit this field."})`;
   - подставить `file_format`/`filemask`: для строк `po-mono`/`*.po`, для
     глоссария `tbx`/`tbx/*.tbx` и `new_lang` по умолчанию `none` (начальное
     значение UI, `create.py:1671`). Без этого DRF отклонит обязательные поля
     раньше `validate`.
3. `ComponentSerializer.validate` (2461-2478): в ветке `zipfile is not None`
   для кита без `is_glossary` вызвать приватный метод `_create_from_kit`:
   - `hint = attrs["source_language"].code`, если `source_language` есть в
     `initial_data`, иначе `None`;
   - `create_component_from_kit(attrs, zipfile, source_lang=hint)`;
     `DjangoValidationError` превращается в
     `serializers.ValidationError({"zipfile": error.messages})`,
     `(BadZipfile, OSError, RepositoryError)` дают ту же ошибку, что и путь ZIP;
   - записать в `attrs` и `instance` значения `template` и `new_base` из
     `kit_info["template"]`, `manage_units = True` (правило UI:
     `bool(template)`, `create.py:450`) и
     `source_language = Language.objects.get(code=kit_info["source_lang"])`
     (`DoesNotExist` даёт 400 `{"source_language": ...}`);
   - если есть `kit_info["explanations"]` и
     `user.has_perm("source.edit", attrs["project"])`, сохранить их на
     сериализаторе; иначе добавить в отчёт то же предупреждение, что даёт UI
     (`create.py:709-716`);
   - `self.loc_kit_report = {"kind": "strings", "source_language",
     "languages", "units", "skipped", "sourceless", "explanations": <число>,
     "notes", "warnings"}`.
   Всё это выполняется до `instance.clean()` и после
   `clean_unique_together()`, как у `docfile`.
4. `ComponentSerializer.create` (2498): для кита создавать через
   `Component(**validated_data)`, как уже сделано для `from_component`.
   Установить `acting_user = request.user` и `loc_kit_explanations`, затем
   `save(force_insert=True)`. Без `acting_user` условие
   `loc_kit_explanations and was_change and user` (`component.py:4932`) не
   выполняется, и объяснения молча теряются.
5. `ProjectViewSet.components` (2012-2025): если у сериализатора есть
   `loc_kit_report`, ответить `{**serializer.data, "loc_kit": report}`. Для
   остальных запросов ответ прежний.

**Тесты** (`weblate/api/tests.py`, рядом с `test_create_component_zipfile`
5456; фикстуры `KIT_CSV`/`GLOSSARY_CSV` импортируются из
`test_loc_kit_ingest_contract.py`):

- CSV из 5 языков с колонками Explanation и Comment: 201,
  `file_format=po-mono`, `template=ru.po`, `source_language=ru`, 5 переводов, у
  исходных единиц заполнены `explanation` и `note`, в `loc_kit.units`
  правильное число;
- то же для TSV и однолистового XLSX;
- `source_language=en` как подсказка: шаблон `en.po`;
- присланный `filemask`: 400 `attr=filemask`, каталога репозитория нет;
- кит с ошибками строк: 400 `attr=zipfile` с сообщениями "Row N: ...",
  компонента и каталога нет;
- многолистовой XLSX: 400, тот же текст, что в UI;
- пользователь с `project.edit` без `source.edit`: 201, `explanation` пустые, в
  `loc_kit.warnings` есть предупреждение;
- пользователь без `project.edit`: 403;
- ZIP без расширения `.zip` по-прежнему идёт путём ZIP (регрессия
  совместимости).

`weblate/trans/tests/test_loc_kit_ingest_contract.py`,
`LocKitUniversalUploadContractTest` (427): аргумент `source_lang` меняет
`info["source_lang"]` и `template`; вызов без аргумента даёт прежний результат.

**Проверка:** `./rundev.sh test weblate/api/tests.py -k kit` и
`./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k Universal`

## Задача 3. Глоссарий из CSV/TSV/XLSX с `is_glossary`

**Файлы:** `weblate/utils/views.py`, `weblate/api/serializers.py`,
`weblate/api/views.py`.

1. Новая `create_glossary_from_table(data, uploaded, sheet=None)` в
   `weblate/utils/views.py`, тот же конвейер, что UI, в одном вызове:
   - `read_sheets` на временном файле (как `_start_glossary_draft`,
     `create.py:780-793`); `ReaderError` и пустая книга дают `ValidationError`;
   - лист: `sheet` или единственный; несколько листов без `sheet` дают
     `ValidationError` со списком листов (`code="sheet"`); неизвестный лист так
     же;
   - `infer_glossary_profile(name, rows, component=data["slug"])` (как
     `_infer_draft_profile`, `create.py:1254`); `InferenceError` даёт
     `ValidationError` с причиной и "use the web upload for analysis or a
     manual profile";
   - `validate_glossary_profile(...)` (`loc_kit.py:706`);
     `GlossaryProfileError` даёт `ValidationError([message, *details])`;
   - `LocalRepository.from_files(fake.full_path, {f"tbx/{n}": d ...})`,
     `fake = Component(project, category, slug, name)` как в
     `create_component_from_zip`;
   - возвращает `(fake, preview, notes)`.
2. `ComponentSerializer`: write-only
   `sheet = serializers.CharField(required=False)`, `validate` забирает его
   вместе с `docfile`/`zipfile`; `sheet` без кита-глоссария даёт 400. Для кита с
   `is_glossary` вызывается `_create_glossary_from_table`:
   - ошибки дают 400 `{"zipfile"|"sheet": messages}`;
   - `source_language` из `preview.source_language`; другое присланное значение
     даёт 400 (UI блокирует поле, `create.py:1648-1656`);
   - `template = new_base = ""`, `is_glossary = True`, `manage_units = True`
     (`create.py:450`);
   - `loc_kit_exact = any("exact" in t.source_flags for t in preview.all_terms)`
     (`create.py:1737-1739`), ставится на экземпляр в `create` вместе с
     `acting_user`;
   - `loc_kit_report = {"kind": "glossary", "sheet", "source_language",
     "target_languages", "terms", "notes", "warnings":
     cap_preview_warnings([*notes, *preview.warnings])}`.
3. `ProjectViewSet.components`: после `post_create` для `kind == "glossary"`
   `transaction.on_commit(lambda: flag_glossary_terminology.delay(component.pk))`,
   как `create.py:1748`. Задача сама ждёт исходных единиц (retry 10 с x 30).

**Тесты** (`weblate/api/tests.py`):

- `GLOSSARY_CSV` с `is_glossary=1`: 201, `tbx`, `tbx/*.tbx`, `new_lang=none`, у
  всех исходных единиц `terminology`;
- язык строкового компонента, которого нет в глоссарии, после создания
  содержит все термины;
- многолистовой XLSX без `sheet`: 400 `attr=sheet`; с `sheet`: 201;
- нераспознаваемая таблица при `LOC_KIT_PROFILE_ANALYSIS_ENABLED=True`: 400,
  мок `weblate.trans.loc_kit.request_profile_proposal` не вызван;
- чужой `source_language`: 400, каталога нет;
- `sheet` при обычном ZIP: 400;
- сначала глоссарий, потом строки: ровно один `is_glossary`.

`test_loc_kit_ingest_contract.py`: unit-тест `create_glossary_from_table` (один
лист, выбор листа, ошибка вывода).

**Проверка:** `./rundev.sh test weblate/api/tests.py -k glossary`

## Задача 4. OpenAPI, документация, changelog, threat model

1. `@extend_schema` на POST `components` (`api/views.py:1987-1990`): ответ
   `ComponentSerializer` плюс необязательный `loc_kit` через
   `inline_serializer`. `sheet` и `access_control` попадут из сериализаторов.
   `make -C docs update-openapi`, `npx @redocly/cli lint docs/specs/openapi.yaml`.
   YAML руками не правится.
2. `docs/api.rst`: `POST /api/projects/` (962): `access_control` (правило, откат
   при 403), по умолчанию `check_flags=repeat-drift`; `access_control` в GET.
   `POST .../components/` (1183): `zipfile` принимает CSV/TSV/XLSX, выводимые
   поля и запрет их присылать, `sheet`, `is_glossary`, `loc_kit`, порядок
   "сначала глоссарий", ожидание готовности, два примера curl.
3. `docs/admin/projects.rst` (`uploading-glossary-tables`, 133): абзац со
   ссылкой на API; анализ и ручной профиль только в UI.
4. `docs/product/guides/loc-kit-ingest.md`: подраздел "Через REST API" в
   "UI-сценарии" (906).
5. `docs/changes.rst`: одна запись в "New features" раздела `2026.8.1`.
6. `docs/security/threat-model.rst`: строку "Loc-kit glossary table intake"
   (105-113) расширить до "Loc-kit table intake" с точкой входа REST (без
   черновика и исходящего запроса); абзац 377-388: шлюз API `project.edit`, как
   `get_creatable_projects` кроме billing; строка 695-705: загрузка через API;
   `access_control` при создании через API под `billing:project.permissions`,
   PATCH отклоняется; в "Conditions that change this model" (1138-1142): REST -
   вторая точка входа для уже рассмотренного формата.

## Асинхронность, ожидание готовности, идемпотентность, ошибки

- **Асинхронность.** 201 сразу после создания строки и репозитория. Загрузка и
  Explanation идут в той же `component_after_save`/`perform_load`. Строки
  готовы, когда `task_url` завершён и `GET /api/translations/<p>/<c>/<src>/`
  даёт `total == loc_kit.units`. Глоссарий: загрузка, затем
  `flag_glossary_terminology`, затем `sync_terminology`; готово, когда в каждом
  языке `total == loc_kit.terms`.
- **Идемпотентность.** POST не идемпотентен. Повтор с тем же slug даёт 400 от
  `clean_unique_together` до записи на диск. После таймаута клиент делает GET по
  slug. Idempotency-Key вне объёма.
- **Ошибки.** drf-standardized-errors: `{"type": "validation_error", "errors":
  [{"attr": "zipfile"|"sheet"|"source_language"|"filemask"|..., "code",
  "detail"}]}`. Ошибки кита по одной на запись. Права: 403
  `permission_denied`.

## Общая проверка

- `./rundev.sh test weblate/api/tests.py -k "kit or glossary or access_control or repeat_drift"`
- `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`
- Повторить на хосте:
  `CI_DB_PORT=5434 uv run pytest weblate/api/tests.py -k "kit or glossary"`
  (dev-контейнер может запускать чужой worktree).
- `uv run prek run --files <изменённые>`, mypy без новых находок.
- `make -C docs update-openapi`, `npx @redocly/cli lint docs/specs/openapi.yaml`.
- Сквозная проверка на dev (`http://localhost:3001/api/`, токен из
  `weblate-mcp/.env`): демо-кит с `zh_Hans`/`zh_Hant`/`pt_BR` публикуется тремя
  curl (проект, глоссарий CSV, строки CSV) без PATCH по единицам. Ожидается:
  12/12 строк, 11 объяснений, по 8 терминов в каждом языке глоссария,
  `check_flags=repeat-drift`, `access_control` как запрошено.
- Celery: код воркера (`component.py`, `tasks.py`, `glossary/tasks.py`) не
  меняется, новый код в веб-процессе (Granian перезагружает). Если понадобится
  трогать worker-модули:
  `docker exec dev-docker-weblate-1 supervisorctl restart celery-celery` перед
  сквозной проверкой.

Деплой на `l10n.herocraft.com` только с отдельного одобрения владельца.
`loc_kit_ingest` на production уже нужен UI.

## Критерии приёмки

1. Один multipart POST с CSV/TSV/XLSX создаёт строковый компонент с тем же
   форматом, маской, шаблоном, исходным языком, заметками и Explanation, что UI
   для того же файла.
2. Один POST с таблицей и `is_glossary` создаёт TBX-глоссарий, термины которого
   после синхронизации есть во всех языках проекта.
3. Ошибка кита или профиля даёт 400, на диске и в БД ничего не остаётся.
   OpenRouter из API не вызывается.
4. `access_control` при создании применяется или даёт 403 с откатом; PATCH даёт
   явный 400.
5. Проект из API без `check_flags` получает `repeat-drift`.
6. Прежние клиенты ZIP/docfile работают, существующие тесты проходят, миграций
   нет.

## Открытые вопросы и риски

1. repeat-drift при явных `check_flags`: план ставит его только при отсутствии
   поля. Альтернатива: дописывать к любым флагам.
2. PATCH `access_control` отложен; для него нужно перенести
   `get_unlicensed_components` из формы в `Project`.
3. Подсказка `source_language` для строк - сознательное расхождение с UI (UI
   выводит язык из заголовков и позволяет сменить его без смены шаблона).
   Строгое равенство с UI: без подсказки, при расхождении 400.
4. Многолистовой XLSX для строк: 400 как в UI, но текст советует CLI, которого
   у пользователя нет. Можно применить `sheet` и к строкам (одна строка кода,
   отход от UI).
5. Ошибки кита идут через `gettext` в общих функциях, хотя сообщения API по
   правилам не локализуются; без Accept-Language они английские.
6. Billing: шлюз компонентов в API его не проверяет, UI проверяет. Расхождение
   существующее, billing на HCGameLoc не установлен; вне объёма, отмечено в
   threat model.
7. `acting_user` на компонентах из API делает загрузку `user_waiting=True`
   (интерактивный приоритет), как в UI; прочие API-загрузки не меняются.
8. Если `instance.clean()` падает после записи файлов, каталог остаётся (так уже
   у ZIP). Запрет выводимых полей до записи сужает окно, но не закрывает.
9. XLSX разбирается синхронно в запросе в пределах
   `COMPONENT_ZIP_UPLOAD_MAX_SIZE`; отдельного rate limit у API-пути нет, только
   общий throttling. Проверить для production.
