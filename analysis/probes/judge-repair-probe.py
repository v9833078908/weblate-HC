#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Measure the judge repair loop on fresh machine translation.

Plan: docs/llm-first/plans/2026-08-25-judge-repair-loop-measurement.md

Runs inside the dev container, against a throwaway component built from
`analysis/data/st2-zh-units.jsonl`. The zh_Hans translation is created
empty on purpose: `judge_loop.py:299` repairs only writable units, and
`autotranslate.py:754-758` makes an already translated unit unwritable,
so a repair can only ever be observed on text the same run produced.

Stages, each idempotent and separately invocable:

    setup     build the local repo and the component
    run       execute the shipped judge path once, record everything
    pairs     emit the blind review sheet for the human reviewer
    score     combine the returned labels with the run into metrics

The verdict of the judge is recorded as exposure and as a description of
the machine state. It is never read as evidence about text quality; that
comes from `pairs` -> human -> `score` only.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import django

if TYPE_CHECKING:
    from collections.abc import Iterable

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings_docker")
django.setup()

from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from weblate.auth.models import User  # noqa: E402
from weblate.lang.models import Language  # noqa: E402
from weblate.trans.autotranslate import AutoTranslate  # noqa: E402
from weblate.trans.models import (  # noqa: E402
    Change,
    Component,
    LLMUsageLog,
    Project,
    Translation,
)
from weblate.trans.models.judge import JudgeVerdict  # noqa: E402

PROJECT_SLUG = "judge-repair-probe"
COMPONENT_SLUG = "st2-summer-update"
TARGET_LANG = "zh_Hans"
SOURCE_LANG = "ru"
ENGINE = "openrouter"
SEED = 20260825


def data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def out_dir() -> Path:
    path = data_dir() / "judge-repair-2026-08-25"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_corpus() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (data_dir() / "st2-zh-units.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    if len({row["context"] for row in rows}) != len(rows):
        msg = "corpus keys are not unique; the key is the po-mono msgid"
        raise SystemExit(msg)
    return rows


# --------------------------------------------------------------------- setup


def write_po(path: Path, rows: Iterable[dict[str, Any]], *, language: str, key: str):
    """Write a monolingual PO: msgid is the key, msgstr is the text."""
    from translate.storage.po import pofile

    store = pofile()
    store.updateheader(
        add=True,
        Language=language,
        Content_Type="text/plain; charset=UTF-8",
        Plural_Forms="nplurals=1; plural=0;",
    )
    for row in rows:
        unit = store.UnitClass(row["context"])
        unit.target = row[key] if key else ""
        store.addunit(unit)
    path.write_bytes(bytes(store))


def stage_setup(*, recreate: bool) -> None:
    rows = load_corpus()
    repo = Path(settings.DATA_DIR) / "judge-repair-probe-repo"

    if Project.objects.filter(slug=PROJECT_SLUG).exists():
        if not recreate:
            print("project exists; pass --recreate to rebuild")
            return
        Project.objects.get(slug=PROJECT_SLUG).delete()

    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=True)
    repo.mkdir(parents=True)

    write_po(repo / f"{SOURCE_LANG}.po", rows, language=SOURCE_LANG, key="source")
    # The target starts empty: an untranslated unit is the only unit the
    # shipped path will ever repair.
    write_po(repo / f"{TARGET_LANG}.po", rows, language=TARGET_LANG, key="")

    env = {**os.environ, "GIT_AUTHOR_NAME": "probe", "GIT_AUTHOR_EMAIL": "p@example.org"}
    env["GIT_COMMITTER_NAME"] = "probe"
    env["GIT_COMMITTER_EMAIL"] = "p@example.org"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "corpus"], check=True, env=env
    )

    project = Project.objects.create(
        name="Judge repair probe",
        slug=PROJECT_SLUG,
        # The measurement never exports; review off keeps state_for_verdict
        # on the same branch the production plan describes.
        translation_review=False,
    )
    component = Component.objects.create(
        project=project,
        name="ST2 summer update",
        slug=COMPONENT_SLUG,
        vcs="git",
        repo=f"file://{repo}",
        push="",
        branch="main",
        filemask="*.po",
        template=f"{SOURCE_LANG}.po",
        file_format="po-mono",
        source_language=Language.objects.get(code=SOURCE_LANG),
        new_lang="none",
        manage_units=False,
    )
    # Translations are discovered as the component finishes creating; the
    # first read right after `create()` can still miss them.
    translation = None
    for _ in range(60):
        translation = component.translation_set.filter(
            language__code=TARGET_LANG
        ).first()
        if translation is not None:
            break
        time.sleep(2)
    if translation is None:
        msg = f"{TARGET_LANG} was never discovered; check the filemask"
        raise SystemExit(msg)
    print(
        f"created {component.full_slug}: "
        f"{component.source_translation.unit_set.count()} source units, "
        f"{translation.unit_set.count()} {TARGET_LANG} units, "
        f"translated={translation.unit_set.filter(state__gte=20).count()}"
    )


