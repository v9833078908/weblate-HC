# Согласование повторов и управляемое повторное использование

Дата: 2026-09-17.
Статус: реализовано в ветке ``feat/repeat-drift-reconciliation``; ожидает
review и merge через pull request. Фактические доказательства и ограничения
проверок записаны в конце документа.

## Цель, основания и границы

Уменьшить число необоснованных различий между точными повторами и число
ручных решений: разбирать группу вхождений, а не каждый `repeat-drift`
отдельно. После подтверждения общности переводить разрешённую группу один
раз и переиспользовать результат при последующих запусках и дозагрузках.
Смысловые исключения, approved и параллельные правки сохраняются.

Основания:

- `docs/product/research/2026-09-17-repeat-drift-tms-practices-and-session-context.md`;
- согласованные в сессии решения UX, зафиксированные ниже;
- контракт экрана очереди
  `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` и ревью
  прототипа `docs/product/reviews/2026-09-22-repeat-queue-ui-variant-review.md`;
- `docs/product/vision/producer-console-design-and-roadmap.md`, волна 2;
- правила разработки и разрешений: `AGENTS.md`.

В объём входят нативная очередь Weblate, долговечные решения и исключения,
пакетные рекомендации LLM, безопасное применение, интеграция редактирования,
импорта и MT. Producer Console получает пункт роадмапа, не новый экран в этой
реализации. Не входят глобальная переделка TM, session canon, fuzzy sharing,
семантическое объединение разных исходников, автоматический LQA-score,
массовое исправление production и платный пилот без отдельного разрешения.

Выбран подход «явная область sharing + решение группы + независимые
вхождения». TM остаётся источником кандидатов, а не реестром разрешений на
замену. Native propagation сохраняет прежнюю семантику вне управляемых групп.
LLM только рекомендует; её ответ не утверждает перевод и не разрешает запись.

**Гейты:** согласование UX не означает утверждение этого технического плана.
До изменения продукта требуется утверждение плана. Деплой, миграции на живых
инстансах, исправление переводов и платные вызовы требуют отдельных разрешений.

## 1. Согласованные решения UX

1. Сейчас — нативный Weblate. Producer Console позже использует те же решения
   и сервисы, а не вторую базу групп.
2. Очередь ограничена проектом и целевым языком, объединяет компоненты;
   компонент — фильтр. Перед любой массовой записью обязателен preview.
3. Правило новых повторов задаётся выбранными компонентами и явными метками
   строк. Длина, префиксы ключей и частота варианта не доказывают общность.
4. Конфликтующий approved не меняется. Выбранный вариант можно применить к
   доступным non-approved; оставшийся конфликт не объявляется разрешённым.
5. Ручная правка связанного вхождения требует выбора: изменить общий перевод
   или сделать это вхождение независимым. Никакого скрытого распространения.
6. LLM заранее готовит рекомендации для всей выбранной очереди. Человек
   подтверждает одну группу или явно выбранный пакет групп.
7. Два платных триггера: отдельная команда подготовки рекомендаций и явный
   opt-in на форме MT. Открытие страницы, фильтрация и preview бесплатны.
8. При неизвестной денежной оценке запуск допустим только с предупреждением
   и явным верхним пределом запросов. Неизвестная цена не отображается как $0.

## 2. Что уже есть и почему этого недостаточно

| Граница | Текущий код | Следствие для изменения |
|---|---|---|
| Detection | `weblate/checks/consistency.py:RepeatDriftCheck` | Advisory, точный source между разными contexts; не механизм sharing |
| Native propagation | `weblate/trans/models/unit.py:Unit.same`, `Unit.propagate`, `Unit.save_backend` | Равенство source и context; нельзя просто включить настройку |
| Запись | `weblate/trans/models/unit.py:Unit.translate`, `Unit.generate_change` | Использовать блокировки, историю и обычные проверки, не bulk SQL targets |
| Массовый preview | `weblate/trans/views/search.py:fix_check`, `weblate/templates/fix_check.html` | Переиспользовать паттерн подписанного scope, не маскировать операцию под существующий mass-fix |
| Метки | `weblate/trans/models/label.py`, `Unit.all_labels`, `ContextForm` | Метки проектные и принадлежат source; не создавать вторые target-only labels |
| MT | `weblate/trans/autotranslate.py:AutoTranslate`, `BatchAutoTranslate` | Ограничить scope до reuse; сохранять через действующий путь записи |
| LLM batching | `weblate/machinery/base.py`, `weblate/machinery/llm.py` | Разные contexts сейчас не обязаны делить генерацию; cache не заменяет явную группу |
| Импорт | `weblate/trans/models/translation.py:Translation.update_units_from_store`, `weblate/trans/loc_kit.py` | Нельзя полагаться только на Unit PK или менять контракты append/import |
| Судья | `weblate/trans/judge.py`, `weblate/trans/judge_loop.py` | Переиспользовать транспорт, но не workflow с repair/изменением state |
| Деньги | `weblate/trans/models/llm_usage.py:LLMUsageLog` | Новая операция, nullable cost; не записывать рекомендации как judge verdict |

В обычном monolingual-формате identity может зависеть от context без source:
source изменится, а Unit останется тем же. В других форматах изменение source
меняет identity. Поэтому ни сохранённый PK, ни `id_hash` в одиночку не доказывают
актуальность решения. Check dismissal тоже не является разрешением sharing.

