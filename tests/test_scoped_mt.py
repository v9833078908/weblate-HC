# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json

import httpx
import pytest
from app.db import DomainError
from app.integrations.weblate import WeblateClient
from app.settings import Settings


@pytest.mark.parametrize("code", ["pt_BR", "es_MX", "zh_Hans"])
def test_regional_language_codes_reach_create_and_scoped_mt_unchanged(tmp_path, code):
    token = tmp_path / "token"
    token.write_text("dummy")
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            assert f"/{code}/units/" in request.url.path
            return httpx.Response(200, json={"results": [{"id": 5}]})
        body = json.loads(request.content)
        if request.url.path.endswith("/translations/"):
            assert body["language_code"] == code
            return httpx.Response(201, json={"language": {"code": code}})
        assert f"/{code}/autotranslate/" in request.url.path
        assert body["q"] == "(id:5) AND state:empty"
        return httpx.Response(202, json={})

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    client.create_translation("game", "ui", code)
    client.start_scoped_translation("game", "ui", code, [5])
    assert [request.method for request in calls] == ["POST", "GET", "POST"]


def test_scoped_mt_validates_exact_ids_before_post(tmp_path):
    token = tmp_path / "token"
    token.write_text("dummy")
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"id": 5}, {"id": 7}]})
        assert json.loads(request.content)["q"] == "(id:5 OR id:7) AND state:empty"
        return httpx.Response(202, json={"task_url": "/api/tasks/test/"})

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    assert hasattr(client, "start_scoped_translation"), "scoped MT is missing"
    client.start_scoped_translation("game", "ui", "de", [5, 7])
    assert [r.method for r in calls] == ["GET", "POST"]
    with pytest.raises(DomainError, match="набор"):
        client.start_scoped_translation("game", "ui", "de", [5])
    assert len([r for r in calls if r.method == "POST"]) == 1


def test_live_mutations_require_operator_switch(tmp_path):
    client = WeblateClient(Settings())
    with pytest.raises(DomainError) as error:
        client.create_project("Should never reach network", "guard")
    assert error.value.code == "external_read_only"
