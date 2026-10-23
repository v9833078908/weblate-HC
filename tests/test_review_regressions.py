# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from pathlib import Path

import httpx
from app.db import Store
from app.integrations.weblate import WeblateClient
from app.services import exports, kit_versions
from app.services.drafts import create_draft
from app.services.maintenance import cleanup_expired
from app.settings import Settings


def test_original_version_survives_temporary_draft_cleanup(tmp_path):
    store = Store(tmp_path)
    payload = b"Key,ru,de\nkey,Source,Target\n"
    draft = create_draft(store, "owner", "local", [("kit.csv", payload)], "string", "quick")
    draft["manifest"] = {"components": [], "source": "ru", "targets": ["de"], "quarantine": []}
    version = kit_versions.retain_version(store, "owner", "local", draft)
    stored = {k: v for k, v in draft.items() if k != "revision"}
    stored["expires"] = time.time() - 1
    store.save("draft", draft["token"], "owner", stored)
    cleanup_expired(store)
    assert not (tmp_path / "uploads" / draft["token"]).exists()
    originals = tmp_path / version["originals"]
    assert any(p.read_bytes() == payload for p in originals.rglob("*") if p.is_file())
    assert store.get("kit_version", version["id"], "owner")["manifest"] == draft["manifest"]


def test_refreshed_project_download_uses_current_snapshot_not_old_pointer(tmp_path):
    store = Store(tmp_path)
    store.save(
        "export_pointer",
        "export-pointer:local",
        "owner",
        {"export_id": "export:old", "run_id": "old"},
    )
    store.save("export", "export:old", "owner", {"sheets": [], "marker": "immutable-history"})
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
                        "source": "Source",
                        "targets": {"de": "New German", "en": "English"},
                    }
                ],
            }
        },
    }
    store.save(
        "project_link",
        "link:local",
        "owner",
        {"snapshot": snapshot, "observed": time.time(), "read_only": True},
    )
    result = exports.project_snapshot(store, "owner", "local")
    assert "New German" in result["sheets"][0]["rows"][1]
    assert result["per_language"]["en"]["translated"] == 1
    assert store.get("export", "export:old", "owner")["marker"] == "immutable-history"


def test_profile_patch_preserves_private_project_routing_and_key(tmp_path):
    token = tmp_path / "token"
    token.write_text("dummy")
    sent = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "openrouter": {
                        "key": "private-key",
                        "base_url": "https://private.invalid",
                        "routing": {"de": "model"},
                        "persona": "old",
                        "style": "old",
                        "language_instructions": {"de": "German"},
                    }
                },
            )
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = WeblateClient(
        Settings(weblate_token_file=token), transport=httpx.MockTransport(handler)
    )
    client.set_profile(
        "game",
        {
            "persona": "New",
            "style": "Style",
            "language_instructions": {"de": "German", "en": "English"},
        },
    )
    cfg = sent[0]["configuration"]
    assert cfg["key"] == "private-key" and cfg["routing"] == {"de": "model"}
    assert cfg["language_instructions"]["de"] == "German"


def test_rollback_prevents_worker_restart_across_reboot():
    script = (Path(__file__).parents[1] / "deploy/rollback.sh").read_text()
    assert "systemctl disable wizard-worker.service" in script
    assert "systemctl mask wizard-worker.service" in script


def test_glossary_publication_needs_separate_consent_and_never_replaces_old():
    import copy

    import pytest
    from app.db import DomainError, revision
    from app.services import incremental

    before = {
        "slug": "game",
        "profile": {"openrouter": {"persona": "P", "style": "S", "language_instructions": {}}},
        "components": {
            "old-glossary": {
                "glossary": True,
                "source": "ru",
                "units": [{"key": "old", "source": "Old", "targets": {"de": "Alt"}}],
            }
        },
    }
    glossary = {
        "source": "ru",
        "targets": ["de"],
        "components": [
            {"id": "kit-1", "units": [{"key": "new", "values": {"ru": "New", "de": "Neu"}}]}
        ],
    }
    data = {
        "remote_project": "game",
        "operations": {},
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["de"]},
            "profile": before["profile"]["openrouter"],
            "glossary": glossary,
            "glossary_confirmed": False,
            "change_plan": {
                "snapshot": copy.deepcopy(before),
                "snapshot_hash": revision(before),
                "components": [],
                "glossary_hash": revision(glossary),
            },
        },
    }
    calls = []

    class Remote:
        def create_component(self, project, cid, source, archive, glossary=False):
            calls.append(cid)
            before["components"][cid] = {
                "source": "ru",
                "complete": True,
                "glossary": True,
                "units": [{"key": "new", "source": "New", "targets": {"de": "Neu"}}],
            }
            return {}

    with pytest.raises(DomainError, match="подтвердите"):
        incremental.process_incremental(
            data,
            Remote(),
            lambda: None,
            snapshot_loader=lambda: copy.deepcopy(before),
            archive_glossary=lambda c, n: b"zip",
        )
    assert calls == []
    data["snapshot"]["glossary_confirmed"] = True
    incremental.process_incremental(
        data,
        Remote(),
        lambda: None,
        snapshot_loader=lambda: copy.deepcopy(before),
        archive_glossary=lambda c, n: b"zip",
    )
    assert len(calls) == 1 and calls[0] != "old-glossary"
    assert before["components"]["old-glossary"]["units"][0]["targets"]["de"] == "Alt"


