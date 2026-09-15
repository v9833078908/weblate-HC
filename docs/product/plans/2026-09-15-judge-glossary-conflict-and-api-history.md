# Judge: проверка, предложения исправлений и управляемый API

**Дата:** 2026-09-15.
**Статус:** задача 1 реализована, смерджена и развёрнута; каркас `/api/producer/` смонтирован, и §5 роадмапа дополнен ресурсами задач 6–8, поэтому задачи 3–8 и 10 готовы к реализации (`todo`, блокеров нет); задачи 2 и 9 требуют собственных разрешений. Платные эксперименты, production-записи и deployment согласуются отдельно.
**Основание:** расследование Pirate Ships / Vietnamese, локальная REST-проба и согласованный сценарий «предпросмотр → запуск → предложение → перепроверка → diff → подтверждение».
**Проверенная основа:** локальная проба product-кода `dadab98e1ceb1eb51112386e4fe74e27b64ac2a5`; первоначальный план — `40c950b`, исследовательская редакция — `360e06d`. Реализация задачи 1 — `7a24a3ac`, слияние `bac40ad8`, уточнение документации `1901547f`. Архитектурный review этой редакции против текущего кода и роадмапа выполнен 2026-09-15: вердикт `approve-with-changes`, пункты B1–B7 и G1–G8 закреплены в тексте.
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

## Отношение к роадмапу консоли

**Решение B1 (2026-09-15, пользователь):** `docs/product/vision/producer-console-design-and-roadmap.md` — источник истины API-контракта. Этот план не создаёт второе семейство эндпоинтов и не вводит свою конвенцию идемпотентности; его требования безопасности перемонтированы на §5 роадмапа, а недостающие там ресурсы внесены в §5 той же правкой документации. Роадмап остаётся источником решений: план не заменяет его.

После реализации задач 3–8 и 10 объём работ роадмапа меняется так:

| Пункт роадмапа | Что меняется |
|---|---|
| §5 «Контракт API (закрытый список)» | **Внесено 2026-09-15:** `runs/{id}/cancel/`, `decisions/{unit}/apply-candidate/`, `projects/{slug}/decisions/apply/`, `decisions/{unit}/clarification/`, `judge-applications/{id}/undo/`, значения `cancel_requested/partial/cancelled` у `Run.status` и определение `revision` |
| 0.2 «Namespace `/api/producer/`» | **Каркас реализован:** пакет `weblate/api/producer/`, `GET me/` и `GET projects/`, тесты `ProducerAPITest`, схема регенерирована. 0.2 сокращена до `POST projects/` и `projects/{slug}/` с `contents`/`languages` |
| 1.4 (поля и adoption `ProducerRun`) | Обобщение adoption, статусы и durable dispatch выполняет задача 5a — 1.4 сокращается до стадий `localize` и своей части черновика; владелец общий |
| 2.1 «Очередь "Требуют решения"» | Объём сохраняется, но judge-сторона приходит готовой: per-row улики, свежесть и eligibility пишет задача 5a; очередь их читает |
| 2.2 «repair, accept, back-translation» | Одноюнитный `decisions/{unit}/repair/` остаётся за 2.2, но обязан идти через proposal-only движок задачи 3 и общий primitive применения задачи 6. Задача 6 даёт accept и `apply-candidate`, задача 7 — уточнение (новый пункт). У 2.2 остаются сам repair, back-translation и экран консоли |
| 2.3 «Судья из консоли» | Оценка (`estimate_id`/`scope_hash`) и асинхронный прогон с `ProducerRun`, отменой и частичным результатом реализуются задачами 4, 5a и 5b; у 2.3 остаётся `RunCard.tsx` |
| §4.3 «Данные» | `ProducerRun` получает ledger отправки и новые статусы, `JudgeVerdict` — `subject`/`candidate_target_hash`, `JudgeRunUnit` — исход `PENDING` |
| DoD волны 2 | D2-2 и D2-3 частично доказываются тестами этого плана; D2-1, D2-4, D2-5, D2-6 не затрагиваются |

Уточняющего цикла продюсера (задача 7) в роадмапе не было вовсе — он вносится как пункт волны 2, иначе его хранилище пересечётся с зоной профиля `_producer` (§4.3).

## Задача 1. Полная история штатного REST-запуска

**Результат:** разрешённый REST judge-run имеет существующую историю с actor, scope, итогом и участием строк; свежие attempts связаны с run. Синхронность, response shape и существующие правила перевода не меняются.

**Состояние:** выполнено 2026-09-15. **Зависимость:** отсутствует; лингвист, production и платный provider не требовались.

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

- [x] До изменения action добавлена целевая REST-регрессия: translated unit, узкий `q`, review-enabled project, разрешённый пользователь, два внешних mock-ответа и реальные ORM-записи. Красное состояние подтверждено на исходном коде (`ProducerRun.DoesNotExist`), зелёное — после правки.
- [x] Constructor переключён на существующий batch с translation scope (`weblate/api/views.py:3702`); lifecycle во view не переносился, соседний TM-путь (`:2861`) и исторические orphan records не тронуты.
- [x] Проверены свежая и cached история, реальные связи attempts/verdicts, cap-skipped участие и failed run при отказе provider; старое evidence к новому cached run не перепривязывается.
- [x] Проверены границы: нет review permission; locked component; malformed form; ID чужого проекта; необработанное исключение после создания run (run переходит в FAILED, а не остаётся RUNNING). Класс `TranslationJudgeAutotranslateAPITest` использует `APITransactionTestCase`, потому что seats обращаются к HTTP из отдельных потоков со своими соединениями.
- [x] Существующий `test_autotranslate_restrict_direct_editing` усилен проверкой наблюдаемого эффекта и реального `ProducerRun`, а не вызова `perform`.
- [x] Runtime smoke: настоящий POST на fixture-translation со stub provider, затем страница `judge-run` уполномоченным пользователем с фильтром `?outcome=passed` — строка и её исход отображаются.
- [x] Обновлены `docs/api.rst`, `docs/changes.rst` и раздел ограничений судьи в `docs/product/guides/producer-guide-weblate.md`. Ложное утверждение о CLI-паритете исправлено: команда `weblate auto_translate` режима `judge` не имеет вовсе.

