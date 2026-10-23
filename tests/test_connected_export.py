# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import Store
from app.services import exports


def test_connected_snapshot_can_be_downloaded_without_original_workbook(tmp_path):
    store = Store(tmp_path)
    snapshot = {
        "languages": ["de", "en"],
        "components": {
            "ui": {
                "source": "ru",
                "name": "UI",
                "glossary": False,
                "units": [
                    {
                        "key": "key",
                        "source": "Привет",
                        "targets": {"de": "Hallo", "en": "Hello"},
                        "note": "Note",
                    }
                ],
            }
        },
    }
    store.save("project_link", "link:local", "owner", {"snapshot": snapshot})
    result = exports.project_snapshot(store, "owner", "local")
    assert result["reconstructed"] is True
    assert result["sheets"][0]["rows"][1][:4] == ["key", "Привет", "Hallo", "Hello"]
    assert result["per_language"]["de"]["translated"] == 1


def test_new_version_keeps_german_and_original_export_immutable(tmp_path):
    import copy

    from app.schemas import UploadPatch
    from app.services.drafts import create_draft, patch_draft

    store = Store(tmp_path)
    draft = create_draft(
        store,
        "owner",
        "local",
        [("kit.csv", "Key,ru,en\nold,Привет,\nnew1,Новый {0},\nnew2,Играть$сейчас,\n".encode())],
        "string",
        "quick",
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
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                "source_language": "ru",
                "included_target_languages": ["en"],
            },
        ),
    )
    manifest = draft["manifest"]
    original = exports.build_export(manifest, {"kit-1": {"en": {"old": "Hello"}}})
    store.save("export", "export:old", "owner", original)
    before = copy.deepcopy(manifest)
    remote = {
        "languages": ["de", "en"],
        "components": {
            "ui": {
                "units": [
                    {"key": "old", "targets": {"de": "Hallo", "en": "Hello"}},
                    {"key": "new1", "targets": {"de": "Neu {0}", "en": "New {0}"}},
                    {"key": "new2", "targets": {"de": "Jetzt$spielen", "en": "Play$now"}},
                ]
            }
        },
    }
    value = exports.version_export(manifest, remote, {"kit.csv\u001fkit": "ui"})
    assert value["per_language"]["de"]["translated"] == 3
    rows = value["sheets"][0]["rows"]
    assert rows[1][rows[0].index("de")] == "Hallo"
    assert "New {0}" in rows[2] and "Play$now" in rows[3]
    assert manifest == before
    assert store.get("export", "export:old", "owner")["sheets"] == original["sheets"]
