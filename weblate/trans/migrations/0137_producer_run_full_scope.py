# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0136_producer_run_preparation"),
    ]

    operations = [
        migrations.AddField(
            model_name="producerrun",
            name="execution_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="scope_cursor",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="execution_options",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
