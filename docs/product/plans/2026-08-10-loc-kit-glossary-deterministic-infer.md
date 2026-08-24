# План: детерминированный разбор глоссарных таблиц

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Загрузка «языковой» таблицы с галочкой «Use as glossary» даёт готовое превью и TBX-компонент без OpenRouter и без ручного JSON.

**Architecture:** Новая функция `infer_glossary_profile` в standalone-пакете `loc_kit_ingest` строит профиль schema v2 (record-map) из заголовков; `_analyze_draft_sheet` в `weblate/trans/views/create.py` пробует её первой и проваливается в существующий OpenRouter-путь; одностраничные файлы пропускают экран выбора листа. Всё, что попадает в превью, проходит существующий gate `validate_glossary_profile` — новых путей доверия нет.

**Tech Stack:** Python/Django, standalone `loc_kit_ingest` (без Django-импортов), pytest.

**Дизайн:** `docs/product/designs/2026-08-10-loc-kit-glossary-deterministic-infer-design.md`

**Status (2026-08-15): implemented and verified.** Deterministic glossary
inference, single-sheet auto-skip, preview validation, and the OpenRouter
fallback are implemented and covered by standalone and Weblate contract tests.

**Критично для исполнителя:**

- Контейнерные тесты гонять `./rundev.sh test <path> -n0` (без `-n0` xdist в контейнере флакует; при массовых setup-ошибках сначала `docker stats --no-stream` — обычная причина не код, а memory pressure от чужих контейнеров).
- После каждой правки `loc_kit_ingest/*.py` копировать в контейнер: `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/` — контейнер импортирует пакет из `/app/data/python`, а не из репо.
- Фикстура `GLOSSARY_CSV` (колонки `domain,ru,en,note_ru,note_en`) **намеренно** не разбирается детерминированно (не-языковые колонки → `InferenceError`): она продолжает покрывать LLM/ручной путь. Не «чинить» её.
- Не запускать общепроектные линтеры/тест-сьюты между задачами; полный прогон — один раз в конце.

---

## Task 1: standalone-тесты `infer_glossary_profile` (провал)

**Files:**

- Create: `loc_kit_ingest/tests/test_infer_glossary.py`

Обрати внимание: `rows` — это `list[list[str]]` уже прочитанного листа (как из `reader.read_sheets`), 0-based внутри, 1-based в JSON-документе.

**Step 1: написать падающие тесты**

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest

from loc_kit_ingest.infer import InferenceError, infer_glossary_profile

# Стандартный «языковой» кит: заголовок-коды, caption-строка с названиями
# языков, секционная строка (импортируется как обычный термин), пустая строка,
# частично заполненный язык (ja) и пустой язык (fr).
STANDARD = [
    ["ru", "en", "fr", "ja"],
    ["Russian", "English", "french", "japanese"],
    ["Персонажи", "Characters", "", "キャラクター"],
    ["Герой", "Hero", "", "ヒーロー"],
    [],
    ["Меч", "Sword", "", ""],
]


def test_standard_layout_infers_v2_record_map() -> None:
    document, notes = infer_glossary_profile(
        "Terms", STANDARD, component="terms"
    )
    assert document["schema_version"] == 2
    (comp,) = document["components"]
    assert comp["kind"] == "tbx"
    assert comp["sheet"] == "Terms"
    assert comp["source_lang"] == "ru"
    assert comp["header_row"] == 1
    codes = [lang["code"] for lang in comp["languages"]]
    assert codes == ["ru", "en", "ja"]  # fr пустой - исключён
    grammar = comp["grammar"]
    assert grammar["type"] == "record-map"
    assert grammar["term_row_offset"] == 0
    assert grammar["skip_rows"] == [2]  # caption-строка
    assert grammar["regions"] == [
        {"first_record_row": 3, "last_record_row": 4, "record_stride": 1},
        {"first_record_row": 6, "last_record_row": 6, "record_stride": 1},
    ]


def test_partially_filled_language_is_not_an_initial_target() -> None:
    document, notes = infer_glossary_profile(
        "Terms", STANDARD, component="terms"
    )
    (comp,) = document["components"]
    # ja пуст в строке 6 -> распознан, но не импортируется.
    assert comp["initial_target_languages"] == ["en"]
    assert any("ja" in note and "6" in note for note in notes)


