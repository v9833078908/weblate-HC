# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Convert the Spaceship Battles glossary sheet into a flat glossary CSV.

The output follows the Weblate "Use as glossary" upload path.

The kit keeps the meaning of a term in an unlabeled first column as
``%ENGINE_KEY% - prose`` and groups terms with caption rows. This script turns
that into one row per term with Weblate language codes and a single ``note``
column, following the shape of the COL4 and Heart Abyss glossaries. Notes are
assembled deterministically from the kit itself: the sheet's own prose, a fixed
per-section rule, the engine key and the section name. Nothing is invented and
no network service is involved.

Plan: docs/plans/2026-08-18-spaceship-battles-glossary.md
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

SOURCE = (
    Path.home() / "Downloads" / "Spaceship Battles - Localization - Glossary (1).csv"
)
TARGET = Path.home() / "Downloads" / "SpaceshipBattles-glossary.csv"

# Kit header -> header written to the output file. ``ru`` comes first because
# the leftmost language column becomes the glossary source language. Indonesian
# is written as ``Indonesian(id)`` because a column headed exactly ``id`` is
# treated as a technical identifier and dropped (loc_kit_ingest/infer.py).
COLUMNS: tuple[tuple[str, str], ...] = (
    ("ru", "ru"),
    ("en", "en"),
    ("de", "de"),
    ("fr", "fr"),
    ("es", "es"),
    ("pt-br", "pt_BR"),
    ("ch-s", "zh_Hans"),
    ("jp", "ja"),
    ("kr", "ko"),
    ("fa", "fa"),
    ("tr", "tr"),
    ("hi", "hi"),
    ("id", "Indonesian(id)"),
    ("th", "th"),
    ("vi", "vi"),
)

# The Indonesian column header carries a language name to survive inference;
# prose inside a note names the plain code.
NOTE_LABELS = {"Indonesian(id)": "id"}

# One fixed rule per kit section. The rule states where the term has to stay
# identical, which is the whole point of a glossary entry.
SECTION_RULES: dict[str, str] = {
    "Корабли": (
        "Базовое понятие про корабли: один и тот же перевод в ангаре, бою и магазине."
    ),
    "Модули": (
        "Термин модульной системы: один и тот же перевод в конструкторе, "
        "магазине и описаниях модулей."
    ),
    "Характеристики модулей": (
        "Характеристика модуля: короткая подпись в UI; тот же перевод в "
        "описании модуля и в сравнении характеристик."
    ),
    "Улучшения": ("Термин системы улучшений: тот же перевод во всех экранах прокачки."),
    "Классы кораблей (полные и сокращенные)": (
        "Класс корабля: полная и сокращённая форма должны быть согласованы "
        "между собой и с фильтрами ангара."
    ),
    "Общие игровые понятия": "Общий игровой термин: один перевод на весь интерфейс.",
    "Режим конструктора": (
        "Термин режима конструктора: тот же перевод в подсказках конструктора."
    ),
    "Игровые режимы": (
        "Название игрового режима: держать единообразно в меню, описаниях "
        "режимов и наградах."
    ),
    "Игровые лиги": (
        "Название лиги: имя собственное, держать единообразно в таблицах лиг, "
        "наградах и уведомлениях."
    ),
    "Кланы": (
        "Термин кланового раздела: тот же перевод в клановых экранах, войне "
        "кланов и уведомлениях."
    ),
    "Лутбоксы": (
        "Название типа галактики: имя собственное, держать единообразно на "
        "карте и в наградах."
    ),
    "Ресурсы": (
        "Название ресурса или валюты: не переводить описательно, держать "
        "единообразно в магазине, наградах и счётчиках."
    ),
    "Пилоты": (
        "Раздел пилотов: имя пилота транслитерировать, прозвище переводить по "
        "смыслу; звания и фракции держать единообразно."
    ),
    "Способности": (
        "Название способности пилота: тот же перевод в карточке пилота и в "
        "описании способности."
    ),
}

# What a term is when the kit gives only its engine key. Derived from the key
# prefix, which is the kit's own classification, not a guess about mechanics.
KEY_SENSES: dict[str, tuple[tuple[str, str], ...]] = {
    "Модули": (
        ("CATEGORY_", "Подкатегория модулей"),
        ("MODULE_KIND_", "Вид модуля"),
        ("", "Название модуля"),
    ),
    "Игровые лиги": (
        ("SUBLEAGUE", "Название ранга внутри лиги"),
        ("CLANWARLEAGUE", "Название лиги войны кланов"),
        ("LEAGUE_", "Название игровой лиги"),
        ("", "Термин раздела лиг"),
    ),
    "Пилоты": (
        ("PILOT_RANK_", "Звание пилота"),
        ("FACTION_", "Название фракции пилотов"),
        ("", "Имя пилота"),
    ),
    "Общие игровые понятия": (
        ("PARAMETER_TIER_", "Название уровня редкости"),
        ("", "Общий игровой термин"),
    ),
    "Классы кораблей (полные и сокращенные)": (("", "Класс корабля"),),
    "Лутбоксы": (("", "Название типа галактики-лутбокса"),),
    "Кланы": (("", "Термин кланового раздела"),),
    "Способности": (("", "Название способности пилота"),),
    "Корабли": (("", "Понятие раздела «Корабли»"),),
    "Характеристики модулей": (("", "Характеристика модуля"),),
    "Улучшения": (("", "Термин системы улучшений"),),
    "Режим конструктора": (("", "Термин режима конструктора"),),
    "Ресурсы": (("", "Название ресурса"),),
}

