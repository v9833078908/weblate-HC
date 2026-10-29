# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0150_llm_delivery_journal"),
    ]

    operations = [
        migrations.AddField(
            model_name="producermtunitattempt",
            name="source_revision",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="producermtoperation",
            name="configuration_digest",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RemoveConstraint(
            model_name="producermtunitattempt",
            name="producer_mt_unit_revision_unique",
        ),
        migrations.AddField(
            model_name="producermtunitattempt",
            name="audit_revision",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="producermtunitattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state", "released"), _negated=True),
                fields=("unit", "revision"),
                name="producer_mt_unit_revision_unique",
            ),
        ),
    ]
