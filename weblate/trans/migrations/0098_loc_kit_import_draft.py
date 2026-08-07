import uuid

import django.db.models.deletion
import weblate.trans.models.loc_kit
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0097_workflowsetting_restrict_direct_editing"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LocKitImportDraft",
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
                (
                    "token",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("session_key", models.CharField(max_length=40)),
                (
                    "slug",
                    models.SlugField(max_length=100),
                ),
                ("name", models.CharField(max_length=100)),
                ("source_filename", models.CharField(max_length=400)),
                (
                    "uploaded",
                    models.FileField(
                        max_length=400,
                        storage=weblate.trans.models.loc_kit.LOC_KIT_DRAFT_STORAGE,
                        upload_to="drafts/%Y/%m/%d/",
                    ),
                ),
                ("sheet", models.CharField(blank=True, max_length=400)),
                ("profile_json", models.TextField(blank=True)),
                ("preview_json", models.TextField(blank=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("uploaded", "Uploaded"),
                            ("sheet-selected", "Sheet selected"),
                            ("preview-ready", "Preview ready"),
                            ("consumed", "Consumed"),
                        ],
                        default="uploaded",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loc_kit_import_drafts",
                        to="trans.category",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loc_kit_import_drafts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="loc_kit_import_drafts",
                        to="trans.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "loc-kit import draft",
                "verbose_name_plural": "loc-kit import drafts",
            },
        ),
    ]
