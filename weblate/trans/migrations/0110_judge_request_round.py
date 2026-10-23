# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Separate judge transport recovery from repair attempts."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0109_judge_request_attempt_and_deferral"),
    ]

    operations = [
        migrations.AddField(
            model_name="judgeverdict",
            name="request_round",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RemoveConstraint(
            model_name="judgeverdict",
            name="judge_one_vote_per_seat",
        ),
        migrations.AddConstraint(
            model_name="judgeverdict",
            constraint=models.UniqueConstraint(
                fields=("unit", "run_id", "attempt", "request_round", "seat"),
                name="judge_one_vote_per_seat_round",
            ),
        ),
    ]
