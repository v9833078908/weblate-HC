# Loc-kit Ingest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use @executing-plans to implement this plan task-by-task.

**Goal:** CLI-препроцессор, который разбирает разнородные лок-киты Hero Craft (`.xlsx`/`.csv`/`.tsv`) в монолингвальные PO-файлы по языку на компонент, готовые к разовому севу в Weblate.

**Architecture:** Отдельный трекаемый пакет `loc_kit_ingest/` в корне форка, вне Django-приложения. Пайплайн: `reader` (файл → строки по листам) → `layout` (автоопределение раскладки) → `normalize` (строки → юниты) → `validate` (отчёт) → `po_writer` (юниты → PO). Автоопределение перекрывается хинтом (escape hatch). Продукт — одноразовый онбординг-импортёр: Weblate становится источником правды, повторный/идемпотентный импорт не нужен.

**Tech Stack:** Python 3.12+, `openpyxl` (xlsx), stdlib `csv`, translate-toolkit `translate.storage.pypo` (запись PO) — всё уже в `.venv`. Тесты: pytest (изолированный `pytest.ini`, без Django). Запуск через `uv run`.

**Design spec:** `docs/specs/loc-kit-ingest.md` (полный дизайн, решения, UI-сценарии).

---

## Контекст и принятые решения

Подтверждённый дизайн: `docs/specs/loc-kit-ingest.md`. Ключевое:

- Weblate — источник правды, таблица — разовый seed; sync/обратной записи нет.
- Канонический формат — монолингвальный PO: `msgid`=ключ, `msgstr`=строка (в шаблоне — исходный текст), `#.`=Character/заметки, `#:`=числовой Id движка.
- Исходный язык определяется на кит (самая заполненная из `ru`/`en`), с оверрайдом.
- Разметку (`[shake]`, `<color=#..>`, `{value:cond:..}`, `&#13;`) переносим байт-в-байт.

Три эталонных кита (в `/Users/eli/Downloads/`, копии — в фикстуры):
`Heart Abyss_Localization - Temple.csv`, `... - Terms.csv`, `UI.xlsx`.

---

## Preflight: скелет пакета и изолированный тест-харнесс

**Files:**
- Create: `loc_kit_ingest/pytest.ini`
- Create: `loc_kit_ingest/loc_kit_ingest/__init__.py`
- Create: `loc_kit_ingest/tests/__init__.py`
- Create: `loc_kit_ingest/tests/test_smoke.py`

**Step 1: Структура каталогов**

```
loc_kit_ingest/                 # проект (rootdir для pytest, НЕ пакет)
  pytest.ini
  loc_kit_ingest/               # импортируемый пакет
    __init__.py
  tests/
    __init__.py
    fixtures/
```

**Step 2: `loc_kit_ingest/pytest.ini`** — изолирует от корневого `pyproject.toml` (иначе подтянется `--cov=weblate` + `DJANGO_SETTINGS_MODULE` + БД):

```ini
[pytest]
addopts = -q
pythonpath = .
python_files = test_*.py
testpaths = tests
```

`pythonpath = .` (относительно rootdir = `loc_kit_ingest/`) делает вложенный пакет `loc_kit_ingest` импортируемым.

**Step 3: `loc_kit_ingest/tests/test_smoke.py`**

```python
def test_harness_runs():
    import loc_kit_ingest  # noqa: F401
```

**Step 4: Прогнать — харнесс работает, Django НЕ грузится**

Run: `cd loc_kit_ingest && uv run pytest`
Expected: `1 passed`, без ошибок про БД/Django/`weblate.settings_test`.

**Step 5: Commit**

```bash
git add loc_kit_ingest/
git commit -m "chore(loc-ingest): scaffold isolated package and test harness"
```

---

## Task 1: Маппинг языков (`langs.py`)

**Files:**
- Create: `loc_kit_ingest/loc_kit_ingest/langs.py`
- Test: `loc_kit_ingest/tests/test_langs.py`

**Step 1: Тест**

