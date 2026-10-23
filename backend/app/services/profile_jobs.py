# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
import uuid

from sqlalchemy import select

from app.db import DomainError, revision
from app.integrations.llm import generate_profile_from_input
from app.models import Record
from app.services.profile_agent import (
    build_profile_input,
    load_profile_skills,
    template_profile,
    validate_agent_result,
)


def put(record, value):
    record.data, record.revision = copy.deepcopy(value), revision(value)


def public_job(value, current=None):
    if value["state"] == "ready" and current is not None:
        value = copy.deepcopy(value)
        if current.data.get("generation_input_key") == value["input_key"]:
            value.update(result=current.data["generated"], profile_revision=current.revision)
        else:
            value.update(state="stale", auto_saved=False)
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "job_id",
            "input_key",
            "state",
            "result",
            "proposal",
            "generation_mode",
            "warnings",
            "auto_saved",
            "profile_revision",
        )
    }


def semantic_key(input, settings):
    return revision(
        [
            {
                k: v
                for k, v in input.items()
                if k
                not in {
                    "profile_revision",
                    "upload_revision",
                    "context_snapshot_id",
                    "warnings",
                    "answer_origins",
                }
            },
            settings.llm_url,
            settings.llm_model,
            settings.llm_model_version,
            settings.llm_reasoning_effort,
            settings.llm_max_completion_tokens,
            bool(settings.profile_agent_enabled and settings.llm_token_file and settings.llm_model),
        ]
    )


def prepare_profile(store, settings, owner, slug, body):
    from app.services.run_isolation import assert_project_not_isolated

    assert_project_not_isolated(store, local_slug=slug)
    input = build_profile_input(store, owner, slug, body)
    if any("profiles require" in warning for warning in input["warnings"]):
        raise DomainError(
            "profile_context_conflict", "BDHC language rules conflict; select rules manually"
        )
    input_key = semantic_key(input, settings)
    index_id = "profile-generation-index:" + revision([owner, slug, input_key])
    enabled = bool(
        settings.profile_agent_enabled and settings.llm_token_file and settings.llm_model
    )
    publish = False
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = session.get(Record, "profile:" + slug)
        draft = session.get(Record, body.upload_token)
        if current.revision != body.revision or draft.revision != body.upload_revision:
            raise DomainError("stale-revision", "Profile or loc-kit changed", 409)
        index = session.get(Record, index_id)
        if index:
            job = session.get(Record, index.data["job_id"])
            value = copy.deepcopy(job.data)
            job_id = job.id
            if value["state"] in {"unknown", "failed", "running"}:
                return public_job(value, current)
            if (
                value["state"] == "ready"
                and current.data.get("generation_input_key") == input_key
                and all(
                    current.data.get("field_origins", {}).get("answers." + key) == origin
                    for key, origin in input["answer_origins"].items()
                )
            ):
                return public_job(value, current)
            candidate = value.get("proposal") or value.get("result")
            if value.get("dispatch_started") and not candidate:
                value.update(state="unknown", auto_saved=False)
                put(job, value)
                return public_job(value, current)
            value.update(
                input=input,
                profile_revision=body.revision,
                state="queued",
                warnings=list(input["warnings"]),
                auto_saved=False,
                lease_until=0,
            )
            if candidate:
                value["result"] = validate_agent_result(candidate, input)
                value.update(state="running", lease_until=time.time() + 180)
                publish = True
            put(job, value)
        else:
            job_id = "profile-generation:" + str(uuid.uuid4())
            value = {
                "job_id": job_id,
                "project": slug,
                "input_key": input_key,
                "input": input,
                "state": "queued",
                "result": None,
                "warnings": list(input["warnings"]),
                "auto_saved": False,
                "profile_revision": body.revision,
                "dispatch_started": False,
                "lease_until": 0,
                "created": time.time(),
                "generation_mode": "agent" if enabled else "template",
            }
            if not enabled:
                value["result"] = template_profile(input)
                value["warnings"].append("profile_agent_not_configured")
                value.update(state="running", lease_until=time.time() + 180)
                publish = True
            session.add(
                Record(
                    id=job_id,
                    kind="profile_generation",
                    owner=owner,
                    data=value,
                    revision=revision(value),
                )
            )
            session.add(
                Record(
                    id=index_id,
                    kind="profile_generation_index",
                    owner=owner,
                    data={"job_id": job_id},
                    revision=revision(job_id),
                )
            )
    if publish:
        from app.services.profiles import commit_prepared_profile

        return commit_prepared_profile(store, owner, slug, job_id)
    with store.session() as session:
        return public_job(value, session.get(Record, "profile:" + slug))


