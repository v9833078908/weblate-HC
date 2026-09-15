# Judge: конфликт с глоссарием и история REST-запуска

**Дата:** 2026-09-15.
**Статус:** предложенный план, ожидает согласования реализации. Запрошено составление плана, не изменение кода или продовых данных.
**Основание:** расследование Pirate Ships / Vietnamese в этой беседе; запрос «составь план для исправления гэпа» и просьба рекомендовать scope.
**Проверенный checkout:** `dadab98e1ceb1eb51112386e4fe74e27b64ac2a5`.
**Правила работы:** `AGENTS.md`. Для реализации использовать `ultrasuperpowers-executing-plans`.

## Цель и выбранный подход

Исправить два независимых дефекта: семантическую слепую зону judge при ошибке в самом глоссарии и отсутствие полноценной истории штатного REST-запуска judge. Восстановление переводов Pirate Ships — отдельная операционная часть с отдельным разрешением.

Приоритет — качество. MT должен сохранять единую терминологию, но judge не должен принимать ошибочный смысл только потому, что target повторяет glossary target. Полнота истории нужна для установления исполнителя, scope и результата следующего инцидента; сама по себе она перевод не улучшает.

Выбран минимальный подход:

- Уточнить семантическую независимость judge и буквальную обратную передачу смысла target, сохранив существующие MQM-схему, severity и правила коллегии.
- Проверить изменение реальными ответами моделей на человеческой разметке, включая допустимые игровые имена. Не заменять эту проверку mock-ответом `reject`.
- Перевести существующий REST endpoint на существующий `BatchAutoTranslate`, который уже владеет жизненным циклом `ProducerRun`. Не создавать второй механизм истории.
- Сначала исправить подтверждённый glossary target, затем адресно восстановить игровые строки. Judge использовать для проверки, а не как гарантированную кнопку повторного перевода.

Согласование этого документа не разрешает deployment, продовые записи или платные LLM-вызовы. Разметка и утверждённый вьетнамский термин пока отсутствуют; это явные входные условия соответствующих задач, не якобы уже полученные результаты.

## Наблюдения и исправление прежнего вывода

### Подтверждённый инцидент

Данные ниже — снимок расследования, не обещание неизменности production:

- Проект `pirate-ships`, язык `vi`; glossary component `glossary-ru`, unit `163309`: `Форт` → `Ben tàu`, state `20`. История показывает добавление из репозитория 2026-08-18 и последующий maintenance-флаг `terminology`, а не доказательство человеческого утверждения перевода.
- Игровой unit `538134`, ключ `title_restriction_forts`: `Форты` → `Ben tàu`. 2026-09-15 в 11:22:26 UTC записан автоматический перевод от `mt:openrouter`.
- Оба judge-seat ответили `pass` без ошибок: `atlas/qwen3.8-max` в 11:24:07 UTC и `deepseek-v4-pro` в 11:24:12 UTC. HTTP 200, ответы разобраны. У первого seat в сохранённом evidence обратный перевод для `Ben tàu` — `Форты`.
- В восстановленном request есть glossary-пара `Форт` → `Ben tàu`, флаг `terminology` и source explanation про fort tier. Конфигурация judge рабочая; project context описывает Pirate Ships и строительство форта.
- В payload термина нет поля человеческого approval. Однако текст judge-промпта называет glossary targets «approved». Это приписанная промптом достоверность, не доказанный статус записи.
- У verdicts есть общий `run_id`, но у соответствующих HTTP attempts `run_id=null`, а `ProducerRun` / `JudgeRunUnit` для случая не найдены.
- Найдены семь игровых строк с `Форт` в source и `Ben tàu` в target. Всего ошибочная glossary-пара совпадала с 52 игровыми строками; это scope проверки, а не 52 доказанных ошибки.
- Отдельная строка `fort_enemy_name_3`, unit `150685`, имеет source `Причал Корсаров`. Совпадение `Ben tàu` в её target не основание заменять причал на форт.

