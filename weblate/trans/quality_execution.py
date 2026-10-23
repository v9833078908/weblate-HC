# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed execution policies and a content-write barrier for verification."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Literal

from django.core.exceptions import PermissionDenied
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404

QualityExecutionMode = Literal["repair", "verify_only"]
PROTECTED_FIELDS = frozenset(
    {
        "source",
        "target",
        "state",
        "original_state",
        "context",
        "flags",
        "extra_flags",
        "explanation",
        "translation",
        "translation_id",
        "source_unit",
        "source_unit_id",
        "note",
        "location",
        "id_hash",
        "previous_source",
        "automatically_translated",
    }
)
_verification_project: ContextVar[int | None] = ContextVar(
    "verification_project", default=None
)


class VerificationChangedError(RuntimeError):
    """Content changed independently; never undo it or publish a clean PASS."""


@dataclass(frozen=True)
class QualityExecutionPolicy:
    mode: QualityExecutionMode

    @classmethod
    def from_mode(cls, mode):
        if mode not in {"repair", "verify_only"}:
            msg = "Unknown quality execution mode"
            raise ValueError(msg)
        return cls(mode)

    @classmethod
    def for_run(cls, run):
        return cls.from_mode(
            (run.execution_options or {}).get("execution_mode", "repair")
            if run
            else "repair"
        )

    @property
    def verify_only(self):
        return self.mode == "verify_only"

    def options(self):
        return {
            "execution_mode": self.mode,
            "judge_pretranslate": False,
            "judge_proposal_only": True,
            "judge_mutating_repairs": False,
            "judge_candidate_severities": []
            if self.verify_only
            else ["critical", "major"],
            "overwrite_existing": False,
            **({"judge_skip_preparation": True} if self.verify_only else {}),
        }


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def protected_scope(units):
    from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
        compute_decision_revision,
    )

    return {
        str(unit.pk): {
            "revision": compute_decision_revision(unit),
            "source_revision": compute_decision_revision(unit.source_unit),
            "digest": _digest(
                {
                    name: getattr(unit, name)
                    for name in PROTECTED_FIELDS
                    if not name.endswith("_unit") and name != "translation"
                }
            ),
        }
        for unit in units
    }


def project_digest(project):
    from weblate.trans.judge_quality import (  # ruff: ignore[import-outside-top-level]
        glossary_quality_revision,
    )
    from weblate.trans.models import Unit  # ruff: ignore[import-outside-top-level]

    project.refresh_from_db()
    components = project.component_set.order_by("pk")
    config_fields = {
        "name",
        "slug",
        "source_language_id",
        "file_format",
        "is_glossary",
        "locked",
        "allow_translation_propagation",
        "check_flags",
    }
    components_config = [
        {
            name: getattr(component, name)
            for name in config_fields
            if hasattr(component, name)
        }
        for component in components
    ]
    glossary = (
        Unit.objects.filter(
            translation__component__project=project,
            translation__component__is_glossary=True,
        )
        .order_by("pk")
        .values(
            "pk",
            "source",
            "target",
            "state",
            "context",
            "flags",
            "extra_flags",
            "explanation",
        )
    )
    return _digest(
        {
            "name": project.name,
            "slug": project.slug,
            "machinery": project.machinery_settings,
            "review": project.translation_review,
            "components": components_config,
            "glossary": list(glossary),
            "glossary_audit": glossary_quality_revision(project),
        }
    )


def assert_protected_scope(run, *, unit_ids=None):
    if not QualityExecutionPolicy.for_run(run).verify_only:
        return
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        Project,
        Unit,
    )

    expected = run.execution_options.get("protected_scope")
    if not expected or set(expected) != {str(ident) for ident in run.scope_snapshot}:
        msg = "Verification has no immutable content baseline"
        raise PermissionDenied(msg)
    selected = set(run.scope_snapshot if unit_ids is None else unit_ids)
    if not selected or not selected <= set(run.scope_snapshot):
        msg_0 = "Verification scope cannot be widened"
        raise PermissionDenied(msg_0)
    units = list(Unit.objects.filter(pk__in=selected).select_related("source_unit"))
    if protected_scope(units) != {
        str(ident): expected[str(ident)] for ident in selected
    }:
        msg = "Verification scope changed; results are stale"
        raise VerificationChangedError(msg)
    project_ids = {unit.translation.component.project_id for unit in units}
    if len(project_ids) != 1:
        msg = "Verification requires one closed project"
        raise PermissionDenied(msg)
    project = Project.objects.get(pk=project_ids.pop())
    if project_digest(project) != run.execution_options.get("protected_project"):
        msg = "Verification project or glossary changed"
        raise VerificationChangedError(msg)


