# Поддержка exact при импорте глоссария

Дата: 2026-09-08.
Статус: план подготовлен; реализация и развёртывание не разрешены.

## Цель и основания

При загрузке таблицы глоссария разрешить `exact` в существующей колонке `flags`, показать его в предпросмотре и сохранить действующее правило точной формы перевода после создания компонента или добавления новых терминов.

Основания: запрос пользователя в чате; `docs/product/guides/loc-kit-ingest.md`; `docs/product/plans/2026-08-31-02-loc-kit-glossary-source-flags.md`. Правила работы: `AGENTS.md`.

Согласованная политика: `exact` никогда не выводится из текста или языка автоматически — он применяется только когда оператор явно указал его в `flags`. Уже существующая автоматическая пометка `terminology` не меняется. Интервью собирает требования нейтрально; фраза «Обычно особые правила не нужны» удалена из managed skill отдельно от этого плана.

## Выбранный подход и границы

Сохранить формат `<source>,<targets...>,explanation,flags`. `exact` в общей колонке означает применение правила к каждому **непустому** импортируемому переводу этой строки. Отдельный русский-only exact в многоязычном файле этим контрактом не выражается. Для него нужна отдельная языковая настройка в Weblate; новые колонки и синтаксис `ru:exact` не вводятся.

Ключевое разделение: расположение правила в CSV не определяет область действия в модели. После создания и append `exact` живёт только на target Unit и никогда не остаётся на source Unit; `read-only` и `forbidden` остаются наследуемыми source modes. Существующая семантика `get_glossary_term_modes` не меняется. Не добавлять `exact` в `SOURCE_SCOPED_MODES`.

Не входят: изменение алгоритма glossary matching, контроль регистра, новая схема языковых колонок пометок, вывод `exact` из данных, переработка обычного PO string-kit и его `explanation`/`Unit.note` flow, изменение старых терминов append-only, продакшен-команды и деплой. Добавленные позднее языки не наследуют exact автоматически.

## Наблюдаемая архитектура

- `loc_kit_ingest/parser.py:GLOSSARY_SOURCE_FLAGS` допускает только `read-only` и `forbidden`; это private legacy name общей колонки, а не область действия каждого токена. Разбор record-map выдаёт `tbx.invalid_source_flag` для exact.
- `loc_kit_ingest/model.py:GlossaryTerm.source_flags` и соответствующее поле профиля несут общую колонку пометок. Закрытая v2-схема и JSON-поле не переименовываются; публичные тексты называют её «glossary flags», а не «source flags».
- `loc_kit_ingest/writer.py` сейчас ставит весь набор `weblate-flags` в каждый TBX `termEntry`, даже когда target пуст. Поэтому writer обязан формировать набор для конкретного target-файла: наследуемые `read-only`/`forbidden` всегда, `exact` — только при непустом target. Его parse-back валидация должна сравнивать этот target-specific набор.
- Существующий TBX import переносит `weblate-flags` и на source, и на target Unit (`test_source_flags_are_previewed_and_created`). Один writer не может обеспечить target-only storage: после `create_translations` нужен loc-kit-only normalizer, который убирает `exact` с source и пустых target Unit, не трогая остальные флаги.
- `weblate/trans/loc_kit.py:GlossaryTermPreview`, `validate_glossary_profile` передают пометки в предпросмотр; шаблон `weblate/templates/trans/loc_kit_glossary_preview.html` показывает их.
- `append_glossary_terms` сейчас объединяет все `term.source_flags` с флагами исходного Unit (строки 1131–1138). Для exact это недостаточно: `weblate/glossary/models.py:get_glossary_term_modes` читает exact только у перевода.
- `weblate/checks/glossary.py:evaluate_glossary_terms` уже задаёт приоритет: `forbidden` доминирует; `read-only` меняет ожидаемую форму на source; `exact` в сочетании с одним из них не добавляет отдельного эффекта. Сопоставление регистронезависимо; менять это поведение не требуется.

## Общий контракт