```python
import pytest
from loc_kit_ingest.langs import normalize_lang

@pytest.mark.parametrize("header,code", [
    ("ru", "ru"), ("Russian", "ru"), ("русский ", "ru"),
    ("en", "en"), ("English(en)", "en"), ("Russian(ru)", "ru"),
    ("pt-PT", "pt_PT"), ("zh-CN", "zh_Hans"), ("简体中文", "zh_Hans"),
    ("zh-TC", "zh_Hant"), ("繁體中文", "zh_Hant"), ("日本語", "ja"),
    ("Character", None), ("id", None), ("", None),
])
def test_normalize_lang(header, code):
    assert normalize_lang(header) == code
```

**Step 2: Run — FAIL** (`cd loc_kit_ingest && uv run pytest tests/test_langs.py`), нет модуля.

**Step 3: Реализация**

```python
from __future__ import annotations

import re

_ALIASES: dict[str, str] = {
    "ru": "ru", "russian": "ru", "русский": "ru",
    "en": "en", "english": "en",
    "fr": "fr", "french": "fr",
    "it": "it", "italian": "it",
    "de": "de", "german": "de",
    "es": "es", "spanish": "es", "spain": "es",
    "ja": "ja", "japanese": "ja", "日本語": "ja",
    "ko": "ko", "korean": "ko",
    "pt-pt": "pt_PT", "pt_pt": "pt_PT", "portuguese": "pt_PT",
    "zh-cn": "zh_Hans", "zh_cn": "zh_Hans", "简体中文": "zh_Hans",
    "zh-tc": "zh_Hant", "zh_tc": "zh_Hant", "繁體中文": "zh_Hant",
}
_PAREN = re.compile(r"\(([^)]+)\)")


def normalize_lang(header: str | None) -> str | None:
    """Map a spreadsheet column header (code or human name) to a Weblate code."""
    if not header:
        return None
    token = header.strip()
    if (code := _ALIASES.get(token.casefold())) is not None:
        return code
    if (m := _PAREN.search(token)) and (
        code := _ALIASES.get(m.group(1).strip().casefold())
    ) is not None:
        return code
    return _ALIASES.get(_PAREN.sub("", token).strip().casefold())
```

**Step 4: Run — PASS.**

**Step 5: Commit** `feat(loc-ingest): map spreadsheet headers to Weblate language codes`.

---

## Task 2: Чтение источников (`reader.py`)

**Files:**
- Create: `loc_kit_ingest/loc_kit_ingest/reader.py`
- Test: `loc_kit_ingest/tests/test_reader.py`
- Test helper: `loc_kit_ingest/tests/conftest.py`

**Step 1: `conftest.py` — строит xlsx-фикстуру программно** (бинарь в план не кладём):

```python
from pathlib import Path

import pytest


@pytest.fixture
def ui_xlsx(tmp_path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Лист 1"
    ws.append(["UI", "", "", ""])
    ws.append(["Key", "Id", "English(en)", "Russian(ru)"])
    ws.append(["vibration", "142945292288", "Vibration", "Вибрация"])
    ws.append(["loading", "136857827676160", "Loading", "Загрузка"])
    ws.append(["multiline", "1", "Line1\nLine2", "Строка1\nСтрока2"])
    path = tmp_path / "UI.xlsx"
    wb.save(path)
    return path
```

**Step 2: Тест**

```python
from loc_kit_ingest.reader import read_sheets


def test_read_csv(tmp_path):
    p = tmp_path / "Temple.csv"
    p.write_text('id,ru,en\nk1,"а,б",hi\n', encoding="utf-8")
    sheets = read_sheets(p)
    assert list(sheets) == ["Temple"]
    assert sheets["Temple"][1] == ["k1", "а,б", "hi"]


def test_read_xlsx_multiline(ui_xlsx):
    sheets = read_sheets(ui_xlsx)
    rows = sheets["Лист 1"]
    assert rows[1] == ["Key", "Id", "English(en)", "Russian(ru)"]
    assert rows[4][2] == "Line1\nLine2"  # multiline cell preserved
```

**Step 3: Реализация**

```python
from __future__ import annotations

import csv
from pathlib import Path


def read_sheets(path: str | Path) -> dict[str, list[list[str]]]:
    """Read a loc-kit file into {sheet_name: rows}, each row a list of str cells."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with p.open(newline="", encoding="utf-8-sig") as handle:
            rows = [list(row) for row in csv.reader(handle, delimiter=delimiter)]
        return {p.stem: rows}
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(p, read_only=True, data_only=True)
        try:
            return {
                sheet.title: [
                    ["" if cell is None else str(cell) for cell in row]
                    for row in sheet.iter_rows(values_only=True)
                ]
                for sheet in workbook.worksheets
            }
        finally:
            workbook.close()
    msg = f"Unsupported input format: {suffix}"
    raise ValueError(msg)
```

