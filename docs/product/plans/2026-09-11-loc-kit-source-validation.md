<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Валидация разметки исходника при приёме лок-кита

## Цель, решения и статус

**Дата:** 2026-09-11. **Последняя доработка:** 2026-09-15.
**Статус:** доработан после ревью, ожидает согласования; не реализован.
**Ревью:** `docs/product/reviews/2026-09-15-loc-kit-source-validation-plan-review.md`.

Повод — дефект 525591 из полного LQA-аудита французского Anvil Saga
(`docs/operations/audits/2026-09-11-anvil-saga-fr-lqa.md`): в русском
источнике стоит `Заказчики приходят в </color=yellow>{0}</color> раза реже.`
Открывающий тег написан как закрывающий. Перевод повторил источник, поэтому
`GameMarkupCheck` его не видит: `TAG_PATTERN` в
`weblate/trans/protected_tokens.py` принимает этот тег, а `GameMarkupCheck`
сравнивает source и target. Строка попадает в игру, промпт переводчика и
промпт судьи как эталон.

Быстрый smoke подтвердил путь: `TAG_PATTERN.fullmatch("</color=yellow>")`
истинен, а у source и идентичного target совпадают `markup_tokens` и
плейсхолдеры. Полный LQA export Anvil Saga содержит эту пару в `SpawnRate`
(`analysis/data/anvil-saga-fr-lqa-golden-2026-09-11.json`).

### Выбранный объём первой поставки

Первая поставка ловит только подтверждённый, однозначный дефект
`source.tag_closing_has_attribute`: закрывающий Unity-тег с `=` или
пробельным атрибутом. Это один теговый токен — ему не нужна эвристика,
сравнение с target или восстановление стека.

    </(?:color|link|size|b|i|u|s|sprite)[=\s][^>]*>

Паттерн регистронезависим, как существующий `TAG_PATTERN`. Его закрытый
список имён не принимает обычную пунктуацию: `HP > 50`, `<3`, `a -> b`,
`Урон < 10`, `2 < 3 > 1` и `<basic>` не являются кандидатами.

**Не входят в эту поставку:** парность/вложенность тегов, общий malformed-tag
parser, незакрытые `{`, conditional DSL, идентификаторы перед `[` и эвристика
внутритегового пробела. Для них нужны отдельные грамматики или межстрочный
контекст; они не могут надёжно жить в `source_markup_defects(text)`. Решение
расширить правило допускается только новым планом после измерения precision
на source corpus.

### Два слоя, один обязательный источник правила

Модуль `loc_kit_ingest/source_markup.py` — чистый Python, без Django. Его
используют:

1. офлайновый CLI и обычный PO intake: до записи в отчёте появляется warning,
   а `--strict-source` делает его error;
2. `GameSourceMarkupCheck` в Weblate: он проверяет source Unit, поэтому ловит
   UI/API/VCS пути, которые не проходят через kit.

`GameSourceMarkupCheck` импортирует модуль **обязательно**. Silent fallback
запрещён: зарегистрированная проверка без модуля должна оборвать загрузку
конфигурации, а не выдать ложное отсутствие дефектов.

У production image уже есть правильная граница поставки: `deploy/Dockerfile`
копирует весь `loc_kit_ingest` в `/app/pylib`, а `deploy/vps.sh` включает этот
каталог в `IMAGE_PATHS`, поэтому изменение модуля пересобирает образ. Для
запущенного dev container модуль нужно отдельно скопировать в
`dev-docker/data/python/loc_kit_ingest/`, откуда его импортирует Python.

## Контракт v1

    @dataclass(frozen=True)
    class SourceDefect:
        code: str
        message: str
        span: tuple[int, int]


    def source_markup_defects(text: str) -> tuple[SourceDefect, ...]:
        """Return one source.tag_closing_has_attribute defect per matching tag."""

- Единственный `code` v1 — `source.tag_closing_has_attribute`.
- Один match даёт ровно один `SourceDefect`; строка с двумя независимыми
  ошибочными закрывающими тегами даёт два дефекта с точными неперекрывающимися
  `span`.
