# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time

from pydantic import ValidationError

from app.db import DomainError, revision
from app.models import Record
from app.schemas import TranslationProfile


def merge_remote_profile(current, generated, targets):
    """Preserve every non-selected locale; never forward infrastructure secrets."""
    instructions = dict(current.get("language_instructions", {}))
    instructions.update(
        {
            code: text
            for code, text in generated.get("language_instructions", {}).items()
            if code in targets
        }
    )
    return {
        "persona": generated["persona"],
        "style": generated["style"],
        "language_instructions": instructions,
    }


def profile_key(slug):
    return "profile:" + slug


def get_profile(store, owner, slug, engine):
    try:
        profile = store.get("profile", profile_key(slug), owner)
    except DomainError as e:
        if e.status != 404:
            raise
        profile = store.save(
            "profile",
            profile_key(slug),
            owner,
            {
                "answers": {},
                "generated": None,
                "generation_mode": "manual",
                "bdhc": None,
                "locale_rules": {},
            },
        )
    required = [
        key
        for key in ["register_ui", "register_dialogue", "profanity"]
        if not profile["answers"].get(key)
    ]
    return {
        **profile,
        "missing_required": required,
        "profile_state": "configured"
        if not required and profile["generated"] and not profile.get("needs_review_fields")
        else "missing",
        "engine": engine,
        "summary": "; ".join(str(v) for v in profile["answers"].values() if v),
        "judge_context_changed": False,
    }


def validate_generated(value, targets):
    try:
        profile = TranslationProfile.model_validate(value).model_dump()
    except ValidationError as error:
        raise DomainError(
            "profile_instructions", "The generated profile has invalid fields or lengths"
        ) from error
    if any(
        code not in targets or len(text) > 1000 or not text.strip()
        for code, text in profile["language_instructions"].items()
    ):
        raise DomainError(
            "profile_instructions",
            "Instructions must belong to selected languages and be at most 1000 characters",
        )
    return profile


def selected_targets(store, owner, slug):
    drafts = [
        d for d in store.list("draft", owner) if d["project"] == slug and d["kind"] == "string"
    ]
    return (drafts[-1]["answers"].get("included_target_languages") or []) if drafts else []


def base_profile(profile):
    value = {
        key: profile[key]
        for key in ["answers", "generated", "generation_mode", "bdhc", "locale_rules"]
    }
    value["generation_input_key"] = profile.get("generation_input_key")
    for key in (
        "field_origins",
        "generation_binding",
        "needs_review_fields",
        "generation_metadata",
    ):
        if key in profile:
            value[key] = copy.deepcopy(profile[key])
    return value


def _write(record, value):
    record.data, record.revision = copy.deepcopy(value), revision(value)


def _binding(input):
    return {
        k: input.get(k)
        for k in (
            "source_language",
            "source_languages",
            "target_languages",
            "game_title",
            "upload_token",
            "upload_revision",
            "context_hash",
            "manifest_hash",
        )
    }