## 3. Общие контракты реализации

Названия новых файлов и сущностей ниже — проектируемые, не существующие API.
Все задачи используют эти контракты; не вводить альтернативные реестры.

### 3.1. Область, группа и независимость

`RepeatPolicy`: проект, source language, target language, выбранные component
IDs, source label IDs, enabled, revision, автор и время. Компоненты непусты;
внутри выбранных компонентов достаточно одной из выбранных меток. Пустой
набор меток означает все строки выбранных компонентов и требует явного
подтверждения в preview. Межпроектные компоненты/метки отвергаются.

Правило применяется динамически к будущим строкам, но не исправляет уже
имеющиеся targets при создании или изменении правила. Пересечение двух
активных правил на одной строке запрещено с перечислением конфликтующей
области: порядок создания не определяет победителя.

`RepeatGroup`: policy, точный массив source forms, совместимая plural-схема,
revision, nullable общий массив target forms, происхождение решения,
автор/время. Хеш служит индексом, полное содержимое сверяется. Нормализация
регистра, пробелов, markup или пунктуации для объединения запрещена.
Группа не привязана к «первой строке»: удаление такого экземпляра не удаляет
принятый общий target.

`RepeatMembership`: группа, ссылка на живой Unit, снимок его identity
`(translation_id, id_hash)`, source/context и ограничений, режим
`shared` либо `independent`, причина, revision. Независимость переживает
обычную перезагрузку той же identity. Удаление и появление нового ключа не
переносят исключение по похожему тексту. Смена source/context/ограничений
делает связь stale; повторное подключение требует решения, не fallback.

`RepeatDecisionEvent`: неизменяемая история scope, old/new решения,
затронутых Unit revisions, исключений, автора, причины и результата применения.
Удалённые компоненты/юниты не уничтожают исторический снимок.

Группа detection может включать строки вне правил. До явного правила или
решения они остаются кандидатами, не получают автоматический sharing.
Detection уже ограничен одним языком: `Unit.repeat_units` и
`RepeatDriftCheck.check_component` фильтруют по `translation__plural_id`, а
`Plural` принадлежит языку (`weblate/lang/models.py:Plural.language`).
Карточка очереди дополнительно сужает участников до компонентов и меток
правила.
Глоссарные строки и запрещённые существующей политикой propagation строки
не включаются автоматически; исключения видны в preview.

Пересечение правил проверяется не только при сохранении правила. Метка,
добавленная к source позже, может подвести строку под два правила одного
target language (`X + L1` и `X + L2`). Такая строка становится неуправляемой:
reuse и применение к ней запрещены, очередь показывает её как конфликт
правил. Пересчёт запускается из `Unit.save_labels` тем же сервисом
reconcile, что и остальные изменения selector.

Закрытые компоненты (`Component.restricted`) не раскрываются. Очередь,
карточка, preview и snapshot рекомендаций строятся только из компонентов
`Component.objects.filter_access(user)`; создание правила отвергает
недоступный actor компонент; worker повторяет проверку перед записью и перед
отправкой payload.

Объём `RepeatMembership` оценивается до задачи 1: число групп повторов,
размер наибольшей группы и число вхождений на язык на данных Anvil Saga
(пустой набор меток означает все строки выбранных компонентов). По замеру
фиксируется стратегия: строка membership на каждое вхождение повторяющегося
в scope source или только на решения и исключения с вычислением остальных.

### Замер membership перед задачей 1 (2026-09-22)

Для воспроизводимого консервативного предела использован сохранённый
read-only снимок `analysis/data/anvil-saga-fr-repeat-drift-2026-09-17.json`:
`anvil-saga` / `locale_v1-02-import-explained-3` / `fr`, точное группирование
исходника, состояние не ниже translated, без глоссария. В нём 748 повторных
групп и 2 156 вхождений; среди 387 расходящихся групп крупнейшая содержит 26
вхождений (1 096 вхождений). Снимок не покрывает все языки и потому не
служит оценкой production-wide; он достаточен, чтобы не выбирать дизайн,
который создаёт запрос на каждый экранный элемент.

Выбрана строка `RepeatMembership` только для уже принятых решений и явных
исключений. Нерешённые повторные группы вычисляются из живых Unit в scope
правила. Это сохраняет долговечность решения/независимости и актуальность,
не материализуя до 2 156 строк на одно правило уже при первом его просмотре.

### 3.2. Актуальность и применение

Предлагаемый сервис `weblate/trans/repeats.py` предоставляет сбор очереди,
preview, изменение политики, применение решения и условный undo. Один
primitive записи используется UI, worker и будущим Producer API.

Preview содержит group revision, точный список member IDs и их fingerprints,
source/target/state, locks, разрешения, ограничения и policy revision.
Подписанный токен связан с actor, проектом, языком, действием и сроком жизни;
сервер не доверяет targets и спискам получателей из HTML.

На POST: проверить токен → открыть транзакцию → заблокировать policy/group
и Units в стабильном порядке → повторить проверку scope/revisions/прав →
проверить кандидат для каждого получателя → записать через Unit и историю
с отключённым неявным propagation → обновить решение и пересчитать checks.
Сетевых LLM-вызовов внутри транзакции нет.

