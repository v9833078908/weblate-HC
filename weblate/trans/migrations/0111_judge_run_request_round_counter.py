# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persist JudgeRun request-round reservations."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0110_judge_request_round"),
    ]

    operations = [
        migrations.AddField(
            model_name="judgerun",
            name="next_request_round",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
