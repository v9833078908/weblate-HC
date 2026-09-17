# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Build the ``ru->vi`` role-split evaluation set (task 9, preparation step).

This writes ``analysis/data/judge-role-split-ru-vi.json``. It reads one
Weblate instance over REST and never writes to it. No Django, no Weblate
imports, stdlib only.

What the set contains, and what each label is actually worth:

``deterministic``
    A distortion this script injected into the corpus's own target. The
    defect is certain *relative to that base target*; the base itself is
    not claimed to be correct. These are the only records where "the
    judge missed a real error" is a provable statement.

``corpus_inconsistency``
    The four production strings where source ``Форт`` is rendered
    ``Bến tàu`` while 45 other strings render the same word
    ``pháo đài``. That two renderings coexist is deterministically
    observable. Which one is right is **not** established here, and
    ``pháo đài`` is a corpus-majority reading, not a verified one.

``established_meaning``
    ``fort_enemy_name_3`` = ``Причал Корсаров`` (a corsair *pier*).
    The plan records that this string must not be repaired by replacing
    the pier with a fort. An arm that demands ``pháo đài`` here is wrong
    even though the lexical glossary rule appears to fire.

``unknown``
    Untouched production Vietnamese. Quality unknown in both directions.
    A model rewriting one of these is recorded as an *intervention*, not
    as proven damage, and not as proven improvement.

Splits are assigned by a stable hash of the record id, so a rebuild moves
nothing. ``control`` is the sealed half: it is only reachable by naming it.

Usage::

    export WEBLATE_API_TOKEN=...      # or --token
    python analysis/probes/judge-role-split-build.py \
        --base-url http://localhost:3001/api/ \
        --component pirate-ships/localization --language vi

    # Show what would be built, write nothing.
    python analysis/probes/judge-role-split-build.py --dry-run

The last output line is ``BUILD_JSON {...}``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "analysis" / "data" / "judge-role-split-ru-vi.json"

#: The incident's glossary pair, from the investigation record in
#: docs/product/plans/2026-09-15-judge-glossary-conflict-and-api-history.md.
#: The dev instance's glossary has no Vietnamese side, so the entry the
#: production judge actually received is reconstructed here as a fixture.
#: `terminology` is maintenance metadata; it does not prove a human
#: approved this rendering.
INCIDENT_GLOSSARY = {
    "source": "Форт",
    "target": "Bến tàu",
    "source_explanation": (
        "The player's home base. It is upgraded tier by tier; its garrison "
        "holds ships and its workshops produce building materials and gold."
    ),
    "target_explanation": "",
    "flags": "terminology",
}

#: The reading the corpus itself uses in 45 strings. Never treated as truth.
CORPUS_MAJORITY_TARGET = "pháo đài"
INCIDENT_TARGET = "Bến tàu"

#: Vietnamese negation particles, for the negation-drop mutation.
NEGATIONS = ("không ", "chưa ", "đừng ")

PLACEHOLDER_RE = re.compile(r"\{\d+\}|\{\[PARAM\d+\]\}|%[A-Z_]+%|%[sd]|\{\}")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>")

FAMILIES = (
    "control_unchanged",
    "number_swap",
    "placeholder_swap",
    "placeholder_drop",
    "negation_drop",
    "clause_omission",
    "glossary_incident",
    "glossary_not_applicable",
)


@dataclass
class Record:
    record_id: str
    split: str
    family: str
    provenance: str
    unit_key: str
    unit_id: int
    source: str
    base_target: str
    target: str
    mutation: dict[str, Any] | None
    expected: dict[str, Any]
    glossary_terms: list[dict[str, str]] = field(default_factory=list)
    note: str = ""
    explanation: str = ""
    checks: list[str] = field(default_factory=list)


def split_for(record_id: str) -> str:
    """Stable two-way split. ``control`` is sealed; ``dev`` is for tuning."""
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return "dev" if int(digest[:8], 16) % 2 == 0 else "control"


def get(base_url: str, path: str, token: str, params: dict[str, str]) -> dict:
    url = urllib.parse.urljoin(base_url, path)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode())


def fetch_units(
    base_url: str, component: str, language: str, token: str, query: str, cap: int
) -> list[dict]:
    """Every unit matching ``query``, following pagination up to ``cap``."""
    path = f"translations/{component}/{language}/units/"
    params = {"q": query, "page_size": "100"}
    units: list[dict] = []
    payload = get(base_url, path, token, params)
    while True:
        units.extend(payload.get("results", []))
        following = payload.get("next")
        if not following or len(units) >= cap:
            return units[:cap]
        request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
            following,
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:  # ruff: ignore[suspicious-url-open-usage]
            payload = json.loads(response.read().decode())