Изменившаяся группа даёт conflict без записи всей этой группы. Пакет допускает
частичный успех между группами, но возвращает результат каждой. Внутри группы
заранее объявленные protected/independent/incompatible участники пропускаются;
неожиданный stale не превращается в тихий skip. Approved target и state всегда
сохраняются; differing approved оставляет статус `approved_conflict`.

Общий target не означает approved. Новый/заменённый non-approved target
получает обычное состояние действующего пути перевода; все применимые
детерминированные checks выполняются. Кандидат с блокирующим нарушением
ограничений конкретного получателя не копируется туда, причина видна.

Сохранённый target получателя может отличаться от массива группы:
`Unit.translate` применяет `fix_target` (autofix зависят от флагов строки,
например `LineSeparatorSpacing` с `ignore-game-line-break` или `safe-html`),
DOS-переводы строк по `file_format_params` компонента и `adjust_plurals`.
Поэтому preview показывает target каждого получателя после autofix, событие
хранит фактически сохранённый target каждого Unit, а «вхождение совпадает с
решением» означает равенство сохранённому результату этой revision, а не
массиву группы. `RepeatDriftCheck` сравнивает сырые targets; расхождение,
созданное только autofix получателя, preview показывает до записи.

Undo восстанавливает только записи, всё ещё равные сохранённому результату
именно этого события, и его актуальную group revision. Более поздняя
человеческая правка или одобрение даёт конфликт; никакого безусловного
восстановления снимка.

### 3.3. LLM-рекомендация — отдельная read-only операция

Применение рекомендаций пакетом («repeats at scale») вынесено в отдельный план
`docs/product/plans/2026-09-23-repeat-queue-speed-and-bulk-recommendations.md`:
ограниченные запросы с продолжением по капу, неизменяемое подтверждение
просмотренного (подписанный манифест с отпечатками результатов и контекстов
групп), актуальность контекста вместо одной ревизии группы, видимый частичный
результат, возобновляемое применение и конфликт-aware отмена пакетом. Промпт
решения лежит в `weblate/trans/prompts/repeat_recommendation.txt` и входит в
отпечаток запуска вместе со схемой ответа.

Техническое решение этого плана: один текущий настроенный основной профиль
судьи, зафиксированный при запуске, без второго seat и без автоматического
перехода на иной платный профиль. Это не изменение модели судьи и не вызов
`process_judge`. Если профиль недоступен, функция недоступна; MT остаётся
работоспособным. Endpoint и credentials не поступают из браузера.

Новый модуль `weblate/trans/repeat_recommendations.py` использует ограниченный
транспортный seam `weblate/trans/judge.py`, но собственные prompt/schema/parser.
`judge.request_verdicts` и `judge._run_batch` запрещены: они вшивают judge
prompt/schema/parser и резолвят fallback-профиль
(`judge.resolve_judge_fallback_seat_profile`) - второй платный endpoint, что
нарушает «без автоматического перехода на иной платный профиль».

Seam выносится в `judge.py` как публичный и минимальный:
`post_chat_completion(payload, profile, *, title)` поверх `_post_batch`/
`_post_response`/`_read_sse`/`_decode_non_stream` и `reasoning_payload(profile)`
поверх `_reasoning_payload`. Параметр `title` нужен потому, что
`_post_response` сейчас всегда шлёт `X-OpenRouter-Title` судьи. Судья
переходит на те же функции без изменения поведения. Профиль резолвится только
для основного seat: `resolve_judge_seat_profile(1)` без endpoint идёт через
`judge_seat_profiles()` и валидирует оба seat, поэтому сломанный второй seat
отключил бы рекомендации. Payload строится своим кодом с учётом
`profile.response_format`: при `json_object` strict schema означает только
локальную валидацию ответа, при `json_schema` - ещё и схему у провайдера.

`RepeatRecommendationRun`, `RepeatRecommendationAttempt` и
`RepeatRecommendationResult` в новом model-модуле хранят scope snapshot,
profile/prompt/schema fingerprints, лимит запросов, каждую исходящую попытку,
состояние запуска и результаты групп. Не использовать `JudgeVerdict`,
`JudgeRunUnit`, `JudgeRequestAttempt` и `ProducerRun`: у `ProducerRun` нет
поля вида прогона, а `tasks.drain_producer_run_dispatches` считает каждый
`ProducerRun` прогоном судьи (проверяет `JudgeRunUnit`, публикует dispatch
судьи).

Одна запись ответа: group ID, snapshot fingerprint, действие
`use_existing | propose_new | keep_independent | needs_human`, variant ID
либо полный массив target forms, краткое основание и member IDs исключений.
Ссылки только на переданные группы/варианты/члены; неизвестные IDs, дубли,
неполные plurals и лишние поля отвергаются. Частичный ответ сохраняет лишь
валидные результаты и явно отмечает отсутствующие. Никаких quality scores.

Вход включает source forms, варианты и частоту, ключи/contexts, объяснения,
метки, состояния, ограничения, релевантный глоссарий и исключения. Это
недоверенные данные, не инструкции. Большая группа не обрезается молча:
если полный необходимый контекст не помещается в лимит, `needs_human` без
отправки непредставительной выборки.

