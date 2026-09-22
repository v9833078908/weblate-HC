# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic ITT summaries for the dual-reference screening study."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


def summarize_itt(outputs: Iterable[Mapping[str, Any]]) -> dict[str, int | float]:
    """Summarize assigned outputs without silently treating missing data as pass."""
    assigned = 0
    valid_outputs = 0
    invalid_outputs = 0
    rated = 0
    usable = 0
    unusable_observed = 0
    missing_ratings = 0

    for output in outputs:
        assigned += 1
        if output.get("valid_output") is not True:
            invalid_outputs += 1
            unusable_observed += 1
            continue

        valid_outputs += 1
        if output.get("unusable") is True:
            rated += 1
            unusable_observed += 1
        elif output.get("unusable") is False:
            rated += 1
            usable += 1
        else:
            missing_ratings += 1

    if assigned == 0:
        msg = "ITT summary requires at least one assigned output."
        raise ValueError(msg)
    return {
        "assigned": assigned,
        "valid_outputs": valid_outputs,
        "invalid_outputs": invalid_outputs,
        "rated": rated,
        "usable": usable,
        "unusable_observed": unusable_observed,
        "missing_ratings": missing_ratings,
        "unusable_lower_bound": unusable_observed / assigned,
        "unusable_upper_bound": (unusable_observed + missing_ratings) / assigned,
    }


def paired_risk_difference(
    outputs: Iterable[Mapping[str, Any]], *, treatment: str, control: str
) -> float:
    """Return treatment minus control risk for fully rated paired outputs."""
    by_arm: dict[str, dict[str, bool]] = {treatment: {}, control: {}}
    for output in outputs:
        arm = str(output["arm"])
        if arm not in by_arm:
            continue
        record_id = str(output["record_id"])
        if record_id in by_arm[arm]:
            msg = f"Duplicate {arm} output for {record_id}"
            raise ValueError(msg)
        if output.get("valid_output", True) is not True:
            by_arm[arm][record_id] = True
            continue
        outcome = output.get("unusable")
        if not isinstance(outcome, bool):
            msg = f"Missing rating for {arm} output {record_id}"
            raise TypeError(msg)
        by_arm[arm][record_id] = outcome

    if not by_arm[treatment] or set(by_arm[treatment]) != set(by_arm[control]):
        msg = "Paired comparison requires the same non-empty record IDs in both arms."
        raise ValueError(msg)
    treatment_risk = sum(by_arm[treatment].values()) / len(by_arm[treatment])
    control_risk = sum(by_arm[control].values()) / len(by_arm[control])
    return treatment_risk - control_risk


def resample_job_vectors(
    jobs: Sequence[Mapping[str, Any]],
    *,
    clusters: Mapping[str, str],
    seed: int,
    samples: int,
) -> list[dict[str, Any]]:
    """Resample complete cluster job vectors without separating E/F from B."""
    if samples < 1:
        msg = "Resampling requires at least one cluster draw."
        raise ValueError(msg)

    jobs_by_cluster: dict[str, list[Mapping[str, Any]]] = {}
    jobs_by_record: dict[str, list[Mapping[str, Any]]] = {}
    for job in jobs:
        record_id = str(job["record_id"])
        try:
            cluster = clusters[record_id]
        except KeyError as error:
            msg = f"No cluster is assigned to {record_id}."
            raise ValueError(msg) from error
        jobs_by_cluster.setdefault(cluster, []).append(job)
        jobs_by_record.setdefault(record_id, []).append(job)

    for record_id, record_jobs in jobs_by_record.items():
        b_job_id = f"generate:{record_id}:B:1"
        job_ids = {str(job["job_id"]) for job in record_jobs}
        if b_job_id not in job_ids:
            msg = f"Missing shared B job for {record_id}."
            raise ValueError(msg)
        for job in record_jobs:
            if job.get("arm") in {"E", "F"} and job.get("stage") in {
                "review",
                "edit",
            }:
                dependencies = job.get("depends_on")
                if not isinstance(dependencies, list) or b_job_id not in dependencies:
                    msg = f"Broken B dependency for {job['job_id']}."
                    raise ValueError(msg)

    if not jobs_by_cluster:
        msg = "Resampling requires at least one job."
        raise ValueError(msg)
    # Reproducible bootstrap sampling is intentionally non-cryptographic.
    # ruff: ignore[suspicious-non-cryptographic-random-usage]
    randomizer = random.Random(seed)
    cluster_ids = sorted(jobs_by_cluster)
    resampled_jobs: list[dict[str, Any]] = []
    for bootstrap_draw in range(samples):
        cluster = randomizer.choice(cluster_ids)
        resampled_jobs.extend(
            {**job, "bootstrap_draw": bootstrap_draw}
            for job in jobs_by_cluster[cluster]
        )
    return resampled_jobs
