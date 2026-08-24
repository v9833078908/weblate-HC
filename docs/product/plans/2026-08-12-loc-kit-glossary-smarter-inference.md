# План: «умнее» детерминированный разбор глоссарных таблиц (Парето, ревизия 3)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Реальные киты Hero Craft (`;`-разделитель, техническая колонка `id`, вендорный код `zh-TC`, частично переведённые языки) проходят детерминированный инференс глоссария и дают превью + живой TBX-компонент — без ручного JSON и без OpenRouter, **не отбрасывая данные молча**.

**Ревизия 3** устраняет расхождение между Task 1 и Task 2, делает распознавание CSV-header одним общим контрактом с `infer`, запрещает speculative-технические заголовки, не создаёт пустой target-язык из одних descriptions и делает Weblate-проверки выполнимыми из изолированного worktree.

**Статус (2026-08-15): реализован и проверен.** Детект разделителя,
алиасы языков, `ignored_columns`, partial targets и сквозной Weblate-путь
покрыты реализацией и тестами.

## Три реальных файла, которые должен пройти план

| Файл | Разделитель | Блокеры сегодня | Проверено |
|---|---|---|---|
| `heart_abyss_glossary_temple.csv` | `,` (был `;`, пересохранён 2026-08-12) | нет — проходит уже сегодня | `read_sheets` → `['ru', 'en', 'notes']`, 58 строк; инференс `ru → ['en']`, регион 2-58, note-колонка. Служит регресс-эталоном: детект разделителя не должен его сломать |
| `heart_abyss_glossary.csv` | `,` | `id`, `zh-TC`, частичные цели | `InferenceError: column 1 ('id') holds data but is not a recognised language column`; после удаления `id` и `zh-TC` остаётся `sheet has no fully filled target language column` |
| `heart_abyss_glossary (1).csv` | `;` | разделитель, `zh-TC`, частичные цели | `read_sheets` даёт одну ячейку `['ru;en;fr;…;notes']`; при `delimiter=';'` → `InferenceError: column 10 ('zh-TC') …` |

Пересохранение temple-файла между замерами — сам по себе аргумент: одна и та же таблица приходит от продюсера то запятой, то точкой с запятой, и импорт не должен зависеть от того, в каком виде её выгрузили.

Заполненность колонок реального кита (216 строк-терминов): `ru 216`, `en 204`, `ja 115`, `fr 67`, `it 66`, `de 66`, `pt-PT 66`, `es 66`, `zh-CN 65`, `zh-TC 65`, `ko 63`, `notes 216`. Минимум — `ko` 29.2%, то есть **порог `DEFAULT_MIN_FILL = 5.0` реальные киты проходят и снимать его не нужно**.

## Архитектура

```text
upload (.csv/.tsv/.xlsx)
   |
   v
reader.read_sheets
   |  .csv -> delimiter, под которым shared header matcher находит header (Task 1)
   |  .tsv -> tab, .xlsx -> openpyxl           (без изменений)
   v
infer_glossary_profile
   |  тот же shared header matcher -> код языка, + алиасы zh-TC/zh-SC   (Task 2)
   |  колонка-язык: пустая -> исключить
   |               numeric -> ОТКАЗ    (сохранено)
   |               < min_fill term-строк -> ОТКАЗ (сохранено)
   |  колонка не язык/не note:
   |       header == "id" -> ignored_columns  (Task 7)
   |       иначе          -> ОТКАЗ (как сейчас)
   |  цель: есть хотя бы один term; пропуски -> allow_empty_targets (Task 8)
   v
profile.parse_profile (schema v2, аддитивно)
   |  ignored_columns: [{column, header}]        (Task 3)
   |  allow_empty_targets: bool                  (Task 3)
   v
reader.validate_sheet_headers
   |  header каждой ignored-колонки сверяется с листом (Task 4)
   v
parser.parse_component (record-map)
   |  ячейка в ignored-колонке -> потреблена, не unmapped (Task 5)
   |  пустой target -> ошибка, если нет allow_empty_targets (Task 6)
   |  v1 term-description-pairs НЕ меняется
   v
writer.render_component + validate_rendered_component  (без изменений)
   v
preview -> confirm -> Component
        пустой target -> Unit.state = STATE_EMPTY
```

**Tech Stack:** Python 3.13, standalone `loc_kit_ingest` (без Django), Django/Weblate, pytest, translate-toolkit (TBX).

**Что проверено заранее (не перепроверять):**

- Пустой TBX-target проходит round-trip: рендер `terms.loc-ingest.json`-фикстуры с пустым `en`-термином даёт `<langSet xml:lang="en"><tig><term></term></tig></langSet>`, а `validate_rendered_component` возвращает **ноль** диагностик. Правок `writer.py` не требуется.
- Weblate импортирует пустой target как непереведённый юнит: `weblate/trans/models/translation.py:2580-2585` (`unit_state = STATE_TRANSLATED if has_translation else STATE_EMPTY`).
- `zh_Hant`/`zh_Hans` — канонические коды Weblate (`weblate/lang/data.py:22-32`, `UNDERSCORE_EXCEPTIONS`); `weblate/lang/tests.py:114-115` подтверждает `zh_HANT → zh_Hant`. `zh_TC` в `ALIASES` нет, поэтому без алиаса `auto_create` создал бы мусорный `zh_TC (generated)`.
- `csv.Sniffer().sniff(sample, delimiters=",;\t")` на реальном `heart_abyss_glossary_temple.csv` падает с `Could not determine delimiter` (в заметках свободный текст с запятыми). Sniffer использовать НЕЛЬЗЯ — нужен ограниченный детект по кандидатам.
- Ширина строк реальных китов постоянна: temple 58×3, `(1)` 217×12, исходный 217×13 — «рваных» строк нет.
- Дубликатов исходных терминов (context-коллизий) в обоих китах нет.

**Критично для исполнителя:**

- Работать только в отдельном worktree. До первого теста выполнить в корне worktree `uv sync --all-extras --dev`; `uv run pytest` из одного `loc_kit_ingest/` создаёт неполную среду без PostgreSQL-драйвера и не годится для Django-контрактов.
- Standalone-тесты: `cd loc_kit_ingest && uv run pytest tests/<file> -v` (без БД).
- Shared `dev-docker` примонтирован к основному checkout, а не к worktree. Из worktree **не** запускать `./rundev.sh test`, не копировать код в `dev-docker/data/python/` и не добавлять его в git: такой тест выполняет старый main-checkout, а не изменения ветки.
- Weblate-контракт выполнять host-side против уже опубликованного PostgreSQL на `5434`, с отдельным `CI_DB_NAME`, `DJANGO_SETTINGS_MODULE=weblate.settings_test` и Homebrew Git. Точный один-shell блок приведён в Task 10.
- Линтер — **targeted**: `uv run prek run --files <изменённые>` (как в `docs/product/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md:574`). `--all-files` может автофиксить чужие файлы.
- Все индексы в JSON-профиле 1-based, внутри dataclass 0-based. `ignored_columns[].column` — то же правило.
- PO-путь (`infer_component`/`infer_profile`/CLI), его `min_fill` и `--include-lang` не трогаем.
- v1-грамматика `term-description-pairs` (`parser.py:378-604`) не трогается вообще.

---

## Phase A — Разделитель CSV

### Task 1: выбрать разделитель `.csv` по распознаваемости заголовка

**Почему не по числу колонок.** Скорер «больше полей в первой непустой строке» измерим и измеримо неверен на 2 из 9 случаев, причём оба — ровно те, ради которых всё затевается:

```text
                                    по 1-й строке   по заголовку
plain semicolon                     ';'             ';'
plain comma                         ','             ','
semicolon + запятые в кавычках      ';'             ';'
banner без разделителя, затем ';'   ','   ОШИБКА    ';'
banner с запятой, затем ';'         ','   ОШИБКА    ';'
banner, затем ','                   ','             ','
kit layout (comma)                  ','             ','
kit layout (semicolon)              ';'             ';'
нет языков вообще                   ','             ','
```

`_find_header_row` (`infer.py:146-165`) существует именно потому, что первая непустая строка бывает баннером; при баннере без разделителя все кандидаты дают одну колонку, ничья уходит запятой, и `;`-лист снова не читается.

**Почему не `csv.Sniffer`.** Замерено на трёх реальных китах, четыре размера сэмпла (1024 / 4096 / 16384 / весь файл):

```text
heart_abyss_glossary_temple.csv (,)   ERR   ERR   ERR   ERR
heart_abyss_glossary.csv        (,)   ERR   ERR   ','   ','
heart_abyss_glossary (1).csv    (;)   ERR   ERR   ERR   ERR
```

