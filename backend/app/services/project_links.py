# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
import uuid

from sqlalchemy import select

from app.db import DomainError, revision
from app.integrations.weblate import WeblateRejected, segment
from app.models import Job, Record


def text_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    raise DomainError(
        "remote_plural", "Форма строки не поддерживается: требуется решение по компоненту", 409
    )


def code_value(value):
    return segment(value["code"] if isinstance(value, dict) else value)


def discover_projects(remote, name, *, match="exact"):
    query = name.strip().casefold()
    if not query:
        return []

    def matches(row):
        values = (row.get("name", "").strip().casefold(), row.get("slug", "").casefold())
        return any(query in value for value in values) if match == "contains" else query in values

    return [
        {"name": row["name"], "slug": segment(row["slug"])}
        for row in remote.collect("projects/")
        if matches(row)
    ]


def snapshot_project(remote, slug):
    project = remote.get_project(slug)
    if project is None:
        raise DomainError("remote_missing", "Проект Weblate не найден", 404)
    components, languages, issues = {}, set(), []
    for component in remote.collect(f"projects/{segment(slug)}/components/"):
        cid = segment(component["slug"])
        source = code_value(component["source_language"])
        translations = remote.collect(f"components/{segment(slug)}/{cid}/translations/")
        targets = [
            code_value(t["language"]) for t in translations if code_value(t["language"]) != source
        ]
        languages.update(targets)
        units = []
        try:
            source_rows = remote.list_units(slug, cid, source)
            by_id = {}
            mono = component.get("file_format") in {"po-mono", "json", "strings", "properties"}
            for row in source_rows:
                original = text_value(row["source"])
                source_text = text_value(row.get("target", "")) if mono else original
                key = row.get("context") or (original if mono else str(row["id"]))
                unit = {
                    "key": key,
                    "source": source_text,
                    "source_id": row["id"],
                    "context": row.get("context", ""),
                    "note": row.get("note", ""),
                    "explanation": row.get("explanation", ""),
                    "targets": {},
                    "target_ids": {},
                }
                units.append(unit)
                by_id[str(row["id"])] = unit
            for code in targets:
                for row in remote.list_units(slug, cid, code):
                    source_id = str(row.get("source_unit", "")).rstrip("/").rsplit("/", 1)[-1]
                    unit = by_id.get(source_id)
                    if unit is None:
                        raise DomainError(
                            "remote_identity", "Не удалось связать перевод с исходной строкой", 409
                        )
                    unit["targets"][code] = text_value(row["target"])
                    unit["target_ids"][code] = row["id"]
        except DomainError as error:
            issues.append({"component": cid, "code": error.code, "detail": error.detail})
            units = []
        components[cid] = {
            "slug": cid,
            "name": component.get("name", cid),
            "source": source,
            "languages": targets,
            "glossary": bool(component.get("is_glossary")),
            "can_add_source": bool(
                component.get("manage_units") and component.get("edit_template")
            ),
            "file_format": component.get("file_format"),
            "units": units,
            "complete": not any(i["component"] == cid for i in issues),
        }
    try:
        settings = remote.request("GET", f"projects/{segment(slug)}/machinery_settings/")
    except WeblateRejected as error:
        if error.remote_status not in {403, 404}:
            raise
        settings = None
    # Credentials and routing are never stored in a producer-visible snapshot.
    profile = {}
    if isinstance(settings, dict):
        for engine in ("openrouter", "litellm"):
            config = settings.get(engine)
            if isinstance(config, dict):
                profile[engine] = {
                    k: copy.deepcopy(config[k])
                    for k in ("persona", "style", "language_instructions")
                    if k in config
                }
    return {
        "slug": slug,
        "name": project["name"],
        "components": components,
        "languages": sorted(languages),
        "profile": profile,
        "issues": issues,
    }


def get_link(store, owner, slug):
    try:
        return store.get("project_link", "link:" + slug, owner)
    except DomainError as error:
        if error.status == 404:
            return migrate_legacy_link(store, owner, slug)
        raise


