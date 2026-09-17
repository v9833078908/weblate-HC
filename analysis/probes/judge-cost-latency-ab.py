#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Every weblate import must follow django.setup(), so it cannot sit at the top.
# ruff: file-ignore[module-import-not-at-top-of-file]
#
# The runner deliberately reaches the judge client's private seams: a
# measurement is only valid when it builds, parses and posts exactly like
# production, and the POST-boundary budget guard has no public hook.
# ruff: file-ignore[private-member-access]

r"""
Measurement runner for the judge batching (Qwen width) and seat-latency A/B.

Task 1 of docs/product/plans/2026-09-17-judge-batching-and-latency-experiments.md.
Experiments A (Qwen batch 1/2/5) and B (DeepSeek seat candidate) share this
runner, its manifest and its block journal; the paid stages still need their
own numeric approval before ``--execute`` will run.

Modes (offline unless stated otherwise):

    # Prepare: split a labeled corpus by source-string family and freeze the
    # manifest skeleton. No DB, no network, no paid request.
    uv run python analysis/probes/judge-cost-latency-ab.py --prepare \
        --corpus analysis/data/<closed-storage>/corpus.json \
        --experiment-id 2026-09-17-anvil-qwen-batch

    # Dry-run (the default): validate the manifest, print scope by language,
    # the calls formula and the cost estimate with caveats. No DB, no network.
    uv run python analysis/probes/judge-cost-latency-ab.py --dry-run \
        --manifest analysis/data/judge-cost-latency-ab/<id>/manifest.json

    # Execute: one paid schedule slot (one arm, one repeat, one language
    # group) against the isolated QA database, through the real
    # run_judge_batch with a POST-boundary budget guard.
    uv run python analysis/probes/judge-cost-latency-ab.py --execute \
        --manifest analysis/data/judge-cost-latency-ab/<id>/manifest.json \
        --slot s001

    # Summarize: recompute every table and the gate verdicts offline from the
    # saved block results. No DB, no network.
    uv run python analysis/probes/judge-cost-latency-ab.py --summarize \
        --manifest analysis/data/judge-cost-latency-ab/<id>/manifest.json

Environment for ``--execute`` (the paid stage; every other mode never dials
out): ``DJANGO_SETTINGS_MODULE`` must name an isolated QA settings module
(``weblate.settings_test`` plus ``CI_DB_*``), never the dev-docker or
production settings, because the ledger is written to the QA database and no
background worker may race the measurement. Provider keys stay in the process
environment; nothing writes them anywhere.

Corpus records live only in the closed artifact directory (0700/0600), which
git ignores; documents get aggregates and manifest hashes only.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import pathlib
import random
import sys
import threading
import time
import uuid as uuid_module
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_test")
os.environ.setdefault("CI_DB_HOST", "127.0.0.1")
os.environ.setdefault("CI_DB_NAME", "weblate")
os.environ.setdefault("CI_DB_USER", "weblate")
os.environ.setdefault("CI_DB_PASSWORD", "weblate")

import django

django.setup()

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, run_judge_batch
from weblate.trans.models.judge import (
    JudgeAdaptiveState,
    JudgeRequestAttempt,
    JudgeVerdict,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.unit import Unit
from weblate.trans.util import join_plural
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / "analysis" / "data" / "judge-cost-latency-ab"
SCHEMA = "judge-cost-latency-ab/manifest-v1"
PLAN = "docs/product/plans/2026-09-17-judge-batching-and-latency-experiments.md"
SEVERITY_RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}
LABELS = ("pass", "defect")
SEVERITIES = ("minor", "major", "critical")
STRATA = ("ui", "long", "terminology", "ambiguous", "glossary")
# The A experiment touches only the Qwen seat; seat 1 stays at the frozen
# baseline width and every other transport knob is inherited from settings.
# Experiment B replaces the seat-1 model (and its reasoning control) per
# arm: the override must reach resolve_judge_seat_profile through settings,
# exactly like the batch width, so the frozen-profile snapshot pins it.
OVERRIDE_KEYS = frozenset({"seat_2_batch_size", "seat_1_model", "seat_1_reasoning"})
OVERRIDE_TO_SETTING = {
    "seat_2_batch_size": "JUDGE_BATCH_SIZE_SEAT_2",
    "seat_1_model": "JUDGE_MODEL_SEAT_1",
    "seat_1_reasoning": "JUDGE_REASONING_EFFORT_SEAT_1",
}
# The dev-docker stack runs background Celery workers: racing them would both
# double-pay batches and let them mutate QA state mid-measurement.
FORBIDDEN_SETTINGS_MODULES = frozenset(
    {"weblate.settings_docker", "weblate.settings", "weblate.settings_example"}
)
PRICE_KEYS = (
    "input_cost_per_token",
    "cache_read_input_token_cost",
    "output_cost_per_token",
)
BUDGET_NUMERIC_FIELDS = (
    "max_unique_units",
    "max_http_attempts_per_slot",
    "max_wall_clock_minutes_per_slot",
    "money_cap_usd",
    "worst_case_completion_tokens",
)
DEFAULT_ARMS = {
    "A0": {
        "title": "control: Qwen batch 1",
        "overrides": {"seat_2_batch_size": 1},
    },
    "A2": {
        "title": "candidate: Qwen batch 2",
        "overrides": {"seat_2_batch_size": 2},
    },
    "A5": {
        "title": "candidate: Qwen batch 5",
        "overrides": {"seat_2_batch_size": 5},
    },
}
BOOTSTRAP_ITERATIONS = 2000


class RunnerError(Exception):
    """A measurement precondition failed; nothing was paid."""


class ManifestError(RunnerError):
    """The manifest is incomplete or internally inconsistent."""


class BudgetExceeded(Exception):
    """The measurement guard tripped; no further POST may be sent."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, payload: object, mode: int = 0o600) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[int | float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return float(ordered[index])


# ---------------------------------------------------------------------------
# Manifest and corpus validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    """The numerically approved paid scope of the whole experiment."""

    max_unique_units: int
    max_http_attempts_per_slot: int
    max_wall_clock_minutes_per_slot: float
    money_cap_usd: Decimal
    worst_case_completion_tokens: int
    provider_side_limit: bool


@dataclass(frozen=True)
class CorpusRecord:
    record_id: str
    family: str
    stratum: str
    language: str
    source_language: str
    source: str
    target: str
    label: str
    severity: str
    project_slug: str
    component_slug: str
    unit_id: int

    @classmethod
    def from_json(cls, raw: object) -> CorpusRecord | None:
        if not isinstance(raw, dict):
            return None
        values: dict[str, object] = {}
        for name in (
            "record_id",
            "family",
            "stratum",
            "language",
            "source_language",
            "source",
            "target",
            "label",
            "severity",
            "project_slug",
            "component_slug",
        ):
            value = raw.get(name)
            values[name] = value if isinstance(value, str) else ""
        unit_id = raw.get("unit_id")
        values["unit_id"] = (
            unit_id if isinstance(unit_id, int) and not isinstance(unit_id, bool) else 0
        )
        return cls(**values)  # type: ignore[arg-type]


def parse_budget(manifest: dict) -> Budget:
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        msg = "manifest.budget must be an object with numeric limits"
        raise ManifestError(msg)
    missing = [name for name in BUDGET_NUMERIC_FIELDS if budget.get(name) is None]
    if missing:
        msg = (
            "manifest.budget is missing numeric limits "
            f"({', '.join(missing)}); paid mode refuses to start without them"
        )
        raise ManifestError(msg)
    numbers: dict[str, Decimal] = {}
    for name in BUDGET_NUMERIC_FIELDS:
        value = budget.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            msg = f"manifest.budget.{name} must be a number: {value!r}"
            raise ManifestError(msg)
        try:
            numbers[name] = Decimal(str(value))
        except ArithmeticError as error:
            msg = f"manifest.budget.{name} is not a finite number: {value!r}"
            raise ManifestError(msg) from error
        if numbers[name] <= 0:
            msg = f"manifest.budget.{name} must be positive"
            raise ManifestError(msg)
    if budget.get("acknowledge_residual_risk") is not True:
        msg = (
            "manifest.budget.acknowledge_residual_risk must be true: without a "
            "provider-side limit the money cap is a local measurement guard, "
            "and the residual risk of unknown charges must be accepted in "
            "writing"
        )
        raise ManifestError(msg)
    return Budget(
        max_unique_units=int(numbers["max_unique_units"]),
        max_http_attempts_per_slot=int(numbers["max_http_attempts_per_slot"]),
        max_wall_clock_minutes_per_slot=float(
            numbers["max_wall_clock_minutes_per_slot"]
        ),
        money_cap_usd=numbers["money_cap_usd"],
        worst_case_completion_tokens=int(numbers["worst_case_completion_tokens"]),
        provider_side_limit=budget.get("provider_side_limit") is True,
    )


