# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json

from app.services import diagnostics


def test_report_excludes_secrets_game_text_and_transport_payloads():
    assert hasattr(diagnostics, "diagnostic_report"), "diagnostics missing"
    value = diagnostics.diagnostic_report(
        "run-id",
        {
            "phase": "machine-translation",
            "failure_code": "weblate_rejected",
            "remote_status": 400,
            "snapshot": {"profile": {"key": "secret"}, "manifest": {"source": "private source"}},
            "failure": "token secret",
            "operations": {"delta:mt:ui:de:0": {"state": "intent", "result": {"key": "secret"}}},
        },
    )
    encoded = json.dumps(value)
    assert "secret" not in encoded and "private source" not in encoded
    assert value["http_status"] == 400
    assert value["operations"][0]["component"] == "ui"


def test_rejected_response_reports_safe_field_reason_without_echoing_payload(tmp_path):
    import httpx
    import pytest
    from app.integrations.weblate import WeblateClient, WeblateRejected
    from app.settings import Settings

    token = tmp_path / "token"
    token.write_text("dummy")
    remote = WeblateClient(
        Settings(weblate_token_file=str(token)),
        httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "slug": ["Project with this slug already exists. secret-token"],
                    "key": "secret-token",
                },
            )
        ),
    )
    with pytest.raises(WeblateRejected) as error:
        remote.create_project("Test", "test")
    assert error.value.safe_reasons == [{"field": "slug", "reason": "already_exists"}]
    assert "secret-token" not in error.value.detail
    assert "slug" in error.value.detail
