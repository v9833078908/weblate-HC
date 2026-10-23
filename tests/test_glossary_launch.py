# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import io
import json
from zipfile import ZipFile

import pytest
from app.main import create_app
from app.models import Job
from app.schemas import UploadPatch
from app.services.drafts import create_draft, patch_draft
from app.services.profiles import base_profile, get_profile, profile_key
from app.settings import Settings
from app.worker import archive_component
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select
from translate.storage.tbx import tbxfile


@pytest.fixture(autouse=True)
def selected_catalog(monkeypatch):
    monkeypatch.setattr(
        "app.services.languages.catalog_for_settings",
        lambda settings: [{"code": c, "name": c} for c in ["ru", "de", "zh_Hans", "zh_Hant"]],
    )


def export_table():
    return [
        ["Synthetic — глоссарий"],
        ["Экспорт для теста"],
        [],
        [],
        [],
        [
            "Ключ / контекст",
            "Русский (ru)",
            "Немецкий (de)",
            "Пояснение исходного термина",
            "Флаги исходного термина",
        ],
        ['["Оружие","Меч"]', "Меч", "Schwert", "Название оружия", "forbidden"],
        ['["Кнопка","Меч"]', "Меч", "", "Другой контекст", ""],
    ]


def launch(tmp_path, rows):
    app = create_app(Settings(data_dir=tmp_path / "data", external_writes_enabled=True))
    store = app.state.store
    store.create_user("owner", "test-long-password")
    store.save(
        "project", "local", "owner", {"slug": "local", "name": "Synthetic", "persistent": True}
    )
    old = get_profile(store, "owner", "local", "openrouter")
    store.save(
        "profile",
        profile_key("local"),
        "owner",
        {
            **base_profile(old),
            "answers": {
                "register_ui": "neutral",
                "register_dialogue": "neutral",
                "profanity": "none",
            },
            "generated": {"persona": "Translator", "style": "Clear", "language_instructions": {}},
        },
        old["revision"],
    )
    draft = create_draft(
        store, "owner", "local", [("kit.csv", "Key,ru\nplay,Играть\n".encode())], "string", "quick"
    )
    draft = patch_draft(
        store,
        "owner",
        draft["token"],
        UploadPatch(
            revision=draft["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "source_language": "ru",
                "included_target_languages": ["zh_Hans", "zh_Hant"],
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
            },
        ),
    )
    assert draft["errors"] == []
    book = Workbook()
    book.active.title = "Глоссарий"
    for row in rows:
        book.active.append(row)
    payload = io.BytesIO()
    book.save(payload)
    glossary = create_draft(
        store, "owner", "local", [("glossary.xlsx", payload.getvalue())], "glossary", "quick"
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = client.post(
            "/auth/login", json={"username": "owner", "password": "test-long-password"}
        ).json()["csrf_token"]
        result = client.post(
            "/api/producer/projects/local/localization/",
            headers={"X-CSRFToken": csrf},
            json={
                "upload_id": draft["token"],
                "revision": draft["revision"],
                "glossary_upload_id": glossary["token"],
                "glossary_revision": glossary["revision"],
                "glossary_confirmed": False,
            },
        )
    return result, store, glossary


def test_launch_with_exported_glossary_preserves_context_notes_flags_and_new_targets(tmp_path):
    rows = export_table()
    result, store, draft = launch(tmp_path, rows)
    assert result.status_code == 202, result.text
    with store.session() as session:
        job = session.get(Job, result.json()["id"])
        assert job.status == "queued"
        assert job.data["remote_project"].startswith("local-")
        glossary = job.data["snapshot"]["glossary"]
    component = glossary["components"][0]
    units = component["units"]
    assert [u["context"] for u in units] == [r[0] for r in rows[6:]]
    assert units[0]["source_explanation"] == "Название оружия"
    assert units[0]["source_flags"] == ["forbidden"]
    assert units[0]["values"]["de"] == "Schwert"
    assert units[1]["values"]["de"] == ""
    assert all(u["values"][code] == "" for u in units for code in ["zh_Hans", "zh_Hant"])
    assert (
        store.get("draft", draft["token"], "owner")["_files"][0]["sheets"]["Глоссарий"][5:]
        == rows[5:]
    )
    archive = ZipFile(io.BytesIO(archive_component(component, tmp_path / "render")))
    assert {"de.tbx", "zh_Hans.tbx", "zh_Hant.tbx"} <= set(archive.namelist())
    tbx = tbxfile.parsestring(archive.read("de.tbx"))
    actual = {u.getid(): u for u in tbx.units if u.source}
    assert actual[rows[6][0]].target == "Schwert"
    assert actual[rows[6][0]].getnotes("definition") == "Название оружия"
    assert "forbidden" in actual[rows[6][0]].xmlelement.get("weblate-flags", "")


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown_header",
        "invalid_context",
        "context_source",
        "unknown_flags",
        "extra_cell",
        "duplicate_context",
        "no_terms",
    ],
)
def test_invalid_glossary_returns_actionable_error_and_does_not_queue_run(tmp_path, invalid):
    rows = export_table()
    if invalid == "unknown_header":
        rows[5][0] = "Unmapped data"
    elif invalid == "invalid_context":
        rows[6][0] = "not-a-serialized-context"
    elif invalid == "context_source":
        rows[6][0] = json.dumps(["Оружие", "Different source"])
    elif invalid == "extra_cell":
        rows[6].append("Must not become a new translation")
    elif invalid == "duplicate_context":
        rows[7][0] = rows[6][0]
    elif invalid == "no_terms":
        rows[6:] = [[], []]
    else:
        rows[6][-1] = "invented-flag"
    result, store, _ = launch(tmp_path, rows)
    assert result.status_code == 400, result.text
    assert result.json()["code"] == "glossary_validation"
    assert "glossary.xlsx" in result.json()["detail"]
    assert "Глоссарий" in result.json()["detail"]
    with store.session() as session:
        assert session.scalar(select(Job.id)) is None