Архив содержит восстановленный request из текущих данных и сохранённые verdict/attempt/change records, не полный неизменяемый transcript исходного HTTP body. Не заявлять большего.

### Штатный API сам допускает отсутствие истории

Прежнее утверждение ассистента «штатный REST уже создаёт run, значит клиент обошёл API» неверно:

1. `weblate/api/views.py:TranslationViewSet.autotranslate` создаёт непосредственно `AutoTranslate`, без `producer_run`.
2. `weblate/trans/autotranslate.py:AutoTranslate.process_judge` передаёт `run` в `run_judge_batch` только при наличии `self.producer_run`; без него также не записывает участие через `JudgeRunUnit`.
3. `weblate/trans/judge_loop.py:run_judge_batch` при `run=None` генерирует UUID для verdicts, но передаёт `run=None` HTTP-клиенту. Это объясняет наблюдаемое сочетание UUID у verdict и NULL у attempt.
4. Создание и финализация `ProducerRun` находятся в `BatchAutoTranslate`, который штатный REST endpoint сейчас не использует.

Фактический URL и тело клиентского запроса не сохранены в этом расследовании; обходной интеграционный маршрут не доказан и не нужен для объяснения дефекта. В предыдущей части расследования SHA-256 файлов `api/views.py`, `trans/autotranslate.py`, `trans/judge.py`, `trans/judge_loop.py` совпали между checkout и production.

Дополнительно: `weblate/trans/management/commands/auto_translate.py:Command.handle` допускает только `translate`, `fuzzy`, `suggest`; Django-команда не поддерживает `--mode judge`. Не смешивать её CLI-флаг с JSON-параметром REST `"mode": "judge"` или флагом неизвестной клиентской обёртки.

## Границы и неизменяемые контракты

### Семантика

- `GlossaryPromptEntry` и `build_glossary_prompt_entry` в `weblate/glossary/models.py` не получают новых полей или правил фильтрации. Импорт, распространение `terminology`, `exact`, `read-only`, `forbidden`, `not-applicable` не меняются.
- MT по-прежнему следует настроенной терминологии. Не включать автоматическое исправление glossary units силами judge и не учить MT молча игнорировать глоссарий.
- Judge рассматривает glossary как настроенную терминологическую справку, но совпадение target с записью не доказывает сохранение смысла source. Даже формулировка project persona/style «glossary as law» не отменяет семантическую проверку judge.
- Конфликт должен быть обоснован предоставленными source/target и пояснениями: неверный референт, функция или действие. Одна лишь необычная словарная форма игрового имени не ошибка. Явно заданное собственное имя или намеренная адаптация остаётся допустимой.
- Judge оценивает перевод строки, не выпускает самостоятельный вердикт качества всего глоссария. Если ошибочная пара исказила target, ошибка привязана к реальному span target, категория `mistranslation` либо `terminology`, с понятным английским описанием конфликта. Severity определяется последствиями для игрока, не самим фактом расхождения.
- Если target передаёт верный смысл, а glossary-пара доказуемо относится к неверному понятию, не создавать ложную terminology-ошибку лишь за несовпадение с ней. Допустимые применимые термины и флаги сохраняют обычную силу.
- `back_translation` передаёт написанный target, а не восстанавливает ожидаемый source через обратную glossary-подстановку. Это evidence для человека, не самостоятельное доказательство правильности.
- JSON ответа, parser, алгоритм consensus, state transitions, лимиты, fallback и набор моделей остаются прежними. Не вводить regex для «Форт» и не обещать нулевое число false negatives.

### REST и история

Существующий endpoint:

```text
POST /api/translations/{project}/{component}/{language}/autotranslate/
```