def update_project_url(session, owner, slug, remote_slug):
    project = session.get(Record, slug)
    if project and project.kind == "project" and project.owner == owner:
        value = {
            **project.data,
            "advanced_url": f"https://l10n.herocraft.com/projects/{segment(remote_slug)}/",
        }
        project.data, project.revision = value, revision(value)


def migrate_legacy_link(store, owner, slug):
    # Only a confirmed creation journal establishes ownership; a name never does.
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        key = "link:" + slug
        existing = session.get(Record, key)
        if existing:
            if existing.kind != "project_link" or existing.owner != owner:
                raise DomainError("not_found", "Not found", 404)
            return {**existing.data, "revision": existing.revision}
        jobs = [
            job.data
            for job in session.scalars(select(Job).where(Job.owner == owner))
            if job.data["project"] == slug
            and not job.data.get("snapshot", {}).get("persistent")
            and job.data.get("operations", {}).get("project", {}).get("state") == "confirmed"
        ]
        if not jobs:
            return None
        job = min(jobs, key=lambda value: value["created"])
        remote_slug = segment(job["remote_project"])
        if any(
            link.data["remote_slug"] == remote_slug
            for link in session.scalars(select(Record).where(Record.kind == "project_link"))
        ):
            raise DomainError("remote_reserved", "Этот внешний проект уже подключён", 409)
        value = {
            "project": slug,
            "remote_slug": remote_slug,
            "read_only": True,
            "origin": "legacy-journal",
            "component_map": {
                c["file"] + "\u001f" + c["sheet"]: c["id"]
                for c in job["snapshot"]["manifest"]["components"]
            },
            "snapshot": {
                "slug": remote_slug,
                "name": job["snapshot"]["name"],
                "languages": [],
                "components": {},
                "profile": {},
                "issues": [
                    {
                        "component": "project",
                        "code": "refresh_required",
                        "detail": "Обновите снимок: привязка восстановлена из журнала",
                    }
                ],
            },
            "observed": time.time(),
        }
        session.add(
            Record(id=key, kind="project_link", owner=owner, data=value, revision=revision(value))
        )
        update_project_url(session, owner, slug, remote_slug)
        return {**value, "revision": revision(value)}


def preview_connection(store, owner, slug, remote, remote_slug):
    store.get("project", slug, owner)
    if get_link(store, owner, slug):
        raise DomainError("already_connected", "Проект уже подключён", 409)
    snapshot = snapshot_project(remote, segment(remote_slug))
    value = {
        "id": "connection:" + uuid.uuid4().hex,
        "project": slug,
        "snapshot": snapshot,
        "snapshot_hash": revision(snapshot),
        "expires": time.time() + 600,
    }
    return store.save("connection_preview", value["id"], owner, value)


def confirm_connection(store, owner, slug, remote, candidate_id, expected_snapshot_hash):
    store.get("project", slug, owner)
    preview = store.get("connection_preview", candidate_id, owner)
    if preview["project"] != slug or preview["expires"] < time.time():
        raise DomainError("connection_expired", "Повторите просмотр подключения", 409)
    snapshot = snapshot_project(remote, preview["snapshot"]["slug"])
    if (
        revision(snapshot) != expected_snapshot_hash
        or expected_snapshot_hash != preview["snapshot_hash"]
    ):
        raise DomainError("remote_changed", "Внешний проект изменился; повторите просмотр", 409)
    value = {
        "project": slug,
        "remote_slug": snapshot["slug"],
        "read_only": True,
        "origin": "connected",
        "component_map": {},
        "snapshot": snapshot,
        "observed": time.time(),
    }
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        key = "link:" + slug
        for link in session.scalars(select(Record).where(Record.kind == "project_link")):
            if link.data["remote_slug"] == value["remote_slug"]:
                if link.owner == owner and link.id == key:
                    return {**link.data, "revision": link.revision}
                raise DomainError("remote_reserved", "Этот внешний проект уже подключён", 409)
        session.add(
            Record(id=key, kind="project_link", owner=owner, data=value, revision=revision(value))
        )
        update_project_url(session, owner, slug, snapshot["slug"])
    return store.get("project_link", key, owner)
