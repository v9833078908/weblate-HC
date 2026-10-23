# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
from collections import Counter

from app.db import DomainError, revision
from app.services.exports import export_sheet_titles, reserved_export_columns
from app.services.kits import selected_header_language
from app.services.languages import normalized_code
from app.services.manifest import component_mapping_key, component_source, component_targets
from app.services.quality_policy import required_issue


def _invalid():
    return DomainError("quality_artifact_binding", "Quality report does not match the export", 409)


def immutable_record_id(store, owner, kind, base, value):
    from app.models import Record

    with store.session() as session:
        old = session.get(Record, base)
        if old is None:
            return base
        if old.owner != owner or old.kind != kind:
            raise DomainError("not_found", "Not found", 404)
        return base if old.revision == revision(value) else base + ":v-" + revision(value)[:24]


def _cells(manifest, exported, language):
    sheets = {sheet["id"]: sheet for sheet in exported["sheets"]}
    if len(sheets) != len(exported["sheets"]):
        raise _invalid()
    for component in manifest["components"]:
        if language not in component_targets(manifest, component):
            continue
        sheet_id = component.get("logical_sheet_id", component["id"])
        sheet = sheets.get(sheet_id)
        if sheet is None:
            raise _invalid()
        original = next((s for s in manifest.get("sheets", []) if s["id"] == sheet_id), component)
        profile = original["profile"]
        columns = {lang["code"]: lang["column"] for lang in profile["languages"]}
        column = columns.get(language)
        if column is None:
            header = sheet["rows"][component.get("header_row", profile.get("header_row", 1) - 1)]
            matches = [
                i
                for i, cell in enumerate(header, 1)
                if i not in columns.values()
                and i - 1 not in reserved_export_columns(profile)
                and selected_header_language(cell, {normalized_code(language): language})
                == language
            ]
            if len(matches) != 1:
                raise _invalid()
            column = matches[0]
        for unit in component["units"]:
            row = unit.get("original_row", unit["row"])
            if (
                type(row) is not int
                or type(column) is not int
                or row < 1
                or column < 1
                or row > len(sheet["rows"])
                or column > len(sheet["rows"][row - 1])
            ):
                raise _invalid()
            yield component, unit, sheet, row, column


def bind_quality_cells(manifest, exported, remote_snapshot, mapping, final_snapshot, language):
    """Join remote target identities to emitted cells, never by source text alone."""
    bindings = []
    for component, unit, sheet, row, column in _cells(manifest, exported, language):
        remote_name = mapping.get(component_mapping_key(manifest, component))
        remote = remote_snapshot.get("components", {}).get(remote_name)
        source = component_source(manifest, component)
        if (
            not remote
            or remote.get("complete") is not True
            or remote.get("source") != source
            or any(i.get("component") == remote_name for i in remote_snapshot.get("issues", []))
        ):
            raise _invalid()
        matches = [u for u in remote["units"] if u["key"] == unit["key"]]
        if len(matches) != 1:
            raise _invalid()
        current = matches[0]
        uid = current.get("target_ids", {}).get(language)
        rev = final_snapshot["revisions"].get(str(uid))
        if (
            type(uid) is not int
            or not rev
            or current.get("source") != unit["values"][source]
            or current.get("targets", {}).get(language) != sheet["rows"][row - 1][column - 1]
        ):
            raise _invalid()
        bindings.append(
            dict(
                unit_id=uid,
                component_id=component["id"],
                key=unit["key"],
                sheet_id=sheet["id"],
                row=row,
                column=column,
                language=language,
                revision=rev,
            )
        )
    if {b["unit_id"] for b in bindings} != set(final_snapshot["unit_ids"]):
        raise _invalid()
    return bindings


