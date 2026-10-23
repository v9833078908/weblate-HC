# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations, models

import weblate.trans.models.loc_kit


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0124_llm_usage_refusal_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="lockitimportdraft",
            name="confirmed_options",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="error_code",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="error_details",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="last_activity_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="next_row",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="payload_checksum",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="payload_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="pending_change_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="prepare_task_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="prepared_payload",
            field=models.FileField(
                blank=True,
                max_length=255,
                storage=weblate.trans.models.loc_kit.LOC_KIT_DRAFT_STORAGE,
                upload_to="prepared/%Y/%m/%d/",
            ),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="progress",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="lockitimportdraft",
            name="retry_phase",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AlterField(
            model_name="lockitimportdraft",
            name="state",
            field=models.CharField(
                choices=[
                    ("uploaded", "Uploaded"),
                    ("preparing", "Preparing"),
                    ("sheet-selected", "Sheet selected"),
                    ("preview-ready", "Preview ready"),
                    ("applying", "Applying"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("consumed", "Consumed"),
                ],
                default="uploaded",
                max_length=20,
            ),
        ),
    ]
