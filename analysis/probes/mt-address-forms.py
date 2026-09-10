#!/usr/bin/env python3
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""
M1 for the scene-context MT slice: address-form consistency, measured.

The instrument is deterministic. For every line it reads the address number
the Russian source states (``ты``-family = singular informal, ``вы``-family =
plural or polite) and the address form the target uses, then counts:

* ``mismatch`` - the target contradicts the source's number or leaves the
  register the project instructions fix (heart-abyss: "Durchgehend du" for
  ``de``, "Tutoiement partout" for ``fr``, so a formal ``Sie``/``vous`` is a
  defect unless a subordinate addresses Pavvaro or Masta, which no hub-1
  scene contains);
* ``flips`` - one speaker inside one scene switching family while the source
  number does not change, which is the systemic defect §6.2 of
  ``docs/product/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md``
  reports for German.

Ambiguous German ``ihr`` (also "her"/"their") is never counted as an address
marker; only ``euch``/``euer``/``eure`` prove informal plural, and only a
non-sentence-initial ``Sie``/``Ihnen``/``Ihr*`` proves formal address. The
count is therefore a floor, and the same floor for every arm.

Usage::

    python3 analysis/probes/mt-address-forms.py --baseline        # prod targets
    python3 analysis/probes/mt-address-forms.py --run analysis/data/mt-scene-context-2026-09-10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import defaultdict
from itertools import pairwise

ROOT = pathlib.Path(__file__).resolve().parents[2]
DUMPS = ROOT / "analysis/data/hub1-remediation-2026-08-25"
SCENES = ("hub1_first_1", "hub1_ramen_1", "hub1_teahouse_1")

# Russian carries second-person address in pronouns and in verb morphology
# alike, and §6.2's German defects sit mostly on the verb forms
# ("Пойдете", "Свернете", "Садитесь"), where no pronoun appears at all.
# Every token the two verb patterns match on this component was vetted by
# hand; the only false positive is the hortative "давайте" ("let's"), which
# addresses nobody and is excluded.
SOURCE_SINGULAR_PRONOUN = re.compile(
    r"\b(ты|тебя|теб[еёе]|тобой|тобою|тво[йяёе]|твои|твоего|твое[йму]|твоих|твоим|твоими|твоём|твоем)\b",
    re.IGNORECASE,
)
SOURCE_PLURAL_PRONOUN = re.compile(
    r"\b(вы|вас|вам|вами|ваш|ваша|ваше|ваши|вашего|ваше[йму]|ваших|вашим|вашими|вашем)\b",
    re.IGNORECASE,
)
SOURCE_SINGULAR_VERB = re.compile(
    r"\b[А-Яа-яЁё]{3,}(?:ешь|ишь|ёшь)(?:ся)?\b", re.IGNORECASE
)
SOURCE_PLURAL_VERB = re.compile(
    r"\b[А-Яа-яЁё]{3,}(?:ете|ёте|ите|йте)(?:сь)?\b", re.IGNORECASE
)
SOURCE_VERB_STOPLIST = frozenset({"давайте"})

TARGET_MARKERS = {
    "de": {
        "singular": re.compile(r"\b(du|dich|dir|dein\w*)\b", re.IGNORECASE),
        # "ihr" is also "her"/"their", so it counts only in front of a
        # second-person plural verb; "euch"/"euer" are unambiguous.
        "plural": re.compile(
            r"\b(euch|euer\w*|eure\w*)\b"
            r"|\bihr\s+(?:seid|habt|wollt|könnt|müsst|sollt|dürft|werdet|wisst|\w+t)\b",
            re.IGNORECASE,
        ),
        # A capitalised "Sie" is also sentence-initial "sie" (she/they), but
        # this only runs on lines whose Russian source addresses the
        # interlocutor, where rendering that address as "Sie ..." is the
        # formal form. Bare imperatives ("Komm vorbei") carry no marker and
        # stay unmeasured, so the count is a floor.
        "formal": re.compile(r"\b(Sie|Ihnen|Ihre\w*|Ihrem|Ihren|Ihrer)\b"),
        "ambiguous": re.compile(r"\bihr\b", re.IGNORECASE),
    },
    "fr": {
        "singular": re.compile(r"\b(tu|te|toi|t'|ton|ta|tes|tien\w*)\b", re.IGNORECASE),
        "plural": re.compile(r"\b(vous|votre|vos|v[oô]tres?)\b", re.IGNORECASE),
        "formal": re.compile(r"(?!x)x"),  # French merges number and politeness
        "ambiguous": re.compile(r"(?!x)x"),
    },
}


def scene_of(context: str) -> str:
    return context.rsplit("_", 1)[0]


