# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Build a deterministic en->fr golden set from the frozen need-for-greed/ui
production dump for judge seat pair search.

Mutation classes, all construction-provable on the French target:
  number-loss          remove a digit sequence from the target
  placeholder-corrupt  corrupt a {placeholder} in the target
  english-leakage      replace a French word with an English word from the source
  negation-antonym     drop negation, swap an antonym, or insert negation
  omission             drop the last sentence or clause
  glossary-wrong       replace a glossary term's French rendering with English
  quote-dropped        strip all quotation marks
  obscenity-injected   insert "putain de" before a noun
  person-switched      swap votre/vos with ton/tes

Output: JSON with records, each a dict carrying unit identity, target text,
label (pass/defect), defect_class, severity, and label_origin. No human
annotation is claimed. The builder is deterministic: fixed seeds, same input
always produces the same output.

Usage:
  python analysis/probes/nfg-ui-fr-goldenset-build.py
"""

from __future__ import annotations

import json
import operator
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNITS_PATH = ROOT / "analysis" / "data" / "nfg-ui-fr-units.jsonl"
GLOSSARY_PATH = ROOT / "analysis" / "data" / "nfg-ui-fr-glossary.json"
OUT_PATH = ROOT / "analysis" / "data" / "nfg-ui-fr-golden.json"

SLICE_SIZE = 150
SLICE_SEED = 20260826
MUTATION_SEED = 20260826001
MAX_MUTATIONS_PER_BASE = 2

# ---------------------------------------------------------------------------
# French-language mutation mechanics
# ---------------------------------------------------------------------------

SAFE_ANTONYMS = {
    "toujours": "jamais", "jamais": "toujours",
    "avant": "après", "après": "avant",
    "vivant": "mort", "mort": "vivant",
    "ouvrir": "fermer", "fermer": "ouvrir",
    "monter": "descendre", "descendre": "monter",
    "gauche": "droite", "droite": "gauche",
    "vrai": "faux", "faux": "vrai",
    "ami": "ennemi", "ennemi": "ami",
    "jour": "nuit", "nuit": "jour",
    "accepter": "refuser", "refuser": "accepter",
    "bon": "mauvais", "mauvais": "bon",
    "grand": "petit", "petit": "grand",
    "haut": "bas", "bas": "haut",
    "plein": "vide", "vide": "plein",
    "chaud": "froid", "froid": "chaud",
    "nouveau": "ancien", "ancien": "nouveau",
    "premier": "dernier", "dernier": "premier",
    "gagner": "perdre", "perdre": "gagner",
    "acheter": "vendre", "vendre": "acheter",
    "entrer": "sortir", "sortir": "entrer",
    "allumer": "éteindre", "éteindre": "allumer",
    "commencer": "terminer", "terminer": "commencer",
    "trouver": "perdre", "retrouver": "perdre",
    "donner": "prendre", "prendre": "donner",
    "aimer": "détester", "détester": "aimer",
    "possible": "impossible", "impossible": "possible",
    "facile": "difficile", "difficile": "facile",
    "rapide": "lent", "lent": "rapide",
    "riche": "pauvre", "pauvre": "riche",
    "fort": "faible", "faible": "fort",
    "beau": "laid", "laid": "beau",
    "jeune": "vieux", "vieux": "jeune",
    "propre": "sale", "sale": "propre",
    "heureux": "triste", "triste": "heureux",
    "ensemble": "séparément", "séparément": "ensemble",
    "tôt": "tard", "tard": "tôt",
    "souvent": "rarement", "rarement": "souvent",
    "beaucoup": "peu", "peu": "beaucoup",
    "mieux": "pire", "pire": "mieux",
    "plus": "moins", "moins": "plus",
    "ici": "là-bas", "là-bas": "ici",
    "partout": "nulle part", "nulle part": "partout",
    "dedans": "dehors", "dehors": "dedans",
    "dessus": "dessous", "dessous": "dessus",
    "devant": "derrière", "derrière": "devant",
}

VOWELS = tuple("aeiouyâàéèêëîïôöûüh")

NEGATION_RE = re.compile(r"\bne\s+(\w+(?:\s+\w+)?)\s+pas\b", re.IGNORECASE)
ANTONYM_RE = re.compile(
    r"(?<![\w'])("
    + "|".join(sorted(SAFE_ANTONYMS, key=len, reverse=True))
    + r")(?![\w])",
    re.IGNORECASE,
)
SUBJECT_RE = re.compile(
    r"\b(vous|tu|il|elle|on|je|nous|ils|elles)\s+([a-zà-ÿ]{3,}(?:ez|es|ons|ent|it|is|e|a|ai|ont|ait))\b"
)
RELATIVE_BEFORE_RE = re.compile(r"(?:qui|que|qu')\s*$", re.IGNORECASE)
CLITICS = {
    "le", "la", "les", "lui", "leur", "en", "se", "me", "te",
    "ne", "une", "des", "ce", "cette", "ses", "mes", "tes", "que", "qui",
}
INFINITIVE_RE = re.compile(r"^([A-ZÀ-Ÿ][a-zà-ÿ']*(?:er|ir|re|oir))\b")
ARTICLE_RE = re.compile(
    r"\b(le|la|les|un|une|des|ce|cette|mon|ma|mes|ton|ta|tes|votre|vos|son|sa|ses)"
    r"\s+([a-zà-ÿ]{4,})\b"
)
POSSESSIVE_RE = re.compile(r"\b(votre|vos|Votre|Vos)\b")
QUOTE_RE = re.compile(r'["«»\u201c\u201d]')
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
DIGIT_RE = re.compile(r"\d+")
PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_]\w*\}")
FRENCH_WORD_RE = re.compile(r"\b[a-zà-ÿA-ZÀ-Ÿ]{4,}\b")
ENGLISH_WORD_RE = re.compile(r"\b[a-zA-Z]{4,}\b")

CRITICAL_KINDS = {
    "negation-dropped",
    "antonym-swapped",
    "negation-inserted",
    "infinitive-negated",
    "number-loss",
}

# Mutation caps: how many of each class to inject into the 150-unit slice.
MUTATION_CAPS = {
    "number-loss": 12,
    "placeholder-corrupt": 8,
    "english-leakage": 12,
    "negation-antonym": 15,
    "omission": 10,
    "glossary-wrong": 12,
    "quote-dropped": 3,
    "obscenity-injected": 10,
    "person-switched": 8,
}

CLASS_ORDER = list(MUTATION_CAPS)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def keep_case(original: str, replacement: str) -> str:
    return replacement.capitalize() if original[:1].isupper() else replacement


# ---------------------------------------------------------------------------
# mutators — each returns (mutated_text, kind) or (None, None)
# ---------------------------------------------------------------------------

def mutate_number_loss(target: str, _source: str, _rng: random.Random):
    """Remove the first digit sequence from the target."""
    match = DIGIT_RE.search(target)
    if not match:
        return None, None
    return target[: match.start()] + target[match.end() :], "number-loss"


def mutate_placeholder_corrupt(target: str, _source: str, rng: random.Random):
    """Corrupt a {placeholder}: drop a brace, rename, or add a typo."""
    hits = list(PLACEHOLDER_RE.finditer(target))
    if not hits:
        return None, None
    hit = rng.choice(hits)
    original = hit.group(0)
    # Pick a corruption that is visibly wrong but structurally similar
    corruptions = [
        original.replace("{", ""),                     # value} — missing opening brace
        original.replace("}", ""),                     # {value — missing closing brace
        "{" + original[1:-1] + "x}",                   # {valuex} — typo
        original[:-1] + "s}",                          # {values} — pluralised
    ]
    corruption = rng.choice(corruptions)
    return target[: hit.start()] + corruption + target[hit.end() :], "placeholder-corrupt"


def mutate_english_leakage(target: str, source: str, rng: random.Random):
    """Replace a French word with an English word from the source."""
    # English donor words: ASCII-only words from source, 4+ chars,
    # not common stopwords, not already in the target (case-insensitive).
    tgt_words_lower = {w.lower() for w in FRENCH_WORD_RE.findall(target)}
    tgt_words_lower.update(w.lower() for w in ENGLISH_WORD_RE.findall(target))
    src_words = [
        w for w in ENGLISH_WORD_RE.findall(source)
        if w.lower() not in {"this", "that", "with", "from", "your", "have",
                              "will", "what", "when", "where", "which", "there",
                              "their", "about", "would", "could", "should",
                              "they", "them", "these", "those", "into", "over",
                              "just", "more", "some", "only", "also", "very"}
        and w.lower() not in tgt_words_lower
    ]
    # French victim words: 4+ chars, not stopwords. Prefer accented.
    tgt_words = [
        (m.group(0), m.start(), m.end())
        for m in FRENCH_WORD_RE.finditer(target)
        if m.group(0).lower() not in {"vous", "nous", "elle", "dans", "pour",
                                        "avec", "mais", "plus", "tout", "sont",
                                        "cette", "votre", "leur", "être", "cela",
                                        "quoi", "fait", "très", "bien", "comme",
                                        "peut", "aussi", "même", "alors", "dont",
                                        "c'est", "est", "pas", "sur", "une", "ses",
                                        "aux", "des", "nos"}
    ]
    accented = [(w, s, e) for w, s, e in tgt_words
                if any(c in "àâäéèêëîïôöùûüç" for c in w)]
    victims = accented if accented else tgt_words
    if not src_words or not victims:
        return None, None
    donor = rng.choice(src_words)
    victim, start, end = rng.choice(victims)
    replacement = donor if target[start:start+1].islower() else donor.capitalize()
    return target[:start] + replacement + target[end:], "english-leakage"


def mutate_negation_antonym(target: str, _source: str, rng: random.Random):
    """Drop negation, swap an antonym, insert negation, or negate an infinitive."""
    match = NEGATION_RE.search(target)
    if match:
        return (
            target[: match.start()] + match.group(1) + target[match.end():],
            "negation-dropped",
        )
    hits = list(ANTONYM_RE.finditer(target))
    if hits:
        hit = rng.choice(hits)
        swapped = keep_case(hit.group(1), SAFE_ANTONYMS[hit.group(1).lower()])
        return target[: hit.start()] + swapped + target[hit.end():], "antonym-swapped"
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
            + target[hit.end():],
            "negation-inserted",
        )
    if INFINITIVE_RE.match(target):
        return "Ne pas " + target[0].lower() + target[1:], "infinitive-negated"
    return None, None


def mutate_omission(target: str, _source: str, _rng: random.Random):
    """Drop the last sentence, or the last comma-separated clause."""
    parts = SENTENCE_RE.split(target)
    if len(parts) >= 2:
        return " ".join(parts[:-1]).strip(), "sentence-dropped"
    if "," in target:
        head, _, tail = target.rpartition(",")
        if len(tail.split()) >= 4 and len(head.split()) >= 3:
            return head.strip(), "clause-dropped"
    return None, None


def mutate_glossary_wrong(
    target: str, _source: str, rng: random.Random,
    glossary_map: dict[str, str],
):
    """Replace a glossary term's French rendering with its English source."""
    hits = []
    for fr_term, en_term in glossary_map.items():
        if len(fr_term) >= 4 and fr_term in target:
            hits.append((fr_term, en_term))
    if not hits:
        return None, None
    fr_term, en_term = rng.choice(hits)
    return target.replace(fr_term, en_term, 1), "glossary-wrong"


