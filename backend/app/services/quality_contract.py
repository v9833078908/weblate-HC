# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db import DomainError

FULL_QUALITY_CAPABILITIES = (
    "candidate_quality_gate",
    "glossary_direct_scan",
    "quality_group_recheck",
    "quality_repeat_plan",
    "quality_final_snapshot",
    "quality_idempotency",
)
_TEXT_FIELDS = {"source", "target", "candidate", "prompt", "evidence", "candidate_text"}


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")


class QualityIssue(ContractModel):
    group_id: str = Field(min_length=1)
    kind: Literal["repeat-drift", "glossary", "check"]
    hard: bool
    advisory: bool
    unit_ids: list[int]


class QualitySnapshot(ContractModel):
    protected_content_digest: str | None = None
    snapshot_hash: str = Field(min_length=1)
    scope_revision: str = Field(min_length=1)
    complete: Literal[True]
    unit_ids: list[int]
    revisions: dict[str, str]
    judged_revisions: dict[str, str]
    issues: list[QualityIssue]


class RepeatGroup(ContractModel):
    group_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    unit_ids: list[int]
    outcome: Literal[
        "pending",
        "ready",
        "applied",
        "no_change",
        "independent",
        "approved_conflict",
        "needs_review",
        "stale",
        "forbidden",
        "unrepairable",
        "failed",
    ]
    kind: Literal["repeat-drift"] = "repeat-drift"


class RepeatPlan(ContractModel):
    token: str = Field(min_length=1)
    status: Literal["queued", "running", "ready", "failed"]
    groups: list[RepeatGroup]


class RepeatResult(ContractModel):
    unit: int
    revision: str
    outcome: Literal[
        "applied",
        "no_change",
        "stale",
        "forbidden",
        "approved",
        "independent",
        "needs_review",
        "failed",
    ]


class RepeatRun(ContractModel):
    token: str = Field(min_length=1)
    status: Literal["queued", "running", "completed", "partial", "failed"]
    changed_unit_ids: list[int]
    results: list[RepeatResult]


def require_text_free(value: object) -> None:
    if isinstance(value, dict):
        if _TEXT_FIELDS.intersection(value):
            raise DomainError("weblate_schema", "Quality response contains translation text", 502)
        for item in value.values():
            require_text_free(item)
    elif isinstance(value, list):
        for item in value:
            require_text_free(item)


def validate_contract(model: type[BaseModel], value: object) -> dict:
    require_text_free(value)
    try:
        model.model_validate(value)
    except ValidationError:
        raise DomainError(
            "weblate_schema", "Weblate quality response is incompatible", 502
        ) from None
    return {key: item for key, item in value.items() if key != "_http_status"}


def validate_snapshot(value: object, scope: dict) -> dict:
    result = validate_contract(QualitySnapshot, value)
    ids = result["unit_ids"]
    expected = scope.get("unit_ids")
    if (
        not isinstance(expected, list)
        or not expected
        or len(ids) != len(set(ids))
        or set(ids) != set(expected)
        or set(result["revisions"]) != {str(unit_id) for unit_id in ids}
        or any(not item for item in result["revisions"].values())
        or not set(result["judged_revisions"]).issubset(result["revisions"])
    ):
        raise DomainError("weblate_schema", "Quality snapshot does not cover the saved scope", 502)
    return result


def require_full_quality_capabilities(identity: dict) -> None:
    capabilities = identity.get("capabilities", {})
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("judge") is not True
        or type(capabilities.get("judge_seats")) is not int
        or capabilities.get("judge_seats") != 2
        or type(capabilities.get("quality_api_version")) is not int
        or capabilities.get("quality_api_version") != 2
        or any(capabilities.get(name) is not True for name in FULL_QUALITY_CAPABILITIES)
    ):
        raise DomainError(
            "quality_configuration",
            "Weblate full quality API version 2 is not available",
            503,
        )
