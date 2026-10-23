# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest


def test_legacy_project_becomes_read_only_after_confirmed_creation(tmp_path):
    changed_source = False
    import io

    from app.db import DomainError, Store
    from app.models import Job
    from app.schemas import UploadPatch
    from app.services.drafts import create_draft, patch_draft
    from app.services.profiles import base_profile, get_profile, profile_key
    from app.services.runs import start_run
    from app.settings import Settings
    from app.worker import process_once
    from translate.storage.xliff import Xliff1File

    store = Store(tmp_path)
    store.save(
        "project",
        "local",
        "producer",
        {
            "slug": "local",
            "name": "Lord Ambermaze",
            "advanced_url": "https://example.test/projects/local/",
        },
    )
    profile = get_profile(store, "producer", "local", "openrouter")
    value = base_profile(profile)
    value.update(
        answers={"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"},
        generated={"persona": "Translator", "style": "Clear", "language_instructions": {}},
    )
    store.save("profile", profile_key("local"), "producer", value, profile["revision"])

    def new_run(target):
        draft = create_draft(
            store,
            "producer",
            "local",
            [
                (
                    "kit.csv",
                    b"Key,en,ru,de\none,Hello,RU one,Ready\ntwo,Goodbye,RU two,Local German\nthree,Play,RU three,\n",
                )
            ],
            "string",
            "quick",
        )
        draft = patch_draft(
            store,
            "producer",
            draft["token"],
            UploadPatch(
                revision=draft["revision"],
                ui_mode="quick",
                current_step="confirm",
                answers={
                    "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                    "source_language": "en",
                    "included_target_languages": [target],
                },
            ),
        )
        return start_run(
            store,
            "producer",
            "local",
            {"upload_id": draft["token"], "revision": draft["revision"]},
            "openrouter",
        )

    class Remote:
        project = None
        component = None
        project_posts = 0
        component_posts = 0
        mt_posts = 0
        uploads = 0
        units_ready = False
        values = {
            "en": {"one": "Hello", "two": "Goodbye", "three": "Play"},
            "ru": {"one": "RU one", "two": "RU two", "three": "RU three"},
            "de": {"one": "", "two": "Existing remote German", "three": ""},
        }

        def get_project(self, *_):
            return self.project

        def create_project(self, name, slug):
            self.project_posts += 1
            self.project = {"name": name, "slug": slug}
            return self.project

        def get_component(self, *_):
            return self.component

        def create_component(self, *_, **kwargs):
            self.component_posts += 1
            self.component = {"slug": "kit-1"}
            return self.component

        def set_profile(self, *_):
            return {}

        def get_translation_status(self, _project, _component, code):
            delayed = changed_source == "delayed-units" and code == "de" and not self.units_ready
            return {"total": 0 if delayed else 3}

        def download_translation(self, _project, _component, code):
            return self.values[code].copy()

        def upload_translation(self, _project, _component, code, payload):
            self.uploads += 1
            for unit in Xliff1File(io.BytesIO(payload)).units:
                if not unit.istranslatable():
                    continue
                key = unit.xmlelement.get("resname")
                if key not in self.values["en"] or str(unit.source) != self.values["en"][key]:
                    return {"not_found": 2, "accepted": 0, "skipped": 0, "total": 2}
                if str(unit.target) and not self.values[code][key]:
                    self.values[code][key] = str(unit.target)
            return {"result": True}

        def start_translation(self, _project, _component, code):
            self.mt_posts += 1
            assert (
                code == "de"
                and self.values[code]["one"] == "Ready"
                and self.values[code]["two"] == "Existing remote German"
            )
            if changed_source == "after-wait":
                raise DomainError("weblate_unknown", "Response lost", 503)
            self.values[code]["three"] = "Spielen"
            return {}

    remote = Remote()
    first = new_run("ru")
    with pytest.raises(DomainError, match="still active"):
        new_run("de")
    process_once(store, Settings(data_dir=tmp_path), remote)
    with pytest.raises(DomainError) as error:
        new_run("de")
    assert error.value.code == "project_read_only"
    with store.session() as session:
        assert session.get(Job, first["id"]).status == "completed"
    assert remote.project_posts == 1 and remote.component_posts == 1
    assert remote.uploads == 0 and remote.mt_posts == 0


