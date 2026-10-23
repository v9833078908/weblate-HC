# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from app.db import DomainError, Store
from app.models import Job
from app.schemas import UploadPatch
from app.services.drafts import create_draft, patch_draft
from app.services.profiles import base_profile, get_profile, profile_key
from app.services.runs import get_run, resume_run, start_run
from app.settings import Settings
from app.worker import process_once


def waiting_run(tmp_path, monkeypatch, *, accepted=False, **settings_overrides):
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    store = Store(tmp_path)
    owner, slug = "producer", "test-local"
    store.save("project", slug, owner, {"slug": slug, "name": "Test"})
    draft = create_draft(
        store,
        owner,
        slug,
        [("kit.csv", b"Key,en,ru\none,Hello,\ntwo,Goodbye,\nthree,Play,\n")],
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
                "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                "source_language": "en",
                "included_target_languages": ["ru"],
            },
        ),
    )
    profile = get_profile(store, owner, slug, "openrouter")
    value = base_profile(profile)
    value.update(
        answers={"register_ui": "neutral", "register_dialogue": "neutral", "profanity": "none"},
        generated={"persona": "Translator", "style": "Clear", "language_instructions": {}},
    )
    store.save("profile", profile_key(slug), owner, value, profile["revision"])
    run = start_run(
        store,
        owner,
        slug,
        {"upload_id": draft["token"], "revision": draft["revision"]},
        "openrouter",
    )
    # These operations were confirmed before the interrupted MT request.
    with store.session() as session:
        job = session.get(Job, run["id"])
        job.data = {
            **job.data,
            "operations": {
                key: {"state": "confirmed"} for key in ["project", "profile", "component:kit-1"]
            },
        }

    class Remote:
        values = {"one": "", "two": "", "three": ""}
        posts = 0

        def get_translation_status(self, *args):
            return {"total": 3}

        def download_translation(self, *args):
            return self.values.copy()

        def start_translation(self, *args):
            self.posts += 1
            if accepted:
                return {"_http_status": 202, "task_url": "tasks/test/"}
            raise DomainError("weblate_unknown", "Response lost", 503)

        def get_task(self, *args):
            return {"completed": False}

    return (
        store,
        owner,
        run["id"],
        Remote(),
        Settings(data_dir=tmp_path, **settings_overrides),
        clock,
    )


def test_default_wait_survives_five_minutes_and_extends_only_on_new_progress(tmp_path, monkeypatch):
    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    origin = clock[0]
    assert process_once(store, settings, remote)
    clock[0] = origin + 601
    assert process_once(Store(tmp_path), settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    clock[0] = origin + 1200
    remote.values["one"] = "Привет"
    assert process_once(store, settings, remote)
    clock[0] = origin + 1801
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    # Re-reading the same translated row must not extend the deadline forever.
    clock[0] = origin + 3001
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "unknown"
    assert remote.posts == 1
    resume_run(store, owner, run, 1)
    remote.values.update(two="Пока", three="Играть")
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "completed"
    assert remote.posts == 1


@pytest.mark.parametrize("accepted", [False, True])
def test_progress_and_running_task_cannot_exceed_absolute_bound(tmp_path, monkeypatch, accepted):
    store, owner, run, remote, settings, clock = waiting_run(
        tmp_path,
        monkeypatch,
        accepted=accepted,
        mt_observation_seconds=1200,
        mt_max_observation_seconds=1800,
    )
    origin = clock[0]
    assert process_once(store, settings, remote)
    for elapsed, key in [(601, "one"), (1202, "two")]:
        clock[0] = origin + elapsed
        remote.values[key] = "Перевод"
        assert process_once(store, settings, remote)
        assert get_run(store, owner, run)["status"] == "running"
    clock[0] = origin + 1801
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "unknown"
    assert remote.posts == 1


def test_idle_timeout_can_be_configured_without_repeating_mt(tmp_path, monkeypatch):
    store, owner, run, remote, settings, clock = waiting_run(
        tmp_path,
        monkeypatch,
        mt_observation_seconds=600,
    )
    assert process_once(store, settings, remote)
    clock[0] += 601
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "unknown"
    assert remote.posts == 1


def test_saved_five_minute_intent_adopts_longer_window_without_a_post(tmp_path, monkeypatch):
    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    with store.session() as session:
        job = session.get(Job, run)
        job.data = {
            **job.data,
            "operations": {
                **job.data["operations"],
                "mt:kit-1:ru": {"state": "intent", "observe_until": clock[0] + 300},
            },
        }
    clock[0] += 601
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    clock[0] += 1200
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "unknown"
    assert remote.posts == 0


@pytest.mark.parametrize("legacy", ["intent", "accepted-task", "post-timeout-deadline"])
def test_legacy_observation_uses_durable_run_start_as_conservative_bound(
    tmp_path, monkeypatch, legacy
):
    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    origin = clock[0]
    state = {"state": "intent"}
    if legacy == "accepted-task":
        state = {"state": "accepted", "result": {"_http_status": 202, "task_url": "tasks/test/"}}
    elif legacy == "post-timeout-deadline":
        state["observe_until"] = origin + 600
    with store.session() as session:
        job = session.get(Job, run)
        job.data = {
            **job.data,
            "started": datetime.fromtimestamp(origin, timezone.utc).isoformat(),
            "operations": {**job.data["operations"], "mt:kit-1:ru": state},
        }
    clock[0] = origin + 7201
    remote.values["one"] = "Поздний результат"
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "unknown"
    assert remote.posts == 0


def test_wait_diagnostics_expose_counts_and_deadline_without_game_text(tmp_path, monkeypatch):
    from app.services.diagnostics import diagnostic_report

    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch)
    assert process_once(store, settings, remote)
    clock[0] += 601
    remote.values["one"] = "Привет"
    assert process_once(store, settings, remote)
    coverage = get_run(store, owner, run)["coverage"]["mt:kit-1:ru"]
    assert coverage["translated"] == 1 and coverage["total"] == 3
    with store.session() as session:
        report = diagnostic_report(run, session.get(Job, run).data)
    mt = next(op for op in report["operations"] if op["operation"] == "mt")
    assert mt["observation"]["filled"] == 1
    assert mt["observation"]["total"] == 3
    assert mt["observe_until"] > clock[0]
    assert "Hello" not in str(report) and "Привет" not in str(report)


@pytest.mark.parametrize("idle,maximum", [(0, 7200), (1800, 600), (1800, 86401)])
def test_invalid_observation_configuration_fails_before_dispatch(idle, maximum):
    with pytest.raises(ValueError):
        Settings(mt_observation_seconds=idle, mt_max_observation_seconds=maximum)
