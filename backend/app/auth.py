# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import secrets

from fastapi import Request

from app.db import DomainError


def require_actor(request: Request):
    actor = request.app.state.store.actor(request.cookies.get("wizard_session", ""))
    if request.method not in {"GET", "HEAD"}:
        if not secrets.compare_digest(request.headers.get("X-CSRFToken", ""), actor["csrf"]):
            raise DomainError("csrf_failed", "CSRF verification failed", 403)
        origin = request.headers.get("Origin")
        allowed = {request.app.state.settings.public_origin}
        if origin and origin not in allowed:
            raise DomainError("origin_failed", "Origin verification failed", 403)
    return actor


def require_project_access(request: Request, actor: dict, slug: str):
    return request.app.state.store.get("project", slug, actor["username"])