def _verb_hit(pattern: re.Pattern[str], text: str) -> bool:
    return any(
        match.group(0).lower() not in SOURCE_VERB_STOPLIST
        for match in pattern.finditer(text)
    )


def source_number(text: str) -> str | None:
    singular = bool(SOURCE_SINGULAR_PRONOUN.search(text)) or _verb_hit(
        SOURCE_SINGULAR_VERB, text
    )
    plural = bool(SOURCE_PLURAL_PRONOUN.search(text)) or _verb_hit(
        SOURCE_PLURAL_VERB, text
    )
    if singular and plural:
        return "both"
    if singular:
        return "sg"
    if plural:
        return "pl"
    return None


def target_families(text: str, lang: str) -> set[str]:
    markers = TARGET_MARKERS[lang]
    found = set()
    for family in ("singular", "plural", "formal"):
        if markers[family].search(text):
            found.add(family)
    return found


def classify(source: str, target: str, lang: str) -> tuple[str | None, set[str], str]:
    """Return (source number, target families, verdict)."""
    number = source_number(source)
    families = target_families(target, lang)
    if number is None or not families:
        return number, families, "not-measured"
    if "formal" in families:
        return number, families, "mismatch-formal"
    if number == "sg" and families == {"plural"}:
        return number, families, "mismatch-number"
    if number == "pl" and families == {"singular"}:
        return number, families, "mismatch-number"
    if number == "both":
        return number, families, "ok"
    return number, families, "ok"


def measure(lines: list[dict], lang: str) -> dict:
    """``lines`` are ``{context, note, source, target}`` in engine order."""
    measured = 0
    mismatch_number = 0
    mismatch_formal = 0
    ambiguous_only = 0
    mismatch_contexts: list[dict] = []
    flip_details: list[dict] = []
    per_speaker: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for line in lines:
        number, families, verdict = classify(line["source"], line["target"], lang)
        if verdict == "not-measured":
            if number is not None and TARGET_MARKERS[lang]["ambiguous"].search(
                line["target"]
            ):
                ambiguous_only += 1
            continue
        measured += 1
        if verdict.startswith("mismatch"):
            if verdict == "mismatch-number":
                mismatch_number += 1
            else:
                mismatch_formal += 1
            mismatch_contexts.append(
                {
                    "context": line["context"],
                    "speaker": line["note"],
                    "source_number": number,
                    "target_families": sorted(families),
                    "source": line["source"],
                    "target": line["target"],
                }
            )
        if number in {"sg", "pl"}:
            family = (
                "formal"
                if "formal" in families
                else ("plural" if "plural" in families else "singular")
            )
            per_speaker[scene_of(line["context"]), line["note"], number].append(
                (line["context"], family)
            )

    flip_scenes: set[str] = set()
    flip_transitions = 0
    for (scene, speaker, number), entries in per_speaker.items():
        families_seen = [family for _context, family in entries]
        transitions = sum(
            1
            for left, right in pairwise(families_seen)
            if left != right
        )
        if transitions:
            flip_scenes.add(scene)
            flip_transitions += transitions
            flip_details.append(
                {
                    "scene": scene,
                    "speaker": speaker,
                    "source_number": number,
                    "sequence": entries,
                }
            )
    return {
        "lines": len(lines),
        "measured": measured,
        "mismatch": mismatch_number + mismatch_formal,
        "mismatch_number": mismatch_number,
        "mismatch_formal": mismatch_formal,
        "ambiguous_only": ambiguous_only,
        "flip_transitions": flip_transitions,
        "flip_scenes": len(flip_scenes),
        "mismatch_contexts": mismatch_contexts,
        "flip_details": flip_details,
    }


def load_dump(lang: str, *, scenes: tuple[str, ...] | None = SCENES) -> list[dict]:
    path = DUMPS / f"heart-abyss__hub-1__{lang}.json"
    units = json.loads(path.read_text(encoding="utf-8"))
    if scenes:
        units = [unit for unit in units if scene_of(unit["context"]) in scenes]
    units.sort(key=lambda unit: unit["position"])
    return [
        {
            "context": unit["context"],
            "note": unit["note"],
            "source": unit["source"][0],
            "target": unit["target"][0],
            "id": unit["id"],
        }
        for unit in units
    ]


def run_lines(path: pathlib.Path, dump: dict[str, dict]) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for record in payload["records"]:
        for context, text in (record.get("translations") or {}).items():
            if not text:
                continue
            unit = dump[context]
            lines.append(
                {
                    "context": context,
                    "note": unit["note"],
                    "source": unit["source"],
                    "target": text,
                }
            )
    lines.sort(key=lambda line: line["context"])
    return lines

