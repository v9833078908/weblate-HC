# План: прогресс-бар фоновой задачи, который переживает перезагрузку страницы

## Контекст

Пользователь запустил автоперевод на `victory-banner/glossary` (component 29,
режим `translate`, `state:empty`, движок `openrouter`), перезагрузил страницу и
потерял всякую индикацию: задача шла ещё несколько минут, но статуса на сайте
не было. Задача завершилась успешно (все 7 языков 202/202), то есть проблема
чисто в UI.

Как это работает сейчас:

1. `weblate/trans/views/edit.py:auto_translation` после `auto_translate.delay()`
   кладёт flash-сообщение с тегом `task:<id>`.
2. `weblate/trans/templatetags/translations.py:show_message` вырезает этот тег
   и передаёт `task_id` в `weblate/templates/message.html`, который рисует
   `data-task="{% url 'api:task-detail' %}"` и пустой `.progress-bar`.
3. `weblate/static/loader-bootstrap.js` (блок `/* Generic messages progress */`)
   поллит этот URL и двигает бар.

Flash-сообщение одноразовое: после перезагрузки страницы `task_id` больше
негде взять, бар исчезает. Идентификатор задачи нигде за пользователем не
закреплён.

## Три дефекта, которые нужно закрыть вместе

### A. Нет закрепления задачи за пользователем

Единственное новое состояние, которое реально нужно, — список активных задач
пользователя. `task-meta-<id>` (`weblate/utils/celery.py`) уже хранит
component/translation для авторизации, но найти по пользователю его задачи
нельзя.

### B. Для части scope-ов бар умирает сразу (существующий баг)

`weblate/trans/views/edit.py:1476-1512`: для `Category` заполняется только
`category_id`, для `ProjectLanguage` — `project_id` + `language_id`, для
`Workspace` — `workspace_id`. `component_id` и `translation_id` при этом оба
`None`, а `store_published_task_metadata`
(`weblate/utils/celery.py:91-103`) в этом случае выходит рано и метаданных не
пишет. Дальше `TasksViewSet.get_task` (`weblate/api/views.py:4877-4910`)
бросает `Http404 "Invalid task"`, а JS трактует 404 как «задача завершена» и гасит
бар. То есть автоперевод на категории, project-language и workspace **сегодня**
показывает бар примерно на одну секунду.

Починка идёт тем же паттерном, что уже применён в `weblate/trans/views/reports.py:245`,
`weblate/trans/views/create.py:324` и `weblate/api/views.py:1710`: явный
`store_task_metadata(...)` сразу после `.delay()`, с `user_id`. Подпись
`store_task_metadata` уже принимает `user_id`, менять её не нужно,
`weblate/utils/celery.py` не трогаем. Приоритет в `get_task`
(translation -> component -> user) сохраняется, поэтому для component-scope
авторизация остаётся по доступу к компоненту, а для остальных scope-ов
владелец задачи получает доступ к своей задаче.

### C. Процент откатывается назад (существующий баг)

`BaseAutoTranslate.set_progress` (`weblate/trans/autotranslate.py:331-337`)
пишет `100 * current // self.progress_steps` в PROGRESS-мету **той же**
Celery-задачи. В `BatchAutoTranslate.perform` на каждый язык создаётся
отдельный `AutoTranslate` со своим `progress_steps`, то есть его обновления
идут в масштабе одного языка, а батч после каждого языка пишет
`pos / len(translations)` (`weblate/trans/autotranslate.py:1052`). На семи
языках бар идёт 0->100 внутри первого языка, падает на 14 %, снова 0->100 и так
далее. В одноразовом flash-баре это мелькало; закреплённый бар будут смотреть
минутами, и откат назад читается как «сломалось».

## Изменения

### 1. `weblate/utils/celery.py`: список задач пользователя

Рядом с существующими хелперами метаданных:

- `USER_TASKS_TTL = TASK_METADATA_TTL` (6 часов) и
  `PENDING_TASK_MAX_AGE = 1800` (30 минут).
- `add_user_task(user_id, task_id, *, text, url)` — дописывает в кэш-список
  `user-tasks-<uid>` запись `{"id", "started", "text", "url"}`. Текст и URL
  денормализованы намеренно: контекст-процессор не должен делать запросов в БД
  на каждый рендер страницы.
