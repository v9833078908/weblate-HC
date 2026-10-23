# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import DomainError, revision
from app.models import Job, Record
from app.services.drafts import get_draft
from app.services.manifest import component_source
from app.services.profiles import get_profile


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def source_identity(manifest):
    source = manifest["source"]
    return [
        source,
        [
            [
                component["id"],
                component["file"],
                component["sheet"],
                component_source(manifest, component),
                [
                    [unit["key"], unit["values"][component_source(manifest, component)]]
                    for unit in component["units"]
                ],
            ]
            for component in manifest["components"]
        ],
    ]


def reusable_project(store, owner, slug, manifest):
    # Bind only to a project imported and completed by this actor's local project.
    with store.session() as session:
        completed = [
            job.data
            for job in session.scalars(
                select(Job).where(Job.owner == owner, Job.status == "completed")
            )
            if job.data["project"] == slug
            and job.data["operations"].get("project", {}).get("state") == "confirmed"
        ]
    if not completed:
        return None
    previous = min(completed, key=lambda data: data["created"])
    if source_identity(previous["snapshot"]["manifest"]) != source_identity(manifest):
        raise DomainError(
            "project_source_changed",
            "The source content differs from the existing project; adding a language requires the same source strings",
        )
    return previous["remote_project"]


def ensure_project_available(session, owner, slug, except_id=None, remote_slug=None):
    link = session.get(Record, "link:" + slug)
    if link and link.owner == owner:
        remote_slug = link.data["remote_slug"]

    def same_project(job):
        return (job.owner == owner and job.data["project"] == slug) or bool(
            remote_slug and job.data["remote_project"] == remote_slug
        )

    active = session.scalars(
        select(Job).where(Job.status.in_(["queued", "running", "unknown", "cancel_requested"]))
    )
    if any(job.id != except_id and same_project(job) for job in active):
        raise DomainError(
            "run_active",
            "A localization run for this project is still active; resume or cancel it before starting another",
            409,
        )

    failed = session.scalars(select(Job).where(Job.status.in_(["failed", "cancelled"])))
    for job in failed:
        if job.id == except_id or not same_project(job):
            continue
        if any(
            key.removeprefix("delta:").startswith("mt:")
            and operation["state"] in {"intent", "accepted"}
            for key, operation in job.data["operations"].items()
        ):
            raise DomainError(
                "run_active",
                "A previous paid translation is unconfirmed; resume that run before starting another",
                409,
            )