**Проверка (выполнена):** регрессия падала на исходном action и проходит на batch. Полный гейт на изолированном Postgres — `weblate/api/tests.py` и `weblate/trans/tests/test_judge_autotranslate.py`: 650 passed, одно предсуществующее падение `SuggestionAPITest::test_accept_judge_candidate_holds_fuzzy_and_queues_recheck`, воспроизведённое на предшествующем коммите в той же среде и относящееся к задаче 3. Развёрнуто: image revision `bac40ad8`, healthy, страница входа `200`.

```sh
./rundev.sh test -n 0 weblate/api/tests.py -k autotranslate
./rundev.sh test -n 0 weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py
```

**Остаточное ограничение (S6).** Для REST-пути `_perform` выбирает `_adopt_producer_run()` (`weblate/trans/autotranslate.py:1506`), а тот при `producer_run_id is None` — это и есть REST — создаёт новый run через `_create_producer_run()` (`:1303-1304` → `:1348`) со статусом RUNNING (`:1383`) до первого исходящего вызова; завершается run в `perform` (`:1463`). Перехваченное исключение внутри прогона переводит run в FAILED — это проверено тестом. **Вывод, не наблюдение:** гибель самого процесса web-воркера (SIGKILL, таймаут) финализатор не выполнит, и run останется RUNNING. Единственный автоматический перевод QUEUED/RUNNING → FAILED найден внутри Celery-задачи (`weblate/trans/tasks.py:1060-1064`) и для синхронного web-пути не исполняется; периодического реапера просроченных прогонов в `tasks.py` нет. Раньше run не создавался вообще, поэтому дефект был ненаблюдаем. Менять задачу 1 не нужно: лечение — durable ledger и resume задачи 5a.

Wire-схема задачей 1 не менялась. Расширенный API требует регенерации `docs/specs/openapi.yaml` и обновления `docs/security/threat-model.rst` в задаче 10.

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
| Оценка до оплаты | Количество выбранных, approved, исключённых, cached и требующих новых вызовов; запуск только по `estimate_id` той же scope | 4 |
| Прямой API-запуск | Автоматизированный клиент может не показывать оценку человеку, но обязан получить `estimate_id` и указать scope; сервер выполняет тот же допуск | 4, 5 |
| Кэш и повтор | По умолчанию только актуальное evidence; force явно оплачивается. Изменение входа, контекста или профиля делает старую оценку непригодной | 3, 4 |
| Один цикл | Одна генерация исправления и одна перепроверка; неудача не запускает новый семантический цикл автоматически | 3 |
| Консервативный apply | Все применимые механические gates и обе смысловые оценки кандидата должны пройти; расхождение, unparsed или сбой блокируют apply | 3, 6 |
| Массовое применение | Только выбранные обычные предложения. Approved и glossary conflicts подтверждаются по одной строке | 6 |
| Частичный результат | Готовые результаты сохраняются; технически неуспешные строки можно повторить без повторной обработки успешных | 5 |
| Фоновая работа | `Run` со `status: queued`, чтение по `runs/{id}/` и `decisions/?run=`; закрытие страницы не прекращает запуск | 5 |
| Права | Существующие project/object permissions, повторная проверка при записи; никаких новых ролей или обхода текущих более строгих candidate guards | 4–8 |
| Отмена | Прекратить новые HTTP-запросы, сохранить ответы уже отправленных; не обещать возврат их стоимости | 5 |
| Лимит | Не обрезать scope молча; превышение блокирует admission до явного изменения выбора/лимита | 4 |
| Совместимость | Старый sync `autotranslate` остаётся синхронным и не меняет контракт; новая асинхронная поверхность живёт в `/api/producer/`, engine/lifecycle общие | 1, 5 |
| Языки | API поддерживает существующие настроенные языки; исследование сначала только `ru→vi` | 9 |
| Нет лингвиста | Никаких вымышленных human labels, процентов уверенности и «AI-approved»; продюсер подтверждает действие, не иностранный язык | 9, 10 |
| Уточнение игры | Вопрос на русском, варианты плюс свой ответ/«не знаю»; уточнение не применяет target и не меняет glossary | 7 |
| Контекст строки | Ответ видим, редактируем и используется далее только для этой строки; старое evidence становится stale | 7 |
| Продолжение после ответа | Явный новый ограниченный запуск с платным предупреждением, а не скрытый retry; не знать ответ допустимо | 7 |
| Конкурентные изменения | Stale proposal не перезаписывает новую работу; bulk возвращает отдельный исход каждой строки | 6 |
| Повтор запроса | Не дублирует запуск, применение и audit-события; идемпотентность — `revision`/`estimate_id`/`scope_hash`, иной payload под тем же `estimate_id` — `409` | 5, 6 |
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

### HTTP-контракт: ресурсы `/api/producer/`

**Решение B1 (2026-09-15, пользователь):** источник истины контракта — `docs/product/vision/producer-console-design-and-roadmap.md` §5 «Контракт API (закрытый список)». Этот план не заводит собственное семейство `/api/judge-runs/` и не вводит header `Idempotency-Key` (такого header в `weblate/api` и `weblate/trans` нет нигде). Идемпотентность — `revision`, `estimate_id` и `scope_hash` плюс `409` на устаревшую ревизию. Ресурсы, которых в §5 не было, внесены в §5 той же датой, поэтому закрытый список остался один: ниже — карта операций плана на этот контракт, а не второй контракт.

Namespace смонтирован: `weblate/api/producer/` подключён в `weblate/api/urls.py` и отвечает `me/` и `projects/` (тесты — `ProducerAPITest` в `weblate/api/tests.py`). Задачам 4–8 остаётся добавлять в него свои ресурсы, а не создавать каркас; `capabilities` расширяется только вместе с реальной функцией. Владелец роадмапа продолжает 0.2 с `POST projects/` и `projects/{slug}/` — при параллельной работе это один и тот же пакет, поэтому новые модули лучше добавлять отдельными файлами, а не переписывать существующие.

