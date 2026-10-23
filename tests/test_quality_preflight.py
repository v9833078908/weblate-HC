# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.db import DomainError


class StubClient:
    def __init__(self, capabilities, estimate):
        self.capabilities = capabilities
        self.estimate = estimate
        self.calls = []

    def producer_me(self):
        self.calls.append("me")
        return {"username": "producer", "capabilities": self.capabilities}

    def judge_estimate(self, project, scope):
        self.calls.append(("estimate", project, scope))
        return self.estimate


def test_preflight_requires_exactly_two_judge_seats():
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {"judge": True, "judge_seats": 1},
        {"estimate_id": "unused"},
    )
    with pytest.raises(DomainError) as error:
        run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert error.value.code == "quality_configuration"
    assert client.calls == ["me"]


def test_preflight_rejects_mt_blockers_before_paid_judge_start():
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {"judge": True, "judge_seats": 2},
        {
            "estimate_id": "estimate",
            "selected": 1,
            "preparation": {
                "missing": 1,
                "engine": None,
                "blockers": ["Machine translation is not configured"],
            },
        },
    )
    with pytest.raises(DomainError) as error:
        run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert error.value.code == "quality_configuration"


def test_preflight_returns_estimate_when_quality_contract_is_ready():
    from app.services.quality_preflight import run_quality_preflight

    estimate = {
        "estimate_id": "estimate",
        "selected": 10,
        "preparation": {"missing": 0, "engine": "openrouter", "blockers": []},
    }
    client = StubClient({"judge": True, "judge_seats": 2}, estimate)
    result = run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert result == estimate
    assert client.calls == ["me", ("estimate", "project", {"unit_ids": [1]})]


@pytest.mark.parametrize("full_cycle", [False, True])
def test_preflight_allows_filled_scope_without_preparation_engine(full_cycle):
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {
            "judge": True,
            "judge_seats": 2,
            "quality_api_version": 2,
            "candidate_quality_gate": True,
            "glossary_direct_scan": True,
            "quality_group_recheck": True,
            "quality_repeat_plan": True,
            "quality_final_snapshot": True,
            "quality_idempotency": True,
        },
        {
            "estimate_id": "filled-scope",
            "selected": 1,
            "preparation": {"missing": 0, "per_language": {}, "engine": None, "blockers": []},
        },
    )
    result = run_quality_preflight(client, "project", {"unit_ids": [1]}, full_cycle=full_cycle)
    assert result["estimate_id"] == "filled-scope"


@pytest.mark.parametrize(
    "preparation",
    [
        {"engine": "openrouter", "blockers": []},
        {"missing": None, "engine": "openrouter", "blockers": []},
        {"missing": True, "engine": "openrouter", "blockers": []},
        {"missing": -1, "engine": "openrouter", "blockers": []},
        {"missing": "0", "engine": "openrouter", "blockers": []},
        {"missing": 0.5, "engine": "openrouter", "blockers": []},
        {"missing": 0, "blockers": []},
        {"missing": 0, "engine": {}, "blockers": []},
        {"missing": 0, "engine": True, "blockers": []},
        {"missing": 0, "engine": " ", "blockers": []},
        {"missing": 0, "engine": None, "blockers": None},
    ],
)
def test_preflight_rejects_incomplete_or_invalid_preparation(preparation):
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {"judge": True, "judge_seats": 2},
        {"estimate_id": "invalid", "selected": 1, "preparation": preparation},
    )
    with pytest.raises(DomainError) as error:
        run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert error.value.code == "weblate_schema"


@pytest.mark.parametrize("missing,blockers", [(1, []), (0, ["Permission denied"])])
def test_preflight_still_rejects_unready_preparation(missing, blockers):
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {"judge": True, "judge_seats": 2},
        {
            "estimate_id": "blocked",
            "selected": 1,
            "preparation": {
                "missing": missing,
                "engine": None,
                "blockers": blockers,
            },
        },
    )
    with pytest.raises(DomainError) as error:
        run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert error.value.code == "quality_configuration"


@pytest.mark.parametrize("selected", [0, -1, True, None, "1"])
def test_preflight_never_starts_judges_without_positive_selected_scope(selected):
    from app.services.quality_preflight import run_quality_preflight

    client = StubClient(
        {"judge": True, "judge_seats": 2},
        {
            "estimate_id": "empty-scope",
            "selected": selected,
            "preparation": {"missing": 0, "engine": None, "blockers": []},
        },
    )
    with pytest.raises(DomainError) as error:
        run_quality_preflight(client, "project", {"unit_ids": [1]})
    assert error.value.code == ("quality_configuration" if selected == 0 else "weblate_schema")
