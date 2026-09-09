<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Массовое исправление пунктуации для продюсера

## Цель, статус и границы

**Дата:** 2026-09-09. **Статус:** на согласовании; реализация и deployment не разрешены. Пользователь разрешил исследование, составление плана и read-only проверку двух языков Anvil Saga на production.

Продюсер видит, какие строки можно исправить, просматривает изменения и одной операцией применяет одинаковое исправление ко всей подходящей выборке. Кнопка не обещает исправление там, где программа умеет только показать ошибку.

План дорабатывает **существующие** `fix_check`, `fix_failing_checks`, lock, preview и task poller. По замечанию пользователя из первоначального варианта удалены две модели запусков/строк, семидневная история, задачи очистки/восстановления и отдельный жизненный цикл операций. Это локальная доработка массового исправления, не новая платформа фоновых запусков.

Основание: обсуждение CoL4/data/fr, затем проверка Anvil Saga/en и zh_Hans. Правила: `AGENTS.md`, `ACCESSIBILITY.md`, `docs/contributing/frontend.rst`. Исторический план: `docs/product/plans/2026-08-25-mass-fix-failing-checks.md`. Действующий контракт: `docs/admin/checks.rst`, раздел `mass-fix-failing-checks`.

**В объёме:** обратное направление для `end_stop`/`end_exclamation` через уже существующий `RemoveAddedFinalStop`; честные входы/счётчики/исключения; применение всего автоисправимого набора; сохранение защиты просмотренных строк; достоверные сообщения о сбоях.

**Вне объёма:** новые модели/migrations/tasks/REST endpoints, долговременная история и восстановление запусков, LLM/rejudge, массовая отмена, смена правил проверок, CJK-удаление, замена одного terminal-знака другим, удаление `?`/`:`, принудительное исправление/игнорирование всех ошибок. Существующие safe-проверки и source-cosmetic сценарии не переводятся на новую подсистему. Существующие review-добавления остаются только для выбранных показанных строк, максимум 250 за применение.

## Что сломано сейчас

- `weblate/templates/snippets/translation.html` показывает «Fix» по `mass_fixup`, без расчёта применимости и без обоих scope-разрешений. `weblate/templates/check_list.html` имеет такой же разрыв. Счётчик ошибок не является счётчиком исправлений.
- `weblate/trans/fix_check.py:_classify` возвращает `no_fixup` **до** autofix-прохода. Терминальные `get_fixup` в `weblate/checks/chars.py` предлагают только добавление потерянного знака.
- Удаление уже существует: `weblate_customization/src/weblate_customization/autofixes.py:RemoveAddedFinalStop`, `fix_id="removed-final-stop"`. Алгоритм удаляет только одиночные ASCII `.`/`!`, защищает многоточия/двойные знаки и учитывает знак за закрывающей кавычкой/тегом исходника. Target-кавычки не разворачивает, `?`/`:` не удаляет.
- `_resolve_fix_check_selection` защищает review signed cohort из максимум 250 строк. Разрешить этому пути произвольное `all` означало бы убрать проверку просмотра, а не просто улучшить пагинацию.
- `fix_failing_checks`/`perform_fix` уже имеют scoped lock, row lock, обычный путь записи, batched checks и счётчики. У progress есть конкретный недостаток: пропуски не всегда увеличивают processed. При исключении после предыдущего committed компонента нельзя выдавать нулевой fixed как точный результат.

## Измеренное покрытие

### CoL4/data/fr

Локальный REST API прочитан без изменения переводов. Из 2 092 `end_stop` все targets заканчиваются ASCII-точкой, sources — нет; три targets заканчиваются `...`. Среди семи `end_exclamation` шесть имеют добавленный конечный `!`; один source заканчивается `с криком "Еретик!"`, этот знак сохраняется. Это классификация текста, не обещание ровно 2 089 разрешённых записей: нормализация/права/флаги могут исключить дополнительные строки.