def validate_manifest(manifest: object, *, require_budget: bool) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        msg = f"manifest.schema must be {SCHEMA!r}"
        raise ManifestError(msg)
    experiment_id = manifest.get("experiment_id", "")
    if not experiment_id or "/" in experiment_id:
        msg = "manifest.experiment_id must be a non-empty slug"
        raise ManifestError(msg)
    corpus = manifest.get("corpus")
    if (
        not isinstance(corpus, dict)
        or not corpus.get("path")
        or not corpus.get("sha256")
    ):
        msg = "manifest.corpus must pin path and sha256"
        raise ManifestError(msg)
    arms = manifest.get("arms")
    if not isinstance(arms, dict) or not arms:
        msg = "manifest.arms must be a non-empty object"
        raise ManifestError(msg)
    seat_1_batch = manifest.get("seat_1_batch_size")
    if (
        not isinstance(seat_1_batch, int)
        or isinstance(seat_1_batch, bool)
        or seat_1_batch < 1
    ):
        msg = "manifest.seat_1_batch_size must be a positive integer"
        raise ManifestError(msg)
    seat_models = manifest.get("seat_models")
    if not isinstance(seat_models, dict) or not all(
        seat_models.get(seat) for seat in ("seat_1", "seat_2")
    ):
        msg = "manifest.seat_models must name both seat models"
        raise ManifestError(msg)
    for arm_id, arm in arms.items():
        if not isinstance(arm, dict) or not isinstance(arm.get("overrides"), dict):
            msg = f"manifest.arms.{arm_id} must carry overrides"
            raise ManifestError(msg)
        overrides = arm["overrides"]
        unknown = set(overrides) - OVERRIDE_KEYS
        if unknown:
            msg = f"manifest.arms.{arm_id}.overrides has unknown keys {sorted(unknown)}"
            raise ManifestError(msg)
        width = overrides.get("seat_2_batch_size")
        if width is not None and (
            isinstance(width, bool) or not isinstance(width, int) or width < 1
        ):
            msg = (
                f"manifest.arms.{arm_id}.overrides.seat_2_batch_size must be a "
                "positive integer"
            )
            raise ManifestError(msg)
        seat_1_model = overrides.get("seat_1_model")
        if seat_1_model is not None and (
            not isinstance(seat_1_model, str) or not seat_1_model.strip()
        ):
            msg = (
                f"manifest.arms.{arm_id}.overrides.seat_1_model must be a "
                "non-empty model name"
            )
            raise ManifestError(msg)
        seat_1_reasoning = overrides.get("seat_1_reasoning")
        if seat_1_reasoning is not None and (
            not isinstance(seat_1_reasoning, str)
            or seat_1_reasoning not in judge._LITELLM_REASONING_VALUES
        ):
            msg = (
                f"manifest.arms.{arm_id}.overrides.seat_1_reasoning must be one "
                "of the closed LiteLLM reasoning values "
                f"{sorted(judge._LITELLM_REASONING_VALUES)}"
            )
            raise ManifestError(msg)
    repeats = manifest.get("repeats")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        msg = "manifest.repeats must be a positive integer"
        raise ManifestError(msg)
    schedule = manifest.get("schedule")
    if not isinstance(schedule, dict) or not isinstance(schedule.get("slots"), list):
        msg = "manifest.schedule.slots must be a list"
        raise ManifestError(msg)
    slot_ids: set[str] = set()
    for slot in schedule["slots"]:
        if not isinstance(slot, dict):
            msg = "manifest.schedule.slots entries must be objects"
            raise ManifestError(msg)
        slot_id = slot.get("id", "")
        if slot_id in slot_ids:
            msg = f"duplicate schedule slot id {slot_id!r}"
            raise ManifestError(msg)
        slot_ids.add(slot_id)
        if slot.get("arm") not in arms:
            msg = f"slot {slot_id!r} names an unknown arm"
            raise ManifestError(msg)
        if (
            not isinstance(slot.get("repeat"), int)
            or not 1 <= slot["repeat"] <= repeats
        ):
            msg = f"slot {slot_id!r} repeat is out of range"
            raise ManifestError(msg)
        if not isinstance(slot.get("group"), str) or not slot["group"]:
            msg = f"slot {slot_id!r} has no language group"
            raise ManifestError(msg)
    if not slot_ids:
        msg = "manifest.schedule.slots is empty"
        raise ManifestError(msg)
    prices = manifest.get("prices")
    if not isinstance(prices, dict) or not prices:
        msg = "manifest.prices must carry the per-model price triple"
        raise ManifestError(msg)
    for model, triple in prices.items():
        if not isinstance(triple, dict) or any(key not in triple for key in PRICE_KEYS):
            msg = f"manifest.prices.{model} must carry {', '.join(PRICE_KEYS)}"
            raise ManifestError(msg)
    if (
        not isinstance(manifest.get("prices_source"), str)
        or not manifest["prices_source"]
    ):
        msg = "manifest.prices_source must name the rate snapshot"
        raise ManifestError(msg)
    gates = manifest.get("gates")
    if not isinstance(gates, dict) or not gates:
        msg = "manifest.gates must carry the preregistered criteria"
        raise ManifestError(msg)
    est_tokens = manifest.get("est_tokens")
    if est_tokens is not None and (
        not isinstance(est_tokens, dict)
        or any(
            key not in est_tokens
            for key in (
                "prefix_prompt_tokens",
                "per_string_prompt_tokens",
                "per_string_completion_tokens",
            )
        )
    ):
        msg = (
            "manifest.est_tokens must carry prefix_prompt_tokens, "
            "per_string_prompt_tokens and per_string_completion_tokens"
        )
        raise ManifestError(msg)
    if require_budget:
        parse_budget(manifest)
    return manifest


def load_manifest(path: pathlib.Path, *, require_budget: bool) -> dict:
    if not path.exists():
        msg = f"manifest does not exist: {path}"
        raise RunnerError(msg)
    return validate_manifest(_read_json(path), require_budget=require_budget)


def _classify_record(raw: object) -> tuple[CorpusRecord | None, str | None]:
    """Return a clean record or an explicit exclusion reason, never a silent drop."""
    record_id = raw.get("record_id") if isinstance(raw, dict) else None
    record = CorpusRecord.from_json(raw)
    if record is None or not record.record_id:
        return None, "malformed-record"
    if record.label not in LABELS:
        return record, "missing-label"
    if not record.source.strip():
        return record, "empty-source"
    if not record.target.strip():
        return record, "empty-target"
    if record.label == "defect" and record.severity not in SEVERITIES:
        return record, "bad-severity"
    if record.stratum not in STRATA:
        return record, "bad-stratum"
    if record.unit_id <= 0:
        return record, "missing-unit-id"
    return record, None


def load_corpus(manifest: dict) -> tuple[list[CorpusRecord], list[dict]]:
    """Load the pinned corpus and verify its hash; report exclusions explicitly."""
    corpus_path = REPO_ROOT / manifest["corpus"]["path"]
    if not corpus_path.exists():
        msg = f"corpus file is missing: {corpus_path}"
        raise RunnerError(msg)
    digest = _sha256_file(corpus_path)
    if digest != manifest["corpus"]["sha256"]:
        msg = (
            "corpus hash changed since registration: "
            f"{digest} != {manifest['corpus']['sha256']}"
        )
        raise RunnerError(msg)
    payload = _read_json(corpus_path)
    raw_records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(raw_records, list):
        msg = "corpus must be an object with a records list"
        raise RunnerError(msg)
    records: list[CorpusRecord] = []
    excluded: list[dict] = []
    seen: set[str] = set()
    for raw in raw_records:
        record, reason = _classify_record(raw)
        raw_id = raw.get("record_id") if isinstance(raw, dict) else None
        raw_id = raw_id if isinstance(raw_id, str) else ""
        if raw_id and raw_id in seen:
            reason = "duplicate-record-id"
        seen.add(raw_id)
        if reason is not None:
            excluded.append({"record_id": raw_id, "reason": reason})
        else:
            records.append(record)
    expected = manifest["corpus"].get("records")
    if isinstance(expected, int) and len(raw_records) != expected:
        msg = f"corpus record count changed: {len(raw_records)} != {expected}"
        raise RunnerError(msg)
    if not records:
        msg = f"every corpus record is excluded: {excluded!r}"
        raise RunnerError(msg)
    return records, excluded


