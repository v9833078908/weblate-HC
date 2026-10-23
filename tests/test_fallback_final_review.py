# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

import pytest
from app.db import DomainError, Store
from app.schemas import ProfileCommitRequest, UploadPatch
from app.services import drafts, kit_versions
from app.services.profiles import commit_profile
from app.services.runs import start_run
from test_profile_context import context_client as context_client
from test_profile_jobs import request


def kit(store, uploads, roles=None):
    draft = drafts.create_draft(store, "owner", "local", uploads, "string", "quick")
    return drafts.patch_draft(
        store,
        "owner",
        draft["token"],
        UploadPatch(
            revision=draft["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "selected_files": [name for name, _ in uploads],
                "selected_sheets": [
                    {"file": name, "sheet": name.removesuffix(".csv")} for name, _ in uploads
                ],
                "source_language": "en",
                "included_target_languages": ["pl"],
                "column_roles": roles or [],
            },
        ),
    )


@pytest.mark.parametrize("good", [True, False])
def test_restore_problem_only_files(tmp_path, good):
    store = Store(tmp_path)
    uploads = [("problems.csv", b"Key,en,ru,pl\na,,,\n")]
    if good:
        uploads.insert(0, ("good.csv", b"Key,en,ru,pl\nb,Hello,,\n"))
    draft = kit(store, uploads)
    assert not draft["errors"]
    kit_versions.retain_version(store, "owner", "local", draft)
    expired = {k: v for k, v in draft.items() if k != "revision"}
    expired["expires"] = time.time() - 1
    store.save("draft", draft["token"], "owner", expired)
    restored = drafts.restore_latest_kit(store, "owner", "local")
    assert {f["name"] for f in restored["_files"]} == {n for n, _ in uploads}
    saved = drafts.patch_draft(
        store,
        "owner",
        restored["token"],
        UploadPatch(
            revision=restored["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers=restored["answers"],
        ),
    )
    assert not saved["errors"]
    assert saved["manifest"]["totals"] == draft["manifest"]["totals"]


def test_zero_admitted_rows_rejected_before_profile_or_job(tmp_path):
    store = Store(tmp_path)
    store.save("project", "local", "owner", {"name": "Game", "slug": "local"})
    draft = kit(store, [("kit.csv", b"Key,en,ru,pl\na,,,\n")])
    with pytest.raises(DomainError) as error:
        start_run(
            store,
            "owner",
            "local",
            {"upload_id": draft["token"], "revision": draft["revision"]},
            "openrouter",
        )
    assert error.value.code == "kit_no_admitted_rows"


def test_confirmed_fallback_column_without_language_header(tmp_path):
    draft = kit(
        Store(tmp_path),
        [("kit.csv", b"Key,Original\na,Privet\n")],
        [{"file": "kit.csv", "sheet": "kit", "column": 2, "role": "language", "language": "ru"}],
    )
    assert not draft["errors"], draft["errors"]
    component = draft["manifest"]["components"][0]
    assert component["source"] == "ru"
    assert component["units"][0]["values"]["ru"] == "Privet"


def test_historical_component_mapping_cannot_be_replaced(tmp_path, monkeypatch):
    store = Store(tmp_path)
    draft = kit(store, [("kit.csv", b"Key,en,ru,pl\nsame,,Privet,\n")])
    store.save(
        "project_link",
        "link:local",
        "owner",
        {
            "project": "local",
            "remote_slug": "game",
            "origin": "created-v2",
            "read_only": False,
            "component_map": {"kit.csv\u001fkit": "kit-1"},
        },
    )

    def unexpected_read(*args):
        raise DomainError("unexpected_remote_read", "Historical mapping must be checked first")

    monkeypatch.setattr(kit_versions, "snapshot_project", unexpected_read)
    with pytest.raises(DomainError) as error:
        kit_versions.preview_changes(
            store,
            "owner",
            "local",
            object(),
            draft["token"],
            draft["revision"],
            {"kit.csv\u001fkit": "empty"},
        )
    assert error.value.code == "component_mapping_conflict"


def test_manual_mixed_source_persona_is_preserved(context_client):
    _, store, slug, _, _, draft = context_client
    value = store.get("draft", "kit", "producer")
    value.pop("revision")
    value["manifest"]["components"][0]["source"] = "en"
    value["manifest"]["components"][0]["units"][0]["values"]["en"] = "Play"
    draft["revision"] = store.save("draft", "kit", "producer", value)["revision"]
    body = ProfileCommitRequest(
        **{
            **request(context_client).model_dump(),
            "generated": {
                "persona": "Use each request's actual source language and preserve game context.",
                "style": "Clear",
                "language_instructions": {"zh_Hant": "Traditional script"},
            },
            "edited_fields": ["persona"],
        }
    )
    saved = commit_profile(store, "producer", slug, body, "openrouter")
    assert saved["generated"]["persona"] == body.generated.persona
    assert saved["field_origins"]["persona"] == "manual"
