# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Propose a per-cell reconciliation of Pirate Ships game files against a
# Weblate export. Read-only: compares two directories of
# Localization_<code>.json and writes CSV decision lists.
# Usage: python3 analysis/probes/pirate-ships-reconcile.py with the beta
# directory, the Weblate directory, the output directory and, optionally, a
# JSON file mapping "<code>:<weblate key>" to its Weblate "last_updated".
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

CODES = [
    "ru",
    "en",
    "es",
    "fr",
    "pt",
    "pl",
    "de",
    "it",
    "th",
    "id",
    "cn",
    "kr",
    "jp",
    "tr",
    "vi",
    "nl",
]
NEWER_THAN = "2026-09-24"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def load(folder: Path) -> dict[str, dict[str, str]]:
    return {
        code: json.loads((folder / f"Localization_{code}.json").read_text("utf-8"))
        for code in CODES
    }


def ellipsis(text: str) -> str:
    return text.replace("...", "…")


def cleanup(text: str) -> str:
    text = ellipsis(text).replace("\u200b", "")
    text = re.sub(r"[  ]", " ", text)
    text = re.sub(r" +([?!:;»])", r"\1", text)
    text = re.sub(r"(«) +", r"\1", text)
    text = re.sub(r"[ \t]+(\n|$)", r"\1", text)
    text = re.sub(r"\s*\$\s*", "$", text)
    text = re.sub(r" {2,}", " ", text)
    return text.rstrip(".。").strip()


def key_pairs(beta_ru: dict[str, str], wl_ru: dict[str, str]):
    renames = {
        key: "dialog_" + key[9:]
        for key in beta_ru
        if key not in wl_ru
        and key.startswith("dialogue_")
        and "dialog_" + key[9:] in wl_ru
    }
    new = [key for key in beta_ru if key not in wl_ru and key not in renames]
    removed = [
        key for key in wl_ru if key not in beta_ru and key not in renames.values()
    ]
    for old in list(removed):
        match = [
            key for key in new if beta_ru[key].strip() and beta_ru[key] == wl_ru[old]
        ]
        if len(match) == 1:
            renames[match[0]] = old
            new.remove(match[0])
            removed.remove(old)
    return renames, new, removed


def ru_rule(beta: str, wl: str) -> str:
    if beta == wl:
        return "same"
    if ellipsis(beta) == ellipsis(wl):
        return "ru-ellipsis"
    if CYRILLIC.search(beta) and not CYRILLIC.search(wl):
        return "ru-english-in-weblate"
    return "ru-team-edit"


def cell_rule(beta: str, wl: str, updated: str) -> tuple[str, str]:
    if not beta.strip() and wl.strip():
        return "weblate", "filled-in-weblate"
    if beta.strip() and not wl.strip():
        return "beta", "filled-in-beta"
    if cleanup(beta) == cleanup(wl):
        return "weblate", "cleanup"
    if updated >= NEWER_THAN:
        return "weblate", "newer-in-weblate"
    return "beta", "review"


def main() -> None:
    beta_dir, wl_dir, out_dir = (Path(arg) for arg in sys.argv[1:4])
    units = (
        json.loads(Path(sys.argv[4]).read_text("utf-8")) if len(sys.argv) > 4 else {}
    )
    beta, wl = load(beta_dir), load(wl_dir)
    renames, new, removed = key_pairs(beta["ru"], wl["ru"])
    pairs = [(key, key, "common") for key in beta["ru"] if key in wl["ru"]]
    pairs += [(key, old, "rename") for key, old in renames.items()]

    rows = []
    for beta_key, wl_key, kind in pairs:
        ru = ru_rule(beta["ru"][beta_key], wl["ru"][wl_key])
        for code in CODES:
            b, w = beta[code].get(beta_key, ""), wl[code].get(wl_key, "")
            if b == w:
                continue
            updated = (units.get(f"{code}:{wl_key}") or {}).get("last_updated") or ""
            if code == "ru":
                take = "weblate" if ru == "ru-ellipsis" else "beta"
                rule = ru
            elif ru in {"ru-english-in-weblate", "ru-team-edit"}:
                take, rule = "beta", "follows-ru"
            else:
                take, rule = cell_rule(b, w, updated)
            rows.append([kind, code, beta_key, wl_key, b, w, updated[:16], take, rule])

    out_dir.mkdir(parents=True, exist_ok=True)
    header = [
        "kind",
        "lang",
        "beta_key",
        "weblate_key",
        "beta_value",
        "weblate_value",
        "weblate_last_updated",
        "take",
        "rule",
    ]
    with (out_dir / "cells.csv").open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([header, *rows])
    with (out_dir / "keys.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["action", "beta_key", "weblate_key", "ru", "translated_langs"])
        for key, old in renames.items():
            writer.writerow(["rename", key, old, beta["ru"][key], ""])
        for key in new:
            done = sum(1 for code in CODES[1:] if beta[code].get(key, "").strip())
            writer.writerow(["add", key, "", beta["ru"][key], done])
        for key in removed:
            writer.writerow(["delete", "", key, wl["ru"][key], ""])

    summary = {
        "renames": len(renames),
        "add": len(new),
        "add_untranslated": sum(
            1
            for key in new
            if not any(beta[code].get(key, "").strip() for code in CODES[1:])
        ),
        "delete": len(removed),
        "cells": len(rows),
        "by_rule": dict(Counter(f"{row[0]}/{row[8]}" for row in rows).most_common()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