DOM-инспекция Lightpanda обнаружила `/fix-check/end_stop/col4/data/fr/` в полученной странице. Это не опровергает отсутствие заметной кнопки в сессии пользователя: визуальная доступность и его авторизованная сессия этим не проверены.

### Production Anvil Saga

Компонент: `anvil-saga/locale_v1-02-import-explained-3`, по 9 482 строки на язык. Container image revision `8d5e7eca71e7c4cada9994ffd7b3ae0802f0c856`; digest `sha256:4445c7c3243296db15e4a15dcfa9a5459d75f790a5f582247e10c81f6e8bb4ba`. В production-образе **нет** `weblate.trans.fix_check`; `removed-final-stop` активен. Код main не равен работающему образу.

Метод: `./deploy/vps.sh ssh` → отдельный `docker exec` Python, `PYTHONDONTWRITEBYTECODE=1`, `PGOPTIONS=-c default_transaction_read_only=on`; PostgreSQL подтвердил `on`. Все активные `Check(dismissed=False)` сопоставлены с локальными `chars.py` и чистыми `apply_fixup_python`, `_compute_final_target`, `_failing_checks`, `_decision_7_holds`, исполнявшимися только в памяти отдельного процесса с production-моделями и активными autofix. Не вызывались `translate`, `save`, `run_checks`, backfill, Celery/deployment; код/файлы работающего приложения не заменялись.

| Язык / проверка | Активных ошибок | Новое удаление | Добавление после просмотра | Существующее safe |
| --- | ---: | ---: | ---: | ---: |
| en: `end_stop` | 470 | 17 | 79 | 0 |
| en: `end_exclamation` | 342 | 0 | 14 | 0 |
| en: `end_question` | 150 | 0 | 1 | 0 |
| en: `end_interrobang` | 24 | 0 | 0 | 0 |
| en: `end_colon` | 1 | 0 | 0 | 0 |
| en: `double_space` | 2 | 0 | 0 | 2 |
| zh_Hans: `end_stop` | 880 | 0 | 249 | 0 |
| zh_Hans: `end_exclamation` | 586 | 0 | 17 | 0 |
| zh_Hans: `end_question` | 183 | 0 | 5 | 0 |
| zh_Hans: `end_interrobang` | 53 | 0 | 0 | 0 |
| zh_Hans: `double_space` | 1 | 0 | 0 | 1 |

Итог en: 1 320 ошибок на 847 уникальных строках; положительная проекция у 113 уникальных строк. zh_Hans: 1 744 ошибки на 1 060 строках; проекция у 272 строк. Это **не** число полностью чистых строк: другие проверки могут остаться. Права конкретного продюсера не проверялись. Это измерение алгоритмического покрытия, не применение и не тест новой реализации.

Не покрываются:

- en: `game-length` 289, `duplicate` 16, `multiple_capital` 12, `game-number` 5, `end_space` 4, `reused` 2, `cyrillic-leak` 2, `game-markup` 1.
- zh_Hans: `game-number` 14, `duplicate` 8, `multiple_capital` 6, `game-markup` 5, `end_space` 4, `xml-tags` 2, `end_ellipsis` 1, `begin_space` 1.
- Остальные terminal-ошибки требуют замены знака, не имеют предложения или защищены guards/normalization. У en отдельно исключены четыре потенциальных удаления из-за полного autofix-прохода: одно с точкой и три с восклицанием, в примерах source имеет конечный пробел.
- Китайские `。`/`！` не входят в ASCII-only удаление; язык учитывается при добавлении, но смена UI не расширяет алгоритм удаления.
- Есть смысловое расхождение: en unit 460578 — исходник о надёжном заказчике, перевод `The young lady next to him is his daughter, Olivia.`. Причина не расследована; нельзя объявлять ошибку импорта. Замена punctuation не исправит смысл.

**Вывод:** план решает тупик CoL4 и даёт ограниченное покрытие Anvil Saga, а не «исправляет всё». Расширение на CJK/замену знаков/смысл — отдельное решение после этой оценки, не скрытая часть реализации.

