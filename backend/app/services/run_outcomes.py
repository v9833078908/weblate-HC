# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def run_outcomes(data):
    scopes = data.get("mt_batches", {}).values()
    filled = sum(scope.get("filled", 0) for scope in scopes)
    total = sum(scope.get("total", 0) for scope in scopes)
    if data.get("mode") == "quality_only" and data.get("quality_input_coverage"):
        coverage = data["quality_input_coverage"]
        filled, total = coverage["filled"], coverage["total"]
    unknown = {ident for scope in scopes for ident in scope.get("payment_unknown", [])}
    exceptions = any(scope.get("exceptions") for scope in scopes)
    quality = data.get("quality", {})
    counts = quality.get("counts", {})
    judged_total = counts.get("processed", 0)
    without_pass = counts.get("not_verified", judged_total)
    parsed = counts.get("both_parsed")
    if parsed is None:
        parsed = sum(
            all(row.get("live_seat_results", {}).get(str(seat)) == "parsed" for seat in (1, 2))
            for row in quality.get("units", [])
        )
    return {
        "translation_coverage": {
            "filled": filled,
            "total": total,
            "missing": max(0, total - filled),
        },
        "judge_coverage": {
            "both_parsed": parsed,
            "pass": max(0, judged_total - without_pass),
            "without_pass": without_pass,
            "total": judged_total,
        },
        "payment_outcome": {
            "scope": "machine_translation",
            "unknown": len(unknown),
            "automatic_paid_replay_allowed": False,
        },
        "quality_outcome": quality.get("status", "not_started"),
        "has_exceptions": bool(
            exceptions
            or unknown
            or filled < total
            or quality.get("status") == "completed_with_exceptions"
        ),
    }
