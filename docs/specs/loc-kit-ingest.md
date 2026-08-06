# Импорт лок-китов: профиль → Weblate (разовый онбординг-сев)

Как один раз перенести лок-кит игры Hero Craft из Excel/CSV/TSV в Weblate. CLI
разбирает таблицу только по явному JSON-профилю, создаёт совместимые с Weblate
артефакты и передаёт источник правды Weblate.

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
- **Импортёр не угадывает.** Профиль обязателен, источник языка в нём обязателен,
  строки и колонки имеют заданную грамматику. Автоопределения, алиасов заголовков,
  hint-файлов и `--source-lang` нет.
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

### Рукописный профиль

Профиль имеет `schema_version: 1`. Загрузчик отклоняет неизвестные поля,
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

Это пример структуры, не профиль реального содержимого. `header` проверяется
как ровно та ячейка строки заголовка, а `name` комментария - явная подпись, а не
выведенное из следующей строки предположение. `component` - безопасный
идентификатор `[A-Za-z0-9][A-Za-z0-9_-]*`, не имя листа; это исключает path
traversal, регистронезависимые коллизии и внезапные имена файлов.

### Закрытая схема v1

Все объекты закрыты: поле, не перечисленное ниже, является
`profile.unknown_field`, а не запасным источником данных.

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

`comments` и `references` используют объект metadata-column. `key` использует
key-column object, поэтому у него нет `name`. `first_data_row`, `key`,
`comments` и `references` запрещены для TBX; `key_language` и
`initial_target_languages` запрещены для PO. Это запрещает полукейсовую или
полупарную интерпретацию до открытия workbook.

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

`key_language` определяет, из какой языковой колонки строится стабильный context
термина. В context включается section; коллизия context - error. Для PO значения
ячеек не trim'ятся: пробелы по краям, tab, `\r\n`, переносы, XML/Unity-разметка
и entities передаются дальше как данные. Для TBX также сохраняются внутренние
пробелы, переносы, разметка и entities, но leading/trailing whitespace у term
или description является `tbx.unsupported_outer_whitespace`: Translate Toolkit
trim'ит его при записи `<descrip>`/`<note>`, поэтому импортёр обязан остановиться,
а не молча изменить данные. `strip()` допустим только для проверки пустой ячейки,
контроля структуры и этого явного TBX validation.

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
- `GlossaryTerm`: context, значения по языкам, explanations по языкам, section
  и физические строки term/description.

Вторая модель сохраняет объяснение отдельно для каждого языка. Для Terms
source-language description становится `source_explanation`, а description
целевого языка - `target_explanation`; биография не превращается в отдельный
термин или безымянный PO comment.

Единый `Diagnostic` имеет `severity`, стабильный `code`, компонент, лист,
физическую строку и сообщение. `error` блокирует publish; `warning` остаётся в
отчёте, но не меняет текст и не блокирует чистый seed.

| Severity | Примеры |
|---|---|
| `error` | profile/schema/header/column/sheet mismatch, duplicate component/path/key/context, missing source, неизвестная строка, orphan term/description, нечитабельный файл, ошибка записи/ZIP/contract validation |
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

## UI-сценарии

- **C1 - PO component:** CLI → `Temple.zip`/`UI.zip` → Weblate project →
  "Upload translation files" → Discover. Check unit count against `report.txt`,
  key/context, `Character` developer comment, UI Id location and valid markup.
- **C2 - warning before go-live:** fix table/profile → rerun CLI → delete and
  recreate component. Warnings are visible evidence, not silent data repair.
- **C3 - Terms glossary:** upload `Terms.zip`, configure `tbx/*.tbx`, empty
  base/template, source language and glossary checkbox. Check a term's source
  and target explanations in glossary UI.
- **C4 - glossary before LLM:** translate and review Terms first. An empty target
  glossary term is intentionally omitted by LLM payload construction. Then run
  Routed LLM for a regular component per target language.
- **C5 - post-import:** add target languages, attach `routed-llm`, enable
  `game-markup` through `WEBLATE_ADD_CHECK`, and configure PO formatting before
  the first VCS commit when needed.

## Verification contract

1. Fast standalone tests use anonymized fixtures and exercise strict profile,
   reader, both parsers, diagnostics, renderers, atomic failure matrix and ZIP
   contents. No Django database is loaded.
2. Weblate contract tests run under the repository test settings and load the
   generated PO/TBX through Weblate's actual formats/components. They assert
   key/context/source/target, comments/references, TBX explanation fields and
   LLM glossary payload structure.
3. A live LLM smoke is opt-in (`LOC_INGEST_LIVE_LLM=1`), marked non-CI, scoped
   to a tiny temporary glossary/component and has an explicit OpenRouter-key
   prerequisite. It verifies one real routed response only after the deterministic
   payload contract is green.
4. The final manual smoke uploads one generated PO ZIP and one TBX ZIP to the
   local Weblate instance at `localhost:3001` and verifies C1-C4.
