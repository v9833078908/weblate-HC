# План: «Добавить новые строки из loc-kit таблицы» для обычного компонента

## Контекст

Команда Space Arena работает китом-таблицей (CSV/XLSX: колонка-ключ + по
колонке на язык), а компонент `space-arena/lockit` — обычный монолингвальный
JSON, источник `ru`. Сейчас догрузить строки можно только родным аплоадом
Weblate по одному языку за раз (`context,source[,target]`), что не подходит:
таблица несёт 2+ языка в одной строке.

Для глоссария такой путь уже есть — «Add new glossary terms from a loc-kit
table» (`LocKitGlossaryUpdateStartView` -> preview -> `append_glossary_terms`).
Нужен его аналог для обычного (не-glossary) компонента.

Особый случай, который обязателен к учёту: источник `ru`, а у части строк
русская ячейка пустая и текст только в `en` (неосновном). Это названия
кораблей и т.п. — они должны сохранить английское значение, остаться без
русского перевода и не попадать под машинный автоперевод.

## Что уже есть и переиспользуется как есть

- `loc_kit_ingest.reader.read_sheets` — чтение CSV/TSV/XLSX.
- `loc_kit_ingest.infer.infer_profile` — вывод профиля `po` из шапки; коды
  языков резолвятся через `langcode` (`ch-s -> zh_Hans`, `jp -> ja`,
  `kr -> ko`, и т.д.).
- `loc_kit_ingest.parser.parse_component` -> `ParseResult`: `units` это
  `StringUnit(key, values: dict[lang -> text], comments, references, row)`,
  плюс диагностики `po.missing_source` (пустой источник при заполненной цели)
  и `po.key_without_content` (пусто везде -> ERROR).
- `weblate.trans.models.loc_kit.LocKitImportDraft` с полем `target_component`
  — временный черновик загрузки, привязанный к существующему компоненту,
  гейтится `upload.perform`.
- Паттерн UI из глоссарного апдейта: start -> preview -> confirm,
  `_insert_draft_and_map`, шаблоны `trans/loc_kit_glossary_update.html` и
  `trans/loc_kit_glossary_preview.html`.
- `weblate.utils.views.create_component_from_kit` — эталон конвертации
  «таблица -> per-language PO» (тот же infer/parse/render), из него берём
  порядок вызовов и обработку ошибок.

## Чего не хватает (это и есть работа)

Аппендер для обычного компонента — аналог `append_glossary_terms`, но через
`Translation.add_unit`, а не TBX. Плюс тонкая UI-обвязка вокруг того же
черновика.

### 1. `weblate/trans/loc_kit.py`: `append_translation_strings(...)`

Сигнатура по образцу `append_glossary_terms`:
`append_translation_strings(request, component, preview) -> StringsAppendResult`.

Логика (под `component.locked_for_update()`, порядок блокировок как в
глоссарном аппендере):

1. Собрать `existing = set(source_translation.unit_set.values_list("context"))`.
2. Классифицировать входящие `StringUnit` на новые и уже существующие по
   `context`. **Append-only:** существующие ключи не трогаем вообще (ни
   `source`, ни цели, ни флаги), считаем `skipped`.
3. Для каждого нового ключа:
   - `source_translation.add_unit(context=key, source=values.get(ru, ""),`
     `target=[], is_batch_update=True)` — юнит создаётся сразу во всех языках
     (`_add_unit_locked`, ветка `is_source`).
   - Для каждого языка из профиля, где в строке есть непустое значение, кроме
     `ru`: записать target в перевод этого языка
     (`translation.unit_set.get(context=key).translate(...)` в батче).
4. Недостающий язык (колонка есть в таблице, а языка в компоненте ещё нет —
   новая колонка в будущем ките): как в `append_glossary_terms`, создать язык,
   если у актора есть `translation.add`, иначе пометить `unavailable` и
   продолжить с остальными. Никогда не падать на `translation_set.get()` и не
   терять колонку молча — показать в превью и в итоге.
5. Флаги из колонки `flags` (см. раздел 2): **после** записи значений применить
   к исходному юниту флаги строки (обычно `read-only`). Порядок обязателен —
   read-only блокирует правку, в т.ч. `en`. Флаг ставится на `ru`-юнит и
   растекается на все языки через `get_all_flags` (`unit.py:2493`).
6. Вернуть `StringsAppendResult(added, skipped, per_language, created_languages,`
   `unavailable_languages, flagged)` для сообщения пользователю.

Заметки к реализации:

- Пустой source в батч-добавлении разрешён: `add_unit(is_batch_update=True)`
  пропускает `validate_new_unit_data` (проверено — `translation.py:2537`
  `if not is_batch_update`), а тот иначе требовал бы `state=STATE_EMPTY` для
  пустой строки. Одиночный UI-add этим путём не идёт.
- Порядок «значение -> флаг»: read-only ставится **после** записи целей,
  потому что read-only блокирует правку (в т.ч. `en`).
- Флаг ставится на **исходный** (ru) юнит: `extra_flags += "read-only"`,
  разотрётся на все языки через `get_all_flags` (`unit.py:2493`).

### 2. Управление read-only: колонка `flags` в таблице (решение принято)