def test_row_without_source_term_is_skipped_with_note() -> None:
    rows = [
        ["ru", "en"],
        ["Герой", "Hero"],
        ["", "Stray"],
        ["Меч", "Sword"],
    ]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["skip_rows"] == [3]
    assert comp["grammar"]["regions"] == [
        {"first_record_row": 2, "last_record_row": 2, "record_stride": 1},
        {"first_record_row": 4, "last_record_row": 4, "record_stride": 1},
    ]
    assert any("row 3" in note for note in notes)


def test_populated_non_language_column_is_refused() -> None:
    rows = [
        ["domain", "ru", "en"],
        ["Оружие", "Меч", "Sword"],
    ]
    with pytest.raises(InferenceError, match="column 1"):
        infer_glossary_profile("S", rows, component="s")


def test_no_language_header_is_refused() -> None:
    with pytest.raises(InferenceError):
        infer_glossary_profile(
            "S", [["key", "value"], ["a", "b"]], component="s"
        )


def test_no_fully_filled_target_is_refused() -> None:
    rows = [
        ["ru", "en"],
        ["Герой", "Hero"],
        ["Меч", ""],
    ]
    with pytest.raises(InferenceError, match="target"):
        infer_glossary_profile("S", rows, component="s")


def test_document_survives_parse_profile() -> None:
    """Выведенный документ обязан быть валидным профилем без правок."""
    from loc_kit_ingest.profile import parse_profile

    document, _ = infer_glossary_profile("Terms", STANDARD, component="terms")
    profile = parse_profile(document)
    assert profile.components[0].source_lang == "ru"
```

**Step 2: убедиться, что тесты падают**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`
Expected: `ImportError: cannot import name 'infer_glossary_profile'`

---

### Task 2: реализация `infer_glossary_profile`

**Files:**

- Modify: `loc_kit_ingest/infer.py` (добавить в конец файла; импорт `SCHEMA_VERSION_RECORD_MAP` — к существующему импорту `SCHEMA_VERSION`)

**Step 1: импорт**

В строке `from loc_kit_ingest.profile import SCHEMA_VERSION` добавить `SCHEMA_VERSION_RECORD_MAP`.

**Step 2: функция** (переиспользует `_find_header_row`, `_language_code`, `_is_caption_row`, `_is_numeric`, `_cell`, `_is_blank_row`, `_KEY_COLUMN`, `_KEY_HEADER_DENYLIST`, `DEFAULT_MIN_FILL`)

