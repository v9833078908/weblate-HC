# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0154_llm_exchange_request_codec"),
    ]

    operations = [
        migrations.AlterField(
            model_name="producerllmexchange",
            name="request_codec",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Unclassified"),
                    ("opaque-v1", "Opaque v1"),
                    ("routed-parts-v1", "Routed parts v1"),
                    ("routed-slots-v2", "Routed slots v2"),
                    ("routed-slots-v3", "Routed slots v3"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
