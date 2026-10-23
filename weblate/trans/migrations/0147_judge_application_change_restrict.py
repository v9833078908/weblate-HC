# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0146_repeat_bulk_runs")]

    operations = [
        migrations.AlterField(
            model_name="judgeapplication",
            name="change",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="judge_application",
                to="trans.change",
            ),
        ),
    ]