- Оставить синхронный контракт и ответ HTTP 200 с `{"details": <message>}`. Не вводить очередь, новые параметры, новый endpoint или обязательный новый ответ.
- После текущих проверок объекта, прав и `AutoForm` endpoint создаёт `BatchAutoTranslate(translation, user=get_request_user(request), q=..., mode=...)` и вызывает его `perform` с текущими очищенными аргументами.
- Для принятых judge-запусков lifecycle принадлежит только `BatchAutoTranslate`: один run с настоящим actor, translation scope, query, configuration snapshot, итоговым статусом и summary. Ошибки до принятия scope могут отказать без run; платных вызовов до этого нет.
- Свежие HTTP attempts и verdicts привязаны к этому run; обработанные и cap-skipped units представлены существующими `JudgeRunUnit`. При полностью кешированном запуске есть новый run и участие, но нет выдуманных новых HTTP attempts и перепривязки старых verdicts.
- Повторить ограничения прав через существующий batch-контракт; нельзя расширять translation scope или доверять actor/scope из JSON. Не устанавливать `enforce_permissions=False` ради прохождения тестов.
- Весь endpoint использует один batch-путь, без ветки-совместимости только для judge. Для остальных режимов сохраняются реальные эффекты: MT получает уже существующую batch-историю, `auto_source=others` — без искусственного producer run.
- `AutoTranslate` остаётся низкоуровневым исполнителем. Его использование внутри batch, при создании перевода из TM и в CLI, который не допускает judge, не удалять. Не делать `run` обязательным для всех внутренних/исследовательских вызовов.
- Исторические orphan verdicts не переписывать и не дополнять выдуманными actor/run records. Новый запуск создаёт новую историю.

## Карта файлов

| Файл | Ответственность в плане |
|---|---|
| `weblate/trans/judge_prompts/verdict.txt` | Уточнение достоверности glossary reference, проверки смысла и back-translation |
| `weblate/trans/judge.py` | Существующие `_load_prompt`, `_payload`, `_prompt_schema_version`, `request_verdicts`; использовать, не менять протокол |
| `weblate/glossary/models.py` | Существующая сериализация и семантика glossary flags; намеренно без изменений |
| `weblate/api/views.py` | Переключение `TranslationViewSet.autotranslate` на batch lifecycle |
| `weblate/trans/autotranslate.py` | Использование существующего `BatchAutoTranslate`; новые lifecycle-методы не требуются |
| `weblate/api/tests.py` | Регрессия через реальный REST action, права, scope, история и побочные эффекты |
| `weblate/trans/tests/test_judge_client.py` | Удаление теста, привязанного к словам промпта; существующие transport/schema тесты |
| `weblate/trans/tests/test_judge_loop.py`, `weblate/trans/tests/test_judge_round.py`, `weblate/trans/tests/test_judge_autotranslate.py` | Существующие cache, snapshot, cap и lifecycle регрессии |
| `docs/admin/checks.rst`, `docs/api.rst`, `docs/product/guides/producer-guide-weblate.md` | Семантические ограничения judge, запуск через API, действия при ошибочном термине |
| `docs/changes.rst` | Краткая запись в текущем unreleased разделе со ссылками на owning docs |
| `analysis/data/`, `analysis/probes/` | Корпус и локальный эксперимент; не продовые fixtures и не новый сервис |

Новые артефакты эксперимента предлагаются в задаче 1; сейчас они не существуют. `docs/specs/openapi.yaml` намеренно не меняется: wire-контракт API не меняется. `docs/security/threat-model.rst` прочитан: нового endpoint family, token mode, outbound class или security claim не вводится; не менять модель угроз без обнаруженного изменения её условий.

## Задача 1. Проверяемая семантическая независимость judge

**Результат:** известный конфликт не скрывается за совпадением с глоссарием; законные игровые имена, режимы терминов и корректные строки не становятся ложными ошибками. Это вероятностное улучшение с измеренным scope, не гарантия автоматического исправления.

**Зависимости:** согласование реализации; человеческие labels и отдельное разрешение на стоимость/объём LLM-эксперимента до платных запросов. Задача 2 этих входов не требует.

