# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared constants and checks for the LLM judge review workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.machinery.models import MACHINERY
from weblate.trans.forms import AutoForm

if TYPE_CHECKING:
    from weblate.trans.models.project import Project
    from weblate.trans.models.translation import Translation

TARGET_PROJECT_SLUGS = (
    "col4",
    "pirate-ships",
    "heart-abyss",
    "strategy-and-tactics-2",
    "korotkij-test",
    "need-for-greed",
    "space-arena",
    "victory-banner",
)


def repair_route(translation: Translation) -> str:
    """Validate and return the configured repair model for a translation."""
    project: Project = translation.component.project
    engine_id = AutoForm.DEFAULT_ENGINE
    try:
        machinery = MACHINERY[engine_id]
    except KeyError as error:
        msg = f"Repair engine {engine_id!r} is not registered."
        raise CommandError(msg) from error
    setting = project.get_machinery_settings().get(engine_id)
    if not setting or not setting.get("key"):
        msg = f"Project {project.slug!r} has no usable {engine_id!r} repair key."
        raise CommandError(msg)
    engine = machinery(setting)
    resolver = getattr(engine, "resolve_model", None)
    if not callable(resolver):
        msg = f"Repair engine {engine_id!r} does not resolve language routes."
        raise CommandError(msg)
    model = resolver(translation.language.code)
    if not model:
        msg = (
            f"Project {project.slug!r} has no repair route for "
            f"{translation.language.code!r}."
        )
        raise CommandError(msg)
    return str(model)