| Операция плана | Ресурс `/api/producer/` | Статус в §5 роадмапа |
|---|---|---|
| Оценка scope и допуск | `POST projects/{slug}/runs/estimate/ {kind: judge, scope}` | существует (`:528-529`); ответ дополняется counts/exclusions/ceilings |
| Запуск проверки | `POST projects/{slug}/runs/ {kind: judge, scope, estimate_id}` | существует (`:530`) |
| Состояние прогона | `GET runs/{id}/` | существует (`:522-524`); нужны новые значения `status` |
| Результаты по строкам | `GET projects/{slug}/decisions/?kind=judge&run=` | существует (`:532-535`); питает очередь 2.1, отдельного `results/` не вводится |
| Возобновление после редоставки | `POST runs/{id}/resume/ {attempt}` | существует (`:527`) |
| Отмена | `POST runs/{id}/cancel/` | внесено в §5 (`:528`) |
| Ремонт одной строки | `POST decisions/{unit}/repair/ {revision, finding_ids[]}` | существует (`:536`) |
| Принятие находки как есть | `POST decisions/{unit}/accept/ {revision, finding_id, reason}` | существует (`:537`) |
| Применение проверенного кандидата | `POST decisions/{unit}/apply-candidate/ {revision, candidate_id, acknowledge{}}` | внесено в §5 (`:541-543`) |
| Пакетное применение | `POST projects/{slug}/decisions/apply/ {items[]}` | внесено в §5 (`:544-546`) |
| Уточнение смысла строки | `GET`/`PATCH decisions/{unit}/clarification/` | внесено в §5 (`:547-548`) |
| Откат применения | `POST judge-applications/{id}/undo/` | внесено в §5 (`:549`) |

Continuation после уточнения — новый `POST projects/{slug}/runs/` с родительским run, своей оценкой и новым снимком; отдельного engine/regenerate-эндпоинта нет. Read-операции и оценка не создают платную работу.

Коды ошибок — по шапке §5: `400` невалидный payload, `403` нет прав, `404` недоступный объект, `423` блокировка, `409` конфликт ревизии (устаревшие snapshot/`estimate_id`, превышенный допуск). Валидное пакетное применение — `200` с отдельными исходами `applied/already_applied/stale/forbidden/needs_individual_confirmation/not_verified`; частичная запись никогда не отображается как «всё применено».

**Определение `revision` (B7).** Одно на продукт, общее с `Decision.revision` роадмапа (`:533`): непрозрачный серверный токен из `(unit.pk, unit.last_updated, максимальный Change.pk этой строки)`. Новое поле и миграция не нужны: `Unit.last_updated` — `auto_now` (`weblate/trans/models/unit.py:669`) и растёт на каждом сохранении, включая propagation, а pk `Change` монотонны, причём `ACCEPT`/`CHANGE` хранят `old` и `details.old_state` (`weblate/trans/actions.py:697-701`, `weblate/trans/models/unit.py:2125`). Клиент токен не разбирает и не конструирует. Возврат идентичного текста создаёт новую `Change`, поэтому «изменили и вернули обратно» ловится: сравнение токенов под `select_for_update` даёт `409`. Лишний `409` из-за несмыслового сохранения допустим; молчаливая перезапись — нет.

Статусы прогона: сегодня `queued/running/completed/failed` (`weblate/trans/models/judge.py:310-314`) плюс `no-update` в ответе роадмапа; добавляются `cancel_requested`, `partial`, `cancelled`. `completed` означает законченную обработку, не правильные переводы. Ошибка provider и смысловая проблема — разные поля. Исход строки различает checked/no issues, proposal ready, needs clarification, unresolved, technical failure, skipped/stale; применение и откат — отдельная история, а не переписывание исходного evidence.

Идентификатор run не является секретом доступа. Каждое чтение и каждая команда заново фильтруют scope и проверяют действующие права, включая project tokens, и переиспользуют существующий `user_can_view_producer_run` (`weblate/trans/views/judge.py:358-367`). Сохранить текущий более строгий guard judge-кандидата (`unit.review` и `translation.auto`) и проверять штатное право редактирования; не ослаблять legacy acceptance ради новой таблицы ролей. Approved требует review и подтверждения потери approval. Уточнение контекста требует существующего права изменения соответствующего контекста.

## Задача 3. Proposal-only проверка и защита состояний

**Результат:** новый цикл не изменяет живой перевод до apply, включая approved, pretranslation, state projection и deterministic `max-length`. Уже существующие candidates используются, но проверяются до записи.

**Зависимости:** согласование реализации; не требует реальных LLM. Задача 1 выполнена и эту задачу не блокирует. Схемные решения B2/B3/B6 ниже закреплены и не переоткрываются исполнителем.

**Файлы:** `weblate/trans/autotranslate.py`, `weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`, `weblate/trans/models/suggestion.py`, `weblate/trans/tasks.py`; одна аддитивная миграция в `weblate/trans/migrations/` с номером от актуального graph (`JudgeVerdict.subject`, `JudgeVerdict.candidate_target_hash`).

**Действия:**

