# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.db import DomainError
from app.schemas import ProfilePrepareRequest
from app.services.profile_agent import (
    build_profile_input,
    load_profile_skills,
    template_profile,
    validate_agent_result,
)
from test_profile_context import context_client as context_client
from test_profile_context import resolve


def make_input(fixture):
    _, store, slug, _, profile, draft = fixture
    context = resolve(fixture).json()
    return build_profile_input(
        store,
        "producer",
        slug,
        ProfilePrepareRequest(
            revision=profile["revision"],
            upload_token="kit",
            upload_revision=draft["revision"],
            context_snapshot_id=context["snapshot_id"],
            answers=context["answers"],
        ),
    )


def test_exact_draft_and_source_only_evidence(context_client):
    value = make_input(context_client)
    assert value["source_language"] == "ru"
    assert value["target_languages"] == ["zh_Hant"]
    assert value["game_title"] == "Dead Shell"
    assert "PRIVATE TARGET" not in str(value)
    assert value["evidence"][0]["source"] == "Начать"
    assert "проверить регион" in value["locale_guides"]["zh_Hant"]


def test_actual_source_profile_evidence(context_client):
    _, store, _, _, _, draft = context_client
    stored = store.get("draft", "kit", "producer")
    stored.pop("revision")
    component = stored["manifest"]["components"][0]
    component["source"] = "en"
    component["units"][0]["values"]["en"] = "Play {0}"
    saved = store.save("draft", "kit", "producer", stored)
    draft["revision"] = saved["revision"]
    value = make_input(context_client)
    assert value["source_languages"] == ["en"]
    assert value["evidence"][0]["source_language"] == "en"
    assert value["evidence"][0]["source"] == "Play {0}"
    assert "{0}" in value["protected_tokens"]["examples"]
    assert "фактического исходного языка" in template_profile(value)["persona"]


def test_template_uses_facts_and_does_not_invent_game(context_client):
    value = make_input(context_client)
    generated = template_profile(value)
    assert "Dead Shell" in generated["persona"]
    assert "RPG" in generated["persona"]
    assert "головолом" not in generated["persona"]
    value.update(game_title=None, answers={}, facts={})
    assert "головолом" not in template_profile(value)["persona"]


@pytest.mark.parametrize(
    "bad",
    [
        {"persona": " ", "style": "a", "language_instructions": {"zh_Hant": "a"}},
        {"persona": "a", "style": "a", "language_instructions": {}},
        {"persona": "a", "style": "a", "language_instructions": {"de": "a"}},
        {"persona": "a", "style": "a", "language_instructions": {"zh_Hant": "a" * 1001}},
    ],
)
def test_invalid_agent_output_rejected(context_client, bad):
    with pytest.raises(DomainError):
        validate_agent_result(bad, make_input(context_client))


def test_engine_rules_attached_even_if_model_omits_them(context_client):
    value = make_input(context_client)
    result = validate_agent_result(
        {"persona": "Роль", "style": "Стиль", "language_instructions": {"zh_Hant": "Правила"}},
        value,
    )
    assert "переменные" in result["style"]
    assert "числа" in result["style"]
    assert "новых фактов" in result["style"]
    assert load_profile_skills()[0] and len(load_profile_skills()[1]) == 64


def test_wrong_draft_revision_rejected(context_client):
    _, store, slug, _, profile, _ = context_client
    with pytest.raises(DomainError):
        build_profile_input(
            store,
            "producer",
            slug,
            ProfilePrepareRequest(
                revision=profile["revision"], upload_token="kit", upload_revision="old", answers={}
            ),
        )


def test_all_locale_guides_and_region_separation(context_client):
    import json

    from app.services.profile_agent import SKILLS

    rules = json.loads((SKILLS / "locales.json").read_text(encoding="utf-8"))["guides"]
    assert len(rules) == 14
    assert rules["zh_Hans"] != rules["zh_Hant"]
    assert "Тайвань" in rules["zh_Hant"]
    assert "pt" not in rules and "es" not in rules
    value = make_input(context_client)
    value.update(
        target_languages=list(rules),
        locale_guides=rules,
        game_title="Lord Ambermaze",
        project_name="Test_API_LOC",
        answers={"genre": "Puzzle, Adventure"},
    )
    result = template_profile(value)
    assert "Lord Ambermaze" in result["persona"] and "Test_API_LOC" not in result["persona"]
    assert set(result["language_instructions"]) == set(rules)


