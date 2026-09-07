# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0120_judge_run_unit_repair_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Model name, reverse accessor, table name, display options and the
        # two new application-level scope choices are state only. No existing
        # row, table, column, index, or FK moves.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel(old_name="JudgeRun", new_name="ProducerRun"),
                migrations.AlterModelTable(
                    name="producerrun", table="trans_judgerun"
                ),
                migrations.AlterModelOptions(
                    name="producerrun",
                    options={
                        "verbose_name": "Producer run",
                        "verbose_name_plural": "Producer runs",
                    },
                ),
                migrations.AlterField(
                    model_name="producerrun",
                    name="actor",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="producer_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AlterField(
                    model_name="producerrun",
                    name="scope_type",
                    field=models.CharField(
                        choices=[
                            ("translation", "Translation"),
                            ("component", "Component"),
                            ("category", "Category"),
                            ("project", "Project"),
                            ("project-language", "Project Language"),
                            ("workspace", "Workspace"),
                        ],
                        max_length=20,
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