### Прирост относительно уже реализованного массового механизма

Дополнительно тем же read-only методом проверены все остальные языки проекта. В Anvil Saga один строковый компонент и глоссарий без неудачных проверок. Сравнение ниже — с существующим массовым механизмом из main **до этой доработки**, не с фактически развёрнутым старым образом, где этого экрана ещё нет. «Авто» означает без обязательного построчного просмотра, но с подтверждением продюсера.

| Перевод | Активных ошибок | Авто до | Авто после | Прирост |
| --- | ---: | ---: | ---: | ---: |
| en | 1 320 | 2 | 19 | +17 |
| ja | 1 481 | 0 | 0 | 0 |
| ko | 1 339 | 2 | 55 | +53 |
| zh_Hant | 1 742 | 0 | 0 | 0 |
| zh_Hans | 1 744 | 1 | 1 | 0 |
| **Все переводы** | **7 626** | **5** | **75** | **+70** |

Доля автоисправимых ошибок переводов: **0,07% → 0,98%, +0,92 процентного пункта**. Все 70 новых случаев — удаление добавленной ASCII-точки; новых автоисправлений восклицаний проекция не нашла.

Если считать также добавления с обязательным просмотром каждой строки, существующий механизм даёт ещё 902 предложения: en 94, ja 132, ko 134, zh_Hant 271, zh_Hans 271. Общее покрытие действий **907/7 626 = 11,89% → 977/7 626 = 12,81%**. Эти 902 предложения нельзя называть автоисправлениями.

В русском исходнике отдельно 2 008 проверок: `ellipsis` 711 и `multiple_failures` 1 297. Чистая проекция существующего `EllipsisCheck` исправляет все 711 `ellipsis` до и после этого плана; новые операции исходник не расширяют. По всему проекту, включая исходник, авто-покрытие **716/9 634 = 7,43% → 786/9 634 = 8,16%, +0,73 п.п.** Это алгоритмическая оценка при необходимых правах, не утверждение о доступности source bulk-edit конкретному пользователю.

Способность `removed-final-stop` исправлять эти 70 случаев уже существует при сохранении/операторском backfill. План добавляет доступ к ней из массового UI, а не новый алгоритм. Для заметного роста покрытия Anvil Saga нужен отдельный согласованный объём по неподдержанным случаям; текущий план прежде всего устраняет UX-тупик.

## Сценарий продюсера

Один существующий экран, режим Operate, Bootstrap/jQuery без редизайна приложения.

1. В списке ошибок — **«Посмотреть исправления»**. Рядом число ошибок, не обещание изменить столько строк. Не вычислять eligible для всех проверок при открытии таблицы: анализ выполняется на существующем GET экрана исправления.
2. Заголовок проверки и заметная область `проект / компонент / язык`; для component/project явно указать, что действие затронет несколько переводов. Все точки входа учитывают доступность операции и scope-права.
3. Разделы: **«Можно исправить автоматически»**, **«Выберите строки после просмотра»**, **«Не изменяем»**. Источник, текущий target и полный before/after diff доступны до применения. Не подменять отсутствие реализации словами «требуется ручная проверка».
4. Автогруппа `Удалить добавленную конечную точку` / `Удалить добавленный восклицательный знак`: страницы по 250, возможность посмотреть весь набор. Две явные альтернативы: **«Применить ко всем N подходящим строкам»** и **«Применить выбранные на этой странице (N)»**. Первое означает все auto-eligible строки снимка, не только текущую страницу. Никаких предварительно выбранных checkbox или молчаливого запуска.
5. Review-добавления: **«Выбрать все на этой странице»**, **«Применить выбранные (N)»** с фактическим selected count. Непоказанные строки никогда не входят в это действие; auto и review не объединяются одним checkbox/POST.
6. Неприменимые строки: короткая причина, source/target и ссылка к этим строкам. Разделить `нет предложения`, `конфликт другого знака/проверки`, `дополнительные изменения при сохранении`, `нет прав`, `provider недоступен`. Если provider не сообщает точную лингвистическую причину, не выдумывать её: «Не удалось получить безопасное исправление; текст оставлен без изменений».
7. Перед применением: реальный N, отсутствие массовой отмены, число устаревающих verdicts. При eligible=0 нет Apply и предупреждения об отмене; вместо тупика — причины и переход к оставшимся строкам. Не писать «никогда не станут доступны»/«пересохраните каждую».
8. После POST — существующий task flash/poller: очередь, `Обработано X из N`, изменено/пропущено/ошибка. Reload продолжающейся задачи использует существующий `add_user_task`; отдельной истории результатов не будет. При неизвестном исходе — **«Не удалось получить итог. Часть строк могла измениться. Проверьте оставшиеся ошибки»**, не успех и не выдуманный ноль.

