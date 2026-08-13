# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Measure whether repeated non-glossary phrases drift across COL4 fr targets.

This is track C of phase 0.

Background. Cathedral's fix 10.1 built a runtime canon of "first mentions" and
spent 23.4% of its correction stage on it. Our system covers the same ground
three different ways already: the machinery cache answers exact repeats, the
glossary carries every term we care about, and the stock ``inconsistent`` check
flags identical sources with different targets. The open question is what is
left over - repeats that are neither exact whole strings nor glossary terms -
and whether that residue is large enough to justify designing a runtime canon
of our own.

The probe answers it in two layers, because they are not equally trustworthy:

* Layer A - identical sources. Two units with the same source have directly
  comparable targets, so divergence here is a fact, not an inference. This is
  the layer the stock ``inconsistent`` check already covers, measured here so
  the two layers can be read on one scale.
* Layer B - repeated sub-phrases. A phrase inside a longer sentence has no
  target-side alignment, so "same rendering" is decided by proxy: the group is
  consistent when some substring of length ``ratio * len(phrase)`` occurs in
  every target of the group. The proxy is reported at two ratios, because the
  number moves with the threshold and a single number would hide that.

Both layers report divergence twice: raw, and after normalization (casefold,
trim, whitespace collapse, trailing punctuation removed). Cosmetic differences
that layer 0 already strips for free must not be counted as semantic drift.

Read-only. No writes, no machinery, no LLM calls, no cost.

Run inside the Weblate container::

    B64=$(base64 < docs/misc/col4-repeat-drift-probe.py | tr -d '\\n')
    docker exec dev-docker-weblate-1 weblate shell -c \\
        "import base64; exec(base64.b64decode('$B64').decode())"

The last output line is ``DRIFT_JSON {...}``.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict

from weblate.checks.models import Check
from weblate.trans.models import Component, Translation

PROJECT_SLUG = os.environ.get("DRIFT_PROJECT", "col4")
COMPONENT_SLUG = os.environ.get("DRIFT_COMPONENT", "data")
LANGUAGE_CODE = os.environ.get("DRIFT_LANGUAGE", "fr")
ENVIRONMENT = os.environ.get("DRIFT_ENVIRONMENT", "dev-docker mirror")

# The plan's floor. A phrase shorter than this is not a phrase.
MIN_PHRASE_CHARS = int(os.environ.get("DRIFT_MIN_CHARS", "3"))
# A bare function word repeats everywhere and means nothing, so a single-token
# phrase has to be longer than that and carry meaning.
MIN_SINGLE_TOKEN_CHARS = int(os.environ.get("DRIFT_MIN_SINGLE_CHARS", "4"))
MAX_NGRAM = int(os.environ.get("DRIFT_MAX_NGRAM", "6"))
# Above this many units a phrase is boilerplate, not a repeated mention.
MAX_GROUP_UNITS = int(os.environ.get("DRIFT_MAX_GROUP", "50"))
# Cap the proxy test, which is the only expensive part of the probe. The whole
# corpus fits well under this; the cap exists so a bigger component cannot turn
# the probe into a long job by accident.
MAX_GROUPS = int(os.environ.get("DRIFT_MAX_GROUPS", "20000"))
# Below this no shared substring is evidence of anything.
MIN_SHARED_CHARS = 4
RATIOS = (0.5, 0.75)

TRAILING_PUNCTUATION = ".!?…:;,·\"'»«)]}"
TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)

