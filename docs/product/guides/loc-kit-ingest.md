# Импорт лок-китов: кит → Weblate (разовый онбординг-сев)

Как один раз перенести лок-кит игры Hero Craft из Excel/CSV/TSV в Weblate.
Два равноправных входа: универсальная загрузка в UI («Отправить файлы
перевода» принимает сам кит) и CLI `python -m loc_kit_ingest`. Оба выводят
профиль из заголовка кита, создают совместимые с Weblate артефакты и передают
источник правды Weblate.

План реализации: `docs/product/plans/2026-08-06-loc-kit-ingest.md`.

Проверено как вход: `Temple.csv` (диалоги), `Terms.csv` (глоссарий/лор) и
`UI.xlsx` (UI-строки). Реальные киты не попадают в тестовые фикстуры. Смежные
конвенции форка: `docs/guides/continuous-localization-loop.md` (git ↔ Weblate,
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

Для `.csv` импортёр выбирает между запятой, точкой с запятой и tab тем
разделителем, при котором в верхних строках впервые распознаётся заголовок с
кодами языков. Ничья и файл без языкового заголовка остаются запятой, как в
прежнем поведении. Это устойчивость чтения, а не новая эвристика парсера:
после чтения парсер по-прежнему принимает только явно объявленную структуру и
не использует длину текста для угадывания полей.

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

**Коды, которыми игры называют языки.** Заголовок распознаётся через
`loc_kit_ingest/langcode.py`, где кроме реестра Translate Toolkit лежит карта
алиасов для кодов, которых в реестре нет: `ch`, `ch-s`, `cn` → `zh_Hans`,
`ch-t`, `zh-TC` → `zh_Hant`, `jp` → `ja`, `kr` → `ko`. Код, который сам по себе
является языком, не переписывается: `pt` остаётся португальским, а решение
«в этом проекте pt значит pt-BR» - это `language_aliases` проекта Weblate
(`weblate/trans/models/project.py`), а не свойство кита.

Заголовок, похожий на код языка, но не опознанный (`chn`), по-прежнему
становится комментарием разработчика, и заметка это прямо говорит: иначе целый
язык бесшумно превращается в комментарий к каждой строке.

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
   заполненные в каждой строке-**термина**. Не более одной заполненной
   колонки может нести примечание к термину: заголовок сравнивается точно
   (`header.strip().casefold()`) с закрытым списком `_NOTE_HEADERS` (`note`,
   `description`, `comment`, `explanation`, `context` и их русские аналоги);
   её текст входит в `grammar.notes` как `scope: "source"`, `row_offset: 0`
   и становится Explanation термина, который автоматические сервисы
   подсказок получают как контекст. Пустая распознанная колонка исключается
   с предупреждением и не конкурирует с единственной заполненной; две
   заполненные распознанные колонки - отказ. Любая другая заполненная
   не-языковая колонка отклоняется (`InferenceError`) - парсер не угадывает
   назначение колонки. Каждый непрерывный блок строк классифицируется отдельно:
   чередование «короткая строка / длинный текст» даёт `record_stride: 2`
   (термин и его описание, описания уходят в `grammar.notes` с
   `row_offset: 1`), нечётный блок с таким чередованием объявляет первую
   строку `section_row`, отсутствие длинных строк - `record_stride: 1`.
   Длинные строки без чередования читаются как термины с предупреждением.
   Все блоки листа обязаны получить одну раскладку: `notes` и
   `term_row_offset` - поля грамматики, а не региона. Порог «длинного» текста
   (80 символов и вчетверо больше медианы терминов) - это предложение
   кандидата, а не семантика разбора: профиль всё равно проходит полный гейт,
   а человек видит термины до создания компонента. Успех сразу даёт локально
   валидированный превью (шаг 4); отказ переходит к шагу 3.
   An exact, case-insensitive `flags` header declares source-scoped glossary
   modes. Each non-empty cell is a comma-separated set containing only
   `read-only` and `forbidden`; unknown, parameterized, or orphaned values are
   rejected before preview.
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
5a. **Переключение раскладки.** Если детекция ошиблась, оператор
   переключает раскладку на превью двумя кнопками («Каждая строка - термин» /
   «Термин, описание на следующей строке»). Вывод перезапускается локально с
   явной раскладкой и проходит тот же гейт; значение вне
   `{flat, pairs}` - 404. Это заменяет ручную правку JSON в самом частом
   случае ошибки.
6. **Обязательный parse-back до создания.** Рендер TBX и parse-back выполняются
   до создания компонента - это ворота публикации, а не пост-проверка. Только
   после успеха оператор подтверждает создание, которое донастраивает
   `file_format="tbx"`, `filemask="tbx/*.tbx"`, пустой template, профильный
   язык-источник и `is_glossary=True`; эти поля неизменяемы в финальной форме.
   Imported terms are marked as terminology, so they also appear in glossary languages added later.

**Canonical producer template:**

```csv
en,ru,definition,flags
HeroCraft,HeroCraft,company name,read-only
Vessel,Судно,never use this wording; use Ship,forbidden
```

The leftmost language is the source. Other language headers must be Weblate
language codes. `definition` is a source explanation; any exact header from
`_NOTE_HEADERS` is accepted. `flags` is optional and carries only `read-only`
and `forbidden`, separated by commas. Weblate shows normalized flags in the
preview, writes them into TBX, and adds `terminology` automatically.

7. **Временный черновик.** Загруженный файл хранится в session-bound,
   owner-bound временном черновике не дольше одного часа; он удаляется при
   создании, отмене или периодической очистке Celery. Чужой владелец, другая
   сессия, истёкший или consumed-токен ведут себя как отсутствующий.

**LLM data minimization.** Only a bounded structural sample leaves Weblate:
sheet metadata, fill rates, headers and capped cell excerpts, never the whole
file. Glossary flags are parsed and validated locally and are not sent to the
profile analyzer as executable instructions. Source-only imports remain
unsupported: one source and at least one target language are required.

### Пополнение существующего глоссария: append-only

Отдельный от создания поток: оператор добавляет новую партию терминов в уже
существующий TBX-глоссарий, ничего в нём не перезаписывая.

1. **Вход.** В меню «Files» компонента-глоссария (видно при `upload.perform`
   на компонент и `object.is_glossary`) появляется пункт «Add new glossary
   terms from a loc-kit table», ведущий на форму загрузки таблицы,
   привязанной к этому конкретному компоненту (`target_component`), а не к
   мастеру создания. Черновик для такой загрузки гейтится тем же
   `upload.perform` на целевой глоссарий, а не project-level правом на
   создание компонента: `translation.add` намеренно не требуется здесь -
   без него оператор всё ещё может пополнить языки, которые уже существуют.
2. **Тот же превью-конвейер.** Выбор листа, детерминированный вывод,
   LLM-fallback, ручная коррекция профиля и parse-back-валидация не
   отличаются от создания. По нажатию **Add new terms** сервер заново читает
   лист черновика и заново вызывает `validate_glossary_profile` с
   `component_name=component.slug` - preview JSON, показанный оператору,
   никогда не становится входом применения напрямую.
3. **Identity - `(context, source)`.** Так же, как при первичном импорте,
   термин считается тем же самым, если совпадает пара «контекст, source».
   Совпадение по identity - старый термин: его target, notes и флаги
   игнорируются, что бы ни лежало в этой строке таблицы.
4. **Существующие термины не меняются.** Target, explanation (source и
   target) и extra flags уже существующего термина остаются как есть даже
   если таблица несёт другое значение для того же identity.
5. **Blank-ячейки и отсутствующие колонки - частичный success, не ошибка.**
   Пустая ячейка target для нового термина не создаёт unit и учитывается в
   `blank`; язык глоссария, у которого в таблице вовсе нет колонки, отмечен
   `absent`. Ни то ни другое не блокирует остальные языки того же apply.
6. **Отсутствующий target-язык создаётся автоматически**, если хотя бы одна
   непустая ячейка нового термина требует его: чек прав идентичен
   стандартной форме Weblate (`translation.add` на проект,
   `translation.add_more` снимает `filter_for_add`, `glossary.add` -
   отдельно). Без нужных прав или без права `unit.add` на существующий
   translation язык считается `unavailable` с причиной для оператора; это
   никогда не отменяет применение остальных языков того же apply.
7. **Результат - per-language, без ложной полноты.** Ответ на apply
   перечисляет `added`/`existing`/`blank`/`absent`/`unavailable` для каждого
   языка отдельно; общее сообщение не утверждает, что таблица применена
   полностью, если хотя бы один язык остался недоступен.
8. **Notes нового термина сохраняются.** Source explanation и explanation
   на каждом добавленном target реально попадают в БД тем же путём, что и
   при первичном импорте (`Unit.update_explanation`); отсутствие notes не
   блокирует добавление термина.
   Source flags on a new term are also preserved: imported `read-only` and
   `forbidden` values are merged with the automatic `terminology` flag.
   Existing terms keep their current flags.
9. **Apply блокируется целиком, если:** source language таблицы не
   совпадает с source language компонента, или один и тот же source
   встречается под другим context, чем уже существующий в глоссарии
   (`GlossaryAppendCollisionError`) - либо между таблицей и глоссарием, либо
   между двумя новыми строками таблицы. В обоих случаях черновик и
   загруженный файл сохраняются, ничего не пишется в БД, и оператор
   возвращается на тот же preview с объяснением конфликта.
10. **Terminology sync.** Каждый новый source term помечается флагом
    `terminology`, как и при первичном импорте, поэтому фоновая
    `sync_terminology` подхватывает его в языках, добавленных позже -
    структурной пустой парой, не меняя уже переведённые target.

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
добавляет аддитивно и только один TBX-грамматику `record-map`; для
документированных простых раскладок (одна строка на термин, term/description-
пары, опциональная колонка примечания) она выводится локально из заголовка
кита. Для любой другой формы листа профиль поставляет либо оператор вручную,
либо OpenRouter-кандидат с последующей локальной валидацией. Документ v1
читается как
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
| Record-map grammar (v2) | `type`, `skip_rows`, `regions`, `term_row_offset`, `section_field`, `notes`, `source_flags` | `type`, `regions`, `term_row_offset`; `section_field`, `notes` and `source_flags` are optional |
| Record region | `first_record_row`, `last_record_row`, `record_stride`, `section_row`, `section_column` | `first_record_row`, `last_record_row`, `record_stride`; `section_row`+`section_column` идут вместе и опциональны |
| Section field | `column`, `header`, `row_offset` | все |
| Note field | `scope`, `column`, `header`, `row_offset`, `language` | `scope`, `column`, `header`, `row_offset`; `language` обязателен для `scope: "target"`, запрещён для `scope: "source"` |
| Source flags field | `column`, `header`, `row_offset` | all |

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

`grammar.ignored_columns` перечисляет технические колонки как пары
`column`/`header`. Заголовок каждой такой колонки обязан совпасть с листом,
иначе profile отклоняется. Заполненная ячейка допустима без
`tbx.unmapped_cell` только в объявленной ignored-колонке; неизвестная
заполненная колонка остаётся error. `grammar.allow_empty_targets: true`
разрешает пустой term в целевом языке record-map. Сгенерированный профиль
ставит этот флаг только если target-язык содержит хотя бы один term, но имеет
пропуски; иначе `tbx.missing_target_term` остаётся error.

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
также сохраняются внутренние пробелы, переносы, разметка и entities, а
leading/trailing whitespace обрезается с диагностикой
`tbx.trimmed_outer_whitespace` (warning) - одинаково у **term** и у
**description/note**. Translate Toolkit всё равно trim'ит значение при записи
`<term>`/`<descrip>`/`<note>`, поэтому импортёр обрезает его сам, сообщает об
этом в превью и сохраняет parse-back точным. Отказ был бы строже данных:
хвостовой пробел в имени термина не несёт смысла, а кит с ним стал бы
неимпортируемым навсегда. Обрезанный термин участвует и в context записи,
поэтому идентичность совпадает с тем, что реально попадёт в TBX. `strip()`
допустим только для проверки пустой ячейки, контроля структуры, этого явного
TBX validation и этой нормализации.

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
| `error` | profile/schema/header/column/sheet mismatch, duplicate component/path/key/context, ключ без текста хоть на одном языке (`po.key_without_content`), missing source term в глоссарии, missing target term without `allow_empty_targets`, неизвестная строка, orphan term/description, unmapped cell (`tbx.unmapped_cell`), нечитабельный файл, ошибка записи/ZIP/contract validation |
| `warning` | target equals source, подозрительный алфавит в языковой колонке, пустой target в PO, ключ без исходной строки (`po.missing_source`), заметка вместо перевода, явно пропущенная пустая строка |

### Ключ без исходной строки

Кит регулярно приносит ключ, переведённый только на неисходный язык: фича
в разработке, текст пишут сразу на языке команды. Такой ключ импортируется:
`po.missing_source` - warning, исходная строка остаётся пустой, переводы
сохраняются. В Weblate это юнит с `source == ""`, который в переводе исходного
языка виден как непереведённая строка - там его и дописывают.

Ключ, у которого пусты и источник, и все целевые ячейки, - это `error`
(`po.key_without_content`): пустая строка-ключ иначе завела бы пустой юнит в
шаблон и во все языковые файлы, не породив ни одного другого диагностического
сообщения (предупреждения о пустом target подавлены пустым источником).

Строка-баннер секции - другое дело: во всех языковых колонках пусто, а ключа
либо нет вовсе (текст стоит в колонке комментария: `,МЕНЮ,,`), либо он сам
является маркером (`# Юниты`). Переводить в ней нечего, поэтому инференс
заносит её в `grammar.skip_rows` и исключает из знаменателя fill-share, а
заметка профиля перечисляет номера таких строк. Ключ, который назван, но
нигде не заполнен, баннером не считается и остаётся `error`: это дырка в ките.

Отчёт и форма загрузки печатают число таких ключей отдельной строкой, потому
что в ките с тысячами warning его иначе не видно.

Следствие для проверок: у юнита с пустым источником нечего сравнивать, поэтому
`game-markup` срабатывает на всех переведённых языках такого ключа, а
автоперевод по нему давал бы мусор. И то и другое исчезает, когда источник
дописан.

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
`weblate.glossary.models.build_glossary_prompt_entry` как
`source_explanation` и `target_explanation`.

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
  `is_glossary=True`, source/target explanations, source flags and
  Unicode-context in the real TBX and the glossary panel of a component with
  the same source language. Source-only import is unsupported.

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
