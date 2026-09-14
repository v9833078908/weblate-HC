# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep why a reply was refused and what the model answered on the ledger row."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0123_loc_kit_import_draft_application"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmusagelog",
            name="refusal_reason",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="llmusagelog",
            name="reply_excerpt",
            field=models.TextField(blank=True),
        ),
    ]
