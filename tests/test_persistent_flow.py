# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time

import pytest
from app.db import Store
from app.models import Job
from app.schemas import UploadPatch
from app.services.drafts import create_draft, patch_draft
from app.services.kit_versions import confirm_changes, preview_changes
from app.services.profiles import base_profile, get_profile, profile_key
from app.services.runs import start_run
from app.settings import Settings
from app.worker import process_once


def test_new_fallback_group_unknown_mt_observed_once_and_merged_export(tmp_path):
    from app.db import DomainError
    from app.services.exports import build_export
    from app.services.incremental import process_incremental
    from app.services.kit_diff import compare_kit
    from test_multisource_manifest import prepared

    manifest = prepared(tmp_path, "Key,de,ru,en,pl\na,Hallo,,,Keep\nb,,Привет,,\n")
    profile = {"persona": "Translator", "style": "Clear", "language_instructions": {}}
    state = {
        "slug": "game",
        "profile": {"openrouter": profile},
        "components": {
            "kit-1": {
                "source": "de",
                "complete": True,
                "can_add_source": True,
                "languages": ["ru", "pl"],
                "file_format": "po-mono",
                "units": [
                    {
                        "key": "a",
                        "source": "Hallo",
                        "source_id": 1,
                        "targets": {"ru": "Ready", "pl": "Keep"},
                        "target_ids": {"ru": 2, "pl": 3},
                    }
                ],
            }
        },
    }
    plan = compare_kit(
        copy.deepcopy(state), manifest, {"kit.csv\u001fkit": "kit-1"}, allow_new_groups=True
    )
    assert not plan["blocked"]
    plan["snapshot"] = copy.deepcopy(state)
    data = {
        "remote_project": "game",
        "operations": {},
        "snapshot": {
            "manifest": manifest,
            "profile": profile,
            "change_plan": plan,
        },
    }
    groups = {c["id"]: c for c in manifest["components"]}

    class Remote:
        creations = []
        mt_calls = []

        def create_component(self, project, cid, source, archive):
            assert project == "game" and source == "ru"
            self.creations.append(cid)
            state["components"][cid] = {
                "source": source,
                "complete": True,
                "can_add_source": True,
                "languages": [],
                "file_format": "po-mono",
                "units": [
                    {
                        "key": u["key"],
                        "source": u["values"][source],
                        "source_id": 10,
                        "targets": {},
                        "target_ids": {},
                    }
                    for u in groups[cid]["units"]
                ],
            }
            return {}

        def create_translation(self, project, cid, code):
            assert code != state["components"][cid]["source"]
            state["components"][cid]["languages"].append(code)
            for unit in state["components"][cid]["units"]:
                unit["targets"][code] = ""
                unit["target_ids"][code] = 11
            return {}

        def start_scoped_translation(self, project, cid, code, ids):
            self.mt_calls.append((cid, code, ids))
            raise DomainError("weblate_unknown", "timeout", 503)

    remote = Remote()
    archive_calls = []

    def archive(group, cid):
        archive_calls.append(cid)
        from app.worker import archive_component

        return archive_component(group, tmp_path / cid)

    for _ in range(2):
        with pytest.raises(DomainError, match="подтверждение") as error:
            process_incremental(
                data,
                remote,
                lambda: None,
                snapshot_loader=lambda: copy.deepcopy(state),
                archive_glossary=archive,
            )
        assert error.value.code == "incremental_wait"
    assert remote.creations == archive_calls == ["kit-1-src-ru"]
    assert remote.mt_calls == [("kit-1-src-ru", "pl", [11])]
    state["components"]["kit-1-src-ru"]["units"][0]["targets"]["pl"] = "Cześć"
    translated = process_incremental(
        data,
        remote,
        lambda: None,
        snapshot_loader=lambda: copy.deepcopy(state),
        archive_glossary=archive,
    )
    exported = build_export(manifest, translated, prefer_remote=True)
    assert len(exported["sheets"]) == 1
    rows = exported["sheets"][0]["rows"]
    assert rows[1][4] == "Keep" and rows[2][1] == "" and rows[2][4] == "Cześć"
    next_plan = compare_kit(state, manifest, plan["mapping"], allow_new_groups=True)
    assert not next_plan["blocked"]
    assert not any(g["new"] or any(g["translate"].values()) for g in next_plan["components"])