Sniffer не просто не чинит `;` — он падает и на запятых, то есть сломал бы уже работающие загрузки, а на третьем файле его ответ зависит от размера сэмпла. В продукт не берём.

**Метрика.** Кандидат выигрывает тот, при котором лист вообще поддаётся разбору: ранжируем по **(индекс первой строки с кодом языка, затем число кодов в ней)**, ничья и «языков нет» → запятая (сегодняшнее поведение и сегодняшний текст ошибки сохраняются). Ранг по одному лишь числу кодов недостаточен и это измеримо: ячейка заметки `"ru; en; fr; de; it; ja"` в запятом ките даёт при `;` четыре кода против трёх у верного заголовка и переворачивает выбор; правило «самая ранняя строка» этот класс шума снимает, потому что настоящий заголовок стоит выше любой строки данных.

Главное — это не вторая эвристика рядом с инференсом. `langcode.header_language_codes` реализует ровно текущую семантику header: сначала ищет recognised-code правее ключевой колонки, затем добавляет первую колонку, только если она не технический `id`. И `infer._find_header_row`, и `reader._detect_delimiter` вызывают её. Поэтому `id;build` не становится ложным semicolon-header, а `ru;en` остаётся валидным header.

**Связность.** `reader` не должен зависеть от `infer` (сегодня зависимости нет, и `infer` — слой выше). Поэтому распознавание кода и header выносится в новый модуль `loc_kit_ingest/langcode.py`, который импортируют оба. Вариант «детектить в pipeline» отвергнут: у `read_sheets` три вызывающих (`weblate/trans/views/create.py:673`, `:1054`, CLI `pipeline`), детект уехал бы в три места.

**Files:**

- Create: `loc_kit_ingest/langcode.py`
- Modify: `loc_kit_ingest/infer.py:21-35,146-165` (shared matcher вместо локальной логики), `loc_kit_ingest/reader.py:22` (константа), `:29-43` (`read_sheets`), новая `_detect_delimiter`
- Test: `loc_kit_ingest/tests/test_reader.py`

**Step 1: написать падающие тесты**

Добавить в `loc_kit_ingest/tests/test_reader.py` рядом с существующим `test_csv_preserves_quoted_newlines_bom_and_trailing_spaces` (`:18-22`):

```python
def test_csv_detects_semicolon_with_bom_quoted_commas_and_newlines(tmp_path):
    """Реальная форма кита: BOM, ';', запятые и перенос строки внутри кавычек."""
    path = tmp_path / "Kit.csv"
    path.write_bytes(
        (
            "\ufeffru;en;notes\n"
            'Леон;Leon;"Имя собственное, мужской род.\nВторая строка."\n'
            "Аки;Aki;\n"
        ).encode()
    )
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["ru", "en", "notes"]
    assert rows[1] == ["Леон", "Leon", "Имя собственное, мужской род.\nВторая строка."]
    assert rows[2] == ["Аки", "Aki", ""]


@pytest.mark.parametrize(
    "banner",
    ["Локализация Heart Abyss", "Heart Abyss, Terms", "UI,,,"],
)
def test_csv_detects_semicolon_under_a_banner_row(tmp_path, banner: str):
    """
    The first non-empty line is not always the header.

    A banner carries no language code, so scoring it by field count picks the
    wrong delimiter - including the case where the banner's own comma outvotes
    the real semicolon header one line below.
    """
    path = tmp_path / "Kit.csv"
    path.write_bytes(f"{banner}\nru;en;notes\nЛеон;Leon;текст\n".encode())
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["ru", "en", "notes"]


def test_csv_keeps_comma_when_a_note_cell_is_full_of_language_codes(tmp_path):
    """A note that lists codes must not outvote the header that names them."""
    path = tmp_path / "Kit.csv"
    path.write_bytes(
        'id,ru,en,notes\nchar_leon,Леон,Leon,"ru; en; fr; de; it; ja"\n'.encode()
    )
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["id", "ru", "en", "notes"]


def test_csv_without_any_language_column_stays_comma(tmp_path):
    """No language anywhere: keep today's behaviour and today's error text."""
    path = tmp_path / "Kit.csv"
    path.write_bytes(b"key,value\na,b\n")
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["key", "value"]


def test_csv_technical_id_without_languages_stays_comma(tmp_path):
    """`id` is an engine key, not proof that a semicolon row is a header."""
    path = tmp_path / "Kit.csv"
    path.write_bytes(b"id;build\n42;123\n")
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["id;build"]


def test_csv_single_column_falls_back_to_comma(tmp_path):
    path = tmp_path / "Kit.csv"
    path.write_bytes("ru\nЛеон\nАки\n".encode("utf-8-sig"))
    rows = read_sheets(path)["Kit"]
    assert rows[0] == ["ru"]
    assert rows[1] == ["Леон"]


def test_tsv_keeps_tab_even_when_a_cell_holds_semicolons(tmp_path):
    path = tmp_path / "Kit.tsv"
    path.write_bytes("id\tru\ten\nkey\ta;b;c\ttext\n".encode())
    rows = read_sheets(path)["Kit"]
    assert rows[1] == ["key", "a;b;c", "text"]
```

`pytest` в `test_reader.py` уже импортирован (`:7`).

**Step 2: прогнать — новые semicolon-assertions упадут, baseline fallback останется зелёным**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py -k "semicolon or banner or language_codes or without_any_language or technical_id or single_column or keeps_tab" -v`
Expected: FAIL — все `;`-случаи с recognised header дают `rows[0] == ["ru;en;notes"]`. Тесты comma-fallback уже проходят: они фиксируют сохранённый контракт, а не новую функциональность.

**Step 3: реализация — общий модуль распознавания**

Создать `loc_kit_ingest/langcode.py` с GPL/SPDX-шапкой как в соседних модулях. Перенести `_PARENS_CODE`, `_BARE_CODE`, `_known_language` и `_language_code` из `infer.py`, переименовав функции в `known_language` и `language_code`. В этом task **не** добавлять вендорные aliases: ими занимается Task 2, поэтому его тест действительно будет red.

Рядом добавить `_KEY_HEADER_DENYLIST = frozenset({"id"})` и публичную функцию:

```python
def header_language_codes(row: Sequence[str]) -> dict[int, str]:
    """Return exactly the columns ``infer`` accepts as a language header."""
    found = {
        col: code
        for col in range(1, len(row))
        if (code := language_code(row[col])) is not None
    }
    if not found:
        return {}
    first = language_code(row[0]) if row else None
    if (
        first is not None
        and row[0].strip().casefold() not in _KEY_HEADER_DENYLIST
        and first not in found.values()
    ):
        return {0: first, **found}
    return found
```

Импортировать `Sequence` из `collections.abc`. В `infer.py` заменить локальные regex/functions и `_KEY_HEADER_DENYLIST` импортом `header_language_codes, language_code`; `_find_header_row` должен вызывать `header_language_codes(row)` и вернуть первый непустой результат. Не оставлять отдельный шаг promotion первой колонки: он уже находится в shared matcher.

**Step 4: реализация — детект в reader**

`loc_kit_ingest/reader.py`. Константы (`:22`):

```python
# CSV delimiter candidates, in tie-break priority order. `.tsv` is always tab
# and is never sniffed.
_CSV_DELIMITERS = (",", ";", "\t")

# Rows scanned when choosing a delimiter. A header sits at the top under at
# most a banner row or two; scanning further only adds free-text noise.
_DELIMITER_SCAN_ROWS = 20
```

`read_sheets` (`:37-43`):

```python
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return {path.stem: _read_csv(path, _detect_delimiter(path))}
    if suffix == ".tsv":
        return {path.stem: _read_csv(path, "\t")}
    if suffix == ".xlsx":
        return _read_xlsx(path)
    msg = f"unsupported file suffix: {suffix!r}"
    raise ReaderError(msg)
```

Новая функция рядом с `_read_csv`:

```python

def _detect_delimiter(path: Path) -> str:
    """
    Choose the delimiter under which the sheet has a recognisable header.

    Candidates are ranked by (index of the first recognisable header,
    number of language columns in it); a tie, or no header, keeps comma.
    The matcher is shared with ``infer._find_header_row``: a technical
    first-column ``id`` therefore cannot make ``id;build`` win as a header.

    ``csv.Sniffer`` is rejected because measured kits with free-text commas
    make it raise or vary by sample size. Field-count scoring is rejected
    because banner rows outvote the real header.
    """
    best: tuple[int, int, int, str] | None = None
    for priority, delimiter in enumerate(_CSV_DELIMITERS):
        for index, row in enumerate(_scan_rows(path, delimiter)):
            codes = header_language_codes(row)
            if codes:
                candidate = (index, -len(codes), priority, delimiter)
                if best is None or candidate < best:
                    best = candidate
                break
    return best[3] if best is not None else ","


