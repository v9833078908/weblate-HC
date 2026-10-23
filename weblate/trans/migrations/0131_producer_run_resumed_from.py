# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0130_producer_run_scope_snapshot")]

    operations = [
        migrations.AddField(
            model_name="producerrun",
            name="resumed_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="resumptions",
                to="trans.producerrun",
            ),
        ),
    ]