На narrow-экране главное действие не теряется за последней колонкой. Native controls, keyboard/focus, доступная scroll-таблица, `aria-live` для динамического статуса. Русские строки через gettext, без смешения английских технических bucket names с пользовательскими причинами.

## Минимальные технические контракты

### Одна реализация удаления

В `weblate/trans/fix_check.py` добавить закрытый mapping `end_stop/end_exclamation → removed-final-stop`. Provider получать только из активного `AUTOFIXES`, проверить `get_related_checks`; не импортировать optional customization из core и не принимать имя класса/regex от клиента. Нет provider — объяснимый отказ, не fallback.

Предложение строится через `provider.fix_target(list(unit.get_target_plurals()), unit)`. Общий порядок plural/multivalue/autofix/DOS подготовки переиспользуется из `_compute_final_target` и сверяется с `Unit.translate`. Если полный save-проход добавляет изменения сверх результата выбранного provider, строка не входит в auto-all: `normalization_conflict`. Не отключать обычные autofix ради принудительной записи.

Сохранить `_decision_7_holds`: выбранная проверка очищается, не появляется новая terminal-проверка или `punctuation_spacing`. Разрешения, ignore-флаги, plural mapping, source/template/glossary/read-only остаются обязательными. Не создавать вторую таблицу Unicode/кавычек и не расширять `TERMINAL_MARKS`. Проверка before/after не доказывает сохранение смысла; UI не выдаёт исправленную пунктуацию за проверенный перевод.

### Снимок в существующем cache, без моделей

Новый небольшой внутренний контракт только для удаления; прочие safe/check-fixup payload не мигрировать без необходимости.

