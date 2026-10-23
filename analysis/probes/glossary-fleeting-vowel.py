#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Read-only measurement of Russian glossary terms the source-side matcher misses.

Snowball strips endings only, so a noun whose fleeting vowel drops outside the
nominative ("Стрелок" / "Стрелков") never matches its own glossary entry. The
probe counts, over every Russian-source production project, the term
occurrences found today, the ones the product's fleeting-vowel alternates
(`weblate.checks.morphology.stem_alternatives`) add, and - when pymorphy3 is
installed - the ones a paradigm expansion of the glossary side would add:

    uv run --with pymorphy3 python analysis/probes/glossary-fleeting-vowel.py

Word matching mirrors `weblate/glossary/models.py:get_glossary_stem_automaton`
(whole words, lowercased, acronyms excluded); term flags are not read, so an
`exact` term is counted like any other. Writes only the summary JSON.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from itertools import product

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weblate.checks.morphology import (
    WORD_RE,
    get_stemmer,
    is_acronym,
    stem_alternatives,
    stem_word,
)

BASE = "https://l10n.herocraft.com/api/"
OUT = ROOT / "analysis/data/glossary-fleeting-vowel-2026-10-21/summary.json"
TIMEOUT = 180
TOP = 25


def token() -> str:
    for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if line.startswith("PROD_WEBLATE_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    msg = "PROD_WEBLATE_API_TOKEN not found in .env.local"
    raise SystemExit(msg)


def get(url: str, key: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Token {key}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def sources(component: dict, key: str) -> tuple[str, bool, list[str]]:
    project = component["project"]["slug"]
    url = (
        f"{BASE}translations/{project}/{component['slug']}/"
        f"{component['source_language']['code']}/units/?page_size=1000"
    )
    result: list[str] = []
    while url:
        page = get(url, key)
        result += [unit["source"][0] for unit in page["results"] if unit["source"]]
        url = page["next"]
    return project, component["is_glossary"], result


def paradigm_stems(word: str, morph, algorithm: str) -> frozenset[str]:
    """Stems of every form of the word read as a nominative dictionary entry."""
    parses = [parse for parse in morph.parse(word) if "nomn" in parse.tag]
    stems = {stem_word(algorithm, word)}
    for parse in parses or morph.parse(word)[:1]:
        stems |= {
            stem_word(algorithm, form.word)
            for form in parse.lexeme
            if form.tag.POS == parse.tag.POS
        }
    return frozenset(stems)


def measure(terms: list[str], strings: list[str], algorithm: str, morph) -> dict:
    counts: collections.Counter[str] = collections.Counter()
    forms: dict[str, collections.Counter] = {
        "rule": collections.Counter(),
        "paradigm": collections.Counter(),
    }
    entries = []
    for term in sorted({term.strip() for term in terms}):
        words = [word.lower() for word in WORD_RE.findall(term)]
        if not words or is_acronym(term):
            continue
        stems = [stem_word(algorithm, word) for word in words]
        rule = {
            tuple(variant)
            for variant in product(*(stem_alternatives(algorithm, s) for s in stems))
        }
        paradigm = (
            [paradigm_stems(word, morph, algorithm) for word in words]
            if morph
            else None
        )
        entries.append((term, tuple(stems), rule, paradigm))
    gained: collections.Counter[str] = collections.Counter()
    for text in strings:
        words = [word.lower() for word in WORD_RE.findall(text)]
        stems = [stem_word(algorithm, word) for word in words]
        hit = {"rule": False, "paradigm": False}
        for term, base, rule, paradigm in entries:
            size = len(base)
            for start in range(len(stems) - size + 1):
                window = tuple(stems[start : start + size])
                surface = " ".join(words[start : start + size])
                if window == base:
                    counts["snowball"] += 1
                    continue
                if window in rule:
                    counts["rule"] += 1
                    forms["rule"][f"{term} <- {surface}"] += 1
                    hit["rule"] = True
                if paradigm and all(window[k] in paradigm[k] for k in range(size)):
                    counts["paradigm"] += 1
                    forms["paradigm"][f"{term} <- {surface}"] += 1
                    hit["paradigm"] = True
        gained.update(name for name, value in hit.items() if value)
    return {
        "terms": len(entries),
        "strings": len(strings),
        "occurrences": dict(counts),
        "strings_gaining_a_term": dict(gained),
        "top_forms": {name: dict(c.most_common(TOP)) for name, c in forms.items()},
    }


def main() -> None:
    key = token()
    stemmer = get_stemmer("ru")
    if stemmer is None:
        msg = "Russian Snowball is not available"
        raise SystemExit(msg)
    try:
        import pymorphy3  # ruff: ignore[import-outside-top-level]

        morph = pymorphy3.MorphAnalyzer()
    except ImportError:
        morph = None
    components = [
        component
        for component in get(f"{BASE}components/?page_size=200", key)["results"]
        if component["source_language"]["code"] == "ru"
        and not component["project"]["slug"].startswith(("wizard-", "test-"))
    ]
    by_project: dict[str, dict[str, list[str]]] = collections.defaultdict(
        lambda: {"terms": [], "strings": []}
    )
    with ThreadPoolExecutor(4) as executor:
        for project, is_glossary, texts in executor.map(
            lambda component: sources(component, key), components
        ):
            by_project[project]["terms" if is_glossary else "strings"] += texts
    result = {
        project: measure(data["terms"], data["strings"], stemmer[0], morph)
        for project, data in sorted(by_project.items())
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    for project, data in result.items():
        print(project, data["occurrences"], data["strings_gaining_a_term"])


if __name__ == "__main__":
    main()
