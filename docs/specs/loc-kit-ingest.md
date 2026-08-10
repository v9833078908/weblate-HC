# Импорт лок-китов: кит → Weblate (разовый онбординг-сев)

Как один раз перенести лок-кит игры Hero Craft из Excel/CSV/TSV в Weblate.
Два равноправных входа: универсальная загрузка в UI («Отправить файлы
перевода» принимает сам кит) и CLI `python -m loc_kit_ingest`. Оба выводят
профиль из заголовка кита, создают совместимые с Weblate артефакты и передают
источник правды Weblate.

План реализации: `docs/plans/2026-08-06-loc-kit-ingest.md`.

Проверено как вход: `Temple.csv` (диалоги), `Terms.csv` (глоссарий/лор) и
`UI.xlsx` (UI-строки). Реальные киты не попадают в тестовые фикстуры. Смежные
конвенции форка: `docs/specs/continuous-localization-loop.md` (git ↔ Weblate,
игровой Id → location), `weblate_customization/` (`game-markup`-check и
Routed LLM).

## Контекст и границы

- **Weblate - источник истины; таблица - одноразовый seed.** После начала
  перевода правки и переводы живут в Weblate + git. Обратной записи в таблицу,
  слияния и повторного импорта нет.
- **Точка невозврата** - первый начатый перевод. До неё неверный seed исправляют
  удалением компонента, исправлением таблицы/профиля и повторным импортом. После
  неё пересев запрещён.
- **Импортёр не угадывает при парсинге.** Разбор всегда идёт по строгому
  профилю с закрытой схемой. Профиль выводится из заголовка кита один раз,
  до разбора, письменно: каждое решение попадает в отчёт, документ
  сохраняется в выход (`profile.loc-ingest.json`) и может быть поправлен и
  передан через `--profile`. `--source-lang` переопределяет язык-источник.
- **Атомарность важнее частичного результата.** Структурная ошибка в любом листе,
  при записи формата или ZIP не публикует ни одного файла и не меняет ранее
  существующий каталог назначения.
- **Формат зависит от назначения.** Диалоги и UI используют монолингвальный PO;
  Terms использует двуязычный TBX по целевому языку, потому что только TBX
  сохраняет source/target explanations глоссария для LLM.

Не поддерживаем на старте Google Sheets API, экспорт обратно в формат движка,
двустороннюю синхронизацию и повторный/idempotent import.

## Вход и профиль: выводится из заголовка, рукописный - опционален

Поддерживаются `.xlsx`, `.csv` и `.tsv`: `openpyxl` для XLSX и стандартный
`csv` для UTF-8/UTF-8-BOM CSV/TSV. CSV/TSV имеет один лист с именем stem файла;
XLSX использует имена листов без переименования.

Профиль **выводится автоматически** из заголовка кита (`loc_kit_ingest/infer.py`):
строка заголовка находится по первому распознанному коду языка (баннеры вроде
`UI,,,` отсеиваются), ключ - колонка 1, языковые колонки распознаются по кодам
(`ru`, `en`) и подписям (`English(en)`), строки-подписи (`id-ignore`, имена
языков) уходят в `skip_rows`. Колонка с языковым заголовком, но числовым
содержимым (`Id`) становится location-ссылкой; текстовая неязыковая колонка
(`Character`) - комментарием переводчику. Языковая колонка, заполненная меньше
чем на 5% (`--min-fill`), исключается как случайный мусор с перечислением строк
(`--include-lang` возвращает её). Язык-источник - первая заполненная языковая
колонка (`--source-lang` переопределяет). Выведенный профиль проходит тот же
строгий загрузчик, что и рукописный, и записывается в выходной каталог
(`profile.loc-ingest.json`) как свидетельство; отчёт перечисляет каждое решение.

**Keyless-киты.** Колонка 0 (ключ PO) сама становится языковой, если её
заголовок резолвится в код языка, не входит в `_KEY_HEADER_DENYLIST = {"id"}`
(коды вроде `id` остаются строковым Id, а не индонезийским языком) и содержит
непустые нечисловые данные. Колонка 0 при этом остаётся ключом PO, поэтому key
и исходный текст совпадают - в отчёт попадает заметка "column 1 is both the PO
key and a language column". Так кит без отдельной латинской key-колонки
(`ru,en,ja` с терминами в `ru`) выводится как монолингвальный PO с `ru` в
качестве источника. Для подлинно неоднозначной первой колонки остаётся
`--profile`.

