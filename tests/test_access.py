# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def test_profile_generation_retry_and_changed_evidence(tmp_path, monkeypatch):
    import app.routes.profiles as profiles
    from app.db import DomainError
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    calls = []

    def generate(settings, answers, evidence, targets, rules):
        calls.append((evidence, targets))
        if len(calls) == 1:
            raise DomainError("llm_failed", "Temporary failure")
        return {
            "persona": "Translator",
            "style": "Clear",
            "language_instructions": {code: "Translate clearly" for code in targets},
        }

    monkeypatch.setattr(profiles, "generate_profile", generate)
    app = create_app(Settings(data_dir=tmp_path, llm_token_file="unused", llm_model="test"))
    app.state.store.create_user("producer", "a-long-test-password")
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        ).json()["csrf_token"]
        headers = {"X-CSRFToken": csrf}
        slug = client.post(
            "/api/producer/projects/", json={"name": "тест"}, headers=headers
        ).json()["slug"]
        path = f"/api/producer/projects/{slug}/profile/"
        answers = {"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"}

        def save():
            revision = client.get(path).json()["revision"]
            return client.patch(
                path, json={"revision": revision, "answers": answers}, headers=headers
            )

        assert save().json()["code"] == "llm_failed"
        assert save().json()["profile_state"] == "configured"
        assert len(calls) == 2
        assert save().status_code == 200 and len(calls) == 2
        app.state.store.save(
            "draft",
            "evidence",
            "producer",
            {
                "project": slug,
                "kind": "string",
                "answers": {"included_target_languages": ["ja"]},
                "manifest": {
                    "components": [{"units": [{"key": "one", "values": {"en": "Changed text"}}]}]
                },
            },
        )
        assert save().json()["generated"]["language_instructions"] == {"ja": "Translate clearly"}
        assert len(calls) == 3


def test_profile_llm_schema_is_closed_for_selected_languages(tmp_path, monkeypatch):
    import json

    import httpx
    from app.integrations.llm import generate_profile
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-secret")

    def handler(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        assert set(schema["required"]) == {"persona", "style", "language_instructions"}
        instructions = schema["properties"]["language_instructions"]
        assert instructions["additionalProperties"] is False
        assert set(instructions["required"]) == set(instructions["properties"]) == {"ja", "ru"}
        for spec in instructions["properties"].values():
            assert spec["type"] == "string"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "persona": "Translator",
                                    "style": "Clear",
                                    "language_instructions": {"ja": "Polite", "ru": "Clear"},
                                }
                            )
                        }
                    }
                ]
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    assert (
        generate_profile(
            Settings(llm_token_file=str(secret), llm_model="test"), {}, [], ["ja", "ru"], {}
        )["persona"]
        == "Translator"
    )


def test_session_csrf_and_project_ownership(tmp_path):
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    app.state.store.create_user("producer", "a-long-test-password")
    app.state.store.create_user("other", "another-long-password")
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/producer/me/").status_code == 401
        response = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        )
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        csrf = response.json()["csrf_token"]
        assert client.post("/api/producer/projects/", json={"name": "тест"}).status_code == 403
        headers = {"X-CSRFToken": csrf, "Origin": "http://testserver"}
        project = client.post("/api/producer/projects/", json={"name": "тест"}, headers=headers)
        assert project.status_code == 201
        slug = project.json()["slug"]
        assert slug.startswith("test-")
        assert client.get(f"/api/producer/projects/{slug}/").json()["name"] == "тест — " + slug[-8:]
        client.post("/auth/logout", headers=headers)
        assert client.get("/api/producer/me/").status_code == 401
        client.post("/auth/login", json={"username": "other", "password": "another-long-password"})
        assert client.get(f"/api/producer/projects/{slug}/").status_code == 404


def test_origin_has_no_testing_hostname_exception(tmp_path):
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    app = create_app(Settings(data_dir=tmp_path, public_origin="https://wizard.example"))
    app.state.store.create_user("producer", "a-long-test-password")
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": "producer", "password": "a-long-test-password"},
        )
        assert response.status_code == 403
        csrf = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        ).json()["csrf_token"]
        assert (
            client.post(
                "/api/producer/projects/",
                headers={"Origin": "http://testserver", "X-CSRFToken": csrf},
                json={"name": "тест"},
            ).status_code
            == 403
        )
