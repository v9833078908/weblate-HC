# Copyright В© HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time

import pytest
from app.db import DomainError, Store
from app.schemas import UploadPatch
from app.services import drafts, kit_versions
from app.services.maintenance import cleanup_expired


def saved_kit(store, owner="owner", slug="local"):
    d = drafts.create_draft(
        store, owner, slug, [("kit.csv", b"Key,ru,en\na,Hello,Hi\n")], "string", "quick"
    )
    d = drafts.patch_draft(
        store,
        owner,
        d["token"],
        UploadPatch(
            revision=d["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "selected_files": ["kit.csv"],
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                "source_language": "ru",
                "included_target_languages": ["en"],
            },
        ),
    )
    v = kit_versions.retain_version(store, owner, slug, d)
    expired = {k: copy.deepcopy(val) for k, val in d.items() if k != "revision"}
    expired["expires"] = time.time() - 1
    store.save("draft", d["token"], owner, expired)
    cleanup_expired(store)
    return {**d, "manifest": store.get("kit_version", v["id"], owner)["manifest"]}, v


def test_restore_keeps_selected_sheets_languages_and_originals_after_expiry(tmp_path):
    store = Store(tmp_path)
    original, version = saved_kit(store)
    restored = drafts.restore_latest_kit(store, "owner", "local")
    assert restored["token"] != original["token"]
    assert restored["answers"] == original["answers"]
    assert restored["current_step"] == "select-content"
    assert restored["manifest"] == original["manifest"]
    assert restored["analysis"]["loc_kit"]["files"][0]["sheets"][0]["selected"]
    assert store.get("kit_version", version["id"], "owner")["manifest"] == original["manifest"]
    assert drafts.restore_latest_kit(store, "owner", "local")["token"] == restored["token"]


def test_restore_legacy_version_without_file_mapping(tmp_path):
    store = Store(tmp_path)
    original, version = saved_kit(store)
    legacy = {
        k: v
        for k, v in version.items()
        if k not in {"revision", "files", "answers", "kind", "ui_mode"}
    }
    store.save("kit_version", version["id"], "owner", legacy)
    restored = drafts.restore_latest_kit(store, "owner", "local")
    assert restored["answers"]["selected_sheets"] == original["answers"]["selected_sheets"]
    assert restored["answers"]["source_language"] == "ru"
    assert restored["answers"]["included_target_languages"] == ["en"]
    assert restored["manifest"] == original["manifest"]
    assert restored["_files"][0]["sheets"]["kit"][1] == ["a", "Hello", "Hi"]


def test_restore_is_owner_project_scoped_and_does_not_pick_glossary(tmp_path):
    store = Store(tmp_path)
    original, version = saved_kit(store)
    glossary = {k: copy.deepcopy(v) for k, v in version.items() if k != "revision"}
    glossary.update(id="kit-version:glossary", kind="glossary", created=time.time() + 50)
    store.save("kit_version", glossary["id"], "owner", glossary)
    assert drafts.restore_latest_kit(store, "owner", "local")["manifest"] == original["manifest"]
    for owner, slug in [("other", "local"), ("owner", "other")]:
        with pytest.raises(DomainError) as e:
            drafts.restore_latest_kit(store, owner, slug)
        assert e.value.status == 404


def test_restore_rejects_original_path_outside_version_storage(tmp_path):
    store = Store(tmp_path)
    _, version = saved_kit(store)
    value = {k: v for k, v in version.items() if k != "revision"}
    value["originals"] = "../outside"
    store.save("kit_version", version["id"], "owner", value)
    with pytest.raises(DomainError) as e:
        drafts.restore_latest_kit(store, "owner", "local")
    assert e.value.code == "original_missing"


def test_restore_route_requires_csrf_and_project_ownership(tmp_path):
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    store.create_user("owner", "test-long-password")
    store.create_user("other", "test-long-password")
    store.save("project", "local", "owner", {"slug": "local", "name": "Game"})
    saved_kit(store)
    with TestClient(app) as client:
        csrf = client.post(
            "/auth/login", json={"username": "owner", "password": "test-long-password"}
        ).json()["csrf_token"]
        path = "/api/producer/projects/local/uploads/restore/"
        assert client.post(path).status_code == 403
        result = client.post(path, headers={"X-CSRFToken": csrf})
        assert result.status_code == 200
        assert result.json()["answers"]["source_language"] == "ru"
        assert "_files" not in result.json()
        csrf = client.post(
            "/auth/login", json={"username": "other", "password": "test-long-password"}
        ).json()["csrf_token"]
        assert client.post(path, headers={"X-CSRFToken": csrf}).status_code == 404