- [ ] **Сначала approved-политика (B6).** Закрепить регрессией `approved 30 → 20` и исправить проекцию: в mutating-режимах `state_for_verdict` (`weblate/trans/models/judge.py:894`) и запись по `locked.state != state` в `process_judge` (`weblate/trans/autotranslate.py:772`) никогда не понижают approved на `PASS`/`FLAG`. На `REJECT` approved сохраняет `30`, а замечание остаётся видимым как advisory-улика: вероятностный вердикт не блокирует релиз (`docs/product/vision/llm-first-product-architecture.md`, инвариант 2). Понижение до `20` возможно только через явное подтверждённое apply задачи 6 при действующем `unit.review`. Исторические records не переписывать. Пинящий тест уже есть — `weblate/api/tests.py::TranslationJudgeAutotranslateAPITest::test_judge_keeps_approved_target_but_lowers_state_pre_task3`: он фиксирует текущий дефект и переписывается на новую политику этой задачей.
- [ ] Ввести явную политику proposal-only для нового run; все ветви, включая MT/pretranslation, max-length и post-judge projection, сохраняют target/state. Не объявлять весь legacy `autotranslate` read-only: его переводные режимы сохраняют назначение.
- [ ] **Дискриминатор subject (B2).** Добавить `JudgeVerdict.subject` (`live|candidate`) и `candidate_target_hash` одной аддитивной миграцией. `_write_verdict` (`weblate/trans/judge_loop.py:295`) уже берёт hash/identity из `JudgeRequest`, поэтому кандидат оценивается без подмены `Unit.target`. Пока строка имеет `subject=candidate`, её обязан исключать каждый читатель живого текста — `current_round` (`weblate/trans/models/judge.py:940`), `_seat_round_rows` (`:973`), `active_round` (`:1016`), `latest_round` (`:922`), `current_verdict` (`:1259`), `judge_status_annotations` (`:1171`), `describe_latest_verdict` (`:1435`), `repair_evidence` (`:1111`). Добавить отдельный `candidate_round(unit, candidate)`. Candidate-строки сохраняют `attempt=0` и получают свежий `request_round` из `_allocate_request_round` (`judge_loop.py:1019`), иначе ломаются unique constraint раунда и места в `JudgeVerdict.Meta` (`models/judge.py:824`) и временное окно `repair_evidence`. Ссылки здесь — на имена символов: номера строк проверены на `main` 2026-09-15 и могут смещаться.
- [ ] **Переход кандидата в живую улику при apply (B2, разрешение противоречия).** Исключение candidate-строк из живых читателей и повторное использование их же после записи — не противоречие только при явном переходе. В той же транзакции, что пишет target (под `select_for_update`), строки кандидата с `candidate_target_hash`, равным hash записанного текста, и с совпадающим контекстом строки повышаются до `subject=live`; `attempt`/`request_round` сохраняются, поэтому unique constraint остаётся верным, а копий не создаётся — ровно одна строка на место и раунд. Не совпавшие строки остаются `candidate`, а apply возвращает `not_verified`/`stale` и ничего не пишет. До apply кандидат живым читателям не виден; после — `_cached_verdict` (`judge_loop.py:416`) находит эти строки как обычную актуальную улику по `request_identity`, и именно поэтому apply не делает нового платного recheck. Без этого перехода одно из двух требований обязательно нарушится, поэтому он входит в приёмку задачи 3, а не в задачу 6.
- [ ] **Deferral для кандидата (B2).** `_persist_verdict_batches` (`judge_loop.py:1034`) вызывает `_sync_deferral` (`:929`) после каждого вердикта, а `_select_drain_requests` (`:1760`) пересобирает request из живой строки и закрывает deferral с несовпавшей identity: candidate-seat молча закроется и кандидат останется непроверенным. Либо пропускать `_sync_deferral` для candidate-subject, либо дать `JudgeDeferral` тот же subject и собирать request из сохранённого кандидата.
- [ ] **Идентичность кандидата (B3).** Идентичность для API — строка `JudgeRunUnit` (unique `(run, unit_id_snapshot)`, `weblate/trans/models/judge.py:667-669`) с `candidate_target_hash`, id проверочных вердиктов и eligibility; pk `Suggestion` — изменяемый указатель, не идентичность. `userdetails` остаётся схемой 1: `JudgeCandidateMetadata` (`:123`) отвергает любой набор ключей, кроме закрытого `_CANDIDATE_METADATA_FIELDS` (`:111`, проверка на `:143`), поэтому новое состояние живёт на judge-таблицах. `_store_candidate` (`judge_loop.py:548`) заменяет только кандидатов своего run/verdict lineage — сегодня `SuggestionManager.add` удаляет все judge-кандидаты строки без receipt (`weblate/trans/models/suggestion.py:134-139`). Ночной `cleanup_suggestions` может удалить кандидата между чтением результатов и apply: apply/undo сравнивают `candidate_target_hash` и revision и отвечают `stale`, а не 404/500.
- [ ] Генерация по конкретным найденным issues → применимые механические gates → обе оценки кандидата. Не записывать кандидата в Unit ради вызова существующего evaluator.
- [ ] Ограничить семантический цикл одной генерацией и одной перепроверкой; исчерпанный лимит/расхождение/новая ошибка дают unresolved. Транспортные retries остаются ограниченными и входят в отдельный request ceiling, а не создают новые semantic attempts.
- [ ] Кэшировать только полное актуальное evidence с профильной identity; cached новый run получает своё участие без переноса старых attempts. Force bypass явный. Неподдерживаемый repair language — явный исход, не fallback на другой движок без контракта.

**Проверка:** исходная approved-regression красная до исправления, зелёная после. Через настоящий ORM со stub HTTP проверить pass/reject/unparsed, cache/force, конфликт и max-length: target/state/Change не меняются при проверке; verified candidate содержит оценку именно предложенного target. Попытка перепроверки не заменяет current evidence живой строки. Использовать `weblate/trans/tests/test_judge_loop.py`, `test_judge_autotranslate.py`, `test_judge_persistence.py`, `test_judge_views.py`; поведенческие тесты старого apply-after-write мигрировать на новый порядок, а не сохранять обход.

## Задача 4. Cache-aware preview и точный допуск

**Результат:** пользователь знает scope до оплаты; работа не обрезается молча и не расширяется между preview и worker.

**Зависимость:** задача 3 (движок и subject) и задача 5a (модель run, snapshot-строки, request key) — preview хранит снимок как строки предсозданного run, а не как подписанный stateless token (G2). **Файлы:** существующий пакет `weblate/api/producer/` (добавлять свои модули, не переписывать `me/`/`projects/` владельца консоли), `weblate/api/tests.py`; `weblate/trans/autotranslate.py` (`preview_judge_scope`, `judge_initial_request_count`), `models/judge.py`, `forms.py`. После правки схемы — `make -C docs update-openapi`.

**Действия:**

