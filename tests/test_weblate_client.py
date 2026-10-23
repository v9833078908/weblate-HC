# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize("enable_review", [False, True])
def test_new_project_requests_review_only_for_quality_cycle(tmp_path, enable_review):
    import json

    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    token = tmp_path / "token"
    token.write_text("fixture-token")
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "slug": body["slug"],
                "translation_review": body.get("translation_review", False),
            },
        )

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    if enable_review:
        created = client.create_project("New project", "new-project", translation_review=True)
    else:
        created = client.create_project("New project", "new-project")
    assert created["translation_review"] is enable_review
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == "/api/projects/"
    expected = {"name": "New project", "slug": "new-project"}
    if enable_review:
        expected["translation_review"] = True
    assert json.loads(requests[0].content) == expected


@pytest.mark.parametrize("returned_review", [False, None])
def test_new_quality_project_requires_confirmed_review(tmp_path, returned_review):
    from app.db import DomainError
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    token = tmp_path / "token"
    token.write_text("fixture-token")
    client = WeblateClient(
        Settings(weblate_token_file=str(token)),
        httpx.MockTransport(
            lambda request: httpx.Response(
                201, json={"slug": "new", "translation_review": returned_review}
            )
        ),
    )
    with pytest.raises(DomainError) as error:
        client.create_project("New", "new", translation_review=True)
    assert error.value.code == "quality_configuration"


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


def test_producer_quality_methods_use_one_dual_seat_run(tmp_path):
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/runs/estimate/"):
            return httpx.Response(200, json={"estimate_id": "estimate-1"})
        if request.url.path.endswith("/runs/"):
            return httpx.Response(201, json={"id": "run-1", "status": "queued"})
        if request.url.path.endswith("/decisions/"):
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        if request.url.path.endswith("/quality/issues/"):
            return httpx.Response(200, json={"snapshot_hash": "a" * 64, "issues": []})
        if request.url.path.endswith("/quality/recheck/"):
            return httpx.Response(200, json={"snapshot_hash": "b" * 64, "items": []})
        if request.url.path.endswith("/decisions/apply/"):
            return httpx.Response(200, json=[])
        raise AssertionError(request.url)

    client = WeblateClient(
        Settings(weblate_token_file=str(secret), external_writes_enabled=True),
        httpx.MockTransport(handler),
    )
    estimate = client.judge_estimate("project", {"unit_ids": [1, 2]})
    run = client.start_judge("project", estimate["estimate_id"], {"unit_ids": [1, 2]})
    assert run["id"] == "run-1"
    assert client.list_decisions("project", "run-1") == []
    assert client.list_quality_issues("project", "de")["issues"] == []
    client.recheck_quality("project", [{"unit_id": 1, "revision": "revision"}])
    client.bulk_apply_candidates("project", [])
    assert sum(request.url.path.endswith("/runs/") for request in requests) == 1


def test_get_retries_transient_response_but_post_does_not(tmp_path):
    from app.db import DomainError
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    attempts = []

    def handler(request):
        attempts.append(request.method)
        if request.method == "GET" and len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"username": "producer", "capabilities": {}})

    client = WeblateClient(
        Settings(weblate_token_file=str(secret), external_writes_enabled=True),
        httpx.MockTransport(handler),
    )
    assert client.producer_me()["username"] == "producer"
    assert attempts == ["GET", "GET", "GET"]

    attempts.clear()

    def post_timeout(request):
        attempts.append(request.method)
        raise httpx.ReadTimeout("timeout", request=request)

    client = WeblateClient(
        Settings(weblate_token_file=str(secret), external_writes_enabled=True),
        httpx.MockTransport(post_timeout),
    )
    with pytest.raises(DomainError) as error:
        client.start_judge("project", "estimate", {})
    assert error.value.code == "weblate_unknown"
    assert attempts == ["POST"]


def test_configured_local_weblate_origin_is_allowed_but_cross_origin_is_not(tmp_path):
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")

    def handler(request):
        assert request.url.host == "localhost" and request.url.port == 3001
        return httpx.Response(200, json={"results": [], "next": "https://evil.invalid/api/"})

    client = WeblateClient(
        Settings(
            weblate_url="http://localhost:3001/api/",
            weblate_token_file=str(secret),
        ),
        httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="Unsafe external Weblate URL"):
        client.collect("producer/projects/project/decisions/")


def test_unknown_remote_run_status_is_schema_error(tmp_path):
    from app.db import DomainError
    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")

    client = WeblateClient(
        Settings(weblate_token_file=str(secret)),
        httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "run-1", "status": "future-unknown"})
        ),
    )
    with pytest.raises(DomainError) as error:
        client.get_producer_run("run-1")
    assert error.value.code == "weblate_schema"


@pytest.mark.parametrize("repeat_check", [False, True])
def test_full_quality_component_creation_explicitly_enables_repeat_membership(
    tmp_path, repeat_check
):
    from email.parser import BytesParser
    from email.policy import default

    from app.integrations.weblate import WeblateClient
    from app.settings import Settings

    secret = tmp_path / "secret"
    secret.write_text("test-private-token")
    fields = {}

    def handler(request):
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + request.headers["Content-Type"].encode()
            + b"\r\n\r\n"
            + request.content
        )
        fields.update(
            {
                part.get_param("name", header="content-disposition"): part.get_payload(
                    decode=True
                ).decode()
                for part in message.iter_parts()
                if part.get_filename() is None
            }
        )
        return httpx.Response(201, json={"slug": "kit-1"})

    client = WeblateClient(
        Settings(weblate_token_file=str(secret), external_writes_enabled=True),
        httpx.MockTransport(handler),
    )
    options = {"allow_translation_propagation": True} if repeat_check else {}
    client.create_component("project", "kit-1", "en", b"test archive", **options)
    assert fields.get("allow_translation_propagation") == ("true" if repeat_check else None)