def build_split(
    records: Sequence[CorpusRecord], seed: int, held_out_fraction: float
) -> dict[str, str]:
    """
    Assign every record to dev or heldout, whole families only.

    Variants of one source string must never straddle the split, so the unit
    of randomization is the family. Strata are balanced within themselves so
    neither part loses a defect class.
    """
    families: dict[str, list[CorpusRecord]] = {}
    for record in records:
        families.setdefault(record.family, []).append(record)
    by_stratum: dict[str, list[str]] = {}
    for family, members in families.items():
        if (
            len({member.language for member in members}) != 1
            or len({member.stratum for member in members}) != 1
        ):
            msg = f"family {family!r} mixes languages or strata; split is unsafe"
            raise RunnerError(msg)
        by_stratum.setdefault(members[0].stratum, []).append(family)
    assignment: dict[str, str] = {}
    for stratum in sorted(by_stratum):
        rng = random.Random(f"{seed}:{stratum}")
        names = sorted(by_stratum[stratum])
        rng.shuffle(names)
        # Proportional allocation, not a modulo of the index: a stratum with
        # a handful of families must still land in both parts.
        held_out_count = round(len(names) * held_out_fraction)
        for index, family in enumerate(names):
            assignment[family] = "heldout" if index < held_out_count else "dev"
    result = {record.record_id: assignment[record.family] for record in records}
    if held_out_fraction > 0:
        heldout_count = sum(1 for v in result.values() if v == "heldout")
        if heldout_count == 0 and len(result) > 0:
            msg = f"held_out_fraction={held_out_fraction} produced zero held-out records; split is unsafe"
            raise RunnerError(msg)
    return result


# ---------------------------------------------------------------------------
# Scope planning (offline)
# ---------------------------------------------------------------------------


def _arm_widths(manifest: dict, arm_id: str) -> tuple[int, int]:
    seat_1 = manifest["seat_1_batch_size"]
    seat_2 = manifest["arms"][arm_id]["overrides"].get("seat_2_batch_size") or 1
    return seat_1, seat_2


def plan_scope(manifest: dict, records: Sequence[CorpusRecord]) -> dict:
    """Group counts, the calls formula and the cost estimate, per arm."""
    split = _read_split_for_scope(manifest)
    groups: dict[str, int] = {}
    for record in records:
        if split.get(record.record_id) == "dev":
            key = f"{record.language}/{record.component_slug}"
            groups[key] = groups.get(key, 0) + 1
    unique_units = sum(groups.values())
    est = manifest.get("est_tokens") or {}
    prefix = Decimal(est.get("prefix_prompt_tokens") or 0)
    per_string = Decimal(est.get("per_string_prompt_tokens") or 0)
    per_completion = Decimal(est.get("per_string_completion_tokens") or 0)
    cache_share = Decimal(
        str(manifest.get("caveats", {}).get("observed_cache_share") or 0)
    )
    repeats = manifest["repeats"]
    arms_plan: dict[str, dict] = {}
    for arm_id in manifest["arms"]:
        seat_1, seat_2 = _arm_widths(manifest, arm_id)
        seat_1_calls = sum(math.ceil(count / seat_1) for count in groups.values())
        seat_2_calls = sum(math.ceil(count / seat_2) for count in groups.values())
        strings = unique_units * repeats
        prompt_by_seat = {
            "seat_1": seat_1_calls * repeats * prefix + strings * per_string,
            "seat_2": seat_2_calls * repeats * prefix + strings * per_string,
        }
        completion_by_seat = dict.fromkeys(prompt_by_seat, strings * per_completion)
        uncached, cached = Decimal(0), Decimal(0)
        for seat, seat_prompt in prompt_by_seat.items():
            seat_completion = completion_by_seat[seat]
            price = manifest["prices"][manifest["seat_models"][seat]]
            uncached += seat_prompt * Decimal(
                str(price["input_cost_per_token"])
            ) + seat_completion * Decimal(str(price["output_cost_per_token"]))
            cached += (
                seat_prompt
                * (1 - cache_share)
                * Decimal(str(price["input_cost_per_token"]))
                + seat_prompt
                * cache_share
                * Decimal(str(price["cache_read_input_token_cost"]))
                + seat_completion * Decimal(str(price["output_cost_per_token"]))
            )
        arms_plan[arm_id] = {
            "seat_widths": {"seat_1": seat_1, "seat_2": seat_2},
            "attempts_per_repeat": {
                "seat_1": seat_1_calls,
                "seat_2": seat_2_calls,
                "pair": seat_1_calls + seat_2_calls,
            },
            "attempts_all_repeats": {
                "seat_1": seat_1_calls * repeats,
                "seat_2": seat_2_calls * repeats,
                "pair": (seat_1_calls + seat_2_calls) * repeats,
            },
            "estimate_usd_uncached_bound": str(uncached.quantize(Decimal("0.0001"))),
            "estimate_usd_cached_scenario": str(cached.quantize(Decimal("0.0001"))),
        }
    return {
        "unique_dev_units": unique_units,
        "groups": dict(sorted(groups.items())),
        "repeats": repeats,
        "arms": arms_plan,
        "caveats": [
            "attempt counts are HTTP arithmetic, not money or wall-clock savings",
            "estimates use manifest.est_tokens and ignore retries and adaptive "
            "width reductions",
            "the cached scenario applies the observed cache share to the whole "
            "prefix; a provider cache makes no promise",
            "unknown charges (timed-out or unmetered attempts) stay unknown",
        ],
    }


def _artifact_dir(manifest: dict) -> pathlib.Path:
    directory = REPO_ROOT / manifest.get("artifact_dir", "")
    if not directory.is_dir():
        msg = f"artifact directory is missing: {directory}"
        raise RunnerError(msg)
    return directory


def _read_split_for_scope(manifest: dict) -> dict[str, str]:
    path = _artifact_dir(manifest) / "corpus.split.json"
    if not path.exists():
        msg = f"split file is missing: {path}; re-run --prepare on the same corpus"
        raise RunnerError(msg)
    payload = _read_json(path)
    split = payload.get("assignment")
    if not isinstance(split, dict):
        msg = "corpus.split.json has no assignment map"
        raise RunnerError(msg)
    return split


def check_split_reproducible(manifest: dict, records: Sequence[CorpusRecord]) -> None:
    split = _read_split_for_scope(manifest)
    rebuilt = build_split(
        records,
        manifest["corpus"]["split_seed"],
        manifest["corpus"]["held_out_fraction"],
    )
    drifted = sum(1 for key, part in rebuilt.items() if split.get(key) != part)
    if drifted:
        msg = (
            f"saved split is not reproducible ({drifted} records moved); "
            "re-register the experiment"
        )
        raise RunnerError(msg)


# ---------------------------------------------------------------------------
# Modes: prepare and dry-run
# ---------------------------------------------------------------------------


def _build_schedule(
    arm_ids: Sequence[str], groups: set[str], repeats: int
) -> list[dict]:
    slots: list[dict] = []
    for repeat in range(1, repeats + 1):
        for group in sorted(groups):
            for arm_id in arm_ids:
                slots.append(
                    {
                        "id": f"s{len(slots) + 1:03d}",
                        "arm": arm_id,
                        "repeat": repeat,
                        "group": group,
                    }
                )
    return slots


