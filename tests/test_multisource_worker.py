# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import Store
from app.models import Job
from app.services.profiles import base_profile, get_profile, profile_key
from app.services.runs import start_run
from app.settings import Settings
from app.worker import process_once
from test_multisource_manifest import prepared


def test_fallback_source_metadata_reaches_mt(tmp_path):
    manifest = prepared(tmp_path / "kit")
    store = Store(tmp_path / "run")
    store.save("project", "local", "owner", {"slug": "local", "name": "Demo"})
    store.save(
        "draft",
        "draft",
        "owner",
        {
            "token": "draft",
            "project": "local",
            "kind": "string",
            "expires": 9999999999,
            "errors": [],
            "manifest": manifest,
        },
    )
    profile = get_profile(store, "owner", "local", "openrouter")
    value = base_profile(profile)
    value.update(
        answers={"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"},
        generated={"persona": "Translator", "style": "Clear", "language_instructions": {}},
    )
    store.save("profile", profile_key("local"), "owner", value)
    draft = store.get("draft", "draft", "owner")
    run = start_run(
        store, "owner", "local", {"upload_id": "draft", "revision": draft["revision"]}, "openrouter"
    )
    components = {c["id"]: c for c in manifest["components"]}

    class Remote:
        project = None
        created = {}
        mt = []
        values = {}

        def get_project(self, *_):
            return self.project

        def create_project(self, name, slug):
            self.project = {"name": name, "slug": slug}
            return self.project

        def set_profile(self, *_):
            return {}

        def get_component(self, project, name):
            return self.created.get(name)

        def create_component(self, project, name, source, payload, **kwargs):
            assert source == components[name]["source"]
            self.created[name] = {"slug": name}
            return self.created[name]

        def get_translation_status(self, project, name, code):
            assert code != components[name]["source"]
            return {"total": len(components[name]["units"])}

        def download_translation(self, project, name, code):
            return {
                u["key"]: self.values.get((name, code, u["key"]), u["values"].get(code, ""))
                for u in components[name]["units"]
            }

        def start_translation(self, project, name, code):
            self.mt.append((name, code))
            for u in components[name]["units"]:
                if not u["values"].get(code):
                    self.values[name, code, u["key"]] = "translated"
            return {}

    remote = Remote()
    assert process_once(
        store, Settings(data_dir=tmp_path / "run", external_writes_enabled=True), remote
    )
    with store.session() as session:
        job = session.get(Job, run["id"])
        assert job.status == "completed", job.data.get("failure")
    assert ("kit-1-src-ru", "ru") not in remote.mt
    assert ("kit-1", "ru") not in remote.mt  # Supplied target preserved.
    assert set(remote.created) == set(components)
