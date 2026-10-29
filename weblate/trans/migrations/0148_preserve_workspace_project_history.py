# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0147_producer_mt_operations"),
        ("workspaces", "0004_workspace_metric_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="change",
            name="workspace",
            field=models.ForeignKey(
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="workspaces.workspace",
            ),
        ),
    ]
