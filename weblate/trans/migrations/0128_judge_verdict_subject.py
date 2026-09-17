# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0127_loc_kit_dispatch_ledger")]

    operations = [
        migrations.AddField(
            model_name="judgeverdict",
            name="subject",
            field=models.CharField(
                choices=[("live", "Live"), ("candidate", "Candidate")],
                db_index=True,
                default="live",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="judgeverdict",
            name="candidate_target_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