- [ ] **Маршрут — роадмап.** Оценка публикуется как `POST /api/producer/projects/{slug}/runs/estimate/ {kind: judge, scope}` → `{estimate_id, scope_hash, strings, cost_usd_min?, cost_usd_max?, minutes_min?, minutes_max?, basis}` (`docs/product/vision/producer-console-design-and-roadmap.md:528-529`). Счётчики этого плана (selected/approved/excluded/cached/new-call, причины исключений, фазы, hard limits) добавляются к тому же ресурсу, отдельного `preview/` эндпоинта не создаётся. Одна scope даёт ту же оценку, что HTML-путь (роадмап D2-3, `:1247-1249`).
- [ ] Повторно использовать selection/permissions текущего flow, но отделить подсчёт полного scope от legacy `units[:limit]`. Сохранить closed ordered IDs и per-row snapshots. Preview не генерирует кандидаты и не вызывает LLM.
- [ ] **Арифметика допуска (G3).** `judge_initial_request_count` и `worst_case_calls = initial × (JUDGE_MAX_REPAIR_ATTEMPTS + 1)` (`weblate/trans/autotranslate.py:758-767`) считают только раунды живого текста. Резерв допущенной строки обязан включать repair-MT и проверку кандидата. `RetryBudget` (`judge_loop.py:1346-1352`) действует на вызов `run_judge_batch`, не на run: в ответе указывать, какой именно ceiling показан.
- [ ] **Стоимость выросла осознанно (S1).** Слитый план триажа откладывал второй раунд до принятия — «Producer keeps current text: 3 total; applies candidate: 5» (`docs/product/plans/2026-09-01-judge-producer-triage-embed.md:95-107`). Здесь кандидат проверяется до решения продюсера, поэтому худший случай помеченной строки — 5 вызовов независимо от применения. Записать это в основание оценки; допустим необязательный knob «проверять выбранные строки по требованию».
- [ ] При превышении отказаться от запуска, не выбирать первые N. Повышение пользовательского лимита не обходит серверный максимум. Не обещать точную денежную стоимость без надёжного тарифа; показывать, что это лимит работы, не валютный бюджет. Отсутствие тарифа — «оценка недоступна», не `$0` (роадмап `:568-570`).
- [ ] Между оценкой и запуском сверить actor/scope/config revision и `scope_hash`. Drift — `409` с конкретным кодом и требованием новой оценки; при прямом запуске те же проверки атомарно. На execution drift не оплачивать повторно без нового допуска.

**Проверка:** permission-filtered counts, foreign project/language IDs, locked component, malformed query, mixed cached/fresh, нулевой cap с all-cached и с fresh scope, превышение, drift и прямой start. Ни одного provider call до успешного допуска. Реальный REST smoke на локальном fixture с несколькими строками и точной сверкой preview/результата.

## Задача 5. Durable async run, частичные результаты и отмена

**Результат:** принятый запуск переживает закрытие клиента, результаты читаются по ID, явный повтор не дублирует работу.

**Зависимость:** 3. Задача делится на **5a** (модель и допуск) и **5b** (worker, отмена, повтор): 4 зависит от 5a, поэтому 5a идёт раньше preview. **Файлы:** `weblate/api/producer/`, `weblate/api/tests.py`; `weblate/trans/models/judge.py`, `autotranslate.py`, `tasks.py`, `judge_loop.py`, `weblate/utils/celery.py`; миграции. Сначала LSP `references` для изменяемых exported symbols и всех потребителей статусов run.

**Действия:**

