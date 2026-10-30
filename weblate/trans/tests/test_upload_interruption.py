# Copyright © HeroCraft
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recovery of a real source upload interrupted at transaction boundaries."""

from __future__ import annotations

import multiprocessing
import time
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.db import connections
from django.test import RequestFactory, TransactionTestCase

from weblate.auth.models import User
from weblate.formats.helpers import NamedBytesIO
from weblate.trans.models import Change, PendingUnitChange, Translation, Unit
from weblate.trans.models.change import ActionEvents
from weblate.trans.tests.test_import_recovery import make_po
from weblate.trans.tests.utils import RepoTestMixin, create_test_user, get_optional_path

UPLOAD = make_po(5, "en")


def request_for(user):
    request = RequestFactory().get("/")
    request.user = user
    request.session = SessionStore()
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]  # ruff: ignore[private-member-access]
    return request


def interrupted_upload(translation_id, user_id, checkpoint, ready) -> None:
    connections.close_all()
    translation = Translation.objects.get(pk=translation_id)
    user = User.objects.get(pk=user_id)
    original = Translation.add_unit
    count = 0

    def add_unit(instance, *args, **kwargs):
        nonlocal count
        if checkpoint == "before" and count == 0:
            ready.set()
            time.sleep(60)
        value = original(instance, *args, **kwargs)
        count += 1
        if checkpoint == "during" and count == 1:
            ready.set()
            time.sleep(60)
        return value

    with patch.object(Translation, "add_unit", add_unit):
        translation.handle_upload(
            request_for(user), NamedBytesIO("upload.po", UPLOAD), "", method="add"
        )
    if checkpoint == "after":
        ready.set()
        time.sleep(60)


class InterruptedUploadTest(RepoTestMixin, TransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.clone_test_repos()
        self.user = create_test_user()
        self.component = self.create_po_mono()
        self.translation = self.component.source_translation

    def snapshot(self):
        return (
            list(
                Unit.objects.filter(translation__component=self.component)
                .order_by("pk")
                .values_list("pk", "translation_id", "context", "source", "target")
            ),
            list(
                Change.objects.filter(component=self.component)
                .order_by("pk")
                .values_list("pk", flat=True)
            ),
            list(
                PendingUnitChange.objects.filter(
                    unit__translation__component=self.component
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            ),
        )

    def check_interruption(self, checkpoint) -> None:
        before = self.snapshot()
        upload_count = Change.objects.filter(
            component=self.component, action=ActionEvents.FILE_UPLOAD
        ).count()
        translation_ids = list(
            self.component.translation_set.values_list("pk", flat=True)
        )
        translation_paths = [
            get_optional_path(item.get_filename())
            for item in self.component.translation_set.all()
        ]
        original_files = {path: path.read_bytes() for path in translation_paths}
        connections.close_all()
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        process = context.Process(
            target=interrupted_upload,
            args=(self.translation.pk, self.user.pk, checkpoint, ready),
        )
        process.start()
        try:
            self.assertTrue(
                ready.wait(45),
                f"checkpoint {checkpoint} not reached; exit={process.exitcode}",
            )
            started = time.monotonic()
            process.kill()
            process.join(10)
            self.assertFalse(process.is_alive())
            self.assertEqual(
                {path: path.read_bytes() for path in translation_paths}, original_files
            )
            after = self.snapshot()
            if checkpoint != "after":
                self.assertEqual(after, before)
            else:
                self.assertGreater(len(after[0]), len(before[0]))
                self.assertEqual(
                    Change.objects.filter(
                        component=self.component, action=ActionEvents.FILE_UPLOAD
                    ).count(),
                    upload_count + 1,
                )
                self.assertGreater(len(after[2]), len(before[2]))
            result = self.translation.handle_upload(
                request_for(self.user),
                NamedBytesIO("upload.po", UPLOAD),
                "",
                method="add",
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 300)
            self.assertEqual(result[2], 0 if checkpoint == "after" else 5)
            self.assertEqual(result[3], 5)
            self.assertEqual(
                Change.objects.filter(
                    component=self.component, action=ActionEvents.FILE_UPLOAD
                ).count(),
                upload_count + (2 if checkpoint == "after" else 1),
            )
            final_units = self.snapshot()[0]
            if checkpoint == "after":
                self.assertEqual(final_units, after[0])
            self.assertEqual(
                len(final_units), len(before[0]) + 5 * len(translation_ids)
            )
            self.assertTrue(set(before[0]).issubset(final_units))
            for target_id in translation_ids:
                added = Unit.objects.filter(
                    translation_id=target_id, context__startswith="key-"
                )
                self.assertEqual(added.count(), 5)
                self.assertEqual(added.values("context").distinct().count(), 5)
                if target_id != self.translation.pk:
                    self.assertEqual(added.filter(source_unit__isnull=True).count(), 0)
                    self.assertEqual(added.exclude(target="").count(), 0)
            source = Unit.objects.filter(
                translation=self.translation, context__startswith="key-"
            )
            self.assertEqual(source.count(), 5)
            self.assertEqual(source.values("context").distinct().count(), 5)
            self.assertEqual(
                list(source.order_by("context").values_list("target", flat=True)),
                [f"en text {index}" for index in range(5)],
            )
        finally:
            if process.is_alive():
                process.kill()
                process.join(10)

    def test_before_writes(self) -> None:
        self.check_interruption("before")

    def test_during_writes(self) -> None:
        self.check_interruption("during")

    def test_after_commit(self) -> None:
        self.check_interruption("after")
