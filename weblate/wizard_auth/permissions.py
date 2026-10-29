# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed route contract; scopes never replace native object permissions."""

from __future__ import annotations

import re

from rest_framework.exceptions import PermissionDenied

SEGMENT = r"[\w@.-]+"
UUID = r"[0-9a-f-]{36}"
TRANSLATION = rf"translations/{SEGMENT}/{SEGMENT}/{SEGMENT}"
RULES = (
    (
        "GET",
        rf"(?:projects(?:/{SEGMENT})?|languages|components/{SEGMENT}/{SEGMENT}|{TRANSLATION}(?:/units|/file)?|units/[0-9]+|tasks/{UUID})/",
        "wizard.read",
    ),
    ("GET", rf"projects/{SEGMENT}/(?:components|translations)/", "wizard.read"),
    ("GET", rf"components/{SEGMENT}/{SEGMENT}/translations/", "wizard.read"),
    (
        "GET",
        rf"producer/(?:me|projects|runs/{UUID}|repeat-plans/{UUID}|repeat-runs/{UUID})/",
        "wizard.read",
    ),
    (
        "GET",
        rf"producer/projects/{SEGMENT}/(?:decisions|quality/issues|quality/operations/{UUID}|mt/batches/{UUID}|translation-profile)/",
        "wizard.read",
    ),
    (
        "POST",
        rf"(?:projects|projects/{SEGMENT}/components|components/{SEGMENT}/{SEGMENT}/translations|{TRANSLATION}/(?:units|file))/",
        "wizard.import",
    ),
    ("PATCH", r"units/[0-9]+/", "wizard.import"),
    ("PATCH", rf"producer/projects/{SEGMENT}/translation-profile/", "wizard.import"),
    (
        "POST",
        rf"producer/projects/{SEGMENT}/mt/batches(?:/{UUID}/reconcile)?/",
        "wizard.translate",
    ),
    (
        "POST",
        rf"producer/projects/{SEGMENT}/(?:runs(?:/estimate)?|quality/(?:snapshot|recheck|repeats/plan)|decisions/apply)/",
        "wizard.quality",
    ),
    (
        "POST",
        rf"producer/(?:runs/{UUID}/(?:resume|cancel)|repeat-plans/{UUID}/apply|decisions/[0-9]+/(?:apply-candidate|clarification)|judge-applications/[0-9]+/undo)/",
        "wizard.quality",
    ),
)


def required_scope(method: str, path: str) -> str:
    """Return a required scope, refusing every route outside the contract."""
    if not path.startswith("/api/"):
        msg = "Wizard token cannot access this route"
        raise PermissionDenied(msg)
    relative = path.removeprefix("/api/")
    for allowed, pattern, scope in RULES:
        if method == allowed and re.fullmatch(pattern, relative):
            return scope
    msg = "Wizard token cannot access this route"
    raise PermissionDenied(msg)
