# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy

from weblate.trans.defines import COMPONENT_NAME_LENGTH, FILENAME_LENGTH
from weblate.utils.data import data_dir

LOC_KIT_DRAFT_STORAGE = FileSystemStorage(location=data_dir("loc_kit_drafts"))

# Draft files live at most one hour. ``LOC_KIT_IMPORT_DRAFT_EXPIRY`` is the
# site-wide setting wired by Task C1; a fallback keeps this model importable
# and testable before that configuration is present.
LOC_KIT_DRAFT_EXPIRY_CAP = 3600

# A single Celery delivery never runs the whole apply: each delivery stops
# at a portion boundary once its own share of the broker's visibility
# timeout is spent, reserves a fresh ``apply_task_id`` under the draft's row
# lock, and publishes its own continuation after commit
# (``weblate.trans.tasks._chain_loc_kit_apply_continuation``). This absolute
# limit is the cumulative wall-clock budget across every one of those
# continuations, measured from the very first confirm
# (``apply_started_at``, set once, never touched by a continuation - a
# retryable failure would just re-measure against the same fixed instant
# and fail again immediately). Exhausting it stops the draft as a terminal
# FAILED with an explicit partial report (rows applied so far) and no
# ``retry_phase`` - the existing Retry action is a resume-in-place contract
# this cumulative cap cannot honor, so it is deliberately absent here, not
# reused with different semantics. The draft is still viewable for the
# normal one-hour FAILED retention; continuing means uploading again.
LOC_KIT_STRING_UPDATE_APPLY_TIME_LIMIT = timedelta(hours=24)


class LocKitImportDraft(models.Model):
    """
    Session- and owner-bound temporary glossary upload draft.

    Holds an uploaded CSV/TSV/XLSX workbook and the validated profile/preview
    metadata produced by the glossary analysis workflow. Drafts are explicitly
    short lived: every lookup path requires the same authenticated owner and
    session binding, and expired or consumed drafts behave as absent.
    """

    class Kind(models.TextChoices):
        GLOSSARY = "glossary", gettext_lazy("Glossary")
        STRING = "string", gettext_lazy("String component")

    class State(models.TextChoices):
        UPLOADED = "uploaded", gettext_lazy("Uploaded")
        PREPARING = "preparing", gettext_lazy("Preparing")
        SHEET_SELECTED = "sheet-selected", gettext_lazy("Sheet selected")
        PREVIEW_READY = "preview-ready", gettext_lazy("Preview ready")
        APPLYING = "applying", gettext_lazy("Applying")
        COMPLETED = "completed", gettext_lazy("Completed")
        FAILED = "failed", gettext_lazy("Failed")
        CONSUMED = "consumed", gettext_lazy("Consumed")

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.GLOSSARY)
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
    target_component = models.ForeignKey(
        "trans.Component",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="loc_kit_update_drafts",
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
    apply_task_id = models.UUIDField(null=True, blank=True)
    prepare_task_id = models.UUIDField(null=True, blank=True)
    confirmed_options = models.JSONField(default=dict, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    next_row = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True)
    error_details = models.TextField(blank=True)
    retry_phase = models.CharField(max_length=20, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    apply_started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    prepared_payload = models.FileField(
        storage=LOC_KIT_DRAFT_STORAGE,
        upload_to="prepared/%Y/%m/%d/",
        max_length=FILENAME_LENGTH,
        blank=True,
    )
    payload_checksum = models.CharField(max_length=64, blank=True)
    payload_size = models.PositiveBigIntegerField(default=0)
    pending_change_ids = models.JSONField(default=list, blank=True)
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
        """Delete all private draft files; safe to call more than once."""
        for stored in (self.uploaded, self.prepared_payload):
            if stored and stored.name:
                stored.storage.delete(stored.name)

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
        except (cls.DoesNotExist, ValidationError, ValueError):
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


@receiver(post_delete, sender=LocKitImportDraft)
def _delete_draft_storage(sender, instance: LocKitImportDraft, **kwargs) -> None:
    """
    Reclaim the uploaded file however the row went away.

    Explicit cancel and the cleanup task call delete_storage themselves, but a
    cascade from the owner, project or category issues a bulk delete that
    never touches the model. Because cleanup is row-driven, such a file would
    otherwise outlive the one-hour lifetime the UI promises, forever.
    """
    instance.delete_storage()
