HCGameLoc
=========

Внутренний сервис непрерывной локализации проектов **Hero Craft**. Код
производный от `Weblate <https://weblate.org/>`_, но репозиторий развивается
самостоятельно: апстрим-remote нет, ребейзов на ``WeblateOrg/weblate`` нет,
править файлы под ``weblate/`` — нормальный способ менять поведение продукта.
Поддерживается v9833078908 (``origin`` = `v9833078908/weblate-HC
<https://github.com/v9833078908/weblate-HC>`_).

О проекте
---------

HCGameLoc — TMS для игровой локализации, в которой машина не только переводит,
но и **проверяет сама себя**. Студия работает без штата переводчиков, поэтому
продукт строится вокруг двух вещей: качественного LLM-перевода с игровым
контекстом и машинного контура валидации, чей результат виден в обычном UI
Weblate как проверки и отчёты.

Что здесь есть поверх исходного Weblate:

- **LLM-судья** — двухместный (seat 1 / seat 2) контур оценки переводов на
  отдельном LLM-эндпоинте, с вердиктами, консенсусным реджектом, циклом
  автопочинки, историей прогонов и отчётами (`LLM-судья`_).
- **Два маршрутизирующих движка перевода** — OpenRouter и корпоративный
  LiteLLM-прокси, с выбором модели по целевому языку
  (`Движки перевода: OpenRouter и LiteLLM`_).
- **Игровые проверки и автофиксы** — Unity-разметка, движковые токены и
  плейсхолдеры, разделитель строк ``$``, числа, кириллические утечки, видимая
  длина (`Игровые проверки и автофиксы`_).
- **Массовое исправление проваленных проверок** — серверный движок фиксапа,
  запускаемый из UI (`Массовое исправление проверок`_).
- **Учёт расходов на LLM** — журнал использования и стоимость по прогонам
  (`Учёт расходов на LLM`_).
- **Приём loc-kit** — детерминированный импорт таблиц строк и глоссариев
  студий (`Приём loc-kit`_).

Роадмап и архитектура: ``docs/product/vision/llm-first-product-architecture.md``.
Планы, замеры и ревью каждой из этих тем — в ``docs/product/``.


.. contents:: Содержание
   :local:
   :depth: 1

Свои части репозитория
----------------------

Свои части репозитория, которых нет в исходном Weblate:

``weblate_customization/``
    Пакет с игровыми проверками, автофиксами и движками машинного перевода:
    ``checks.py`` (``game-markup``, ``game-line-break``, ``game-token``,
    ``game-number``, ``cyrillic-leak``, ``game-length`` и переопределения
    штатных ``max-length`` / ``max-length-source``), ``autofixes.py``
    (``LineSeparatorSpacing``, ``RemoveAddedFinalStop``,
    ``AddFrenchPunctuationSpacing``) и ``machinery.py``
    (``RoutedLLMTranslation`` — OpenRouter, ``RoutedLiteLLMTranslation`` —
    LiteLLM). Подробности — в `Игровые проверки и автофиксы`_ и
    `Движки перевода: OpenRouter и LiteLLM`_.

``loc_kit_ingest/``
    Автономный (без Django) детерминированный импортёр loc-kit: чтение
    CSV/TSV/XLSX, вывод строгого профиля из шапки таблицы, генерация
    монолингвального PO и билингвального TBX с обратным разбором. См.
    `Приём loc-kit`_.

``weblate-mcp/``
    Вендоренный MCP-сервер `@mmntm/weblate-mcp
    <https://github.com/mmntm/weblate-mcp>`_ на NestJS, который ходит в
    локальный REST API Weblate, чтобы агентская сессия могла напрямую управлять
    проектами, компонентами и юнитами. См. `MCP-сервер`_ ниже.

