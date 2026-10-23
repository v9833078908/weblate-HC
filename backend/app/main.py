# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import html
import time
import uuid
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_actor, require_project_access
from app.db import DomainError, Store
from app.login_page import render_login_page
from app.schemas import Login, ProjectCreate
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="BDHC Weblate wizard")
    app.state.settings = settings or Settings()
    app.state.store = Store(app.state.settings.data_dir)
    app.state.login_attempts = {}
    from app.routes.producer import router

    app.include_router(router)
    from app.routes.bdhc import router as bdhc_router
    from app.routes.profiles import router as profiles_router

    app.include_router(bdhc_router)
    app.include_router(profiles_router)
    from app.routes.runs import router as runs_router

    app.include_router(runs_router)
    from app.routes.project_links import router as project_links_router

    app.include_router(project_links_router)
    from app.routes.languages import router as languages_router

    app.include_router(languages_router)

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse({"code": error.code, "detail": error.detail}, status_code=error.status)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            heartbeat = app.state.store.get("heartbeat", "worker-heartbeat", "system")
            if heartbeat["updated"] < time.time() - 75:
                raise DomainError("worker_unavailable", "Worker is not responding", 503)
            result = {"status": "ok", "database": "ok", "worker": "ok"}
            if app.state.settings.profile_agent_enabled:
                path = app.state.settings.data_dir / "profile-worker-heartbeat"
                try:
                    updated = float(path.read_text())
                    if not time.time() - 150 <= updated <= time.time() + 5:
                        raise ValueError("stale heartbeat")
                except (OSError, ValueError):
                    return JSONResponse(
                        {"status": "not_ready", "profile_worker": "unavailable"}, status_code=503
                    )
                result["profile_worker"] = "ok"
            return result
        except DomainError:
            return JSONResponse({"status": "not_ready", "worker": "unavailable"}, status_code=503)

    @app.post("/auth/login")
    def login(body: Login, request: Request):
        origin = request.headers.get("Origin")
        if origin and origin != app.state.settings.public_origin:
            raise DomainError("origin_failed", "Origin verification failed", 403)
        peer = request.client.host if request.client else "unknown"
        attempts = [t for t in app.state.login_attempts.get(peer, []) if t > time.time() - 300]
        if len(attempts) >= 10:
            raise DomainError("login_rate_limit", "Try again later", 429)
        app.state.login_attempts[peer] = attempts + [time.time()]
        token, csrf = app.state.store.login(body.username, body.password)
        app.state.login_attempts[peer] = []
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie(
            "wizard_session",
            token,
            httponly=True,
            secure=app.state.settings.secure_cookie,
            samesite="lax",
            max_age=8 * 3600,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/auth/logout")
    def logout(request: Request, actor=Depends(require_actor)):
        app.state.store.logout(request.cookies.get("wizard_session", ""))
        response = JSONResponse({"status": "ok"})
        response.delete_cookie("wizard_session")
        return response

    @app.get("/auth/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return HTMLResponse(
            render_login_page(request.query_params.get("next", "")),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/producer/me/")
    def me(actor=Depends(require_actor)):
        return {
            "username": actor["username"],
            "full_name": actor["username"],
            "is_superuser": False,
            "capabilities": {"judge": False, "glossary_profile_analysis": False},
            "advanced_url": "https://l10n.herocraft.com/",
        }

    @app.post("/api/producer/projects/", status_code=201)
    def create_project(body: ProjectCreate, actor=Depends(require_actor)):
        slug = (
            f"test-{uuid.uuid4().hex[:16]}"
            if body.name.strip().casefold() in {"тест", "test"}
            else f"wizard-{uuid.uuid4().hex[:16]}"
        )
        value = app.state.store.save(
            "project",
            slug,
            actor["username"],
            {
                "name": "тест — " + slug[-8:] if slug.startswith("test-") else body.name.strip(),
                "persistent": True,
                "slug": slug,
                "languages": [],
                "contents": [],
                "profile_state": "missing",
                "advanced_url": f"https://l10n.herocraft.com/projects/{slug}/",
            },
        )
        return {key: value[key] for key in ("slug", "name", "advanced_url")}

    @app.get("/api/producer/projects/")
    def projects(actor=Depends(require_actor)):
        values = app.state.store.list("project", actor["username"])
        values = [{key: value[key] for key in ("slug", "name", "advanced_url")} for value in values]
        return {
            "results": values,
            "next": None,
            "previous": None,
            "count": len(app.state.store.list("project", actor["username"])),
        }

    @app.get("/api/producer/projects/{slug}/")
    def project(slug: str, request: Request, actor=Depends(require_actor)):
        value = require_project_access(request, actor, slug)
        from app.services.project_links import get_link

        link = get_link(app.state.store, actor["username"], slug)
        if link:
            value["advanced_url"] = f"https://l10n.herocraft.com/projects/{link['remote_slug']}/"
        from app.services.profiles import get_profile

        value["profile_state"] = get_profile(
            app.state.store, actor["username"], slug, app.state.settings.engine
        )["profile_state"]
        try:
            from app.services.exports import project_snapshot

            exported = project_snapshot(app.state.store, actor["username"], slug)
            value["contents"] = [
                {
                    "id": key,
                    "type": "loc_kit",
                    "label": part["sheets"][0]["name"],
                    "keys": part["keys"],
                    "uploadable": False,
                    "exportable": True,
                    "advanced_url": value["advanced_url"],
                }
                for key, part in exported["components"].items()
            ]
            value["languages"] = [
                {
                    "code": code,
                    "name": code,
                    "strings": counts["strings"],
                    "status": "decisions" if counts["empty"] else "ready",
                    "decisions": counts["empty"],
                    "in_kit": True,
                }
                for code, counts in exported["per_language"].items()
            ]
        except DomainError as error:
            if error.status != 404:
                raise
        value.pop("revision", None)
        value.pop("persistent", None)
        return value

    static = app.state.settings.static_dir
    if (static / "assets").is_dir():
        app.mount("/console/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/")
    def root():
        return RedirectResponse("/console/projects")

    @app.get("/console/{path:path}", response_class=HTMLResponse)
    def console(path: str, request: Request):
        try:
            actor = app.state.store.actor(request.cookies.get("wizard_session", ""))
        except DomainError:
            destination = request.url.path
            if request.url.query:
                destination += "?" + request.url.query
            return RedirectResponse(
                "/auth/login?next=" + quote(destination, safe=""), status_code=303
            )
        candidate = (static / path).resolve()
        if (
            path
            and candidate.is_relative_to(static.resolve())
            and candidate.is_file()
            and candidate.suffix in {".js", ".css"}
        ):
            from fastapi.responses import FileResponse

            return FileResponse(candidate)
        index = static / "index.html"
        if not index.exists():
            return HTMLResponse(
                "<h1>Визард</h1><p>Соберите frontend: pnpm --dir console build</p>", status_code=503
            )
        page = index.read_text(encoding="utf-8")
        metadata = "".join(
            f'<meta name="{name}" content="{html.escape(value, quote=True)}">'
            for name, value in {
                "console-base": "/console/",
                "producer-console-root": "/console/",
                "producer-api-root": "/api/producer/",
                "producer-login-url": "/auth/login",
                "csrf-token": actor["csrf"],
            }.items()
        )
        response = HTMLResponse(page.replace("<!--WIZARD_META-->", metadata))
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