Пустой `ru` бывает у названий (read-only) и у описаний (перевести позже) — в
таблице они выглядят одинаково, автомат не различит. Решение (эволюция от
глобальной галочки к пер-строчному контролю): **зарезервированная колонка
`flags`** в ките. Её ячейка — флаги Weblate для строки (обычное значение
`read-only`), применяются к исходному юниту; пустая ячейка = без флагов. Так в
одном ките имена помечаются `read-only`, а описания остаются без флага — по
строкам, без угадывания.

Это тянет правку конвертера `loc_kit_ingest` (раньше была «вне объёма»):

- `infer.py`: распознать заголовок `flags` (набор
  `_FLAGS_HEADERS = {"flags", "weblate-flags", "флаги"}`) как служебную колонку
  — не язык (мимо `langcode`) и не заметку (мимо `_NOTE_HEADERS`), занести её в
  профиль `po`. **Проверку `_FLAGS_HEADERS` поставить ДО ветки
  `comments.append(entry)` (`infer.py:326-327`):** колонка `flags` в основном
  пустая, а непустая неязыковая колонка сейчас сваливается в developer-comment
  (fallback срабатывает, если заполнена хоть одна ячейка, `infer.py:310-317`),
  иначе `read-only` вбивается в комментарий каждого юнита и в po-mono, и в
  глоссарном пути. Проверить, что `infer_glossary_profile` колонку `flags`
  игнорирует, а не тащит как ложную языковую/термин-колонку.
- `model.py`: добавить `StringUnit.flags: str` (по умолчанию пустая).
- `parser.py` `_parse_keyed`: заполнять `flags` из ячейки колонки.
- Киты без колонки `flags` работают как раньше.

Валидация: флаг применяется через `weblate.checks.flags.Flags`; невалидное
значение — ERROR в превью, а не тихое проглатывание.

### 3. Вьюхи `weblate/trans/views/create.py`

Параллельно глоссарным (не переиспользуя `LocKitGlossaryConfirmView`, который
явно отказывает не-glossary таргету):

- `LocKitStringsUpdateStartView` — как `LocKitGlossaryUpdateStartView`, но
  `get_component` требует **не** glossary, `has_template()` и `manage_units`
  (иначе `add_unit` недоступен), плюс `upload.perform`.
- `LocKitStringsPreviewView` — прогоняет `infer_profile`/`parse_component`,
  показывает: сколько новых ключей, сколько уже есть (skip), по языкам —
  сколько значений, сколько строк с пустым источником, сколько под `flags`
  (read-only), какие языки будут созданы и какие недоступны; ERROR-диагностики
  (`po.key_without_content`, невалидный флаг) блокируют применение.
- `LocKitStringsConfirmView` — применяет через
  `append_translation_strings`.

### 4. Форма, шаблоны, URL, меню

- `LocKitStringsUpdateForm` (поле `table`) — с `FormHelper(self);`
  `form_tag = False` (конвенция репозитория, иначе crispy вложит вторую форму).
  Галочка не нужна: read-only задаётся колонкой `flags` в самой таблице.
- Шаблоны `trans/loc_kit_strings_update.html` и `..._preview.html` по образцу
  глоссарных.
- URL `loc-kit-strings-update` в `weblate/urls.py`.
- Пункт меню в `weblate/templates/component.html` рядом с glossary-вариантом,
  под `if user_can_upload_translation and not object.is_glossary and`
  `object.manage_units`.

## Проверка

- Standalone: логика конвертации уже покрыта в `loc_kit_ingest/tests`.
- Weblate-контракт, новый класс в
  `weblate/trans/tests/test_loc_kit_ingest_contract.py`:
  - таблица с 2+ языками добавляет новый ключ во все языки, цели проставлены
    из своих колонок;
  - строка с пустым `ru` + текст в `en` -> юнит с пустым source, `en` target
    заполнен; при `flags=read-only` -> `state=READONLY` во всех языках, при
    пустой ячейке -> обычный untranslated с фолбэком на английский;
  - колонка `flags` с невалидным значением -> ERROR в превью, ничего не создано;
  - язык, которого нет в компоненте: с правом `translation.add` создаётся, без
    права -> пропуск с отметкой `unavailable`, остальные языки применяются;
  - существующий ключ не меняется (append-only): ни target, ни флаги;
  - строка без текста вообще (`po.key_without_content`) -> применение
    заблокировано, ничего не создано;
  - коды игры (`ch-s/jp/kr`) резолвятся в языки, а не в комментарии.
- Живой прогон в dev-контейнере через реальную форму (как для zip): загрузить
  маленькую таблицу на 3 языка, проверить компонент.
- Мутационная: откат ветки read-only -> падает тест про флаги; откат
  append-only guard -> падает тест про неизменность существующего ключа.

## Вне объёма

- Изменение существующих строк, их целей и флагов (это не append; отдельный
  разговор про conflicts).
- Автоопределение «имя vs описание» — не делаем, различие задаёт колонка
  `flags`.
- Деплой; глоссарный путь не трогаем.

## Решения приняты

1. read-only управляется **пер-строчно колонкой `flags`** в таблице (вариант B,
   доведён от галочки к колонке — так имена и описания разделяются в одном
   ките).
2. Недостающий язык из таблицы: создаётся при праве `translation.add`, иначе
   `unavailable` + показ в превью (никаких падений и тихих потерь).
3. Превью показывает новые/skip, по языкам, число строк с пустым источником,
   создаваемые/недоступные языки.
