# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import httpx
import pytest
from app.db import DomainError, Store
from app.integrations.weblate import WeblateClient
from app.services.quality_preflight import run_quality_preflight
from app.services.quality_state import create_quality_cycle
from app.settings import Settings


def test_full_cycle_refuses_legacy_server_before_estimate():
    class Legacy:
        def producer_me(self):
            return {"capabilities": {"judge": True, "judge_seats": 2}}

        def judge_estimate(self, *args):
            pytest.fail("An incompatible server must not reach estimate")

    with pytest.raises(DomainError) as error:
        run_quality_preflight(Legacy(), "project", {"unit_ids": [1]}, full_cycle=True)
    assert error.value.code == "quality_configuration"


def test_snapshot_client_rejects_incomplete_and_text_leaking_responses(tmp_path):
    token = tmp_path / "token"
    token.write_text("test-token")
    response = {
        "snapshot_hash": "snapshot-1",
        "scope_revision": "scope-1",
        "complete": True,
        "unit_ids": [1],
        "revisions": {"1": "revision-1"},
        "judged_revisions": {"1": "revision-1"},
        "issues": [],
    }
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    assert client.quality_snapshot("project", "de", {"unit_ids": [1]}) == response
    assert calls[0].url.path == "/api/producer/projects/project/quality/snapshot/"
    response["issues"] = [{"source": "must not enter local quality state"}]
    with pytest.raises(DomainError) as error:
        client.quality_snapshot("project", "de", {"unit_ids": [1]})
    assert error.value.code == "weblate_schema"
    response["issues"] = []
    response["complete"] = False
    with pytest.raises(DomainError):
        client.quality_snapshot("project", "de", {"unit_ids": [1]})


def test_repeat_client_uses_stable_idempotency_key_and_checks_status(tmp_path):
    token = tmp_path / "token"
    token.write_text("test-token")
    calls = []
    response = {"token": "plan-1", "status": "ready", "groups": []}

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    plan = client.plan_repeat_quality("project", "de", {"unit_ids": [1]}, "hash", [9], "op-1")
    assert plan["token"] == "plan-1"
    assert calls[0].headers["Idempotency-Key"] == "op-1"
    response["status"] = "unknown-status"
    with pytest.raises(DomainError) as error:
        client.get_repeat_plan("plan-1")
    assert error.value.code == "weblate_schema"


def test_group_state_survives_store_restart_and_preserves_owner_boundary(tmp_path):
    from app.services.quality_state import list_quality_groups, record_quality_groups

    store = Store(tmp_path)
    cycle = create_quality_cycle(store, "owner", "job", "remote", {"revision": "v2"})
    groups = [
        {
            "group_id": "repeat:1",
            "revision": "r1",
            "unit_ids": [1, 2],
            "kind": "repeat-drift",
            "outcome": "pending",
        }
    ]
    record_quality_groups(store, "owner", cycle["id"], groups, 0)
    store.engine.dispose()
    reopened = Store(tmp_path)
    assert list_quality_groups(reopened, "owner", cycle["id"])[0]["unit_ids"] == [1, 2]
    with pytest.raises(DomainError) as error:
        list_quality_groups(reopened, "someone-else", cycle["id"])
    assert error.value.status == 404


@pytest.mark.parametrize("counts", [{"consistency_issues": 1}, {"quarantined_transport": 1}])
def test_unresolved_issues_never_become_completed(counts):
    from app.services.quality_report import terminal_quality_status

    assert terminal_quality_status(counts) == "completed_with_exceptions"
