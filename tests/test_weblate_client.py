# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import httpx
import pytest


def test_existing_translation_upload_ignores_conflicts(tmp_path):
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"result": True})

    client = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(handler))
    client.upload_translation("project", "kit-1", "de", b"PO payload")
    request = requests[0]
    assert (
        request.method == "POST" and request.url.path == "/api/translations/project/kit-1/de/file/"
    )
    assert b'name="method"\r\n\r\ntranslate' in request.content
    assert b'name="conflicts"\r\n\r\nignore' in request.content
    assert b"PO payload" in request.content
    assert b'filename="de.xlf"' in request.content
    assert b"application/x-xliff+xml" in request.content
    assert len(requests) == 1


def test_fixed_host_token_and_empty_only_translation(tmp_path):
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "l10n.herocraft.com"
        assert request.headers["authorization"] == "Token test-private-token"
        return httpx.Response(
            200, json={"details": "Queued", "task_url": "https://l10n.herocraft.com/api/tasks/123/"}
        )

    client = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(handler))
    client.create_translation("test-123", "kit-1", "de")
    import json

    assert requests[-1].method == "POST"
    assert requests[-1].url.path.endswith("/components/test-123/kit-1/translations/")
    assert json.loads(requests[-1].content) == {"language_code": "de"}
    client.start_translation("test-123", "kit-1", "ja")
    import json

    body = json.loads(requests[-1].content)
    assert body["q"] == "state:empty"
    assert body["mode"] == "translate" and body["auto_source"] == "mt"
    assert body["threshold"] >= 1 and body["engines"] == ["openrouter"]
    with pytest.raises(ValueError):
        client.request("GET", "https://other.example/api/tasks/123/")
    assert len(requests) == 2


@pytest.mark.parametrize("status,exists", [(400, True), (400, False), (403, True)])
def test_language_created_during_import_is_observed_after_rejection(tmp_path, status, exists):
    from app.db import DomainError
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(status, json={"language_code": "Could not add 'de'!"})
        assert request.method == "GET"
        assert request.url.path == "/api/translations/test-123/kit-1/de/"
        return httpx.Response(200, json={"total": 13}) if exists else httpx.Response(404)

    client = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(handler))
    if status == 400 and exists:
        assert client.create_translation("test-123", "kit-1", "de")["total"] == 13
    else:
        with pytest.raises(DomainError) as error:
            client.create_translation("test-123", "kit-1", "de")
        assert error.value.code == "weblate_rejected"
        assert str(status) in error.value.detail
    assert [request.method for request in requests] == (
        ["POST", "GET"] if status == 400 else ["POST"]
    )


def test_unknown_post_is_not_retried_and_redirect_cannot_send_token(tmp_path):
    from app.db import DomainError
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("test-private-token", request=request)

    client = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(timeout))
    with pytest.raises(DomainError) as error:
        client.start_translation("test-123", "kit-1", "ja")
    assert error.value.code == "weblate_unknown"
    assert "test-private-token" not in error.value.detail and len(requests) == 1

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/"})

    client = WeblateClient(Settings(weblate_token_file=str(secret)), httpx.MockTransport(redirect))
    with pytest.raises(DomainError):
        client.request("GET", "projects/")
    assert len(requests) == 2