```python
def infer_glossary_profile(
    sheet_name: str,
    rows: list[list[str]],
    *,
    component: str,
    min_fill: float = DEFAULT_MIN_FILL,
) -> tuple[dict[str, Any], list[str]]:
    """
    Infer a schema v2 record-map TBX profile for one worksheet.

    Deterministic v0: every populated column must be a language column; the
    leftmost language is the source, targets are languages filled on every
    record row. The record-map parser treats any unmapped populated cell as
    an error, so an extra column is a refusal, never a guess.
    Returns (document, notes).
    """
    if not rows:
        msg = f"sheet {sheet_name!r} is empty"
        raise InferenceError(msg)

    header_index, candidates = _find_header_row(rows)
    header_row = rows[header_index]
    notes: list[str] = []

    # Column 0 is an ordinary column here; promote it when its header is a
    # language code (a keyless kit like ``ru,en,ja`` keeps terms in column 1).
    first_header = _cell(header_row, _KEY_COLUMN)
    first_code = _language_code(first_header)
    if (
        first_code is not None
        and first_header.strip().casefold() not in _KEY_HEADER_DENYLIST
        and first_code not in candidates.values()
    ):
        candidates = {_KEY_COLUMN: first_code, **candidates}

    skip_rows: list[int] = []  # 0-based
    cursor = header_index + 1
    while cursor < len(rows) and _is_caption_row(rows[cursor], candidates):
        skip_rows.append(cursor)
        cursor += 1

    content_indexes = [
        index
        for index in range(cursor, len(rows))
        if not _is_blank_row(rows[index])
    ]
    if not content_indexes:
        msg = f"sheet {sheet_name!r} has no data rows"
        raise InferenceError(msg)

    languages: dict[int, str] = {}
    for col in sorted(candidates):
        code = candidates[col]
        values = [_cell(rows[index], col) for index in content_indexes]
        filled = sum(1 for value in values if value.strip())
        if not filled:
            notes.append(
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) "
                "is empty; excluded"
            )
            continue
        if _is_numeric(values):
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r}) holds only "
                "numbers; this layout needs an explicit profile"
            )
            raise InferenceError(msg)
        share = 100.0 * filled / len(content_indexes)
        if share < min_fill:
            msg = (
                f"column {col + 1} ({_cell(header_row, col)!r} -> {code}) is "
                f"filled in {filled}/{len(content_indexes)} rows "
                f"({share:.1f}%); too sparse to map deterministically"
            )
            raise InferenceError(msg)
        languages[col] = code

    if not languages:
        msg = f"sheet {sheet_name!r} has no populated language column"
        raise InferenceError(msg)

    # Every populated column must be a declared language: the record-map
    # parser errors on any populated cell no field reads (tbx.unmapped_cell).
    width = max(len(row) for row in rows)
    for col in range(width):
        if col in languages:
            continue
        if any(_cell(rows[index], col).strip() for index in content_indexes):
            header_text = _cell(header_row, col) or f"column{col + 1}"
            msg = (
                f"column {col + 1} ({header_text!r}) holds data but is not a "
                "recognised language column; this layout needs an explicit "
                "profile"
            )
            raise InferenceError(msg)

    source_col = min(languages)
    source_lang = languages[source_col]

    record_rows: list[int] = []
    for index in content_indexes:
        if _cell(rows[index], source_col).strip():
            record_rows.append(index)
        else:
            skip_rows.append(index)
            notes.append(f"row {index + 1} has no {source_lang} term; skipped")
    if not record_rows:
        msg = f"sheet {sheet_name!r} has no rows with a {source_lang} term"
        raise InferenceError(msg)

    regions: list[dict[str, int]] = []
    start = prev = record_rows[0]
    for index in record_rows[1:]:
        if index == prev + 1:
            prev = index
            continue
        regions.append(
            {
                "first_record_row": start + 1,
                "last_record_row": prev + 1,
                "record_stride": 1,
            }
        )
        start = prev = index
    regions.append(
        {
            "first_record_row": start + 1,
            "last_record_row": prev + 1,
            "record_stride": 1,
        }
    )

    target_langs: list[str] = []
    for col in sorted(languages):
        if col == source_col:
            continue
        code = languages[col]
        missing = [
            index + 1
            for index in record_rows
            if not _cell(rows[index], col).strip()
        ]
        if missing:
            shown = ", ".join(str(row) for row in missing[:10])
            if len(missing) > 10:
                shown += f", +{len(missing) - 10} more"
            notes.append(
                f"language {code} is missing a term on row(s) {shown}; "
                "recognised but not imported"
            )
            continue
        target_langs.append(code)
    if not target_langs:
        msg = f"sheet {sheet_name!r} has no fully filled target language column"
        raise InferenceError(msg)

    document = {
        "schema_version": SCHEMA_VERSION_RECORD_MAP,
        "components": [
            {
                "sheet": sheet_name,
                "component": component,
                "kind": "tbx",
                "source_lang": source_lang,
                "header_row": header_index + 1,
                "languages": [
                    {
                        "code": languages[col],
                        "xml_lang": languages[col].replace("_", "-"),
                        "column": col + 1,
                        "header": _cell(header_row, col),
                    }
                    for col in sorted(languages)
                ],
                "initial_target_languages": target_langs,
                "grammar": {
                    "type": "record-map",
                    "skip_rows": sorted(index + 1 for index in skip_rows),
                    "regions": regions,
                    "term_row_offset": 0,
                },
            }
        ],
    }
    return document, notes
```

**Step 3: прогнать тесты**

Run: `cd loc_kit_ingest && uv run pytest tests/ -q`
Expected: все зелёные, включая `test_infer_glossary.py` (7 passed в новом файле) и без регрессий в остальных.

**Step 4: задеплоить в контейнер и закоммитить**

```bash
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): deterministic glossary profile inference"
```

---

### Task 3: contract-тест детерминированного пути в визарде (провал)

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: фикстура и тесты** — рядом с `GLOSSARY_CSV` (~строка 529) добавить:

```python
# Языковой кит без служебных колонок: детерминированный разбор обязан дать
# превью без OpenRouter. GLOSSARY_CSV выше намеренно НЕ разбирается
# детерминированно (domain/note_*) и покрывает LLM/ручной путь.
GLOSSARY_LANG_ONLY_CSV = "ru,en\nRussian,English\nГерой,Hero\nМеч,Sword\n"
```