def commit_prepared_profile(store, owner, slug, job_id):
    from app.services.profile_jobs import public_job

    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = session.get(Record, job_id)
        if (
            not job
            or job.kind != "profile_generation"
            or job.owner != owner
            or job.data["project"] != slug
        ):
            raise DomainError("not_found", "Not found", 404)
        value = copy.deepcopy(job.data)
        if value["state"] == "ready":
            return public_job(value)
        if value["state"] not in {"queued", "running"} or not value["result"]:
            raise DomainError("profile_not_ready", "No validated profile result", 409)
        input = value["input"]
        record = session.get(Record, profile_key(slug))
        draft = session.get(Record, input["upload_token"])
        if (
            not record
            or record.owner != owner
            or record.revision != input["profile_revision"]
            or not draft
            or draft.owner != owner
            or draft.kind != "draft"
            or draft.revision != input["upload_revision"]
            or draft.data["expires"] <= time.time()
        ):
            value.update(state="stale", lease_until=0)
            value["warnings"].append("stale-revision")
            _write(job, value)
            return public_job(value)
        current = copy.deepcopy(record.data)
        origins = current.setdefault("field_origins", {})
        mode = value["generation_mode"]
        answers = copy.deepcopy(current["answers"])
        for key, answer in input["answers"].items():
            field = "answers." + key
            if (
                not answers.get(key)
                or origins.get(field) in {"agent", "template", "bdhc"}
                or input.get("answer_origins", {}).get(key) == "manual"
            ):
                answers[key] = answer
                origins[field] = input.get("answer_origins", {}).get(key, "template")
        old = current.get("generated") or {}
        generated = copy.deepcopy(old)
        review = []
        changed_context = (current.get("generation_binding") or {}) != _binding(input)
        for key in ("persona", "style"):
            if old.get(key) and origins.get(key, "manual") == "manual":
                if key == "persona" and changed_context:
                    review.append(key)
            else:
                generated[key] = value["result"][key]
                origins[key] = mode
        languages = generated.setdefault("language_instructions", {})
        for code in input["target_languages"]:
            field = "language_instructions." + code
            if not languages.get(code) or origins.get(field, "manual") != "manual":
                languages[code] = value["result"]["language_instructions"][code]
                origins[field] = mode
        current.update(
            answers=answers,
            generated=generated,
            generation_mode=mode,
            generation_input_key=value["input_key"],
            generation_binding=_binding(input),
            needs_review_fields=review,
            generation_metadata=value.get("metadata", {}),
            locale_rules=input["locale_rules"],
        )
        if input.get("context_snapshot_id"):
            snapshot = session.get(Record, input["context_snapshot_id"])
            current["bdhc"] = {
                k: snapshot.data[k] for k in ("title_id", "schema_version", "context_hash")
            }
        _write(record, current)
        value.update(
            state="ready",
            proposal=value["result"],
            result=generated,
            auto_saved=True,
            profile_revision=record.revision,
            lease_until=0,
        )
        if review:
            value["warnings"].append("manual_persona_requires_review")
        _write(job, value)
        return public_job(value)


def commit_profile(store, owner, slug, body, engine):
    from app.services.profile_agent import build_profile_input, validate_agent_result

    input = build_profile_input(store, owner, slug, body)
    generated = validate_agent_result(body.generated.model_dump(), input, generated_proposal=False)
    with store.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        record = session.get(Record, profile_key(slug))
        draft = session.get(Record, body.upload_token)
        if record.revision != body.revision or draft.revision != body.upload_revision:
            raise DomainError("stale-revision", "Profile or loc-kit changed", 409)
        current = copy.deepcopy(record.data)
        origins = current.setdefault("field_origins", {})
        old_answers = current["answers"]
        answers = input["answers"]
        if "answers.player_role" not in body.edited_fields:
            answers["player_role"] = old_answers.get("player_role", "")
        for key, value in answers.items():
            field = "answers." + key
            if value != old_answers.get(key) or field in body.edited_fields:
                origins[field] = "manual"
        old = current.get("generated") or {}
        for key in ("persona", "style"):
            if generated[key] != old.get(key) or key in body.edited_fields:
                origins[key] = "manual"
        for code, text in generated["language_instructions"].items():
            field = "language_instructions." + code
            if (
                text != old.get("language_instructions", {}).get(code)
                or field in body.edited_fields
            ):
                origins[field] = "manual"
        generated["language_instructions"] = {
            **old.get("language_instructions", {}),
            **generated["language_instructions"],
        }
        current.update(
            answers=answers,
            generated=generated,
            generation_mode="manual",
            generation_binding=_binding(input),
            needs_review_fields=[],
        )
        current["locale_rules"] = input["locale_rules"]
        if input.get("context_snapshot_id"):
            snapshot = session.get(Record, input["context_snapshot_id"])
            current["bdhc"] = {
                k: snapshot.data[k] for k in ("title_id", "schema_version", "context_hash")
            }
        _write(record, current)
    return get_profile(store, owner, slug, engine)
