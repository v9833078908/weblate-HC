# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.auth import require_actor, require_project_access
from app.db import DomainError
from app.schemas import UploadPatch
from app.services import languages
from app.services.drafts import (
    create_draft,
    get_draft,
    patch_draft,
    public_draft,
    restore_latest_kit,
)
from app.services.kits import MIB

router = APIRouter(prefix="/api/producer")


@router.post("/projects/{slug}/uploads/", status_code=201)
async def upload(
    slug: str,
    request: Request,
    files: list[UploadFile] = File(...),
    declared_kind: Literal["string", "glossary"] = Form("string"),
    ui_mode: Literal["quick", "detailed"] = Form("quick"),
    actor=Depends(require_actor),
):
    require_project_access(request, actor, slug)
    if len(files) > 100:
        raise DomainError("file_limit", "Too many files", 413)
    uploaded = []
    total = 0
    for file in files:
        limit = 50 * MIB if (file.filename or "").lower().endswith(".zip") else 20 * MIB
        payload = await file.read(limit + 1)
        await file.close()
        if len(payload) > limit:
            raise DomainError("file_limit", "File too large", 413)
        total += len(payload)
        if total > 100 * MIB:
            raise DomainError("file_limit", "Upload too large", 413)
        uploaded.append((file.filename or "kit.csv", payload))
    return public_draft(
        create_draft(
            request.app.state.store, actor["username"], slug, uploaded, declared_kind, ui_mode
        )
    )


@router.get("/projects/{slug}/uploads/active/")
def active(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    drafts = [
        d
        for d in request.app.state.store.list("draft", actor["username"])
        if d["project"] == slug and d["kind"] == "string" and d["expires"] > time.time()
    ]
    if not drafts:
        raise DomainError("not_found", "No active upload", 404)
    return public_draft(drafts[-1])


@router.get("/uploads/{token}/")
def get_upload(token: str, request: Request, actor=Depends(require_actor)):
    return public_draft(get_draft(request.app.state.store, token, actor["username"]))


@router.post("/projects/{slug}/uploads/restore/")
def restore_upload(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return public_draft(restore_latest_kit(request.app.state.store, actor["username"], slug))


@router.patch("/uploads/{token}/")
def update_upload(token: str, body: UploadPatch, request: Request, actor=Depends(require_actor)):
    get_draft(request.app.state.store, token, actor["username"])
    if body.answers is not None:
        answers = body.answers
        if (
            answers.source_language
            or answers.included_target_languages
            or any(r.language for r in answers.column_roles)
        ):
            catalog = languages.catalog_for_settings(request.app.state.settings)
            source, targets = languages.validate_language_selection(
                answers.source_language or "", answers.included_target_languages, catalog
            )
            answers.source_language = source or None
            answers.included_target_languages = targets
            for role in answers.column_roles:
                if role.language:
                    role.language = languages.canonical_language(role.language, catalog)
    return public_draft(patch_draft(request.app.state.store, actor["username"], token, body))


@router.post("/uploads/{token}/heartbeat/")
def heartbeat(token: str, request: Request, actor=Depends(require_actor)):
    store = request.app.state.store
    draft = get_draft(store, token, actor["username"])
    old = draft.pop("revision")
    draft["expires"] = time.time() + 3600
    saved = store.save("draft", token, actor["username"], draft, old)
    return {"expires_at": saved["expires"], "revision": saved["revision"]}


@router.get("/uploads/{token}/quarantine/")
@router.get("/uploads/{token}/rows/")
def rows(
    token: str, request: Request, page: int = 1, page_size: int = 200, actor=Depends(require_actor)
):
    draft = get_draft(request.app.state.store, token, actor["username"])
    data = [
        row
        for row in (draft.get("manifest") or {}).get("quarantine", [])
        if row.get("reason_code", row.get("reason")) != "blank"
    ]
    size = max(1, min(page_size, 200))
    offset = max(0, page - 1) * size
    return {
        "count": len(data),
        "next": page + 1 if offset + size < len(data) else None,
        "previous": page - 1 if page > 1 else None,
        "results": data[offset : offset + size],
    }