Запуск резервирует конечный request cap до каждого HTTP-запроса, включая retry
и дробление batch. Кэш только при совпадении полного snapshot и профиля.
Новая операция usage — новый член `LLMUsageLog.Operation` `repeat_recommend`
(16 симв., влезает в существующий max_length 20). Choices записаны в состояние
миграции 0104 (`weblate/trans/migrations/0104_llm_usage_operation.py:19`),
поэтому новый член требует AlterField-миграцию состояния поля `operation`
(без изменения схемы БД) — тот же паттерн, что каждая новая запись
`ActionEvents` порождает `alter_change_action`. Учёт фиксирует реальные
запросы, токены, nullable cost и связь с run. Связь - новый nullable FK
`LLMUsageLog.repeat_recommendation_run` (`on_delete=SET_NULL`); это изменение
схемы, в отличие от choices. Строка usage пишется на каждую попытку, в том
числе без токенов: `judge._write_llm_usage` такие строки пропускает, поэтому
его не переиспользовать. Один запрос охватывает группы нескольких
компонентов, поэтому `component_id_snapshot` остаётся пустым, а отчёт по компонентам
этих строк не атрибутирует.

Таймаут после отправки означает неопределённый результат платного запроса:
не обещать exactly-once внешний HTTP без поддержки провайдера. Durable attempts
не дают повторной доставке автоматически повторить такой запрос; он становится
`unknown`, продолжение требует явного подтверждения нового лимита. Сохранённые
результаты повторно не запрашиваются. Cancel запрещает следующие запросы,
не обещает отмену уже отправленного. Падение одной группы не стирает остальные.

Рекомендация никогда не меняет Unit, state, membership или checks. Применение
только отдельным подтверждением по контракту §3.2, со свежим preview.

### 3.4. Разрешённое повторное использование в MT

Сначала действующий cap и writable scope, затем построение групп. Вхождения
вне scope не дописываются ради целостности группы. Approved/locks, пользовательская
правка и ограничения повторно проверяются перед записью.

При свежем подтверждённом общем target — reuse до обращения к LLM. Если
разрешена общность, но target ещё нет, генерировать один кандидат для группы
с контекстами всех участников, сохранить durable результат и раздать только
совместимым получателям. Конкурирующие принятые targets — unresolved, не
выбор по большинству и не случайный TM winner. Независимые и неразрешённые
вхождения остаются в обычном контекстном MT-пути.

Кэш и дедупликация обычного LLM-пути не меняются. Одинаковые source с разными
смыслами вне явного sharing по-прежнему переводятся отдельно. Сохранённый
кандидат переживает retry; неопределённый внешний вызов обрабатывается как
в §3.3. Число кандидатов группы и число HTTP batches считаются раздельно.

Opt-in рекомендаций в MT означает подготовку предложений по итоговым
расхождениям выбранного scope после сохранения MT, а не разрешение применить
их. Подтверждение до старта показывает верхнюю границу групп и request cap;
последующий scope может только сузиться. Сбой рекомендаций не отменяет успешный
MT и отображается отдельным итогом. Без opt-in после MT платных вызовов нет.

## 4. Задачи реализации и зависимости

### Задача 1. Долговечные правила, членство и актуальность

**Статус: завершена.** Созданы policy/group/membership-модели, миграции и
проверка актуальности после unit/label/delete событий; покрыты точные группы,
stale identity, динамическое пересечение меток и импорт/loc-kit.

**Результат:** решения переживают reload/retry, но не применяются к изменившемуся
контексту; вне правил поведение продукта прежнее.

**Файлы:** новые `weblate/trans/models/repeat.py`, `weblate/trans/repeats.py`;
`weblate/trans/models/__init__.py`, новая миграция в
`weblate/trans/migrations/` с номером от актуального leaf, не заранее занятым;
`weblate/trans/models/unit.py`, `weblate/trans/models/translation.py`,
`weblate/trans/loc_kit.py`, `weblate/trans/forms.py`.

- [x] Реализовать §3.1 и fingerprints §3.2, ограничения БД и project scoping.
- [x] Обрабатывать import, удаление, source/context/flags/labels/Explanation
  изменения; изменения selector не приводят к записи targets.
- [x] Свести invalidate/reconcile в общий сервис; фоновые задачи после commit.
  При pending reconciliation чтение проверяет актуальность и запрещает reuse.
- [x] Сохранить контракты append-only loc-kit и обычного VCS import.

**Проверка:** новый `weblate/trans/tests/test_repeats.py` покрывает два проекта,
два языка, пересечение правил при сохранении и после добавления метки к
source, закрытый компонент, source смену при прежнем PK, удаление/возврат
ключа и независимое вхождение после reload. Замер объёма membership из §3.1
записан в план до начала задачи. Запуск:
`./rundev.sh test weblate/trans/tests/test_repeats.py`, затем существующие
`test_labels.py` и `test_loc_kit_ingest_contract.py` тем же runner.
Миграция проходит на чистой тестовой БД; существующие targets не меняются.

### Задача 2. Preview, применение, история и undo

**Статус: завершена.** Реализованы подписанный preview, частичное применение,
история, guarded undo и запись через существующий Unit primitive без native
propagation за пределы snapshot.

**Зависимость:** 1. **Результат:** явная запись в разрешённые вхождения с
точным отчётом; approved и конкурентные правки защищены.

