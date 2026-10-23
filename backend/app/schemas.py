# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(ClosedModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


class ProjectCreate(ClosedModel):
    name: str = Field(min_length=1, max_length=200)


class SheetSelection(ClosedModel):
    file: str = Field(max_length=500)
    sheet: str = Field(max_length=200)


class ColumnRole(SheetSelection):
    column: int = Field(ge=1, le=10000)
    role: Literal["key", "language", "comment", "reference", "explanation", "flags", "ignored"]
    language: str | None = None


class UploadAnswers(ClosedModel):
    selected_files: list[str] = Field(default_factory=list, max_length=100)
    selected_sheets: list[SheetSelection] = Field(default_factory=list, max_length=100)
    source_language: str | None = None
    included_target_languages: list[str] = Field(default_factory=list, max_length=100)
    column_roles: list[ColumnRole] = Field(default_factory=list, max_length=10000)
    duplicate_policy: Literal["separate-components", "quarantine"] = "separate-components"
    profile_answers: dict = Field(default_factory=dict)


class UploadPatch(ClosedModel):
    revision: str
    answers: UploadAnswers | None = None
    ui_mode: Literal["quick", "detailed"]
    current_step: Literal["select-content", "map-columns", "confirm"]


class ProfileAnswers(ClosedModel):
    genre: str = Field(default="", max_length=100)
    setting: str = Field(default="", max_length=600)
    player_role: str = Field(default="", max_length=300)
    audience: str = Field(default="", max_length=400)
    register_ui: str = Field(default="", max_length=100)
    register_dialogue: str = Field(default="", max_length=100)
    profanity: str = Field(default="", max_length=100)
    profanity_level: str | None = Field(default=None, max_length=100)
    cjk_politeness: str | None = Field(default=None, max_length=200)


class TranslationProfile(ClosedModel):
    persona: str = Field(min_length=1, max_length=4000)
    style: str = Field(min_length=1, max_length=4000)
    language_instructions: dict[str, str] = Field(default_factory=dict)


class ProfilePatch(ClosedModel):
    revision: str
    answers: ProfileAnswers


class ManualProfilePatch(ClosedModel):
    revision: str
    generated: TranslationProfile


class BDHCPreviewRequest(ClosedModel):
    revision: str
    title_id: str


class BDHCApplyRequest(ClosedModel):
    revision: str
    preview_id: str


class ContextResolveRequest(ClosedModel):
    revision: str
    upload_token: str = Field(min_length=1, max_length=200)
    query: str | None = Field(default=None, max_length=200)
    title_id: str | None = Field(default=None, max_length=200)


class ProfilePrepareRequest(ClosedModel):
    revision: str
    upload_token: str = Field(min_length=1, max_length=200)
    upload_revision: str
    context_snapshot_id: str | None = None
    answers: ProfileAnswers
    edited_fields: list[str] = Field(default_factory=list, max_length=120)


class ProfileCommitRequest(ProfilePrepareRequest):
    job_id: str | None = None
    generated: TranslationProfile