- [ ] **5a. Статусы и их потребители (S4).** Добавить `cancel_requested/partial/cancelled` в `ProducerRun.Status` (`weblate/trans/models/judge.py:310`) и обновить каждого потребителя: `RUN_KIND_LABELS`, `JUDGE_MODES`/`HISTORY_MODES` и `user_can_view_producer_run` (`weblate/trans/views/judge.py:359`), `weblate/templates/snippets/producer-run.html`, `producer-runs-menu.html`, а также терминальный guard `_finish_producer_run` (`weblate/trans/autotranslate.py:1420`), который сейчас считает перезаписываемым всё, кроме COMPLETED/FAILED. Полный список потребителей брать через LSP `references`, а не из этого перечня.
- [ ] **5a. Durable dispatch — назвать существующий паттерн (B5).** Единственный проверенный ledger в репозитории — лок-китовый: `LocKitImportDraft.dispatch_task_id/dispatch_phase/dispatch_requested_at/dispatch_published_at/dispatch_attempts/dispatch_error` (`weblate/trans/models/loc_kit.py:117-136`, миграция `0127_loc_kit_dispatch_ledger.py`), диспетчер `_publish_loc_kit_dispatch` (`weblate/trans/tasks.py:1696-1786`), запись итога (`:1788-1851`) и периодический `drain_loc_kit_dispatches` (`:2588-2611`, регистрация `:2789-2791`). Перенести те же шесть полей (или общий abstract mixin) на `ProducerRun`, добавить `drain_producer_run_dispatches` и зафиксировать fence: worker забирает run под `select_for_update` по `(pk, task_id)`, per-row upsert идемпотентен по `(run, unit_id_snapshot)`, платный вызов не делается для строк с полной живой/кандидатной уликой. Безопасность дублирующей доставки в лок-ките держится именно на fencing-токене каждой порции — без per-row fence «safe duplicate» превращается в повторную оплату.
- [ ] **5a. Закрыть существующую дыру QUEUED-навсегда.** `queue_judge_recheck` публикует через голый `transaction.on_commit` и помечает FAILED только при исключении публикации (`weblate/trans/judge_loop.py:2077-2105`); крах между commit и callback оставляет run в QUEUED, после чего `active_recheck_run` (`:1979-1991`) подавляет все будущие перепроверки этой строки. Перевести этот путь на тот же ledger, а не оставлять третий механизм отправки.
- [ ] **5a. Допуск и claim.** Создавать и читать существующий `ProducerRun`; durable request key с unique constraint на actor/scope/operation и fingerprint payload. Одновременные одинаковые POST возвращают один run; иной payload под тем же ключом — `409`. `_adopt_producer_run` (`weblate/trans/autotranslate.py:1293-1345`) сегодня жёстко фильтрует `requested_mode="recheck"` и QUEUED (`:1316-1322`) — добавить ветвь resume для RUNNING по совпадающему `task_id`, зеркально лок-китовому `dispatch_task_id`. Права берутся у актуального actor, не из сохранённого флага допуска.
- [ ] **5a. Per-row durability (B4).** Заменить финальный цикл `JudgeRunUnit.update_or_create` в `process_judge` (`weblate/trans/autotranslate.py:772`) на per-row upsert внутри пути сохранения батча (`_persist_verdict_batches`, `judge_loop.py:1034`): строка = пара вердиктов + кандидат + проверка + eligibility. Сейчас на каждый батч сохраняются только `JudgeVerdict`, а участие пишется в конце, поэтому редоставка задачи с `acks_late`/`reject_on_worker_lost` (`weblate/trans/tasks.py`, декоратор `auto_translate`) либо теряет частичный результат, либо платит заново. Добавить значение `PENDING` в `JudgeRunUnit.Outcome` (`models/judge.py:578`; сейчас `outcome` обязателен и такого значения нет) — это же значение хранит snapshot preview из задачи 4 — либо назвать альтернативное хранилище снимка.
- [ ] **5b. Worker и прогресс.** Развести progress/liveness и качество; готовые rows фиксируются до перехода к следующим; необработанное исключение даёт наблюдаемый terminal/recoverable исход, не вечный RUNNING. Phase/heartbeat читать из того же liveness-record, что показывает `/api/tasks/{id}/` (`weblate/api/views.py:5027-5040`, `weblate/utils/celery.py:170-206`); новое имя задачи, если оно появится, добавить в `LIVENESS_TASKS` (`:103-109`). Run-центричный `GET runs/{id}/` остаётся: run живёт дольше задачи.
- [ ] **5b. Отмена (G4).** Нужен предикат отмены **до** исходящего вызова. Сегодня единственный per-batch шов — `on_batch`/`_persist_verdict_batches`, и он исполняется уже после HTTP; seats работают в потоках с барьером подтверждения того же `batch_index` (`judge_loop.py:1103-1252`). Протянуть предикат в `request_verdicts`/`_SeatJob` и проверять его в обеих seat-ветвях, в repair, retries и deferral-drain. Уже отправленный запрос может завершиться — его ответ сохраняется. Cancel не удаляет готовые предложения и не даёт drain обойти остановку.
- [ ] **5b. Повтор.** Retry технических сбоев создаёт связанный новый run с новым явным лимитом; semantic unresolved техническим сбоем не считается. Успешные rows/seat evidence повторно не оплачиваются при неизменном входе. Маршруты — `GET /api/producer/runs/{id}/`, `POST /api/producer/runs/{id}/resume/ {attempt}` (роадмап `:525-527`); отдельный `retry/` вводится только если resume семантически не покрывает повтор, и тогда добавляется в §5 роадмапа.

**Граница гарантии:** сервер обеспечивает идемпотентность клиентских команд и сохранённых результатов. Crash после принятия HTTP провайдером, но до сохранения ответа, без provider idempotency не позволяет гарантировать exactly-once billing. Такой outcome показывать как неизвестный, не скрывать за автоматическим платным повтором; отдельное подтверждение сообщает о возможной повторной оплате.

**Проверка:** локальный API → настоящий worker → stub provider → status/results; закрыть клиент и получить тот же run заново. Воспроизвести конкурентный POST, publish failure, redelivery, частичный HTTP failure, снятые права и отмену между запросами/до repair/при deferral. Счётчик stub подтверждает отсутствие новых вызовов после наблюдения cancel и отсутствие повторных успешных calls. Проверить существующие `weblate/trans/tests/test_judge_deferrals.py` и task/liveness tests, найденные через references; не тестировать только Mock forwarding.

## Задача 6. Подтверждение, bulk apply и защита от обходов

**Результат:** пользователь применяет только проверенный актуальный diff; массовая команда возвращает честные отдельные исходы.

**Зависимость:** 3–5. **Файлы:** `weblate/trans/judge_loop.py:accept_judge_candidate` (`:2108-2208`), `models/suggestion.py:Suggestion.accept` (`:282-288`), `models/judge.py`, `models/unit.py`, `weblate/trans/models/change.py`; `weblate/api/producer/`, `weblate/api/tests.py` и `weblate/trans/views/edit.py`.

**Действия:**

- [ ] **Единственный primitive apply (G5).** `accept_judge_candidate` (`weblate/trans/judge_loop.py:2108-2208`) становится единственной точкой записи кандидата: проверка обеих оценок, флаг подтверждения потери approval, флаг конфликта терминологии, receipt. Сегодня она пишет `STATE_TRANSLATED`, удаляет Suggestion и ставит платную перепроверку (`:2192-2205`) — постфактумный `queue_judge_recheck` убирается, а не остаётся вторым путём, иначе старая карточка UI продолжает платить за то, чего новый API не делает. Все существующие вызывающие обязаны идти через него: `Suggestion.accept` (`models/suggestion.py:282-288`), REST `weblate/api/views.py:4133-4165`, UI `weblate/trans/views/edit.py:1172-1187` и `:2156-2160`, bulk-задача (`weblate/trans/tasks.py:405-428`), автоприём по голосам (`models/suggestion.py:364-368`).
- [ ] Применять через штатный `Unit.translate`, без propagation на другие строки и без прямого SQL target update. Audit сохраняет actor, run/candidate/evidence, before/after plurals и state. Receipt сохраняется до удаления native suggestion.
- [ ] **Конфликт терминологии до задачи 9 (G6).** Структурированного признака нет: `JudgeVerdict.errors` — свободные `{severity, category, description}` (`weblate/trans/models/judge.py:874-880`). Промежуточное правило: `category == "terminology"` любой severity ⇒ строка выходит из обычного bulk и требует отдельного подтверждения. Задача 9 может заменить правило измеренным, но задача 6 выходит раньше и без правила выйти не может.
- [ ] Обычный bulk допускает только выбранные обычные кандидаты. Approved и конфликт терминологии подтверждаются по одной строке с явным acknowledgement; если строка стала approved после чтения результатов, обычный apply её не меняет. Применение approved переводит `30` в `20` и не выдаёт нового approval; остальные применённые исправления тоже не получают AI-approval. Неприменённые строки остаются в исходных state.
- [ ] Каждая строка — отдельная атомарная операция с повторным guard под `select_for_update`. Ошибка одной не откатывает уже применённые; durable receipts позволяют безопасно повторить прерванный batch без дублирования audit.
- [ ] **Маршруты и дополнение роадмапа.** Принятие находки как есть — существующий `POST /api/producer/decisions/{unit}/accept/ {revision, finding_id, reason}`, ремонт — `POST decisions/{unit}/repair/ {revision, finding_ids[]}` (роадмап `:536-537`). Записи проверенного кандидата в §5 роадмапа сейчас нет: добавить туда `POST decisions/{unit}/apply-candidate/ {revision, candidate_id, acknowledge{approval_loss?, terminology_conflict?}}` и пакетный `POST projects/{slug}/decisions/apply/ {items[]}` с per-row исходами. Контракт закрытый: дополнение вносится в роадмап до реализации, а не заводится параллельным семейством.

