# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import DomainError

DISPOSITIONS = (
    "ready",
    "never_sent",
    "waiting",
    "paid_unknown",
    "paid_invalid",
    "stale",
    "forbidden",
)


def reconcile_mt_receipt(receipt, rows, expected_scope):
    """Accept only a complete, disjoint server proof for the original scope."""
    results = receipt.get("units")
    groups = receipt.get("reconciliation")
    if (
        not isinstance(results, list)
        or not isinstance(groups, dict)
        or set(groups) != set(DISPOSITIONS)
        or len(results) != len(expected_scope)
        or any(
            not isinstance(row, dict)
            or type(row.get("unit_id")) is not int
            or row["unit_id"] not in expected_scope
            or row.get("revision") != expected_scope[row["unit_id"]]
            for row in results
        )
        or {row["unit_id"] for row in results} != set(expected_scope)
    ):
        raise DomainError("weblate_schema", "Invalid MT reconciliation scope", 502)
    seen = set()
    current = {row["id"]: row for row in rows}
    for disposition in DISPOSITIONS:
        members = groups[disposition]
        if not isinstance(members, list):
            raise DomainError("weblate_schema", "Invalid MT reconciliation group", 502)
        for ident in members:
            if type(ident) is not int or ident not in expected_scope or ident in seen:
                raise DomainError("weblate_schema", "Overlapping MT reconciliation groups", 502)
            seen.add(ident)
            row = current.get(ident)
            if row is None or (disposition == "ready" and not any(row["target"])):
                raise DomainError(
                    "weblate_schema", "Reconciliation does not match current rows", 502
                )
            if disposition == "never_sent":
                from app.services.mt_batches import mt_unit_revision

                if (
                    any(row["target"])
                    or row["state"] != 0
                    or mt_unit_revision(row) != expected_scope[ident]
                ):
                    raise DomainError("weblate_schema", "Unsent proof has changed", 502)
    if seen != set(expected_scope):
        raise DomainError("weblate_schema", "Incomplete MT reconciliation", 502)
    return {name: list(groups[name]) for name in DISPOSITIONS}
