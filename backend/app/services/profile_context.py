# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import json
import time
import unicodedata
import uuid

from app.db import DomainError
from app.integrations.bdhc import BDHCClient, map_bdhc_profile
from app.services.drafts import get_draft
from app.services.profiles import get_profile


def normalize_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def bounded_card(card):
    """Only knowledge fields enter the agent, never an integration envelope."""
    value = {k: copy.deepcopy(card.get(k)) for k in ("game", "brief", "style_profiles")}
    warnings = []
    if len(json.dumps(value, ensure_ascii=False)) > 24000:
        # Examples are optional; rules and facts must never be silently truncated.
        def strip_examples(item):
            if isinstance(item, dict):
                for key in list(item):
                    if key in {"examples", "example_lines", "sample_dialogue"}:
                        del item[key]
                    else:
                        strip_examples(item[key])
            elif isinstance(item, list):
                for child in item:
                    strip_examples(child)

        strip_examples(value)
        warnings.append("Optional BDHC examples omitted to fit the context limit")
    if len(json.dumps(value, ensure_ascii=False)) > 24000:
        raise DomainError(
            "context_too_large", "BDHC rules exceed the context limit; select context manually"
        )
    return value, warnings


def resolve_context(store, settings, owner, slug, body):
    project = store.get("project", slug, owner)
    profile = get_profile(store, owner, slug, settings.engine)
    if profile["revision"] != body.revision:
        raise DomainError("stale-revision", "Profile changed", 409)
    draft = get_draft(store, body.upload_token, owner)
    if draft["project"] != slug or draft.get("kind") != "string":
        raise DomainError("not_found", "Not found", 404)
    client = BDHCClient(settings)
    saved_title = (profile.get("bdhc") or {}).get("title_id")
    title_id = body.title_id or (saved_title if not body.query else None)
    matches = []
    if not title_id:
        query = (body.query or project["name"]).strip()
        # A pasted URL is explicit selection, not fuzzy automatic linking.
        if body.query and query.startswith(("https://", "http://")):
            title_id = client.resolve(query)
        else:
            matches = client.search(query)
            exact = [m for m in matches if normalize_name(m["name"]) == normalize_name(query)]
            if len(exact) != 1:
                return {
                    "state": "choice_required" if matches else "not_found",
                    "matches": matches,
                    "snapshot_id": None,
                    "title": None,
                    "answers": {},
                    "locale_rules": {},
                    "warnings": [],
                }
            title_id = exact[0]["ref"]
    try:
        title_id = str(uuid.UUID(title_id))
    except ValueError:
        title_id = client.resolve(title_id)
    card = client.read_profile(title_id)
    if card.get("schema_version") != "12.7":
        raise DomainError("bdhc_schema", "Unsupported BDHC schema; use manual context")
    facts, warnings = bounded_card(card)
    title = (card.get("game") or {}).get("name") or next(
        (m["name"] for m in matches if m["ref"] == title_id), ""
    )
    if not title:
        raise DomainError("bdhc_schema", "BDHC game name missing")
    suggestion = map_bdhc_profile(
        {**card, **facts}, {}, draft["answers"].get("included_target_languages", [])
    )
    # Legacy nonempty fields without provenance are manual, as are explicit edits.
    origins = profile.get("field_origins", {})
    answers = {
        **profile["answers"],
        **{
            k: v
            for k, v in suggestion["answers"].items()
            if not profile["answers"].get(k)
            or origins.get("answers." + k) in {"bdhc", "agent", "template"}
        },
    }
    snapshot_id = "profile-context:" + str(uuid.uuid4())
    value = {
        "project": slug,
        "upload_token": body.upload_token,
        "upload_revision": draft["revision"],
        "profile_revision": profile["revision"],
        "title_id": title_id,
        "title": title,
        "answers": answers,
        "locale_rules": suggestion["locale_rules"],
        "facts": facts,
        "context_hash": card["context_hash"],
        "schema_version": "12.7",
        "expires": time.time() + 3600,
        "warnings": warnings + suggestion["warnings"],
    }
    store.save("profile_context", snapshot_id, owner, value)
    return {
        "state": "matched",
        "matches": matches,
        "snapshot_id": snapshot_id,
        "title": title,
        "answers": answers,
        "locale_rules": suggestion["locale_rules"],
        "warnings": value["warnings"],
    }


def get_context_snapshot(store, owner, slug, snapshot_id):
    snapshot = store.get("profile_context", snapshot_id, owner)
    if snapshot["project"] != slug or snapshot["expires"] <= time.time():
        raise DomainError("context_expired", "Resolve the game card again", 409)
    return snapshot
