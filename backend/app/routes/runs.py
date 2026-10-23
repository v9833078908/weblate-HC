# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, field_validator

from app.auth import require_actor, require_project_access
from app.integrations.weblate import WeblateClient
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
        artifact_id = job.data.get("quality_artifact_id")
    value = store.get("export", export_id, actor["username"])
    value.pop("revision", None)
    if format == "xlsx" and artifact_id:
        from app.services.quality_artifact_files import serialize_quality_translation
        from app.services.quality_artifacts import load_quality_artifact

        artifact, value = load_quality_artifact(store, actor["username"], run_id)
        payload, mime, filename = serialize_quality_translation(value, artifact)
    else:
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


@router.get("/runs/{run_id}/quality-report/")
def saved_quality_report(
    run_id: str,
    request: Request,
    format: Literal["xlsx", "json"] = "xlsx",
    category: Literal["all", "blocking", "unverified", "advisory"] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    actor=Depends(require_actor),
):
    from fastapi.responses import JSONResponse

    from app.services.quality_artifact_files import serialize_quality_report
    from app.services.quality_artifacts import load_quality_artifact

    artifact, exported = load_quality_artifact(request.app.state.store, actor["username"], run_id)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if format == "json":
        issues = [
            i
            for i in artifact["issues"]
            if category == "all"
            or (i["unverified"] if category == "unverified" else i["category"] == category)
        ]
        start = (page - 1) * page_size
        return JSONResponse(
            {
                "schema_version": artifact["schema_version"],
                "run_id": run_id,
                "export_id": artifact["export_id"],
                "provenance": artifact["provenance"],
                "filename": f"run-{run_id}-quality-report.xlsx",
                "summary": artifact["summary"],
                "issues": issues[start : start + page_size],
                "total": len(issues),
                "page": page,
                "page_size": page_size,
            },
            headers=headers,
        )
    payload, mime, filename = serialize_quality_report(artifact, exported)
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(payload, media_type=mime, headers=headers)


@router.get("/runs/{run_id}/result-bundle/")
def saved_quality_bundle(run_id: str, request: Request, actor=Depends(require_actor)):
    from app.services.quality_artifact_files import serialize_quality_bundle
    from app.services.quality_artifacts import load_quality_artifact

    artifact, exported = load_quality_artifact(request.app.state.store, actor["username"], run_id)
    payload, mime, filename = serialize_quality_bundle(artifact, exported)
    return Response(
        payload,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
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


@router.get("/runs/{run_id}/quality/")
def quality_report(run_id: str, request: Request, actor=Depends(require_actor)):
    from app.db import DomainError
    from app.models import Job
    from app.services.quality_report import full_quality_report

    store = request.app.state.store
    with store.session() as session:
        job = session.get(Job, run_id)
        if not job or job.owner != actor["username"]:
            raise DomainError("not_found", "Not found", 404)
        cycle_id = job.data.get("quality_cycle_id")
    if not cycle_id:
        raise DomainError("not_found", "Quality cycle is not available", 404)
    report = full_quality_report(store, actor["username"], cycle_id)
    from urllib.parse import quote

    base = request.app.state.settings.weblate_url.rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    with store.session() as session:
        project = session.get(Job, run_id).data["remote_project"]
    report["weblate_project_url"] = f"{base}/projects/{quote(project, safe='')}/"
    return report


class QualityPolicy(ClosedModel):
    enabled: bool = False
    execution_mode: Literal["repair", "verify_only"] = "repair"
    language: str = Field(default="de", pattern=r"^[A-Za-z0-9_-]{1,100}$")
    max_repair_rounds: Literal[0, 1, 2] = 2
    consistency: list[Literal["repeat-drift", "glossary"]] = Field(
        default_factory=lambda: ["repeat-drift", "glossary"],
        max_length=2,
    )


class LocalizationRequest(ClosedModel):
    upload_id: str
    revision: str
    glossary_upload_id: str | None = None
    glossary_revision: str | None = None
    glossary_confirmed: bool = False
    change_plan_id: str | None = None
    change_plan_revision: str | None = None
    quality: QualityPolicy = Field(default_factory=QualityPolicy)


class ResumeRequest(ClosedModel):
    attempt: int = Field(ge=1)


class QualityOnlyScope(ClosedModel):
    unit_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1, max_length=10000)

    @field_validator("unit_ids")
    @classmethod
    def unique_units(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Unit IDs must be unique")
        return value


class QualityOnlyRequest(ClosedModel):
    execution_mode: Literal["repair", "verify_only"] = "repair"
    parent_run_id: str = Field(min_length=1, max_length=100)
    language: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    scope: QualityOnlyScope
    glossary_revision: str = Field(min_length=1, max_length=128)
    client_request_id: UUID


@router.get("/runs/{run_id}/verification-preview/")
def preview_verification(
    run_id: str, request: Request, language: str | None = None, actor=Depends(require_actor)
):
    from app.services.quality_only import verification_preview

    return verification_preview(
        request.app.state.store,
        actor["username"],
        run_id,
        WeblateClient(request.app.state.settings),
        language,
    )


@router.post("/projects/{slug}/quality-runs/", status_code=202)
def start_quality_only(
    slug: str, body: QualityOnlyRequest, request: Request, actor=Depends(require_actor)
):
    from app.db import DomainError
    from app.services.quality_only import start_quality_only_run

    require_project_access(request, actor, slug)
    settings = request.app.state.settings
    if not settings.external_writes_enabled:
        raise DomainError("external_read_only", "External writes are disabled", 403)
    if not settings.quality_cycle_enabled or not settings.full_quality_cycle_enabled:
        raise DomainError("quality_cycle_disabled", "Full quality cycle is disabled", 409)
    return start_quality_only_run(
        request.app.state.store,
        actor["username"],
        slug,
        body.model_dump(mode="json"),
        WeblateClient(settings),
        settings.engine,
    )


@router.post("/projects/{slug}/localization/", status_code=202)
def start(slug: str, body: LocalizationRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    if not request.app.state.settings.external_writes_enabled:
        from app.db import DomainError

        raise DomainError(
            "external_read_only", "Запись во внешний Weblate отключена оператором", 403
        )
    if body.quality.enabled and not request.app.state.settings.quality_cycle_enabled:
        from app.db import DomainError

        raise DomainError(
            "quality_cycle_disabled",
            "Автоматический цикл качества не включён оператором",
            409,
        )
    from app.services.drafts import get_draft
    from app.services.languages import validate_draft_languages

    validate_draft_languages(
        get_draft(request.app.state.store, body.upload_id, actor["username"]),
        request.app.state.settings,
    )
    payload = body.model_dump()
    if body.quality.enabled and request.app.state.settings.full_quality_cycle_enabled:
        payload["quality"]["workflow_version"] = 2
    return start_run(
        request.app.state.store,
        actor["username"],
        slug,
        payload,
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
