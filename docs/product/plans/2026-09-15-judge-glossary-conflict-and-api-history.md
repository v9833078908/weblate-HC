# Judge: проверка, предложения исправлений и управляемый API

**Дата:** 2026-09-15.
**Статус:** расширенный план по согласованным в чате решениям; разрешено обновление документа. Реализация, платные эксперименты, production-записи и deployment требуют отдельных разрешений.
**Основание:** расследование Pirate Ships / Vietnamese, локальная REST-проба и согласованный сценарий «предпросмотр → запуск → предложение → перепроверка → diff → подтверждение».
**Проверенная основа:** локальная проба product-кода `dadab98e1ceb1eb51112386e4fe74e27b64ac2a5`; первоначальный план — `40c950b`, исследовательская редакция — `360e06d`. При расширении 2026-09-15 повторно прочитаны текущие границы API, judge, suggestions и Celery.
**Правила:** `AGENTS.md`. Для согласованной реализации — `ultrasuperpowers-executing-plans`.

## Цель и выбранный подход

Дать продюсеру без знания целевого языка управляемую проверку и исправление перевода через API. Судьи предлагают и перепроверяют; пользователь разрешает запись, но его нажатие не считается лингвистической оценкой. Существующий синхронный REST сохраняет wire-контракт; новый фоновый API использует существующие producer runs и suggestions, а не второй независимый движок.

В компании нет переводчиков и лингвистов. Человеческий лингвистический эталон не является доступной зависимостью. Продюсер может уточнить смысл игры на русском; система сохраняет это как контекст конкретной строки. Согласие двух моделей, обратный перевод и пользовательское подтверждение не доказывают качество иностранного текста.

По сравнению с минимальным планом в scope добавлены защита approved, проверка кандидата до записи, preview, async API, cache/force, лимиты, идемпотентность, частичные результаты, отмена, массовое применение, уточнение контекста и безопасный откат. Миграции допустимы для необходимых durable-состояний. Полный редизайн frontend, новые роли, смена провайдера, автоматическое исправление глоссария и обещание лингвистической точности не входят в scope.

Две независимые линии доказательства: API/безопасность проверяются локально со stub provider; качество судей — отдельным ограниченным экспериментом `ru→vi`. Действующие промпты не меняются до сравнения. API проектируется без ограничения языка, но результат эксперимента не обобщается на другие пары. История сама по себе перевод не исправляет.

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

Последняя строка — обнаруженный дефект, **не успешная проверка сохранения approval**. Проба сначала проверяет реальное состояние `30` в БД, затем сравнивает оба пути. Это существующая проекция в `AutoTranslate.process_judge` через `state_for_verdict`, а не новая регрессия batch. Узкая задача истории не исправляет её; расширенный план закрывает её отдельно в задаче 3. До этого нельзя включать approved строки в восстановительный judge-запуск.

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

## Почему промпт меняется только после эксперимента

Доказан конкретный false negative, но не доказано, что выбранная формулировка его устраняет без ложных ошибок на допустимых игровых именах.