def _scan_rows(path: Path, delimiter: str) -> list[list[str]]:
    """Parse the top of the file with one candidate; a candidate may be junk."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(
                islice(csv.reader(handle, delimiter=delimiter), _DELIMITER_SCAN_ROWS)
            )
    except csv.Error:
        return []
```

Добавить `from itertools import islice` и `from loc_kit_ingest.langcode import header_language_codes` в импорты `reader.py`.

Проверено на 14 синтетических формах и трёх реальных китах: все три файла из «Три реальных файла» получают верный разделитель, включая tab-файл, пустой файл и файл из одних пустых строк (оба последних → запятая).

**Step 5: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py tests/test_infer_glossary.py -v`
Expected: PASS, включая существующие `test_csv_preserves_quoted_newlines_bom_and_trailing_spaces` и `test_tsv_uses_tab_delimiter`.

**Step 6: проверка на реальных файлах (ручная, один раз)**

```bash
cd "/Users/eli/Documents/PythonProjects/gamedev tools/weblate"
.venv/bin/python -c "
from pathlib import Path
from loc_kit_ingest.reader import read_sheets
for name in ['heart_abyss_glossary_temple.csv', 'heart_abyss_glossary.csv', 'heart_abyss_glossary (1).csv']:
    rows = next(iter(read_sheets(Path('/Users/eli/Downloads') / name).values()))
    print(name, rows[0][:4], len(rows))
"
```

Expected: у всех трёх первая строка разобрана на колонки (`['ru', 'en', 'notes'…]` / `['id', 'ru', 'en'…]`), а не одной ячейкой.

**Step 7: commit**

```bash
git add loc_kit_ingest/langcode.py loc_kit_ingest/reader.py loc_kit_ingest/infer.py \
  loc_kit_ingest/tests/test_reader.py
git commit -m "feat(loc-kit): pick the CSV delimiter that makes the header recognisable"
```

---

## Phase B — Алиасы кодов языков

### Task 2: закрытая таблица алиасов вендорных кодов

**Files:**

- Modify: `loc_kit_ingest/langcode.py` (`_LANGUAGE_ALIASES`, `known_language`)
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: написать падающий тест**

В `loc_kit_ingest/tests/test_infer_glossary.py` добавить `from loc_kit_ingest.langcode import language_code` и:

```python
@pytest.mark.parametrize(
    ("header", "code"),
    [
        ("zh-TC", "zh_Hant"),
        ("zh_TC", "zh_Hant"),
        ("ZH_tc", "zh_Hant"),
        ("zh-SC", "zh_Hans"),
        ("zh-Hant", "zh_Hant"),
        ("Chinese (zh-TC)", "zh_Hant"),
        ("zh-CN", "zh_CN"),  # translate-toolkit, unchanged
        ("pt-PT", "pt_PT"),  # translate-toolkit, unchanged
        ("en", "en"),  # unchanged
        ("id", "id"),  # recognition; header policy rejects it separately
    ],
)
def test_vendor_language_codes_alias_to_canonical(header: str, code: str) -> None:
    assert language_code(header) == code
```

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k vendor_language -v`
Expected: FAIL — `zh-TC`, `zh_TC`, `ZH_tc`, `zh-SC`, `zh-Hant` и `Chinese (zh-TC)` дают `None`.

**Step 3: реализация**

В `loc_kit_ingest/langcode.py` после regex добавить закрытую таблицу:

```python
# Each alias is an exact Weblate canonical code. A broader guess could create
# a generated language row, so every future entry needs an explicit fixture.
_LANGUAGE_ALIASES = {
    "zh_tc": "zh_Hant",
    "zh_hant": "zh_Hant",
    "zh_sc": "zh_Hans",
    "zh_hans": "zh_Hans",
}
```

В `known_language` нормализовать `-` в `_`, затем проверить
`_LANGUAGE_ALIASES.get(norm.casefold())` **до** translate-toolkit registry.
Остальную логику registry перенести без изменений. `infer.py` не меняется в
этом task: он уже вызывает `language_code` из нового модуля.

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -v`
Expected: PASS.

**Step 5: commit**

```bash
git add loc_kit_ingest/langcode.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): alias vendor Chinese codes to canonical Weblate codes"
```

---

## Phase C — Схема профиля (аддитивно, schema v2)

### Task 3: `ignored_columns` и `allow_empty_targets` в record-map-грамматике

Оба поля аддитивны и опциональны: профиль без них парсится ровно как сегодня (`ignored_columns=()`, `allow_empty_targets=False`), версия схемы не меняется.

**Files:**

- Modify: `loc_kit_ingest/profile.py:103-112` (`_RECORD_MAP_GRAMMAR_FIELDS`), `:188-203` (dataclasses), `:591-699` (`_parse_record_map_grammar`), `:702-740` (`_check_record_map_field_locations`)
- Test: `loc_kit_ingest/tests/test_profile_v2.py`

**Step 1: написать падающие тесты**

`_one_row_document` (`test_profile_v2.py:33-86`) занимает колонки: 1 `section_field`, 2 `ru`, 3 `en`, 4 source-note, 5 target-note. Свободны 6+ — тесты обязаны использовать именно их.

```python
def test_ignored_columns_parse_to_zero_based_records():
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 6, "header": "id"},
        {"column": 7, "header": "build"},
    ]
    grammar = parse_profile(document).components[0].grammar
    assert grammar.ignored_columns == (
        IgnoredColumn(column=5, header="id"),
        IgnoredColumn(column=6, header="build"),
    )


def test_ignored_columns_default_to_empty():
    grammar = parse_profile(_one_row_document()).components[0].grammar
    assert grammar.ignored_columns == ()
    assert grammar.allow_empty_targets is False


@pytest.mark.parametrize(
    ("column", "what"),
    [
        (2, "language ru"),
        (3, "language en"),
        (4, "source note"),
        (5, "target note"),
        (1, "section field"),
    ],
)
def test_ignored_column_colliding_with_a_declared_field_is_rejected(column, what):
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": column, "header": "x"}
    ]
    with pytest.raises(ProfileError, match="profile.ignored_column_collision"):
        parse_profile(document)

def test_ignored_column_needs_a_positive_integer_column():
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 0, "header": "id"}
    ]
    with pytest.raises(ProfileError, match="profile.invalid_index"):
        parse_profile(document)


def test_duplicate_ignored_column_is_rejected():
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 6, "header": "id"},
        {"column": 6, "header": "id"},
    ]
    with pytest.raises(ProfileError, match="profile.duplicate_column"):
        parse_profile(document)


def test_ignored_column_colliding_with_region_caption_is_rejected():
    document = _one_row_document()
    grammar = document["components"][0]["grammar"]
    del grammar["section_field"]
    grammar["regions"][0].update(
        {
            "first_record_row": 3,
            "last_record_row": 8,
            "section_row": 2,
            "section_column": 6,
        }
    )
    grammar["ignored_columns"] = [{"column": 6, "header": "build"}]
    with pytest.raises(ProfileError, match="profile.ignored_column_collision"):
        parse_profile(document)


def test_allow_empty_targets_parses_as_bool():
    document = _one_row_document()
    document["components"][0]["grammar"]["allow_empty_targets"] = True
    assert parse_profile(document).components[0].grammar.allow_empty_targets is True


def test_allow_empty_targets_rejects_a_non_bool():
    document = _one_row_document()
    document["components"][0]["grammar"]["allow_empty_targets"] = "yes"
    with pytest.raises(ProfileError, match="profile.invalid_value"):
        parse_profile(document)
```

Импорт `IgnoredColumn` добавить в блок `from loc_kit_ingest.profile import (...)` (`test_profile_v2.py:20-28`).

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_profile_v2.py -k "ignored_column or allow_empty" -v`
Expected: FAIL — `_check_unknown` бросает `profile.unknown_field`.

**Step 3: реализация**

`loc_kit_ingest/profile.py`:

1. `_RECORD_MAP_GRAMMAR_FIELDS` (`:103-112`) — добавить два ключа:

```python
_RECORD_MAP_GRAMMAR_FIELDS = frozenset(
    {
        "type",
        "skip_rows",
        "regions",
        "term_row_offset",
        "section_field",
        "notes",
        "ignored_columns",
        "allow_empty_targets",
    }
)
_IGNORED_COLUMN_FIELDS = frozenset({"column", "header"})
```

2. Новый dataclass рядом с `NoteField` (`:179-185`) и два поля в `RecordMapGrammar` (`:197-203`):

```python
@dataclass(frozen=True)
class IgnoredColumn:
    """A populated column the kit carries for its engine, never for content."""

    column: int
    header: str


@dataclass(frozen=True)
class RecordMapGrammar:
    skip_rows: tuple[int, ...]
    regions: tuple[RecordRegion, ...]
    term_row_offset: int
    section_field: SectionField | None
    notes: tuple[NoteField, ...]
    ignored_columns: tuple[IgnoredColumn, ...] = ()
    allow_empty_targets: bool = False
```

3. `_parse_record_map_grammar` — распарсить оба поля после блока `notes` (`:623-629`):

```python
    ignored_raw = obj.get("ignored_columns", [])
    if not isinstance(ignored_raw, list):
        msg = "profile.invalid_value"
        raise _err(msg, "'ignored_columns' must be a list")
    ignored_columns = tuple(_parse_ignored_column(item) for item in ignored_raw)
    seen_ignored: set[int] = set()
    for ignored in ignored_columns:
        if ignored.column in seen_ignored:
            msg = "profile.duplicate_column"
            raise _err(
                msg, f"duplicate ignored column {ignored.column + 1}"
            )
        seen_ignored.add(ignored.column)

    allow_empty_targets = obj.get("allow_empty_targets", False)
    if not isinstance(allow_empty_targets, bool):
        msg = "profile.invalid_value"
        raise _err(msg, "'allow_empty_targets' must be a boolean")
```

и дополнить `return RecordMapGrammar(...)` (`:693-699`):

```python
    return RecordMapGrammar(
        skip_rows=skip_rows,
        regions=regions,
        term_row_offset=term_row_offset,
        section_field=section_field,
        notes=notes,
        ignored_columns=ignored_columns,
        allow_empty_targets=allow_empty_targets,
    )
```

4. Новый парсер рядом с `_parse_note_field` (`:483-517`):

```python
def _parse_ignored_column(obj: Any) -> IgnoredColumn:
    if not isinstance(obj, dict):
        msg = "profile.invalid_value"
        raise _err(msg, "an ignored column must be an object")
    _check_unknown(obj, _IGNORED_COLUMN_FIELDS, label="ignored column")
    column = _require_int(obj, "column", label="ignored column")
    header = _require(obj, "header", label="ignored column")
    if not isinstance(header, str):
        msg = "profile.invalid_value"
        raise _err(msg, "an ignored column header must be a string")
    return IgnoredColumn(column=column - 1, header=header)
```

`_require_int` уже отвергает нецелые и `< 1` значения кодом `profile.invalid_index` — свериться с его сигнатурой перед написанием и не дублировать проверку.

5. `_check_record_map_field_locations` (`:702-740`) — коллизии в конце функции. `ignored_columns` описывает данные, которые парсер *не* читает, поэтому оно не может совпадать ни с языком, ни с note, ни с обоими видами section:

```python
    if grammar.ignored_columns:
        declared = {lang.column for lang in languages}
        declared |= {note.column for note in grammar.notes}
        if grammar.section_field is not None:
            declared.add(grammar.section_field.column)
        declared |= {
            region.section_column
            for region in grammar.regions
            if region.section_column is not None
        }
        collision = sorted(
            ignored.column for ignored in grammar.ignored_columns
            if ignored.column in declared
        )
        if collision:
            msg = "profile.ignored_column_collision"
            raise _err(
                msg,
                f"ignored columns {[col + 1 for col in collision]} collide with a "
                "declared language, note, or section column",
            )
```

`profile.py` не объявляет `__all__`, поэтому список экспорта править не нужно — достаточно определить `IgnoredColumn` рядом с соседними dataclass'ами.

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_profile_v2.py tests/test_profile.py -v`
Expected: PASS (v1-профили не затронуты — `_TBX_FIELDS`/`_parse_pairs_grammar` без изменений).

**Step 5: commit**

```bash
git add loc_kit_ingest/profile.py loc_kit_ingest/tests/test_profile_v2.py
git commit -m "feat(loc-kit): declare ignored columns and empty targets in v2 grammar"
```

---

### Task 4: header игнорируемой колонки сверяется с листом

Без этого профиль, переиспользованный на другом экспорте, молча пропустит уже другую колонку — ровно та потеря данных, которую запрещает спека.

**Files:**

- Modify: `loc_kit_ingest/reader.py:141-156` (record-map-ветка `validate_sheet_headers`)
- Test: `loc_kit_ingest/tests/test_reader.py`

**Step 1: написать падающий тест**

Рядом с `test_unnamed_note_column_rejects_a_named_header_cell` (`test_reader.py:184-189`), по образцу фикстуры `unnamed_description_component` (`:134-174`) добавить фикстуру с `ignored_columns` и тесты:

```python
@pytest.fixture
def ignored_id_component():
    """A record-map glossary that declares an ignored technical id column."""
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Glossary",
                "component": "Glossary",
                "kind": "tbx",
                "source_lang": "ru",
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 3, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": 2,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "ignored_columns": [{"column": 1, "header": "id"}],
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    return parse_profile(document).components[0]


def test_ignored_column_header_matches_the_sheet(ignored_id_component):
    rows = [["id", "ru", "en"], ["char_leon", "Леон", "Leon"]]
    assert validate_sheet_headers(ignored_id_component, rows) == ()


@pytest.mark.parametrize(
    "header",
    [["build", "ru", "en"], ["id", "ru"]],
)
def test_ignored_column_header_mismatch_is_an_error(ignored_id_component, header):
    diagnostics = validate_sheet_headers(ignored_id_component, [header])
    assert [(item.code, item.row) for item in diagnostics] == [("header.mismatch", 1)]
```

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py -k ignored_column -v`
Expected: FAIL — `test_ignored_column_header_mismatch_is_an_error` не получает диагностики.

**Step 3: реализация**

`loc_kit_ingest/reader.py`, в ветке `isinstance(grammar, RecordMapGrammar)` после цикла по `grammar.notes` (`:150-156`):

```python
        for ignored in grammar.ignored_columns:
            _check("ignored column", ignored.column, ignored.header)
```

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_reader.py -v`
Expected: PASS.

**Step 5: commit**

```bash
git add loc_kit_ingest/reader.py loc_kit_ingest/tests/test_reader.py
git commit -m "feat(loc-kit): validate ignored column headers against the sheet"
```

---

## Phase D — Parser

### Task 5: record-map чтит `ignored_columns`

**Files:**

- Modify: `loc_kit_ingest/parser.py:623-625` (локальные множества), `:693` (`allowed` caption-строки), `:788-800` (проверка непокрытых ячеек)
- Test: `loc_kit_ingest/tests/test_parser_tbx.py`

**Step 1: написать падающие тесты**

В `test_parser_tbx.py` уже есть v2-фикстуры (`STRIDE_TWO_PROFILE`, `:410-425`). Добавить локальный конструктор плоского record-map-компонента для record-строк и расширить существующую stride-two fixture для caption-строки; не плодить второй caption-профиль.

```python
def _flat_record_map(*, ignored_columns=(), allow_empty_targets=False, last_row=2):
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Glossary",
                "component": "Glossary",
                "kind": "tbx",
                "source_lang": "ru",
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": last_row,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "ignored_columns": [
                        {"column": column, "header": header}
                        for column, header in ignored_columns
                    ],
                    "allow_empty_targets": allow_empty_targets,
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    return parse_profile(document).components[0]


IGNORED_ROWS = [["ru", "en", "id"], ["Леон", "Leon", "char_leon"]]


def test_record_map_ignored_column_is_not_unmapped():
    component = _flat_record_map(ignored_columns=((3, "id"),))
    result = parse_component(component, [row[:] for row in IGNORED_ROWS])
    assert [d.code for d in result.diagnostics] == []
    assert len(result.units) == 1
    assert result.units[0].values == {"ru": "Леон", "en": "Leon"}


def test_record_map_populated_column_without_a_declaration_is_unmapped():
    component = _flat_record_map()
    result = parse_component(component, [row[:] for row in IGNORED_ROWS])
    assert any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)


def test_record_map_ignored_column_is_allowed_on_a_caption_row():
    profile = deepcopy(STRIDE_TWO_PROFILE)
    profile["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 3, "header": "id"}
    ]
    rows = [[*row, f"id_{index}"] for index, row in enumerate(STRIDE_TWO_ROWS)]
    component = parse_profile(profile).components[0]
    result = parse_component(component, rows)
    assert not any(d.code == "tbx.unmapped_cell" for d in result.diagnostics)
```

Добавить `from copy import deepcopy`. Этот тест действительно проходит ветку
caption-row: `id_1` находится в section caption, а не в очередной term-строке.

Если в файле уже есть похожий конструктор v2-компонента — использовать его и добавить параметры, а не плодить второй.

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -k ignored_column -v`
Expected: FAIL — первый и третий тесты видят `tbx.unmapped_cell`.

**Step 3: реализация**

`loc_kit_ingest/parser.py`, `_parse_record_map`. После `skip_set = set(grammar.skip_rows)` (`:625`):

```python
    ignored_columns = {ignored.column for ignored in grammar.ignored_columns}
```

`allowed` на caption-строке (`:693`):

```python
            allowed = {
                region.section_column,
                *lang_columns.values(),
                *ignored_columns,
            }
```

Проверка непокрытых ячеек (`:793-800`):

```python
                for col, value in enumerate(rows[row_idx]):
                    if col in ignored_columns:
                        continue
                    if (row_idx, col) not in consumed and not _is_blank(value):
                        err(
                            "tbx.unmapped_cell",
                            row_idx + 1,
                            f"column {col + 1} holds data but no declared field "
                            "reads it",
                        )
```

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -v`
Expected: PASS.

**Step 5: commit**

```bash
git add loc_kit_ingest/parser.py loc_kit_ingest/tests/test_parser_tbx.py
git commit -m "feat(loc-kit): record-map parser consumes declared ignored columns"
```

---

### Task 6: пустой target разрешён только при `allow_empty_targets`

v1 `_parse_pairs` (`parser.py:490-502`) **не трогается**: ручные v1-профили остаются строгими.

**Files:**

- Modify: `loc_kit_ingest/parser.py:749-755` (только record-map)
- Test: `loc_kit_ingest/tests/test_parser_tbx.py`

**Step 1: написать падающие тесты**

Существующий `test_missing_initial_target_term_is_error` (`:122-125`) работает на **v1**-фикстуре `terms_component` (`terms.loc-ingest.json`, `schema_version: 1`, grammar `term-description-pairs`) — он остаётся без изменений и продолжает защищать v1. Добавить рядом два v2-теста:

```python
def test_record_map_blank_target_is_an_error_by_default():
    component = _flat_record_map()
    rows = [["ru", "en"], ["Леон", ""]]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.missing_target_term" for d in result.diagnostics)


def test_record_map_blank_target_is_untranslated_when_allowed():
    component = _flat_record_map(allow_empty_targets=True)
    rows = [["ru", "en"], ["Леон", ""]]
    result = parse_component(component, rows)
    assert [d.code for d in result.diagnostics] == []
    assert result.units[0].values == {"ru": "Леон", "en": ""}


def test_record_map_blank_source_stays_an_error_when_targets_are_allowed():
    component = _flat_record_map(allow_empty_targets=True)
    rows = [["ru", "en"], ["", "Leon"]]
    result = parse_component(component, rows)
    assert any(d.code == "tbx.missing_term" for d in result.diagnostics)
```

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -k "blank_target or blank_source" -v`
Expected: FAIL — `…_untranslated_when_allowed` получает `tbx.missing_target_term`.

**Step 3: реализация**

`loc_kit_ingest/parser.py:749-755`:

```python
            # A WIP kit legitimately has gaps: the profile says so explicitly,
            # and Weblate imports a blank target as an untranslated unit
            # (STATE_EMPTY). Without the flag a gap stays an error, so a hand
            # written profile keeps its strict contract.
            if not grammar.allow_empty_targets:
                for tlang in target_langs:
                    if _is_blank(term_values.get(tlang, "")):
                        err(
                            "tbx.missing_target_term",
                            term_1based,
                            f"target term in language {tlang!r} is empty",
                        )
```

Проверку источника (`:743-748`) не трогать. Никаких per-row предупреждений при включённом флаге не добавлять: 216 строк × 9 языков утопят превью, а сводка по языкам уже приходит из инференса (Task 8).

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_parser_tbx.py -v`
Expected: PASS, включая нетронутый v1-тест `test_missing_initial_target_term_is_error`.

**Step 5: commit**

```bash
git add loc_kit_ingest/parser.py loc_kit_ingest/tests/test_parser_tbx.py
git commit -m "feat(loc-kit): allow blank record-map targets behind an explicit flag"
```

---

## Phase E — Inference

### Task 7: техническая колонка (`id`) объявляется игнорируемой; остальное по-прежнему отказ

**Files:**

- Modify: `loc_kit_ingest/infer.py:643-662` (блок unmapped), `:770-775` (эмит grammar), новая константа рядом с `_NOTE_HEADERS` (`:50-76`)
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: написать падающие тесты**

```python
@pytest.mark.parametrize("header", ["id", "ID"])
def test_technical_id_column_becomes_an_ignored_column(header: str) -> None:
    rows = [
        [header, "ru", "en"],
        ["char_leon", "Леон", "Leon"],
        ["char_aki", "Аки", "Aki"],
    ]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["ignored_columns"] == [{"column": 1, "header": header}]
    assert [lang["code"] for lang in comp["languages"]] == ["ru", "en"]
    assert any("column 1" in note and "not imported" in note for note in notes)
    parse_profile(document)

def test_ignored_columns_absent_when_every_column_maps() -> None:
    rows = [["ru", "en"], ["Леон", "Leon"], ["Аки", "Aki"]]
    document, _notes = infer_glossary_profile("S", rows, component="s")
    assert "ignored_columns" not in document["components"][0]["grammar"]
```

Существующие `test_populated_non_language_column_is_refused` (`:75-81`, заголовок `domain`) и `test_unrecognised_extra_column_has_actionable_error` (`:368-373`, заголовок `Character limit`) **остаются без изменений** — оба заголовка не входят ни в `_NOTE_HEADERS`, ни в новый список, значит отказ сохраняется. Это и есть защита от молчаливой потери данных.

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k "technical_id or ignored_columns_absent" -v`
Expected: FAIL — `InferenceError: column 1 ('id') holds data…`.

**Step 3: реализация**

Константа после `_NOTE_HEADERS` (`:76`):

```python
# Only the header observed in the real kit is safe to discard. Adding "key",
# "ключ", or a generic metadata rule would create an untested data-loss path.
# Every future entry needs a real-kit fixture and a fallback-contract review.
_IGNORABLE_HEADERS = frozenset({"id"})
```

Блок unmapped (`:643-662`):

```python
    populated: set[int] = set()
    for index in content_indexes:
        for col, value in enumerate(rows[index]):
            if value.strip():
                populated.add(col)
    note_col = _find_note_column(header_row, populated, languages, notes)
    mapped = set(languages)
    if note_col is not None:
        mapped.add(note_col)

    ignored_cols: list[int] = []
    for col in sorted(populated - mapped):
        header_text = _cell(header_row, col)
        if header_text.strip().casefold() in _IGNORABLE_HEADERS:
            ignored_cols.append(col)
            notes.append(
                f"column {col + 1} ({header_text!r}) is a technical "
                "identifier; not imported"
            )
            continue
        msg = (
            f"column {col + 1} ({header_text or f'column{col + 1}'!r}) holds "
            "data but is not a recognised language column; rename the header "
            "to a recognised term-note header, for example note, description, "
            "comment, or explanation, or supply an explicit profile"
        )
        raise InferenceError(msg)
```

Эмит grammar (`:770-775`):

```python
    grammar: dict[str, Any] = {
        "type": "record-map",
        "skip_rows": sorted(index + 1 for index in skip_rows),
        "regions": regions,
        "term_row_offset": 0,
    }
    if ignored_cols:
        grammar["ignored_columns"] = [
            {"column": col + 1, "header": _cell(header_row, col)}
            for col in ignored_cols
        ]
```

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -v`
Expected: PASS, включая оба сохранённых теста на отказ.

**Step 5: commit**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): declare a technical id column as ignored, refuse the rest"
```

---

### Task 8: частичные цели с term-значениями импортируются целиком

Числовой guard и порог `min_fill` **сохраняются и применяются к term-строкам target-языка**: убирается только требование «target заполнен в каждой строке-записи». Колонка, содержащая только descriptions и ни одного target-term, по-прежнему отказ: иначе импорт создаст пустой TBX-язык и замаскирует неверную раскладку.

**Files:**

- Modify: `loc_kit_ingest/infer.py:742-768` (сбор целей), `:770-775` (эмит `allow_empty_targets`), `:777-811` (paired-ветка)
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: обновить тесты**

Заменить `test_partially_filled_language_is_not_an_initial_target` (`:50-55`) и `test_no_fully_filled_target_is_refused` (`:89-96`):

```python
def test_partially_filled_language_is_imported_as_a_target() -> None:
    document, notes = infer_glossary_profile("Terms", STANDARD, component="terms")
    (comp,) = document["components"]
    # ja carries a term on some rows and not on others: imported, blank where
    # the kit is blank. fr is entirely empty and stays excluded.
    assert comp["initial_target_languages"] == ["en", "ja"]
    assert comp["grammar"]["allow_empty_targets"] is True
    assert any("ja" in note and "untranslated" in note for note in notes)


def test_complete_kit_does_not_declare_allow_empty_targets() -> None:
    rows = [["ru", "en"], ["Леон", "Leon"], ["Аки", "Aki"]]
    document, _notes = infer_glossary_profile("S", rows, component="s")
    assert "allow_empty_targets" not in document["components"][0]["grammar"]


def test_target_with_gaps_is_imported_instead_of_refused() -> None:
    rows = [["ru", "en"], ["Леон", ""], ["Аки", "Aki"]]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["initial_target_languages"] == ["en"]
    assert comp["grammar"]["allow_empty_targets"] is True
    assert any("en" in note and "untranslated" in note for note in notes)


def test_sheet_without_any_target_language_is_refused() -> None:
    # The only non-source language column is entirely empty, so nothing can
    # be a target. A single-column sheet never gets this far: header
    # detection refuses it earlier with "no recognised language code".
    rows = [["ru", "en"], ["Леон", ""], ["Аки", ""]]
    with pytest.raises(InferenceError, match="no target language"):
        infer_glossary_profile("S", rows, component="s")


def test_sparse_language_column_is_still_refused() -> None:
    """min_fill stays a refusal: one stray cell must not create a language."""
    rows = [["ru", "en"], *[[f"термин{i}", ""] for i in range(40)]]
    rows[1][1] = "Leon"  # 1/40 = 2.5% < DEFAULT_MIN_FILL
    with pytest.raises(InferenceError, match="too sparse"):
        infer_glossary_profile("S", rows, component="s")
```

Также обязательно сохранить существующий `test_language_with_descriptions_but_missing_terms_is_refused` (`:247-256`): это не устаревший тест. Language with descriptions but no target terms не является частично переведённым языком и должен требовать explicit profile.

Добавить отдельный red-тест, что descriptions не обходят term-level `min_fill`:

```python
def test_target_descriptions_do_not_bypass_term_min_fill() -> None:
    rows = [["ru", "en", "ja"]]
    for index in range(40):
        rows.extend(
            [
                [f"термин{index}", f"term{index}", "訳" if index == 0 else ""],
                [DESC_RU, DESC_EN, DESC_EN],
            ]
        )
    with pytest.raises(InferenceError, match="too sparse"):
        infer_glossary_profile("Terms", rows, component="terms")
```

**Step 2: прогнать — упадёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k "partially_filled or allow_empty or with_gaps or without_any_target or sparse_language or missing_terms" -v`
Expected: FAIL — старый код дропает `ja` и отказывает без полного языка.

**Step 3: реализация**

1. Первичный цикл по колонкам-кандидатам (`:606-635`) оставляет текущие
`_is_numeric` и content-level `min_fill` guards. Он отсекает пустые,
числовые и явно разреженные колонки до layout-classification.

2. При сборе `target_langs` (`:742-768`) вычислять заполненность отдельно по
`term_rows`. В target попадает только язык с хотя бы одним term и долей не
меньше `min_fill`; полностью заполненный target не ставит flag, а неполный
ставит:

```python
    target_langs: list[str] = []
    allow_empty_targets = False
    for col in sorted(languages):
        if col == source_col:
            continue
        code = languages[col]
        missing_rows = [
            index + 1 for index in term_rows if not _cell(rows[index], col).strip()
        ]
        term_filled = len(term_rows) - len(missing_rows)
        if not term_filled:
            continue
        share = 100.0 * term_filled / len(term_rows)
        if share < min_fill:
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is "
                f"filled in {term_filled}/{len(term_rows)} term rows "
                f"({share:.1f}%); too sparse to map deterministically"
            )
            raise InferenceError(msg)
        target_langs.append(code)
        if missing_rows:
            allow_empty_targets = True
            shown = ", ".join(str(row) for row in missing_rows[:_MISSING_ROWS_SHOWN])
            if len(missing_rows) > _MISSING_ROWS_SHOWN:
                shown += f", +{len(missing_rows) - _MISSING_ROWS_SHOWN} more"
            notes.append(
                f"language {code} has no term on {len(missing_rows)} row(s) "
                f"({shown}); imported untranslated"
            )
```

После цикла сохранить существующий отказ при пустом `target_langs`. Он
покрывает flat-kit без target-term. В paired-kit текущий блок
`:777-798` **не удалять**: если язык не попал в `target_langs`, но держит
description, он должен сохранить точный actionable refusal
`has descriptions ... but is missing terms`. Также сохранить фильтр
`if col == source_col or languages[col] in target_langs` при создании
`note_fields`, иначе target-note у неимпортируемого языка создаст
неконсистентный профиль.

3. После блока `ignored_columns` из Task 7 добавить:

```python
    if allow_empty_targets:
        grammar["allow_empty_targets"] = True
```

4. Обновить docstring `infer_glossary_profile` (`:558-570`): target — это
не-source language с хотя бы одним term и term-level fill не ниже порога;
его gaps включают `allow_empty_targets`. `id` объявляется в
`ignored_columns`; любая иная populated unmapped column остаётся отказом.

**Step 4: прогнать — пройдёт**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -v`
Expected: PASS.

**Step 5: commit**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): import partially translated glossary target languages"
```

---

## Phase F — Интеграция

### Task 9: сквозной standalone-прогон трёх реальных форм

**Files:**

- Test: `loc_kit_ingest/tests/test_infer_glossary.py` (без БД)

**Step 1: написать тесты**

```python
def _render_round_trip(document, rows, tmp_path):
    """The same pipeline as weblate/trans/loc_kit.py validate_glossary_profile."""
    from loc_kit_ingest.model import Severity
    from loc_kit_ingest.parser import parse_component
    from loc_kit_ingest.reader import validate_sheet_headers
    from loc_kit_ingest.writer import render_component, validate_rendered_component

    component = parse_profile(document).components[0]
    diagnostics = list(validate_sheet_headers(component, rows))
    result = parse_component(component, rows)
    diagnostics.extend(result.diagnostics)
    render_component(component, result, tmp_path)
    diagnostics.extend(validate_rendered_component(component, result, tmp_path))
    assert [d for d in diagnostics if d.severity is Severity.ERROR] == []
    return component, result


def _infer_csv(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8-sig")
    rows = read_sheets(path)[path.stem]
    document, notes = infer_glossary_profile(path.stem, rows, component=path.stem)
    return rows, document, notes


@pytest.mark.parametrize(
    ("name", "body", "targets", "ignored_columns", "blank_source"),
    [
        (
            "Temple.csv",
            "ru;en;notes\nЛеон;Leon;Имя собственное, мужской род.\nАки;Aki;Сестра Леона.\n",
            ["en"],
            None,
            None,
        ),
        (
            "Terms.csv",
            "id,ru,en,ja,zh-TC,notes\n"
            "char_leon,Леон,Leon,レオン,,главный герой\n"
            "char_aki,Аки,Aki,,阿姬,\n"
            "char_joe,Джо,Joe,,,паук\n",
            ["en", "ja", "zh_Hant"],
            [{"column": 1, "header": "id"}],
            "Джо",
        ),
        (
            "Terms-semicolon.csv",
            "ru;en;ja;zh-TC;notes\nЛеон;Leon;レオン;;главный герой\nАки;Aki;;阿姬;\n",
            ["en", "ja", "zh_Hant"],
            None,
            "Аки",
        ),
    ],
)
def test_real_kit_csv_shapes_survive_infer_and_render(
    tmp_path, name, body, targets, ignored_columns, blank_source
) -> None:
    rows, document, notes = _infer_csv(tmp_path, name, body)
    (component,) = document["components"]
    assert component["source_lang"] == "ru"
    assert component["initial_target_languages"] == targets
    if ignored_columns is None:
        assert "ignored_columns" not in component["grammar"]
    else:
        assert component["grammar"]["ignored_columns"] == ignored_columns
    if blank_source is None:
        assert "allow_empty_targets" not in component["grammar"]
    else:
        assert component["grammar"]["allow_empty_targets"] is True
        assert any("untranslated" in note for note in notes)

    _component, result = _render_round_trip(document, rows, tmp_path)
    if blank_source is not None:
        blank = next(unit for unit in result.units if unit.values["ru"] == blank_source)
        assert blank.values["zh_Hant"] == ""
```

Добавить `read_sheets` в import test-файла. Все три формы теперь начинают с
байтового CSV и проходят реальный delimiter-reader; literal `rows` не
доказывает semicolon-contract.

**Step 2: прогнать**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k real_kit_csv_shapes -v`
Expected: PASS. Затем весь пакет: `cd loc_kit_ingest && uv run pytest -q` → PASS.

**Step 3: прогон на трёх реальных файлах (ручной, один раз)**

```bash
uv run python -c "
from pathlib import Path
from loc_kit_ingest.reader import read_sheets
from loc_kit_ingest.infer import infer_glossary_profile
for name in [
    'heart_abyss_glossary_temple.csv',
    'heart_abyss_glossary.csv',
    'heart_abyss_glossary (1).csv',
]:
    rows = next(iter(read_sheets(Path('/Users/eli/Downloads') / name).values()))
    doc, notes = infer_glossary_profile('S', rows, component='s')
    comp = doc['components'][0]
    print(name, comp['source_lang'], comp['initial_target_languages'], len(notes))
"
```

Expected: все три разбираются; у второго и третьего среди целей `zh_Hant`, у второго `ignored_columns` содержит `id`.

**Step 4: commit**

```bash
git add loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "test(loc-kit): real kit shapes infer and survive TBX round-trip"
```

---

### Task 10: контрактная матрица в Weblate (upload → превью → живой компонент)

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py` (фикстуры рядом с `:526-559`, тесты в `LocKitGlossaryUploadUITest`)

**Step 1: подготовить host-side Django runner**

`dev-docker` примонтирован к main-checkout и не видит этот worktree. В одном
shell подготовить отдельную test DB и static files для *этой* ветки:

Перед этим установить полный набор dependency для worktree:

```bash
uv sync --all-extras --dev
```

Если sync не смог собрать native dependency, остановиться и исправить именно
локальную prerequisite (например, Homebrew ICU/xxhash); не обходить Django
контракт запуском mounted container.

```bash
export PATH="/opt/homebrew/bin:$PATH"
export DJANGO_SETTINGS_MODULE=weblate.settings_test
export CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434
export CI_DB_USER=weblate CI_DB_PASSWORD=weblate
export CI_DB_NAME="loc_kit_glossary_${PPID}"
uv run ./manage.py collectstatic --noinput
```

`CI_DB_NAME` намеренно уникален: Django создаст отдельную `test_…` database и
не конфликтует с тестом работающего контейнера. Если Postgres на `5434` не
доступен, остановиться и запросить доступ; не перезапускать shared stack.

**Step 2: написать тесты**

Три фикстуры рядом с существующими (`:526-559`):

```python
# Temple kit shape: ';' delimiter, one target, a notes column with commas
# inside quoted cells. Covers the reader's delimiter detection end to end.
GLOSSARY_SEMICOLON_CSV = (
    "ru;en;notes\n"
    'Леон;Leon;"Имя собственное, мужской род."\n'
    "Аки;Aki;Сестра Леона.\n"
)

# Full kit shape: technical id column, a vendor Chinese code and target
# languages that are only partly filled in.
GLOSSARY_ID_PARTIAL_CSV = (
    "id,ru,en,ja,zh-TC,notes\n"
    "char_leon,Леон,Leon,レオン,,главный герой\n"
    "char_aki,Аки,Aki,,阿姬,\n"
    "char_joe,Джо,Joe,,,паук\n"
)

# Both at once: ';' delimiter plus a vendor code and partial targets.
GLOSSARY_SEMICOLON_PARTIAL_CSV = (
    "ru;en;ja;zh-TC;notes\n"
    "Леон;Leon;レオン;;главный герой\n"
    "Аки;Aki;;阿姬;\n"
)
```

Тесты в `LocKitGlossaryUploadUITest` (использовать существующие `_start`, `_csv`, `_draft`, `_confirm` — `:626-683`; `_confirm` уже проводит превью и финальную форму компонента, никакого ручного POST-хвоста писать не нужно):

```python
@override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
def test_semicolon_kit_gets_a_deterministic_preview(self) -> None:
    """Разделитель ';' - это формат экспорта, а не повод требовать профиль."""
    self._start(
        upload=self._csv("Temple.csv", GLOSSARY_SEMICOLON_CSV), slug=self.slug
    )
    draft = self._draft()
    draft.refresh_from_db()

    self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
    preview = json.loads(draft.preview_json)
    self.assertEqual(preview["source_language"], "ru")
    self.assertEqual(preview["target_languages"], ["en"])
    self.assertEqual(preview["term_count"], 2)
    self.assertIn("мужской род", preview["terms"][0]["source_explanation"])


@override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
def test_semicolon_partial_kit_maps_the_vendor_code(self) -> None:
    self._start(
        upload=self._csv("Terms.csv", GLOSSARY_SEMICOLON_PARTIAL_CSV),
        slug=self.slug,
    )
    draft = self._draft()
    draft.refresh_from_db()

    self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
    preview = json.loads(draft.preview_json)
    self.assertIn("zh_Hant", preview["target_languages"])


@override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
def test_id_partial_kit_creates_a_component_with_untranslated_terms(self) -> None:
    """Полная форма реального кита доходит до живого компонента."""
    self._start(
        upload=self._csv("Terms.csv", GLOSSARY_ID_PARTIAL_CSV), slug=self.slug
    )
    draft = self._draft()
    draft.refresh_from_db()

    self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
    preview = json.loads(draft.preview_json)
    self.assertEqual(preview["source_language"], "ru")
    self.assertEqual(preview["term_count"], 3)
    self.assertIn("zh_Hant", preview["target_languages"])
    self.assertTrue(
        any("untranslated" in warning for warning in preview["warnings"]),
        preview["warnings"],
    )

    self._confirm()
    component = Component.objects.get(slug=self.slug, project=self.project)
    self.assertTrue(component.is_glossary)
    self.assertEqual(component.source_language.code, "ru")

    translation = component.translation_set.get(language__code="zh_Hant")
    filled = translation.unit_set.get(source="Аки")
    self.assertEqual(filled.target, "阿姬")
    blank = translation.unit_set.get(source="Джо")
    self.assertEqual(blank.target, "")
    self.assertEqual(blank.state, STATE_EMPTY)
```

Добавить `from weblate.utils.state import STATE_EMPTY` в импорты файла — сейчас его там нет (блок импортов `:43-53`).

**Step 3: прогнать в том же shell**

```bash
uv run pytest weblate/trans/tests/test_loc_kit_ingest_contract.py \
  -k "semicolon or id_partial" -n0
```

Expected: PASS. При массовых setup-ошибках сначала `docker stats --no-stream` — обычная причина не код, а нехватка памяти из-за чужих контейнеров.

**Step 4: commit**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "test(loc-kit): contract matrix for semicolon, id column and partial targets"
```

---

### Task 11: проверить, что fallback-фикстуры не сломались

Ручной и LLM-путь держатся на том, что `GLOSSARY_CSV` (`domain,ru,en,note_ru,note_en`) детерминированно **не** разбирается. Ни `domain`, ни `note_ru`/`note_en` не входят в `_NOTE_HEADERS` (`infer.py:50-76`) и не входят в `_IGNORABLE_HEADERS`, поэтому отказ сохраняется — но это надо доказать прогоном, а не рассуждением.

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py:533-536` (комментарий у фикстуры)

**Step 1: прогнать зависящие тесты**

В shell из Task 10:

```bash
uv run pytest weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
```

Expected: PASS, в том числе:

- `test_glossary_upload_creates_a_draft_and_asks_for_a_sheet` (`:689-702`, ждёт `SHEET_SELECTED`),
- `test_disabled_analyzer_offers_manual_profile_upload` (`:875-886`),
- `test_sheet_selection_is_not_rate_limited_without_analysis` (`:917-948`),
- `test_analysis_attempts_are_capped_per_session` (`:950-990`),
- `test_multiple_worksheets_require_an_explicit_choice` (`:1060-1093`).

Если любой из них падает — значит `_IGNORABLE_HEADERS` или note-детект захватили лишний заголовок. Это регрессия задачи 7, а не «устаревший тест»: чинить код, не тест.

**Step 2: зафиксировать зависимость в комментарии фикстуры** (`:533-536`)

```python
# Language-only kit with no extra columns: deterministic inference must give
# a preview. GLOSSARY_CSV above is intentionally NOT parsed deterministically:
# `domain` and `note_ru`/`note_en` are in neither _NOTE_HEADERS nor
# _IGNORABLE_HEADERS (loc_kit_ingest/infer.py), so it keeps covering the
# LLM/manual path. Widening either set breaks that coverage - re-point these
# tests at a new fixture instead of relaxing them.
```

**Step 3: commit**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "test(loc-kit): pin the manual-profile fixture to the refusal contract"
```

---

## Phase G — Документация и финализация

### Task 12: changelog, спецификация, threat model

**Files:**

- Modify: `docs/changes.rst` (верхняя нерелизная секция), `docs/guides/loc-kit-ingest.md`, `docs/security/threat-model.rst`

**Step 1: changelog**

Добавить в верхнюю нерелизную секцию `docs/changes.rst` (стиль соседних строк `:12-17`):

```rst
* Glossary table import now detects the CSV delimiter, recognises vendor Chinese codes such as ``zh-TC``, skips a declared technical identifier column, and imports partially translated languages as untranslated terms, see :ref:`uploading-glossary-tables`.
```

**Step 2: спецификация**

`docs/guides/loc-kit-ingest.md`:

- в разделе record-map (`:343-376`) описать `ignored_columns` и `allow_empty_targets`. Явно сохранить формулировку про `tbx.unmapped_cell`, добавив к ней исключение: заполненная ячейка допустима только в колонке, **объявленной** в `ignored_columns`, header которой совпал с листом; неизвестная колонка по-прежнему ошибка;
- в таблице диагностик (`:435-437`) уточнить, что `tbx.missing_target_term` — ошибка, если профиль не объявил `allow_empty_targets`; зафиксировать, что generated profile ставит flag только для target-языка с хотя бы одним term;
- добавить абзац про детект разделителя CSV и про то, что запрет длинометрических эвристик в парсере остаётся: меняется инференс и устойчивость, а не парсер.

**Step 3: threat model**

`docs/security/threat-model.rst:737-751` — строка про loc-kit сейчас перечисляет «unmapped cell … missing target term» среди того, что не должно создавать компонент. Обновить перечень так, чтобы он соответствовал новому контракту:

- unmapped cell = заполненная колонка, которую профиль не объявил ни языком, ни note, ни секцией, ни `ignored_columns` с совпадающим header;
- missing target term = ошибка для профиля без `allow_empty_targets`;
- добавить, что `ignored_columns` и `allow_empty_targets` — часть локально валидируемого профиля и не расширяют доверие: и предложенный моделью, и загруженный оператором профиль проходят тот же gate `validate_glossary_profile`.

Это требуется разделом «Conditions that change this model»: меняется заявленное security property импорта.

**Step 4: commit**

```bash
git add docs/changes.rst docs/guides/loc-kit-ingest.md docs/security/threat-model.rst
git commit -m "docs(loc-kit): document delimiter detection, ignored columns and partial targets"
```

---

### Task 13: линт, типы, тесты и push

**Step 1: targeted-линт и типы**

```bash
uv run prek run --files \
  loc_kit_ingest/langcode.py \
  loc_kit_ingest/reader.py \
  loc_kit_ingest/infer.py \
  loc_kit_ingest/profile.py \
  loc_kit_ingest/parser.py \
  loc_kit_ingest/tests/test_reader.py \
  loc_kit_ingest/tests/test_infer_glossary.py \
  loc_kit_ingest/tests/test_profile_v2.py \
  loc_kit_ingest/tests/test_parser_tbx.py \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  docs/changes.rst \
  docs/guides/loc-kit-ingest.md \
  docs/security/threat-model.rst
uv run pylint loc_kit_ingest/
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: без новых ошибок. Удалять только имена, осиротевшие этой серией
изменений, и отдельным `refactor(loc-kit): …` commit.

**Step 2: полный standalone-прогон**

```bash
cd loc_kit_ingest && uv run pytest -q && cd -
```

**Step 3: полный Weblate contract**

В shell с exports из Task 10:

```bash
uv run pytest weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
```

**Step 4: push ветки**

```bash
/usr/bin/git push origin HEAD
```

**После интеграции, отдельная обязательная проверка UI**

Browser smoke нельзя честно выполнить из worktree: запущенный `:3001`
обслуживает main-checkout. После того как пользователь выберет интеграцию
через `finishing-a-development-branch`, и только на уже смонтированном
main-checkout, с отдельным одобрением проверить три real files в браузере:

1. На :3001 загрузить каждый CSV в `Upload translation files` с `Use as glossary`.
2. Дойти до preview **кликом реальной кнопки**, не `form.submit()` и не прямым POST.
3. Проверить `ru`, `zh_Hant` для двух partial kits, warning `id` только для id-kit и отсутствие automatic-mapping error.
4. Создать один glossary component и проверить `STATE_EMPTY` для blank target.

Это post-integration smoke, не подмена branch-контрактов. Не пересоздавать и не
перезапускать shared stack без отдельного approval.

---

## Что вне объёма (сознательно)

- **Generic-пропуск любой неизвестной колонки.** `_IGNORABLE_HEADERS` закрыт и содержит только наблюдавшийся `id`. Неизвестная заполненная колонка по-прежнему отказ: контракт «данные никогда не отбрасываются молча» (`docs/guides/loc-kit-ingest.md:372-374`) сохраняется. Расширять список — отдельным решением, с real-kit fixture и пересмотром fallback-контракта Task 11.
- **v1-грамматика `term-description-pairs`.** Пустой target там остаётся ошибкой; ручные v1-профили строгие.
- **Пустой target-язык из одних descriptions.** `allow_empty_targets` означает gap в языке, содержащем хотя бы один term, а не создание пустого TBX-языка.
- **Снятие numeric-guard и term-level `min_fill`.** Реальные киты порог проходят (минимум 29.2%), а снятие позволило бы одной случайной ячейке или descriptions создать целый target-язык и лишний TBX-файл.
- **PO-путь** (`infer_component`/`infer_profile`), CLI и его `--min-fill`/`--include-lang`.
- **Широкая таблица алиасов кодов.** Только `zh`-семейство (TC/SC/Hant/Hans); каждый новый алиас — однозначный и указывает на канонический код Weblate.
- **UI для ручного задания `ignored_columns`.** Инференс проставляет их сам, оператор видит предупреждение на preview и может загрузить свой profile как раньше.
- **Импорт данных из колонки, которую пропустили.** Чтобы данные попали в глоссарий, header должен быть code-language или распознанным note-header — осознанный «refuse-to-guess» компромисс.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Scope review | `/plan-eng-review` | Minimum complete end-to-end path | 1 | accepted | Full reader → Weblate path is required by the goal; standalone-only would not prove live component creation. |
| Repository audit | Current `reader`, `infer`, `profile`, `parser`, contract tests | API and data-flow correctness | 1 | folded | Shared header matcher, caption collision, term-level target eligibility, and real CSV reader coverage were missing. |
| Standalone baseline | `cd loc_kit_ingest && uv run pytest` | Test baseline | 1 | passed | 227 passed in 3.00s. |
| Host contract feasibility | `collectstatic` from fresh worktree | Worktree/Django environment | 1 | folded | The locally created environment lacked `psycopg`; plan now requires `uv sync --all-extras --dev` before any Django command and uses a unique host-side test DB. |
| UI review | Existing UI contract and mounted-checkout topology | Browser verification validity | 1 | folded | A worktree cannot exercise :3001 code; browser smoke is now explicit post-integration work, not a false branch test. |

Ревизия 3: (1) Task 1 extracts a shared `header_language_codes` contract before
Task 2 introduces aliases, so the vendor-alias test is genuinely red and no
symbol remains imported from its old home; (2) `id;build` retains comma fallback;
(3) `ignored_columns` rejects duplicates and every language/note/section
collision, including per-region captions; (4) the parser test now reaches an
actual caption row; (5) only evidenced `id` is discardable; (6) partial targets
must carry at least one term and meet term-level `min_fill`; (7) standalone
tests read actual CSV bytes for all three shapes; (8) Django contracts run
against the worktree, never the mounted main-checkout.

**VERDICT:** PLAN REVISED — implementation may begin after the documented
`uv sync --all-extras --dev` setup.

NO UNRESOLVED DECISIONS