def get_translation() -> Translation:
    return Translation.objects.get(
        component__project__slug=PROJECT_SLUG,
        component__slug=COMPONENT_SLUG,
        language__code=TARGET_LANG,
    )


# ----------------------------------------------------------------------- run


def _snapshot(
    translation: Translation,
    started,
    *,
    status: str,
    message: str | None,
    error: str | None,
    before: dict[int, dict[str, Any]],
    tag: str,
) -> Path:
    """Persist everything the run produced, successful or not.

    An aborted run is evidence too: the OpenRouter batch refusal of
    2026-08-25 is a shipped-path failure, and deleting the component
    would cascade its units, verdicts, changes and usage rows away.
    """
    changes = [
        {
            "unit": change.unit_id,
            "action": change.action,
            "action_name": str(change.get_action_display()),
            "old": change.old,
            "target": change.target,
            "at": change.timestamp.isoformat(),
        }
        for change in Change.objects.filter(
            translation=translation, timestamp__gte=started
        )
        .order_by("timestamp", "pk")
        .iterator()
    ]
    verdicts = [
        {
            "unit": verdict.unit_id,
            "seat": verdict.seat,
            "attempt": verdict.attempt,
            "run": str(verdict.run_id),
            "verdict": verdict.verdict,
            "judge_model": verdict.judge_model,
            "max_severity": verdict.max_severity,
            "unparsed": verdict.unparsed,
            "errors": verdict.errors,
            "created": verdict.timestamp.isoformat(),
        }
        for verdict in JudgeVerdict.objects.filter(
            unit__translation=translation, timestamp__gte=started
        )
        .order_by("unit_id", "attempt", "seat")
        .iterator()
    ]
    usage = [
        {
            "model": row.model,
            "batch_size": row.batch_size,
            "outcome": row.outcome,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "cost_usd": None if row.cost_usd is None else float(row.cost_usd),
            "at": row.created_at.isoformat(),
        }
        for row in LLMUsageLog.objects.filter(
            created_at__gte=started, project_slug=PROJECT_SLUG
        )
        .order_by("created_at")
        .iterator()
    ]
    finished = timezone.now()
    # A salvaged run has no honest clock: its window is reconstructed, so
    # timing is reported as unavailable rather than as a week-long run.
    salvaged = status == "collected"
    payload = {
        "plan": "docs/llm-first/plans/2026-08-25-judge-repair-loop-measurement.md",
        "status": status,
        "error": error,
        "message": message,
        "started": None if salvaged else started.isoformat(),
        "finished": None if salvaged else finished.isoformat(),
        "wall_seconds": None if salvaged else (finished - started).total_seconds(),
        "settings": {
            "seat_1": settings.JUDGE_MODEL_SEAT_1,
            "seat_2": settings.JUDGE_MODEL_SEAT_2,
            "max_repair_attempts": settings.JUDGE_MAX_REPAIR_ATTEMPTS,
            "may_approve": settings.JUDGE_MAY_APPROVE,
            "engine": ENGINE,
            "enable_review": translation.enable_review,
        },
        "before": before,
        "after": {
            unit.id: {"target": unit.target, "state": unit.state}
            for unit in translation.unit_set.all()
        },
        "changes": changes,
        "verdicts": verdicts,
        "usage": usage,
    }
    path = out_dir() / (f"run-{tag}.json" if tag else "run.json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return path


def stage_run(*, tag: str, collect_only: bool) -> None:
    translation = get_translation()
    user = User.objects.filter(is_superuser=True).order_by("pk").first()

    if collect_only:
        # Salvage a run that already happened: take everything the
        # component holds, with the pre-run target reconstructed as empty.
        before = {
            unit.id: {
                "key": unit.context,
                "source": unit.source,
                "target": "",
                "state": 0,
            }
            for unit in translation.unit_set.all()
        }
        # The window is the component's own earliest write, not a fixed
        # lookback, so no other project's usage row can drift in.
        earliest = min(
            filter(
                None,
                (
                    Change.objects.filter(translation=translation)
                    .order_by("timestamp")
                    .values_list("timestamp", flat=True)
                    .first(),
                    JudgeVerdict.objects.filter(unit__translation=translation)
                    .order_by("timestamp")
                    .values_list("timestamp", flat=True)
                    .first(),
                ),
            ),
            default=timezone.now(),
        )
        path = _snapshot(
            translation,
            earliest,
            status="collected",
            message=None,
            error="collected after the fact; `before` is reconstructed",
            before=before,
            tag=tag,
        )
        print(f"collected -> {path}")
        return

    started = timezone.now()
    before = {
        unit.id: {
            "key": unit.context,
            "source": unit.source,
            "target": unit.target,
            "state": unit.state,
        }
        for unit in translation.unit_set.all()
    }
    if any(row["target"] for row in before.values()):
        msg = "target is not empty; run setup --recreate first"
        raise SystemExit(msg)

    auto = AutoTranslate(
        translation=translation,
        user=user,
        q="",
        mode="judge",
        overwrite_existing=False,
    )
    status, message, error = "ok", None, None
    try:
        message = auto.perform(
            auto_source="mt",
            engines=[ENGINE],
            threshold=0,
            source_component_ids=None,
        )
        if auto.failure_message:
            status, error = "failed", auto.failure_message
    except BaseException as exc:  # noqa: BLE001 - evidence beats a clean exit
        status, error = "crashed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        path = _snapshot(
            translation,
            started,
            status=status,
            message=message,
            error=error,
            before=before,
            tag=tag,
        )
        print(f"status={status} error={error}\nwrote {path}")


