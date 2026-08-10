# Колонка примечаний в выводе глоссарного профиля

Дата: 2026-08-10. Статус: утверждён.

Продолжение `docs/plans/2026-08-10-loc-kit-glossary-deterministic-infer-design.md`,
где в границах v0 записано: «секции и notes не выводятся», а таблицы с
колонкой note уходят в OpenRouter или в рукописный профиль.

## Проблема

Типовая глоссарная таблица игры — языковые колонки плюс одна колонка прозы
о термине:

```text
ru,en,tr,fr,note
Russian,English,Turkish,French,Note
Партия,Party,Parti,Parti,"Правящая политическая партия. Во французском
                          le Parti, мужской род, не la partie"
```

Детерминированный вывод профиля на ней отказывает. `infer.py:560-569` требует,
чтобы каждая заполненная колонка была опознанным языком, и пятая колонка даёт
`InferenceError`: «column 5 ('note') holds data but is not a recognised
language column; this layout needs an explicit profile».

Цена отказа не в лишнем шаге, а в потерянном качестве автоперевода. Пояснение
к термину — единственный способ снять омонимию адресно, на конкретной строке:

```mermaid
graph LR
  A[колонка note в CSV] --> B[notes scope=source в профиле]
  B --> C[descrip в TBX]
  C --> D[поле Explanation у термина]
  D --> E[source_explanation в запросе к модели]
```

Без звена B модель получает только пару «Партия = Parti» и на французском
может выдать `la partie` — партию товара вместо правящей партии. С пояснением
в запрос уходит:

```json
{"source": "Партия", "target": "Parti",
 "source_explanation": "Правящая политическая партия. Во французском le Parti, мужской род, не la partie"}
```

Правило 4 промпта (`weblate/machinery/llm.py:56`) использует
`source_explanation` именно для снятия омонимии.

Обходной путь — рукописный профиль — гарантирует, что пояснения заводить не
будут.

## Что уже работает

Проверено прогоном, а не чтением: ручной профиль с полем

```json
"notes": [{"scope": "source", "column": 5, "header": "note", "row_offset": 0}]
```

на приведённом CSV даёт `python -m loc_kit_ingest` код возврата 0 и
`out/Terms/tbx/fr.tbx` с `<descrip>Правящая политическая партия…</descrip>`.

Следовательно, менять не нужно:

| Слой | Состояние |
|---|---|
| Схема профиля v2 | `notes[]` допускает `row_offset: 0` на неязыковой колонке |
| Парсер | `_join_notes` (`parser.py:368`) склеивает несколько source-заметок, пустые ячейки выбрасывает |
| Коллизии полей | `_check_record_map_field_locations` (`profile.py:702`) считает по `(row_offset, column)`; `(0, note_col)` не пересекается ни с терминами `(0, язык)`, ни с парной заметкой `(1, source_col)` |
| Writer | пишет source explanation в `<descrip>` (`writer.py:181`) |
| Гейт публикации | `validate_glossary_profile` — та же схема, точные заголовки, парс, рендер, парс-бэк |
| UI превью | уже рендерит колонку «Source note» и счётчик `note_count` (`loc_kit_glossary_preview.html:39,65,76`) |
| Weblate | `TBXUnit.source_explanation` читает `<descrip>` (`ttkit.py:3388`) |

Не хватает ровно одного звена: вывод профиля не предлагает то, что всё
остальное умеет принять.

## Решение

### 1. Правило опознания — по тексту заголовка

Новая константа рядом с `_KEY_HEADER_DENYLIST` (`infer.py:45`):

```python
# A column of prose about the term, not a translation of it. Recognised by
# header text only: a shape rule would silently route "Character limit" into
# every LLM prompt.
_NOTE_HEADERS = frozenset({
    # English
    "note", "notes", "comment", "comments", "description", "descriptions",
    "explanation", "explanations", "context", "usage", "definition", "meaning",
    # Russian
    "примечание", "примечания", "комментарий", "комментарии",
    "описание", "описания", "пояснение", "пояснения",
    "контекст", "определение", "значение",
})
```

Сравнение — `header.strip().casefold()`, точное совпадение.

Отвергнутая альтернатива — распознавание по форме данных, порогами
`_DESCRIPTION_MIN_CHARS = 80` и `_DESCRIPTION_RATIO = 4.0`, которые уже
работают в детекции пар (`infer.py:50-51`). Отвергнута сознательно: колонка
«Лимит символов» или «Персонаж» прошла бы порог и молча уехала бы в каждый
промпт к модели. Ошибка первого рода здесь дороже отказа.