**Step 4: Run — PASS.** **Step 5: Commit** `feat(loc-ingest): read xlsx/csv/tsv kits into rows`.

---

## Task 3: Модель юнита (`model.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/model.py`; Test `tests/test_model.py`.

**Step 1: Тест** — dataclass с дефолтами:

```python
from loc_kit_ingest.model import Unit


def test_unit_defaults():
    u = Unit(key="k1", values={"en": "Hi"})
    assert u.comments == [] and u.references == [] and u.section is None
```

**Step 2: Run — FAIL. Step 3:**

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Unit:
    key: str
    values: dict[str, str]                              # lang code -> string
    comments: list[str] = field(default_factory=list)  # -> PO "#." notes
    references: list[str] = field(default_factory=list) # -> PO "#:" locations
    section: str | None = None
    row: int = 0                                        # 1-based source row
```

**Step 4: PASS. Step 5: Commit** `feat(loc-ingest): add Unit model`.

---

## Task 4: Автоопределение раскладки (`layout.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/layout.py`; Test `tests/test_layout.py`. Fixtures: `tests/fixtures/temple.csv`, `tests/fixtures/terms.csv` (первые ~12 строк каждого кита, включая строки-метки/секции/пустые — скопировать из Downloads).

**Step 1: Тесты** (по одному на кит):

```python
from pathlib import Path

from loc_kit_ingest.layout import detect_layout
from loc_kit_ingest.reader import read_sheets

FIX = Path(__file__).parent / "fixtures"


def _rows(name):
    return next(iter(read_sheets(FIX / name).values()))


def test_temple_layout():
    lay = detect_layout(_rows("temple.csv"))
    assert lay.key_col == 0
    assert lay.lang_cols[2] == "ru" and lay.lang_cols[3] == "en"
    assert 1 in lay.meta_cols               # Character column
    assert lay.source_lang == "ru"


def test_terms_layout_keyless():
    lay = detect_layout(_rows("terms.csv"))
    assert lay.key_col is None              # no id column
    assert lay.lang_cols[0] == "ru"
    assert lay.source_lang == "ru"


def test_ui_layout_dual_key(ui_xlsx):
    lay = detect_layout(next(iter(read_sheets(ui_xlsx).values())))
    assert lay.key_col == 0                 # semantic "Key"
    assert lay.ref_cols == [1]              # numeric "Id"
    assert set(lay.lang_cols.values()) == {"en", "ru"}
    assert lay.source_lang == "en"
