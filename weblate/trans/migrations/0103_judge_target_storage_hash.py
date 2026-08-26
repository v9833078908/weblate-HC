# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import json

from django.db import migrations, models


def populate_target_storage_hash(apps, schema_editor) -> None:
    Unit = apps.get_model("trans", "Unit")
    JudgeVerdict = apps.get_model("trans", "JudgeVerdict")

    for unit in (
        Unit.objects.filter(judge_verdicts__isnull=False)
        .distinct()
        .iterator(chunk_size=1000)
    ):
        target_hash = hashlib.sha256(
            json.dumps(
                unit.target.split("\x1e\x1e"), ensure_ascii=False, sort_keys=False
            ).encode()
        ).hexdigest()
        target_storage_hash = hashlib.md5(
            unit.target.encode(), usedforsecurity=False
        ).hexdigest()
        JudgeVerdict.objects.filter(unit_id=unit.pk, target_hash=target_hash).update(
            target_storage_hash=target_storage_hash
        )


class Migration(migrations.Migration):
    dependencies = [("trans", "0102_component_spreadsheet_import_draft")]

    operations = [
        migrations.AddField(
            model_name="judgeverdict",
            name="target_storage_hash",
            field=models.CharField(db_index=True, max_length=32, null=True),
        ),
        migrations.RunPython(populate_target_storage_hash, migrations.RunPython.noop),
    ]