```sh
# профиль выводится сам
uv run python -m loc_kit_ingest "/path/Temple.csv" --out /tmp/seed --zip
# рукописный профиль для нестандартных китов
uv run python -m loc_kit_ingest "/path/Temple.csv" \
  --profile "/path/Temple.loc-ingest.json" --out /tmp/seed --zip
```

### Универсальная загрузка в UI

Вкладка «Отправить файлы перевода» мастера создания компонента принимает ZIP
(исторический путь: файлы с кодами локалей) **и киты CSV/TSV/XLSX напрямую**
(`weblate/utils/views.py:create_component_from_kit`). Таблица конвертируется в
монолингвальные PO на месте, discovery пропускается - create-форма приходит
уже заполненной (`po-mono`, `*.po`, шаблон и язык-источник из кита), отчёт
конверсии показывается сообщениями интерфейса. Ошибки (дубликаты ключей -
с номерами обеих строк) блокируют загрузку до исправления таблицы. Расширение
на новые форматы = суффикс в `KIT_TABLE_SUFFIXES` + поддержка в `reader.py`.

### Глоссарий через UI: явный «Use as glossary»

Тот же кит CSV/TSV/XLSX становится TBX-глоссарием только когда оператор явно
отметит галку **Use as glossary** при загрузке. Это единственный сигнал
намерения: без неё обычный PO-кит остаётся локальным и никогда не уходит в LLM.
Анализ детерминированного маппинга запускается сразу после выбора листа (или
автоматически для одностраничных файлов, см. ниже); OpenRouter - только
запасной путь.

1. **Выбор листа.** Для многолистового XLSX показывается форма выбора одного
   листа (имена и размеры как radio-варианты). Один выбранный лист создаёт один
   глоссарий-компонент; листы не объединяются и не батчатся. CSV/TSV и
   однолистовой XLSX пропускают этот экран: лист фиксируется автоматически и
   сразу выполняется шаг 2 (сетевой вызов внутри этого POST недопустим, поэтому
   здесь пробуется только детерминированный путь).
2. **Детерминированный вывод (первым, локально, бесплатно).** Из заголовка
   листа выводится профиль schema v2 `record-map`: языковые колонки
   распознаются по кодам/подписям, самая левая - источник, целевые - колонки,
   заполненные в каждой строке-записи. Любая заполненная не-языковая колонка
   отклоняется (`InferenceError`) - парсер не угадывает назначение колонки.
   Успех сразу даёт локально валидированный превью (шаг 4); отказ переходит к
   шагу 3.
3. **Кандидат-профиль (опционально, fallback).** Если детерминированный вывод
   отказал и site-wide анализатор включён и настроен, из выбранного листа
   строится детерминированный структурный сэмпл и отправляется одним POST в
   фиксированный OpenRouter endpoint. Модель лишь *предлагает* профиль v2; её
   ответ не исполняет код и не создаёт компонент. При выключенном/недоступном
   анализаторе или слишком большом сэмпле предлагается ручная загрузка профиля
   вместо fallback на PO-вывод.
4. **Локальная валидация (обязательна).** Кандидат-профиль (детерминированный
   или LLM) или загруженный вручную `.loc-ingest.json` проходит локальный
   конвейер: `parse_profile` → точная проверка заголовков → `parse_component` →
   рендер TBX → parse-back. Один TBX-компонент для выбранного листа; имя
   компонента генерирует сервер, а не LLM и не исправленный профиль. Предпросмотр
   (источник, целевые языки, число терминов, предупреждения, bounded-сэмпл
   терминов и скачиваемый профиль) появляется только при нулевых ошибках.
5. **Исправление профиля.** Предпросмотр отдаёт профиль JSON как скачиваемый
   файл и принимает исправленный `.loc-ingest.json` взамен; исправление
   повторно валидируется против того же черновика и листа, без нового вызова
   LLM.
