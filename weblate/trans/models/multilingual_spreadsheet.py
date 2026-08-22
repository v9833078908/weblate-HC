# Copyright © 2026 Weblate contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Private draft storage for component multilingual spreadsheet imports."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy

from weblate.trans.defines import FILENAME_LENGTH
from weblate.utils.data import data_dir

COMPONENT_SPREADSHEET_DRAFT_STORAGE = FileSystemStorage(
    location=data_dir("component_spreadsheet_drafts")
)
COMPONENT_SPREADSHEET_DRAFT_EXPIRY = 3600


class ComponentSpreadsheetImportDraft(models.Model):
    """Owner- and session-bound staged import for one component."""

    class State(models.TextChoices):
        PREVIEW_READY = "preview-ready", gettext_lazy("Preview ready")
        CONSUMED = "consumed", gettext_lazy("Consumed")

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="component_spreadsheet_drafts",
    )
    session_key = models.CharField(max_length=40)
    component = models.ForeignKey(
        "trans.Component",
        on_delete=models.CASCADE,
        related_name="multilingual_spreadsheet_import_drafts",
    )
    source_filename = models.CharField(max_length=FILENAME_LENGTH)
    uploaded = models.FileField(
        storage=COMPONENT_SPREADSHEET_DRAFT_STORAGE,
        upload_to="",
        blank=True,
    )
    preview_json = models.TextField(blank=True)
    baseline_json = models.TextField(blank=True)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.PREVIEW_READY
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        app_label = "trans"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs) -> None:
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(
                seconds=COMPONENT_SPREADSHEET_DRAFT_EXPIRY
            )
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def delete_storage(self) -> None:
        if self.uploaded and self.uploaded.name:
            self.uploaded.storage.delete(self.uploaded.name)

    @classmethod
    def get_active(cls, *, token, owner, session_key):
        try:
            draft = cls.objects.get(
                token=token,
                owner=owner,
                session_key=session_key,
                state=cls.State.PREVIEW_READY,
            )
        except cls.DoesNotExist:
            return None
        return None if draft.is_expired else draft


@receiver(post_delete, sender=ComponentSpreadsheetImportDraft)
def _delete_draft_storage(
    sender, instance: ComponentSpreadsheetImportDraft, **kwargs
) -> None:
    instance.delete_storage()
