# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from app.auth import require_actor, require_project_access
from app.schemas import ClosedModel
from app.services.exports import export_file, export_summary
from app.services.runs import cancel_run, get_run, resume_run, start_run

router = APIRouter(prefix="/api/producer")


@router.get("/projects/{slug}/history/")
def history(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    from sqlalchemy import select

    from app.models import Job
    from app.services.runs import public_run

    with request.app.state.store.session() as session:
        runs = [
            public_run(job)
            for job in session.scalars(select(Job).where(Job.owner == actor["username"]))
            if job.data["project"] == slug
        ]
    return {"results": sorted(runs, key=lambda run: run["created"], reverse=True)}


@router.get("/runs/{run_id}/export/")
def historic_export(
    run_id: str,
    request: Request,
    format: Literal["xlsx", "csv", "json"] = "xlsx",
    actor=Depends(require_actor),
):
    from app.db import DomainError
    from app.models import Job
    from app.services.exports import serialize_export

    store = request.app.state.store
    with store.session() as session:
        job = session.get(Job, run_id)
        if (
            not job
            or job.owner != actor["username"]
            or job.status != "completed"
            or not job.data.get("export_id")
        ):
            raise DomainError("not_found", "Not found", 404)
        export_id = job.data["export_id"]
    value = store.get("export", export_id, actor["username"])
    value.pop("revision", None)
    payload, mime, filename = serialize_export(value, "run-" + run_id, format)
    return Response(
        payload,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs/{run_id}/diagnostics/")
def diagnostics(run_id: str, request: Request, actor=Depends(require_actor)):
    import json

    from app.db import DomainError
    from app.models import Job
    from app.services.diagnostics import diagnostic_report

    with request.app.state.store.session() as session:
        job = session.get(Job, run_id)
        if not job or job.owner != actor["username"]:
            raise DomainError("not_found", "Not found", 404)
        result = diagnostic_report(job.id, job.data)
    return Response(
        json.dumps(result, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="wizard-diagnostics.json"',
            "Cache-Control": "no-store",
        },
    )


class LocalizationRequest(ClosedModel):
    upload_id: str
    revision: str
    glossary_upload_id: str | None = None
    glossary_revision: str | None = None
    glossary_confirmed: bool = False
    change_plan_id: str | None = None
    change_plan_revision: str | None = None


class ResumeRequest(ClosedModel):
    attempt: int = Field(ge=1)


@router.post("/projects/{slug}/localization/", status_code=202)
def start(slug: str, body: LocalizationRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    if not request.app.state.settings.external_writes_enabled:
        from app.db import DomainError

        raise DomainError(
            "external_read_only", "Запись во внешний Weblate отключена оператором", 403
        )
    from app.services.drafts import get_draft
    from app.services.languages import validate_draft_languages

    validate_draft_languages(
        get_draft(request.app.state.store, body.upload_id, actor["username"]),
        request.app.state.settings,
    )
    return start_run(
        request.app.state.store,
        actor["username"],
        slug,
        body.model_dump(),
        request.app.state.settings.engine,
    )


@router.get("/runs/{run_id}/")
def run(run_id: str, request: Request, actor=Depends(require_actor)):
    return get_run(request.app.state.store, actor["username"], run_id)


@router.post("/runs/{run_id}/resume/")
def resume(run_id: str, body: ResumeRequest, request: Request, actor=Depends(require_actor)):
    return resume_run(request.app.state.store, actor["username"], run_id, body.attempt)


@router.post("/runs/{run_id}/cancel/")
def cancel(run_id: str, request: Request, actor=Depends(require_actor)):
    return cancel_run(request.app.state.store, actor["username"], run_id)


@router.get("/projects/{slug}/export/summary/")
def summary(slug: str, request: Request, content: str = "", actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return export_summary(
        request.app.state.store, actor["username"], slug, content.split(",") if content else []
    )


@router.get("/projects/{slug}/export/")
def download(
    slug: str,
    request: Request,
    revision: str,
    format: Literal["xlsx", "csv", "json"] = "xlsx",
    content: str = "",
    actor=Depends(require_actor),
):
    require_project_access(request, actor, slug)
    payload, mime, filename = export_file(
        request.app.state.store,
        actor["username"],
        slug,
        content.split(",") if content else [],
        format,
        revision,
    )
    return Response(
        payload,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
