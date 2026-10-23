# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.db import Store
from app.schemas import UploadPatch
from app.services.drafts import create_draft, patch_draft


def prepared(
    tmp_path,
    text="Key,de,ru,en,pl\na,Hallo,Привет,Hello,\nb,,Мир,World,\nc,,,Play,\nd,,,,\n,,,,\n",
    source="de",
):
    store = Store(tmp_path)
    draft = create_draft(store, "owner", "local", [("kit.csv", text.encode())], "string", "quick")
    saved = patch_draft(
        store,
        "owner",
        draft["token"],
        UploadPatch(
            revision=draft["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "source_language": source,
                "included_target_languages": ["ru", "pl"] if source != "ru" else ["pl"],
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
            },
        ),
    )
    assert not saved["errors"], saved["errors"]
    return saved["manifest"]


def test_manifest_groups_have_actual_source(tmp_path):
    manifest = prepared(tmp_path)
    assert manifest["manifest_version"] == 2
    assert manifest["source"] == "de"
    groups = {c["source"]: c for c in manifest["components"]}
    assert set(groups) == {"de", "ru", "en"}
    assert groups["de"]["id"] == "kit-1"
    assert groups["ru"]["id"] == "kit-1-src-ru"
    assert groups["en"]["id"] == "kit-1-src-en"
    assert groups["ru"]["targets"] == ["pl"]
    assert groups["ru"]["units"][0]["source_origin"] == "fallback"
    assert groups["ru"]["units"][0]["original_row"] == 3
    assert manifest["sheets"][0]["rows"][2][1] == ""
    assert manifest["totals"] == {"input_rows": 4, "imported_rows": 3, "quarantined_rows": 1}
    assert manifest["quarantine"][0]["row"] == 5


def test_absent_declared_column_and_unselected_fallback(tmp_path):
    manifest = prepared(tmp_path, "Key,ru,en,pl\na,Текст,Text,\nb,,Play,\n", source="de")
    assert {c["source"] for c in manifest["components"]} == {"ru", "en"}
    assert manifest["sheets"][0]["rows"][0] == ["Key", "ru", "en", "pl"]


def test_duplicate_key_across_source_groups(tmp_path):
    manifest = prepared(tmp_path, "Key,de,ru,en,pl\na,Hallo,,,\na,,Привет,,\n")
    assert not manifest["components"]
    assert {q["reason_code"] for q in manifest["quarantine"]} == {"duplicate_key"}


def test_rendered_keys_equal_group_members(tmp_path):
    from app.worker import archive_component

    manifest = prepared(tmp_path / "data")
    for group in manifest["components"]:
        archive_component(group, tmp_path / group["id"])
    bad = manifest["components"][0]
    bad["units"] = []
    with pytest.raises(Exception, match="membership"):
        archive_component(bad, tmp_path / "bad")


def test_old_manifest_readable():
    from app.services.manifest import component_mapping_key, component_source, component_targets

    manifest = {"source": "en", "targets": ["ru"]}
    component = {"file": "kit.csv", "sheet": "UI"}
    assert component_source(manifest, component) == "en"
    assert component_targets(manifest, component) == ["ru"]
    assert component_mapping_key(manifest, component) == "kit.csv\u001fUI"
