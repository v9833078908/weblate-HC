# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0153_merge_production_repair_history")]

    operations = [
        migrations.AddField(
            model_name="producerllmexchange",
            name="request_codec",
            field=models.CharField(
                max_length=32,
                blank=True,
                default="",
                choices=[
                    ("", "Unclassified"),
                    ("opaque-v1", "Opaque v1"),
                    ("routed-parts-v1", "Routed parts v1"),
                    ("routed-slots-v2", "Routed slots v2"),
                ],
            ),
        ),
    ]
