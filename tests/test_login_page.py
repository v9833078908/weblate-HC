# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient


def test_login_page_is_styled_and_public(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert 'name="viewport"' in response.text
    assert 'class="topbar"' in response.text and "HCGameLoc" in response.text
    assert 'class="login-card"' in response.text
    assert 'autocomplete="username"' in response.text
    assert 'autocomplete="current-password"' in response.text
    assert 'type="submit"' in response.text
    assert 'role="alert"' in response.text


def test_console_preserves_return_destination(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)), follow_redirects=False)
    response = client.get("/console/projects/example/localize?u=kit&step=3")
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == [
        "/console/projects/example/localize?u=kit&step=3"
    ]
    page = client.get(response.headers["location"])
    target = json.loads(re.search(r"location\.assign\((.+)\)", page.text)[1])
    assert target == "/console/projects/example/localize?u=kit&step=3"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        "/auth/logout",
        "/console/../../auth/logout",
        "/console/%2e%2e/auth/logout",
        "/console/\\evil",
        "/console/\nprojects",
    ],
)
def test_login_rejects_unsafe_return_destinations(tmp_path, target):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    page = client.get("/auth/login", params={"next": target})
    assert 'location.assign("/console/projects")' in page.text


def test_return_destination_cannot_inject_html(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    page = client.get(
        "/auth/login", params={"next": "/console/projects?q=</script><script>alert(1)</script>"}
    )
    assert "<script>alert(1)</script>" not in page.text