# --------------------------------------------------------------------- pairs


def repair_pairs(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct pre-repair and post-repair text from the change log.

    Phase 1 writes the machine translation, the repair writes over it.
    Both are ordinary unit writes, so the persisted history carries the
    two texts without the probe patching anything in the judged path.
    """
    by_unit: dict[int, list[dict[str, Any]]] = {}
    for change in run["changes"]:
        if change["unit"] is None or not change["target"]:
            continue
        by_unit.setdefault(change["unit"], []).append(change)

    pairs = []
    for unit_id, writes in by_unit.items():
        if len(writes) < 2:
            continue
        pre, post = writes[0]["target"], writes[-1]["target"]
        if pre == post:
            continue
        before = run["before"][str(unit_id)]
        pairs.append(
            {
                "unit": unit_id,
                "key": before["key"],
                "source": before["source"],
                "pre": pre,
                "post": post,
                "final_state": run["after"][str(unit_id)]["state"],
            }
        )
    return sorted(pairs, key=lambda pair: pair["key"])


def stage_pairs(*, run_name: str = "run.json") -> None:
    run = json.loads((out_dir() / run_name).read_text(encoding="utf-8"))
    pairs = repair_pairs(run)
    rng = random.Random(SEED)

    blind, key = [], []
    for index, pair in enumerate(pairs, start=1):
        pair_id = f"P{index:03d}"
        swapped = rng.random() < 0.5
        x, y = (pair["post"], pair["pre"]) if swapped else (pair["pre"], pair["post"])
        blind.append((pair_id, pair["source"], x, y))
        key.append({**pair, "pair_id": pair_id, "x_is_post": swapped})

    # Game strings carry embedded newlines and the engine separator `$`;
    # a hand-joined TSV would split one pair across several records and
    # silently misalign every label that follows.
    sheet = out_dir() / "review-blind.tsv"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["pair_id", "source", "X", "Y", "X_faithful", "Y_faithful", "better"]
        )
        for pid, src, x, y in blind:
            writer.writerow([pid, src, x, y, "", "", ""])
    (out_dir() / "review-key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"{len(pairs)} changed pairs -> {sheet}\n"
        "Reviewer fills X_faithful/Y_faithful with yes|no and better with X|Y|equal."
    )


# --------------------------------------------------------------------- score


def upper_bound(events: int, total: int) -> float:
    """One-sided 95% Clopper-Pearson upper bound."""
    if total == 0:
        return 1.0
    if events == 0:
        return 1.0 - 0.05 ** (1.0 / total)

    def cdf(p: float) -> float:
        return sum(
            math.comb(total, k) * p**k * (1 - p) ** (total - k)
            for k in range(events + 1)
        )

    low, high = 0.0, 1.0
    for _ in range(200):
        mid = (low + high) / 2
        if cdf(mid) > 0.05:
            low = mid
        else:
            high = mid
    return high


def stage_score(*, run_name: str = "run.json") -> None:
    run = json.loads((out_dir() / run_name).read_text(encoding="utf-8"))
    key = {
        row["pair_id"]: row
        for row in json.loads(
            (out_dir() / "review-key.json").read_text(encoding="utf-8")
        )
    }
    labels_path = out_dir() / "review-labels.tsv"
    if not labels_path.exists():
        raise SystemExit(f"missing {labels_path}: the human stage has not returned")

    with labels_path.open(encoding="utf-8", newline="") as handle:
        rows = [
            row
            for row in list(csv.reader(handle, delimiter="\t"))[1:]
            if row and any(cell.strip() for cell in row)
        ]
    # A silent regression is text the judge itself cleared: the collegium
    # verdict of the LAST round must be `pass`. Taking the last round
    # alone would count a repaired-but-still-flagged unit as cleared,
    # which would skip R1 on a real silent regression.
    rank = {"pass": 0, "flag": 1, "reject": 2}
    rounds: dict[int, int] = {}
    for verdict in run["verdicts"]:
        unit = verdict["unit"]
        rounds[unit] = max(rounds.get(unit, 0), verdict["attempt"])
    strictest: dict[int, int] = {}
    parsed_seats: dict[int, int] = {}
    for verdict in run["verdicts"]:
        unit = verdict["unit"]
        if verdict["attempt"] != rounds[unit]:
            continue
        if verdict["unparsed"] or verdict["verdict"] not in rank:
            continue
        parsed_seats[unit] = parsed_seats.get(unit, 0) + 1
        strictest[unit] = max(strictest.get(unit, 0), rank[verdict["verdict"]])
    # `collegium_verdict` ignores an unparsed seat whenever another seat
    # parsed (`models/judge.py:323-332`), so PASS next to UNPARSED is a
    # shipped PASS and must stay eligible for a silent regression. Only a
    # round where every seat failed to parse carries no opinion.
    final_pass = {
        unit
        for unit, worst in strictest.items()
        if worst == 0 and parsed_seats.get(unit, 0) > 0
    }

    clean_total = clean_broken = silent = defect_total = fixed = style_total = 0
    style_worse = 0
    detail = []
    for row in rows:
        pair_id, _source, _x, _y, x_faithful, y_faithful, better = (row + [""] * 7)[:7]
        item = key.get(pair_id)
        if item is None:
            continue
        swap = item["x_is_post"]
        pre_ok = (y_faithful if swap else x_faithful).strip().lower() == "yes"
        post_ok = (x_faithful if swap else y_faithful).strip().lower() == "yes"
        choice = better.strip().upper()
        post_better = (choice == "X") == swap and choice in {"X", "Y"}

        if pre_ok:
            clean_total += 1
            if not post_ok:
                clean_broken += 1
                if item["unit"] in final_pass:
                    silent += 1
            elif choice in {"X", "Y"}:
                style_total += 1
                if not post_better:
                    style_worse += 1
        else:
            defect_total += 1
            if post_ok:
                fixed += 1
        detail.append(
            {"pair_id": pair_id, "pre_ok": pre_ok, "post_ok": post_ok,
             "post_better": post_better, "unit": item["unit"]}
        )

    result = {
        "regression": {
            "events": clean_broken,
            "total": clean_total,
            "upper95": round(upper_bound(clean_broken, clean_total), 4),
        },
        "silent_regression": {"events": silent, "total": clean_total},
        "yield": {
            "events": fixed,
            "total": defect_total,
            "upper95": round(upper_bound(fixed, defect_total), 4),
        },
        "style_regression": {"events": style_worse, "total": style_total},
        "verdict_gate": (
            "R1: FLAG back to non-repairable"
            if silent
            else "R2: FLAG stops blocking export"
            if clean_broken
            else "R3: policy retained provisionally, not certified"
        ),
        "detail": detail,
    }
    path = out_dir() / "metrics.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "detail"},
                     ensure_ascii=False, indent=1))
    print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("setup", "run", "pairs", "score"))
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument(
        "--tag", default="", help="suffix for run-<tag>.json; empty means run.json"
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="dump the state a previous run left behind without translating",
    )
    parser.add_argument(
        "--run", default="run.json", help="run file consumed by pairs and score"
    )
    args = parser.parse_args()

    if args.stage == "setup":
        stage_setup(recreate=args.recreate)
    elif args.stage == "run":
        stage_run(tag=args.tag, collect_only=args.collect_only)
    elif args.stage == "pairs":
        stage_pairs(run_name=args.run)
    else:
        stage_score(run_name=args.run)


if __name__ == "__main__":
    sys.exit(main())