def mutate_quote_dropped(target: str, _source: str, _rng: random.Random):
    """Strip all quotation marks from the target."""
    if not QUOTE_RE.search(target):
        return None, None
    return QUOTE_RE.sub("", target), "quote-dropped"


def mutate_obscenity(target: str, _source: str, rng: random.Random):
    """Insert 'putain de/d'' before a noun following an article."""
    hits = list(ARTICLE_RE.finditer(target))
    if not hits:
        return None, None
    hit = rng.choice(hits)
    noun = hit.group(2)
    insert = "putain d'" if noun[:1].lower() in VOWELS else "putain de "
    return (
        target[: hit.start()] + f"{hit.group(1)} {insert}{noun}" + target[hit.end():],
        "obscenity-injected",
    )


def mutate_person(target: str, _source: str, _rng: random.Random):
    """Swap votre/vos with ton/tes (formal -> informal)."""
    if not POSSESSIVE_RE.search(target):
        return None, None
    mapping = {"votre": "ton", "vos": "tes", "Votre": "Ton", "Vos": "Tes"}
    return POSSESSIVE_RE.sub(lambda m: mapping[m.group(0)], target), "person-switched"


MUTATORS = {
    "number-loss": mutate_number_loss,
    "placeholder-corrupt": mutate_placeholder_corrupt,
    "english-leakage": mutate_english_leakage,
    "negation-antonym": mutate_negation_antonym,
    "omission": mutate_omission,
    "glossary-wrong": mutate_glossary_wrong,
    "quote-dropped": mutate_quote_dropped,
    "obscenity-injected": mutate_obscenity,
    "person-switched": mutate_person,
}


