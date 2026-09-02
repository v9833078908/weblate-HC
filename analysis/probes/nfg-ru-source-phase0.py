#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Phase 0 measurement for the need-for-greed source-language migration.

Read-only against https://l10n.herocraft.com. Covers the open items of
docs/operations/plans/2026-09-02-need-for-greed-source-language-to-russian.md
Phase 0:

1. full settings of all eight components
2. yesterday's keys: still absent live? change log since 2026-09-01
3. glossary explanations (source + per-language targets), language flags,
   homonym collisions under a ru source
4. per-component triage state: source flags/explanations, dismissed checks,
   comments, suggestions, labels, screenshots, per-language stats
5. project machinery overrides (site-wide routing has no read-only API and
   weblate shell needs explicit approval - recorded as inaccessible)
6. add-ons on the project's components

Usage:
    PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/nfg-ru-source-phase0.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://l10n.herocraft.com/api"
PROJECT = "need-for-greed"
COMPONENTS = [
    "buyers",
    "characterdialogue",
    "loot",
    "orders",
    "survey",
    "tutorial",
    "ui",
]
GLOSSARY = "glossary"
CHANGES_AFTER = "2026-09-01T00:00:00Z"
LOST_KEYS_PATH = "analysis/data/nfg-ru-source-2026-09-02/orders-lost-keys.json"

TOKEN = os.environ.get("PROD_WEBLATE_API_TOKEN", "").strip()
if not TOKEN:
    sys.exit("PROD_WEBLATE_API_TOKEN is required")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/data/nfg-ru-source-2026-09-02/phase0.json"

# Numeric Change.action codes (weblate/trans/actions.py): Resource updated (0),
# Source string added (13), String added (31), String updated in the
# repository (59), String removed (63), String added in the repository (71),
# Source string added in the repository (90). Filtered by number because the
# API returns action_name localized to the requesting user.
RELEVANT_ACTIONS = {0, 13, 31, 59, 63, 71, 90}

# A template explanation is one or more `[Section] key` segments chained with
# "; ". Anything left after cutting every such segment is a real human note
# (the plan's fact 2 - Ancient Tome's definition survives as the residue).
SEGMENT_RE = re.compile(r"\[[^\]]*\][^;\n]*")


def is_template_note(explanation: str) -> bool:
    residue = SEGMENT_RE.sub("", explanation)
    return not residue.strip(" ;\n")


