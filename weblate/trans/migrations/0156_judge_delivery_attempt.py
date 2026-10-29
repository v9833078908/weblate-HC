# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0155_routed_gemini_reasoning_codec"),
    ]

    operations = [
        migrations.AddField(
            model_name="producerllmexchange",
            name="judge_attempt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="delivery_exchange",
                to="trans.judgerequestattempt",
            ),
        ),
        migrations.AddField(
            model_name="judgerequestattempt",
            name="response_complete",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