```

**Step 2: Run — FAIL.**

**Step 3: Реализация**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .langs import normalize_lang

_KEY_HEADERS = {"id", "key", "ключ"}


@dataclass
class Layout:
    header_row: int
    lang_cols: dict[int, str]                       # col -> lang code
    key_col: int | None
    ref_cols: list[int] = field(default_factory=list)
    meta_cols: dict[int, str] = field(default_factory=dict)  # col -> name
    source_lang: str = "en"


def _lang_cols(row: list[str]) -> dict[int, str]:
    return {j: code for j, c in enumerate(row) if (code := normalize_lang(c))}


def _col_is_numeric(rows, header_row, col, sample=12) -> bool:
    seen = 0
    for row in rows[header_row + 1 :]:
        if col < len(row) and row[col].strip():
            if not row[col].strip().isdigit():
                return False
            seen += 1
            if seen >= sample:
                break
    return seen > 0


def _detect_source_lang(rows, header_row, lang_cols) -> str:
    filled = {code: 0 for code in lang_cols.values()}
    for row in rows[header_row + 1 :]:
        for col, code in lang_cols.items():
            if col < len(row) and row[col].strip():
                filled[code] += 1
    for pref in ("ru", "en"):                       # HC source is ru or en
        if filled.get(pref):
            return pref
    return max(filled, key=filled.get) if filled else "en"


def detect_layout(rows, *, source_lang=None, hint=None) -> Layout:
    hint = hint or {}
    header_row = None
    for i, row in enumerate(rows[:20]):
        langs = _lang_cols(row)
        has_key = any(c.strip().lower() in _KEY_HEADERS for c in row)
        if len(langs) >= 2 or (has_key and langs):
            header_row = i
            break
    if header_row is None:
        msg = "No header row with language columns found"
        raise ValueError(msg)

    header = rows[header_row]
    lang_cols = _lang_cols(header)

    id_like = [
        j
        for j, c in enumerate(header)
        if j not in lang_cols and c.strip().lower() in _KEY_HEADERS
    ]
    key_col: int | None = None
    ref_cols: list[int] = []
    for j in id_like:
        if _col_is_numeric(rows, header_row, j) or key_col is not None:
            ref_cols.append(j)
        else:
            key_col = j

    label_names = _label_row_names(rows, header_row, lang_cols, id_like)
    meta_cols = {
        j: (header[j].strip() or label_names.get(j) or f"col{j}")
        for j in range(len(header))
        if j not in lang_cols and j not in id_like and j != key_col
    }

    resolved_source = (
        source_lang or hint.get("source_lang") or _detect_source_lang(rows, header_row, lang_cols)
    )
    lay = Layout(header_row, lang_cols, key_col, ref_cols, meta_cols, resolved_source)
    _apply_hint(lay, hint)
    return lay


def _label_row_names(rows, header_row, lang_cols, id_like) -> dict[int, str]:
    """If the row after the header is a label row (e.g. id-ignore / language names),
    borrow its cells to name unlabeled metadata columns (Temple's Character)."""
    nxt = header_row + 1
    if nxt >= len(rows):
        return {}
    row = rows[nxt]
    key_cell = row[0].strip().lower() if row else ""
    looks_label = key_cell == "id-ignore" or all(
        normalize_lang(row[c]) is not None or not row[c].strip()
        for c in lang_cols
    )
    if not looks_label:
        return {}
    return {
        j: row[j].strip()
        for j in range(len(row))
        if j not in lang_cols and j not in id_like and row[j].strip()
    }


def _apply_hint(lay: Layout, hint: dict) -> None:
    if "key_column" in hint:
        lay.key_col = hint["key_column"]
    if "meta_columns" in hint:
        lay.meta_cols.update(hint["meta_columns"])
```

**Step 4: Run — PASS** (правь эвристики до зелёного; тесты фиксируют контракт).
**Step 5: Commit** `feat(loc-ingest): auto-detect sheet layout with hint override`.

---

## Task 5: Нормализация в юниты (`normalize.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/normalize.py`; Test `tests/test_normalize.py`.

**Step 1: Тесты** — покрыть: пропуск метки/секции/пустых; слаг для Terms; метаданные и reference; сохранность разметки.

```python
from loc_kit_ingest.layout import detect_layout
from loc_kit_ingest.normalize import build_units
from loc_kit_ingest.reader import read_sheets
from tests.test_layout import FIX, _rows


def test_temple_units_skip_junk_and_keep_markup():
    rows = _rows("temple.csv")
    units = build_units(rows, detect_layout(rows))
    keys = {u.key for u in units}
    assert "id-ignore" not in keys                 # label row skipped
    u = next(u for u in units if u.key == "intro_temple_1_1")
    assert u.values["ru"] == "Не расслабляйтесь."   # trailing space trimmed
    assert any(c.startswith("Character") for c in u.comments)  # Joe
    shaken = next(u for u in units if "[shake]" in u.values.get("ru", ""))
    assert "[/shake]" in shaken.values["ru"]        # markup byte-preserved


def test_terms_units_slug_and_sections():
    rows = _rows("terms.csv")
    units = build_units(rows, detect_layout(rows))
    assert all(u.key for u in units)                # every unit has a (slugged) key
    assert len({u.key for u in units}) == len(units)  # keys unique
    assert any(u.section for u in units)            # section captured


def test_ui_units_reference(ui_xlsx):
    rows = next(iter(read_sheets(ui_xlsx).values()))
    from loc_kit_ingest.layout import detect_layout as dl
    units = build_units(rows, dl(rows))
    vib = next(u for u in units if u.key == "vibration")
    assert vib.references == ["142945292288"]
    assert vib.values == {"en": "Vibration", "ru": "Вибрация"}
```

