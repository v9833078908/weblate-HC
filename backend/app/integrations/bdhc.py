# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
from urllib.parse import quote
from uuid import UUID

import httpx

from app.db import DomainError
from app.settings import read_secret_file


class BDHCClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def _get(self, path, params=None):
        try:
            token = read_secret_file(self.settings.bdhc_token_file)
            with httpx.Client(
                transport=self.transport, timeout=15, follow_redirects=False
            ) as client:
                response = client.get(
                    self.settings.bdhc_url.rstrip("/") + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                )
            if response.status_code != 200:
                raise DomainError(
                    "bdhc_unavailable", f"BDHC request returned HTTP {response.status_code}", 503
                )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Unexpected envelope")
            return body
        except (httpx.HTTPError, ValueError, OSError) as error:
            raise DomainError(
                "bdhc_unavailable", "BDHC is unavailable; continue with manual answers", 503
            ) from error

    def search(self, query):
        body = self._get("/api/v1/games", {"q": query, "limit": 20, "offset": 0})
        return [
            {
                "name": str(item.get("title") or item.get("name") or item.get("game_id") or ""),
                "ref": str(item.get("title_id") or item.get("game_id") or item.get("slug") or ""),
            }
            for item in body.get("items", [])
        ]

    def resolve(self, ref):
        body = self._get("/api/v2/games/resolve", {"ref": ref})
        try:
            return str(UUID(body["title_id"]))
        except (KeyError, ValueError) as error:
            raise DomainError("bdhc_schema", "Invalid BDHC identifier") from error

    def read_profile(self, title_id):
        title_id = str(UUID(title_id))
        game = self._get(f"/api/v1/games/{quote(title_id)}")
        card = self._get(f"/api/v2/games/{quote(title_id)}/knowledge")
        if not card.get("context_hash"):
            raise DomainError("bdhc_schema", "BDHC context hash missing")
        card["game"] = {"name": str(game.get("title") or game.get("name") or "")}
        return card


def normalize_locale(value):
    return str(value).replace("_", "-").casefold()


def map_bdhc_profile(card, manual, targets):
    answers = copy.deepcopy(manual)
    warnings = []
    locale_rules = {}
    if card.get("schema_version") != "12.7":
        return {
            "answers": answers,
            "locale_rules": {},
            "warnings": ["Unsupported BDHC schema; use manual answers"],
        }
    brief = card.get("brief") or {}
    proposals = {
        "genre": ", ".join(brief.get("genre_codes") or []),
        "setting": brief.get("setting_and_era"),
        "audience": brief.get("target_audience"),
    }
    if brief.get("genre_note"):
        proposals["genre"] = "; ".join(filter(None, [proposals["genre"], brief["genre_note"]]))
    global_profiles = [p for p in card.get("style_profiles", []) if p.get("locale") is None]
    global_style = copy.deepcopy(global_profiles[0]) if len(global_profiles) == 1 else {}
    if len(global_profiles) > 1:
        warnings.append("Multiple global profiles require manual selection")
    if global_style.get("register") in {"formal", "informal"}:
        proposals["register_ui"] = global_style["register"]
        proposals["register_dialogue"] = global_style["register"]
    elif global_style.get("register") == "mixed":
        warnings.append("Mixed register requires separate UI and dialogue answers")
    proposals["profanity"] = global_style.get("profanity_policy")
    limits = {
        "genre": 100,
        "setting": 600,
        "audience": 400,
        "register_ui": 100,
        "register_dialogue": 100,
        "profanity": 100,
    }
    for key, value in proposals.items():
        if not value:
            continue
        if len(str(value)) > limits[key]:
            warnings.append(f"{key}: shorten the BDHC value manually")
            continue
        answers[key] = str(value)
    for target in targets:
        overrides = [
            p
            for p in card.get("style_profiles", [])
            if p.get("locale") is not None
            and normalize_locale(p["locale"]) == normalize_locale(target)
        ]
        if len(overrides) > 1:
            warnings.append(f"{target}: multiple locale profiles require selection")
            continue
        rules = copy.deepcopy(global_style)
        if overrides:
            for key, value in overrides[0].items():
                if value is not None:
                    rules[key] = copy.deepcopy(value)
        for key in rules.get("not_applicable_fields", []):
            rules.pop(key, None)
        rules["voices"] = [v for v in rules.get("voices", []) if not v.get("is_disabled")]
        for key in [
            "profile_id",
            "title_id",
            "created_at",
            "updated_at",
            "source",
            "content_locale",
        ]:
            rules.pop(key, None)
        locale_rules[target] = rules
    return {"answers": answers, "locale_rules": locale_rules, "warnings": warnings}
