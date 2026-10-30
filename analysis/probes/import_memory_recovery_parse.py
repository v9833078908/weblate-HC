#!/usr/bin/env python3
# Copyright © 2026 Weblate contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Measure the source upload parser in a fresh process; synthetic data only.

Run from a checkout with DJANGO_SETTINGS_MODULE=weblate.settings_test and its
Python environment. Repeat in isolated processes for comparable peak RSS.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import sys
import time
import tracemalloc
from io import BytesIO
from unittest.mock import MagicMock

import django

django.setup()

from weblate.formats.ttkit import PoMonoFormat
from weblate.trans.models.translation import Translation


def fixture(count: int) -> bytes:
    records = ['msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n']
    records.extend(
        f"\n#. Character: synthetic speaker {i % 31}; preserve <b>markup</b>\n"
        f"#: synthetic/{i // 100}.txt:{i + 1}\n"
        + ("#, fuzzy\n" if i % 5 == 0 else "")
        + f'msgctxt "chapter.{i // 100}.line.{i}"\n'
        + f'msgid "line_{i:05}"\n'
        + (
            f'msgid_plural "line_{i:05}_plural"\nmsgstr[0] "Synthetic line {i} with {{0}}."\nmsgstr[1] "Synthetic lines {i} with {{0}}."\n'
            if i % 20 == 0
            else f'msgstr "Synthetic line {i} with {{0}} and <b>text</b>."\n'
        )
        for i in range(count)
    )
    return "".join(records).encode()


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 9483
    payload = fixture(count)
    handle = BytesIO(payload)
    handle.name = "synthetic.po"
    component = MagicMock()
    component.has_template.return_value = False
    component.file_format_cls = PoMonoFormat
    component.file_format_params = {}
    component.source_language.code = "en"
    translation = MagicMock()
    translation.component = component
    translation.is_template = True
    translation.language_code = "en"
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.environ.get("IMPORT_TRACE"):
        tracemalloc.start()
    started = time.monotonic()
    store = Translation.load_uploaded_file(translation, MagicMock(), handle, "add")
    digest = hashlib.sha256()
    for unit in store.content_units:
        digest.update(
            repr(
                (unit.context, unit.source, unit.target, unit.notes, unit.is_fuzzy())
            ).encode()
        )
    merged = sum(1 for _ in store.iterate_merge("process"))
    seconds = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    traced = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else None
    print(
        json.dumps(
            {
                "rows": count,
                "bytes": len(payload),
                "merged": merged,
                "seconds": round(seconds, 3),
                "peak_rss_raw": rss,
                "digest": digest.hexdigest(),
                "platform": sys.platform,
                "rss_before_raw": rss_before,
                "traced_current_peak_bytes": traced,
            }
        )
    )
    if merged != count:
        msg = "unexpected merged count"
        raise SystemExit(msg)


if __name__ == "__main__":
    main()
