# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0149_merge_producer_quality_mt"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProducerLLMExchange",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("request_digest", models.CharField(max_length=64)),
                ("state", models.CharField(default="reserved", max_length=20)),
                ("owner", models.CharField(max_length=32)),
                ("lease_until", models.DateTimeField()),
                ("response_path", models.CharField(blank=True, max_length=200)),
                ("response_digest", models.CharField(blank=True, max_length=64)),
                ("response_id", models.CharField(blank=True, max_length=200)),
                ("usage_recorded", models.BooleanField(default=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="ProducerLLMWork",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("semantic_key", models.CharField(max_length=64, unique=True)),
                ("identity", models.CharField(max_length=500)),
                ("position", models.PositiveSmallIntegerField()),
                (
                    "exchange",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="work",
                        to="trans.producerllmexchange",
                    ),
                ),
            ],
        ),
    ]