**Файлы и интерфейсы:** prompt, существующие request/parser/cache интерфейсы из карты; предлагаемые новые файлы `analysis/data/pirate-ships-glossary-conflict-cases.json` и `analysis/probes/judge-glossary-conflict-eval.py`. Это ограниченный исследовательский runner, не production-компонент.

**Действия:**

- [ ] Зафиксировать reproduction из расследования как известный dev-case; не класть Pirate Ships / `Форт` / `Ben tàu` в system prompt как специальное правило.
- [ ] Собрать и отдать лингвисту на разметку минимум 20 корректных и 20 ошибочных примеров именно для данного failure mode. Отделить source, target, context, glossary с пояснениями/флагами, reference back-translation, label и причину. Не объявлять семь однотипных строк семью независимыми доказательствами.
- [ ] Разделить примеры на dev и закрытый test по термину/понятию: варианты одной пары не должны попасть в обе части. Известный инцидент остаётся только dev. Не использовать test-примеры в промпте. Если нужны few-shot examples, брать их из отдельной train-части, а не dev/test.
- [ ] Взять контрактные контрольные случаи: ошибочная пара и ошибочный target; ошибочная пара и правильный target; исправленная пара; обычный причал; адаптированное игровое имя с явным explanation; регулярное склонение, `exact`, `read-only`, `forbidden`, неприменимое понятие. Labels ставит человек, не проверяемый judge.
- [ ] Изменить prompt согласно семантическому контракту выше. Убрать необоснованное общее утверждение о человеческом approval всех entries; не удалять glossary из request и не ослаблять правила применимых терминов.
- [ ] Удалить `SegmentGlossaryTest.test_prompt_defines_glossary_context_and_modes`: поиск слов в prompt не доказывает поведение модели. Не перепривязывать этот тест к новой формулировке. Сохранить тесты публичного payload и parser-контрактов.
- [ ] Runner читает локальный frozen corpus, поддерживает явный выбор baseline/candidate prompt, ограничение case IDs и выходной JSON с case ID, arm, seat, raw parsed errors, back-translation, unparsed и redacted profile metadata. Вызывает существующий `request_verdicts` с `persist_attempts=False`; не вызывает `AutoTranslate`, repair и продовую БД. Не сохраняет credentials или полные HTTP headers.
- [ ] На dev выполнить baseline/candidate с одинаковыми входами и seat profiles. Проверить фактически обслужившие модели, prompt fingerprints и отсутствие cache; не смешивать разные fallback-профили в один результат. Изменять prompt только по dev, затем один раз измерить candidate на закрытом test.
- [ ] После проверенного поведения обновить раздел `llm-judge` в `docs/admin/checks.rst` и разделы про glossary/judge в `docs/product/guides/producer-guide-weblate.md`: наличие glossary target не доказывает его качество; сначала исправляется термин, потом зависимые строки. Отразить изменение и пересчёт cache identity в unreleased changelog.

**Проверка модели:**

- На известном dev-case оба seat должны обнаружить замену понятия, а back-translation не должен подставлять `Форты` вместо фактического смысла target. Это проверяет человек, не exact-string assert на единственный русский синоним.
- На контрольном причале и явно заданном игровом имени не должно быть ложного semantic error. На правильном target с доказуемо неверной glossary-парой не должно быть ложного требования вернуть неверный термин.
- Для оценки данного failure mode `Pass` означает отсутствие относящейся к нему substantive ошибки; `Fail` — обнаруженный смысловой/терминологический дефект. Minor-замечания и общая MQM severity отчётно сохраняются отдельно. Отдельно показать TPR (сохранение человеческих Pass) и TNR (обнаружение человеческих Fail), абсолютные числители/знаменатели и долю unparsed для каждого seat и итоговой коллегии. Unparsed — не Pass и не корректное распознавание Fail; не исключать его молча из знаменателей.
- Предлагаемый выпускной порог: TPR и TNR не ниже 90% на закрытой выборке, все названные контрольные случаи верны, каждый спорный ответ просмотрен человеком. Малый корпус даёт ограниченную уверенность: приложить интервалы и не переносить эти проценты на весь production. Если gate не пройден, задача не закрыта; не включать в этот план смену моделей или новую архитектуру без отдельного решения.

