# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic offline study snapshots and dependency graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

from .run import run_dry_run


def build_jobs(
    records: Iterable[Mapping[str, Any]], *, reviewer_seats: Sequence[str]
) -> list[dict[str, object]]:
    """Return the paired A--F job graph without invoking a model."""
    jobs: list[dict[str, object]] = []
    for record in records:
        record_id = str(record["record_id"])
        for arm in ("A", "B", "C", "D"):
            job_id = f"generate:{record_id}:{arm}:1"
            jobs.append(
                {
                    "job_id": job_id,
                    "record_id": record_id,
                    "arm": arm,
                    "stage": "generate",
                    "depends_on": [],
                    "max_attempts": 1,
                }
            )

        b_job_id = f"generate:{record_id}:B:1"
        for arm in ("E", "F"):
            review_ids = []
            for reviewer_seat in reviewer_seats:
                job_id = f"review:{record_id}:{arm}:{reviewer_seat}:1"
                review_ids.append(job_id)
                jobs.append(
                    {
                        "job_id": job_id,
                        "record_id": record_id,
                        "arm": arm,
                        "stage": "review",
                        "reviewer_seat": reviewer_seat,
                        "depends_on": [b_job_id],
                        "max_attempts": 1,
                    }
                )
            jobs.append(
                {
                    "job_id": f"edit:{record_id}:{arm}:1",
                    "record_id": record_id,
                    "arm": arm,
                    "stage": "edit",
                    "depends_on": [b_job_id, *review_ids],
                    "max_attempts": 1,
                }
            )
    return jobs


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Iterable[Mapping[str, object]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _has_text(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(form, str) and form.strip() for form in value)
    )


def _validate_records(records: Sequence[Mapping[str, Any]]) -> None:
    record_ids: set[str] = set()
    identities: set[tuple[str, str, str, str]] = set()
    for record in records:
        record_id = str(record["record_id"])
        identity = (
            str(record["project"]),
            str(record["component"]),
            str(record["id_hash"]),
            str(record["key"]),
        )
        if record_id in record_ids:
            msg = f"Duplicate record_id: {record_id}"
            raise ValueError(msg)
        if identity in identities:
            msg = f"Duplicate stable identity: {identity}"
            raise ValueError(msg)
        if not _has_text(record["ru"]) or not _has_text(record["en"]):
            msg = f"Blank source form: {record_id}"
            raise ValueError(msg)
        record_ids.add(record_id)
        identities.add(identity)


def _cluster_key(record: Mapping[str, Any]) -> str:
    project = str(record["project"])
    component = str(record["component"])
    if project == "heart-abyss" and component == "hub-1":
        return f"{project}/{component}:{str(record['key']).rsplit('_', 1)[0]}"
    return f"{project}/{component}"