**Step 2: Run — FAIL. Step 3:**

```python
from __future__ import annotations

import re

from .layout import Layout
from .model import Unit

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.strip().casefold()).strip("_")[:60] or "item"


def _is_label_row(row, layout: Layout) -> bool:
    if row and row[0].strip().lower() == "id-ignore":
        return True
    from .langs import normalize_lang

    langs = [layout.lang_cols[c] for c in layout.lang_cols if c < len(row)]
    return bool(langs) and all(
        normalize_lang(row[c]) is not None
        for c in layout.lang_cols
        if c < len(row) and row[c].strip()
    ) and any(row[c].strip() for c in layout.lang_cols if c < len(row))


def _is_blank(row) -> bool:
    return not any(c.strip() for c in row)


def _is_section(row, layout: Layout) -> bool:
    has_lang = any(
        c < len(row) and row[c].strip() for c in layout.lang_cols
    )
    has_meta = any(row[c].strip() for c in range(len(row)) if c not in layout.lang_cols)
    return has_meta and not has_lang


def build_units(rows, layout: Layout) -> list[Unit]:
    units: list[Unit] = []
    seen: dict[str, int] = {}
    section: str | None = None
    for idx in range(layout.header_row + 1, len(rows)):
        row = rows[idx]
        if _is_blank(row) or _is_label_row(row, layout):
            continue
        if layout.key_col is None and _is_section(row, layout):
            # keyless kit: a text-only row is a category divider
            section = next((row[c].strip() for c in range(len(row)) if row[c].strip()), None)
            continue

        values = {
            code: row[c].strip()
            for c, code in layout.lang_cols.items()
            if c < len(row) and row[c].strip()
        }
        if not values:
            continue

        if layout.key_col is not None and layout.key_col < len(row) and row[layout.key_col].strip():
            key = row[layout.key_col].strip()
        else:
            base = values.get(layout.source_lang) or next(iter(values.values()))
            key = f"{_slug(section)}_{_slug(base)}" if section else _slug(base)
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 0

        comments = [
            f"{name}: {row[c].strip()}"
            for c, name in layout.meta_cols.items()
            if c < len(row) and row[c].strip()
        ]
        references = [
            row[c].strip() for c in layout.ref_cols if c < len(row) and row[c].strip()
        ]
        units.append(
            Unit(key=key, values=values, comments=comments,
                 references=references, section=section, row=idx + 1)
        )
    return units
```

**Step 4: Run — PASS.** **Step 5: Commit** `feat(loc-ingest): normalize rows into units (slug, sections, metadata)`.

---

## Task 6: Валидация и отчёт (`validate.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/validate.py`; Test `tests/test_validate.py`.

**Step 1: Тесты** — каждое предупреждение срабатывает:

```python
from loc_kit_ingest.model import Unit
from loc_kit_ingest.validate import validate


def test_untranslated_placeholder():
    units = [Unit("k", {"ru": "Дом", "en": "Дом"}, row=3)]
    w = validate(units, source_lang="ru")
    assert any("en == ru" in x.message or "untranslated" in x.message.lower() for x in w)


def test_column_shift_alphabet():
    units = [Unit("k", {"ru": "Дом", "en": "Кириллица тут"}, row=4)]
    w = validate(units, source_lang="ru")
    assert any("shift" in x.message.lower() or "alphabet" in x.message.lower() for x in w)
```

**Step 2: Run — FAIL. Step 3:**

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Unit

_CYR = re.compile(r"[а-яёА-ЯЁ]")
_LAT = re.compile(r"[a-zA-Z]")


@dataclass
class Warning:
    row: int
    key: str
    message: str


def _mostly_cyrillic(text: str) -> bool:
    cyr, lat = len(_CYR.findall(text)), len(_LAT.findall(text))
    return cyr > lat


def validate(units: list[Unit], *, source_lang: str) -> list[Warning]:
    warnings: list[Warning] = []
    for u in units:
        src = u.values.get(source_lang, "")
        for code, val in u.values.items():
            if code == source_lang:
                continue
            if val and src and val == src:
                warnings.append(Warning(u.row, u.key, f"{code} == {source_lang} (untranslated)"))
            # English column holding Cyrillic (or vice versa) => likely column shift
            if code == "en" and val and _mostly_cyrillic(val):
                warnings.append(Warning(u.row, u.key, "possible column shift (Cyrillic in en)"))
            if source_lang == "ru" and code != "ru" and val and _mostly_cyrillic(val) and code != "ru":
                warnings.append(Warning(u.row, u.key, f"possible column shift (Cyrillic in {code})"))
    return warnings