# ---------------------------------------------------------------------------
# stratified slice selection
# ---------------------------------------------------------------------------

def select_slice(units: list[dict], size: int, seed: int) -> list[dict]:
    """Select a stratified slice by target length, deterministic."""
    rng = random.Random(seed)  # ruff: ignore[suspicious-non-cryptographic-random-usage]
    sorted_units = sorted(units, key=lambda u: (len(u["target"]), u["id"]))
    # Systematic sampling: every Nth after shuffle within length strata
    # Group by length bucket (power-of-two)
    buckets: dict[int, list[dict]] = {}
    for u in sorted_units:
        bucket = 1
        n = len(u["target"])
        while bucket * 2 <= n:
            bucket *= 2
        buckets.setdefault(bucket, []).append(u)
    # Allocate proportionally
    total = len(units)
    selected: list[dict] = []
    for bucket_size in sorted(buckets):
        bucket_units = buckets[bucket_size]
        quota = max(1, round(size * len(bucket_units) / total))
        rng.shuffle(bucket_units)
        selected.extend(bucket_units[:quota])
    # Trim to exact size
    rng.shuffle(selected)
    return sorted(selected[:size], key=lambda u: u["id"])


# ---------------------------------------------------------------------------
# mutation builder
# ---------------------------------------------------------------------------

