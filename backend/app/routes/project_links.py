# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.auth import require_actor, require_project_access
from app.integrations.weblate import WeblateClient
from app.schemas import ClosedModel
from app.services.kit_versions import confirm_changes, preview_changes
from app.services.project_links import (
    confirm_connection,
    discover_projects,
    get_link,
    preview_connection,
    snapshot_project,
)

router = APIRouter(prefix="/api/producer")


@router.get("/integration-policy/")
def integration_policy(request: Request, actor=Depends(require_actor)):
    return {
        "external_writes": request.app.state.settings.external_writes_enabled,
        "quality_cycle": request.app.state.settings.quality_cycle_enabled,
    }


class ConnectionPreview(ClosedModel):
    remote_slug: str = Field(min_length=1, max_length=100)


class ConnectionConfirm(ClosedModel):
    candidate_id: str
    expected_snapshot_hash: str


class ChangesPreview(ClosedModel):
    upload_id: str
    revision: str
    mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    include_old_empty: bool = False
    glossary_upload_id: str | None = None
    glossary_revision: str | None = None


class ChangesConfirm(ClosedModel):
    plan_id: str
    revision: str


@router.post("/projects/{slug}/changes/preview/")
def changes_preview(
    slug: str, body: ChangesPreview, request: Request, actor=Depends(require_actor)
):
    require_project_access(request, actor, slug)
    return preview_changes(
        request.app.state.store,
        actor["username"],
        slug,
        remote(request),
        body.upload_id,
        body.revision,
        body.mapping,
        body.include_old_empty,
        engine=request.app.state.settings.engine,
        glossary_upload_id=body.glossary_upload_id,
        glossary_revision=body.glossary_revision,
    )


@router.post("/projects/{slug}/changes/confirm/")
def changes_confirm(
    slug: str, body: ChangesConfirm, request: Request, actor=Depends(require_actor)
):
    require_project_access(request, actor, slug)
    from app.db import DomainError
    from app.services.drafts import get_draft
    from app.services.languages import validate_draft_languages

    plan = request.app.state.store.get("change_plan", body.plan_id, actor["username"])
    if plan["project"] != slug:
        raise DomainError("not_found", "Not found", 404)
    validate_draft_languages(
        get_draft(request.app.state.store, plan["upload_id"], actor["username"]),
        request.app.state.settings,
    )
    return confirm_changes(
        request.app.state.store,
        actor["username"],
        slug,
        remote(request),
        body.plan_id,
        body.revision,
    )


def remote(request):
    return WeblateClient(request.app.state.settings)


@router.get("/remote-projects/")
def discover(
    request: Request,
    name: str,
    match: Literal["exact", "contains"] = "exact",
    actor=Depends(require_actor),
):
    return {"results": discover_projects(remote(request), name, match=match)}


@router.get("/projects/{slug}/connection/")
def connection(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return get_link(request.app.state.store, actor["username"], slug)


@router.post("/projects/{slug}/connection/refresh/")
def refresh(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    store = request.app.state.store
    link = get_link(store, actor["username"], slug)
    if not link:
        from app.db import DomainError

        raise DomainError("not_connected", "Проект не подключён", 409)
    import time

    return store.save(
        "project_link",
        "link:" + slug,
        actor["username"],
        {
            **{k: v for k, v in link.items() if k != "revision"},
            "snapshot": snapshot_project(remote(request), link["remote_slug"]),
            "observed": time.time(),
        },
        expected=link["revision"],
    )


@router.post("/projects/{slug}/connection/preview/")
def preview(slug: str, body: ConnectionPreview, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return preview_connection(
        request.app.state.store, actor["username"], slug, remote(request), body.remote_slug
    )


@router.post("/projects/{slug}/connection/confirm/")
def confirm(slug: str, body: ConnectionConfirm, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return confirm_connection(
        request.app.state.store,
        actor["username"],
        slug,
        remote(request),
        body.candidate_id,
        body.expected_snapshot_hash,
    )
