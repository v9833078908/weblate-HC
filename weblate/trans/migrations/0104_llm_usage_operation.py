# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0103_judge_target_storage_hash")]

    operations = [
        migrations.AddField(
            model_name="llmusagelog",
            name="operation",
            field=models.CharField(
                blank=True,
                choices=[("translation", "Translation"), ("judge", "Judge")],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="llmusagelog",
            name="unit_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
