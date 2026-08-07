# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy

from weblate.trans.defines import COMPONENT_NAME_LENGTH, FILENAME_LENGTH
from weblate.utils.data import data_dir

LOC_KIT_DRAFT_STORAGE = FileSystemStorage(location=data_dir("loc_kit_drafts"))

# Draft files live at most one hour. ``LOC_KIT_IMPORT_DRAFT_EXPIRY`` is the
# site-wide setting wired by Task C1; a fallback keeps this model importable
# and testable before that configuration is present.
LOC_KIT_DRAFT_EXPIRY_CAP = 3600


class LocKitImportDraft(models.Model):
    """
    Session- and owner-bound temporary glossary upload draft.

    Holds an uploaded CSV/TSV/XLSX workbook and the validated profile/preview
    metadata produced by the glossary analysis workflow. Drafts are explicitly
    short lived: every lookup path requires the same authenticated owner and
    session binding, and expired or consumed drafts behave as absent.
    """

    class State(models.TextChoices):
        UPLOADED = "uploaded", gettext_lazy("Uploaded")
        SHEET_SELECTED = "sheet-selected", gettext_lazy("Sheet selected")
        PREVIEW_READY = "preview-ready", gettext_lazy("Preview ready")
        CONSUMED = "consumed", gettext_lazy("Consumed")

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loc_kit_import_drafts",
    )
    session_key = models.CharField(max_length=40)
    project = models.ForeignKey(
        "trans.Project", on_delete=models.CASCADE, related_name="loc_kit_import_drafts"
    )
    category = models.ForeignKey(
        "trans.Category",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="loc_kit_import_drafts",
    )
    slug = models.SlugField(max_length=COMPONENT_NAME_LENGTH)
    name = models.CharField(max_length=COMPONENT_NAME_LENGTH)
    source_filename = models.CharField(max_length=FILENAME_LENGTH)
    uploaded = models.FileField(
        storage=LOC_KIT_DRAFT_STORAGE,
        upload_to="drafts/%Y/%m/%d/",
        max_length=FILENAME_LENGTH,
    )
    sheet = models.CharField(max_length=FILENAME_LENGTH, blank=True)
    profile_json = models.TextField(blank=True)
    preview_json = models.TextField(blank=True)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.UPLOADED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        app_label = "trans"
        required_db_vendor = "postgresql"
        verbose_name = "loc-kit import draft"
        verbose_name_plural = "loc-kit import drafts"

    def __str__(self) -> str:
        return f"LocKitImportDraft({self.token})"

    def save(self, *args, **kwargs) -> None:
        if not self.expires_at:
            expiry = int(
                getattr(
                    settings, "LOC_KIT_IMPORT_DRAFT_EXPIRY", LOC_KIT_DRAFT_EXPIRY_CAP
                )
            )
            self.expires_at = timezone.now() + timedelta(
                seconds=min(expiry, LOC_KIT_DRAFT_EXPIRY_CAP)
            )
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def delete_storage(self) -> None:
        """Delete the uploaded file from storage; safe to call more than once."""
        if self.uploaded and self.uploaded.name:
            self.uploaded.storage.delete(self.uploaded.name)

    @classmethod
    def get_active(cls, *, token, owner, session_key):
        """
        Return the draft for ``token`` if it is available to this caller.

        Available means it belongs to ``owner``, was created under
        ``session_key``, is not expired, and is not yet consumed.

        Returns ``None`` for every other case (wrong owner, wrong session,
        expired, consumed, or nonexistent token). Callers must treat all of
        these identically as "draft not available", never leaking which reason
        applied -- that is the whole point of the owner/session binding.

        Project-level component-creation permission is intentionally NOT
        checked here; it is enforced at the view layer in Task D2.
        """
        try:
            draft = cls.objects.get(token=token)
        except (cls.DoesNotExist, ValueError):
            return None
        if draft.owner_id != owner.id:
            return None
        if draft.session_key != session_key:
            return None
        if draft.state == cls.State.CONSUMED:
            return None
        if draft.is_expired:
            return None
        return draft