def start_run(store, owner, slug, body, engine):
    project = store.get("project", slug, owner)
    draft = get_draft(store, body["upload_id"], owner)
    if draft["project"] != slug or draft["kind"] != "string":
        raise DomainError("draft_project", "Select this project’s loc-kit")
    if draft["revision"] != body["revision"]:
        raise DomainError("stale-revision", "Reload the changed draft", 409)
    if draft["errors"] or not draft["manifest"]:
        raise DomainError("kit_not_ready", "Confirm the loc-kit decisions before starting")
    if not any(c["units"] for c in draft["manifest"]["components"]):
        raise DomainError(
            "kit_no_admitted_rows",
            "Нет строк для перевода. Проверьте исходные тексты и структуру колонок.",
            409,
        )
    profile = get_profile(store, owner, slug, engine)
    if profile["profile_state"] != "configured":
        raise DomainError(
            "profile_not_ready", "Save a complete translation profile before starting"
        )
    targets = draft["manifest"]["targets"]
    binding = profile.get("generation_binding")
    if binding and (
        binding["source_language"] != draft["manifest"]["source"]
        or binding["upload_token"] != draft["token"]
        or binding.get("manifest_hash") != revision(draft["manifest"])
    ):
        raise DomainError(
            "profile_stale", "Prepare instructions for this loc-kit and language selection"
        )
    if binding and any(
        not profile["generated"]["language_instructions"].get(code) for code in targets
    ):
        raise DomainError("profile_languages", "Prepare instructions for every selected language")
    actual_sources = {
        component_source(draft["manifest"], c) for c in draft["manifest"]["components"]
    }
    bound_sources = set(
        (binding or {}).get("source_languages") or [(binding or {}).get("source_language")]
    )
    if (
        profile.get("field_origins", {}).get("persona") == "manual"
        and binding
        and not actual_sources <= bound_sources
    ):
        raise DomainError(
            "profile_source_conflict",
            "Ручная роль переводчика привязана к другому исходному языку. Проверьте инструкции для запасных источников.",
            409,
        )
    run_profile = copy.deepcopy(profile["generated"])
    run_profile["language_instructions"] = {
        code: text for code, text in run_profile["language_instructions"].items() if code in targets
    }
    from app.services.kit_versions import prepare_glossary

    glossary = prepare_glossary(store, owner, slug, draft["manifest"], body)
    run_id = str(uuid.uuid4())
    remote_slug = slug + "-" + run_id[:8]
    snapshot = {
        "manifest": copy.deepcopy(draft["manifest"]),
        "profile": run_profile,
        "glossary": copy.deepcopy(glossary),
        "glossary_confirmed": bool(body.get("glossary_confirmed")),
        "name": project["name"],
        "engine": engine,
        "persistent": project.get("persistent", False),
    }
    from app.services.project_links import get_link

    link = get_link(store, owner, slug)
    if link:
        if link["read_only"] or link.get("origin") != "created-v2":
            raise DomainError(
                "project_read_only",
                "Существующий проект подключён только для чтения; запись запрещена",
                403,
            )
        plan = store.get("change_plan", body.get("change_plan_id") or "", owner)
        if (
            not plan.get("confirmed")
            or plan["project"] != slug
            or plan["upload_id"] != draft["token"]
            or plan["draft_revision"] != body["revision"]
            or plan["revision"] != body.get("change_plan_revision")
            or plan["manifest_hash"] != revision(draft["manifest"])
            or plan.get("profile_hash") != revision(profile["generated"])
            or plan["expires"] < time.time()
        ):
            raise DomainError(
                "changes_not_confirmed", "Повторите просмотр и подтвердите изменения", 409
            )
        if glossary and (
            not body.get("glossary_confirmed") or plan.get("glossary_hash") != revision(glossary)
        ):
            raise DomainError(
                "glossary_confirmation", "Отдельно подтвердите просмотренный глоссарий", 409
            )
        snapshot["change_plan"] = copy.deepcopy(plan)
        remote_slug = link["remote_slug"]
    if snapshot.get("persistent"):
        from app.services.kit_versions import retain_version

        snapshot["kit_version_id"] = retain_version(store, owner, slug, draft)["id"]
    # Execution identity excludes navigation and expiry renewal, but retains all paid inputs.
    key = revision([owner, slug, draft["token"], snapshot])
    if project["name"].casefold() in {"тест", "test"} or project["name"].casefold().startswith(
        "тест — "
    ):
        snapshot["name"] = "тест — " + run_id[:8]
    value = {
        "project": slug,
        "remote_project": remote_slug,
        "reuse_project": False,
        "snapshot": snapshot,
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
    try:
        with store.session() as s:
            # Serialize project reservations against concurrent start/resume.
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = s.scalar(select(Job).where(Job.idempotency_key == key, Job.owner == owner))
            if existing:
                return public_run(existing)
            ensure_project_available(s, owner, slug)
            reused_project = (
                link["remote_slug"]
                if link
                else reusable_project(store, owner, slug, draft["manifest"])
            )
            if reused_project and glossary and not link:
                raise DomainError(
                    "glossary_extension",
                    "Add languages without a new glossary upload; the existing project glossary is preserved",
                )
            if reused_project:
                value.update(remote_project=reused_project, reuse_project=True)
            job = Job(id=run_id, owner=owner, idempotency_key=key, status="queued", data=value)
            s.add(job)
            s.flush()
            return public_run(job)
    except IntegrityError:
        with store.session() as s:
            existing = s.scalar(select(Job).where(Job.idempotency_key == key, Job.owner == owner))
            if existing:
                return public_run(existing)
        raise


def public_run(job):
    from app.services.mt_batches import public_mt_observation

    data = job.data
    status = job.status
    if status == "running" and job.lease_until < time.time():
        status = "no-update"
    operations = data["operations"]

    def stages(prefix):
        return [
            {"id": key, "status": "completed" if value["state"] == "confirmed" else value["state"]}
            for key, value in operations.items()
            if key.startswith(prefix)
            or key.startswith("delta:" + prefix)
            or prefix == "component:"
            and key.startswith("delta:source:")
        ]

    return {
        "id": job.id,
        "status": status,
        "requested_mode": "translate",
        "created": data["created"],
        "started": data["started"],
        "finished": data["finished"],
        "summary": {"remote_project": data["remote_project"]},
        "failure": data["failure"],
        "rate_limit": data.get("rate_limit"),
        "warnings": data["warnings"],
        "stages": {
            "mt_observation": public_mt_observation(data),
            "components": stages("component:"),
            "languages": stages("language:"),
            "glossary": stages("glossary:"),
            "translate": stages("mt:"),
            "checks": {"status": "completed" if data["export_id"] else "pending"},
            "execution": {"attempt": data["attempt"]},
        },
        "liveness": "no-update" if status == "no-update" else status,
        "outcome": ("ready" if not data["warnings"] else "attention")
        if data["export_id"]
        else None,
        "resume_allowed": status in {"failed", "unknown"},
        "resume_reason": "Only unfinished operations are observed or processed"
        if status in {"failed", "unknown"}
        else None,
        "preparation_phase": data["phase"],
        "coverage": {
            key: {
                "translated": value["observation"]["filled"],
                "total": value["observation"]["total"],
                "last_progress": value["observation"]["last_progress"],
                "observe_until": value["observe_until"],
            }
            for key, value in operations.items()
            if "observation" in value
        },
    }


def get_run(store, owner, run_id):
    with store.session() as s:
        job = s.get(Job, run_id)
        if not job or job.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        return public_run(job)


def resume_run(store, owner, run_id, attempt):
    with store.session() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = s.get(Job, run_id)
        if not job or job.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        if job.status not in {"unknown", "failed"} or job.data["attempt"] != attempt:
            raise DomainError("stale_run", "Reload the current run", 409)
        if job.lease_until > time.time():
            raise DomainError("run_active", "The worker is still active", 409)
        ensure_project_available(
            s, owner, job.data["project"], except_id=run_id, remote_slug=job.data["remote_project"]
        )
        value = copy.deepcopy(job.data)
        value.update(attempt=attempt + 1, failure="", finished=None)
        job.data = value
        job.status = "queued"
        job.lease_until = 0
        return public_run(job)


def cancel_run(store, owner, run_id):
    with store.session() as s:
        job = s.get(Job, run_id)
        if not job or job.owner != owner:
            raise DomainError("not_found", "Not found", 404)
        if job.status in {"queued", "running", "unknown"}:
            job.status = "cancel_requested"
            if not job.lease_owner and job.data.get("rate_limit", {}).get("waiting"):
                # A deferred job has no owner; cancellation must not wait
                # for Retry-After and sends no request to Weblate.
                job.lease_until = 0
        return public_run(job)


def claim_job(store, lease_owner):
    stamp = time.time()
    with store.session() as s:
        candidate = s.scalar(
            select(Job.id)
            .where(
                Job.status.in_(["queued", "running", "cancel_requested"]), Job.lease_until <= stamp
            )
            .order_by(Job.id)
            .limit(1)
        )
        if candidate is None:
            return None
        changed = s.execute(
            update(Job)
            .where(
                Job.id == candidate,
                Job.lease_until <= stamp,
                Job.status.in_(["queued", "running", "cancel_requested"]),
            )
            .values(lease_owner=lease_owner, lease_until=stamp + 900)
        )
        if changed.rowcount != 1:
            return None
        job = s.get(Job, candidate)
        if job.status != "cancel_requested":
            job.status = "running"
        return {"id": job.id, "owner": job.owner, "data": copy.deepcopy(job.data)}
