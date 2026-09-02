# Phase 2 preparation for need-for-greed source-language en -> ru.
#
# Reads kit-original/ (fresh ZIP from Phase 1) and phase0.json (DB truth),
# writes kit-ru-source/<component>.zip for 7 PO components and
# kit-ru-source/glossary.zip. Works through translate-storage (po/tbx),
# never textual replacement, except the two ui homoglyph msgstr fixes which
# are exact string substitutions inside known msgstr values.
#
# Plan: docs/operations/plans/2026-09-02-need-for-greed-source-language-to-russian.md
# Phase 2 items 1-5.

import json
import shutil
import zipfile
from pathlib import Path

from translate.storage.pypo import pofile
from translate.storage.tbx import tbxfile

BASE = Path("analysis/data/nfg-ru-source-2026-09-02")
SRC = BASE / "kit-original/need-for-greed"
KIT = BASE / "kit-work/need-for-greed"
OUT = BASE / "kit-ru-source"

LANGS = [
    "bg", "cs", "de", "es", "fil", "fr", "hu", "id", "it",
    "lt", "lv", "nl", "pl", "pt", "ro", "ru", "tr",
]

# homonym groups (phase0.json): under ru source, identity is
# (section, ru term) and both pair members share the ru term, so separate
# them by the old en term as section (mirrors the Space Arena precedent,
# where section = the member-specific ru term under en source).
HOMONYM_SECTIONS = {
    "Belomar": "Belomar",
    "Whitelands": "Whitelands",
    "Buried Chest": "Buried Chest",
    "Chest": "Chest",
    "Chest of the Jarl": "Chest of the Jarl",
    "Jarl Chest": "Jarl Chest",
    "Meteorfall": "Meteorfall",
    "Starfall": "Starfall",
}

report: dict = {"po": {}, "glossary": {}, "conflicts": []}

def parse_po(path: Path) -> pofile:
    with path.open("rb") as handle:
        return pofile(handle)


def find_unit(store: pofile, key: str):
    for unit in store.units:
        if unit.isheader():
            continue
        if unit.getid() == key:
            return unit
    return None


def add_after(store: pofile, anchor_key: str | None, key: str, value: str) -> None:
    """Add a unit with proper escaping, positioned next to its siblings."""
    unit = store.addsourceunit(key)
    unit.target = value
    if anchor_key is None:
        return
    store.units.remove(unit)
    for i, u in enumerate(store.units):
        if not u.isheader() and u.getid() == anchor_key:
            store.units.insert(i + 1, unit)
            return
    msg = f"anchor {anchor_key!r} vanished"
    raise RuntimeError(msg)


# ---------------------------------------------------------------- Phase 2.1
def prepare_orders() -> None:
    lost = json.loads((BASE / "orders-lost-keys.json").read_text(encoding="utf-8"))
    stats = {"added_with_text": 0, "added_empty": 0, "already_present": []}
    for lang in ["en", *LANGS]:
        path = KIT / "orders" / f"{lang}.po"
        store = parse_po(path)
        target_text = lang == "ru"
        for key, info in lost.items():
            if find_unit(store, key) is not None:
                stats["already_present"].append(f"{lang}:{key}")
                continue
            # anchor: sibling key with same prefix (Title/Description/Thank)
            prefix = key[: -len("HelpInfo")]
            anchor = None
            for suffix in ("Title", "Description", "Thank"):
                cand = prefix + suffix
                if find_unit(store, cand) is not None:
                    anchor = cand
                    break
            value = info["text"] if target_text else ""
            add_after(store, anchor, key, value)
            stats["added_with_text" if target_text else "added_empty"] += 1
        path.write_bytes(bytes(store))
    report["po"]["orders"] = {
        "keys": len(lost),
        "ru_text_added": stats["added_with_text"],
        "empty_added_per_other_lang": stats["added_empty"],
        "already_present_in_fresh_kit": stats["already_present"],
    }
    report["conflicts"] += [
        f"orders {c}: already present in fresh kit, not overwritten"
        for c in stats["already_present"]
    ]


# ---------------------------------------------------------------- Phase 2.2
def fix_ui_homoglyphs() -> None:
    path = KIT / "ui" / "en.po"
    store = parse_po(path)
    fixed = []
    for key, old, new in [
        ("dailyRewardClaimDescription", "\u04252", "X2"),  # Х2 -> X2
        ("terrainProgressInfoSlide2", "ADOR\u0415", "ADORE"),  # Е -> E
    ]:
        unit = find_unit(store, key)
        assert unit is not None, key
        assert old in unit.target, f"{key}: {old!r} not in target"
        unit.target = unit.target.replace(old, new)
        fixed.append(key)
    path.write_bytes(bytes(store))
    report["po"]["ui"] = {"homoglyph_fixed_in_en": fixed}