def _git_revision() -> str:
    import subprocess  # ruff: ignore[import-outside-top-level]

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cmd_prepare(args: argparse.Namespace) -> int:
    corpus_path = pathlib.Path(args.corpus)
    if not corpus_path.is_absolute():
        corpus_path = pathlib.Path.cwd() / corpus_path
    corpus_path = corpus_path.resolve()
    if not corpus_path.exists():
        msg = f"corpus does not exist: {corpus_path}"
        raise RunnerError(msg)
    payload = _read_json(corpus_path)
    raw_records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(raw_records, list) or not raw_records:
        msg = "corpus must be an object with a non-empty records list"
        raise RunnerError(msg)
    experiment_id = args.experiment_id
    if not experiment_id or "/" in experiment_id or experiment_id.startswith("."):
        msg = "--experiment-id must be a plain slug"
        raise RunnerError(msg)
    directory = ARTIFACT_ROOT / experiment_id
    if directory.exists():
        msg = f"experiment directory already exists: {directory}"
        raise RunnerError(msg)
    try:
        arms = (
            json.loads(args.arms) if args.arms else json.loads(json.dumps(DEFAULT_ARMS))
        )
    except ValueError as error:
        msg = f"--arms is not valid JSON: {error}"
        raise RunnerError(msg) from error
    validate_manifest(
        {
            "schema": SCHEMA,
            "experiment_id": experiment_id,
            "corpus": {"path": "x", "sha256": "x"},
            "seat_models": {"seat_1": args.seat_1_model, "seat_2": args.seat_2_model},
            "seat_1_batch_size": 2,
            "arms": arms,
            "repeats": args.repeats,
            "schedule": {
                "slots": [
                    {"id": "s001", "arm": next(iter(arms)), "repeat": 1, "group": "g"}
                ]
            },
            "prices": {"placeholder": dict.fromkeys(PRICE_KEYS, 0)},
            "prices_source": "placeholder",
            "gates": {"control_arm": next(iter(arms))},
        },
        require_budget=False,
    )
    records: list[CorpusRecord] = []
    excluded: list[dict] = []
    for raw in raw_records:
        record, reason = _classify_record(raw)
        if reason is not None:
            raw_id = raw.get("record_id") if isinstance(raw, dict) else None
            excluded.append(
                {
                    "record_id": raw_id if isinstance(raw_id, str) else "",
                    "reason": reason,
                }
            )
        else:
            records.append(record)
    if not records:
        msg = "every corpus record is excluded; nothing to split"
        raise RunnerError(msg)
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    frozen_corpus = directory / "corpus.json"
    _write_json(
        frozen_corpus,
        {
            "experiment_id": experiment_id,
            "imported_from": str(corpus_path),
            "records": raw_records,
        },
    )
    split = build_split(records, args.seed, args.held_out_fraction)
    _write_json(
        directory / "corpus.split.json",
        {
            "schema": "judge-cost-latency-ab/split-v1",
            "seed": args.seed,
            "held_out_fraction": args.held_out_fraction,
            "corpus_sha256": _sha256_file(frozen_corpus),
            "assignment": split,
            "excluded": excluded,
        },
    )
    languages = sorted({record.language for record in records})
    families = {record.family for record in records}
    groups = {f"{r.language}/{r.component_slug}" for r in records}
    arm_ids = list(arms)
    manifest = {
        "schema": SCHEMA,
        "plan": PLAN,
        "experiment_id": experiment_id,
        "artifact_dir": str(directory.relative_to(REPO_ROOT)),
        "registered_at": timezone.now().isoformat(),
        "source_commit": _git_revision(),
        "corpus": {
            "path": str(frozen_corpus.relative_to(REPO_ROOT)),
            "sha256": _sha256_file(frozen_corpus),
            "records": len(raw_records),
            "clean_records": len(records),
            "excluded": len(excluded),
            "families": len(families),
            "languages": languages,
            "labels_origin": payload.get("labels_origin", "unspecified"),
            "split_seed": args.seed,
            "held_out_fraction": args.held_out_fraction,
        },
        "seat_models": {
            "seat_1": args.seat_1_model,
            "seat_2": args.seat_2_model,
        },
        "seat_1_batch_size": 2,
        "arms": arms,
        "repeats": args.repeats,
        "schedule": {
            "note": (
                "slots alternate arms within each repeat so control and "
                "candidates share the time of day; one slot = one paid block"
            ),
            "slots": _build_schedule(arm_ids, groups, args.repeats),
        },
        "est_tokens": {
            "prefix_prompt_tokens": None,
            "per_string_prompt_tokens": None,
            "per_string_completion_tokens": None,
        },
        "caveats": {"observed_cache_share": None},
        "prices": {},
        "prices_source": "",
        "budget": dict.fromkeys(BUDGET_NUMERIC_FIELDS)
        | {
            "provider_side_limit": False,
            "acknowledge_residual_risk": False,
        },
        "gates": {
            "control_arm": arm_ids[0],
            "note": (
                "proposed engineering policy from the plan; the owner must "
                "confirm or replace these numbers before the paid comparison"
            ),
            "critical_misses": "zero new confirmed critical misses on held-out",
            "major_recall_drop_pp": 2,
            "false_flag_growth_pp": 3,
            "terminal_unparsed_growth_pp": 1,
            "cost_drop_percent_A": 10,
            "wall_clock_growth_percent_A": 5,
            "wall_clock_drop_percent_B": 20,
            "cost_growth_percent_B": 5,
        },
        "frozen_profiles": None,
        "excluded_records": excluded,
    }
    manifest_path = directory / "manifest.json"
    _write_json(manifest_path, manifest)
    print(f"experiment directory: {directory}")
    print(f"clean records: {len(records)} in {len(families)} families")
    print(f"excluded (explicit, before any LLM call): {len(excluded)}")
    for language in languages:
        count = sum(1 for record in records if record.language == language)
        print(f"  {language}: {count} records")
    dev = sum(1 for part in split.values() if part == "dev")
    print(f"split: dev={dev}, heldout={len(records) - dev} (seed {args.seed})")
    print(f"schedule: {len(manifest['schedule']['slots'])} slots")
    print("\nA human must still fill in manifest.json before execution:")
    print("  - budget: all numeric limits + acknowledge_residual_risk=true")
    print("  - prices and prices_source from the current rate snapshot")
    print("  - est_tokens and caveats.observed_cache_share from the archive")
    print(f"then: --dry-run --manifest {manifest_path}")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    manifest = load_manifest(pathlib.Path(args.manifest), require_budget=False)
    records, excluded = load_corpus(manifest)
    check_split_reproducible(manifest, records)
    scope = plan_scope(manifest, records)
    print(f"experiment: {manifest['experiment_id']}")
    print(f"corpus: {manifest['corpus']['records']} records, sha256 pinned")
    print(f"excluded before LLM (explicit): {len(excluded)}")
    print(f"unique dev units: {scope['unique_dev_units']}")
    for group, count in scope["groups"].items():
        print(f"  {group}: {count}")
    for arm_id, plan in scope["arms"].items():
        widths = plan["seat_widths"]
        print(
            f"arm {arm_id}: widths {widths['seat_1']}/{widths['seat_2']}, "
            f"pair calls {plan['attempts_per_repeat']['pair']}/repeat, "
            f"{plan['attempts_all_repeats']['pair']} total (no retries)"
        )
        print(
            f"  estimate: ${plan['estimate_usd_uncached_bound']} uncached bound, "
            f"${plan['estimate_usd_cached_scenario']} with observed cache share"
        )
    try:
        budget = parse_budget(manifest)
    except ManifestError as error:
        print(f"\nBUDGET NOT SET: --execute will refuse to start ({error})")
        return 0
    if scope["unique_dev_units"] > budget.max_unique_units:
        print(
            "\nWARNING: dev scope exceeds budget.max_unique_units "
            f"({scope['unique_dev_units']} > {budget.max_unique_units})"
        )
    print(f"\nbudget: money cap ${budget.money_cap_usd}")
    print(
        "provider-side limit: "
        + (
            "acknowledged"
            if budget.provider_side_limit
            else "NOT set - residual unknown-charge risk applies"
        )
    )
    for caveat in scope["caveats"]:
        print(f"caveat: {caveat}")
    if manifest.get("frozen_profiles") is None:
        print("\nprofiles are not frozen yet: the first executed slot pins them")
    return 0


# ---------------------------------------------------------------------------
# Paid execution
# ---------------------------------------------------------------------------