- `get_user_tasks(user_id)` — возвращает живые записи и вычищает мёртвые:
  готовые (`AsyncResult.ready()`) и «вечно PENDING» (Celery отвечает `PENDING`
  на любой неизвестный id, поэтому потерянная задача иначе провисит весь TTL
  на нуле). Если список изменился, он перезаписывается.
- `remove_user_task(user_id, task_id)` — для отмены/завершения.

### 2. `weblate/trans/views/edit.py:auto_translation`

После `auto_translate.delay(...)`:

- `store_task_metadata(task.id, component_id=..., translation_id=..., user_id=request.user.id)`
  — закрывает дефект B для всех шести scope-ов;
- `add_user_task(request.user.id, task.id, text=..., url=obj.get_absolute_url())`,
  где текст — переводимая строка с названием объекта, чтобы бар на чужой
  странице говорил, где именно идёт работа.

Flash-сообщение остаётся как есть.

### 3. `weblate/trans/context_processors.py:weblate_context`

Добавить `"user_tasks"`: для аутентифицированного пользователя —
`get_user_tasks(user.pk)`, иначе пустой список. Стоимость: один `cache.get` на
запрос (промах, когда задач нет) и по одному `AsyncResult` на живую запись.

### 4. `weblate/templates/base.html`

Сразу после блока `{% if messages %}` отрендерить закреплённые бары тем же
существующим тегом: `{% show_message task.tags task.text %}`, где `tags`
содержит `task:<id>`. Новой разметки не появляется, `message.html` и поллинг
переиспользуются целиком.

### 5. `weblate/static/loader-bootstrap.js`: дедуп

Сразу после старта автоперевода на странице окажутся два бара для одной
задачи: flash-сообщение и закреплённая запись. Перед сбором `progressBars`
пройти по `[data-task]` и удалить повторы по значению `data-task` (первым идёт
flash — он и остаётся).

### 6. `weblate/trans/autotranslate.py`: честный процент

- `BaseAutoTranslate.progress_range: tuple[int, int] = (0, 100)`;
  `set_progress` отображает свой локальный процент в этот диапазон.
- `BatchAutoTranslate.perform` выставляет дочернему `AutoTranslate`
  `progress_range = (100 * pos // total, 100 * (pos + 1) // total)`.

Итог: обновления внутри языка занимают его срез общей шкалы, батч закрывает
срез, бар монотонен.

### 7. Документация

- `docs/changes.rst`, верхняя (неизданная) секция — одна запись про то, что
  прогресс автоперевода виден после перезагрузки страницы.
- `docs/user/translating.rst`, раздел про автоматический перевод — одно
  предложение о том, что прогресс сохраняется.

## Верификация

- `weblate/trans/tests/test_autotranslate.py` — обновления прогресса батча
  монотонны и лежат в срезе своего языка (мок `current_task` по образцу
  `weblate/trans/tests/test_bulk_suggestions.py:601`).
- Тест вьюхи с `CELERY_TASK_ALWAYS_EAGER=False` и замоканным
  `auto_translate.delay`: метаданные содержат `user_id` для scope-ов без
  компонента; список задач пользователя пополняется; страница отдаёт ровно
  один `data-task` для этой задачи.
- Тест вычистки: готовая задача и «вечно PENDING» из списка исчезают.
- Живой прогон в dev-контейнере (`./rundev.sh`): запустить автоперевод,
  перезагрузить страницу кликом по реальному элементу, увидеть бар и его рост,
  дождаться завершения и увидеть финальное сообщение.

## Вне объёма

- Персистентность через рестарт Redis или воркера: состояние в кэше, потеря
  записи приводит к 404 на поллинге и тихому исчезновению бара.
- Кнопка отмены в закреплённом баре: `TasksViewSet.destroy` требует
  `component_id`, для category/project-language/workspace задача всё равно не
  отменяема.
- Прогресс чужих пользователей и любые новые REST-эндпоинты: намеренно ничего
  не добавляем, чтобы не тянуть регенерацию `docs/specs/openapi.yaml`
  (`.github/workflows/api.yml:82-88`).
