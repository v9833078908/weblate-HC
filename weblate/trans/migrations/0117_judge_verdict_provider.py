# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0116_judge_deferral_closed_retention"),
    ]

    operations = [
        migrations.AddField(
            model_name="judgeverdict",
            name="judge_provider",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