**Проверка кода:** существующий `test_prompt_schema_change_invalidates_cached_verdict` должен проходить. `_prompt_schema_version()` уже хеширует bytes `verdict.txt`: ручная миграция или удаление старых verdicts не нужны. Глобальный prompt изменит identity последующих запросов во всех проектах — не запускать автоматическую перепроверку всех строк.

Команда после реализации в уже работающем dev-container:

```sh
./rundev.sh test -n 0 weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/glossary/tests.py
```

Платный эксперимент проводится локальным runner после утверждения списка случаев, profiles и верхнего лимита запросов. Конкретная CLI runner определяется и записывается в его help при создании; сейчас такой команды нет. HTTP mocks доказывают transport/cache контракты, не семантическое устранение инцидента.

## Задача 2. Полная история штатного REST-запуска

**Результат:** разрешённый REST judge-run виден в существующей истории с actor, scope, итогом и участием строк; свежие attempts привязаны к run. Синхронность, response shape, permissions и семантика режимов остаются прежними.

**Зависимости:** согласование реализации. Независима от задачи 1; её можно завершить первой, не называя проблему качества исправленной.

**Файлы и интерфейсы:** `TranslationViewSet.autotranslate` в `weblate/api/views.py`, `weblate/api/tests.py`, существующий `BatchAutoTranslate.perform`, `docs/api.rst`; lifecycle и модели из карты используются без нового слоя.

**Действия:**

- [ ] До правки добавить регрессию через `api:translation-autotranslate`: разрешённый пользователь, существующий translated unit, узкий `q`, `mode=judge`, валидная конфигурация, реальные ORM-записи и mock только на внешнем HTTP/MT. До исправления запрос может успешно вернуть details и сохранить verdicts, но не создаёт полноценный run/участие.
- [ ] Заменить прямое создание `AutoTranslate` в этом action на `BatchAutoTranslate` с объектом translation. Сохранить вызов `AutoForm`, `check_auto_translate_permission`, проверки locked component, текущие аргументы perform и ответ. Не переносить lifecycle в view и не менять соседний путь создания translation через TM.
- [ ] Проверить связи и доступность истории через существующий report view, а не только факт вызова batch или копирование полей. В `test_autotranslate_restrict_direct_editing` заменить plumbing-assert `perform.assert_called_once` проверкой реального запрета записи / доступного suggestion и неизменности target.
- [ ] Добавить только отсутствующее покрытие границ из списка ниже; существующие batch-тесты не дублировать по всем параметрам.
- [ ] После smoke обновить `docs/api.rst`: REST `mode=judge`, narrow `q`, права, что режим делает с существующим переводом, где находится история, что HTTP 200 не означает качественный перевод. Не советовать `weblate auto_translate --mode judge`. Добавить краткую unreleased запись со ссылкой на API/`llm-judge`.

**Регрессии и ожидаемые результаты:**

1. Свежий ответ двух seat: один terminal `ProducerRun` с actor из authentication, translation scope и исходным query; выбранные `JudgeRunUnit`; реальные новые `JudgeRequestAttempt.run_id` и verdict run identity соответствуют run. Существующий report показывает выбранный unit и итог, доступный уполномоченному пользователю.
2. Повтор с подходящим cache: новый законченный run и participation; существующее evidence переиспользовано, старые verdicts не переписаны, новых HTTP attempts нет.
3. `q` с ID строки другого проекта/языка: чужие units не оцениваются и не изменяются. Нет review permission или component locked: 403 и ни одного платного вызова/записи перевода; malformed form: 400.
4. Пустой scope и превышение cap: truthful summary, cap-skipped участие, никакого платного вызова для пропущенного unit. Использовать поведение существующих batch-тестов как контракт.
5. Отказ сервиса и неожиданный exception после создания run: run не остаётся RUNNING, ошибка видна в сохранённом исходе; не синтезировать pass и не удалять успевшее сохраниться evidence. Unparsed остаётся отдельным исходом, не автоматически FAILED и не Pass.
6. Approved target не перезаписывается; `suggest` сохраняет suggestion без изменения target; existing translated target не получает безусловного повторного MT только из-за `mode=judge`. Сохраняются действующие candidate/repair правила.

