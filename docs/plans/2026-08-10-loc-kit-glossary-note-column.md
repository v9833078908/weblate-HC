# Glossary note column in profile inference — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Плоская глоссарная таблица с колонкой прозы о термине (`ru,en,tr,fr,note`) импортируется галкой «Use as glossary» без рукописного профиля, и текст колонки доезжает до модели как `source_explanation`.

**Architecture:** Правка живёт в одной функции — `loc_kit_ingest.infer.infer_glossary_profile`. Колонка с заголовком из закрытого списка объявляется полем `grammar.notes[scope=source, row_offset=0]`. Схема профиля v2, парсер, writer, гейт публикации и шаблон превью уже это принимают и отображают — проверено прогоном рукописного профиля, менять их не нужно.

**Tech Stack:** Python, `loc_kit_ingest` (standalone, без Django), Django-контракт в `weblate/trans/tests/`.

**Дизайн:** `docs/plans/2026-08-10-loc-kit-glossary-note-column-design.md` (утверждён).

**Ключевые решения (не пересматривать по ходу):**

- **Опознание только по тексту заголовка.** Правило по форме данных (пороги `_DESCRIPTION_MIN_CHARS = 80` / `_DESCRIPTION_RATIO = 4.0`, `infer.py:50-51`) отвергнуто: колонка «Character limit» прошла бы порог и молча уехала бы в каждый промпт к модели. Отказ дешевле ложного срабатывания.
- **Точное совпадение, не префиксное.** Префикс принял бы `Context ID` и `Note count`. `Комментарий переводчику` остаётся случаем отказа.
- **Одна колонка на лист, только `scope: source`.** Заметки к целевому языку (`fr note` → `<note from="translator">`) схема поддерживает, но не выводятся.
- **Порядок в `grammar.notes`.** Для pairs: сначала запись строки-описания (`row_offset: 1`), затем колонка (`row_offset: 0`). `_join_notes` (`parser.py:368`) склеивает в порядке объявления, основное описание идёт первым.
- **Никаких правок схемы, парсера, writer'а и UI.** Если по ходу кажется, что нужна — остановиться, это признак ошибки в реализации.

**Общие правила исполнения:** после правок `loc_kit_ingest/*.py` — `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`; контейнерные тесты `./rundev.sh test <path> -n0`; линтеры и полный сьют — один раз в конце (Task 6).

---

### Task 1: список заголовков и поиск колонки

**Files:**
- Modify: `loc_kit_ingest/infer.py:41-53` (константы), новая функция перед `infer_glossary_profile:462`
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: падающие тесты** — в конец файла:

```python
FLAT_WITH_NOTE = [
    ["ru", "en", "tr", "fr", "note"],
    ["Russian", "English", "Turkish", "French", "Note"],
    ["Партия", "Party", "Parti", "Parti", "Правящая политическая партия."],
    ["Самосбор", "Samosbor", "Samosbor", "Samosbor", "Термин вселенной."],
]


def test_note_column_becomes_a_source_note_field() -> None:
    document, notes = infer_glossary_profile("S", FLAT_WITH_NOTE, component="s")
    (comp,) = document["components"]
    assert [lang["code"] for lang in comp["languages"]] == ["ru", "en", "tr", "fr"]
    assert comp["grammar"]["notes"] == [
        {"scope": "source", "column": 5, "header": "note", "row_offset": 0}
    ]
    assert any("explanation of the source term" in note for note in notes)
    parse_profile(document)  # closed schema accepts it


@pytest.mark.parametrize("header", ["Notes", "ОПИСАНИЕ", "Comment", " context "])
def test_note_header_matching_ignores_case_and_padding(header: str) -> None:
    rows = [row[:] for row in FLAT_WITH_NOTE]
    rows[0][4] = header
    document, _notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["notes"][0]["column"] == 5


def test_two_note_columns_are_refused() -> None:
    rows = [
        ["ru", "en", "note", "comment"],
        ["Партия", "Party", "Проза", "Ещё проза"],
    ]
    with pytest.raises(InferenceError, match="columns 3, 4 all look like term notes"):
        infer_glossary_profile("S", rows, component="s")


def test_empty_note_column_is_excluded_with_a_note() -> None:
    rows = [
        ["ru", "en", "note"],
        ["Партия", "Party", ""],
        ["Самосбор", "Samosbor", ""],
    ]
    document, notes = infer_glossary_profile("S", rows, component="s")
    (comp,) = document["components"]
    assert "notes" not in comp["grammar"]
    assert any("column 3" in note and "empty" in note for note in notes)


def test_unrecognised_extra_column_names_the_accepted_headers() -> None:
    rows = [
        ["ru", "en", "Character limit"],
        ["Партия", "Party", "40"],
    ]
    with pytest.raises(InferenceError, match="rename the header to one of"):
        infer_glossary_profile("S", rows, component="s")
```