def test_tokens_found_beyond_sample(context_client):
    _, store, _, _, _, draft = context_client
    value = {k: v for k, v in draft.items() if k != "revision"}
    value["manifest"]["components"][0]["units"] += [
        {"key": str(i), "values": {"ru": "Пример"}} for i in range(15)
    ]
    value["manifest"]["components"][0]["units"].append(
        {"key": "last", "values": {"ru": "item_type[|{0}]$<b>42</b>"}}
    )
    saved = store.save("draft", "kit", "producer", value, draft["revision"])
    fixture = (*context_client[:5], saved)
    result = make_input(fixture)
    assert len(result["evidence"]) == 10
    assert result["protected_tokens"]["has_dollar_separator"]
    assert "item_type" in result["protected_tokens"]["examples"]


def test_rare_locale_is_explicitly_unverified(context_client):
    _, store, _, _, _, draft = context_client
    value = {k: v for k, v in draft.items() if k != "revision"}
    value["answers"]["included_target_languages"] = ["ga"]
    saved = store.save("draft", "kit", "producer", value, draft["revision"])
    result = make_input((*context_client[:5], saved))
    assert result["target_languages"] == ["ga"]
    assert "ga: no verified local locale guide" in result["warnings"]
    assert "вручную" in template_profile(result)["language_instructions"]["ga"]


def test_template_includes_confirmed_game_locale_override(context_client):
    value = make_input(context_client)
    value["locale_rules"] = {
        "zh_Hant": {
            "register": "informal",
            "locale_notes": "Уникальное правило игры",
            "voices": [{"name": "Hero", "register": "formal"}],
        }
    }
    result = template_profile(value)
    instructions = result["language_instructions"]["zh_Hant"]
    assert "Уникальное правило игры" in instructions and "Hero" in instructions


def test_aggregate_agent_input_limit_rejects_rule_multiplication(context_client):
    _, store, slug, _, profile, draft = context_client
    context = resolve(context_client).json()
    snapshot = store.get("profile_context", context["snapshot_id"], "producer")
    expected = snapshot.pop("revision")
    snapshot["locale_rules"] = {"zh_Hant": {"mandatory": "x" * 30000}}
    store.save("profile_context", context["snapshot_id"], "producer", snapshot, expected)
    with pytest.raises(DomainError, match="context limit"):
        build_profile_input(
            store,
            "producer",
            slug,
            ProfilePrepareRequest(
                revision=profile["revision"],
                upload_token="kit",
                upload_revision=draft["revision"],
                context_snapshot_id=context["snapshot_id"],
                answers={},
            ),
        )


def test_conflicting_game_profiles_block_automatic_preparation(context_client, monkeypatch):
    from app.integrations.bdhc import BDHCClient
    from app.services.profile_jobs import prepare_profile
    from app.settings import Settings

    _, store, slug, _, profile, draft = context_client
    monkeypatch.setattr(
        BDHCClient,
        "read_profile",
        lambda *_: {
            "schema_version": "12.7",
            "context_hash": "conflict",
            "game": {"name": "Dead Shell"},
            "brief": {},
            "style_profiles": [
                {"locale": "zh_Hant", "register": "formal"},
                {"locale": "zh_Hant", "register": "informal"},
            ],
        },
    )
    context = resolve(context_client).json()
    body = ProfilePrepareRequest(
        revision=profile["revision"],
        upload_token="kit",
        upload_revision=draft["revision"],
        context_snapshot_id=context["snapshot_id"],
        answers={},
    )
    with pytest.raises(DomainError, match="rules conflict"):
        prepare_profile(
            store,
            Settings(
                data_dir=store.data_dir,
                profile_agent_enabled=True,
                llm_model="openai/gpt-5.4-mini",
                llm_token_file="unused",
            ),
            "producer",
            slug,
            body,
        )
    assert not store.list("profile_generation", "producer")
