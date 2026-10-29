# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0151_mt_reconciliation")]
    operations = [
        migrations.AddField(
            model_name="producerllmexchange",
            name="run",
            field=models.ForeignKey(
                to="trans.producerrun", null=True, blank=True, on_delete=models.PROTECT
            ),
        ),
        migrations.AddField(
            model_name="producerllmexchange",
            name="resolved_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