def build_quality_artifacts(
    *,
    run_id: str,
    export_id: str,
    exported: dict,
    manifest: dict,
    final_snapshot: dict,
    unit_results: list[dict],
    bindings: list[dict],
) -> dict:
    provenance = exported["quality_provenance"]
    language = provenance["language"]
    if final_snapshot.get("complete") is not True or any(
        final_snapshot.get(k) != provenance.get(k) for k in ("scope_revision", "snapshot_hash")
    ):
        raise _invalid()
    scope = set(final_snapshot["unit_ids"])
    expected = {
        (c["id"], u["key"], s["id"], row, col): (c, u, s)
        for c, u, s, row, col in _cells(manifest, exported, language)
    }
    if not scope or {b["unit_id"] for b in bindings} != scope:
        raise _invalid()
    seen, positions, cells = set(), {}, []
    titles = export_sheet_titles(exported)
    for binding in bindings:
        key = (
            binding["component_id"],
            binding["key"],
            binding["sheet_id"],
            binding["row"],
            binding["column"],
        )
        uid = binding["unit_id"]
        if (
            key not in expected
            or key in seen
            or binding["language"] != language
            or binding["revision"] != final_snapshot["revisions"].get(str(uid))
        ):
            raise _invalid()
        seen.add(key)
        coord = key[2:]
        if coord in positions and positions[coord] != uid:
            raise _invalid()
        positions[coord] = uid
        component, unit, sheet = expected[key]
        cells.append(
            {
                **binding,
                "file": sheet["file"],
                "sheet": titles[sheet["id"]],
                "original_sheet": sheet["name"],
                "source": unit["values"][component_source(manifest, component)],
                "target": sheet["rows"][binding["row"] - 1][binding["column"] - 1],
            }
        )
    if seen != set(expected):
        raise _invalid()
    current_issues = final_snapshot["issues"]
    if any(not set(i["unit_ids"]) <= scope for i in current_issues):
        raise _invalid()
    results = {u["unit_id"]: u for u in unit_results}
    issues, highlights, blocking_cells, unverified_cells = [], {}, set(), set()
    for cell in sorted(cells, key=lambda c: (c["sheet_id"], c["row"], c["column"], c["unit_id"])):
        uid = cell["unit_id"]
        unverified = final_snapshot["judged_revisions"].get(str(uid)) != cell["revision"]
        decision = results.get(uid, {})
        same_revision = decision.get("input_revision") == cell["revision"]
        seats = decision.get("live_seat_results", {}) if same_revision else {}
        if not unverified:
            # judged_revisions is the server's current two-seat PASS contract.
            seats = {"1": "approved", "2": "approved"}
        # The API's severity belongs to run_unit, but its revision describes the
        # live unit at response time. Neither this pair nor parsed seat states
        # proves that an old major/critical verdict concerns the exported text.
        residual = [i for i in current_issues if uid in i["unit_ids"]]
        rows = [
            (
                i["kind"],
                i["group_id"],
                required_issue(i),
                str(i.get("reason") or i.get("check_id") or i["kind"]),
                str(i.get("check_id") or ""),
            )
            for i in residual
        ]
        if unverified:
            rows.append(("unverified", "", False, "current_revision_not_verified", ""))
        coord = (cell["sheet_id"], cell["row"], cell["column"])
        for kind, group_id, blocking, reason, check_id in rows:
            issues.append(
                {
                    **cell,
                    "kind": kind,
                    "group_id": group_id,
                    "check_id": check_id,
                    "blocking": blocking,
                    "unverified": unverified,
                    "category": "blocking"
                    if blocking
                    else "unverified"
                    if kind == "unverified"
                    else "advisory",
                    "severity": "not_verified"
                    if kind == "unverified"
                    else "required"
                    if blocking
                    else "advisory",
                    "reason": reason,
                    "judge_1": seats.get("1", "missing"),
                    "judge_2": seats.get("2", "missing"),
                    "action": "complete_verification"
                    if kind == "unverified"
                    else "check_component_repeat_settings"
                    if reason == "translation_propagation_disabled"
                    else "manual_review"
                    if blocking
                    else "review_recommendation",
                }
            )
            if blocking:
                blocking_cells.add(coord)
            if unverified:
                unverified_cells.add(coord)
        if rows and (any(r[2] for r in rows) or unverified):
            category = "blocking" if any(r[2] for r in rows) else "unverified"
            if highlights.get(coord) != "blocking":
                highlights[coord] = category
    counts = provenance.get("counts", {})
    judge_seats = {"1": Counter(), "2": Counter()}
    for uid in scope:
        current = final_snapshot["revisions"][str(uid)]
        decision = results.get(uid, {})
        for seat, totals in judge_seats.items():
            state = (
                "approved"
                if final_snapshot["judged_revisions"].get(str(uid)) == current
                else (
                    decision.get("live_seat_results", {}).get(seat, "missing")
                    if decision.get("input_revision") == current
                    else "missing"
                )
            )
            totals[state] += 1
    summary = {
        "judge_seats": {seat: dict(totals) for seat, totals in judge_seats.items()},
        "status": provenance["status"],
        "language": language,
        "processed": len(scope),
        "automatically_corrected": counts.get("automatically_corrected", 0),
        "blocking_cells": len(blocking_cells),
        "unverified_cells": len(unverified_cells),
        "highlighted_cells": len(highlights),
        "issue_count": len(issues),
        "repeat_groups": len(
            {i["group_id"] for i in current_issues if i["kind"] == "repeat-drift"}
        ),
        "project_issues": [
            {
                "kind": i["kind"],
                "group_id": i["group_id"],
                "hard": i["hard"],
                "reason": str(i.get("reason") or i.get("check_id") or i["kind"]),
                "check_id": str(i.get("check_id") or ""),
            }
            for i in current_issues
            if not i["unit_ids"]
        ],
        "quarantined_transport": counts.get("quarantined_transport", 0),
        "judge_verified_units": len(scope)
        - len(
            {
                c["unit_id"]
                for c in cells
                if final_snapshot["judged_revisions"].get(str(c["unit_id"])) != c["revision"]
            }
        ),
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "export_id": export_id,
        "export_revision": revision(exported),
        "provenance": copy.deepcopy(provenance),
        "summary": summary,
        "issues": issues,
        "highlights": [
            {"sheet_id": sheet, "row": row, "column": col, "category": category}
            for (sheet, row, col), category in sorted(highlights.items())
        ],
    }


def load_quality_artifact(store, owner, run_id):
    from app.models import Job

    with store.session() as session:
        job = session.get(Job, run_id)
        if (
            not job
            or job.owner != owner
            or job.status != "completed"
            or not job.data.get("quality_artifact_id")
            or not job.data.get("export_id")
        ):
            raise DomainError("not_found", "Quality report is not available", 404)
        key, export_id = job.data["quality_artifact_id"], job.data["export_id"]
    artifact = store.get("quality_artifact", key, owner)
    exported = store.get("export", export_id, owner)
    exported.pop("revision", None)
    artifact.pop("revision", None)
    if (
        artifact["run_id"] != run_id
        or artifact["export_id"] != export_id
        or artifact["export_revision"] != revision(exported)
        or artifact["provenance"] != exported.get("quality_provenance")
    ):
        raise _invalid()
    return artifact, exported