def first(values: object) -> str:
    if isinstance(values, list):
        return str(values[0]) if values else ""
    return str(values or "")


def mechanics(text: str) -> dict[str, list[str]]:
    """Extract the deterministic, engine-visible content of one string."""
    return {
        "numbers": sorted(NUMBER_RE.findall(text)),
        "placeholders": sorted(PLACEHOLDER_RE.findall(text)),
        # Order is kept separately: the engine may reorder a numbered or
        # named placeholder, so a reorder is not a mechanical defect, but
        # a repair that silently reorders one still has to be visible.
        "placeholder_order": PLACEHOLDER_RE.findall(text),
        "markup": sorted(MARKUP_RE.findall(text)),
    }


def mask_protected(text: str) -> str:
    """
    Blank out placeholders and tags so a digit search cannot enter them.

    ``{0}`` holds a placeholder index, not a number the string states.
    Incrementing it produces a dangling slot the engine cannot fill - a
    different defect kind, and one the deterministic checks already
    catch, so it must not be filed as a stated-number swap.
    """
    masked = list(text)
    for pattern in (PLACEHOLDER_RE, MARKUP_RE):
        for found in pattern.finditer(text):
            for index in range(found.start(), found.end()):
                masked[index] = "\x00"
    return "".join(masked)


def swap_number(target: str) -> tuple[str, dict[str, Any]] | None:
    """Change one number so the target states a fact the source does not."""
    match = NUMBER_RE.search(mask_protected(target))
    if match is None:
        return None
    original = match.group()
    if "." in original or "," in original:
        return None
    replacement = str(int(original) + 1) if original != "1" else "7"
    mutated = target[: match.start()] + replacement + target[match.end() :]
    return mutated, {"kind": "number_swap", "from": original, "to": replacement}


def swap_placeholders(target: str) -> tuple[str, dict[str, Any]] | None:
    """Exchange two placeholders, so values land in each other's slot."""
    found = list(PLACEHOLDER_RE.finditer(target))
    if len(found) < 2:
        return None
    one, two = found[0], found[1]
    if one.group() == two.group():
        return None
    mutated = (
        target[: one.start()]
        + two.group()
        + target[one.end() : two.start()]
        + one.group()
        + target[two.end() :]
    )
    return mutated, {
        "kind": "placeholder_swap",
        "from": f"{one.group()}…{two.group()}",
        "to": f"{two.group()}…{one.group()}",
    }


def drop_placeholder(target: str) -> tuple[str, dict[str, Any]] | None:
    """Remove a placeholder, so the engine has nowhere to put its value."""
    found = list(PLACEHOLDER_RE.finditer(target))
    if not found:
        return None
    last = found[-1]
    mutated = (target[: last.start()] + target[last.end() :]).replace("  ", " ")
    return mutated.strip(), {
        "kind": "placeholder_drop",
        "from": last.group(),
        "to": "",
    }


def drop_negation(target: str) -> tuple[str, dict[str, Any]] | None:
    """Remove a negation, which inverts what the line tells the player."""
    for particle in NEGATIONS:
        index = target.find(particle)
        if index >= 0:
            mutated = target[:index] + target[index + len(particle) :]
            return mutated, {
                "kind": "negation_drop",
                "from": particle.strip(),
                "to": "",
            }
    return None


def omit_clause(target: str) -> tuple[str, dict[str, Any]] | None:
    """Drop the final clause, so the line stops saying part of the source."""
    pieces = [piece for piece in target.split(",") if piece.strip()]
    if len(pieces) < 2:
        return None
    tail = pieces[-1].strip()
    if len(tail) < 12:
        return None
    # A tail carrying a number, placeholder or tag would make this
    # omission visible to the deterministic checks, and the arm would be
    # credited for a finding the existing pipeline already makes. Keep
    # this family purely semantic.
    if any(pattern.search(tail) for pattern in (NUMBER_RE, PLACEHOLDER_RE, MARKUP_RE)):
        return None
    mutated = ",".join(pieces[:-1]).rstrip()
    if not mutated.endswith((".", "!", "?")):
        mutated = f"{mutated}."
    return mutated, {"kind": "clause_omission", "from": tail, "to": ""}


MUTATORS = {
    "number_swap": swap_number,
    "placeholder_swap": swap_placeholders,
    "placeholder_drop": drop_placeholder,
    "negation_drop": drop_negation,
    "clause_omission": omit_clause,
}

#: A mutation is either mechanically visible to the deterministic checks
#: or only visible to a reader. The distinction decides what a miss means.
# A numbered placeholder may legally be reordered (the engine fills by
# index), so `placeholder_swap` is a meaning defect no deterministic check
# can see. It is exactly the case where the judge is the only line of
# defence, and it is labelled semantic on purpose.
MECHANICAL = {"number_swap", "placeholder_drop"}