**Step 2: убедиться, что падают**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k note -q`
Expected: FAIL — первые тесты падают на `InferenceError: column 5 ('note') holds data but is not a recognised language column`.

**Step 3: константа** — после `_KEY_HEADER_DENYLIST` (`infer.py:45`):

```python
# A column of prose about the term, not a translation of it. Recognised by
# header text alone: a length rule would accept "Character limit" and route it
# into every LLM prompt, where a wrong guess is invisible.
_NOTE_HEADERS = frozenset(
    {
        "note",
        "notes",
        "comment",
        "comments",
        "description",
        "descriptions",
        "explanation",
        "explanations",
        "context",
        "usage",
        "definition",
        "meaning",
        "примечание",
        "примечания",
        "комментарий",
        "комментарии",
        "описание",
        "описания",
        "пояснение",
        "пояснения",
        "контекст",
        "определение",
        "значение",
    }
)
```

**Step 4: функция поиска** — перед `infer_glossary_profile` (`infer.py:462`):

```python
def _find_note_column(
    rows: list[list[str]],
    header_row: list[str],
    content_indexes: list[int],
    languages: dict[int, str],
    notes: list[str],
) -> int | None:
    """
    Return the column holding prose about the term, or None.

    Appends the decision to ``notes`` so it reaches the import preview: the
    text of this column ends up in every LLM prompt for a matching string,
    and the operator has to see that before creating the component.
    """
    candidates = [
        col
        for col in range(len(header_row))
        if col not in languages
        and _cell(header_row, col).strip().casefold() in _NOTE_HEADERS
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        shown = ", ".join(str(col + 1) for col in candidates)
        msg = (
            f"columns {shown} all look like term notes; this layout needs an "
            "explicit profile"
        )
        raise InferenceError(msg)

    col = candidates[0]
    header_text = _cell(header_row, col)
    if not any(_cell(rows[index], col).strip() for index in content_indexes):
        notes.append(f"column {col + 1} ({header_text!r}) is empty; excluded")
        return None
    notes.append(
        f"column {col + 1} ({header_text!r}) -> explanation of the source term"
    )
    return col
```

**Step 5: вызов и вычитание из проверки** — в `infer_glossary_profile`, после блока «no populated language column» (`infer.py:545-547`) вставить:

```python
    note_col = _find_note_column(rows, header_row, content_indexes, languages, notes)
```

и заменить строку `unmapped = sorted(populated - languages.keys())` (`infer.py:560`) на:

```python
    mapped = languages.keys() | ({note_col} if note_col is not None else set())
    unmapped = sorted(populated - mapped)
```

**Step 6: новый текст отказа** — заменить тело `msg` (`infer.py:564-568`):

```python
        msg = (
            f"column {col + 1} ({header_text!r}) holds data but is not a "
            "recognised language column; rename the header to one of "
            "note, description, comment, explanation, примечание, описание, "
            "комментарий, пояснение to import it as a term explanation, or "
            "supply an explicit profile"
        )
```

**Step 7: прогон**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`
Expected: PASS — четыре теста про `note` зелёные; `test_note_column_becomes_a_source_note_field` пока падает на `assert comp["grammar"]["notes"] == [...]`, потому что грамматика ещё не собирает поле. Это ожидаемо, чинится в Task 2.

Если падают старые тесты — остановиться: значит вычитание `note_col` задело язык.

**Step 8: коммит**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): recognise a glossary note column by its header"
```

---

### Task 2: поле в грамматике

**Files:**
- Modify: `loc_kit_ingest/infer.py:677-715`
- Test: `loc_kit_ingest/tests/test_infer_glossary.py`

**Step 1: тест на порядок для pairs** — в конец файла:

```python
PAIRS_WITH_NOTE = [
    ["ru", "en", "note"],
    ["Партия", "Party", "Мужской род во французском."],
    ["Правящая политическая партия страны, а не партия товара.", "The ruling party of the country, not a batch of goods.", ""],
    ["Самосбор", "Samosbor", "Транслитерируется."],
    ["Аномальное явление, разрушающее материю вокруг себя.", "An anomaly that dissolves the matter around it.", ""],
]


