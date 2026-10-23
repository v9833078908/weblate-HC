# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest
from app.db import Store
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / ("deploy/" + name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state", ["running", "unknown"])
def test_maintenance_blocks_unsettled_profile_calls(tmp_path, state):
    store = Store(tmp_path)
    store.save(
        "profile_generation",
        "agent-job",
        "owner",
        {"state": state, "dispatch_started": True, "warnings": []},
    )
    with pytest.raises(ValueError, match="profile"):
        load("preflight").assert_quiet(tmp_path / "wizard.sqlite")
    load("preflight").assert_quiet(tmp_path / "wizard.sqlite", allow_profile_unknown=True)


def test_rollback_marks_sent_calls_unknown_without_dispatch(tmp_path):
    store = Store(tmp_path)
    store.save(
        "profile_generation",
        "sent",
        "owner",
        {"state": "running", "dispatch_started": True, "warnings": [], "lease_until": 999},
    )
    store.save(
        "profile_generation",
        "waiting",
        "owner",
        {"state": "queued", "dispatch_started": False, "warnings": []},
    )
    assert load("reconcile_profile_jobs").reconcile(tmp_path / "wizard.sqlite") == 1
    value = store.get("profile_generation", "sent", "owner")
    assert value["state"] == "unknown" and "llm_unknown" in value["warnings"]
    assert store.get("profile_generation", "waiting", "owner")["state"] == "queued"
    assert load("reconcile_profile_jobs").reconcile(tmp_path / "wizard.sqlite") == 0


def test_enabled_agent_requires_its_own_live_worker(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, profile_agent_enabled=True))
    app.state.store.save("heartbeat", "worker-heartbeat", "system", {"updated": time.time()})
    client = TestClient(app)
    assert client.get("/health/ready").status_code == 503
    (tmp_path / "profile-worker-heartbeat").write_text(str(time.time()))
    assert client.get("/health/ready").json()["profile_worker"] == "ok"
    (tmp_path / "profile-worker-heartbeat").write_text(str(time.time() - 200))
    assert client.get("/health/ready").status_code == 503


def test_disabled_agent_does_not_require_profile_worker(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, profile_agent_enabled=False))
    app.state.store.save("heartbeat", "worker-heartbeat", "system", {"updated": time.time()})
    assert TestClient(app).get("/health/ready").status_code == 200


def test_full_backup_and_restoration_preserve_profile_queue_and_current_ui(tmp_path):
    import shutil

    from app.backup import create_full_backup, verify_full_backup

    root = tmp_path / "instance"
    release = root / "releases" / "current-ui"
    (release / "console/dist").mkdir(parents=True)
    (release / "console/dist/index.html").write_text("current UI patch")
    store = Store(root / "shared/data")
    store.save(
        "profile_generation",
        "queued",
        "owner",
        {"state": "queued", "dispatch_started": False, "warnings": []},
    )
    private = root / "shared/secrets"
    private.mkdir()
    (private / "profile-llm-token").write_text("private-test-secret")
    (root / "shared/wizard.env").write_text("WIZARD_EXTERNAL_WRITES=0")
    units = tmp_path / "systemd"
    units.mkdir()
    (units / "wizard-profile-worker.service").write_text("test unit")
    destination = tmp_path / "backup"
    create_full_backup(root, release, destination, systemd_dir=units)
    verify_full_backup(destination)
    assert (destination / "release/console/dist/index.html").read_text() == "current UI patch"
    assert (destination / "systemd/wizard-profile-worker.service").read_text() == "test unit"
    restored = tmp_path / "restored"
    shutil.copytree(destination / "data", restored)
    old_store = Store(restored)
    assert old_store.get("profile_generation", "queued", "owner")["state"] == "queued"
    load("preflight").ensure_compatible(
        store.data_dir / "wizard.sqlite", old_store.data_dir / "wizard.sqlite"
    )


def test_failed_install_recovery_disables_legacy_synchronous_profile_calls(tmp_path, monkeypatch):
    settings_file = tmp_path / "wizard.env"
    settings_file.write_text(
        "PROFILE_LLM_MODEL=openai/gpt-5.4-mini\nPROFILE_LLM_TOKEN_FILE=/private/key\nWIZARD_PROFILE_AGENT_ENABLED=1\nWIZARD_EXTERNAL_WRITES=0\nBDHC_API_BASE_URL=http://localhost\n"
    )
    recovery = tmp_path / "recovery/wizard.env"
    load("disable_profile_agent").disable(settings_file, recovery)
    saved = recovery.read_text()
    assert "PROFILE_LLM_MODEL=openai/gpt-5.4-mini" in saved
    for line in settings_file.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            monkeypatch.setenv(key, value)
    settings = Settings(data_dir=tmp_path / "data")
    assert (
        not settings.profile_agent_enabled
        and not settings.llm_model
        and not settings.llm_token_file
    )
    app = create_app(settings)
    app.state.store.create_user("test", "local-test-password")
    import app.routes.profiles as routes

    monkeypatch.setattr(
        routes,
        "generate_profile",
        lambda *_: (_ for _ in ()).throw(AssertionError("paid legacy call")),
    )
    client = TestClient(app)
    csrf = client.post(
        "/auth/login", json={"username": "test", "password": "local-test-password"}
    ).json()["csrf_token"]
    headers = {"X-CSRFToken": csrf}
    slug = client.post("/api/producer/projects/", json={"name": "Local"}, headers=headers).json()[
        "slug"
    ]
    rev = client.get(f"/api/producer/projects/{slug}/profile/").json()["revision"]
    response = client.patch(
        f"/api/producer/projects/{slug}/profile/",
        headers=headers,
        json={
            "revision": rev,
            "answers": {
                "register_ui": "clear",
                "register_dialogue": "natural",
                "profanity": "preserve",
            },
        },
    )
    assert response.status_code == 200