- При существующем GET анализа создать короткоживущий snapshot в общем Django cache, TTL один час, UUID + Django-signed handle. Запись в cache не меняет переводов и не запускает задачи. Pagination использует тот же handle; нельзя пересчитывать новый набор незаметно на каждой странице.
- Snapshot: actor ID, неизменяемые scope type/ID, check ID, operation `autofix-remove`, protocol version, ordered autofix fingerprint; для каждого candidate — `unit_id/translation_id/component_id/project_id/language_id`, digest исходного состояния и ожидаемого результата. Иерархию повторно сверять под lock: перемещение компонента в другой проект не должно расширять исходную область даже при правах actor в обоих проектах. Для исключений — IDs/reason в той же закрытой области. Тексты, токены/секреты и целые ORM objects в cache/очередь не складывать.
- Digest включает полные source/target plurals, state, `automatically_translated`, effective flags и влияющие на запись language/plural/template/component/file-format настройки. `get_source_plurals` читает `Unit.source`, поэтому source/target повторно читаются после Unit row lock. Ordered autofix IDs не являются версией алгоритма: включить explicit repair protocol version, повышать при изменении семантики и заново сравнивать computed-after digest при apply.
- Для preview страницы читать текущие разрешённые units и пересчитывать предложение, сверяя digest. Изменившуюся строку не показывать как прежнее подтверждённое исправление: отключить её выбор и предложить обновить анализ. Counts явно относятся к моменту подготовки.
- POST передаёт signed handle и `selection=all|page`. `all` берёт только auto-eligible IDs из snapshot. `page` дополнительно требует действующий actor/check/scope-bound signed cohort показанной страницы. Нельзя подменить actor/scope/check/operation или передать review IDs через auto-all.
- До показа auto-применения, взятия lock и enqueue проверить `is_redis_cache() or settings.CELERY_TASK_ALWAYS_EAGER`: только общий Redis либо заведомо однопроцессный eager. В обычном многопроцессном LocMem новый режим недоступен с объяснением; существующие safe/review пути остаются. Даже при выполнении этого условия cache miss в worker всегда fail-closed, никогда live-query fallback.
- Нет/expired/evicted snapshot при POST или до начала worker — **ничего не применять**, предложить заново открыть анализ. Worker получает signed/серверно проверенный snapshot reference, загружает его один раз перед работой; если задача простояла в очереди дольше TTL, это явная неуспешная попытка без записи. Автоочистка TTL уже принадлежит cache, новых cleanup tasks нет.
- Snapshot не история и не гарантия восстановления. После завершения/потери backend результат может исчезнуть; UI честно объясняет это. Данные snapshot/preview доступны только actor с текущим scope/language доступом; UUID не разрешение. Не включать скрытые переводы в текст/счётчики исключений.
- Повторный POST: атомарный `cache.add` claim по UUID snapshot с selection digest/task ID. Одинаковая selection возвращает тот же task, другая отклоняется. Claim хранится до expiry snapshot; enqueue error не удаляет чужой/неопределённый claim, пользователь начинает новый анализ. Это два короткоживущих cache records, не история запусков.

### Существующая задача и запись

Расширить `fix_failing_checks` явной operation/snapshot-reference веткой; check-fixup/review defaults сохраняют существующий контракт. Все HTTP/worker consumers мигрируются вместе, без deprecated aliases. Сигнатуры до изменения проверить LSP references.

- Сохранить lock `(check, scope)`, не добавлять независимый lock по operation. Actor/scope существует и имеет `unit.bulk_edit` + `unit.edit` при POST и старте worker; `unit.edit` для каждой строки проверяется перед записью. Нет anonymous/system fallback.
- Auto-ветка проходит только snapshot IDs, никогда `unit_ids=None` с новым live query. Row lock, свежие данные/флаги, active non-dismissed check, совпадение исходного и output digest. Changed/deleted/dismissed/denied/уже исправленная строка пропускается; не рассчитывать новую невиденную правку на изменённом тексте.
- Сохранить обычную компонентную транзакцию и batched checks, не переделывать механизм в resumable batches. Пагинация 250 ограничивает показ, не общее применение. Масштаб 2 092 проверяется реальным smoke; произвольный hard cap/молчаливое усечение не добавлять.
- Путь записи — `Unit.translate` с текущим state, `FIX_FAILING_CHECK`, `propagate=False`. Сохранить `automatically_translated` у Unit и PendingUnitChange внутри транзакции по имеющемуся примеру `reapply_autofixes.Command.repair`; этот флаг `translate` иначе сбрасывает. Не восстанавливать старый target/автора самой правки. Source, flags, labels, explanations и история judge не меняются; платного recheck нет.
- Не запускать CLI `reapply_autofixes`: он применяет все active fixes, пишет от bot и имеет отдельные repository lock/foreign-pending/commit safeguards. UI остаётся от имени пользователя; обычный scheduler обрабатывает pending, task сам не делает Git commit/push.
- Progress считает каждую обработанную выбранную строку, включая skip/denied; lease refresh не зависит от успешных изменений. Счётчик fixed увеличивается только после commit соответствующей компонентной транзакции; rollback текущего компонента не считается выполнением.
- Обрабатываемое исключение после предыдущих commits возвращает `status=failed`, `partial=true` и **известные committed** counters. Если точный результат потерян из-за worker/backend — `counts_known=false`/сообщение о неизвестном исходе вместо нулей. Для этого достаточно передать accumulated result из engine в task error path, не хранить outcomes в новой БД.
- Перед первой записью атомарно пометить snapshot как `mutation_started`; это одноразовый переход существующего cache record, не независимо вытесняемый ключ без связи со снимком. Ошибка/исчезновение cache — остановка до записи. Повторная доставка после этого перехода не продолжает изменения и сообщает unknown/failed: без persistent journal нельзя восстановить итог погибшей попытки. Существующий retry допустим только до начала mutation; потерянная lease — остановка без захвата чужого lock. Новый анализ уже исправленных строк идемпотентен.
- Сбой `run_batched_checks` после commit — failed/partial с известными counters и вызов существующего `component.schedule_update_checks`, не новая recovery-задача. При worker loss гарантии завершения bookkeeping нет; это часть unknown outcome. Не обещать автоматическое восстановление.
- Использовать существующие task metadata/API/poller, без нового status endpoint. Для новой auto-ветки передавать область через snapshot и записывать **user_id-only metadata**: `TasksViewSet.get_task` иначе выбирает translation/component раньше user и разрешает другому участнику читать результат. Проверить также `store_published_task_metadata`, чтобы signal не вернул scope metadata. Result не содержит before/after текстов/скрытых IDs. При 404/неизвестном результате `data-task-status` показывает неизвестный исход и ссылку к текущим ошибкам, не останавливает индикатор молча.

