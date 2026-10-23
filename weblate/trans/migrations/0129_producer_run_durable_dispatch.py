# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("trans", "0128_judge_verdict_subject")]

    operations = [
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_phase",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="dispatch_task_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="producerrun",
            name="request_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="judgerununit",
            name="candidate_target_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="judgerununit",
            name="candidate_verdict_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="judgerununit",
            name="eligibility",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="judgerununit",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("passed", "Passed"),
                    ("minor", "Minor"),
                    ("major", "Major"),
                    ("critical", "Critical"),
                    ("unparsed", "Unparsed"),
                    ("deferred", "Deferred"),
                    ("refused", "Refused"),
                    ("skipped", "Skipped"),
                    ("stale-conflict", "Stale conflict"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="producerrun",
            constraint=models.UniqueConstraint(
                condition=~Q(idempotency_key=""),
                fields=(
                    "actor",
                    "scope_type",
                    "scope_id",
                    "requested_mode",
                    "idempotency_key",
                ),
                name="producer_run_idempotency_key",
            ),
        ),
    ]