- В собственном исследовании `docs/product/measurements/2026-08-19-severity-recalibration-final.md` изменение severity-рубрики не прошло заданные гейты: ни одно плечо не обнаружило все critical во всех повторах. Это другой корпус/язык, не измерение текущего Vietnamese-инцидента; результат запрещает обещание, а не доказывает бесполезность любой правки промпта.
- [Huang et al., ACL Findings 2024](https://aclanthology.org/2024.findings-acl.211/) показали зависимость LLM-оценки перевода от состава входа: reference помогал, а source иногда ухудшал оценку. Исследование не проверяет наш glossary-промпт или текущие модели.
- [Kocmi and Federmann, EAMT 2023](https://aclanthology.org/2023.eamt-1.19.pdf) отдельно ограничивают результаты GEMBA тремя языковыми парами и различают качество на уровне систем и отдельных сегментов. Эти цифры нельзя переносить на `ru→vi` и гарантировать обнаружение конкретной ошибки.

Выполненная локальная проба не меняла prompt/schema/cache identity и не обращалась к реальным платным моделям. Расширенный план добавляет сравнение разделённых проверок смысла и терминологии, но не объявляет этот подход победителем заранее. Без лингвиста доступны контролируемые искажения и предметные уточнения продюсера; общую точность вьетнамской оценки ими измерить нельзя. Задача 9 фиксирует эту границу и разрешения на эксперимент.

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
- [ ] Переключить constructor на существующий batch с translation scope. Не переносить lifecycle во view, не менять соседний TM-путь создания translation, parser, prompt и исторические orphan records. Защита состояния и новый proposal-only режим принадлежат задаче 3, а не этой правке истории.
- [ ] Проверить свежую и cached историю, реальные связи attempts/verdicts, cap-skipped участие и failed run при отказе provider. Старое evidence не перепривязывать к новому cached run.
- [ ] Проверить недостающие границы: нет review permission; locked component; malformed form; ID чужого проекта; exception после создания run. Не должно быть чужой оценки/записи, вызова provider до допуска или зависшего RUNNING после обработанной ошибки. Использовать существующие batch-тесты, не дублировать их целиком.
- [ ] Проверить существующие `translate`/`suggest` эффекты и неизменность target в `suggest`; не заменять это assert на вызов `perform`. Для затронутого plumbing-теста `test_autotranslate_restrict_direct_editing` проверять наблюдаемый запрет записи/разрешённый suggestion.
- [ ] Выполнить локальный REST smoke на fixture-translation со stub HTTP provider: один translated unit, настоящий POST и просмотр соответствующего run/report уполномоченным пользователем. История должна показывать этот unit и исход. Production и платный provider не использовать.
- [ ] После smoke обновить owning docs и changelog. REST JSON `"mode": "judge"` не равен CLI `weblate auto_translate --mode judge`: Django-команда допускает только `translate`, `fuzzy`, `suggest`. HTTP 200 / judge pass не доказывает правильный смысл. До задачи 3 документировать существующие изменения state; после неё описать точную границу защищённой проверки и legacy-поведения.

**Проверка:** регрессия должна падать на исходном action и проходить на batch. Выполнить целевые существующие наборы в тестовой БД, затем описанный runtime smoke:

```sh
./rundev.sh test -n 0 weblate/api/tests.py -k autotranslate
./rundev.sh test -n 0 weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py
```

Только для задачи 1 wire-схема не меняется. Расширенный API требует обновления `docs/specs/openapi.yaml` и `docs/security/threat-model.rst` в задаче 10: прежнее решение оставить эти файлы неизменными больше не относится ко всему плану.

## Задача 2. Адресное восстановление Pirate Ships

**Результат:** адресное исправление согласованных данных Pirate Ships с сохранением корректного причала, placeholders и метаданных. Это восстановление данных, не доказательство общего качества judge.

**Зависимости:** отдельное разрешение на актуальное production-чтение и точный write scope; предметное уточнение продюсером значения «Форт» и явное разрешение применить предложенные targets с пониманием отсутствия лингвистической проверки. Вариант `Pháo đài` остаётся кандидатом, не утверждённым эталоном. Обязательный ответственный лингвист из прежнего плана исключён как недоступный. Штатный новый proposal-сценарий зависит от задач 3–7; отдельный ручной audited patch возможен только по собственному разрешению.

**Интерфейсы:** штатное редактирование units через Weblate с audit/checks/VCS; production-доступ через `deploy/vps.sh` и `weblate shell` по `AGENTS.md`. Прямой `QuerySet.update` не использовать.

**Действия:**

- [ ] Перечитать glossary unit `163309` и allowlist ниже; сохранить source/target/state, context, explanations и flags. Проверить project/component/language. Более новая правка или approved-state исключают автоматическую запись по старому снимку.
- [ ] Получить предметное уточнение на русском, сформировать варианты и объяснения для glossary target и каждой строки. Подтверждение продюсера — разрешение изменения, не человеческая оценка вьетнамского. Изменение самого glossary согласовать отдельно: оно не следует из принятия предложения для игровой строки. При разрешённой правке glossary проверить новый термин в `build_request` зависимой строки.
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

**Проверка:** exact before/after по разрешённому набору, соответствие уточнённому игровому смыслу в доступных объяснениях, существующие checks и сохранность placeholders. Это не проверка естественности вьетнамского. Реальные MT/judge-вызовы требуют отдельного scope/лимита/разрешения; они записывают evidence и не называются полностью read-only. Approved не менять по старому снимку; в новом сценарии требуется отдельное подтверждение снятия одобрения.

## Согласованный пользовательский контракт

Эти решения утверждены пользователем в обсуждении; названия новых HTTP routes и технических полей ниже — проектируемый контракт, а не уже существующий API.

| Решение | Наблюдаемое поведение | Реализация |
|---|---|---|
| Подтверждение вместо автозаписи | Проверка, генерация и перепроверка не меняют текущие target/state; запись только после diff и явного действия | 3, 6 |
| Approved участвуют в проверке | Замечания доступны; проверка сохраняет текст и `30`. Применение построчно предупреждает о переходе в `20`; AI не выдаёт approval | 3, 6 |
| Конфликт с глоссарием | Отдельный результат, объяснение и предложение; только отдельное подтверждение, без автоматического изменения glossary | 3, 6, 7, 9 |
| Предпросмотр | Количество выбранных, approved, исключённых, cached и требующих новых вызовов; явное подтверждение запуска | 4 |
| Прямой API-запуск | Автоматизированный клиент может обойти preview, но обязан указать scope и лимит; сервер выполняет тот же допуск | 4, 5 |
| Кэш и повтор | По умолчанию только актуальное evidence; force явно оплачивается. Изменение входа, контекста или профиля делает старую оценку непригодной | 3, 4 |
| Один цикл | Одна генерация исправления и одна перепроверка; неудача не запускает новый семантический цикл автоматически | 3 |
| Консервативный apply | Все применимые механические gates и обе смысловые оценки кандидата должны пройти; расхождение, unparsed или сбой блокируют apply | 3, 6 |
| Массовое применение | Только выбранные обычные предложения. Approved и glossary conflicts подтверждаются по одной строке | 6 |
| Частичный результат | Готовые результаты сохраняются; технически неуспешные строки можно повторить без повторной обработки успешных | 5 |
| Фоновая работа | `202`, `run_id`, status/results URLs; закрытие страницы не прекращает запуск | 5 |
| Права | Существующие project/object permissions, повторная проверка при записи; никаких новых ролей или обхода текущих более строгих candidate guards | 4–8 |
| Отмена | Прекратить новые HTTP-запросы, сохранить ответы уже отправленных; не обещать возврат их стоимости | 5 |
| Лимит | Не обрезать scope молча; превышение блокирует admission до явного изменения выбора/лимита | 4 |
| Совместимость | Старый sync `autotranslate` не превращается в `202`; новый async API отдельный, engine/lifecycle общие | 1, 5 |
| Языки | API поддерживает существующие настроенные языки; исследование сначала только `ru→vi` | 9 |
| Нет лингвиста | Никаких вымышленных human labels, процентов уверенности и «AI-approved»; продюсер подтверждает действие, не иностранный язык | 9, 10 |
| Уточнение игры | Вопрос на русском, варианты плюс свой ответ/«не знаю»; уточнение не применяет target и не меняет glossary | 7 |
| Контекст строки | Ответ видим, редактируем и используется далее только для этой строки; старое evidence становится stale | 7 |
| Продолжение после ответа | Явный новый ограниченный запуск с платным предупреждением, а не скрытый retry; не знать ответ допустимо | 7 |
| Конкурентные изменения | Stale proposal не перезаписывает новую работу; bulk возвращает отдельный исход каждой строки | 6 |
| Повтор запроса | Не дублирует запуск, применение и audit-события; изменённый payload с прежним ключом — конфликт | 5, 6 |
| Откат | Связан с исходным apply и разрешён только без последующих изменений; восстановление `30` требует review | 8 |
| Порядок внедрения | Сначала доказательства API-безопасности; действующие промпты меняются только после отдельного сравнения | 9, 10 |

### Важные существующие границы

Не строить новый TMS или второй judge engine. Повторно прочитанный код:

- `weblate/trans/models/judge.py`: `ProducerRun`, `JudgeRunUnit`, `JudgeRequestAttempt`, `JudgeVerdict`, `JudgeCandidateMetadata`, hashes и разрешения verdict resolution. У run сейчас только `queued/running/completed/failed`; partial/cancel и durable idempotency не появляются от переименования UI.
- `weblate/trans/autotranslate.py`: `preview_judge_scope` сейчас обрезает queryset по cap и считает вызовы по выбранному объёму, не предоставляет новый cache-aware admission. `process_judge` может pretranslate, передаёт mutating repairs и отдельно проецирует verdict в state. Просто `writable_ids=set()` не защищает state.
- `weblate/trans/judge_loop.py`: `run_judge_batch`, `repair_targets`, `_store_candidate`, `active_judge_candidate`, `accept_judge_candidate`, `queue_judge_recheck`. Кандидат уже хранится как native `Suggestion`; создавать параллельную сущность с копией текста не нужно.
- `accept_judge_candidate` сейчас сохраняет target в `20`, удаляет suggestion и затем вызывает `queue_judge_recheck`. Новое требование обратное: перепроверка кандидата **до** записи, без нового платного запроса от apply.
- `weblate/trans/models/suggestion.py`: `Suggestion.accept` отправляет judge-кандидаты в усиленный guard; vote autoaccept их пропускает. Защита должна остаться общей для UI, REST и bulk, иначе новый API можно обойти обычным принятием suggestion.
- `weblate/trans/tasks.py:auto_translate` уже принимает `producer_run_id`, actor и параметры judge; использует `BatchAutoTranslate`. Повторное использование требует явного proposal-only режима, а не передачи запрета в один из нескольких mutating paths.
- `weblate/trans/models/unit.py:Unit.update_explanation` пишет audit и может распространять explanation исходной unit на связанные языковые units. Изолированное уточнение нельзя без анализа записать в source explanation или затереть существующее Explanation.
- `weblate/trans/views/edit.py`, `weblate/trans/views/judge.py`, `weblate/templates/snippets/judge-verdict.html` уже обслуживают candidate/report. Политика apply общая; старый UI не должен позволить то, что новый API запрещает.

## Общие интерфейсы расширения

### Снимок и evidence

Единица допуска — стабильный ordered набор разрешённых unit IDs, не повторно раскрываемый в worker произвольный `q`. Снимок строки включает source и все plural forms target, state, значимый context, glossary/profile/request identity и версию изменения. Новый контекст делает старое evidence stale. Hash текста недостаточен для защиты от «изменили и вернули обратно»: apply/undo проверяют также монотонную revision/audit identity.

Хранить кандидат в `Suggestion`, а проверку кандидата связывать с его ID и hash, исходным снимком, run и версиями профиля. Проверка альтернативного target не должна попадать в `current_verdict(unit)` как оценка живого текста. Durable evidence для кандидата расширяет существующую модель там, где она не умеет различать subject; не подменять `Unit.target` даже временно ради проверки.

Существующий severity `minor` сворачивается в `pass`; поэтому один агрегированный `pass` недостаточен для нового строгого допуска. Хранить структурированные issues и полноту обеих оценок. Если осталась смысловая ошибка любого уровня, предложение не становится применимым. Конфликт терминологии — отдельная ось: explicit acknowledgement не обходит механический провал или расхождение о смысле.

### Проектируемый HTTP-контракт

Предпочесть стандартные DRF serializers/router patterns в `weblate/api/`. Названия ниже предлагаются для реализации и должны быть опубликованы в OpenAPI одновременно с кодом. Scope использует существующие типы `ProducerRun.ScopeType` и object permissions; новая поверхность не расширяет доступ actor.

| Запрос | Результат |
|---|---|
| `POST /api/judge-runs/preview/` | `200`: scope snapshot/token, counts, exclusions, approved/cached/new-call counts, лимит и предупреждения, `can_start`; без LLM-вызовов |
| `POST /api/judge-runs/` | `202`: `run_id`, `status_url`, `results_url`; preview token либо явные scope/query/limit, `force_recheck`; обязательный `Idempotency-Key` |
| `GET /api/judge-runs/{id}/` | Состояние выполнения, phase/progress/heartbeat, counts, warnings, доступные действия; состояние качества отдельно |
| `GET /api/judge-runs/{id}/results/` | Пагинированные строки, evidence provenance/freshness, issues, before/after plurals, diff, apply eligibility и причины запрета |
| `POST /api/judge-runs/{id}/cancel/` | Идемпотентный запрос остановки новых вызовов; terminal run не возобновляется |
| `POST /api/judge-runs/{id}/retry/` | Новый связанный run только для технически неуспешных строк; явный лимит и idempotency key |
| `POST /api/judge-runs/{id}/apply/` | Выбранные candidate IDs и expected revisions; per-row results. Особый кандидат — ровно один и с явными acknowledgement flags |
| `POST /api/judge-runs/{id}/clarifications/` | ID строки, expected context revision и ответ продюсера; сохраняет контекст, не запускает provider |
| `POST /api/judge-runs/{id}/applications/{application_id}/undo/` | Guarded audited восстановление конкретного apply, не откат run или проекта |

Continuation после clarification использует новый `POST /api/judge-runs/` с родительским run и новым снимком; отдельный engine/неограниченный regenerate endpoint не нужен. Для write-команд нужен idempotency key; read/preview не должны создавать платную работу.

Поля и error codes закрытые, machine-readable, не зависят от локализованного текста. Невалидный payload — `400`, недоступный scope — существующий permission-filtered `403/404`, stale snapshot/несовпадающий idempotency payload/превышенный admission limit — `409` с конкретным code. Preview показывает превышение без LLM, start отказывает. Валидный bulk apply — `200` с отдельными `applied/already_applied/stale/forbidden/needs_individual_confirmation/not_verified` исходами; не превращать частичную запись в ложное «всё применено».

Execution status: `queued`, `running`, `cancel_requested`, `completed`, `partial`, `failed`, `cancelled`. `completed` означает законченную обработку, не правильные переводы. Ошибка provider и semantic issue — разные поля. Row result различает checked/no issues, proposal ready, needs clarification, unresolved, technical failure, skipped/stale; применение/откат — отдельная история, а не переписывание исходного evidence.

Run ID не секрет доступа. GET, retry, cancel, clarify, apply и undo заново фильтруют scope и проверяют действующие права, включая project tokens. Сохранять текущий более строгий guard judge-кандидата (`unit.review` и `translation.auto`) и проверять штатное право редактирования; не ослаблять legacy acceptance ради новой таблицы ролей. Approved требует review и подтверждения потери approval. Уточнение контекста требует существующего права изменения соответствующего контекста.

## Задача 3. Proposal-only проверка и защита состояний

**Результат:** новый цикл не изменяет живой перевод до apply, включая approved, pretranslation, state projection и deterministic `max-length`. Уже существующие candidates используются, но проверяются до записи.

**Зависимости:** согласование реализации; не требует реальных LLM. Задача 1 может выполняться независимо до общей интеграции.

**Файлы:** `weblate/trans/autotranslate.py`, `weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`, `weblate/trans/models/suggestion.py`, `weblate/trans/tasks.py`; при необходимости новая миграция в `weblate/trans/migrations/` с номером от актуального graph. Номера и имена новых моделей заранее не выдумывать.

**Действия:**

- [ ] Сначала закрепить регрессией `approved 30 → 20` и устранить отмену человеческого одобрения простой judge-проверкой в общих вызовах. Не переписывать исторические records.
- [ ] Ввести явную политику proposal-only для нового run; все ветви, включая MT/pretranslation, max-length и post-judge projection, сохраняют target/state. Не объявлять весь legacy `autotranslate` read-only: его переводные режимы сохраняют назначение.
- [ ] Разделить проверку текущего текста и кандидата; исходные verdicts и свежая оценка альтернативы имеют разный subject. Сохранить all-plurals, контекст и provenance.
- [ ] Генерация по конкретным найденным issues → применимые механические gates → обе оценки кандидата. Не записывать кандидата в Unit ради вызова существующего evaluator.
- [ ] Ограничить семантический цикл одной генерацией и одной перепроверкой; исчерпанный лимит/расхождение/новая ошибка дают unresolved. Транспортные retries остаются ограниченными и входят в отдельный request ceiling, а не создают новые semantic attempts.
- [ ] Кэшировать только полное актуальное evidence с профильной identity; cached новый run получает своё участие без переноса старых attempts. Force bypass явный. Неподдерживаемый repair language — явный исход, не fallback на другой движок без контракта.

**Проверка:** исходная approved-regression красная до исправления, зелёная после. Через настоящий ORM со stub HTTP проверить pass/reject/unparsed, cache/force, конфликт и max-length: target/state/Change не меняются при проверке; verified candidate содержит оценку именно предложенного target. Попытка перепроверки не заменяет current evidence живой строки. Использовать `weblate/trans/tests/test_judge_loop.py`, `test_judge_autotranslate.py`, `test_judge_persistence.py`, `test_judge_views.py`; поведенческие тесты старого apply-after-write мигрировать на новый порядок, а не сохранять обход.

## Задача 4. Cache-aware preview и точный допуск

**Результат:** пользователь знает scope до оплаты; работа не обрезается молча и не расширяется между preview и worker.

**Зависимость:** snapshot/cache contract задачи 3. **Файлы:** `weblate/api/views.py`, `serializers.py`, `urls.py`, `tests.py`; `weblate/trans/autotranslate.py`, `models/judge.py`, `forms.py`.

**Действия:**

- [ ] Повторно использовать selection/permissions текущего flow, но отделить подсчёт полного scope от legacy `units[:limit]`. Сохранить closed ordered IDs и per-row snapshots.
- [ ] Вернуть selected/approved/excluded/cached/new-call counts, причины исключений, возможные фазы и hard limits; preview не генерирует кандидаты и не вызывает LLM.
- [ ] Admission limit относится к строкам, требующим новых вызовов; резерв покрывает возможное исправление и перепроверку каждой допущенной строки. Cached строки не расходуют его. Отдельный серверный bound ограничивает общий размер snapshot и HTTP attempts с retries.
- [ ] При превышении отказаться от запуска, не выбирать первые N. Повышение пользовательского лимита не обходит серверный максимум. Не обещать точную денежную стоимость без надёжного тарифа; показывать, что это лимит работы, не валютный бюджет.
- [ ] Между preview/start проверить actor/scope/config revisions. При drift вернуть явный stale preview и потребовать обновления; при прямом start выполнить те же проверки атомарно. На execution drift не оплачивать повторно без нового допуска.

**Проверка:** permission-filtered counts, foreign project/language IDs, locked component, malformed query, mixed cached/fresh, нулевой cap с all-cached и с fresh scope, превышение, drift и прямой start. Ни одного provider call до успешного допуска. Реальный REST smoke на локальном fixture с несколькими строками и точной сверкой preview/результата.

## Задача 5. Durable async run, частичные результаты и отмена

**Результат:** принятый запуск переживает закрытие клиента, результаты читаются по ID, явный повтор не дублирует работу.

**Зависимость:** 3–4. **Файлы:** `weblate/api/views.py`, `serializers.py`, `urls.py`, `tests.py`; `weblate/trans/models/judge.py`, `autotranslate.py`, `tasks.py`, `judge_loop.py`, `weblate/utils/celery.py`; необходимые миграции. Сначала LSP references для изменяемых exported symbols и всех потребителей новых run statuses.

**Действия:**

- [ ] Создавать и читать существующий `ProducerRun`; durable request key с unique constraint на actor/scope/operation и fingerprint payload. Одновременные одинаковые POST возвращают один run, иной payload под тем же ключом — conflict.
- [ ] Связать admission и dispatch так, чтобы crash между DB commit и broker publish не оставлял бесконечный queued. Переиспользовать проверенные repository dispatch/recovery patterns, не обещать надёжность одного `on_commit(delay)`. Не создавать второй общий orchestration framework.
- [ ] Worker использует pre-created run и snapshots; повторная доставка не создаёт новый run/кандидат/платный вызов для уже сохранённого результата. Источник прав — актуальный actor, не сохранённый флаг допуска.
- [ ] Развести progress/liveness и качество; расширить все enum consumers/report counts для partial/cancel. Готовые rows фиксируются до перехода к следующим; необработанное исключение даёт наблюдаемый terminal/recoverable исход, не вечный RUNNING.
- [ ] Проверять cancel перед каждым новым внешним вызовом, включая обе seat-ветви, repair, retries и deferrals. Уже отправленный запрос может завершиться; сохранить его ответ. Cancel не удаляет готовые предложения и не даёт deferred drain обойти остановку.
- [ ] Retry technical failures создаёт связанный новый run с новым явным лимитом; semantic unresolved не считается техническим сбоем. Успешные rows/seat evidence повторно не оплачивать при неизменном входе.

**Граница гарантии:** сервер обеспечивает идемпотентность клиентских команд и сохранённых результатов. Crash после принятия HTTP провайдером, но до сохранения ответа, без provider idempotency не позволяет гарантировать exactly-once billing. Такой outcome показывать как неизвестный, не скрывать за автоматическим платным повтором; отдельное подтверждение сообщает о возможной повторной оплате.

**Проверка:** локальный API → настоящий worker → stub provider → status/results; закрыть клиент и получить тот же run заново. Воспроизвести конкурентный POST, publish failure, redelivery, частичный HTTP failure, снятые права и отмену между запросами/до repair/при deferral. Счётчик stub подтверждает отсутствие новых вызовов после наблюдения cancel и отсутствие повторных успешных calls. Проверить существующие `weblate/trans/tests/test_judge_deferrals.py` и task/liveness tests, найденные через references; не тестировать только Mock forwarding.

## Задача 6. Подтверждение, bulk apply и защита от обходов

**Результат:** пользователь применяет только проверенный актуальный diff; массовая команда возвращает честные отдельные исходы.

**Зависимость:** 3–5. **Файлы:** `weblate/trans/judge_loop.py:accept_judge_candidate`, `models/suggestion.py:Suggestion.accept`, `models/judge.py`, `models/unit.py`; API views/serializers/tests и `weblate/trans/views/edit.py`.

**Действия:**

- [ ] Общий guard проверяет actual edit/review/auto permissions, current state/revision, source/context/profile, candidate hash и обе полные оценки кандидата. Legacy API/UI acceptance, bulk и votes не обходят guard; обычные non-judge suggestions сохраняют свои правила.
- [ ] Применять через штатный `Unit.translate`, без propagation на другие строки и без прямого SQL target update. Audit сохраняет actor, run/candidate/evidence, before/after plurals и state. Сохранить receipt до удаления native suggestion.
- [ ] Обычный bulk допускает выбранные кандидаты. Approved и glossary conflict исключены из bulk; отдельный запрос подтверждает потерю approval и/или конфликт. Если строка стала approved после preview, старый обычный apply её не меняет.
- [ ] Применение approved меняет `30` на `20`, не выдаёт новое approval. Другие применённые исправления также не получают AI approval. Неприменённые строки остаются в исходных state.
- [ ] Каждая строка — отдельная атомарная операция с повторным guard под lock. Ошибка одной не откатывает уже применённые остальные; durable receipts позволяют безопасно повторить прерванный batch без дублирования audit.
- [ ] Apply не вызывает новый платный recheck: он уже завершён до подтверждения. Сохранённые candidate evidence связываются с применением так, чтобы ни чужой target, ни stale profile не стали current evidence.

**Проверка:** two users edit/apply race, ABA edit, permission revoked, old candidate after new profile/context, bypass через ordinary suggestion API, approved/conflict в bulk, оба acknowledgement для совмещённого случая, partial commit и retry. Проверить реальные target/state, audit и file/VCS persistence, не только HTTP status. Строка с failed/unparsed candidate evaluation не применяется даже индивидуально. Ручное обычное редактирование остаётся доступно по своим правам, не маскируется под validated apply.

## Задача 7. Уточнение смысла без лингвиста

**Результат:** продюсер отвечает на предметный вопрос на русском, контекст сохраняется, затем явно запускается новая ограниченная попытка.

**Зависимость:** 3–5; экспериментальное обнаружение вопросов — 9. **Файлы:** `weblate/trans/models/judge.py`, `judge_loop.py:build_request`, `weblate/trans/models/unit.py`, API serializers/views/tests; подтверждённая интеграционная граница `weblate/machinery/llm.py` для последующих переводов, при необходимости миграция.

**Действия:**

- [ ] Возвращать вопрос, основание конфликта, контекст строки и варианты; всегда разрешать свой ответ/«не знаю». Модель не выбирает ответ за продюсера и не выдаёт вариант за установленный факт.
- [ ] Сохранить actor/version и отдельное уточнение конкретной target unit, не затирать Explanation/Character/flags и не распространять ответ через source explanation или glossary. Переиспользовать подходящее existing storage только при сохранении этих границ; иначе минимальное durable дополнение модели.
- [ ] Сделать уточнение доступным для чтения/редактирования через API и включить в общую сборку контекста следующих переводов и обеих проверок. Hash/cache identity учитывает его revision. Не ограничивать использование одним текущим run.
- [ ] Clarification write не вызывает LLM, не применяет кандидат и не меняет target/state/glossary. «Не знаю» оставляет unresolved; ответ с новым смыслом инвалидирует старое предложение.
- [ ] Новая попытка после ответа — отдельное подтверждённое admission с родительской связью и лимитом 1 repair + 1 recheck. Старый исчерпанный semantic cycle не запрещает эту новую авторизованную работу.

**Проверка:** русский ответ доступен в следующем MT и judge request на stub, соседняя строка/язык и существующее Explanation неизменны, old evidence stale; duplicate clarification не создаёт лишние версии. Изменение ответа другим пользователем блокирует старый apply. Нет outbound call до явного continuation.

## Задача 8. Безопасный отменяющий patch

**Результат:** пользователь отменяет конкретное применённое исправление, не теряя последующую работу.

**Зависимость:** receipts/revisions задачи 6. **Файлы:** `weblate/trans/models/judge.py`, `models/unit.py`, `weblate/api/views.py`, `serializers.py`, `tests.py`; существующие audit механизмы.

**Действия:**

- [ ] Хранить before target/state и applied revision в durable receipt, даже когда Suggestion удалена; чтение защищено теми же scope permissions.
- [ ] Под lock сравнить текущую revision со своей applied revision. Любое последующее изменение, включая возврат идентичного текста, запрещает автоматический undo; вернуть diff/conflict вместо overwrite.
- [ ] Восстановить через штатный audited edit без propagation; прежний `30` только с действующим review permission. Undo не выдаёт новые judge evidence за проверку восстановленного текста и не запускает платный вызов.
- [ ] Идемпотентный повтор возвращает прежний undo receipt; нельзя повторно откатить более новую работу. Откат только своего application, не всей истории run.

**Проверка:** apply → undo восстанавливает plural targets и state, повтор не добавляет Change; subsequent edit/ABA и потеря permission блокируют; сохранность файла/VCS и соседних строк проверяется после flush штатного writer.

## Задача 9. Эксперимент качества без ложного эталона

**Результат:** измеренное сравнение гипотезы на ограниченных задачах, а не обещание общей точности и не обязательная победа нового промпта.

**Зависимости:** локальная подготовка не требует provider; платное сравнение требует отдельного разрешения с точными моделями, числом строк/повторов и request ceiling. Производственные выгрузки отдельно разрешаются. Лингвист не является условием продолжения.

**Артефакты:** существующие `analysis/probes/`, `analysis/data/`; новый датированный результат в `docs/product/measurements/` создаётся при выполнении эксперимента. Product-кандидат затрагивает `weblate/trans/judge.py`, `judge_loop.py`, `models/judge.py`, существующие `weblate/trans/judge_prompts/` только после сравнительного решения.

**Действия:**

- [ ] Подготовить `ru→vi` набор: известный инцидент и контроль `Причал Корсаров`; контролируемые пропуски, подмены чисел/placeholders; разные glossary/context conflicts. Не называть неизвестные исходные вьетнамские строки «правильными» только потому, что они уже в продукте.
- [ ] Разделить provenance меток: детерминированно известное искажение, установленный предметный смысл, модельная гипотеза и неизвестное. Подготовить неизменённые контрольные inputs; изменение моделью их текста — сигнал вмешательства, не автоматически доказанная порча.
- [ ] Зафиксировать набор, критерии и отдельные development/контрольные случаи до подбора промптов. Не подгонять эксперимент только под `Форт`, не считать обратный перевод или третью модель независимым human gold.
- [ ] Сравнить текущие две проверки и гипотезу разных обязанностей: смысл исходник/target/игровой контекст без целевой glossary-пары; терминология с glossary и явным конфликтом с контекстом. Генератор исправлений отдельной ролью использует найденные issues, после чего кандидат проходит обе проверки.
- [ ] До вызовов зафиксировать ограничение leakage: смысловая ветвь не получает целевой термин через explanation другого seat, резюме или repair justification. Изменение доступного контекста само может ухудшить оценку игровых имён — измерить, не скрывать.
- [ ] Измерить обнаружение контролируемых ошибок, соблюдение механики после repair, обнаружение известного инцидента, вмешательства в неизменённые inputs, unresolved/conflict rates, число запросов и наблюдаемую стоимость. Повторить выбранные случаи для оценки нестабильности; обеим группам дать одинаковый budget.
- [ ] Не вычислять общую precision/recall или «процент правильного вьетнамского» без независимых меток. Семантическое качество ремонта неизвестных строк остаётся неизвестным. Agreement и back-translation публиковать только как дополнительные наблюдения.
- [ ] Если проверяемые gates не улучшены или есть регрессия на установленных случаях, действующие prompts сохранить и описать отрицательный результат. При ограниченном улучшении запросить отдельное разрешение rollout с явно оставшимся риском; обновить prompt/schema/cache identity, чтобы old evidence не считалось новой проверкой.

**Проверка:** локально прогнать подготовку/механические контрольные cases без LLM; после разрешения выполнить оба реальных плеча, сохранить входные версии, полные счётчики/ошибки и воспроизводимую команду. Stub-тесты не доказывают семантическое улучшение. Результат допустимо завершить отрицательным выводом; это не оставляет API-задачи недоделанными.

## Задача 10. Документация, API UX и сквозная приёмка

**Результат:** новый контракт документирован и проверен end-to-end; никакая кнопка или старый endpoint не обходят новый guard.

**Зависимости:** 1, 3–8; промпты допускаются только по решению 9. Восстановление production из задачи 2 отдельно.

**Файлы:** `docs/api.rst`, `docs/specs/openapi.yaml`, `docs/product/guides/producer-guide-weblate.md`, `docs/changes.rst`, `docs/security/threat-model.rst`; существующие `weblate/trans/views/edit.py`, `weblate/trans/views/judge.py`, `weblate/templates/snippets/judge-verdict.html` и report consumers, найденные через references. Полный новый frontend не является скрытым условием API-плана; существующие поверхности должны оставаться согласованными с общими правилами.

**Действия:**

- [ ] Документировать preview/start/poll/results/cancel/retry/clarify/apply/undo и HTTP error codes, сроки действительности снимка/хранения receipts, server ceilings и идемпотентность. Значения брать из утверждённой при реализации конфигурации, не обещать бесконечное хранение.
- [ ] Показывать «Судьи не обнаружили ошибок», а не «AI-approved»; cached/fresh/stale/unparsed и partial execution отдельно. Русское объяснение и diff помогают принять решение, но не превращают пользователя в проверяющего вьетнамский.
- [ ] Объяснить платность start/force/continuation/retry и предел cancel, отсутствие платного вызова у apply/clarify/undo. Не показывать некалиброванные проценты уверенности.
- [ ] Обновить threat model для нового async API, mutable context, candidate evidence, idempotency/retention и проверки до apply вместо после. Scope-bound tokens/CSRF для session клиентов, private project access, отсутствие credentials/raw provider payload в producer report; проверить входы модели как недоверенные данные.
- [ ] Совместить текущий UI candidate acceptance с проверкой до apply: не оставлять ссылку, которая записывает непроверенный текст, или обещание автоматического post-apply recheck. Если затронута визуальная поверхность, соблюдать `ACCESSIBILITY.md` и `docs/contributing/frontend.rst`; отдельный большой redesign не добавлять.
- [ ] После runtime smoke выполнить scoped lint/format, обновить актуальный changelog и owning docs. Commit/push не означают deployment. Миграции и worker restart требуют отдельного разрешения.

**Сквозная приёмка:** локальная fixture содержит обычную строку, approved, glossary conflict, cached evidence и provider failure. Настоящий API/worker со stub выполняет preview → start → poll → results; клиент закрывается и возвращается; cancellation/retry сохраняют готовые результаты. До apply target/state неизменны; ordinary bulk, individual approved/conflict apply, clarification/continuation, drift и undo дают ожидаемые per-row результаты. Проверить также обращения через старый suggestion API. Для изменённого UI подтвердить реальную страницу и keyboard interaction; если браузер недоступен, явно сообщить и выполнить реальный API smoke, не называть его визуальной проверкой.

Команды существующих наборов (тестовая БД, без пересоздания shared stack):

```sh
./rundev.sh test -n 0 weblate/api/tests.py -k 'autotranslate or judge'
./rundev.sh test -n 0 weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_persistence.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_deferrals.py
```

Новые API-тесты должны соответствовать selector либо запускаться по явным node IDs. Добавить затронутые task/suggestion/context consumers после LSP references, не считать приведённую команду полным покрытием всех изменений. Полезные регрессии защищают права, границы, состояния и гонки; не удерживать tests, привязанные только к wording/wiring. OpenAPI проверить штатным генератором/проверками репозитория, а не только визуальным чтением YAML.

## Порядок, разрешения и готовность

Порядок реализации: задача 1 независимо; затем 3 → 4 → 5 → 6 → 7/8 → 10. Семантическая линия 9 может исследоваться независимо от API после утверждения экспериментального scope. Задача 2 не является автоматическим последствием остальных. Общие файлы `models/judge.py`, `judge_loop.py`, `autotranslate.py` меняются последовательно одним integration owner; параллельные исполнители допустимы только после фиксации контрактов и разделения ownership.

Согласованы продуктовые правила из таблицы, включая отсутствие лингвиста. Предложенные HTTP names, persistence layout, сроки хранения и численные server limits требуют технической фиксации до соответствующей реализации; существующие guards нельзя ослаблять молча. Перед большим изменением провести архитектурный review этого плана против текущего кода и roadmap. План не означает, что новая API-семья уже реализована или что семантическая гипотеза подтверждена.

**Доказано локальной пробой ранее:** исходный REST создаёт orphan evidence; существующий batch закрывает именно этот дефект в выполненных сценариях; approved-state может понизиться после pass в обоих путях. **Подтверждено чтением кода при расширении:** native candidates уже есть, перепроверка сейчас после записи, обычное suggestion acceptance использует общий guard. **Не доказано:** общее качество судей/ремонта, эффективность разделения обязанностей, безопасность ещё не реализованного async цикла.

**В этой редакции выполнено только обновление плана.** Нет product-правок, новых результатов платного эксперимента, восстановления production или deployment. Следующее разрешение — реализация согласованного технического scope; платные модели и производственные операции согласуются отдельно.