def create_study_snapshot(
    records: Iterable[Mapping[str, Any]],
    *,
    study_directory: Path,
    study_id: str,
    bundle_hashes: Mapping[str, str],
    reviewer_seats: Sequence[str],
) -> dict[str, object]:
    """Freeze a screening corpus outside the repository and without inference."""
    selected_records = list(records)
    _validate_records(selected_records)
    study_directory.mkdir(parents=True, exist_ok=True)
    output_paths = [
        study_directory / name
        for name in (
            "inputs.jsonl",
            "eligibility.jsonl",
            "jobs.jsonl",
            "manifest.json",
            "splits.json",
            "decisions.md",
            "preregistration.json",
            "README.md",
        )
    ]
    if any(path.exists() for path in output_paths):
        msg = f"Study directory already contains snapshot files: {study_directory}"
        raise FileExistsError(msg)

    record_ids = [str(record["record_id"]) for record in selected_records]
    eligibility = []
    for record_id, record in zip(record_ids, selected_records, strict=True):
        human_review_status = str(record.get("human_review_status", "unverified"))
        semantic_alignment_review = str(
            record.get("semantic_alignment_review", "pending")
        )
        authoring_language = str(record.get("authoring_language", "unknown"))
        en_automatically_translated = record.get("en_automatically_translated")
        reasons = [
            f"human_review_status={human_review_status}",
            f"semantic_alignment_review={semantic_alignment_review}",
            f"authoring_language={authoring_language}",
        ]
        if en_automatically_translated is True:
            reasons.append("en_automatically_translated=true")
        eligibility.append(
            {
                "record_id": record_id,
                "screening_status": "included",
                "confirmatory_status": "excluded",
                "confirmatory_reason": "; ".join(reasons),
                "authoring_language": authoring_language,
                "semantic_alignment_review": semantic_alignment_review,
                "en_automatically_translated": en_automatically_translated,
            }
        )
    manifest: dict[str, object] = {
        "study_id": study_id,
        "scope": "screening",
        "gates": {"G0": "closed", "G1": "closed", "G2": "open"},
        "bundle_hashes": dict(sorted(bundle_hashes.items())),
        "reviewer_seats": list(reviewer_seats),
        "profile_status": "pending-safe-source",
        "budget_status": "unconfirmed",
        "projected_tokens": None,
        "budget_cap": None,
        "max_attempts_per_job": 1,
        "inference_run": False,
    }
    splits = {
        "pilot": record_ids,
        "calibration": [],
        "holdout": [],
        "clusters": {
            record_id: _cluster_key(record)
            for record_id, record in zip(record_ids, selected_records, strict=True)
        },
        "note": "Screening-only pilot; no confirmatory holdout is assigned.",
    }
    preregistration = {
        "study_id": study_id,
        "scope": "screening",
        "status": "incomplete",
        "gates": manifest["gates"],
        "hypotheses": {
            "H1": "B minus A",
            "H2": "C minus B",
            "H3": "F minus E",
        },
        "analysis": {
            "primary_population": "all assigned screening outputs",
            "missing_ratings": "report lower and upper ITT bounds; never score as pass",
            "paired_clusters": "preserve complete arm vectors by recorded cluster",
        },
        "blockers": [
            "Exact safe-source model and reviewer profiles are not frozen.",
            "A spending cap has not been confirmed.",
            "No concrete pilot inference authorization has been recorded.",
        ],
    }
    decisions = f"""# Решения исследования {study_id}

- 2026-09-16: владелец утвердил корпус из 120 строк: Need For Greed
  UI/Tutorial/Loot (по 30) и Heart Abyss hub-1 dialogue (30).
- 2026-09-16: текущий режим — screening. Confirmatory LQA запрещён до
  доказательств human review конкретных RU/EN ревизий и независимой китайской LQA.
- G0 закрыт этим решением. G1 закрывается данным локальным snapshot/eligibility
  report. G2 остаётся открыт: не зафиксированы безопасные точные runtime profiles,
  расходный лимит и отдельное разрешение конкретного inference pilot.
- До G2 этот study не выполняет HTTP inference и не обращается к production.
"""
    readme = f"""# {study_id}: offline screening preflight

This directory contains raw study inputs and must remain local. Do not commit
it with the probe code or send its game text to an external service before G2.

The snapshot was created with the no-network command:

```sh
PYTHONPATH=analysis/probes uv run python -m dual_reference_zh.prepare \\
  --bundle-directory <bundle-directory> \\
  --study-directory <new-empty-study-directory> \\
  --study-id {study_id} \\
  --dry-run
```

`--dry-run` only writes and validates local files. Its `dry-run.json` must
report `network_requests: 0`. G2 is open until exact safe-source runtime
profiles, a spending cap, and concrete pilot inference authorization are
recorded. The current corpus is screening-only and cannot support a
confirmatory claim.
"""

    _write_jsonl(study_directory / "inputs.jsonl", selected_records)
    _write_jsonl(study_directory / "eligibility.jsonl", eligibility)
    _write_jsonl(
        study_directory / "jobs.jsonl",
        build_jobs(selected_records, reviewer_seats=reviewer_seats),
    )
    _write_json(study_directory / "manifest.json", manifest)
    _write_json(study_directory / "splits.json", splits)
    _write_json(study_directory / "preregistration.json", preregistration)
    (study_directory / "decisions.md").write_text(decisions, encoding="utf-8")
    (study_directory / "README.md").write_text(readme, encoding="utf-8")
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(arguments: Sequence[str] | None = None) -> int:
    """Create a local screening snapshot and run its no-network dry-run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-directory", type=Path, required=True)
    parser.add_argument("--study-directory", type=Path, required=True)
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--reviewer-seat", action="append", dest="reviewer_seats")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(arguments)
    if not args.dry_run:
        parser.error(
            "Only --dry-run is implemented; inference is not available in this tool."
        )

    pilot_path = args.bundle_directory / "pilot-120.jsonl"
    records = _read_jsonl(pilot_path)
    bundle_hashes = {"pilot-120.jsonl": _file_hash(pilot_path)}
    candidate_path = args.bundle_directory / "candidate-pool.jsonl"
    if candidate_path.is_file():
        bundle_hashes["candidate-pool.jsonl"] = _file_hash(candidate_path)
    reviewer_seats = tuple(args.reviewer_seats or ("seat-1", "seat-2"))
    create_study_snapshot(
        records,
        study_directory=args.study_directory,
        study_id=args.study_id,
        bundle_hashes=bundle_hashes,
        reviewer_seats=reviewer_seats,
    )
    print(json.dumps(run_dry_run(args.study_directory), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