Тестовые образцы: `weblate/api/tests.py:test_autotranslate`, `test_autotranslate_json`, `test_autotranslate_restrict_direct_editing`; `weblate/trans/tests/test_judge_autotranslate.py:test_project_launch_records_one_run_across_translations`, `test_refused_request_fails_the_run_without_a_fake_verdict`, `test_task_exception_marks_the_run_failed`, `test_capped_units_record_a_cap_skip`. Для связи attempts мокать внешний HTTP по образцу `weblate/trans/tests/test_judge_client.py`, не подменять весь `run_judge_batch`.

```sh
./rundev.sh test -n 0 weblate/api/tests.py -k autotranslate
./rundev.sh test -n 0 weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py
```

Runtime smoke после тестов: на изолированной локальной fixture-translation с настроенным stub HTTP provider выполнить настоящий POST этого endpoint для одного unit; наблюдать HTTP response и соответствующий сохранённый run/report. Это доказывает REST lifecycle, не качество judge. Production и paid provider для API-regression не нужны.

## Задача 3. Адресное восстановление Pirate Ships

**Результат:** подтверждённый неверный glossary target исправлен, зависимые ошибочные переводы восстановлены без массовой замены корректных причалов, approved строк, placeholders и метаданных.

**Зависимости:** отдельное разрешение на каждую операционную область — чтение актуального scope, продовые записи, оплачиваемый MT/judge и deployment при его необходимости. Утверждённый вьетнамский термин и целевые переводы даёт ответственный лингвист. Задачи 1–2 не заменяют это решение; ручное исправление данных может быть разрешено раньше deployment кода.

**Файлы и интерфейсы:** production units ниже; штатный путь редактирования через Weblate, с доступом только по `deploy/vps.sh` и `weblate shell` согласно `AGENTS.md`. Не использовать прямой `QuerySet.update` для обхода unit history, checks и VCS. Результат операции сохраняется отдельным датированным документом в `docs/operations/reports/` при её выполнении.

**Действия:**

- [ ] С разрешения перечитать glossary unit `163309` и seven-unit allowlist ниже, сохранив actual source/target/state, explanations и flags до записи. Проверить project/component/language/context по каждому ID. Расхождение с incident snapshot — причина пересмотреть конкретный patch, не затереть более новую правку.
- [ ] Получить canonical target для `Форт` и согласованные переводы предложений, включая грамматику, диакритику и формы fort tier. Упомянутый в расследовании вариант `Pháo đài` — кандидат, не автоматически утверждённый термин.
- [ ] Сначала исправить только glossary target стандартным путём с сохранением context, explanations, flags и audit history. Подтвердить, что новый `build_request` зависимой строки содержит исправленный термин и новый context hash.
- [ ] Просмотреть 52 ранее совпавшие строки как candidate scope; не считать все ошибочными и не расширять write allowlist без согласования. Восстановить только подтверждённые строки, адресно. State=approved по умолчанию исключён из записи; его изменение требует отдельного явного разрешения.
- [ ] При выборе MT сначала получить кандидаты после исправления glossary и проверить их человеком. Повторный `mode=judge` не гарантирует переписывание существующего target; применять согласованный patch штатным действием, не полагаться на него как на force-translate.
- [ ] Только после отдельного разрешения проверить исправленные строки свежим judge без cache. Если применяется `run_judge_batch` как translation-read-only canary, обязательны `writable_ids=set()`, `mutating_repairs=False`, `candidate_severities=()`, `use_cache=False` и настоящий разрешённый run. Такой canary всё равно платный и пишет audit tables, поэтому не называется полностью read-only.
- [ ] Подтвердить новые DB targets, соответствующий файл/VCS, сохранность `{0}`, metadata и approved строк. Контроль `fort_enemy_name_3` не меняется. Сохранить run IDs, per-seat outcomes, unresolved замечания и фактически изменённые IDs; никакого «AI-approved».