class Journal:
    """Append-only JSONL log of experiment blocks and outbound POSTs."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._lock = threading.Lock()
        if not path.exists():
            path.touch(mode=0o600)

    def append(self, event: dict) -> None:
        event = {"ts": timezone.now().isoformat(), **event}
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def slots_by_state(self) -> dict[str, list[str]]:
        states: dict[str, list[str]] = {
            "completed": [],
            "failed": [],
            "interrupted": [],
        }
        if not self.path.exists():
            return states
        mapping = {
            "slot_end": "completed",
            "slot_failed": "failed",
            "slot_interrupted": "interrupted",
        }
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                key = mapping.get(event.get("event"))
                slot = event.get("slot")
                if key and slot and slot not in states[key]:
                    states[key].append(slot)
        return states


class PostGuard:
    """
    Reserve every outbound judge POST before it is sent.

    Wraps ``judge._post_batch`` so internal retries, width-one isolation and
    fallback attempts all pass the same gate. This is a local measurement
    guard, not a billing subsystem: without a provider-side limit the money
    ceiling is a best-effort reservation.
    """

    def __init__(
        self,
        budget: Budget,
        prices: dict[str, dict],
        wall_deadline: float,
    ):
        self.budget = budget
        self.prices = prices
        self.wall_deadline = wall_deadline
        self.attempts = 0
        self.reserved = Decimal(0)
        self.spent = Decimal(0)
        self.unmetered = 0
        self.tripped: str | None = None
        self._lock = threading.Lock()
        self._original = judge._post_batch
        self._installed = False

    def install(self) -> None:
        judge._post_batch = self._guarded_post  # type: ignore[assignment]
        self._installed = True

    def uninstall(self) -> None:
        if self._installed:
            judge._post_batch = self._original  # type: ignore[assignment]
            self._installed = False

    def _worst_case(self, payload: dict, profile: judge.JudgeSeatProfile) -> Decimal:
        price = self.prices.get(profile.model)
        if price is None:
            msg = f"no price entry for model {profile.model!r}; refusing to send"
            raise RunnerError(msg)
        prompt_estimate = Decimal(len(json.dumps(payload).encode()) // 3 + 1)
        completion = Decimal(
            profile.max_tokens or self.budget.worst_case_completion_tokens
        )
        return prompt_estimate * Decimal(
            str(price["input_cost_per_token"])
        ) + completion * Decimal(str(price["output_cost_per_token"]))

    def _reserve(self, payload: dict, profile: judge.JudgeSeatProfile) -> Decimal:
        with self._lock:
            if self.tripped is not None:
                raise BudgetExceeded(self.tripped)
            if time.monotonic() > self.wall_deadline:
                self.tripped = "wall-clock limit reached"
                raise BudgetExceeded(self.tripped)
            if self.attempts + 1 > self.budget.max_http_attempts_per_slot:
                self.tripped = "max_http_attempts_per_slot reached"
                raise BudgetExceeded(self.tripped)
            worst = self._worst_case(payload, profile)
            if self.reserved + self.spent + worst > self.budget.money_cap_usd:
                self.tripped = f"money cap ${self.budget.money_cap_usd} reached"
                raise BudgetExceeded(self.tripped)
            self.attempts += 1
            self.reserved += worst
            return worst

    def _guarded_post(
        self, payload: dict, profile: judge.JudgeSeatProfile
    ) -> judge._BatchResponse:
        worst = self._reserve(payload, profile)
        response = self._original(payload, profile)
        self._settle(worst, response, profile)
        return response

    def _settle(
        self,
        worst: Decimal,
        response: judge._BatchResponse,
        profile: judge.JudgeSeatProfile,
    ) -> None:
        with self._lock:
            self.reserved -= worst
            usage = response.payload.get("usage") if response.payload else None
            if not isinstance(usage, dict):
                # Unknown cost keeps its worst-case reservation.
                self.unmetered += 1
                self.reserved += worst
                return
            price = self.prices[profile.model]
            prompt = Decimal(usage.get("prompt_tokens") or 0)
            completion = Decimal(usage.get("completion_tokens") or 0)
            details = usage.get("prompt_tokens_details")
            cached = (
                Decimal(details.get("cached_tokens") or 0)
                if isinstance(details, dict)
                else Decimal(0)
            )
            self.spent += (
                (prompt - cached) * Decimal(str(price["input_cost_per_token"]))
                + cached * Decimal(str(price["cache_read_input_token_cost"]))
                + completion * Decimal(str(price["output_cost_per_token"]))
            )


def profile_snapshot(profile: judge.JudgeSeatProfile) -> dict:
    """Safe resolved-profile metadata; never the api key."""
    return {
        "seat": profile.seat,
        "model": profile.model,
        "provider": profile.provider,
        "upstream_model": profile.upstream_model,
        "alias_revision": profile.alias_revision,
        "batch_size": profile.batch_size,
        "response_format": profile.response_format,
        "reasoning": profile.reasoning,
        "stream": profile.stream,
        "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
        "request_deadline": profile.request_deadline,
        "endpoint_fingerprint": profile.endpoint_fingerprint,
        "model_fingerprint": profile.model_fingerprint,
        "profile_fingerprint": profile.profile_fingerprint,
        "prompt_schema_version": profile.prompt_schema_version,
    }


def apply_arm(arm: dict) -> None:
    """Apply one arm's seat overrides to the local settings, nothing else."""
    for key, value in arm["overrides"].items():
        setattr(settings, OVERRIDE_TO_SETTING[key], value)


class _MissingSetting:
    """Sentinel: the setting did not exist before the arm touched it."""


@contextlib.contextmanager
def arm_overrides(arm: dict) -> Iterator[None]:
    """
    Apply one arm's seat overrides and always restore them afterwards.

    apply_arm mutates the process-global settings in place; without a
    restore, an arm that overrides JUDGE_MODEL_SEAT_1 leaks its candidate
    into every later consumer of this process (in the test runner, the
    alphabetically following test cases resolve the leaked model and refuse
    with a manifest mismatch). The measurement process itself exits after
    one slot, but the runner must not rely on that.
    """
    saved: dict[str, object] = {}
    for key in arm["overrides"]:
        name = OVERRIDE_TO_SETTING[key]
        try:
            saved[name] = getattr(settings, name)
        except AttributeError:
            saved[name] = _MissingSetting
    apply_arm(arm)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is _MissingSetting:
                with contextlib.suppress(AttributeError):
                    delattr(settings, name)
            else:
                setattr(settings, name, value)


def freeze_or_check_profiles(
    frozen_path: pathlib.Path, profiles: Sequence[judge.JudgeSeatProfile]
) -> dict:
    """
    Pin the resolved profiles of one arm, or refuse a drifted re-run.

    Freezing is per arm: arms legitimately differ in batch width, so the
    compared fields are the identity fields a mid-comparison alias retarget
    would change, plus the width this arm froze.
    """
    current = [profile_snapshot(profile) for profile in profiles]
    if not frozen_path.exists():
        payload = {"frozen_at": timezone.now().isoformat(), "seats": current}
        _write_json(frozen_path, payload)
        return payload
    frozen = _read_json(frozen_path)
    mismatches = []
    for old, new in zip(frozen["seats"], current, strict=True):
        for key in (
            "profile_fingerprint",
            "alias_revision",
            "upstream_model",
            "batch_size",
            "reasoning",
            "response_format",
        ):
            if old.get(key) != new.get(key):
                mismatches.append(
                    f"seat {new['seat']}: {key} {old.get(key)!r} -> {new.get(key)!r}"
                )
    if mismatches:
        raise RunnerError(
            "resolved profiles drifted from the frozen snapshot; the block "
            "would be incomparable. Re-register the experiment. "
            + "; ".join(mismatches)
        )
    return frozen


def load_slot_units(
    manifest: dict, records: Sequence[CorpusRecord], split: dict[str, str]
) -> tuple[list[Unit], list[dict]]:
    """
    Load the QA-database units for the dev split and verify frozen texts.

    Every mismatch aborts the run before any POST: a frozen scope that no
    longer matches the QA database is not the registered experiment. Empty
    and incomplete translations are excluded with an explicit record, before
    any LLM is involved.
    """
    problems: list[str] = []
    units: list[Unit] = []
    excluded: list[dict] = []
    cache: dict[tuple[str, str], dict[int, Unit]] = {}
    for record in records:
        if split.get(record.record_id) != "dev":
            continue
        key = (record.project_slug, record.component_slug)
        if key not in cache:
            scoped = [
                item
                for item in records
                if item.project_slug == record.project_slug
                and item.component_slug == record.component_slug
            ]
            found = Unit.objects.filter(
                translation__component__project__slug=record.project_slug,
                translation__component__slug=record.component_slug,
                id__in=[item.unit_id for item in scoped],
            ).prefetch()
            cache[key] = {unit.id: unit for unit in found}
        unit = cache[key].get(record.unit_id)
        if unit is None:
            problems.append(f"{record.record_id}: unit {record.unit_id} not in QA DB")
            continue
        if unit.state < STATE_TRANSLATED:
            excluded.append(
                {"record_id": record.record_id, "reason": f"state={unit.state}"}
            )
            continue
        if not join_plural(unit.get_target_plurals()).strip():
            excluded.append({"record_id": record.record_id, "reason": "empty-target"})
            continue
        if unit.source != record.source:
            problems.append(f"{record.record_id}: source drifted from the corpus")
            continue
        if join_plural(unit.get_target_plurals()) != record.target:
            problems.append(f"{record.record_id}: target drifted from the corpus")
            continue
        units.append(unit)
    if problems:
        raise RunnerError(
            "frozen scope does not match the QA database; nothing was sent:\n  "
            + "\n  ".join(problems)
        )
    if not units:
        dev_records = [
            record for record in records if split.get(record.record_id) == "dev"
        ]
        msg = (
            "no dev-split units resolved for this slot: "
            f"{len(dev_records)} dev records, exclusions {excluded!r}, "
            f"split keys {sorted(split)[:5]!r}, "
            f"record ids {[record.record_id for record in records][:5]!r}"
        )
        raise RunnerError(msg)
    return units, excluded