def build_mutations(
    bases: list[dict],
    glossary_map: dict[str, str],
) -> list[dict]:
    """Inject mutations into eligible bases, respecting caps."""
    rng = random.Random(MUTATION_SEED)  # ruff: ignore[suspicious-non-cryptographic-random-usage]
    used: Counter[int] = Counter()
    made: Counter[str] = Counter()
    mutations: list[dict] = []

    for name in CLASS_ORDER:
        for base in bases:
            if made[name] >= MUTATION_CAPS[name]:
                break
            if used[base["id"]] >= MAX_MUTATIONS_PER_BASE:
                continue
            mutator = MUTATORS[name]
            kwargs = {}
            if name == "glossary-wrong":
                kwargs["glossary_map"] = glossary_map
            mutated, kind = mutator(base["target"], base["source"], rng, **kwargs)
            if not mutated or mutated == base["target"]:
                continue
            used[base["id"]] += 1
            made[name] += 1
            mutations.append({
                "base": base,
                "mutation_class": name,
                "mutation_kind": kind,
                "target": mutated,
                "severity": "critical" if kind in CRITICAL_KINDS else "major",
            })
    return mutations


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    units = [json.loads(line) for line in UNITS_PATH.read_text(encoding="utf-8").splitlines()]
    glossary_terms = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))["terms"]

    # Exclude units that fail deterministic checks
    clean = [u for u in units if not u["fails_check"]]
    fails = [u for u in units if u["fails_check"]]
    print(f"total: {len(units)}, fails_check: {len(fails)}, clean: {len(clean)}")

    # Build glossary map: French target -> English source (only terms with length >= 4)
    glossary_map: dict[str, str] = {}
    for term in glossary_terms:
        fr = term["target"]
        en = term["source"]
        if len(fr) >= 4:
            glossary_map[fr] = en

    # Select stratified slice
    slice_units = select_slice(clean, SLICE_SIZE, SLICE_SEED)
    print(f"slice: {len(slice_units)} units")

    # Build mutations
    mutations = build_mutations(slice_units, glossary_map)

    # Build records
    records: list[dict] = []

    # Clean passes: every unit in the 150-unit slice, including mutation bases.
    # The held-out confirmation population is the subset with no mutations.
    for unit in slice_units:
        records.append({
            "record_id": f"clean-{unit['id']}",
            "stratum": "clean",
            "unit_id": unit["id"],
            "context": unit["context"],
            "source": unit["source"],
            "target": unit["target"],
            "label": "pass",
            "defect_class": None,
            "severity": None,
            "label_origin": "construction",
            "annotator": "generator:nfg-ui-fr",
        })

    # Mutated records
    for mutation in mutations:
        base = mutation["base"]
        records.append({
            "record_id": f"mut-{base['id']}-{mutation['mutation_kind']}",
            "stratum": "mutation",
            "unit_id": base["id"],
            "context": base["context"],
            "source": base["source"],
            "target": mutation["target"],
            "label": "defect",
            "defect_class": mutation["mutation_kind"],
            "severity": mutation["severity"],
            "label_origin": "construction",
            "annotator": f"generator:{MUTATION_SEED}",
            "base_target": base["target"],
        })

    payload = {
        "dataset": "need-for-greed/ui/fr — frozen production dump 2026-08-26",
        "built_by": "analysis/probes/nfg-ui-fr-goldenset-build.py",
        "seeds": {
            "slice": SLICE_SEED,
            "mutation": MUTATION_SEED,
        },
        "records": records,
    }

    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Summary
    print(f"records: {len(records)} -> {OUT_PATH.relative_to(ROOT)}")
    for stratum in ("clean", "mutation"):
        rows = [r for r in records if r["stratum"] == stratum]
        print(f"  {stratum}: {len(rows)}")
    for sev in ("critical", "major"):
        rows = [r for r in records if r["severity"] == sev]
        print(f"  {sev}: {len(rows)}")
    print("  mutation kinds:", dict(
        Counter(r["defect_class"] for r in records if r["stratum"] == "mutation").most_common()
    ))
    print(f"  pass: {sum(1 for r in records if r['label'] == 'pass')}")
    print(f"  defect: {sum(1 for r in records if r['label'] == 'defect')}")


if __name__ == "__main__":
    main()