- Никакая другая часть строки не анализируется. В частности,
  `<color=>`, `</sprite>` и несбалансированная пара не получают v1 verdict,
  пока не появится отдельная проверенная грамматика.
- В parser v1 проверяется только source value PO-единицы `_parse_keyed`.
  TBX term, explanation, notes и section captions вне scope: glossary не
  доставляет игровой rich text, а их выбор нельзя вывести из одного
  «source language column».
- `Diagnostic.message` включает ключ строки (`key <…>`) и безопасный
  диагностический текст; текущая модель `Diagnostic` не имеет отдельного
  поля key.

## Политика найденного

- По умолчанию source defect — **WARNING**. Импорт сохраняется, а существующий
  wizard уже переносит non-ERROR diagnostics в `kit_info["warnings"]` и
  выводит первые пять предупреждений; новый шаблон не нужен.
- `--strict-source` повышает только diagnostics этого правила, включая summary
  о подавленных результатах, до **ERROR**. `pipeline.run()` возвращает 2 до
  staging и не создаёт output directory.
- Лимит — 100 source-markup diagnostics на component: первые 99 в
  детерминированном порядке строки/позиции и один
  `source.markup_diagnostics_suppressed` с числом остальных. Это не даёт
  битому листу разрастить report или `kit_info["warnings"]` без границы.
- SourceCheck возвращает `True`, если хотя бы одна source plural form имеет
  дефект, и подсвечивает каждый `SourceDefect.span`. Его статичное описание
  называет конкретную ошибку закрывающего тега — v1 не сворачивает разные
  типы ошибок в один непрозрачный check.

## Задачи

### Задача 0. Подтвердить rule и измерить source corpus

**Outcome.** Чистый модуль определяет один проверенный rule; read-only probe
доказывает его частоту и precision до включения source check на production.
Никакие данные Weblate не изменяются.

**Файлы и интерфейсы:** новый `loc_kit_ingest/source_markup.py`; новый
`loc_kit_ingest/tests/test_source_markup.py`; новый
`analysis/probes/source-markup-defects.py`; новый dated report в
`docs/operations/measurements/`.

**Действия:**

- [ ] Реализовать `SourceDefect` и `source_markup_defects()` строго по
      контракту v1; не копировать более широкий `TAG_PATTERN` и не добавлять
      стек, brace или DSL parsing.
- [ ] Добавить unit tests: полный текст 525591, один match, два независимых
      closing tags, пустая строка, корректные открывающий/закрывающий теги и
      все перечисленные punctuation negatives.
- [ ] Написать read-only probe поверх этого модуля. Он получает список
      проектов и components, берёт `source_language` у **каждого** component,
      cursor-paginate source translation через `?page_size=1000`, следует
      `next` до конца и обрабатывает source один раз на `(component, context)`.
- [ ] Напечатать components, число просмотренных source units, Counter по
      code и не более трёх `(project, component, context, source)` примеров
      на code. Сохранить в dated measurement ручную классификацию каждого
      кандидата как true/false positive.

**Verification.** `cd loc_kit_ingest && uv run pytest tests/test_source_markup.py`.
Read-only production run требует отдельной авторизации доступа; report обязан
зафиксировать cursor coverage и число просмотренных source units. Отсутствие
найденных кандидатов — корректный результат, не повод расширять rule без
новой evidence.

### Задача 1. Показывать v1 diagnostics при PO intake

**Outcome.** Обычный PO kit с bad closing tag проходит с ограниченным warning;
strict CLI отказывает до записи. Existing wizard показывает тот же warning и
не блокирует переход к созданию component.

**Файлы и интерфейсы:** `loc_kit_ingest/parser.py` (`parse_component`,
`_parse_keyed`); `loc_kit_ingest/pipeline.py` (`run`, `_build_report`);
`loc_kit_ingest/cli.py`; существующие tests в `loc_kit_ingest/tests/`;
`weblate/trans/tests/test_loc_kit_ingest_contract.py`.

**Действия:**

- [ ] Добавить явный `strict_source: bool = False` от CLI до PO parser; UI
      всегда вызывает default `False`.
