# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

from app.db import DomainError
from app.services.exports import build_export, component_exports, version_export
from app.services.full_quality_cycle import assert_verification_baseline, finalize_quality_snapshot
from app.services.manifest import component_mapping_key, component_source, component_targets
from app.services.quality_contract import validate_snapshot
from app.services.quality_state import get_quality_cycle, get_quality_operation


def build_corrected_quality_export(
    manifest, translations, *, change_plan=None, remote_snapshot=None
):
    if change_plan is not None:
        if remote_snapshot is None:
            raise DomainError(
                "quality_export_incomplete", "Fresh project snapshot is required", 409
            )
        _validate_version_snapshot(manifest, remote_snapshot, change_plan["mapping"])
        return version_export(manifest, remote_snapshot, change_plan["mapping"])
    prepared = copy.deepcopy(manifest)
    for component in prepared["components"]:
        source = component_source(prepared, component)
        for unit in component["units"]:
            for code in component_targets(prepared, component):
                values = translations.get(component["id"], {}).get(code, {})
                if unit["key"] not in values:
                    raise DomainError(
                        "quality_export_incomplete", "Downloaded translation is incomplete", 409
                    )
                if code != source:
                    unit["values"][code] = ""
    result = build_export(prepared, translations, True)
    result["components"] = component_exports(prepared, translations, True)
    return result


def _validate_version_snapshot(manifest, snapshot, mapping):
    """A best-effort project refresh is not evidence for a complete export."""
    targets = set(manifest["targets"]) | set(snapshot.get("languages", []))
    for component in manifest["components"]:
        remote_id = mapping.get(component_mapping_key(manifest, component))
        remote = snapshot.get("components", {}).get(remote_id)
        source = component_source(manifest, component)
        if (
            not remote
            or remote.get("complete") is not True
            or remote.get("source") != source
            or any(issue.get("component") == remote_id for issue in snapshot.get("issues", []))
            or not (targets - {source}) <= set(remote.get("languages", []))
        ):
            raise DomainError(
                "quality_export_incomplete", "Mapped project snapshot is incomplete", 409
            )
        units = {unit["key"]: unit for unit in remote["units"]}
        if len(units) != len(remote["units"]):
            raise DomainError(
                "quality_export_incomplete", "Project snapshot contains duplicate keys", 409
            )
        for unit in component["units"]:
            observed = units.get(unit["key"])
            if (
                observed is None
                or observed.get("source") != unit["values"][source]
                or any(
                    not isinstance(observed.get("targets", {}).get(code), str)
                    for code in targets - {source}
                )
            ):
                raise DomainError(
                    "quality_export_incomplete", "Project snapshot does not cover the loc-kit", 409
                )


def download_stable_quality_export(
    store, owner, cycle_id, client, download, *, include_snapshot=False
):
    cycle = get_quality_cycle(store, owner, cycle_id)
    operation = get_quality_operation(store, cycle_id, "full-context")
    final = get_quality_operation(store, cycle_id, "final-snapshot")
    if cycle["phase"] != "completed" or operation is None or final is None:
        raise DomainError("quality_export_unverified", "Final quality snapshot is unavailable", 409)
    context = operation["data"]
    scope = context["scope"]
    language = cycle["policy"].get("language", "de")
    for _attempt in range(2):
        before = validate_snapshot(
            client.quality_snapshot(cycle["remote_project"], language, scope), scope
        )
        assert_verification_baseline(store, cycle, before)
        values = download()
        after = validate_snapshot(
            client.quality_snapshot(cycle["remote_project"], language, scope), scope
        )
        assert_verification_baseline(store, cycle, after)
        if before["scope_revision"] == after["scope_revision"]:
            updated = finalize_quality_snapshot(store, cycle, context, after)
            if include_snapshot:
                values["quality_final_snapshot"] = copy.deepcopy(after)
            return values, {
                "remote_project": cycle["remote_project"],
                "language": language,
                "scope_revision": after["scope_revision"],
                "snapshot_hash": after["snapshot_hash"],
                "status": updated["status"],
                "counts": updated["counts"],
            }
    raise DomainError(
        "quality_export_unstable",
        "Translations changed during both export attempts; no file was published",
        409,
    )