**Проверка:** two users edit/apply race, ABA edit, permission revoked, old candidate after new profile/context, bypass через ordinary suggestion API, approved/conflict в bulk, оба acknowledgement для совмещённого случая, partial commit и retry. Проверить реальные target/state, audit и file/VCS persistence, не только HTTP status. Строка с failed/unparsed candidate evaluation не применяется даже индивидуально. Ручное обычное редактирование остаётся доступно по своим правам, не маскируется под validated apply.

## Задача 7. Уточнение смысла без лингвиста

**Результат:** продюсер отвечает на предметный вопрос на русском, контекст сохраняется, затем явно запускается новая ограниченная попытка.

**Зависимость:** 3–5; экспериментальное обнаружение вопросов — 9. **Требует записи в роадмап:** уточняющего цикла нет ни в §5, ни в §9 роадмапа, а хранилище ответов пересекается с зоной профиля/`_producer` (§4.3) — внести его как пункт волны 2 (или частью 2.2) до реализации. **Файлы:** `weblate/trans/models/judge.py`, `judge_loop.py:build_request`, `weblate/trans/models/unit.py`, `weblate/machinery/llm.py`, `weblate/api/producer/`; миграция.

**Действия:**

- [ ] Возвращать вопрос, основание конфликта, контекст строки и варианты; всегда разрешать свой ответ/«не знаю». Модель не выбирает ответ за продюсера и не выдаёт вариант за установленный факт.
- [ ] **Где живёт уточнение (G1).** Закрепить: новое поле/модель на конкретной target-unit, одно расширение `compute_context_hash` (`weblate/trans/models/judge.py:218-240`, вызывается из ≥10 мест) и явное включение в оба сборщика промпта — `build_request` (`weblate/trans/judge_loop.py:111-138`, читает сейчас только `source_unit.explanation` и `note`) и MT (`weblate/machinery/llm.py:562-572`, берёт explanation целевой unit только когда исходный пуст, то есть для инцидента Pirate Ships уточнение молча не дошло бы). Не писать в `source_unit.explanation`: `Unit.update_explanation` распространяет его на все языковые units и пишет PendingUnitChange (`weblate/trans/models/unit.py:2879-2925`).
- [ ] Сохранить actor/version; уточнение принадлежит одной target unit и не затирает Explanation/Character/flags. Ответ доступен для чтения и правки через `/api/producer/decisions/{unit}/clarification/` и входит в контекст последующих переводов и обеих проверок; `hash`/cache identity учитывает его revision. Использование не ограничено текущим run.
- [ ] Clarification write не вызывает LLM, не применяет кандидат и не меняет target/state/glossary. «Не знаю» оставляет unresolved; ответ с новым смыслом инвалидирует старое предложение.
- [ ] Новая попытка после ответа — отдельное подтверждённое admission с родительской связью и лимитом 1 repair + 1 recheck. Старый исчерпанный semantic cycle не запрещает эту новую авторизованную работу.

**Проверка:** русский ответ доступен в следующем MT и judge request на stub, соседняя строка/язык и существующее Explanation неизменны, old evidence stale; duplicate clarification не создаёт лишние версии. Изменение ответа другим пользователем блокирует старый apply. Нет outbound call до явного continuation.

## Задача 8. Безопасный отменяющий patch

**Результат:** пользователь отменяет конкретное применённое исправление, не теряя последующую работу.

**Зависимость:** receipts/revisions задачи 6. **Файлы:** `weblate/trans/models/judge.py`, `models/unit.py`, `weblate/trans/models/change.py`, `weblate/api/producer/`, `weblate/api/tests.py`; существующие audit механизмы.

**Действия:**

- [ ] **Receipt поверх существующего audit (S3).** `Change.revert` уже восстанавливает `old`/`old_state` под блокировкой строки (`weblate/trans/models/change.py:975-1000`), а `accept_judge_candidate` пишет `change_details={judge_verdict_id, judge_run_id}` (`judge_loop.py:2192-2202`). Receipt — тонкая связь `(application_id → Change.pk, run, JudgeRunUnit, candidate_target_hash, applied_revision)`, доступная и после удаления Suggestion; чтение защищено теми же scope permissions. Недостающий guard — «на строке нет более новой отменяемой Change»: сегодня `Change.revert` этого не проверяет.
- [ ] Под `select_for_update` сравнить текущую revision со своей applied revision. Любое последующее изменение, включая возврат идентичного текста, запрещает автоматический undo; вернуть diff/`409`, не overwrite.
- [ ] Восстановить через штатный audited edit без propagation; прежний `30` только при действующем review permission. Undo не выдаёт новые judge evidence за проверку восстановленного текста и не запускает платный вызов.
- [ ] Идемпотентный повтор возвращает прежний undo receipt; нельзя повторно откатить более новую работу. Откат только своего application, не всей истории run. Маршрут плоский — `POST /api/producer/judge-applications/{id}/undo/`: `WeblateRouter` регистрирует только плоские ресурсы (`weblate/api/urls.py:31-48`), вложенный `runs/{id}/applications/{id}/undo/` потребовал бы ручного `path()`.

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

