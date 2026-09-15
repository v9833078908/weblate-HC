# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0126_loc_kit_import_draft_apply_started_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_phase",
            field=models.CharField(
                blank=True,
                choices=[("prepare", "Prepare"), ("apply", "Apply")],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="dispatch_task_id",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