1. Пустая `flags` не меняет поведение. `read-only` и `forbidden`, включая их сочетания, сохраняют совместимость.
2. `exact` — допустимый токен общей колонки. Он применяется только к фактически созданным target Unit с непустым target: ни source Unit, ни пустой target, ни язык без колонки его не получают.
3. Неизвестные токены, `exact:ru`, пустые элементы списка остаются ошибками. Диагностика перечисляет `read-only`, `forbidden` и `exact` как glossary flags, не объявляя их source-scoped.
4. Existing-term identity и append-only неизменны: входящие пометки не переписывают существующие термины ни на одном языке.
5. Пустые переводы, отсутствующие языки и ограничения разрешений сохраняют текущие результаты partial-success. Не создавать фиктивный перевод ради пометки.
6. Комбинации имеют наблюдаемую текущую семантику: `exact, forbidden` действует как `forbidden`; `exact, read-only` действует как `read-only` и требует source-форму. Новые комбинации не назначаются автоматически.
7. `terminology` продолжает автоматически добавляться только как существующий признак нового glossary term; это не вывод и не наследование `exact`.
8. Успех проверяется эффективными режимами target Unit, отсутствием `exact` на source и пустых target Unit, а не только наличием слова в CSV/TBX/preview.

## Задача 1 — Офлайн-разбор и передача exact

**Результат:** CSV с exact проходит parse → target-specific TBX → parse-back; `exact` сохранён только в TBX непустых target, существующие файлы работают по-прежнему.

**Файлы:** `loc_kit_ingest/parser.py`, `loc_kit_ingest/writer.py`; тесты `loc_kit_ingest/tests/test_parser_tbx.py`, `test_writer.py`, `test_infer_glossary.py`, `test_pipeline.py`. Схема v2 и поле `source_flags` профиля неизменны.

- [ ] Добавить exact в allowlist общей колонки; обновить диагностическое перечисление и private-комментарии о том, что колонка общая, а `exact` target-scoped. Не менять код диагностического события без отдельной причины совместимости.
- [ ] Исправить существующий тест `test_record_map_rejects_unsupported_source_flags`: exact перестаёт быть отрицательным примером; неизвестные, параметризованные и некорректные значения остаются отрицательными.
- [ ] В writer разделить наследуемые и target-scoped токены. Каждый output TBX сохраняет `read-only`/`forbidden`; он сохраняет `exact` только когда значение именно этого target непустое. Обновить `_validate_tbx`, чтобы ожидание строилось по target, а не по общему `GlossaryTerm.source_flags`.
- [ ] Добавить строку с `en` и `de`, где один target пуст: parse-back подтверждает exact в одном TBX и его отсутствие в другом. Отдельно покрыть пустые flags, сохранность source/target explanatory text и сочетания `exact, forbidden` / `exact, read-only`.

**Проверка:** `uv run pytest` из `loc_kit_ingest/`. Дополнительно временный CSV `en,ru,de,explanation,flags` с одной заполненной и одной пустой target-ячейкой: `infer_glossary_profile`, затем `python -m loc_kit_ingest INPUT --profile PROFILE --out NEW_DIRECTORY` из корня через `uv run` и `PYTHONPATH` корня. До изменения exact даёт `tbx.invalid_source_flag`; после — exact только в TBX заполненного target, без ошибок. Неподдерживаемый токен продолжает давать отказ.

Зависимости: нет.

## Задача 2 — Действующее правило после создания и append

**Результат:** exact виден в предпросмотре, у каждого нового непустого target действует как exact-only mode, а у source и пустых target отсутствует. На старые термины и будущие языки не распространяется.

**Файлы и интерфейсы:** `weblate/trans/loc_kit.py:append_glossary_terms`, `GlossaryTermPreview`, `validate_glossary_profile`; `weblate/trans/views/create.py:LocKitGlossaryConfirmView`; `weblate/trans/models/component.py:Component.save`/`after_save`; `weblate/trans/tasks.py:component_after_save`; `weblate/trans/tests/test_loc_kit_ingest_contract.py`; существующий TBX-import. Использовать `Unit.update_extra_flags` и `Flags`, не прямые SQL-обновления.

- [ ] В append отделить exact от наследуемых source flags: read-only/forbidden и terminology остаются на source; после успешного `translation.add_unit` exact через `Flags` добавляется только новому непустому `target_unit`. Не менять флаги existing term, source Unit, пустого, absent или unavailable target.
- [ ] Для creation-path передать из `LocKitGlossaryConfirmView` через transient marker, `Component.save` и `component_after_save` факт, что валидированный loc-kit содержит exact. После успешного `create_translations` normalizer под существующей блокировкой компонента удаляет exact с каждого source Unit и target Unit с пустым `target`, сохраняя exact на непустых target и все остальные флаги. Маркер и normalizer применяются только к этому loc-kit glossary creation path; обычный TBX import не меняется.
- [ ] Передавать original acting user в фоновую задачу; не подменять автора системным пользователем. При неуспешном repository update или отсутствии созданных переводов normalizer ничего не меняет и задача использует существующий retry/error path, а не частичную нормализацию.
- [ ] Расширить `test_source_flags_are_previewed_and_created`: реальное подтверждение glossary creation с двумя target-языками, один пуст. Проверить preview, effective `get_glossary_term_modes`, exact на непустом target и отсутствие exact на source/пустом target.
- [ ] Расширить `test_new_terms_merge_source_flags_with_terminology` и `test_incoming_source_flags_never_rewrite_existing_terms`: exact у новых непустых target в двух языках, отсутствие exact на source, сохранность terminology/read-only/forbidden, старых флагов и текста.
- [ ] Проверить append с пустой ячейкой, отсутствующим языком и недоступным созданием языка; результаты пропусков и границы транзакции сохраняются. Добавленный впоследствии язык не получает exact от исходника.
- [ ] В `weblate/checks/tests/test_glossary_checks.py` закрепить: склоняемая форма обычного термина допускается, exact требует заданную форму, `exact, forbidden` запрещает форму, `exact, read-only` требует source-форму. Тесты фиксируют уже существующий приоритет, а не меняют его.