GOLD_PATH = (
    ROOT / "analysis/data/mt-scene-context-2026-09-10/address-gold-hub1.json"
)


def score_against_gold(lines: list[dict], lang: str, gold: dict) -> dict:
    """
    Score target address forms against the scene-resolved expected form.

    The expectation is read from the Russian scene alone - who speaks to
    whom and how many people are addressed - before any arm was seen, and
    the project's own instruction fixes the register ("Durchgehend du",
    "Tutoiement partout"), so a formal ``Sie``/``vous`` on a single
    addressee is a defect rather than a preference.
    """
    correct = wrong_number = wrong_register = unmeasured = 0
    details: list[dict] = []
    for line in lines:
        entry = gold.get(line["context"])
        if entry is None:
            continue
        families = target_families(line["target"], lang)
        if not families:
            unmeasured += 1
            continue
        used = (
            "formal"
            if "formal" in families
            else ("plural" if "plural" in families else "singular")
        )
        expected = entry["expected"]
        if used == "formal" and lang == "de":
            verdict = "wrong-register"
            wrong_register += 1
        elif expected == "any" or (
            (expected == "sg" and used == "singular")
            or (expected == "pl" and used == "plural")
        ):
            verdict = "correct"
            correct += 1
        else:
            verdict = "wrong-number"
            wrong_number += 1
        if verdict != "correct":
            details.append(
                {
                    "context": line["context"],
                    "speaker": entry["speaker"],
                    "expected": expected,
                    "used": used,
                    "verdict": verdict,
                    "target": line["target"],
                }
            )
    return {
        "gold_lines": len(gold),
        "scored": correct + wrong_number + wrong_register,
        "correct": correct,
        "wrong_number": wrong_number,
        "wrong_register": wrong_register,
        "unmeasured": unmeasured,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--run")
    parser.add_argument("--json")
    args = parser.parse_args()

    out: dict[str, object] = {}
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    if args.baseline:
        print("== baseline: current production targets ==")
        print(f"{'scope':28} {'lines':>5} {'meas':>5} {'mism':>5} {'num':>4} {'formal':>6} {'flipT':>5} {'flipS':>5}")
        for lang in ("de", "fr"):
            for label, scenes in (("hub-1 full", None), ("3 probe scenes", SCENES)):
                lines = load_dump(lang, scenes=scenes)
                stats = measure(lines, lang)
                print(
                    f"{lang} {label:24} {stats['lines']:5} {stats['measured']:5} "
                    f"{stats['mismatch']:5} {stats['mismatch_number']:4} "
                    f"{stats['mismatch_formal']:6} {stats['flip_transitions']:5} "
                    f"{stats['flip_scenes']:5}"
                )
                out[f"baseline-{lang}-{label}"] = stats
        print(
            f"\n{'gold scope':16} {'gold':>4} {'scored':>6} {'ok':>4} "
            f"{'wrongNum':>8} {'wrongReg':>8} {'unmeas':>6}"
        )
        for lang in ("de", "fr"):
            scored = score_against_gold(load_dump(lang), lang, gold)
            print(
                f"{lang + ' production':16} {scored['gold_lines']:4} "
                f"{scored['scored']:6} {scored['correct']:4} "
                f"{scored['wrong_number']:8} {scored['wrong_register']:8} "
                f"{scored['unmeasured']:6}"
            )
            out[f"gold-baseline-{lang}"] = scored

    if args.run:
        run_dir = pathlib.Path(args.run)
        print("\n== run ==")
        print(
            f"{'file':16} {'lines':>5} {'gold':>4} {'scored':>6} {'ok':>4} "
            f"{'wrongNum':>8} {'wrongReg':>8} {'unmeas':>6} {'flipT':>5}"
        )
        for lang in ("de", "fr"):
            dump = {unit["context"]: unit for unit in load_dump(lang, scenes=None)}
            for arm in ("P", "S0", "S"):
                for repeat in (1, 2, 3):
                    path = run_dir / f"{lang}-{arm}-r{repeat}.json"
                    if not path.exists():
                        continue
                    lines = run_lines(path, dump)
                    stats = measure(lines, lang)
                    scored = score_against_gold(lines, lang, gold)
                    print(
                        f"{path.stem:16} {stats['lines']:5} "
                        f"{scored['gold_lines']:4} {scored['scored']:6} "
                        f"{scored['correct']:4} {scored['wrong_number']:8} "
                        f"{scored['wrong_register']:8} {scored['unmeasured']:6} "
                        f"{stats['flip_transitions']:5}"
                    )
                    out[path.stem] = {**stats, "gold": scored}

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