def assert_verification_admissible(run, *, unit_ids=None):
    """Re-read authorization and audit state before admitting evidence."""
    if run is None:
        return
    run.refresh_from_db()
    if not QualityExecutionPolicy.for_run(run).verify_only:
        return
    from weblate.auth.models import User  # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Unit  # ruff: ignore[import-outside-top-level]

    if run.status in {"cancel_requested", "cancelled", "failed"}:
        msg = "Verification was cancelled or failed"
        raise VerificationChangedError(msg)
    actor = User.objects.filter(pk=run.actor_id).first()
    units = list(
        Unit.objects.filter(
            pk__in=run.scope_snapshot if unit_ids is None else unit_ids
        ).select_related("translation__component__project")
    )
    if (
        not actor
        or not actor.is_active
        or not units
        or any(
            not actor.has_perm("translation.auto", unit.translation) for unit in units
        )
    ):
        msg = "Verification authorization changed"
        raise PermissionDenied(msg)
    assert_protected_scope(run, unit_ids=unit_ids)


def verification_evidence_allowed(verdict):
    """Historical raw/evidence survives, but stale evidence is never current."""
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        ProducerRun,
    )

    if (
        hasattr(verdict, "verification_mode")
        and verdict.verification_mode != "verify_only"
    ):
        return True
    run = ProducerRun.objects.filter(pk=verdict.run_id).first()
    if run is None or not QualityExecutionPolicy.for_run(run).verify_only:
        return True
    try:
        assert_verification_admissible(run, unit_ids=[verdict.unit_id])
    except (PermissionDenied, VerificationChangedError):
        return False
    return True


def annotate_verification_origin(queryset):
    """Keep native bulk readers at one query; inspect only verification runs."""
    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        ProducerRun,
    )

    return queryset.annotate(
        verification_mode=Subquery(
            ProducerRun.objects.filter(pk=OuterRef("run_id")).values(
                "execution_options__execution_mode"
            )[:1]
        )
    )


@contextmanager
def verification_context(run):
    if not QualityExecutionPolicy.for_run(run).verify_only:
        yield
        return
    assert_verification_admissible(run)
    from weblate.trans.models import Unit  # ruff: ignore[import-outside-top-level]

    project = Unit.objects.get(
        pk=run.scope_snapshot[0]
    ).translation.component.project_id
    token = _verification_project.set(project)
    try:
        yield
        assert_verification_admissible(run)
    finally:
        _verification_project.reset(token)


def require_mutation_allowed():
    if _verification_project.get() is not None:
        msg = "verify_only does not permit translation changes"
        raise PermissionDenied(msg)


def verification_session(function):
    @wraps(function)
    def execute(*args, **kwargs):
        with verification_context(kwargs.get("run")):
            return function(*args, **kwargs)

    return execute


def guard_unit_save(unit, update_fields):
    project_id = _verification_project.get()
    if project_id is None:
        return
    original_project = (
        type(unit)
        .objects.filter(pk=unit.pk)
        .values_list("translation__component__project_id", flat=True)
        .first()
    )
    if project_id not in {original_project, unit.translation.component.project_id}:
        return
    if unit.pk is None or unit._state.adding:  # ruff: ignore[private-member-access]
        msg = "verify_only does not create translation units"
        raise PermissionDenied(msg)
    relevant = set(
        PROTECTED_FIELDS
        if update_fields is None
        else PROTECTED_FIELDS.intersection(update_fields)
    )
    fields = {field.attname for field in unit._meta.concrete_fields}  # ruff: ignore[private-member-access]
    relevant = {unit._meta.get_field(name).attname for name in relevant}  # ruff: ignore[private-member-access]
    relevant = relevant.intersection(fields)
    if not relevant:
        return
    original = type(unit).objects.filter(pk=unit.pk).values(*relevant).get()
    if any(getattr(unit, name) != original[name] for name in relevant):
        msg = "verify_only does not modify translation content"
        raise PermissionDenied(msg)


def guard_unit_update(queryset, fields):
    project_id = _verification_project.get()
    if (
        project_id is not None
        and PROTECTED_FIELDS.intersection(fields)
        and queryset.filter(translation__component__project_id=project_id).exists()
    ):
        msg = "verify_only does not permit bulk content changes"
        raise PermissionDenied(msg)


def reject_verification_mutation(request, project):
    """Bind a write to its actor/project context before looking up candidates."""
    context = request.data.get("execution_context")
    if context is None:
        return

    from weblate.trans.models import (  # ruff: ignore[import-outside-top-level]
        ProducerRun,
    )

    run = get_object_or_404(
        ProducerRun,
        pk=context,
        actor=request.user,
        scope_type="project",
        scope_id=str(project.pk),
    )
    if QualityExecutionPolicy.for_run(run).verify_only:
        msg = "verify_only does not permit translation changes"
        raise PermissionDenied(msg)
