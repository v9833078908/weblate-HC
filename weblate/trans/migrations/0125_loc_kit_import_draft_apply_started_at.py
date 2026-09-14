# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0124_loc_kit_full_table_update"),
    ]

    operations = [
        migrations.AddField(
            model_name="lockitimportdraft",
            name="apply_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
