# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def test_partial_bdhc_preserves_manual_answers_and_exact_locale():
    from app.integrations.bdhc import map_bdhc_profile

    card = {
        "schema_version": "12.7",
        "context_hash": "hash",
        "brief": {
            "genre_codes": ["Strategy"],
            "setting_and_era": "Space",
            "target_audience": "Adults",
            "player_goal": "Win",
        },
        "style_profiles": [
            {
                "locale": None,
                "register": "mixed",
                "profanity_policy": "none",
                "dnt_brand_terms": ["Hero"],
            },
            {"locale": "ja", "locale_notes": "Use polite form", "dnt_brand_terms": []},
        ],
    }
    result = map_bdhc_profile(
        card, {"player_role": "Commander", "register_ui": "formal"}, ["ja", "ko"]
    )
    assert result["answers"]["player_role"] == "Commander"
    assert result["answers"]["register_ui"] == "formal"
    assert result["answers"]["genre"] == "Strategy"
    assert result["locale_rules"]["ja"]["dnt_brand_terms"] == []
    assert result["locale_rules"]["ko"]["dnt_brand_terms"] == ["Hero"]
    unknown = map_bdhc_profile({"schema_version": "99"}, {"setting": "Keep"}, [])
    assert unknown["answers"] == {"setting": "Keep"}
    assert unknown["warnings"]


def test_bdhc_timeout_and_token_stay_on_backend(tmp_path):
    import httpx
    import pytest
    from app.db import DomainError
    from app.integrations.bdhc import BDHCClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("private-test-token")

    def handler(request):
        assert request.headers["authorization"] == "Bearer private-test-token"
        assert request.method == "GET"
        raise httpx.ReadTimeout("private-test-token", request=request)

    client = BDHCClient(
        Settings(bdhc_token_file=str(secret)), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DomainError) as error:
        client.search("тест")
    assert "private-test-token" not in error.value.detail


def test_preview_is_confirmed_once_and_bound_to_profile(tmp_path, monkeypatch):
    from app.integrations.bdhc import BDHCClient
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        BDHCClient,
        "read_profile",
        lambda _, title_id: {
            "schema_version": "12.7",
            "context_hash": "test-hash",
            "brief": {"setting_and_era": "Test space"},
            "style_profiles": [{"locale": None, "locale_notes": "Global style rule"}],
        },
    )
    app = create_app(Settings(data_dir=tmp_path))
    app.state.store.create_user("producer", "a-long-test-password")
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        ).json()["csrf_token"]
        headers = {"X-CSRFToken": csrf}
        slug = client.post(
            "/api/producer/projects/", json={"name": "тест"}, headers=headers
        ).json()["slug"]
        path = f"/api/producer/projects/{slug}"
        app.state.store.save(
            "draft",
            "selected-targets",
            "producer",
            {"project": slug, "kind": "string", "answers": {"included_target_languages": ["ja"]}},
        )
        profile = client.get(path + "/profile/").json()
        preview = client.post(
            path + "/bdhc-preview/",
            headers=headers,
            json={
                "revision": profile["revision"],
                "title_id": "00000000-0000-4000-8000-000000000001",
            },
        )
        assert preview.status_code == 200
        proposed = preview.json()
        assert proposed["locale_rules_diff"][0]["language"] == "ja"
        assert proposed["locale_rules_diff"][0]["after"]["locale_notes"] == "Global style rule"
        assert proposed["provenance"]["context_hash"] == "test-hash"
        assert client.get(path + "/profile/").json()["answers"] == {}
        body = {"revision": profile["revision"], "preview_id": preview.json()["preview_id"]}
        applied = client.post(path + "/bdhc-apply/", headers=headers, json=body)
        assert applied.status_code == 200, applied.text
        assert applied.json()["answers"]["setting"] == "Test space"
        assert applied.json()["bdhc"]["context_hash"] == "test-hash"
        assert client.post(path + "/bdhc-apply/", headers=headers, json=body).status_code == 409