## Задачи реализации

### A. Подключить существующий autofix и разделить предложения

**Результат:** обратные `.`/`!` больше не попадают в тупик `no_fixup`; защищённые случаи остаются без изменения. Добавление — по-прежнему review.

**Файлы/интерфейсы:** `weblate/trans/fix_check.py:collect_fix_candidates/_classify/_compute_final_target`, существующие `AUTOFIXES`/`AutoFix.fix_target` в `weblate/trans/autofixes/`. Custom provider не переписывается.

- [ ] Ввести закрытый operation mapping и причины `eligible/no_change/guard_rejected/normalization_conflict/provider_unavailable/denied`.
- [ ] Переиспользовать общий normalization pipeline и guards; не дублировать punctuation/locale правила.
- [ ] Разделить auto-removal, review-append и невыбираемые причины; сохранить остальные safe/source сценарии.

**Проверка:** regression `source без знака` → `target.` до изменения даёт `no_fixup`, после — предложение `target`; реальное сохранение равно preview. `...`, `!!`, `"Еретик!"`, source `?`/target `.`, Chinese `。`/`！`, empty/plural/ignore/readonly/template/glossary не получают опасного удаления. Collateral autofix исключает auto-all. Команда после test setup: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_fix_check.py weblate/checks/tests/test_mass_fixup.py weblate/trans/tests/test_autofix.py`. Regression до/после; не tests на имя helper/точный текст.

### B. Применить всю подтверждённую выборку существующей задачей

**Зависимость:** A. **Результат:** один POST исправляет auto-eligible >250, не подхватывая новые совпадения и не обходя review.

**Файлы/интерфейсы:** `weblate/trans/fix_check.py` snapshot helper/`perform_fix`; `weblate/trans/views/search.py:fix_check/_resolve_fix_check_selection`; `weblate/trans/forms.py:FixCheckConfirmForm`; `weblate/trans/tasks.py:fix_failing_checks`. Никаких моделей/migrations/new tasks.

- [ ] Добавить cache snapshot/signing/TTL, pagination handle, actor/scope/operation validation и отказ при cache miss/non-shared cache.
- [ ] Протянуть auto operation/snapshot в существующую задачу; review retain signed shown-only IDs. Не отдавать snapshot payload браузеру как доверенную selection.
- [ ] Реализовать свежую проверку digests, прав и active checks под lock; сохранить state/machine-origin/pending/audit.
- [ ] Исправить progress всех обработанных строк и известные partial counters; неизвестные результаты не подменять нулём. Не добавлять recovery subsystem.

**Проверка:** 251+ candidates; создать snapshot, добавить новую 252-ю ошибку — её target не меняется. Поменять source/target/state/flags/config одного candidate — только он пропущен. Отозвать bulk permission до worker и per-unit edit перед записью — изменений нет. Чужой/expired/evicted handle, подмена check/operation/cohort, review-all, arbitrary IDs не enqueue/write. Duplicate delivery и overlapping scopes не создают вторую правку; state и автоматически/вручную переведённое происхождение сохранены. Второй компонент падает — первый остаётся committed, результат partial с точным known count, rollback не засчитан. Worker/backend loss — unknown, не success/zero. Все skipped всё равно завершают processed. Команда: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_fix_check.py weblate/trans/tests/test_fix_check_view.py weblate/trans/tests/test_fix_check_task.py`. Настоящие гонки — TransactionTestCase, не общая внешняя test transaction.