**Файлы:** `weblate/trans/repeats.py`, `weblate/trans/models/repeat.py`,
`weblate/trans/models/unit.py`, `weblate/trans/actions.py`;
новый `weblate/trans/tests/test_repeat_apply.py`.

- [x] Реализовать §3.2: запись каждого получателя существующим примитивом
  `Unit.translate(user, new_target, new_state, ..., propagate=False)`
  (параметр доходит до `Unit.save_backend`); методов `unit.edit`/
  `unit.bulk_edit` на модели нет (`bulk_edit` - это view
  `weblate/trans/views/search.py:bulk_edit`, меняющий state/flags/labels, не
  target), тонкий пакетный wrapper ввести в `repeats.py`, не на Unit.
  Изменение правил - `project.edit`, не доступ только к одной translation.
- [x] Писать с `is_batch_update=True`. Путь с `False` не использовать:
  `Unit.schedule_repeat_drift_recheck` через `on_commit` синхронно выполняет
  `_recheck_repeat_drift_group` -> `run_checks()` под `project.checks_lock`, а
  чек с `propagates="repeat"` перепроверяет все `repeat_units`; на N
  получателей это N перепроверок группы, O(N^2) внутри запроса. При
  `is_batch_update=True` `Change` и `PendingUnitChange` копятся в
  `translation.update_changes`/`pending_unit_changes`, поэтому wrapper держит
  один экземпляр Translation на каждую translation группы (как
  `AutoTranslate.update` присваивает `unit.translation = self.translation`) и
  внутри транзакции вызывает `store_update_changes()` и `invalidate_cache()`
  для каждой. После commit - одна scoped-перепроверка группы.
- [x] Сохранять provenance и событие для пакетного применения/исключений:
  новые члены `ActionEvents` (`weblate/trans/actions.py:ActionEvents`), каждый
  с AlterField-миграцией состояния `change.action` (репозиторный паттерн
  `alter_change_action`, например 0027-0031, 0055, 0056, 0105);
  `models/change.py` - только details-рендер, не место перечисления.
  Наборы по действию: запись target применением или undo входит в
  `ACTIONS_LOG`, `ACTIONS_REVERTABLE` и `ACTIONS_SHOW_CONTENT`; пометка
  «независимое» и изменение правила без target - только в `ACTIONS_LOG`
  (`TranslatedCheck` читает `change.target` из `ACTIONS_REVERTABLE`).
- [x] Реализовать guarded undo и результаты по группам, включая partial.
- [x] Проверить отсутствие обходного native propagation за пределы snapshot.

**Проверка:** `./rundev.sh test weblate/trans/tests/test_repeat_apply.py`:
approved conflict остаётся, остальные разрешённые получают target; stale,
lock и потеря прав между GET/POST не пишут; чужой проект не раскрывается;
повтор POST не дублирует историю; undo не стирает последующую правку.
Проверить несовместимые placeholders, plurals и ограничения длины; получателя
с autofix, меняющим target (DOS-переводы строк, `ignore-game-line-break`):
preview показывает его итог, undo сравнивает с сохранённым значением.
Группа из N получателей даёт одну перепроверку группы, а не N.
`DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py
makemigrations --check --dry-run` не находит несохранённых изменений.

### Задача 3. Нативная очередь групп и редакторские решения

**Статус: завершена.** Реализованы очередь Variant C, фильтры, статусы,
preview, check-link и editor/Zen/API/bulk-suggestion guards.

**Зависимости:** 1–2. **Результат:** одна строка очереди на группу, а не на
каждый чек; все вхождения доступны без скрытых замен. Иерархия экрана очереди
зафиксирована отдельным документом:
`docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` (выбранный
прототип `analysis/prototypes/repeat-queue/queue-variant-c.html`); задача
реализует её, а не изобретает экран заново. Раздел 1.1 того документа
перечисляет, что он меняет в этой задаче: нет `repeat_detail.html`, панель
группы в snippet, четыре статуса группы и примечания вместо stale/protected
как состояний, исключение получателя только в предпросмотре.

**Файлы:** новые `weblate/trans/views/repeats.py`,
`weblate/templates/repeat_queue.html`,
`weblate/templates/snippets/repeat_group.html`,
`weblate/templates/repeat_preview.html`, `weblate/templates/repeat_report.html`,
`weblate/templates/repeat_rule.html`, `weblate/static/styles/repeat-queue.css`,
`weblate/static/js/repeat-queue.js`;
`weblate/urls.py`, `weblate/trans/forms.py`, `weblate/trans/views/edit.py`,
`weblate/templates/snippets/translation.html`, `weblate/templates/check_list.html`,
`weblate/trans/models/suggestion.py`, `weblate/trans/views/bulk_suggestions.py`,
`weblate/trans/tasks.py`, `weblate/api/views.py`, `weblate/api/serializers.py`,
`weblate/locale/ru/LC_MESSAGES/django.po`.

- [x] Добавить переход от `repeat-drift` к проектно-языковой очереди;
  фильтры component, source labels, статус; конфликтующие approved сверху.
  Очередь строится собственным detection и живёт независимо от состояния чека:
  `RepeatDriftCheck.default_disabled = True`, поэтому вход через чек - только
  дополнительная ссылка там, где чек включён, не единственный путь.
