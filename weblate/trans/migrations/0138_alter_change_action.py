# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models

from weblate.trans.actions import ActionEvents


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0137_producer_run_full_scope"),
    ]

    operations = [
        migrations.AlterField(
            model_name="change",
            name="action",
            field=models.IntegerField(
                choices=ActionEvents.choices, default=ActionEvents.CHANGE
            ),
        ),
    ]