Исходный allowlist расследования:

| Unit ID | Context |
|---|---|
| `538134` | `title_restriction_forts` |
| `538150` | `description_restriction_forts` |
| `416646` | `territorial_wars_game_manager_capital_open` |
| `148566` | `ship_name_fort_1` |
| `149614` | `dialog_name_fort_fleet` |
| `149625` | `screen_name_fort_equipment` |
| `150679` | `fort_label` |

**Проверка:** exact before/after по утверждённому набору, человеческая оценка смысла, checks и сохранность placeholder в `description_restriction_forts`; неизменность контрольного unit `150685`. Нет массового поиска-замены `Ben tàu`, нет переписывания старых verdicts. Rollback данных — адресный audited patch по сохранённому before с проверкой отсутствия последующих правок, не откат всего проекта.

## Интеграция, выпуск и готовность

Задачи 1 и 2 имеют независимых владельцев файлов. Общий `docs/changes.rst` редактирует один интегратор после smoke. Задача 3 не запускается как часть CI или deployment. Нового glossary approval workflow, schema migrations, language-instructions integration, смены моделей, async API и переработки report UI в scope нет.

Итоговая проверка реализации: приведённые целевые тесты, локальный REST smoke, человечески размеченный LLM-experiment и lint/format изменённых файлов через `uv run prek run --files ...`. Команды тестов используют уже работающий dev-container и тестовую БД; если контейнера нет, подготовить изолированное окружение по `docs/contributing/tests.rst`, не пересоздавать shared stack без разрешения. Для host-инструментов соблюдать dependency setup из `AGENTS.md`.

После проверок убрать только временный smoke scaffold; измерительные corpus/results оставить в `analysis/`. Коммит и push по правилам репозитория. Deploy отдельно разрешается и проверяет одинаковую версию prompt/code у web и Celery; запуск новых контейнеров или restart workers не подразумевается разрешением на commit.

Выпуск задачи 1 глобально меняет judge prompt identity. Это может потребовать новых оплачиваемых проверок при следующих запусках; старые evidence остаются историей и не перепроецируются массово. При провале семантического gate prompt не выпускается; задачу 2 можно выпустить отдельно как исправление аудита. Rollback prompt/API — штатный откат кода с отдельным разрешением на deployment, без удаления созданной истории.

**Проверка покрытия плана:**

- Ошибочная glossary-пара и source-biased back-translation → задача 1 и восстановление данных в задаче 3.
- Допустимые игровые имена и glossary modes → контрольные случаи задачи 1, без нового approval subsystem.
- Orphan API evidence → задача 2, с сохранением cache/history semantics.
- Неправильная рекомендация `--mode judge` → owning API/producer docs в задачах 1–2.
- Семь подтверждённых совпадений, 52 кандидата и корректный причал → ограниченный scope задачи 3.
- Права, чужой project scope, approved targets, cache и ошибки → регрессии задачи 2 и before/after задачи 3.

**Готовность:** границы реализации и API-решение определены. План не утверждён. Для завершения семантической проверки нужны человеческие labels и разрешение на ограниченные платные вызовы; для восстановления production — canonical target и разрешение на точный write scope. Пока этих входов нет, нельзя объявить весь инцидент устранённым. Подготовка данного плана не запускала тесты продукта, LLM, deployment или запись production.