``docs/product/``, ``docs/operations/``
    Документация форка (в основном на русском) под задачи игровой локализации:
    ``product`` — сам форк и LLM-first TMS (видение, роадмап, судья,
    MT-machinery, чеки, loc-kit, вечнозелёные руководства в
    ``docs/product/guides/``), ``operations`` — работа с живыми инстансами и
    конкретными играми. Прежние ``docs/llm-first/`` и ``docs/guides/`` влиты в
    ``docs/product/``, новые документы туда не создаются. Правило раскладки —
    раздел «Documentation layout» в ``AGENTS.md``.

``analysis/probes/``, ``analysis/data/``
    Одноразовые скрипты замеров и данные, которые они читают и пишут
    (корпуса, золотые наборы, выводы прогонов). Не документация и не часть
    продукта.

Собственный код есть и внутри ``weblate/``: судья
(``weblate/trans/judge.py``, ``judge_loop.py``, ``judge_workflow.py``,
``weblate/trans/models/judge.py``, ``weblate/checks/judge.py``,
``weblate/trans/views/judge.py``), учёт расходов
(``weblate/trans/models/llm_usage.py``), массовый фиксап
(``weblate/trans/fix_check.py``) и Weblate-сторона loc-kit
(``weblate/trans/loc_kit.py``, ``weblate/trans/models/loc_kit.py``).

Локальные правки ``dev-docker/docker-compose.yml``: PostgreSQL публикуется на
порту ``5434`` (``5433`` занят другим проектом), а ``WEBLATE_VCS_ALLOW_SCHEMES``
расширен схемой ``file``, чтобы в качестве источника перевода можно было
подключать локальные git-репозитории.

Как работает интеграция с Git
-----------------------------

Weblate — это не хранилище переводов, а слой поверх Git. Единственный источник
правды — это git-репозиторий с файлами локализации; Weblate только читает и
пишет в него.