В `LocKitGlossaryUploadUITest` добавить (анализ выключен по умолчанию — `override_settings` не нужен, но ставим для явности):

```python
    @override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
    def test_language_only_sheet_gets_deterministic_preview(self) -> None:
        """Языковая таблица даёт превью локально, без OpenRouter и без JSON."""
        self._start(
            upload=self._csv("Terms.csv", GLOSSARY_LANG_ONLY_CSV), slug=self.slug
        )
        draft = self._draft()

        draft.refresh_from_db()
        self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
        preview = json.loads(draft.preview_json)
        self.assertEqual(preview["source_language"], "ru")
        self.assertEqual(preview["target_languages"], ["en"])
        self.assertEqual(preview["term_count"], 2)

        page = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertContains(page, "Герой")

    @override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
    def test_deterministic_preview_confirms_into_live_component(self) -> None:
        self._start(
            upload=self._csv("Terms.csv", GLOSSARY_LANG_ONLY_CSV), slug=self.slug
        )
        response = self._confirm()
        component = Component.objects.get(slug=self.slug)
        self.assertTrue(component.is_glossary)
        self.assertEqual(component.source_language.code, "ru")
        codes = set(
            component.translation_set.values_list("language__code", flat=True)
        )
        self.assertIn("en", codes)
```

ВАЖНО: эти тесты предполагают auto-skip выбора листа (Task 4-5): `_start` сразу даёт превью. Пишутся до реализации — падают.

**Step 2: убедиться, что падают**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUploadUITest::test_language_only_sheet_gets_deterministic_preview -n0`
Expected: FAIL — `draft.state == UPLOADED`, превью пустое (детерминированного пути ещё нет).

---

### Task 4: включить детерминированный путь в `_analyze_draft_sheet`

**Files:**

- Modify: `weblate/trans/views/create.py` — `_analyze_draft_sheet` (~строка 1097) и `_store_validated_profile` (~строка 1140)

**Step 1: `_store_validated_profile` принимает предупреждения инференса**

Сигнатура: `def _store_validated_profile(draft, document, rows, extra_warnings=()):`
В `draft.preview_json` строку warnings заменить на:

```python
            "warnings": [*extra_warnings, *preview.warnings],
```

**Step 2: детерминированный шаг перед OpenRouter**

В начало `_analyze_draft_sheet` (после docstring, вместо немедленной проверки
`LOC_KIT_PROFILE_ANALYSIS_ENABLED`):

```python
    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.infer import InferenceError, infer_glossary_profile

    # Deterministic first: local, free, offline. The analyzer is a fallback
    # for layouts the header-driven inference refuses.
    try:
        document, notes = infer_glossary_profile(
            draft.sheet, [list(row) for row in rows], component=draft.slug
        )
    except InferenceError as error:
        infer_reason = str(error)
    else:
        error_message = _store_validated_profile(
            draft, document, rows, extra_warnings=notes
        )
        if error_message is None:
            return None
        infer_reason = error_message

    if not settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED:
        return gettext(
            "Automatic mapping did not recognize this sheet (%s) "
            "and analysis is disabled. Upload a profile to continue."
        ) % infer_reason
```

Остальной код функции (rate limit, sample, OpenRouter) — без изменений.

**Step 3: прогнать существующий класс тестов**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUploadUITest -n0`
Expected: все существующие зелёные (фикстура GLOSSARY_CSV не разбирается детерминированно, её путь не изменился; сообщение при выключенном анализе теперь длиннее, тест `test_disabled_analyzer_offers_manual_profile_upload` проверяет `profile_json == ""` и наличие «Upload corrected profile» — это сохраняется). Новые тесты из Task 3 всё ещё падают (auto-skip нет).

Если какой-то существующий тест ассертит точный текст «Automatic analysis is disabled.» — обновить ожидание на новое сообщение.

**Step 4: commit**

```bash
git add weblate/trans/views/create.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): try deterministic glossary inference before the analyzer"
```

---

### Task 5: auto-skip выбора листа для одностраничных файлов

**Files:**

- Modify: `weblate/trans/views/create.py` — `_start_glossary_draft` (~строка 673, конец метода)

**Step 1: реализация**

Заменить финальный `return redirect("loc-kit-sheet-select", token=draft.token)` на:

