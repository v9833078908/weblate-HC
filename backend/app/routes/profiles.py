# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import require_actor, require_project_access
from app.db import DomainError, revision
from app.integrations.llm import generate_profile
from app.schemas import (
    ManualProfilePatch,
    ProfileCommitRequest,
    ProfilePatch,
    ProfilePrepareRequest,
)
from app.services.profile_jobs import get_profile_job, prepare_profile
from app.services.profiles import (
    base_profile,
    commit_profile,
    get_profile,
    profile_key,
    selected_targets,
    validate_generated,
)

router = APIRouter(prefix="/api/producer")


@router.post("/projects/{slug}/profile/prepare/")
def prepare(slug: str, body: ProfilePrepareRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return prepare_profile(
        request.app.state.store, request.app.state.settings, actor["username"], slug, body
    )


@router.get("/projects/{slug}/profile/preparations/{job_id}/")
def preparation(slug: str, job_id: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return get_profile_job(request.app.state.store, actor["username"], slug, job_id)


@router.post("/projects/{slug}/profile/commit/")
def commit(slug: str, body: ProfileCommitRequest, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return commit_profile(
        request.app.state.store, actor["username"], slug, body, request.app.state.settings.engine
    )


@router.get("/projects/{slug}/profile/")
def get(slug: str, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    return get_profile(
        request.app.state.store, actor["username"], slug, request.app.state.settings.engine
    )


@router.patch("/projects/{slug}/profile/")
def patch(slug: str, body: ProfilePatch, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    store = request.app.state.store
    owner = actor["username"]
    settings = request.app.state.settings
    profile = get_profile(store, owner, slug, settings.engine)
    if body.revision != profile["revision"]:
        raise DomainError("stale-revision", "Profile changed", 409)
    answers = body.answers.model_dump()
    value = base_profile(profile)
    if settings.profile_agent_enabled:
        value["answers"] = answers
        origins = value.setdefault("field_origins", {})
        for key, answer in answers.items():
            if answer != profile["answers"].get(key):
                origins["answers." + key] = "manual"
        value["needs_review_fields"] = ["answers"]
        store.save("profile", profile_key(slug), owner, value, body.revision)
        return get_profile(store, owner, slug, settings.engine)
    targets = selected_targets(store, owner, slug)
    drafts = [
        d for d in store.list("draft", owner) if d["project"] == slug and d["kind"] == "string"
    ]
    manifest = (drafts[-1].get("manifest") or {}) if drafts else {}
    evidence = [
        {"key": u["key"], "values": u["values"]}
        for c in manifest.get("components", [])
        for u in c["units"][:5]
    ][:10]
    input_key = revision([answers, manifest, targets, value["locale_rules"], settings.llm_model])
    same_answers = answers == profile["answers"]
    same_generation = profile.get("generation_input_key") == input_key
    if (
        same_answers
        and profile["generated"]
        and (profile["generation_mode"] == "manual" or same_generation)
    ):
        return profile
    if same_answers and not (settings.llm_token_file and settings.llm_model):
        return profile
    value.update(answers=answers, generated=None, generation_input_key=None)
    saved = store.save("profile", profile_key(slug), owner, value, body.revision)
    if settings.llm_token_file and settings.llm_model:
        generated = generate_profile(settings, answers, evidence, targets, value["locale_rules"])
        value["generated"] = validate_generated(generated, targets)
        if set(value["generated"]["language_instructions"]) != set(targets):
            raise DomainError(
                "profile_instructions", "Generate instructions for every selected language"
            )
        value["generation_mode"] = "llm"
        value["generation_input_key"] = input_key
        store.save("profile", profile_key(slug), owner, value, saved["revision"])
    return get_profile(store, owner, slug, settings.engine)


@router.patch("/projects/{slug}/profile/manual/")
def manual(slug: str, body: ManualProfilePatch, request: Request, actor=Depends(require_actor)):
    require_project_access(request, actor, slug)
    store = request.app.state.store
    owner = actor["username"]
    settings = request.app.state.settings
    profile = get_profile(store, owner, slug, settings.engine)
    value = base_profile(profile)
    value["generated"] = validate_generated(
        body.generated.model_dump(), selected_targets(store, owner, slug)
    )
    value["generation_mode"] = "manual"
    store.save("profile", profile_key(slug), owner, value, body.revision)
    return get_profile(store, owner, slug, settings.engine)