# Closed class only: pronouns, prepositions, conjunctions, particles. Content
# words stay in, including short ones, because a four-letter realia noun is
# exactly what this probe is looking for.
RU_FUNCTION_WORDS = frozenset(
    [
        "а",
        "без",
        "бы",
        "был",
        "была",
        "были",
        "было",
        "быть",
        "в",
        "вам",
        "вас",
        "ваш",
        "весь",
        "вот",
        "все",
        "всего",
        "всех",
        "всё",
        "вы",
        "где",
        "да",
        "для",
        "до",
        "его",
        "ее",
        "её",
        "ей",
        "ему",
        "если",
        "есть",
        "еще",
        "ещё",
        "же",
        "за",
        "и",
        "из",
        "или",
        "им",
        "их",
        "к",
        "как",
        "когда",
        "кто",
        "ли",
        "меня",
        "мне",
        "много",
        "мной",
        "мог",
        "может",
        "мои",
        "мой",
        "моя",
        "мы",
        "на",
        "над",
        "нам",
        "нас",
        "не",
        "него",
        "нее",
        "неё",
        "ней",
        "нем",
        "нём",
        "нет",
        "ни",
        "них",
        "но",
        "ну",
        "о",
        "об",
        "он",
        "она",
        "они",
        "оно",
        "от",
        "очень",
        "по",
        "под",
        "после",
        "при",
        "про",
        "раз",
        "с",
        "сам",
        "свое",
        "своё",
        "свои",
        "свой",
        "себя",
        "сюда",
        "та",
        "так",
        "такой",
        "там",
        "те",
        "тебя",
        "тем",
        "то",
        "тобой",
        "тогда",
        "того",
        "тоже",
        "той",
        "только",
        "том",
        "тот",
        "ту",
        "ты",
        "у",
        "уже",
        "чего",
        "чем",
        "что",
        "чтобы",
        "чуть",
        "эта",
        "эти",
        "это",
        "этого",
        "этой",
        "этом",
        "этот",
        "эту",
        "я",
    ]
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(TRAILING_PUNCTUATION).strip()


def glossary_terms(component: Component) -> set[str]:
    """Every glossary source string of the project, normalized."""
    terms: set[str] = set()
    for glossary in component.project.glossaries:
        for unit in glossary.source_translation.unit_set.iterator():
            term = normalize(unit.source)
            if term:
                terms.add(term)
    return terms


def build_glossary_matcher(terms: set[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    # Longest first so the alternation prefers the most specific term.
    ordered = sorted(terms, key=len, reverse=True)
    joined = "|".join(re.escape(term) for term in ordered)
    return re.compile(rf"(?<!\w)(?:{joined})(?!\w)")


def meaningful(tokens: list[str]) -> bool:
    if len(tokens) == 1:
        token = tokens[0]
        return len(token) >= MIN_SINGLE_TOKEN_CHARS and token not in RU_FUNCTION_WORDS
    return any(
        len(token) >= MIN_SINGLE_TOKEN_CHARS and token not in RU_FUNCTION_WORDS
        for token in tokens
    )


def shared_substring(targets: list[str], length: int) -> str:
    """
    Find a run of ``length`` chars present in every target of the group.

    The group is scanned against its shortest member, so a candidate window is
    tested with ``in``, which runs at C speed. This answers the boolean the
    proxy needs without computing a real longest common substring.
    """
    if length <= 0:
        return ""
    base = min(targets, key=len)
    if len(base) < length:
        return ""
    others = [target for target in targets if target is not base]
    if not others:
        return ""
    for start in range(len(base) - length + 1):
        candidate = base[start : start + length]
        if all(candidate in target for target in others):
            return candidate
    return ""


def consistent(targets: list[str], phrase: str, ratio: float) -> tuple[bool, str]:
    needed = max(MIN_SHARED_CHARS, math.ceil(ratio * len(phrase)))
    found = shared_substring(targets, needed)
    return bool(found), found


def measure_identical_sources(
    translation: Translation,
    by_source: dict[str, list[tuple[int, str, str]]],
    matcher: re.Pattern[str] | None,
    context_by_id: dict[int, str],
) -> dict:
    """
    Report drift over groups of units that share a source verbatim.

    These targets are directly comparable, so the numbers are facts rather
    than the proxy that layer B has to settle for.
    """
    # Does the stock check already see this? If it does, the drift is visible
    # for free and a runtime canon buys detection nobody needs.
    inconsistent_unit_ids = set(
        Check.objects.filter(
            unit__translation=translation, name="inconsistent"
        ).values_list("unit_id", flat=True)
    )

    groups = raw = normalized = 0
    units_total = drifting_units = flagged_groups = shared_context = 0
    examples: list[dict] = []
    for key, members in by_source.items():
        if len(members) < 2 or (matcher is not None and matcher.fullmatch(key)):
            continue
        groups += 1
        units_total += len(members)
        raw_renderings = {target for _, _, target in members}
        norm_renderings = {normalize(target) for _, _, target in members}
        if len(raw_renderings) > 1:
            raw += 1
        if len(norm_renderings) == 1:
            continue
        normalized += 1
        drifting_units += len(members)
        contexts = [context_by_id[unit_id] for unit_id, _, _ in members]
        if len(set(contexts)) < len(contexts):
            shared_context += 1
        flagged = any(unit_id in inconsistent_unit_ids for unit_id, _, _ in members)
        if flagged:
            flagged_groups += 1
        if len(examples) < 15:
            examples.append(
                {
                    "source": key[:120],
                    "units": len(members),
                    "flagged_by_inconsistent": flagged,
                    "renderings": sorted(r[:120] for r in norm_renderings),
                }
            )

    return {
        "_what": "Groups of units whose normalized source is identical. Directly comparable targets, so these numbers are facts.",
        "groups": groups,
        "units_in_groups": units_total,
        "units_in_drifting_groups": drifting_units,
        "raw_drift": raw,
        "normalized_drift": normalized,
        "raw_drift_pct": round(100 * raw / groups, 1) if groups else None,
        "normalized_drift_pct": (
            round(100 * normalized / groups, 1) if groups else None
        ),
        "drifting_groups_seen_by_inconsistent_check": flagged_groups,
        "drifting_groups_seen_by_inconsistent_check_pct": (
            round(100 * flagged_groups / normalized, 1) if normalized else None
        ),
        "drifting_groups_with_a_shared_context": shared_context,
        "_why_the_check_is_blind": "Unit.objects.same() (weblate/trans/models/unit.py:380-388) matches on source AND context, so the stock inconsistent check only sees repeats that also share a msgctxt. In a keyed game corpus every unit has its own key, which is why the count above is what it is. This is structural, not a configuration mistake.",
        "examples": examples,
    }


def main() -> None:
    translation = Translation.objects.get(
        component__project__slug=PROJECT_SLUG,
        component__slug=COMPONENT_SLUG,
        language__code=LANGUAGE_CODE,
    )
    component = translation.component

    units = []
    context_by_id: dict[int, str] = {}
    for unit in translation.unit_set.order_by("id").iterator():
        if not unit.source.strip() or not unit.target.strip():
            continue
        units.append((unit.id, unit.source, unit.target))
        context_by_id[unit.id] = unit.context
    if not units:
        print("DRIFT_JSON", json.dumps({"error": "no translated units"}))
        return

    terms = glossary_terms(component)
    matcher = build_glossary_matcher(terms)

    # Layer A: identical sources.
    by_source: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for unit_id, source, target in units:
        by_source[normalize(source)].append((unit_id, source, target))

    layer_a = measure_identical_sources(translation, by_source, matcher, context_by_id)

    # Layer B: repeated sub-phrases.
    #
    # Stratified, because one number over all repeats would be dominated by
    # ordinary vocabulary. "даже" occurs in 49 units and has no canonical
    # French rendering; counting it as drift measures the Russian language, not
    # our pipeline. The stratum Cathedral's fix 10.1 was actually about is
    # repeated entity mentions, so proper nouns are separated out.
    phrase_units: dict[str, set[int]] = defaultdict(set)
    # A token is a name only if it is usually capitalized. A single capitalized
    # occurrence proves nothing: an ALL-CAPS header capitalizes every token in
    # it, which promoted "лаборатории" and "парень" to proper nouns on the
    # first pass. Fully uppercase forms are therefore not evidence at all, and
    # the titlecase forms have to carry the majority of the occurrences.
    token_total: Counter[str] = Counter()
    token_titlecased: Counter[str] = Counter()
    whole_sources = set(by_source)
    for unit_id, source, _target in units:
        flat = re.sub(r"\s+", " ", unicodedata.normalize("NFC", source))
        tokens: list[str] = []
        for match in TOKEN_RE.finditer(flat):
            token = match.group()
            folded = token.casefold()
            tokens.append(folded)
            if len(token) < 2 or token == token.upper():
                continue
            token_total[folded] += 1
            if token[0].isupper():
                head = flat[: match.start()].rstrip(" \"'«([-—")
                # Sentence-initial capitals say nothing about the token.
                if head and head[-1] not in ".!?…":
                    token_titlecased[folded] += 1
        seen: set[str] = set()
        for size in range(1, MAX_NGRAM + 1):
            for start in range(len(tokens) - size + 1):
                window = tokens[start : start + size]
                phrase = " ".join(window)
                if phrase in seen or len(phrase) < MIN_PHRASE_CHARS:
                    continue
                if not meaningful(window):
                    continue
                seen.add(phrase)
                phrase_units[phrase].add(unit_id)

    candidates = {
        phrase: unit_ids
        for phrase, unit_ids in phrase_units.items()
        if 2 <= len(unit_ids) <= MAX_GROUP_UNITS and phrase not in whole_sources
    }
    if matcher is not None:
        candidates = {
            phrase: unit_ids
            for phrase, unit_ids in candidates.items()
            if not matcher.search(phrase)
        }

    # Keep only maximal phrases: a shorter phrase that occurs in exactly the
    # same units as a longer one containing it carries no extra information and
    # would count the same drift several times.
    by_footprint: dict[frozenset[int], list[str]] = defaultdict(list)
    for phrase, unit_ids in candidates.items():
        by_footprint[frozenset(unit_ids)].append(phrase)
    maximal: dict[str, set[int]] = {}
    for footprint, phrases in by_footprint.items():
        phrases.sort(key=len, reverse=True)
        kept: list[str] = []
        for phrase in phrases:
            if not any(phrase in longer for longer in kept):
                kept.append(phrase)
        for phrase in kept:
            maximal[phrase] = set(footprint)

    proper_tokens = {
        token
        for token, titlecased in token_titlecased.items()
        if titlecased >= 2 and titlecased / token_total[token] >= 0.6
    }

    def stratum_of(phrase: str) -> str:
        if " " in phrase:
            return "multiword"
        return "single_proper" if phrase in proper_tokens else "single_common"

    target_by_id = {unit_id: target for unit_id, _source, target in units}
    ordered = sorted(maximal.items(), key=lambda item: (-len(item[1]), item[0]))
    measured = ordered[:MAX_GROUPS]

    strata = ("multiword", "single_proper", "single_common")
    layer_b = {
        stratum: {
            "groups": 0,
            **{
                f"ratio_{ratio}": {"raw_drift": 0, "normalized_drift": 0}
                for ratio in RATIOS
            },
        }
        for stratum in strata
    }
    layer_b_examples: dict[str, list[dict]] = {stratum: [] for stratum in strata}
    for phrase, unit_ids in measured:
        stratum = stratum_of(phrase)
        bucket_root = layer_b[stratum]
        bucket_root["groups"] += 1
        raw_targets = [target_by_id[unit_id] for unit_id in sorted(unit_ids)]
        norm_targets = [normalize(target) for target in raw_targets]
        for ratio in RATIOS:
            bucket = bucket_root[f"ratio_{ratio}"]
            if not consistent(raw_targets, phrase, ratio)[0]:
                bucket["raw_drift"] += 1
            ok, _found = consistent(norm_targets, phrase, ratio)
            if not ok:
                bucket["normalized_drift"] += 1
                if ratio == RATIOS[0] and len(layer_b_examples[stratum]) < 12:
                    layer_b_examples[stratum].append(
                        {
                            "phrase": phrase,
                            "units": len(unit_ids),
                            "targets": [t[:100] for t in norm_targets[:4]],
                        }
                    )

    for stratum in strata:
        bucket_root = layer_b[stratum]
        total = bucket_root["groups"]
        for ratio in RATIOS:
            bucket = bucket_root[f"ratio_{ratio}"]
            bucket["raw_drift_pct"] = (
                round(100 * bucket["raw_drift"] / total, 1) if total else None
            )
            bucket["normalized_drift_pct"] = (
                round(100 * bucket["normalized_drift"] / total, 1) if total else None
            )

    result = {
        "_environment": ENVIRONMENT,
        "_scope": f"{PROJECT_SLUG}/{COMPONENT_SLUG}/{LANGUAGE_CODE}",
        "units_scanned": len(units),
        "glossary_terms_excluded": len(terms),
        "layer_a_identical_sources": layer_a,
        "layer_b_repeated_phrases": {
            "_what": "Repeated non-glossary source phrases inside different sentences. No target-side alignment exists, so consistency is a proxy: some substring of length ratio*len(phrase) present in every target of the group.",
            "_proxy_bias": "A high ratio calls loose but correct paraphrase a drift; a low ratio calls an accidental shared article a match. Both ratios are reported for that reason.",
            "_strata": "multiword = phrases of two or more tokens. single_proper = one token seen capitalized mid-sentence somewhere, so a name or a piece of realia - the stratum Cathedral's first-mention canon was about. single_common = one token never capitalized, ordinary vocabulary, reported so its noise is visible rather than folded into the headline.",
            "candidate_phrases": len(candidates),
            "maximal_groups": len(maximal),
            "groups_measured": len(measured),
            "max_group_units": MAX_GROUP_UNITS,
            "strata": layer_b,
            "examples": layer_b_examples,
        },
    }
    print("DRIFT_JSON", json.dumps(result, ensure_ascii=False, sort_keys=True))


main()