def get_profile_job(store, owner, slug, job_id):
    value = store.get("profile_generation", job_id, owner)
    if value["project"] != slug:
        raise DomainError("not_found", "Not found", 404)
    with store.session() as session:
        return public_job(value, session.get(Record, "profile:" + slug))


def process_profile_once(store, settings, generator=generate_profile_from_input):
    now = time.time()
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        jobs = list(session.scalars(select(Record).where(Record.kind == "profile_generation")))
        from app.services.run_isolation import ensure_project_not_isolated

        available = []
        for candidate in jobs:
            try:
                ensure_project_not_isolated(session, "", candidate.data["project"])
            except DomainError as error:
                if error.code != "run_isolated":
                    raise
                continue
            available.append(candidate)
        jobs = available
        for job in jobs:
            value = copy.deepcopy(job.data)
            if value["state"] == "running" and value["lease_until"] <= now:
                uncertain = value["dispatch_started"] and not value.get("result")
                value["state"] = "unknown" if uncertain else "queued"
                value["warnings"].append("llm_unknown" if uncertain else "worker_recovered")
                put(job, value)
                return True
        active = {job.owner for job in jobs if job.data["state"] == "running"}
        job = next(
            (
                job
                for job in sorted(jobs, key=lambda j: j.data["created"])
                if job.data["state"] == "queued" and job.owner not in active
            ),
            None,
        )
        if not job:
            return False
        value = copy.deepcopy(job.data)
        input = value["input"]
        from app.services.run_isolation import ensure_project_not_isolated

        ensure_project_not_isolated(session, "", value["project"])
        current = session.get(Record, "profile:" + value["project"])
        draft = session.get(Record, input["upload_token"])
        if (
            not current
            or current.revision != input["profile_revision"]
            or not draft
            or draft.revision != input["upload_revision"]
            or draft.data["expires"] <= now
            or load_profile_skills()[1] != input["skills_version"]
            or semantic_key(input, settings) != value["input_key"]
        ):
            value.update(state="stale", warnings=value["warnings"] + ["stale-revision"])
            put(job, value)
            return True
        value.update(state="running", lease_until=now + 180)
        put(job, value)
        job_id, owner, slug = job.id, job.owner, value["project"]
    if value.get("result"):
        from app.services.profiles import commit_prepared_profile

        commit_prepared_profile(store, owner, slug, job_id)
        return True
    # Durable intent BEFORE network dispatch, independent of browser lifetime.
    saved = store.get("profile_generation", job_id, owner)
    expected = saved.pop("revision")
    saved["dispatch_started"] = True
    store.save("profile_generation", job_id, owner, saved, expected)
    try:
        response = generator(settings, value["input"])
        result = response.get("generated", response)
        generated = validate_agent_result(result, value["input"])
    except Exception as error:
        record = store.get("profile_generation", job_id, owner)
        expected = record.pop("revision")
        known = isinstance(error, DomainError) and error.code not in {"llm_unknown"}
        record.update(state="failed" if known else "unknown", lease_until=0)
        record["warnings"].append(error.code if isinstance(error, DomainError) else "llm_unknown")
        store.save("profile_generation", job_id, owner, record, expected)
        return True
    record = store.get("profile_generation", job_id, owner)
    expected = record.pop("revision")
    record.update(result=generated, metadata=response.get("metadata", {}))
    store.save("profile_generation", job_id, owner, record, expected)
    from app.services.profiles import commit_prepared_profile

    commit_prepared_profile(store, owner, slug, job_id)
    return True
