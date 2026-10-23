# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import uuid

from sqlalchemy import select

from app.db import DomainError, revision
from app.models import Job, Record
from app.services.manifest import component_mapping_key, component_source, component_targets
from app.services.project_links import snapshot_project
from app.services.quality_contract import require_full_quality_capabilities, validate_snapshot
from app.services.runs import ensure_project_available, now_iso, public_run, source_identity


def _copy_binding(store, owner, slug, parent_id, *, verify_only=False):
    store.get("project", slug, owner)
    with store.session() as session:
        parent = session.get(Job, parent_id)
        if not parent or parent.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        link = session.get(Record, "link:" + slug)
        if (
            verify_only
            and parent.status == "completed"
            and parent.data.get("export_id")
            and (
                parent.data["project"] == slug
                and link
                and link.owner == owner
                and link.kind == "project_link"
                and link.data.get("project") == slug
                and link.data.get("remote_slug") == parent.data["remote_project"]
            )
        ):
            # Verification uses the existing binding without granting write access.
            return copy.deepcopy(parent.data["snapshot"]["manifest"]), copy.deepcopy(link.data)
        if (
            parent.status != "completed"
            or not parent.data.get("export_id")
            or parent.data["project"] == slug
            or not link
            or link.kind != "project_link"
            or link.owner != owner
            or link.data.get("read_only") is not False
            or link.data.get("origin") != "created-v2"
            or link.data.get("project") != slug
            or link.data["remote_slug"] == parent.data["remote_project"]
        ):
            raise DomainError(
                "quality_copy_required", "Use a separate owned, prepared project copy", 403
            )
        prepared = [
            job
            for job in session.scalars(
                select(Job).where(Job.owner == owner, Job.status == "completed")
            )
            if job.data.get("project") == slug
            and job.data.get("remote_project") == link.data["remote_slug"]
            and job.data.get("mode") != "quality_only"
            and job.data.get("export_id")
            and job.data.get("operations", {}).get("project", {}).get("state") == "confirmed"
            and job.data["operations"]["project"].get("result", {}).get("slug")
            == link.data["remote_slug"]
        ]
        if not prepared:
            raise DomainError(
                "quality_copy_required", "The project copy has no completed import", 409
            )
        seed = max(prepared, key=lambda job: job.data["created"])
        manifest = seed.data["snapshot"]["manifest"]
        if source_identity(manifest) != source_identity(parent.data["snapshot"]["manifest"]):
            raise DomainError(
                "quality_source_changed", "The copy has different source content", 409
            )
        return copy.deepcopy(manifest), copy.deepcopy(link.data)


def _validate_current_copy(client, project, manifest, mapping, language, scope):
    snapshot = snapshot_project(client, project)
    admitted = []
    for component in manifest["components"]:
        if language not in component_targets(manifest, component):
            continue
        name = mapping.get(component_mapping_key(manifest, component))
        observed = snapshot["components"].get(name)
        source = component_source(manifest, component)
        if (
            not observed
            or observed.get("complete") is not True
            or observed.get("source") != source
            or language not in observed.get("languages", [])
            or any(issue.get("component") == name for issue in snapshot["issues"])
        ):
            raise DomainError("quality_scope", "The mapped project snapshot is incomplete", 409)
        by_key = {unit["key"]: unit for unit in observed["units"]}
        if len(by_key) != len(observed["units"]):
            raise DomainError("quality_scope", "Ambiguous source keys in the project copy", 409)
        for unit in component["units"]:
            current = by_key.get(unit["key"])
            if not current or current["source"] != unit["values"][source]:
                raise DomainError("quality_source_changed", "The project copy source changed", 409)
            target = current["targets"].get(language)
            if not isinstance(target, str) or not target.strip():
                raise DomainError(
                    "quality_input_incomplete",
                    "Fill missing translations separately; quality-only never runs MT",
                    409,
                )
            unit_id = current["target_ids"].get(language)
            if type(unit_id) is not int or unit_id < 1:
                raise DomainError("quality_scope", "Invalid target unit identity", 409)
            admitted.append(unit_id)
    if (
        not admitted
        or len(admitted) != len(set(admitted))
        or set(admitted) != set(scope["unit_ids"])
    ):
        raise DomainError(
            "quality_scope", "Scope must exactly match the prepared copy and language", 409
        )