@pytest.mark.parametrize(
    "other_status", ["queued", "running", "unknown", "failed-intent", "failed-accepted"]
)
def test_resume_cannot_overlap_another_run_for_the_same_project(tmp_path, other_status):
    from app.db import DomainError, Store
    from app.models import Job
    from app.services.runs import ensure_project_available, resume_run

    store = Store(tmp_path)
    data = {
        "project": "local",
        "remote_project": "remote",
        "created": "now",
        "started": None,
        "finished": None,
        "failure": "",
        "warnings": [],
        "attempt": 1,
        "operations": {},
        "phase": "queued",
        "export_id": None,
    }
    with store.session() as session:
        session.add(
            Job(
                id="first",
                owner="producer",
                idempotency_key="first",
                status="failed",
                data=data.copy(),
            )
        )
        session.add(
            Job(
                id="second",
                owner="producer",
                idempotency_key="second",
                status="failed" if other_status.startswith("failed-") else other_status,
                data={
                    **data,
                    "operations": {"mt:kit-1:de": {"state": other_status.split("-")[1]}}
                    if other_status.startswith("failed-")
                    else {},
                },
            )
        )
    message = (
        "paid translation is unconfirmed" if other_status.startswith("failed-") else "still active"
    )
    with store.session() as session, pytest.raises(DomainError, match=message):
        ensure_project_available(session, "producer", "local")
    with pytest.raises(DomainError, match=message):
        resume_run(store, "producer", "first", 1)
    with store.session() as session:
        assert session.get(Job, "first").status == "failed"
        assert session.get(Job, "first").data["attempt"] == 1


@pytest.mark.parametrize("rejected", [True, False])
def test_project_validation_rejection_can_resume_but_unknown_intent_cannot_repeat(
    tmp_path, rejected
):
    from app.db import DomainError, Store
    from app.integrations.weblate import WeblateRejected
    from app.models import Job
    from app.services.runs import resume_run
    from app.settings import Settings
    from app.worker import process_once

    store = Store(tmp_path)
    with store.session() as session:
        session.add(
            Job(
                id="run",
                owner="producer",
                idempotency_key="unique",
                status="queued",
                data={
                    "project": "local",
                    "remote_project": "remote-12345678",
                    "created": "now",
                    "started": None,
                    "finished": None,
                    "attempt": 1,
                    "failure": "",
                    "phase": "queued",
                    "warnings": [],
                    "export_id": None,
                    "operations": {},
                    "snapshot": {
                        "name": "Lord Ambermaze",
                        "engine": "openrouter",
                        "manifest": {},
                        "profile": {},
                    },
                },
            )
        )

    class Remote:
        posts = 0
        project = None

        def get_project(self, *_):
            return self.project

        def create_project(self, *_):
            self.posts += 1
            if self.posts == 1:
                if rejected:
                    raise WeblateRejected(400)
                raise DomainError("weblate_unknown", "Response lost", 503)
            self.project = {"slug": "remote-12345678"}
            return self.project

        def set_profile(self, *_):
            # Stop after project creation, without importing or translating.
            raise DomainError("test_stop", "Stop before translation", 400)

    remote = Remote()
    process_once(store, Settings(data_dir=tmp_path), remote)
    with store.session() as session:
        state = session.get(Job, "run").data["operations"]["project"]
        assert state["state"] == ("rejected" if rejected else "intent")
    resume_run(store, "producer", "run", 1)
    process_once(store, Settings(data_dir=tmp_path), remote)
    assert remote.posts == (2 if rejected else 1)
    with store.session() as session:
        job = session.get(Job, "run")
        assert job.data["operations"]["project"]["state"] == ("confirmed" if rejected else "intent")
        assert job.status == ("failed" if rejected else "unknown")