# ---------------------------------------------------------------- Phase 2.4
def prepare_glossary() -> None:
    p0 = json.loads((BASE / "phase0.json").read_text(encoding="utf-8"))
    source_terms = p0["glossary_source"]
    targets = p0["glossary_targets"]  # DB dump: 17 langs, ru-source era (no en)

    # New ru-source kit needs one TBX per target language: the 16 non-ru
    # dumped targets plus en. The en translation of a term is the old DB
    # source term itself. ru is now the source: no ru.tbx.
    langs_out = ["en", *(l for l in sorted(targets) if l != "ru")]
    assert len(langs_out) == 17, langs_out

    expected_descrips = sum(1 for t in source_terms if t["explanation"])

    written = []
    ru_by_ctx = {r["context"]: r["target"] for r in targets["ru"]}
    for lang in langs_out:
        store = tbxfile(sourcelanguage="ru", targetlanguage=lang)
        rows = {r["context"]: r for r in targets.get(lang, [])}
        for term in source_terms:
            section, en_term = json.loads(term["context"])
            ru_term = ru_by_ctx[term["context"]]
            assert ru_term, f"missing ru target for {en_term!r}"
            if en_term in HOMONYM_SECTIONS:
                section = HOMONYM_SECTIONS[en_term]
            ctx = json.dumps([section, ru_term], ensure_ascii=False)
            unit = store.addsourceunit(ru_term)
            unit.setid(ctx)
            unit.target = (
                en_term if lang == "en" else rows[term["context"]]["target"]
            )
            if term["explanation"]:
                unit.addnote(term["explanation"], origin="definition")
            target_expl = (
                "" if lang == "en" else rows[term["context"]].get("explanation") or ""
            )
            if target_expl:
                unit.addnote(target_expl, origin="translator")
            unit.xmlelement.set("weblate-flags", term["extra_flags"] or "terminology")
        path = KIT / "glossary" / "tbx" / f"{lang}.tbx"
        path.write_bytes(bytes(store))
        written.append(lang)
    # ru.tbx is no longer part of a ru-source kit
    (KIT / "glossary" / "tbx" / "ru.tbx").unlink()

    # parse-back validation with the same stack
    counts = {}
    for lang in langs_out:
        path = KIT / "glossary" / "tbx" / f"{lang}.tbx"
        with path.open("rb") as handle:
            parsed = tbxfile(handle)
        units = [u for u in parsed.units if u.source or u.target]
        descrips = sum(1 for u in units if u.getnotes("definition").strip())
        translator_notes = sum(1 for u in units if u.getnotes("translator").strip())
        ids = [u.getid() for u in units]
        assert len(ids) == len(set(ids)), f"{lang}: duplicate contexts"
        counts[lang] = {
            "terms": len(units),
            "descrips": descrips,
            "translator_notes": translator_notes,
        }
    assert all(c["terms"] == len(source_terms) for c in counts.values())
    assert all(c["descrips"] == expected_descrips for c in counts.values())

    db_target_notes = {
        "en": 0,
        **{
            lang: sum(1 for r in rows if r["explanation"])
            for lang, rows in targets.items()
            if lang != "ru"
        },
    }
    for lang, c in counts.items():
        assert c["translator_notes"] == db_target_notes.get(lang, 0), lang

    report["glossary"] = {
        "files_written": written,
        "terms_per_file": len(source_terms),
        "descrips_per_file": expected_descrips,
        "homonym_groups_separated": 4,
        "translator_notes_per_lang": db_target_notes,
        "parse_back": "ok",
    }


# ---------------------------------------------------------------- Phase 2.5
def make_zips() -> None:
    OUT.mkdir(exist_ok=True)
    for comp in ["buyers", "characterdialogue", "loot", "orders", "survey", "tutorial", "ui"]:
        zpath = OUT / f"{comp}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for lang in ["en", *LANGS]:
                f = KIT / comp / f"{lang}.po"
                z.write(f, f"{lang}.po")
    zpath = OUT / "glossary.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for lang in sorted((KIT / "glossary" / "tbx").glob("*.tbx")):
            z.write(lang, f"tbx/{lang.name}")
    report["zips"] = sorted(p.name for p in OUT.glob("*.zip"))


def verify_untouched() -> None:
    """All po files must still parse and keep unit counts (except orders +9)."""
    expected = {
        "buyers": 10, "characterdialogue": 25, "loot": 154, "orders": 102,
        "survey": 34, "tutorial": 102, "ui": 466,
    }
    for comp, n in expected.items():
        for lang in ["en", *LANGS]:
            store = parse_po(KIT / comp / f"{lang}.po")
            units = [u for u in store.units if not u.isheader()]
            want = n + (9 if comp == "orders" else 0)
            assert len(units) == want, (comp, lang, len(units), want)
    report["po"]["unit_counts_verified"] = True


def main() -> None:
    if KIT.exists():
        shutil.rmtree(KIT)
    shutil.copytree(SRC, KIT)
    prepare_orders()
    fix_ui_homoglyphs()
    prepare_glossary()
    make_zips()
    verify_untouched()
    (BASE / "phase2-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