Отвергнута и разметка колонок оператором в превью: она даёт ноль ложных
срабатываний, но стоит нового экрана ради случая, который закрытый список
покрывает без единого клика.

Префиксное совпадение не используется: оно приняло бы `Context ID` и
`Note count`. Заголовок `Комментарий переводчику` остаётся случаем отказа.

### 2. Куда встраивается

`infer_glossary_profile`, между сборкой `languages` (`infer.py:518-547`) и
проверкой неотображённых колонок (`infer.py:555-569`):

```text
_find_header_row → languages{} → [НОВОЕ: note_col] → unmapped check
                 → regions → grammar
```

- ровно одна колонка-примечание на лист; две и более → `InferenceError` с
  перечислением номеров, потому что выбирать за оператора нечего;
- колонка не проходит проверки, применяемые к языкам: ни `min_fill`, ни отказ
  на числовых значениях. Примечание, заполненное в 3 строках из 40, — норма,
  а не разреженный язык;
- пустая целиком → исключается с заметкой, как пустая языковая колонка;
- проверка `populated - languages.keys()` получает вычитание `note_col`.

Ниже по функции не меняется ничего: колонка не участвует ни в определении
блоков (они идут по `source_col`), ни в классификации `flat`/`pairs`, ни в
подсчёте пропущенных терминов целевых языков.

### 3. Что попадает в профиль

`grammar["notes"]` сейчас заполняется только при `paired`
(`infer.py:705-715`). Становится списком из двух источников:

```json
"notes": [
  {"scope": "source", "column": 5, "header": "note", "row_offset": 0}
]
```

Для раскладки pairs с колонкой примечаний порядок — сначала запись
строки-описания (`row_offset: 1`), затем колонка (`row_offset: 0`):
`_join_notes` склеивает в порядке объявления, и основное описание должно идти
первым.

Диагностическая заметка в отчёт и превью:
`column 5 ('note') -> explanation of the source term`. Она попадает в тот же
список предупреждений, что и «column 3 ('fr') is empty; excluded», то есть
решение видно до создания компонента.

Область — только `scope: source`, только одна колонка на лист.

### 4. Сообщение об отказе

Единственное изменение за пределами правила. Было:

```text
column 5 ('Контекст') holds data but is not a recognised language column;
this layout needs an explicit profile
```

Станет:

```text
column 5 ('Контекст') holds data but is not a recognised language column;
rename the header to one of note, description, comment, explanation,
примечание, описание, комментарий, пояснение to import it as a term
explanation, or supply an explicit profile
```

Без кнопок и новых экранов.

## Границы

Сознательно не делается:

- заметки к конкретному целевому языку (колонка `fr note` →
  `<note from="translator">`), хотя схема их поддерживает;
- эвристика по длине текста;
- распознавание фраз вроде `Комментарий переводчику`;
- разметка колонок оператором в UI;
- глоссарные флаги (`terminology`, `read-only`, `forbidden`) из таблицы —
  отдельная тема, writer их пока не пишет вообще.

## Риск и чем ограничен

Заголовок из списка становится доверенным. Если в колонке `context` лежит не
проза, а идентификатор сцены `menu.settings`, он уедет в Explanation и в
каждый промпт.

Ограничено тремя вещами: список закрытый и короткий (`Character limit`,
`Персонаж`, `Status` в него не входят и по-прежнему дают отказ); решение
попадает в отчёт заметкой; содержимое видно в превью в колонке «Source note»
до создания компонента, и оператор отменяет импорт до записи в базу.

## Проверка

- `loc_kit_ingest/tests/test_infer_glossary.py`: плоская таблица с `note` даёт
  профиль с одним source-полем; `Notes` и `ОПИСАНИЕ` тоже; две таких колонки —
  отказ; пустая — исключается с заметкой; `Контекст` — отказ с новым текстом;
  pairs плюс `note` даёт два note-поля в правильном порядке.
- `loc_kit_ingest/tests/test_pipeline.py`: CSV `ru,en,tr,fr,note` проходит
  `python -m loc_kit_ingest` без профиля, и в `fr.tbx` есть `<descrip>`.
- `weblate/trans/tests/test_loc_kit_ingest_contract.py`: тот же CSV через
  настоящий визард даёт превью с непустым `note_count` и текстом примечания в
  колонке «Source note».
- Живой smoke кликами: загрузка файла → превью → создать компонент → у термина
  «Партия» заполнено Explanation → автоперевод на fr, `source_explanation` в
  запросе.
- `docs/specs/loc-kit-ingest.md`, раздел вывода профиля; запись в
  `docs/changes.rst`.