Модель данных выстроена в цепочку:
``Project`` → ``Component`` → ``Translation`` → ``Unit``. **Компонент** — это
и есть точка привязки к репозиторию: у него есть URL исходного репозитория
(``repo``), ветка (``branch``), маска файлов (``filemask``, например
``Localization/*.json``) и, опционально, отдельный URL для push (``push``).

Жизненный цикл одного компонента:

1. **Clone.** При создании компонента Weblate клонирует ``repo`` в свой рабочий
   каталог (см. `Где хранятся файлы локализации`_). Файлы под ``filemask``
   разбираются на юниты — отдельные строки с ``source``, ``target``,
   состоянием и списком проваленных проверок.
2. **Перевод.** Переводчик правит строки в веб-интерфейсе. Weblate записывает
   изменения в соответствующий файл в локальном клоне и делает коммит от имени
   переводчика.
3. **Push.** Коммиты уходят обратно в git — в тот же репозиторий или в ``push``
   URL (например, в отдельную ветку под pull request), сразу или по расписанию.
4. **Pull / merge.** Когда разработчики меняют исходные строки в игре и пушат в
   репозиторий, Weblate подтягивает изменения (по вебхуку или периодически),
   мёржит/ребейзит их поверх своих коммитов и перечитывает файлы: новые строки
   появляются на перевод, удалённые — исчезают.

Что это даёт:

- **Один источник правды.** Переводы живут в git рядом с игрой, а не в БД
  Weblate. Файлы можно собрать в билд напрямую из репозитория.
- **Двусторонний обмен без ручной передачи файлов.** Разработчики пушат новые
  строки — они автоматически появляются на перевод; готовые переводы возвращаются
  в репозиторий коммитами.
- **История и ревью.** Каждая правка перевода — это git-коммит с автором. Через
  ``push`` в отдельную ветку переводы можно проводить через pull request и код-ревью.
- **Устойчивость к сбоям.** Даже если инстанс Weblate потерян, все переводы уже
  в git.
- **game-markup на входе.** Проверка ``game-markup`` не даёт «сломанным» по
  разметке строкам утечь в репозиторий незамеченными.

Подключение репозитория (кратко): создать проект → создать компонент → указать
``repo``, ``branch`` и ``filemask`` → выбрать формат файла и (для push обратно)
задать ``push`` URL и способ (direct push или pull request через API GitHub).
Для локальной разработки подойдёт репозиторий по схеме ``file://`` (поэтому в
compose добавлена схема ``file``).

Где хранятся файлы локализации
------------------------------

Всё состояние Weblate лежит в **каталоге данных** ``DATA_DIR``, который внутри
контейнера равен ``/app/data`` (``weblate/settings_docker.py``). Ключевое
устройство:

- ``DATA_DIR/vcs/<проект>/<компонент>/`` — рабочий git-клон каждого компонента.
  Именно здесь физически лежат все файлы локализации всех проектов
  (``weblate/trans/mixins.py`` собирает путь через ``data_dir("vcs")``).
- ``DATA_DIR/media/`` — загруженные файлы (скриншоты для контекста и т. п.).
- Метаданные (юниты, состояния, комментарии, пользователи, история изменений)
  хранятся не в файлах, а в **PostgreSQL**.

**В dev-инстансе** каталог ``/app/data`` смонтирован как bind-mount из
``dev-docker/data/`` на хосте (``$PWD/data:/app/data`` в
``dev-docker/docker-compose.yml``). То есть git-клоны переводов лежат в
``dev-docker/data/vcs/…``, а данные PostgreSQL — в именованном томе
``postgres-data``.

**На VPS (production).** Dev-compose для продакшена не годится (об этом
предупреждает шапка файла). В боевом деплое на базе `WeblateOrg/docker-compose
<https://github.com/WeblateOrg/docker-compose>`_ ``DATA_DIR`` монтируется в
именованный Docker-том (``weblate-data``), который по умолчанию лежит под
``/var/lib/docker/volumes/<стек>_weblate-data/_data/`` на диске VPS. Внутри —
та же структура: ``vcs/<проект>/<компонент>/`` с git-клонами всех переводов и
``media/`` с загрузками. Данные PostgreSQL — в отдельном томе
``postgres-data``. Практические следствия:

- Для бэкапа нужны **и** том с ``DATA_DIR`` (git-клоны + media), **и** дамп
  PostgreSQL — они должны быть согласованы между собой.
- Место на диске под ``DATA_DIR`` = суммарный размер всех клонированных
  репозиториев (с историей git), поэтому объём растёт вместе с числом и
  историей компонентов.
- Можно смонтировать ``DATA_DIR`` на отдельный диск/раздел, задав путь тома в
  production-compose; расположение управляется именно этим монтированием, а не
  настройками Weblate.

Требования
----------

- Docker + Docker Compose (для dev-инстанса).
- `uv <https://docs.astral.sh/uv/>`_ (для тестов, линтинга и проверки типов на
  хосте).
- Node.js + `pnpm <https://pnpm.io/>`_ (только если работаете над ``weblate-mcp/``).

Быстрый старт
-------------

Dev-инстанс целиком работает в Docker (``dev-docker/``) и запускается из корня
репозитория:

.. code-block:: sh

   WEBLATE_PORT=3001 ./rundev.sh # сборка + запуск (пересоздаёт контейнеры)
   ./rundev.sh logs -f weblate # смотреть логи
   ./rundev.sh stop

По умолчанию ``rundev.sh`` использует порт ``8080``, но этот деплой работает на
**3001** — именно его ожидают MCP-сервер и API-скрипты, поэтому всегда
экспортируйте ``WEBLATE_PORT=3001``.

После запуска:

- Веб-интерфейс: http://localhost:3001/ — логин ``admin`` / ``admin``.
- Исходящая почта ловится maildev на http://localhost:1081/ (порт ``1081``
  вместо апстримного ``1080``, который занят другим проектом; переопределяется
  переменной ``MAILDEV_PORT``).

Корень репозитория смонтирован в ``/app/src``, а Granian перезагружается при
изменениях под ``/app/src/weblate`` — поэтому правки Python в ``weblate/``
подхватываются на лету. ``dev-docker/data/`` смонтирован в ``/app/data``.

Проверки внутри контейнера:

.. code-block:: sh

   ./rundev.sh test weblate/checks/tests/test_markup.py # pytest в контейнере
   ./rundev.sh check # django `weblate check`

Игровые проверки и автофиксы
----------------------------

``weblate_customization/`` — это пакет ``uv_build``, но dev-контейнер его **не
устанавливает**. Вместо этого модуль *копируется* в ``sys.path`` контейнера
через ``/app/data/python``. После правки ``checks.py``, ``autofixes.py`` или
``machinery.py``:

.. code-block:: sh

   cp -r weblate_customization/src/weblate_customization dev-docker/data/python/

Проверки и автофиксы регистрируются переменными окружения сервиса ``weblate``
(уже прописаны в ``dev-docker/docker-compose.yml`` и
``deploy/environment.example``):

.. code-block:: yaml

   WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck,weblate_customization.checks.GameTokenCheck,weblate_customization.checks.GameLengthCheck,weblate_customization.checks.GameMaxLengthCheck,weblate_customization.checks.GameSourceMaxLengthCheck
   WEBLATE_ADD_AUTOFIX: weblate_customization.autofixes.LineSeparatorSpacing,weblate_customization.autofixes.RemoveAddedFinalStop,weblate_customization.autofixes.AddFrenchPunctuationSpacing

Что проверяется:

``game-markup``
    Unity-теги форматирования (``<color=#RRGGBB>``, ``<link>``, ``<size=N>``,
    ``<b>``, ``<sprite name="fire">``) и движковые плейсхолдеры (``{0}``,
    ``%KEY%``) в переводе совпадают с исходником как мультимножество.
``game-line-break``
    Разделитель строк движка Hero Craft ``$`` не потерян и не добавлен, и
    вокруг него нет пробелов. Флаг ``ignore-game-line-break``.
``game-token``
    Идентификатор подстановки перед скобкой (``item_type[|{0}]``,
    ``skirmish_league_id[gen|в {0}|в любой лиге]``) не переведён: переводится
    только тело в скобках, сам идентификатор — ключ поиска в движке. Флаг
    ``ignore-game-token``.
``game-number``
    Каждое число из исходника присутствует в переводе после снятия разметки,
    плейсхолдеров и полных дат и нормализации десятичного разделителя.
    Проверка асимметрична: добавленное в переводе число допустимо. Флаг
    ``ignore-game-number``.
``cyrillic-leak``
    Кириллица не протекла в перевод на язык, который её не использует.
``game-length``, ``max-length``, ``max-length-source``
    Бюджет длины по **видимой** длине: разметка и плейсхолдеры из расчёта
    исключаются. Последние две переопределяют штатные проверки Weblate.

Автофиксы применяются до записи перевода: ``LineSeparatorSpacing`` снимает
пробелы вокруг тесного ``$``, ``RemoveAddedFinalStop`` убирает добавленную
финальную точку, ``AddFrenchPunctuationSpacing`` ставит французские пробелы
перед ``: ; ! ?``.

``settings_docker.py`` вкладывает ``WEBLATE_ADD_CHECK`` /
``WEBLATE_REMOVE_CHECK`` в ``CHECK_LIST`` и ``WEBLATE_ADD_AUTOFIX`` /
``WEBLATE_REMOVE_AUTOFIX`` в ``AUTOFIX_LIST`` через ``modify_env_list``
(``weblate/utils/environment.py``). Правка блока окружения требует полного
``./rundev.sh`` (пересборка + запуск), а не рестарта контейнера.

Движки перевода: OpenRouter и LiteLLM
-------------------------------------

``machinery.py`` даёт два движка автоматических предложений с одинаковой
моделью настроек:

- **OpenRouter** (слаг ``openrouter``, ``RoutedLLMTranslation``);
- **LiteLLM** (слаг ``litellm``, ``RoutedLiteLLMTranslation``) — тот же движок
  против корпоративного прокси, base URL по умолчанию
  ``https://hcbifrost.herocraft.com/litellm/v1``, без OpenRouter-поля
  ``provider``.

Регистрация:

.. code-block:: yaml

   WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation,weblate_customization.machinery.RoutedLiteLLMTranslation

Настройки задаются глобально в ``/manage/machinery/``. Поле ``routing`` — это
JSON-объект, где ключом служит код целевого языка или ``"*"`` для fallback, а
значением — model ID. Точное совпадение проверяется до базового кода языка и
fallback. Карта без ``"*"`` допустима.

Project-level настройка **перекрывает глобальную поле за полем**: проект
хранит только то, что меняет (persona, style, ``language_instructions``), а
``key``, ``base_url`` и ``routing`` наследует.

Какой из двух движков использует проект, решают ``ROUTED_ENGINES`` и хелперы
``configured_routed_engine`` / ``available_routed_engine`` в
``weblate/trans/forms.py``: побеждает первый движок с пригодной конфигурацией
проекта (при обеих настроенных — ``openrouter``), причём на весь проект, без
per-language fallback на второй. Те же хелперы предвыбирают источник в форме
автоперевода и выбирают движок починки для судьи. Если переименовать движок,
надо переименовать и запись настроек (``Setting`` с ``category=2``, ``name`` =
слаг сервиса), иначе конфигурация «потеряется».

LLM-судья
---------

Судья — отдельный от перевода контур: он берёт уже существующие переводы и
выносит по ним вердикты, которые попадают в обычные проверки Weblate
``judge-flag``, ``judge-reject`` и ``judge-note`` (``weblate/checks/judge.py``)
и в отчёты по прогонам.

Как это устроено:

- **Два места (seats).** Прогон выполняют две модели (``JUDGE_SEATS = (1, 2)``,
  ``weblate/trans/judge.py``) параллельно, каждая со своим профилем: модель,
  размер батча, дедлайн, ``reasoning_effort``, ``response_format``,
  стриминг, температура. При ``WEBLATE_JUDGE_CONSENSUS_REJECT`` реджект
  требует согласия обоих.
- **Транспорт.** Единый эндпоинт chat-completions (по умолчанию
  LiteLLM-прокси), строгая JSON-схема ответа, бюджеты ретраев отдельно для
  транспортных, протокольных и переходных HTTP-ошибок, per-seat дедлайны,
  фолбэк-эндпоинт, разрешение алиасов моделей LiteLLM.
- **Цикл починки.** По вердикту судья формирует машинные инструкции ремонта и
  запрашивает у движка перевода новый кандидат
  (``WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS``); кандидат виден в редакторе строки.
- **История и отчёты.** ``ProducerRun``, ``JudgeRunUnit``, ``JudgeVerdict``,
  ``JudgeRequestAttempt``, ``JudgeAdaptiveState``, ``JudgeDeferral``
  (``weblate/trans/models/judge.py``); страница прогона —
  ``judge-runs/<uuid>/`` (``weblate/trans/views/judge.py``) с фильтрами по
  исходу, Pareto-разрезом и оверлеем изменившихся с момента вердикта строк.
- **Обслуживание.** Management-команды ``judge_backfill_candidates``,
  ``judge_close_refused_verdicts``, ``judge_release_advisory_holds``,
  ``check_judge_repair_routes``.

Судья по умолчанию выключен: ``WEBLATE_JUDGE_ENABLED=0``. Полный список
переменных (ключ, base URL, модели мест, батчи, дедлайны, ретраи,
``WEBLATE_JUDGE_MAY_APPROVE``) — в ``deploy/environment.example``, разбор
настроек — в ``weblate/settings_docker.py``.

Замеры прогонов и калибровки лежат в
``docs/product/measurements/`` (индекс — ``judge-measurements-index.md``),
планы и ревью — в ``docs/product/plans/`` и ``docs/product/reviews/``.

Учёт расходов на LLM
--------------------

Каждый вызов LLM (перевод и судья) пишется в ``LLMUsageLog``
(``weblate/trans/models/llm_usage.py``) с моделью, токенами и стоимостью;
``RunSpend`` агрегирует расход по прогону, чтобы у прогона был чек. Отчёт из
консоли:

.. code-block:: sh

   ./rundev.sh exec weblate weblate llm_usage_report

Массовое исправление проверок
-----------------------------

Детерминированно исправимые проваленные проверки можно починить пачкой, не
открывая строки по одной: серверный движок фиксапа
(``weblate/trans/fix_check.py``) и Celery-задача ``fix_failing_checks``
(``weblate/trans/tasks.py``) доступны из UI по адресу
``fix-check/<имя проверки>/<путь объекта>/``. Правка исходника при
косметическом фиксе каскадится на переводы. Документация —
``docs/admin/checks.rst``.

Приём loc-kit
-------------

``loc_kit_ingest/`` — детерминированный офлайн-импортёр таблиц строк студий
(CSV/TSV/XLSX): профиль выводится из шапки самой таблицы, на выходе
монолингвальный PO или билингвальный TBX с проверкой обратным разбором.

.. code-block:: sh

   python -m loc_kit_ingest --help
   cd loc_kit_ingest && uv run pytest # автономные тесты, без БД

Форма создания компонента принимает такой файл напрямую (вкладка «Upload
translation files»), а с галкой «Use as glossary» таблица уходит в
глоссарный сценарий: выбор листа, детерминированный вывод профиля, опциональный
LLM-фолбэк, локальная валидация перед публикацией, TBX-компонент. Импорт в
существующий глоссарий — **только добавление**: совпадение по
``(context, source)`` не перезаписывается. Site-wide LLM-анализ профиля
отдельный от машинного перевода и по умолчанию выключен
(``WEBLATE_LOC_KIT_PROFILE_ANALYSIS_ENABLED``).

Контейнер импортирует пакет из ``/app/data/python``, поэтому после правки:

.. code-block:: sh

   cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/

Руководство — ``docs/product/guides/loc-kit-ingest.md``.

MCP-сервер
----------

Для любых операций с работающим инстансом (проекты, компоненты, языки, юниты,
статистика) предпочитайте вендоренный MCP-сервер, когда он подключён к
агентской сессии, вместо ручных REST-вызовов.

.. code-block:: sh

   cd weblate-mcp
   pnpm install && pnpm build # dist/main.js — stdio-точка входа
   pnpm dev # nest start --watch
   pnpm test

Его ``.env`` указывает на ``http://localhost:3001/api/``. Если MCP-сервер не
подключён, используйте прямые REST-вызовы по этому URL с ``WEBLATE_API_TOKEN``,
который уже лежит в ``weblate-mcp/.env`` — не создавайте второй токен.

Разработка на хосте
-------------------

Для тестов, линтинга и проверки типов вне контейнера один раз установите
dev-зависимости:

.. code-block:: sh

   uv sync --all-extras --dev

Далее:

.. code-block:: sh

   uv run pytest weblate/utils/tests/test_search.py # один файл
   uv run pytest weblate/trans/tests/test_views.py -k slug # один тест
   uv run prek run --all-files # линт/формат (Ruff)
   uv run pylint weblate/ scripts/
   uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh

Хостовому pytest нужны ``DJANGO_SETTINGS_MODULE=weblate.settings_test``, сервер
PostgreSQL (``source scripts/test-database.sh`` выставляет ``CI_DB_*``) и
предварительный ``uv run ./manage.py collectstatic --noinput``. Запуск через
``./rundev.sh test`` избавляет от всей этой подготовки.

Документация
------------

- Гайд по проекту и для контрибьюторов этого форка — в ``AGENTS.md`` и каталоге
  ``docs/`` исходного кода.
- Документация upstream Weblate: https://docs.weblate.org/.

Лицензия
--------

Copyright © Michal Čihař michal@weblate.org (upstream).

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the `GNU General Public License
<https://www.gnu.org/licenses/gpl-3.0.html>`_ for more details.