def test_pairs_layout_declares_the_description_before_the_column() -> None:
    document, _notes = infer_glossary_profile("S", PAIRS_WITH_NOTE, component="s")
    (comp,) = document["components"]
    assert comp["grammar"]["regions"][0]["record_stride"] == 2
    scopes = [(n["scope"], n["column"], n["row_offset"]) for n in comp["grammar"]["notes"]]
    # The description row first: _join_notes concatenates in declaration order.
    assert scopes[0] == ("source", 1, 1)
    assert scopes[-1] == ("source", 3, 0)
    parse_profile(document)


def test_note_column_on_a_description_row_is_refused() -> None:
    rows = [row[:] for row in PAIRS_WITH_NOTE]
    rows[2][2] = "проза на строке описания"
    with pytest.raises(InferenceError, match="row\\(s\\) 3"):
        infer_glossary_profile("S", rows, component="s")
```

**Step 2: убедиться, что падают**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -k pairs_layout_declares -q`
Expected: FAIL — `KeyError`/несовпадение, поля колонки в `notes` нет.

**Step 3: сборка списка** — заменить блок `infer.py:677-715` на:

```python
    grammar: dict[str, Any] = {
        "type": "record-map",
        "skip_rows": sorted(index + 1 for index in skip_rows),
        "regions": regions,
        "term_row_offset": 0,
    }
    note_fields: list[dict[str, Any]] = []
    if paired:
        # A description cell is read by a note field, and a note field may
        # only name an initial target language. A language that carries
        # descriptions but misses a term has nowhere to put them, and every
        # unread populated cell is a parse error - refuse instead of
        # emitting a profile that cannot survive its own gate.
        for col in sorted(languages):
            code = languages[col]
            if col == source_col or code in target_langs:
                continue
            offenders = [
                index + 1
                for index in description_rows
                if _cell(rows[index], col).strip()
            ][:_MISSING_ROWS_SHOWN]
            if offenders:
                shown = ", ".join(str(row) for row in offenders)
                msg = (
                    f"language {code} has descriptions on row(s) {shown} but "
                    "is missing terms; this layout needs an explicit profile"
                )
                raise InferenceError(msg)
        note_fields.extend(
            {
                "scope": "source" if col == source_col else "target",
                "column": col + 1,
                "header": _cell(header_row, col),
                "row_offset": 1,
            }
            | ({} if col == source_col else {"language": languages[col]})
            for col in sorted(languages)
            if col == source_col or languages[col] in target_langs
        )
    if note_col is not None:
        if paired:
            # The note field reads offset 0 only. A populated cell on the
            # description row would be claimed by nothing and fail the parse
            # as tbx.unmapped_cell, after the profile passed the schema.
            offenders = [
                index + 1
                for index in description_rows
                if _cell(rows[index], note_col).strip()
            ][:_MISSING_ROWS_SHOWN]
            if offenders:
                shown = ", ".join(str(row) for row in offenders)
                msg = (
                    f"column {note_col + 1} holds text on description row(s) "
                    f"{shown}; this layout needs an explicit profile"
                )
                raise InferenceError(msg)
        # Declared last: the description row carries the primary text.
        note_fields.append(
            {
                "scope": "source",
                "column": note_col + 1,
                "header": _cell(header_row, note_col),
                "row_offset": 0,
            }
        )
    if note_fields:
        grammar["notes"] = note_fields
```

