# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy


class RepeatPolicy(models.Model):
    """An explicit, project-local scope in which exact repeats may be shared."""

    project = models.ForeignKey(
        "trans.Project", on_delete=models.CASCADE, related_name="repeat_policies"
    )
    source_language = models.ForeignKey(
        "lang.Language",
        on_delete=models.PROTECT,
        related_name="repeat_source_policies",
    )
    target_language = models.ForeignKey(
        "lang.Language",
        on_delete=models.PROTECT,
        related_name="repeat_target_policies",
    )
    components = models.ManyToManyField(
        "trans.Component", related_name="repeat_policies"
    )
    source_labels = models.ManyToManyField(
        "trans.Label", blank=True, related_name="repeat_policies"
    )
    enabled = models.BooleanField(default=True)
    revision = models.PositiveBigIntegerField(default=1)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repeat_policies",
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.CheckConstraint(
                condition=~Q(source_language=F("target_language")),
                name="repeat_policy_languages_differ",
            )
        ]

    def __str__(self) -> str:
        return f"Repeat policy {self.pk} for {self.project}"

    def clean(self) -> None:
        super().clean()
        if self.pk and not self.components.exists():
            raise ValidationError({"components": gettext_lazy("Select a component.")})
        if self.pk and self.components.exclude(project=self.project).exists():
            raise ValidationError(
                {"components": gettext_lazy("Components must belong to this project.")}
            )
        if self.pk and self.source_labels.exclude(project=self.project).exists():
            raise ValidationError(
                {"source_labels": gettext_lazy("Labels must belong to this project.")}
            )

    def bump_revision(self) -> None:
        self.revision = F("revision") + 1
        self.save(update_fields=["revision", "updated"])
        self.refresh_from_db(fields=["revision"])


class RepeatGroup(models.Model):
    """One exact source/plural shape inside a repeat policy."""

    policy = models.ForeignKey(
        RepeatPolicy, on_delete=models.CASCADE, related_name="groups"
    )
    source_forms = models.JSONField(default=list)
    source_hash = models.CharField(max_length=64)
    plural_number = models.PositiveSmallIntegerField()
    revision = models.PositiveBigIntegerField(default=1)
    shared_target = models.JSONField(default=list, blank=True)
    decision_origin = models.CharField(max_length=40, blank=True)
    decision_author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repeat_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["policy", "source_hash", "plural_number"],
                name="repeat_group_policy_source_plural_unique",
            )
        ]

    def __str__(self) -> str:
        return f"Repeat group {self.pk} for policy {self.policy_id}"

    @property
    def has_decision(self) -> bool:
        return bool(self.shared_target)


class RepeatMembership(models.Model):
    """A durable decision or independent exception for a live Unit identity."""

    class Mode(models.TextChoices):
        SHARED = "shared", gettext_lazy("Shared")
        INDEPENDENT = "independent", gettext_lazy("Independent")

    group = models.ForeignKey(
        RepeatGroup, on_delete=models.CASCADE, related_name="memberships"
    )
    unit = models.ForeignKey(
        "trans.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repeat_memberships",
    )
    translation_id_snapshot = models.PositiveBigIntegerField()
    id_hash_snapshot = models.BigIntegerField()
    source_forms_snapshot = models.JSONField(default=list)
    context_snapshot = models.TextField(default="")
    constraints_snapshot = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    reason = models.CharField(max_length=80, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    stale_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        constraints = [  # ruff: ignore[mutable-class-default]
            models.UniqueConstraint(
                fields=["group", "unit"], name="repeat_membership_group_unit_unique"
            )
        ]

    def __str__(self) -> str:
        return f"Repeat membership {self.pk} for group {self.group_id}"

    @property
    def is_stale(self) -> bool:
        return self.stale_at is not None or self.unit_id is None


class RepeatDecisionEvent(models.Model):
    """Append-only provenance for a previewed repeat decision and its undo."""

    class Action(models.TextChoices):
        APPLY = "apply", gettext_lazy("Apply repeat decision")
        INDEPENDENT = "independent", gettext_lazy("Keep repeats independent")
        UNDO = "undo", gettext_lazy("Undo repeat decision")

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    group = models.ForeignKey(
        RepeatGroup, on_delete=models.PROTECT, related_name="decision_events"
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="repeat_decision_events",
    )
    group_revision = models.PositiveBigIntegerField()
    policy_revision = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        ordering = ("-created",)

    def __str__(self) -> str:
        return f"Repeat decision event {self.token}"
