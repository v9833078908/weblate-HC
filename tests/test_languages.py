# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import httpx
import pytest
from app.db import DomainError
from app.integrations.weblate import WeblateClient
from app.main import create_app
from app.services import languages
from app.settings import Settings
from fastapi.testclient import TestClient

REAL_CATALOG_LOADER = languages.catalog_for_settings

CATALOG = [
    {"code": c, "name": c} for c in ("ru", "en", "de", "pt", "pt_BR", "es", "es_MX", "zh_Hans")
]


def test_catalog_readonly_pagination_and_auth(tmp_path):
    token = tmp_path / "token"
    token.write_text("test-only")
    seen = []

    def handler(request):
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Token test-only"
        seen.append(request.url.path)
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "results": CATALOG[:2],
                    "next": "https://l10n.herocraft.com/api/languages/?page=2",
                },
            )
        return httpx.Response(200, json={"results": CATALOG[2:], "next": None})

    remote = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    assert languages.load_language_catalog(remote) == CATALOG
    assert len(seen) == 2


@pytest.mark.parametrize(
    "raw,expected", [("pt-br", "pt_BR"), ("ES-mx", "es_MX"), ("zh-hans", "zh_Hans"), ("pt", "pt")]
)
def test_canonical_regional_codes(raw, expected):
    assert languages.canonical_language(raw, CATALOG) == expected


def test_unknown_and_source_target_blocked():
    with pytest.raises(DomainError) as error:
        languages.canonical_language("pt_MADEUP", CATALOG)
    assert error.value.code == "language_unknown"
    with pytest.raises(DomainError) as error:
        languages.validate_language_selection("ru", ["RU"], CATALOG)
    assert error.value.code == "language_source_target"
    assert languages.validate_language_selection("RU", ["pt-br", "pt_BR", "pt"], CATALOG) == (
        "ru",
        ["pt_BR", "pt"],
    )


def test_catalog_rejects_partial_and_ambiguous_response():
    class Remote:
        def collect(self, path):
            return [{"code": "en", "name": "English"}, {"code": "fr"}]

    with pytest.raises(DomainError):
        languages.load_language_catalog(Remote())
    Remote.collect = lambda self, path: [
        {"code": "pt_BR", "name": "Portuguese"},
        {"code": "pt-br", "name": "Other"},
    ]
    with pytest.raises(DomainError):
        languages.load_language_catalog(Remote())


def test_catalog_endpoint_and_patch_preserve_invalid_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(languages, "catalog_for_settings", lambda settings: CATALOG)
    app = create_app(Settings(data_dir=tmp_path))
    app.state.store.create_user("producer", "a-long-test-password")
    with TestClient(app) as client:
        assert client.get("/api/producer/languages/").status_code == 401
        csrf = client.post(
            "/auth/login", json={"username": "producer", "password": "a-long-test-password"}
        ).json()["csrf_token"]
        headers = {"X-CSRFToken": csrf}
        assert client.get("/api/producer/languages/").json() == {"results": CATALOG}
        slug = client.post(
            "/api/producer/projects/", json={"name": "languages"}, headers=headers
        ).json()["slug"]
        draft = client.post(
            f"/api/producer/projects/{slug}/uploads/",
            files={"files": ("kit.csv", b"key,ru,en\none,Hello,Hi\n")},
            headers=headers,
        ).json()
        path = f"/api/producer/uploads/{draft['token']}/"
        original = client.get(path).json()
        body = {
            "revision": draft["revision"],
            "ui_mode": "quick",
            "current_step": "map-columns",
            "answers": {"source_language": "ru", "included_target_languages": ["invented"]},
        }
        assert client.patch(path, json=body, headers=headers).json()["code"] == "language_unknown"
        assert client.get(path).json() == original
        body["answers"]["included_target_languages"] = []
        assert client.patch(path, json=body, headers=headers).status_code == 200
        body["revision"] = client.get(path).json()["revision"]
        body["answers"]["included_target_languages"] = ["pt-br", "pt_BR", "pt"]
        saved = client.patch(path, json=body, headers=headers).json()
        assert saved["answers"]["included_target_languages"] == ["pt_BR", "pt"]
        before = client.get(path).json()

        def unavailable(settings):
            raise DomainError("language_catalog_unavailable", "Unavailable", 503)

        monkeypatch.setattr(languages, "catalog_for_settings", unavailable)
        body["revision"] = saved["revision"]
        assert client.patch(path, json=body, headers=headers).status_code == 503
        assert client.get(path).json() == before


@pytest.mark.parametrize("endpoint", ["start", "confirm"])
def test_launch_and_confirm_reject_unverified_codes_before_mutation(monkeypatch, endpoint):
    from types import SimpleNamespace

    import app.routes.project_links as links
    import app.routes.runs as runs

    monkeypatch.setattr(languages, "catalog_for_settings", lambda settings: CATALOG)
    draft = {"manifest": {"source": "ru", "targets": ["made-up"]}}

    class Store:
        def get(self, *args):
            return {"upload_id": "upload", "project": "project"}

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(store=Store(), settings=Settings(external_writes_enabled=True))
        )
    )
    actor = {"username": "producer"}
    calls = []
    monkeypatch.setattr(runs, "require_project_access", lambda *args: None)
    monkeypatch.setattr(links, "require_project_access", lambda *args: None)
    monkeypatch.setattr("app.services.drafts.get_draft", lambda *args: draft)
    monkeypatch.setattr(runs, "start_run", lambda *args: calls.append("started"))
    monkeypatch.setattr(links, "confirm_changes", lambda *args: calls.append("confirmed"))
    with pytest.raises(DomainError) as error:
        if endpoint == "start":
            runs.start(
                "project",
                runs.LocalizationRequest(upload_id="upload", revision="revision"),
                request,
                actor,
            )
        else:
            links.changes_confirm(
                "project", links.ChangesConfirm(plan_id="plan", revision="revision"), request, actor
            )
    assert error.value.code == "language_unknown"
    assert calls == []


def test_unsupported_weblate_variant_is_not_coerced():
    with pytest.raises(DomainError) as error:
        languages.validate_language_selection(
            "ru", ["de@formal"], CATALOG + [{"code": "de@formal", "name": "German formal"}]
        )
    assert error.value.code == "language_unsupported"


def test_failed_catalog_refresh_is_not_cached(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("test-only")
    settings = Settings(weblate_token_file=str(token))
    languages._cache.clear()
    clock = [0]
    monkeypatch.setattr(languages.time, "monotonic", lambda: clock[0])
    calls = []

    def load(remote):
        calls.append(True)
        if len(calls) == 2:
            raise DomainError("language_catalog_unavailable", "Unavailable", 503)
        return CATALOG

    monkeypatch.setattr(languages, "load_language_catalog", load)
    assert REAL_CATALOG_LOADER(settings) == CATALOG
    clock[0] = 61
    with pytest.raises(DomainError):
        REAL_CATALOG_LOADER(settings)
    assert REAL_CATALOG_LOADER(settings) == CATALOG
    assert len(calls) == 3
