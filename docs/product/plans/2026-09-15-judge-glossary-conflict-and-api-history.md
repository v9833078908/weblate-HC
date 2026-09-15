# Judge: конфликт с глоссарием и история REST-запуска

**Дата:** 2026-09-15.
**Статус:** исследование выполнено; минимальный план реализации ожидает согласования. Изменение product-кода и production не разрешено.
**Основание:** расследование Pirate Ships / Vietnamese; просьба оставить только необходимое и подтвердить решение экспериментом.
**Проверенная основа:** product-код checkout `dadab98e1ceb1eb51112386e4fe74e27b64ac2a5`; первоначальный план — коммит `40c950b`.
**Правила:** `AGENTS.md`. Для согласованной реализации — `ultrasuperpowers-executing-plans`.

## Цель и выбранный подход

Закрыть воспроизведённый дефект истории штатного REST judge-запуска через существующий `BatchAutoTranslate`. Уточнить документацию: `mode=judge` не означает безусловный повторный перевод. Отдельно, после утверждения переводов и разрешения на записи, исправить конкретные данные Pirate Ships.

Из обязательной реализации исключены изменение judge-промпта, новый eval-runner/корпус, смена моделей, новый glossary approval workflow, миграции, асинхронный API и переработка UI. Нельзя выдавать правдоподобное улучшение промпта за доказанное устранение смысловой ошибки. Семантический пропуск judge остаётся известной нерешённой проблемой.

Согласование плана не разрешает deployment, продовые записи или платные LLM-вызовы. История сама по себе перевод не исправляет. Восстановление данных не зависит от выпуска API-правки.

## Подтверждённый инцидент

Снимок расследования, не обещание неизменности production:

- `pirate-ships/glossary-ru/vi`, unit `163309`: `Форт` → `Ben tàu`, state `20`. Запись пришла из репозитория 2026-08-18; флаг `terminology` не доказывает человеческое утверждение перевода.
- Игровой unit `538134`, `title_restriction_forts`: `Форты` → `Ben tàu`, автоматический перевод от `mt:openrouter` 2026-09-15 в 11:22:26 UTC.
- Оба seat вернули `pass` без ошибок: `atlas/qwen3.8-max` в 11:24:07 UTC и `deepseek-v4-pro` в 11:24:12 UTC. HTTP 200, ответы разобраны; обратный перевод одного seat — `Форты`.
- В восстановленном request есть неверная glossary-пара и explanation про fort tier; project context описывает строительство форта. Конфигурация judge рабочая. Это не подтверждение гипотезы об отключённом judge.
- Найдены семь игровых совпадений `Форт` / `Ben tàu`; всего glossary-пара совпадала с 52 игровыми строками. 52 — область проверки, не число доказанных ошибок. `fort_enemy_name_3`, unit `150685`, означает `Причал Корсаров`: его нельзя исправлять заменой причала на форт.
- У verdicts общий UUID, но у HTTP attempts `run_id=null`; соответствующих `ProducerRun` и `JudgeRunUnit` нет.

Сохранены verdict/attempt/change records и восстановленный request из текущих данных, не неизменяемый transcript исходного HTTP body. URL и тело запроса клиентской обёртки неизвестны. Прежний вывод ассистента об обходе штатного API неверен: штатный endpoint сам воспроизводит это сочетание записей.

## Локальный эксперимент и границы доказательства

Код: `analysis/probes/judge-rest-history-plan-probe.py`. Настоящий Django REST action, token authentication, ORM, judge parser, обе worker-thread ветви и batch lifecycle; отдельная тестовая PostgreSQL БД. Внешний HTTP подменён `http_mock`, ключ фиктивный. В candidate-плече только binding конструктора `weblate.api.views.AutoTranslate` временно заменяется адаптером к существующему `BatchAutoTranslate`; файлы продукта не меняются.

**Результат:** 8 исследовательских тестов прошли. Проверены следующие наблюдения:

