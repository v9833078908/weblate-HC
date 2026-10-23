# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.main import create_app
from app.models import Job
from app.services.exports import reconstructed_export
from app.settings import Settings
from fastapi.testclient import TestClient


def test_read_only_policy_and_historic_download_are_owner_scoped(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    for name in ("owner", "other"):
        store.create_user(name, "test-long-password")
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "owner", "password": "test-long-password"}
        ).json()["csrf_token"]
        project = client.post(
            "/api/producer/projects/", json={"name": "Game"}, headers={"X-CSRFToken": csrf}
        ).json()
        assert set(project) == {"slug", "name", "advanced_url"}
        assert client.get("/api/producer/integration-policy/").json() == {
            "external_writes": False,
            "quality_cycle": False,
        }
        assert (
            client.post(
                f"/api/producer/projects/{project['slug']}/localization/",
                json={"upload_id": "none", "revision": "a" * 64},
                headers={"X-CSRFToken": csrf},
            ).status_code
            == 403
        )
        result = reconstructed_export(
            {
                "languages": ["en"],
                "components": {
                    "ui": {
                        "name": "UI",
                        "source": "ru",
                        "units": [{"key": "old", "source": "Привет", "targets": {"en": "Hello"}}],
                    }
                },
            }
        )
        store.save("export", "export:old", "owner", result)
        with store.session() as session:
            session.add(
                Job(
                    id="old",
                    owner="owner",
                    idempotency_key="old",
                    status="completed",
                    data={
                        "project": project["slug"],
                        "remote_project": "game",
                        "export_id": "export:old",
                        "created": "2026-10-01T00:00:00Z",
                        "started": None,
                        "finished": None,
                        "operations": {},
                        "warnings": [],
                        "failure": "",
                        "attempt": 1,
                        "phase": "completed",
                    },
                )
            )
        history = f"/api/producer/projects/{project['slug']}/history/"
        assert client.get(history).json()["results"][0]["id"] == "old"
        assert (
            client.get("/api/producer/runs/old/export/?format=json").json()["sheets"][0]["rows"][1][
                2
            ]
            == "Hello"
        )
        client.post("/auth/login", json={"username": "other", "password": "test-long-password"})
        assert client.get(history).status_code == 404
        assert client.get("/api/producer/runs/old/export/?format=json").status_code == 404
        assert client.get("/api/producer/runs/old/diagnostics/").status_code == 404