6. **Обязательный parse-back до создания.** Рендер TBX и parse-back выполняются
   до создания компонента - это ворота публикации, а не пост-проверка. Только
   после успеха оператор подтверждает создание, которое донастраивает
   `file_format="tbx"`, `filemask="tbx/*.tbx"`, пустой template, профильный
   язык-источник и `is_glossary=True`; эти поля неизменяемы в финальной форме.
7. **Временный черновик.** Загруженный файл хранится в session-bound,
   owner-bound временном черновике не дольше одного часа; он удаляется при
   создании, отмене или периодической очистке Celery. Чужой владелец, другая
   сессия, истёкший или consumed-токен ведут себя как отсутствующий.

**Минимизация данных LLM.** Наружу уходит только bounded структурный сэмпл:
метаданные листа, заполняемость строк/колонок, заголовки и усечённые выдержки
ячеек (capped) - никогда весь файл. Флаги глоссария (`forbidden`/`read-only`/
`terminology`) и source-only импорты в этом потоке не поддерживаются: требуется
один источник и хотя бы один целевой язык.

### Рукописный профиль

Профиль имеет `schema_version` 1 (PO и `term-description-pairs`) или 2
(`record-map` глоссарий). Загрузчик отклоняет неизвестные поля,
несовпадающую версию, неверные типы, дубликаты языков/компонентов, небезопасные
имена компонентов, отсутствующий `source_lang`, несуществующие листы и колонки,
а также несовпадение заданных заголовков. Все номера строк и колонок в профиле
**1-based**, как в таблице.

Минимальная форма профиля:

```json
{
  "schema_version": 1,
  "components": [
    {
      "sheet": "Temple",
      "component": "Temple",
      "kind": "po",
      "source_lang": "ru",
      "header_row": 1,
      "first_data_row": 3,
      "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 3, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 4, "header": "en"}
      ],
      "key": {"column": 1, "header": "id"},
      "comments": [{"column": 2, "name": "Character", "header": ""}],
      "grammar": {
        "type": "keyed",
        "skip_rows": [2],
        "allow_blank_rows": true
      }
    },
    {
      "sheet": "Terms",
      "component": "Terms",
      "kind": "tbx",
      "source_lang": "ru",
      "header_row": 1,
      "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
        {"code": "ja", "xml_lang": "ja", "column": 6, "header": "ja"}
      ],
      "grammar": {
        "type": "term-description-pairs",
        "skip_rows": [2, 18],
        "regions": [
          {"section_row": 3, "first_term_row": 4, "last_description_row": 17},
          {"section_row": 19, "first_term_row": 20, "last_description_row": 29}
        ]
      },
      "key_language": "en",
      "initial_target_languages": ["en", "ja"]
    }
  ]
}
```

Пример профиля v2 `record-map` для табличного глоссария с одной строкой на
термин и per-record колонкой домена:

```json
{
  "schema_version": 2,
  "components": [
    {
      "sheet": "Glossary",
      "component": "CoL4-Glossary",
      "kind": "tbx",
      "source_lang": "ru",
      "header_row": 1,
      "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 3, "header": "en"}
      ],
      "grammar": {
        "type": "record-map",
        "skip_rows": [],
        "regions": [
          {"first_record_row": 2, "last_record_row": 7, "record_stride": 1}
        ],
        "term_row_offset": 0,
        "section_field": {"column": 1, "header": "domain", "row_offset": 0},
        "notes": [
          {"scope": "source", "column": 4, "header": "note_ru", "row_offset": 0},
          {"scope": "target", "language": "en", "column": 5, "header": "note_en", "row_offset": 0}
        ]
      },
      "initial_target_languages": ["en"]
    }
  ]
}
```

`record_stride: 2` представляет чередующийся кит term/description; вместо
`section_field` регион тогда задаёт `section_row`+`section_column` как
ячейку-заголовок над блоком.

Это пример структуры, не профиль реального содержимого. `header` проверяется
как ровно та ячейка строки заголовка, а `name` комментария - явная подпись, а не
выведенное из следующей строки предположение. `component` - безопасный
идентификатор `[A-Za-z0-9][A-Za-z0-9_-]*`, не имя листа; это исключает path
traversal, регистронезависимые коллизии и внезапные имена файлов.

Пустая строка - допустимое значение `header` для любой колонки, включая
`section_field` и `notes`: профиль тем самым требует, чтобы ячейка строки
заголовка была пустой. Это обычная форма таблиц, где первая колонка содержит
описание термина и не подписана.