_KEY = re.compile(r"^\s*%([^%\s]+)%\s*")
_JUNK = frozenset({"#VALUE!"})


def clean(cell: str) -> str:
    """Drop the kit's spreadsheet junk and characters TBX cannot carry."""
    text = cell.replace("\ufeff", "").strip()
    return "" if text in _JUNK else text


def split_description(text: str) -> tuple[list[str], str]:
    """Return the engine keys of a description cell and its prose remainder."""
    rest = text.replace("\n", " ").strip()
    keys: list[str] = []
    while match := _KEY.match(rest):
        keys.append(match.group(1))
        rest = rest[match.end() :]
    rest = re.sub(r"^[-–—:]\s*", "", rest).strip()
    return keys, rest


def sense_text(section: str, keys: list[str], prose: str) -> str:
    """One sentence saying what the term is."""
    if prose:
        return prose[0].upper() + prose[1:].rstrip(" .")
    key = keys[0] if keys else ""
    for prefix, sense in KEY_SENSES.get(section, ()):
        if key.startswith(prefix):
            return sense
    return f"Термин раздела «{section}»"


class Term:
    """One glossary row: the kit's terms per language plus its own metadata."""

    def __init__(
        self, section: str, keys: list[str], prose: str, values: dict[str, str]
    ):
        self.section = section
        self.keys = keys
        self.values = values
        self.sense = sense_text(section, keys, prose)
        self.homonyms: list[Term] = []

    @property
    def source(self) -> str:
        return self.values["ru"]

    def key_text(self) -> str:
        if not self.keys:
            return ""
        label = "Ключи движка" if len(self.keys) > 1 else "Ключ движка"
        keys = ", ".join(f"%{key}%" for key in self.keys)
        return f"{label}: {keys}."

    def divergences(self, other: Term) -> str:
        """Target values of ``other`` that contradict this row."""
        parts = [
            f"{NOTE_LABELS.get(code, code)}={other.values[code]}"
            for _kit, code in COLUMNS
            if code not in {"ru", "en"}
            and other.values[code]
            and other.values[code] != self.values[code]
        ]
        return ", ".join(parts)

    def note(self) -> str:
        rule = SECTION_RULES[self.section]
        # A key-only sense such as "Название способности пилота" is the opening
        # of its own section rule; printing both repeats the sentence.
        sense = "" if rule.startswith(self.sense) else f"{self.sense}."
        parts = [sense, rule, self.key_text()]
        if self.homonyms:
            senses = []
            for index, other in enumerate(self.homonyms, start=1):
                keys = ", ".join(f"%{key}%" for key in other.keys)
                diverging = self.divergences(other)
                tail = diverging or "переводы совпадают"
                senses.append(f"({index}) {other.sense.lower()} [{keys}]: {tail}")
            parts.append(
                "Тот же исходный термин в ките значит также: "
                + "; ".join(senses)
                + " - расхождение кита, свести к канону."
            )
        parts.append(f"Раздел: {self.section}.")
        return " ".join(part for part in parts if part)


def read_terms(path: Path) -> tuple[list[Term], list[str]]:
    """Parse the kit into terms, collecting a report of everything dropped."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header = [cell.strip() for cell in rows[0]]
    columns = {kit: header.index(kit) for kit, _code in COLUMNS}

    report: list[str] = []
    terms: list[Term] = []
    section = ""
    for number, row in enumerate(rows[1:], start=2):
        cells = [clean(cell) for cell in row]
        description = cells[0] if cells else ""
        values = {
            code: cells[columns[kit]] if len(cells) > columns[kit] else ""
            for kit, code in COLUMNS
        }
        if not description and not any(values.values()):
            continue
        if description and "%" not in description and not any(values.values()):
            section = description
            continue
        if not values["ru"]:
            report.append(f"row {number}: no ru term; skipped")
            continue
        if not section:
            msg = f"row {number}: term before any section caption"
            raise SystemExit(msg)
        keys, prose = split_description(description)
        if not keys:
            report.append(f"row {number}: no engine key in the description column")
        terms.append(Term(section, keys, prose, values))
    return terms, report


def merge_homonyms(terms: list[Term]) -> tuple[list[Term], list[str]]:
    """Keep the first sense of a repeated source term, fold the rest into it."""
    kept: dict[str, Term] = {}
    order: list[Term] = []
    report: list[str] = []
    for term in terms:
        first = kept.get(term.source)
        if first is None:
            kept[term.source] = term
            order.append(term)
            continue
        first.homonyms.append(term)
        report.append(
            f"{term.source!r}: sense {', '.join(term.keys)} folded into "
            f"{', '.join(first.keys)}"
        )
    return order, report


def write_glossary(terms: list[Term], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([code for _kit, code in COLUMNS] + ["note"])
        for term in terms:
            writer.writerow(
                [term.values[code] for _kit, code in COLUMNS] + [term.note()]
            )


def main() -> None:
    terms, report = read_terms(SOURCE)
    merged, merge_report = merge_homonyms(terms)
    write_glossary(merged, TARGET)
    sections: dict[str, int] = {}
    for term in merged:
        sections[term.section] = sections.get(term.section, 0) + 1
    print(f"{TARGET}: {len(merged)} terms from {len(terms)} kit rows")
    for section, count in sections.items():
        print(f"  {count:4d}  {section}")
    for line in report + merge_report:
        print(f"  note: {line}", file=sys.stderr)


if __name__ == "__main__":
    main()