@pytest.mark.parametrize("interruption", ["cancel", "lease"])
def test_export_publication_is_fenced(tmp_path, monkeypatch, interruption):
    import time

    import app.worker as worker
    from app.db import Store
    from app.models import Job, Record
    from app.schemas import UploadPatch
    from app.services.drafts import create_draft, patch_draft
    from app.services.runs import cancel_run
    from app.settings import Settings

    store = Store(tmp_path)
    draft = create_draft(
        store, "producer", "local", [("kit.csv", b"Key,en,ru\none,Hello,Keep\n")], "string", "quick"
    )
    draft = patch_draft(
        store,
        "producer",
        draft["token"],
        UploadPatch(
            revision=draft["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                "source_language": "en",
                "included_target_languages": ["ru"],
            },
        ),
    )
    with store.session() as s:
        s.add(
            Job(
                id="run",
                owner="producer",
                idempotency_key="unique",
                status="queued",
                data={
                    "project": "local",
                    "remote_project": "remote",
                    "created": "now",
                    "finished": None,
                    "attempt": 1,
                    "failure": "",
                    "phase": "queued",
                    "started": None,
                    "snapshot": {
                        "engine": "openrouter",
                        "manifest": draft["manifest"],
                        "glossary": None,
                    },
                    "operations": {
                        key: {"state": "confirmed"}
                        for key in ["project", "profile", "component:kit-1"]
                    },
                    "warnings": [],
                    "export_id": None,
                },
            )
        )
    original = worker.build_export

    def interrupted(*args):
        result = original(*args)
        if interruption == "cancel":
            cancel_run(store, "producer", "run")
        else:
            with store.session() as s:
                job = s.get(Job, "run")
                job.lease_owner = "replacement"
                job.lease_until = time.time() + 900
        return result

    monkeypatch.setattr(worker, "build_export", interrupted)

    class Remote:
        def get_translation_status(self, *args):
            return {"total": 1}

        def download_translation(self, *args):
            return {"one": "Keep"}

    worker.process_once(store, Settings(data_dir=tmp_path), Remote())
    with store.session() as s:
        assert s.get(Record, "export-pointer:local") is None
        assert s.get(Record, "export:run") is None
        job = s.get(Job, "run")
        assert job.status == ("cancelled" if interruption == "cancel" else "running")
        assert job.data["export_id"] is None


