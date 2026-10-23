# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import DomainError


@pytest.mark.parametrize("throttled", [False, True])
def test_incremental_translation_retries_only_observe_and_preserve_existing(monkeypatch, throttled):
    from app.db import revision
    from app.services import incremental

    clock = [1000.0]
    monkeypatch.setattr(incremental.time, "time", lambda: clock[0])

    snapshot = {
        "slug": "game",
        "profile": {
            "openrouter": {"persona": "P", "style": "S", "language_instructions": {"de": "D"}}
        },
        "components": {
            "ui": {
                "source": "ru",
                "languages": ["de"],
                "complete": True,
                "can_add_source": True,
                "file_format": "po-mono",
                "units": [
                    {
                        "key": "old",
                        "source": "Old",
                        "targets": {"de": "Alt"},
                        "target_ids": {"de": 2},
                    },
                    {"key": "new", "source": "New", "targets": {"de": ""}, "target_ids": {"de": 4}},
                ],
            }
        },
    }
    plan = {
        "snapshot": copy.deepcopy(snapshot),
        "snapshot_hash": revision(snapshot),
        "components": [
            {
                "local_id": "kit1",
                "remote_id": "ui",
                "new": [],
                "seed": {"de": {}},
                "translate": {"de": ["new"]},
            }
        ],
    }
    data = {
        "operations": {},
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["de"]},
            "profile": snapshot["profile"]["openrouter"],
            "change_plan": plan,
        },
        "remote_project": "game",
    }

    class Remote:
        calls = 0

        def snapshot(self):
            return copy.deepcopy(snapshot)

        def set_profile(self, *args):
            raise AssertionError("Profile is unchanged")

        def start_scoped_translation(self, project, component, language, ids):
            assert ids == [4]
            self.calls += 1
            if throttled and self.calls == 1:
                import httpx
                from app.integrations.weblate import WeblateRejected

                raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "90"}))
            raise DomainError("weblate_unknown", "timeout", 503)

    remote = Remote()
    assert hasattr(incremental, "process_incremental"), "incremental worker is missing"
    if throttled:
        with pytest.raises(DomainError) as limited:
            incremental.process_incremental(
                data, remote, lambda: None, snapshot_loader=remote.snapshot
            )
        assert limited.value.remote_status == 429
        assert data["operations"]["delta:mt:ui:de:0"]["state"] == "throttled"
        clock[0] += 90
    with pytest.raises(DomainError) as error:
        incremental.process_incremental(
            data, remote, lambda: None, snapshot_loader=lambda: remote.snapshot()
        )
    assert error.value.code == "incremental_wait"
    clock[0] += 601
    with pytest.raises(DomainError) as error:
        incremental.process_incremental(
            data, remote, lambda: None, snapshot_loader=lambda: remote.snapshot()
        )
    assert error.value.code == "incremental_wait"
    clock[0] += 1200
    with pytest.raises(DomainError) as error:
        incremental.process_incremental(
            data, remote, lambda: None, snapshot_loader=lambda: remote.snapshot()
        )
    assert error.value.code == "weblate_unknown"
    snapshot["components"]["ui"]["units"][1]["targets"]["de"] = "Neu"
    result = incremental.process_incremental(
        data, remote, lambda: None, snapshot_loader=lambda: remote.snapshot()
    )
    assert result["kit1"]["de"] == {"old": "Alt", "new": "Neu"}
    assert remote.calls == (2 if throttled else 1)


def test_source_changed_during_language_creation_blocks_translation():
    from app.db import revision
    from app.services.incremental import process_incremental

    snapshot = {
        "slug": "game",
        "profile": {},
        "components": {
            "ui": {
                "source": "ru",
                "languages": [],
                "complete": True,
                "units": [{"key": "old", "source": "Old", "targets": {}, "target_ids": {}}],
            }
        },
    }
    plan = {
        "snapshot": copy.deepcopy(snapshot),
        "snapshot_hash": revision(snapshot),
        "components": [
            {
                "local_id": "kit1",
                "remote_id": "ui",
                "new": [],
                "seed": {"en": {}},
                "translate": {"en": ["old"]},
            }
        ],
    }
    profile = {"persona": "P", "style": "S", "language_instructions": {}}
    snapshot["profile"] = {"openrouter": profile}
    plan["snapshot"] = copy.deepcopy(snapshot)
    plan["snapshot_hash"] = revision(snapshot)
    data = {
        "remote_project": "game",
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["en"]},
            "profile": profile,
            "change_plan": plan,
        },
        "operations": {},
    }

    class Remote:
        def create_translation(self, *args):
            component = snapshot["components"]["ui"]
            component["languages"] = ["en"]
            component["units"][0].update(source="Changed", target_ids={"en": 2}, targets={"en": ""})
            return {}

        def start_scoped_translation(self, *args):
            raise AssertionError("Changed source must not reach MT")

    with pytest.raises(DomainError) as error:
        process_incremental(
            data, Remote(), lambda: None, snapshot_loader=lambda: copy.deepcopy(snapshot)
        )
    assert error.value.code == "remote_changed"


def test_cancelled_unconfirmed_paid_operation_still_reserves_project(tmp_path):
    from app.db import Store
    from app.models import Job
    from app.services.runs import ensure_project_available

    store = Store(tmp_path)
    with store.session() as session:
        session.add(
            Job(
                id="cancelled",
                owner="owner",
                idempotency_key="cancelled",
                status="cancelled",
                data={
                    "project": "local",
                    "remote_project": "game",
                    "operations": {"delta:mt:ui:de:0": {"state": "accepted"}},
                },
            )
        )
    with store.session() as session, pytest.raises(DomainError) as error:
        ensure_project_available(session, "owner", "local")
    assert error.value.code == "run_active"