@pytest.mark.parametrize("next_code", ["en", "pt_BR", "es_MX", "zh_Hans"])
def test_persistent_updates_and_new_locale_preserve_old_exports(tmp_path, monkeypatch, next_code):
    store = Store(tmp_path)
    store.save("project", "local", "owner", {"slug": "local", "name": "Test", "persistent": True})
    profile = {"persona": "P", "style": "S", "language_instructions": {}}
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
            "generated": profile,
        },
        old["revision"],
    )
    state = {
        "slug": "game",
        "name": "Test",
        "languages": ["de"],
        "profile": {"openrouter": profile},
        "issues": [],
        "components": {
            "ui": {
                "slug": "ui",
                "name": "UI",
                "source": "ru",
                "languages": ["de"],
                "glossary": False,
                "complete": True,
                "can_add_source": True,
                "file_format": "po-mono",
                "units": [
                    {
                        "key": "old",
                        "source": "Привет",
                        "source_id": 1,
                        "targets": {"de": "Hallo"},
                        "target_ids": {"de": 2},
                    }
                ],
            }
        },
    }
    store.save(
        "project_link",
        "link:local",
        "owner",
        {
            "project": "local",
            "remote_slug": "game",
            "origin": "created-v2",
            "read_only": False,
            "component_map": {"kit.csv\u001fkit": "ui"},
            "snapshot": copy.deepcopy(state),
            "observed": time.time(),
        },
    )
    monkeypatch.setattr(
        "app.services.project_links.snapshot_project", lambda *args: copy.deepcopy(state)
    )
    monkeypatch.setattr(
        "app.services.kit_versions.snapshot_project", lambda *args: copy.deepcopy(state)
    )
    monkeypatch.setattr(
        "app.services.incremental.snapshot_project", lambda *args: copy.deepcopy(state)
    )

    class Remote:
        calls = []
        created = []

        def set_profile(self, project, generated):
            state["profile"]["openrouter"] = copy.deepcopy(generated)
            return {}

        def add_source_unit(self, project, cid, source, key, text, **kwargs):
            units = state["components"][cid]["units"]
            uid = max(u["source_id"] for u in units) + 10
            units.append(
                {
                    "key": key,
                    "source": text,
                    "source_id": uid,
                    "targets": {"de": ""},
                    "target_ids": {"de": uid + 1},
                }
            )
            return {}

        def create_translation(self, project, cid, code):
            self.created.append(code)
            state["languages"].append(code)
            state["components"][cid]["languages"].append(code)
            for u in state["components"][cid]["units"]:
                u["targets"][code] = ""
                u["target_ids"][code] = u["source_id"] + 100
            return {}

        def start_scoped_translation(self, project, cid, code, ids):
            self.calls.append((code, sorted(ids)))
            for u in state["components"][cid]["units"]:
                if u["target_ids"].get(code) in ids:
                    u["targets"][code] = code + ":" + u["source"]
            return {}

    remote = Remote()

    def run(code):
        saved_profile = get_profile(store, "owner", "local", "openrouter")
        store.save(
            "profile",
            profile_key("local"),
            "owner",
            {
                **base_profile(saved_profile),
                "generated": {**profile, "language_instructions": {code: "Use correct locale"}},
            },
            saved_profile["revision"],
        )
        d = create_draft(
            store,
            "owner",
            "local",
            [("kit.csv", "Key,ru\nold,Привет\nnew1,Новый {0}\nnew2,Играть$сейчас\n".encode())],
            "string",
            "quick",
        )
        d = patch_draft(
            store,
            "owner",
            d["token"],
            UploadPatch(
                revision=d["revision"],
                ui_mode="quick",
                current_step="confirm",
                answers={
                    "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                    "source_language": "ru",
                    "included_target_languages": [code],
                },
            ),
        )
        p = preview_changes(
            store, "owner", "local", remote, d["token"], d["revision"], {"kit.csv\u001fkit": "ui"}
        )
        p = confirm_changes(store, "owner", "local", remote, p["id"], p["revision"])
        assert d["manifest"]["targets"] == [code]
        assert list(p["components"][0]["translate"]) == [code]
        body = {
            "upload_id": d["token"],
            "revision": d["revision"],
            "change_plan_id": p["id"],
            "change_plan_revision": p["revision"],
        }
        r = start_run(store, "owner", "local", body, "openrouter")
        assert start_run(store, "owner", "local", body, "openrouter")["id"] == r["id"]
        process_once(store, Settings(data_dir=tmp_path), remote)
        with store.session() as session:
            j = session.get(Job, r["id"])
            assert j.status == "completed", j.data["failure"]
            assert j.data["snapshot"]["profile"]["language_instructions"] == {
                code: "Use correct locale"
            }
        return store.get("export", "export:" + r["id"], "owner")

    german = run("de")
    immutable = copy.deepcopy(german)
    english = run(next_code)
    assert german == immutable
    assert english["per_language"]["de"]["translated"] == 3
    assert english["per_language"][next_code]["translated"] == 3
    assert remote.calls == [("de", [12, 22]), (next_code, [101, 111, 121])]
    assert remote.created == [next_code]
    assert state["components"]["ui"]["units"][0]["targets"]["de"] == "Hallo"