@pytest.mark.parametrize(
    "outcome",
    [
        "immediate",
        "delayed",
        "absent",
        "missing-keys",
        "read-unavailable",
        "read-server-error",
        "accepted",
    ],
)
def test_persistent_snapshot_unknown_mt_resumes_without_second_post(tmp_path, monkeypatch, outcome):
    import time

    import app.worker as worker

    clock = [time.time()]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    from app.db import DomainError, Store
    from app.schemas import UploadPatch
    from app.services.drafts import create_draft, patch_draft
    from app.services.profiles import base_profile, get_profile, profile_key
    from app.services.runs import get_run, resume_run, start_run
    from app.settings import Settings
    from app.worker import process_once

    store = Store(tmp_path)
    owner = "producer"
    slug = "test-local"
    store.save("project", slug, owner, {"slug": slug, "name": "тест"})
    draft = create_draft(
        store,
        owner,
        slug,
        [("kit.csv", b"Key,en,ru\none,Hello,\ntwo,Goodbye,Keep this\n")],
        "string",
        "quick",
    )
    draft = patch_draft(
        store,
        owner,
        draft["token"],
        UploadPatch(
            revision=draft["revision"],
            ui_mode="quick",
            current_step="confirm",
            answers={
                "selected_files": ["kit.csv"],
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                "source_language": "en",
                "included_target_languages": ["ru"],
            },
        ),
    )
    assert not draft["errors"]
    profile = get_profile(store, owner, slug, "openrouter")
    value = base_profile(profile)
    value.update(
        answers={"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"},
        generated={
            "persona": "Game translator",
            "style": "Clear language",
            "language_instructions": {},
        },
    )
    store.save("profile", profile_key(slug), owner, value, profile["revision"])
    body = {"upload_id": draft["token"], "revision": draft["revision"]}
    run = start_run(store, owner, slug, body, "openrouter")
    from app.models import Job

    with store.session() as session:
        first = session.get(Job, run["id"])
        assert first.data["snapshot"]["name"] == "тест — " + run["id"][:8]
    assert start_run(store, owner, slug, body, "openrouter")["id"] == run["id"]
    navigated = patch_draft(
        store,
        owner,
        draft["token"],
        UploadPatch(revision=draft["revision"], ui_mode="detailed", current_step="map-columns"),
    )
    assert (
        start_run(
            store,
            owner,
            slug,
            {"upload_id": draft["token"], "revision": navigated["revision"]},
            "openrouter",
        )["id"]
        == run["id"]
    )

    class Remote:
        project = None
        component = None
        language = None
        language_posts = 0
        posts = 0
        translated = False
        read_failed = False

        def get_project(self, *args):
            return self.project

        def create_project(self, name, slug):
            self.project = {"slug": slug, "name": name}
            return self.project

        def set_profile(self, *args):
            return {}

        def get_component(self, *args):
            return self.component

        def create_component(self, *args, **kwargs):
            self.component = {"task_url": None}
            return self.component

        def get_translation_status(self, *args):
            return self.language

        def create_translation(self, *args):
            self.language_posts += 1
            self.language = {"total": 2}
            return self.language

        def start_translation(self, *args):
            self.posts += 1
            self.translated = outcome == "immediate"
            if outcome == "accepted":
                return {"_http_status": 202}
            raise DomainError("weblate_unknown", "No response", 503)

        def download_translation(self, *args):
            if outcome == "missing-keys" and self.posts and not self.translated:
                return {}
            if (
                outcome in {"read-unavailable", "read-server-error"}
                and self.posts
                and not self.read_failed
            ):
                self.read_failed = True
                if outcome == "read-server-error":
                    from app.integrations.weblate import WeblateRejected

                    raise WeblateRejected(503)
                raise DomainError("weblate_unavailable", "Read temporarily unavailable", 503)
            return {"one": "Привет" if self.translated else "", "two": "Changed remotely"}

    remote = Remote()
    settings = Settings(data_dir=tmp_path)
    assert process_once(store, settings, remote)
    interrupted = get_run(store, owner, run["id"])
    assert remote.posts == 1
    if outcome == "immediate":
        assert interrupted["status"] == "completed"
    else:
        assert interrupted["status"] == "running" and interrupted["finished"] is None
        store = Store(tmp_path)
        assert get_run(store, owner, run["id"])["status"] == "running"
        assert not process_once(store, settings, remote), "Observe only after the polling delay"
        clock[0] += 301 if outcome in {"absent", "missing-keys"} else 31
        remote.translated = outcome not in {"absent", "missing-keys"}
        assert process_once(store, settings, remote)
        if outcome in {"absent", "missing-keys"}:
            assert get_run(store, owner, run["id"])["status"] == "running"
            clock[0] += 1500
            assert process_once(store, settings, remote)
            assert get_run(store, owner, run["id"])["status"] == "unknown"
            assert remote.posts == 1
            resume_run(store, owner, run["id"], 1)
            remote.translated = True
            assert process_once(store, settings, remote)
    completed = get_run(store, owner, run["id"])
    assert completed["status"] == "completed" and remote.posts == 1 and remote.language_posts == 1
    from app.services.exports import export_file, export_summary

    summary = export_summary(store, owner, slug, [])
    assert summary["keys"] == 2 and summary["empty"] == 0
    payload, _, _ = export_file(store, owner, slug, [], "json", summary["revision"])
    import json

    result = json.loads(payload)
    assert result["sheets"][0]["rows"][1][2] == "Привет"
    assert result["sheets"][0]["rows"][2][2] == "Keep this"
    assert "snapshot" not in completed