### C. Исправить входы, выбор и обратную связь

**Зависимость:** контракты A/B; окончательная интеграция после B. **Результат:** продюсер видит область, доступное действие и реальные исключения; zero-eligible не обманывает.

**Файлы:** `weblate/trans/checklists.py`, `weblate/checks/views.py`, при необходимости context редактора `weblate/trans/views/edit.py`; `weblate/templates/snippets/translation.html`, `weblate/templates/check_list.html`, `weblate/templates/translate.html`, `weblate/templates/fix_check.html`; `weblate/static/loader-bootstrap.js`, при необходимости существующий `weblate/static/styles/main.css`; `weblate/locale/ru/LC_MESSAGES/django.po` и `weblate/locale/ru/LC_MESSAGES/djangojs.po`.

- [ ] Обновить translation/check-list/editor входы и scope/language permission visibility. В редакторе групповой вход не должен зависеть от наличия индивидуального append-fixup.
- [ ] Добавить группы/причины, стабильную pagination auto-снимка, раздельные all/page/review действия и динамический фактический selected count. Исходный review-cohort остаётся ограниченным показанным набором.
- [ ] Показать исключения прямо на preview с source/target и доступными edit links; ссылку «Показать исключения» связывать с этой группой, не со всеми ошибками.
- [ ] Переиспользовать task flash/poller для done/skipped/partial/unknown; не добавлять историю. Не оставлять generic 404 молчаливым успехом.
- [ ] Native controls, keyboard/focus, длинные строки/RTL/narrow экран; form ownership по crispy правилам, без nested forms. Перевести только msgids этого изменения, не весь gettext backlog.

**Проверка:** `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_fix_check_view.py`. UI — реальный разрешённый isolated dev/test: 0/1/7/251/2 092, mixed auto/review/blocked, нет прав, cache miss, reload/back, duplicate click, known partial/unknown result. Desktop 1440 и narrow 390 px, keyboard-only от входа до подтверждения/итога. Lightpanda для DOM/переходов; visual screenshots через поддерживаемый Chromium либо явно указать отсутствие visual проверки. Не выполнять Apply на CoL4/prod без отдельного разрешения. Нет JS harness — browser smoke вместо assertions по тексту исходного JS; новую framework ради этой доработки не создавать.

### D. Обновить контракты и проверить интеграцию

**Зависимость:** A–C. **Результат:** документация и поведение совпадают, ограничения Anvil не скрыты; операторская команда и review-защита не ослаблены.

