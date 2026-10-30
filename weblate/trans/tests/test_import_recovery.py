# Copyright © HeroCraft
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Full-scale source upload regression against a local repository."""

from __future__ import annotations

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import threading
import time
from copy import copy
from pathlib import Path

from django.db import connection
from django.db.models import F

from weblate.formats.auto import try_load
from weblate.formats.helpers import NamedBytesIO
from weblate.formats.ttkit import PoMonoFormat
from weblate.trans.models import Unit
from weblate.trans.tests.test_views import ViewTestCase

EXISTING = 9383
ADDED = 100
LANGUAGES = ("en", "cs", "de", "es", "fr", "it", "ja", "pt", "ru")


def make_po(count: int, language: str, *, with_notes: bool = False) -> bytes:
    header = 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
    note = f"#. {'n' * 150}\n" if with_notes else ""
    entries = (
        f'{note}msgid "key-{i}"\nmsgstr "{language} text {i}"\n\n' for i in range(count)
    )
    return (header + "".join(entries)).encode()


class FullSourceUploadTest(ViewTestCase):
    def create_component(self):
        git = shutil.which("git")
        assert git is not None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            subprocess.run(
                [git, "clone", self.git_repo_path, str(path)],
                check=True,
                capture_output=True,
            )
            po_dir = path / "po-mono"
            po_dir.mkdir(exist_ok=True)
            for existing in po_dir.glob("*.po"):
                existing.unlink()
            for language in LANGUAGES:
                (po_dir / f"{language}.po").write_bytes(make_po(1, language))
            subprocess.run([git, "add", "po-mono"], cwd=path, check=True)
            subprocess.run(
                [
                    git,
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.org",
                    "commit",
                    "-m",
                    "Test corpus",
                ],
                cwd=path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [git, "push", "origin", "HEAD:main"],
                cwd=path,
                check=True,
                capture_output=True,
            )
        return self.create_po_mono()

    def get_translation(self, language="en"):
        return super().get_translation(language)

    def setUp(self):
        super().setUp()
        translations = list(self.component.translation_set.select_related("language"))
        source = next(item for item in translations if item.language.code == "en")
        source_example = source.unit_set.get()
        source_units = []
        for index in range(1, EXISTING):
            unit = copy(source_example)
            unit.pk = None
            unit.context = f"key-{index}"
            unit.source = unit.target = f"en text {index}"
            unit.id_hash = self.component.file_format_cls.unit_class.calculate_id_hash(
                True, unit.source, unit.context
            )
            unit.position = index + 1
            unit.source_unit_id = None
            source_units.append(unit)
        Unit.objects.bulk_create(source_units, batch_size=1000)
        Unit.objects.filter(translation=source).update(source_unit_id=F("pk"))
        for translation in translations:
            filename = translation.get_filename()
            assert filename is not None
            Path(filename).write_bytes(make_po(EXISTING, translation.language.code))
            if translation.pk == source.pk:
                continue
            example = translation.unit_set.get()
            units = []
            for index, source_unit in enumerate(source_units, 1):
                unit = copy(example)
                unit.pk = None
                unit.source_unit = source_unit
                unit.context = source_unit.context
                unit.source = source_unit.source
                unit.target = f"{translation.language.code} text {index}"
                unit.id_hash = source_unit.id_hash
                unit.position = index + 1
                units.append(unit)
            Unit.objects.bulk_create(units, batch_size=1000)
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE trans_unit")
            cursor.execute("ANALYZE checks_check")
        self.translation = self.get_translation()

    def test_source_upload_and_repeat(self):
        translation = self.get_translation()
        before = {
            item.pk: list(
                item.unit_set.order_by("id").values_list("id", "context", "target")
            )
            for item in self.component.translation_set.all()
        }
        self.assertEqual(len(before), len(LANGUAGES))
        self.assertEqual(len(before[translation.pk]), EXISTING)
        request = self.get_request()
        payload = make_po(
            EXISTING + ADDED, "en", with_notes=bool(os.environ.get("FULL_IMPORT_LARGE"))
        )
        results = []
        for iteration in range(2):
            samples: list[int] = []
            stopped = threading.Event()

            def sample_memory(stopped, samples):
                while not stopped.is_set():
                    output = subprocess.check_output(
                        ["/bin/ps", "-o", "rss=", "-p", str(os.getpid())], text=True
                    )
                    samples.append(int(output.strip()))
                    stopped.wait(0.2)

            sampler = threading.Thread(target=sample_memory, args=(stopped, samples))
            profiling = bool(os.environ.get("PROFILE_IMPORT_MEMORY"))
            if profiling:
                sampler.start()
            started = time.monotonic()
            try:
                results.append(
                    translation.handle_upload(
                        request,
                        NamedBytesIO("upload.po", payload),
                        "",
                        method="add",
                    )
                )
            finally:
                elapsed = time.monotonic() - started
                stopped.set()
                if profiling:
                    sampler.join()
            if profiling:
                print(  # ruff: ignore[print]
                    f"FULL_IMPORT bytes={len(payload)} iteration={iteration + 1} seconds={elapsed:.3f} "
                    f"rss_start_kib={samples[0]} rss_peak_kib={max(samples)}"
                )
        self.assertEqual(results[0][2:], (ADDED, EXISTING + ADDED))
        self.assertEqual(results[1][2:], (0, EXISTING + ADDED))
        filename = translation.get_filename()
        assert filename is not None
        source_store = try_load(
            filename, Path(filename).read_bytes(), PoMonoFormat, None, is_template=True
        )
        self.assertEqual(len(source_store.content_units), EXISTING + ADDED)
        for target in self.component.translation_set.all():
            previous = before[target.pk]
            ids = [item[0] for item in previous]
            self.assertEqual(
                list(
                    target.unit_set.filter(id__in=ids)
                    .order_by("id")
                    .values_list("id", "context", "target")
                ),
                previous,
            )
            self.assertEqual(target.unit_set.count(), EXISTING + ADDED)
            self.assertEqual(
                target.unit_set.values("context").distinct().count(), EXISTING + ADDED
            )
            if target.pk != translation.pk:
                self.assertEqual(
                    target.unit_set.filter(source_unit__isnull=True).count(), 0
                )
                self.assertFalse(
                    target.unit_set.exclude(id_hash=F("source_unit__id_hash")).exists()
                )
                self.assertEqual(
                    target.unit_set.filter(
                        context__in=[
                            f"key-{i}" for i in range(EXISTING, EXISTING + ADDED)
                        ],
                        target="",
                    ).count(),
                    ADDED,
                )
