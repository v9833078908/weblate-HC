# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the temporary LocKitImportDraft model and its cleanup task.

Run via: ./rundev.sh test weblate/trans/tests/test_loc_kit_drafts.py
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from weblate.trans.models import LocKitImportDraft
from weblate.trans.models.loc_kit import LOC_KIT_DRAFT_STORAGE
from weblate.trans.tasks import cleanup_loc_kit_drafts
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.tests.utils import create_another_user


def make_draft(
    owner,
    project,
    *,
    session_key="test-session",
    state=LocKitImportDraft.State.UPLOADED,
    expires_at=None,
    source_filename="terms.csv",
) -> LocKitImportDraft:
    return LocKitImportDraft.objects.create(
        owner=owner,
        session_key=session_key,
        project=project,
        category=project.category_set.first(),
        slug="glossary",
        name="Glossary",
        source_filename=source_filename,
        uploaded=SimpleUploadedFile(
            source_filename, b"col1,col2\na,b\n", content_type="text/csv"
        ),
        state=state,
        expires_at=expires_at,
    )


class LocKitDraftModelTest(ViewTestCase):
    """Owner/session/expiry isolation and lifecycle for LocKitImportDraft."""

    def test_owner_isolation(self) -> None:
        """get_active returns None for a token owned by another user."""
        other = create_another_user(suffix="-loc-kit")
        draft = make_draft(owner=self.user, project=self.project)
        result = LocKitImportDraft.get_active(
            token=draft.token,
            owner=other,
            session_key="test-session",
        )
        self.assertIsNone(result)

    def test_session_isolation(self) -> None:
        """get_active returns None for a mismatched session_key on the same owner."""
        draft = make_draft(owner=self.user, project=self.project)
        result = LocKitImportDraft.get_active(
            token=draft.token,
            owner=self.user,
            session_key="different-session",
        )
        self.assertIsNone(result)

    # NOTE: project-level component-creation permission revocation is intentionally
    # not tested here -- it is enforced at the view layer in Task D2, not by this
    # model. Enforcing it here would duplicate the view's authorization and couple
    # the model to the permission system.

    def test_expiry(self) -> None:
        """get_active returns None once expires_at has passed."""
        draft = make_draft(
            owner=self.user,
            project=self.project,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertTrue(draft.is_expired)
        self.assertIsNone(
            LocKitImportDraft.get_active(
                token=draft.token,
                owner=self.user,
                session_key="test-session",
            )
        )

    def test_active_draft_returns(self) -> None:
        """get_active returns the draft for the correct owner and session."""
        draft = make_draft(owner=self.user, project=self.project)
        result = LocKitImportDraft.get_active(
            token=draft.token,
            owner=self.user,
            session_key="test-session",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, draft.pk)

    def test_unknown_token_returns_none(self) -> None:
        """A nonexistent token behaves identically to a mismatched one."""
        self.assertIsNone(
            LocKitImportDraft.get_active(
                token=uuid.uuid4(),
                owner=self.user,
                session_key="test-session",
            )
        )

    def test_cancellation_removes_file_and_row(self) -> None:
        """delete_storage + delete removes both the file and the row."""
        draft = make_draft(owner=self.user, project=self.project)
        name = draft.uploaded.name
        self.assertTrue(LOC_KIT_DRAFT_STORAGE.exists(name))
        draft.delete_storage()
        draft.delete()
        self.assertFalse(LOC_KIT_DRAFT_STORAGE.exists(name))
        self.assertFalse(LocKitImportDraft.objects.filter(pk=draft.pk).exists())

    def test_delete_storage_idempotent(self) -> None:
        """delete_storage is safe to call more than once."""
        draft = make_draft(owner=self.user, project=self.project)
        draft.delete_storage()
        # Second call must not raise even though the file is already gone.
        draft.delete_storage()

    def test_consumed_draft_unavailable(self) -> None:
        """A CONSUMED draft is absent from get_active regardless of expiry."""
        draft = make_draft(
            owner=self.user,
            project=self.project,
            state=LocKitImportDraft.State.CONSUMED,
        )
        self.assertIsNone(
            LocKitImportDraft.get_active(
                token=draft.token,
                owner=self.user,
                session_key="test-session",
            )
        )

    def test_storage_deletion_on_row_delete(self) -> None:
        """The uploaded file is physically removed from LOC_KIT_DRAFT_STORAGE."""
        draft = make_draft(owner=self.user, project=self.project)
        name = draft.uploaded.name
        self.assertTrue(LOC_KIT_DRAFT_STORAGE.exists(name))
        draft.delete_storage()
        self.assertFalse(LOC_KIT_DRAFT_STORAGE.exists(name))

    def test_expires_at_auto_set_on_save(self) -> None:
        """save() populates expires_at = now + clamped expiry when unset."""
        before = timezone.now()
        draft = make_draft(owner=self.user, project=self.project)
        after = timezone.now()
        self.assertIsNotNone(draft.expires_at)
        # Default cap is 3600 seconds.
        lower = before + timedelta(seconds=3599)
        upper = after + timedelta(seconds=3600)
        self.assertGreaterEqual(draft.expires_at, lower)
        self.assertLessEqual(draft.expires_at, upper)

    @override_settings(LOC_KIT_IMPORT_DRAFT_EXPIRY=60)
    def test_expires_at_respects_setting(self) -> None:
        """The configured expiry is used to compute expires_at."""
        before = timezone.now()
        draft = make_draft(owner=self.user, project=self.project)
        after = timezone.now()
        lower = before + timedelta(seconds=59)
        upper = after + timedelta(seconds=60)
        self.assertGreaterEqual(draft.expires_at, lower)
        self.assertLessEqual(draft.expires_at, upper)

    @override_settings(LOC_KIT_IMPORT_DRAFT_EXPIRY=99999)
    def test_expires_at_clamped_to_cap(self) -> None:
        """An oversized setting is clamped down to the 3600-second cap."""
        draft = make_draft(owner=self.user, project=self.project)
        delta = draft.expires_at - draft.created_at
        self.assertLessEqual(delta.total_seconds(), 3600)

    def test_state_transitions(self) -> None:
        """All four State values are distinct and round-trip through the DB."""
        draft = make_draft(owner=self.user, project=self.project)
        self.assertEqual(draft.state, LocKitImportDraft.State.UPLOADED)
        for state in LocKitImportDraft.State:
            draft.state = state
            draft.save(update_fields=["state"])
            draft.refresh_from_db()
            self.assertEqual(draft.state, state)

    def test_token_is_unique_and_uuid(self) -> None:
        """Two drafts get distinct unguessable UUID tokens."""
        d1 = make_draft(owner=self.user, project=self.project)
        d2 = make_draft(owner=self.user, project=self.project)
        self.assertNotEqual(d1.token, d2.token)


class LocKitDraftCleanupTest(ViewTestCase):
    """The 15-minute Celery cleanup task is idempotent and thorough."""

    def test_cleanup_deletes_expired(self) -> None:
        """Expired rows and their files are removed."""
        expired = make_draft(
            owner=self.user,
            project=self.project,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        name = expired.uploaded.name
        self.assertTrue(LOC_KIT_DRAFT_STORAGE.exists(name))
        cleanup_loc_kit_drafts()
        self.assertFalse(LocKitImportDraft.objects.filter(pk=expired.pk).exists())
        self.assertFalse(LOC_KIT_DRAFT_STORAGE.exists(name))

    def test_cleanup_keeps_active(self) -> None:
        """Non-expired drafts survive cleanup."""
        active = make_draft(
            owner=self.user,
            project=self.project,
            expires_at=timezone.now() + timedelta(seconds=3600),
        )
        cleanup_loc_kit_drafts()
        self.assertTrue(LocKitImportDraft.objects.filter(pk=active.pk).exists())

    def test_cleanup_idempotent(self) -> None:
        """Running cleanup twice in a row does not raise and leaves zero expired rows."""
        make_draft(
            owner=self.user,
            project=self.project,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        cleanup_loc_kit_drafts()
        cleanup_loc_kit_drafts()
        self.assertFalse(
            LocKitImportDraft.objects.filter(expires_at__lt=timezone.now()).exists()
        )

    def test_cleanup_no_op_when_empty(self) -> None:
        """Cleanup on an empty table is a harmless no-op."""
        cleanup_loc_kit_drafts()
        self.assertEqual(LocKitImportDraft.objects.count(), 0)

    def test_cleanup_deletes_file_for_already_missing_storage(self) -> None:
        """Cleanup does not raise if the file was already deleted manually."""
        expired = make_draft(
            owner=self.user,
            project=self.project,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        expired.delete_storage()
        # The file is gone but the row remains; cleanup must still remove the row.
        cleanup_loc_kit_drafts()
        self.assertFalse(LocKitImportDraft.objects.filter(pk=expired.pk).exists())