def attribute_attempts(
    started: datetime,
    ended: datetime,
    profiles: Sequence[judge.JudgeSeatProfile],
) -> list[JudgeRequestAttempt]:
    """
    Attribute attempt rows to one block without PK watermarks or global joins.

    The block window plus each seat's resolved identity is exact in an
    isolated QA database: nothing else writes to these tables.
    """
    query = Q()
    for profile in profiles:
        query |= Q(
            seat=profile.seat,
            model=profile.model,
            profile_fingerprint=profile.profile_fingerprint,
            endpoint_fingerprint=profile.endpoint_fingerprint,
        )
    return list(
        JudgeRequestAttempt.objects.filter(query)
        .filter(created_at__gte=started, created_at__lte=ended)
        .order_by("pk")
    )


def _safe_attempt_row(row: JudgeRequestAttempt) -> dict:
    return {
        "id": row.pk,
        "seat": row.seat,
        "model": row.model,
        "attempt": row.attempt,
        "batch_size": row.batch_size,
        "batch_digest": row.batch_digest,
        "parsed": row.parsed,
        "failure_kind": row.failure_kind,
        "http_status": row.http_status,
        "finish_reason": row.finish_reason,
        "elapsed_ms": row.elapsed_ms,
        "first_byte_ms": row.first_byte_ms,
        "response_bytes": row.response_bytes,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "response_id": row.response_id,
    }


def _safe_verdict_row(row: JudgeVerdict) -> dict:
    return {
        "unit_id": row.unit_id,
        "seat": row.seat,
        "attempt": row.attempt,
        "request_round": row.request_round,
        "model_verdict": row.model_verdict,
        "max_severity": row.max_severity,
        "unparsed": row.unparsed,
        "failure_kind": (
            row.request_attempt.failure_kind if row.request_attempt else ""
        ),
        "errors": [
            {"category": error.get("category"), "severity": error.get("severity")}
            for error in row.errors
            if isinstance(error, dict)
        ],
        "judge_model": row.judge_model,
        "profile_fingerprint": row.profile_fingerprint,
    }


def guard_summary(guard: PostGuard) -> dict:
    return {
        "posts_sent": guard.attempts,
        "reserved_usd": str(guard.reserved.quantize(Decimal("0.000001"))),
        "settled_usd": str(guard.spent.quantize(Decimal("0.000001"))),
        "unmetered_responses": guard.unmetered,
        "tripped": guard.tripped,
    }


def _snapshot_units(units: Sequence[Unit]) -> dict[int, list]:
    return {
        unit.id: [join_plural(unit.get_target_plurals()), unit.state] for unit in units
    }


def run_block(
    manifest: dict,
    slot: dict,
    units: list[Unit],
    profiles: tuple[judge.JudgeSeatProfile, ...],
    guard: PostGuard,
    journal: Journal,
) -> dict:
    """
    Run one schedule slot through the real run_judge_batch.

    The call keeps exactly the kwargs the plan pins: no MT, no candidates, no
    verdict cache, no writable strings. Only audit-ledger rows are written,
    and only to the QA database.
    """
    # Fresh QA state per slot (plan: "каждый повтор — свежий QA state").
    # JudgeAdaptiveState persists per (endpoint, model, seat) across runs:
    # without a reset, an earlier slot's budget leaks into the next arm and
    # silently collapses the width this slot is supposed to measure (A5
    # shrunk to width 1 by A0's leftover state is not a batch=5 result).
    # Resetting only opens the slot at the arm's requested width; the
    # adaptive policy itself keeps running unchanged inside the slot.
    for profile in profiles:
        JudgeAdaptiveState.objects.update_or_create(
            endpoint_fingerprint=profile.endpoint_fingerprint,
            model=profile.model,
            seat=profile.seat,
            defaults={
                "batch_budget": profile.batch_size,
                "clean_attempt_streak": 0,
            },
        )
    run_uuid = uuid_module.uuid4()
    block_id = slot["id"]
    expected_digests: dict[int, list[str]] = {}
    requests = [build_request(unit) for unit in units]
    for profile in profiles:
        expected_digests[profile.seat] = [
            judge._batch_digest(requests[start : start + profile.batch_size])
            for start in range(0, len(requests), profile.batch_size)
        ]
    before = _snapshot_units(units)
    journal.append(
        {
            "event": "block_start",
            "slot": block_id,
            "arm": slot["arm"],
            "repeat": slot["repeat"],
            "group": slot["group"],
            "run_id": str(run_uuid),
            "unit_ids": [unit.id for unit in units],
            "expected_batch_digests": expected_digests,
            "seats": [profile_snapshot(profile) for profile in profiles],
        }
    )
    batches_seen: list[dict] = []

    def on_batch(requests, results) -> None:
        batches_seen.append(
            {
                "size": len(requests),
                "unit_keys": [request.unit_key for request in requests],
                "parsed": [not result.unparsed for result in results],
            }
        )

    started_wall = timezone.now()
    started_mono = time.monotonic()
    try:
        run_judge_batch(
            units,
            writable_ids=set(),
            user=None,
            use_cache=False,
            candidate_severities=(),
            mutating_repairs=False,
            evidence_run_id=run_uuid,
            on_batch=on_batch,
        )
    except BaseException as error:
        elapsed = time.monotonic() - started_mono
        attempts = attribute_attempts(started_wall, timezone.now(), profiles)
        journal.append(
            {
                "event": "slot_interrupted",
                "slot": block_id,
                "error_class": type(error).__name__,
                "error_message": str(error)[:500],
                "guard_tripped": guard.tripped,
                "posts_sent": guard.attempts,
                "attempts_recorded": len(attempts),
                "wall_clock_seconds": round(elapsed, 3),
            }
        )
        _write_json(
            _artifact_dir(manifest) / f"results/interrupted-{block_id}.json",
            {
                "slot": block_id,
                "run_id": str(run_uuid),
                "error_class": type(error).__name__,
                "attempts": [_safe_attempt_row(row) for row in attempts],
                "guard": guard_summary(guard),
            },
        )
        raise
    elapsed = time.monotonic() - started_mono
    ended_wall = timezone.now()
    attempts = attribute_attempts(started_wall, ended_wall, profiles)
    verdicts = list(
        JudgeVerdict.objects.filter(run_id=run_uuid).order_by("unit_id", "seat")
    )
    usage_rows = list(
        LLMUsageLog.objects.filter(request_attempt_id__in=[row.pk for row in attempts])
    )
    snapshot_ok = _snapshot_units(units) == before
    result = {
        "schema": "judge-cost-latency-ab/block-v1",
        "slot": block_id,
        "arm": slot["arm"],
        "repeat": slot["repeat"],
        "group": slot["group"],
        "run_id": str(run_uuid),
        "wall_clock_seconds": round(elapsed, 3),
        "unit_ids": [unit.id for unit in units],
        "state_snapshot_ok": snapshot_ok,
        "attempts": [_safe_attempt_row(row) for row in attempts],
        "verdicts": [_safe_verdict_row(row) for row in verdicts],
        "usage": [
            {
                "model": row.model,
                "service": row.service,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "cached_tokens": row.cached_tokens,
                "reasoning_tokens": row.reasoning_tokens,
                "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
            }
            for row in usage_rows
        ],
        "batches": batches_seen,
        "guard": guard_summary(guard),
    }
    journal.append(
        {
            "event": "slot_end",
            "slot": block_id,
            "run_id": str(run_uuid),
            "posts_sent": guard.attempts,
            "attempts_recorded": len(attempts),
            "verdicts_recorded": len(verdicts),
            "wall_clock_seconds": round(elapsed, 3),
            "state_snapshot_ok": snapshot_ok,
        }
    )
    _write_json(_artifact_dir(manifest) / f"results/block-{block_id}.json", result)
    return result