```python
        if len(sheets) == 1:
            # A CSV/TSV always has exactly one sheet; the selection screen
            # is noise. Only the deterministic step may run here: this POST
            # is atomic, a provider call inside it would hold a transaction
            # open for the whole network timeout.
            name, rows = next(iter(sheets.items()))
            draft.sheet = name
            draft.state = LocKitImportDraft.State.SHEET_SELECTED
            draft.save(update_fields=["sheet", "state"])
            error = _infer_draft_profile(draft, rows)
            if error is None:
                return redirect("loc-kit-glossary-preview", token=draft.token)
            messages.info(self.request, error)
        return redirect("loc-kit-sheet-select", token=draft.token)
```

И выделить детерминированный шаг из Task 4 в хелпер, чтобы не дублировать
(в `_analyze_draft_sheet` вызывать его же):

```python
def _infer_draft_profile(draft: LocKitImportDraft, rows: list) -> str | None:
    """Deterministic local mapping. Returns None on success, else the reason."""
    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.infer import InferenceError, infer_glossary_profile

    try:
        document, notes = infer_glossary_profile(
            draft.sheet, [list(row) for row in rows], component=draft.slug
        )
    except InferenceError as error:
        return str(error)
    return _store_validated_profile(draft, document, rows, extra_warnings=notes)
```

(`_analyze_draft_sheet` тогда начинается с `infer_reason = _infer_draft_profile(draft, rows)`; `if infer_reason is None: return None`.)

**Step 2: прогнать новые тесты Task 3**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUploadUITest -n0`
Expected: PASS все, включая оба теста Task 3. Проверить особо:

- `test_glossary_upload_creates_a_draft_and_asks_for_a_sheet` — GLOSSARY_CSV одностраничный, но детерминированно НЕ разбирается → redirect остаётся на sheet-select; ассерт `draft.state == UPLOADED` смени́тся на `SHEET_SELECTED` (auto-skip зафиксировал лист) — обновить тест.
- `test_multiple_worksheets_require_an_explicit_choice` — без изменений.

**Step 3: commit**

```bash
git add weblate/trans/views/create.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): skip sheet selection for single-sheet glossary uploads"
```

---

### Task 6: документация

**Files:**

- Modify: `docs/changes.rst` — верхняя (нерелизнутая) секция
- Modify: `docs/guides/loc-kit-ingest.md` — раздел про glossary-workflow
- Modify: `AGENTS.md` — абзац про `loc_kit_ingest` («optional OpenRouter profile proposal» → «deterministic inference first, optional OpenRouter fallback»)

**Step 1:** changes.rst, кратко: glossary tables with only language columns are now mapped deterministically; the sheet-selection step is skipped for single-sheet files.

**Step 2:** спека: зафиксировать границу v0 (только языковые колонки; не-языковая заполненная колонка → отказ и fallback) и порядок анализа.

**Step 3: commit**

```bash
git add docs/changes.rst docs/guides/loc-kit-ingest.md AGENTS.md
git commit -m "docs(loc-kit): document deterministic glossary inference"
```

---

### Task 7: полный прогон и живой smoke

**Step 1: полные тесты затронутых областей**

```bash
cd loc_kit_ingest && uv run pytest -q && cd ..
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
uv run prek run --files weblate/trans/views/create.py loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py weblate/trans/tests/test_loc_kit_ingest_contract.py
```

**Step 2: живой smoke в браузере** (dev-инстанс на :3001, admin/admin).
КЛИКАМИ по реальным контролам (`tab.click`), не `form.submit()` — прошлый баг с вложенными формами пережил smoke именно из-за этого:

1. Создать компонент → «Upload translation files» → выбрать `/Users/eli/Downloads/Heart Abyss_Localization - Terms.csv`, галочка «Use as glossary», проект Heart Abyss → Continue.
2. Ожидание: экран выбора листа НЕ показывается; сразу превью: source **ru**, targets **en, ja** (остальные колонки пусты — в предупреждениях), терминов ~26, секционные строки («Персонажи» и т.п.) видны как термины.
3. «Create glossary component» → компонент создан, в нём en/ja переводы терминов.
4. На компоненте `temple`: Settings → Translation flags → добавить `check_glossary`; открыть перевод строки с термином из словаря и убедиться, что термин показан в сайдбаре.

**Step 3: финальный commit при расхождениях** — если smoke потребовал правок, зафиксировать их отдельным `fix(loc-kit): ...` с явными путями.
