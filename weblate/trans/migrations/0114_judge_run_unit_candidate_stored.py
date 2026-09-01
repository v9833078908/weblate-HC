# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0113_llm_usage_scope"),
    ]

    operations = [
        migrations.AlterField(
            model_name="judgerununit",
            name="repair_status",
            field=models.CharField(
                choices=[
                    ("not-attempted", "Not Attempted"),
                    ("no-candidate", "No Candidate"),
                    ("candidate-stored", "Candidate Stored"),
                    ("applied", "Applied"),
                    ("rolled-back", "Rolled Back"),
                ],
                default="not-attempted",
                max_length=20,
            ),
        ),
    ]