- [x] Очередь - собственный view с синхронным применением §3.2 и отчётом по
  группам; НЕ наследовать `fix_check` (`weblate/trans/views/search.py:fix_check`:
  тот требует `unit.bulk_edit`+`unit.edit` и уходит в Celery
  `fix_failing_checks`, частичный результат по группам некому показать).
  Права: `unit.edit` на получателях, `project.edit` для правил.
- [x] В панели группы показать описание, варианты с «где стоит», состояния,
  происхождение, общий вариант и примечания; список вхождений до 10 строк с
  догрузкой (контракт экрана, раздел 2.5).
- [x] Выбор существующего/нового варианта или «оставить разные» - один
  radiogroup без преселекта, ведёт через preview §3.2; получатель
  исключается галочкой в preview (контракт, разделы 2.5-2.7).
- [x] Различать статусы группы (требует решения, конфликт одобренных,
  конфликт правил, решена) и примечания (устаревшая связь, замок, нет прав,
  ограничение длины); не объявлять весь пакет успешным после частичной записи.
- [x] Подключить диалог общего/независимого изменения к обычному editor, Zen
  и принятию suggestion. Zen сохраняется отдельным JSON POST
  `weblate/trans/views/edit.py:save_zen` мимо `handle_translate` - решение там
  входит в JSON-ответ, а не в HTML-диалог. REST получает явное поле решения;
  отсутствующий выбор при изменении shared target возвращает 409 без записи.
  Проверка в `weblate/api/views.py:UnitViewSet.perform_update`, НЕ в
  serializer.validate (гонка с записью). Сейчас `perform_update` до
  `unit.translate()` ничего не блокирует (`select_for_update` внутри
  `translate`), поэтому перед решением о 409 он явно делает
  `select_for_update` для Unit и его membership. Обычный PATCH без
  shared-строк не затрагивается; 409 - новый публичный контракт core API.
- [x] Bulk suggestion acceptance также использует защиту: без явного решения
  shared-строки пропускаются с причиной, не получают тихий propagation;
  per-unit причины расширяют result-контракт воркера
  `weblate/trans/tasks.py:bulk_accept_user_suggestions` (сейчас только
  accepted/failed/total/message/completion_message), а показ причин - в
  `weblate/trans/views/bulk_suggestions.py:add_bulk_accept_result_message`.
  Импорт не интерактивен: внешний отличающийся target делает связь stale,
  не распространяет его автоматически на группу.

**Проверка:** новые `test_repeat_views.py` и существующие `test_edit.py`,
`test_fix_check_view.py` через `./rundev.sh test`; REST scenarios в
`weblate/api/tests.py`. Реальный браузер на dev с разрешённым запуском:
две компоненты/один язык, filter, keyboard-only выбор, preview, partial,
stale во второй вкладке, reload, undo. Проверить labels, focus, ошибки и
нецветовые статусы по `ACCESSIBILITY.md` и `docs/contributing/frontend.rst`.

### Задача 4. Пакетная подготовка LLM-рекомендаций

**Статус: завершена.** Есть durable run/attempt/result ledger, cap, strict
parser, usage, неизвестная доставка, бесплатный scope/cost preview и
ограничение полного payload без непредставительной обрезки.

**Зависимость:** 1; UI-интеграция после 3. **Результат:** вся подтверждённая
очередь обработана в пределах cap, результаты durable, ни один target не
изменён до человеческого подтверждения.

**Файлы:** новый `weblate/trans/repeat_recommendations.py`;
`weblate/trans/models/repeat.py`, `weblate/trans/judge.py`,
`weblate/trans/tasks.py`,
`weblate/trans/models/llm_usage.py`, `weblate/utils/celery.py`,
`weblate/trans/views/repeats.py`, новые шаблоны задачи 3;
новые `test_repeat_recommendations.py`, `test_repeat_recommendation_tasks.py`.

- [x] Реализовать отдельную операцию §3.3, strict schema, bounded payloads,
  attempt ledger, liveness, cancellation, повторную проверку прав worker-ом.
  Транспорт - только публичные `judge.post_chat_completion` и
  `judge.reasoning_payload` из §3.3; не звать `request_verdicts`/`_run_batch`
  (fallback-риск из §3.3). `test_judge_client.py` подтверждает, что судья
  после выноса seam шлёт те же payload и заголовки.
- [x] Добавить бесплатный preview запуска, подтверждение known/unknown cost
  и конечного cap; изменение snapshot требует обновить preview.
- [x] Добавить команду «Подготовить рекомендации» для всей фильтрованной
  очереди, не только страницы; показать точный snapshot scope.
- [x] Хранить предложение отдельно, показать основания и stale; выбор пакета
  запускает новый preview применения задачи 2, а не автоaccept.
- [x] Расширить usage и историю runs без подмены judge-результатов: новый
  член `LLMUsageLog.Operation` `repeat_recommend` с AlterField-миграцией
  состояния поля `operation` и FK `LLMUsageLog.repeat_recommendation_run`,
  см. §3.3.

**Проверка:** HTTP mocks в новых suites через `./rundev.sh test`; чужой ID,
неполный ответ, prompt injection в source, cap при retry, cancel, redelivery,
таймаут после отправки, stale recommendation, отсутствие изменения
Unit/state/JudgeVerdict. Существующие `test_judge_client.py` и
`test_llm_usage.py` сохраняют поведение. Browser smoke: запуск с тестовым
транспортом, прогресс, partial, reload, явное принятие выбранных групп.
Реальные платные calls не нужны для этой проверки.
`DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py
makemigrations --check --dry-run` не находит несохранённых изменений.