def format_report(sheet: str, taken: int, skipped: int, warnings: list[Warning]) -> str:
    lines = [f"[{sheet}] взято {taken}, пропущено {skipped}, предупреждений {len(warnings)}"]
    lines += [f"  {sheet}!{w.row} {w.key}: {w.message}" for w in warnings]
    return "\n".join(lines)
```

**Step 4: Run — PASS.** **Step 5: Commit** `feat(loc-ingest): validation warnings and report`.

---

## Task 7: Escape-hatch хинт (`hints.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/hints.py`; Test `tests/test_hints.py`.

**Step 1: Тест** — хинт рядом с китом переопределяет `source_lang`/`key_column`:

```python
import json

from loc_kit_ingest.hints import load_hint


def test_load_hint_json(tmp_path):
    (tmp_path / "UI.hint.json").write_text(json.dumps({"source_lang": "en"}), encoding="utf-8")
    assert load_hint(tmp_path / "UI.xlsx") == {"source_lang": "en"}


def test_missing_hint(tmp_path):
    assert load_hint(tmp_path / "UI.xlsx") == {}
```

**Step 2: Run — FAIL. Step 3:**

```python
from __future__ import annotations

import json
from pathlib import Path


def load_hint(kit_path: str | Path) -> dict:
    """Load an optional sibling hint file `<stem>.hint.json` (escape hatch)."""
    p = Path(kit_path)
    hint = p.with_suffix(".hint.json")
    if hint.exists():
        return json.loads(hint.read_text(encoding="utf-8"))
    return {}
```

**Step 4: PASS. Step 5: Commit** `feat(loc-ingest): optional per-kit hint file`.

---

## Task 8: Запись PO (`po_writer.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/po_writer.py`; Test `tests/test_po_writer.py`.

**Step 1: Тесты** — PO читается обратно; шаблон несёт метаданные:

```python
from pathlib import Path

from loc_kit_ingest.model import Unit
from loc_kit_ingest.po_writer import write_component


def test_write_component_roundtrip(tmp_path):
    units = [
        Unit("vibration", {"en": "Vibration", "ru": "Вибрация"},
             comments=["Character: Joe"], references=["142945292288"], row=3),
        Unit("loading", {"en": "Loading", "ru": ""}, row=4),
    ]
    out = tmp_path / "UI"
    write_component(units, source_lang="en", langs=["en", "ru"], outdir=out)

    from translate.storage.pypo import pofile

    template = pofile.parsefile(str(out / "en.po"))
    by_id = {u.getid(): u for u in template.units if u.getid()}
    assert by_id["vibration"].target == "Vibration"          # msgstr = source in template
    assert "142945292288" in by_id["vibration"].getlocations()
    assert any("Joe" in n for n in [by_id["vibration"].getnotes("developer")])

    ru = pofile.parsefile(str(out / "ru.po"))
    ru_by_id = {u.getid(): u for u in ru.units if u.getid()}
    assert ru_by_id["vibration"].target == "Вибрация"
    assert ru_by_id["loading"].target == ""                  # untranslated
```

**Step 2: Run — FAIL. Step 3:**

```python
from __future__ import annotations

from pathlib import Path

from translate.storage.pypo import pofile

from .model import Unit


def _build(units: list[Unit], lang: str, *, is_template: bool) -> pofile:
    store = pofile()
    store.settargetlanguage(lang)
    for u in units:
        unit = store.addsourceunit(u.key)     # msgid = key
        unit.target = u.values.get(lang, "")  # msgstr = string for this language
        if is_template:
            for note in u.comments:
                unit.addnote(note, origin="developer")   # "#." comment
            for ref in u.references:
                unit.addlocation(ref)                    # "#:" location
    return store


