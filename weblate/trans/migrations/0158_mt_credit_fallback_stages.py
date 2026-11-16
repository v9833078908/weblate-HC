# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0157_routed_qwen_reasoning_codec"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProducerMTFallbackAuthorization",
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
                ("scope_digest", models.CharField(max_length=64)),
                ("scope_size", models.PositiveIntegerField()),
                (
                    "policy",
                    models.CharField(
                        default="openrouter-credit-to-litellm-qwen37-none-v1",
                        max_length=64,
                    ),
                ),
                ("primary_engine", models.CharField(max_length=50)),
                ("primary_configuration_digest", models.CharField(max_length=64)),
                ("fallback_engine", models.CharField(max_length=50)),
                ("fallback_configuration_digest", models.CharField(max_length=64)),
                ("fallback_model", models.CharField(max_length=100)),
                (
                    "fallback_profile",
                    models.CharField(
                        default="openrouter-credit-to-litellm-qwen37-none-v1",
                        max_length=64,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("observing", "Observing"),
                            ("authorized", "Authorized"),
                            ("closed", "Closed"),
                        ],
                        default="observing",
                        max_length=20,
                    ),
                ),
                ("authorized_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("actor_id_snapshot", models.IntegerField(blank=True, null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mt_fallback_authorizations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "operation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mt_fallbacks",
                        to="trans.producermtoperation",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mt_fallbacks",
                        to="trans.project",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mt_fallbacks",
                        to="trans.producerrun",
                    ),
                ),
                (
                    "translation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mt_fallbacks",
                        to="trans.translation",
                    ),
                ),
            ],
            options={
                "app_label": "trans",
            },
        ),
        migrations.CreateModel(
            name="ProducerMTFallbackUnit",
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
                ("identity", models.CharField(max_length=500)),
                ("semantic_key", models.CharField(max_length=64, unique=True)),
                ("unit_id_snapshot", models.IntegerField()),
                ("source_decision_revision", models.CharField(max_length=64)),
                ("language_code", models.CharField(max_length=32)),
                ("audit_revision", models.CharField(blank=True, max_length=64)),
                ("target_revision", models.CharField(blank=True, max_length=64)),
                ("source_slots", models.JSONField(default=list)),
                ("primary_dispositions", models.JSONField(default=list)),
                ("primary_answers", models.JSONField(default=list)),
                ("eligibility_reason", models.CharField(blank=True, max_length=64)),
                ("fallback_state", models.CharField(default="none", max_length=20)),
                ("fallback_answer", models.TextField(blank=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "authorization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fallback_units",
                        to="trans.producermtfallbackauthorization",
                    ),
                ),
                (
                    "fallback_exchange",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fallback_unit_slots",
                        to="trans.producerllmexchange",
                    ),
                ),
                (
                    "primary_work",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fallback_unit_slots",
                        to="trans.producerllmwork",
                    ),
                ),
                (
                    "unit",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="trans.unit",
                    ),
                ),
                (
                    "unit_attempt",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fallback_unit_slots",
                        to="trans.producermtunitattempt",
                    ),
                ),
            ],
            options={
                "app_label": "trans",
            },
        ),
        migrations.CreateModel(
            name="ProducerMTStageMember",
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
                (
                    "stage",
                    models.CharField(
                        choices=[("primary", "Primary"), ("fallback", "Fallback")],
                        max_length=20,
                    ),
                ),
                ("source_slot", models.PositiveSmallIntegerField()),
                ("position", models.PositiveSmallIntegerField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "exchange",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stage_members",
                        to="trans.producerllmexchange",
                    ),
                ),
                (
                    "unit_stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stage_members",
                        to="trans.producermtfallbackunit",
                    ),
                ),
            ],
            options={
                "app_label": "trans",
            },
        ),
        migrations.AddConstraint(
            model_name="producermtfallbackauthorization",
            constraint=models.UniqueConstraint(
                fields=("run", "translation"),
                name="mt_fallback_run_translation_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="producermtstagemember",
            constraint=models.UniqueConstraint(
                fields=("unit_stage", "stage", "source_slot"),
                name="mt_stage_member_slot_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="producermtstagemember",
            constraint=models.UniqueConstraint(
                fields=("exchange", "position"), name="mt_stage_member_position_unique"
            ),
        ),
        migrations.AddField(
            model_name="producerllmexchange",
            name="mt_authorization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="exchanges",
                to="trans.producermtfallbackauthorization",
            ),
        ),
        migrations.AddField(
            model_name="producerllmexchange",
            name="mt_stage",
            field=models.CharField(
                blank=True,
                choices=[("primary", "Primary"), ("fallback", "Fallback")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="producerllmexchange",
            name="request_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
