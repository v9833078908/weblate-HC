# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Build the phase-0 judge golden set (task B1) from the B0 dump and annotation.

Deterministic and offline. Inputs:
    dev-docker/data/col4-b0-units.jsonl   (docs/misc/col4-b0-dump.py output)
    docs/misc/col4-b0-annotations.jsonl   (task B0)
Output:
    docs/misc/col4-judge-golden.json

Three strata:
  clean        - units the B0 annotator labelled pass (label by annotation)
  terminology  - source carries a glossary term, target lacks the rendering
                 (label by construction; precision hand-verified 30/30 in B0)
  mutation     - deterministic defect injected into a clean unit
                 (label by construction)

Splits: train (few-shot source) / dev (prompt iteration) / test (measured once).
Mutations only ever sit on base strings of their own split, so a test mutation
never shares a base with a dev one.

    uv run python docs/misc/col4-judge-goldenset-build.py
"""

from __future__ import annotations

import json
import operator
import random
import re
from collections import Counter
from pathlib import Path

import snowballstemmer

ROOT = Path(__file__).resolve().parents[2]
DUMP = ROOT / "dev-docker" / "data" / "col4-b0-units.jsonl"
ANNOTATION_FILES = (
    ROOT / "docs" / "misc" / "col4-b0-annotations.jsonl",
    ROOT / "docs" / "misc" / "col4-b0-annotations-topup-20260812.jsonl",
)
OUT = ROOT / "docs" / "misc" / "col4-judge-golden.json"

TERM_CAP = 8
TERM_SEED = 20260812
SPLIT_SEED = 20260812
MUTATION_SEEDS = {"train": 20260812001, "dev": 20260812002, "test": 20260812003}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
FR_STEM = snowballstemmer.stemmer("french")
RU_STEM = snowballstemmer.stemmer("russian")


# --------------------------------------------------------------------------
# terminology stratum: the probe logic of docs/misc/col4-visibility-adherence-probe.py
# --------------------------------------------------------------------------
def stems(text: str) -> list[str]:
    return [FR_STEM.stemWord(word.lower()) for word in WORD_RE.findall(text)]


def contains_inflected(term: str, text: str) -> bool:
    needle, hay = stems(term), stems(text)
    if not needle or len(needle) > len(hay):
        return False
    return any(
        hay[i : i + len(needle)] == needle for i in range(len(hay) - len(needle) + 1)
    )


def is_acronym(term: str) -> bool:
    return term.isupper() and len(term) <= 5


def classify(term: str, text: str) -> str | None:
    """Return "visible", "missed", or None for this term in this source."""
    hay = text if is_acronym(term) else text.lower()
    needle = term if is_acronym(term) else term.lower()
    seen_visible = seen_missed = False
    start = hay.find(needle)
    while start != -1:
        end = start + len(needle)
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before and before.isalpha():
            pass
        elif after and after.isalpha():
            seen_missed = True
        else:
            seen_visible = True
        start = hay.find(needle, start + 1)
    if seen_visible:
        return "visible"
    return "missed" if seen_missed else None


def genuine_form(term: str, source: str) -> bool:
    """
    Report whether a single-word term really occurs in the source.

    It counts only when some source word shares its stem. Guards against
    substring collisions - `Блок` inside `блокирует`, `Максим` inside
    `максимально`. A multiword term already required its exact phrase.
    """
    if " " in term:
        return True
    stem = RU_STEM.stemWord(term.lower())
    return any(RU_STEM.stemWord(w.lower()) == stem for w in WORD_RE.findall(source))


def terminology_stratum(units: list[dict], glossary: dict[str, str]) -> list[dict]:
    candidates: dict[tuple[int, str], dict] = {}
    for unit in units:
        for term, rendering in glossary.items():
            bucket = classify(term, unit["source"])
            if bucket is None or contains_inflected(rendering, unit["target"]):
                continue
            if not genuine_form(term, unit["source"]):
                continue
            key = (unit["unit_id"], rendering.lower())
            previous = candidates.get(key)
            if previous is None or (
                previous["bucket"] == "missed" and bucket == "visible"
            ):
                candidates[key] = {
                    "unit": unit,
                    "term": term,
                    "rendering": rendering,
                    "bucket": bucket,
                }

    by_rendering: dict[str, list[dict]] = {}
    for candidate in sorted(candidates.values(), key=lambda c: c["unit"]["unit_id"]):
        by_rendering.setdefault(candidate["rendering"], []).append(candidate)

    rng = random.Random(TERM_SEED)  # ruff: ignore[suspicious-non-cryptographic-random-usage]
    stratum: list[dict] = []
    for _, found in sorted(by_rendering.items()):
        stratum.extend(found if len(found) <= TERM_CAP else rng.sample(found, TERM_CAP))
    stratum.sort(key=lambda c: c["unit"]["unit_id"])
    return stratum


# --------------------------------------------------------------------------
# mutations: every class was observed in the B0 error analysis
# --------------------------------------------------------------------------
# Pairs that keep the sentence grammatical when swapped. Deliberately absent:
# avec/sans, entrer/sortir, commencer/finir - they govern different
# prepositions or complements, so a swap yields broken French ("avec bouger",
# "Entrer de la voiture") and the judge would then catch the grammar, not the
# inverted meaning.
SAFE_ANTONYMS = {
    "toujours": "jamais",
    "jamais": "toujours",
    "avant": "après",
    "après": "avant",
    "vivant": "mort",
    "mort": "vivant",
    "ouvrir": "fermer",
    "fermer": "ouvrir",
    "monter": "descendre",
    "descendre": "monter",
    "gauche": "droite",
    "droite": "gauche",
    "vrai": "faux",
    "faux": "vrai",
    "ami": "ennemi",
    "ennemi": "ami",
    "jour": "nuit",
    "nuit": "jour",
    "accepter": "refuser",
    "refuser": "accepter",
}
# Wrong renderings are not invented: every one appears in the real COL4 output.
WRONG_RENDERING = {
    "Gigastructure": "Gigahrush",
    "GIGASTRUCTURE": "GIGAKHROUCHT",
    "Samosbor": "auto-assemblage",
    "SAMOSBOR": "ASSEMBLAGE",
    "liquidateur": "nettoyeur",
    "porte étanche": "porte blindée",
    "Parti": "Faction",
    "Yulia": "Julia",
    "Nastya": "Naste",
    "Pasha": "Pachka",
    "Ancien": "aîné",
    "Commune": "communauté",
    "moisissure": "champignon",
    "Moisissure": "Champignon",
    "cellule": "cabine",
    "bloc": "pâté",
    "coupon": "ticket",
    "clochard": "sans-abri",
    "commissaire": "inspecteur",
    "Institut de recherche": "NIIR",
    "bannière": "étendard",
    "Petrovich": "Petrovitch",
    "Maxim": "Maksim",
    "concentré": "ration",
    "PTN": "CHZ",
    "Purs": "Propriétaires",
    "Betonovich": "Betonovitch",
}
REALIA = (
    "Parti",
    "Gigastructure",
    "GIGASTRUCTURE",
    "Samosbor",
    "SAMOSBOR",
    "Commune",
    "Purs",
    "Ancien",
    "Moisissure",
    "Liquidateur",
    "Bannière",
    "Institut",
)
FRENCH_STOPWORDS = {
    "vous",
    "nous",
    "elle",
    "dans",
    "pour",
    "avec",
    "mais",
    "plus",
    "tout",
    "sont",
    "cette",
    "votre",
    "leur",
    "être",
    "cela",
    "quoi",
}
VOWELS = tuple("aeiouyâàéèêëîïôöûüh")

# Only the unelided "ne": dropping the "n'" of "je n'ai pas" would leave
# "je ai", a spelling error the judge spots without reading the meaning.
NEGATION_RE = re.compile(r"\bne\s+(\w+(?:\s+\w+)?)\s+pas\b", re.IGNORECASE)
ANTONYM_RE = re.compile(
    r"(?<![\w'])("
    + "|".join(sorted(SAFE_ANTONYMS, key=len, reverse=True))
    + r")(?![\w])",
    re.IGNORECASE,
)
SUBJECT_RE = re.compile(
    r"\b(vous|tu|il|elle|on|je|nous)\s+([a-zà-ÿ]{3,}(?:ez|es|ons|ent|it|is|e|a|ai))\b"
)
# "qui vous tombe sous la main": the pronoun is an object, not the subject,
# and negating around it produces word salad.
RELATIVE_BEFORE_RE = re.compile(r"(?:qui|que|qu')\s*$", re.IGNORECASE)
# Clitics that look like verbs to the pattern above ("tu le faisais").
CLITICS = {
    "le",
    "la",
    "les",
    "lui",
    "leur",
    "en",
    "se",
    "me",
    "te",
    "ne",
    "une",
    "des",
    "ce",
    "cette",
    "ses",
    "mes",
    "tes",
    "que",
    "qui",
}
INFINITIVE_RE = re.compile(r"^([A-ZÀ-Ÿ][a-zà-ÿ']*(?:er|ir|re|oir))\b")
ARTICLE_RE = re.compile(
    r"\b(le|la|les|un|une|des|ce|cette|mon|ma|mes|ton|ta|tes|votre|vos|son|sa|ses)"
    r"\s+([a-zà-ÿ]{4,})\b"
)
POSSESSIVE_RE = re.compile(r"\b(votre|vos|Votre|Vos)\b")
QUOTE_RE = re.compile(r'["«»""]')
RUSSIAN_WORD_RE = re.compile(r"[\u0400-\u04FF]{4,}")
FRENCH_WORD_RE = re.compile(r"\b[a-zà-ÿA-ZÀ-Ÿ]{4,}\b")
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")

CRITICAL_KINDS = {
    "cyrillic-fragment",
    "negation-dropped",
    "antonym-swapped",
    "negation-inserted",
    "infinitive-negated",
}


def keep_case(original: str, replacement: str) -> str:
    return replacement.capitalize() if original[:1].isupper() else replacement


def mutate_inversion(target: str, _source: str, rng: random.Random):
    match = NEGATION_RE.search(target)
    if match:
        return target[: match.start()] + match.group(1) + target[
            match.end() :
        ], "negation-dropped"
    hits = list(ANTONYM_RE.finditer(target))
    if hits:
        hit = rng.choice(hits)
        swapped = keep_case(hit.group(1), SAFE_ANTONYMS[hit.group(1).lower()])
        return target[: hit.start()] + swapped + target[hit.end() :], "antonym-swapped"
    hits = [
        hit
        for hit in SUBJECT_RE.finditer(target)
        if hit.group(2).lower() not in CLITICS
        and not RELATIVE_BEFORE_RE.search(target[: hit.start()])
    ]
    if hits:
        hit = rng.choice(hits)
        subject, verb = hit.group(1), hit.group(2)
        particle = "n'" if verb.lower().startswith(VOWELS) else "ne "
        return (
            target[: hit.start()]
            + f"{subject} {particle}{verb} pas"
            + target[hit.end() :],
            "negation-inserted",
        )
    if INFINITIVE_RE.match(target):
        # ANSWER_* strings are bare infinitives by corpus convention.
        return "Ne pas " + target[0].lower() + target[1:], "infinitive-negated"
    return None, None


def mutate_cyrillic(target: str, source: str, rng: random.Random):
    donors = RUSSIAN_WORD_RE.findall(source)
    victims = [
        m
        for m in FRENCH_WORD_RE.finditer(target)
        if m.group(0).lower() not in FRENCH_STOPWORDS
    ]
    if not victims:
        victims = list(FRENCH_WORD_RE.finditer(target))
    if not donors or not victims:
        return None, None
    victim, donor = rng.choice(victims), rng.choice(donors)
    return target[: victim.start()] + donor + target[
        victim.end() :
    ], "cyrillic-fragment"


def mutate_glossary(target: str, _source: str, rng: random.Random):
    hits = [w for w in WRONG_RENDERING if w in target]
    if not hits:
        return None, None
    chosen = rng.choice(sorted(hits, key=len, reverse=True)[:2])
    return target.replace(chosen, WRONG_RENDERING[chosen], 1), "glossary-substituted"


def mutate_obscenity(target: str, _source: str, rng: random.Random):
    hits = list(ARTICLE_RE.finditer(target))
    if not hits:
        return None, None
    hit = rng.choice(hits)
    noun = hit.group(2)
    insert = "putain d'" if noun[:1].lower() in "aeiouyâàéèêëîïôöûü" else "putain de "
    return (
        target[: hit.start()] + f"{hit.group(1)} {insert}{noun}" + target[hit.end() :],
        "obscenity-injected",
    )


def mutate_quote(target: str, _source: str, _rng: random.Random):
    if not QUOTE_RE.search(target):
        return None, None
    return QUOTE_RE.sub("", target), "quote-frame-dropped"


def mutate_realia_case(target: str, _source: str, rng: random.Random):
    hits = [w for w in REALIA if w in target and w != w.lower()]
    if not hits:
        return None, None
    chosen = rng.choice(sorted(hits))
    replacement = chosen.capitalize() if chosen.isupper() else chosen.lower()
    return target.replace(chosen, replacement, 1), "realia-decapitalised"


def mutate_omission(target: str, _source: str, _rng: random.Random):
    parts = SENTENCE_RE.split(target)
    if len(parts) >= 2:
        return " ".join(parts[:-1]).strip(), "sentence-dropped"
    if "," in target:
        head, _, tail = target.rpartition(",")
        if len(tail.split()) >= 4 and len(head.split()) >= 3:
            return head.strip(), "clause-dropped"
    return None, None


def mutate_person(target: str, _source: str, _rng: random.Random):
    if not POSSESSIVE_RE.search(target):
        return None, None
    mapping = {"votre": "ton", "vos": "tes", "Votre": "Ton", "Vos": "Tes"}
    return POSSESSIVE_RE.sub(lambda m: mapping[m.group(0)], target), "person-switched"


MUTATORS = {
    "inversion": mutate_inversion,
    "cyrillic": mutate_cyrillic,
    "glossary": mutate_glossary,
    "obscenity": mutate_obscenity,
    "quote": mutate_quote,
    "realia": mutate_realia_case,
    "omission": mutate_omission,
    "person": mutate_person,
}
CLASS_ORDER = list(MUTATORS)
CAPS = {
    "train": {
        "inversion": 6,
        "cyrillic": 6,
        "glossary": 3,
        "obscenity": 3,
        "quote": 2,
        "realia": 2,
        "omission": 2,
        "person": 1,
    },
    "dev": {
        "inversion": 25,
        "cyrillic": 25,
        "glossary": 10,
        "obscenity": 12,
        "quote": 6,
        "realia": 6,
        "omission": 8,
        "person": 3,
    },
    "test": {
        "inversion": 60,
        "cyrillic": 60,
        "glossary": 20,
        "obscenity": 26,
        "quote": 10,
        "realia": 10,
        "omission": 15,
        "person": 5,
    },
}
MAX_MUTATIONS_PER_BASE = 3


def assign_splits(
    items: list, group_key, unit_key, seed: int, pinned: dict
) -> dict[int, str]:
    """
    Assign a stratified 15/45/40 split; ``group_key`` of None means one group.

    Groups of one or two go to test - too small to slice.

    ``pinned`` maps unit_id to an already-decided split and is updated in
    place. A unit carrying two defects (two glossary terms in one string) must
    not land in dev for one and test for the other: the same text would be
    read while iterating the prompt and then measured as if it were unseen.
    """
    rng = random.Random(seed)  # ruff: ignore[suspicious-non-cryptographic-random-usage]
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(group_key(item) if group_key else "", []).append(item)
    splits: dict[int, str] = {}
    for _, found in sorted(groups.items()):
        found = sorted(found, key=unit_key)
        rng.shuffle(found)
        size = len(found)
        if size <= 2:
            quota = ["test"] * size
        else:
            n_train, n_dev = round(size * 0.15), round(size * 0.45)
            quota = (
                ["train"] * n_train
                + ["dev"] * n_dev
                + ["test"] * (size - n_train - n_dev)
            )
        cursor = 0
        for item in found:
            unit_id = unit_key(item)
            if unit_id in pinned:
                splits[id(item)] = pinned[unit_id]
                continue
            splits[id(item)] = pinned[unit_id] = quota[cursor]
            cursor += 1
    return splits


def build_mutations(clean: list[dict], clean_splits: dict[int, str]) -> list[dict]:
    mutations: list[dict] = []
    for split in ("train", "dev", "test"):
        bases = sorted(
            (u for u in clean if clean_splits[id(u)] == split),
            key=operator.itemgetter("unit_id"),
        )
        rng = random.Random(MUTATION_SEEDS[split])  # ruff: ignore[suspicious-non-cryptographic-random-usage]
        used, made = Counter(), Counter()
        for name in CLASS_ORDER:
            for base in bases:
                if made[name] >= CAPS[split][name]:
                    break
                if used[base["unit_id"]] >= MAX_MUTATIONS_PER_BASE:
                    continue
                mutated, kind = MUTATORS[name](base["target"], base["source"], rng)
                if not mutated or mutated == base["target"]:
                    continue
                used[base["unit_id"]] += 1
                made[name] += 1
                mutations.append(
                    {
                        "base": base,
                        "split": split,
                        "mutation_class": name,
                        "mutation_kind": kind,
                        "target": mutated,
                        "severity": "critical" if kind in CRITICAL_KINDS else "major",
                    }
                )
    return mutations


def main() -> None:
    lines = DUMP.read_text(encoding="utf-8").splitlines()
    glossary = json.loads(lines[0])["glossary"]
    units = [json.loads(line) for line in lines[1:]]
    by_id = {u["unit_id"]: u for u in units}

    annotated = [
        json.loads(line)
        for annotation_file in ANNOTATION_FILES
        for line in annotation_file.read_text(encoding="utf-8").splitlines()
    ]
    terminology = terminology_stratum(units, glossary)
    defective = {c["unit"]["unit_id"] for c in terminology}

    passed = [
        by_id[a["unit_id"]]
        for a in annotated
        if a["label"] == "pass" and a["stratum"].startswith("random-clean")
    ]
    clean_annotators = {a["unit_id"]: a["annotator"] for a in annotated}
    # A unit cannot be both a clean pass and a terminology defect. The count is
    # printed rather than swallowed: anything above zero means the annotator
    # missed a glossary breach, which is the bias B0 exists to keep out of the
    # clean stratum.
    clean = [u for u in passed if u["unit_id"] not in defective]
    contested = len(passed) - len(clean)
    pinned: dict[int, str] = {}
    clean_splits = assign_splits(
        clean, None, operator.itemgetter("unit_id"), SPLIT_SEED, pinned
    )
    term_splits = assign_splits(
        terminology,
        operator.itemgetter("rendering"),
        lambda c: c["unit"]["unit_id"],
        SPLIT_SEED,
        pinned,
    )
    mutations = build_mutations(clean, clean_splits)

    records = [
        {
            "record_id": f"clean-{unit['unit_id']}",
            "stratum": "clean",
            "split": clean_splits[id(unit)],
            "unit_id": unit["unit_id"],
            "context": unit["context"],
            "source": unit["source"],
            "target": unit["target"],
            "label": "pass",
            "defect_class": None,
            "severity": None,
            "label_origin": "annotation",
            "annotator": clean_annotators[unit["unit_id"]],
        }
        for unit in clean
    ]
    for candidate in terminology:
        unit = candidate["unit"]
        records.append(
            {
                "record_id": f"term-{unit['unit_id']}-{candidate['rendering'].lower().replace(' ', '_')}",
                "stratum": "terminology",
                "split": term_splits[id(candidate)],
                "unit_id": unit["unit_id"],
                "context": unit["context"],
                "source": unit["source"],
                "target": unit["target"],
                "label": "defect",
                "defect_class": "terminology",
                "severity": "major",
                "label_origin": "construction",
                "annotator": "probe:visibility-adherence",
                "term": candidate["term"],
                "expected_rendering": candidate["rendering"],
                "matcher_bucket": candidate["bucket"],
            }
        )
    for mutation in mutations:
        base = mutation["base"]
        records.append(
            {
                "record_id": f"mut-{base['unit_id']}-{mutation['mutation_kind']}",
                "stratum": "mutation",
                "split": mutation["split"],
                "unit_id": base["unit_id"],
                "context": base["context"],
                "source": base["source"],
                "target": mutation["target"],
                "label": "defect",
                "defect_class": mutation["mutation_kind"],
                "severity": mutation["severity"],
                "label_origin": "construction",
                "annotator": f"generator:{MUTATION_SEEDS[mutation['split']]}",
                "base_target": base["target"],
            }
        )

    payload = {
        "dataset": "col4/data/fr - dev mirror of the prod run of 2026-08-11 (task ff7843b4)",
        "normalization": "fix_target + RemoveAddedFinalStop + terminal-!?: + AddFrenchPunctuationSpacing",
        "built_by": "docs/misc/col4-judge-goldenset-build.py",
        "seeds": {
            "terminology": TERM_SEED,
            "split": SPLIT_SEED,
            "mutation": MUTATION_SEEDS,
        },
        "records": records,
    }
    # indent=2 with a trailing newline is what the pretty-format-json hook
    # rewrites the file to; emitting it directly keeps the artifact stable.
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"records: {len(records)} -> {OUT.relative_to(ROOT)}")
    print(f"clean passes dropped as terminology defects: {contested}")
    for stratum in ("clean", "terminology", "mutation"):
        rows = [r for r in records if r["stratum"] == stratum]
        by_split = Counter(r["split"] for r in rows)
        print(f"  {stratum}: {len(rows)} {dict(by_split)}")
    for split in ("train", "dev", "test"):
        rows = [r for r in records if r["split"] == split]
        print(
            f"  {split}: {len(rows)}"
            f" | pass {sum(1 for r in rows if r['label'] == 'pass')}"
            f" | critical {sum(1 for r in rows if r['severity'] == 'critical')}"
            f" | major {sum(1 for r in rows if r['severity'] == 'major')}"
        )
    print(
        "  mutation kinds:",
        dict(
            Counter(
                r["defect_class"] for r in records if r["stratum"] == "mutation"
            ).most_common()
        ),
    )


if __name__ == "__main__":
    main()