- [ ] `docs/admin/checks.rst`: два направления, all-removal vs reviewed-addition, preview cache expiry, исключения, no-undo и отсутствие постоянной истории.
- [ ] `docs/product/guides/producer-guide-weblate.md`, шаг 9: детерминированное исправление вместо совета повторно переводить punctuation моделью. Другие устаревшие разделы не переписывать.
- [ ] `docs/admin/management.rst`: UI отличается от полного operator backfill; `docs/security/threat-model.rst`: provider allowlist, cached actor-bound snapshot, неизменность signed review, scope revalidation, concurrency и честный unknown outcome.
- [ ] `docs/changes.rst`: исправить текущую unreleased запись, не released sections. Одним интеграционным проходом проверить тесты ниже, scoped lint и реальный UI; записать доказательства здесь.
- [ ] После проверки убрать throwaway smoke/probe scripts, обновить статус плана, commit/push по `AGENTS.md`. Deployment и реальные массовые правки требуют отдельного одобрения.

После `uv sync --all-extras --dev` и настройки DB/static по `docs/contributing/tests.rst`:

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_fix_check.py weblate/trans/tests/test_fix_check_view.py weblate/trans/tests/test_fix_check_task.py weblate/checks/tests/test_mass_fixup.py weblate/trans/tests/test_autofix.py
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_commands.py::ReapplyAutofixesCommandTest weblate/trans/tests/test_commands.py::ReapplyAutofixesTest
```

Оба command test класса проверены в текущем коде. Не запускать независимые pytest одновременно в одном checkout/reuse DB. Scoped lint: `uv run prek run --files <все изменённые файлы>`; общий `--all-files` не должен переписать постороннюю работу. Русские каталоги — `msgfmt --check`. Это команды будущей проверки, не заявления о пройденных тестах.

## Порядок, приёмка и одобрение

A → B → C integration → D. Entry templates C можно готовить после фиксации A/B контрактов, но shared `search.py`/`forms.py`/`fix_check.html` имеют одного владельца. Не создавать параллельные варианты интерфейсов. До изменения exported symbols — LSP references и миграция реальных consumers.

Готово, когда:

- [ ] >250 автоисправимых строк применяются одним подтверждением; новые/изменившиеся строки не перезаписываются.
- [ ] Кавычки/многоточия/conflicts/CJK-ограничения соблюдены; zero-eligible имеет объяснение и следующий шаг.
- [ ] Review-добавления нельзя включить через auto-all или непоказанные ID.
- [ ] Scope, selected counts, исключения, known partial и unknown исходы понятны продюсеру; narrow/keyboard сценарий проверен.
- [ ] State/machine-origin/source/judge history не повреждены; платных recheck и forced VCS commits нет.
- [ ] Нет новых моделей, migrations, Celery tasks, истории или cleanup/recovery подсистемы.

На одобрение: массовое удаление с доступным preview и краткоживущим общим cache, при сохранении построчного просмотра добавлений. План не расширяет покрытие до CJK-удаления/замены знаков без отдельного решения.

## Проверка документа

Проверены текущие пути/символы, test classes, DOM входа и read-only production-проекция всех пяти переводных языков и русского исходника. Первоначальная архитектура с persisted runs отклонена пользователем и удалена.

Независимый архитектор признал упрощённый подход пригодным при явном shared-cache условии и честных partial/unknown результатах; эти условия включены. Вместо предложенного им общего autofix-прохода с фильтрацией applied IDs сохранён прямой вызов конкретного активного provider плюс проверка save parity: это существующий registry lookup, не новая абстракция, и он сохраняет измеренную границу collateral changes. Preconditions state/flags сохранены, поскольку неизменность after сама по себе не подтверждает неизменность условий согласованной правки.

Security-review учтён в actor-only task metadata, immutable per-row scope, atomic selection claim и запрете продолжения mutation после неоднозначной повторной доставки. Код реализации не написан; будущие behavioral tests не запускались.

Первая проверка `uv run --no-sync prek run --files docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md`: проверки документа, включая rumdl/codespell, прошли; общерепозиторийные hooks `reuse` и `typos` завершились ошибками вне документа (`.omp/lsp.json` без licensing metadata и существующие `analysis/data/` corpora). Эти файлы не меняются ради плана. Повторная scoped проверка с `SKIP=reuse,typos` отделяет состояние документа от этих известных внешних блокеров.