def cmd_execute(args: argparse.Namespace) -> int:
    manifest = load_manifest(pathlib.Path(args.manifest), require_budget=True)
    budget = parse_budget(manifest)
    if os.environ.get("DJANGO_SETTINGS_MODULE") in FORBIDDEN_SETTINGS_MODULES:
        msg = (
            "execute must run against an isolated QA database "
            "(DJANGO_SETTINGS_MODULE=weblate.settings_test + CI_DB_*), never "
            "the dev-docker or production settings: background workers must "
            "not race the measurement"
        )
        raise RunnerError(msg)
    directory = _artifact_dir(manifest)
    (directory / "results").mkdir(mode=0o700, exist_ok=True)
    journal = Journal(directory / "journal.jsonl")
    states = journal.slots_by_state()
    slot = next(
        (s for s in manifest["schedule"]["slots"] if s["id"] == args.slot), None
    )
    if slot is None:
        msg = f"slot {args.slot!r} is not in the schedule"
        raise RunnerError(msg)
    if slot["id"] in states["completed"]:
        if args.resume:
            msg = (
                f"slot {slot['id']} is complete; --resume never repeats finished blocks"
            )
            raise RunnerError(msg)
        print(f"slot {slot['id']} already completed; nothing to do")
        return 0
    if states["interrupted"] and not args.resume:
        raise RunnerError(
            "interrupted slots exist ("
            + ", ".join(states["interrupted"])
            + "); their outcome is unknown and the attempt reserve is spent. "
            "Re-running them is a new paid decision: pass --resume explicitly."
        )
    slots = manifest["schedule"]["slots"]
    position = next(
        index for index, item in enumerate(slots) if item["id"] == slot["id"]
    )
    last_completed = -1
    for index, item in enumerate(slots):
        if item["id"] in states["completed"]:
            last_completed = index
    if last_completed != position - 1 and not args.allow_out_of_order:
        msg = (
            f"slot {slot['id']} is out of schedule order (last completed slot "
            f"is #{last_completed + 1}); pass --allow-out-of-order to record "
            "the deviation explicitly"
        )
        raise RunnerError(msg)
    # The judge must never silently fall back mid-measurement: a fallback
    # attempt evaluates a different model, and a fallback arm is exactly what
    # experiment B screens.
    if judge.judge_fallback_endpoint() is not None:
        msg = (
            "a judge fallback endpoint is configured; measurements must run "
            "with JUDGE_FALLBACK_* unset so a failure is counted as a failure"
        )
        raise RunnerError(msg)
    if settings.JUDGE_DEFERRAL_ENABLED:
        msg = (
            "JUDGE_DEFERRAL_ENABLED must be false for the measurement: "
            "deferral rows would leave recoverable state behind"
        )
        raise RunnerError(msg)
    judge.validate_request_settings()
    records, _excluded = load_corpus(manifest)
    check_split_reproducible(manifest, records)
    split = _read_split_for_scope(manifest)
    units, excluded = load_slot_units(manifest, records, split)
    arm = manifest["arms"][slot["arm"]]
    overrides = arm["overrides"]
    with arm_overrides(arm):
        # Resolving profiles performs the alias capability GET on the LiteLLM
        # host; execute mode is the explicit permission for that refresh.
        profiles = judge.judge_seat_profiles()
        # The resolved widths must be the manifest's widths: a QA setting that
        # silently disagrees would measure a different experiment.
        expected_widths = {
            1: manifest["seat_1_batch_size"],
            2: overrides.get("seat_2_batch_size", manifest["seat_1_batch_size"]),
        }
        # Experiment B swaps the seat-1 model per arm; the resolved model must
        # be exactly the one the arm asks for (baseline when not overridden).
        expected_models = {
            1: overrides.get("seat_1_model", manifest["seat_models"]["seat_1"]),
            2: manifest["seat_models"]["seat_2"],
        }
        for profile in profiles:
            expected = expected_widths[profile.seat]
            if profile.batch_size != expected:
                msg = (
                    f"resolved seat {profile.seat} batch width "
                    f"{profile.batch_size} != manifest width {expected}; align "
                    "the QA settings (JUDGE_BATCH_SIZE / "
                    "JUDGE_BATCH_SIZE_SEAT_2) with the manifest"
                )
                raise RunnerError(msg)
            expected_model = expected_models[profile.seat]
            if profile.model != expected_model:
                msg = (
                    f"resolved seat {profile.seat} model {profile.model!r} != "
                    f"manifest model {expected_model!r}; align the QA settings "
                    "(JUDGE_MODEL_SEAT_1 / JUDGE_MODEL_SEAT_2) with the manifest"
                )
                raise RunnerError(msg)
        frozen = freeze_or_check_profiles(
            directory / f"frozen-profiles-{slot['arm']}.json", profiles
        )
        guard = PostGuard(
            budget=budget,
            prices=manifest["prices"],
            wall_deadline=time.monotonic()
            + budget.max_wall_clock_minutes_per_slot * 60,
        )
        journal.append(
            {
                "event": "slot_start",
                "slot": slot["id"],
                "arm": slot["arm"],
                "repeat": slot["repeat"],
                "group": slot["group"],
                "units": len(units),
                "excluded_before_llm": excluded,
                "resumed": bool(args.resume),
                "frozen_profiles_at": frozen.get("frozen_at"),
            }
        )
        guard.install()
        try:
            result = run_block(manifest, slot, units, profiles, guard, journal)
        except BudgetExceeded:
            # In-flight POSTs completed and are journaled by run_block; no new
            # POST may start after the guard tripped.
            print(f"GUARD TRIPPED: {guard.tripped}")
            return 3
        finally:
            guard.uninstall()
    print(
        f"slot {slot['id']} complete: {result['guard']['posts_sent']} POSTs, "
        f"wall clock {result['wall_clock_seconds']}s, "
        f"verdicts {len(result['verdicts'])}, "
        f"state snapshot ok: {result['state_snapshot_ok']}"
    )
    if result["guard"]["tripped"]:
        print(f"GUARD TRIPPED: {result['guard']['tripped']}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# Offline summarize and gates
# ---------------------------------------------------------------------------


def pair_severity(rows: Sequence[dict]) -> str:
    """Current max-severity policy; an unparsed seat means no coverage."""
    if any(row["unparsed"] for row in rows):
        return "unparsed"
    return max(
        (row["max_severity"] for row in rows),
        key=lambda severity: SEVERITY_RANK.get(severity, 0),
    )


def quality_metrics(blocks: list[dict], gold_by_unit: dict[int, CorpusRecord]) -> dict:
    """
    Quality against human labels; failed rows stay in the denominator.

    A conservative reading treats a missing or unparsed seat as no detection:
    for a gold defect that is a miss, never a silent exclusion.
    """
    per_unit: dict[int, dict] = {}
    for block in blocks:
        for row in block["verdicts"]:
            per_unit.setdefault(row["unit_id"], {})[row["seat"]] = row
    misses_critical = 0
    misses_major = 0
    false_flags = 0
    false_critical = 0
    disagreements = 0
    unique_by_seat: dict[int, set[int]] = {1: set(), 2: set()}
    per_language: dict[str, dict] = {}
    per_family: dict[str, dict] = {}
    for unit_id, seats in per_unit.items():
        record = gold_by_unit.get(unit_id)
        if record is None:
            continue
        language_bucket = per_language.setdefault(
            record.language,
            {"defects": 0, "clean": 0, "misses_major": 0, "false_flags": 0},
        )
        family_bucket = per_family.setdefault(
            record.family, {"defects": 0, "major_recalled": 0, "clean": 0, "flagged": 0}
        )
        pair = pair_severity(list(seats.values()))
        pair_rank = 0 if pair in {"unparsed", "none"} else SEVERITY_RANK[pair]
        if record.label == "defect":
            language_bucket["defects"] += 1
            family_bucket["defects"] += 1
            if record.severity == "critical" and pair_rank < 3:
                misses_critical += 1
            if SEVERITY_RANK[record.severity] >= 2 and pair_rank < 2:
                misses_major += 1
                language_bucket["misses_major"] += 1
            if pair_rank >= 2:
                family_bucket["major_recalled"] += 1
        else:
            language_bucket["clean"] += 1
            family_bucket["clean"] += 1
            if pair_rank >= 1:
                false_flags += 1
                language_bucket["false_flags"] += 1
                family_bucket["flagged"] += 1
                if pair_rank >= 3:
                    false_critical += 1
        seat_flags = {
            seat: (row["max_severity"] if not row["unparsed"] else "unparsed")
            for seat, row in seats.items()
        }
        if len(set(seat_flags.values())) > 1:
            disagreements += 1
        for seat, severity in seat_flags.items():
            if record.label == "defect" and SEVERITY_RANK.get(severity, 0) >= 1:
                unique_by_seat[seat].add(unit_id)
    unique_1, unique_2 = unique_by_seat[1], unique_by_seat[2]
    return {
        "critical_misses": misses_critical,
        "major_misses": misses_major,
        "false_flags": false_flags,
        "false_critical": false_critical,
        "seat_disagreements": disagreements,
        "unique_defects_seat_1": len(unique_1 - unique_2),
        "unique_defects_seat_2": len(unique_2 - unique_1),
        "per_language": per_language,
        "per_family": per_family,
    }


def summarize_arm(blocks: list[dict], gold_by_unit: dict[int, CorpusRecord]) -> dict:
    attempts = [row for block in blocks for row in block["attempts"]]
    verdicts = [row for block in blocks for row in block["verdicts"]]
    usage = [row for block in blocks for row in block["usage"]]
    elapsed = [row["elapsed_ms"] for row in attempts if row["elapsed_ms"] is not None]
    widths: dict[int, dict[int, int]] = {}
    for row in attempts:
        seat_widths = widths.setdefault(row["seat"], {})
        seat_widths[row["batch_size"]] = seat_widths.get(row["batch_size"], 0) + 1
    unparsed = [row for row in verdicts if row["unparsed"]]
    strings = len({row["unit_id"] for row in verdicts})
    tokens = {
        "prompt": sum(row["prompt_tokens"] or 0 for row in usage),
        "completion": sum(row["completion_tokens"] or 0 for row in usage),
        "cached": sum(row["cached_tokens"] or 0 for row in usage),
        "reasoning": sum(row["reasoning_tokens"] or 0 for row in usage),
    }
    reported = sum(
        (Decimal(row["cost_usd"]) for row in usage if row["cost_usd"] is not None),
        Decimal(0),
    )
    per_thousand = (
        (reported / Decimal(strings) * 1000).quantize(Decimal("0.0001"))
        if strings
        else None
    )
    unmetered_attempts = sum(1 for row in attempts if row["prompt_tokens"] is None)
    return {
        "blocks": [block["slot"] for block in blocks],
        "wall_clock_seconds": round(
            sum(block["wall_clock_seconds"] for block in blocks), 3
        ),
        "attempts": len(attempts),
        "unmetered_attempts": unmetered_attempts,
        "effective_batch_distribution": {
            str(seat): widths.get(seat, {}) for seat in sorted(widths)
        },
        "failure_kinds": dict(
            sorted(Counter(row["failure_kind"] for row in unparsed).items())
        ),
        "http_elapsed_ms": {
            "p50": _percentile(elapsed, 0.5),
            "p95": _percentile(elapsed, 0.95),
        },
        "verdicts": len(verdicts),
        "unparsed": len(unparsed),
        "unique_strings": strings,
        "tokens": tokens,
        "cost_reported_usd": str(reported.quantize(Decimal("0.0001"))),
        "cost_per_1000_strings_usd": str(per_thousand) if per_thousand else None,
        "quality": quality_metrics(blocks, gold_by_unit),
    }


def _bootstrap_delta(
    control: dict, candidate: dict, seed: int
) -> dict[str, list[float]]:
    """
    Paired family bootstrap of recall and false-flag deltas.

    Families are the resampling unit because variants of one source string
    are dependent. Deterministic under the manifest seed.
    """
    families = sorted(set(control) & set(candidate))
    if not families:
        return {}
    rng = random.Random(seed)
    deltas = {"major_recall": [], "false_flags": []}
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [families[rng.randrange(len(families))] for _ in families]
        recall_c = sum(control[name]["major_recalled"] for name in sample)
        defects_c = sum(control[name]["defects"] for name in sample)
        recall_a = sum(candidate[name]["major_recalled"] for name in sample)
        defects_a = sum(candidate[name]["defects"] for name in sample)
        flags_c = sum(control[name]["flagged"] for name in sample)
        clean_c = sum(control[name]["clean"] for name in sample)
        flags_a = sum(candidate[name]["flagged"] for name in sample)
        clean_a = sum(candidate[name]["clean"] for name in sample)
        rate_c = recall_c / defects_c if defects_c else 0.0
        rate_a = recall_a / defects_a if defects_a else 0.0
        ff_c = flags_c / clean_c if clean_c else 0.0
        ff_a = flags_a / clean_a if clean_a else 0.0
        deltas["major_recall"].append(rate_a - rate_c)
        deltas["false_flags"].append(ff_a - ff_c)
    return deltas


def evaluate_gates(manifest: dict, control: dict, candidate: dict, seed: int) -> dict:
    """Preregistered gates; inconclusive when the paired bound cannot decide."""
    gates = manifest["gates"]
    quality_c = control["quality"]
    quality_a = candidate["quality"]
    critical = quality_a["critical_misses"] - quality_c["critical_misses"]
    unparsed_c = control["unparsed"] / max(control["verdicts"], 1)
    unparsed_a = candidate["unparsed"] / max(candidate["verdicts"], 1)
    results: dict = {
        "critical_misses_delta": critical,
        "critical_misses_ok": critical <= 0,
        "terminal_unparsed_growth_pp": round((unparsed_a - unparsed_c) * 100, 3),
        "terminal_unparsed_ok": (
            (unparsed_a - unparsed_c) * 100 <= gates["terminal_unparsed_growth_pp"]
        ),
    }
    deltas = _bootstrap_delta(quality_c["per_family"], quality_a["per_family"], seed)
    if deltas:
        recall_deltas = sorted(deltas["major_recall"])
        flag_deltas = sorted(deltas["false_flags"])
        recall_low = recall_deltas[int(0.05 * len(recall_deltas))]
        flag_high = flag_deltas[min(len(flag_deltas) - 1, int(0.95 * len(flag_deltas)))]
        results["major_recall_delta_low95_pp"] = round(recall_low * 100, 3)
        results["major_recall_ok"] = recall_low * 100 >= -gates["major_recall_drop_pp"]
        results["false_flags_delta_high95_pp"] = round(flag_high * 100, 3)
        results["false_flags_ok"] = flag_high * 100 <= gates["false_flag_growth_pp"]
        results["verdict"] = (
            "pass"
            if all(
                results[key]
                for key in (
                    "critical_misses_ok",
                    "terminal_unparsed_ok",
                    "major_recall_ok",
                    "false_flags_ok",
                )
            )
            else "fail"
        )
    else:
        results["verdict"] = "inconclusive"
    return results


def cmd_summarize(args: argparse.Namespace) -> int:
    manifest = load_manifest(pathlib.Path(args.manifest), require_budget=False)
    directory = _artifact_dir(manifest)
    records, _excluded = load_corpus(manifest)
    split = _read_split_for_scope(manifest)
    gold_by_unit = {
        record.unit_id: record
        for record in records
        if split.get(record.record_id) == "dev"
    }
    blocks = sorted((directory / "results").glob("block-*.json"))
    if not blocks:
        msg = "no completed block results to summarize"
        raise RunnerError(msg)
    by_arm: dict[str, list[dict]] = {}
    for path in blocks:
        block = _read_json(path)
        by_arm.setdefault(block["arm"], []).append(block)
    summary: dict = {
        "schema": "judge-cost-latency-ab/summary-v1",
        "experiment_id": manifest["experiment_id"],
        "arms": {},
    }
    for arm_id, arm_blocks in sorted(by_arm.items()):
        summary["arms"][arm_id] = summarize_arm(arm_blocks, gold_by_unit)
    control = manifest["gates"].get("control_arm", next(iter(summary["arms"])))
    for arm_id, arm_summary in summary["arms"].items():
        if arm_id == control:
            continue
        arm_summary["gates"] = evaluate_gates(
            manifest,
            summary["arms"][control],
            arm_summary,
            manifest["corpus"]["split_seed"],
        )
    _write_json(directory / "summary.json", summary)
    print(f"wrote {directory / 'summary.json'}")
    for arm_id, arm_summary in summary["arms"].items():
        print(f"\narm {arm_id}:")
        for key, value in arm_summary.items():
            if key in {"quality", "gates"}:
                continue
            print(f"  {key}: {value}")
        print(f"  quality: {json.dumps(arm_summary['quality'], sort_keys=True)}")
        if arm_summary.get("gates") is not None:
            print(f"  gates: {json.dumps(arm_summary['gates'], sort_keys=True)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="split a labeled corpus")
    mode.add_argument(
        "--dry-run", action="store_true", help="offline scope and estimate"
    )
    mode.add_argument(
        "--execute", action="store_true", help="run one PAID schedule slot"
    )
    mode.add_argument(
        "--summarize", action="store_true", help="offline recompute of tables"
    )
    parser.add_argument("--manifest", help="path to manifest.json")
    parser.add_argument("--corpus", help="labeled corpus JSON (prepare mode)")
    parser.add_argument("--experiment-id", help="slug for a new experiment")
    parser.add_argument("--arms", help="arms JSON object (prepare mode)")
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--held-out-fraction", type=float, default=0.5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seat-1-model", default="unset-seat-1")
    parser.add_argument("--seat-2-model", default="unset-seat-2")
    parser.add_argument("--slot", help="schedule slot id for --execute")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-out-of-order", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare:
        if not args.corpus or not args.experiment_id:
            parser.error("--prepare needs --corpus and --experiment-id")
        return cmd_prepare(args)
    if args.execute:
        if not args.manifest or not args.slot:
            parser.error("--execute needs --manifest and --slot")
        return cmd_execute(args)
    if args.summarize:
        if not args.manifest:
            parser.error("--summarize needs --manifest")
        return cmd_summarize(args)
    # Dry-run is the default mode.
    if not args.manifest:
        parser.error("--dry-run (default) needs --manifest")
    return cmd_dry_run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BudgetExceeded as error:
        print(f"GUARD TRIPPED: {error}", file=sys.stderr)
        sys.exit(3)
    except (RunnerError, judge.JudgeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