def visible_to_checks(base: str, target: str) -> bool:
    """
    Report whether a deterministic check can see this mutation at all.

    Measured, not declared per family: a dropped clause that happened to
    carry a number is mechanically visible, and mislabelling it as a
    pure meaning defect would credit the judge for a finding the
    existing checks already make for free.
    """
    before, after = mechanics(base), mechanics(target)
    return any(
        before[key] != after[key] for key in ("numbers", "placeholders", "markup")
    )


def expected_for(
    family: str,
    mutation: dict[str, Any] | None,
    base: str = "",
    target: str = "",
) -> dict[str, Any]:
    if family == "control_unchanged":
        return {
            "defect_injected": False,
            "min_severity": None,
            "must_not_rewrite": True,
            "mechanically_visible": False,
            "note": (
                "Untouched production text of unknown quality. A rewrite is "
                "an intervention to be counted, not proven damage."
            ),
        }
    if family == "glossary_incident":
        return {
            "defect_injected": False,
            "min_severity": None,
            "must_not_rewrite": False,
            "mechanically_visible": False,
            "note": (
                "Same source term rendered two ways across the corpus. The "
                "inconsistency is observable; the correct reading is not "
                "established here. Scored on whether the arm raises the "
                "terminology question at all."
            ),
        }
    if family == "glossary_not_applicable":
        return {
            "defect_injected": False,
            "min_severity": None,
            "must_not_rewrite": True,
            "mechanically_visible": False,
            "note": (
                "A corsair pier, not the player's fort. Demanding the "
                "glossary target here is a false positive, and replacing "
                "the pier is a wrong repair."
            ),
        }
    kind = (mutation or {}).get("kind", "")
    return {
        "defect_injected": True,
        "min_severity": "critical" if kind != "clause_omission" else "major",
        "must_not_rewrite": False,
        "mechanically_visible": visible_to_checks(base, target),
        "note": "Injected into the corpus's own target; certain relative to it.",
    }


def build_incident_records(units: list[dict]) -> list[Record]:
    """Collect the ``Форт`` strings and the ``Причал Корсаров`` counter-case."""
    records: list[Record] = []
    for unit in units:
        source, target = first(unit["source"]), first(unit["target"])
        if INCIDENT_TARGET not in target:
            continue
        pier = source.strip().lower().startswith("причал")
        family = "glossary_not_applicable" if pier else "glossary_incident"
        provenance = "established_meaning" if pier else "corpus_inconsistency"
        record_id = f"{family}-{unit['context']}"
        records.append(
            Record(
                record_id=record_id,
                split=split_for(record_id),
                family=family,
                provenance=provenance,
                unit_key=unit["context"],
                unit_id=unit["id"],
                source=source,
                base_target=target,
                target=target,
                mutation=None,
                expected=expected_for(family, None),
                glossary_terms=[dict(INCIDENT_GLOSSARY)],
                note=first(unit.get("note")),
                explanation=first(unit.get("explanation")),
                checks=list(unit.get("checks") or []),
            )
        )
    return records


def build_mutated_records(units: list[dict], per_family: int) -> list[Record]:
    """Deterministic distortions, spread over the families that can carry them."""
    records: list[Record] = []
    used: set[int] = set()
    for family, mutate in MUTATORS.items():
        made = 0
        for unit in units:
            if made >= per_family or unit["id"] in used:
                continue
            source, base = first(unit["source"]), first(unit["target"])
            if not base.strip() or INCIDENT_TARGET in base:
                continue
            outcome = mutate(base)
            if outcome is None:
                continue
            mutated, mutation = outcome
            if mutated == base or not mutated.strip():
                continue
            record_id = f"{family}-{unit['context']}"
            records.append(
                Record(
                    record_id=record_id,
                    split=split_for(record_id),
                    family=family,
                    provenance="deterministic",
                    unit_key=unit["context"],
                    unit_id=unit["id"],
                    source=source,
                    base_target=base,
                    target=mutated,
                    mutation=mutation,
                    expected=expected_for(family, mutation, base, mutated),
                    note=first(unit.get("note")),
                    explanation=first(unit.get("explanation")),
                    checks=[],
                )
            )
            used.add(unit["id"])
            made += 1
    return records