### Закрытая схема v1 и v2

Поддерживаются две версии схемы. `schema_version: 1` сохраняет свою точную
интерпретацию: PO-киты и `term-description-pairs` TBX. `schema_version: 2`
добавляет аддитивно и только один TBX-грамматику `record-map`; она никогда не
выводится из заголовка автоматически, её поставляет либо оператор, либо
OpenRouter-кандидат с последующей локальной валидацией. Документ v1 читается как
v1 и не переинтерпретируется как v2. Все объекты обеих версий закрыты: поле, не
перечисленное ниже, является `profile.unknown_field`, а не запасным источником
данных.

| Объект | Разрешённые поля | Обязательные поля |
|---|---|---|
| Корень | `schema_version`, `components` | оба |
| Общий component | `sheet`, `component`, `kind`, `source_lang`, `header_row`, `languages`, `grammar` | все |
| PO component | common + `first_data_row`, `key`, `comments`, `references` | `first_data_row`, `key`; массивы необязательны, default `[]` |
| TBX component | common + `key_language`, `initial_target_languages` | оба |
| Языковая колонка | `code`, `xml_lang`, `column`, `header` | все |
| Key/metadata column | `column`, `header`, `name` | key: `column`, `header`; metadata: все |
| Keyed grammar | `type`, `skip_rows`, `allow_blank_rows` | `type`; пропущенные `skip_rows` = `[]`, `allow_blank_rows` = `false` |
| Pair grammar | `type`, `skip_rows`, `regions` | `type`, `regions`; пропущенные `skip_rows` = `[]` |
| Pair region | `section_row`, `first_term_row`, `last_description_row` | все |
| TBX v2 component | common + `initial_target_languages` (без `key_language`) | `initial_target_languages` |
| Record-map grammar (v2) | `type`, `skip_rows`, `regions`, `term_row_offset`, `section_field`, `notes` | `type`, `regions`, `term_row_offset`; `section_field` и `notes` необязательны |
| Record region | `first_record_row`, `last_record_row`, `record_stride`, `section_row`, `section_column` | `first_record_row`, `last_record_row`, `record_stride`; `section_row`+`section_column` идут вместе и опциональны |
| Section field | `column`, `header`, `row_offset` | все |
| Note field | `scope`, `column`, `header`, `row_offset`, `language` | `scope`, `column`, `header`, `row_offset`; `language` обязателен для `scope: "target"`, запрещён для `scope: "source"` |

`comments` и `references` используют объект metadata-column. `key` использует
key-column object, поэтому у него нет `name`. `first_data_row`, `key`,
`comments` и `references` запрещены для TBX; `key_language` и
`initial_target_languages` запрещены для PO. Это запрещает полукейсовую или
полупарную интерпретацию до открытия workbook.

Для v2 `key_language` отсутствует (он не нужен: context строится Unicode-безопасно
из `(section, term)`, а не из латинской key-колонки); v1 сохраняет `key_language`
только для совместимости схемы. `record_stride` - положительное целое; каждый
`row_offset` лежит в `[0, record_stride)`. Диапазон региона должен делиться на
`record_stride` нацело; регионы, строки секций и skip-строки не пересекаются.
Компонент объявляет либо `grammar.section_field`, либо region-ячейки секции, но
никогда оба (`profile.section_conflict`). `section_row` и `section_column` идут
вместе и проверяются по диапазону. Колонка `section_field` не должна совпадать с
языковой или note-колонкой. target-note обязан назвать один из
`initial_target_languages`; source-note не имеет поля `language`. Заголовки
языков, `section_field` и заметок проверяются на точное равенство со строкой
заголовка листа.

### Детерминированная грамматика

`kind: "po"` поддерживает только `grammar.type: "keyed"`: после
`first_data_row` разрешены явные `skip_rows` и пустые строки (если
`allow_blank_rows`); каждая иная строка обязана иметь ключ и source value.
`comments` и `references` берутся только из колонок профиля.