**Файлы:** `docs/api.rst`, `docs/specs/openapi.yaml` (только регенерация), `docs/product/guides/producer-guide-weblate.md`, `docs/changes.rst`, `docs/security/threat-model.rst`; существующие `weblate/trans/views/edit.py`, `weblate/trans/views/judge.py`, `weblate/templates/snippets/judge-verdict.html` и report consumers, найденные через references. Полный новый frontend не является скрытым условием API-плана; существующие поверхности должны оставаться согласованными с общими правилами.

**Действия:**

- [ ] Документировать ресурсы `/api/producer/` этого плана (`runs/estimate/`, `runs/`, `runs/{id}/`, `runs/{id}/resume/`, `decisions/`, `decisions/{unit}/repair|accept|apply-candidate|clarification/`, `projects/{slug}/decisions/apply/`, `judge-applications/{id}/undo/`), коды ошибок `400/403/404/409/423`, сроки действительности снимка и хранения receipts, server ceilings и модель идемпотентности `revision`/`estimate_id`/`scope_hash`. Значения брать из утверждённой при реализации конфигурации, не обещать бесконечное хранение.
- [ ] **OpenAPI — регенерация, не ручная правка.** `docs/specs/openapi.yaml` обновляется `make -C docs update-openapi`; CI падает на расхождении (`.github/workflows/api.yml:82-88`), а `AGENTS.md` запрещает держать в `docs/specs/` рукописные документы форка. `409` и его enum регистрируются рядом с существующим `423` (`weblate/api/serializers.py:4313-4316`, `weblate/api/spectacular.py` `ENUM_NAME_OVERRIDES`); заблокированный компонент отвечает типизированным `423`, а не `403`, как сейчас в синхронном action (`weblate/api/views.py:3675-3676`).
- [ ] Показывать «Судьи не обнаружили ошибок», а не «AI-approved»; cached/fresh/stale/unparsed и partial execution отдельно. Русское объяснение и diff помогают принять решение, но не превращают пользователя в проверяющего вьетнамский.
- [ ] Объяснить платность запуска/force/continuation/повтора и предел cancel, отсутствие платного вызова у apply/clarify/undo. Не показывать некалиброванные проценты уверенности.
- [ ] **Threat model — конкретные записи (G7).** Новое семейство эндпоинтов, тратящее деньги по токену без UI: отдельный DRF throttle scope и запись actor/ключа на run (`AGENTS.md` перечисляет rate limits среди триггеров модели угроз). Текст уточнения продюсера попадает в два LLM-промпта — распространить существующее правило про explanation. Хранение snapshot-ов preview и receipts сверить с `cleanup_judge_observability` (`weblate/trans/tasks.py:1603-1641`), которая удаляет attempts через N дней. Утверждение модели угроз о том, что применение сохранённого кандидата отказывается без свежей улики (`docs/security/threat-model.rst:378-399`), переписывается под проверку-до-записи.
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

Порядок реализации: задача 1 **выполнена**; затем 3 → 5a → 4 → 5b → 6 → 7/8 → 10. Preview (4) поставлен после 5a намеренно: его снимок и stale-`409` опираются на модель run, статусы и request key задачи 5a, а не наоборот. Семантическая линия 9 исследуется независимо после утверждения экспериментального scope. Задача 2 не является автоматическим последствием остальных.

`weblate/trans/models/judge.py`, `judge_loop.py` и `autotranslate.py` меняет один integration owner — и этот же владелец держит пересечение с роадмапом: задача 1.4 роадмапа (`docs/product/vision/producer-console-design-and-roadmap.md:1121`) владеет стадиями `localize`, а задача 2.3 (`:1343`) — экраном запуска судьи; в обеих теперь есть раздел «Пересечение», называющий границу. Две правки `trans_judgerun` из разных планов без единого владельца дадут конфликтующие миграции на таблице с производственными прогонами. Параллельные исполнители допустимы только после фиксации контрактов и разделения ownership.

**Внесено в роадмап 2026-09-15** (закрытый список §5 остался один): пять ресурсов задач 6–8 и уточнения, значения `cancel_requested/partial/cancelled` у `Run.status`, определение `revision`, отметка о частично выполненной 0.2 и разделы «Пересечение» в задачах 1.4, 2.1, 2.2 и 2.3 — они называют, что приходит из этого плана, а что остаётся владельцу консоли. Дополнять §5 дальше без правки самого роадмапа нельзя.

Согласованы продуктовые правила из таблицы, включая отсутствие лингвиста. Persistence layout, сроки хранения и численные server limits требуют технической фиксации до соответствующей реализации; существующие guards нельзя ослаблять молча. Архитектурный review этого плана против текущего кода и роадмапа выполнен 2026-09-15 — вердикт `approve-with-changes`, его блокирующие пункты B1–B7 и пробелы G1–G8 закреплены в тексте выше.

**Доказано локальной пробой и реализацией задачи 1:** исходный REST создавал orphan evidence, batch закрывает этот дефект, история появляется на странице отчёта; approved-state понижается после `pass` в обоих путях — дефект воспроизведён на baseline в той же среде и запинен тестом. **Подтверждено чтением кода:** native candidates уже есть, перепроверка сейчас после записи, обычное suggestion acceptance использует общий guard, durable dispatch существует только в лок-кит-пути. **Не доказано:** общее качество судей/ремонта, эффективность разделения обязанностей, безопасность ещё не реализованного async цикла.

**В этой редакции:** задача 1 реализована, смерджена (`bac40ad8`, доп. правка документации `1901547f`) и развёрнута; остальное — обновление плана. Нет других product-правок, новых результатов платного эксперимента, восстановления production. Следующее разрешение — реализация задач 3 и 5a; платные модели и производственные операции согласуются отдельно.
