# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

import httpx
import pytest
from app.db import Store
from app.models import Job
from app.settings import Settings
from app.worker import process_once


def test_incompatible_full_quality_server_is_detected_before_remote_writes(tmp_path):
    store = Store(tmp_path)
    with store.session() as session:
        session.add(
            Job(
                id="job",
                owner="owner",
                idempotency_key="job",
                status="queued",
                data={
                    "started": None,
                    "phase": "queued",
                    "remote_project": "remote",
                    "snapshot": {
                        "engine": "openrouter",
                        "manifest": {},
                        "quality": {
                            "enabled": True,
                            "workflow_version": 2,
                        },
                    },
                    "operations": {},
                },
            )
        )

    class LegacyRemote:
        def producer_me(self):
            return {"capabilities": {"judge": True, "judge_seats": 2}}

    settings = Settings(
        data_dir=tmp_path, quality_cycle_enabled=True, full_quality_cycle_enabled=True
    )
    process_once(store, settings, LegacyRemote())
    with store.session() as session:
        job = session.get(Job, "job")
        assert job.status == "running"
        assert job.data["phase"] == "quality-waiting-configuration"
        assert job.data["operations"] == {}
        assert job.data["quality"]["status"] == "waiting_for_configuration"


@pytest.mark.parametrize("throttle", [None, "get_producer_run", "apply_quality_candidates"])
def test_worker_publishes_corrected_file_instead_of_nonempty_uploaded_target(
    tmp_path, monkeypatch, throttle
):
    from app.integrations.weblate import WeblateRejected
    from app.schemas import UploadPatch
    from app.services.drafts import create_draft, patch_draft
    from app.services.profiles import base_profile, get_profile, profile_key
    from app.services.quality_state import update_quality_cycle
    from app.services.runs import start_run
    from test_full_quality_cycle import FullRemote

    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    store = Store(tmp_path)
    store.save("project", "local", "owner", {"slug": "local", "name": "Test", "advanced_url": ""})
    profile = get_profile(store, "owner", "local", "openrouter")
    value = base_profile(profile)
    value.update(
        answers={"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"},
        generated={"persona": "Translator", "style": "Clear", "language_instructions": {}},
    )
    store.save("profile", profile_key("local"), "owner", value, profile["revision"])
    draft = create_draft(
        store,
        "owner",
        "local",
        [("kit.csv", b"Key,en,de\none,Play,old\ntwo,Play,other\n")],
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
                "source_language": "en",
                "included_target_languages": ["de"],
            },
        ),
    )
    run = start_run(
        store,
        "owner",
        "local",
        {
            "upload_id": draft["token"],
            "revision": draft["revision"],
            "quality": {
                "enabled": True,
                "language": "de",
                "workflow_version": 2,
                "max_repair_rounds": 2,
            },
        },
        "openrouter",
    )

    class Remote(FullRemote):
        project = None
        component = None

        def get_project(self, project):
            return self.project

        def create_project(self, name, project, **options):
            self.project = {
                "slug": project,
                "name": name,
                "translation_review": options.get("translation_review", False),
            }
            return self.project

        def get_component(self, *args):
            return self.component

        def create_component(self, *args, **kwargs):
            self.component = {
                "slug": "kit-1",
                "allow_translation_propagation": kwargs.get("allow_translation_propagation", False),
            }
            return self.component

        def set_profile(self, *args):
            return {}

        def get_translation_status(self, *args):
            return {"total": 2, "translated": 2}

        def download_translation(self, *args):
            return {
                "one": "corrected" if self.revisions["1"] != "r1" else "old",
                "two": "corrected" if self.repeat_applied else "other",
            }

        def list_units(self, project, component, language):
            if language == "en":
                return [
                    {"id": 100 + i, "source": [key], "target": ["Play"], "context": key}
                    for i, key in enumerate(("one", "two"), 1)
                ]
            values = self.download_translation()
            return [
                {"id": i, "source_unit": 100 + i, "target": [values[key]]}
                for i, key in enumerate(("one", "two"), 1)
            ]

        def collect(self, path):
            if path.endswith("/components/"):
                return [
                    {
                        "slug": "kit-1",
                        "name": "Kit",
                        "source_language": "en",
                        "file_format": "po-mono",
                    }
                ]
            assert path.endswith("/translations/")
            return [{"language": "en"}, {"language": "de"}]

        def request(self, method, path):
            assert method == "GET" and path.endswith("/machinery_settings/")
            return {}

    remote = Remote()
    refusals = []
    if throttle:
        original = getattr(remote, throttle)

        def limited(*args, **kwargs):
            if len(refusals) < 7:
                refusals.append(clock[0])
                raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "90"}))
            return original(*args, **kwargs)

        setattr(remote, throttle, limited)
    settings = Settings(
        data_dir=tmp_path, quality_cycle_enabled=True, full_quality_cycle_enabled=True
    )
    for _ in range(70):
        process_once(store, settings, remote)
        with store.session() as session:
            job = session.get(Job, run["id"])
            assert job.status not in {"failed", "unknown"}, job.data
            if job.status == "completed":
                export_id = job.data["export_id"]
                break
            if job.data.get("rate_limit", {}).get("waiting"):
                assert job.data["rate_limit"]["retry_after"] == 90
                clock[0] = job.data["rate_limit"]["next_retry_at"]
            else:
                job.lease_until = 0
            cycle_id = job.data.get("quality_cycle_id")
        if cycle_id:
            update_quality_cycle(store, cycle_id, next_wakeup=0)
    else:
        raise AssertionError("Worker did not complete")
    exported = store.get("export", export_id, "owner")
    assert exported["sheets"][0]["rows"][1][2] == "corrected"
    assert exported["sheets"][0]["rows"][2][2] == "corrected"
    assert exported["quality_provenance"]["scope_revision"] == "scope-after"
    assert remote.project["translation_review"] is True
    assert remote.component["allow_translation_propagation"] is True
    if throttle:
        assert len(refusals) == 7
    assert remote.starts == [[1, 2], [2]], (
        "Previously accepted judge runs must not be started again"
    )
