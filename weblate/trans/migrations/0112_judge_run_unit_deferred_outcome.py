# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add the deferred-retry outcome bucket to JudgeRunUnit."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0111_judge_run_request_round_counter"),
    ]

    operations = [
        migrations.AlterField(
            model_name="judgerununit",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("passed", "Passed"),
                    ("minor", "Minor"),
                    ("major", "Major"),
                    ("critical", "Critical"),
                    ("unparsed", "Unparsed"),
                    ("deferred", "Deferred"),
                    ("skipped", "Skipped"),
                    ("stale-conflict", "Stale Conflict"),
                ],
                max_length=20,
            ),
        ),
    ]