**Step 4: прогон**

Run: `cd loc_kit_ingest && uv run pytest tests/test_infer_glossary.py -q`
Expected: PASS, все тесты файла, включая `test_note_column_becomes_a_source_note_field` из Task 1.

**Step 5: коммит**

```bash
git add loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py
git commit -m "feat(loc-kit): emit the glossary note column as a source note field"
```

---

### Task 3: сквозной прогон CLI

**Files:**
- Test: `loc_kit_ingest/tests/test_pipeline.py`

**Step 1: тест** — в конец файла (стиль существующих тестов файла: временный каталог, запуск пайплайна, проверка артефактов):

```python
def test_flat_glossary_with_a_note_column_needs_no_profile(tmp_path) -> None:
    kit = tmp_path / "Glossary.csv"
    kit.write_text(
        "ru,en,fr,note\n"
        "Russian,English,French,Note\n"
        "Партия,Party,Parti,"
        '"Правящая политическая партия. Во французском le Parti, мужской род."\n'
        "Самосбор,Samosbor,Samosbor,Термин вселенной.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"

    report = run_pipeline(kit, profile_path=None, out_dir=out, glossary=True)

    assert report.exit_code == 0
    rendered = (out / "Glossary" / "tbx" / "fr.tbx").read_text(encoding="utf-8")
    assert "<descrip>Правящая политическая партия." in rendered
    assert "le Parti, мужской род.</descrip>" in rendered
```

Точную сигнатуру запуска взять из соседних тестов файла: если там вызывается CLI через `subprocess`, а не `run_pipeline`, повторить их способ, не изобретая свой.

**Step 2: прогон**

Run: `cd loc_kit_ingest && uv run pytest tests/test_pipeline.py -k note_column -q`
Expected: PASS.

**Step 3: коммит**

```bash
git add loc_kit_ingest/tests/test_pipeline.py
git commit -m "test(loc-kit): cover the note column end to end in the CLI"
```

---

### Task 4: контракт визарда Weblate

**Files:**
- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py` (фикстуры рядом с `GLOSSARY_PAIRS_CSV:545`)

**Step 1: синхронизировать пакет в контейнер**

```bash
cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
```

Без этого тест увидит старую версию `infer.py` и упадёт по причине, не связанной с кодом.

**Step 2: фикстура и тест** — фикстура рядом с существующими:

```python
GLOSSARY_NOTE_CSV = (
    "ru,en,fr,note\n"
    "Russian,English,French,Note\n"
    "Партия,Party,Parti,"
    '"Правящая политическая партия. Во французском le Parti, мужской род."\n'
    "Самосбор,Samosbor,Samosbor,Термин вселенной.\n"
)
```

Тест — в тот же класс, где лежит `test_term_description_sheet_maps_descriptions_as_explanations:729`:

```python
    @override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False)
    def test_note_column_reaches_the_live_term_explanation(self) -> None:
        """Колонка note доезжает до Explanation, откуда её берёт промпт."""
        self._start(upload=self._csv("Terms.csv", GLOSSARY_NOTE_CSV), slug=self.slug)
        draft = self._draft()
        draft.refresh_from_db()

        self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
        preview = json.loads(draft.preview_json)
        self.assertEqual(preview["term_count"], 2)
        self.assertEqual(preview["note_count"], 2)
        self.assertIn(
            "мужской род", preview["terms"][0]["source_explanation"]
        )

        page = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertContains(page, "мужской род")

        self._confirm()
        component = Component.objects.get(slug=self.slug)
        unit = component.source_translation.unit_set.get(source="Партия")
        self.assertIn("мужской род", unit.explanation)
```

**Step 3: прогон**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryWizardTest::test_note_column_reaches_the_live_term_explanation -n0`

Имя класса взять фактическое — из строки 729 файла.

Expected: PASS. Если падает `note_count` — проверить, что `cp` из шага 1 выполнен.