`kind: "tbx"` поддерживает только `grammar.type: "term-description-pairs"`.
Каждый region задаёт строку секции и чётный непрерывный диапазон
`term, description, term, description, ...`. Все непустые строки после
заголовка должны быть либо явно skipped, либо принадлежать ровно одному region.
Описание без термина, термин без описания, пересекающиеся/дырявые ranges и
неожиданная строка являются error, а не эвристикой "длиннее 200 символов".

### Record-map (v2): глоссарий как повторяющиеся группы строк

`schema_version: 2` вводит единственную новую TBX-грамматику
`grammar.type: "record-map"`. Это обобщённый движок для табличного глоссария с
фиксированным повторяющимся макетом, а не специализированный парсер конкретного
кита. Запись (record) - это повторяющаяся группа из `record_stride` строк.
Термины языков читаются на смещении `term_row_offset`, а каждое note-поле - на
своём `row_offset` и в своей `column`. Таким образом таблица с одной строкой на
термин имеет `stride: 1`, а чередующийся кит term/description - `stride: 2`.

Секция записи (домен) берётся ровно из одного из двух источников, никогда из
обоих:

- `grammar.section_field` - колонка, читаемая один раз для каждой записи. Это
  предпочтительная форма: плоская таблица, где каждая строка несёт свой домен.
  Пустая ячейка `section_field` означает "без секции" - это не ошибка, и секция
  не наследуется от предыдущей записи.
- `region.section_row` + `region.section_column` - ячейка-заголовок над блоком
  записей. Поддерживает таблицы, группирующие термины под заголовками, а не
  повторяющие домен в каждой строке. Ячейка-заголовок может дублировать
  перевод названия секции в объявленных языковых колонках - это метаданные
  секции, они никогда не становятся терминами; любая иная заполненная ячейка на
  строке-заголовке не учтена полем и падает как `tbx.unmapped_cell`.

Компонент без обоих источников имеет записи без секции. Для каждой записи: термины
источника и всех целевых языков читаются из объявленных языковых колонок;
заметки читаются в порядке объявления, source-заметки и target-заметки (по
целевому языку) группируются отдельно и объединяются переносами строки с
отбрасыванием пустых. Все объявленные ячейки term/section/note помечаются как
потреблённые. Заполненная ячейка данных внутри записи, которую не читает ни одно
объявленное поле, падает как `tbx.unmapped_cell` - данные никогда не
отбрасываются молча. Сохраняется защита `grammar.uncovered_row` для любой
непустой строки после заголовка, не покрытой регионом, строкой секции или явным
skip.

**Context identity.** Стабильный collision-free context термина - это каноническая
сериализация пары `(section, source_term)`:
`json.dumps([section, source_term], ensure_ascii=False, separators=(",",":"))` -
например `["Персонажи","Герой"]`. JSON-quoting делает её collision-free: секция
или термин, содержащие разделитель, не могут подделать чужой ключ. Weblate хранит
`Unit.context` в `TextField`, поэтому Unicode (кириллица, CJK) сохраняется
целиком и глоссарию больше не нужна латинская key-колонка. Коллизия context -
error. Поле v1 `key_language` больше не определяет context: v1-профили
сохраняют его только для совместимости схемы, а их runtime-context теперь
считается тем же Unicode-безопасным помощником; v2 не имеет `key_language`
вовсе. Для PO значения ячеек не trim'ятся: пробелы по краям, tab, `\r\n`,
переносы, XML/Unity-разметка и entities передаются дальше как данные. Для TBX
также сохраняются внутренние пробелы, переносы, разметка и entities, но
leading/trailing whitespace у term или description является
`tbx.unsupported_outer_whitespace`: Translate Toolkit trim'ит его при записи
`<descrip>`/`<note>`, поэтому импортёр обязан остановиться, а не молча изменить
данные. `strip()` допустим только для проверки пустой ячейки, контроля
структуры и этого явного TBX validation.

`code` - код Weblate, использующийся в имени PO/TBX файла. `xml_lang` - явный
BCP-47 тег в `xml:lang`: например, `pt_PT` → `pt-PT`, `zh_Hans` → `zh-Hans`,
`zh_Hant` → `zh-Hant`. Нельзя неявно использовать код Weblate как XML-тег.
`code` должен соответствовать `[A-Za-z][A-Za-z0-9_]*`; коды уникальны после
`casefold()` внутри компонента, потому что входят в имена файлов на
регистронезависимой файловой системе. `xml_lang` должен соответствовать
`[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*` и также передаётся только как явное
значение профиля.