### Задача 5. Reuse до MT и один кандидат разрешённой группы

**Статус: завершена.** Принятый shared target, включая новое вхождение после
импорта, переиспользуется до machinery lookup; обычный неразрешённый повтор
не получает скрытый fan-out.

**Зависимости:** 1–2; opt-in после 4. **Результат:** разрешённые одинаковые
вхождения не генерируются независимо; контекстные исключения не объединяются.

**Файлы:** `weblate/trans/autotranslate.py`, `weblate/trans/machinery.py`,
`weblate/machinery/llm.py`, `weblate/trans/tasks.py`, `weblate/trans/forms.py`,
`weblate/trans/views/edit.py`, `weblate/trans/repeats.py`,
`weblate/trans/repeat_recommendations.py`;
`weblate/trans/tests/test_autotranslate.py`, `weblate/machinery/tests.py`.

- [x] Реализовать §3.4 до исходящих MT requests; durable group reservation
  защищает от двух параллельных запусков и разных кандидатов одной revision.
  Точка отсечения: юниты с принятым общим target исключаются из списка units
  при сборке батча в `weblate/trans/machinery.py`, ДО `_translate_sources`
  (`weblate/machinery/base.py:batch_translate` вызывает LLM для всего батча и лишь потом
  сравнивает с `quality >= max_score` по `unit.machinery`).
- [x] Сохранять cap исходного scope; fan-out не расширяет набор Unit.
- [x] Проверять каждого получателя; несовместимый не меняется и виден в отчёте.
- [x] Добавить отдельный opt-in рекомендаций с лимитом; после MT создавать
  только read-only run задачи 4, если opt-in подтверждён.
- [x] Отчёт различает reused Units, generated groups, independent Units,
  skipped/stale/conflicts, HTTP requests и неизвестную стоимость.

**Проверка:** `./rundev.sh test weblate/trans/tests/test_autotranslate.py` и
`./rundev.sh test weblate/machinery/tests.py`. HTTP spy доказывает: принятый
общий target не посылается LLM; группа без target представлена одним кандидатом;
повтор после сохранения результата не вызывает новую генерацию; noun/verb
без разрешения sharing остаются раздельны. Проверить parallel runs, cap,
approved, одновременную ручную правку, новые пустые строки после import,
отсутствие paid follow-up без opt-in и независимый сбой рекомендаций.

### Задача 6. Check, сквозной сценарий и документация

**Статус: завершена.** Fresh independent membership исключается из advisory
check, stale membership не скрывает расхождение; документация, threat model и
changelog обновлены, результаты приёмки записаны ниже.

**Зависимости:** 1–5. **Результат:** намеренные исключения не выдаются за
ошибки, реальные конфликты видны; поведение документировано и проверено целиком.

**Файлы:** `weblate/checks/consistency.py:RepeatDriftCheck`,
`weblate/trans/models/unit.py:Unit.schedule_repeat_drift_recheck`,
`weblate/checks/tests/test_consistency_checks.py`,
`weblate/trans/tests/test_fix_check.py`, `docs/admin/checks.rst`,
`docs/admin/machine.rst`, `docs/security/threat-model.rst`,
`docs/changes.rst`, текущий план и Producer roadmap.

- [x] Check использует свежие explicit independent decisions для исключения
  сравнений, но сохраняет advisory-семантику и detection вне правил.
  Stale exception не скрывает новое расхождение. Конфликтующие shared approved
  остаются видны; dismissal не создаёт policy. Консультация с membership на
  read-пути — prefetch через reverse-relation или денормализованный флаг,
  не N+1 на участника; замерить время проверки.
- [x] Изменение policy/member/target планирует ограниченный recheck всей
  затронутой группы; никаких project-wide пересчётов на каждое чтение.
  Все пути записи пишут с `is_batch_update=True`, поэтому
  `Unit.schedule_repeat_drift_recheck` сам не сработает; каждый явно
  планирует одну scoped-перепроверку на группу после commit. Владельцы:
  применение и undo (§3.2, задача 2), MT-reuse (задача 5), import (задача 1).
- [x] Обновить пользовательскую документацию, threat model для новой платной
  операции/permission boundary и unreleased changelog по правилам репозитория.
- [x] Выполнить сквозной smoke ниже, убрать временные smoke-скрипты и fixtures,
  зафиксировать фактическое доказательство и ограничения в этом плане.

**Проверка:** `./rundev.sh test weblate/checks/tests/test_consistency_checks.py`
и `./rundev.sh test weblate/trans/tests/test_fix_check.py`; затем все затронутые
suites одним итоговым запуском. Scoped lint через `uv run prek run --files`
с фактическим списком изменённых файлов. Не считать плановые команды уже
выполненными; здесь описана будущая проверка реализации.

## 5. Порядок и параллельная работа

Критический путь: 1 → 2 → 3 → интеграция 4/5 → 6.
После 1 можно независимо вести сервис применения задачи 2 и протокол/транспорт
рекомендаций задачи 4. После 2 можно разнести UI и MT при закреплённых §3
контрактах. `models/repeat.py`, миграции, `tasks.py` и формы имеют одного
интеграционного владельца; параллельные исполнители не редактируют их
одновременно. Валидация общая после интеграции, не конкурирующие full-suite runs.