def build_control_records(
    units: list[dict], count: int, exclude: set[int]
) -> list[Record]:
    """Untouched strings, to measure unrequested rewriting."""
    records: list[Record] = []
    for unit in units:
        if len(records) >= count or unit["id"] in exclude:
            continue
        source, target = first(unit["source"]), first(unit["target"])
        if not target.strip() or INCIDENT_TARGET in target:
            continue
        record_id = f"control_unchanged-{unit['context']}"
        records.append(
            Record(
                record_id=record_id,
                split=split_for(record_id),
                family="control_unchanged",
                provenance="unknown",
                unit_key=unit["context"],
                unit_id=unit["id"],
                source=source,
                base_target=target,
                target=target,
                mutation=None,
                expected=expected_for("control_unchanged", None),
                note=first(unit.get("note")),
                explanation=first(unit.get("explanation")),
                checks=list(unit.get("checks") or []),
            )
        )
    return records


def verify(records: list[Record]) -> list[str]:
    """Offline invariants. A violated one means the set cannot be measured."""
    problems: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            problems.append(f"{record.record_id}: duplicate record id")
        seen.add(record.record_id)
        if record.family not in FAMILIES:
            problems.append(f"{record.record_id}: unknown family {record.family}")
        injected = record.expected["defect_injected"]
        if injected and record.target == record.base_target:
            problems.append(f"{record.record_id}: claims a defect but target is base")
        if not injected and record.target != record.base_target:
            problems.append(f"{record.record_id}: no defect claimed but target moved")
        measured = visible_to_checks(record.base_target, record.target)
        if record.expected["mechanically_visible"] != measured:
            problems.append(
                f"{record.record_id}: label says mechanically_visible="
                f"{record.expected['mechanically_visible']} but the "
                f"deterministic content says {measured}"
            )
        if (
            record.family == "glossary_incident"
            and INCIDENT_TARGET not in record.target
        ):
            problems.append(f"{record.record_id}: incident target missing")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the ru->vi role-split set")
    parser.add_argument("--base-url", default="http://localhost:3001/api/")
    parser.add_argument("--component", default="pirate-ships/localization")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--token", default=os.environ.get("WEBLATE_API_TOKEN", ""))
    parser.add_argument("--per-family", type=int, default=8)
    parser.add_argument("--controls", type=int, default=20)
    parser.add_argument("--cap", type=int, default=400)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.token:
        sys.exit("WEBLATE_API_TOKEN is required (read-only access is enough)")

    incident_units = fetch_units(
        args.base_url,
        args.component,
        args.language,
        args.token,
        f'target:"{INCIDENT_TARGET}"',
        args.cap,
    )
    # Three pools, because a family can only be built from a string that
    # carries what it mutates. One generic query returns almost no
    # placeholder-bearing rows, which silently empties two families.
    pools = [
        fetch_units(
            args.base_url,
            args.component,
            args.language,
            args.token,
            query,
            args.cap,
        )
        for query in (
            'target:r"\\{[0-9]\\}" AND state:>=translated',
            'target:r"[0-9]" AND state:>=translated',
            "state:>=translated",
        )
    ]
    pool: list[dict] = []
    seen_ids: set[int] = set()
    for chunk in pools:
        for unit in chunk:
            if unit["id"] not in seen_ids:
                seen_ids.add(unit["id"])
                pool.append(unit)

    records = build_incident_records(incident_units)
    mutated = build_mutated_records(pool, args.per_family)
    records.extend(mutated)
    taken = {record.unit_id for record in records}
    records.extend(build_control_records(pool, args.controls, taken))

    problems = verify(records)
    summary = {
        "records": len(records),
        "by_family": dict(sorted(Counter(r.family for r in records).items())),
        "by_provenance": dict(sorted(Counter(r.provenance for r in records).items())),
        "by_split": dict(sorted(Counter(r.split for r in records).items())),
        "problems": problems,
        "out": str(args.out),
        "dry_run": args.dry_run,
    }

    if problems:
        print("BUILD_JSON", json.dumps(summary, ensure_ascii=False, sort_keys=True))
        sys.exit("dataset invariants violated; nothing written")

    if not args.dry_run:
        payload = {
            "meta": {
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "builder": "analysis/probes/judge-role-split-build.py",
                "instance": args.base_url,
                "component": args.component,
                "language": args.language,
                "corpus_majority_target": CORPUS_MAJORITY_TARGET,
                "incident_target": INCIDENT_TARGET,
                "label_policy": (
                    "deterministic = injected, certain against its own base; "
                    "corpus_inconsistency = two renderings coexist, correct "
                    "one not established; established_meaning = recorded in "
                    "the plan; unknown = untouched production text. No "
                    "record is a human linguistic gold label."
                ),
            },
            "glossary": {INCIDENT_GLOSSARY["source"]: dict(INCIDENT_GLOSSARY)},
            "records": [asdict(record) for record in records],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print("BUILD_JSON", json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