## Нормализованная модель и диагностика

После чтения каждый компонент становится одним из двух типов:

- `StringUnit`: key, значения по языкам, developer comments, references и
  физическая строка источника;
- `GlossaryTerm`: context, значения терминов по языкам, `source_explanation`
  (объединённые source-заметки), `target_explanations` (заметки по каждому
  целевому языку), section и физические строки термина и заметок (`term_row`,
  `note_rows`).

Вторая модель хранит объяснения как детерминированную конкатенацию объявленных
заметок. `source_explanation` - объединение source-заметок в порядке объявления;
`target_explanations` ставит в соответствие каждому целевому языку объединение
его target-заметок. Пустые ячейки заметок опускаются, непустые соединяются
переводом строки. Описание исходного языка, разметка и примечания целевого
языка не превращаются в отдельный термин или безымянный PO comment; `note_rows`
фиксирует физические строки заметок для диагностики.

Единый `Diagnostic` имеет `severity`, стабильный `code`, компонент, лист,
физическую строку и сообщение. `error` блокирует publish; `warning` остаётся в
отчёте, но не меняет текст и не блокирует чистый seed.

| Severity | Примеры |
|---|---|
| `error` | profile/schema/header/column/sheet mismatch, duplicate component/path/key/context, missing source, неизвестная строка, orphan term/description, unmapped cell (`tbx.unmapped_cell`), нечитабельный файл, ошибка записи/ZIP/contract validation |
| `warning` | target equals source, подозрительный алфавит в языковой колонке, пустой target в PO, заметка вместо перевода, явно пропущенная пустая строка |

Отчёт содержит число юнитов, typed skipped rows и все diagnostics с адресом
`component:sheet!row`. При error он идёт в stderr, CLI возвращает nonzero и
`report.txt` не создаётся. При success `report.txt` публикуется внутри output и
дублируется в stdout.

## Артефакты и импорт в Weblate

### Temple и UI: монолингвальный PO

```text
out/
  Temple/
    ru.po                 # source template
    en.po
    ...
  UI/
    en.po                 # source template
    ru.po
```

Для PO: `msgid` = key/context, `msgstr` = значение соответствующего языка;
source PO содержит исходные строки. `#.` хранит developer comments, `#:` -
числовые Id. Это сохраняет `Character` для Temple и Id для UI. Игровая разметка
остаётся внутри `msgstr`; `game-markup` сравнивает её после загрузки в Weblate.

Weblate component для Temple/UI:

| Поле | Значение |
|---|---|
| File mask | `*.po` внутри ZIP компонента |
| Monolingual base language file | `<source_lang>.po` |
| File format | `gettext PO file (monolingual)` |
| Source language | `source_lang` из profile |

### Terms: двуязычный TBX по целевому языку

```text
out/
  Terms/
    tbx/
      en.tbx              # ru source + en target
      ja.tbx              # ru source + ja target
```

У Terms **нет** `ru.tbx`: Weblate уже хранит source language и воспринимает
файл с её именем как duplicate language. Для каждого
`initial_target_languages` writer создаёт отдельный bilingual TBX с двумя
`langSet`: source и target. Каждый выбранный target обязан иметь непустой term
во всех Terms entries; иначе import блокируется до publish.

TBX хранит source explanation в `<descrip>`, target explanation в
`<note from="translator">`. Это ровно то, что читает `TBXUnit` и передаёт
`weblate.machinery.llm._get_glossary_entry` как `source_explanation` и
`target_explanation`.

Weblate glossary component для Terms:

| Поле | Значение |
|---|---|
| File mask | `tbx/*.tbx` |
| Monolingual base language file | Empty |
| Template for new translations | Empty |
| File format | `TermBase eXchange file` |
| Source language | `source_lang` из profile |
| Use as glossary | включить |

Weblate сопоставляет target glossary language с именем TBX файла, даже если TBX
содержит более двух языков. Поэтому multilanguage TBX не используется.

## Атомарный CLI

