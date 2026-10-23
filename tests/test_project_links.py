# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import httpx
import pytest
from app.db import DomainError, Store
from app.integrations.weblate import WeblateClient
from app.settings import Settings


def test_project_search_matches_partial_name_and_slug_without_writes(tmp_path):
    from app.services.project_links import discover_projects

    token = tmp_path / "token"
    token.write_text("dummy")
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": "Pirate Ships", "slug": "pirate-ships"},
                    {"name": "Other Game", "slug": "other"},
                ],
                "next": None,
            },
        )

    remote = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    assert discover_projects(remote, "  PIRATE  ", match="contains") == [
        {"name": "Pirate Ships", "slug": "pirate-ships"}
    ]
    assert discover_projects(remote, "pirate-sh", match="contains")[0]["slug"] == "pirate-ships"
    assert discover_projects(remote, "Pirate") == []
    assert discover_projects(remote, "  ", match="contains") == []
    assert set(calls) == {"GET"}


def test_search_api_defaults_to_exact_and_validates_match_mode(tmp_path, monkeypatch):
    from app.main import create_app
    from app.routes import project_links
    from fastapi.testclient import TestClient

    class Remote:
        def collect(self, path):
            assert path == "projects/"
            return [{"name": "Pirate Ships", "slug": "pirate-ships"}]

    monkeypatch.setattr(project_links, "remote", lambda request: Remote())
    app = create_app(Settings(data_dir=tmp_path))
    app.state.store.create_user("owner", "test-long-password")
    with TestClient(app) as client:
        path = "/api/producer/remote-projects/"
        assert client.get(path, params={"name": "Pirate", "match": "contains"}).status_code == 401
        client.post("/auth/login", json={"username": "owner", "password": "test-long-password"})
        assert client.get(path, params={"name": "Pirate"}).json() == {"results": []}
        assert (
            client.get(path, params={"name": "Pirate", "match": "contains"}).json()["results"][0][
                "slug"
            ]
            == "pirate-ships"
        )
        assert client.get(path, params={"name": "Pirate", "match": "invalid"}).status_code == 422
        assert app.state.store.list("project", "owner") == []


def test_existing_project_snapshot_and_confirmation_are_read_only(tmp_path):
    from app.services import project_links

    secret = tmp_path / "token"
    secret.write_text("dummy-token")
    calls = []

    def handler(request):
        calls.append(request.method)
        path = request.url.path.removeprefix("/api/")
        responses = {
            "projects/": {"results": [{"name": "Game", "slug": "game"}], "next": None},
            "projects/game/": {"slug": "game", "name": "Game"},
            "projects/game/components/": {
                "results": [
                    {
                        "slug": "ui",
                        "name": "UI",
                        "source_language": {"code": "ru"},
                        "file_format": "po-mono",
                        "manage_units": True,
                        "edit_template": True,
                    }
                ]
            },
            "components/game/ui/translations/": [
                {"language": {"code": "ru"}, "is_source": True},
                {"language": {"code": "de"}},
            ],
            "translations/game/ui/ru/units/": {
                "results": [
                    {
                        "id": 1,
                        "source": ["key"],
                        "target": ["Привет"],
                        "context": "",
                        "source_unit": "https://l10n.herocraft.com/api/units/1/",
                    }
                ]
            },
            "translations/game/ui/de/units/": {
                "results": [
                    {
                        "id": 2,
                        "source": ["key"],
                        "target": ["Hallo"],
                        "source_unit": "https://l10n.herocraft.com/api/units/1/",
                    }
                ]
            },
            "projects/game/machinery_settings/": {
                "openrouter": {"persona": "existing", "language_instructions": {"de": "German"}}
            },
        }
        return httpx.Response(200, json=responses[path])

    remote = WeblateClient(
        Settings(data_dir=tmp_path, weblate_token_file=str(secret)), httpx.MockTransport(handler)
    )
    store = Store(tmp_path / "data")
    store.save("project", "local", "owner", {"slug": "local", "name": "Game"})
    assert project_links.discover_projects(remote, "game")[0]["slug"] == "game"
    preview = project_links.preview_connection(store, "owner", "local", remote, "game")
    assert preview["snapshot"]["components"]["ui"]["units"][0]["source"] == "Привет"
    with pytest.raises(DomainError):
        project_links.confirm_connection(
            store, "other-owner", "local", remote, preview["id"], preview["snapshot_hash"]
        )
    link = project_links.confirm_connection(
        store, "owner", "local", remote, preview["id"], preview["snapshot_hash"]
    )
    assert link["read_only"] is True
    assert link["snapshot"]["components"]["ui"]["units"][0]["targets"] == {"de": "Hallo"}
    assert set(calls) == {"GET"}
    assert (
        store.get("project", "local", "owner")["advanced_url"]
        == "https://l10n.herocraft.com/projects/game/"
    )


def test_pagination_upgrades_same_host_http_links_and_never_follows_foreign_host(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("dummy")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"results": [{"id": 2}], "next": None})
        return httpx.Response(
            200,
            json={"results": [{"id": 1}], "next": "http://l10n.herocraft.com/api/projects/?page=2"},
        )

    remote = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(handler))
    assert remote.collect("projects/") == [{"id": 1}, {"id": 2}]
    assert all(url.startswith("https://l10n.herocraft.com/") for url in seen)
    with pytest.raises(ValueError):
        remote.request("GET", "https://foreign.example/api/projects/")


def test_legacy_binding_uses_confirmed_journal_and_stays_read_only(tmp_path):
    from app.models import Job
    from app.services.project_links import get_link

    store = Store(tmp_path)
    store.save("project", "local", "owner", {"name": "Game", "slug": "local"})
    with store.session() as session:
        session.add(
            Job(
                id="old",
                owner="owner",
                idempotency_key="old",
                status="failed",
                data={
                    "project": "local",
                    "remote_project": "real-game",
                    "created": "2026-10-01",
                    "operations": {"project": {"state": "confirmed"}},
                    "snapshot": {"name": "Game", "manifest": {"components": []}},
                },
            )
        )
    link = get_link(store, "owner", "local")
    assert link["remote_slug"] == "real-game"
    assert link["read_only"] is True
    assert get_link(store, "owner", "different") is None