**Step 4: весь файл**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0`
Expected: PASS целиком. Особое внимание — тест на строке ~690: `GLOSSARY_CSV` (`domain,ru,en,note_ru,note_en`) обязан по-прежнему оставаться на `SHEET_SELECTED`, потому что `domain` и `note_ru`/`note_en` не входят в список. Если он позеленел — список заголовков стал шире задуманного.

**Step 5: коммит**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py dev-docker/data/python/loc_kit_ingest/
git commit -m "test(loc-kit): pin the note column through the glossary wizard"
```

---

### Task 5: документация

**Files:**
- Modify: `docs/specs/loc-kit-ingest.md:106-108`
- Modify: `docs/changes.rst` (секция `Weblate 2026.8.1`, rubric `Improvements`)

**Step 1: спека.** Заменить предложение «Любая заполненная не-языковая колонка отклоняется (`InferenceError`) - парсер не угадывает назначение колонки.» на:

```rst
Колонка, чей заголовок точно совпадает с одним из закрытого списка
(`note`, `notes`, `comment`, `comments`, `description`, `descriptions`,
`explanation`, `explanations`, `context`, `usage`, `definition`, `meaning`,
`примечание`, `примечания`, `комментарий`, `комментарии`, `описание`,
`описания`, `пояснение`, `пояснения`, `контекст`, `определение`,
`значение`), объявляется полем `grammar.notes` со `scope: source` и
`row_offset: 0` - её текст становится пояснением к исходному термину.
Таких колонок допускается не больше одной на лист. Опознание идёт только по
заголовку: правило по длине текста приняло бы колонку вроде «Лимит символов»
и молча отправило бы её в каждый промпт. Любая другая заполненная
не-языковая колонка отклоняется (`InferenceError`) - парсер не угадывает
назначение колонки.
```

**Step 2: changelog.** Одна запись в конец rubric `Improvements`:

```rst
* Glossary table import now recognises a column of term notes by its header and imports it as the source term explanation, which automatic suggestion services receive as context, see :ref:`uploading-glossary-tables`.
```

**Step 3: коммит**

```bash
git add docs/specs/loc-kit-ingest.md docs/changes.rst
git commit -m "docs(loc-kit): document the glossary note column"
```

---

### Task 6: полный прогон и живой smoke

**Step 1: сьюты**

```bash
cd loc_kit_ingest && uv run pytest
cd .. && cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
```

Expected: оба зелёные.

**Step 2: линтеры**

```bash
uv run prek run --files loc_kit_ingest/infer.py loc_kit_ingest/tests/test_infer_glossary.py loc_kit_ingest/tests/test_pipeline.py weblate/trans/tests/test_loc_kit_ingest_contract.py docs/specs/loc-kit-ingest.md docs/changes.rst
```

**Step 3: живой smoke кликами.** Не `form.submit()` и не прямой POST — только настоящие клики по контролам, иначе проверяется не тот путь.

1. Собрать файл `Glossary.csv` с содержимым `GLOSSARY_NOTE_CSV`.
2. `http://localhost:3001/create/component/?project=<id>` → вкладка «Upload translation files» → файл → галка «Use as glossary» → Continue.
3. Превью: 2 термина, в колонке «Source note» видно «Правящая политическая партия…», в предупреждениях — `column 4 ('note') -> explanation of the source term`.
4. Создать компонент. В глоссарии открыть термин «Партия» → поле Explanation заполнено.
5. Автоперевод строки, содержащей слово «Партия», на fr движком OpenRouter: убедиться, что перевод `Parti`, а не `la partie`.

**Step 4: финальный коммит, если что-то поправилось**

```bash
git add -A -- loc_kit_ingest weblate/trans/tests docs dev-docker/data/python/loc_kit_ingest
git commit -m "fix(loc-kit): close defects found in the note column smoke test"
```

## После этого

Остаются открытыми и не входят в этот план:

- заметки к конкретному целевому языку (`note_ru` / `note_en` → `<note from="translator">`) — фикстура `GLOSSARY_CSV` в контрактном тесте всё ещё их не принимает;
- глоссарные флаги (`terminology`, `read-only`, `forbidden`) из таблицы — `writer.py` не пишет их вообще;
- повторный импорт в существующий глоссарий — `docs/plans/2026-08-10-loc-kit-glossary-update-existing.md`.