def start_quality_only_run(store, owner, slug, body, client, engine):
    from app.services.run_isolation import assert_project_not_isolated

    assert_project_not_isolated(store, local_slug=slug)
    body = copy.deepcopy(body)
    body["scope"]["unit_ids"] = sorted(body["scope"]["unit_ids"])
    key = revision(["quality-only", owner, slug, body["client_request_id"]])
    store.get("project", slug, owner)
    with store.session() as session:
        existing = session.scalar(select(Job).where(Job.idempotency_key == key, Job.owner == owner))
        if existing:
            if existing.data.get("quality_only_request") != body:
                raise DomainError(
                    "quality_request_conflict", "Request ID was used with different inputs", 409
                )
            return public_run(existing)
    manifest, link = _copy_binding(
        store,
        owner,
        slug,
        body["parent_run_id"],
        verify_only=body.get("execution_mode") == "verify_only",
    )
    assert_project_not_isolated(store, link["remote_slug"], slug)
    language = body["language"]
    if language not in manifest["targets"]:
        raise DomainError("quality_scope", "Language is not present in the prepared copy", 409)
    manifest["targets"] = [language]
    require_full_quality_capabilities(client.producer_me())
    project = link["remote_slug"]
    from app.services.quality_reservations import reconcile_cancelled_quality

    reconcile_cancelled_quality(store, owner, project, client)
    _validate_current_copy(
        client, project, manifest, link["component_map"], language, body["scope"]
    )
    current = validate_snapshot(
        client.quality_snapshot(project, language, body["scope"]), body["scope"]
    )
    if body.get("execution_mode") == "verify_only":
        from app.services.quality_preflight import run_quality_preflight

        run_quality_preflight(
            client,
            project,
            body["scope"],
            full_cycle=True,
            require_no_mt=True,
            execution_mode="verify_only",
        )
    # The API exposes one opaque version covering glossary AND relevant rows/policies.
    if current["scope_revision"] != body["glossary_revision"]:
        raise DomainError("quality_glossary_changed", "Refresh the glossary/scope revision", 409)
    run_id = str(uuid.uuid4())
    value = {
        "mode": "quality_only",
        "parent_run_id": body["parent_run_id"],
        "quality_only_request": body,
        "project": slug,
        "remote_project": project,
        "snapshot": {
            "manifest": manifest,
            "engine": engine,
            "quality_only": {"mapping": link["component_map"]},
            "quality": {
                "enabled": True,
                "execution_mode": body.get("execution_mode", "repair"),
                "workflow_version": 2,
                "language": language,
                "max_repair_rounds": 0 if body.get("execution_mode") == "verify_only" else 2,
                "require_no_mt": True,
            },
        },
        "quality_scope": body["scope"],
        "quality_input_coverage": {
            "filled": len(body["scope"]["unit_ids"]),
            "total": len(body["scope"]["unit_ids"]),
            "missing": 0,
        },
        "created": now_iso(),
        "started": None,
        "finished": None,
        "attempt": 1,
        "operations": {},
        "warnings": [],
        "failure": "",
        "phase": "queued",
        "export_id": None,
    }
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        existing = session.scalar(select(Job).where(Job.idempotency_key == key, Job.owner == owner))
        if existing:
            if existing.data.get("quality_only_request") != body:
                raise DomainError(
                    "quality_request_conflict", "Request ID was used with different inputs", 409
                )
            return public_run(existing)
        current_link = session.get(Record, "link:" + slug)
        if not current_link or current_link.owner != owner or current_link.data != link:
            raise DomainError("quality_copy_required", "The project binding changed", 409)
        ensure_project_available(session, owner, slug, remote_slug=project)
        job = Job(id=run_id, owner=owner, idempotency_key=key, status="queued", data=value)
        session.add(job)
        session.flush()
        return public_run(job)


def verification_preview(store, owner, parent_id, client, language=None):
    with store.session() as session:
        parent = session.get(Job, parent_id)
        if parent is None or parent.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        slug = parent.data["project"]
    manifest, link = _copy_binding(store, owner, slug, parent_id, verify_only=True)
    languages = manifest["targets"]
    language = language or languages[0]
    if language not in languages:
        raise DomainError("quality_scope", "Language is not in the completed job", 409)
    current = snapshot_project(client, link["remote_slug"])
    ids = []
    for component in manifest["components"]:
        if language not in component_targets(manifest, component):
            continue
        name = link["component_map"].get(component_mapping_key(manifest, component))
        observed = current.get("components", {}).get(name, {})
        ids.extend(row.get("target_ids", {}).get(language) for row in observed.get("units", []))
    if not ids or any(type(ident) is not int or ident < 1 for ident in ids):
        raise DomainError("quality_scope", "Project has no complete verification scope", 409)
    scope = {"unit_ids": sorted(ids)}
    _validate_current_copy(
        client, link["remote_slug"], manifest, link["component_map"], language, scope
    )
    snapshot = validate_snapshot(
        client.quality_snapshot(link["remote_slug"], language, scope), scope
    )
    from app.services.quality_preflight import run_quality_preflight

    estimate = run_quality_preflight(
        client,
        link["remote_slug"],
        scope,
        full_cycle=True,
        require_no_mt=True,
        execution_mode="verify_only",
    )
    return {
        "project": slug,
        "languages": languages,
        "selected": len(ids),
        "initial_calls": estimate.get("initial_calls"),
        "cost": None,
        "request": {
            "parent_run_id": parent_id,
            "language": language,
            "scope": scope,
            "glossary_revision": snapshot["scope_revision"],
            "execution_mode": "verify_only",
        },
    }


def validate_quality_only_job(store, owner, data, client):
    manifest, link = _copy_binding(
        store,
        owner,
        data["project"],
        data["parent_run_id"],
        verify_only=data["snapshot"]["quality"].get("execution_mode") == "verify_only",
    )
    snapshot = data["snapshot"]
    if (
        link["remote_slug"] != data["remote_project"]
        or link["component_map"] != snapshot["quality_only"]["mapping"]
        or source_identity(manifest) != source_identity(snapshot["manifest"])
    ):
        raise DomainError("quality_scope", "The frozen project copy binding changed", 409)
    _validate_current_copy(
        client,
        data["remote_project"],
        snapshot["manifest"],
        link["component_map"],
        snapshot["quality"]["language"],
        data["quality_scope"],
    )
    if not data.get("quality_cycle_id"):
        current = validate_snapshot(
            client.quality_snapshot(
                data["remote_project"], snapshot["quality"]["language"], data["quality_scope"]
            ),
            data["quality_scope"],
        )
        if current["scope_revision"] != data["quality_only_request"]["glossary_revision"]:
            raise DomainError(
                "quality_glossary_changed", "The glossary/scope changed before start", 409
            )
