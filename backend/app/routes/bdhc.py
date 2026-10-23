# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Request

from app.auth import require_actor, require_project_access
from app.db import DomainError
from app.integrations.bdhc import BDHCClient, map_bdhc_profile
from app.schemas import BDHCApplyRequest, BDHCPreviewRequest, ContextResolveRequest
from app.services.profile_context import resolve_context
from app.services.profiles import base_profile, get_profile, profile_key, selected_targets

router = APIRouter(prefix="/api/producer")


@router.post("/projects/{slug}/context/resolve/")
def context_resolve(
    slug: str, body: ContextResolveRequest, request: Request, actor=Depends(require_actor)
):
    require_project_access(request, actor, slug)
    return resolve_context(
        request.app.state.store, request.app.state.settings, actor["username"], slug, body
    )


@router.get("/bdhc/titles/")
def search(request: Request, q: str = "", actor=Depends(require_actor)):
    return {"results": BDHCClient(request.app.state.settings).search(q[:200])}


@router.post("/projects/{slug}/bdhc-preview/")
def preview(slug: str, body: BDHCPreviewRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    store = request.app.state.store
    settings = request.app.state.settings
    owner = actor["username"]
    profile = get_profile(store, owner, slug, settings.engine)
    if body.revision != profile["revision"]:
        raise DomainError("stale-revision", "Profile changed", 409)
    client = BDHCClient(settings)
    try:
        title_id = str(uuid.UUID(body.title_id))
    except ValueError:
        title_id = client.resolve(body.title_id)
    card = client.read_profile(title_id)
    suggestion = map_bdhc_profile(card, profile["answers"], selected_targets(store, owner, slug))
    diff = [
        {"field": key, "before": profile["answers"].get(key, ""), "after": value}
        for key, value in suggestion["answers"].items()
        if value != profile["answers"].get(key)
    ]
    locale_rules_diff = [
        {
            "language": code,
            "before": profile["locale_rules"].get(code, {}),
            "after": suggestion["locale_rules"].get(code, {}),
        }
        for code in sorted(profile["locale_rules"].keys() | suggestion["locale_rules"].keys())
        if profile["locale_rules"].get(code, {}) != suggestion["locale_rules"].get(code, {})
    ]
    provenance = {
        "title_id": title_id,
        "schema_version": card.get("schema_version"),
        "context_hash": card.get("context_hash"),
    }
    preview_id = str(uuid.uuid4())
    value = {
        "project": slug,
        "profile_revision": body.revision,
        "expires": time.time() + 600,
        "used": False,
        "title_id": title_id,
        "schema_version": card.get("schema_version"),
        "context_hash": card.get("context_hash"),
        "suggestion": suggestion,
        "diff": diff,
        "locale_rules_diff": locale_rules_diff,
    }
    store.save("bdhc_preview", preview_id, owner, value)
    return {
        "preview_id": preview_id,
        "diff": diff,
        "warnings": suggestion["warnings"],
        "title_id": title_id,
        "locale_rules_diff": locale_rules_diff,
        "provenance": provenance,
    }


@router.post("/projects/{slug}/bdhc-apply/")
def apply(slug: str, body: BDHCApplyRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    store = request.app.state.store
    settings = request.app.state.settings
    owner = actor["username"]
    preview = store.get("bdhc_preview", body.preview_id, owner)
    if preview["project"] != slug or preview["expires"] < time.time() or preview["used"]:
        raise DomainError("preview_expired", "Request a new BDHC preview", 409)
    profile = get_profile(store, owner, slug, settings.engine)
    if body.revision != preview["profile_revision"] or body.revision != profile["revision"]:
        raise DomainError("stale-revision", "Profile changed", 409)
    value = base_profile(profile)
    value.update(
        answers=preview["suggestion"]["answers"],
        generated=None,
        locale_rules=preview["suggestion"]["locale_rules"],
        bdhc={k: preview[k] for k in ["title_id", "schema_version", "context_hash"]},
    )
    store.save("profile", profile_key(slug), owner, value, body.revision)
    old = preview.pop("revision")
    preview["used"] = True
    store.save("bdhc_preview", body.preview_id, owner, preview, old)
    return get_profile(store, owner, slug, settings.engine)