- [ ] Для каждого source value `_parse_keyed` преобразовать defects в
      `Diagnostic` с component, sheet, точной spreadsheet row и message,
      содержащим key. Соблюдать per-component cap 99 + summary 1.
- [ ] Не вызывать v1 rule для target value, TBX grammar или пустого source.
- [ ] Оставить существующий `kit_info["warnings"]` и message flow без template
      change. Расширить только contract test, чтобы response содержит code и
      ключ, но create form остаётся доступной.

**Verification.** `cd loc_kit_ingest && uv run pytest` доказывает: одна
ошибка даёт один warning и output; две ошибки дают две diagnostics; 101 ошибки
дают 99 details и summary; `--strict-source` даёт exit 2 и output directory
не появляется. `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`
доказывает UI warning без блока создания.

### Задача 2. Включить обязательный source check и поставку модуля

**Outcome.** Любая source Unit с closing tag attribute получает
`check:game-source-markup`, причина и highlight; отсутствие общей библиотеки
останавливает конфигурацию вместо silent pass.

**Файлы и интерфейсы:**
`weblate_customization/src/weblate_customization/checks.py`;
`weblate_customization/tests/test_checks.py`;
`dev-docker/docker-compose.yml`; `deploy/environment.example`;
`deploy/Dockerfile`; `docs/product/guides/producer-guide-weblate.md`;
`docs/changes.rst`.

**Действия:**

- [ ] Добавить `GameSourceMarkupCheck(SourceCheck)` с `check_id =
      "game-source-markup"`, `default_disabled = False`, обязательным импортом
      `source_markup_defects`, `check_source_unit(sources, unit)` и
      `check_highlight(source, unit)` по `SourceDefect.span`.
- [ ] Добавить translatable name/description, прямо называющие closing tag с
      attribute; не использовать `check_single` и не терять plural source
      forms.
- [ ] Зарегистрировать class в `WEBLATE_ADD_CHECK` для dev и deployment
      environment. Не добавлять fallback when import fails.
- [ ] Обновить build assertion в `deploy/Dockerfile`, чтобы он проверял
      `loc_kit_ingest.source_markup` наряду с пакетом. Существующий
      `IMAGE_PATHS` уже пересобирает production image при изменении
      `loc_kit_ingest`; не создавать вторую deployment path.
- [ ] Для dev verification до запуска test скопировать оба обязательных
      модуля: `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`
      и `cp -r weblate_customization/src/weblate_customization
      dev-docker/data/python/`. Полная пересборка/перезапуск dev stack требует
      отдельного разрешения.
- [ ] Описать source warning/check в producer guide и добавить короткую запись
      в текущую unreleased section `docs/changes.rst`.

**Verification.** Unit tests вызывают `check_source_unit()` на корректном
source, 525591 и plural source; проверяют boolean и exact highlight span.
`./rundev.sh test weblate_customization/tests/test_checks.py` проходит после
копирования пакетов. На уже разрешённой dev instance source Unit с
`</color=yellow>` появляется в `check:game-source-markup`; production rollout
только после отчёта Task 0 и отдельного разрешения deployment.

## Вне области

- Автопочинка source. Правка меняет игровой текст и инвалидирует переводы; её
  принимает игровая команда, не импортёр.
- Любая target-проверка (`GameMarkupCheck` и прочие), проверка орфографии,
  терминологии или смысла source.
- Unbalanced/malformed tags, placeholders, conditional DSL, token identifiers
  и `tag_inner_whitespace`: это будущие независимые designs после measured
  precision и, где нужно, batch context.
- TBX/glossary parser diagnostics в v1.
- Любая запись на production; Task 0 только читает, а rollout требует
  отдельного разрешения.

## Условия реализации

План готов к реализации только после согласования. Последовательность
жёсткая: Task 0 → Task 1 → Task 2. Measurement Task 0 — evidence для
production включения, а не основание незаметно расширить v1. Новое source
rule не должно повторять судьбу 525591: успешный import/registration обязан
быть проверяемым, а недоступная библиотека не имеет безопасного режима
«молчать».
