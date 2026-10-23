# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

import pytest
from app.integrations.bdhc import BDHCClient
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient

TITLE = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def context_client(tmp_path, monkeypatch):
    monkeypatch.setattr(BDHCClient, "search", lambda _, q: [{"name": "Dead Shell", "ref": TITLE}])
    monkeypatch.setattr(
        BDHCClient,
        "read_profile",
        lambda _, title: {
            "schema_version": "12.7",
            "context_hash": "card-v1",
            "game": {"name": "Dead Shell"},
            "brief": {"genre_codes": ["RPG"], "setting_and_era": "Space"},
            "style_profiles": [{"locale": None, "register": "formal", "profanity_policy": "mild"}],
        },
    )
    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    store.create_user("producer", "a-long-test-password")
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        ).json()["csrf_token"]
        headers = {"X-CSRFToken": csrf}
        slug = client.post(
            "/api/producer/projects/", json={"name": "Dead Shell"}, headers=headers
        ).json()["slug"]
        draft = store.save(
            "draft",
            "kit",
            "producer",
            {
                "project": slug,
                "token": "kit",
                "kind": "string",
                "expires": time.time() + 3600,
                "answers": {"source_language": "ru", "included_target_languages": ["zh_Hant"]},
                "manifest": {
                    "components": [
                        {
                            "sheet": "UI",
                            "units": [
                                {"key": "start", "values": {"ru": "Начать", "de": "PRIVATE TARGET"}}
                            ],
                        }
                    ]
                },
            },
        )
        profile = client.get(f"/api/producer/projects/{slug}/profile/").json()
        yield client, store, slug, headers, profile, draft


def resolve(fixture, **overrides):
    client, _, slug, headers, profile, _ = fixture
    return client.post(
        f"/api/producer/projects/{slug}/context/resolve/",
        headers=headers,
        json={"revision": profile["revision"], "upload_token": "kit", **overrides},
    )


def test_exact_project_match_captures_context_without_changing_profile(context_client):
    response = resolve(context_client)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["state"] == "matched"
    assert value["title"] == "Dead Shell"
    assert value["answers"]["genre"] == "RPG"
    client, store, slug, _, old, _ = context_client
    assert (
        client.get(f"/api/producer/projects/{slug}/profile/").json()["revision"] == old["revision"]
    )
    assert store.get("profile_context", value["snapshot_id"], "producer")["project"] == slug


@pytest.mark.parametrize(
    "names,state",
    [
        (["Dead Shell Deluxe"], "choice_required"),
        (["Dead Shell", "DEAD SHELL"], "choice_required"),
        ([], "not_found"),
        (["  dead   shell  "], "matched"),
    ],
)
def test_similar_or_ambiguous_names_are_not_silently_linked(
    context_client, monkeypatch, names, state
):
    monkeypatch.setattr(
        BDHCClient, "search", lambda _, q: [{"name": n, "ref": TITLE} for n in names]
    )
    response = resolve(context_client)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == state
    if state != "matched":
        assert response.json()["snapshot_id"] is None


def test_manual_title_overrides_project_name(context_client):
    response = resolve(context_client, title_id=TITLE)
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Dead Shell"


def test_resolution_refuses_wrong_owner_draft_and_stale_profile(context_client):
    _, store, slug, _, profile, _ = context_client
    store.save("draft", "foreign", "other", {"project": slug, "expires": time.time() + 100})
    assert resolve(context_client, upload_token="foreign").status_code == 404
    assert resolve(context_client, revision="stale").status_code == 409
    store.save("draft", "expired", "producer", {"project": slug, "expires": 0})
    assert resolve(context_client, upload_token="expired").status_code == 404


def test_unknown_schema_is_not_published_as_a_snapshot(context_client, monkeypatch):
    monkeypatch.setattr(
        BDHCClient, "read_profile", lambda _, title: {"schema_version": "99", "context_hash": "x"}
    )
    assert resolve(context_client).status_code == 400


def test_game_name_is_preserved_when_reading_knowledge(tmp_path):
    client = BDHCClient(Settings(data_dir=tmp_path))
    client._get = lambda path, params=None: (
        {"title": "Dead Shell"}
        if "/api/v1/" in path
        else {"context_hash": "hash", "schema_version": "12.7"}
    )
    assert client.read_profile(TITLE).get("game", {}).get("name") == "Dead Shell"


def test_oversized_optional_examples_are_omitted_from_locale_rules(context_client, monkeypatch):
    _, store, _, _, _, draft = context_client
    card = {
        "schema_version": "12.7",
        "context_hash": "large",
        "game": {"name": "Dead Shell"},
        "brief": {},
        "style_profiles": [{"locale": None, "example_lines": ["x" * 100000]}],
    }
    monkeypatch.setattr(BDHCClient, "read_profile", lambda *_: card)
    response = resolve(context_client)
    assert response.status_code == 200
    snapshot = store.get("profile_context", response.json()["snapshot_id"], "producer")
    import json

    assert len(json.dumps(snapshot["locale_rules"])) < 24000
    assert "x" * 1000 not in json.dumps(snapshot)