**Проверка:** в согласованном dev-контейнере `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py` и `./rundev.sh test weblate/checks/tests/test_glossary_checks.py`. Не пересоздавать shared dev stack. Выполнить реальный dev UI-сценарий загрузки/предпросмотра/подтверждения и append после отдельного разрешения на изменения dev-данных; не выдавать офлайн-проверку за этот сценарий.

Зависимость: задача 1. До изменения экспортируемых символов проверить ссылки через LSP; не вводить дублирующий путь сохранения пометок.

## Задача 3 — Документация, подсказки и окончательная интеграция

**Результат:** документация и скилл обещают ровно поддержанное glossary-only поведение и не смешивают его с обычным PO string-kit explanation-flow.

**Файлы:** `docs/product/guides/loc-kit-ingest.md`, `docs/changes.rst`; managed skill `~/.omp/agent/managed-skills/game-glossary-builder/SKILL.md`; при необходимости пользовательские пояснения в существующем `weblate/templates/trans/loc_kit_glossary_preview.html`.

- [ ] Заменить в glossary-разделах гайда формулировку «source-scoped flags»: `flags` — общая колонка; `read-only`/`forbidden` наследуются от source, `exact` относится к каждому непустому target. Обновить canonical CSV и указать, что отсутствующий target и добавленный позже язык exact не получают.
- [ ] Описать фактический приоритет сочетаний `exact, forbidden` и `exact, read-only`, отсутствие гарантии регистра и ограничение правила только одного языка. Не объявлять новый синтаксис.
- [ ] Не менять разделы про обычный PO-kit, `#. developer comment`, `Unit.note` или импорт explanation: это отдельный, не согласованный план `docs/product/plans/2026-08-28-kit-explanation-on-string-import.md`.
- [ ] Обновить сообщение о допустимых флагах в существующем пользовательском интерфейсе, если оно перечисляет два значения; пользовательские строки локализовать по правилам репозитория.
- [ ] После подтверждения задач 1–2 убрать из скилла утверждение, что exact не принимается. Сохранить нейтральный вопрос интервью, отдельное согласование исключений, английские инструкции и русское общение.
- [ ] Добавить короткую запись в верхний unreleased-раздел changelog со ссылкой на руководство. Проверить `docs/security/threat-model.rst`: изменение допускает лишь встроенный режим сопоставления, не произвольные проверки или исполняемые флаги; если реализация расширит эти границы, остановить расширение и согласовать отдельно.

**Проверка:** повторить офлайн-шаблон с пустыми flags, со старыми двумя значениями и с exact; сравнить реальные effective modes и raw `extra_flags` после creation и append. `uv run prek run --files` для всех изменённых файлов после завершения общей работы. Код и документацию коммитить/пушить вместе после проверок; managed skill вне Git и фиксируется через managed-skill tool отдельно. Не копировать его в репозиторий только ради коммита.

Зависимость: задачи 1–2. Изменения в общих файлах выполнять последовательно; независимая подготовка документации возможна по зафиксированному контракту.

## Готовность и разрешения

План готов к повторному рассмотрению, но реализация не разрешена. До этого закрыты архитектурные неопределённости: writer создаёт target-specific TBX, creation-path нормализует import-артефакт до target-only exact, пустые targets не получают пометку, а приоритет сочетаний закреплён текущими проверками. Обычный PO string-kit explanation-flow и его отдельный review не являются зависимостью этого инкремента.

В этой сессии поддержка exact не реализована. Утверждение плана разрешает только последующую реализацию в согласованном рабочем окружении, но не развёртывание и не изменения production. При разрешении реализации использовать ultrasuperpowers-executing-plans.