```text
profile + kit
  -> strict profile and input validation
  -> deterministic per-kind parsing
  -> diagnostics
  -> errors? ----yes----> stderr + exit 2; no output mutation
       |
       no
       v
  -> sibling staging directory
  -> PO/TBX render + parse-back validation + ZIP + report
  -> errors? ----yes----> remove staging + exit 2; output unchanged
       |
       no
       v
  -> atomic rename staging -> requested output
  -> stdout report + exit 0
```

`--out` must not exist. The parent directory must exist and staging is created
under it, so `os.replace()` is an atomic same-filesystem publish. ZIP files and
`report.txt` are created in staging and move in the same rename. A previous
output tree is never merged, overwritten or deleted. A successful `--zip`
creates one archive per component with the component's files at the ZIP root:
`Temple.zip` contains `ru.po`, `en.po`, ...; `Terms.zip` contains `tbx/en.tbx`,
`tbx/ja.tbx`, ... .

CLI остаётся детерминированным и офлайн: `--profile` - единственный вход для
v2 `record-map` глоссария, и его справка упоминает это. В CLI нет `--terms`,
`--suggest-profile` и никакой сетевой зависимости или вызова OpenRouter;
кандидат-профиль и весь UI-воркфлоун живут только в Weblate.

## UI-сценарии

- **C1 - строковый компонент из кита (основной путь):** проект → «Добавить
  компонент» → вкладка «Отправить файлы перевода» → выбрать сам CSV/TSV/XLSX.
  Create-форма приходит заполненной (`po-mono`, `*.po`, шаблон и язык-источник
  из кита), отчёт конверсии - сообщениями интерфейса. Проверить число строк,
  key/context, `Character` developer comment, UI Id location и разметку.
  CLI-вариант того же: `Temple.zip` через ту же вкладку (ZIP идёт историческим
  путём с Discover).
- **C2 - ошибки до go-live:** дубликаты ключей блокируют загрузку со списком
  строк («first seen at row N») - починить таблицу и загрузить заново;
  предупреждения (wrong script, пустые target) видимы, но не блокируют.
  Warnings are visible evidence, not silent data repair.
- **C3 - Terms glossary:** upload `Terms.zip`, configure `tbx/*.tbx`, empty
  base/template, source language and glossary checkbox. Check a term's source
  and target explanations in glossary UI.
- **C4 - glossary before LLM:** translate and review Terms first. An empty target
  glossary term is intentionally omitted by LLM payload construction. Then run
  Routed LLM for a regular component per target language.
- **C5 - post-import:** add target languages, attach `openrouter` machinery,
  enable `game-markup` through `WEBLATE_ADD_CHECK`, and configure PO formatting
  before the first VCS commit when needed.
- **C6 - глоссарий из таблицы (v2 `record-map`):** загрузить CSV/TSV/XLSX с
  отмеченной **Use as glossary**, выбрать лист, проверить предложенный профиль
  (источник и цели, термины, заметки, скачиваемый JSON), при желании
  исправить `.loc-ingest.json` и перезагрузить. Локальная валидация
  (render → parse-back) обязана пройти до создания компонента. После создания
  проверить `file_format="tbx"`, `filemask="tbx/*.tbx"`, пустой template,
  `is_glossary=True`, source/target explanations и Unicode-context в реальном
  TBX и совпадение с панелью глоссария компонента с тем же языком-источником.
  Source-only импорт и флаги глоссария (`forbidden`/`read-only`/`terminology`)
  не поддерживаются.

## Verification contract

1. Fast standalone tests use anonymized fixtures and exercise strict profile,
   header inference, reader, both parsers, diagnostics, renderers, atomic
   failure matrix and ZIP contents. No Django database is loaded.
2. Weblate contract tests run under the repository test settings and load the
   generated PO/TBX through Weblate's actual formats/components, plus drive the
   universal upload view end to end (CSV in, live component out: N languages,
   context=key, developer comments). They assert key/context/source/target,
   comments/references, TBX explanation fields and LLM glossary payload
   structure.
3. A live LLM smoke is opt-in (`LOC_INGEST_LIVE_LLM=1`) and reads the
   `openrouter` machinery configuration from the live database; it spends
   exactly one real request through `download_multiple_translations`.
   Verified passing on 2026-08-06.
4. The manual smoke uploads a real kit CSV through the UI at `localhost:3001`
   and verifies C1-C2 on a live component (performed on `heart-abyss/temple`).