def test_new_source_context_is_delivered_and_verified_before_translation():
    import copy

    from app.db import revision
    from app.services.incremental import process_incremental

    state = {
        "slug": "game",
        "profile": {"openrouter": {"persona": "P", "style": "S", "language_instructions": {}}},
        "components": {
            "ui": {
                "source": "ru",
                "languages": ["de"],
                "complete": True,
                "file_format": "po-mono",
                "units": [
                    {
                        "key": "new",
                        "source": "New",
                        "source_id": 1,
                        "explanation": "",
                        "targets": {"de": "Neu"},
                        "target_ids": {"de": 2},
                    }
                ],
            }
        },
    }
    plan = {
        "snapshot": copy.deepcopy(state),
        "snapshot_hash": revision(state),
        "components": [
            {
                "local_id": "kit-1",
                "remote_id": "ui",
                "new": [{"key": "new", "source": "New", "explanation": "A menu button"}],
                "seed": {"de": {}},
                "translate": {"de": []},
            }
        ],
    }
    calls = []

    class Remote:
        def set_unit_explanation(self, uid, text):
            calls.append((uid, text))
            state["components"]["ui"]["units"][0]["explanation"] = text
            return {}

    data = {
        "remote_project": "game",
        "operations": {},
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["de"]},
            "profile": state["profile"]["openrouter"],
            "change_plan": plan,
        },
    }
    process_incremental(data, Remote(), lambda: None, snapshot_loader=lambda: copy.deepcopy(state))
    assert calls == [(1, "A menu button")]
    assert data["operations"]["delta:context:ui:new"]["state"] == "confirmed"


def test_observing_one_mt_batch_loads_one_snapshot_not_one_per_string():
    import copy

    from app.services.incremental import process_incremental

    profile = {"persona": "P", "style": "S", "language_instructions": {}}
    units = [
        {
            "key": str(i),
            "source": "S" + str(i),
            "source_id": i + 1,
            "targets": {"de": "T" + str(i)},
            "target_ids": {"de": i + 101},
        }
        for i in range(100)
    ]
    snapshot = {
        "slug": "game",
        "profile": {"openrouter": profile},
        "components": {
            "ui": {"source": "ru", "languages": ["de"], "complete": True, "units": units}
        },
    }
    keys = [u["key"] for u in units]
    plan = {
        "snapshot": copy.deepcopy(snapshot),
        "components": [
            {
                "local_id": "kit-1",
                "remote_id": "ui",
                "new": [],
                "seed": {"de": {}},
                "translate": {"de": keys},
            }
        ],
    }
    data = {
        "remote_project": "game",
        "operations": {"delta:mt:ui:de:0": {"state": "accepted"}},
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["de"]},
            "profile": profile,
            "change_plan": plan,
        },
    }
    count = 0

    def load():
        nonlocal count
        count += 1
        return copy.deepcopy(snapshot)

    process_incremental(data, object(), lambda: None, snapshot_loader=load)
    assert count < 10


def test_uploaded_glossary_prepares_real_table_and_valid_tbx(tmp_path):
    import io
    import zipfile

    from app.services.kit_versions import prepare_glossary
    from app.worker import archive_component

    store = Store(tmp_path)
    draft = create_draft(
        store, "owner", "local", [("terms.csv", b"ru,de\nSword,Schwert\n")], "glossary", "quick"
    )
    result = prepare_glossary(
        store,
        "owner",
        "local",
        {"source": "ru", "targets": ["de"]},
        {"glossary_upload_id": draft["token"], "glossary_revision": draft["revision"]},
    )
    assert result["components"][0]["units"][0]["values"]["de"] == "Schwert"
    archive = archive_component(result["components"][0], tmp_path / "tbx")
    with zipfile.ZipFile(io.BytesIO(archive)) as value:
        assert any(b"Schwert" in value.read(name) for name in value.namelist())
