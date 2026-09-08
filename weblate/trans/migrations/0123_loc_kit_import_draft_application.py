# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0122_llm_usage_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="lockitimportdraft",
            name="apply_task_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="kind",
            field=models.CharField(
                choices=[("glossary", "Glossary"), ("string", "String component")],
                default="glossary",
                max_length=20,
            ),
        ),
    ]
