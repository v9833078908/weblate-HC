#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Dump per-unit comparison rows for the nightfall-spire de/zh_Hans arm study.

Run inside the Weblate container:

    docker exec hcgameloc-weblate-1 weblate shell \\
        -c "import base64;exec(base64.b64decode('<b64>').decode())"

Writes JSON to stdout: one row per (arm, language, sample key) with the current
judge verdict, the unit's active layer-0 checks and both texts. The sample is
computed here, deterministically, so the local analysis never depends on a
sample file that could drift from what was judged:

* judgeable sources only (must contain a letter),
* 100 keys per source-length band (<=20, 21-60, >60 characters),
* ordering by sha256("nightfall-2026-09-30" + context).

This is a read-only measurement probe.
"""

from __future__ import annotations

import hashlib
import json
import re

from weblate.trans.models import Unit
from weblate.trans.models.judge import current_verdict

SALT = "nightfall-2026-09-30"
ARMS = (("A", "strings"), ("B", "strings-arm-weblate"))
LANGUAGES = ("de", "zh_Hans")
LETTERS = re.compile(
    r"[A-Za-z\u00c0-\u024f\u0400-\u04ff\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0600-\u06ff]"
)


def band(text: str) -> str:
    size = len(text)
    if size <= 20:
        return "S"
    if size <= 60:
        return "M"
    return "L"


def build_sample() -> list[str]:
    """Build the 300 sample keys, in the same order the estimates were priced in."""
    template = Unit.objects.filter(
        translation__component__project__slug="nightfall-spire",
        translation__component__slug="strings",
        translation__language__code="en",
    ).values_list("context", "source")
    buckets: dict[str, list[tuple[str, str]]] = {"S": [], "M": [], "L": []}
    for context, source in template:
        if source and LETTERS.search(source):
            buckets[band(source)].append((context, source))
    sample: list[tuple[str, str]] = []
    for name in ("S", "M", "L"):
        pool = sorted(
            buckets[name],
            key=lambda item: hashlib.sha256((SALT + item[0]).encode()).hexdigest(),
        )
        sample.extend(pool[:100])
    return [context for context, _ in sample]


def dump() -> list[dict]:
    keys = build_sample()
    rows: list[dict] = []
    for arm, component in ARMS:
        for language in LANGUAGES:
            queryset = Unit.objects.filter(
                translation__component__project__slug="nightfall-spire",
                translation__component__slug=component,
                translation__language__code=language,
                context__in=keys,
            )
            by_context = {unit.context: unit for unit in queryset}
            for key in keys:
                unit = by_context.get(key)
                if unit is None:
                    rows.append(
                        {"arm": arm, "language": language, "key": key, "missing": True}
                    )
                    continue
                verdict = current_verdict(unit)
                rows.append(
                    {
                        "arm": arm,
                        "language": language,
                        "key": key,
                        "unit_id": unit.pk,
                        "source": unit.source,
                        "target": unit.target,
                        "state": unit.state,
                        "checks": sorted(check.name for check in unit.active_checks),
                        "severity": verdict.effective_severity if verdict else None,
                        "model_severity": verdict.max_severity if verdict else None,
                        "verdict": verdict.verdict if verdict else None,
                        "errors": verdict.errors if verdict else [],
                        "back_translation": verdict.back_translation if verdict else "",
                        "judge_model": verdict.judge_model if verdict else "",
                    }
                )
    return rows


print(json.dumps(dump(), ensure_ascii=False))
