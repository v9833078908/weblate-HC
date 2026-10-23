# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0121_producer_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmusagelog",
            name="run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="usage_logs",
                to="trans.producerrun",
            ),
        ),
        migrations.AddIndex(
            model_name="llmusagelog",
            index=models.Index(
                fields=["run", "-created_at"], name="llm_usage_run_recent_idx"
            ),
        ),
    ]
