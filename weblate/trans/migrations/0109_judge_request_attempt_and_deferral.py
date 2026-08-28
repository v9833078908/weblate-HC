# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistence foundation for LiteLLM Judge attempts and deferrals."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0108_judge_verdict_instruction"),
    ]

    operations = [
        migrations.CreateModel(
            name="JudgeAdaptiveState",
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
                ("endpoint_fingerprint", models.CharField(max_length=64)),
                ("model", models.CharField(max_length=200)),
                ("seat", models.PositiveSmallIntegerField()),
                ("batch_budget", models.PositiveSmallIntegerField()),
                ("clean_attempt_streak", models.PositiveSmallIntegerField(default=0)),
                ("failure_streak", models.PositiveSmallIntegerField(default=0)),
                (
                    "last_failure_kind",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("transport", "Transport"),
                            ("deadline", "Deadline"),
                            ("response-too-large", "Response Too Large"),
                            ("http-auth", "Http Auth"),
                            ("http-rate-limit", "Http Rate Limit"),
                            ("http-server", "Http Server"),
                            ("http-other", "Http Other"),
                            ("empty-response", "Empty Response"),
                            ("invalid-json", "Invalid Json"),
                            ("invalid-envelope", "Invalid Envelope"),
                            ("segment-count", "Segment Count"),
                            ("invalid-segment", "Invalid Segment"),
                            ("finish-length", "Finish Length"),
                            ("unknown", "Unknown"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "circuit_state",
                    models.CharField(
                        choices=[
                            ("closed", "Closed"),
                            ("open", "Open"),
                            ("half-open", "Half Open"),
                            ("operator-stopped", "Operator Stopped"),
                        ],
                        default="closed",
                        max_length=20,
                    ),
                ),
                ("circuit_opened_at", models.DateTimeField(blank=True, null=True)),
                ("circuit_open_until", models.DateTimeField(blank=True, null=True)),
                ("token_bucket_capacity", models.PositiveIntegerField(default=0)),
                (
                    "token_bucket_available",
                    models.DecimalField(decimal_places=6, default=0, max_digits=18),
                ),
                (
                    "token_bucket_refill_per_second",
                    models.DecimalField(decimal_places=6, default=0, max_digits=18),
                ),
                (
                    "token_bucket_updated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Judge adaptive state",
                "verbose_name_plural": "Judge adaptive states",
            },
        ),
        migrations.CreateModel(
            name="JudgeRequestAttempt",
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
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("seat", models.PositiveSmallIntegerField()),
                ("attempt", models.PositiveSmallIntegerField(default=0)),
                ("provider", models.CharField(blank=True, max_length=32)),
                ("endpoint_fingerprint", models.CharField(max_length=64)),
                ("model", models.CharField(max_length=200)),
                ("model_fingerprint", models.CharField(blank=True, max_length=64)),
                ("profile_fingerprint", models.CharField(max_length=64)),
                ("prompt_schema_version", models.CharField(max_length=64)),
                ("batch_digest", models.CharField(max_length=64)),
                ("batch_size", models.PositiveSmallIntegerField()),
                ("transport_succeeded", models.BooleanField(default=False)),
                ("parsed", models.BooleanField(default=False)),
                (
                    "failure_kind",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("transport", "Transport"),
                            ("deadline", "Deadline"),
                            ("response-too-large", "Response Too Large"),
                            ("http-auth", "Http Auth"),
                            ("http-rate-limit", "Http Rate Limit"),
                            ("http-server", "Http Server"),
                            ("http-other", "Http Other"),
                            ("empty-response", "Empty Response"),
                            ("invalid-json", "Invalid Json"),
                            ("invalid-envelope", "Invalid Envelope"),
                            ("segment-count", "Segment Count"),
                            ("invalid-segment", "Invalid Segment"),
                            ("finish-length", "Finish Length"),
                            ("unknown", "Unknown"),
                        ],
                        db_index=True,
                        max_length=24,
                    ),
                ),
                (
                    "http_status",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                ("exception_class", models.CharField(blank=True, max_length=255)),
                ("finish_reason", models.CharField(blank=True, max_length=64)),
                ("response_shape", models.CharField(blank=True, max_length=64)),
                (
                    "response_segment_count",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                ("elapsed_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("first_byte_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("response_bytes", models.PositiveIntegerField(blank=True, null=True)),
                ("prompt_tokens", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "completion_tokens",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("total_tokens", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "reasoning_tokens",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("response_id", models.CharField(blank=True, max_length=255)),
                (
                    "run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="request_attempts",
                        to="trans.judgerun",
                    ),
                ),
            ],
            options={
                "verbose_name": "Judge request attempt",
                "verbose_name_plural": "Judge request attempts",
            },
        ),
        migrations.CreateModel(
            name="JudgeDeferral",
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
                ("request_identity", models.CharField(max_length=64)),
                ("target_hash", models.CharField(max_length=64)),
                ("context_hash", models.CharField(max_length=64)),
                ("project_context_hash", models.CharField(max_length=64)),
                ("source_language", models.CharField(max_length=32)),
                ("target_language", models.CharField(max_length=32)),
                ("profile_fingerprint", models.CharField(max_length=64)),
                ("prompt_schema_version", models.CharField(max_length=64)),
                ("seat", models.PositiveSmallIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("slow", "Slow"),
                            ("closed", "Closed"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=10,
                    ),
                ),
                ("consecutive_failures", models.PositiveSmallIntegerField(default=0)),
                (
                    "last_failure_kind",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("transport", "Transport"),
                            ("deadline", "Deadline"),
                            ("response-too-large", "Response Too Large"),
                            ("http-auth", "Http Auth"),
                            ("http-rate-limit", "Http Rate Limit"),
                            ("http-server", "Http Server"),
                            ("http-other", "Http Other"),
                            ("empty-response", "Empty Response"),
                            ("invalid-json", "Invalid Json"),
                            ("invalid-envelope", "Invalid Envelope"),
                            ("segment-count", "Segment Count"),
                            ("invalid-segment", "Invalid Segment"),
                            ("finish-length", "Finish Length"),
                            ("unknown", "Unknown"),
                        ],
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("next_attempt_at", models.DateTimeField(db_index=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("claim_expires_at", models.DateTimeField(blank=True, null=True)),
                ("claim_token", models.CharField(blank=True, max_length=64)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="judge_deferrals",
                        to="trans.unit",
                    ),
                ),
            ],
            options={
                "verbose_name": "Judge deferral",
                "verbose_name_plural": "Judge deferrals",
            },
        ),
        migrations.AddField(
            model_name="judgerun",
            name="configuration_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="profile_fingerprint",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="project_context_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="prompt_schema_version",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="request_identity",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="request_attempt",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="verdicts",
                to="trans.judgerequestattempt",
            ),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="source_language",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="target_language",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="llmusagelog",
            name="request_attempt",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="usage_logs",
                to="trans.judgerequestattempt",
            ),
        ),
        migrations.AddIndex(
            model_name="judgerequestattempt",
            index=models.Index(
                fields=["endpoint_fingerprint", "model", "seat", "-created_at"],
                name="judge_attempt_seat_recent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="judgerequestattempt",
            index=models.Index(
                fields=["failure_kind", "-created_at"],
                name="judge_attempt_failure_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="judgerequestattempt",
            index=models.Index(
                fields=["run", "-created_at"], name="judge_attempt_run_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="judgeadaptivestate",
            constraint=models.UniqueConstraint(
                fields=("endpoint_fingerprint", "model", "seat"),
                name="judge_adaptive_state_identity",
            ),
        ),
        migrations.AddIndex(
            model_name="judgedeferral",
            index=models.Index(
                fields=["state", "next_attempt_at"],
                name="judge_deferral_ready_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="judgedeferral",
            index=models.Index(
                fields=["state", "claim_expires_at"],
                name="judge_deferral_claim_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="judgedeferral",
            index=models.Index(
                fields=["unit", "seat", "-created_at"],
                name="judge_deferral_unit_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="judgedeferral",
            constraint=models.UniqueConstraint(
                fields=("unit", "seat", "request_identity"),
                name="judge_deferral_identity",
            ),
        ),
        migrations.AddIndex(
            model_name="judgeverdict",
            index=models.Index(
                fields=[
                    "unit",
                    "request_identity",
                    "profile_fingerprint",
                    "prompt_schema_version",
                    "-timestamp",
                ],
                name="judge_verdict_cache_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="llmusagelog",
            index=models.Index(
                fields=["operation", "-created_at"],
                name="llm_usage_operation_recent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="llmusagelog",
            index=models.Index(
                fields=["request_attempt", "-created_at"],
                name="llm_usage_attempt_recent_idx",
            ),
        ),
    ]
