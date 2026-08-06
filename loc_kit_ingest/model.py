# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    component: str
    sheet: str
    row: int
    message: str


@dataclass(frozen=True)
class SkippedRow:
    component: str
    sheet: str
    row: int
    reason: str


@dataclass(frozen=True)
class StringUnit:
    key: str
    values: dict[str, str]
    comments: tuple[str, ...]
    references: tuple[str, ...]
    row: int


@dataclass(frozen=True)
class GlossaryTerm:
    context: str
    values: dict[str, str]
    explanations: dict[str, str]
    section: str
    term_row: int
    description_row: int


class ParsedUnit(Protocol):
    """StringUnit or GlossaryTerm - the two normalized record types."""


@dataclass(frozen=True)
class ParseResult:
    component: str
    kind: str
    units: tuple[ParsedUnit, ...]
    diagnostics: tuple[Diagnostic, ...]
    skipped_rows: tuple[SkippedRow, ...]
