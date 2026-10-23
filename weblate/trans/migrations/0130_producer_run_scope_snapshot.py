# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0129_producer_run_durable_dispatch")]

    operations = [
        migrations.AddField(
            model_name="producerrun",
            name="scope_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="scope_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