def get(url: str) -> dict | list:
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage] - fixed https host above
        url, headers={"Authorization": f"Token {TOKEN}"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def paginate(url: str) -> list[dict]:
    rows: list[dict] = []
    while url:
        page = get(url)
        if isinstance(page, list):
            return page
        rows.extend(page.get("results", []))
        url = page.get("next") or ""
    return rows


def first(values: list | None) -> str:
    if not values:
        return ""
    return values[0] if isinstance(values[0], str) else str(values[0])


def units_url(scope: str, query: str = "") -> str:
    params = {"page_size": "1000"}
    if query:
        params["q"] = query
    base = f"{API}/units/" if scope == "units" else f"{API}/{scope}/units/"
    return f"{base}?{urllib.parse.urlencode(params)}"


def username_of(user_url: str | None) -> str:
    if not user_url:
        return ""
    return user_url.rstrip("/").rsplit("/", 1)[-1]


def main() -> None:
    result: dict = {"project": PROJECT, "captured_at": "2026-09-02"}

    # 0. Per-language project stats: total/translated baseline for 18 languages.
    lang_stats = paginate(f"{API}/projects/{PROJECT}/languages/?format=json")
    result["project_language_stats"] = {
        s["code"]: {
            "total": s["total"],
            "translated": s["translated"],
            "approved": s["approved"],
            "fuzzy": s["fuzzy"],
        }
        for s in lang_stats
    }
    languages = sorted(result["project_language_stats"])
    result["languages"] = languages
    print(f"languages: {len(languages)}")

    # 1. Full component settings.
    components = paginate(f"{API}/projects/{PROJECT}/components/?format=json")
    result["components"] = {c["slug"]: c for c in components}
    print(f"components: {sorted(c['slug'] for c in components)}")

    # 2a. Yesterday's keys: still absent from live units?
    lost_keys = json.loads((ROOT / LOST_KEYS_PATH).read_text(encoding="utf-8"))
    rows = paginate(units_url("units", f"project:{PROJECT} AND context:HelpInfo"))
    live_lost = [
        {
            "id": r["id"],
            "language": r.get("language_code"),
            "context": r.get("context"),
            "source": first(r.get("source"))[:80],
            "target": first(r.get("target"))[:80],
        }
        for r in rows
        if r.get("context") in lost_keys
    ]
    result["lost_keys_live"] = live_lost
    print(f"lost keys present live: {len(live_lost)} rows")

    # 2b. Change log since 2026-09-01.
    changes = paginate(
        f"{API}/projects/{PROJECT}/changes/?format=json&page_size=1000"
        f"&timestamp_after={urllib.parse.quote(CHANGES_AFTER)}"
    )
    relevant = [
        {
            "timestamp": c.get("timestamp"),
            "action": c.get("action"),
            "action_name": c.get("action_name"),
            "user": username_of(c.get("author") or c.get("user")),
            "component": (c.get("component") or "").rstrip("/").rsplit("/", 1)[-1],
            "translation": (c.get("translation") or "").rstrip("/").rsplit("/", 1)[-1],
            "details": {
                k: v
                for k, v in (c.get("details") or {}).items()
                if k in {"key", "context", "value", "source", "target", "name"}
            },
        }
        for c in changes
        if c.get("action") in RELEVANT_ACTIONS
    ]
    result["changes_since_2026_09_01"] = relevant
    print(f"changes since 2026-09-01: total={len(changes)} relevant={len(relevant)}")

    # 2c. Cyrillic-in-English sweep (expected: only the two ui homoglyphs).
    cyrillic_en = {}
    for component in COMPONENTS:
        rows = paginate(
            units_url(f"translations/{PROJECT}/{component}/en", 'target:r"[А-Яа-яЁё]"')
        )
        cyrillic_en[component] = [
            {"context": r.get("context"), "target": first(r.get("target"))}
            for r in rows
        ]
    result["cyrillic_in_en"] = cyrillic_en
    print("cyrillic in en:", {k: len(v) for k, v in cyrillic_en.items()})

    # 3. Glossary: source (en) units and each target language.
    source_units = paginate(units_url(f"translations/{PROJECT}/{GLOSSARY}/en"))
    notes_beyond_template = 0
    glossary_source = []
    for unit in source_units:
        explanation = unit.get("explanation", "")
        if explanation and not is_template_note(explanation):
            notes_beyond_template += 1
        glossary_source.append(
            {
                "id": unit["id"],
                "context": unit.get("context", ""),
                "source": first(unit.get("source")),
                "explanation": explanation,
                "extra_flags": unit.get("extra_flags", ""),
            }
        )
    result["glossary_source"] = glossary_source
    result["glossary_source_total"] = len(source_units)
    result["glossary_notes_beyond_template"] = notes_beyond_template
    print(
        f"glossary source: {len(source_units)} units, "
        f"notes beyond template: {notes_beyond_template}"
    )

    target_languages = [code for code in languages if code != "en"]
    glossary_targets: dict[str, list] = {}
    for lang in target_languages:
        rows = paginate(units_url(f"translations/{PROJECT}/{GLOSSARY}/{lang}"))
        glossary_targets[lang] = [
            {
                "id": r["id"],
                "context": r.get("context", ""),
                "target": first(r.get("target")),
                "explanation": r.get("explanation", ""),
                "extra_flags": r.get("extra_flags", ""),
                "state": r.get("state"),
            }
            for r in rows
        ]
    result["glossary_targets"] = glossary_targets
    result["glossary_target_explanations"] = [
        {
            "language": lang,
            "id": row["id"],
            "context": row["context"],
            "explanation": row["explanation"],
        }
        for lang, rows in glossary_targets.items()
        for row in rows
        if row["explanation"]
    ]
    result["glossary_language_flags"] = [
        {
            "language": lang,
            "id": row["id"],
            "context": row["context"],
            "extra_flags": row["extra_flags"],
        }
        for lang, rows in glossary_targets.items()
        for row in rows
        if row["extra_flags"]
    ]
    print(
        f"glossary target explanations: {len(result['glossary_target_explanations'])}, "
        f"language flags: {len(result['glossary_language_flags'])}"
    )

    # Homonyms under a ru source: context is compact JSON [section, en term];
    # the ru identity would be (section, ru term). Collision = same
    # (section, ru term) from different en terms.
    section_by_context = {}
    for unit in glossary_source:
        try:
            section = json.loads(unit["context"])[0]
        except (json.JSONDecodeError, TypeError, IndexError, KeyError):
            section = ""
        section_by_context[unit["context"]] = section
    en_by_context = {unit["context"]: unit["source"] for unit in glossary_source}
    groups: dict[tuple, list] = {}
    for row in glossary_targets.get("ru", []):
        ru_term = row["target"]
        if not ru_term:
            continue
        key = (section_by_context.get(row["context"], ""), ru_term)
        groups.setdefault(key, []).append(en_by_context.get(row["context"], ""))
    homonyms = [
        {"section": key[0], "ru_term": key[1], "en_terms": sorted(set(ens))}
        for key, ens in groups.items()
        if len(set(ens)) > 1
    ]
    result["glossary_homonyms_ru_source"] = homonyms
    result["glossary_terms_without_ru"] = [
        {"context": row["context"], "en": en_by_context.get(row["context"], "")}
        for row in glossary_targets.get("ru", [])
        if not row["target"]
    ]
    print(
        f"homonym collisions: {len(homonyms)}; "
        f"terms without ru: {len(result['glossary_terms_without_ru'])}"
    )

    # 4. Per-component triage.
    triage = {}
    for component in COMPONENTS:
        entry: dict = {}
        rows = paginate(units_url(f"translations/{PROJECT}/{component}/en"))
        entry["source_units_total"] = len(rows)
        entry["source_extra_flags"] = [
            {"context": r.get("context"), "extra_flags": r.get("extra_flags")}
            for r in rows
            if r.get("extra_flags")
        ]
        entry["source_explanations"] = [
            {"context": r.get("context"), "explanation": r.get("explanation")}
            for r in rows
            if r.get("explanation")
        ]
        scope = f"project:{PROJECT} AND component:={component}"
        for key, token in (
            ("dismissed_checks", "has:dismissed-check"),
            ("comments", "has:comment"),
            ("suggestions", "has:suggestion"),
            ("labels", "has:label"),
        ):
            rows = paginate(units_url("units", f"{scope} AND {token}"))
            entry[key] = [
                {
                    "id": r["id"],
                    "language": r.get("language_code"),
                    "context": r.get("context"),
                    "flags": r.get("flags", ""),
                    "labels": r.get("labels"),
                }
                for r in rows
            ]
        shots = paginate(
            f"{API}/components/{PROJECT}/{component}/screenshots/"
            "?format=json&page_size=1000"
        )
        entry["screenshots"] = len(shots)
        stats = paginate(
            f"{API}/components/{PROJECT}/{component}/statistics/"
            "?format=json&page_size=1000"
        )
        entry["language_stats"] = {
            s["code"]: {
                "total": s["total"],
                "translated": s["translated"],
                "approved": s["approved"],
                "fuzzy": s["fuzzy"],
                "failing": s["failing"],
                "readonly": s["readonly"],
            }
            for s in stats
        }
        triage[component] = entry
        print(
            f"{component}: src={entry['source_units_total']} "
            f"flags={len(entry['source_extra_flags'])} "
            f"expl={len(entry['source_explanations'])} "
            f"dismissed={len(entry['dismissed_checks'])} "
            f"comments={len(entry['comments'])} sugg={len(entry['suggestions'])} "
            f"labels={len(entry['labels'])} shots={entry['screenshots']}"
        )
    result["triage"] = triage

    # 5. Machinery: project overrides only (what the API exposes).
    try:
        machinery = get(f"{API}/projects/{PROJECT}/machinery_settings/?format=json")
        assert isinstance(machinery, dict)
        redacted = {}
        for service, conf in machinery.items():
            if isinstance(conf, dict):
                conf = {
                    key: ("<redacted>" if "key" in key.lower() else value)
                    for key, value in conf.items()
                }
            redacted[service] = conf
        result["machinery_settings_project"] = redacted
        print(f"machinery (project overrides): {sorted(machinery)}")
    except urllib.error.HTTPError as error:
        result["machinery_settings_project"] = {
            "error": f"HTTP {error.code} - endpoint requires project.edit"
        }
        print(f"machinery settings: HTTP {error.code}")
    result["machinery_global_routing"] = {
        "status": "INACCESSIBLE_VIA_API",
        "note": (
            "Site-wide machinery settings (routing, language_instructions) are "
            "admin-DB Configuration objects; no read-only API exposes them and "
            "weblate shell on production requires explicit owner approval "
            "(AGENTS.md). Must be confirmed before Phase 5: resolve_model('en') "
            "has to return a model (an en entry or the '*' fallback), "
            "otherwise autotranslate fails with "
            "'No routed model for target language: en'."
        ),
    }

    # 6. Add-ons: global read-only list, keep this project's scope.
    addons = paginate(f"{API}/addons/?format=json&page_size=1000")
    scoped = [
        a
        for a in addons
        if PROJECT in (a.get("component") or "")
        or (a.get("project") or "").endswith(f"/{PROJECT}/")
    ]
    result["addons"] = scoped
    print(f"addons on project scope: {len(scoped)}")

    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
