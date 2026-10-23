# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from app.schemas import ProfilePrepareRequest
from app.services.profile_jobs import get_profile_job, prepare_profile, process_profile_once
from app.settings import Settings
from test_profile_context import context_client as context_client
from test_profile_context import resolve


def request(fixture):
    _, _, _, _, profile, draft = fixture
    context = resolve(fixture).json()
    return ProfilePrepareRequest(
        revision=profile["revision"],
        upload_token="kit",
        upload_revision=draft["revision"],
        context_snapshot_id=context["snapshot_id"],
        answers=context["answers"],
    )


def configured(store):
    return Settings(
        data_dir=store.data_dir,
        profile_agent_enabled=True,
        llm_model="openai/gpt-5.4-mini",
        llm_token_file="test-key",
    )


def test_concurrent_identical_prepare_creates_one_job(context_client):
    _, store, slug, _, _, _ = context_client
    body = request(context_client)
    settings = configured(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(
            pool.map(lambda _: prepare_profile(store, settings, "producer", slug, body), range(4))
        )
    assert len({job["job_id"] for job in jobs}) == 1
    assert jobs[0]["state"] == "queued"


def test_different_model_or_inputs_have_distinct_key(context_client):
    _, store, slug, _, _, _ = context_client
    body = request(context_client)
    settings = configured(store)
    first = prepare_profile(store, settings, "producer", slug, body)
    settings.llm_model = "other-model"
    second = prepare_profile(store, settings, "producer", slug, body)
    assert first["input_key"] != second["input_key"]


def test_worker_timeout_is_unknown_without_retry(context_client):
    _, store, slug, _, _, _ = context_client
    settings = configured(store)
    job = prepare_profile(store, settings, "producer", slug, request(context_client))
    calls = []

    def timeout(settings, value):
        calls.append(value)
        raise TimeoutError("secret provider text")

    assert process_profile_once(store, settings, timeout)
    result = get_profile_job(store, "producer", slug, job["job_id"])
    assert result["state"] == "unknown" and not result["auto_saved"]
    assert "secret" not in str(result)
    assert not process_profile_once(store, settings, timeout)
    assert len(calls) == 1


def test_crash_after_dispatch_does_not_repay(context_client):
    _, store, slug, _, _, _ = context_client
    settings = configured(store)
    job = prepare_profile(store, settings, "producer", slug, request(context_client))
    value = store.get("profile_generation", job["job_id"], "producer")
    rev = value.pop("revision")
    value.update(state="running", dispatch_started=True, lease_until=time.time() - 1)
    store.save("profile_generation", job["job_id"], "producer", value, rev)
    assert process_profile_once(
        store, settings, lambda *args: (_ for _ in ()).throw(AssertionError("must not dispatch"))
    )
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "unknown"


def test_navigation_revision_does_not_repay_same_input(context_client):
    _, store, slug, _, _, draft = context_client
    settings = configured(store)
    body = request(context_client)
    job = prepare_profile(store, settings, "producer", slug, body)
    from app.services.profile_agent import template_profile

    process_profile_once(store, settings, lambda _, value: template_profile(value))
    from app.services.profiles import get_profile

    profile = get_profile(store, "producer", slug, settings.engine)
    value = {k: v for k, v in draft.items() if k != "revision"}
    value["current_step"] = "confirm"
    saved = store.save("draft", "kit", "producer", value, draft["revision"])
    context = resolve(context_client, revision=profile["revision"]).json()
    body.revision = profile["revision"]
    body.upload_revision = saved["revision"]
    body.context_snapshot_id = context["snapshot_id"]
    assert prepare_profile(store, settings, "producer", slug, body)["job_id"] == job["job_id"]


def test_worker_avoids_paid_call_for_already_stale_profile(context_client):
    _, store, slug, _, profile, _ = context_client
    settings = configured(store)
    job = prepare_profile(store, settings, "producer", slug, request(context_client))
    from app.services.profiles import base_profile, profile_key

    value = base_profile(profile)
    value["answers"]["genre"] = "Manual"
    store.save("profile", profile_key(slug), "producer", value, profile["revision"])
    called = []
    process_profile_once(store, settings, lambda *args: called.append(True))
    assert not called
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "stale"


def test_undispatched_stale_job_rebinds_to_current_navigation(context_client):
    _, store, slug, _, _, draft = context_client
    settings = configured(store)
    body = request(context_client)
    job = prepare_profile(store, settings, "producer", slug, body)
    value = {k: v for k, v in draft.items() if k != "revision"}
    value["current_step"] = "confirm"
    fresh = store.save("draft", "kit", "producer", value, draft["revision"])
    process_profile_once(
        store, settings, lambda *args: (_ for _ in ()).throw(AssertionError("must not call"))
    )
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "stale"
    body.upload_revision = fresh["revision"]
    body.context_snapshot_id = resolve(context_client).json()["snapshot_id"]
    rebound = prepare_profile(store, settings, "producer", slug, body)
    assert rebound["state"] == "queued" and rebound["job_id"] == job["job_id"]
    from app.services.profile_agent import template_profile

    calls = []
    process_profile_once(
        store, settings, lambda _, value: (calls.append(True), template_profile(value))[1]
    )
    assert len(calls) == 1
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "ready"


def test_validated_old_result_is_republished_without_paid_call(context_client):
    from app.services.profile_agent import template_profile
    from app.services.profiles import get_profile

    _, store, slug, _, _, _ = context_client
    settings = configured(store)
    first_body = request(context_client)
    first = prepare_profile(store, settings, "producer", slug, first_body)
    calls = []

    def generator(_, value):
        calls.append(True)
        return template_profile(value)

    process_profile_once(store, settings, generator)
    profile = get_profile(store, "producer", slug, settings.engine)
    body = request((*context_client[:4], profile, context_client[5]))
    body.answers.setting = "Different input B"
    second = prepare_profile(store, settings, "producer", slug, body)
    assert second["job_id"] != first["job_id"]
    process_profile_once(store, settings, generator)
    profile = get_profile(store, "producer", slug, settings.engine)
    first_body.revision = profile["revision"]
    first_body.context_snapshot_id = resolve(context_client, revision=profile["revision"]).json()[
        "snapshot_id"
    ]
    again = prepare_profile(store, settings, "producer", slug, first_body)
    assert again["job_id"] == first["job_id"] and again["state"] == "ready"
    assert len(calls) == 2


def test_recovered_validated_result_is_published_without_network(context_client):
    from app.services.profile_agent import template_profile

    _, store, slug, _, _, _ = context_client
    settings = configured(store)
    job = prepare_profile(store, settings, "producer", slug, request(context_client))
    value = store.get("profile_generation", job["job_id"], "producer")
    expected = value.pop("revision")
    value.update(
        state="running",
        result=template_profile(value["input"]),
        dispatch_started=True,
        lease_until=time.time() - 1,
    )
    store.save("profile_generation", job["job_id"], "producer", value, expected)

    def generator(*_):
        raise AssertionError("validated result must not be paid again")

    process_profile_once(store, settings, generator)
    process_profile_once(store, settings, generator)
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "ready"