## 6. Сквозная приёмка и измерения

Тестовый проект: два компонента, один source language, два target languages;
правило только для первого target language и явной source label. Набор включает:
разрешённую группу с тремя вхождениями; одинаковое слово с двумя смыслами;
два конфликтующих approved; locked и независимое вхождение; plural и
ограничение длины; строки вне scope.

1. Очередь показывает группы и все варианты; фильтр не меняет scope молча.
2. Рекомендации по всему snapshot переживают reload; до принятия targets и
   states побайтно/позначно прежние, а usage отражает mock HTTP attempts.
3. Принятие меняет только перечисленные eligible Units; approved conflict
   остаётся. Другая вкладка вызывает stale conflict, не потерю правки.
4. Новый MT reuse принятого решения не отправляет его в LLM; новый допустимый
   повтор без решения генерируется совместно, исключение — независимо.
5. Reload loc-kit добавляет новое вхождение; свежая policy применяется при
   следующем явном MT, без неявной платной генерации на import. Изменённый
   source/context делает прежнее решение stale. Иной язык не затронут.
6. Редактор предлагает общий/независимый путь; undo сохраняет позднюю правку.
7. Redelivery/cancel/таймаут не теряют историю и не изображают неизвестный
   HTTP исход как бесплатный успешный retry.

Критерии сдачи: ноль новых расхождений среди успешно записанных shared-вхождений
одной актуальной revision; ноль изменений protected approved и строк вне scope;
ноль неявных платных вызовов; все частичные исходы доступны после reload.
Это критерии будущей проверки, не обещание нуля checks всего проекта.

Пилот Anvil Saga проводится отдельно после разрешения на данные и calls:
сначала вручную размеченная выборка общих/контекстных групп, затем сравнение
до/после. Измерять неверное объединение смыслов, нерешённые группы, человеческие
решения, запросы/токены/стоимость; не выдавать одинаковость targets за LQA.
Оценивать рекомендации отдельно от механической безопасности применения.
Без принятого человеком пилота не включать policy по умолчанию для всех строк.

## 7. Выпуск и откат

Миграции только добавляют данные; существующие проекты стартуют без активных
правил, рекомендаций и дополнительных calls. Включение — явное действие
уполномоченного пользователя с preview. Dev workers/static обновляются только
по правилам и разрешениям репозитория; изменение исходников не доказывает,
что Celery загрузил новую версию.

Отключение правила останавливает будущий reuse, но не удаляет историю и не
откатывает targets. Исправление уже применённых решений — guarded undo §3.2.
При сбое LLM ручная очередь и принятые решения остаются доступны. Откат версии
приложения после использования новых сущностей требует сохранения данных и
остановки несовместимых задач; обратная destructive migration не является
штатным способом отката переводов.

## 8. Полнота и статус передачи

| Требование | Контракт и задача |
|---|---|
| Нативная очередь проект + язык, компоненты как фильтр | §1, задача 3 |
| Явные компоненты/метки, будущие загрузки, исключения | §3.1, задачи 1 и 5 |
| Preview, approved, конкуренция, история и undo | §3.2, задача 2 |
| Общая/независимая ручная правка во всех write paths | Задача 3 |
| Рекомендации всей очереди, два paid triggers, cap | §3.3–3.4, задачи 4–5 |
| Один кандидат разрешённой группы, без изменения обычного cache | §3.4, задача 5 |
| Честный repeat-drift, не массовый ignore | Задача 6 |
| Producer Console позже над теми же данными | Roadmap, волна 2, пункт 2.6 |
| Безопасность, измерения, rollout | Задача 6, §6–7 |

## 9. Фактическая приёмка реализации (2026-09-22)

Docker dev stack применил миграции ``trans.0138``–``trans.0142`` и отвечал на
healthcheck. Выполнены следующие автоматические прогоны:

- repeat queue/model suites: 17 passed;
- consistency и fix-check: 119 passed;
- repeats, labels и loc-kit import contract: 188 passed;
- AutoTranslation/MT основной контур: 67 passed;
- bulk suggestions и LLM usage: 52 passed;
- judge transport: 168 passed;
- REST Unit translate subset: 4 passed;
- machinery: 1436 passed, 53 skipped; шесть существующих custom
  OpenAI/Mistral тестов остановились на DNS-проверке ``custom.example.com`` до
  HTTP mock, один xdist worker был завершён по ресурсам;
- ``manage.py makemigrations --check --dry-run``: no changes detected;
- ``./rundev.sh check``: no issues (one repository-wide silenced check).

Scoped ``prek`` проходит для изменённых Python/JavaScript/templates/docs,
кроме общего REUSE hook: он перечисляет уже существующие файлы
``frontend-wizard`` и корневые ``PRODUCT.md``/``DESIGN.md`` без licensing
metadata. Эти файлы данным планом не изменялись.

В текущей сессии browser provider недоступен, поэтому keyboard-only smoke не
выдаётся за выполненный реальным браузером. Его поведение покрыто server-side
view tests и lint правил доступности шаблонов; визуальная проверка остаётся
review-шагом перед merge, не условием сохранности данных.