def write_component(units: list[Unit], *, source_lang: str, langs, outdir) -> list[Path]:
    """Write one monolingual PO per language; the source-language file is the template."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for lang in langs:
        store = _build(units, lang, is_template=(lang == source_lang))
        path = out / f"{lang}.po"
        store.savefile(str(path))
        written.append(path)
    return written
```

**Step 4: Run — PASS** (если API translate-toolkit отличается — `getnotes`/`addnote` origin, `getlocations` — правь до зелёного; контракт фиксирует тест).
**Step 5: Commit** `feat(loc-ingest): write monolingual PO per language`.

---

## Task 9: CLI-оркестрация (`cli.py`, `__main__.py`)

**Files:** Create `loc_kit_ingest/loc_kit_ingest/cli.py`, `loc_kit_ingest/loc_kit_ingest/__main__.py`; Test `tests/test_cli.py`.

**Step 1: Тест** — сквозной прогон на xlsx-фикстуре пишет PO и отчёт:

```python
from loc_kit_ingest.cli import run


def test_cli_run_writes_components(ui_xlsx, tmp_path, capsys):
    out = tmp_path / "out"
    code = run([str(ui_xlsx), "-o", str(out)])
    assert code == 0
    assert (out / "Лист 1" / "en.po").exists()
    assert (out / "Лист 1" / "ru.po").exists()
    assert "взято" in capsys.readouterr().out          # report printed
```

**Step 2: Run — FAIL. Step 3:**

```python
# cli.py
from __future__ import annotations

import argparse
from pathlib import Path

from .hints import load_hint
from .layout import detect_layout
from .normalize import build_units
from .po_writer import write_component
from .reader import read_sheets
from .validate import format_report, validate


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loc-ingest", description="Ingest a loc kit into per-language PO.")
    parser.add_argument("kit", help="path to .xlsx/.csv/.tsv loc kit")
    parser.add_argument("-o", "--out", required=True, help="output directory")
    parser.add_argument("--zip", action="store_true", help="also write one ZIP per component")
    parser.add_argument("--source-lang", default=None, help="override source language")
    args = parser.parse_args(argv)

    hint = load_hint(args.kit)
    sheets = read_sheets(args.kit)
    out_root = Path(args.out)
    reports: list[str] = []
    for sheet, rows in sheets.items():
        try:
            layout = detect_layout(rows, source_lang=args.source_lang, hint=hint)
        except ValueError as exc:
            reports.append(f"[{sheet}] ПРОПУЩЕН: {exc}")
            continue
        units = build_units(rows, layout)
        langs = list(dict.fromkeys([layout.source_lang, *layout.lang_cols.values()]))
        comp_dir = out_root / sheet
        write_component(units, source_lang=layout.source_lang, langs=langs, outdir=comp_dir)
        warnings = validate(units, source_lang=layout.source_lang)
        reports.append(format_report(sheet, taken=len(units), skipped=0, warnings=warnings))
        if args.zip:
            import shutil

            shutil.make_archive(str(comp_dir), "zip", root_dir=comp_dir)
    report = "\n".join(reports)
    print(report)
    (out_root / "report.txt").write_text(report + "\n", encoding="utf-8")
    return 0
```

```python
# __main__.py
import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run())
```

**Step 4: Run — PASS.** **Step 5: Commit** `feat(loc-ingest): CLI orchestration with report and --zip`.

---

## Task 10: Сквозной прогон на реальных китах

**Files:** none (ручная проверка + фикстуры уже есть).

**Step 1:** Скопировать реальные киты и прогнать полный файл (не урезанный):

```bash
cp "/Users/eli/Downloads/Heart Abyss_Localization - Temple.csv" /tmp/Temple.csv
cp "/Users/eli/Downloads/Heart Abyss_Localization - Terms.csv" /tmp/Terms.csv
cp "/Users/eli/Downloads/UI.xlsx" /tmp/UI.xlsx
cd loc_kit_ingest
uv run python -m loc_kit_ingest /tmp/Temple.csv -o /tmp/out
uv run python -m loc_kit_ingest /tmp/Terms.csv -o /tmp/out
uv run python -m loc_kit_ingest /tmp/UI.xlsx -o /tmp/out --zip
```

**Step 2: Проверить глазами:**
- `/tmp/out/<лист>/{ru,en,...}.po` существуют; `ru.po`/`en.po` — шаблон исходного языка.
- отчёт показал предупреждения на известных дефектах Temple (стр. 82, 204 — сдвиг; строки `en == ru`).
- в Temple `#.` содержит имя персонажа; в UI `#:` содержит числовой Id.
- разметка `[shake]`, `<color=#..>`, `{value:cond:..}` цела.

**Step 3: Прогнать весь тест-набор пакета**

Run: `cd loc_kit_ingest && uv run pytest`
Expected: все тесты зелёные.

**Step 4: Commit** (если правил код под реальные данные) `test(loc-ingest): cover real Heart Abyss kits`.

---

## Task 11: UI-сценарии онбординга в Weblate (ручной runbook + проверка)

**Files:** Modify `docs/specs/loc-kit-ingest.md` (детализировать раздел «UI-сценарии», если по ходу проверки что-то уточнилось).

Weblate работает на `localhost:3001` (логин `admin`/`admin`), проект **Heart Abyss** уже существует. Проверяем PO вживую — это финальное доказательство, что формат читается.

**Сценарий C1 — Онбординг кита (happy path):**
1. `uv run python -m loc_kit_ingest /tmp/UI.xlsx -o /tmp/out --zip` → `/tmp/out/Лист 1.zip`.
2. Weblate → проект Heart Abyss → **Добавить компонент** → вкладка **«Отправить файлы перевода»** → загрузить ZIP.
3. Поля: имя компонента = имя листа; **Маска файла** `*.po`; **Одноязычный базовый файл** = `en.po` (для UI) / `ru.po` (для Temple/Terms — исходный язык из отчёта); **Формат файла** = `PO-файл gettext (одноязычный)`; **Исходный язык** = как в отчёте.
4. Стадия **Discover** → подтвердить → создать.
5. **Проверка:** число юнитов = «взято N» из отчёта; контекст строки = ключ; для UI числовой Id виден в «расположении»; для Temple имя персонажа видно в комментарии; строки с игровой разметкой не падают на разборе.

**Сценарий C2 — Кит с предупреждениями:** отчёт показал `en == ru`/сдвиг → правишь в исходной таблице (Weblate ещё не источник правды) → перезапуск CLI → перезалив (компонент пересоздать, п. C5).

**Сценарий C3 — Кит без ключей (Terms):** после загрузки открыть несколько строк — контекст = сгенерённый слаг; убедиться, что слаги читаемые и уникальные (в отчёте нет дублей).

**Сценарий C4 — Двухключевой кит (UI):** переводчик видит семантический ключ (`vibration`) как контекст, числовой Id — в «расположении»; Id сохранён под будущий экспорт.

**Сценарий C5 — Пересев до go-live:** если раскладка распозналась неверно и перевод ещё не начинали — удалить компонент, перезапустить CLI, перезалить. После старта перевода Weblate = источник правды, пересев запрещён.

**Сценарий C6 — Пост-импорт:** добавить целевые языки; привязать `routed-llm` (machinery); включить `game-markup`-чек (`WEBLATE_ADD_CHECK`, см. `AGENTS.md`); при необходимости — аддон форматирования PO, чтобы первый коммит Weblate не переформатировал файл.

**Acceptance:** хотя бы один кит (UI) реально загружен в Heart Abyss, все проверки C1 зелёные, `game-markup` не даёт ложных срабатываний на перенесённой разметке.

---

## Task 12: Финальная проверка

**Step 1:** `cd loc_kit_ingest && uv run pytest` → всё зелёное.
**Step 2:** `git status` — трекается только `loc_kit_ingest/`, `docs/specs/loc-kit-ingest.md`, `docs/plans/2026-08-06-loc-kit-ingest.md`; временные `/tmp/out`, `/tmp/*.csv|xlsx` не в репозитории.
**Step 3:** прогнать линтер по новым файлам: `uv run prek run --files $(git ls-files loc_kit_ingest)` (если prek доступен).
**Step 4: Commit** остатков.

---

## Не входит в задачу

- Экспорт из Weblate в формат движка (метаданные сохраняем, чтобы не заблокировать).
- Обратная запись в таблицу; двусторонняя синхронизация.
- Google Sheets по API.
- Повторяемый/идемпотентный импорт (Weblate — источник правды, seed разовый).

См. `docs/specs/loc-kit-ingest.md`.