| Сценарий | Наблюдение |
|---|---|
| Исходный REST, один translated unit, два `pass` | HTTP 200; 2 verdicts с UUID; 2 attempts без run; 0 `ProducerRun`, 0 `JudgeRunUnit` |
| Candidate, свежий запуск | HTTP 200 с прежним `details`; 1 completed run с actor, translation scope и query; 1 participation; 2 attempts и 2 verdicts связаны с run |
| Candidate, повтор из cache | Новый completed run и cached participation; новых HTTP-вызовов нет; прежнее evidence остаётся у прежнего run |
| Пользователь без `translation.auto` | HTTP 403, нет run и HTTP-вызовов |
| Query с ID строки другого языка | Completed run с нулём оценённых строк; нет participation и HTTP-вызовов |
| Нулевой cap | Completed run, 1 skipped participation с причиной `cap`, HTTP-вызовов нет |
| HTTP 400 от обоих seat | REST по-прежнему отвечает HTTP 200 с сообщением об ошибке; run failed, 2 связанных attempts, 0 выдуманных verdicts |
| Существующий JSON API smoke | Прежние проверки invalid mode, `suggest/others` и `suggest/mt` проходят на batch-пути |
| Approved unit, исходный и candidate-путь | Текст не меняется, но оба пути переводят state `30` → `20` после `pass` при `JUDGE_MAY_APPROVE=False` |

Последняя строка — обнаруженное ограничение, **не успешная проверка сохранения approval**. Проба сначала проверяет реальное состояние `30` в БД, затем сравнивает оба пути. Это существующая проекция в `AutoTranslate.process_judge` через `state_for_verdict`, а не новая регрессия batch. Правило approved-state не меняется в узком исправлении истории; нельзя обещать его сохранность или включать approved строки в восстановительный judge-запуск.

Начальные ошибки пробы были исправлены в fixture: для REST judge нужен включённый `project.translation_review` даже у superuser; для существующего MT smoke явно установлена тестовая `weblate` machinery. Permission/configuration gates не отключались. Первоначальное предположение о сохранении approved-state не замаскировано: оно опровергнуто и заменено сравнением наблюдаемого поведения.

Пройдены не все будущие регрессии: отдельный запрет `unit.review`, locked component, чужой проект, неожиданное исключение и отображение report не проверены этой пробой. Они остаются проверками реализации ниже. Проба не измеряет качество judge или безопасность всех существующих переходов состояния.

Воспроизведение в уже работающем dev-container без его пересоздания:

```sh
docker exec \
  -e DJANGO_SETTINGS_MODULE=weblate.settings_test \
  -e CI_BASE_DIR=/tmp/weblate-judge-rest-plan-probe-20260915 \
  -e CI_DB_HOST=database -e CI_DB_NAME=judge_plan_probe_20260915 \
  -e CI_DB_USER=weblate -e CI_DB_PASSWORD=weblate \
  -w /app/src dev-docker-weblate-1 \
  pytest -n 0 -q -s analysis/probes/judge-rest-history-plan-probe.py
```

Имена БД и каталога выше выделены только для этой пробы; не заменять их именами рабочей БД/DATA_DIR. Исследовательский файл сохраняется для воспроизводимости на указанной основе. Его имя не совпадает с `python_files` из `pyproject.toml`, поэтому обычный pytest его не собирает: запуск только по явному пути. Baseline намеренно воспроизводит текущий дефект; после реализации он больше не является проходящей регрессией. В продуктовый набор переносится проверка исправленного поведения, не этот baseline.

## Почему правка промпта исключена

Доказан конкретный false negative, но не доказано, что выбранная формулировка его устраняет без ложных ошибок на допустимых игровых именах.

