# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.services.profile_agent import template_profile
from app.services.profile_jobs import get_profile_job, prepare_profile, process_profile_once
from app.services.profiles import base_profile, get_profile, profile_key
from app.settings import Settings
from test_profile_context import context_client as context_client
from test_profile_jobs import configured, request


def test_result_saved_without_browser_and_refresh_does_not_generate(context_client):
    _, store, slug, _, _, _ = context_client
    settings = configured(store)
    body = request(context_client)
    job = prepare_profile(store, settings, "producer", slug, body)
    calls = []

    def generate(settings, value):
        calls.append(value)
        return template_profile(value)

    assert process_profile_once(store, settings, generate)
    saved = get_profile_job(store, "producer", slug, job["job_id"])
    profile = get_profile(store, "producer", slug, settings.engine)
    assert saved["state"] == "ready" and saved["auto_saved"]
    assert profile["generated"] == saved["result"]
    body.revision = profile["revision"]
    assert prepare_profile(store, settings, "producer", slug, body)["job_id"] == job["job_id"]
    assert not process_profile_once(store, settings, generate)
    assert len(calls) == 1


def test_profile_modified_during_generation_is_not_overwritten(context_client):
    _, store, slug, _, profile, _ = context_client
    settings = configured(store)
    job = prepare_profile(store, settings, "producer", slug, request(context_client))
    value = base_profile(profile)
    value["answers"]["genre"] = "Manually changed"
    store.save("profile", profile_key(slug), "producer", value, profile["revision"])
    process_profile_once(store, settings, lambda _, value: template_profile(value))
    assert get_profile_job(store, "producer", slug, job["job_id"])["state"] == "stale"
    assert (
        get_profile(store, "producer", slug, settings.engine)["answers"]["genre"]
        == "Manually changed"
    )


def test_disabled_agent_autosaves_template_not_llm(context_client):
    _, store, slug, _, _, _ = context_client
    result = prepare_profile(
        store, Settings(data_dir=store.data_dir), "producer", slug, request(context_client)
    )
    assert result["state"] == "ready" and result["auto_saved"]
    assert "profile_agent_not_configured" in result["warnings"]
    assert get_profile(store, "producer", slug, "openrouter")["generation_mode"] == "template"


def test_manual_commit_updates_answers_and_preserves_other_languages(context_client):
    client, store, slug, headers, _, _ = context_client
    body = request(context_client)
    result = prepare_profile(store, Settings(data_dir=store.data_dir), "producer", slug, body)
    profile = get_profile(store, "producer", slug, "openrouter")
    value = base_profile(profile)
    value["generated"]["language_instructions"]["de"] = "Protected German"
    value["answers"]["player_role"] = "Legacy role"
    profile = store.save("profile", profile_key(slug), "producer", value, profile["revision"])
    generated = result["result"]
    generated["style"] = "Manual style"
    data = {
        **body.model_dump(),
        "revision": profile["revision"],
        "generated": generated,
        "edited_fields": ["style"],
        "answers": {**profile["answers"], "genre": "User genre"},
    }
    response = client.post(
        f"/api/producer/projects/{slug}/profile/commit/", headers=headers, json=data
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["answers"]["genre"] == "User genre"
    assert value["answers"]["player_role"] == "Legacy role"
    assert value["generated"]["language_instructions"]["de"] == "Protected German"
    assert value["field_origins"]["style"] == "manual"
    assert (
        client.post(
            f"/api/producer/projects/{slug}/profile/commit/", headers=headers, json=data
        ).status_code
        == 409
    )


def test_prepare_endpoint_and_owner_bound_poll(context_client):
    client, _, slug, headers, _, _ = context_client
    response = client.post(
        f"/api/producer/projects/{slug}/profile/prepare/",
        headers=headers,
        json=request(context_client).model_dump(),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert client.get(
        f"/api/producer/projects/{slug}/profile/preparations/{result['job_id']}/"
    ).json()["auto_saved"]


def test_enabled_legacy_answer_patch_does_not_call_llm(context_client, monkeypatch):
    client, _, slug, headers, profile, _ = context_client
    client.app.state.settings.profile_agent_enabled = True
    client.app.state.settings.llm_token_file = "no-file"
    client.app.state.settings.llm_model = "model"
    monkeypatch.setattr(
        "app.routes.profiles.generate_profile",
        lambda *args: (_ for _ in ()).throw(AssertionError("synchronous call")),
    )
    response = client.patch(
        f"/api/producer/projects/{slug}/profile/",
        headers=headers,
        json={"revision": profile["revision"], "answers": {"genre": "RPG"}},
    )
    assert response.status_code == 200, response.text


def test_cached_result_uses_current_manual_text(context_client):
    _, store, slug, _, _, _ = context_client
    settings = Settings(data_dir=store.data_dir)
    body = request(context_client)
    result = prepare_profile(store, settings, "producer", slug, body)
    profile = get_profile(store, "producer", slug, "openrouter")
    value = base_profile(profile)
    value["generated"]["style"] = "Current manual style"
    value["field_origins"]["style"] = "manual"
    saved = store.save("profile", profile_key(slug), "producer", value, profile["revision"])
    body.revision = saved["revision"]
    reused = prepare_profile(store, settings, "producer", slug, body)
    assert reused["result"]["style"] == "Current manual style"
    assert reused["profile_revision"] == saved["revision"]
    assert reused["job_id"] == result["job_id"]


def test_explicit_context_override_keeps_manual_origin_after_autosave(context_client):
    from app.schemas import ProfilePrepareRequest
    from app.services.profile_jobs import prepare_profile
    from app.services.profiles import get_profile
    from app.settings import Settings
    from test_profile_context import resolve

    _, store, slug, _, profile, draft = context_client
    context = resolve(context_client).json()
    body = ProfilePrepareRequest(
        revision=profile["revision"],
        upload_token="kit",
        upload_revision=draft["revision"],
        context_snapshot_id=context["snapshot_id"],
        answers={"genre": "USER MANUAL GENRE"},
    )
    prepare_profile(store, Settings(data_dir=store.data_dir), "producer", slug, body)
    saved = get_profile(store, "producer", slug, "openrouter")
    assert saved["field_origins"]["answers.genre"] == "manual"
    refreshed = resolve(context_client, revision=saved["revision"]).json()
    assert refreshed["answers"]["genre"] == "USER MANUAL GENRE"
