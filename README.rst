HCGameLoc
=========

Внутренний сервис непрерывной локализации проектов **Hero Craft**,
построенный на форке `Weblate <https://weblate.org/>`_. Поддерживается
v9833078908 (``origin`` = `v9833078908/weblate-HC
<https://github.com/v9833078908/weblate-HC>`_).


.. contents:: Содержание
   :local:
   :depth: 1

Что добавлено поверх upstream
-----------------------------

Всё под ``weblate/`` следует за upstream Weblate. Три вещи есть здесь, но
отсутствуют в upstream:

``weblate_customization/``
    Пакет с кастомной проверкой (см. `Проверка game-markup`_ ниже). Содержит
    ``GameMarkupCheck`` (id проверки ``game-markup``), который убеждается, что
    Unity-теги форматирования (``<color=#RRGGBB>``, ``<link>``, ``<size=N>``,
    ``<b>``) и движковые плейсхолдеры (``{0}``, ``%KEY%``) в переводе совпадают
    с исходником. Пакет также содержит ``RoutedLLMTranslation`` — OpenRouter-совместимый
    движок автоматических предложений, который выбирает model ID по целевому
    языку из JSON-карты ``routing``. Движок подключается через
    ``WEBLATE_ADD_MACHINERY``.

``weblate-mcp/``
    Вендоренный MCP-сервер `@mmntm/weblate-mcp
    <https://github.com/mmntm/weblate-mcp>`_ на NestJS, который ходит в
    локальный REST API Weblate, чтобы агентская сессия могла напрямую управлять
    проектами, компонентами и юнитами. См. `MCP-сервер`_ ниже.

``docs/plans/``
    Локальные планы (на русском) под задачи игровой локализации, например
    ``llm-judge-external-pipeline.md`` (внешний пайплайн валидации переводов
    через LLM-judge поверх REST API).

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
- Исходящая почта ловится maildev на http://localhost:1080/.

Корень репозитория смонтирован в ``/app/src``, а Granian перезагружается при
изменениях под ``/app/src/weblate`` — поэтому правки Python в ``weblate/``
подхватываются на лету. ``dev-docker/data/`` смонтирован в ``/app/data``.

Проверки внутри контейнера:

.. code-block:: sh

   ./rundev.sh test weblate/checks/tests/test_markup.py # pytest в контейнере
   ./rundev.sh check # django `weblate check`

Проверка game-markup
--------------------

``weblate_customization/`` — это пакет ``uv_build``, но dev-контейнер его **не
устанавливает**. Вместо этого модуль *копируется* в ``sys.path`` контейнера
через ``/app/data/python``. После правки
``weblate_customization/src/weblate_customization/checks.py``:

.. code-block:: sh

   cp -r weblate_customization/src/weblate_customization dev-docker/data/python/

Проверка импортируема, но **по умолчанию не зарегистрирована**. Чтобы включить
её, добавьте в окружение сервиса ``weblate`` в
``dev-docker/docker-compose.yml`` и перезапустите:

.. code-block:: yaml

   WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck

``settings_docker.py`` вкладывает ``WEBLATE_ADD_CHECK`` / ``WEBLATE_REMOVE_CHECK``
в ``CHECK_LIST`` через ``modify_env_list`` (``weblate/utils/environment.py``).
Тот же механизм есть для ``WEBLATE_ADD_ADDONS``, ``WEBLATE_ADD_APPS`` и т. д.

Routed LLM
----------

``RoutedLLMTranslation`` находится в
``weblate_customization/src/weblate_customization/machinery.py``. После каждой
правки скопируйте пакет в каталог Python-модулей dev-контейнера:

.. code-block:: sh

   cp -r weblate_customization/src/weblate_customization dev-docker/data/python/

Для регистрации движка сервису ``weblate`` нужна переменная:

.. code-block:: yaml

   WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation

Настройки задаются глобально в ``/manage/machinery/``. Поле ``routing`` — это
JSON-объект, где ключом служит код целевого языка или ``"*"`` для fallback, а
значением — OpenRouter model ID. Точное совпадение проверяется до базового кода
языка и fallback. Карта без ``"*"`` допустима.

Project-level настройка заменяет весь глобальный конфиг сервиса, поэтому для
неё нужно повторно задать API key, ``base_url`` и остальные нужные поля.

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