- В собственном исследовании `docs/product/measurements/2026-08-19-severity-recalibration-final.md` изменение severity-рубрики не прошло заданные гейты: ни одно плечо не обнаружило все critical во всех повторах. Это другой корпус/язык, не измерение текущего Vietnamese-инцидента; результат запрещает обещание, а не доказывает бесполезность любой правки промпта.
- [Huang et al., ACL Findings 2024](https://aclanthology.org/2024.findings-acl.211/) показали зависимость LLM-оценки перевода от состава входа: reference помогал, а source иногда ухудшал оценку. Исследование не проверяет наш glossary-промпт или текущие модели.
- [Kocmi and Federmann, EAMT 2023](https://aclanthology.org/2023.eamt-1.19.pdf) отдельно ограничивают результаты GEMBA тремя языковыми парами и различают качество на уровне систем и отдельных сегментов. Эти цифры нельзя переносить на `ru→vi` и гарантировать обнаружение конкретной ошибки.

Решение: prompt/schema/cache identity не менять. Реальные платные модели в этом исследовании не запускались. Для обоснования будущего семантического изменения потребуются человеческие метки, положительные контрольные случаи и отдельный разрешённый эксперимент; это не обязательная задача данного минимального плана.

## Задача 1. Полная история штатного REST-запуска

**Результат:** разрешённый REST judge-run имеет существующую историю с actor, scope, итогом и участием строк; свежие attempts связаны с run. Синхронность, response shape и существующие правила перевода не меняются.

**Зависимость:** согласование реализации. Лингвист, production и платный provider для этой задачи не нужны.

**Файлы и интерфейсы:**

| Файл | Изменение |
|---|---|
| `weblate/api/views.py` | `TranslationViewSet.autotranslate`: использовать `BatchAutoTranslate(translation, user=..., q=..., mode=...)` вместо прямого `AutoTranslate` |
| `weblate/api/tests.py` | Регрессия через REST и только недостающие проверки рисковых границ |
| `weblate/trans/autotranslate.py` | Существующий `BatchAutoTranslate.perform` используется без нового lifecycle-слоя |
| `docs/api.rst`, `docs/product/guides/producer-guide-weblate.md` | Точный REST-контракт, ограничения judge и порядок действий при неверном термине |
| `docs/changes.rst` | Краткая запись в текущем unreleased разделе со ссылкой на API |

**Контракт:** `POST /api/translations/{project}/{component}/{language}/autotranslate/` остаётся синхронным, ответ — HTTP 200 с `{"details": <message>}`. Сохраняются `AutoForm`, object/locked/permission gates, actor из authentication и очищенные аргументы `perform`. Никаких новых параметров, очередей и `enforce_permissions=False`. Весь action использует batch, не только ветка judge. Для MT действует существующая batch-история; `auto_source=others` не получает искусственный run. Низкоуровневый `AutoTranslate` остаётся для внутренних вызовов, TM и CLI.

**Действия:**

- [ ] До изменения action добавить целевую REST-регрессию: translated unit, узкий `q`, review-enabled project, разрешённый пользователь, два внешних mock-ответа и реальные ORM-записи. До исправления требование связанной истории падает; после — проходит.
- [ ] Переключить constructor на существующий batch с translation scope. Не переносить lifecycle во view, не менять соседний TM-путь создания translation, parser, prompt, state projection и исторические orphan records.
- [ ] Проверить свежую и cached историю, реальные связи attempts/verdicts, cap-skipped участие и failed run при отказе provider. Старое evidence не перепривязывать к новому cached run.
- [ ] Проверить недостающие границы: нет review permission; locked component; malformed form; ID чужого проекта; exception после создания run. Не должно быть чужой оценки/записи, вызова provider до допуска или зависшего RUNNING после обработанной ошибки. Использовать существующие batch-тесты, не дублировать их целиком.
- [ ] Проверить существующие `translate`/`suggest` эффекты и неизменность target в `suggest`; не заменять это assert на вызов `perform`. Для затронутого plumbing-теста `test_autotranslate_restrict_direct_editing` проверять наблюдаемый запрет записи/разрешённый suggestion.
- [ ] Выполнить локальный REST smoke на fixture-translation со stub HTTP provider: один translated unit, настоящий POST и просмотр соответствующего run/report уполномоченным пользователем. История должна показывать этот unit и исход. Production и платный provider не использовать.
- [ ] После smoke обновить owning docs и changelog. REST JSON `"mode": "judge"` не равен CLI `weblate auto_translate --mode judge`: Django-команда допускает только `translate`, `fuzzy`, `suggest`. Для неверного glossary сначала исправляют термин и подтверждённые targets; HTTP 200 / judge pass не доказывает правильный смысл. Предупредить, что судейская оценка может менять state и не является read-only аудитом.

**Проверка:** регрессия должна падать на исходном action и проходить на batch. Выполнить целевые существующие наборы в тестовой БД, затем описанный runtime smoke:

```sh
./rundev.sh test -n 0 weblate/api/tests.py -k autotranslate
./rundev.sh test -n 0 weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py
```

Wire-схема не меняется, поэтому `docs/specs/openapi.yaml` остаётся прежним. `docs/security/threat-model.rst` прочитан: новый endpoint family, token mode, outbound class или security claim не вводится; не расширять модель угроз без фактического изменения её условий.

## Задача 2. Адресное восстановление Pirate Ships

**Результат:** исправлены утверждённые неверные glossary/игровые targets; корректный причал, approved строки, placeholders и метаданные сохранены. Это исправление данных, не улучшение judge.

**Зависимости:** утверждённый вьетнамский термин и переводы от ответственного лингвиста; отдельное разрешение на актуальное production-чтение и точный write scope. Вариант `Pháo đài` пока кандидат. Задача не зависит от выпуска задачи 1.

**Интерфейсы:** штатное редактирование units через Weblate с audit/checks/VCS; production-доступ через `deploy/vps.sh` и `weblate shell` по `AGENTS.md`. Прямой `QuerySet.update` не использовать.

**Действия:**

- [ ] Перечитать glossary unit `163309` и allowlist ниже; сохранить source/target/state, context, explanations и flags. Проверить project/component/language. Более новая правка или approved-state исключают автоматическую запись по старому снимку.
- [ ] Утвердить canonical glossary target и каждый затронутый перевод. Сначала исправить glossary стандартным путём; проверить новый термин в `build_request` зависимой строки.
- [ ] Адресно применить согласованные targets. 52 ранее совпавшие строки можно рассмотреть как кандидатов, но write allowlist без разрешения не расширять. Не делать глобальную замену `Ben tàu` и не запускать `mode=judge` как force-translate.
- [ ] Сверить before/after в БД и файле/VCS, сохранность `{0}`, explanations/flags и неизменность контрольного unit `150685`. Зафиксировать фактически изменённые IDs в операционном отчёте. Rollback — адресный audited patch с проверкой отсутствия последующих правок, не откат проекта.

| Unit ID | Context |
|---|---|
| `538134` | `title_restriction_forts` |
| `538150` | `description_restriction_forts` |
| `416646` | `territorial_wars_game_manager_capital_open` |
| `148566` | `ship_name_fort_1` |
| `149614` | `dialog_name_fort_fleet` |
| `149625` | `screen_name_fort_equipment` |
| `150679` | `fort_label` |

**Проверка:** человеческое подтверждение смысла и exact before/after по разрешённому набору; существующие checks и сохранность placeholders. Платный MT/judge не обязателен для применения уже утверждённого patch и не включён в эту задачу. Если его запросят отдельно, согласовать scope/стоимость/записи audit tables; не называть запуск полностью read-only или его результат «AI-approved».

## Интеграция и готовность

После реализации задачи 1: целевые регрессии, REST/report smoke, lint/format изменённых файлов, commit и push по правилам репозитория. Deployment отдельно разрешается; пересоздание shared dev-stack и restart workers не входят в разрешение на исследование или commit. Откат API — штатный откат кода с отдельным разрешением на deployment, без удаления записанной истории.

**Доказано сейчас:** исходный REST создаёт orphan evidence; подстановка существующего batch устраняет именно этот дефект в выполненных сценариях. **Не доказано:** устранение смыслового пропуска judge, сохранность approved-state или отсутствие всех возможных регрессий. **Не выполнено:** product-правка, восстановление production и deployment.

API-решение готово к согласованию реализации. Production-восстановление заблокировано только предметными входами и разрешениями задачи 2. Исследование повысило уверенность в узком API-решении, но не превращает весь план в гарантию «100%».
